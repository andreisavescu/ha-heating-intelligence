"""Physical-model validation, forecasting and isolated what-if scenarios."""
from __future__ import annotations
import math
from datetime import datetime
from typing import Any
from .multizone_thermal_model import simulate_thermal_step

FIXED_ROOM_TEMPERATURES_C = {"baie_matr": 22.0, "baie_parter": 20.5}

def _number(value: Any):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None

def _parameters(rooms, absolute_parameters):
    result = {}
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        fit = (absolute_parameters.get("rooms") or {}).get(room_id) or {}
        capacity = _number(fit.get("effective_room_capacity_wh_per_k"))
        envelope = _number(room.get("envelope_conductance_w_per_k"))
        if capacity is not None and capacity > 0 and envelope is not None and envelope > 0:
            result[room_id] = {"capacity": capacity, "envelope": envelope}
    return result

def _step(initial, outdoor, capacity, envelope, seconds, gains_w=0.0):
    return simulate_thermal_step(
        {"room": {"temperature_c": initial, "capacity_wh_per_k": capacity, "gains_w": gains_w},
         "outdoors": {"fixed_temperature_c": outdoor}},
        [{"from": "room", "to": "outdoors", "conductance_w_per_k": envelope}],
        seconds,
    )["temperatures_c"]["room"]

def build_physical_model_validation(samples, rooms, absolute_parameters, maximum_interval_minutes=60.0):
    """Chronological one-step validation using measured room temperatures only."""
    parameters = _parameters(rooms, absolute_parameters)
    results = {}
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        errors = []
        parameter = parameters.get(room_id)
        prior = None
        if parameter:
            for sample in samples:
                state = (sample.get("rooms") or {}).get(room_id) or {}
                temp = _number(state.get("temperature"))
                outside = _number((sample.get("channels") or {}).get("outdoor_temperature"))
                stamp = sample.get("timestamp")
                if temp is None or outside is None or not stamp:
                    prior = None
                    continue
                if prior:
                    try:
                        seconds = (datetime.fromisoformat(stamp) - datetime.fromisoformat(prior[0])).total_seconds()
                    except (TypeError, ValueError):
                        seconds = 0
                    if 0 < seconds <= maximum_interval_minutes * 60:
                        predicted = _step(prior[1], (prior[2] + outside) / 2,
                                          parameter["capacity"], parameter["envelope"], seconds)
                        errors.append(predicted - temp)
                prior = (stamp, temp, outside)
        if errors:
            results[room_id] = {
                "status": "scored_envelope_only_one_step_validation",
                "valid_intervals": len(errors),
                "mae_c": round(sum(abs(e) for e in errors) / len(errors), 4),
                "rmse_c": round(math.sqrt(sum(e * e for e in errors) / len(errors)), 4),
                "mean_bias_c": round(sum(errors) / len(errors), 4),
            }
        else:
            results[room_id] = {
                "status": "withheld_missing_accepted_absolute_parameters_or_history",
                "valid_intervals": 0, "mae_c": None, "rmse_c": None, "mean_bias_c": None,
            }
    scored = sum(bool(v["valid_intervals"]) for v in results.values())
    return {
        "status": "validation_scores_available" if scored else "awaiting_parameters_and_history",
        "method": "chronological_one_step_rc_envelope_only",
        "rooms_scored": scored, "rooms_configured": len(rooms), "rooms": results,
        "limitations": [
            "Measured starting temperatures only; bathroom fixed values are never validation targets.",
            "Envelope-only: no room coupling, attic, solar, internal gains or UFH slab paths.",
            "One-step scores are not free-running forecast validation.",
        ],
    }

def _initial(room_id, current_rooms):
    value = _number((current_rooms.get(room_id) or {}).get("temperature"))
    if value is not None:
        return value, "live_room_sensor"
    if room_id in FIXED_ROOM_TEMPERATURES_C:
        return FIXED_ROOM_TEMPERATURES_C[room_id], "fixed_scenario_assumption"
    return None, "unavailable"

