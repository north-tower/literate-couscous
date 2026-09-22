# -*- coding: utf-8 -*-
"""Sendline login sessions, password hashing, and request guards."""
from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict
from functools import wraps
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from flask import jsonify, redirect, request, session

import db

_hasher = PasswordHasher()
_login_attempts: dict[str, list[float]] = defaultdict(list)
LOGIN_WINDOW_SEC = 15 * 60
LOGIN_MAX_ATTEMPTS = 8
FLASK_SECRET_FILE = db.DATA_DIR / "flask_secret.txt"


def load_flask_secret() -> str:
    env = _env("SECRET_KEY")
    if env:
        return env
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if FLASK_SECRET_FILE.is_file():
        stored = FLASK_SECRET_FILE.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    value = secrets.token_hex(32)
    FLASK_SECRET_FILE.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(FLASK_SECRET_FILE, 0o600)
    except OSError:
        pass
    print("[sendline] Generated data/flask_secret.txt — set SECRET_KEY in production.")
    return value


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHash, ValueError):
        return False


def _env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def bootstrap_admin() -> None:
    if db.user_count() > 0:
        return
    email = _env("SENDLINE_ADMIN_EMAIL").lower()
    password = _env("SENDLINE_ADMIN_PASSWORD")
    if not email or not password:
        print(
            "[sendline] No users yet. Set SENDLINE_ADMIN_EMAIL and SENDLINE_ADMIN_PASSWORD "
            "then restart to create the first admin."
        )
        return
    db.create_user(email, hash_password(password), role="admin")
    print(f"[sendline] Created first admin: {email}")


def current_user() -> dict | None:
    user = db.get_user_by_id(session.get("user_id"))
    if not user or not user.get("is_active"):
        return None
    return user


def login_user(user: dict) -> None:
    session.clear()
    session.permanent = True
    session["user_id"] = int(user["id"])
    session["csrf"] = secrets.token_hex(16)


def logout_user() -> None:
    session.clear()


def wants_json() -> bool:
    if request.path.startswith("/api/"):
        return True
    accept = request.headers.get("Accept") or ""
    return "application/json" in accept and "text/html" not in accept


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            session.pop("user_id", None)
            if wants_json():
                return jsonify({"ok": False, "error": "Please log in."}), 401
            return redirect("/login")
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            if wants_json():
                return jsonify({"ok": False, "error": "Please log in."}), 401
            return redirect("/login")
        if user.get("role") != "admin":
            if wants_json():
                return jsonify({"ok": False, "error": "Admin only."}), 403
            return redirect("/")
        return view(*args, **kwargs)

    return wrapped


def public_user(user: dict) -> dict:
    return {
        "id": int(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "is_active": bool(user["is_active"]),
    }


def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def login_allowed(ip: str) -> bool:
    now = time.time()
    stamps = [t for t in _login_attempts[ip] if now - t < LOGIN_WINDOW_SEC]
    _login_attempts[ip] = stamps
    return len(stamps) < LOGIN_MAX_ATTEMPTS


def record_login_attempt(ip: str) -> None:
    _login_attempts[ip].append(time.time())


def origin_allowed() -> bool:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    origin = request.headers.get("Origin")
    if not origin:
        return True
    host = (request.host or "").split(":")[0]
    origin_host = urlparse(origin).hostname
    if not origin_host:
        return False
    if origin_host == host:
        return True
    if host in ("127.0.0.1", "localhost") and origin_host in ("127.0.0.1", "localhost"):
        return True
    return False
