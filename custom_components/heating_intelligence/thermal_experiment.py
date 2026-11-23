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


def identify_free_cooling_h_over_c(
    rows: list[dict[str, Any]],
    room_temperature_entity: str,
    outdoor_temperature_entity: str,
    *,
    heating_entity: str | None = None,
    max_gap_seconds: float = 900.0,
    min_temperature_difference_c: float = 2.0,
    min_intervals: int = 30,
) -> dict[str, Any]:
    """Estimate the free-cooling H/C ratio from dT/dt = -k(Troom-Tout).

    This identifies only the ratio H/C (1/hour), not H or C separately.
    Intervals with heating active, inadequate temperature difference, or
    large/missing samples are excluded. Solar and internal gains remain
    potential confounders and are disclosed in the result.
    """
    room = dict(numeric_series(rows, room_temperature_entity))
    outdoor = dict(numeric_series(rows, outdoor_temperature_entity))
    heating_events = []
    if heating_entity:
        for row in rows:
            if row.get("entity_id") != heating_entity:
                continue
            stamp = _timestamp(row.get("last_changed"))
            if stamp is not None:
                state = str(row.get("state", "")).lower()
                heating_events.append((stamp, state not in ("off", "false", "0", "idle", "unavailable", "unknown")))
        heating_events.sort()

    def heating_active(stamp):
        state = False
        for event_time, active in heating_events:
            if event_time > stamp:
                break
            state = active
        return state

    common = sorted(set(room) & set(outdoor))
    xs, ys = [], []
    excluded = {"gap": 0, "small_temperature_difference": 0, "heating_active": 0}
    for first, second in zip(common, common[1:]):
        seconds = (second - first).total_seconds()
        if seconds <= 0 or seconds > max_gap_seconds:
            excluded["gap"] += 1
            continue
        if heating_entity and heating_active(first):
            excluded["heating_active"] += 1
            continue
        delta0 = room[first] - outdoor[first]
        delta1 = room[second] - outdoor[second]
        if min(delta0, delta1) < min_temperature_difference_c:
            excluded["small_temperature_difference"] += 1
            continue
        hours = seconds / 3600.0
        midpoint_delta = (delta0 + delta1) / 2.0
        rate = (room[second] - room[first]) / hours
        xs.append(midpoint_delta)
        ys.append(rate)

    if len(xs) < min_intervals:
        return {
            "status": "insufficient_free_cooling_data",
            "identified_parameter": "H_over_C_per_hour",
            "estimate": None,
            "valid_interval_count": len(xs),
            "minimum_required_intervals": min_intervals,
            "excluded_intervals": excluded,
        }
    denominator = sum(value * value for value in xs)
    if denominator <= 0:
        return {"status": "unidentifiable_temperature_excitation", "estimate": None}
    slope = sum(x * y for x, y in zip(xs, ys)) / denominator
    ratio = -slope
    residuals = [y - slope * x for x, y in zip(xs, ys)]
    mean_y = sum(ys) / len(ys)
    total = sum((value - mean_y) ** 2 for value in ys)
    residual_sum = sum(value * value for value in residuals)
    r_squared = 1.0 - residual_sum / total if total > 0 else None
    if ratio <= 0 or not math.isfinite(ratio):
        return {
            "status": "non_physical_or_confounded_fit",
            "identified_parameter": "H_over_C_per_hour",
            "estimate": None,
            "valid_interval_count": len(xs),
            "excluded_intervals": excluded,
        }
    return {
        "status": "candidate_estimate",
        "identified_parameter": "H_over_C_per_hour",
        "estimate": round(ratio, 6),
        "unit": "1/hour",
        "valid_interval_count": len(xs),
        "r_squared": round(r_squared, 4) if r_squared is not None else None,
        "excluded_intervals": excluded,
        "method": "through_origin_least_squares_free_cooling",
        "limitations": [
            "Identifies H/C only; absolute heat-loss coefficient and thermal capacity remain inseparable.",
            "Solar and internal gains can bias the estimate unless measured or excluded.",
            "Room temperature is not slab temperature or delivered heat.",
            "A candidate estimate requires independent validation before use.",
        ],
    }