def build_predictive_forecast(current_rooms, rooms, absolute_parameters, outdoor_temperature_c,
                              horizons_hours=(1, 3, 6, 12, 24)):
    outside = _number(outdoor_temperature_c)
    parameters = _parameters(rooms, absolute_parameters)
    forecasts = {}
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        initial, source = _initial(room_id, current_rooms)
        parameter = parameters.get(room_id)
        points = []
        status = "forecast_available_experimental"
        if initial is None or outside is None or parameter is None:
            status = "withheld_missing_temperature_weather_or_accepted_parameters"
        else:
            for horizon in horizons_hours:
                points.append({"hours": float(horizon), "temperature_c": round(_step(
                    initial, outside, parameter["capacity"], parameter["envelope"],
                    float(horizon) * 3600), 3)})
        forecasts[room_id] = {"status": status, "initial_temperature_c": initial,
                              "initial_temperature_source": source, "forecast": points}
    available = sum(v["status"] == "forecast_available_experimental" for v in forecasts.values())
    return {
        "status": "experimental_forecasts_available" if available else "awaiting_accepted_parameters_or_live_inputs",
        "method": "single_zone_envelope_only_free_coasting_constant_outdoor",
        "outdoor_temperature_c": outside,
        "outdoor_boundary_assumption": "held_constant_for_entire_forecast",
        "rooms_forecast": available, "rooms": forecasts,
        "limitations": ["No future weather, solar, occupancy or heating schedule.",
                        "No room coupling, attic path or UFH slab dynamics.",
                        "Bathroom values are scenario initial conditions, not sensor observations."],
    }

def build_what_if_simulator(current_rooms, rooms, absolute_parameters, outdoor_temperature_c):
    outside = _number(outdoor_temperature_c)
    parameters = _parameters(rooms, absolute_parameters)
    scenarios = {
        "baseline": {"outdoor_delta_c": 0.0, "additional_gains_w": 0.0},
        "colder_outdoor_5c": {"outdoor_delta_c": -5.0, "additional_gains_w": 0.0},
        "warmer_outdoor_5c": {"outdoor_delta_c": 5.0, "additional_gains_w": 0.0},
        "additional_internal_gain_200w": {"outdoor_delta_c": 0.0, "additional_gains_w": 200.0},
    }
    results = {}
    for scenario_id, scenario in scenarios.items():
        room_results = {}
        for room in rooms:
            room_id = str(room.get("id", "unknown"))
            initial, source = _initial(room_id, current_rooms)
            parameter = parameters.get(room_id)
            prediction = None
            status = "withheld_missing_temperature_weather_or_accepted_parameters"
            if initial is not None and outside is not None and parameter:
                prediction = round(_step(initial, outside + scenario["outdoor_delta_c"],
                    parameter["capacity"], parameter["envelope"], 21600,
                    scenario["additional_gains_w"]), 3)
                status = "experimental_scenario_available"
            room_results[room_id] = {"status": status, "temperature_after_6h_c": prediction,
                                     "initial_temperature_source": source}
        results[scenario_id] = {"inputs": scenario,
            "outdoor_temperature_c": outside + scenario["outdoor_delta_c"] if outside is not None else None,
            "rooms": room_results}
    available = any(v["temperature_after_6h_c"] is not None
                    for scenario in results.values() for v in scenario["rooms"].values())
    return {
        "status": "experimental_scenarios_available" if available else "awaiting_accepted_parameters_or_live_inputs",
        "horizon_hours": 6, "scenarios": results,
        "isolation": "read_only_no_home_assistant_state_or_heating_control_changes",
        "limitations": ["Envelope-only experimental scenarios, not a calibrated whole-house simulator.",
                        "The additional 200 W gain is applied independently to each simulated room.",
                        "No inter-room, attic, solar or UFH slab dynamics.",
                        "Bathroom fixed values are scenario assumptions only."],
    }


