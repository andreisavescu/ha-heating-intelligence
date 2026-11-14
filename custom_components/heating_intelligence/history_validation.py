"""Recorder-backed historical data coverage for model calibration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    ac_power_scale_to_w: float = 1.0,
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
                    ac_power = float(ac_raw) * ac_power_scale_to_w
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



def build_preliminary_room_calibration(
    history: dict[str, list[Any]],
    rooms: list[dict[str, Any]],
    outdoor_temperature_entity: str | None,
) -> dict[str, Any]:
    """Estimate an apparent free-cooling time constant from clean Recorder intervals.

    This is a directional, data-derived estimate, not an independently
    identified room capacity or envelope conductance. Adjacent-room heat
    exchange, solar/internal gains and unmeasured plant input remain confounders.
    """
    from statistics import median

    outdoor = _timeline(history, outdoor_temperature_entity)
    results: dict[str, Any] = {}
    for room in rooms:
        room_id = room.get("id", "unknown")
        temperatures = []
        for state in history.get(room.get("temperature_entity"), []):
            stamp = _timestamp(state)
            value = _state_value(state)
            if stamp is not None and value is not None:
                temperatures.append((stamp, value))
        temperatures.sort(key=lambda item: item[0])
        demand = _timeline(history, room.get("demand_entity"))
        windows = _timeline(history, room.get("window_entity"))
        observations = []
        exclusions = {"unknown_demand": 0, "window_not_confirmed_closed": 0,
                      "outdoor_unavailable": 0, "invalid_interval": 0,
                      "insufficient_indoor_outdoor_delta": 0}
        previous = None
        for stamp, temp in temperatures:
            mode = _switch_state(_latest_at(demand, stamp))
            window = _switch_state(_latest_at(windows, stamp))
            outside_raw = _latest_at(outdoor, stamp)
            try:
                outside = float(outside_raw)
            except (TypeError, ValueError):
                outside = None
            if mode is not False:
                exclusions["unknown_demand"] += 1
                previous = None
                continue
            if window is not False:
                exclusions["window_not_confirmed_closed"] += 1
                previous = None
                continue
            if outside is None or not math.isfinite(outside):
                exclusions["outdoor_unavailable"] += 1
                previous = None
                continue
            delta = temp - outside
            if delta < 1.0:
                exclusions["insufficient_indoor_outdoor_delta"] += 1
                previous = None
                continue
            if previous is not None:
                prev_stamp, prev_temp, prev_delta = previous
                hours = (stamp - prev_stamp).total_seconds() / 3600
                if 1 / 6 <= hours <= 1.0 and prev_delta >= 1.0:
                    observations.append(((prev_delta + delta) / 2,
                                         (temp - prev_temp) / hours))
                else:
                    exclusions["invalid_interval"] += 1
            previous = (stamp, temp, delta)

        # OLS: dT/dt = intercept + slope * (Troom - Tout).
        # A negative slope implies an apparent first-order decay time constant.
        n = len(observations)
        tau = slope = r_squared = None
        if n >= 8:
            xs = [item[0] for item in observations]
            ys = [item[1] for item in observations]
            x_bar, y_bar = sum(xs) / n, sum(ys) / n
            sxx = sum((x - x_bar) ** 2 for x in xs)
            if sxx > 1e-9:
                slope = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys)) / sxx
                intercept = y_bar - slope * x_bar
                residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
                total = sum((y - y_bar) ** 2 for y in ys)
                r_squared = max(0.0, min(1.0, 1 - residual / total)) if total > 1e-9 else None
                if slope < -1e-5:
                    tau = -1 / slope
        status = "preliminary_estimate" if tau is not None else "insufficient_or_non_identifiable_data"
        results[room_id] = {
            "status": status,
            "method": "free_cooling_ols_dTdt_vs_indoor_outdoor_delta",
            "valid_intervals": n,
            "apparent_time_constant_hours": round(tau, 2) if tau is not None else None,
            "decay_slope_per_hour": round(slope, 5) if slope is not None else None,
            "fit_r_squared": round(r_squared, 3) if r_squared is not None else None,
            "excluded_observations": exclusions,
            "interpretation": (
                "Directional first-order decay estimate from demand-off, confirmed-closed-window periods. "
                "It includes room coupling and unmeasured gains; do not interpret it as a physical room "
                "capacity or envelope conductance."
            ),
        }
    available = sum(1 for item in results.values() if item["status"] == "preliminary_estimate")
    return {
        "status": "preliminary_estimates_available" if available else "collecting_clean_cooling_data",
        "rooms_with_estimates": available,
        "rooms_analysed": len(results),
        "rooms": results,
        "method": "apparent_free_cooling_time_constant",
        "note": "First-pass house-specific calibration evidence. Parameters are directional and require held-out validation.",
    }


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


def build_held_out_room_validation(
    history: dict[str, list[Any]],
    rooms: list[dict[str, Any]],
    outdoor_temperature_entity: str | None,
    outdoor_max_age_minutes: float = 90.0,
    train_fraction: float = 0.7,
) -> dict[str, Any]:
    """Fit a first-order free-cooling response on early history and score later data.

    The chronological holdout is never used to fit the model. This validates
    short-horizon room-temperature prediction only; it does not establish
    physical heat capacity or envelope conductance.
    """
    from statistics import median

    outdoor = _timeline(history, outdoor_temperature_entity)
    results: dict[str, Any] = {}
    for room in rooms:
        room_id = room.get("id", "unknown")
        temperatures = []
        for state in history.get(room.get("temperature_entity"), []):
            stamp = _timestamp(state)
            temp = _state_value(state)
            if stamp is not None and temp is not None:
                temperatures.append((stamp, temp))
        temperatures.sort(key=lambda item: item[0])
        demand = _timeline(history, room.get("demand_entity"))
        windows = _timeline(history, room.get("window_entity"))
        intervals = []
        exclusions = {"unknown_or_active_demand": 0, "window_not_closed": 0,
                      "outdoor_missing_or_stale": 0, "invalid_interval": 0}
        for (prev_stamp, prev_temp), (stamp, temp) in zip(temperatures, temperatures[1:]):
            hours = (stamp - prev_stamp).total_seconds() / 3600
            if not (1 / 6 <= hours <= 1.0):
                exclusions["invalid_interval"] += 1
                continue
            if _switch_state(_latest_at(demand, stamp)) is not False:
                exclusions["unknown_or_active_demand"] += 1
                continue
            if _switch_state(_latest_at(windows, stamp)) is not False:
                exclusions["window_not_closed"] += 1
                continue
            outdoor_stamp = None
            outdoor_index = bisect_right(outdoor, stamp, key=lambda item: item[0]) - 1
            if outdoor_index >= 0:
                outdoor_stamp = outdoor[outdoor_index][0]
            outside_raw = _latest_at(outdoor, stamp)
            if (outside_raw is None or outdoor_stamp is None
                    or (stamp - outdoor_stamp).total_seconds() > outdoor_max_age_minutes * 60):
                exclusions["outdoor_missing_or_stale"] += 1
                continue
            outside = _finite_number_or_none(outside_raw)
            if outside is None:
                exclusions["outdoor_missing_or_stale"] += 1
                continue
            delta = prev_temp - outside
            if delta < 1.0:
                continue
            intervals.append({
                "timestamp": stamp,
                "previous_temperature_c": prev_temp,
                "delta_c": delta,
                "hours": hours,
                "observed_rate_c_per_hour": (temp - prev_temp) / hours,
                "observed_temperature_c": temp,
            })

        split = int(len(intervals) * train_fraction)
        train = intervals[:split]
        holdout = intervals[split:]
        slope = intercept = tau = None
        train_r2 = None
        validation_mae = baseline_mae = validation_rmse = None
        status = "insufficient_train_or_holdout_history"
        if len(train) >= 8 and len(holdout) >= 4:
            xs = [item["delta_c"] for item in train]
            ys = [item["observed_rate_c_per_hour"] for item in train]
            x_bar, y_bar = sum(xs) / len(xs), sum(ys) / len(ys)
            sxx = sum((x - x_bar) ** 2 for x in xs)
            if sxx > 1e-9:
                slope = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys)) / sxx
                intercept = y_bar - slope * x_bar
                residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
                total = sum((y - y_bar) ** 2 for y in ys)
                train_r2 = max(0.0, min(1.0, 1 - residual / total)) if total > 1e-9 else None
                if slope < -1e-5:
                    tau = -1 / slope
                errors = []
                baseline_errors = []
                for item in holdout:
                    predicted = item["previous_temperature_c"] + (
                        intercept + slope * item["delta_c"]
                    ) * item["hours"]
                    errors.append(predicted - item["observed_temperature_c"])
                    baseline_errors.append(
                        item["previous_temperature_c"] - item["observed_temperature_c"]
                    )
                validation_mae = sum(abs(error) for error in errors) / len(errors)
                baseline_mae = sum(abs(error) for error in baseline_errors) / len(baseline_errors)
                validation_rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
                status = (
                    "improved_over_persistence"
                    if validation_mae < baseline_mae
                    else "did_not_improve_over_persistence"
                )
            else:
                status = "non_identifiable_training_excitation"
        results[room_id] = {
            "status": status,
            "method": "chronological_holdout_first_order_free_cooling",
            "training_intervals": len(train),
            "holdout_intervals": len(holdout),
            "valid_intervals": len(intervals),
            "train_fraction": train_fraction,
            "decay_slope_per_hour": round(slope, 6) if slope is not None else None,
            "intercept_c_per_hour": round(intercept, 6) if intercept is not None else None,
            "apparent_time_constant_hours": round(tau, 2) if tau is not None else None,
            "training_r_squared": round(train_r2, 3) if train_r2 is not None else None,
            "holdout_mae_c": round(validation_mae, 4) if validation_mae is not None else None,
            "persistence_baseline_mae_c": round(baseline_mae, 4) if baseline_mae is not None else None,
            "holdout_rmse_c": round(validation_rmse, 4) if validation_rmse is not None else None,
            "excluded_intervals": exclusions,
            "interpretation": (
                "A chronological holdout tests prediction of clean free-cooling intervals against "
                "a persistence baseline. Even a successful score validates only this operating "
                "regime; room coupling and unmeasured solar/internal/plant gains remain confounders, "
                "so this is not a physical capacity or envelope-conductance measurement."
            ),
        }
    successful = sum(item["status"] == "improved_over_persistence" for item in results.values())
    return {
        "status": "holdout_improved_for_some_rooms" if successful else "no_room_passed_holdout_yet",
        "rooms_improved_over_persistence": successful,
        "rooms_analysed": len(results),
        "rooms": results,
        "method": "chronological_holdout_first_order_free_cooling",
        "note": "Held-out prediction is a validation gate, not proof that fitted coefficients are uniquely physical.",
    }


def _finite_number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_house_effective_rc_calibration(
    history: dict[str, list[Any]],
    rooms: list[dict[str, Any]],
    outdoor_temperature_entity: str | None,
    heat_loss_coefficient_w_k: float | None,
    solar_radiation_entity: str | None = None,
    solar_exclusion_threshold_w_m2: float = 50.0,
    outdoor_max_age_minutes: float = 90.0,
    heat_loss_sensitivity_range_w_k: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Combine independently estimated house heat loss and night cooling tau.

    The result is an aggregate effective C=H*tau estimate, not a room-level
    physical capacity. It is withheld without a positive measured-performance
    heat-loss coefficient and clean low-solar, demand-off, closed-window data.
    """
    outdoor = _timeline(history, outdoor_temperature_entity)
    solar = _timeline(history, solar_radiation_entity)
    heat_loss = _finite_number_or_none(heat_loss_coefficient_w_k)
    if heat_loss is None or heat_loss <= 0:
        return {
            "status": "withheld_heat_loss_coefficient_unavailable",
            "effective_building_thermal_capacity_wh_per_k": None,
            "heat_loss_coefficient_w_k": None,
            "apparent_time_constant_hours": None,
            "valid_intervals": 0,
            "note": "A separately estimated positive whole-house heat-loss coefficient is required; room cooling slopes alone cannot identify absolute capacity.",
        }
    room_data = []
    for room in rooms:
        entity = room.get("temperature_entity")
        if not entity:
            continue
        points = []
        for state in history.get(entity, []):
            stamp, temp = _timestamp(state), _state_value(state)
            if stamp is not None and temp is not None:
                points.append((stamp, temp))
        points.sort(key=lambda item: item[0])
        if points:
            room_data.append((room, points, _timeline(history, room.get("demand_entity")),
                              _timeline(history, room.get("window_entity"))))
    if not room_data or not outdoor:
        return {
            "status": "insufficient_history",
            "effective_building_thermal_capacity_wh_per_k": None,
            "heat_loss_coefficient_w_k": round(heat_loss, 2),
            "apparent_time_constant_hours": None,
            "valid_intervals": 0,
            "note": "Indoor and outdoor Recorder history are required.",
        }

    # Use the union of indoor sample times, with area-weighted latest room
    # readings. A room contributes only when its temperature is recent and
    # both demand and window states are explicitly known and inactive.
    stamps = sorted({stamp for _, points, _, _ in room_data for stamp, _ in points})
    aggregate = []
    exclusions = {
        "insufficient_room_coverage": 0, "demand_or_window_not_confirmed": 0,
        "outdoor_missing_or_stale": 0, "solar_gain_period": 0,
    }
    total_weight = sum(
        max(0.0, _finite_number_or_none(room.get("area_m2")) or 0.0)
        for room, _, _, _ in room_data
    )
    for stamp in stamps:
        weighted_temp = weight_sum = 0.0
        eligible = 0
        for room, points, demand, windows in room_data:
            idx = bisect_right(points, stamp, key=lambda item: item[0]) - 1
            if idx < 0 or (stamp - points[idx][0]).total_seconds() > 90 * 60:
                continue
            if _switch_state(_latest_at(demand, stamp)) is not False or _switch_state(_latest_at(windows, stamp)) is not False:
                continue
            weight = _finite_number_or_none(room.get("area_m2"))
            weight = weight if weight is not None and weight > 0 else 1.0
            weighted_temp += points[idx][1] * weight
            weight_sum += weight
            eligible += 1
        if eligible < max(1, math.ceil(len(room_data) * 0.7)) or weight_sum <= 0:
            exclusions["insufficient_room_coverage"] += 1
            continue
        outdoor_idx = bisect_right(outdoor, stamp, key=lambda item: item[0]) - 1
        if outdoor_idx < 0 or (stamp - outdoor[outdoor_idx][0]).total_seconds() > outdoor_max_age_minutes * 60:
            exclusions["outdoor_missing_or_stale"] += 1
            continue
        if solar:
            solar_idx = bisect_right(solar, stamp, key=lambda item: item[0]) - 1
            if solar_idx >= 0:
                solar_value = _finite_number_or_none(solar[solar_idx][1])
                if solar_value is not None and solar_value > solar_exclusion_threshold_w_m2:
                    exclusions["solar_gain_period"] += 1
                    continue
        aggregate.append((stamp, weighted_temp / weight_sum, float(outdoor[outdoor_idx][1])))

    observations = []
    for first, second in zip(aggregate, aggregate[1:]):
        stamp0, temp0, outside0 = first
        stamp1, temp1, outside1 = second
        hours = (stamp1 - stamp0).total_seconds() / 3600
        delta = (temp0 + temp1 - outside0 - outside1) / 2
        if 1 / 6 <= hours <= 1.0 and delta >= 1.0:
            observations.append((delta, (temp1 - temp0) / hours))
    n = len(observations)
    slope = intercept = r_squared = tau = capacity = None
    status = "insufficient_clean_cooling_history"
    if n >= 12:
        xs, ys = [item[0] for item in observations], [item[1] for item in observations]
        x_bar, y_bar = sum(xs) / n, sum(ys) / n
        sxx = sum((x - x_bar) ** 2 for x in xs)
        if sxx > 1e-9:
            slope = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys)) / sxx
            intercept = y_bar - slope * x_bar
            residual = sum((y - (intercept + slope * x)) ** 2 for x, y in observations)
            total = sum((y - y_bar) ** 2 for y in ys)
            r_squared = max(0.0, min(1.0, 1 - residual / total)) if total > 1e-9 else None
            if slope < -1e-5:
                tau = -1 / slope
                capacity = heat_loss * tau
                status = "effective_aggregate_capacity_estimate"
            else:
                status = "non_identifiable_cooling_response"
    # Resample contiguous cooling-response observations, preserving short
    # temporal dependence. These are sensitivity ranges, not formal CIs.
    capacity_resamples = []
    bootstrap_block_length = min(4, max(1, n // 4))
    bootstrap_replicates = 80 if n >= 40 else 0
    if bootstrap_replicates:
        for replicate in range(bootstrap_replicates):
            selected = []
            block_number = 0
            while len(selected) < n:
                max_start = n - bootstrap_block_length
                start_index = (replicate * 37 + block_number * 17) % (max_start + 1)
                selected.extend(observations[start_index:start_index + bootstrap_block_length])
                block_number += 1
            selected = selected[:n]
            bx = [item[0] for item in selected]
            by = [item[1] for item in selected]
            bx_bar, by_bar = sum(bx) / n, sum(by) / n
            denominator = sum((value - bx_bar) ** 2 for value in bx)
            if denominator <= 1e-9:
                continue
            block_slope = sum(
                (x - bx_bar) * (y - by_bar) for x, y in zip(bx, by)
            ) / denominator
            if block_slope < -1e-5:
                capacity_resamples.append(heat_loss * (-1 / block_slope))

    capacity_resampling = {
        "method": "deterministic_moving_block_bootstrap",
        "block_length_observations": bootstrap_block_length,
        "requested_replicates": bootstrap_replicates,
        "successful_replicates": len(capacity_resamples),
        "lower_90_percentile_wh_per_k": None,
        "median_wh_per_k": None,
        "upper_90_percentile_wh_per_k": None,
        "relative_90_percentile_width": None,
        "status": "insufficient_history_for_resampling_sensitivity",
        "note": (
            "This is a resampling sensitivity range, not a formal confidence interval. "
            "It does not include uncertainty in the independent heat-loss coefficient, "
            "sensor bias, unmeasured gains or model misspecification."
        ),
    }
    if len(capacity_resamples) >= 40:
        capacity_resamples.sort()
        lower = capacity_resamples[int(0.05 * (len(capacity_resamples) - 1))]
        middle = capacity_resamples[int(0.50 * (len(capacity_resamples) - 1))]
        upper = capacity_resamples[int(0.95 * (len(capacity_resamples) - 1))]
        capacity_resampling.update({
            "lower_90_percentile_wh_per_k": round(lower, 1),
            "median_wh_per_k": round(middle, 1),
            "upper_90_percentile_wh_per_k": round(upper, 1),
            "relative_90_percentile_width": round(
                (upper - lower) / max(abs(middle), 1e-6), 3
            ),
            "status": "resampling_sensitivity_available",
        })
    capacity_assumption_sensitivity = {
        "status": "withheld_heat_loss_assumption_range_unavailable",
        "lower_wh_per_k": None,
        "median_wh_per_k": None,
        "upper_wh_per_k": None,
        "heat_loss_range_w_k": None,
        "time_constant_range_hours": None,
        "note": "Combines the independently supplied heat-loss assumption range with cooling time-constant resampling. This is a sensitivity envelope, not a confidence interval.",
    }
    if capacity is not None and heat_loss_sensitivity_range_w_k:
        h_min = _finite_number_or_none(heat_loss_sensitivity_range_w_k.get("minimum"))
        h_max = _finite_number_or_none(heat_loss_sensitivity_range_w_k.get("maximum"))
        if h_min is not None and h_max is not None and 0 < h_min <= h_max:
            tau_values = (
                [value / heat_loss for value in capacity_resamples]
                if len(capacity_resamples) >= 40 else [tau]
            )
            tau_values = sorted(value for value in tau_values if value is not None and math.isfinite(value) and value > 0)
            if tau_values:
                tau_low = tau_values[int(0.05 * (len(tau_values) - 1))]
                tau_mid = tau_values[int(0.50 * (len(tau_values) - 1))]
                tau_high = tau_values[int(0.95 * (len(tau_values) - 1))]
                capacity_assumption_sensitivity.update({
                    "status": "combined_heat_loss_and_time_constant_sensitivity_available",
                    "lower_wh_per_k": round(h_min * tau_low, 1),
                    "median_wh_per_k": round(((h_min + h_max) / 2) * tau_mid, 1),
                    "upper_wh_per_k": round(h_max * tau_high, 1),
                    "heat_loss_range_w_k": {"minimum": round(h_min, 2), "maximum": round(h_max, 2)},
                    "time_constant_range_hours": {
                        "minimum": round(tau_low, 2),
                        "median": round(tau_mid, 2),
                        "maximum": round(tau_high, 2),
                    },
                    "note": (
                        "Combines heat-loss assumption endpoints with the 5th/50th/95th percentile "
                        "cooling time constants where block resampling is available; otherwise uses "
                        "the point-estimated time constant. It is a sensitivity envelope, not a "
                        "formal confidence interval, and does not allocate capacity to rooms."
                    ),
                })
    return {
        "status": status,
        "method": "area_weighted_night_cooling_tau_times_independent_house_heat_loss",
        "capacity_assumption_sensitivity": capacity_assumption_sensitivity,
        "effective_building_thermal_capacity_wh_per_k": round(capacity, 1) if capacity is not None else None,
        "heat_loss_coefficient_w_k": round(heat_loss, 2),
        "apparent_time_constant_hours": round(tau, 2) if tau is not None else None,
        "decay_slope_per_hour": round(slope, 6) if slope is not None else None,
        "intercept_c_per_hour": round(intercept, 6) if intercept is not None else None,
        "fit_r_squared": round(r_squared, 3) if r_squared is not None else None,
        "valid_intervals": n,
        "capacity_resampling_sensitivity": capacity_resampling,
        "rooms_included": len(room_data),
        "exclusions": exclusions,
        "interpretation": (
            "Aggregate effective capacity is H_house multiplied by the fitted whole-house cooling time constant. "
            "It is conditional on the independent energy-derived H estimate and assumes the selected low-solar "
            "periods have negligible unmeasured gains. It must not be distributed to rooms or UFH slabs as a "
            "physical capacity; inter-zone exchange and internal gains remain unresolved."
        ),
    }



def build_recorder_calibration_dataset(
    history: dict[str, list[Any]],
    rooms: list[dict[str, Any]],
    channels: dict[str, str | None],
    start: datetime,
    end: datetime,
    interval_minutes: int = 30,
    max_age_minutes: int = 45,
    include_samples: bool = False,
) -> dict[str, Any]:
    """Align Recorder channels onto a bounded common timeline with freshness metadata.

    Values are carried forward only while fresh. The returned sample rows are
    transient calibration inputs; callers should not persist raw Recorder data.
    """
    if interval_minutes <= 0 or max_age_minutes < 0 or end <= start:
        raise ValueError("Invalid calibration dataset time bounds or cadence")

    room_channels: dict[str, dict[str, str | None]] = {}
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        room_channels[room_id] = {
            "temperature": room.get("temperature_entity"),
            "demand": room.get("demand_entity"),
            "window": room.get("window_entity"),
            "humidity": room.get("humidity_entity"),
            "co2": room.get("co2_entity"),
        }

    def calibration_timeline(entity_id):
        if not entity_id:
            return []
        points = []
        for state in history.get(entity_id, []):
            stamp = _timestamp(state)
            raw = state.get("state") if isinstance(state, dict) else getattr(state, "state", None)
            attributes = state.get("attributes", {}) if isinstance(state, dict) else getattr(state, "attributes", {})
            unit = attributes.get("unit_of_measurement") if isinstance(attributes, dict) else None
            if stamp is not None:
                # Preserve invalid transitions and source units alongside values.
                points.append((stamp, raw, unit))
        return sorted(points, key=lambda item: item[0])

    timelines: dict[str, list[tuple[datetime, Any]]] = {}
    for key, entity_id in channels.items():
        timelines[key] = calibration_timeline(entity_id)
    for room_id, configured in room_channels.items():
        for key, entity_id in configured.items():
            timelines[f"room:{room_id}:{key}"] = calibration_timeline(entity_id)

    cadence = timedelta(minutes=interval_minutes)
    max_age = timedelta(minutes=max_age_minutes)
    first = start.astimezone(timezone.utc)
    last = end.astimezone(timezone.utc)
    # Anchor to the requested start to keep rows deterministic across runs.
    samples = []
    channel_counts = {
        key: {"fresh": 0, "missing_or_stale": 0}
        for key in timelines
    }
    room_temperature_count = {room_id: 0 for room_id in room_channels}
    eligible_core_rows = 0
    complete_room_state_rows = 0
    stamp = first
    while stamp <= last:
        row: dict[str, Any] = {"timestamp": stamp.isoformat(), "channels": {}, "rooms": {}}
        fresh_global = {}
        for key, points in timelines.items():
            index = bisect_right(points, stamp, key=lambda item: item[0]) - 1
            value = None
            age_minutes = None
            if index >= 0:
                point_stamp, raw, unit = points[index]
                age = stamp - point_stamp
                age_minutes = max(0.0, age.total_seconds() / 60)
                if age <= max_age and raw not in (None, "unknown", "unavailable"):
                    value = _state_value({"state": raw})
                    if value is None:
                        value = raw
            fresh = value is not None
            channel_counts[key]["fresh" if fresh else "missing_or_stale"] += 1
            if key.startswith("room:"):
                _, room_id, field = key.split(":", 2)
                row["rooms"].setdefault(room_id, {})[field] = value
                row["rooms"][room_id][field + "_age_minutes"] = round(age_minutes, 1) if age_minutes is not None else None
            else:
                row["channels"][key] = value
                row["channels"][key + "_age_minutes"] = round(age_minutes, 1) if age_minutes is not None else None
                row["channels"][key + "_unit"] = unit
                fresh_global[key] = fresh

        available_room_temps = sum(
            row["rooms"].get(room_id, {}).get("temperature") is not None
            for room_id in room_channels
        )
        for room_id in room_channels:
            if row["rooms"].get(room_id, {}).get("temperature") is not None:
                room_temperature_count[room_id] += 1
        outdoor_ok = row["channels"].get("outdoor_temperature") is not None
        core_ok = outdoor_ok and available_room_temps >= max(1, math.ceil(len(room_channels) * 0.7))
        if core_ok:
            eligible_core_rows += 1
        all_room_states_known = bool(room_channels) and all(
            row["rooms"].get(room_id, {}).get("temperature") is not None
            and row["rooms"].get(room_id, {}).get("demand") is not None
            and row["rooms"].get(room_id, {}).get("window") is not None
            for room_id in room_channels
        )
        if core_ok and all_room_states_known:
            complete_room_state_rows += 1
        row["quality"] = {
            "core_environment_and_room_coverage": core_ok,
            "all_room_temperature_demand_window_known": all_room_states_known,
        }
        if include_samples:
            samples.append(row)
        stamp += cadence

    total_rows = int((last - first).total_seconds() // cadence.total_seconds()) + 1
    coverage = {
        key: {
            "fresh_samples": counts["fresh"],
            "missing_or_stale_samples": counts["missing_or_stale"],
            "coverage_percent": round(100 * counts["fresh"] / total_rows, 1) if total_rows else 0.0,
        }
        for key, counts in channel_counts.items()
    }
    return {
        "status": "aligned_dataset_available" if eligible_core_rows else "insufficient_core_coverage",
        "source": "home_assistant_recorder",
        "window_start": first.isoformat(),
        "window_end": last.isoformat(),
        "cadence_minutes": interval_minutes,
        "max_carry_forward_age_minutes": max_age_minutes,
        "aligned_sample_count": total_rows,
        "core_eligible_sample_count": eligible_core_rows,
        "complete_room_state_sample_count": complete_room_state_rows,
        "room_temperature_coverage_samples": room_temperature_count,
        "channel_coverage": coverage,
        "raw_history_retained": False,
        "note": (
            "Common-cadence observations use last-known values only within the freshness limit. "
            "Rows with missing channels remain explicitly marked; no interpolation or fabricated "
            "sensor values are introduced."
        ),
        **({"samples": samples} if include_samples else {}),
    }




def build_ufh_slab_observability(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    interval_minutes: int = 30,
    minimum_on_intervals: int = 4,
    minimum_off_intervals: int = 4,
) -> dict[str, Any]:
    """Assess UFH excitation and room response without inferring slab capacity.

    Pump electrical power is used only as an on/off indicator. Supply/return
    delta-T is a temperature observation, not thermal output without flow.
    """
    if interval_minutes <= 0 or minimum_on_intervals < 1 or minimum_off_intervals < 1:
        raise ValueError("Invalid UFH observability thresholds")
    results = {}
    for floor_id in ("ground_floor", "upstairs"):
        floor_rooms = [
            room for room in rooms
            if room.get("floor", "ground_floor") == floor_id
        ]
        pump_key = f"pump:{floor_id}:power"
        on_rates, off_rates, on_water_delta = [], [], []
        on_count = off_count = skipped = 0
        previous = None
        for sample in samples:
            channels = sample.get("channels") or {}
            room_values = sample.get("rooms") or {}
            try:
                stamp = datetime.fromisoformat(str(sample.get("timestamp")).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                pump_power = _finite_number_or_none(channels.get(pump_key))
                pump_age = _finite_number_or_none(channels.get(pump_key + "_age_minutes"))
                if pump_power is None or (pump_age is not None and pump_age > 45):
                    previous = None
                    skipped += 1
                    continue
                temperatures = [
                    _finite_number_or_none(room_values.get(str(room.get("id")), {}).get("temperature"))
                    for room in floor_rooms
                ]
                temperatures = [value for value in temperatures if value is not None]
                if not temperatures:
                    previous = None
                    skipped += 1
                    continue
                average_temp = sum(temperatures) / len(temperatures)
                supply = _finite_number_or_none(channels.get("water_supply_temperature"))
                return_temp = _finite_number_or_none(channels.get("water_return_temperature"))
                supply_age = _finite_number_or_none(channels.get("water_supply_temperature_age_minutes"))
                return_age = _finite_number_or_none(channels.get("water_return_temperature_age_minutes"))
                water_delta = (
                    supply - return_temp
                    if supply is not None and return_temp is not None
                    and (supply_age is None or supply_age <= 45)
                    and (return_age is None or return_age <= 45)
                    else None
                )
            except (TypeError, ValueError):
                previous = None
                skipped += 1
                continue
            current = (stamp, average_temp, pump_power > 5.0, water_delta)
            if previous is not None:
                elapsed = (stamp - previous[0]).total_seconds() / 60
                if abs(elapsed - interval_minutes) <= 2:
                    rate = (average_temp - previous[1]) / (elapsed / 60)
                    # Use the current sample's pump state; room temperatures are
                    # an observed response, not a direct measure of slab heat.
                    if current[2]:
                        on_count += 1
                        on_rates.append(rate)
                        if water_delta is not None:
                            on_water_delta.append(water_delta)
                    else:
                        off_count += 1
                        off_rates.append(rate)
                else:
                    skipped += 1
            previous = current

        def median_or_none(values):
            ordered = sorted(values)
            if not ordered:
                return None
            middle = len(ordered) // 2
            return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2

        enough_excitation = on_count >= minimum_on_intervals and off_count >= minimum_off_intervals
        results[floor_id] = {
            "status": (
                "pump_on_off_excitation_observed_but_capacity_unidentifiable"
                if enough_excitation else
                "insufficient_pump_excitation_or_history"
            ),
            "pump_on_intervals": on_count,
            "pump_off_intervals": off_count,
            "skipped_intervals": skipped,
            "median_room_temperature_rate_pump_on_c_per_hour": round(median_or_none(on_rates), 4) if on_rates else None,
            "median_room_temperature_rate_pump_off_c_per_hour": round(median_or_none(off_rates), 4) if off_rates else None,
            "median_supply_return_delta_t_pump_on_c": round(median_or_none(on_water_delta), 3) if on_water_delta else None,
            "rooms_included": len(floor_rooms),
            "slab_temperature_available": False,
            "thermal_output_w": None,
            "slab_capacity_wh_per_k": None,
            "note": (
                "Pump-on/off and adjacent-room temperature responses indicate whether useful UFH "
                "excitation exists in Recorder history. They do not identify slab temperature or "
                "absolute slab capacity. Supply-return delta-T is not heat output without water flow; "
                "pump electrical power is auxiliary consumption, not delivered heat."
            ),
        }
    return {
        "status": "available" if any(
            item["status"] == "pump_on_off_excitation_observed_but_capacity_unidentifiable"
            for item in results.values()
        ) else "insufficient_ufh_excitation",
        "interval_minutes": interval_minutes,
        "floors": results,
        "interpretation": (
            "This is an excitation and observability screen only. It does not calibrate UFH slab "
            "capacity or water-side thermal output."
        ),
    }



def build_thermal_input_energy_observability(
    samples: list[dict[str, Any]],
    interval_minutes: int = 30,
    max_age_minutes: float = 45.0,
    minimum_valid_intervals: int = 4,
) -> dict[str, Any]:
    """Derive interval energy only from fresh, monotonic cumulative kWh meters.

    Gas m3 and other non-energy units are intentionally not converted without
    a configured calorific value / meter calibration. AC COP and boiler
    efficiency are reported as explicit sensitivity scenarios, not facts.
    """
    if interval_minutes <= 0 or max_age_minutes < 0 or minimum_valid_intervals < 1:
        raise ValueError("Invalid thermal-input observability thresholds")
    meter_specs = {
        "gas_consumption": {"accepted_units": {"kwh": 1.0, "kw h": 1.0, "wh": 0.001, "mwh": 1000.0}},
        "ac_energy": {"accepted_units": {"kwh": 1.0, "kw h": 1.0, "wh": 0.001, "mwh": 1000.0}},
    }
    results = {}
    for channel, spec in meter_specs.items():
        deltas = []
        invalid = {"missing_or_stale": 0, "unit_not_energy_or_unsupported": 0, "unit_changed": 0,
                   "meter_reset_or_decrease": 0, "invalid_interval": 0}
        previous = None
        for sample in samples:
            channels = sample.get("channels") or {}
            value = _finite_number_or_none(channels.get(channel))
            age = _finite_number_or_none(channels.get(channel + "_age_minutes"))
            unit_raw = channels.get(channel + "_unit")
            unit = str(unit_raw or "").strip().lower().replace(" ", "")
            if value is None or (age is not None and age > max_age_minutes):
                invalid["missing_or_stale"] += 1
                previous = None
                continue
            if unit not in spec["accepted_units"]:
                invalid["unit_not_energy_or_unsupported"] += 1
                previous = None
                continue
            try:
                stamp = datetime.fromisoformat(str(sample.get("timestamp")).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                invalid["invalid_interval"] += 1
                previous = None
                continue
            current = (stamp, value, unit, spec["accepted_units"][unit])
            if previous is not None:
                elapsed = (stamp - previous[0]).total_seconds() / 60
                if previous[2] != unit:
                    invalid["unit_changed"] += 1
                elif not interval_minutes * 0.5 <= elapsed <= interval_minutes * 1.5:
                    invalid["invalid_interval"] += 1
                elif value < previous[1]:
                    invalid["meter_reset_or_decrease"] += 1
                else:
                    delta_kwh = (value - previous[1]) * current[3]
                    if delta_kwh >= 0:
                        deltas.append((delta_kwh, elapsed))
            previous = current
        total_kwh = sum(item[0] for item in deltas)
        valid_count = len(deltas)
        results[channel] = {
            "status": "measured_interval_energy_available" if valid_count >= minimum_valid_intervals else "insufficient_valid_energy_intervals",
            "meter_type": "cumulative_energy" if valid_count else "unverified_or_unavailable",
            "source_unit": sorted({str((sample.get("channels") or {}).get(channel + "_unit")) for sample in samples if (sample.get("channels") or {}).get(channel + "_unit")}) or [],
            "valid_intervals": valid_count,
            "measured_energy_kwh": round(total_kwh, 4) if valid_count >= minimum_valid_intervals else None,
            "mean_input_power_kw": round(total_kwh / (sum(item[1] for item in deltas) / 60), 4) if valid_count >= minimum_valid_intervals and sum(item[1] for item in deltas) > 0 else None,
            "invalid_intervals": invalid,
        }
    gas = results["gas_consumption"]
    if gas["measured_energy_kwh"] is not None:
        gas["useful_heat_sensitivity_kwh"] = {
            "boiler_efficiency_0_80": round(gas["measured_energy_kwh"] * 0.80, 4),
            "boiler_efficiency_0_90": round(gas["measured_energy_kwh"] * 0.90, 4),
            "boiler_efficiency_0_98": round(gas["measured_energy_kwh"] * 0.98, 4),
        }
    ac = results["ac_energy"]
    if ac["measured_energy_kwh"] is not None:
        ac["delivered_heat_sensitivity_kwh"] = {
            "cop_2": round(ac["measured_energy_kwh"] * 2.0, 4),
            "cop_3": round(ac["measured_energy_kwh"] * 3.0, 4),
            "cop_5": round(ac["measured_energy_kwh"] * 5.0, 4),
        }
    return {
        "status": "measured_energy_channels_available" if any(
            item["measured_energy_kwh"] is not None for item in results.values()
        ) else "no_verified_energy_input",
        "interval_minutes": interval_minutes,
        "meters": results,
        "interpretation": (
            "Only fresh monotonic cumulative meters with explicit energy units are integrated. "
            "Gas volume units such as m3 are withheld until calorific value and meter basis are configured. "
            "Boiler efficiency and AC COP ranges are sensitivity scenarios, not measured efficiencies. "
            "Gas/AC heat estimates are system-level input proxies, not room-specific heat delivery; "
            "they cannot identify slab or room capacity without heat allocation and independent validation."
        ),
    }




def _pearson_diagnostics(xs: list[float], ys: list[float]) -> dict[str, Any]:
    """Return Pearson r, Fisher interval and a chronological split-half check."""
    def correlation(left, right):
        if len(left) < 2:
            return None
        mean_left, mean_right = sum(left) / len(left), sum(right) / len(right)
        var_left = sum((value - mean_left) ** 2 for value in left)
        var_right = sum((value - mean_right) ** 2 for value in right)
        if var_left <= 1e-12 or var_right <= 1e-12:
            return None
        return sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right)) / math.sqrt(var_left * var_right)

    r = correlation(xs, ys)
    interval = None
    if r is not None and len(xs) > 3:
        bounded = max(-0.999999, min(0.999999, r))
        z = 0.5 * math.log((1 + bounded) / (1 - bounded))
        margin = 1.96 / math.sqrt(len(xs) - 3)
        interval = [round(math.tanh(z - margin), 4), round(math.tanh(z + margin), 4)]
    midpoint = len(xs) // 2
    first = correlation(xs[:midpoint], ys[:midpoint]) if midpoint >= 4 else None
    second = correlation(xs[midpoint:], ys[midpoint:]) if len(xs) - midpoint >= 4 else None
    split_status = "insufficient_split_half_pairs"
    if first is not None and second is not None:
        if first * second > 0 and abs(first - second) <= 0.5:
            split_status = "same_direction_reasonably_stable"
        elif first * second < 0:
            split_status = "sign_reversal_between_halves"
        else:
            split_status = "material_split_half_change"
    return {
        "confidence_interval_95": interval,
        "split_half": {
            "first_half_pearson_r": round(first, 4) if first is not None else None,
            "second_half_pearson_r": round(second, 4) if second is not None else None,
            "status": split_status,
        },
    }



def build_thermal_input_room_response_alignment(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    interval_minutes: int = 30,
    max_age_minutes: float = 45.0,
    minimum_intervals: int = 8,
    maximum_lag_intervals: int = 4,
) -> dict[str, Any]:
    """Align cumulative plant input with room response across bounded lags.

    This is an observational association screen, not a causal heat-transfer
    model. Every accepted interval requires temperatures for all configured
    rooms. No input energy is allocated to rooms and no capacity is fit.
    """
    if (interval_minutes <= 0 or max_age_minutes < 0 or minimum_intervals < 2
            or maximum_lag_intervals < 0 or maximum_lag_intervals > 12):
        raise ValueError("Invalid input-response alignment thresholds")
    room_ids = [str(room.get("id", "unknown")) for room in rooms]
    if not room_ids or len(set(room_ids)) != len(room_ids):
        return {"status": "insufficient_room_configuration", "meters": {}, "rooms": {}}
    parsed = []
    for sample in samples:
        try:
            stamp = datetime.fromisoformat(str(sample.get("timestamp", "")).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            parsed.append((stamp.astimezone(timezone.utc), sample))
        except (TypeError, ValueError):
            continue
    parsed.sort(key=lambda item: item[0])
    meter_channels = ("gas_consumption", "ac_energy")
    unit_scales = {"kwh": 1.0, "wh": 0.001, "mwh": 1000.0}
    exclusions = {key: {
        "missing_or_stale_meter": 0, "unsupported_unit": 0,
        "meter_reset_or_decrease": 0, "invalid_interval": 0,
        "incomplete_room_temperature_coverage": 0,
    } for key in meter_channels}
    interval_rows = []
    for (stamp0, first), (stamp1, second) in zip(parsed, parsed[1:]):
        elapsed_minutes = (stamp1 - stamp0).total_seconds() / 60
        if abs(elapsed_minutes - interval_minutes) > 1:
            for key in meter_channels:
                exclusions[key]["invalid_interval"] += 1
            interval_rows.append(None)
            continue
        first_rooms, second_rooms = first.get("rooms") or {}, second.get("rooms") or {}
        slopes = {}
        complete = True
        for room_id in room_ids:
            first_temp = _finite_number_or_none(first_rooms.get(room_id, {}).get("temperature"))
            second_temp = _finite_number_or_none(second_rooms.get(room_id, {}).get("temperature"))
            if first_temp is None or second_temp is None:
                complete = False
                break
            slopes[room_id] = (second_temp - first_temp) / (elapsed_minutes / 60)
        if not complete:
            for key in meter_channels:
                exclusions[key]["incomplete_room_temperature_coverage"] += 1
            interval_rows.append(None)
            continue
        interval_rows.append({
            "stamp": stamp0,
            "aggregate_slope": sum(slopes.values()) / len(slopes),
            "room_slopes": slopes,
            "meters": {},
        })
        row = interval_rows[-1]
        for key in meter_channels:
            first_channels, second_channels = first.get("channels") or {}, second.get("channels") or {}
            first_value = _finite_number_or_none(first_channels.get(key))
            second_value = _finite_number_or_none(second_channels.get(key))
            first_age = _finite_number_or_none(first_channels.get(key + "_age_minutes"))
            second_age = _finite_number_or_none(second_channels.get(key + "_age_minutes"))
            if (first_value is None or second_value is None
                    or (first_age is not None and first_age > max_age_minutes)
                    or (second_age is not None and second_age > max_age_minutes)):
                exclusions[key]["missing_or_stale_meter"] += 1
                continue
            first_unit = str(first_channels.get(key + "_unit") or "").strip().lower().replace(" ", "")
            second_unit = str(second_channels.get(key + "_unit") or "").strip().lower().replace(" ", "")
            if first_unit not in unit_scales or second_unit not in unit_scales or first_unit != second_unit:
                exclusions[key]["unsupported_unit"] += 1
                continue
            if second_value < first_value:
                exclusions[key]["meter_reset_or_decrease"] += 1
                continue
            energy_kwh = (second_value - first_value) * unit_scales[second_unit]
            row["meters"][key] = energy_kwh * 1000.0 / (elapsed_minutes / 60)

    def pearson(xs, ys):
        if len(xs) < 2:
            return None
        mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
        variance_x = sum((x - mean_x) ** 2 for x in xs)
        variance_y = sum((y - mean_y) ** 2 for y in ys)
        if variance_x <= 1e-12 or variance_y <= 1e-12:
            return None
        return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / math.sqrt(variance_x * variance_y)

    results, room_results = {}, {}
    for key in meter_channels:
        lag_results = []
        for lag in range(maximum_lag_intervals + 1):
            pairs = []
            room_pairs = {room_id: [] for room_id in room_ids}
            for index, input_row in enumerate(interval_rows):
                response_index = index + lag
                if input_row is None or response_index >= len(interval_rows):
                    continue
                response_row = interval_rows[response_index]
                if response_row is None or key not in input_row["meters"]:
                    continue
                input_power = input_row["meters"][key]
                pairs.append((input_power, response_row["aggregate_slope"]))
                for room_id in room_ids:
                    room_pairs[room_id].append((input_power, response_row["room_slopes"][room_id]))
            xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
            correlation = pearson(xs, ys)
            diagnostics = _pearson_diagnostics(xs, ys)
            input_mean = sum(xs) / len(xs) if xs else 0.0
            input_std = math.sqrt(sum((value - input_mean) ** 2 for value in xs) / len(xs)) if xs else 0.0
            lag_results.append({
                "lag_intervals": lag,
                "lag_minutes": lag * interval_minutes,
                "paired_intervals": len(pairs),
                "pearson_r": round(correlation, 4) if correlation is not None else None,
                "confidence_interval_95": diagnostics["confidence_interval_95"],
                "confidence_interval_method": "nominal_fisher_z_independent_pairs",
                "confidence_interval_note": (
                    "Nominal interval assumes independent pairs; serial correlation can make it optimistic."
                ),
                "split_half": diagnostics["split_half"],
                "input_power_range_w": round(max(xs) - min(xs), 3) if xs else None,
                "input_power_std_w": round(input_std, 3) if xs else None,
                "mean_input_power_w": round(sum(xs) / len(xs), 2) if len(pairs) >= minimum_intervals else None,
                "mean_aggregate_room_temperature_rate_c_per_hour": round(sum(ys) / len(ys), 4) if len(pairs) >= minimum_intervals else None,
                "rooms": {room_id: {
                    "paired_intervals": len(room_pairs[room_id]),
                    "pearson_r": (
                        round(pearson([x for x, _ in room_pairs[room_id]], [y for _, y in room_pairs[room_id]]), 4)
                        if len(room_pairs[room_id]) >= minimum_intervals and pearson([x for x, _ in room_pairs[room_id]], [y for _, y in room_pairs[room_id]]) is not None else None
                    ),
                } for room_id in room_ids},
            })
        valid_lags = [item for item in lag_results if item["paired_intervals"] >= minimum_intervals and item["pearson_r"] is not None]
        best_lag = max(valid_lags, key=lambda item: abs(item["pearson_r"])) if valid_lags else None
        ranked_lags = sorted(valid_lags, key=lambda item: abs(item["pearson_r"]), reverse=True)
        lag_peak_status = "insufficient_valid_lags"
        if ranked_lags:
            lag_peak_status = (
                "distinct_descriptive_peak"
                if len(ranked_lags) == 1 or abs(ranked_lags[0]["pearson_r"]) - abs(ranked_lags[1]["pearson_r"]) >= 0.05
                else "ambiguous_neighbouring_lag_peaks"
            )
        stability_statuses = [
            item["split_half"]["status"] for item in valid_lags
            if item["split_half"]["status"] != "insufficient_split_half_pairs"
        ]
        if "sign_reversal_between_halves" in stability_statuses:
            response_stability = "unstable_sign_between_history_halves"
        elif stability_statuses and all(value == "same_direction_reasonably_stable" for value in stability_statuses):
            response_stability = "same_direction_across_history_halves"
        elif stability_statuses:
            response_stability = "mixed_or_changing_response"
        else:
            response_stability = "insufficient_split_half_history"
        zero_lag = lag_results[0]
        best_interval = best_lag["confidence_interval_95"] if best_lag else None
        correlation_uncertainty_status = (
            "interval_excludes_zero"
            if best_interval is not None and (best_interval[0] > 0 or best_interval[1] < 0)
            else "interval_includes_zero_or_unavailable"
        )
        enough_input_excitation = (
            zero_lag["paired_intervals"] >= minimum_intervals
            and (zero_lag["input_power_range_w"] or 0.0) > 1.0
            and (zero_lag["input_power_std_w"] or 0.0) > 0.25
        )
        if not valid_lags:
            evidence_status = "insufficient_verified_input_response_pairs"
        elif not enough_input_excitation:
            evidence_status = "withheld_weak_plant_input_excitation"
        elif response_stability != "same_direction_across_history_halves":
            evidence_status = "withheld_non_repeatable_response"
        elif correlation_uncertainty_status != "interval_excludes_zero":
            evidence_status = "withheld_uncertain_correlation"
        elif lag_peak_status != "distinct_descriptive_peak":
            evidence_status = "descriptive_lag_ambiguous"
        else:
            evidence_status = "repeatable_descriptive_association_only"
        results[key] = {
            "status": "lagged_input_response_pairs_available" if valid_lags else "insufficient_verified_input_response_pairs",
            "evidence_status": evidence_status,
            "plant_input_excitation_status": "sufficient_variation_for_association_screen" if enough_input_excitation else "weak_or_flat_input_variation",
            "response_stability_status": response_stability,
            "correlation_uncertainty_status": correlation_uncertainty_status,
            "lag_peak_status": lag_peak_status,
            "paired_intervals_at_zero_lag": lag_results[0]["paired_intervals"],
            "lag_scan": lag_results,
            "strongest_observed_lag_minutes": best_lag["lag_minutes"] if best_lag else None,
            "strongest_observed_lag_pearson_r": best_lag["pearson_r"] if best_lag else None,
            "strongest_observed_lag_confidence_interval_95": best_lag["confidence_interval_95"] if best_lag else None,
            "exclusions": exclusions[key],
        }
        room_results[key] = {
            room_id: {
                "paired_intervals_at_zero_lag": lag_results[0]["rooms"][room_id]["paired_intervals"],
                "zero_lag_pearson_r": lag_results[0]["rooms"][room_id]["pearson_r"],
                "lag_scan": [{"lag_minutes": item["lag_minutes"], **item["rooms"][room_id]} for item in lag_results],
            } for room_id in room_ids
        }
    return {
        "status": "available" if any(item["status"] == "lagged_input_response_pairs_available" for item in results.values()) else "insufficient_verified_input_response_pairs",
        "interval_minutes": interval_minutes,
        "minimum_intervals": minimum_intervals,
        "maximum_lag_minutes": maximum_lag_intervals * interval_minutes,
        "meters": results,
        "rooms": room_results,
        "capacity_identification_status": "withheld_no_room_heat_allocation_or_independent_thermal_output",
        "note": (
            "Lag scans pair verified whole-system gas/AC input power with room temperature rates "
            "from the same or a later interval. All configured rooms must have temperatures for "
            "each response interval. Fisher intervals and chronological split-half checks "
            "quantify uncertainty and repeatability; flat plant input, sign reversals and "
            "ambiguous neighbouring lag peaks are withheld from repeatable-association status. "
            "The strongest lag remains descriptive, not causal: thermal storage, controls, "
            "weather, gains and distribution losses can confound correlations. No heat is "
            "allocated to rooms and absolute room or slab capacities remain unidentifiable."
        ),
    }

def build_room_level_excitation_identifiability(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    interval_minutes: int = 30,
    minimum_mode_intervals: int = 4,
    max_age_minutes: float = 45.0,
) -> dict[str, Any]:
    """Screen room-demand and floor-pump excitation before fitting active RC inputs.

    Demand is a controller request, not measured heat delivery. Pump power is
    an auxiliary on/off indicator, not water-side thermal output.
    """
    if interval_minutes <= 0 or minimum_mode_intervals < 1 or max_age_minutes < 0:
        raise ValueError("Invalid room excitation thresholds")
    room_ids = [str(room.get("id", "unknown")) for room in rooms]
    if not room_ids or len(set(room_ids)) != len(room_ids):
        return {"status": "insufficient_room_configuration", "rooms": {}, "floors": {}}
    parsed = []
    for sample in samples:
        try:
            stamp = datetime.fromisoformat(str(sample.get("timestamp", "")).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            parsed.append((stamp.astimezone(timezone.utc), sample))
        except (TypeError, ValueError):
            continue
    parsed.sort(key=lambda item: item[0])
    room_counts = {
        room_id: {"on_intervals": 0, "off_intervals": 0, "unknown_intervals": 0,
                  "on_transitions": 0, "off_transitions": 0,
                  "on_temperature_rates": [], "off_temperature_rates": []}
        for room_id in room_ids
    }
    pair_counts = {
        (left, right): {"both_on": 0, "left_only": 0, "right_only": 0, "both_off": 0}
        for index, left in enumerate(room_ids) for right in room_ids[index + 1:]
    }
    patterns = {}
    floors = {}
    for floor_id in ("ground_floor", "upstairs"):
        floor_rooms = [room for room in rooms if room.get("floor", "ground_floor") == floor_id]
        floors[floor_id] = {
            "rooms": [str(room.get("id", "unknown")) for room in floor_rooms],
            "pump_on_intervals": 0, "pump_off_intervals": 0,
            "pump_unknown_intervals": 0, "pump_transitions": 0,
            "room_demand_and_pump_disagreement_intervals": 0,
        }
    previous_modes = {}
    previous_pumps = {}
    for (stamp0, first), (stamp1, second) in zip(parsed, parsed[1:]):
        elapsed = (stamp1 - stamp0).total_seconds() / 60
        if abs(elapsed - interval_minutes) > 1:
            previous_modes = {}
            previous_pumps = {}
            continue
        first_rooms, second_rooms = first.get("rooms") or {}, second.get("rooms") or {}
        modes = {}
        for room_id in room_ids:
            state0 = _switch_state(first_rooms.get(room_id, {}).get("demand"))
            state1 = _switch_state(second_rooms.get(room_id, {}).get("demand"))
            age0 = _finite_number_or_none(first_rooms.get(room_id, {}).get("demand_age_minutes"))
            age1 = _finite_number_or_none(second_rooms.get(room_id, {}).get("demand_age_minutes"))
            if (state0 is None or state1 is None
                    or (age0 is not None and age0 > max_age_minutes)
                    or (age1 is not None and age1 > max_age_minutes)):
                room_counts[room_id]["unknown_intervals"] += 1
                continue
            mode = state1
            modes[room_id] = mode
            bucket = "on_intervals" if mode else "off_intervals"
            room_counts[room_id][bucket] += 1
            if room_id in previous_modes and previous_modes[room_id] != mode:
                transition = "on_transitions" if mode else "off_transitions"
                room_counts[room_id][transition] += 1
            temp0 = _finite_number_or_none(first_rooms.get(room_id, {}).get("temperature"))
            temp1 = _finite_number_or_none(second_rooms.get(room_id, {}).get("temperature"))
            if temp0 is not None and temp1 is not None:
                rate = (temp1 - temp0) / (elapsed / 60)
                room_counts[room_id]["on_temperature_rates" if mode else "off_temperature_rates"].append(rate)
            previous_modes[room_id] = mode
        if len(modes) == len(room_ids):
            pattern = "".join("1" if modes[room_id] else "0" for room_id in room_ids)
            patterns[pattern] = patterns.get(pattern, 0) + 1
            for pair, counts in pair_counts.items():
                left_on, right_on = modes[pair[0]], modes[pair[1]]
                key = "both_on" if left_on and right_on else "left_only" if left_on else "right_only" if right_on else "both_off"
                counts[key] += 1
        for floor_id, floor_result in floors.items():
            floor_rooms = [room for room in rooms if room.get("floor", "ground_floor") == floor_id]
            if not floor_rooms:
                continue
            pump_key = f"pump:{floor_id}:power"
            channels0, channels1 = first.get("channels") or {}, second.get("channels") or {}
            power0 = _finite_number_or_none(channels0.get(pump_key))
            power1 = _finite_number_or_none(channels1.get(pump_key))
            age0 = _finite_number_or_none(channels0.get(pump_key + "_age_minutes"))
            age1 = _finite_number_or_none(channels1.get(pump_key + "_age_minutes"))
            if (power0 is None or power1 is None
                    or (age0 is not None and age0 > max_age_minutes)
                    or (age1 is not None and age1 > max_age_minutes)):
                floor_result["pump_unknown_intervals"] += 1
                previous_pumps.pop(floor_id, None)
                continue
            pump_on = power1 > 5.0
            floor_result["pump_on_intervals" if pump_on else "pump_off_intervals"] += 1
            if floor_id in previous_pumps and previous_pumps[floor_id] != pump_on:
                floor_result["pump_transitions"] += 1
            previous_pumps[floor_id] = pump_on
            known_floor_modes = [modes.get(str(room.get("id"))) for room in floor_rooms]
            known_floor_modes = [mode for mode in known_floor_modes if mode is not None]
            if known_floor_modes and pump_on != any(known_floor_modes):
                floor_result["room_demand_and_pump_disagreement_intervals"] += 1

    def phi(counts):
        both_on, left_only, right_only, both_off = (
            counts["both_on"], counts["left_only"], counts["right_only"], counts["both_off"]
        )
        denominator = math.sqrt(
            (both_on + left_only) * (right_only + both_off)
            * (both_on + right_only) * (left_only + both_off)
        )
        return (both_on * both_off - left_only * right_only) / denominator if denominator > 1e-12 else None

    room_results = {}
    for room_id, counts in room_counts.items():
        on_rates, off_rates = counts["on_temperature_rates"], counts["off_temperature_rates"]
        enough = len(on_rates) >= minimum_mode_intervals and len(off_rates) >= minimum_mode_intervals
        room_results[room_id] = {
            "status": "both_demand_modes_observed" if enough else "insufficient_demand_mode_excitation",
            "demand_on_intervals": counts["on_intervals"],
            "demand_off_intervals": counts["off_intervals"],
            "unknown_intervals": counts["unknown_intervals"],
            "demand_on_transitions": counts["on_transitions"],
            "demand_off_transitions": counts["off_transitions"],
            "temperature_rate_median_demand_on_c_per_hour": round(sorted(on_rates)[len(on_rates)//2], 4) if on_rates else None,
            "temperature_rate_median_demand_off_c_per_hour": round(sorted(off_rates)[len(off_rates)//2], 4) if off_rates else None,
            "temperature_rate_separation_c_per_hour": (
                round(sorted(on_rates)[len(on_rates)//2] - sorted(off_rates)[len(off_rates)//2], 4)
                if enough else None
            ),
            "active_input_identifiable": False,
            "note": "Demand is a controller request, not measured room heat delivery; temperature-rate separation is descriptive only.",
        }
    pair_results = {
        f"{left}__{right}": {
            **counts,
            "demand_coactivation_phi": round(phi(counts), 4) if phi(counts) is not None else None,
        }
        for (left, right), counts in pair_counts.items()
    }
    room_mode_coverage = sum(
        1 for item in room_results.values()
        if item["status"] == "both_demand_modes_observed"
    )
    complete_patterns = [
        [1.0 if pattern[index] == "1" else 0.0 for index in range(len(room_ids))]
        for pattern, count in patterns.items() if count > 0
    ]
    column_means = [
        sum(row[column] for row in complete_patterns) / len(complete_patterns)
        for column in range(len(room_ids))
    ] if complete_patterns else []
    gram = [
        [
            sum((row[left] - column_means[left]) * (row[right] - column_means[right])
                for row in complete_patterns) / len(complete_patterns)
            for right in range(len(room_ids))
        ]
        for left in range(len(room_ids))
    ] if complete_patterns else []
    eigenvalues = _symmetric_matrix_eigenvalues(gram)
    largest_eigenvalue = max(eigenvalues) if eigenvalues else 0.0
    smallest_positive = min((value for value in eigenvalues if value > max(1e-10, largest_eigenvalue * 1e-10)), default=0.0)
    demand_design_rank = sum(
        value > max(1e-10, largest_eigenvalue * 1e-10) for value in eigenvalues
    )
    demand_design_condition = (
        largest_eigenvalue / smallest_positive
        if demand_design_rank == len(room_ids) and smallest_positive > 0 else None
    )
    independent_patterns = len(complete_patterns)
    independent_room_excitation = (
        room_mode_coverage == len(room_ids)
        and independent_patterns >= len(room_ids) + 1
        and demand_design_rank == len(room_ids)
    )
    return {
        "status": "room_demand_excitation_observed" if room_mode_coverage else "insufficient_room_demand_excitation",
        "interval_minutes": interval_minutes,
        "room_count": len(room_ids),
        "rooms_with_both_demand_modes": room_mode_coverage,
        "distinct_simultaneous_demand_patterns": independent_patterns,
        "demand_design_matrix_rank": demand_design_rank,
        "demand_design_matrix_full_rank": demand_design_rank == len(room_ids),
        "demand_design_matrix_condition_number": round(demand_design_condition, 3) if demand_design_condition is not None else None,
        "observed_demand_patterns": patterns,
        "pairwise_demand_coactivation": pair_results,
        "rooms": room_results,
        "floors": floors,
        "room_specific_input_separation": (
            "potentially_excited_but_not_identified" if independent_room_excitation
            else "insufficient_independent_room_demand_patterns"
        ),
        "absolute_capacity_identification": "withheld_no_independent_room_heat_delivery_measurement",
        "note": (
            "This screens whether room demand controllers independently switch and whether floor "
            "pump activity broadly overlaps room demand. Binary demand and pump power are excitation "
            "indicators only. Correlated room demands, shared manifold operation, unknown hydraulic "
            "flow and unmeasured heat delivery prevent room-specific watts, absolute capacities or "
            "conductances from being identified."
        ),
    }


def _symmetric_matrix_eigenvalues(matrix: list[list[float]]) -> list[float]:
    """Return eigenvalues of a small real symmetric matrix via Jacobi rotations."""
    size = len(matrix)
    values = [row[:] for row in matrix]
    if size == 0:
        return []
    for _ in range(max(1, 50 * size * size)):
        pivot = max(
            ((abs(values[i][j]), i, j) for i in range(size) for j in range(i + 1, size)),
            default=(0.0, 0, 0),
        )
        magnitude, p, q = pivot
        if magnitude < 1e-12:
            break
        angle = 0.5 * math.atan2(2.0 * values[p][q], values[q][q] - values[p][p])
        cosine, sine = math.cos(angle), math.sin(angle)
        app, aqq, apq = values[p][p], values[q][q], values[p][q]
        values[p][p] = cosine * cosine * app - 2 * sine * cosine * apq + sine * sine * aqq
        values[q][q] = sine * sine * app + 2 * sine * cosine * apq + cosine * cosine * aqq
        values[p][q] = values[q][p] = 0.0
        for index in range(size):
            if index in (p, q):
                continue
            aip, aiq = values[index][p], values[index][q]
            values[index][p] = values[p][index] = cosine * aip - sine * aiq
            values[index][q] = values[q][index] = sine * aip + cosine * aiq
    return [values[index][index] for index in range(size)]


def build_multizone_response_identification(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    interval_minutes: int = 30,
    train_fraction: float = 0.7,
    minimum_train_intervals: int = 24,
    minimum_holdout_intervals: int = 8,
    solar_max_w_m2: float = 30.0,
    ac_power_max_w: float = 50.0,
    require_ac_power: bool = False,
    require_boiler_burner_off: bool = False,
    require_boiler_modulation_zero: bool = False,
    max_predictor_correlation: float = 0.985,
    max_predictor_condition_number: float = 1_000_000.0,
) -> dict[str, Any]:
    """Identify normalized passive room-to-room and envelope response rates.

    Fits dT_i/dt = b_envelope*(T_out-T_i) +
    sum_j b_ij*(T_j-T_i) + intercept. Coefficients have units 1/hour:
    without independently identified room capacities they are G/C ratios,
    never physical W/K conductances. Only clean free-coasting intervals are
    eligible, and the final chronological segment is held out.
    """
    if interval_minutes <= 0 or not 0 < train_fraction < 1:
        raise ValueError("Invalid multi-zone identification cadence or split")
    if not 0 < max_predictor_correlation <= 1:
        raise ValueError("Maximum predictor correlation must be in (0, 1]")
    if not math.isfinite(max_predictor_condition_number) or max_predictor_condition_number <= 1:
        raise ValueError("Maximum predictor condition number must be finite and greater than 1")
    room_ids = [str(room.get("id", "unknown")) for room in rooms]
    if len(room_ids) < 2:
        return {
            "status": "insufficient_zones",
            "rooms": {},
            "note": "At least two configured rooms are required to identify inter-zone response.",
        }

    def numeric(value):
        return _finite_number_or_none(value)

    def state_off(value):
        return _switch_state(value) is False

    # Keep only contiguous rows. A missing cadence point must break an
    # interval rather than silently stretching the derivative horizon.
    parsed = []
    for row in samples:
        try:
            stamp = datetime.fromisoformat(row.get("timestamp", ""))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            stamp = stamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        parsed.append((stamp, row))
    parsed.sort(key=lambda item: item[0])

    room_results = {}
    all_interval_counts = {"candidate": max(0, len(parsed) - 1), "eligible": 0, "excluded": {}}
    for room_id in room_ids:
        predictors = ["envelope"] + [other for other in room_ids if other != room_id]
        rows = []
        exclusions = {
            "non_contiguous_or_missing_temperature": 0,
            "demand_or_window_not_confirmed_off_closed": 0,
            "solar_missing_or_high": 0,
            "ac_heating_active_or_unavailable": 0,
            "boiler_active_or_unavailable": 0,
            "invalid_interval": 0,
        }
        for (stamp0, first), (stamp1, second) in zip(parsed, parsed[1:]):
            elapsed_minutes = (stamp1 - stamp0).total_seconds() / 60
            if abs(elapsed_minutes - interval_minutes) > 1:
                exclusions["invalid_interval"] += 1
                continue
            first_rooms, second_rooms = first.get("rooms", {}), second.get("rooms", {})
            temps0 = {rid: numeric(first_rooms.get(rid, {}).get("temperature")) for rid in room_ids}
            temps1 = {rid: numeric(second_rooms.get(rid, {}).get("temperature")) for rid in room_ids}
            if any(temps0[rid] is None or temps1[rid] is None for rid in room_ids):
                exclusions["non_contiguous_or_missing_temperature"] += 1
                continue
            clean_states = all(
                state_off(first_rooms.get(rid, {}).get("demand"))
                and state_off(second_rooms.get(rid, {}).get("demand"))
                and state_off(first_rooms.get(rid, {}).get("window"))
                and state_off(second_rooms.get(rid, {}).get("window"))
                for rid in room_ids
            )
            if not clean_states:
                exclusions["demand_or_window_not_confirmed_off_closed"] += 1
                continue
            solar0 = numeric(first.get("channels", {}).get("solar_radiation"))
            solar1 = numeric(second.get("channels", {}).get("solar_radiation"))
            if solar0 is None or solar1 is None or max(solar0, solar1) > solar_max_w_m2:
                exclusions["solar_missing_or_high"] += 1
                continue
            ac0 = numeric(first.get("channels", {}).get("ac_power"))
            ac1 = numeric(second.get("channels", {}).get("ac_power"))
            if ((require_ac_power and (ac0 is None or ac1 is None))
                    or (ac0 is not None and ac0 > ac_power_max_w)
                    or (ac1 is not None and ac1 > ac_power_max_w)):
                exclusions["ac_heating_active_or_unavailable"] += 1
                continue
            burner0 = first.get("channels", {}).get("boiler_burner")
            burner1 = second.get("channels", {}).get("boiler_burner")
            if require_boiler_burner_off and not (state_off(burner0) and state_off(burner1)):
                exclusions["boiler_active_or_unavailable"] += 1
                continue
            modulation0 = numeric(first.get("channels", {}).get("boiler_modulation"))
            modulation1 = numeric(second.get("channels", {}).get("boiler_modulation"))
            if require_boiler_modulation_zero and (
                modulation0 is None or modulation1 is None
                or modulation0 > 0.1 or modulation1 > 0.1
            ):
                exclusions["boiler_active_or_unavailable"] += 1
                continue
            outside0 = numeric(first.get("channels", {}).get("outdoor_temperature"))
            outside1 = numeric(second.get("channels", {}).get("outdoor_temperature"))
            if outside0 is None or outside1 is None:
                exclusions["non_contiguous_or_missing_temperature"] += 1
                continue
            dt_hours = elapsed_minutes / 60
            x = [(outside0 + outside1) / 2 - (temps0[room_id] + temps1[room_id]) / 2]
            x.extend(
                (temps0[other] + temps1[other] - temps0[room_id] - temps1[room_id]) / 2
                for other in room_ids if other != room_id
            )
            y = (temps1[room_id] - temps0[room_id]) / dt_hours
            rows.append((stamp1, x, y))
        all_interval_counts["eligible"] += len(rows)

        split = int(len(rows) * train_fraction)
        train, holdout = rows[:split], rows[split:]
        result = {
            "status": "insufficient_clean_intervals",
            "candidate_intervals": len(parsed) - 1,
            "eligible_intervals": len(rows),
            "training_intervals": len(train),
            "holdout_intervals": len(holdout),
            "predictors": predictors,
            "normalized_rates_per_hour": None,
            "training_r_squared": None,
            "holdout_mae_c": None,
            "holdout_rmse_c": None,
            "persistence_baseline_mae_c": None,
            "max_abs_predictor_correlation": None,
            "predictor_correlations": {},
            "identifiability_threshold": max_predictor_correlation,
            "predictor_condition_number": None,
            "maximum_predictor_condition_number": max_predictor_condition_number,
            "predictor_matrix_rank": None,
            "excluded_intervals": exclusions,
        }
        if len(train) >= minimum_train_intervals and len(holdout) >= minimum_holdout_intervals:
            # Non-negative coordinate descent with an unpenalized intercept.
            # Scale predictors by their RMS magnitude for numerical stability.
            p = len(predictors)
            means = [sum(row[1][j] for row in train) / len(train) for j in range(p)]
            scales = [
                math.sqrt(sum((row[1][j] - means[j]) ** 2 for row in train) / len(train))
                for j in range(p)
            ]
            # Report the actual temperature-gradient excitation for each
            # physical driver, not just the aggregate matrix condition number.
            gradient_deadband_c = 0.1
            predictor_excitation = {}
            for j, predictor in enumerate(predictors):
                values = [row[1][j] for row in train]
                positive = sum(value > gradient_deadband_c for value in values)
                negative = sum(value < -gradient_deadband_c for value in values)
                near_zero = len(values) - positive - negative
                predictor_excitation[predictor] = {
                    "standard_deviation_c": round(scales[j], 4),
                    "minimum_gradient_c": round(min(values), 4),
                    "maximum_gradient_c": round(max(values), 4),
                    "positive_gradient_intervals": positive,
                    "negative_gradient_intervals": negative,
                    "near_zero_gradient_intervals": near_zero,
                    "gradient_deadband_c": gradient_deadband_c,
                    "bidirectional_excitation": positive >= 3 and negative >= 3 and scales[j] >= gradient_deadband_c,
                }
            room_link_excitation = {
                key: value["bidirectional_excitation"]
                for key, value in predictor_excitation.items() if key != "envelope"
            }
            result["predictor_excitation"] = predictor_excitation
            result["bidirectionally_excited_room_links"] = [
                key for key, excited in room_link_excitation.items() if excited
            ]
            result["weakly_excited_room_links"] = [
                key for key, excited in room_link_excitation.items() if not excited
            ]
            result["room_link_excitation_status"] = (
                "all_room_links_bidirectionally_excited"
                if room_link_excitation and all(room_link_excitation.values())
                else "one_or_more_room_links_weakly_or_one_sided_excited"
            )
            if all(scale > 1e-5 for scale in scales):
                # Pairwise correlation is a transparent first identifiability
                # screen: if two physical drivers move together, this dataset
                # cannot reliably allocate response between them.
                correlations = {}
                max_abs_correlation = 0.0
                for left in range(p):
                    for right in range(left + 1, p):
                        left_values = [row[1][left] for row in train]
                        right_values = [row[1][right] for row in train]
                        left_mean = means[left]
                        right_mean = means[right]
                        numerator = sum(
                            (a - left_mean) * (b - right_mean)
                            for a, b in zip(left_values, right_values)
                        )
                        denominator = math.sqrt(
                            sum((a - left_mean) ** 2 for a in left_values)
                            * sum((b - right_mean) ** 2 for b in right_values)
                        )
                        correlation = numerator / denominator if denominator > 1e-12 else 0.0
                        key = f"{predictors[left]}__{predictors[right]}"
                        correlations[key] = round(correlation, 4)
                        max_abs_correlation = max(max_abs_correlation, abs(correlation))
                result["predictor_correlations"] = correlations
                result["max_abs_predictor_correlation"] = round(max_abs_correlation, 4)
                # Pairwise correlations can all look acceptable while a
                # combination of three or more predictors is nearly dependent.
                # Inspect the full standardized Gram/correlation matrix too.
                correlation_matrix = [[0.0] * p for _ in range(p)]
                for left in range(p):
                    for right in range(p):
                        numerator = sum(
                            (row[1][left] - means[left]) * (row[1][right] - means[right])
                            for row in train
                        )
                        correlation_matrix[left][right] = (
                            numerator / (scales[left] * scales[right] * len(train))
                        )
                eigenvalues = _symmetric_matrix_eigenvalues(correlation_matrix)
                largest_eigenvalue = max(eigenvalues) if eigenvalues else 0.0
                smallest_eigenvalue = min(eigenvalues) if eigenvalues else 0.0
                matrix_rank = sum(value > max(1e-10, largest_eigenvalue * 1e-10) for value in eigenvalues)
                condition_number = (
                    largest_eigenvalue / smallest_eigenvalue
                    if smallest_eigenvalue > 1e-12 else float("inf")
                )
                result["predictor_matrix_rank"] = matrix_rank
                result["predictor_condition_number"] = (
                    round(condition_number, 2) if math.isfinite(condition_number) else None
                )
                collinear = max_abs_correlation >= max_predictor_correlation
                ill_conditioned = (
                    matrix_rank < p or not math.isfinite(condition_number)
                    or condition_number > max_predictor_condition_number
                )
                if collinear or ill_conditioned:
                    result["status"] = (
                        "non_identifiable_predictor_collinearity" if collinear
                        else "non_identifiable_predictor_matrix"
                    )
                    result["identifiability_note"] = (
                        "The full predictor matrix is rank-deficient or too ill-conditioned "
                        "to allocate separate normalized response rates."
                        if ill_conditioned else
                        "At least two thermal drivers are too strongly correlated in the training "
                        "history to separate their individual normalized response rates."
                    )
                    result["interpretation"] = (
                        "Coefficients are withheld because the available training history does not "
                        "independently excite the complete set of envelope and inter-room drivers."
                    )
                    room_results[room_id] = result
                    continue
                ys = [row[2] for row in train]
                y_mean = sum(ys) / len(ys)
                design = [[(row[1][j] - means[j]) / scales[j] for j in range(p)] for row in train]
                coefficients = [0.0] * p
                residual = [y - y_mean for y in ys]
                for _ in range(1200):
                    largest_change = 0.0
                    for j in range(p):
                        old = coefficients[j]
                        column = [row[j] for row in design]
                        denominator = sum(value * value for value in column)
                        if denominator <= 1e-10:
                            continue
                        numerator = sum(value * (err + value * old) for value, err in zip(column, residual))
                        new = max(0.0, numerator / denominator)
                        change = new - old
                        if change:
                            residual = [err - value * change for err, value in zip(residual, column)]
                            coefficients[j] = new
                            largest_change = max(largest_change, abs(change))
                    if largest_change < 1e-9:
                        break
                physical = [coefficients[j] / scales[j] for j in range(p)]
                intercept = y_mean - sum(physical[j] * means[j] for j in range(p))

                # Moving-block bootstrap: resample contiguous four-interval
                # blocks from the training chronology, preserving short-term
                # autocorrelation better than independent row resampling.
                # Fixed, evenly distributed block starts make this diagnostic
                # deterministic and reproducible without a random seed.
                bootstrap_block_length = min(4, max(1, len(train) // 4))
                bootstrap_replicates = 80 if len(train) >= 40 else 0
                bootstrap_coefficients = [[] for _ in range(p)]

                def fit_bootstrap_rows(bootstrap_rows):
                    count = len(bootstrap_rows)
                    local_means = [
                        sum(row[1][j] for row in bootstrap_rows) / count
                        for j in range(p)
                    ]
                    local_scales = [
                        math.sqrt(sum((row[1][j] - local_means[j]) ** 2 for row in bootstrap_rows) / count)
                        for j in range(p)
                    ]
                    if any(scale <= 1e-5 for scale in local_scales):
                        return None
                    local_design = [
                        [(row[1][j] - local_means[j]) / local_scales[j] for j in range(p)]
                        for row in bootstrap_rows
                    ]
                    local_y_mean = sum(row[2] for row in bootstrap_rows) / count
                    local_coefficients = [0.0] * p
                    local_residual = [row[2] - local_y_mean for row in bootstrap_rows]
                    for _ in range(500):
                        largest_change = 0.0
                        for j in range(p):
                            column = [row[j] for row in local_design]
                            denominator = sum(value * value for value in column)
                            if denominator <= 1e-10:
                                continue
                            old_value = local_coefficients[j]
                            numerator = sum(
                                value * (error + value * old_value)
                                for value, error in zip(column, local_residual)
                            )
                            new_value = max(0.0, numerator / denominator)
                            change = new_value - old_value
                            if change:
                                local_residual = [
                                    error - value * change
                                    for error, value in zip(local_residual, column)
                                ]
                                local_coefficients[j] = new_value
                                largest_change = max(largest_change, abs(change))
                        if largest_change < 1e-8:
                            break
                    return [local_coefficients[j] / local_scales[j] for j in range(p)]

                # Compare three contiguous chronological sections of the
                # training segment. Each section is independently checked for
                # excitation and full-matrix rank before its fit is considered.
                temporal_window_count = 3
                minimum_temporal_window_rows = max(12, p * 4)
                temporal_windows = []
                temporal_window_size = len(train) // temporal_window_count
                for window_index in range(temporal_window_count):
                    start_index = window_index * temporal_window_size
                    end_index = (
                        len(train) if window_index == temporal_window_count - 1
                        else (window_index + 1) * temporal_window_size
                    )
                    window_rows = train[start_index:end_index]
                    window_status = "insufficient_intervals"
                    window_estimate = None
                    window_condition = None
                    if len(window_rows) >= minimum_temporal_window_rows:
                        window_means = [
                            sum(row[1][j] for row in window_rows) / len(window_rows)
                            for j in range(p)
                        ]
                        window_scales = [
                            math.sqrt(sum(
                                (row[1][j] - window_means[j]) ** 2
                                for row in window_rows
                            ) / len(window_rows))
                            for j in range(p)
                        ]
                        if all(scale > 1e-5 for scale in window_scales):
                            window_design = [
                                [(row[1][j] - window_means[j]) / window_scales[j] for j in range(p)]
                                for row in window_rows
                            ]
                            gram = [
                                [
                                    sum(values[i] * values[j] for values in window_design) / len(window_design)
                                    for j in range(p)
                                ]
                                for i in range(p)
                            ]
                            eigenvalues = sorted(
                                max(0.0, value)
                                for value in _symmetric_matrix_eigenvalues(gram)
                            )
                            largest_eigenvalue = eigenvalues[-1] if eigenvalues else 0.0
                            positive_eigenvalues = [
                                value for value in eigenvalues
                                if value > max(1e-10, largest_eigenvalue * 1e-10)
                            ]
                            if len(positive_eigenvalues) == p and largest_eigenvalue > 0:
                                window_condition = math.sqrt(
                                    largest_eigenvalue / positive_eigenvalues[0]
                                )
                                if window_condition <= max_predictor_condition_number:
                                    window_estimate = fit_bootstrap_rows(window_rows)
                                    window_status = (
                                        "fit_available" if window_estimate is not None
                                        else "insufficient_predictor_excitation"
                                    )
                                else:
                                    window_status = "non_identifiable_predictor_condition"
                            else:
                                window_status = "non_identifiable_predictor_matrix"
                        else:
                            window_status = "insufficient_predictor_excitation"
                    temporal_windows.append({
                        "window": window_index + 1,
                        "training_interval_start": window_rows[0][0].isoformat() if window_rows else None,
                        "training_interval_end": window_rows[-1][0].isoformat() if window_rows else None,
                        "intervals": len(window_rows),
                        "status": window_status,
                        "predictor_condition_number": (
                            round(window_condition, 2) if window_condition is not None else None
                        ),
                        "normalized_rates_per_hour": (
                            {
                                key: round(value, 6)
                                for key, value in zip(predictors, window_estimate)
                            } if window_estimate is not None else None
                        ),
                    })

                valid_temporal_estimates = [
                    window["normalized_rates_per_hour"]
                    for window in temporal_windows
                    if window["normalized_rates_per_hour"] is not None
                ]
                temporal_coefficient_stability = {}
                for predictor in predictors:
                    values = sorted(
                        estimate[predictor] for estimate in valid_temporal_estimates
                    )
                    if values:
                        median_value = values[len(values) // 2]
                        deviations = sorted(abs(value - median_value) for value in values)
                        mad = deviations[len(deviations) // 2]
                        relative_range = (
                            (values[-1] - values[0]) / max(abs(median_value), 1e-6)
                        )
                        temporal_coefficient_stability[predictor] = {
                            "valid_windows": len(values),
                            "total_windows": temporal_window_count,
                            "minimum_1_per_hour": round(values[0], 6),
                            "median_1_per_hour": round(median_value, 6),
                            "maximum_1_per_hour": round(values[-1], 6),
                            "median_absolute_deviation_1_per_hour": round(mad, 6),
                            "relative_range": round(relative_range, 3),
                            "stability": (
                                "consistent_across_training_periods"
                                if len(values) == temporal_window_count and relative_range <= 1.0
                                else "time_varying_or_insufficient_windows"
                            ),
                        }
                    else:
                        temporal_coefficient_stability[predictor] = {
                            "valid_windows": 0,
                            "total_windows": temporal_window_count,
                            "minimum_1_per_hour": None,
                            "median_1_per_hour": None,
                            "maximum_1_per_hour": None,
                            "median_absolute_deviation_1_per_hour": None,
                            "relative_range": None,
                            "stability": "insufficient_identifiable_windows",
                        }
                result["temporal_coefficient_stability"] = temporal_coefficient_stability
                result["temporal_training_windows"] = temporal_windows
                result["temporal_stability_method"] = (
                    "three_contiguous_training_periods_with_full_rank_gate"
                )
                result["temporal_stability_note"] = (
                    "Only the chronological training segment is divided into three contiguous periods; "
                    "the final holdout is never used. Periods failing minimum sample, excitation, rank "
                    "or condition checks are withheld. Relative range is a descriptive stability "
                    "heuristic, not a confidence interval or proof of physical parameter constancy."
                )

                if bootstrap_replicates:
                    n_train = len(train)
                    for replicate in range(bootstrap_replicates):
                        bootstrap_rows = []
                        block_number = 0
                        while len(bootstrap_rows) < n_train:
                            max_start = n_train - bootstrap_block_length
                            start_index = (
                                replicate * 37 + block_number * 17
                            ) % (max_start + 1)
                            bootstrap_rows.extend(
                                train[start_index:start_index + bootstrap_block_length]
                            )
                            block_number += 1
                        bootstrap_rows = bootstrap_rows[:n_train]
                        estimate = fit_bootstrap_rows(bootstrap_rows)
                        if estimate is not None:
                            for j, value in enumerate(estimate):
                                bootstrap_coefficients[j].append(value)

                coefficient_uncertainty = {}
                for j, predictor in enumerate(predictors):
                    estimates = sorted(bootstrap_coefficients[j])
                    if len(estimates) >= 40:
                        lower = estimates[int(0.05 * (len(estimates) - 1))]
                        median = estimates[int(0.50 * (len(estimates) - 1))]
                        upper = estimates[int(0.95 * (len(estimates) - 1))]
                        relative_width = (
                            (upper - lower) / max(abs(median), 1e-6)
                        )
                        coefficient_uncertainty[predictor] = {
                            "method": "deterministic_moving_block_bootstrap",
                            "block_length_intervals": bootstrap_block_length,
                            "requested_replicates": bootstrap_replicates,
                            "successful_replicates": len(estimates),
                            "lower_90_percentile_1_per_hour": round(lower, 6),
                            "median_1_per_hour": round(median, 6),
                            "upper_90_percentile_1_per_hour": round(upper, 6),
                            "relative_90_percentile_width": round(relative_width, 3),
                            "stability": (
                                "relatively_stable" if relative_width <= 1.0
                                else "high_uncertainty"
                            ),
                        }
                    else:
                        coefficient_uncertainty[predictor] = {
                            "method": "deterministic_moving_block_bootstrap",
                            "block_length_intervals": bootstrap_block_length,
                            "requested_replicates": bootstrap_replicates,
                            "successful_replicates": len(estimates),
                            "lower_90_percentile_1_per_hour": None,
                            "median_1_per_hour": None,
                            "upper_90_percentile_1_per_hour": None,
                            "relative_90_percentile_width": None,
                            "stability": "insufficient_bootstrap_replicates",
                        }
                result["coefficient_uncertainty"] = coefficient_uncertainty
                result["coefficient_uncertainty_method"] = (
                    "deterministic_moving_block_bootstrap_90_percentile_range"
                )
                result["coefficient_uncertainty_note"] = (
                    "Ranges quantify sensitivity to resampling contiguous training blocks; "
                    "they are not formal confidence intervals and do not include sensor bias, "
                    "unmeasured gains, model misspecification or uncertainty in room heat delivery."
                )
                fitted = [intercept + sum(physical[j] * row[1][j] for j in range(p)) for row in train]
                residual_sum = sum((pred - row[2]) ** 2 for pred, row in zip(fitted, train))
                total_sum = sum((row[2] - y_mean) ** 2 for row in train)
                r2 = max(0.0, min(1.0, 1 - residual_sum / total_sum)) if total_sum > 1e-10 else None
                errors, baseline = [], []
                for _, x, observed_rate in holdout:
                    pred_rate = intercept + sum(physical[j] * x[j] for j in range(p))
                    errors.append(pred_rate * interval_minutes / 60)
                    baseline.append(0.0)
                # Temperature change prediction: predicted rate * interval;
                # persistence baseline predicts no temperature change.
                holdout_changes = [row[2] * interval_minutes / 60 for row in holdout]
                mae = sum(abs(pred - obs) for pred, obs in zip(errors, holdout_changes)) / len(errors)
                rmse = math.sqrt(sum((pred - obs) ** 2 for pred, obs in zip(errors, holdout_changes)) / len(errors))
                baseline_mae = sum(abs(value) for value in holdout_changes) / len(holdout_changes)
                result.update({
                    "status": "holdout_better_than_persistence" if mae < baseline_mae else "holdout_not_better_than_persistence",
                    "normalized_rates_per_hour": {
                        key: round(value, 6) for key, value in zip(predictors, physical)
                    },
                    "intercept_c_per_hour": round(intercept, 6),
                    "training_r_squared": round(r2, 3) if r2 is not None else None,
                    "holdout_mae_c": round(mae, 4),
                    "holdout_rmse_c": round(rmse, 4),
                    "persistence_baseline_mae_c": round(baseline_mae, 4),
                    "nonzero_predictors": sum(value > 1e-5 for value in physical),
                    "identifiability_note": (
                        "No severe pairwise predictor collinearity was detected in the training set; "
                        "this is a screening diagnostic, not proof of full multivariate identifiability."
                    ),
                    "interpretation": (
                        "Coefficients are non-negative normalized response rates G/C in 1/hour, "
                        "not physical conductances in W/K. Room capacities are not yet independently "
                        "identified, so absolute room-to-room and envelope conductances remain withheld. "
                        "The final chronological segment was not used for fitting."
                    ),
                })
            else:
                result["status"] = "non_identifiable_predictor_variation"
        result["excluded_intervals"] = exclusions
        room_results[room_id] = result

    validated_rooms = [
        item for item in room_results.values()
        if item.get("holdout_mae_c") is not None
        and item.get("persistence_baseline_mae_c") is not None
        and item.get("holdout_rmse_c") is not None
        and item.get("holdout_intervals", 0) > 0
    ]
    holdout_count = sum(item["holdout_intervals"] for item in validated_rooms)
    aggregate_holdout_mae = (
        sum(item["holdout_mae_c"] * item["holdout_intervals"] for item in validated_rooms) / holdout_count
        if holdout_count else None
    )
    aggregate_baseline_mae = (
        sum(item["persistence_baseline_mae_c"] * item["holdout_intervals"] for item in validated_rooms) / holdout_count
        if holdout_count else None
    )
    aggregate_holdout_rmse = (
        math.sqrt(sum(item["holdout_rmse_c"] ** 2 * item["holdout_intervals"] for item in validated_rooms) / holdout_count)
        if holdout_count else None
    )
    house_holdout_validation = {
        "status": (
            "holdout_better_than_persistence"
            if aggregate_holdout_mae is not None and aggregate_baseline_mae is not None
            and aggregate_holdout_mae < aggregate_baseline_mae
            else "holdout_not_better_than_persistence"
            if aggregate_holdout_mae is not None else "insufficient_validated_rooms"
        ),
        "rooms_validated": len(validated_rooms),
        "configured_rooms": len(room_ids),
        "holdout_room_intervals": holdout_count,
        "weighted_mae_c": round(aggregate_holdout_mae, 4) if aggregate_holdout_mae is not None else None,
        "weighted_rmse_c": round(aggregate_holdout_rmse, 4) if aggregate_holdout_rmse is not None else None,
        "persistence_baseline_weighted_mae_c": round(aggregate_baseline_mae, 4) if aggregate_baseline_mae is not None else None,
        "weighting": "holdout_interval_count_per_room",
        "note": "Aggregate diagnostic over room-level chronological holdouts; rooms without a valid fitted holdout are excluded and counted explicitly. This is not an independent sensor or plant-energy validation.",
    }
    return {
        "status": "room_response_identification_available" if any(
            item.get("normalized_rates_per_hour") is not None for item in room_results.values()
        ) else "insufficient_or_non_identifiable_history",
        "house_level_holdout_validation": house_holdout_validation,
        "method": "nonnegative_multizone_free_coasting_regression",
        "interval_minutes": interval_minutes,
        "train_fraction": train_fraction,
        "max_predictor_correlation_threshold": max_predictor_correlation,
        "identifiability_method": "maximum_absolute_pairwise_training_predictor_correlation",
        "rooms": room_results,
        "global_interval_counts": all_interval_counts,
        "units": "1/hour normalized conductance-to-capacity ratios (G/C), not W/K",
        "note": (
            "This is an initial directional multi-zone identification. It requires all configured rooms "
            "to be coasting with confirmed closed windows and low measured solar radiation. It does not "
            "identify absolute capacities or conductances, and unmeasured internal gains or plant heat "
            "can still bias the fit."
        ),
    }
