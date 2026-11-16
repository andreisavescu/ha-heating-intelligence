"""Offline experiment analysis primitives for thermal identification.

The functions accept Recorder/CSV-shaped rows and deliberately keep
observations separate from user-supplied operating events. They do not infer
heat delivery, slab temperature, or room-level AC power.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from statistics import median
from typing import Any


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def numeric_series(
    rows: list[dict[str, Any]], entity_id: str
) -> list[tuple[datetime, float]]:
    """Return sorted finite numeric states for one entity."""
    points = []
    for row in rows:
        if row.get("entity_id") != entity_id:
            continue
        stamp = _timestamp(row.get("last_changed"))
        value = _number(row.get("state"))
        if stamp is not None and value is not None:
            points.append((stamp, value))
    points.sort(key=lambda item: item[0])
    # Recorder exports can contain repeated identical timestamps. Keep the
    # last row for that timestamp so an interval is never double-counted.
    deduplicated: dict[datetime, float] = {}
    for stamp, value in points:
        deduplicated[stamp] = value
    return sorted(deduplicated.items())


def integrate_power_samples(
    rows: list[dict[str, Any]],
    entity_id: str,
    *,
    max_gap_seconds: float = 300.0,
    power_unit: str = "W",
) -> dict[str, Any]:
    """Integrate a power sensor using trapezoids over valid short intervals.

    Negative readings, unavailable states, and long gaps are excluded rather
    than clamped or bridged. Only W is converted to Wh; other units are
    reported as unsupported to prevent a silently incorrect energy estimate.
    """
    if power_unit != "W":
        return {
            "status": "unsupported_power_unit",
            "power_unit": power_unit,
            "energy_wh": None,
            "valid_interval_count": 0,
            "covered_seconds": 0.0,
        }
    points = numeric_series(rows, entity_id)
    energy_wh = 0.0
    covered = 0.0
    valid = 0
    excluded_negative = 0
    excluded_gap = 0
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        seconds = (t1 - t0).total_seconds()
        if seconds <= 0 or seconds > max_gap_seconds:
            excluded_gap += 1
            continue
        if p0 < 0 or p1 < 0:
            excluded_negative += 1
            continue
        energy_wh += ((p0 + p1) / 2.0) * seconds / 3600.0
        covered += seconds
        valid += 1
    return {
        "status": "available" if valid else "insufficient_valid_intervals",
        "power_unit": power_unit,
        "energy_wh": round(energy_wh, 3),
        "energy_kwh": round(energy_wh / 1000.0, 6),
        "valid_interval_count": valid,
        "covered_seconds": round(covered, 1),
        "coverage_hours": round(covered / 3600.0, 3),
        "excluded_negative_intervals": excluded_negative,
        "excluded_long_or_invalid_gaps": excluded_gap,
        "max_gap_seconds": max_gap_seconds,
        "scope": "combined_sensor_input_not_room_level_heat_output",
    }


def temperature_response(
    rows: list[dict[str, Any]],
    entity_id: str,
    start: str,
    end: str,
    *,
    max_endpoint_distance_minutes: float = 20.0,
) -> dict[str, Any]:
    """Estimate endpoint slope within a declared interval, without interpolation."""
    start_at, end_at = _timestamp(start), _timestamp(end)
    if start_at is None or end_at is None or end_at <= start_at:
        return {"status": "invalid_interval", "temperature_change_c": None}
    points = numeric_series(rows, entity_id)
    if len(points) < 2:
        return {"status": "insufficient_temperature_samples", "temperature_change_c": None}

    def nearest(target: datetime):
        return min(points, key=lambda item: abs((item[0] - target).total_seconds()))

    first, last = nearest(start_at), nearest(end_at)
    first_offset = abs((first[0] - start_at).total_seconds()) / 60
    last_offset = abs((last[0] - end_at).total_seconds()) / 60
    if max(first_offset, last_offset) > max_endpoint_distance_minutes:
        return {
            "status": "endpoint_coverage_insufficient",
            "temperature_change_c": None,
            "start_sample_time": first[0].isoformat(),
            "end_sample_time": last[0].isoformat(),
            "max_endpoint_distance_minutes": max_endpoint_distance_minutes,
            "observed_start_offset_minutes": round(first_offset, 2),
            "observed_end_offset_minutes": round(last_offset, 2),
        }
    elapsed_hours = (last[0] - first[0]).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return {"status": "invalid_observed_span", "temperature_change_c": None}
    change = last[1] - first[1]
    return {
        "status": "available",
        "start_temperature_c": round(first[1], 3),
        "end_temperature_c": round(last[1], 3),
        "temperature_change_c": round(change, 3),
        "observed_duration_hours": round(elapsed_hours, 3),
        "rate_c_per_hour": round(change / elapsed_hours, 4),
        "start_sample_time": first[0].isoformat(),
        "end_sample_time": last[0].isoformat(),
        "endpoint_distance_minutes": {
            "start": round(first_offset, 2),
            "end": round(last_offset, 2),
        },
        "method": "nearest_observed_endpoints_no_interpolation",
    }


def climate_mode_intervals(
    rows: list[dict[str, Any]], entity_id: str
) -> list[dict[str, Any]]:
    """Convert climate state changes into half-open UTC mode intervals."""
    events = []
    for row in rows:
        if row.get("entity_id") != entity_id:
            continue
        stamp = _timestamp(row.get("last_changed"))
        mode = row.get("state")
        if stamp is not None and isinstance(mode, str) and mode.lower() not in ("", "unknown", "unavailable"):
            events.append((stamp, mode.lower()))
    events.sort(key=lambda item: item[0])
    intervals = []
    for index, (start, mode) in enumerate(events):
        end = events[index + 1][0] if index + 1 < len(events) else None
        if intervals and intervals[-1]["mode"] == mode and intervals[-1]["end"] == start.isoformat():
            intervals[-1]["end"] = end.isoformat() if end else None
        else:
            intervals.append({
                "mode": mode,
                "start": start.isoformat(),
                "end": end.isoformat() if end else None,
                "end_is_open": end is None,
            })
    return intervals


def build_experiment_summary(
    rows: list[dict[str, Any]],
    *,
    room_temperature_entities: dict[str, str],
    outdoor_temperature_entity: str | None = None,
    ac_power_entity: str | None = None,
    climate_entity: str | None = None,
    power_unit: str = "W",
    max_power_gap_seconds: float = 300.0,
    periods: dict[str, dict[str, str]] | None = None,
    user_supplied_event_annotations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a transparent data-quality inventory for an exported experiment."""
    entities = set(room_temperature_entities.values())
    if outdoor_temperature_entity:
        entities.add(outdoor_temperature_entity)
    if ac_power_entity:
        entities.add(ac_power_entity)
    if climate_entity:
        entities.add(climate_entity)

    inventory = {}
    for entity_id in sorted(entities):
        points = numeric_series(rows, entity_id)
        inventory[entity_id] = {
            "numeric_sample_count": len(points),
            "first_sample_time": points[0][0].isoformat() if points else None,
            "last_sample_time": points[-1][0].isoformat() if points else None,
            "observed_span_hours": round(
                (points[-1][0] - points[0][0]).total_seconds() / 3600.0, 3
            ) if len(points) > 1 else 0.0,
        }

    result: dict[str, Any] = {
        "status": "available" if inventory else "no_matching_entities",
        "source": "user_supplied_recorder_export",
        "time_basis": "UTC",
        "entities": inventory,
        "room_temperature_entities": dict(room_temperature_entities),
        "interpretation_limits": [
            "Room temperature response is not a direct measurement of delivered heat.",
            "Combined AC electrical input is not room-level thermal output.",
            "No floor surface or screed temperature is present unless separately configured.",
            "User-supplied boiler and pump events remain external annotations, not CSV observations.",
        ],
    }
    if periods:
        result["room_temperature_responses"] = {
            period_name: {
                room_name: temperature_response(
                    rows, entity_id, period.get("start"), period.get("end")
                )
                for room_name, entity_id in room_temperature_entities.items()
            }
            for period_name, period in periods.items()
        }
    if user_supplied_event_annotations:
        # Keep manual operating facts separate from Recorder-derived states.
        result["user_supplied_event_annotations"] = [
            dict(item) for item in user_supplied_event_annotations
        ]
    if ac_power_entity:
        result["combined_ac_electrical_input"] = integrate_power_samples(
            rows, ac_power_entity, max_gap_seconds=max_power_gap_seconds,
            power_unit=power_unit,
        )
    if climate_entity:
        result["ac_mode_intervals"] = climate_mode_intervals(rows, climate_entity)
    return result
