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
        envelope = _number(fit.get("envelope_anchor_w_per_k"))
        if envelope is None:
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
        "horizon_hours": 6, "model_scope": "single_room_envelope_only",
        "baseline_comparable_to_coupled_forecast": False,
        "scenarios": results,
        "isolation": "read_only_no_home_assistant_state_or_heating_control_changes",
        "limitations": ["Envelope-only experimental scenarios, not a calibrated whole-house simulator.",
                        "The additional 200 W gain is applied independently to each simulated room.",
                        "No inter-room, attic, solar or UFH slab dynamics.",
                        "Bathroom fixed values are scenario assumptions only."],
    }


def build_simulation_audit(rooms, current_rooms, absolute_parameters, physical_validation,
                           predictive_forecast, identification_dataset, response_identification):
    """Report five independent model lifecycle states for every configured room."""
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
        has_provisional_parameters = capacity is not None and capacity > 0
        forecast_status = str(forecast.get("status") or "not_available")
        forecast_points = forecast.get("forecast") or []
        experimental_forecast_available = (
            "forecast_available_experimental" in forecast_status
            or ("experimental" in forecast_status and bool(forecast_points))
        )
        live_simulation_executed = bool(forecast_points and forecast.get("initial_temperature_source") == "live_room_sensor")
        valid_intervals = int(validation.get("valid_intervals") or 0)
        one_step_evaluated = valid_intervals > 0
        physical_model_validated = (
            validation.get("physical_model_validation_status") == "validated"
            or validation.get("physical_model_validated") is True
        )
        parameter_state = (
            "validated" if fit.get("parameter_state") == "validated"
            else "provisional" if has_provisional_parameters else "withheld"
        )
        blockers = []
        if not has_envelope: blockers.append("missing_room_envelope_conductance")
        if not has_provisional_parameters: blockers.append("missing_provisional_effective_room_capacity")
        if live_temperature is None: blockers.append("missing_live_room_temperature")
        if _number((predictive_forecast or {}).get("outdoor_temperature_c")) is None:
            blockers.append("missing_live_outdoor_temperature")
        if not experimental_forecast_available and not any(x.startswith("missing_") for x in blockers):
            blockers.append("experimental_forecast_not_available")
        if not one_step_evaluated: blockers.append("one_step_prediction_not_evaluated")
        if not physical_model_validated: blockers.append("physical_model_not_validated")
        audited[room_id] = {
            "status": ("physical_model_validated" if physical_model_validated
                       else "experimental_forecast_available" if experimental_forecast_available
                       else "awaiting_parameters" if not has_provisional_parameters or not has_envelope
                       else "awaiting_live_inputs"),
            "one_step_prediction_evaluation_status": "evaluated" if one_step_evaluated else "not_evaluated",
            "one_step_prediction_evaluation_method": physical_validation.get("method"),
            "parameter_identification_status": fit.get("status", "not_identified"),
            "parameter_state": parameter_state,
            "experimental_forecast_status": "available" if experimental_forecast_available else forecast_status,
            "physical_model_validation_status": "validated" if physical_model_validated else "not_validated",
            "live_simulation_execution_status": "executed" if live_simulation_executed else "not_executed",
            "live_simulation_horizons": [point.get("hours") for point in forecast_points],
            "live_temperature_c": live_temperature,
            "temperature_source": "live_room_sensor" if live_temperature is not None else ("fixed_scenario_assumption_only" if room_id in FIXED_ROOM_TEMPERATURES_C else "unavailable"),
            "envelope_conductance_configured": has_envelope,
            "effective_capacity_wh_per_k": capacity,
            "effective_capacity_source": fit.get("effective_room_capacity_source"),
            "response_identification_status": response.get("status"),
            "forecast_status": forecast_status,
            "validation_intervals": valid_intervals,
            "mae_c": validation.get("mae_c"),
            "rmse_c": validation.get("rmse_c"),
            "persistence_mae_c": validation.get("persistence_mae_c"),
            "beats_persistence": validation.get("beats_persistence"),
            "blockers": blockers,
        }
    simulated = sum(item["live_simulation_execution_status"] == "executed" for item in audited.values())
    evaluated = sum(item["one_step_prediction_evaluation_status"] == "evaluated" for item in audited.values())
    forecasts = sum(item["experimental_forecast_status"] == "available" for item in audited.values())
    provisional = sum(item["parameter_state"] == "provisional" for item in audited.values())
    validated = sum(item["physical_model_validation_status"] == "validated" for item in audited.values())
    return {
        "status": "model_activity_available" if simulated or evaluated or forecasts else "awaiting_parameters_or_live_inputs",
        "rooms_configured": len(audited), "rooms_simulated": simulated,
        "rooms_live_simulation_executed": simulated, "rooms_with_one_step_evaluation": evaluated,
        "rooms_with_experimental_forecast": forecasts, "rooms_with_provisional_parameters": provisional,
        "rooms_with_validated_physical_model": validated, "rooms_validated": validated,
        "rooms_awaiting_parameters": sum(item["status"] == "awaiting_parameters" for item in audited.values()),
        "rooms_awaiting_live_inputs": sum(item["status"] == "awaiting_live_inputs" for item in audited.values()),
        "dataset_status": (identification_dataset or {}).get("status"),
        "dataset_samples": ((identification_dataset or {}).get("event_sample_count") or (identification_dataset or {}).get("aligned_sample_count", 0)),
        "core_eligible_samples": (identification_dataset or {}).get("core_eligible_sample_count", 0),
        "complete_room_state_samples": (identification_dataset or {}).get("complete_room_state_sample_count", 0),
        "method": "independent_room_statuses_for_prediction_evaluation_forecast_parameters_validation_and_execution",
        "rooms": audited,
        "interpretation": "One-step evaluation, experimental forecast availability, provisional parameter identification, validated physical model and live simulation execution are separate states. A one-step score or experimental forecast never promotes parameters or validates the physical model.",
    }

