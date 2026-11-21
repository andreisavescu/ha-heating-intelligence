"""Pure helpers for timed UFH circuit activation and hydraulic estimates."""
from __future__ import annotations

from datetime import datetime
from typing import Any

FLOW_START_POWER_THRESHOLD_W = 7.0
FLOW_START_DELAY_SECONDS = 180
WATER_DELTA_T_C = 9.0
WATER_DENSITY_KG_L = 1.0
WATER_HEAT_CAPACITY_J_KG_K = 4186.0


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
    circuit_state: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Apply the 7 W + 3 minute start rule and estimate heat transfer at ΔT=9°C."""
    circuit_results: dict[str, dict[str, Any]] = {}
    pump_totals: dict[str, float] = {}
    total_thermal_power_w = 0.0
    now_ts = now.timestamp()

    for circuit in circuits:
        circuit_id = str(circuit.get("id", ""))
        if not circuit_id:
            continue
        demand_scope = circuit.get("demand_scope", "selected_rooms")
        if demand_scope == "floor_any":
            floor_id = circuit.get("floor", "unassigned")
            demand_room_ids = [
                room_id for room_id, room_data in rooms.items()
                if room_data.get("floor", "unassigned") == floor_id
            ]
        else:
            demand_room_ids = circuit.get("demand_rooms", [])
        demand_values = []
        for room_id in demand_room_ids:
            room = rooms.get(room_id)
            state = room.get("demand_state") if room else None
            if state in (None, "unavailable", "unknown"):
                demand_values.append(None)
            else:
                demand_values.append(state == "on" and bool(room.get("demand", False)))

        demand_active = evaluate_activation(demand_values, circuit.get("demand_logic", "or"))
        pump_id = circuit.get("pump_id")
        pump = pumps.get(pump_id) if pump_id else None
        pump_power = pump.get("power") if pump else None
        try:
            pump_power = float(pump_power) if pump_power is not None else None
        except (TypeError, ValueError):
            pump_power = None
        pump_running_long_enough = False
        state = circuit_state.setdefault(circuit_id, {})
        pending_since = state.get("pump_above_threshold_since")
        flow_active = bool(state.get("flow_active", False))

        # The three-minute timer belongs exclusively to pump power. It starts
        # as soon as the pump exceeds 7 W, regardless of relay/Wi-Fi demand delay.
        if pump_power is None or pump_power <= FLOW_START_POWER_THRESHOLD_W:
            state["pump_above_threshold_since"] = None
            state["flow_active"] = False
            flow_active = False
        else:
            if pending_since is None:
                state["pump_above_threshold_since"] = now_ts
                pending_since = now_ts
            pump_running_long_enough = (
                now_ts - float(pending_since) >= FLOW_START_DELAY_SECONDS
            )
            flow_active = demand_active is True and pump_running_long_enough
            state["flow_active"] = flow_active


        configured_flow = round(max(0.0, min(6.0, float(flow_rates.get(circuit_id, 0.0)))), 2)
        active_flow = configured_flow if flow_active else 0.0
        thermal_power_w = (
            active_flow / 60.0 * WATER_DENSITY_KG_L
            * WATER_HEAT_CAPACITY_J_KG_K * WATER_DELTA_T_C
        )
        thermal_power_w = round(thermal_power_w, 1)
        total_thermal_power_w += thermal_power_w
        if flow_active and pump_id:
            pump_totals[pump_id] = pump_totals.get(pump_id, 0.0) + active_flow

        circuit_results[circuit_id] = {
            "name": circuit.get("name", circuit_id),
            "room_id": circuit.get("room_id"),
            "pipe_length_m": circuit.get("pipe_length_m"),
            "pump_id": pump_id,
            "demand_rooms": list(demand_room_ids),
            "demand_scope": demand_scope,
            "demand_floor": circuit.get("floor", "unassigned"),
            "demand_logic": circuit.get("demand_logic", "or"),
            "demand_active": demand_active,
            "pump_power_w": pump_power,
            "pump_start_threshold_w": FLOW_START_POWER_THRESHOLD_W,
            "pump_confirmation_delay_seconds": FLOW_START_DELAY_SECONDS,
            "pump_above_threshold_seconds": round(max(0.0, now_ts - float(pending_since))) if pending_since is not None and pump_power is not None and pump_power > FLOW_START_POWER_THRESHOLD_W else 0,
            "flow_active": flow_active,
            "circulation_confirmed": flow_active,
            "configured_flow_l_min": configured_flow,
            "active_configured_flow_l_min": active_flow,
            "assumed_water_delta_t_c": WATER_DELTA_T_C,
            "estimated_thermal_power_w": thermal_power_w,
            "flow_is_manual_input": True,
        }

    return {
        "circuits": circuit_results,
        "pump_configured_flow_totals_l_min": {
            pump_id: round(value, 2) for pump_id, value in sorted(pump_totals.items())
        },
        "active_estimated_thermal_power_w": round(total_thermal_power_w, 1),
        "assumed_water_delta_t_c": WATER_DELTA_T_C,
        "flow_semantics": (
            "The 180-second timer starts when associated pump power first stays above "
            "7 W, independently of demand relay availability. Flow activates once that "
            "timer has elapsed and the configured demand is on. Demand may be a selected "
            "room set or any room on the circuit floor. It stops immediately when either "
            "condition is no longer true. Thermal power is estimated from manual flow "
            "and an assumed 9°C water temperature drop; it is not measured."
        ),
    }
