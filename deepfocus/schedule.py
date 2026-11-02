"""Session schedules: one-shot (timer or long-term) and weekly.

A weekly schedule keeps the blocks on all week and lifts them only inside
"free windows", one optional window per weekday given in minutes of the day:

    {"kind": "weekly", "weeks": 4,
     "free": {"4": [900, 1440], "5": [0, 1440], "6": [0, 1440]}}

That example is the default: free from Friday 15:00, all day Saturday and all
day Sunday, blocked the rest of the time, repeating for four weeks. Weekdays
are Monday = 0 ... Sunday = 6 (JSON keys are strings). Consecutive windows
that touch at midnight behave as one long free window, so the default is a
single break from Friday 15:00 to Monday 00:00.

The elevated helper evaluates `is_blocking` every minute and switches the
blocks on or off accordingly; the session's `restore_at` is the moment the
whole schedule ends for good.
"""
from __future__ import annotations

from datetime import datetime, timedelta

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MINUTES_PER_DAY = 1440
MAX_WEEKS = 52

DEFAULT_FREE = {"4": [900, MINUTES_PER_DAY], "5": [0, MINUTES_PER_DAY], "6": [0, MINUTES_PER_DAY]}


def once() -> dict:
    return {"kind": "once"}


def default_weekly(weeks: int = 1) -> dict:
    return {"kind": "weekly", "weeks": weeks, "free": {k: list(v) for k, v in DEFAULT_FREE.items()}}


def normalize(sched: dict | None) -> dict:
    """Return a clean copy. Anything that is not a valid weekly schedule becomes a one-shot."""
    if not isinstance(sched, dict) or sched.get("kind") != "weekly":
        kind = sched.get("kind") if isinstance(sched, dict) else None
        return {"kind": "long" if kind == "long" else "once"}
    free: dict[str, list[int]] = {}
    for k, v in (sched.get("free") or {}).items():
        try:
            day = int(k)
            a, b = int(v[0]), int(v[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not 0 <= day <= 6:
            continue
        a = max(0, min(MINUTES_PER_DAY, a))
        b = max(0, min(MINUTES_PER_DAY, b))
        if b > a:
            free[str(day)] = [a, b]
    try:
        weeks = max(1, min(MAX_WEEKS, int(sched.get("weeks", 1))))
    except (TypeError, ValueError):
        weeks = 1
    return {"kind": "weekly", "weeks": weeks, "free": free}


def is_weekly(sched: dict | None) -> bool:
    return isinstance(sched, dict) and sched.get("kind") == "weekly"


def has_blocked_time(sched: dict) -> bool:
    """False when every day is free all day, which would be a schedule that never blocks."""
    if not is_weekly(sched):
        return True
    return any(sched["free"].get(str(d), [0, 0]) != [0, MINUTES_PER_DAY] for d in range(7))


def is_blocking(sched: dict | None, now: datetime | None = None) -> bool:
    if not is_weekly(sched):
        return True
    now = now or datetime.now()
    win = sched["free"].get(str(now.weekday()))
    if not win:
        return True
    minute = now.hour * 60 + now.minute
    return not (win[0] <= minute < win[1])


def _candidates(sched: dict, origin: datetime, days: range) -> list[datetime]:
    base = origin.replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[datetime] = []
    for off in days:
        day = base + timedelta(days=off)
        out.append(day)
        win = sched["free"].get(str(day.weekday()))
        if win:
            out.append(day + timedelta(minutes=win[0]))
            out.append(day + timedelta(minutes=win[1]))
    return out


def next_change(sched: dict | None, now: datetime | None = None) -> datetime | None:
    """The next instant at which `is_blocking` flips, or None if it never does."""
    if not is_weekly(sched):
        return None
    now = now or datetime.now()
    cur = is_blocking(sched, now)
    for c in sorted(_candidates(sched, now, range(0, 9))):
        if c > now and is_blocking(sched, c) != cur:
            return c
    return None


def prev_change(sched: dict | None, now: datetime | None = None) -> datetime | None:
    """The most recent instant at which `is_blocking` flipped to its current value."""
    if not is_weekly(sched):
        return None
    now = now or datetime.now()
    cur = is_blocking(sched, now)
    for c in sorted(_candidates(sched, now, range(-8, 1)), reverse=True):
        if c <= now and is_blocking(sched, c) == cur and is_blocking(sched, c - timedelta(minutes=1)) != cur:
            return c
    return None


def fmt_minute(m: int) -> str:
    if m >= MINUTES_PER_DAY:
        return "midnight"
    return f"{m // 60:02d}:{m % 60:02d}"


def describe(sched: dict | None) -> str:
    """One sentence, e.g. 'Blocked all week except Friday from 15:00, Saturday and Sunday.'"""
    if not is_weekly(sched):
        return "Blocked continuously until the end time."
    parts: list[str] = []
    for d in range(7):
        win = sched["free"].get(str(d))
        if not win:
            continue
        a, b = win
        if a == 0 and b == MINUTES_PER_DAY:
            parts.append(DAY_NAMES[d])
        elif b == MINUTES_PER_DAY:
            parts.append(f"{DAY_NAMES[d]} from {fmt_minute(a)}")
        elif a == 0:
            parts.append(f"{DAY_NAMES[d]} until {fmt_minute(b)}")
        else:
            parts.append(f"{DAY_NAMES[d]} {fmt_minute(a)} to {fmt_minute(b)}")
    if not parts:
        return "Blocked all week with no free windows."
    if not has_blocked_time(sched):
        return "Free all week (nothing would be blocked)."
    if len(parts) == 1:
        joined = parts[0]
    else:
        joined = ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Blocked all week except {joined}."


def phase_text(sched: dict | None, now: datetime | None = None) -> str:
    """Short status for the session screen, e.g. 'Free until Monday 00:00'."""
    if not is_weekly(sched):
        return ""
    now = now or datetime.now()
    nxt = next_change(sched, now)
    when = nxt.strftime("%A %H:%M") if nxt else "the end of the schedule"
    if is_blocking(sched, now):
        return f"Blocking. Free time starts {when}."
    return f"Free window. Blocks return {when}."