def build_coupled_network_forecast(current_rooms, rooms, absolute_parameters,
                                   outdoor_temperature_c, multizone_model,
                                   room_heat_inputs_w=None,
                                   horizons_hours=(1, 3, 6, 12, 24)):
    """Run a coupled room-air RC forecast using configured network paths.

    Only rooms with a live temperature and accepted effective capacity are
    dynamic states. Conductances come from the house assessment/envelope
    inventory; room heat inputs are the calibrated modulation allocation
    supplied by the integration. Missing paths and latent slab parameters are
    reported rather than silently invented.
    """
    from .multizone_thermal_model import simulate_thermal_step

    outside = _number(outdoor_temperature_c)
    model = multizone_model if isinstance(multizone_model, dict) else {}
    source_nodes = model.get("nodes") or {}
    connections = model.get("configured_connections") or []
    room_heat_inputs_w = room_heat_inputs_w or {}
    params = _parameters(rooms, absolute_parameters)
    dynamic = {}
    room_ids = []
    excluded = {}
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        temp = _number((current_rooms.get(room_id) or {}).get("temperature"))
        parameter = params.get(room_id)
        if temp is None:
            excluded[room_id] = "missing_live_room_temperature"
            continue
        if parameter is None:
            excluded[room_id] = "missing_accepted_effective_capacity"
            continue
        dynamic["room:" + room_id] = {
            "temperature_c": temp,
            "capacity_wh_per_k": parameter["capacity"],
            "gains_w": max(0.0, _number(room_heat_inputs_w.get(room_id)) or 0.0),
        }
        room_ids.append(room_id)

    if outside is None:
        return {"status": "withheld_missing_outdoor_temperature",
                "method": "coupled_multi_zone_rc_with_calibrated_room_heat_inputs",
                "rooms": {}, "rooms_simulated": 0, "excluded_rooms": excluded}
    if not dynamic:
        return {"status": "withheld_no_rooms_with_live_temperature_and_accepted_capacity",
                "method": "coupled_multi_zone_rc_with_calibrated_room_heat_inputs",
                "rooms": {}, "rooms_simulated": 0, "excluded_rooms": excluded}

    nodes = dict(dynamic)
    nodes["boundary:outdoors"] = {"fixed_temperature_c": outside}
    usable_paths = []
    skipped_paths = []
    for path in connections:
        source, target = path.get("from"), path.get("to")
        conductance = _number(path.get("conductance_w_per_k"))
        if conductance is None or conductance <= 0:
            continue
        # The present live network has outdoor and inter-room air paths.
        # Attic/ground paths are only used when their boundary temperature is
        # actually observed; otherwise they are explicitly skipped.
        if source in dynamic and target in dynamic:
            usable_paths.append({**path, "from": source, "to": target,
                                 "conductance_w_per_k": conductance})
        elif source in dynamic and target == "boundary:outdoors":
            usable_paths.append({**path, "from": source, "to": target,
                                 "conductance_w_per_k": conductance})
        elif target in dynamic and source == "boundary:outdoors":
            usable_paths.append({**path, "from": source, "to": target,
                                 "conductance_w_per_k": conductance})
        else:
            skipped_paths.append({"path": path.get("path"), "from": source,
                                  "to": target, "reason": "boundary_or_node_not_observed"})

    # Ventilation is a separate configured heat-loss path and is included
    # alongside opaque/window envelope conductance.
    for path in model.get("ventilation_paths") or []:
        room_node = path.get("from")
        g = _number(path.get("conductance_w_per_k"))
        if room_node in dynamic and g is not None and g > 0:
            usable_paths.append({"from": room_node, "to": "boundary:outdoors",
                                 "conductance_w_per_k": g, "path": "ventilation"})

    # Avoid duplicate links if a topology producer already included ventilation.
    unique = {}
    for link in usable_paths:
        key = (link["from"], link["to"], round(link["conductance_w_per_k"], 6), link.get("path"))
        unique[key] = link
    usable_paths = list(unique.values())

    room_points = {room_id: [] for room_id in room_ids}
    energy_checks = []
    for horizon in horizons_hours:
        trial_nodes = {key: dict(value) for key, value in nodes.items()}
        result = simulate_thermal_step(trial_nodes, usable_paths,
                                       float(horizon) * 3600.0)
        for room_id in room_ids:
            room_points[room_id].append({
                "hours": float(horizon),
                "temperature_c": result["temperatures_c"]["room:" + room_id],
            })
        energy_checks.append({"hours": float(horizon),
                              "status": result["energy_balance"]["status"],
                              "error_wh": result["energy_balance"]["error_wh"]})

    output = {}
    for room_id in room_ids:
        output[room_id] = {
            "status": "coupled_forecast_available_experimental",
            "initial_temperature_c": nodes["room:" + room_id]["temperature_c"],
            "initial_temperature_source": "live_room_sensor",
            "heat_input_w": nodes["room:" + room_id]["gains_w"],
            "forecast": room_points[room_id],
        }
    for room_id, reason in excluded.items():
        output[room_id] = {"status": "withheld_" + reason, "forecast": []}
    return {
        "status": "coupled_experimental_forecasts_available",
        "method": "coupled_multi_zone_rc_with_calibrated_room_heat_inputs",
        "rooms_simulated": len(room_ids),
        "rooms_configured": len(rooms),
        "rooms": output,
        "network_paths_used": len(usable_paths),
        "network_paths_skipped": skipped_paths,
        "energy_balance_checks": energy_checks,
        "outdoor_temperature_c": outside,
        "boundary_assumption": "outdoor temperature held constant; future weather and gains are not forecast",
        "heating_input_assumption": "current calibrated modulation room allocation held constant over each forecast horizon",
        "limitations": [
            "Only rooms with live temperature and accepted effective capacity are simulated.",
            "Inter-room, envelope and configured ventilation conductances are included when both endpoints are available.",
            "UFH slab storage is not yet a dynamic state: slab-to-room conductance and slab temperature are not identified, so allocated heat is applied as a room-side input proxy.",
            "Solar and occupancy gains are not converted to watts without calibrated gain parameters.",
            "This is an experimental coupled forecast, not a validated free-running prediction.",
        ],
    }


