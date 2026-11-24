"""Chronological, coupled multi-zone passive replay on held-out Recorder data.

This is a normalized G/C response replay, not an absolute physical RC simulation.
It uses measured outdoor and (when fitted) attic temperatures as boundaries and
never converts normalized response coefficients into W/K.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _timestamp(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _off(value: Any) -> bool:
    if isinstance(value, bool):
        return value is False
    if isinstance(value, (int, float)):
        return value == 0
    return str(value).strip().lower() in {"off", "false", "0", "closed", "closed_position"}


def _fresh(row: dict, key: str, maximum_age_minutes: float) -> bool:
    age = _number((row.get("channels") or {}).get(key + "_age_minutes"))
    return age is None or age <= maximum_age_minutes


def _scaled_channel(row: dict, key: str, scales: dict[str, float]) -> float | None:
    channels = row.get("channels") or {}
    value = _number(channels.get(key))
    if value is None or not _fresh(row, key, 45.0):
        return None
    unit = str(channels.get(key + "_unit") or "").strip()
    if unit and unit not in scales:
        return None
    return value * scales.get(unit, 1.0)


def build_multizone_historical_replay(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    response_identification: dict[str, Any] | None,
    interval_minutes: int = 30,
    max_age_minutes: float = 45.0,
    solar_max_w_m2: float = 30.0,
    ac_power_max_w: float = 50.0,
    require_ac_power: bool = False,
    require_boiler_burner_off: bool = False,
    require_boiler_modulation_zero: bool = False,
) -> dict[str, Any]:
    """Replay all fitted rooms recursively over a common chronological holdout.

    Room states are initialized once per continuous eligible segment. During a
    segment, predicted room temperatures feed the other rooms' equations; they
    are never reset to measured temperatures after each interval. Gaps and
    excluded operating intervals start a new explicitly counted segment.
    """
    if interval_minutes <= 0 or max_age_minutes < 0:
        raise ValueError("Invalid multi-zone replay cadence or freshness limit")
    identification = response_identification or {}
    room_ids = [str(room.get("id", "unknown")) for room in rooms]
    fits = identification.get("rooms") or {}
    if len(room_ids) < 2:
        return {
            "status": "insufficient_zones",
            "method": "recursive_normalized_multi_zone_holdout_replay",
            "note": "At least two configured rooms are required.",
        }

    models: dict[str, dict] = {}
    training_ends = []
    missing_fits = []
    for room_id in room_ids:
        fit = fits.get(room_id) or {}
        coefficients = fit.get("normalized_rates_per_hour")
        predictors = fit.get("predictors") or []
        training_end = _timestamp(fit.get("training_end"))
        if not isinstance(coefficients, dict) or not predictors or training_end is None:
            missing_fits.append(room_id)
            continue
        if any(_number(coefficients.get(key)) is None for key in predictors):
            missing_fits.append(room_id)
            continue
        models[room_id] = {
            "predictors": list(predictors),
            "coefficients": {key: float(coefficients[key]) for key in predictors},
            "intercept": _number(fit.get("intercept_c_per_hour")) or 0.0,
        }
        training_ends.append(training_end)
    if missing_fits:
        return {
            "status": "withheld_missing_room_response_fits",
            "method": "recursive_normalized_multi_zone_holdout_replay",
            "rooms_missing_fits": missing_fits,
            "note": "Every configured room needs a chronological training fit before a coupled replay is meaningful.",
        }

    cutoff = max(training_ends)
    parsed = []
    for sample in samples:
        stamp = _timestamp(sample.get("timestamp"))
        if stamp is not None and stamp >= cutoff:
            parsed.append((stamp, sample))
    parsed.sort(key=lambda item: item[0])
    if not parsed:
        return {
            "status": "insufficient_holdout_history",
            "method": "recursive_normalized_multi_zone_holdout_replay",
            "training_end": cutoff.isoformat(),
            "eligible_intervals": 0,
        }

    room_config = {str(room.get("id", "unknown")): room for room in rooms}
    attic_required = any("attic" in model["predictors"] for model in models.values())
    exclusions = {
        "missing_or_stale_room_temperature": 0,
        "demand_not_off_or_window_not_closed": 0,
        "outdoor_missing_or_stale": 0,
        "solar_missing_or_high": 0,
        "ac_active_or_unavailable": 0,
        "boiler_active_or_unavailable": 0,
        "attic_missing_or_stale": 0,
        "invalid_interval": 0,
    }
    solar_scales = {"W/m2": 1.0, "W/m²": 1.0, "w/m2": 1.0, "w/m²": 1.0, "kW/m2": 1000.0, "kW/m²": 1000.0, "kw/m2": 1000.0, "kw/m²": 1000.0}
    power_scales = {"W": 1.0, "w": 1.0, "watt": 1.0, "watts": 1.0, "kW": 1000.0, "kw": 1000.0, "MW": 1_000_000.0, "mW": 0.001}

    def clean(sample: dict) -> tuple[bool, str | None, dict[str, float]]:
        room_values = {}
        room_data = sample.get("rooms") or {}
        for room_id in room_ids:
            record = room_data.get(room_id) or {}
            temp = _number(record.get("temperature"))
            age = _number(record.get("temperature_age_minutes"))
            if temp is None or (age is not None and age > max_age_minutes):
                return False, "missing_or_stale_room_temperature", {}
            demand_age = _number(record.get("demand_age_minutes"))
            window_age = _number(record.get("window_age_minutes"))
            if ((demand_age is not None and demand_age > max_age_minutes)
                    or (window_age is not None and window_age > max_age_minutes)
                    or not _off(record.get("demand"))
                    or not _off(record.get("window"))):
                return False, "demand_not_off_or_window_not_closed", {}
            room_values[room_id] = temp

        outside = _number((sample.get("channels") or {}).get("outdoor_temperature"))
        if outside is None or not _fresh(sample, "outdoor_temperature", max_age_minutes):
            return False, "outdoor_missing_or_stale", {}
        solar = _scaled_channel(sample, "solar_radiation", solar_scales)
        if solar is None or solar > solar_max_w_m2:
            return False, "solar_missing_or_high", {}
        ac_raw = _number((sample.get("channels") or {}).get("ac_power"))
        ac_unit = str((sample.get("channels") or {}).get("ac_power_unit") or "").strip()
        if ac_raw is not None:
            if ac_unit and ac_unit not in power_scales:
                return False, "ac_active_or_unavailable", {}
            if ac_raw * power_scales.get(ac_unit, 1.0) > ac_power_max_w:
                return False, "ac_active_or_unavailable", {}
        elif require_ac_power:
            return False, "ac_active_or_unavailable", {}

        channels = sample.get("channels") or {}
        burner = channels.get("boiler_burner")
        if require_boiler_burner_off and not _off(burner):
            return False, "boiler_active_or_unavailable", {}
        modulation = _number(channels.get("boiler_modulation"))
        if require_boiler_modulation_zero and (modulation is None or modulation > 0.1):
            return False, "boiler_active_or_unavailable", {}
        if attic_required:
            attic = _number(channels.get("attic_temperature"))
            if attic is None or not _fresh(sample, "attic_temperature", max_age_minutes):
                return False, "attic_missing_or_stale", {}
            room_values["attic"] = attic
        room_values["outdoor"] = outside
        return True, None, room_values

    errors = {room_id: [] for room_id in room_ids}
    baseline_errors = {room_id: [] for room_id in room_ids}
    predicted_state: dict[str, float] | None = None
    previous_observed: dict[str, float] | None = None
    previous_row = None
    previous_stamp = None
    first_replay_stamp = None
    last_replay_stamp = None
    eligible_intervals = 0
    reinitialized_segments = 0
    for stamp, sample in parsed:
        is_clean, reason, observed = clean(sample)
        if not is_clean:
            exclusions[reason] += 1
            predicted_state = None
            previous_observed = None
            previous_row = None
            previous_stamp = None
            continue
        if predicted_state is None:
            predicted_state = {room_id: observed[room_id] for room_id in room_ids}
            previous_observed = dict(observed)
            previous_row = sample
            previous_stamp = stamp
            reinitialized_segments += 1
            if first_replay_stamp is None:
                first_replay_stamp = stamp
            last_replay_stamp = stamp
            continue

        elapsed_minutes = (stamp - previous_stamp).total_seconds() / 60
        if abs(elapsed_minutes - interval_minutes) > 1:
            exclusions["invalid_interval"] += 1
            predicted_state = {room_id: observed[room_id] for room_id in room_ids}
            previous_observed = dict(observed)
            previous_row = sample
            previous_stamp = stamp
            reinitialized_segments += 1
            last_replay_stamp = stamp
            continue

        previous_channels = previous_row.get("channels") or {}
        current_channels = sample.get("channels") or {}
        outside0 = _number(previous_channels.get("outdoor_temperature"))
        outside1 = _number(current_channels.get("outdoor_temperature"))
        if outside0 is None or outside1 is None:
            exclusions["outdoor_missing_or_stale"] += 1
            predicted_state = None
            previous_observed = None
            previous_row = None
            previous_stamp = None
            continue
        outside_mid = (outside0 + outside1) / 2
        attic_mid = None
        if attic_required:
            attic0 = _number(previous_channels.get("attic_temperature"))
            attic1 = _number(current_channels.get("attic_temperature"))
            if attic0 is None or attic1 is None:
                exclusions["attic_missing_or_stale"] += 1
                predicted_state = None
                previous_observed = None
                previous_row = None
                previous_stamp = None
                continue
            attic_mid = (attic0 + attic1) / 2

        dt_hours = elapsed_minutes / 60.0
        rates = {}
        for room_id in room_ids:
            model = models[room_id]
            current_prediction = predicted_state[room_id]
            rate = model["intercept"]
            for predictor in model["predictors"]:
                coefficient = model["coefficients"][predictor]
                if predictor == "envelope":
                    gradient = outside_mid - current_prediction
                elif predictor == "attic":
                    gradient = attic_mid - current_prediction
                else:
                    gradient = predicted_state[predictor] - current_prediction
                rate += coefficient * gradient
            rates[room_id] = rate

        next_state = {
            room_id: predicted_state[room_id] + rates[room_id] * dt_hours
            for room_id in room_ids
        }
        for room_id in room_ids:
            error = next_state[room_id] - observed[room_id]
            errors[room_id].append(error)
            baseline_errors[room_id].append(previous_observed[room_id] - observed[room_id])
        predicted_state = next_state
        previous_observed = dict(observed)
        previous_row = sample
        previous_stamp = stamp
        eligible_intervals += 1
        last_replay_stamp = stamp

    room_metrics = {}
    all_errors = []
    all_baseline_errors = []
    for room_id in room_ids:
        values = errors[room_id]
        baseline = baseline_errors[room_id]
        count = len(values)
        mae = sum(abs(value) for value in values) / count if count else None
        rmse = math.sqrt(sum(value * value for value in values) / count) if count else None
        bias = sum(values) / count if count else None
        baseline_mae = sum(abs(value) for value in baseline) / count if count else None
        room_metrics[room_id] = {
            "sample_count": count,
            "mae_c": round(mae, 4) if mae is not None else None,
            "rmse_c": round(rmse, 4) if rmse is not None else None,
            "bias_c": round(bias, 4) if bias is not None else None,
            "persistence_baseline_mae_c": round(baseline_mae, 4) if baseline_mae is not None else None,
            "improved_over_persistence": mae < baseline_mae if mae is not None and baseline_mae is not None else None,
        }
        all_errors.extend(values)
        all_baseline_errors.extend(baseline)

    if eligible_intervals == 0:
        status = "insufficient_clean_holdout_intervals"
    else:
        status = "available"
    aggregate_mae = sum(abs(value) for value in all_errors) / len(all_errors) if all_errors else None
    aggregate_rmse = math.sqrt(sum(value * value for value in all_errors) / len(all_errors)) if all_errors else None
    baseline_mae = sum(abs(value) for value in all_baseline_errors) / len(all_baseline_errors) if all_baseline_errors else None
    return {
        "status": status,
        "method": "recursive_normalized_multi_zone_holdout_replay",
        "training_cutoff": cutoff.isoformat(),
        "start": first_replay_stamp.isoformat() if first_replay_stamp else None,
        "end": last_replay_stamp.isoformat() if last_replay_stamp else None,
        "eligible_intervals": eligible_intervals,
        "reinitialized_segments": reinitialized_segments,
        "attic_boundary_used": attic_required,
        "aggregate_mae_c": round(aggregate_mae, 4) if aggregate_mae is not None else None,
        "aggregate_rmse_c": round(aggregate_rmse, 4) if aggregate_rmse is not None else None,
        "persistence_baseline_mae_c": round(baseline_mae, 4) if baseline_mae is not None else None,
        "improved_over_persistence": aggregate_mae < baseline_mae if aggregate_mae is not None and baseline_mae is not None else None,
        "rooms": room_metrics,
        "exclusions": exclusions,
        "units": "normalized G/C coefficients in 1/hour; temperature error in °C",
        "interpretation": (
            "Predicted room states are recursively fed into the coupled network during each continuous segment; "
            "they are not reset to measured temperatures at every interval. This validates normalized passive "
            "response only, not absolute W/K conductances, room capacities, UFH heat delivery or an enabled physical RC simulation."
        ),
        "raw_history_retained": False,
    }
