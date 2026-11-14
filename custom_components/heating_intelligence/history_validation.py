"""Recorder-backed historical data coverage for model calibration."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any


def _timestamp(state: Any) -> datetime | None:
    value = None
    if isinstance(state, dict):
        value = state.get("last_changed") or state.get("last_updated")
    else:
        value = getattr(state, "last_changed", None) or getattr(state, "last_updated", None)
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _state_value(state: Any) -> float | None:
    raw = state.get("state") if isinstance(state, dict) else getattr(state, "state", None)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_recorder_history_summary(
    history: dict[str, list[Any]],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Summarise recorder samples without retaining or exporting raw history."""
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    entities: dict[str, dict[str, Any]] = {}
    all_stamps: list[datetime] = []
    for entity_id, states in history.items():
        valid = []
        numeric = 0
        for state in states:
            stamp = _timestamp(state)
            if stamp is None or not start_utc <= stamp <= end_utc:
                continue
            valid.append(stamp)
            if _state_value(state) is not None:
                numeric += 1
        valid.sort()
        all_stamps.extend(valid)
        span_hours = (
            (valid[-1] - valid[0]).total_seconds() / 3600
            if len(valid) > 1 else 0.0
        )
        gaps = [
            (second - first).total_seconds() / 60
            for first, second in zip(valid, valid[1:])
        ]
        entities[entity_id] = {
            "samples": len(valid),
            "numeric_samples": numeric,
            "first_sample": valid[0].isoformat() if valid else None,
            "last_sample": valid[-1].isoformat() if valid else None,
            "span_hours": round(span_hours, 2),
            "median_interval_minutes": round(sorted(gaps)[len(gaps) // 2], 2) if gaps else None,
            "status": "available" if len(valid) >= 2 else "insufficient_history",
        }
    observed = len(all_stamps)
    return {
        "status": "available" if observed else "no_recorder_history",
        "source": "home_assistant_recorder",
        "window_start": start_utc.isoformat(),
        "window_end": end_utc.isoformat(),
        "window_hours": round((end_utc - start_utc).total_seconds() / 3600, 2),
        "entity_count": len(entities),
        "entities_with_history": sum(item["samples"] >= 2 for item in entities.values()),
        "total_samples": observed,
        "entities": entities,
        "raw_history_retained": False,
        "note": (
            "Recorder history is queried locally and reduced to coverage metadata. "
            "Detailed history remains in Home Assistant Recorder; no raw samples are copied "
            "into integration storage or sent externally."
        ),
    }
