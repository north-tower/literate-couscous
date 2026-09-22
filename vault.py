# -*- coding: utf-8 -*-
"""Fernet encryption for LinkedIn passwords. Never log or return plaintext."""
from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache
from cryptography.fernet import Fernet, InvalidToken

from db import DATA_DIR

KEY_FILE = DATA_DIR / "fernet.key"


def _key_from_secret(raw: str) -> bytes:
    raw = raw.strip()
    if not raw:
        raise ValueError("empty secret")
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("utf-8"))
        if len(decoded) == 32:
            return raw.encode("utf-8") if isinstance(raw, str) else raw
    except Exception:
        pass
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    env = (os.environ.get("SENDLINE_SECRET_KEY") or "").strip()
    if env:
        key = _key_from_secret(env)
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if KEY_FILE.is_file():
            stored = KEY_FILE.read_text(encoding="utf-8").strip()
            key = _key_from_secret(stored) if stored else Fernet.generate_key()
        else:
            key = Fernet.generate_key()
            KEY_FILE.write_text(key.decode("utf-8") if isinstance(key, bytes) else str(key), encoding="utf-8")
            try:
                os.chmod(KEY_FILE, 0o600)
            except OSError:
                pass
            print("[sendline] Generated data/fernet.key — set SENDLINE_SECRET_KEY in production.")
        if isinstance(key, str):
            key = key.encode("utf-8")
    return Fernet(key)


def encrypt_password(plaintext: str) -> str:
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_password(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt LinkedIn password. Check SENDLINE_SECRET_KEY.") from exc
