# -*- coding: utf-8 -*-
"""Sendline — multi-user UI, auth, and one-Chrome job queue."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import auth
import db
import jobs
import pace
import vault

STATIC_DIR = ROOT / "static"
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
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = MAX_ATTACHMENT_BYTES
app.config["SECRET_KEY"] = auth.load_flask_secret()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SENDLINE_SECURE_COOKIES", "0") == "1"
app.config["SESSION_COOKIE_NAME"] = "sendline_session"


@app.before_request
def _guard_origin():
    if not auth.origin_allowed():
        return jsonify({"ok": False, "error": "Invalid origin."}), 403


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


def _is_managed_attachment(user_id: int, path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = db.attachments_dir(user_id).resolve()
        if hasattr(resolved, "is_relative_to"):
            return resolved.is_relative_to(root)
        return os.path.commonpath([str(resolved), str(root)]) == str(root)
    except (OSError, ValueError):
        return False


def _remove_managed_attachment(user_id: int, path_str: str | None) -> None:
    if not path_str:
        return
    path = Path(path_str)
    if not _is_managed_attachment(user_id, path):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _pace_note(job: dict | None) -> str:
    if not job or job.get("status") != "paused":
        return ""
    path = db.job_dir(int(job["user_id"]), int(job["id"])) / "pace_result.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return "Pace limit reached. Start again later — already-messaged people are skipped."
    note = data.get("note") if isinstance(data, dict) else ""
    if isinstance(note, str) and note.strip():
        return note.strip()
    return "Pace limit reached. Start again later — already-messaged people are skipped."


def _public_config(user_id: int) -> dict:
    campaign = db.get_campaign(user_id)
    secret = db.get_linkedin_secret(user_id)
    username = secret.get("username") or ""
    connected = bool(username and (secret.get("password_ciphertext") or ""))
    att_path = campaign.get("attachment_path")
    att_name = campaign.get("attachment_name")
    if att_path and not Path(str(att_path)).is_file():
        att_path = None
        att_name = None
    return {
        "people_names": campaign["people_names"],
        "message_template": campaign["message_template"],
        "linkedin_username": username,
        "linkedin_connected": connected,
        "attachment_path": att_path,
        "attachment_name": att_name,
        "pace_preset": campaign.get("pace_preset") or "careful",
        "pace": pace.public_usage(db.user_dir(user_id), campaign.get("pace_preset")),
    }


def _save_campaign_from_body(user_id: int, body: dict) -> dict:
    names = body.get("people_names")
    if isinstance(names, str):
        names = [n.strip() for n in names.splitlines() if n.strip()]
    elif isinstance(names, list):
        names = [str(n).strip() for n in names if str(n).strip()]
    else:
        names = None

    template = body.get("message_template")
    if template is not None:
        template = str(template).replace("\r\n", "\n")

    preset = body.get("pace_preset")
    db.upsert_campaign(
        user_id,
        people_names=names,
        message_template=template,
        pace_preset=None if preset is None else str(preset),
    )

    username = body.get("linkedin_username")
    if username is not None:
        db.upsert_linkedin_secret(user_id, username=_clean_username(username))

    password = _clean_password(body.get("linkedin_password"))
    if password:
        db.upsert_linkedin_secret(user_id, password_ciphertext=vault.encrypt_password(password))

    return _public_config(user_id)


@app.get("/")
def index():
    if not auth.current_user():
        return redirect("/welcome")
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/welcome")
def welcome_page():
    return send_from_directory(STATIC_DIR, "welcome.html")


@app.get("/login")
def login_page():
    if auth.current_user():
        return redirect("/")
    return send_from_directory(STATIC_DIR, "login.html")


@app.get("/admin")
@auth.admin_required
def admin_page():
    return send_from_directory(STATIC_DIR, "admin.html")


@app.get("/api/people-template")
@auth.login_required
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


@app.post("/api/login")
def api_login():
    ip = auth.client_ip()
    if not auth.login_allowed(ip):
        return jsonify({"ok": False, "error": "Too many login attempts. Try again in a few minutes."}), 429
    body = request.get_json(force=True, silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    auth.record_login_attempt(ip)
    user = db.get_user_by_email(email) if email else None
    if not user or not user.get("is_active") or not auth.verify_password(user["password_hash"], password):
        time.sleep(0.25)
        return jsonify({"ok": False, "error": "Email or password is incorrect."}), 401
    auth.login_user(user)
    return jsonify({"ok": True, "user": auth.public_user(user)})


@app.post("/api/logout")
@auth.login_required
def api_logout():
    auth.logout_user()
    return jsonify({"ok": True})


@app.get("/api/me")
@auth.login_required
def api_me():
    user = auth.current_user()
    assert user is not None
    return jsonify({"ok": True, "user": auth.public_user(user)})


@app.post("/api/attachment")
@auth.login_required
def api_upload_attachment():
    user = auth.current_user()
    assert user is not None
    user_id = int(user["id"])
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "Choose a file to attach."}), 400

    original_name = _safe_attachment_name(uploaded.filename)
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_ATTACHMENT_EXT:
        return jsonify(
            {"ok": False, "error": "Please attach a PDF, Word, PowerPoint, Excel, or image file."}
        ), 400

    dest_dir = db.attachments_dir(user_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / original_name
    current = db.get_campaign(user_id)
    old_path = current.get("attachment_path")
    if old_path and Path(old_path).resolve() != dest.resolve():
        _remove_managed_attachment(user_id, old_path)

    uploaded.save(str(dest))
    saved = db.upsert_campaign(
        user_id,
        attachment_path=str(dest),
        attachment_name=original_name,
    )
    return jsonify(
        {
            "ok": True,
            "attachment_path": saved["attachment_path"],
            "attachment_name": saved["attachment_name"],
        }
    )


@app.delete("/api/attachment")
@auth.login_required
def api_clear_attachment():
    user = auth.current_user()
    assert user is not None
    user_id = int(user["id"])
    current = db.get_campaign(user_id)
    _remove_managed_attachment(user_id, current.get("attachment_path"))
    db.upsert_campaign(user_id, attachment_path=None, attachment_name=None)
    return jsonify({"ok": True, "attachment_path": None, "attachment_name": None})


@app.get("/api/config")
@auth.login_required
def api_get_config():
    user = auth.current_user()
    assert user is not None
    return jsonify(_public_config(int(user["id"])))


@app.post("/api/config")
@auth.login_required
def api_save_config():
    user = auth.current_user()
    assert user is not None
    body = request.get_json(force=True, silent=True) or {}
    saved = _save_campaign_from_body(int(user["id"]), body)
    return jsonify({"ok": True, "config": saved, "count": len(saved["people_names"])})


@app.get("/api/status")
@auth.login_required
def api_status():
    user = auth.current_user()
    assert user is not None
    return jsonify(jobs.status_for_user(int(user["id"]), is_admin=user.get("role") == "admin"))


@app.get("/api/logs")
@auth.login_required
def api_logs():
    user = auth.current_user()
    assert user is not None
    after = request.args.get("after", "0")
    try:
        idx = max(0, int(after))
    except ValueError:
        idx = 0
    lines, next_idx, job = jobs.logs_for_user(int(user["id"]), idx)
    status = (job or {}).get("status")
    running = status in ("running", "stopping")
    return jsonify(
        {
            "lines": lines,
            "next": next_idx,
            "running": running,
            "job_id": (job or {}).get("id"),
            "job_status": status or "idle",
            "queue_position": db.queue_position(int(job["id"])) if job else None,
            "is_mine": bool(job and job.get("user_id") == user["id"] and status in db.JOB_OPEN),
            "exit_code": None if running or status in db.JOB_OPEN else (job or {}).get("exit_code"),
            "pace_note": _pace_note(job),
            "pace": pace.public_usage(db.user_dir(int(user["id"])), db.get_campaign(int(user["id"])).get("pace_preset")),
        }
    )


@app.post("/api/run")
@auth.login_required
def api_run():
    user = auth.current_user()
    assert user is not None
    user_id = int(user["id"])
    body = request.get_json(force=True, silent=True) or {}
    saved = _save_campaign_from_body(user_id, body if body else {})

    if not saved["people_names"]:
        return jsonify({"ok": False, "error": "Add at least one name before launching."}), 400
    if "{name}" not in saved["message_template"]:
        return jsonify({"ok": False, "error": "Message must include {name} for personalization."}), 400
    if not saved.get("linkedin_username") or not saved.get("linkedin_connected"):
        return jsonify({"ok": False, "error": "Add your LinkedIn username and password before launching."}), 400

    try:
        job = jobs.enqueue(user_id, len(saved["people_names"]))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409

    fresh = db.get_job(int(job["id"])) or job
    queued = fresh["status"] == "queued"
    payload = {
        "ok": True,
        "count": len(saved["people_names"]),
        "job_id": fresh["id"],
        "job_status": fresh["status"],
        "queue_position": db.queue_position(int(fresh["id"])),
    }
    if queued:
        return jsonify(payload), 202
    return jsonify(payload)


@app.post("/api/stop")
@auth.login_required
def api_stop():
    user = auth.current_user()
    assert user is not None
    try:
        job = jobs.request_stop(int(user["id"]))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "job_status": job.get("status"), "job_id": job.get("id")})


@app.get("/api/admin/users")
@auth.admin_required
def api_admin_users():
    users = []
    for row in db.list_users():
        users.append(
            {
                "id": row["id"],
                "email": row["email"],
                "role": row["role"],
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
            }
        )
    return jsonify({"ok": True, "users": users, "queue": db.queue_snapshot()})


@app.post("/api/admin/users")
@auth.admin_required
def api_admin_create_user():
    body = request.get_json(force=True, silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    role = str(body.get("role") or "operator").strip().lower()
    if role not in ("operator", "admin"):
        return jsonify({"ok": False, "error": "Role must be operator or admin."}), 400
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Enter a valid email."}), 400
    if len(password) < 8:
        return jsonify({"ok": False, "error": "Password must be at least 8 characters."}), 400
    if db.get_user_by_email(email):
        return jsonify({"ok": False, "error": "That email already has an account."}), 409
    user = db.create_user(email, auth.hash_password(password), role=role)
    return jsonify({"ok": True, "user": auth.public_user(user)}), 201


@app.post("/api/admin/users/<int:user_id>/disable")
@auth.admin_required
def api_admin_disable(user_id: int):
    actor = auth.current_user()
    assert actor is not None
    target = db.get_user_by_id(user_id)
    if not target:
        return jsonify({"ok": False, "error": "User not found."}), 404
    if int(target["id"]) == int(actor["id"]):
        return jsonify({"ok": False, "error": "You cannot disable your own account."}), 400
    if target["role"] == "admin" and db.active_admin_count() <= 1:
        return jsonify({"ok": False, "error": "Keep at least one active admin."}), 400
    updated = db.set_user_active(user_id, False)
    return jsonify({"ok": True, "user": auth.public_user(updated or target)})


@app.post("/api/admin/users/<int:user_id>/enable")
@auth.admin_required
def api_admin_enable(user_id: int):
    target = db.get_user_by_id(user_id)
    if not target:
        return jsonify({"ok": False, "error": "User not found."}), 404
    updated = db.set_user_active(user_id, True)
    return jsonify({"ok": True, "user": auth.public_user(updated or target)})


def setup() -> None:
    STATIC_DIR.mkdir(exist_ok=True)
    db.init_db()
    auth.bootstrap_admin()
    jobs.recover_and_start()


setup()


if __name__ == "__main__":
    print("Sendline UI -> http://127.0.0.1:5055")
    app.run(host="127.0.0.1", port=5055, debug=False, threaded=True)
