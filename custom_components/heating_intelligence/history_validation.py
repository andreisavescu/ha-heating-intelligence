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
