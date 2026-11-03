# -*- coding: utf-8 -*-
"""SQLite tenancy for Sendline users, campaigns, secrets, and jobs."""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "sendline.db"

JOB_OPEN = ("queued", "running", "stopping")
JOB_QUEUED_OR_RUNNING = ("queued", "running")

EXECUTION_MODE_SERVER = "server"
EXECUTION_MODE_LOCAL = "local"
EXECUTION_MODES = (EXECUTION_MODE_SERVER, EXECUTION_MODE_LOCAL)
AGENT_ONLINE_WINDOW_SEC = 60

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def user_dir(user_id: int) -> Path:
    return DATA_DIR / "users" / str(user_id)


def chrome_dir(user_id: int) -> Path:
    return user_dir(user_id) / "chrome"


def attachments_dir(user_id: int) -> Path:
    return user_dir(user_id) / "attachments"


def job_dir(user_id: int, job_id: int) -> Path:
    return user_dir(user_id) / "jobs" / str(job_id)


def ensure_user_dirs(user_id: int) -> None:
    chrome_dir(user_id).mkdir(parents=True, exist_ok=True)
    attachments_dir(user_id).mkdir(parents=True, exist_ok=True)
    (user_dir(user_id) / "jobs").mkdir(parents=True, exist_ok=True)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.execute("PRAGMA busy_timeout=5000")
        return _conn


