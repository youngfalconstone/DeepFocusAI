"""JSON-backed records: current session, history, settings, presets.

All writes are atomic (write temp, then os.replace) so a crash mid-write can
never corrupt the record the restore step depends on.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import paths

ISO = "%Y-%m-%dT%H:%M:%S"


def now_iso() -> str:
    return datetime.now().strftime(ISO)


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s[:19], ISO)


def _load(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path: Path, data: Any) -> None:
    paths.ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- session ---
def new_session(goal: str, restore_at: datetime, hostnames: list[str], processes: list[str],
                files: list[str], services: list[str], options: dict, schedule: dict | None = None) -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "created": now_iso(),
        "started": None,
        "restore_at": restore_at.strftime(ISO),
        "schedule": schedule or {"kind": "once"},
        "paused": False,
        "schedule_log": [],
        "goal": goal.strip(),
        "services": services,
        "hostnames": hostnames,
        "processes": processes,
        "files_requested": files,
        "hidden_files": [],
        "hosts_backup": None,
        "doh_backup": [],
        "notif_backup": None,
        "enforce_task_registered": False,
        "enforce_fixes": [],
        "options": options,
        "status": "planned",
        "ended": None,
        "end_reason": None,
        "kills": 0,
        "notes": "",
    }


def load_session() -> dict | None:
    return _load(paths.SESSION_FILE, None)


def save_session(session: dict) -> None:
    _save(paths.SESSION_FILE, session)


def clear_session() -> None:
    try:
        os.remove(paths.SESSION_FILE)
    except FileNotFoundError:
        pass


def session_is_active(session: dict | None) -> bool:
    return bool(session) and session.get("status") == "active"


def session_expired(session: dict | None, grace_seconds: int = 0) -> bool:
    if not session_is_active(session):
        return False
    return datetime.now() >= parse_iso(session["restore_at"]) + timedelta(seconds=grace_seconds)


def seconds_remaining(session: dict) -> int:
    return max(0, int((parse_iso(session["restore_at"]) - datetime.now()).total_seconds()))


def planned_duration_seconds(session: dict) -> int:
    start = parse_iso(session.get("started") or session["created"])
    return max(1, int((parse_iso(session["restore_at"]) - start).total_seconds()))


# ---------------------------------------------------------------- history ---
def load_history() -> list[dict]:
    return _load(paths.HISTORY_FILE, [])


def append_history(entry: dict) -> None:
    hist = load_history()
    hist.append(entry)
    _save(paths.HISTORY_FILE, hist[-1000:])


def update_history_entry(session_id: str, **fields) -> None:
    hist = load_history()
    for e in hist:
        if e.get("id") == session_id:
            e.update(fields)
    _save(paths.HISTORY_FILE, hist)


def stats(history: list[dict]) -> dict:
    """Aggregate focus time, completion rate and the current daily streak."""
    total_sec = 0
    completed = 0
    early = 0
    days: set[str] = set()
    per_day: dict[str, int] = {}
    for e in history:
        if e.get("status") != "ended" or not e.get("started"):
            continue
        start = parse_iso(e["started"])
        end = parse_iso(e.get("ended") or e["restore_at"])
        sec = max(0, int((end - start).total_seconds()))
        total_sec += sec
        day = start.strftime("%Y-%m-%d")
        days.add(day)
        per_day[day] = per_day.get(day, 0) + sec
        if e.get("end_reason") == "early":
            early += 1
        else:
            completed += 1
    # streak: consecutive days ending today or yesterday
    streak = 0
    d = datetime.now().date()
    if d.strftime("%Y-%m-%d") not in days:
        d = d - timedelta(days=1)
    while d.strftime("%Y-%m-%d") in days:
        streak += 1
        d = d - timedelta(days=1)
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "total_seconds": total_sec,
        "sessions": completed + early,
        "completed": completed,
        "early": early,
        "streak_days": streak,
        "today_seconds": per_day.get(today, 0),
        "per_day": per_day,
    }


# --------------------------------------------------------------- settings ---
DEFAULT_SETTINGS = {
    "theme": "dark",
    "system_accent": False,
    "doh_off_default": True,
    "lock_files_default": False,
    "block_notifications_default": False,
    "focus_sound": "off",
    "extended_subdomains": True,
    "strict_default": False,
    "end_delay_seconds": 30,
    "pomodoro_default": False,
    "pomodoro_work_min": 25,
    "pomodoro_break_min": 5,
    "close_browsers_default": False,
    "notifications": True,
    "process_watch_default": True,
    "ipv6": True,
    "default_duration_min": 60,
    # AI Block: provider settings only; the API key lives in the Windows Credential Manager
    "ai_provider": "anthropic",
    "ai_model": "",
    "ai_base_url": "",
    # Assistant chat: include the current session, today's stats and saved lists in the system prompt
    "chat_share_context": True,
}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(_load(paths.SETTINGS_FILE, {}))
    return s


def save_settings(settings: dict) -> None:
    _save(paths.SETTINGS_FILE, settings)


# ---------------------------------------------------------------- presets ---
def load_presets() -> dict[str, dict]:
    return _load(paths.PRESETS_FILE, {})


def save_presets(presets: dict[str, dict]) -> None:
    _save(paths.PRESETS_FILE, presets)


# -------------------------------------------------------------- AI block lists ---
def load_ai_blocks() -> dict[str, dict]:
    """Saved AI block lists keyed by slug (see aiblock.to_record)."""
    data = _load(paths.AI_BLOCKS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_ai_blocks(lists: dict[str, dict]) -> None:
    _save(paths.AI_BLOCKS_FILE, lists)


# ----------------------------------------------------------------------- chat ---
CHAT_KEEP = 60  # messages kept on disk


def load_chat() -> list[dict]:
    """Assistant conversation: [{"role": "user"|"assistant", "content": str, "time": iso}, ...]."""
    data = _load(paths.CHAT_FILE, [])
    return [m for m in data if isinstance(m, dict) and m.get("role") in ("user", "assistant")] \
        if isinstance(data, list) else []


def save_chat(messages: list[dict]) -> None:
    valid = [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    _save(paths.CHAT_FILE, valid[-CHAT_KEEP:])


# -------------------------------------------------------------------- log ---
def log(msg: str) -> None:
    try:
        paths.ensure_dirs()
        try:
            if paths.LOG_FILE.stat().st_size > paths.MAX_LOG_BYTES:
                os.replace(paths.LOG_FILE, paths.LOG_FILE.with_suffix(".log.1"))
        except FileNotFoundError:
            pass
        with open(paths.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {msg}\n")
    except OSError:
        pass


def prune_backups(keep: int = paths.MAX_BACKUPS) -> int:
    """Delete all but the newest `keep` hosts backups. Returns how many were removed."""
    try:
        files = sorted(paths.BACKUP_DIR.glob("hosts_*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    removed = 0
    for p in files[keep:]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed
