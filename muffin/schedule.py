"""Per-worker render schedules — pure logic, shared by the manager (which
enforces them) and the Monitor GUI (which edits them). No Qt / DB / network deps.

A schedule says, for each weekday, when the worker is *free to render*. The mental
model is the one artists use: render during free time, keep the PC for the artist
during working hours. Each day is one of three modes:

  * ``off``    — never render this day
  * ``all``    — render all day
  * ``window`` — render only between ``start`` and ``end`` (minutes after midnight,
                 so any time like 09:30 works); the window wraps past midnight when
                 ``end <= start`` (e.g. 18:00 → 09:00 = render the evening + the
                 early morning, free 09:00–18:00).

Stored as JSON:  ``{"days": [<7 day dicts, Mon..Sun>]}``  where each day dict is
``{"mode": "off"|"all"|"window", "start": <min>, "end": <min>}``.

Times are evaluated in the **worker's** local timezone (the worker reports its UTC
offset), so "09:00" means 9am where the machine physically sits, even if the
manager runs on a NAS in another timezone.
"""

import datetime
from typing import Any, Optional

DAYS = 7
DAY_MINUTES = 24 * 60                 # 1440
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS = (0, 1, 2, 3, 4)           # Mon–Fri
WEEKEND = (5, 6)                      # Sat, Sun
MODES = ("off", "all", "window")


# --------------------------------------------------------------- build ---------
def _day(mode: str, start: int = 0, end: int = 0) -> dict:
    return {"mode": mode, "start": _clamp(start), "end": _clamp(end)}


def _clamp(v: Any) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return 0
    return max(0, min(DAY_MINUTES, v))


def empty() -> dict:
    """No rendering at all (every day off)."""
    return {"days": [_day("off") for _ in range(DAYS)]}


def full() -> dict:
    """Render around the clock (the default when scheduling is off)."""
    return {"days": [_day("all") for _ in range(DAYS)]}


def nights_and_weekends(start: int = 18 * 60, end: int = 9 * 60) -> dict:
    """The classic studio pattern: render on weekday nights (out of office hours)
    and all weekend. Default window 18:00 → 09:00 frees the PC 09:00–18:00."""
    return {"days": [
        _day("window", start, end) if d in WEEKDAYS else _day("all")
        for d in range(DAYS)
    ]}


def normalize(obj: Any) -> dict:
    """Coerce anything into a valid schedule dict (7 day entries). Never raises —
    a corrupt schedule degrades to 'render allowed', never to a silent block."""
    days_in = obj.get("days") if isinstance(obj, dict) else None
    if not isinstance(days_in, list):
        return full()
    days = []
    for d in range(DAYS):
        src = days_in[d] if d < len(days_in) and isinstance(days_in[d], dict) else {}
        mode = src.get("mode")
        if mode not in MODES:
            mode = "all"
        days.append(_day(mode, src.get("start", 0), src.get("end", 0)))
    return {"days": days}


def set_window(schedule: dict, days, start: int, end: int) -> dict:
    """Set each listed weekday to a render window [start, end). Days not listed
    are left untouched."""
    s = normalize(schedule)
    for d in days:
        if 0 <= d < DAYS:
            s["days"][d] = _day("window", start, end)
    return s


# ----------------------------------------------------------- evaluation --------
def day_allows(day: dict, minute: int) -> bool:
    """Is `minute` (0–1439) inside this single day's render window?"""
    mode = day.get("mode")
    if mode == "all":
        return True
    if mode == "off":
        return False
    start, end = _clamp(day.get("start", 0)), _clamp(day.get("end", 0))
    if start == end:
        return True                    # zero-length window == all day
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end   # wraps past midnight


def is_allowed(schedule: Any, weekday: int, minute: int) -> bool:
    """Is rendering allowed at (weekday, minute) for this schedule?"""
    if not (0 <= weekday < DAYS):
        return False
    return day_allows(normalize(schedule)["days"][weekday], minute)


def local_now(tz_offset_min: Optional[int], now_ts: Optional[float] = None):
    """(weekday, minute-of-day) in the worker's local time. ``tz_offset_min`` is
    minutes east of UTC as reported by the worker; if None we fall back to the
    local time of whatever process is asking."""
    import time
    ts = time.time() if now_ts is None else now_ts
    if tz_offset_min is None:
        dt = datetime.datetime.fromtimestamp(ts)
    else:
        dt = datetime.datetime.utcfromtimestamp(ts) + datetime.timedelta(minutes=tz_offset_min)
    return dt.weekday(), dt.hour * 60 + dt.minute


def worker_allowed_now(worker: dict, now_ts: Optional[float] = None) -> bool:
    """May this worker render right now? True when scheduling is off (or nothing
    is configured), or the current local time is inside its render window."""
    if not worker.get("schedule_enabled"):
        return True
    sched = worker.get("schedule")
    if not sched:
        return True                    # enabled but unconfigured — don't block
    wd, minute = local_now(worker.get("tz_offset"), now_ts)
    return is_allowed(sched, wd, minute)


def worker_standby(worker: dict, now_ts: Optional[float] = None) -> bool:
    """True when a scheduled worker is currently OUTSIDE its render window (it
    should not be handed work, and a running render should be stopped)."""
    return bool(worker.get("schedule_enabled")) and not worker_allowed_now(worker, now_ts)


# -------------------------------------------------------------- display --------
def fmt_minute(m: int) -> str:
    m = _clamp(m)
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def _day_phrase(day: dict) -> str:
    mode = day.get("mode")
    if mode == "off":
        return "no render"
    if mode == "all":
        return "all day"
    start, end = _clamp(day.get("start", 0)), _clamp(day.get("end", 0))
    if start == end:
        return "all day"
    nxt = " next day" if end <= start else ""
    return f"{fmt_minute(start)}–{fmt_minute(end)}{nxt}"


def human_summary(sched: Any) -> str:
    """Plain-English description of a schedule, grouping consecutive identical
    days, e.g. 'Mon–Fri 18:00–09:00 next day · Sat–Sun all day'."""
    days = normalize(sched)["days"]
    parts, i = [], 0
    while i < DAYS:
        j = i
        while j + 1 < DAYS and days[j + 1] == days[i]:
            j += 1
        label = DAY_NAMES[i] if i == j else f"{DAY_NAMES[i]}–{DAY_NAMES[j]}"
        parts.append(f"{label} {_day_phrase(days[i])}")
        i = j + 1
    return "   ·   ".join(parts)


def summary(worker_or_sched: Any, enabled: Optional[bool] = None) -> str:
    """Short label for a worker's schedule (e.g. for the worker picker)."""
    if isinstance(worker_or_sched, dict) and "schedule_enabled" in worker_or_sched:
        enabled = worker_or_sched.get("schedule_enabled")
        sched = worker_or_sched.get("schedule")
    else:
        sched = worker_or_sched
    if not enabled:
        return "24/7"
    s = normalize(sched)
    modes = {d["mode"] for d in s["days"]}
    if modes == {"off"}:
        return "off (never)"
    if modes == {"all"}:
        return "24/7"
    return "scheduled"
