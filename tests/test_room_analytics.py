"""Tests for persistent room heating analytics."""
from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.heating_intelligence.room_analytics import (
    demand_is_active,
    room_statistics,
    update_room_history,
)


TZ = ZoneInfo("Europe/Bucharest")


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def test_availability_semantics():
    assert demand_is_active("on")
    assert demand_is_active("off")
    assert demand_is_active("unknown")
    assert not demand_is_active("unavailable")
    assert not demand_is_active(None)


def test_demand_duration_splits_at_local_midnight():
    history = {}
    start = dt("2026-11-11T23:50:00")
    end = dt("2026-11-12T00:10:00")

    update_room_history(history, "bedroom", True, start, start, start)
    room = update_room_history(history, "bedroom", True, start, start, end)

    assert room["daily"]["2026-11-11"]["demand_seconds"] == 600
    assert room["daily"]["2026-11-12"]["demand_seconds"] == 600
    assert room["total_cycles"] == 1


def test_cycle_count_and_duration_survive_history_reload():
    history = {}
    start = dt("2026-11-12T07:00:00")
    end = dt("2026-11-12T07:45:00")
    update_room_history(history, "living_room", True, start, start, start)

    # Simulate a restart by reusing the persisted history dictionary.
    persisted = history
    room = update_room_history(
        persisted, "living_room", False, end, start, end
    )
    stats = room_statistics(room, end)

    assert room["total_cycles"] == 1
    assert room["cycles"][0]["duration_seconds"] == 2700
    assert stats["cycles_today"] == 1
    assert stats["average_cycle_seconds_week"] == 2700
    assert stats["demand_seconds_today"] == 2700


def test_week_and_month_aggregate_daily_buckets():
    history = {}
    first = dt("2026-11-09T08:00:00")
    second = dt("2026-11-10T08:00:00")
    now = dt("2026-11-12T09:00:00")
    update_room_history(history, "office", True, first, first, first)
    update_room_history(history, "office", True, first, first, second)
    room = update_room_history(history, "office", True, first, second, now)

    stats = room_statistics(room, now)
    assert stats["demand_seconds_week"] == 25 * 3600
    assert stats["demand_seconds_month"] == 25 * 3600
    assert stats["cycles_week"] == 1
