# -*- coding: utf-8 -*-
"""One-Chrome FIFO dispatcher: at most one worker on the Windows VPS."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import db
import pace
import vault

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


def _pids_on_port(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    pids: list[int] = []
    needle = f":{port}"
    for line in (result.stdout or "").splitlines():
        upper = line.upper()
        if "LISTEN" not in upper:
            continue
        if needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid and pid not in pids:
            pids.append(pid)
    return pids


def kill_pid_tree(pid: int | None) -> None:
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid), "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
        return
    try:
        os.kill(int(pid), 15)
    except OSError:
        pass


def kill_debug_port_listeners(port: int = DEBUG_PORT) -> None:
    for pid in _pids_on_port(port):
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


def _write_job_payload(user_id: int, job_id: int) -> None:
    folder = db.job_dir(user_id, job_id)
    folder.mkdir(parents=True, exist_ok=True)
    db.ensure_user_dirs(user_id)
    campaign = db.get_campaign(user_id)
    secret = db.get_linkedin_secret(user_id)
    password = vault.decrypt_password(secret.get("password_ciphertext") or "")
    run_path = folder / "run.json"
    secrets_path = folder / "secrets.json"
    run_path.write_text(
        json.dumps(
            {
                "people_names": campaign["people_names"],
                "message_template": campaign["message_template"],
                "attachment_path": campaign.get("attachment_path"),
                "pace_preset": pace.normalize(campaign.get("pace_preset")),
                "ledger_path": str(pace.ledger_path(db.user_dir(user_id))),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    secrets_path.write_text(
        json.dumps(
            {
                "linkedin_username": secret.get("username") or "",
                "linkedin_password": password,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
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
    job = db.get_job(job_id)
    status = (job or {}).get("status")
    if status in ("running", "stopping", "queued"):
        if code == 0:
            final = "done"
        elif code == 75:
            final = "paused"
        elif code in (1, 130) or status == "stopping":
            final = "cancelled"
        else:
            final = "failed"
        db.update_job(job_id, status=final, finished_at=time.time(), exit_code=code)
    append_log(job_id, f"[sendline] worker finished (exit {code})")
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
        if db.running_job():
            return
        nxt = db.next_queued_job()
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
    job = db.create_job(user_id, people_count)
    start_next()
    fresh = db.get_job(int(job["id"])) or job
    return fresh


def request_stop(user_id: int) -> dict:
    job = db.open_job_for_user(user_id)
    if not job:
        raise ValueError("Nothing is running.")
    job_id = int(job["id"])
    if job["status"] == "queued":
        db.update_job(job_id, status="cancelled", finished_at=time.time())
        append_log(job_id, "[sendline] removed from the Chrome queue")
        return db.get_job(job_id) or job
    write_stop_flag(int(job["user_id"]), job_id)
    db.update_job(job_id, status="stopping")
    append_log(job_id, "[sendline] stop requested — worker will close Chrome")
    with _lock:
        proc = _proc if _job_id == job_id else None
        already = job_id in _force_stop_started
        if proc is not None:
            _force_stop_started.add(job_id)
    if proc is not None and not already:
        threading.Thread(
            target=_force_stop_if_needed,
            args=(proc, job_id, int(job["user_id"])),
            daemon=True,
        ).start()
    return db.get_job(job_id) or job


def _force_stop_if_needed(proc: subprocess.Popen, job_id: int, user_id: int) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
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


def current_proc_job_id() -> int | None:
    with _lock:
        return _job_id


def status_for_user(user_id: int, is_admin: bool = False) -> dict:
    open_job = db.open_job_for_user(user_id)
    latest = open_job or db.latest_job_for_user(user_id)
    running = db.running_job()
    snapshot = db.queue_snapshot()
    job_id = int(latest["id"]) if latest else None
    job_status = latest["status"] if latest else "idle"
    is_mine = bool(open_job)
    queue_pos = db.queue_position(job_id) if open_job and job_id else None
    machine_busy = running is not None and (not open_job or int(running["id"]) != int(open_job["id"]))
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
    return payload