def build_coupled_network_validation(samples, rooms, absolute_parameters, multizone_model,
                                     maximum_interval_minutes=120.0):
    """Chronologically validate all observable rooms in one coupled RC step.

    Each interval starts from the measured room state at t0 and predicts t1.
    Only measured room temperatures are scored; missing sensors are reported
    as unobserved, never filled with scenario constants.
    """
    from datetime import datetime
    from .multizone_thermal_model import simulate_thermal_step

    parameters = _parameters(rooms, absolute_parameters)
    model = multizone_model if isinstance(multizone_model, dict) else {}
    connections = model.get("configured_connections") or []
    results = {}
    errors = {str(room.get("id")): [] for room in rooms}
    eligible = {str(room.get("id")): 0 for room in rooms}
    rejection = {str(room.get("id")): {} for room in rooms}
    for previous, current in zip(samples or [], (samples or [])[1:]):
        try:
            t0 = datetime.fromisoformat(previous.get("timestamp"))
            t1 = datetime.fromisoformat(current.get("timestamp"))
            seconds = (t1 - t0).total_seconds()
        except (TypeError, ValueError):
            continue
        if seconds <= 0 or seconds > maximum_interval_minutes * 60:
            continue
        outside0 = _number((previous.get("channels") or {}).get("outdoor_temperature"))
        outside1 = _number((current.get("channels") or {}).get("outdoor_temperature"))
        if outside0 is None or outside1 is None:
            for room in rooms:
                rejection[str(room.get("id"))]["missing_outdoor_temperature"] = (
                    rejection[str(room.get("id"))].get("missing_outdoor_temperature", 0) + 1)
            continue
        dynamic = {}
        target_values = {}
        for room in rooms:
            rid = str(room.get("id"))
            p = parameters.get(rid)
            a = _number(((previous.get("rooms") or {}).get(rid) or {}).get("temperature"))
            b = _number(((current.get("rooms") or {}).get(rid) or {}).get("temperature"))
            if p is None or a is None or b is None:
                reason = "missing_accepted_capacity" if p is None else "room_temperature_unobserved"
                rejection[rid][reason] = rejection[rid].get(reason, 0) + 1
                continue
            dynamic["room:" + rid] = {"temperature_c": a,
                                       "capacity_wh_per_k": p["capacity"]}
            target_values[rid] = b
        if not dynamic:
            continue
        nodes = dict(dynamic)
        nodes["boundary:outdoors"] = {"fixed_temperature_c": (outside0 + outside1) / 2.0}
        links = []
        for link in connections:
            source, target = link.get("from"), link.get("to")
            g = _number(link.get("conductance_w_per_k"))
            if g is not None and g > 0 and source in dynamic and target in dynamic:
                links.append({"from": source, "to": target, "conductance_w_per_k": g})
            elif g is not None and g > 0 and source in dynamic and target == "boundary:outdoors":
                links.append({"from": source, "to": target, "conductance_w_per_k": g})
            elif g is not None and g > 0 and target in dynamic and source == "boundary:outdoors":
                links.append({"from": source, "to": target, "conductance_w_per_k": g})
        for link in model.get("ventilation_paths") or []:
            rid = link.get("from")
            g = _number(link.get("conductance_w_per_k"))
            if rid in dynamic and g is not None and g > 0:
                links.append({"from": rid, "to": "boundary:outdoors",
                              "conductance_w_per_k": g})
        try:
            predicted = simulate_thermal_step(nodes, links, seconds)["temperatures_c"]
        except (ValueError, KeyError):
            continue
        for rid, actual in target_values.items():
            errors[rid].append(predicted["room:" + rid] - actual)
            eligible[rid] += 1

    for room in rooms:
        rid = str(room.get("id"))
        values = errors[rid]
        if values:
            results[rid] = {
                "status": "scored_coupled_multizone_one_step",
                "valid_intervals": len(values),
                "mae_c": round(sum(abs(x) for x in values) / len(values), 4),
                "rmse_c": round(math.sqrt(sum(x*x for x in values) / len(values)), 4),
                "mean_bias_c": round(sum(values) / len(values), 4),
                "rejected_intervals": rejection[rid],
            }
        else:
            results[rid] = {
                "status": "withheld_no_eligible_coupled_intervals",
                "valid_intervals": 0, "mae_c": None, "rmse_c": None,
                "mean_bias_c": None, "rejected_intervals": rejection[rid],
            }
    scored = sum(bool(item["valid_intervals"]) for item in results.values())
    return {
        "status": "coupled_validation_scores_available" if scored else "awaiting_coupled_history_or_parameters",
        "method": "chronological_coupled_multizone_rc_one_step_measured_room_targets",
        "rooms_scored": scored, "rooms_configured": len(rooms), "rooms": results,
        "limitations": [
            "Measured starting and target room temperatures only; uninstrumented bathrooms are not imputed.",
            "The current validation is one-step and initializes from measured states at each interval; it is not free-running holdout validation.",
            "Heating, solar and occupancy gains are not applied unless separately measured and mapped into the Recorder event dataset.",
            "Slab storage remains excluded until slab temperature and slab-to-room conductance are identifiable.",
        ],
    }


def select_predictive_forecast(coupled_forecast, fallback_forecast):
    """Select the coupled forecast without creating a self-referential attribute tree.

    Keep the attempted coupled result alongside the legacy fallback only when
    the coupled network cannot produce a forecast. When it succeeds, it is
    already the published forecast and must not be attached to itself.
    """
    if coupled_forecast.get("rooms_simulated", 0) > 0:
        return coupled_forecast
    return {**fallback_forecast, "coupled_network_attempt": coupled_forecast}
