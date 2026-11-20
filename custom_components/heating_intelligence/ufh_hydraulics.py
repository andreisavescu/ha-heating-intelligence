"""Pure helpers for UFH circuit activation and hydraulic summaries."""
from __future__ import annotations

from typing import Any


def evaluate_activation(demand_values: list[bool | None], logic: str = "or") -> bool | None:
    """Evaluate configured room demands, preserving unknown source states."""
    if not demand_values:
        return None
    if logic == "and":
        if False in demand_values:
            return False
        return None if None in demand_values else True
    if True in demand_values:
        return True
    return None if None in demand_values else False


def build_ufh_hydraulic_snapshot(
    circuits: list[dict[str, Any]],
    rooms: dict[str, dict[str, Any]],
    pumps: dict[str, dict[str, Any]],
    flow_rates: dict[str, float],
) -> dict[str, Any]:
    """Combine room demand, configured pump state and manual circuit flow."""
    circuit_results: dict[str, dict[str, Any]] = {}
    pump_totals: dict[str, float] = {}

    for circuit in circuits:
        circuit_id = str(circuit.get("id", ""))
        if not circuit_id:
            continue
        demand_room_ids = circuit.get("demand_rooms", [])
        demand_values = []
        for room_id in demand_room_ids:
            room = rooms.get(room_id)
            if room is None or not room.get("demand_state_known", False):
                demand_values.append(None)
            else:
                demand_values.append(bool(room.get("demand", False)))

        logic = circuit.get("demand_logic", "or")
        demand_active = evaluate_activation(demand_values, logic)
        pump_id = circuit.get("pump_id")
        pump = pumps.get(pump_id) if pump_id else None
        pump_running = None if pump is None or pump.get("power") is None else bool(pump.get("running"))
        configured_flow = round(max(0.0, min(6.0, float(flow_rates.get(circuit_id, 0.0)))), 2)
        circulation_confirmed = demand_active is True and pump_running is True
        active_flow = configured_flow if circulation_confirmed else 0.0
        if circulation_confirmed and pump_id:
            pump_totals[pump_id] = pump_totals.get(pump_id, 0.0) + active_flow

        circuit_results[circuit_id] = {
            "name": circuit.get("name", circuit_id),
            "room_id": circuit.get("room_id"),
            "pipe_length_m": circuit.get("pipe_length_m"),
            "pump_id": pump_id,
            "demand_rooms": list(demand_room_ids),
            "demand_logic": logic,
            "demand_active": demand_active,
            "pump_running": pump_running,
            "circulation_confirmed": circulation_confirmed,
            "configured_flow_l_min": configured_flow,
            "active_configured_flow_l_min": active_flow,
            "flow_is_manual_input": True,
        }

    return {
        "circuits": circuit_results,
        "pump_configured_flow_totals_l_min": {
            pump_id: round(value, 2) for pump_id, value in sorted(pump_totals.items())
        },
        "flow_semantics": (
            "Flow values are manually entered flowmeter readings. Active flow totals "
            "are conditional estimates based on configured room-demand logic and pump "
            "power state; they are not independently measured per-circuit flow."
        ),
    }
