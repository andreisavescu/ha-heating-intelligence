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
    dt_hours = duration / steps / 3600.0

    for _ in range(steps):
        net_w = {node_id: gains[node_id] for node_id in nodes}
        for source, target, conductance in links:
            flow_w = conductance * (temperatures[source] - temperatures[target])
            if source not in fixed:
                net_w[source] -= flow_w
            if target not in fixed:
                net_w[target] += flow_w
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

    return {
        "temperatures_c": {
            node_id: round(value, 4) for node_id, value in temperatures.items()
        },
        "connections": final_flows,
        "substeps": steps,
        "duration_seconds": duration,
        "model": "lumped_rc_network",
    }



def build_multizone_observation_model(
    rooms: dict[str, dict[str, Any]],
    weather: dict[str, Any] | None = None,
    attic: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
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
            "configured_ach": _finite_or_none(room.get("air_changes_per_hour")),
            "co2_ppm": _finite_or_none(room.get("co2_ppm")),
            "humidity_percent": _finite_or_none(room.get("humidity")),
            "thermal_capacity_wh_per_k": None,
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

    observed_rooms = sum(1 for node in nodes.values() if node["kind"] == "room_air" and node["temperature_c"] is not None)
    room_count = len(rooms)
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
        "pending_paths": pending_paths,
        "coverage": {
            "configured_rooms": room_count,
            "rooms_with_temperature": observed_rooms,
            "room_temperature_coverage_percent": round(100 * observed_rooms / room_count) if room_count else None,
            "rooms_with_co2": sum(1 for room in rooms.values() if _finite_or_none(room.get("co2_ppm")) is not None),
            "rooms_with_humidity": sum(1 for room in rooms.values() if _finite_or_none(room.get("humidity")) is not None),
            "rooms_with_ach": sum(1 for room in rooms.values() if _finite_or_none(room.get("air_changes_per_hour")) is not None),
            "attic_temperature_available": attic_c is not None,
            "outdoor_temperature_available": outdoor_c is not None,
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


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
