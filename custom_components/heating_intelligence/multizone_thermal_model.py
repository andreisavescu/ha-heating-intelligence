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
                boundary_heat_exchange_wh += flow_w * dt_hours
            if target not in fixed:
                net_w[target] += flow_w
            else:
                boundary_heat_exchange_wh -= flow_w * dt_hours
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
    circuits: list[dict[str, Any]] | None = None,
    envelope_audit: dict[str, Any] | None = None,
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
    circuits = circuits or []
    envelope_audit = envelope_audit or {}
    calculated_envelope_rooms = envelope_audit.get("rooms", {}) or {}

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
            "envelope_conductance_w_per_k": _finite_or_none(room.get("envelope_conductance_w_per_k")),
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

    # Room-level UFH topology is the physical representation. Each room
    # receives its own latent slab node; floor summaries are never treated as
    # a shared thermal storage state.
    room_ufh_topology = build_room_ufh_topology(rooms, circuits)
    stage5_rooms = (
        ((plant.get("stage5_ufh_slab_model") or {}).get("rooms") or {})
    )
    room_slab_nodes = {}
    floor_nodes = {"ground_floor": [], "upstairs": []}
    for room_id, room in rooms.items():
        stage5_room = stage5_rooms.get(room_id) or {}
        screed = stage5_room.get("screed") or {}
        finish = stage5_room.get("floor_finish") or {}
        topology = (room_ufh_topology.get("room_slab_nodes") or {}).get(room_id) or {}
        floor_id = room.get("floor", "unassigned")
        slab_node_id = f"slab:{room_id}"
        nodes[slab_node_id] = {
            "kind": "ufh_slab",
            "name": f"{room.get('name', room_id)} UFH slab",
            "room_id": room_id,
            "floor": floor_id,
            "slab_temperature_c": _finite_or_none(stage5_room.get("slab_temperature_c")),
            "temperature_c": _finite_or_none(stage5_room.get("slab_temperature_c")),
            # Material priors are visible but never silently promoted to an
            # identified dynamic capacity or a solver-ready temperature.
            "thermal_capacity_wh_per_k": None,
            "screed_capacity_prior_wh_per_k": _finite_or_none(screed.get("capacity_wh_per_k")),
            "finish_capacity_prior_wh_per_k": _finite_or_none(finish.get("capacity_wh_per_k")),
            "combined_material_capacity_prior_wh_per_k": _finite_or_none(
                stage5_room.get("combined_screed_and_finish_capacity_wh_per_k")
            ),
            "finish_resistance_m2k_w": _finite_or_none(
                finish.get("equivalent_parallel_resistance_m2k_w")
            ),
            "circuit_ids": topology.get("circuit_ids") or stage5_room.get("circuits") or [],
            "active_flow_l_min": _finite_or_none(stage5_room.get("active_flow_l_min")) or 0.0,
            "allocated_useful_heat_power_w": _finite_or_none(
                stage5_room.get("allocated_useful_heat_power_w")
            ),
            "slab_to_room_conductance_w_per_k": None,
            "slab_to_lower_boundary_conductance_w_per_k": None,
            "transfer_coefficients_status": (
                stage5_room.get("transfer_coefficients_status")
                or "not_identifiable_without_room_slab_temperature_or_independent_room_heat_input"
            ),
            "status": (
                "awaiting_room_slab_temperature_and_identifiable_capacity"
                if stage5_room else "room_level_topology_only"
            ),
        }
        room_slab_nodes[room_id] = slab_node_id
        if floor_id in floor_nodes:
            floor_nodes[floor_id].append(slab_node_id)

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

    # Conductive attic paths are only exposed from explicitly configured
    # U-values and areas. The outdoor boundary exists even if its live sensor
    # is unavailable; a boundary temperature is an observation, not topology.
    connections = []
    pending_paths = []
    attic_floor_paths = []
    roof_u = _finite_or_none(profile.get("roof_u_value"))
    roof_area = _finite_or_none(profile.get("roof_area_m2"))
    # Summarise attic paths without inferring any missing building values.
    upstairs_room_ids = [
        room_id for room_id, room in rooms.items()
        if room.get("floor") == "upstairs"
    ]
    configured_attic_shares = profile.get("attic_floor_room_conductances_w_per_k") or {}
    if not isinstance(configured_attic_shares, dict):
        configured_attic_shares = {}
    valid_attic_shares = {}
    for room_id, raw in configured_attic_shares.items():
        numeric = _finite_or_none(raw)
        if room_id in upstairs_room_ids and numeric is not None and numeric > 0:
            valid_attic_shares[room_id] = numeric
    # Valid per-room attic surface records supersede legacy attic conductance entries.
    for room_id in upstairs_room_ids:
        inventory = calculated_envelope_rooms.get(room_id, {}) or {}
        counts = inventory.get("opaque_surface_counts", {}) or {}
        attic_count = _finite_or_none(counts.get("attic")) or 0
        attic_value = _finite_or_none((inventory.get("conductance_by_boundary_w_per_k") or {}).get("attic"))
        if attic_count > 0 and attic_value is not None and attic_value > 0:
            valid_attic_shares[room_id] = attic_value

    if "attic:air" in nodes and roof_u is not None and roof_u > 0 and roof_area is not None and roof_area > 0 and "boundary:outdoors" not in nodes:
        nodes["boundary:outdoors"] = {
            "kind": "fixed_boundary", "name": "Outdoors",
            "temperature_c": outdoor_c,
            "temperature_entity": outdoor_record.get("entity_id") if isinstance(outdoor_record, dict) else None,
            "status": "observed" if outdoor_c is not None else "temperature_unavailable",
        }
    for node_id, u_key, area_key, label in (
        ("attic:air", "roof_u_value", "roof_area_m2", "roof"),
        ("attic:air", "attic_floor_u_value", "attic_floor_area_m2", "attic_floor"),
    ):
        if node_id not in nodes:
            continue
        u_value = _finite_or_none(profile.get(u_key))
        area = _finite_or_none(profile.get(area_key))
        has_total = u_value is not None and u_value > 0 and area is not None and area > 0

        if label == "roof":
            if "boundary:outdoors" not in nodes or not has_total:
                continue
            connections.append({
                "from": node_id,
                "to": "boundary:outdoors",
                "path": label,
                "conductance_w_per_k": round(u_value * area, 4),
                "source": "configured_u_value_times_area",
                "status": "configured",
            })
            continue

        # Explicit room-level attic shares are valid topology even when the
        # optional whole-attic U-value/area pair has not been configured.
        valid_shares = list(valid_attic_shares.items())
        if valid_shares:
            for room_id, room_conductance in valid_shares:
                inventory = calculated_envelope_rooms.get(room_id, {}) or {}
                attic_surface_count = (_finite_or_none((inventory.get("opaque_surface_counts") or {}).get("attic")) or 0)
                attic_floor_paths.append({
                    "from": "attic:air",
                    "to": "room:" + room_id,
                    "path": "attic_floor",
                    "conductance_w_per_k": round(room_conductance, 4),
                    "source": "per_room_surface_inventory" if attic_surface_count > 0 else "legacy_room_level_attic_floor_conductance",
                    "status": "configured" if attic_surface_count > 0 else "legacy_fallback",
                })
            if has_total and abs(sum(value for _, value in valid_shares) - u_value * area) > max(0.1, u_value * area * 0.05):
                pending_paths.append({
                    "path": "attic_floor",
                    "conductance_w_per_k": round(u_value * area, 4),
                    "status": "configured_room_shares_do_not_match_total",
                    "configured_room_conductance_w_per_k": round(sum(value for _, value in valid_shares), 4),
                })
        elif has_total:
            pending_paths.append({
                "path": label,
                "conductance_w_per_k": round(u_value * area, 4),
                "source": "configured_u_value_times_area",
                "status": "awaiting_upstairs_zone_coupling",
                "detail": "Room-level allocation requires explicit attic_floor_room_conductances_w_per_k entries.",
            })

    # Only explicit conductances create room-to-room or room-to-outdoor links.
    inter_room_paths = []
    unresolved_room_paths = []
    for index, item in enumerate(profile.get("room_connections", []) or []):
        if not isinstance(item, dict):
            unresolved_room_paths.append({"index": index, "reason": "invalid_connection_record"})
            continue
        source_room = str(item.get("from_room") or "")
        target_room = str(item.get("to_room") or "")
        conductance = _finite_or_none(item.get("conductance_w_per_k"))
        if source_room not in rooms or target_room not in rooms or source_room == target_room:
            unresolved_room_paths.append({"index": index, "reason": "invalid_room_reference"})
            continue
        if conductance is None or conductance <= 0:
            unresolved_room_paths.append({"index": index, "reason": "conductance_not_configured"})
            continue
        inter_room_paths.append({
            "from": "room:" + source_room, "to": "room:" + target_room,
            "path": "inter_room", "conductance_w_per_k": round(conductance, 4),
            "source": "explicit_house_assessment", "status": "configured",
        })

    room_envelope_paths = []
    ground_paths = []
    envelope_reconciliation = {}
    for room_id, room in rooms.items():
        configured = _finite_or_none(room.get("envelope_conductance_w_per_k"))
        calculated = calculated_envelope_rooms.get(room_id, {}) or {}
        calculated_by_boundary = calculated.get("conductance_by_boundary_w_per_k", {}) or {}
        opaque_counts = calculated.get("opaque_surface_counts", {}) or {}
        calculated_outdoor = _finite_or_none(calculated_by_boundary.get("outdoor"))
        has_room_wall_surfaces = (_finite_or_none(opaque_counts.get("outdoor")) or 0) > 0
        selected_outdoor = calculated_outdoor if has_room_wall_surfaces else configured
        difference = (
            round(configured - calculated_outdoor, 4)
            if configured is not None and calculated_outdoor is not None else None
        )
        envelope_reconciliation[room_id] = {
            "configured_room_to_outdoors_w_per_k": configured,
            "calculated_surface_inventory_outdoor_w_per_k": calculated_outdoor,
            "difference_configured_minus_inventory_w_per_k": difference,
            "status": (
                "surface_inventory_authoritative"
                if has_room_wall_surfaces and calculated_outdoor is not None
                else "legacy_configuration_fallback"
                if configured is not None
                else "awaiting_room_wall_surfaces"
            ),
            "model_source": "per_room_surface_inventory_including_windows" if has_room_wall_surfaces else "legacy_room_configuration",
            "calculation_policy": (
                "Once valid opaque outdoor wall surfaces are configured for a room, their "
                "conductance plus that room's window/door records replace the legacy room "
                "conductance. The two sources are never added together."
            ),
        }
        if selected_outdoor is not None and selected_outdoor > 0:
            room_envelope_paths.append({
                "from": "room:" + room_id, "to": "boundary:outdoors",
                "path": "room_envelope", "conductance_w_per_k": round(selected_outdoor, 4),
                "source": "per_room_surface_inventory_including_windows" if has_room_wall_surfaces else "legacy_room_configuration",
                "status": "configured" if has_room_wall_surfaces else "legacy_fallback",
            })
        ground = _finite_or_none(calculated_by_boundary.get("ground"))
        ground_count = _finite_or_none(opaque_counts.get("ground")) or 0
        if ground is not None and ground_count > 0:
            ground_paths.append({
                "from": "room:" + room_id, "to": "boundary:ground",
                "path": "ground", "conductance_w_per_k": round(ground, 4),
                "source": "per_room_surface_inventory", "status": "configured",
            })
    if room_envelope_paths and "boundary:outdoors" not in nodes:
        nodes["boundary:outdoors"] = {
            "kind": "fixed_boundary", "name": "Outdoors",
            "temperature_c": outdoor_c,
            "temperature_entity": outdoor_record.get("entity_id") if isinstance(outdoor_record, dict) else None,
            "status": "observed" if outdoor_c is not None else "temperature_unavailable",
        }
    if ground_paths:
        nodes["boundary:ground"] = {
            "kind": "fixed_boundary", "name": "Ground",
            "temperature_c": None,
            "status": "ground_temperature_not_configured",
        }

    attic_floor_total_g = (
        _finite_or_none(profile.get("attic_floor_u_value"))
        * _finite_or_none(profile.get("attic_floor_area_m2"))
        if _finite_or_none(profile.get("attic_floor_u_value")) is not None
        and _finite_or_none(profile.get("attic_floor_area_m2")) is not None
        and _finite_or_none(profile.get("attic_floor_u_value")) > 0
        and _finite_or_none(profile.get("attic_floor_area_m2")) > 0
        else None
    )
    attic_share_sum = sum(valid_attic_shares.values()) if valid_attic_shares else None
    missing_attic_rooms = [room_id for room_id in upstairs_room_ids if room_id not in valid_attic_shares]
    roof_connection = next((item for item in connections if item.get("path") == "roof"), None)
    attic_thermal_diagnostics = {
        "attic_sensor_available": attic_c is not None,
        "upstairs_rooms_configured": len(upstairs_room_ids),
        "upstairs_rooms_with_attic_conductance": len(valid_attic_shares),
        "rooms_missing_attic_conductance": missing_attic_rooms,
        "attic_floor_area_m2": _finite_or_none(profile.get("attic_floor_area_m2")),
        "attic_floor_u_value_w_m2k": _finite_or_none(profile.get("attic_floor_u_value")),
        "attic_floor_total_conductance_w_per_k": round(attic_floor_total_g, 4) if attic_floor_total_g is not None else None,
        "attic_floor_room_conductance_sum_w_per_k": round(attic_share_sum, 4) if attic_share_sum is not None else None,
        "attic_floor_status": (
            "attic_sensor_unavailable" if "attic:air" not in nodes
            else "no_upstairs_rooms" if not upstairs_room_ids
            else "room_shares_complete" if not missing_attic_rooms
            else "partial_room_shares" if valid_attic_shares
            else "awaiting_room_shares"
        ),
        "attic_floor_total_match_status": (
            "not_comparable_without_total_u_and_area" if attic_floor_total_g is None or attic_share_sum is None
            else "matches_within_5_percent" if abs(attic_share_sum - attic_floor_total_g) <= max(0.1, attic_floor_total_g * 0.05)
            else "room_shares_differ_from_configured_total"
        ),
        "roof_area_m2": _finite_or_none(profile.get("roof_area_m2")),
        "roof_u_value_w_m2k": _finite_or_none(profile.get("roof_u_value")),
        "roof_conductance_w_per_k": roof_connection.get("conductance_w_per_k") if roof_connection else None,
        "roof_status": (
            "attic_sensor_unavailable" if "attic:air" not in nodes
            else "configured" if roof_connection
            else "awaiting_explicit_roof_u_value_and_area"
        ),
        "note": "Room-to-attic links use explicit per-room W/K values. Attic-to-outdoor requires explicit roof U-value and area. No roof insulation or geometry is inferred from the floor build-up.",
    }

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
    dynamic_node_kinds = {"room_air", "attic_air", "ufh_slab", "thermal_mass", "buffer", "dhw"}
    missing_capacity = [
        node_id for node_id, node in nodes.items()
        if node.get("kind") in dynamic_node_kinds
        and node.get("thermal_capacity_wh_per_k") is None
    ]
    missing_envelope = [
        node_id for node_id, node in nodes.items()
        if node.get("kind") == "room_air"
        and (_finite_or_none(node.get("envelope_conductance_w_per_k")) or 0) <= 0
    ]
    room_node_ids = [node_id for node_id, node in nodes.items() if node["kind"] == "room_air"]
    configured_interzone = {
        frozenset((item["from"], item["to"])): item["conductance_w_per_k"]
        for item in inter_room_paths
    }
    interzone_pairs = []
    for index, left in enumerate(room_node_ids):
        for right in room_node_ids[index + 1:]:
            conductance = configured_interzone.get(frozenset((left, right)))
            interzone_pairs.append({
                "from": left,
                "to": right,
                "conductance_w_per_k": conductance,
                "status": "configured" if conductance is not None else "conductance_not_identified",
            })
    missing_links = list(missing_envelope) + [
        f"{pair['from']}<->{pair['to']}"
        for pair in interzone_pairs if pair["status"] != "configured"
    ]
    room_parameter_readiness = {}
    for node_id in room_node_ids:
        node = nodes[node_id]
        envelope_conductance = node.get("envelope_conductance_w_per_k")
        attic_conductance = next(
            (item["conductance_w_per_k"] for item in attic_floor_paths if item["to"] == node_id),
            None,
        )
        room_parameter_readiness[node_id] = {
            "air_only_capacity_wh_per_k": node.get("air_thermal_capacity_wh_per_k"),
            "effective_room_capacity_wh_per_k": node.get("thermal_capacity_wh_per_k"),
            "capacity_status": (
                "effective_capacity_available"
                if node.get("thermal_capacity_wh_per_k") is not None
                else "air_only_capacity_only"
                if node.get("air_thermal_capacity_wh_per_k") is not None
                else "capacity_unavailable"
            ),
            "envelope_conductance_w_per_k": envelope_conductance,
            "envelope_conductance_status": (
                "explicitly_configured" if envelope_conductance is not None and envelope_conductance > 0
                else "not_configured"
            ),
            "attic_floor_conductance_w_per_k": attic_conductance,
            "interzone_links": [
                pair for pair in interzone_pairs
                if pair["from"] == node_id or pair["to"] == node_id
            ],
        }
    simulation_blockers = []
    if missing_capacity:
        simulation_blockers.append("effective_thermal_capacity_missing")
    if missing_envelope:
        simulation_blockers.append("room_envelope_conductances_not_identified")
    if any(item["status"] != "configured" for item in interzone_pairs):
        simulation_blockers.append("interzone_conductances_not_identified")
    if "attic:air" in nodes and upstairs_room_ids and missing_attic_rooms:
        simulation_blockers.append("attic_floor_room_conductances_incomplete")
    if "attic:air" in nodes and roof_connection is None:
        simulation_blockers.append("attic_roof_conductance_unavailable")
    simulation_readiness = {
        "status": "withheld_missing_physical_parameters" if simulation_blockers else "physical_parameters_available",
        "blockers": simulation_blockers,
        "rooms": room_parameter_readiness,
        "unidentified_interzone_pairs": interzone_pairs,
        "note": (
            "Air-only room capacities are not substitutes for effective room capacities. "
            "Aggregate house capacity and residual envelope loss are not distributed to rooms, "
            "and normalized G/C response rates are not converted to W/K conductances."
        ),
    }
    return {
        "model": "multi_zone_lumped_rc",
        "status": "observations_ready_parameters_incomplete" if nodes else "waiting_for_configured_sensors",
        "simulation_status": simulation_readiness["status"],
        "simulation_readiness": simulation_readiness,
        "nodes": nodes,
        "configured_connections": connections + attic_floor_paths + inter_room_paths + room_envelope_paths + ground_paths,
        "inter_room_paths": inter_room_paths,
        "room_envelope_paths": room_envelope_paths,
        "ground_paths": ground_paths,
        "envelope_conductance_reconciliation": envelope_reconciliation,
        "attic_floor_paths": attic_floor_paths,
        "attic_thermal_diagnostics": attic_thermal_diagnostics,
        "room_path_diagnostics": {
            room_id: {
                "envelope_w_per_k": room.get("envelope_conductance_w_per_k"),
                "attic_floor_w_per_k": valid_attic_shares.get(room_id),
                "ventilation_w_per_k": next((item["conductance_w_per_k"] for item in ventilation_paths if item["from"] == "room:" + room_id), None),
                "inter_room_links": sum(1 for item in inter_room_paths if item["from"] == "room:" + room_id or item["to"] == "room:" + room_id),
                "status": "configured" if (_finite_or_none(room.get("envelope_conductance_w_per_k")) or 0) > 0 else "envelope_conductance_missing",
            }
            for room_id, room in rooms.items()
        },
        "network_diagnostics": {
            "configured_conductive_paths": len(connections) + len(attic_floor_paths) + len(inter_room_paths) + len(room_envelope_paths) + len(ground_paths),
            "configured_ventilation_paths": len(ventilation_paths),
            "pending_paths": len(pending_paths),
            "unresolved_room_paths": len(unresolved_room_paths),
            "simulation_blockers": list(simulation_blockers),
            "physical_simulation_enabled": not simulation_blockers,
        },
        "unresolved_room_paths": unresolved_room_paths,
        "room_ufh_topology": room_ufh_topology,
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
        "floor_nodes": floor_nodes,  # compatibility grouping; values are room slab node IDs\n        "room_slab_nodes": room_slab_nodes,
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
            "ufh_room_slab_nodes": len(room_slab_nodes),
            "ufh_floor_groups": {key: len(value) for key, value in floor_nodes.items()},
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


def build_room_ufh_topology(
    rooms: dict[str, dict[str, Any]],
    circuits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build room-level latent UFH slab nodes while preserving circuit topology.

    Each configured room receives one effective slab node. Hydraulic circuits
    remain individual records and may contribute to more than one room. A room
    may receive heat from multiple circuits. This is topology only: no slab
    temperature, capacity, flow-derived heat or thermal conductance is inferred.
    """
    room_nodes: dict[str, dict[str, Any]] = {}
    room_air_nodes: dict[str, dict[str, Any]] = {}
    room_slab_couplings: list[dict[str, Any]] = []
    for room_id, room in rooms.items():
        volume = _finite_or_none(room.get("volume_m3"))
        air_capacity = (
            1.2 * 1005.0 / 3600.0 * volume
            if volume is not None and volume > 0 else None
        )
        room_air_nodes[f"room:{room_id}"] = {
            "kind": "room_air",
            "room_id": room_id,
            "room_name": room.get("name", room_id),
            "floor": room.get("floor", "unassigned"),
            "temperature_c": _finite_or_none(room.get("temperature")),
            "temperature_entity": room.get("temperature_entity"),
            "temperature_source": (
                "configured_sensor" if _finite_or_none(room.get("temperature")) is not None
                else "unavailable"
            ),
            "volume_m3": volume,
            "air_only_capacity_wh_per_k": round(air_capacity, 4) if air_capacity is not None else None,
            "effective_room_capacity_wh_per_k": None,
            "effective_capacity_status": "not_identified",
            "status": "observed" if _finite_or_none(room.get("temperature")) is not None else "temperature_unavailable",
        }
        room_nodes[f"slab:{room_id}"] = {
            "kind": "room_ufh_slab",
            "room_id": room_id,
            "room_name": room.get("name", room_id),
            "floor": room.get("floor", "unassigned"),
            "temperature_c": None,
            "temperature_source": "latent_unobserved_state",
            "capacity_wh_per_k": None,
            "room_slab_conductance_w_per_k": None,
            "circuit_ids": [],
            "status": "topology_only_parameters_unidentified",
        }

    circuit_records: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, circuit in enumerate(circuits):
        circuit_id = str(circuit.get("id") or "").strip()
        if not circuit_id:
            unresolved.append({"index": index, "reason": "missing_circuit_id"})
            continue
        if circuit_id in seen:
            unresolved.append({"circuit_id": circuit_id, "reason": "duplicate_circuit_id"})
            continue
        seen.add(circuit_id)

        # served_rooms is the explicit thermal destination. Preserve the
        # existing legacy fallback order without treating demand_scope itself
        # as a physical heat-delivery measurement.
        served = circuit.get("served_rooms")
        if served is None:
            served = []
        if not served and circuit.get("room_id"):
            served = [circuit["room_id"]]
        if not served:
            served = circuit.get("demand_rooms") or []

        room_ids = list(dict.fromkeys(
            str(room_id) for room_id in served if room_id is not None
        ))
        valid_room_ids = [room_id for room_id in room_ids if room_id in rooms]
        unknown_room_ids = [room_id for room_id in room_ids if room_id not in rooms]
        if unknown_room_ids:
            unresolved.append({
                "circuit_id": circuit_id,
                "reason": "served_room_not_configured",
                "room_ids": unknown_room_ids,
            })

        record = {
            "circuit_id": circuit_id,
            "name": circuit.get("name", circuit_id),
            "floor": circuit.get("floor", "unassigned"),
            "pump_id": circuit.get("pump_id"),
            "pipe_length_m": _finite_or_none(circuit.get("pipe_length_m")),
            "served_room_ids": valid_room_ids,
            "unresolved_room_ids": unknown_room_ids,
            "source": (
                "explicit_served_rooms" if circuit.get("served_rooms")
                else "legacy_room_id" if circuit.get("room_id")
                else "legacy_demand_rooms" if circuit.get("demand_rooms")
                else "no_room_assignment"
            ),
            "thermal_output_w": None,
            "status": "mapped_to_room_slabs" if valid_room_ids else "no_valid_served_rooms",
        }
        circuit_records[circuit_id] = record
        for room_id in valid_room_ids:
            room_nodes[f"slab:{room_id}"]["circuit_ids"].append(circuit_id)

    for node in room_nodes.values():
        node["circuit_count"] = len(node["circuit_ids"])
        if not node["circuit_ids"]:
            node["status"] = "no_configured_ufh_circuit"

    for room_id in rooms:
        room_slab_couplings.append({
            "from": f"slab:{room_id}",
            "to": f"room:{room_id}",
            "path": "ufh_slab_to_room_air",
            "conductance_w_per_k": None,
            "status": "conductance_not_identified",
            "note": "Topology edge only; excluded from RC solver until conductance is identified.",
        })

    return {
        "representation": "one_room_air_and_one_effective_ufh_slab_node_per_room",
        "room_air_nodes": room_air_nodes,
        "room_slab_nodes": room_nodes,
        "room_slab_couplings": room_slab_couplings,
        "circuits": circuit_records,
        "coverage": {
            "configured_rooms": len(rooms),
            "rooms_with_circuits": sum(bool(node["circuit_ids"]) for node in room_nodes.values()),
            "configured_circuits": len(circuits),
            "mapped_circuits": sum(bool(item["served_room_ids"]) for item in circuit_records.values()),
            "unresolved_assignments": len(unresolved),
        },
        "unresolved_assignments": unresolved,
        "parameter_status": {
            "slab_temperature": "not_measured_or_estimated",
            "slab_capacity": "not_identified",
            "room_slab_conductance": "not_identified",
            "circuit_heat_output": "not_measured",
            "configured_flow": "not_used_as_thermal_power",
        },
    }
