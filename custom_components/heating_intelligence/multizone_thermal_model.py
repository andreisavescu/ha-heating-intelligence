"""Physics-based multi-zone thermal network primitives.

No Home Assistant dependencies: sensor mapping, parameter learning and
persistence belong in the integration layer.
"""
from __future__ import annotations

import math
from typing import Any


def _finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"{name} must be numeric") from err
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def simulate_thermal_step(
    nodes: dict[str, dict[str, Any]],
    connections: list[dict[str, Any]],
    duration_seconds: float,
) -> dict[str, Any]:
    """Advance a coupled thermal network and report directional heat flows.

    Dynamic nodes require temperature_c and capacity_wh_per_k and may specify
    gains_w (positive into the node). Boundary nodes instead set
    fixed_temperature_c and have no thermal capacity.

    Each connection contains from, to and conductance_w_per_k. Heat flows
    from the warmer node to the cooler node. Bounded explicit substeps keep
    the integration stable for stiff networks. Missing physical parameters
    are never inferred.
    """
    duration = _finite_number(duration_seconds, "duration_seconds")
    if duration <= 0:
        raise ValueError("duration_seconds must be greater than zero")
    if not nodes:
        raise ValueError("at least one thermal node is required")

    temperatures: dict[str, float] = {}
    capacities: dict[str, float] = {}
    gains: dict[str, float] = {}
    fixed: set[str] = set()

    for node_id, config in nodes.items():
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node IDs must be non-empty strings")
        if "fixed_temperature_c" in config:
            temperatures[node_id] = _finite_number(
                config["fixed_temperature_c"], f"{node_id}.fixed_temperature_c"
            )
            fixed.add(node_id)
            gains[node_id] = 0.0
        else:
            temperatures[node_id] = _finite_number(
                config.get("temperature_c"), f"{node_id}.temperature_c"
            )
            capacity = _finite_number(
                config.get("capacity_wh_per_k"), f"{node_id}.capacity_wh_per_k"
            )
            if capacity <= 0:
                raise ValueError(f"{node_id}.capacity_wh_per_k must be positive")
            capacities[node_id] = capacity
            gains[node_id] = _finite_number(
                config.get("gains_w", 0.0), f"{node_id}.gains_w"
            )

    links: list[tuple[str, str, float]] = []
    conductance_sum = {node_id: 0.0 for node_id in nodes}
    for index, connection in enumerate(connections):
        source, target = connection.get("from"), connection.get("to")
        if source not in nodes or target not in nodes or source == target:
            raise ValueError(f"connection {index} references an invalid node")
        conductance = _finite_number(
            connection.get("conductance_w_per_k"),
            f"connection {index}.conductance_w_per_k",
        )
        if conductance <= 0:
            raise ValueError(f"connection {index} conductance must be positive")
        links.append((source, target, conductance))
        if source not in fixed:
            conductance_sum[source] += conductance
        if target not in fixed:
            conductance_sum[target] += conductance

    # Quarter of the smallest local time constant is a conservative
    # explicit-Euler stability limit for this passive RC network.
    stable_seconds = [
        capacities[node_id] * 3600.0 / total_g
        for node_id, total_g in conductance_sum.items()
        if node_id not in fixed and total_g > 0
    ]
    max_substep = min(stable_seconds) * 0.25 if stable_seconds else duration
    steps = max(1, math.ceil(duration / max_substep))
    maximum_steps = 100_000
    if steps > maximum_steps:
        raise ValueError(
            f"thermal step requires {steps} substeps; maximum is {maximum_steps}. "
            "Check unusually small capacities or large conductances."
        )
    dt_hours = duration / steps / 3600.0
    initial_temperatures = dict(temperatures)
    applied_gains_wh = 0.0
    boundary_heat_exchange_wh = 0.0

    for _ in range(steps):
        net_w = {node_id: gains[node_id] for node_id in nodes}
        applied_gains_wh += sum(gains[node_id] for node_id in capacities) * dt_hours
        for source, target, conductance in links:
            flow_w = conductance * (temperatures[source] - temperatures[target])
            if source not in fixed:
                net_w[source] -= flow_w
            else:
                boundary_heat_exchange_wh -= flow_w * dt_hours
            if target not in fixed:
                net_w[target] += flow_w
            else:
                boundary_heat_exchange_wh += flow_w * dt_hours
        for node_id, capacity in capacities.items():
            temperatures[node_id] += net_w[node_id] * dt_hours / capacity

    final_flows = []
    for source, target, conductance in links:
        flow_w = conductance * (temperatures[source] - temperatures[target])
        final_flows.append({
            "from": source,
            "to": target,
            "conductance_w_per_k": conductance,
            "heat_flow_w": round(flow_w, 4),
        })

    stored_energy_change_wh = sum(
        capacity * (temperatures[node_id] - initial_temperatures[node_id])
        for node_id, capacity in capacities.items()
    )
    expected_energy_change_wh = applied_gains_wh + boundary_heat_exchange_wh
    energy_balance_error_wh = stored_energy_change_wh - expected_energy_change_wh

    return {
        "temperatures_c": {
            node_id: round(value, 4) for node_id, value in temperatures.items()
        },
        "connections": final_flows,
        "energy_balance": {
            "stored_energy_change_wh": round(stored_energy_change_wh, 6),
            "applied_gains_wh": round(applied_gains_wh, 6),
            "boundary_heat_exchange_wh": round(boundary_heat_exchange_wh, 6),
            "expected_energy_change_wh": round(expected_energy_change_wh, 6),
            "error_wh": round(energy_balance_error_wh, 6),
            "status": (
                "balanced_within_numerical_precision"
                if abs(energy_balance_error_wh) <= max(0.01, abs(expected_energy_change_wh) * 1e-6)
                else "energy_balance_error"
            ),
        },
        "substeps": steps,
        "duration_seconds": duration,
        "model": "lumped_rc_network",
    }