def init_db() -> None:
    conn = connect()
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('operator', 'admin')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS campaigns (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                people_names_json TEXT NOT NULL DEFAULT '[]',
                message_template TEXT NOT NULL DEFAULT '',
                attachment_path TEXT,
                attachment_name TEXT,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS linkedin_secrets (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                username TEXT NOT NULL DEFAULT '',
                password_ciphertext TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                exit_code INTEGER,
                chrome_pid INTEGER,
                people_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at DESC);
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(campaigns)")}
        if "pace_preset" not in columns:
            conn.execute(
                "ALTER TABLE campaigns ADD COLUMN pace_preset TEXT NOT NULL DEFAULT 'careful'"
            )
        user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "proxy_ciphertext" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN proxy_ciphertext TEXT NOT NULL DEFAULT ''")
        if "proxy_label" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN proxy_label TEXT NOT NULL DEFAULT ''")
        if "proxy_mode" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN proxy_mode TEXT NOT NULL DEFAULT ''")
        if "execution_mode" not in user_columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'server'"
            )

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                label TEXT NOT NULL DEFAULT '',
                token_hash TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                last_seen_at REAL,
                revoked_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_agent_tokens_user_id ON agent_tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_agent_tokens_token_hash ON agent_tokens(token_hash);
            """
        )


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = connect()
    with _lock:
        return conn.execute(sql, tuple(params))


def fetchone(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    return _row_to_dict(execute(sql, params).fetchone())


def fetchall(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [_row_to_dict(row) or {} for row in execute(sql, params).fetchall()]


def get_user_by_id(user_id: int | None) -> dict[str, Any] | None:
    if not user_id:
        return None
    return fetchone("SELECT * FROM users WHERE id = ?", (user_id,))


def get_user_by_email(email: str) -> dict[str, Any] | None:
    return fetchone("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))


def user_count() -> int:
    row = fetchone("SELECT COUNT(*) AS n FROM users")
    return int((row or {}).get("n") or 0)


def active_admin_count() -> int:
    row = fetchone("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1")
    return int((row or {}).get("n") or 0)


def create_user(email: str, password_hash: str, role: str = "operator") -> dict[str, Any]:
    now = time.time()
    email = email.strip().lower()
    if role not in ("operator", "admin"):
        raise ValueError("role must be operator or admin")
    with _lock:
        cur = execute(
            "INSERT INTO users (email, password_hash, role, is_active, created_at) VALUES (?, ?, ?, 1, ?)",
            (email, password_hash, role, now),
        )
        user_id = int(cur.lastrowid)
        execute(
            """INSERT INTO campaigns (user_id, people_names_json, message_template, updated_at)
               VALUES (?, '[]', ?, ?)""",
            (user_id, "Hi {name},\n\n", now),
        )
        execute(
            """INSERT INTO linkedin_secrets (user_id, username, password_ciphertext, updated_at)
               VALUES (?, '', '', ?)""",
            (user_id, now),
        )
    ensure_user_dirs(user_id)
    user = get_user_by_id(user_id)
    assert user is not None
    return user


def list_users() -> list[dict[str, Any]]:
    return fetchall(
        """SELECT id, email, role, is_active, created_at, proxy_label, proxy_mode, execution_mode
           FROM users ORDER BY created_at ASC, id ASC"""
    )


def get_execution_mode(user_id: int) -> str:
    row = fetchone("SELECT execution_mode FROM users WHERE id = ?", (user_id,))
    if not row:
        return EXECUTION_MODE_SERVER
    mode = str(row.get("execution_mode") or EXECUTION_MODE_SERVER).strip().lower()
    if mode not in EXECUTION_MODES:
        return EXECUTION_MODE_SERVER
    return mode


def set_user_execution_mode(user_id: int, mode: str) -> dict[str, Any] | None:
    if mode not in EXECUTION_MODES:
        raise ValueError("execution_mode must be server or local")
    execute("UPDATE users SET execution_mode = ? WHERE id = ?", (mode, user_id))
    return get_user_by_id(user_id)


def get_user_proxy(user_id: int) -> dict[str, Any] | None:
    return fetchone(
        "SELECT id, proxy_ciphertext, proxy_label, proxy_mode FROM users WHERE id = ?",
        (user_id,),
    )


def set_user_proxy(user_id: int, ciphertext: str, label: str, mode: str) -> None:
    execute(
        "UPDATE users SET proxy_ciphertext = ?, proxy_label = ?, proxy_mode = ? WHERE id = ?",
        (ciphertext, label, mode, user_id),
    )


def clear_user_proxy(user_id: int) -> None:
    execute(
        "UPDATE users SET proxy_ciphertext = '', proxy_label = '', proxy_mode = '' WHERE id = ?",
        (user_id,),
    )


def list_proxy_secrets() -> list[dict[str, Any]]:
    return fetchall(
        """SELECT id, email, proxy_ciphertext, proxy_label, proxy_mode
           FROM users ORDER BY id ASC"""
    )


def set_user_active(user_id: int, active: bool) -> dict[str, Any] | None:
    execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))
    return get_user_by_id(user_id)


def get_campaign(user_id: int) -> dict[str, Any]:
    row = fetchone("SELECT * FROM campaigns WHERE user_id = ?", (user_id,))
    if not row:
        now = time.time()
        execute(
            """INSERT INTO campaigns (user_id, people_names_json, message_template, updated_at)
               VALUES (?, '[]', ?, ?)""",
            (user_id, "Hi {name},\n\n", now),
        )
        row = fetchone("SELECT * FROM campaigns WHERE user_id = ?", (user_id,))
    names_raw = (row or {}).get("people_names_json") or "[]"
    try:
        names = json.loads(names_raw)
    except json.JSONDecodeError:
        names = []
    if not isinstance(names, list):
        names = []
    people = [str(n).strip() for n in names if str(n).strip()]
    return {
        "people_names": people,
        "message_template": (row or {}).get("message_template") or "",
        "attachment_path": (row or {}).get("attachment_path"),
        "attachment_name": (row or {}).get("attachment_name"),
    }


def upsert_campaign(
    user_id: int,
    *,
    people_names: list[str] | None = None,
    message_template: str | None = None,
    attachment_path: str | None | object = Ellipsis,
    attachment_name: str | None | object = Ellipsis,
) -> dict[str, Any]:
    current = get_campaign(user_id)
    names = people_names if people_names is not None else current["people_names"]
    template = current["message_template"] if message_template is None else message_template
    att_path = current["attachment_path"] if attachment_path is Ellipsis else attachment_path
    att_name = current["attachment_name"] if attachment_name is Ellipsis else attachment_name
    execute(
        """UPDATE campaigns
           SET people_names_json = ?, message_template = ?, attachment_path = ?,
               attachment_name = ?, updated_at = ?
           WHERE user_id = ?""",
        (json.dumps(names, ensure_ascii=False), template, att_path, att_name, time.time(), user_id),
    )
    return get_campaign(user_id)


def get_linkedin_secret(user_id: int) -> dict[str, Any]:
    row = fetchone("SELECT * FROM linkedin_secrets WHERE user_id = ?", (user_id,))
    if not row:
        execute(
            """INSERT INTO linkedin_secrets (user_id, username, password_ciphertext, updated_at)
               VALUES (?, '', '', ?)""",
            (user_id, time.time()),
        )
        row = fetchone("SELECT * FROM linkedin_secrets WHERE user_id = ?", (user_id,))
    return {
        "username": (row or {}).get("username") or "",
        "password_ciphertext": (row or {}).get("password_ciphertext") or "",
    }


def upsert_linkedin_secret(
    user_id: int,
    *,
    username: str | None = None,
    password_ciphertext: str | None = None,
) -> dict[str, Any]:
    current = get_linkedin_secret(user_id)
    user_name = current["username"] if username is None else username.strip()
    cipher = current["password_ciphertext"] if password_ciphertext is None else password_ciphertext
    execute(
        """UPDATE linkedin_secrets
           SET username = ?, password_ciphertext = ?, updated_at = ?
           WHERE user_id = ?""",
        (user_name, cipher, time.time(), user_id),
    )
    return get_linkedin_secret(user_id)


def clear_linkedin_secret(user_id: int) -> None:
    upsert_linkedin_secret(user_id, username="", password_ciphertext="")


def clear_chrome_profile(user_id: int) -> bool:
    """Delete automation Chrome data for this user. Returns False if the folder could not be removed."""
    root = chrome_dir(user_id)
    if not root.exists():
        ensure_user_dirs(user_id)
        return True
    try:
        shutil.rmtree(root)
    except OSError:
        return False
    ensure_user_dirs(user_id)
    return True


def create_job(user_id: int, people_count: int) -> dict[str, Any]:
    cur = execute(
        """INSERT INTO jobs (user_id, status, created_at, people_count)
           VALUES (?, 'queued', ?, ?)""",
        (user_id, time.time(), people_count),
    )
    job = get_job(int(cur.lastrowid))
    assert job is not None
    return job


def get_job(job_id: int | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    return fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))


def update_job(job_id: int, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_job(job_id)
    allowed = {"status", "started_at", "finished_at", "exit_code", "chrome_pid"}
    parts = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Cannot update job field {key}")
        parts.append(f"{key} = ?")
        values.append(value)
    values.append(job_id)
    execute(f"UPDATE jobs SET {', '.join(parts)} WHERE id = ?", values)
    return get_job(job_id)


def open_job_for_user(user_id: int) -> dict[str, Any] | None:
    placeholders = ",".join("?" * len(JOB_OPEN))
    return fetchone(
        f"""SELECT * FROM jobs
            WHERE user_id = ? AND status IN ({placeholders})
            ORDER BY id DESC LIMIT 1""",
        (user_id, *JOB_OPEN),
    )


def running_job() -> dict[str, Any] | None:
    return fetchone(
        "SELECT * FROM jobs WHERE status IN ('running', 'stopping') ORDER BY id ASC LIMIT 1"
    )


def running_server_job() -> dict[str, Any] | None:
    """VPS Chrome slot: running/stopping job for a ``execution_mode=server`` user."""
    return fetchone(
        """SELECT jobs.* FROM jobs
           JOIN users ON users.id = jobs.user_id
           WHERE jobs.status IN ('running', 'stopping')
             AND users.execution_mode = ?
           ORDER BY jobs.id ASC LIMIT 1""",
        (EXECUTION_MODE_SERVER,),
    )


def next_queued_job() -> dict[str, Any] | None:
    return fetchone("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id ASC LIMIT 1")


def next_queued_server_job() -> dict[str, Any] | None:
    """Next FIFO job for the VPS worker (skips ``local`` users)."""
    return fetchone(
        """SELECT jobs.* FROM jobs
           JOIN users ON users.id = jobs.user_id
           WHERE jobs.status = 'queued'
             AND users.execution_mode = ?
           ORDER BY jobs.id ASC LIMIT 1""",
        (EXECUTION_MODE_SERVER,),
    )


def stale_running_jobs() -> list[dict[str, Any]]:
    return fetchall("SELECT * FROM jobs WHERE status IN ('running', 'stopping') ORDER BY id ASC")


def queue_position(job_id: int) -> int | None:
    job = get_job(job_id)
    if not job or job["status"] != "queued":
        return None
    row = fetchone(
        "SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued' AND id <= ?",
        (job_id,),
    )
    return int((row or {}).get("n") or 0) or None


def latest_job_for_user(user_id: int) -> dict[str, Any] | None:
    return fetchone("SELECT * FROM jobs WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))


def queue_snapshot() -> dict[str, Any]:
    current = running_server_job()
    queued = fetchall(
        """SELECT jobs.id, jobs.user_id, jobs.status, jobs.created_at, jobs.people_count, users.email
           FROM jobs JOIN users ON users.id = jobs.user_id
           WHERE jobs.status = 'queued' AND users.execution_mode = ?
           ORDER BY jobs.id ASC""",
        (EXECUTION_MODE_SERVER,),
    )
    local_running = fetchall(
        """SELECT jobs.id, jobs.user_id, jobs.status, jobs.started_at, jobs.people_count, users.email
           FROM jobs JOIN users ON users.id = jobs.user_id
           WHERE jobs.status IN ('running', 'stopping') AND users.execution_mode = ?
           ORDER BY jobs.id ASC""",
        (EXECUTION_MODE_LOCAL,),
    )
    running = None
    if current:
        owner = get_user_by_id(int(current["user_id"]))
        running = {
            "id": current["id"],
            "user_id": current["user_id"],
            "email": (owner or {}).get("email"),
            "status": current["status"],
            "people_count": current["people_count"],
            "started_at": current["started_at"],
        }
    return {
        "running": running,
        "queued": queued,
        "queue_length": len(queued),
        "local_running": local_running,
    }


def hash_agent_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def create_agent_token(user_id: int, label: str) -> tuple[str, dict[str, Any]]:
    """Insert agent_tokens row; return (plaintext token, row dict)."""
    plaintext = secrets.token_urlsafe(32)
    token_hash = hash_agent_token(plaintext)
    now = time.time()
    label = (label or "").strip()[:120] or "Desktop"
    with _lock:
        cur = execute(
            """INSERT INTO agent_tokens (user_id, label, token_hash, created_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, label, token_hash, now),
        )
        token_id = int(cur.lastrowid)
    row = get_agent_token(token_id)
    assert row is not None
    return plaintext, row