def simulate_historical_room(
    rows: list[dict[str, Any]],
    *,
    room_temperature_entity: str,
    outdoor_temperature_entity: str,
    initial_temperature_c: float,
    capacity_wh_per_k: float,
    conductance_w_per_k: float,
    gain_entity: str | None = None,
    max_gap_seconds: float = 900.0,
    max_step_seconds: float = 300.0,
) -> dict[str, Any]:
    """Replay a single-room RC model against historical boundary/gain samples.

    Uses zero-order-held inputs and explicit substeps. Observed room
    temperatures are used only for scoring, never to reset the simulated
    state after the initial timestamp.
    """
    if capacity_wh_per_k <= 0 or conductance_w_per_k <= 0:
        return {"status": "invalid_parameters", "predictions": []}
    room_points = numeric_series(rows, room_temperature_entity)
    outdoor = dict(numeric_series(rows, outdoor_temperature_entity))
    gains = dict(numeric_series(rows, gain_entity)) if gain_entity else {}
    if len(room_points) < 2 or not outdoor:
        return {"status": "insufficient_history", "predictions": []}
    start, end = room_points[0][0], room_points[-1][0]
    input_times = sorted(set(outdoor) | set(gains))
    if not input_times:
        return {"status": "insufficient_boundary_samples", "predictions": []}

    def held(series, stamp, default=None):
        prior = [time for time in series if time <= stamp]
        if not prior:
            return default
        return series[max(prior)]

    simulated = float(initial_temperature_c)
    predictions = []
    skipped = 0
    for observed_at, observed in room_points:
        if observed_at < start or observed_at > end:
            continue
        outside = held(outdoor, observed_at)
        if outside is None:
            skipped += 1
            continue
        if predictions:
            previous = predictions[-1]["timestamp"]
            seconds = (observed_at - _timestamp(previous)).total_seconds()
            if seconds <= 0 or seconds > max_gap_seconds:
                skipped += 1
                continue
            steps = max(1, math.ceil(seconds / max_step_seconds))
            dt_hours = seconds / steps / 3600.0
            for step in range(steps):
                step_time = _timestamp(previous).timestamp() + (step + 0.5) * seconds / steps
                from datetime import datetime
                stamp = datetime.fromtimestamp(step_time, tz=timezone.utc)
                outside_step = held(outdoor, stamp, outside)
                gain_step = held(gains, stamp, 0.0) or 0.0
                rate_w = conductance_w_per_k * (outside_step - simulated) + gain_step
                simulated += rate_w * dt_hours / capacity_wh_per_k
        predictions.append({
            "timestamp": observed_at.isoformat(),
            "observed_temperature_c": round(observed, 4),
            "predicted_temperature_c": round(simulated, 4),
            "outdoor_temperature_c": round(outside, 4),
        })
    if len(predictions) < 2:
        return {"status": "insufficient_replay_coverage", "predictions": predictions, "skipped_intervals": skipped}
    return {
        "status": "available",
        "model": "single_room_lumped_rc",
        "predictions": predictions,
        "prediction_count": len(predictions),
        "skipped_intervals": skipped,
        "parameter_source": "caller_supplied_not_fitted_on_this_replay",
    }


def validate_historical_simulation(
    simulation: dict[str, Any],
    *,
    validation_start: str | None = None,
    validation_end: str | None = None,
) -> dict[str, Any]:
    """Score a historical replay over an explicitly selected holdout period."""
    if simulation.get("status") != "available":
        return {"status": "insufficient_simulation", "metrics": None}
    start_at = _timestamp(validation_start) if validation_start else None
    end_at = _timestamp(validation_end) if validation_end else None
    errors = []
    for item in simulation.get("predictions", []):
        stamp = _timestamp(item.get("timestamp"))
        if stamp is None or (start_at and stamp < start_at) or (end_at and stamp > end_at):
            continue
        observed = _number(item.get("observed_temperature_c"))
        predicted = _number(item.get("predicted_temperature_c"))
        if observed is not None and predicted is not None:
            errors.append(predicted - observed)
    if len(errors) < 2:
        return {"status": "insufficient_validation_samples", "sample_count": len(errors), "metrics": None}
    mae = sum(abs(error) for error in errors) / len(errors)
    rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
    bias = sum(errors) / len(errors)
    return {
        "status": "scored",
        "sample_count": len(errors),
        "metrics": {
            "mae_c": round(mae, 4),
            "rmse_c": round(rmse, 4),
            "bias_c": round(bias, 4),
            "max_absolute_error_c": round(max(abs(error) for error in errors), 4),
        },
        "error_definition": "predicted_minus_observed",
        "holdout_period": {"start": validation_start, "end": validation_end},
        "calibration_data_reused": False,
    }
