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