def build_multizone_observation_model(
    rooms: dict[str, dict[str, Any]],
    weather: dict[str, Any] | None = None,
    attic: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    plant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map configured HA observations into a transparent, parameter-aware zone inventory.

    This deliberately does not invent thermal capacities or room-to-room
    conductances. The inventory is useful before those physical parameters
    are identifiable; simulation remains withheld until required parameters
    are explicitly configured or defensibly learned.
    """
    weather = weather or {}
    attic = attic or {}
    profile = profile or {}
    plant = plant or {}

    def value(record):
        if isinstance(record, dict):
            return _finite_or_none(record.get("value"))
        return _finite_or_none(record)

    outdoor_record = weather.get("outdoor_temperature")
    outdoor_c = value(outdoor_record)
    nodes: dict[str, dict[str, Any]] = {}
    for room_id, room in rooms.items():
        nodes[f"room:{room_id}"] = {
            "kind": "room_air",
            "name": room.get("name", room_id),
            "floor": room.get("floor", "unassigned"),
            "temperature_c": _finite_or_none(room.get("temperature")),
            "temperature_entity": room.get("temperature_entity"),
            "demand": room.get("demand"),
            "demand_state_known": room.get("demand_state_known", False),
            "window_open": room.get("window_open"),
            "window_state_known": room.get("window_state_known", False),
            "area_m2": _finite_or_none(room.get("area_m2")),
            "volume_m3": _finite_or_none(room.get("volume_m3")),
            "configured_ach": _finite_or_none(room.get("air_changes_per_hour")),
            "air_thermal_capacity_wh_per_k": (
                round(1.2 * 1005.0 / 3600.0 * float(room.get("volume_m3")), 4)
                if _finite_or_none(room.get("volume_m3")) is not None
                and _finite_or_none(room.get("volume_m3")) > 0 else None
            ),
            "co2_ppm": _finite_or_none(room.get("co2_ppm")),
            "humidity_percent": _finite_or_none(room.get("humidity")),
            "thermal_capacity_wh_per_k": None,
            "thermal_response_evidence": _room_response_evidence(room.get("thermal_model")),
            "status": "observed" if _finite_or_none(room.get("temperature")) is not None else "temperature_unavailable",
        }

    attic_c = _finite_or_none(attic.get("attic_temperature"))
    if attic.get("temperature_entity") or attic_c is not None:
        nodes["attic:air"] = {
            "kind": "attic_air",
            "name": "Attic",
            "temperature_c": attic_c,
            "temperature_entity": attic.get("temperature_entity"),
            "humidity_percent": _finite_or_none(attic.get("attic_humidity")),
            "thermal_capacity_wh_per_k": None,
            "status": "observed" if attic_c is not None else "temperature_unavailable",
        }
    if outdoor_c is not None or outdoor_record:
        nodes["boundary:outdoors"] = {
            "kind": "fixed_boundary",
            "name": "Outdoors",
            "temperature_c": outdoor_c,
            "temperature_entity": outdoor_record.get("entity_id") if isinstance(outdoor_record, dict) else None,
            "status": "observed" if outdoor_c is not None else "temperature_unavailable",
        }

    # Floor-level observations remain separate from room air. A screed node
    # is pending until its temperature/capacity can be identified.
    floor_nodes = {}
    for floor_id in ('ground_floor', 'upstairs'):
        floor_rooms = [room for room in rooms.values() if room.get('floor', 'unassigned') == floor_id and _finite_or_none(room.get('temperature')) is not None]
        if not floor_rooms:
            continue
        pump = (plant.get('pumps') or {}).get(floor_id, {})
        average_c = sum(float(room['temperature']) for room in floor_rooms) / len(floor_rooms)
        screed_cm = _finite_or_none(profile.get('ufh_screed_cm'))
        floor_node_id = 'floor:' + floor_id
        nodes[floor_node_id] = {
            'kind': 'ufh_slab',
            'name': 'Downstairs UFH slab' if floor_id == 'ground_floor' else 'Upstairs UFH slab',
            'floor': floor_id,
            'adjacent_room_average_temperature_c': round(average_c, 3),
            'slab_temperature_c': None,
            'screed_thickness_cm': screed_cm,
            'thermal_capacity_wh_per_k': None,
            'pump_running': pump.get('running'),
            'observability': (
                (plant.get('ufh_slab_observability') or {}).get('floors', {}).get(floor_id, {})
            ),
            'status': 'awaiting_slab_sensor_or_identifiable_capacity' if screed_cm is not None else 'screed_profile_incomplete',
        }
        floor_nodes[floor_id] = floor_node_id

    # Plant telemetry is not equated with delivered heat: flow rate is absent.
    boiler_context = plant.get('boiler_context') or {}
    plant_node = {
        'kind': 'heating_plant',
        'name': profile.get('boiler_model') or 'Heating plant',
        'supply_water_temperature_c': _finite_or_none(plant.get('water_temperature')),
        'return_water_temperature_c': _finite_or_none(plant.get('water_return_temperature')),
        'burner_state': boiler_context.get('burner_state'),
        'burner_on': boiler_context.get('burner_on'),
        'modulation_percent': _finite_or_none(boiler_context.get('modulation_percent', boiler_context.get('modulation'))),
        'pumps': {pump_id: {'running': pump.get('running'), 'power_w': _finite_or_none(pump.get('power')), 'stage': pump.get('stage')} for pump_id, pump in (plant.get('pumps') or {}).items()},
        'thermal_output_w': None,
        'status': 'telemetry_observed' if (plant.get('water_temperature') is not None or plant.get('water_return_temperature') is not None or boiler_context.get('burner_state') not in (None, 'unknown', 'unavailable') or _finite_or_none(boiler_context.get('modulation_percent', boiler_context.get('modulation'))) is not None) else 'plant_sensors_unavailable',
        'note': 'No water-side heat output is calculated without flow rate and a validated flow/return measurement pair.',
    }
    nodes['plant:heating'] = plant_node
    # Ventilation conductance is a property of configured volume and ACH;
    # it does not depend on whether an outdoor-temperature reading is live.
    # rho*cp/3600 converts m3/h into W/K.
    ventilation_paths = []
    ventilation_factor_w_per_k_per_m3_ach = 1.2 * 1005.0 / 3600.0
    for room_id, room in rooms.items():
        volume = _finite_or_none(room.get("volume_m3"))
        ach = _finite_or_none(room.get("air_changes_per_hour"))
        if volume is None or ach is None or volume <= 0 or ach <= 0:
            continue
        nominal_conductance = ventilation_factor_w_per_k_per_m3_ach * volume * ach
        ventilation_paths.append({
            "from": "room:" + room_id,
            "to": "boundary:outdoors",
            "path": "ventilation",
            "conductance_w_per_k": round(nominal_conductance, 4),
            "sensitivity_ach_minus_10_percent_w_per_k": round(nominal_conductance * 0.9, 4),
            "sensitivity_ach_plus_10_percent_w_per_k": round(nominal_conductance * 1.1, 4),
            "sensitivity_note": (
                "Illustrative +/-10% ACH input scenario only; not a measured uncertainty "
                "interval or confidence bound."
            ),
            "source": "configured_room_volume_and_ach",
            "volume_m3": volume,
            "air_changes_per_hour": ach,
            "status": "estimated_from_configured_inputs",
        })
    if ventilation_paths and "boundary:outdoors" not in nodes:
        nodes["boundary:outdoors"] = {
            "kind": "fixed_boundary",
            "name": "Outdoors",
            "temperature_c": None,
            "temperature_entity": None,
            "status": "temperature_unavailable",
        }

    # Conductive roof and ceiling paths are only exposed when both U-value
    # and area were explicitly entered in the house assessment.
    connections = []
    pending_paths = []
    for node_id, u_key, area_key, label in (
        ("attic:air", "roof_u_value", "roof_area_m2", "roof"),
        ("attic:air", "attic_floor_u_value", "attic_floor_area_m2", "attic_floor"),
    ):
        if node_id not in nodes or "boundary:outdoors" not in nodes:
            continue
        try:
            u_value = float(profile.get(u_key))
            area = float(profile.get(area_key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(u_value) and math.isfinite(area) and u_value > 0 and area > 0:
            if label == "roof":
                connections.append({
                    "from": node_id,
                    "to": "boundary:outdoors",
                    "path": label,
                    "conductance_w_per_k": round(u_value * area, 4),
                    "source": "configured_u_value_times_area",
                    "status": "configured",
                })
            else:
                pending_paths.append({
                    "path": label,
                    "conductance_w_per_k": round(u_value * area, 4),
                    "source": "configured_u_value_times_area",
                    "status": "awaiting_upstairs_zone_coupling",
                })

    solar_record = weather.get("solar_radiation")
    solar_w_m2 = value(solar_record)
    window_areas = {direction: _finite_or_none(profile.get("window_area_" + direction + "_m2")) for direction in ("north", "south", "east", "west")}
    configured_window_areas = {direction: area for direction, area in window_areas.items() if area is not None and area > 0}
    gain_context = {
        "solar_radiation_w_m2": solar_w_m2,
        "window_area_by_orientation_m2": configured_window_areas,
        "window_glazing": profile.get("window_glazing"),
        "main_orientation": profile.get("main_orientation"),
        "shading": profile.get("shading"),
        "internal_gains_classification": profile.get("internal_gains"),
        "solar_gain_w": None,
        "internal_gain_w": None,
        "status": "context_only_missing_solar_transmittance_and_orientation_model" if solar_w_m2 is not None or configured_window_areas else "awaiting_solar_and_window_inputs",
        "note": "Solar and internal gains are retained as context. No gain in watts is inferred without calibrated glazing solar transmittance, shading and orientation-specific irradiance; occupancy categories are not converted into heat watts.",
    }

    observed_rooms = sum(1 for node in nodes.values() if node["kind"] == "room_air" and node["temperature_c"] is not None)
    room_count = len(rooms)
    rooms_with_volume = sum(
        1 for room in rooms.values()
        if (_finite_or_none(room.get("volume_m3")) or 0) > 0
    )
    total_configured_volume = sum(
        float(room["volume_m3"]) for room in rooms.values()
        if (_finite_or_none(room.get("volume_m3")) or 0) > 0
    )
    air_capacity_wh_per_k = 1.2 * 1005.0 / 3600.0 * total_configured_volume
    total_ventilation_conductance = sum(
        item["conductance_w_per_k"] for item in ventilation_paths
    )
    rooms_with_volume_and_ach = sum(
        1 for room in rooms.values()
        if (_finite_or_none(room.get("volume_m3")) or 0) > 0
        and (_finite_or_none(room.get("air_changes_per_hour")) or 0) > 0
    )
    ventilation_coverage_complete = bool(room_count) and rooms_with_volume_and_ach == room_count
    house_heat_loss = _finite_or_none(plant.get("house_heat_loss_coefficient_w_k"))
    if house_heat_loss is None:
        house_heat_loss = _finite_or_none(
            (profile.get("house_effective_rc_calibration") or {}).get("heat_loss_coefficient_w_k")
        )
    if house_heat_loss is None:
        house_heat_loss = _finite_or_none(profile.get("house_heat_loss_coefficient_w_k"))
    aggregate_capacity = _finite_or_none(
        (profile.get("house_effective_rc_calibration") or {}).get(
            "effective_building_thermal_capacity_wh_per_k"
        )
    )
    if aggregate_capacity is None:
        aggregate_capacity = _finite_or_none(
            profile.get("effective_building_thermal_capacity_wh_per_k")
        )
    capacity_budget = {
        "status": "withheld_incomplete_room_volume_coverage",
        "effective_building_capacity_wh_per_k": round(aggregate_capacity, 2) if aggregate_capacity is not None else None,
        "configured_air_only_capacity_wh_per_k": round(air_capacity_wh_per_k, 2),
        "implied_non_air_capacity_wh_per_k": None,
        "volume_coverage_percent": round(100 * rooms_with_volume / room_count) if room_count else None,
        "rooms_with_volume": rooms_with_volume,
        "configured_rooms": room_count,
        "room_capacity_allocation": None,
        "note": (
            "This is an aggregate consistency check only. It does not allocate effective "
            "capacity to individual rooms; wall, furnishing, floor and slab storage remain "
            "unresolved. The subtraction is meaningful only when all configured room volumes "
            "are represented and the effective building estimate describes the same conditioned zone."
        ),
    }
    if rooms_with_volume != room_count or room_count == 0:
        capacity_budget["status"] = "withheld_incomplete_room_volume_coverage"
    elif aggregate_capacity is None or aggregate_capacity <= 0:
        capacity_budget["status"] = "withheld_effective_building_capacity_unavailable"
    elif aggregate_capacity < air_capacity_wh_per_k:
        capacity_budget["status"] = "inconsistent_effective_capacity_below_air_only"
        capacity_budget["note"] += (
            " The estimated whole-building effective capacity is below the calculated air-only "
            "capacity; inspect the heat-loss estimate, cooling fit, room volumes and zone boundary."
        )
    else:
        capacity_budget["status"] = "aggregate_non_air_capacity_available"
        capacity_budget["implied_non_air_capacity_wh_per_k"] = round(
            aggregate_capacity - air_capacity_wh_per_k, 2
        )
    envelope_decomposition = {
        "status": "withheld_incomplete_ventilation_coverage",
        "total_house_heat_loss_coefficient_w_k": round(house_heat_loss, 3) if house_heat_loss is not None else None,
        "ventilation_heat_loss_coefficient_w_k": round(total_ventilation_conductance, 3),
        "residual_envelope_heat_loss_coefficient_w_k": None,
        "rooms_with_volume_and_ach": rooms_with_volume_and_ach,
        "configured_rooms": room_count,
        "ventilation_coverage_complete": ventilation_coverage_complete,
        "note": (
            "Residual envelope loss is withheld unless every configured room has positive "
            "volume and ACH and a positive independently estimated whole-house heat-loss "
            "coefficient is supplied. The subtraction assumes the whole-house coefficient "
            "already includes ventilation loss and uses the same indoor/outdoor boundary."
        ),
    }
    if not ventilation_coverage_complete:
        envelope_decomposition["status"] = "withheld_incomplete_ventilation_coverage"
    elif house_heat_loss is None or house_heat_loss <= 0:
        envelope_decomposition["status"] = "withheld_house_heat_loss_unavailable"
    elif total_ventilation_conductance >= house_heat_loss:
        envelope_decomposition["status"] = "withheld_ventilation_not_below_total"
        envelope_decomposition["note"] += (
            " Configured ventilation conductance is not below the aggregate coefficient; "
            "check ACH, room volumes, heat-loss assumptions and boundary consistency."
        )
    else:
        envelope_decomposition["status"] = "conditional_residual_available"
        envelope_decomposition["residual_envelope_heat_loss_coefficient_w_k"] = round(
            house_heat_loss - total_ventilation_conductance, 3
        )
    missing_capacity = [node_id for node_id, node in nodes.items() if node["kind"] != "fixed_boundary" and node.get("thermal_capacity_wh_per_k") is None]
    missing_links = [
        node_id for node_id, node in nodes.items()
        if node["kind"] == "room_air"
    ]
    return {
        "model": "multi_zone_lumped_rc",
        "status": "observations_ready_parameters_incomplete" if nodes else "waiting_for_configured_sensors",
        "simulation_status": "withheld_missing_physical_parameters",
        "nodes": nodes,
        "configured_connections": connections,
        "ventilation_paths": ventilation_paths,
        "house_heat_loss_decomposition": envelope_decomposition,
        "aggregate_capacity_budget": capacity_budget,
        "air_only_thermal_inventory": {
            "status": "partial_configured_volume" if rooms_with_volume < room_count else (
                "available" if room_count else "no_configured_rooms"
            ),
            "rooms_with_volume": rooms_with_volume,
            "configured_rooms": room_count,
            "volume_coverage_percent": round(100 * rooms_with_volume / room_count) if room_count else None,
            "total_configured_room_volume_m3": round(total_configured_volume, 3),
            "air_only_capacity_wh_per_k": round(air_capacity_wh_per_k, 3),
            "known_ventilation_conductance_w_per_k": round(total_ventilation_conductance, 4),
            "interpretation": (
                "Air-only sensible heat capacity from configured room volumes using rho=1.2 kg/m3 "
                "and cp=1005 J/kg/K. It excludes walls, furnishings, floors and slab storage; "
                "it is not the effective room or building thermal capacity."
            ),
        },
        "pending_paths": pending_paths,
        "floor_nodes": floor_nodes,
        "plant": plant_node,
        "gain_context": gain_context,
        "coverage": {
            "configured_rooms": room_count,
            "rooms_with_temperature": observed_rooms,
            "room_temperature_coverage_percent": round(100 * observed_rooms / room_count) if room_count else None,
            "rooms_with_co2": sum(1 for room in rooms.values() if _finite_or_none(room.get("co2_ppm")) is not None),
            "rooms_with_humidity": sum(1 for room in rooms.values() if _finite_or_none(room.get("humidity")) is not None),
            "rooms_with_ach": sum(1 for room in rooms.values() if _finite_or_none(room.get("air_changes_per_hour")) is not None),
            "rooms_with_volume_and_ach": sum(1 for room in rooms.values() if (_finite_or_none(room.get("volume_m3")) or 0) > 0 and (_finite_or_none(room.get("air_changes_per_hour")) or 0) > 0),
            "rooms_with_air_capacity_estimate": rooms_with_volume,
            "attic_temperature_available": attic_c is not None,
            "outdoor_temperature_available": outdoor_c is not None,
            "ufh_floor_nodes": len(floor_nodes),
            "plant_supply_temperature_available": plant_node["supply_water_temperature_c"] is not None,
            "plant_return_temperature_available": plant_node["return_water_temperature_c"] is not None,
        },
        "missing_parameters": {
            "thermal_capacities": missing_capacity,
            "room_envelope_and_interzone_conductances": missing_links,
        },
        "interpretation": (
            "Room, attic and outdoor observations are mapped from configured entities. "
            "The physical network is not simulated until thermal capacities and the "
            "required envelope/inter-zone conductances are available; no values are fabricated."
        ),
    }


def _room_response_evidence(model: Any) -> dict[str, Any]:
    """Expose learned room response as evidence, never as an RC parameter."""
    if not isinstance(model, dict):
        return {
            "status": "not_available",
            "heating_rate_c_per_hour": None,
            "coasting_rate_c_per_hour": None,
            "heating_confidence": 0,
            "coasting_confidence": 0,
            "note": "No validated room-response model is available.",
        }
    heating = model.get("heating") if isinstance(model.get("heating"), dict) else {}
    coasting = model.get("coasting") if isinstance(model.get("coasting"), dict) else {}
    heating_rate = _finite_or_none(heating.get("rate_c_per_hour"))
    coasting_rate = _finite_or_none(coasting.get("rate_c_per_hour"))
    heating_confidence = int(_finite_or_none(heating.get("confidence")) or 0)
    coasting_confidence = int(_finite_or_none(coasting.get("confidence")) or 0)
    available = heating_rate is not None or coasting_rate is not None
    return {
        "status": "observed_response_available" if available else "collecting_clean_response_history",
        "heating_rate_c_per_hour": heating_rate,
        "coasting_rate_c_per_hour": coasting_rate,
        "heating_confidence": heating_confidence,
        "coasting_confidence": coasting_confidence,
        "heating_valid_intervals": int(heating.get("valid_intervals") or 0),
        "coasting_valid_intervals": int(coasting.get("valid_intervals") or 0),
        "note": (
            "Observed temperature response only. Without a measured thermal input and independently "
            "identified storage capacity, these rates cannot identify room heat capacity or envelope conductance."
        ),
    }


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
