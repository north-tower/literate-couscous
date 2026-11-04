# -*- coding: utf-8 -*-
"""Job dispatcher: one VPS Chrome (server users) + per-user desktop agents (local)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import auth
import db
import egress
import pace
import platform_chrome
import vault

ATTACHMENT_DOWNLOAD_TTL_SEC = 3600


class JobPayloadError(RuntimeError):
    """Campaign, vault, or proxy data could not be prepared for a job."""


class AgentOfflineError(Exception):
    """Local user tried to launch without a connected desktop agent."""

ROOT = Path(__file__).resolve().parent
DEBUG_PORT = 9222

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_job_id: int | None = None
_log_job_id: int | None = None
_log_lines: list[str] = []
_force_stop_started: set[int] = set()


def _worker_python() -> str:
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


kill_pid_tree = platform_chrome.kill_pid_tree


def kill_debug_port_listeners(port: int = DEBUG_PORT) -> None:
    for pid in platform_chrome.pids_on_port(port):
        kill_pid_tree(pid)


def read_chrome_pid(user_id: int, job_id: int) -> int | None:
    path = db.job_dir(user_id, job_id) / "chrome.pid"
    try:
        raw = path.read_text(encoding="utf-8").strip()
        pid = int(raw)
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def write_stop_flag(user_id: int, job_id: int) -> None:
    flag = db.job_dir(user_id, job_id) / "stop"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("stop\n", encoding="utf-8")


def append_log(job_id: int, line: str) -> None:
    with _lock:
        global _log_job_id, _log_lines
        if _log_job_id != job_id:
            _log_job_id = job_id
            _log_lines = []
        _log_lines.append(line)
        if len(_log_lines) > 2000:
            _log_lines = _log_lines[-1500:]
        job = db.get_job(job_id)
        if job:
            log_path = db.job_dir(int(job["user_id"]), job_id) / "logs.txt"
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass


def _read_log_file(user_id: int, job_id: int) -> list[str]:
    path = db.job_dir(user_id, job_id) / "logs.txt"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()


def _log_line_count(user_id: int, job_id: int) -> int:
    with _lock:
        if _log_job_id == job_id:
            return len(_log_lines)
    return len(_read_log_file(user_id, job_id))


def finalize_job_from_exit_code(job_id: int, exit_code: int) -> str | None:
    """Apply worker exit semantics (same as ``_watch``). Returns final status or None if unchanged."""
    job = db.get_job(job_id)
    status = (job or {}).get("status")
    if status not in ("running", "stopping", "queued"):
        return None
    if exit_code == 0:
        final = "done"
    elif exit_code == 75:
        final = "paused"
    elif exit_code in (1, 130) or status == "stopping":
        final = "cancelled"
    else:
        final = "failed"
    db.update_job(job_id, status=final, finished_at=time.time(), exit_code=exit_code)
    append_log(job_id, f"[sendline] worker finished (exit {exit_code})")
    return final


def agent_poll_work(user_id: int, *, request_base_url: str = "") -> dict:
    """Claim queued local work or return in-progress job metadata (Phase 1.4)."""
    job = db.open_job_for_user(user_id)
    if not job:
        return {"ok": True, "work": None}

    job_id = int(job["id"])
    status = job["status"]

    if status in ("running", "stopping"):
        return {
            "ok": True,
            "work": {
                "job_id": job_id,
                "status": status,
                "people_count": job.get("people_count"),
            },
            "stop_requested": status == "stopping",
            "log_cursor": _log_line_count(user_id, job_id),
        }

    if status != "queued":
        return {"ok": True, "work": None}

    if db.get_execution_mode(user_id) != db.EXECUTION_MODE_LOCAL:
        return {"ok": True, "work": None}

    try:
        build_job_payload(user_id, job_id)
    except JobPayloadError as exc:
        db.update_job(job_id, status="failed", finished_at=time.time())
        append_log(job_id, f"[sendline] could not prepare job: {exc}")
        start_next()
        return {"ok": True, "work": None}

    now = time.time()
    global _log_job_id, _log_lines
    with _lock:
        _log_job_id = job_id
        _log_lines = []

    db.update_job(job_id, status="running", started_at=now)
    append_log(job_id, f"[sendline] launching worker for {job.get('people_count') or 0} people...")
    label = (db.get_user_proxy(user_id) or {}).get("proxy_label") or ""
    if label:
        append_log(job_id, f"[sendline] residential exit {label}")
    else:
        append_log(job_id, "[sendline] no residential proxy configured — this run uses the server IP")
    append_log(job_id, "[sendline] desktop agent claimed job")

    try:
        bundle = build_agent_work_bundle(user_id, job_id, request_base_url=request_base_url)
    except JobPayloadError as exc:
        db.update_job(job_id, status="failed", finished_at=time.time())
        append_log(job_id, f"[sendline] could not prepare job: {exc}")
        start_next()
        return {"ok": True, "work": None}

    return {
        "ok": True,
        "work": bundle,
        "stop_requested": False,
        "log_cursor": _log_line_count(user_id, job_id),
    }


def agent_append_logs(user_id: int, job_id: int, lines: list[str]) -> None:
    job = db.get_job(job_id)
    if not job or int(job["user_id"]) != user_id:
        raise ValueError("Job not found.")
    if job["status"] not in ("running", "stopping"):
        raise ValueError("Job is not running.")
    for line in lines:
        if line is None:
            continue
        append_log(job_id, str(line).rstrip("\n"))


def agent_complete_job(user_id: int, job_id: int, exit_code: int) -> dict:
    job = db.get_job(job_id)
    if not job or int(job["user_id"]) != user_id:
        raise ValueError("Job not found.")
    if job["status"] not in ("running", "stopping"):
        raise ValueError("Job is not active.")
    finalize_job_from_exit_code(job_id, int(exit_code))
    start_next()
    updated = db.get_job(job_id)
    assert updated is not None
    return updated


def logs_for_user(user_id: int, after: int = 0) -> tuple[list[str], int, dict | None]:
    job = db.open_job_for_user(user_id) or db.latest_job_for_user(user_id)
    if not job:
        return [], 0, None
    job_id = int(job["id"])
    with _lock:
        if _log_job_id == job_id:
            lines = list(_log_lines)
        else:
            lines = _read_log_file(int(job["user_id"]), job_id)
    idx = max(0, after)
    return lines[idx:], len(lines), job


def _attachment_signing_key() -> bytes:
    return auth.load_flask_secret().encode("utf-8")


def sign_attachment_download(job_id: int, user_id: int, expires_at: int) -> str:
    message = f"{job_id}:{user_id}:{expires_at}".encode("utf-8")
    return hmac.new(_attachment_signing_key(), message, hashlib.sha256).hexdigest()


def verify_attachment_download(job_id: int, user_id: int, expires_at: int, signature: str) -> bool:
    if expires_at <= 0 or time.time() > expires_at:
        return False
    if not signature:
        return False
    expected = sign_attachment_download(job_id, user_id, expires_at)
    return hmac.compare_digest(expected, signature)


def attachment_download_url(job_id: int, user_id: int, base_url: str = "") -> str | None:
    """Time-limited attachment URL (optional alternative to Bearer on the same route)."""
    campaign = db.get_campaign(user_id)
    if not campaign.get("attachment_path"):
        return None
    expires_at = int(time.time()) + ATTACHMENT_DOWNLOAD_TTL_SEC
    sig = sign_attachment_download(job_id, user_id, expires_at)
    path = f"/api/agent/jobs/{job_id}/attachment?expires={expires_at}&sig={sig}"
    if base_url:
        return f"{base_url.rstrip('/')}{path}"
    return path


def build_job_payload(user_id: int, job_id: int) -> dict:
    """Build run + secrets dicts and proxy metadata (no disk write)."""
    db.ensure_user_dirs(user_id)
    campaign = db.get_campaign(user_id)
    secret = db.get_linkedin_secret(user_id)
    password = vault.decrypt_password(secret.get("password_ciphertext") or "")
    try:
        proxy = egress.assign_proxy(user_id)
    except egress.EgressError as exc:
        raise JobPayloadError(str(exc)) from exc

    att_path = campaign.get("attachment_path")
    attachment_name = campaign.get("attachment_name")
    if att_path and not attachment_name:
        attachment_name = Path(str(att_path)).name

    proxy_label = (db.get_user_proxy(user_id) or {}).get("proxy_label") or ""
    run = {
        "people_names": campaign["people_names"],
        "message_template": campaign["message_template"],
        "attachment_path": att_path,
        "ledger_path": str(pace.ledger_path(db.user_dir(user_id))),
    }
    secrets = {
        "linkedin_username": secret.get("username") or "",
        "linkedin_password": password,
        "proxy_url": proxy.as_url() if proxy else "",
    }
    attachment = None
    if att_path:
        attachment = {
            "name": attachment_name or "attachment",
            "server_path": str(att_path),
        }
    return {
        "job_id": job_id,
        "user_id": user_id,
        "run": run,
        "secrets": secrets,
        "attachment": attachment,
        "proxy_label": proxy_label,
    }


def build_agent_work_bundle(user_id: int, job_id: int, *, request_base_url: str = "") -> dict:
    """JSON-ready work bundle for desktop agent (Phase 1.4 GET /api/agent/work)."""
    job = db.get_job(job_id)
    if not job or int(job["user_id"]) != user_id:
        raise JobPayloadError("Job not found.")
    payload = build_job_payload(user_id, job_id)
    run = dict(payload["run"])
    run["attachment_path"] = None
    run["ledger_path"] = None

    attachment_block = None
    if payload.get("attachment"):
        attachment_block = {
            "name": payload["attachment"]["name"],
            "path": f"/api/agent/jobs/{job_id}/attachment",
            "download_url": attachment_download_url(job_id, user_id, request_base_url),
        }

    return {
        "job_id": job_id,
        "user_id": user_id,
        "people_count": job.get("people_count"),
        "people_names": run["people_names"],
        "message_template": run["message_template"],
        "attachment": attachment_block,
        "run": run,
        "secrets": payload["secrets"],
        "proxy_label": payload["proxy_label"],
        "ledger": {
            "use_local_path": True,
            "filename": pace.ledger_path(Path(".")).name,
            "server_sync": False,
        },
    }


def resolve_job_attachment_path(user_id: int, job_id: int) -> Path | None:
    job = db.get_job(job_id)
    if not job or int(job["user_id"]) != user_id:
        return None
    campaign = db.get_campaign(user_id)
    raw = campaign.get("attachment_path")
    if not raw:
        return None
    path = Path(str(raw))
    root = db.attachments_dir(user_id).resolve()
    try:
        resolved = path.resolve()
        if hasattr(resolved, "is_relative_to"):
            if not resolved.is_relative_to(root):
                return None
        elif os.path.commonpath([str(resolved), str(root)]) != str(root):
            return None
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _write_job_payload(user_id: int, job_id: int) -> None:
    if db.get_execution_mode(user_id) == db.EXECUTION_MODE_LOCAL:
        return
    folder = db.job_dir(user_id, job_id)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        payload = build_job_payload(user_id, job_id)
    except JobPayloadError as exc:
        raise RuntimeError(str(exc)) from exc
    run_path = folder / "run.json"
    secrets_path = folder / "secrets.json"
    run_path.write_text(
        json.dumps(payload["run"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    secrets_path.write_text(
        json.dumps(payload["secrets"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(secrets_path, 0o600)
    except OSError:
        pass
    stop = folder / "stop"
    try:
        stop.unlink(missing_ok=True)
    except OSError:
        pass


def _spawn(job: dict) -> None:
    global _proc, _job_id, _log_job_id, _log_lines
    user_id = int(job["user_id"])
    if db.get_execution_mode(user_id) == db.EXECUTION_MODE_LOCAL:
        return
    job_id = int(job["id"])
    db.ensure_user_dirs(user_id)
    folder = db.job_dir(user_id, job_id)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        _write_job_payload(user_id, job_id)
    except Exception as exc:
        db.update_job(job_id, status="failed", finished_at=time.time())
        append_log(job_id, f"[sendline] could not prepare job: {exc}")
        start_next()
        return

    python = _worker_python()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    with _lock:
        _job_id = job_id
        _log_job_id = job_id
        _log_lines = []

    db.update_job(job_id, status="running", started_at=job.get("started_at") or time.time())
    append_log(job_id, f"[sendline] launching worker for {job.get('people_count') or 0} people...")
    label = (db.get_user_proxy(user_id) or {}).get("proxy_label") or ""
    if label:
        append_log(job_id, f"[sendline] residential exit {label}")
    else:
        append_log(job_id, "[sendline] no residential proxy configured — this run uses the server IP")
    append_log(job_id, f"[sendline] python: {python}")

    try:
        proc = subprocess.Popen(
            [
                python,
                "-u",
                str(ROOT / "worker.py"),
                "--job-dir",
                str(folder),
                "--user-data-dir",
                str(db.chrome_dir(user_id)),
                "--debug-port",
                str(DEBUG_PORT),
            ],
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
        db.update_job(job_id, status="failed", finished_at=time.time())
        append_log(job_id, f"[sendline] launch failed: {exc}")
        with _lock:
            _proc = None
            _job_id = None
        start_next()
        return

    with _lock:
        _proc = proc
    try:
        (folder / "worker.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")
    except OSError:
        pass

    threading.Thread(target=_reader, args=(proc, job_id), daemon=True).start()
    threading.Thread(target=_watch, args=(proc, job_id, user_id), daemon=True).start()


def _reader(proc: subprocess.Popen, job_id: int) -> None:
    pipe = proc.stdout
    if pipe is None:
        return
    try:
        for raw in iter(pipe.readline, ""):
            if raw == "":
                break
            append_log(job_id, raw.rstrip("\n"))
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _watch(proc: subprocess.Popen, job_id: int, user_id: int) -> None:
    global _proc, _job_id
    code = proc.wait()
    chrome_pid = read_chrome_pid(user_id, job_id)
    if chrome_pid:
        kill_pid_tree(chrome_pid)
        db.update_job(job_id, chrome_pid=chrome_pid)
    finalize_job_from_exit_code(job_id, code)
    with _lock:
        if _job_id == job_id:
            _proc = None
            _job_id = None
        _force_stop_started.discard(job_id)
    start_next()


def start_next() -> None:
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return
        if db.running_server_job():
            return
        nxt = db.next_queued_server_job()
        if not nxt:
            return
        db.update_job(int(nxt["id"]), status="running", started_at=time.time())
    _spawn(nxt)


def _read_pid_file(path: Path) -> int | None:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def recover_and_start() -> None:
    for job in db.stale_running_jobs():
        user_id = int(job["user_id"])
        job_id = int(job["id"])
        if db.get_execution_mode(user_id) == db.EXECUTION_MODE_LOCAL:
            continue
        folder = db.job_dir(user_id, job_id)
        kill_pid_tree(_read_pid_file(folder / "worker.pid"))
        kill_pid_tree(read_chrome_pid(user_id, job_id) or job.get("chrome_pid"))
        kill_debug_port_listeners()
        db.update_job(job_id, status="failed", finished_at=time.time(), exit_code=1)
        append_log(job_id, "[sendline] previous run did not survive a server restart")
    start_next()


def enqueue(user_id: int, people_count: int) -> dict:
    open_job = db.open_job_for_user(user_id)
    if open_job:
        raise ValueError("You already have a campaign queued or running.")
    if db.get_execution_mode(user_id) == db.EXECUTION_MODE_LOCAL:
        if not db.user_agent_online(user_id):
            raise AgentOfflineError(
                "Open Sendline Desktop on your computer to run campaigns."
            )
    job = db.create_job(user_id, people_count)
    start_next()
    fresh = db.get_job(int(job["id"])) or job
    return fresh


def request_stop(user_id: int) -> dict:
    job = db.open_job_for_user(user_id)
    if not job:
        raise ValueError("Nothing is running.")
    job_id = int(job["id"])
    local = db.get_execution_mode(user_id) == db.EXECUTION_MODE_LOCAL
    if job["status"] == "queued":
        db.update_job(job_id, status="cancelled", finished_at=time.time())
        append_log(
            job_id,
            "[sendline] removed from the queue"
            if local
            else "[sendline] removed from the Chrome queue",
        )
        return db.get_job(job_id) or job
    if job["status"] == "stopping":
        return db.get_job(job_id) or job
    if not local:
        write_stop_flag(int(job["user_id"]), job_id)
    db.update_job(job_id, status="stopping")
    if local:
        append_log(job_id, "[sendline] stop requested — desktop agent will close Chrome")
    else:
        append_log(job_id, "[sendline] stop requested — worker will close Chrome")
    with _lock:
        proc = _proc if _job_id == job_id else None
        already = job_id in _force_stop_started
        if proc is not None:
            _force_stop_started.add(job_id)
    if not local and proc is not None and not already:
        threading.Thread(
            target=_force_stop_if_needed,
            args=(proc, job_id, int(job["user_id"])),
            daemon=True,
        ).start()
    elif local and not already:
        with _lock:
            _force_stop_started.add(job_id)
        threading.Thread(
            target=_force_stop_local_if_needed,
            args=(job_id,),
            daemon=True,
        ).start()
    return db.get_job(job_id) or job


def _force_stop_local_if_needed(job_id: int) -> None:
    """If a desktop job stays ``stopping`` too long, finalize so the UI can recover."""
    deadline = time.time() + 20
    while time.time() < deadline:
        job = db.get_job(job_id)
        if not job or job["status"] != "stopping":
            with _lock:
                _force_stop_started.discard(job_id)
            return
        time.sleep(0.5)
    job = db.get_job(job_id)
    if not job or job["status"] != "stopping":
        with _lock:
            _force_stop_started.discard(job_id)
        return
    append_log(
        job_id,
        "[sendline] desktop agent did not confirm stop in time — marking campaign cancelled",
    )
    finalize_job_from_exit_code(job_id, 130)
    start_next()
    with _lock:
        _force_stop_started.discard(job_id)


def _force_stop_if_needed(proc: subprocess.Popen, job_id: int, user_id: int) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            with _lock:
                _force_stop_started.discard(job_id)
            return
        time.sleep(0.3)
    append_log(job_id, "[sendline] worker did not stop in time — forcing exit and closing Chrome")
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
    kill_pid_tree(read_chrome_pid(user_id, job_id))
    kill_debug_port_listeners()
    with _lock:
        _force_stop_started.discard(job_id)


def current_proc_job_id() -> int | None:
    with _lock:
        return _job_id


def status_for_user(user_id: int, is_admin: bool = False) -> dict:
    open_job = db.open_job_for_user(user_id)
    latest = open_job or db.latest_job_for_user(user_id)
    running_vps = db.running_server_job()
    execution_mode = db.get_execution_mode(user_id)
    snapshot = db.queue_snapshot()
    job_id = int(latest["id"]) if latest else None
    job_status = latest["status"] if latest else "idle"
    is_mine = bool(open_job)
    if execution_mode == db.EXECUTION_MODE_LOCAL:
        queue_pos = None
    else:
        queue_pos = db.queue_position(job_id) if open_job and job_id else None
    if execution_mode == db.EXECUTION_MODE_LOCAL:
        machine_busy = False
    else:
        machine_busy = running_vps is not None and (
            not open_job or int(running_vps["id"]) != int(open_job["id"])
        )
    running_flag = bool(open_job and open_job["status"] in ("running", "stopping"))
    payload = {
        "running": running_flag,
        "job_id": job_id,
        "job_status": job_status,
        "queue_position": queue_pos,
        "is_mine": is_mine,
        "machine_busy": machine_busy,
        "started_at": (open_job or {}).get("started_at") if running_flag else None,
        "exit_code": None if open_job else (latest or {}).get("exit_code"),
        "log_count": 0,
        "people_count": (latest or {}).get("people_count"),
    }
    if latest:
        with _lock:
            if _log_job_id == int(latest["id"]):
                payload["log_count"] = len(_log_lines)
            else:
                payload["log_count"] = len(_read_log_file(int(latest["user_id"]), int(latest["id"])))
    if is_admin:
        payload["admin_queue"] = snapshot
    payload.update(db.get_desktop_feedback(user_id))
    return payload
