# -*- coding: utf-8 -*-
"""
Dev desktop agent — polls Sendline API and runs worker.py locally (Phase 1.7).

Usage:
  py scripts/agent_dev.py register --email you@example.com --password '...' --label "Dev PC"
  set SENDLINE_AGENT_TOKEN=<token from register>
  py scripts/agent_dev.py run

Requires: user execution_mode=local, Flask on SENDLINE_API_URL (default http://127.0.0.1:5055).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

AGENT_VERSION = "agent_dev/1.0"
DEFAULT_API_URL = "http://127.0.0.1:5055"
POLL_IDLE_SEC = 2.5
HEARTBEAT_SEC = 30
LOG_BATCH_SIZE = 40
DEBUG_PORT = 9222

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_api_url() -> str:
    return (os.environ.get("SENDLINE_API_URL") or DEFAULT_API_URL).rstrip("/")


def agent_data_root() -> Path:
    override = os.environ.get("SENDLINE_AGENT_DATA", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Sendline"
    return Path.home() / ".sendline"


def job_folder(job_id: int) -> Path:
    return agent_data_root() / "jobs" / str(job_id)


def chrome_profile_dir() -> Path:
    return agent_data_root() / "chrome"


def attachments_dir() -> Path:
    return agent_data_root() / "attachments"


def local_ledger_path() -> Path:
    return agent_data_root() / "send_ledger.json"


def worker_python() -> str:
    candidates = [
        sys.executable,
        str(REPO_ROOT / "venv" / "Scripts" / "python.exe"),
        str(Path.home() / "hailmary" / "venv" / "Scripts" / "python.exe"),
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


class ApiClient:
    def __init__(self, base_url: str, bearer: str | None = None, cookie_jar: CookieJar | None = None):
        self.base_url = base_url.rstrip("/")
        self.bearer = bearer
        self._jar = cookie_jar

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        hdrs = dict(headers or {})
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        elif raw_body is not None:
            payload = raw_body
        else:
            payload = None
        if self.bearer:
            hdrs["Authorization"] = f"Bearer {self.bearer}"
        req = urllib.request.Request(url, data=payload, headers=hdrs, method=method)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar or CookieJar()))
        try:
            with opener.open(req, timeout=120) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            data = exc.read()
            status = exc.code
        else:
            status = resp.status
        if not data:
            return status, None
        try:
            return status, json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            return status, data

    def get_work(self) -> dict:
        status, data = self._request("GET", "/api/agent/work")
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(f"GET /api/agent/work failed ({status}): {data!r}")
        return data

    def post_logs(self, job_id: int, lines: list[str]) -> None:
        status, data = self._request(
            "POST",
            "/api/agent/logs",
            body={"job_id": job_id, "lines": lines},
        )
        if status != 200:
            raise RuntimeError(f"POST /api/agent/logs failed ({status}): {data!r}")

    def post_heartbeat(self) -> None:
        status, data = self._request(
            "POST",
            "/api/agent/heartbeat",
            body={"agent_version": AGENT_VERSION},
        )
        if status != 200:
            raise RuntimeError(f"POST /api/agent/heartbeat failed ({status}): {data!r}")

    def post_complete(self, job_id: int, exit_code: int) -> None:
        status, data = self._request(
            "POST",
            "/api/agent/complete",
            body={"job_id": job_id, "exit_code": exit_code},
        )
        if status != 200:
            raise RuntimeError(f"POST /api/agent/complete failed ({status}): {data!r}")

    def download_attachment(self, job_id: int, attachment: dict) -> Path:
        attachments_dir().mkdir(parents=True, exist_ok=True)
        name = Path(str(attachment.get("name") or "attachment")).name
        dest = attachments_dir() / name
        rel = attachment.get("path") or f"/api/agent/jobs/{job_id}/attachment"
        url = attachment.get("download_url") or f"{self.base_url}{rel}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.bearer}"}, method="GET")
        opener = urllib.request.build_opener()
        with opener.open(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
        return dest

    def login(self, email: str, password: str) -> None:
        status, data = self._request("POST", "/api/login", body={"email": email, "password": password})
        if status != 200 or not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError(f"Login failed ({status}): {data!r}")

    def fetch_csrf(self) -> str:
        status, data = self._request("GET", "/api/me")
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(f"GET /api/me failed ({status}): {data!r}")
        token = data.get("csrf")
        if not token:
            raise RuntimeError("Not logged in — no CSRF token from /api/me")
        return str(token)

    def register_agent(self, label: str, csrf: str) -> str:
        status, data = self._request(
            "POST",
            "/api/agent/register",
            body={"label": label},
            headers={"X-CSRF-Token": csrf},
        )
        if status not in (200, 201) or not isinstance(data, dict) or not data.get("token"):
            raise RuntimeError(f"Register failed ({status}): {data!r}")
        return str(data["token"])


def _is_full_work_bundle(work: dict) -> bool:
    return isinstance(work.get("secrets"), dict) and isinstance(work.get("run"), dict)


def prepare_job_dir(api: ApiClient, work: dict) -> Path:
    job_id = int(work["job_id"])
    folder = job_folder(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    chrome_profile_dir().mkdir(parents=True, exist_ok=True)
    local_ledger_path().parent.mkdir(parents=True, exist_ok=True)

    run = dict(work["run"])
    attachment_meta = work.get("attachment")
    if attachment_meta:
        local_file = api.download_attachment(job_id, attachment_meta)
        run["attachment_path"] = str(local_file)
    else:
        run["attachment_path"] = None
    run["ledger_path"] = str(local_ledger_path())

    secrets = dict(work["secrets"])
    run_path = folder / "run.json"
    secrets_path = folder / "secrets.json"
    run_path.write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    secrets_path.write_text(json.dumps(secrets, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(secrets_path, 0o600)
    except OSError:
        pass
    stop = folder / "stop"
    try:
        stop.unlink(missing_ok=True)
    except OSError:
        pass
    return folder


def spawn_worker(folder: Path) -> subprocess.Popen:
    python = worker_python()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    return subprocess.Popen(
        [
            python,
            "-u",
            str(REPO_ROOT / "worker.py"),
            "--job-dir",
            str(folder),
            "--user-data-dir",
            str(chrome_profile_dir()),
            "--debug-port",
            str(DEBUG_PORT),
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=creationflags,
    )


def run_worker_job(api: ApiClient, work: dict) -> None:
    job_id = int(work["job_id"])
    folder = prepare_job_dir(api, work)
    print(f"[agent_dev] job {job_id} → {folder}", flush=True)

    proc = spawn_worker(folder)
    stop_event = threading.Event()
    log_lock = threading.Lock()
    pending: list[str] = []

    def flush_logs() -> None:
        with log_lock:
            if not pending:
                return
            batch = pending[:LOG_BATCH_SIZE]
            del pending[: len(batch)]
        api.post_logs(job_id, batch)

    def heartbeat_loop() -> None:
        while not stop_event.is_set():
            try:
                api.post_heartbeat()
            except Exception as exc:
                print(f"[agent_dev] heartbeat error: {exc}", flush=True)
            stop_event.wait(HEARTBEAT_SEC)

    def stop_poll_loop() -> None:
        while not stop_event.is_set():
            if proc.poll() is not None:
                return
            try:
                poll = api.get_work()
            except Exception as exc:
                print(f"[agent_dev] stop poll error: {exc}", flush=True)
                stop_event.wait(POLL_IDLE_SEC)
                continue
            if poll.get("stop_requested"):
                flag = folder / "stop"
                flag.write_text("stop\n", encoding="utf-8")
                print(f"[agent_dev] stop requested → wrote {flag}", flush=True)
                return
            stop_event.wait(POLL_IDLE_SEC)

    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    stop_thread = threading.Thread(target=stop_poll_loop, daemon=True)
    hb_thread.start()
    stop_thread.start()

    try:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, ""):
            if raw == "":
                break
            line = raw.rstrip("\n")
            print(line, flush=True)
            with log_lock:
                pending.append(line)
            if len(pending) >= LOG_BATCH_SIZE:
                flush_logs()
        flush_logs()
    finally:
        stop_event.set()
        code = proc.wait()
        try:
            flush_logs()
        except Exception as exc:
            print(f"[agent_dev] final log flush failed: {exc}", flush=True)
        try:
            api.post_complete(job_id, code)
        except Exception as exc:
            print(f"[agent_dev] complete failed: {exc}", flush=True)
            raise
        print(f"[agent_dev] job {job_id} finished exit {code}", flush=True)


def agent_run_loop(api: ApiClient) -> None:
    print(f"[agent_dev] data root: {agent_data_root()}", flush=True)
    print(f"[agent_dev] API: {api.base_url}", flush=True)
    api.post_heartbeat()
    active_job: int | None = None

    while True:
        try:
            poll = api.get_work()
        except Exception as exc:
            print(f"[agent_dev] work poll error: {exc}", flush=True)
            time.sleep(POLL_IDLE_SEC)
            continue

        work = poll.get("work")
        if not work:
            active_job = None
            time.sleep(POLL_IDLE_SEC)
            continue

        job_id = int(work["job_id"])
        if _is_full_work_bundle(work):
            active_job = job_id
            run_worker_job(api, work)
            active_job = None
            continue

        if active_job is None and work.get("status") in ("running", "stopping"):
            print(
                f"[agent_dev] job {job_id} in progress on server but this agent did not start it "
                f"(status={work.get('status')}). Waiting…",
                flush=True,
            )
        time.sleep(POLL_IDLE_SEC)


def cmd_register(args: argparse.Namespace) -> int:
    jar = CookieJar()
    api = ApiClient(args.api_url, cookie_jar=jar)
    api.login(args.email.strip().lower(), args.password)
    csrf = api.fetch_csrf()
    token = api.register_agent(args.label.strip() or "Dev agent", csrf)
    print("Agent token (store securely — shown once):")
    print(token)
    print()
    print("PowerShell:")
    print(f'  $env:SENDLINE_AGENT_TOKEN = "{token}"')
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    token = (args.token or os.environ.get("SENDLINE_AGENT_TOKEN") or "").strip()
    if not token:
        print("Missing agent token. Use --token or set SENDLINE_AGENT_TOKEN.", file=sys.stderr)
        print("Register: py scripts/agent_dev.py register --email ... --password ...", file=sys.stderr)
        return 2
    api = ApiClient(args.api_url, bearer=token)
    try:
        agent_run_loop(api)
    except KeyboardInterrupt:
        print("\n[agent_dev] stopped", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sendline dev desktop agent (Phase 1.7)")
    parser.add_argument(
        "--api-url",
        default=_env_api_url(),
        help=f"Sendline API base URL (default: {DEFAULT_API_URL}, env SENDLINE_API_URL)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="Login and create an agent token")
    reg.add_argument("--email", required=True)
    reg.add_argument("--password", required=True)
    reg.add_argument("--label", default="Dev agent")

    run = sub.add_parser("run", help="Poll for work and run worker.py locally")
    run.add_argument("--token", help="Agent bearer token (else SENDLINE_AGENT_TOKEN)")

    args = parser.parse_args(argv)
    if args.command == "register":
        return cmd_register(args)
    if args.command == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
