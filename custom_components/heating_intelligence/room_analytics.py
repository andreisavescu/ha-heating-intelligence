"""Persistent room-level heating demand analytics.

Demand sensors in this integration use availability semantics: any present state
(including "on", "off", or "unknown") means demand; "unavailable" or a missing
entity means no demand.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any


HISTORY_DAYS = 400
MAX_CYCLE_RECORDS = 5000


def demand_is_active(state: str | None) -> bool:
    """Return whether the configured demand entity is available."""
    return state is not None and state != "unavailable"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _add_interval_to_daily(
    daily: dict[str, dict[str, Any]],
    start: datetime,
    end: datetime,
) -> None:
    """Split an aware interval at local midnights and add seconds per date."""
    if end <= start:
        return
    cursor = start
    while cursor.date() < end.date():
        next_midnight = datetime.combine(
            cursor.date() + timedelta(days=1), time.min, tzinfo=cursor.tzinfo
        )
        seconds = max(0.0, (next_midnight.astimezone(timezone.utc) - cursor.astimezone(timezone.utc)).total_seconds())
        bucket = daily.setdefault(cursor.date().isoformat(), {})
        bucket["demand_seconds"] = float(bucket.get("demand_seconds", 0.0)) + seconds
        cursor = next_midnight
    seconds = max(0.0, (end.astimezone(timezone.utc) - cursor.astimezone(timezone.utc)).total_seconds())
    if seconds:
        bucket = daily.setdefault(cursor.date().isoformat(), {})
        bucket["demand_seconds"] = float(bucket.get("demand_seconds", 0.0)) + seconds


def update_room_history(
    history: dict[str, Any],
    room_id: str,
    active: bool,
    state_changed: datetime | None,
    interval_start: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Update one room's persisted history from a coordinator observation.

    HA's last_changed timestamp lets us place a detected transition at the
    source's actual transition time rather than at the next polling tick.
    """
    room = history.setdefault("rooms", {}).setdefault(
        room_id,
        {
            "last_demand": None,
            "cycle_start": None,
            "total_cycles": 0,
            "daily": {},
            "cycles": [],
        },
    )
    room.setdefault("daily", {})
    room.setdefault("cycles", [])
    previous = room.get("last_demand")
    cycle_start = _parse_datetime(room.get("cycle_start"))
    transition = state_changed or now

    if active and previous is not True:
        # On first observation, start counting now; do not backfill time from
        # before the integration was configured or Home Assistant restarted.
        cycle_start = max(transition, interval_start)
        room["cycle_start"] = cycle_start.isoformat()
        room["total_cycles"] = int(room.get("total_cycles", 0)) + 1
        bucket = room["daily"].setdefault(cycle_start.date().isoformat(), {})
        bucket["cycles_started"] = int(bucket.get("cycles_started", 0)) + 1

    if active:
        start = interval_start
        if previous is not True:
            start = max(interval_start, transition)
        _add_interval_to_daily(room["daily"], start, now)
    elif previous is True:
        # The state change timestamp marks when demand actually ended.
        end = min(now, transition)
        if cycle_start is not None and end >= cycle_start:
            duration = (end.astimezone(timezone.utc) - cycle_start.astimezone(timezone.utc)).total_seconds()
            room["cycles"].append(
                {
                    "start": cycle_start.isoformat(),
                    "end": end.isoformat(),
                    "start_date": cycle_start.date().isoformat(),
                    "duration_seconds": round(duration, 3),
                }
            )
            room["cycles"] = room["cycles"][-MAX_CYCLE_RECORDS:]
        room["cycle_start"] = None

    room["last_demand"] = active

    # Keep a little more than one year so calendar-week/month summaries work
    # across year boundaries without unbounded daily history.
    cutoff = now.date() - timedelta(days=HISTORY_DAYS)
    room["daily"] = {
        key: value
        for key, value in room["daily"].items()
        if date.fromisoformat(key) >= cutoff
    }
    return room


def room_statistics(room: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Calculate daily, Monday-based weekly and calendar-month statistics."""
    daily = room.get("daily", {})
    today = now.date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    def sum_daily(start: date) -> tuple[float, int]:
        seconds = 0.0
        cycles = 0
        for key, values in daily.items():
            day = date.fromisoformat(key)
            if start <= day <= today:
                seconds += float(values.get("demand_seconds", 0.0))
                cycles += int(values.get("cycles_started", 0))
        return seconds, cycles

    today_seconds, today_cycles = sum_daily(today)
    week_seconds, week_cycles = sum_daily(week_start)
    month_seconds, month_cycles = sum_daily(month_start)

    def cycle_metrics(start: date) -> tuple[float | None, float | None, float | None]:
        durations = [
            float(item["duration_seconds"])
            for item in room.get("cycles", [])
            if date.fromisoformat(item["start_date"]) >= start
            and date.fromisoformat(item["start_date"]) <= today
        ]
        if not durations:
            return None, None, None
        return (
            sum(durations) / len(durations),
            min(durations),
            max(durations),
        )

    average, shortest, longest = cycle_metrics(week_start)
    active_start = _parse_datetime(room.get("cycle_start"))
    active_duration = (
        max(0.0, (now.astimezone(timezone.utc) - active_start.astimezone(timezone.utc)).total_seconds())
        if room.get("last_demand") is True and active_start
        else 0.0
    )
    return {
        "demand_seconds_today": round(today_seconds),
        "demand_seconds_week": round(week_seconds),
        "demand_seconds_month": round(month_seconds),
        "cycles_today": today_cycles,
        "cycles_week": week_cycles,
        "cycles_month": month_cycles,
        "total_cycles": int(room.get("total_cycles", 0)),
        "average_cycle_seconds_week": round(average) if average is not None else None,
        "shortest_cycle_seconds_week": round(shortest) if shortest is not None else None,
        "longest_cycle_seconds_week": round(longest) if longest is not None else None,
        "active_cycle_seconds": round(active_duration),
    }
