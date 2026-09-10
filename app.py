# -*- coding: utf-8 -*-
"""Sendline — local UI to edit recipients and launch worker.py"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "run_config.json"
STOP_FLAG = ROOT / "sendline.stop"
STATIC_DIR = ROOT / "static"
ATTACHMENTS_DIR = ROOT / "attachments"
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
ALLOWED_ATTACHMENT_EXT = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
}

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_ATTACHMENT_BYTES

_lock = threading.Lock()
_state = {
    "running": False,
    "started_at": None,
    "exit_code": None,
    "log_lines": [],
}
_proc: subprocess.Popen | None = None


def _default_config() -> dict:
    return {
        "people_names": [],
        "message_template": "Hi {name},\n\n",
        "linkedin_username": "",
        "linkedin_password": "",
        "attachment_path": None,
        "attachment_name": None,
    }


def _clean_username(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _clean_password(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\r\n", "").replace("\n", "")


def _safe_attachment_name(filename: str) -> str:
    name = Path(filename or "").name.replace("\x00", "")
    name = re.sub(r"[\\/]+", "_", name).strip(" .")
    if not name or name in {".", ".."}:
        return "attachment"
    return name[:180]


def _is_managed_attachment(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = ATTACHMENTS_DIR.resolve()
        if hasattr(resolved, "is_relative_to"):
            return resolved.is_relative_to(root)
        return os.path.commonpath([str(resolved), str(root)]) == str(root)
    except (OSError, ValueError):
        return False


def _remove_managed_attachment(path_str: str | None) -> None:
    if not path_str:
        return
    path = Path(path_str)
    if not _is_managed_attachment(path):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _attachment_fields(path_str, name: str | None) -> tuple[str | None, str | None]:
    if not isinstance(path_str, str) or not path_str.strip():
        return None, None
    path = Path(path_str)
    if not path.is_file():
        return None, None
    display = name.strip() if isinstance(name, str) and name.strip() else path.name
    return str(path), display


def read_config() -> dict:
    if not CONFIG_PATH.is_file():
        return _default_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_config()
    names = data.get("people_names") or []
    if isinstance(names, str):
        names = [n.strip() for n in names.splitlines() if n.strip()]
    attachment_path, attachment_name = _attachment_fields(
        data.get("attachment_path"),
        data.get("attachment_name"),
    )
    return {
        "people_names": [n for n in names if isinstance(n, str) and n.strip()],
        "message_template": data.get("message_template") or "",
        "linkedin_username": _clean_username(data.get("linkedin_username")),
        "linkedin_password": _clean_password(data.get("linkedin_password")),
        "attachment_path": attachment_path,
        "attachment_name": attachment_name,
    }


def write_config(data: dict) -> dict:
    names = data.get("people_names") or []
    if isinstance(names, str):
        names = [n.strip() for n in names.splitlines() if n.strip()]
    else:
        names = [str(n).strip() for n in names if str(n).strip()]

    attachment_path, attachment_name = _attachment_fields(
        data.get("attachment_path"),
        data.get("attachment_name"),
    )

    payload = {
        "people_names": names,
        "message_template": (data.get("message_template") or "").replace("\r\n", "\n"),
        "linkedin_username": _clean_username(data.get("linkedin_username")),
        "linkedin_password": _clean_password(data.get("linkedin_password")),
        "attachment_path": attachment_path,
        "attachment_name": attachment_name,
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _worker_python() -> str:
    """Prefer an interpreter that has selenium/pyperclip installed."""
    candidates = [
        sys.executable,
        str(Path.home() / "hailmary" / "venv" / "Scripts" / "python.exe"),
        str(ROOT / "venv" / "Scripts" / "python.exe"),
    ]
    seen: set[str] = set()
    for candidate in candidates:
        path = str(Path(candidate))
        if path in seen or not Path(path).is_file():
            continue
        seen.add(path)
        try:
            check = subprocess.run(
                [path, "-c", "import selenium, pyperclip"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if check.returncode == 0:
                return path
        except Exception:
            continue
    return sys.executable


def _clear_stop_flag() -> None:
    try:
        STOP_FLAG.unlink(missing_ok=True)
    except OSError:
        pass


def _kill_chrome() -> None:
    if os.name != "nt":
        return
    subprocess.run(
        ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
        capture_output=True,
        text=True,
        check=False,
    )


def _force_stop_if_needed(proc: subprocess.Popen) -> None:
    """If the worker ignores the stop flag, kill it and close Chrome."""
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.3)
    _append_log("[sendline] worker did not stop in time — forcing exit and closing Chrome")
    try:
        proc.terminate()
    except Exception:
        pass
    time.sleep(1.5)
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass
    _kill_chrome()


def _append_log(line: str) -> None:
    with _lock:
        _state["log_lines"].append(line)
        # keep memory bounded
        if len(_state["log_lines"]) > 2000:
            _state["log_lines"] = _state["log_lines"][-1500:]


def _reader(pipe, prefix: str = "") -> None:
    try:
        for raw in iter(pipe.readline, ""):
            if raw == "":
                break
            _append_log(prefix + raw.rstrip("\n"))
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _watch_process(proc: subprocess.Popen) -> None:
    global _proc
    code = proc.wait()
    with _lock:
        _state["running"] = False
        _state["exit_code"] = code
        _proc = None
    _append_log(f"[sendline] worker finished (exit {code})")


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/people-template")
def api_people_template():
    return send_from_directory(
        STATIC_DIR,
        "people_template.csv",
        as_attachment=True,
        download_name="sendline-people-template.csv",
        mimetype="text/csv; charset=utf-8",
    )


@app.errorhandler(413)
def api_too_large(_err):
    return jsonify({"ok": False, "error": "That file is too large. Keep attachments under 25 MB."}), 413


@app.post("/api/attachment")
def api_upload_attachment():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "Choose a file to attach."}), 400

    original_name = _safe_attachment_name(uploaded.filename)
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_ATTACHMENT_EXT:
        return jsonify(
            {
                "ok": False,
                "error": "Please attach a PDF, Word, PowerPoint, Excel, or image file.",
            }
        ), 400

    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ATTACHMENTS_DIR / original_name
    current = read_config()
    old_path = current.get("attachment_path")
    if old_path and Path(old_path).resolve() != dest.resolve():
        _remove_managed_attachment(old_path)

    uploaded.save(str(dest))
    current["attachment_path"] = str(dest)
    current["attachment_name"] = original_name
    saved = write_config(current)
    return jsonify(
        {
            "ok": True,
            "attachment_path": saved["attachment_path"],
            "attachment_name": saved["attachment_name"],
        }
    )


@app.delete("/api/attachment")
def api_clear_attachment():
    current = read_config()
    _remove_managed_attachment(current.get("attachment_path"))
    current["attachment_path"] = None
    current["attachment_name"] = None
    write_config(current)
    return jsonify({"ok": True, "attachment_path": None, "attachment_name": None})


@app.get("/api/config")
def api_get_config():
    return jsonify(read_config())


@app.post("/api/config")
def api_save_config():
    body = request.get_json(force=True, silent=True) or {}
    saved = write_config(body)
    return jsonify({"ok": True, "config": saved, "count": len(saved["people_names"])})


@app.get("/api/status")
def api_status():
    with _lock:
        return jsonify(
            {
                "running": _state["running"],
                "started_at": _state["started_at"],
                "exit_code": _state["exit_code"],
                "log_count": len(_state["log_lines"]),
            }
        )


@app.get("/api/logs")
def api_logs():
    after = request.args.get("after", "0")
    try:
        idx = max(0, int(after))
    except ValueError:
        idx = 0
    with _lock:
        lines = _state["log_lines"][idx:]
        next_idx = len(_state["log_lines"])
        running = _state["running"]
        exit_code = _state["exit_code"]
    return jsonify(
        {
            "lines": lines,
            "next": next_idx,
            "running": running,
            "exit_code": exit_code,
        }
    )


@app.post("/api/run")
def api_run():
    global _proc
    body = request.get_json(force=True, silent=True) or {}
    saved = write_config(body if body else read_config())

    if not saved["people_names"]:
        return jsonify({"ok": False, "error": "Add at least one name before launching."}), 400
    if "{name}" not in saved["message_template"]:
        return jsonify({"ok": False, "error": "Message must include {name} for personalization."}), 400
    if not saved.get("linkedin_username") or not saved.get("linkedin_password"):
        return jsonify({"ok": False, "error": "Add your LinkedIn username and password before launching."}), 400

    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "error": "Worker is already running."}), 409
        _state["running"] = True
        _state["started_at"] = time.time()
        _state["exit_code"] = None
        _state["log_lines"] = []

    _clear_stop_flag()
    _append_log(f"[sendline] launching worker for {len(saved['people_names'])} people...")

    python = _worker_python()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    try:
        _append_log(f"[sendline] python: {python}")
        proc = subprocess.Popen(
            [python, "-u", str(ROOT / "worker.py")],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
    except Exception as exc:
        with _lock:
            _state["running"] = False
        return jsonify({"ok": False, "error": str(exc)}), 500

    _proc = proc
    threading.Thread(target=_reader, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=_watch_process, args=(proc,), daemon=True).start()
    return jsonify({"ok": True, "count": len(saved["people_names"])})


@app.post("/api/stop")
def api_stop():
    global _proc
    with _lock:
        proc = _proc
        running = _state["running"]
    if not running or proc is None:
        return jsonify({"ok": False, "error": "Nothing is running."}), 400
    try:
        STOP_FLAG.write_text("stop\n", encoding="utf-8")
        _append_log("[sendline] stop requested — worker will close Chrome")
        threading.Thread(target=_force_stop_if_needed, args=(proc,), daemon=True).start()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


if __name__ == "__main__":
    STATIC_DIR.mkdir(exist_ok=True)
    ATTACHMENTS_DIR.mkdir(exist_ok=True)
    if not CONFIG_PATH.exists():
        write_config(_default_config())
    print("Sendline UI -> http://127.0.0.1:5055")
    app.run(host="127.0.0.1", port=5055, debug=False, threaded=True)
