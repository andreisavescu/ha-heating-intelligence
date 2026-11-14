"""Recorder-backed historical data coverage for model calibration."""
from __future__ import annotations

from datetime import datetime, timezone
from bisect import bisect_right
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


def _timeline(history: dict[str, list[Any]], entity_id: str | None):
    if not entity_id:
        return []
    points = []
    for state in history.get(entity_id, []):
        stamp = _timestamp(state)
        raw = state.get("state") if isinstance(state, dict) else getattr(state, "state", None)
        if stamp is not None and raw not in (None, "unknown", "unavailable"):
            points.append((stamp, raw))
    return sorted(points, key=lambda item: item[0])


def _latest_at(points, stamp):
    if not points:
        return None
    index = bisect_right(points, stamp, key=lambda item: item[0]) - 1
    return points[index][1] if index >= 0 else None


def _switch_state(value):
    if value is None:
        return None
    normal = str(value).strip().lower()
    if normal in ("on", "true", "1", "heat", "heating", "open"):
        return True
    if normal in ("off", "false", "0", "idle", "closed"):
        return False
    return None


def build_recorder_room_response(
    history: dict[str, list[Any]],
    rooms: list[dict[str, Any]],
    ac_power_entity: str | None = None,
) -> dict[str, Any]:
    """Estimate independent room temperature slopes from Recorder history."""
    from statistics import median

    ac_points = _timeline(history, ac_power_entity)
    output = {}
    for room in rooms:
        room_id = room.get("id", "unknown")
        temperature_points = []
        for state in history.get(room.get("temperature_entity"), []):
            stamp = _timestamp(state)
            value = _state_value(state)
            if stamp is not None and value is not None:
                temperature_points.append((stamp, value))
        temperature_points.sort(key=lambda item: item[0])
        demand_points = _timeline(history, room.get("demand_entity"))
        window_points = _timeline(history, room.get("window_entity"))
        rates = {"heating": [], "coasting": []}
        eligible = {"heating": 0, "coasting": 0}
        excluded = {"unknown_mode": 0, "open_window": 0, "ac_heating": 0, "invalid_interval": 0}
        previous = None
        for stamp, temperature in temperature_points:
            demand = _switch_state(_latest_at(demand_points, stamp))
            window = _switch_state(_latest_at(window_points, stamp))
            if demand is None:
                excluded["unknown_mode"] += 1
                previous = None
                continue
            if window is not False:
                excluded["open_window" if window is True else "unknown_mode"] += 1
                previous = None
                continue
            if ac_power_entity:
                ac_raw = _latest_at(ac_points, stamp)
                try:
                    ac_power = float(ac_raw)
                except (TypeError, ValueError):
                    ac_power = None
                if ac_power is None or not math.isfinite(ac_power) or ac_power > 50:
                    excluded["ac_heating"] += 1
                    previous = None
                    continue
            mode = "heating" if demand else "coasting"
            eligible[mode] += 1
            if previous is not None:
                prev_stamp, prev_temp, prev_mode = previous
                minutes = (stamp - prev_stamp).total_seconds() / 60
                if prev_mode == mode and 5 <= minutes <= 45:
                    rates[mode].append((temperature - prev_temp) / (minutes / 60))
                elif minutes > 45 or minutes < 0:
                    excluded["invalid_interval"] += 1
            previous = (stamp, temperature, mode)
        modes = {}
        for mode in ("heating", "coasting"):
            samples = rates[mode]
            span_hours = (
                (temperature_points[-1][0] - temperature_points[0][0]).total_seconds() / 3600
                if len(temperature_points) > 1 else 0
            )
            if len(samples) >= 6:
                rate = median(samples)
                noise = median([abs(sample - rate) for sample in samples])
                confidence = round(100 * min(1, len(samples) / 36) * min(1, span_hours / 24) * max(.15, 1 - noise / .35))
                modes[mode] = {
                    "status": "available",
                    "rate_c_per_hour": round(rate, 3),
                    "noise_c_per_hour": round(noise, 3),
                    "valid_intervals": len(samples),
                    "confidence": max(5, min(95, confidence)),
                }
            else:
                modes[mode] = {
                    "status": "learning",
                    "rate_c_per_hour": None,
                    "noise_c_per_hour": None,
                    "valid_intervals": len(samples),
                    "confidence": 0,
                }
        output[room_id] = {
            "heating": modes["heating"],
            "coasting": modes["coasting"],
            "eligible_samples": eligible,
            "excluded_samples": excluded,
            "source": "recorder_history",
            "interpretation": "Historical temperature response only; not a calibrated thermal capacity or conductance.",
        }
    return output


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
