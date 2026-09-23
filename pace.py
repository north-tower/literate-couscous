# -*- coding: utf-8 -*-
"""Sending pace for LinkedIn campaigns.

LinkedIn does not publish a quota for ordinary messages. Weekly invitation
limits are a separate control and vary by account. These caps only slow a
long list so one run cannot fire every message at once. They do not make
automation compliant with LinkedIn's User Agreement, and they do not
guarantee an account stays unrestricted.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

RETENTION_DAYS = 90
MAX_IN_RUN_WAIT = 20 * 60

_PRESETS = {
    "careful": {
        "label": "Careful",
        "min_gap": 120,
        "max_gap": 240,
        "hourly": 8,
        "daily": 20,
        "weekly": 80,
    },
    "established": {
        "label": "Established account",
        "min_gap": 90,
        "max_gap": 180,
        "hourly": 12,
        "daily": 35,
        "weekly": 120,
    },
}


@dataclass(frozen=True)
class Limits:
    preset: str
    label: str
    min_gap: int
    max_gap: int
    hourly: int
    daily: int
    weekly: int


@dataclass(frozen=True)
class Decision:
    action: str
    seconds: float = 0.0
    outcome: str = ""
    log_message: str = ""
    note: str = ""


def normalize(value: object) -> str:
    key = str(value or "").strip().lower()
    if key in _PRESETS:
        return key
    return "careful"


def limits_for(value: object) -> Limits:
    key = normalize(value)
    raw = _PRESETS[key]
    return Limits(
        preset=key,
        label=str(raw["label"]),
        min_gap=int(raw["min_gap"]),
        max_gap=int(raw["max_gap"]),
        hourly=int(raw["hourly"]),
        daily=int(raw["daily"]),
        weekly=int(raw["weekly"]),
    )


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


def _day_start(now: float) -> float:
    dt = datetime.fromtimestamp(now)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _week_start(now: float) -> float:
    dt = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return (dt - timedelta(days=dt.weekday())).timestamp()


def _next_midnight(now: float) -> float:
    dt = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return (dt + timedelta(days=1)).timestamp()


def _next_week_start(now: float) -> float:
    dt = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return (dt + timedelta(days=7 - dt.weekday())).timestamp()


def counts(sends: list[dict], now: float) -> tuple[int, int, int]:
    hour_cut = now - 3600
    day_cut = _day_start(now)
    week_cut = _week_start(now)
    hour_n = day_n = week_n = 0
    for item in sends:
        at = item["at"]
        if at >= hour_cut:
            hour_n += 1
        if at >= day_cut:
            day_n += 1
        if at >= week_cut:
            week_n += 1
    return hour_n, day_n, week_n


def decide(sends: list[dict], now: float, limits: Limits) -> Decision:
    hour_n, day_n, week_n = counts(sends, now)
    if week_n >= limits.weekly:
        resume = _next_week_start(now)
        return Decision(
            action="stop",
            outcome="weekly",
            log_message=(
                f"Weekly pace reached ({week_n}/{limits.weekly} since Monday). "
                f"Start again after {format_local(resume)}. "
                "People already messaged are skipped."
            ),
            note="Weekly pace reached. Start again next week — already-messaged people are skipped.",
        )
    if day_n >= limits.daily:
        resume = _next_midnight(now)
        return Decision(
            action="stop",
            outcome="daily",
            log_message=(
                f"Daily pace reached ({day_n}/{limits.daily} since midnight). "
                f"Start again after {format_local(resume)}. "
                "People already messaged are skipped."
            ),
            note="Daily pace reached. Start again tomorrow — already-messaged people are skipped.",
        )
    if hour_n >= limits.hourly:
        oldest = min(item["at"] for item in sends if item["at"] >= now - 3600)
        resume = oldest + 3600 + 1
        wait = max(0.0, resume - now)
        if wait > MAX_IN_RUN_WAIT:
            return Decision(
                action="stop",
                outcome="hourly",
                log_message=(
                    f"Hourly pace reached ({hour_n}/{limits.hourly} in the last hour). "
                    f"Next message is allowed after {format_local(resume)}. "
                    "Chrome will close so the session is not left waiting. "
                    "People already messaged are skipped."
                ),
                note="Hourly pace reached. Start again in a little while — already-messaged people are skipped.",
            )
        return Decision(
            action="wait",
            seconds=wait,
            log_message=f"Hourly pace reached ({hour_n}/{limits.hourly}). Waiting {int(wait)}s.",
        )
    return Decision(action="send")


def wait_after_send(sends: list[dict], now: float, limits: Limits, gap: float | None = None) -> Decision:
    """Gap before the next person, or a stop if a cap is already full."""
    decision = decide(sends, now, limits)
    if decision.action == "stop":
        return decision
    if gap is None:
        gap = random.uniform(limits.min_gap, limits.max_gap)
    seconds = max(float(gap), decision.seconds)
    if decision.action == "wait" and decision.log_message:
        message = f"{decision.log_message.rstrip('.')}. Then continuing."
        if seconds > decision.seconds:
            message = f"Waiting {int(seconds)}s before the next message (pace)."
    else:
        message = f"Waiting {int(seconds)}s before the next message."
    return Decision(action="wait", seconds=seconds, log_message=message)


def public_usage(user_dir: Path, preset: object, now: float | None = None) -> dict:
    limits = limits_for(preset)
    moment = time.time() if now is None else now
    sends = load_sends(ledger_path(user_dir))
    hour_n, day_n, week_n = counts(sends, moment)
    cutoff = moment - RETENTION_DAYS * 86400
    recent = [item["name"].strip().casefold() for item in sends if item["at"] >= cutoff and item["name"].strip()]
    return {
        "preset": limits.preset,
        "label": limits.label,
        "today": day_n,
        "today_limit": limits.daily,
        "week": week_n,
        "week_limit": limits.weekly,
        "hour": hour_n,
        "hour_limit": limits.hourly,
        "min_gap": limits.min_gap,
        "max_gap": limits.max_gap,
        "recent_names": recent,
    }
