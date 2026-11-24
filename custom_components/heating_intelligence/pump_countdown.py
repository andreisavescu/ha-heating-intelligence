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
