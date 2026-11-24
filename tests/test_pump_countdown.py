"""Tests for backend-owned manifold countdown timing."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pump_countdown",
    ROOT / "custom_components" / "heating_intelligence" / "pump_countdown.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

COUNTDOWN_SECONDS = MODULE.COUNTDOWN_SECONDS
advance_deadline = MODULE.advance_deadline
remaining_seconds = MODULE.remaining_seconds
start_deadline = MODULE.start_deadline
timer_snapshot = MODULE.timer_snapshot


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


def test_manifold_deadlines_are_independent():
    now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    downstairs = {"pwm_timer_next_at": start_deadline(now).isoformat()}
    upstairs_start = now + timedelta(minutes=7)
    upstairs = {"pwm_timer_next_at": start_deadline(upstairs_start).isoformat()}

    check_at = now + timedelta(minutes=26)
    downstairs_snapshot, downstairs_changed = timer_snapshot(downstairs, check_at)
    upstairs_snapshot, upstairs_changed = timer_snapshot(upstairs, check_at)

    assert downstairs_changed is True
    assert downstairs_snapshot["remaining_seconds"] == COUNTDOWN_SECONDS - 60
    assert upstairs_changed is False
    assert upstairs_snapshot["remaining_seconds"] == COUNTDOWN_SECONDS - (19 * 60)
    assert downstairs["pwm_timer_next_at"] != upstairs["pwm_timer_next_at"]


def test_starting_one_manifold_does_not_change_the_other_deadline():
    now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    downstairs = {"pwm_timer_next_at": start_deadline(now).isoformat()}
    upstairs = {"pwm_timer_next_at": start_deadline(now + timedelta(minutes=4)).isoformat()}
    upstairs_deadline = upstairs["pwm_timer_next_at"]

    downstairs["pwm_timer_next_at"] = start_deadline(now + timedelta(minutes=10)).isoformat()

    assert upstairs["pwm_timer_next_at"] == upstairs_deadline
