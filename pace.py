# -*- coding: utf-8 -*-
"""Send ledger (dedupe) and human-like pauses between LinkedIn messages."""
from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path

RETENTION_DAYS = 90

# Typing speed (~35–55 WPM feel)
_CHAR_DELAY_MIN = 0.045
_CHAR_DELAY_MAX = 0.14
_PUNCT_PAUSE_MIN = 0.25
_PUNCT_PAUSE_MAX = 0.75
_WORD_PAUSE_CHANCE = 0.12
_WORD_PAUSE_MIN = 0.08
_WORD_PAUSE_MAX = 0.35

# After one send completes, before opening the next new message
_BETWEEN_PEOPLE_MIN = 16.0
_BETWEEN_PEOPLE_MAX = 42.0


def ledger_path(user_dir: Path) -> Path:
    return Path(user_dir) / "send_ledger.json"


def format_local(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%a %d %b, %H:%M")


def load_sends(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("sends") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    sends: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        at = item.get("at")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            stamp = float(at)
        except (TypeError, ValueError):
            continue
        sends.append({"name": name.strip(), "at": stamp})
    return sends


def save_sends(path: Path, sends: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sends": [{"name": item["name"], "at": item["at"]} for item in sends]}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_send(path: Path, name: str, at: float | None = None) -> list[dict]:
    now = time.time() if at is None else at
    cutoff = now - RETENTION_DAYS * 86400
    sends = [item for item in load_sends(path) if item["at"] >= cutoff]
    sends.append({"name": name.strip(), "at": now})
    save_sends(path, sends)
    return sends


def recent_send_at(sends: list[dict], name: str, now: float | None = None) -> float | None:
    key = name.strip().casefold()
    if not key:
        return None
    moment = time.time() if now is None else now
    cutoff = moment - RETENTION_DAYS * 86400
    latest: float | None = None
    for item in sends:
        if item["name"].strip().casefold() != key or item["at"] < cutoff:
            continue
        if latest is None or item["at"] > latest:
            latest = item["at"]
    return latest


def public_recent(user_dir: Path) -> dict:
    moment = time.time()
    sends = load_sends(ledger_path(user_dir))
    cutoff = moment - RETENTION_DAYS * 86400
    recent = [item["name"].strip().casefold() for item in sends if item["at"] >= cutoff and item["name"].strip()]
    return {"recent_names": recent, "retention_days": RETENTION_DAYS}


def char_typing_delay(ch: str) -> float:
    delay = random.uniform(_CHAR_DELAY_MIN, _CHAR_DELAY_MAX)
    if ch in ".,!?;:":
        delay += random.uniform(_PUNCT_PAUSE_MIN, _PUNCT_PAUSE_MAX)
    elif ch == "\n":
        delay += random.uniform(_PUNCT_PAUSE_MIN, _PUNCT_PAUSE_MAX * 1.2)
    elif ch == " " and random.random() < _WORD_PAUSE_CHANCE:
        delay += random.uniform(_WORD_PAUSE_MIN, _WORD_PAUSE_MAX)
    return delay


def pause_between_people() -> tuple[float, str]:
    seconds = random.uniform(_BETWEEN_PEOPLE_MIN, _BETWEEN_PEOPLE_MAX)
    return seconds, f"Pausing {int(seconds)}s before the next person (natural pace)."
