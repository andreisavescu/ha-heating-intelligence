"""Experimental gas-calibrated boiler heat allocation (v0.17)."""
from __future__ import annotations

from typing import Any

DEFAULT_GAS_KWH_PER_M3 = 10.55
DEFAULT_BOILER_EFFICIENCY = 0.90
METER_RESOLUTION_M3 = 0.1


def update_boiler_energy_model(
    history: dict[str, Any],
    heating_gas_m3: float | None,
    dhw_gas_m3: float | None,
    burner_on: bool | None,
    modulation_percent: float | None,
    circuits: dict[str, dict[str, Any]],
    rooms: dict[str, dict[str, Any]],
    elapsed_seconds: float,
    gas_kwh_per_m3: float = DEFAULT_GAS_KWH_PER_M3,
    boiler_efficiency: float = DEFAULT_BOILER_EFFICIENCY,
) -> dict[str, Any]:
    """Accumulate burner-on room exposure, then reconcile it to heating gas.

    Gas meters update infrequently, so energy is allocated over the exposure
    accumulated since the previous meter increment, not at the instant of the
    coordinator refresh. Modulation is a relative weight only; it is not
    interpreted as a linear measurement of boiler output.
    """
    state = history.setdefault("boiler_energy_experiment", {
        "last_heating_gas_m3": None,
        "last_dhw_gas_m3": None,
        "room_exposure": {},
        "room_energy_kwh": {},
        "generated_heat_kwh": 0.0,
        "allocated_heat_kwh": 0.0,
        "unallocated_heat_kwh": 0.0,
        "dhw_input_kwh": 0.0,
        "heating_input_kwh": 0.0,
        "meter_reset_count": 0,
    })
    factor = max(0.1, float(gas_kwh_per_m3 or DEFAULT_GAS_KWH_PER_M3))
    efficiency = min(1.0, max(0.5, float(boiler_efficiency or DEFAULT_BOILER_EFFICIENCY)))
    interval = min(90.0, max(0.0, float(elapsed_seconds or 0.0)))
    exposure = state.setdefault("room_exposure", {})
    room_energy = state.setdefault("room_energy_kwh", {})

    # Exposure only accrues during confirmed burner operation. Pump overrun
    # with burner off therefore contributes no newly generated boiler energy.
    if burner_on is True and interval > 0:
        modulation_weight = (
            min(100.0, max(0.0, float(modulation_percent))) / 100.0
            if modulation_percent is not None else 1.0
        )
        room_weights: dict[str, float] = {}
        for circuit in circuits.values():
            if not circuit.get("flow_active"):
                continue
            flow = max(0.0, float(circuit.get("configured_flow_l_min") or 0.0))
            served = circuit.get("served_rooms") or circuit.get("demand_rooms") or []
            eligible = [
                room_id for room_id in served
                if room_id in rooms and rooms[room_id].get("demand") is True
            ]
            if not eligible or flow <= 0:
                continue
            # Shared circuit flow is divided among only the served rooms that
            # are actually requesting heat at this time.
            share = flow / len(eligible)
            for room_id in eligible:
                room_weights[room_id] = room_weights.get(room_id, 0.0) + share
        for room_id, flow_weight in room_weights.items():
            exposure[room_id] = float(exposure.get(room_id, 0.0)) + flow_weight * modulation_weight * interval

    def meter_delta(key: str, current: float | None) -> tuple[float, bool]:
        previous = state.get(key)
        if current is None:
            return 0.0, False
        value = max(0.0, float(current))
        state[key] = value
        if previous is None:
            return 0.0, False
        if value < float(previous):
            # Monthly cumulative meters reset to a lower value. Count the
            # post-reset register as new usage, never emit negative energy.
            state["meter_reset_count"] = int(state.get("meter_reset_count", 0)) + 1
            return value, True
        return value - float(previous), False

    heating_delta_m3, heating_reset = meter_delta("last_heating_gas_m3", heating_gas_m3)
    dhw_delta_m3, dhw_reset = meter_delta("last_dhw_gas_m3", dhw_gas_m3)
    heating_input = heating_delta_m3 * factor
    dhw_input = dhw_delta_m3 * factor
    useful_heat = heating_input * efficiency
    state["heating_input_kwh"] = float(state.get("heating_input_kwh", 0.0)) + heating_input
    state["dhw_input_kwh"] = float(state.get("dhw_input_kwh", 0.0)) + dhw_input
    state["generated_heat_kwh"] = float(state.get("generated_heat_kwh", 0.0)) + useful_heat

    total_exposure = sum(max(0.0, float(value)) for value in exposure.values())
    allocated = 0.0
    allocations: dict[str, float] = {}
    if useful_heat > 0 and total_exposure > 0:
        for room_id, room_exposure in exposure.items():
            share = useful_heat * max(0.0, float(room_exposure)) / total_exposure
            if share <= 0:
                continue
            room_energy[room_id] = float(room_energy.get(room_id, 0.0)) + share
            allocations[room_id] = share
            allocated += share
    unallocated = max(0.0, useful_heat - allocated)
    state["allocated_heat_kwh"] = float(state.get("allocated_heat_kwh", 0.0)) + allocated
    state["unallocated_heat_kwh"] = float(state.get("unallocated_heat_kwh", 0.0)) + unallocated

    # Exposure describes only the interval just reconciled to the meter.
    if heating_delta_m3 > 0:
        exposure.clear()

    return {
        "heating_input_energy_kwh": round(state["heating_input_kwh"], 4),
        "dhw_input_energy_kwh": round(state["dhw_input_kwh"], 4),
        "boiler_generated_heat_kwh": round(state["generated_heat_kwh"], 4),
        "boiler_allocated_heat_kwh": round(state["allocated_heat_kwh"], 4),
        "boiler_unallocated_heat_kwh": round(state["unallocated_heat_kwh"], 4),
        "boiler_meter_interval_input_kwh": round(heating_input, 4),
        "boiler_meter_interval_useful_heat_kwh": round(useful_heat, 4),
        "boiler_room_allocations_kwh": {key: round(value, 6) for key, value in allocations.items()},
        "boiler_room_energy_kwh": {key: round(float(value), 6) for key, value in room_energy.items()},
        "boiler_allocation_exposure": round(total_exposure, 3),
        "boiler_allocation_status": (
            "allocated" if allocated and not unallocated else
            "partially_unallocated" if allocated else
            "unallocated_no_confirmed_room_exposure" if useful_heat else
            "waiting_for_heating_gas_meter"
        ),
        "boiler_meter_resolution_kwh": round(METER_RESOLUTION_M3 * factor, 4),
        "boiler_meter_reset_detected": heating_reset or dhw_reset,
        "boiler_meter_reset_count": int(state.get("meter_reset_count", 0)),
        "boiler_efficiency_assumption": efficiency,
        "boiler_gas_energy_factor_kwh_m3": factor,
        "boiler_modulation_used_as_relative_weight": modulation_percent is not None,
        "boiler_energy_model_note": (
            "Experimental gas-calibrated estimate. Gas energy is reconciled only "
            "when the cumulative heating meter changes; 0.1 m³ meter resolution "
            "limits precision. Modulation weights burner-on room exposure but is "
            "not treated as a linear boiler-output measurement. DHW gas is excluded "
            "from room allocation. No new heat is generated during burner-off pump overrun."
        ),
    }
