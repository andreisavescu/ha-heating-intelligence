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
    return {
        "status": status,
        "method": "area_weighted_night_cooling_tau_times_independent_house_heat_loss",
        "effective_building_thermal_capacity_wh_per_k": round(capacity, 1) if capacity is not None else None,
        "heat_loss_coefficient_w_k": round(heat_loss, 2),
        "apparent_time_constant_hours": round(tau, 2) if tau is not None else None,
        "decay_slope_per_hour": round(slope, 6) if slope is not None else None,
        "intercept_c_per_hour": round(intercept, 6) if intercept is not None else None,
        "fit_r_squared": round(r_squared, 3) if r_squared is not None else None,
        "valid_intervals": n,
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
            if stamp is not None:
                # Preserve explicit unknown/unavailable transitions so an older
                # valid value cannot be carried through an invalid sensor state.
                points.append((stamp, raw))
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
                point_stamp, raw = points[index]
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

    return {
        "status": "room_response_identification_available" if any(
            item.get("normalized_rates_per_hour") is not None for item in room_results.values()
        ) else "insufficient_or_non_identifiable_history",
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