def get_agent_token(token_id: int) -> dict[str, Any] | None:
    return fetchone("SELECT * FROM agent_tokens WHERE id = ?", (token_id,))


def get_agent_token_by_hash(token_hash: str) -> dict[str, Any] | None:
    return fetchone(
        """SELECT * FROM agent_tokens
           WHERE token_hash = ? AND revoked_at IS NULL""",
        (token_hash,),
    )


def list_agent_tokens(user_id: int) -> list[dict[str, Any]]:
    return fetchall(
        """SELECT id, user_id, label, created_at, last_seen_at, revoked_at
           FROM agent_tokens
           WHERE user_id = ?
           ORDER BY created_at DESC, id DESC""",
        (user_id,),
    )


def revoke_agent_token(user_id: int, token_id: int) -> dict[str, Any] | None:
    row = fetchone(
        "SELECT * FROM agent_tokens WHERE id = ? AND user_id = ?",
        (token_id, user_id),
    )
    if not row or row.get("revoked_at") is not None:
        return None
    now = time.time()
    execute("UPDATE agent_tokens SET revoked_at = ? WHERE id = ?", (now, token_id))
    return get_agent_token(token_id)


def touch_agent_token_last_seen(token_id: int, when: float | None = None) -> None:
    execute(
        "UPDATE agent_tokens SET last_seen_at = ? WHERE id = ? AND revoked_at IS NULL",
        (when if when is not None else time.time(), token_id),
    )


def user_agent_online(user_id: int, within_sec: float | None = None) -> bool:
    """True if the user has a non-revoked agent token with recent heartbeat/work poll."""
    window = AGENT_ONLINE_WINDOW_SEC if within_sec is None else within_sec
    cutoff = time.time() - window
    row = fetchone(
        """SELECT 1 FROM agent_tokens
           WHERE user_id = ? AND revoked_at IS NULL
             AND last_seen_at IS NOT NULL AND last_seen_at >= ?
           LIMIT 1""",
        (user_id, cutoff),
    )
    return row is not None


def get_desktop_feedback(user_id: int) -> dict[str, Any]:
    mode = get_execution_mode(user_id)
    return {
        "execution_mode": mode,
        "agent_online": user_agent_online(user_id) if mode == EXECUTION_MODE_LOCAL else False,
    }
