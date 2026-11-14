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
            unit = None
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






def build_floor_thermal_output_observability(
    samples: list[dict[str, Any]],
    interval_minutes: int = 30,
    max_age_minutes: int = 45,
) -> dict[str, Any]:
    """Summarise independently metered thermal output for each hydronic floor.

    These inputs must be thermal-output sensors (W/kW), not pump electrical
    consumption. Floor totals still do not identify delivery to individual rooms.
    """
    if interval_minutes <= 0 or max_age_minutes < 0:
        raise ValueError("Invalid floor thermal-output sampling thresholds")
    floors = {}
    for floor_id, channel in (
        ("ground_floor", "ground_floor_thermal_output"),
        ("upstairs", "upstairs_thermal_output"),
    ):
        valid = []
        excluded = {"missing_or_stale": 0, "unsupported_unit": 0, "negative_output": 0}
        for sample in samples:
            channels = sample.get("channels") or {}
            value = _finite_number_or_none(channels.get(channel))
            age = _finite_number_or_none(channels.get(channel + "_age_minutes"))
            unit = str(channels.get(channel + "_unit") or "").strip().lower()
            if value is None or (age is not None and age > max_age_minutes):
                excluded["missing_or_stale"] += 1
                continue
            if unit in ("w", "watt", "watts"):
                watts = value
            elif unit in ("kw", "kilowatt", "kilowatts"):
                watts = value * 1000.0
            else:
                excluded["unsupported_unit"] += 1
                continue
            if watts < 0:
                excluded["negative_output"] += 1
                continue
            try:
                stamp = datetime.fromisoformat(str(sample.get("timestamp")).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                valid.append((stamp, watts))
            except (TypeError, ValueError):
                excluded["missing_or_stale"] += 1
        valid.sort(key=lambda item: item[0])
        integrated_wh = 0.0
        intervals = 0
        for previous, current in zip(valid, valid[1:]):
            elapsed = (current[0] - previous[0]).total_seconds() / 60
            if abs(elapsed - interval_minutes) <= 2:
                integrated_wh += (previous[1] + current[1]) / 2 * elapsed / 60
                intervals += 1
        values = sorted(value for _, value in valid)
        median = (
            (values[(len(values) - 1) // 2] + values[len(values) // 2]) / 2
            if values else None
        )
        floors[floor_id] = {
            "status": "floor_thermal_output_available" if valid else "withheld_no_valid_floor_thermal_output",
            "source_channel": channel,
            "valid_samples": len(valid),
            "median_thermal_output_w": round(median, 1) if median is not None else None,
            "integrated_valid_intervals": intervals,
            "integrated_thermal_energy_kwh": round(integrated_wh / 1000, 4) if intervals else None,
            "excluded_samples": excluded,
            "room_heat_allocation_w": None,
            "scope": "measured_floor_total_not_room_level_delivery",
        }
    available = [floor for floor in floors.values() if floor["valid_samples"] > 0]
    return {
        "status": "floor_thermal_output_available" if available else "withheld_no_floor_thermal_output_sensors",
        "floors": floors,
        "independent_floor_totals_available": len(available) == 2,
        "room_level_delivery_identified": False,
        "note": (
            "Only explicitly configured thermal-output power sensors in W or kW are accepted. "
            "Pump electrical power is not thermal output. Floor totals do not establish how heat "
            "is divided among rooms on that floor."
        ),
    }




def build_floor_room_heat_allocation_inference(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    interval_minutes: int = 30,
    max_age_minutes: float = 45.0,
    minimum_intervals: int = 24,
) -> dict[str, Any]:
    """Estimate demand-associated floor-output contributions, never room heat meters.

    A floor's measured thermal output is regressed against its rooms' demand
    states. Independent demand patterns and repeatable coefficients are required.
    Results remain observational marginal associations: slab inertia, valve
    flow balance, room gains and controller behaviour can confound them.
    """
    if interval_minutes <= 0 or max_age_minutes < 0 or minimum_intervals < 4:
        raise ValueError("Invalid room heat allocation inference thresholds")

    def solve(matrix, vector):
        size = len(vector)
        augmented = [list(matrix[row]) + [vector[row]] for row in range(size)]
        for column in range(size):
            pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
            if abs(augmented[pivot][column]) < 1e-9:
                return None
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
            scale = augmented[column][column]
            augmented[column] = [value / scale for value in augmented[column]]
            for row in range(size):
                if row == column:
                    continue
                factor = augmented[row][column]
                augmented[row] = [
                    value - factor * pivot_value
                    for value, pivot_value in zip(augmented[row], augmented[column])
                ]
        return [augmented[row][-1] for row in range(size)]

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
    floors = {}
    for floor_id, output_key in (
        ("ground_floor", "ground_floor_thermal_output"),
        ("upstairs", "upstairs_thermal_output"),
    ):
        floor_rooms = [
            str(room.get("id", "unknown")) for room in rooms
            if room.get("floor", "ground_floor") == floor_id
        ]
        if not floor_rooms:
            floors[floor_id] = {"status": "no_rooms_configured", "rooms": {}}
            continue
        rows = []
        excluded = {"invalid_interval": 0, "missing_or_stale_floor_output": 0,
                    "unsupported_floor_output_unit": 0, "incomplete_or_stale_room_demand": 0}
        for (stamp0, first), (stamp1, second) in zip(parsed, parsed[1:]):
            elapsed = (stamp1 - stamp0).total_seconds() / 60
            if abs(elapsed - interval_minutes) > 1:
                excluded["invalid_interval"] += 1
                continue
            channels0, channels1 = first.get("channels") or {}, second.get("channels") or {}
            output = _finite_number_or_none(channels1.get(output_key))
            age = _finite_number_or_none(channels1.get(output_key + "_age_minutes"))
            unit = str(channels1.get(output_key + "_unit") or "").strip().lower()
            if output is None or (age is not None and age > max_age_minutes):
                excluded["missing_or_stale_floor_output"] += 1
                continue
            if unit in ("w", "watt", "watts"):
                output_w = output
            elif unit in ("kw", "kilowatt", "kilowatts"):
                output_w = output * 1000
            else:
                excluded["unsupported_floor_output_unit"] += 1
                continue
            if output_w < 0:
                excluded["missing_or_stale_floor_output"] += 1
                continue
            first_rooms, second_rooms = first.get("rooms") or {}, second.get("rooms") or {}
            demand = []
            valid = True
            for room_id in floor_rooms:
                state0 = _switch_state(first_rooms.get(room_id, {}).get("demand"))
                state1 = _switch_state(second_rooms.get(room_id, {}).get("demand"))
                age0 = _finite_number_or_none(first_rooms.get(room_id, {}).get("demand_age_minutes"))
                age1 = _finite_number_or_none(second_rooms.get(room_id, {}).get("demand_age_minutes"))
                if (state0 is None or state1 is None
                        or (age0 is not None and age0 > max_age_minutes)
                        or (age1 is not None and age1 > max_age_minutes)):
                    valid = False
                    break
                # Pair the endpoint output reading with the contemporaneous
                # endpoint controller state. The preceding state is still
                # required to establish that demand history is known/fresh.
                demand.append(float(state1))
            if not valid:
                excluded["incomplete_or_stale_room_demand"] += 1
                continue
            rows.append((demand, output_w, stamp1))

        feature_count = len(floor_rooms) + 1
        def fit(subset):
            if len(subset) < max(minimum_intervals // 2, feature_count + 2):
                return None
            design = [[1.0] + row[0] for row in subset]
            targets = [row[1] for row in subset]
            gram = [[sum(vector[i] * vector[j] for vector in design)
                     for j in range(feature_count)] for i in range(feature_count)]
            rhs = [sum(vector[i] * target for vector, target in zip(design, targets))
                   for i in range(feature_count)]
            coefficients = solve(gram, rhs)
            if coefficients is None:
                return None
            predictions = [sum(a * b for a, b in zip(vector, coefficients)) for vector in design]
            mean_target = sum(targets) / len(targets)
            residual = sum((actual - predicted) ** 2 for actual, predicted in zip(targets, predictions))
            total = sum((actual - mean_target) ** 2 for actual in targets)
            return {"coefficients": coefficients, "r_squared": 1 - residual / total if total > 1e-9 else None,
                    "residual_rmse_w": math.sqrt(residual / len(targets))}

        # Keep the final chronological 30% completely out of coefficient fitting.
        # The full-history fit is retained only as a descriptive diagnostic.
        split = int(len(rows) * 0.7)
        training_rows, holdout_rows = rows[:split], rows[split:]
        training_fit = fit(training_rows)
        overall = fit(rows)
        midpoint = len(rows) // 2
        first_fit, second_fit = fit(rows[:midpoint]), fit(rows[midpoint:])
        holdout_metrics = None
        if training_fit is not None and holdout_rows:
            train_mean = sum(row[1] for row in training_rows) / len(training_rows)
            predictions = [
                sum(a * b for a, b in zip([1.0] + row[0], training_fit["coefficients"]))
                for row in holdout_rows
            ]
            actuals = [row[1] for row in holdout_rows]
            residuals = [actual - predicted for actual, predicted in zip(actuals, predictions)]
            baseline_residuals = [actual - train_mean for actual in actuals]
            model_rmse = math.sqrt(sum(value * value for value in residuals) / len(residuals))
            baseline_rmse = math.sqrt(
                sum(value * value for value in baseline_residuals) / len(baseline_residuals)
            )
            holdout_total = sum((actual - sum(actuals) / len(actuals)) ** 2 for actual in actuals)
            holdout_residual = sum(value * value for value in residuals)
            holdout_metrics = {
                "training_intervals": len(training_rows),
                "holdout_intervals": len(holdout_rows),
                "holdout_rmse_w": round(model_rmse, 1),
                "training_mean_baseline_rmse_w": round(baseline_rmse, 1),
                "holdout_r_squared": (
                    round(1 - holdout_residual / holdout_total, 4)
                    if holdout_total > 1e-9 else None
                ),
                "improved_over_training_mean_baseline": model_rmse < baseline_rmse,
                "holdout_start": holdout_rows[0][2].isoformat(),
                "holdout_end": holdout_rows[-1][2].isoformat(),
            }
        if len(rows) < minimum_intervals or overall is None or training_fit is None or holdout_metrics is None:
            floors[floor_id] = {
                "status": "insufficient_or_collinear_floor_output_and_room_demand_history",
                "valid_intervals": len(rows), "minimum_intervals": minimum_intervals,
                "excluded_intervals": excluded, "rooms": {},
                "interpretation": "No room contribution is estimated without sufficient independent demand excitation.",
            }
            continue
        # Report coefficients fitted on training history only; do not refit on holdout.
        coefficients = training_fit["coefficients"]
        room_betas = coefficients[1:]
        split_stable = bool(first_fit and second_fit) and all(
            first_fit["coefficients"][index + 1] * second_fit["coefficients"][index + 1] > 0
            for index in range(len(floor_rooms))
        )
        positive_total = sum(max(0.0, beta) for beta in room_betas)
        room_results = {}
        for index, room_id in enumerate(floor_rooms):
            beta = room_betas[index]
            room_results[room_id] = {
                "demand_associated_marginal_output_w": round(beta, 1),
                "relative_positive_demand_association": (
                    round(max(0.0, beta) / positive_total, 4) if positive_total > 0 else None
                ),
                "not_a_measured_room_heat_share": True,
            }
        validated = split_stable and holdout_metrics["improved_over_training_mean_baseline"]
        status = (
            "repeatable_demand_association_not_physical_room_allocation"
            if validated else "unstable_or_unverified_demand_association"
        )
        floors[floor_id] = {
            "status": status,
            "valid_intervals": len(rows),
            "excluded_intervals": excluded,
            "model_r_squared": round(overall["r_squared"], 4) if overall["r_squared"] is not None else None,
            "model_residual_rmse_w": round(overall["residual_rmse_w"], 1),
            "chronological_holdout_validation": holdout_metrics,
            "baseline_intercept_w": round(coefficients[0], 1),
            "history_half_coefficients_same_sign": split_stable,
            "first_half_r_squared": round(first_fit["r_squared"], 4) if first_fit and first_fit["r_squared"] is not None else None,
            "second_half_r_squared": round(second_fit["r_squared"], 4) if second_fit and second_fit["r_squared"] is not None else None,
            "rooms": room_results,
            "room_heat_output_w": None,
            "physical_room_allocation_identified": False,
            "interpretation": (
                "Regression coefficients describe associations between room demand states and measured "
                "floor output. They are not direct room heat measurements; UFH slab storage, hydraulic "
                "balancing, bypass flow, room gains and controller logic can confound the relationship. "
                "The repeatable label additionally requires improvement over a chronological holdout baseline."
            ),
        }
    inferred = [
        floor for floor in floors.values()
        if floor.get("status") == "repeatable_demand_association_not_physical_room_allocation"
    ]
    return {
        "status": "demand_association_available_not_physical_room_allocation" if inferred else "no_repeatable_room_demand_output_association",
        "floors": floors,
        "physical_room_allocation_identified": False,
        "note": "Requires independently measured floor thermal output. Demand is a valve/controller request, not a heat meter.",
    }




def build_floor_room_demand_lag_analysis(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    interval_minutes: int = 30,
    max_age_minutes: float = 45.0,
    maximum_lag_intervals: int = 4,
    minimum_pairs: int = 20,
) -> dict[str, Any]:
    """Scan demand-to-floor-output timing as descriptive evidence, not heat allocation.

    Each room's demand is compared with measured floor output at delays from zero
    to the configured maximum. Correlations are selected on the early timeline
    and checked, unchanged, on the later holdout. This does not identify room
    heat delivery: common plant control, hydraulic coupling and stored slab heat
    remain potential confounders.
    """
    if interval_minutes <= 0 or max_age_minutes < 0:
        raise ValueError("Invalid floor demand lag analysis thresholds")
    if maximum_lag_intervals < 0 or maximum_lag_intervals > 12 or minimum_pairs < 8:
        raise ValueError("Invalid floor demand lag analysis limits")

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

    def correlation(xs, ys):
        if len(xs) < 3:
            return None
        x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
        x_var = sum((value - x_mean) ** 2 for value in xs)
        y_var = sum((value - y_mean) ** 2 for value in ys)
        if x_var <= 1e-9 or y_var <= 1e-9:
            return None
        return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / math.sqrt(x_var * y_var)

    floors = {}
    for floor_id, output_key in (
        ("ground_floor", "ground_floor_thermal_output"),
        ("upstairs", "upstairs_thermal_output"),
    ):
        floor_rooms = [
            str(room.get("id", "unknown")) for room in rooms
            if room.get("floor", "ground_floor") == floor_id
        ]
        if not floor_rooms:
            floors[floor_id] = {"status": "no_rooms_configured", "rooms": {}}
            continue

        timeline = []
        excluded = {"invalid_output": 0, "stale_output": 0, "unsupported_output_unit": 0,
                    "invalid_or_stale_room_demand": 0}
        for stamp, sample in parsed:
            channels = sample.get("channels") or {}
            raw_output = _finite_number_or_none(channels.get(output_key))
            output_age = _finite_number_or_none(channels.get(output_key + "_age_minutes"))
            unit = str(channels.get(output_key + "_unit") or "").strip().lower()
            if raw_output is None or raw_output < 0:
                output_w = None
                excluded["invalid_output"] += 1
            elif output_age is not None and output_age > max_age_minutes:
                output_w = None
                excluded["stale_output"] += 1
            elif unit in ("w", "watt", "watts"):
                output_w = raw_output
            elif unit in ("kw", "kilowatt", "kilowatts"):
                output_w = raw_output * 1000
            else:
                output_w = None
                excluded["unsupported_output_unit"] += 1

            room_states = {}
            observed_rooms = sample.get("rooms") or {}
            for room_id in floor_rooms:
                room_data = observed_rooms.get(room_id, {})
                demand = _switch_state(room_data.get("demand"))
                age = _finite_number_or_none(room_data.get("demand_age_minutes"))
                if demand is None or (age is not None and age > max_age_minutes):
                    room_states[room_id] = None
                    excluded["invalid_or_stale_room_demand"] += 1
                else:
                    room_states[room_id] = float(demand)
            timeline.append((stamp, output_w, room_states))

        room_results = {}
        for room_id in floor_rooms:
            lag_results = []
            for lag in range(maximum_lag_intervals + 1):
                pairs = []
                for index in range(lag, len(timeline)):
                    source_stamp, _, source_rooms = timeline[index - lag]
                    target_stamp, output_w, _ = timeline[index]
                    expected_minutes = lag * interval_minutes
                    actual_minutes = (target_stamp - source_stamp).total_seconds() / 60
                    if abs(actual_minutes - expected_minutes) > 1:
                        continue
                    demand = source_rooms.get(room_id)
                    if demand is None or output_w is None:
                        continue
                    pairs.append((demand, output_w, target_stamp))
                split = int(len(pairs) * 0.7)
                training, holdout = pairs[:split], pairs[split:]
                train_r = correlation([item[0] for item in training], [item[1] for item in training])
                holdout_r = correlation([item[0] for item in holdout], [item[1] for item in holdout])
                lag_results.append({
                    "lag_intervals": lag,
                    "lag_minutes": lag * interval_minutes,
                    "paired_intervals": len(pairs),
                    "training_intervals": len(training),
                    "holdout_intervals": len(holdout),
                    "training_pearson_r": round(train_r, 4) if train_r is not None else None,
                    "holdout_pearson_r": round(holdout_r, 4) if holdout_r is not None else None,
                    "holdout_same_direction": (
                        train_r is not None and holdout_r is not None and train_r * holdout_r > 0
                    ),
                })

            eligible = [
                item for item in lag_results
                if item["paired_intervals"] >= minimum_pairs
                and item["training_pearson_r"] is not None
                and item["holdout_pearson_r"] is not None
            ]
            eligible.sort(key=lambda item: abs(item["training_pearson_r"]), reverse=True)
            selected = eligible[0] if eligible else None
            if selected is None:
                status = "insufficient_or_constant_demand_output_pairs"
                ambiguous = None
            else:
                runner_up = eligible[1] if len(eligible) > 1 else None
                ambiguous = bool(
                    runner_up
                    and abs(selected["training_pearson_r"]) - abs(runner_up["training_pearson_r"]) <= 0.05
                )
                stable = selected["holdout_same_direction"]
                status = (
                    "descriptive_lag_association_repeatable"
                    if stable and not ambiguous
                    else "lag_association_unstable_or_ambiguous"
                )
            room_results[room_id] = {
                "status": status,
                "selected_training_lag_minutes": selected["lag_minutes"] if selected else None,
                "selected_training_pearson_r": selected["training_pearson_r"] if selected else None,
                "selected_holdout_pearson_r": selected["holdout_pearson_r"] if selected else None,
                "neighbouring_lag_peak_ambiguous": ambiguous,
                "lag_scan": lag_results,
                "interpretation": (
                    "Timing association only. The selected lag is not a measured UFH slab time constant "
                    "or room heat-delivery delay; room demand is not a heat meter."
                ),
            }
        repeatable = sum(item["status"] == "descriptive_lag_association_repeatable"
                         for item in room_results.values())
        floors[floor_id] = {
            "status": "descriptive_room_demand_lag_evidence_available" if repeatable else "no_repeatable_room_demand_lag_evidence",
            "rooms": room_results,
            "excluded_samples": excluded,
            "maximum_lag_minutes": maximum_lag_intervals * interval_minutes,
            "physical_room_allocation_identified": False,
        }
    return {
        "status": "descriptive_lag_evidence_available" if any(
            item.get("status") == "descriptive_room_demand_lag_evidence_available"
            for item in floors.values()
        ) else "no_repeatable_room_demand_lag_evidence",
        "floors": floors,
        "physical_room_allocation_identified": False,
        "note": "Lag scans are descriptive timing evidence only; they do not measure room-level heat output or identify slab thermal capacity.",
    }



def build_solar_room_response_analysis(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    interval_minutes: int = 30,
    max_age_minutes: float = 45.0,
    maximum_lag_intervals: int = 4,
    minimum_pairs: int = 24,
) -> dict[str, Any]:
    """Screen room-temperature response to solar variation, without estimating watts.

    Solar irradiance is paired with later room-temperature slopes. A chronological
    training/holdout split selects and checks the lag; available outdoor-temperature
    and room-demand covariates are partialled out. Window-open intervals and cadence
    gaps are excluded. Correlation is not a solar-gain coefficient or causal proof.
    """
    if interval_minutes <= 0 or max_age_minutes < 0:
        raise ValueError("Invalid solar response thresholds")
    if maximum_lag_intervals < 0 or maximum_lag_intervals > 12 or minimum_pairs < 12:
        raise ValueError("Invalid solar response limits")

    room_ids = [str(room.get("id", "unknown")) for room in rooms]
    if not room_ids or len(set(room_ids)) != len(room_ids):
        return {"status": "insufficient_room_configuration", "rooms": {}}

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

    exclusions = {
        room_id: {
            "invalid_or_stale_solar": 0,
            "unsupported_solar_unit": 0,
            "invalid_or_stale_temperature": 0,
            "open_window": 0,
            "invalid_or_stale_demand": 0,
            "invalid_cadence": 0,
        } for room_id in room_ids
    }
    intervals = []
    for (stamp0, first), (stamp1, second) in zip(parsed, parsed[1:]):
        elapsed = (stamp1 - stamp0).total_seconds() / 60
        if abs(elapsed - interval_minutes) > 1:
            intervals.append(None)
            for room_id in room_ids:
                exclusions[room_id]["invalid_cadence"] += 1
            continue
        first_channels = first.get("channels") or {}
        solar = _finite_number_or_none(first_channels.get("solar_radiation"))
        solar_age = _finite_number_or_none(first_channels.get("solar_radiation_age_minutes"))
        solar_unit = str(first_channels.get("solar_radiation_unit") or "").strip().lower().replace(" ", "")
        solar_scales = {"w/m2": 1.0, "w/m²": 1.0, "kw/m2": 1000.0, "kw/m²": 1000.0}
        if solar is None or solar < 0 or (solar_age is not None and solar_age > max_age_minutes):
            solar_w_m2 = None
        elif solar_unit in solar_scales:
            solar_w_m2 = solar * solar_scales[solar_unit]
        else:
            solar_w_m2 = None
        response_channels = second.get("channels") or {}
        outdoor = _finite_number_or_none(response_channels.get("outdoor_temperature"))
        outdoor_age = _finite_number_or_none(response_channels.get("outdoor_temperature_age_minutes"))
        if outdoor_age is not None and outdoor_age > max_age_minutes:
            outdoor = None
        room_intervals = {}
        first_rooms, second_rooms = first.get("rooms") or {}, second.get("rooms") or {}
        for room_id in room_ids:
            left, right = first_rooms.get(room_id, {}), second_rooms.get(room_id, {})
            t0 = _finite_number_or_none(left.get("temperature"))
            t1 = _finite_number_or_none(right.get("temperature"))
            age0 = _finite_number_or_none(left.get("temperature_age_minutes"))
            age1 = _finite_number_or_none(right.get("temperature_age_minutes"))
            if (t0 is None or t1 is None
                    or (age0 is not None and age0 > max_age_minutes)
                    or (age1 is not None and age1 > max_age_minutes)):
                exclusions[room_id]["invalid_or_stale_temperature"] += 1
                room_intervals[room_id] = None
                continue
            window_open = False
            window_invalid = False
            for state in (left, right):
                if "window" in state:
                    window = _switch_state(state.get("window"))
                    age = _finite_number_or_none(state.get("window_age_minutes"))
                    if window is True and (age is None or age <= max_age_minutes):
                        window_open = True
                    elif window is None or (age is not None and age > max_age_minutes):
                        window_invalid = True
            if window_open:
                exclusions[room_id]["open_window"] += 1
                room_intervals[room_id] = None
                continue
            demand = _switch_state(right.get("demand"))
            demand_age = _finite_number_or_none(right.get("demand_age_minutes"))
            if demand is None or (demand_age is not None and demand_age > max_age_minutes):
                exclusions[room_id]["invalid_or_stale_demand"] += 1
                demand_value = None
            else:
                demand_value = float(demand)
            room_intervals[room_id] = {
                "slope": (t1 - t0) / (elapsed / 60),
                "solar_w_m2": solar_w_m2,
                "solar_valid": solar_w_m2 is not None,
                "outdoor_c": outdoor,
                "demand": demand_value,
                "window_unknown": window_invalid,
            }
            if solar_w_m2 is None:
                if solar is None or solar < 0 or (solar_age is not None and solar_age > max_age_minutes):
                    exclusions[room_id]["invalid_or_stale_solar"] += 1
                else:
                    exclusions[room_id]["unsupported_solar_unit"] += 1
        intervals.append({"_timestamp": stamp0, **room_intervals})

    room_results = {}
    for room_id, room in zip(room_ids, rooms):
        lag_results = []
        for lag in range(maximum_lag_intervals + 1):
            pairs = []
            for index, source_row in enumerate(intervals):
                target_index = index + lag
                if source_row is None or target_index >= len(intervals):
                    continue
                target_row = intervals[target_index]
                if target_row is None:
                    continue
                elapsed_lag = (target_row["_timestamp"] - source_row["_timestamp"]).total_seconds() / 60
                if abs(elapsed_lag - lag * interval_minutes) > 1:
                    continue
                source = source_row.get(room_id)
                target = target_row.get(room_id)
                if not source or not target or not source["solar_valid"]:
                    continue
                if source["solar_w_m2"] is None or target["slope"] is None:
                    continue
                pairs.append({
                    "solar": source["solar_w_m2"],
                    "slope": target["slope"],
                    "outdoor": target["outdoor_c"],
                    "demand": target["demand"],
                })
            split = int(len(pairs) * 0.7)
            training, holdout = pairs[:split], pairs[split:]
            candidate_covariates = ("outdoor", "demand")
            covariates = [
                name for name in candidate_covariates
                if training and sum(item[name] is not None for item in training) / len(training) >= 0.7
            ]
            train_complete = [item for item in training if all(item[name] is not None for name in covariates)]
            holdout_complete = [item for item in holdout if all(item[name] is not None for name in covariates)]
            train_adjusted = _partial_correlation_diagnostics(
                [item["solar"] for item in train_complete],
                [item["slope"] for item in train_complete],
                {name: [item[name] for item in train_complete] for name in covariates},
            ) if covariates else {
                "status": "withheld_no_adjustment_covariates",
                "partial_pearson_r": None,
                "confidence_interval_95": None,
                "covariates": [],
            }
            holdout_adjusted = _partial_correlation_diagnostics(
                [item["solar"] for item in holdout_complete],
                [item["slope"] for item in holdout_complete],
                {name: [item[name] for item in holdout_complete] for name in covariates},
            ) if covariates else {
                "status": "withheld_no_adjustment_covariates",
                "partial_pearson_r": None,
                "confidence_interval_95": None,
                "covariates": [],
            }
            lag_results.append({
                "lag_intervals": lag,
                "lag_minutes": lag * interval_minutes,
                "paired_intervals": len(pairs),
                "training_intervals": len(training),
                "holdout_intervals": len(holdout),
                "adjustment_covariates": covariates,
                "training_adjusted_status": train_adjusted["status"],
                "training_partial_pearson_r": train_adjusted.get("partial_pearson_r"),
                "training_partial_confidence_interval_95": train_adjusted.get("confidence_interval_95"),
                "training_complete_cases": train_adjusted.get("paired_intervals", len(train_complete)),
                "holdout_adjusted_status": holdout_adjusted["status"],
                "holdout_partial_pearson_r": holdout_adjusted.get("partial_pearson_r"),
                "holdout_partial_confidence_interval_95": holdout_adjusted.get("confidence_interval_95"),
                "holdout_complete_cases": holdout_adjusted.get("paired_intervals", len(holdout_complete)),
                "holdout_same_direction": (
                    train_adjusted.get("partial_pearson_r") is not None
                    and holdout_adjusted.get("partial_pearson_r") is not None
                    and train_adjusted["partial_pearson_r"] * holdout_adjusted["partial_pearson_r"] > 0
                ),
            })
        eligible = [
            item for item in lag_results
            if item["paired_intervals"] >= minimum_pairs
            and item["training_adjusted_status"] == "adjusted_association_available"
            and item["holdout_adjusted_status"] == "adjusted_association_available"
            and item["training_partial_pearson_r"] is not None
            and item["holdout_partial_pearson_r"] is not None
            and item["training_partial_confidence_interval_95"] is not None
            and (item["training_partial_confidence_interval_95"][0] > 0 or item["training_partial_confidence_interval_95"][1] < 0)
            and item["holdout_partial_confidence_interval_95"] is not None
            and (item["holdout_partial_confidence_interval_95"][0] > 0 or item["holdout_partial_confidence_interval_95"][1] < 0)
        ]
        eligible.sort(key=lambda item: abs(item["training_partial_pearson_r"]), reverse=True)
        selected = eligible[0] if eligible else None
        runner_up = eligible[1] if len(eligible) > 1 else None
        ambiguous = bool(
            selected and runner_up
            and abs(selected["training_partial_pearson_r"]) - abs(runner_up["training_partial_pearson_r"]) <= 0.05
        )
        repeatable = bool(selected and selected["holdout_same_direction"] and not ambiguous)
        room_results[room_id] = {
            "status": (
                "repeatable_adjusted_solar_response_association_only" if repeatable
                else "solar_response_association_ambiguous_or_unstable" if selected
                else "insufficient_solar_response_pairs_or_adjustment"
            ),
            "selected_training_lag_minutes": selected["lag_minutes"] if selected else None,
            "training_partial_pearson_r": selected["training_partial_pearson_r"] if selected else None,
            "holdout_partial_pearson_r": selected["holdout_partial_pearson_r"] if selected else None,
            "holdout_same_direction": selected["holdout_same_direction"] if selected else None,
            "neighbouring_lag_peak_ambiguous": ambiguous if selected else None,
            "lag_scan": lag_results,
            "configured_window_area_m2": _finite_number_or_none(room.get("window_area_m2")),
            "window_orientation": room.get("window_orientation"),
            "solar_gain_w": None,
            "interpretation": (
                "Adjusted solar/temperature timing association only. Global irradiance and room "
                "temperature do not identify orientation-specific transmitted solar heat; no solar "
                "gain in watts, glazing coefficient or room thermal parameter is estimated."
            ),
        }

    repeatable_rooms = [
        room_id for room_id, item in room_results.items()
        if item["status"] == "repeatable_adjusted_solar_response_association_only"
    ]
    return {
        "status": "repeatable_solar_response_associations_available" if repeatable_rooms else "no_repeatable_solar_response_associations",
        "rooms_with_repeatable_association": repeatable_rooms,
        "rooms": room_results,
        "excluded_observations": exclusions,
        "maximum_lag_minutes": maximum_lag_intervals * interval_minutes,
        "solar_gain_w_identified": False,
        "physical_room_parameters_identified": False,
        "note": (
            "Solar response scans use a chronological 70/30 split and adjust for adequately "
            "covered outdoor-temperature and room-demand covariates. Open-window intervals and "
            "cadence gaps are excluded. This is observational evidence, not a solar heat-gain "
            "coefficient, transmitted watts, room heat delivery or proof of causality."
        ),
    }



def build_water_side_thermal_output(
    samples: list[dict[str, Any]],
    interval_minutes: int = 30,
    max_age_minutes: int = 45,
    water_density_kg_m3: float = 997.0,
    specific_heat_j_kg_k: float = 4186.0,
) -> dict[str, Any]:
    """Calculate hydronic heat-transfer rate only from fresh flow and ΔT.

    Flow units are read from the Recorder entity's unit_of_measurement.
    The result is system-side water heat transfer, not room-level delivery.
    """
    if interval_minutes <= 0 or max_age_minutes < 0:
        raise ValueError("Invalid thermal-output sampling thresholds")
    if not math.isfinite(water_density_kg_m3) or water_density_kg_m3 <= 0:
        raise ValueError("Water density must be positive")
    if not math.isfinite(specific_heat_j_kg_k) or specific_heat_j_kg_k <= 0:
        raise ValueError("Specific heat must be positive")

    watts = []
    timestamped_watts = []
    excluded = {
        "missing_or_stale_flow": 0,
        "missing_or_stale_supply_or_return": 0,
        "unsupported_flow_unit": 0,
        "nonpositive_flow": 0,
        "nonpositive_supply_return_delta": 0,
    }
    units_seen = set()
    for sample in samples:
        channels = sample.get("channels") or {}
        flow = _finite_number_or_none(channels.get("water_flow_rate"))
        supply = _finite_number_or_none(channels.get("water_supply_temperature"))
        return_temp = _finite_number_or_none(channels.get("water_return_temperature"))
        flow_age = _finite_number_or_none(channels.get("water_flow_rate_age_minutes"))
        supply_age = _finite_number_or_none(channels.get("water_supply_temperature_age_minutes"))
        return_age = _finite_number_or_none(channels.get("water_return_temperature_age_minutes"))
        if flow is None or (flow_age is not None and flow_age > max_age_minutes):
            excluded["missing_or_stale_flow"] += 1
            continue
        if (
            supply is None or return_temp is None
            or (supply_age is not None and supply_age > max_age_minutes)
            or (return_age is not None and return_age > max_age_minutes)
        ):
            excluded["missing_or_stale_supply_or_return"] += 1
            continue
        unit = str(channels.get("water_flow_rate_unit") or "").strip().lower()
        units_seen.add(unit or "missing")
        normalized = unit.replace("³", "3").replace("^", "").replace(" ", "")
        if normalized in ("l/min", "lpm", "l/minute"):
            mass_flow_kg_s = flow / 60.0 * water_density_kg_m3 / 1000.0
        elif normalized in ("m3/h", "m3/hr", "m3/hour", "m3h-1"):
            mass_flow_kg_s = flow / 3600.0 * water_density_kg_m3
        elif normalized in ("l/s", "lps", "l/sec"):
            mass_flow_kg_s = flow * water_density_kg_m3 / 1000.0
        elif normalized in ("kg/h", "kg/hr", "kg/hour"):
            mass_flow_kg_s = flow / 3600.0
        elif normalized in ("kg/s", "kg/sec"):
            mass_flow_kg_s = flow
        else:
            excluded["unsupported_flow_unit"] += 1
            continue
        if mass_flow_kg_s <= 0:
            excluded["nonpositive_flow"] += 1
            continue
        delta_t = supply - return_temp
        if delta_t <= 0:
            excluded["nonpositive_supply_return_delta"] += 1
            continue
        output_w = mass_flow_kg_s * specific_heat_j_kg_k * delta_t
        watts.append(output_w)
        try:
            stamp = datetime.fromisoformat(str(sample.get("timestamp")).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            timestamped_watts.append((stamp, output_w))
        except (TypeError, ValueError):
            pass

    timestamped_watts.sort(key=lambda item: item[0])
    integrated_energy_wh = 0.0
    integrated_intervals = 0
    for previous, current in zip(timestamped_watts, timestamped_watts[1:]):
        elapsed_minutes = (current[0] - previous[0]).total_seconds() / 60
        if abs(elapsed_minutes - interval_minutes) <= 2:
            integrated_energy_wh += ((previous[1] + current[1]) / 2 * elapsed_minutes / 60)
            integrated_intervals += 1
    ordered = sorted(watts)
    def percentile(values, fraction):
        if not values:
            return None
        position = (len(values) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    return {
        "status": "system_water_side_output_available" if ordered else "withheld_missing_valid_flow_and_delta_t",
        "valid_samples": len(ordered),
        "sample_count": len(samples),
        "coverage_percent": round(100 * len(ordered) / len(samples), 1) if samples else 0.0,
        "median_thermal_output_w": round(percentile(ordered, 0.5), 1) if ordered else None,
        "p10_thermal_output_w": round(percentile(ordered, 0.1), 1) if ordered else None,
        "p90_thermal_output_w": round(percentile(ordered, 0.9), 1) if ordered else None,
        "minimum_thermal_output_w": round(ordered[0], 1) if ordered else None,
        "maximum_thermal_output_w": round(ordered[-1], 1) if ordered else None,
        "integrated_water_side_energy_kwh": round(integrated_energy_wh / 1000, 4),
        "integrated_valid_intervals": integrated_intervals,
        "integration_method": "trapezoidal_between_adjacent_valid_samples_at_configured_cadence",
        "flow_units_observed": sorted(units_seen),
        "excluded_samples": excluded,
        "assumptions": {
            "water_density_kg_m3": water_density_kg_m3,
            "specific_heat_j_kg_k": specific_heat_j_kg_k,
            "formula": "mass_flow_kg_s * specific_heat_j_kg_k * (supply_c - return_c)",
        },
        "room_heat_allocation_w": None,
        "status_scope": "system_water_side_only_not_room_delivery",
        "note": (
            "Calculated water-side heat transfer requires fresh positive flow and a positive "
            "supply-return temperature difference. It is not automatically equal to heat "
            "delivered to conditioned rooms: distribution losses, bypasses, buffer tanks and "
            "other hydraulic paths may intervene. No room allocation or RC capacity is inferred."
        ),
    }


def build_ufh_slab_observability(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    interval_minutes: int = 30,
    minimum_on_intervals: int = 4,
    minimum_off_intervals: int = 4,
) -> dict[str, Any]:
    """Assess UFH excitation using complete-room, cadence-valid observations.

    Pump electricity is only an on/off indicator. The room response is reported
    per room and for the complete configured floor; partial-room averages are
    deliberately rejected because changing sensor coverage can mimic heating.
    """
    if interval_minutes <= 0 or minimum_on_intervals < 1 or minimum_off_intervals < 1:
        raise ValueError("Invalid UFH observability thresholds")

    results = {}
    for floor_id in ("ground_floor", "upstairs"):
        floor_rooms = [
            room for room in rooms
            if room.get("floor", "ground_floor") == floor_id
        ]
        room_ids = [str(room.get("id", "unknown")) for room in floor_rooms]
        if not room_ids:
            results[floor_id] = {
                "status": "no_rooms_configured",
                "pump_on_intervals": 0,
                "pump_off_intervals": 0,
                "skipped_intervals": 0,
                "excluded_observations": {},
                "room_response": {},
                "slab_temperature_available": False,
                "thermal_output_w": None,
                "slab_capacity_wh_per_k": None,
                "note": "No configured rooms exist on this floor; no room response is calculated.",
            }
            continue
        pump_key = f"pump:{floor_id}:power"
        on_rates, off_rates, on_water_delta = [], [], []
        per_room_rates = {room_id: {"pump_on": [], "pump_off": []} for room_id in room_ids}
        on_count = off_count = skipped = 0
        exclusions = {
            "incomplete_room_temperature": 0,
            "open_window": 0,
            "invalid_window_state": 0,
            "invalid_or_stale_pump": 0,
            "invalid_timestamp_or_cadence": 0,
        }
        previous = None
        parsed_samples = []
        for sample in samples:
            try:
                stamp = datetime.fromisoformat(str(sample.get("timestamp")).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                parsed_samples.append((stamp.astimezone(timezone.utc), sample))
            except (TypeError, ValueError):
                exclusions["invalid_timestamp_or_cadence"] += 1
        parsed_samples.sort(key=lambda item: item[0])

        for stamp, sample in parsed_samples:
            channels = sample.get("channels") or {}
            room_values = sample.get("rooms") or {}
            pump_power = _finite_number_or_none(channels.get(pump_key))
            pump_age = _finite_number_or_none(channels.get(pump_key + "_age_minutes"))
            if pump_power is None or (pump_age is not None and pump_age > 45):
                previous = None
                skipped += 1
                exclusions["invalid_or_stale_pump"] += 1
                continue

            temperatures = {}
            invalid_temperature = False
            open_window = False
            invalid_window = False
            for room_id in room_ids:
                room_data = room_values.get(room_id, {})
                temperature = _finite_number_or_none(room_data.get("temperature"))
                temp_age = _finite_number_or_none(room_data.get("temperature_age_minutes"))
                if temperature is None or (temp_age is not None and temp_age > 45):
                    invalid_temperature = True
                    break
                temperatures[room_id] = temperature
                # An explicitly open window invalidates this interval. Unknown
                # window state is counted but does not imply that the window is
                # open; integrations without a configured window sensor remain usable.
                if "window" in room_data:
                    window = _switch_state(room_data.get("window"))
                    window_age = _finite_number_or_none(room_data.get("window_age_minutes"))
                    if window is True and (window_age is None or window_age <= 45):
                        open_window = True
                    elif window is None or (window_age is not None and window_age > 45):
                        invalid_window = True
            if invalid_temperature:
                previous = None
                skipped += 1
                exclusions["incomplete_room_temperature"] += 1
                continue
            if open_window:
                previous = None
                skipped += 1
                exclusions["open_window"] += 1
                continue
            if invalid_window:
                exclusions["invalid_window_state"] += 1

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
            current = (stamp, temperatures, pump_power > 5.0, water_delta)
            if previous is not None:
                elapsed = (stamp - previous[0]).total_seconds() / 60
                if abs(elapsed - interval_minutes) <= 2:
                    rates = {
                        room_id: (temperatures[room_id] - previous[1][room_id]) / (elapsed / 60)
                        for room_id in room_ids
                    }
                    if current[2]:
                        on_count += 1
                        on_rates.append(sum(rates.values()) / len(rates))
                        for room_id, rate in rates.items():
                            per_room_rates[room_id]["pump_on"].append(rate)
                        if water_delta is not None:
                            on_water_delta.append(water_delta)
                    else:
                        off_count += 1
                        off_rates.append(sum(rates.values()) / len(rates))
                        for room_id, rate in rates.items():
                            per_room_rates[room_id]["pump_off"].append(rate)
                else:
                    skipped += 1
                    exclusions["invalid_timestamp_or_cadence"] += 1
                    previous = None
                    continue
            previous = current

        def median_or_none(values):
            ordered = sorted(values)
            if not ordered:
                return None
            middle = len(ordered) // 2
            return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2

        enough_excitation = on_count >= minimum_on_intervals and off_count >= minimum_off_intervals
        room_response = {
            room_id: {
                "pump_on_intervals": len(values["pump_on"]),
                "pump_off_intervals": len(values["pump_off"]),
                "median_temperature_rate_pump_on_c_per_hour": (
                    round(median_or_none(values["pump_on"]), 4) if values["pump_on"] else None
                ),
                "median_temperature_rate_pump_off_c_per_hour": (
                    round(median_or_none(values["pump_off"]), 4) if values["pump_off"] else None
                ),
            }
            for room_id, values in per_room_rates.items()
        }
        results[floor_id] = {
            "status": (
                "pump_on_off_excitation_observed_but_capacity_unidentifiable"
                if enough_excitation else "insufficient_pump_excitation_or_history"
            ),
            "pump_on_intervals": on_count,
            "pump_off_intervals": off_count,
            "skipped_intervals": skipped,
            "excluded_observations": exclusions,
            "median_room_temperature_rate_pump_on_c_per_hour": (
                round(median_or_none(on_rates), 4) if on_rates else None
            ),
            "median_room_temperature_rate_pump_off_c_per_hour": (
                round(median_or_none(off_rates), 4) if off_rates else None
            ),
            "median_supply_return_delta_t_pump_on_c": (
                round(median_or_none(on_water_delta), 3) if on_water_delta else None
            ),
            "rooms_included": len(floor_rooms),
            "room_response": room_response,
            "slab_temperature_available": False,
            "thermal_output_w": None,
            "slab_capacity_wh_per_k": None,
            "note": (
                "Only intervals with fresh temperatures for every configured room are used; "
                "explicitly open-window intervals and cadence gaps are excluded. Per-room rates "
                "are descriptive responses, not delivered heat. Pump electricity is auxiliary "
                "consumption, and supply-return delta-T is not thermal output without measured flow."
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
            "This is a complete-room excitation and observability screen only. It does not "
            "calibrate UFH slab capacity, water-side output or room-level heat delivery."
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




def _partial_correlation_diagnostics(
    xs: list[float],
    ys: list[float],
    covariates: dict[str, list[float]],
) -> dict[str, Any]:
    """Residualize input and response against linear covariates.

    Modified Gram-Schmidt is used so rank-deficient or badly conditioned
    adjustment designs are reported rather than silently inverted.
    """
    names = list(covariates)
    n = len(xs)
    if n < max(8, len(names) + 5) or len(ys) != n:
        return {
            "status": "insufficient_complete_cases",
            "paired_intervals": n,
            "covariates": names,
            "partial_pearson_r": None,
            "design_rank": 0,
            "design_columns": len(names) + 1,
            "confidence_interval_95": None,
            "confidence_interval_degrees_of_freedom": n - len(names) - 3,
            "condition_proxy": None,
        }
    columns = [[1.0] * n]
    for name in names:
        values = covariates[name]
        if len(values) != n:
            raise ValueError("Covariate row length mismatch")
        mean = sum(values) / n
        variance = sum((value - mean) ** 2 for value in values) / n
        scale = math.sqrt(variance)
        if scale <= 1e-10:
            return {
                "status": "rank_deficient_adjustment_design",
                "paired_intervals": n,
                "covariates": names,
                "partial_pearson_r": None,
                "design_rank": len(columns),
                "design_columns": len(names) + 1,
                "condition_proxy": None,
                "confidence_interval_95": None,
                "confidence_interval_degrees_of_freedom": n - len(names) - 3,
            }
        columns.append([(value - mean) / scale for value in values])

    orthogonal = []
    original_norms = []
    orthogonal_norms = []
    for column in columns:
        vector = list(column)
        original_norm = math.sqrt(sum(value * value for value in vector))
        original_norms.append(original_norm)
        for basis in orthogonal:
            projection = sum(a * b for a, b in zip(vector, basis))
            vector = [value - projection * direction for value, direction in zip(vector, basis)]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= max(1e-9, original_norm * 1e-8):
            return {
                "status": "rank_deficient_adjustment_design",
                "paired_intervals": n,
                "covariates": names,
                "partial_pearson_r": None,
                "design_rank": len(orthogonal),
                "design_columns": len(columns),
                "condition_proxy": None,
                "confidence_interval_95": None,
                "confidence_interval_degrees_of_freedom": n - len(names) - 3,
            }
        orthogonal.append([value / norm for value in vector])
        orthogonal_norms.append(norm)

    def residualize(values):
        residual = list(values)
        for basis in orthogonal:
            projection = sum(a * b for a, b in zip(residual, basis))
            residual = [value - projection * direction for value, direction in zip(residual, basis)]
        return residual

    residual_x, residual_y = residualize(xs), residualize(ys)
    mean_x, mean_y = sum(residual_x) / n, sum(residual_y) / n
    var_x = sum((value - mean_x) ** 2 for value in residual_x)
    var_y = sum((value - mean_y) ** 2 for value in residual_y)
    if var_x <= 1e-12 or var_y <= 1e-12:
        return {
            "status": "no_residual_variation_after_adjustment",
            "paired_intervals": n,
            "covariates": names,
            "partial_pearson_r": None,
            "design_rank": len(orthogonal),
            "design_columns": len(columns),
            "confidence_interval_95": None,
            "confidence_interval_degrees_of_freedom": n - len(names) - 3,
            "condition_proxy": round(max(orthogonal_norms) / max(min(orthogonal_norms), 1e-12), 3),
        }
    correlation = sum(
        (a - mean_x) * (b - mean_y) for a, b in zip(residual_x, residual_y)
    ) / math.sqrt(var_x * var_y)
    condition_proxy = max(orthogonal_norms) / max(min(orthogonal_norms), 1e-12)
    partial_df = n - len(names) - 3
    bounded_r = max(-0.999999, min(0.999999, correlation))
    fisher_z = 0.5 * math.log((1 + bounded_r) / (1 - bounded_r))
    fisher_margin = 1.96 / math.sqrt(partial_df) if partial_df > 0 else None
    partial_interval = (
        [round(math.tanh(fisher_z - fisher_margin), 4), round(math.tanh(fisher_z + fisher_margin), 4)]
        if fisher_margin is not None else None
    )
    if condition_proxy > 100.0:
        return {
            "status": "ill_conditioned_adjustment_design",
            "paired_intervals": n,
            "covariates": names,
            "partial_pearson_r": None,
            "design_rank": len(orthogonal),
            "design_columns": len(columns),
            "condition_proxy": round(condition_proxy, 3),
            "confidence_interval_95": partial_interval,
            "confidence_interval_degrees_of_freedom": partial_df,
        }
    return {
        "status": "adjusted_association_available",
        "paired_intervals": n,
        "covariates": names,
        "partial_pearson_r": round(max(-1.0, min(1.0, correlation)), 4),
        "design_rank": len(orthogonal),
        "design_columns": len(columns),
        "condition_proxy": round(condition_proxy, 3),
        "confidence_interval_95": partial_interval,
        "confidence_interval_degrees_of_freedom": partial_df,
        "confidence_interval_method": "nominal_fisher_z_partial_correlation_independent_pairs",
    }




def _partial_correlation_block_sensitivity(
    xs: list[float],
    ys: list[float],
    covariates: dict[str, list[float]],
    minimum_pairs: int = 40,
    replicates: int = 80,
    block_length: int = 4,
) -> dict[str, Any]:
    """Deterministic moving-block sensitivity for adjusted association."""
    n = len(xs)
    result = {
        "status": "insufficient_history_for_block_sensitivity",
        "method": "deterministic_moving_block_resampling",
        "block_length_intervals": min(block_length, n) if n else None,
        "requested_replicates": replicates if n >= minimum_pairs else 0,
        "successful_replicates": 0,
        "lower_5_percentile_partial_r": None,
        "median_partial_r": None,
        "upper_95_percentile_partial_r": None,
        "note": (
            "This is a moving-block sensitivity range, not a confidence interval. "
            "It does not correct for serial dependence in the nominal Fisher interval "
            "or account for unmeasured confounding."
        ),
    }
    if n < minimum_pairs or len(ys) != n:
        return result
    block = min(block_length, max(1, n // 4))
    values = []
    for replicate in range(replicates):
        selected = []
        block_number = 0
        while len(selected) < n:
            max_start = n - block
            start_index = (replicate * 37 + block_number * 17) % (max_start + 1)
            selected.extend(range(start_index, start_index + block))
            block_number += 1
        selected = selected[:n]
        sample_covariates = {
            name: [series[index] for index in selected]
            for name, series in covariates.items()
        }
        fitted = _partial_correlation_diagnostics(
            [xs[index] for index in selected],
            [ys[index] for index in selected],
            sample_covariates,
        )
        value = fitted.get("partial_pearson_r")
        if fitted.get("status") == "adjusted_association_available" and value is not None:
            values.append(value)
    result["successful_replicates"] = len(values)
    if len(values) < max(20, replicates // 2):
        result["status"] = "insufficient_successful_block_replicates"
        return result
    values.sort()
    lower = values[int(0.05 * (len(values) - 1))]
    middle = values[int(0.50 * (len(values) - 1))]
    upper = values[int(0.95 * (len(values) - 1))]
    result.update({
        "status": "block_sensitivity_available",
        "lower_5_percentile_partial_r": round(lower, 4),
        "median_partial_r": round(middle, 4),
        "upper_95_percentile_partial_r": round(upper, 4),
    })
    return result



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
        response_channels = second.get("channels") or {}
        response_rooms = second.get("rooms") or {}
        demand_states = [
            _switch_state(response_rooms.get(room_id, {}).get("demand"))
            for room_id in room_ids
        ]
        window_states = [
            _switch_state(response_rooms.get(room_id, {}).get("window"))
            for room_id in room_ids
        ]
        covariates = {
            "outdoor_temperature_c": _finite_number_or_none(response_channels.get("outdoor_temperature")),
            "solar_radiation_w_m2": _finite_number_or_none(response_channels.get("solar_radiation")),
            "room_demand_fraction": (
                sum(value is True for value in demand_states) / len(demand_states)
                if all(value is not None for value in demand_states) else None
            ),
            "open_window_fraction": (
                sum(value is True for value in window_states) / len(window_states)
                if all(value is not None for value in window_states) else None
            ),
        }
        interval_rows.append({
            "stamp": stamp0,
            "aggregate_slope": sum(slopes.values()) / len(slopes),
            "room_slopes": slopes,
            "covariates": covariates,
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
                pairs.append((
                    input_power,
                    response_row["aggregate_slope"],
                    response_row["covariates"],
                ))
                for room_id in room_ids:
                    room_pairs[room_id].append((input_power, response_row["room_slopes"][room_id]))
            xs, ys = [item[0] for item in pairs], [item[1] for item in pairs]
            correlation = pearson(xs, ys)
            diagnostics = _pearson_diagnostics(xs, ys)
            candidate_covariates = (
                "outdoor_temperature_c", "solar_radiation_w_m2",
                "room_demand_fraction", "open_window_fraction",
            )
            selected_covariates = [
                name for name in candidate_covariates
                if pairs and sum(item[2].get(name) is not None for item in pairs) / len(pairs) >= 0.7
            ]
            adjusted_pairs = [
                item for item in pairs
                if all(item[2].get(name) is not None for name in selected_covariates)
            ]
            adjusted_covariate_values = {
                name: [item[2][name] for item in adjusted_pairs]
                for name in selected_covariates
            }
            adjusted_xs = [item[0] for item in adjusted_pairs]
            adjusted_ys = [item[1] for item in adjusted_pairs]
            adjusted_diagnostics = _partial_correlation_diagnostics(
                adjusted_xs, adjusted_ys, adjusted_covariate_values,
            )
            adjusted_diagnostics["block_sensitivity"] = (
                _partial_correlation_block_sensitivity(
                    adjusted_xs, adjusted_ys, adjusted_covariate_values,
                )
                if selected_covariates and adjusted_diagnostics["status"] == "adjusted_association_available"
                else {
                    "status": "withheld_no_adjustment_model",
                    "method": "deterministic_moving_block_resampling",
                    "successful_replicates": 0,
                }
            )
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
                "confounder_adjustment": adjusted_diagnostics,
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
        adjusted_valid_lags = [
            item for item in lag_results
            if item["paired_intervals"] >= minimum_intervals
            and item["confounder_adjustment"]["status"] == "adjusted_association_available"
            and item["confounder_adjustment"]["covariates"]
            and item["confounder_adjustment"]["partial_pearson_r"] is not None
        ]
        best_adjusted_lag = max(
            adjusted_valid_lags,
            key=lambda item: abs(item["confounder_adjustment"]["partial_pearson_r"]),
        ) if adjusted_valid_lags else None
        ranked_adjusted_lags = sorted(
            adjusted_valid_lags,
            key=lambda item: abs(item["confounder_adjustment"]["partial_pearson_r"]),
            reverse=True,
        )
        adjusted_lag_peak_status = "insufficient_adjusted_lags"
        if ranked_adjusted_lags:
            adjusted_lag_peak_status = (
                "distinct_adjusted_descriptive_peak"
                if len(ranked_adjusted_lags) == 1
                or abs(ranked_adjusted_lags[0]["confounder_adjustment"]["partial_pearson_r"])
                - abs(ranked_adjusted_lags[1]["confounder_adjustment"]["partial_pearson_r"]) >= 0.05
                else "ambiguous_adjusted_neighbouring_lag_peaks"
            )
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
        elif not adjusted_valid_lags:
            evidence_status = "withheld_confounder_adjustment_unavailable"
        elif not (
            best_adjusted_lag["confounder_adjustment"]["confidence_interval_95"] is not None
            and (
                best_adjusted_lag["confounder_adjustment"]["confidence_interval_95"][0] > 0
                or best_adjusted_lag["confounder_adjustment"]["confidence_interval_95"][1] < 0
            )
        ):
            evidence_status = "withheld_uncertain_adjusted_correlation"
        elif adjusted_lag_peak_status != "distinct_adjusted_descriptive_peak":
            evidence_status = "descriptive_adjusted_lag_ambiguous"
        else:
            evidence_status = "repeatable_adjusted_descriptive_association_only"
        results[key] = {
            "status": "lagged_input_response_pairs_available" if valid_lags else "insufficient_verified_input_response_pairs",
            "evidence_status": evidence_status,
            "plant_input_excitation_status": "sufficient_variation_for_association_screen" if enough_input_excitation else "weak_or_flat_input_variation",
            "response_stability_status": response_stability,
            "correlation_uncertainty_status": correlation_uncertainty_status,
            "confounder_adjusted_lag_count": len(adjusted_valid_lags),
            "strongest_adjusted_lag_minutes": best_adjusted_lag["lag_minutes"] if best_adjusted_lag else None,
            "strongest_adjusted_partial_pearson_r": (
                best_adjusted_lag["confounder_adjustment"]["partial_pearson_r"]
                if best_adjusted_lag else None
            ),
            "adjusted_lag_peak_status": adjusted_lag_peak_status,
            "strongest_adjusted_partial_correlation_sensitivity": (
                best_adjusted_lag["confounder_adjustment"]["block_sensitivity"]
                if best_adjusted_lag else None
            ),
            "confounder_adjustment_status": (
                "available" if adjusted_valid_lags else "withheld_no_well_conditioned_covariate_model"
            ),
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


def _multizone_holdout_diagnostics(
    holdout: list[tuple[datetime, list[float], float]],
    physical: list[float],
    intercept: float,
    interval_minutes: int,
    minimum_resamples: int = 20,
    replicates: int = 80,
) -> dict[str, Any]:
    """Describe chronological holdout residuals and block-resampling sensitivity."""
    if not holdout:
        return {
            "status": "insufficient_holdout",
            "interval_count": 0,
            "mean_bias_c": None,
            "residual_standard_deviation_c": None,
            "lag_one_residual_correlation": None,
            "residual_linear_trend_c_per_day": None,
            "block_error_sensitivity": None,
        }
    residuals = [
        (intercept + sum(physical[j] * row[1][j] for j in range(len(physical)))
         - row[2]) * interval_minutes / 60
        for row in holdout
    ]
    count = len(residuals)
    mean_error = sum(residuals) / count
    variance = sum((value - mean_error) ** 2 for value in residuals) / count
    lag_correlation = None
    if count >= 3:
        left, right = residuals[:-1], residuals[1:]
        left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
        numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
        denominator = math.sqrt(
            sum((a - left_mean) ** 2 for a in left)
            * sum((b - right_mean) ** 2 for b in right)
        )
        if denominator > 1e-12:
            lag_correlation = numerator / denominator
    elapsed_days = (
        (holdout[-1][0] - holdout[0][0]).total_seconds() / 86400
        if count > 1 else 0.0
    )
    trend = None
    if count >= 3 and elapsed_days > 0:
        xs = [(row[0] - holdout[0][0]).total_seconds() / 86400 for row in holdout]
        x_mean = sum(xs) / count
        denominator = sum((value - x_mean) ** 2 for value in xs)
        if denominator > 1e-12:
            trend = sum(
                (x - x_mean) * (error - mean_error)
                for x, error in zip(xs, residuals)
            ) / denominator
    block_length = min(4, max(1, count // 4))
    mae_values, rmse_values = [], []
    requested = replicates if count >= 20 else 0
    if requested:
        for replicate in range(requested):
            selected = []
            block_number = 0
            while len(selected) < count:
                max_start = count - block_length
                start_index = (replicate * 29 + block_number * 13) % (max_start + 1)
                selected.extend(residuals[start_index:start_index + block_length])
                block_number += 1
            selected = selected[:count]
            mae_values.append(sum(abs(value) for value in selected) / count)
            rmse_values.append(math.sqrt(sum(value * value for value in selected) / count))
    def percentile(values, fraction):
        ordered = sorted(values)
        return ordered[int(fraction * (len(ordered) - 1))] if ordered else None
    sensitivity = None
    if len(mae_values) >= minimum_resamples:
        sensitivity = {
            "method": "deterministic_moving_block_resampling",
            "block_length_intervals": block_length,
            "requested_replicates": requested,
            "successful_replicates": len(mae_values),
            "mae_5th_50th_95th_c": [
                round(percentile(mae_values, q), 4) for q in (0.05, 0.5, 0.95)
            ],
            "rmse_5th_50th_95th_c": [
                round(percentile(rmse_values, q), 4) for q in (0.05, 0.5, 0.95)
            ],
            "interpretation": "Holdout error sensitivity range, not a confidence interval.",
        }
    timestamps = [row[0] for row in holdout]
    gaps = [
        (second - first).total_seconds() / 60
        for first, second in zip(timestamps, timestamps[1:])
    ]
    return {
        "status": "holdout_residuals_available",
        "interval_count": count,
        "start": timestamps[0].isoformat(),
        "end": timestamps[-1].isoformat(),
        "span_hours": round(elapsed_days * 24, 2),
        "median_gap_minutes": round(sorted(gaps)[len(gaps) // 2], 2) if gaps else None,
        "cadence_deviation_intervals": sum(abs(gap - interval_minutes) > 1 for gap in gaps),
        "mean_bias_c": round(mean_error, 4),
        "residual_standard_deviation_c": round(math.sqrt(variance), 4),
        "maximum_absolute_residual_c": round(max(abs(value) for value in residuals), 4),
        "lag_one_residual_correlation": round(lag_correlation, 4) if lag_correlation is not None else None,
        "residual_linear_trend_c_per_day": round(trend, 4) if trend is not None else None,
        "block_error_sensitivity": sensitivity,
        "note": (
            "Residual autocorrelation and trend are descriptive diagnostics. Block resampling "
            "summarises sensitivity of holdout errors and is not a confidence interval."
        ),
    }


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
    max_age_minutes: float = 45.0,
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
    if not math.isfinite(max_age_minutes) or max_age_minutes < 0:
        raise ValueError("Maximum sensor age must be finite and non-negative")
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
            "stale_room_temperature": 0,
            "stale_room_state": 0,
            "stale_solar_or_outdoor": 0,
            "stale_ac_or_boiler_telemetry": 0,
            "unsupported_solar_unit": 0,
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
            stale_temperature = any(
                (numeric(first_rooms.get(rid, {}).get("temperature_age_minutes")) is not None
                 and numeric(first_rooms.get(rid, {}).get("temperature_age_minutes")) > max_age_minutes)
                or (numeric(second_rooms.get(rid, {}).get("temperature_age_minutes")) is not None
                    and numeric(second_rooms.get(rid, {}).get("temperature_age_minutes")) > max_age_minutes)
                for rid in room_ids
            )
            if stale_temperature:
                exclusions["stale_room_temperature"] += 1
                continue
            if any(temps0[rid] is None or temps1[rid] is None for rid in room_ids):
                exclusions["non_contiguous_or_missing_temperature"] += 1
                continue
            def fresh_room_state(room_data, field):
                age = numeric(room_data.get(field + "_age_minutes"))
                return (age is None or age <= max_age_minutes) and state_off(room_data.get(field))
            stale_room_states = any(
                (numeric(data.get(field + "_age_minutes")) is not None
                 and numeric(data.get(field + "_age_minutes")) > max_age_minutes)
                for rid in room_ids
                for data in (first_rooms.get(rid, {}), second_rooms.get(rid, {}))
                for field in ("demand", "window")
            )
            if stale_room_states:
                exclusions["stale_room_state"] += 1
            clean_states = all(
                fresh_room_state(first_rooms.get(rid, {}), "demand")
                and fresh_room_state(second_rooms.get(rid, {}), "demand")
                and fresh_room_state(first_rooms.get(rid, {}), "window")
                and fresh_room_state(second_rooms.get(rid, {}), "window")
                for rid in room_ids
            )
            if not clean_states:
                exclusions["demand_or_window_not_confirmed_off_closed"] += 1
                continue
            first_channels, second_channels = first.get("channels", {}), second.get("channels", {})
            solar0 = numeric(first_channels.get("solar_radiation"))
            solar1 = numeric(second_channels.get("solar_radiation"))
            sensor_ages = (
                numeric(first_channels.get("solar_radiation_age_minutes")),
                numeric(second_channels.get("solar_radiation_age_minutes")),
                numeric(first_channels.get("outdoor_temperature_age_minutes")),
                numeric(second_channels.get("outdoor_temperature_age_minutes")),
            )
            if any(age is not None and age > max_age_minutes for age in sensor_ages):
                exclusions["stale_solar_or_outdoor"] += 1
                continue
            solar_unit0 = str(first_channels.get("solar_radiation_unit") or "").strip().lower().replace(" ", "")
            solar_unit1 = str(second_channels.get("solar_radiation_unit") or "").strip().lower().replace(" ", "")
            solar_scales = {"w/m2": 1.0, "w/m²": 1.0, "kw/m2": 1000.0, "kw/m²": 1000.0}
            if (solar_unit0 and solar_unit0 not in solar_scales) or (solar_unit1 and solar_unit1 not in solar_scales):
                exclusions["unsupported_solar_unit"] += 1
                continue
            if solar0 is not None and solar_unit0 in solar_scales:
                solar0 *= solar_scales[solar_unit0]
            if solar1 is not None and solar_unit1 in solar_scales:
                solar1 *= solar_scales[solar_unit1]
            if solar0 is None or solar1 is None or max(solar0, solar1) > solar_max_w_m2:
                exclusions["solar_missing_or_high"] += 1
                continue
            ac0 = numeric(first_channels.get("ac_power"))
            ac1 = numeric(second_channels.get("ac_power"))
            ac_ages = (numeric(first_channels.get("ac_power_age_minutes")), numeric(second_channels.get("ac_power_age_minutes")))
            if any(age is not None and age > max_age_minutes for age in ac_ages):
                exclusions["stale_ac_or_boiler_telemetry"] += 1
                continue
            if ((require_ac_power and (ac0 is None or ac1 is None))
                    or (ac0 is not None and ac0 > ac_power_max_w)
                    or (ac1 is not None and ac1 > ac_power_max_w)):
                exclusions["ac_heating_active_or_unavailable"] += 1
                continue
            burner0 = first_channels.get("boiler_burner")
            burner1 = second_channels.get("boiler_burner")
            boiler_ages = (
                numeric(first_channels.get("boiler_burner_age_minutes")),
                numeric(second_channels.get("boiler_burner_age_minutes")),
                numeric(first_channels.get("boiler_modulation_age_minutes")),
                numeric(second_channels.get("boiler_modulation_age_minutes")),
            )
            if any(age is not None and age > max_age_minutes for age in boiler_ages):
                exclusions["stale_ac_or_boiler_telemetry"] += 1
                continue
            if require_boiler_burner_off and not (state_off(burner0) and state_off(burner1)):
                exclusions["boiler_active_or_unavailable"] += 1
                continue
            modulation0 = numeric(first_channels.get("boiler_modulation"))
            modulation1 = numeric(second_channels.get("boiler_modulation"))
            if require_boiler_modulation_zero and (
                modulation0 is None or modulation1 is None
                or modulation0 > 0.1 or modulation1 > 0.1
            ):
                exclusions["boiler_active_or_unavailable"] += 1
                continue
            outside0 = numeric(first_channels.get("outdoor_temperature"))
            outside1 = numeric(second_channels.get("outdoor_temperature"))
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
                # The eigenvalue ratio is the squared 2-norm condition
                # number of the standardized design matrix.
                condition_number = (
                    math.sqrt(largest_eigenvalue / smallest_eigenvalue)
                    if smallest_eigenvalue > 1e-12 and largest_eigenvalue > 0
                    else float("inf")
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
                bootstrap_rejected_non_identifiable = 0

                def fit_bootstrap_rows(bootstrap_rows):
                    nonlocal bootstrap_rejected_non_identifiable
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
                        bootstrap_rejected_non_identifiable += 1
                        return None
                    local_design = [
                        [(row[1][j] - local_means[j]) / local_scales[j] for j in range(p)]
                        for row in bootstrap_rows
                    ]
                    local_gram = [
                        [sum(row[i] * row[j] for row in local_design) / count for j in range(p)]
                        for i in range(p)
                    ]
                    local_eigenvalues = _symmetric_matrix_eigenvalues(local_gram)
                    local_largest = max(local_eigenvalues) if local_eigenvalues else 0.0
                    local_positive = [
                        value for value in local_eigenvalues
                        if value > max(1e-10, local_largest * 1e-10)
                    ]
                    if len(local_positive) != p or local_largest <= 0:
                        bootstrap_rejected_non_identifiable += 1
                        return None
                    local_condition = math.sqrt(local_largest / min(local_positive))
                    if local_condition > max_predictor_condition_number:
                        bootstrap_rejected_non_identifiable += 1
                        return None
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
                result["bootstrap_rejected_non_identifiable_replicates"] = bootstrap_rejected_non_identifiable
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
                result["holdout_residual_diagnostics"] = _multizone_holdout_diagnostics(
                    holdout, physical, intercept, interval_minutes,
                )
                result["physical_parameter_identification_status"] = (
                    "withheld_independent_capacity_and_room_heat_input"
                )
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
        "rooms_better_than_persistence": sum(
            1 for item in validated_rooms if item.get("status") == "holdout_better_than_persistence"
        ),
        "rooms_not_better_than_persistence": sum(
            1 for item in validated_rooms if item.get("status") == "holdout_not_better_than_persistence"
        ),
        "holdout_residual_diagnostics_available_rooms": sum(
            1 for item in validated_rooms
            if item.get("holdout_residual_diagnostics", {}).get("status") == "holdout_residuals_available"
        ),
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
        "identifiability_method": "pairwise_correlation_and_full_standardized_training_matrix_condition_number",
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


def build_multizone_physical_calibration_readiness(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    response_identification: dict[str, Any] | None = None,
    energy_observability: dict[str, Any] | None = None,
    ufh_observability: dict[str, Any] | None = None,
    water_side_output: dict[str, Any] | None = None,
    aggregate_capacity_calibration: dict[str, Any] | None = None,
    floor_thermal_output: dict[str, Any] | None = None,
    floor_room_allocation_inference: dict[str, Any] | None = None,
    floor_room_demand_lag_analysis: dict[str, Any] | None = None,
    solar_room_response_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarise which absolute multi-zone parameters are evidenced vs blocked.

    This deliberately separates useful normalized response fits and system-level
    energy meters from the missing room-level heat allocation and storage evidence.
    It never promotes pump electrical power or water delta-T to thermal output.
    """
    response_identification = response_identification or {}
    energy_observability = energy_observability or {}
    ufh_observability = ufh_observability or {}
    water_side_output = water_side_output or {}
    aggregate_capacity_calibration = aggregate_capacity_calibration or {}
    floor_thermal_output = floor_thermal_output or {}
    floor_room_allocation_inference = floor_room_allocation_inference or {}
    floor_room_demand_lag_analysis = floor_room_demand_lag_analysis or {}
    solar_room_response_analysis = solar_room_response_analysis or {}
    aggregate_capacity = _finite_number_or_none(
        aggregate_capacity_calibration.get("effective_building_thermal_capacity_wh_per_k")
    )
    aggregate_capacity_available = (
        aggregate_capacity_calibration.get("status") == "effective_aggregate_capacity_estimate"
        and aggregate_capacity is not None and aggregate_capacity > 0
    )
    water_output_available = (
        water_side_output.get("status") == "system_water_side_output_available"
        and int(water_side_output.get("valid_samples") or 0) > 0
    )
    room_ids = [str(room.get("id", "unknown")) for room in rooms]
    response_rooms = response_identification.get("rooms") or {}
    energy_meters = energy_observability.get("meters") or {}

    room_coverage = {}
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        valid_temperatures = 0
        valid_states = 0
        for sample in samples:
            observed = (sample.get("rooms") or {}).get(room_id, {})
            if _finite_number_or_none(observed.get("temperature")) is not None:
                valid_temperatures += 1
            if (
                _finite_number_or_none(observed.get("temperature")) is not None
                and _switch_state(observed.get("demand")) is not None
                and _switch_state(observed.get("window")) is not None
            ):
                valid_states += 1
        fit = response_rooms.get(room_id, {})
        coefficients = fit.get("normalized_rates_per_hour") or {}
        bootstrap = fit.get("coefficient_uncertainty") or {}
        temporal = fit.get("temporal_coefficient_stability") or {}
        room_coverage[room_id] = {
            "temperature_samples": valid_temperatures,
            "temperature_and_state_samples": valid_states,
            "volume_m3_configured": (
                _finite_number_or_none(room.get("volume_m3"))
                if (_finite_number_or_none(room.get("volume_m3")) or 0) > 0 else None
            ),
            "ach_configured": (
                _finite_number_or_none(room.get("air_changes_per_hour"))
                if (_finite_number_or_none(room.get("air_changes_per_hour")) or 0) > 0 else None
            ),
            "normalized_response_fit_status": fit.get("status", "not_available"),
            "normalized_rates_per_hour": coefficients or None,
            "holdout_mae_c": fit.get("holdout_mae_c"),
            "holdout_baseline_mae_c": fit.get("persistence_baseline_mae_c"),
            "coefficient_resampling_status": (
                "available" if any(
                    item.get("stability") in ("relatively_stable", "high_uncertainty")
                    for item in bootstrap.values()
                ) else "insufficient_or_unavailable"
            ),
            "temporal_stability_status": (
                "available" if any(
                    item.get("valid_windows", 0) >= 2 for item in temporal.values()
                ) else "insufficient_or_unavailable"
            ),
            "effective_room_capacity_wh_per_k": None,
            "room_heat_input_w": None,
            "absolute_conductance_w_per_k": None,
            "absolute_parameter_status": "withheld_missing_independent_capacity_and_room_heat_input",
        }

    # Allocate an independently estimated aggregate capacity only as
    # conservative, energy-conserving scenarios. Neither scenario is a
    # room-level identification or a calibrated RC parameter.
    room_volumes = {
        str(room.get("id", "unknown")): _finite_number_or_none(room.get("volume_m3"))
        for room in rooms
    }
    room_volumes = {
        room_id: volume for room_id, volume in room_volumes.items()
        if volume is not None and volume > 0
    }
    total_volume = sum(room_volumes.values())
    air_capacity_by_room = {
        room_id: 1.2 * 1005.0 / 3600.0 * volume
        for room_id, volume in room_volumes.items()
    }
    total_air_capacity = sum(air_capacity_by_room.values())
    capacity_scenarios = {
        "status": "withheld_aggregate_capacity_or_complete_room_volume_missing",
        "aggregate_capacity_wh_per_k": round(aggregate_capacity, 1) if aggregate_capacity_available else None,
        "configured_air_only_capacity_wh_per_k": round(total_air_capacity, 1) if total_volume > 0 else None,
        "rooms": {},
        "assumptions": [
            "The aggregate effective capacity estimate describes the same conditioned boundary as all configured rooms.",
            "Configured room volumes are complete and use consistent units.",
            "Non-air capacity is allocated either in proportion to room volume or equally per room.",
        ],
        "interpretation": (
            "These are aggregate-conserving allocation scenarios for sensitivity analysis only. "
            "They are not measurements, fitted room capacities, confidence intervals or permission "
            "to enable physical simulation."
        ),
    }
    if aggregate_capacity_available and total_volume > 0 and len(room_volumes) == len(rooms):
        excess_capacity = aggregate_capacity - total_air_capacity
        if excess_capacity >= 0:
            capacity_scenarios["status"] = "scenario_ranges_available_not_room_identification"
            capacity_scenarios["non_air_capacity_pool_wh_per_k"] = round(excess_capacity, 1)
            for room_id, volume in room_volumes.items():
                air_only = air_capacity_by_room[room_id]
                volume_weighted = air_only + excess_capacity * volume / total_volume
                equal_share = air_only + excess_capacity / len(room_volumes)
                capacity_scenarios["rooms"][room_id] = {
                    "air_only_capacity_wh_per_k": round(air_only, 2),
                    "volume_weighted_scenario_wh_per_k": round(volume_weighted, 2),
                    "equal_non_air_share_scenario_wh_per_k": round(equal_share, 2),
                    "scenario_min_wh_per_k": round(min(volume_weighted, equal_share), 2),
                    "scenario_max_wh_per_k": round(max(volume_weighted, equal_share), 2),
                    "identified_effective_capacity_wh_per_k": None,
                }
        else:
            capacity_scenarios["status"] = "inconsistent_aggregate_capacity_below_configured_air_only"
            capacity_scenarios["note"] = (
                "The aggregate effective capacity is below the calculated air-only capacity. "
                "Check room volumes, heat-loss calibration and conditioned-zone boundaries."
            )

    measured_energy_channels = [
        key for key, item in energy_meters.items()
        if item.get("measured_energy_kwh") is not None
    ]
    room_heat_output_channels = [
        key for key in ("room_heat_output_w", "zone_heat_output_w")
        if any(
            _finite_number_or_none((sample.get("channels") or {}).get(key)) is not None
            for sample in samples
        )
    ]
    water_flow_channels = [
        key for key in ("water_flow_rate", "water_flow_l_min", "water_flow_m3_h")
        if any(
            _finite_number_or_none((sample.get("channels") or {}).get(key)) is not None
            for sample in samples
        )
    ]
    measured_floor_output_ids = [
        floor_id for floor_id, item in (floor_thermal_output.get("floors") or {}).items()
        if int(item.get("valid_samples") or 0) > 0
    ]
    blockers = []
    if not measured_energy_channels:
        blockers.append("no_verified_system_energy_meter")
    if not water_flow_channels:
        blockers.append("no_water_flow_measurement_for_water_side_output")
    elif not water_output_available:
        blockers.append("water_side_output_unavailable_from_fresh_flow_and_delta_t")
    if not room_heat_output_channels:
        blockers.append("no_independent_room_or_zone_heat_output_measurement")
    blockers.append("no_independent_effective_room_capacity_measurement")
    blockers.append("no_validated_room_level_heat_allocation")
    if any(
        item.get("normalized_rates_per_hour") is None
        for item in room_coverage.values()
    ):
        blockers.append("one_or_more_rooms_lack_identifiable_normalized_response")
    return {
        "status": (
            "normalized_response_evidence_available_absolute_parameters_withheld"
            if any(item.get("normalized_rates_per_hour") for item in room_coverage.values())
            else "absolute_parameters_withheld_insufficient_response_evidence"
        ),
        "configured_rooms": len(room_ids),
        "rooms_with_normalized_response_fit": sum(
            1 for item in room_coverage.values()
            if item.get("normalized_rates_per_hour")
        ),
        "system_energy_meters_with_verified_deltas": measured_energy_channels,
        "water_flow_measurement_channels": water_flow_channels,
        "system_water_side_output": {
            "status": water_side_output.get("status", "not_available"),
            "valid_samples": int(water_side_output.get("valid_samples") or 0),
            "coverage_percent": water_side_output.get("coverage_percent"),
            "median_thermal_output_w": water_side_output.get("median_thermal_output_w"),
            "integrated_water_side_energy_kwh": water_side_output.get("integrated_water_side_energy_kwh"),
            "integrated_valid_intervals": int(water_side_output.get("integrated_valid_intervals") or 0),
            "scope": "system_water_side_only_not_room_delivery",
            "available": water_output_available,
        },
        "independent_room_heat_output_channels": room_heat_output_channels,
        "floor_room_heat_allocation_inference": {
            "status": floor_room_allocation_inference.get("status", "not_available"),
            "physical_room_allocation_identified": False,
            "floors_with_repeatable_demand_association": [
                floor_id for floor_id, item in (floor_room_allocation_inference.get("floors") or {}).items()
                if item.get("status") == "repeatable_demand_association_not_physical_room_allocation"
            ],
            "interpretation": "Demand-associated floor-output regression is observational evidence only; it does not measure room heat delivery.",
        },
        "floor_room_demand_lag_analysis": {
            "status": floor_room_demand_lag_analysis.get("status", "not_available"),
            "floors_with_repeatable_lag_evidence": [
                floor_id for floor_id, item in (floor_room_demand_lag_analysis.get("floors") or {}).items()
                if item.get("status") == "descriptive_room_demand_lag_evidence_available"
            ],
            "physical_room_allocation_identified": False,
            "interpretation": "Demand-to-floor-output lag scans are descriptive timing evidence, not measured room heat delivery or slab capacity.",
        },
        "solar_room_response_evidence": {
            "status": solar_room_response_analysis.get("status", "not_available"),
            "rooms_with_repeatable_association": list(
                solar_room_response_analysis.get("rooms_with_repeatable_association") or []
            ),
            "solar_gain_w_identified": False,
            "physical_room_parameters_identified": False,
            "interpretation": (
                "Solar/room-temperature associations are adjusted observational timing evidence; "
                "they do not identify transmitted solar watts, glazing gain coefficients or room RC parameters."
            ),
        },
        "measured_floor_thermal_output": {
            "status": floor_thermal_output.get("status", "not_available"),
            "floors_with_measured_output": measured_floor_output_ids,
            "independent_floor_totals_available": len(measured_floor_output_ids) == 2,
            "room_level_delivery_identified": False,
            "scope": "floor_total_not_room_level_delivery",
        },
        "aggregate_capacity_evidence": {
            "status": aggregate_capacity_calibration.get("status", "not_available"),
            "effective_building_thermal_capacity_wh_per_k": round(aggregate_capacity, 1) if aggregate_capacity_available else None,
            "room_allocation_status": capacity_scenarios["status"],
        },
        "room_capacity_allocation_scenarios": capacity_scenarios,
        "ufh_observability_status": ufh_observability.get("status", "not_available"),
        "absolute_room_parameters_identified": False,
        "physical_simulation_ready": False,
        "blockers": blockers,
        "rooms": room_coverage,
        "next_required_evidence": [
            *([] if water_output_available else [
                "Calibrated water flow rate paired with fresh supply and return temperatures, or another independently measured delivered-heat source",
            ]),
            "A defensible method to allocate each floor's measured heat among individual rooms",
            "Independent effective room thermal capacities, including UFH slab storage where applicable",
            "Held-out validation against measured thermal output, not only room-temperature persistence",
        ],
        "interpretation": (
            "Normalized G/C coefficients and whole-system gas/AC energy are useful directional evidence, "
            "but they do not identify absolute W/K conductances or Wh/K capacities. Pump electrical "
            "consumption and supply-return delta-T without flow are not thermal output. Absolute parameters "
            "and physical simulation remain withheld."
        ),
    }

