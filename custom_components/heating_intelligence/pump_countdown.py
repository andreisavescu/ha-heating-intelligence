"""Backend-owned fixed manifold countdown helpers."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

COUNTDOWN_SECONDS = 25 * 60


def start_deadline(now: datetime) -> datetime:
    """Return the next expiry for a newly detected pump cycle."""
    return now + timedelta(seconds=COUNTDOWN_SECONDS)


def advance_deadline(deadline: datetime, now: datetime) -> datetime:
    """Advance an expired deadline by whole periods without shifting its phase."""
    if now < deadline:
        return deadline
    periods = int((now - deadline).total_seconds() // COUNTDOWN_SECONDS) + 1
    return deadline + timedelta(seconds=periods * COUNTDOWN_SECONDS)


def remaining_seconds(deadline: datetime, now: datetime) -> int:
    """Return the non-negative, ceiling-rounded countdown display value."""
    return max(0, math.ceil((deadline - now).total_seconds()))


def timer_snapshot(history: dict, now: datetime) -> tuple[dict, bool]:
    """Build one manifold's timer state without touching any other manifold."""
    next_at = history.get("pwm_timer_next_at")
    if isinstance(next_at, str):
        # Keep this module independent of Home Assistant's datetime helpers.
        from datetime import datetime
        next_at = datetime.fromisoformat(next_at)
    if next_at is None:
        return {
            "next_at": None,
            "remaining_seconds": None,
            "status": "waiting_for_pump_start",
            "duration_seconds": COUNTDOWN_SECONDS,
        }, False

    advanced = advance_deadline(next_at, now)
    changed = advanced != next_at
    if changed:
        history["pwm_timer_next_at"] = advanced.isoformat()
    return {
        "next_at": advanced.isoformat(),
        "remaining_seconds": remaining_seconds(advanced, now),
        "status": "available",
        "duration_seconds": COUNTDOWN_SECONDS,
    }, changed