def build_simulation_audit(rooms, current_rooms, absolute_parameters, physical_validation,
                           predictive_forecast, identification_dataset, response_identification):
    """Summarize per-room simulation readiness without confusing scenarios with validation."""
    parameter_rooms = (absolute_parameters or {}).get("rooms") or {}
    validation_rooms = (physical_validation or {}).get("rooms") or {}
    forecast_rooms = (predictive_forecast or {}).get("rooms") or {}
    response_rooms = (response_identification or {}).get("rooms") or {}
    audited = {}
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        fit = parameter_rooms.get(room_id) or {}
        validation = validation_rooms.get(room_id) or {}
        forecast = forecast_rooms.get(room_id) or {}
        response = response_rooms.get(room_id) or {}
        live_temperature = _number((current_rooms.get(room_id) or {}).get("temperature"))
        has_envelope = (_number(room.get("envelope_conductance_w_per_k")) or 0) > 0
        capacity = _number(fit.get("effective_room_capacity_wh_per_k"))
        accepted_capacity = capacity is not None and capacity > 0
        forecast_available = forecast.get("status") == "forecast_available_experimental"
        valid_intervals = int(validation.get("valid_intervals") or 0)
        blockers = []
        if not has_envelope:
            blockers.append("missing_room_envelope_conductance")
        if not accepted_capacity:
            blockers.append("missing_accepted_effective_room_capacity")
        if live_temperature is None:
            blockers.append("missing_live_room_temperature")
        if _number((predictive_forecast or {}).get("outdoor_temperature_c")) is None:
            blockers.append("missing_live_outdoor_temperature")
        if not forecast_available and not blockers:
            blockers.append("forecast_not_generated")
        if valid_intervals == 0:
            blockers.append("no_chronological_validation_intervals")
        if valid_intervals > 0:
            status = "validated_one_step"
        elif forecast_available:
            status = "experimental_simulation_available"
        elif accepted_capacity and has_envelope:
            status = "awaiting_live_inputs"
        else:
            status = "awaiting_parameters"
        audited[room_id] = {
            "status": status,
            "live_temperature_c": live_temperature,
            "temperature_source": "live_room_sensor" if live_temperature is not None else
                ("fixed_scenario_assumption_only" if room_id in FIXED_ROOM_TEMPERATURES_C else "unavailable"),
            "envelope_conductance_configured": has_envelope,
            "effective_capacity_wh_per_k": capacity,
            "effective_capacity_source": fit.get("effective_room_capacity_source"),
            "response_identification_status": response.get("status"),
            "forecast_status": forecast.get("status"),
            "validation_intervals": valid_intervals,
            "mae_c": validation.get("mae_c"),
            "rmse_c": validation.get("rmse_c"),
            "blockers": blockers,
        }
    simulated = sum(item["status"] == "experimental_simulation_available" for item in audited.values())
    validated = sum(item["status"] == "validated_one_step" for item in audited.values())
    return {
        "status": "simulation_activity_available" if simulated or validated else "awaiting_parameters_or_live_inputs",
        "rooms_configured": len(audited),
        "rooms_simulated": simulated,
        "rooms_validated": validated,
        "rooms_awaiting_parameters": sum(item["status"] == "awaiting_parameters" for item in audited.values()),
        "rooms_awaiting_live_inputs": sum(item["status"] == "awaiting_live_inputs" for item in audited.values()),
        "dataset_status": (identification_dataset or {}).get("status"),
        "dataset_samples": (identification_dataset or {}).get("aligned_sample_count", 0),
        "core_eligible_samples": (identification_dataset or {}).get("core_eligible_sample_count", 0),
        "complete_room_state_samples": (identification_dataset or {}).get("complete_room_state_sample_count", 0),
        "method": "per_room_readiness_plus_experimental_envelope_forecast_and_chronological_validation",
        "rooms": audited,
        "interpretation": "Experimental simulation availability is not calibration or validation; bathroom fixed values are scenario assumptions only.",
    }
