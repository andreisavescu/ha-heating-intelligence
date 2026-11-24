"""Tests for backend-owned manifold countdown timing."""
from datetime import datetime, timedelta, timezone

from custom_components.heating_intelligence.pump_countdown import (
    COUNTDOWN_SECONDS,
    advance_deadline,
    remaining_seconds,
    start_deadline,
)


def test_new_pump_cycle_starts_a_full_25_minute_countdown():
    now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    deadline = start_deadline(now)
    assert deadline == now + timedelta(minutes=25)
    assert remaining_seconds(deadline, now) == COUNTDOWN_SECONDS


def test_countdown_deadline_does_not_move_before_expiry():
    now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    deadline = start_deadline(now)
    later = now + timedelta(minutes=4, seconds=12)
    assert advance_deadline(deadline, later) == deadline
    assert remaining_seconds(deadline, later) == 1248


def test_expired_deadline_advances_by_whole_periods_without_phase_drift():
    now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    deadline = start_deadline(now)
    after_one_cycle = deadline + timedelta(seconds=5)
    advanced = advance_deadline(deadline, after_one_cycle)
    assert advanced == deadline + timedelta(seconds=COUNTDOWN_SECONDS)
    assert remaining_seconds(advanced, after_one_cycle) == COUNTDOWN_SECONDS - 5


def test_long_home_assistant_gap_skips_elapsed_periods_instead_of_restarting_now():
    now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    deadline = start_deadline(now)
    after_three_cycles = deadline + timedelta(seconds=COUNTDOWN_SECONDS * 3 + 7)
    advanced = advance_deadline(deadline, after_three_cycles)
    assert advanced == deadline + timedelta(seconds=COUNTDOWN_SECONDS * 4)
    assert remaining_seconds(advanced, after_three_cycles) == COUNTDOWN_SECONDS - 7
