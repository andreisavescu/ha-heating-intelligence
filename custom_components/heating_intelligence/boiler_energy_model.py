"""Experimental gas-calibrated boiler heat allocation (v0.17.1)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

DEFAULT_GAS_KWH_PER_M3 = 10.55
DEFAULT_BOILER_EFFICIENCY = 0.90
DEFAULT_BOILER_RATED_OUTPUT_KW = 25.0
DEFAULT_BOILER_MIN_OUTPUT_KW = 3.2


def _modulation_output_kw(modulation_percent: float | None) -> float | None:
    """Convert the boiler modulation percentage to an initial heat-output estimate."""
    if modulation_percent is None:
        return None
    modulation = min(100.0, max(0.0, float(modulation_percent)))
    if modulation <= 0:
        return 0.0
    return max(
        DEFAULT_BOILER_MIN_OUTPUT_KW,
        DEFAULT_BOILER_RATED_OUTPUT_KW * modulation / 100.0,
    )


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
    now: datetime | None = None,
) -> dict[str, Any]:
    """Estimate output from modulation, then calibrate it against heating gas.

    Modulation initially maps linearly to the 25 kW rated output, with a
    3.2 kW minimum firing output while modulation is above zero. Actual
    heating-gas counter deltas provide interval energy references. The
    cumulative ratio of gas-derived useful heat to modulation-estimated heat
    becomes the calibration factor. Room allocation continues to reconcile
    actual gas-derived useful heat, not an unverified instantaneous estimate.
    """
    now = now or datetime.now().astimezone()
    today = now.date().isoformat()
    state = history.setdefault("boiler_energy_experiment", {
        "last_heating_gas_m3": None, "last_dhw_gas_m3": None,
        "room_exposure_by_date": {}, "burner_seconds_by_date": {},
        "room_energy_kwh": {}, "daily_room_energy_kwh": {},
        "daily_generated_heat_kwh": {}, "daily_allocated_heat_kwh": {},
        "daily_unallocated_heat_kwh": {}, "generated_heat_kwh": 0.0,
        "allocated_heat_kwh": 0.0, "unallocated_heat_kwh": 0.0,
        "dhw_input_kwh": 0.0, "heating_input_kwh": 0.0,
        "meter_reset_count": 0, "meter_sync_count": 0,
        "last_meter_sync": None, "last_meter_interval_input_kwh": 0.0,
        "last_meter_interval_useful_heat_kwh": 0.0,
        "last_meter_interval_seconds": 0.0,
        "last_room_interval_allocations_kwh": {},
        "modulation_estimated_energy_kwh": 0.0,
        "calibration_reference_heat_kwh": 0.0,
        "calibration_reference_modulation_kwh": 0.0,
        "modulation_energy_since_meter_kwh": 0.0,
        "calibration_factor": 1.0,
        "last_meter_interval_modulation_kwh": 0.0,
        "daily_modulation_room_energy_kwh": {},
        "modulation_room_energy_kwh": {},
        "daily_modulation_generated_heat_kwh": {},
    })
    # Backfill defaults when restoring history written by an earlier version.
    for key, default in {
        "modulation_estimated_energy_kwh": 0.0,
        "calibration_reference_heat_kwh": 0.0,
        "calibration_reference_modulation_kwh": 0.0,
        "modulation_energy_since_meter_kwh": 0.0,
        "calibration_factor": 1.0,
        "last_meter_interval_modulation_kwh": 0.0,
        "daily_modulation_room_energy_kwh": {},
        "modulation_room_energy_kwh": {},
        "daily_modulation_generated_heat_kwh": {},
    }.items():
        state.setdefault(key, default)

    factor = max(0.1, float(gas_kwh_per_m3 or DEFAULT_GAS_KWH_PER_M3))
    efficiency = min(1.0, max(0.5, float(boiler_efficiency or DEFAULT_BOILER_EFFICIENCY)))
    interval = max(0.0, min(float(elapsed_seconds or 0.0), 90.0))
    exposures = state.setdefault("room_exposure_by_date", {})
    burner_seconds = state.setdefault("burner_seconds_by_date", {})
    room_energy = state.setdefault("room_energy_kwh", {})
    daily_rooms = state.setdefault("daily_room_energy_kwh", {})

    output_kw = _modulation_output_kw(modulation_percent) if burner_on is True else 0.0
    modulation_energy = (
        output_kw * interval / 3600.0
        if burner_on is True and output_kw is not None and interval > 0
        else 0.0
    )
    if modulation_energy > 0:
        state["modulation_estimated_energy_kwh"] = (
            float(state.get("modulation_estimated_energy_kwh", 0.0)) + modulation_energy
        )
        state["modulation_energy_since_meter_kwh"] = (
            float(state.get("modulation_energy_since_meter_kwh", 0.0)) + modulation_energy
        )
        room_weights: dict[str, float] = {}
        for circuit in circuits.values():
            if not circuit.get("flow_active"):
                continue
            flow = max(0.0, float(circuit.get("active_configured_flow_l_min") or circuit.get("configured_flow_l_min") or 0.0))
            served = circuit.get("served_rooms") or circuit.get("demand_rooms") or []
            eligible = [rid for rid in served if rid in rooms]
            if flow <= 0 or not eligible:
                continue
            for rid in eligible:
                room_weights[rid] = room_weights.get(rid, 0.0) + flow / len(eligible)
        total_weight = sum(room_weights.values())
        if total_weight > 0:
            daily_generated = state.setdefault("daily_modulation_generated_heat_kwh", {})
            daily_generated[today] = float(daily_generated.get(today, 0.0)) + modulation_energy
            daily_rooms = state.setdefault("daily_modulation_room_energy_kwh", {}).setdefault(today, {})
            lifetime_rooms = state.setdefault("modulation_room_energy_kwh", {})
            for rid, weight in room_weights.items():
                allocated = modulation_energy * weight / total_weight
                daily_rooms[rid] = float(daily_rooms.get(rid, 0.0)) + allocated
                lifetime_rooms[rid] = float(lifetime_rooms.get(rid, 0.0)) + allocated

    if burner_on is True and interval > 0:
        burner_seconds[today] = float(burner_seconds.get(today, 0.0)) + interval
        # Modulation-derived output is the exposure weight. If modulation is
        # temporarily unavailable, retain the previous neutral weighting.
        output_weight = output_kw if output_kw is not None else DEFAULT_BOILER_RATED_OUTPUT_KW
        room_weights: dict[str, float] = {}
        for circuit in circuits.values():
            if not circuit.get("flow_active"):
                continue
            flow = max(0.0, float(circuit.get("configured_flow_l_min") or circuit.get("active_configured_flow_l_min") or 0.0))
            served = circuit.get("served_rooms") or circuit.get("demand_rooms") or []
            eligible = [rid for rid in served if rid in rooms and rooms[rid].get("demand") is True]
            if flow <= 0 or not eligible:
                continue
            for rid in eligible:
                room_weights[rid] = room_weights.get(rid, 0.0) + flow / len(eligible)
        day_exposure = exposures.setdefault(today, {})
        for rid, weight in room_weights.items():
            day_exposure[rid] = float(day_exposure.get(rid, 0.0)) + weight * output_weight * interval

    def meter_delta(key: str, current: float | None) -> tuple[float, bool]:
        previous = state.get(key)
        if current is None:
            return 0.0, False
        value = max(0.0, float(current))
        state[key] = value
        if previous is None:
            if key == "last_heating_gas_m3":
                # Establish a clean modulation baseline at the first valid
                # cumulative gas reading; pre-baseline runtime has no matching
                # gas interval and must not influence calibration.
                state["modulation_energy_since_meter_kwh"] = 0.0
            return 0.0, False
        if value < float(previous):
            state["meter_reset_count"] = int(state.get("meter_reset_count", 0)) + 1
            return value, True
        return value - float(previous), False

    heating_delta, heating_reset = meter_delta("last_heating_gas_m3", heating_gas_m3)
    dhw_delta, dhw_reset = meter_delta("last_dhw_gas_m3", dhw_gas_m3)
    heating_input = heating_delta * factor
    dhw_input = dhw_delta * factor
    useful_heat = heating_input * efficiency
    state["heating_input_kwh"] = float(state.get("heating_input_kwh", 0.0)) + heating_input
    state["dhw_input_kwh"] = float(state.get("dhw_input_kwh", 0.0)) + dhw_input
    state["generated_heat_kwh"] = float(state.get("generated_heat_kwh", 0.0)) + useful_heat

    interval_allocations: dict[str, float] = {}
    interval_allocated = 0.0
    interval_unallocated = 0.0
    interval_seconds = sum(max(0.0, float(v)) for v in burner_seconds.values())
    interval_modulation_energy = float(state.get("modulation_energy_since_meter_kwh", 0.0))
    if heating_delta > 0:
        # Learn a stable cumulative calibration ratio instead of reacting to
        # each coarse (and variably sized) meter increment independently.
        if interval_modulation_energy > 0:
            state["calibration_reference_heat_kwh"] = (
                float(state.get("calibration_reference_heat_kwh", 0.0)) + useful_heat
            )
            state["calibration_reference_modulation_kwh"] = (
                float(state.get("calibration_reference_modulation_kwh", 0.0))
                + interval_modulation_energy
            )
            reference_modulation = float(state["calibration_reference_modulation_kwh"])
            if reference_modulation > 0:
                state["calibration_factor"] = (
                    float(state["calibration_reference_heat_kwh"]) / reference_modulation
                )
        state["last_meter_interval_modulation_kwh"] = interval_modulation_energy
        state["modulation_energy_since_meter_kwh"] = 0.0

        total_burner_seconds = sum(max(0.0, float(v)) for v in burner_seconds.values())
        date_heat: dict[str, float] = {}
        if total_burner_seconds > 0:
            for day, seconds in burner_seconds.items():
                date_heat[day] = useful_heat * max(0.0, float(seconds)) / total_burner_seconds
        else:
            date_heat[today] = useful_heat
        for day, day_heat in date_heat.items():
            day_exposure = exposures.get(day, {})
            total_exposure = sum(max(0.0, float(v)) for v in day_exposure.values())
            day_allocated = 0.0
            if day_heat > 0 and total_exposure > 0:
                daily = daily_rooms.setdefault(day, {})
                for rid, exposure in day_exposure.items():
                    amount = day_heat * max(0.0, float(exposure)) / total_exposure
                    if amount <= 0:
                        continue
                    room_energy[rid] = float(room_energy.get(rid, 0.0)) + amount
                    daily[rid] = float(daily.get(rid, 0.0)) + amount
                    interval_allocations[rid] = interval_allocations.get(rid, 0.0) + amount
                    day_allocated += amount
            day_unallocated = max(0.0, day_heat - day_allocated)
            state.setdefault("daily_generated_heat_kwh", {})[day] = float(state["daily_generated_heat_kwh"].get(day, 0.0)) + day_heat
            state.setdefault("daily_allocated_heat_kwh", {})[day] = float(state["daily_allocated_heat_kwh"].get(day, 0.0)) + day_allocated
            state.setdefault("daily_unallocated_heat_kwh", {})[day] = float(state["daily_unallocated_heat_kwh"].get(day, 0.0)) + day_unallocated
            interval_allocated += day_allocated
            interval_unallocated += day_unallocated
        state["allocated_heat_kwh"] = float(state.get("allocated_heat_kwh", 0.0)) + interval_allocated
        state["unallocated_heat_kwh"] = float(state.get("unallocated_heat_kwh", 0.0)) + interval_unallocated
        state["room_exposure_by_date"] = {}
        state["burner_seconds_by_date"] = {}
        state["meter_sync_count"] = int(state.get("meter_sync_count", 0)) + 1
        state["last_meter_sync"] = now.isoformat()
        state["last_meter_interval_input_kwh"] = heating_input
        state["last_meter_interval_useful_heat_kwh"] = useful_heat
        state["last_meter_interval_seconds"] = interval_seconds
        state["last_room_interval_allocations_kwh"] = interval_allocations

    for key in ("daily_room_energy_kwh", "daily_generated_heat_kwh", "daily_allocated_heat_kwh", "daily_unallocated_heat_kwh", "daily_modulation_room_energy_kwh", "daily_modulation_generated_heat_kwh"):
        mapping = state.setdefault(key, {})
        for day in list(mapping):
            try:
                if (now.date() - datetime.fromisoformat(day).date()).days > 30:
                    del mapping[day]
            except (TypeError, ValueError):
                del mapping[day]

    # Calendar-period totals are derived from the retained daily ledger.
    daily_modulation_rooms = state.get("daily_modulation_room_energy_kwh", {})
    week_start = now.date().fromordinal(now.date().toordinal() - now.date().weekday())
    month_start = now.date().replace(day=1)
    weekly_modulation_rooms: dict[str, float] = {}
    monthly_modulation_rooms: dict[str, float] = {}
    today_modulation_rooms = daily_modulation_rooms.get(today, {})
    for day, allocations in daily_modulation_rooms.items():
        try:
            date = datetime.fromisoformat(day).date()
        except (TypeError, ValueError):
            continue
        if not isinstance(allocations, dict):
            continue
        if date >= week_start:
            for rid, value in allocations.items():
                if isinstance(value, (int, float)):
                    weekly_modulation_rooms[rid] = weekly_modulation_rooms.get(rid, 0.0) + float(value)
        if date >= month_start:
            for rid, value in allocations.items():
                if isinstance(value, (int, float)):
                    monthly_modulation_rooms[rid] = monthly_modulation_rooms.get(rid, 0.0) + float(value)

    last_seconds = float(state.get("last_meter_interval_seconds") or 0.0)
    avg_power = {
        rid: (value * 1000.0 / (last_seconds / 3600.0)) if last_seconds > 0 else 0.0
        for rid, value in state.get("last_room_interval_allocations_kwh", {}).items()
    }
    latest_useful = float(state.get("last_meter_interval_useful_heat_kwh") or 0.0)
    latest_input = float(state.get("last_meter_interval_input_kwh") or 0.0)
    calibration = max(0.0, float(state.get("calibration_factor", 1.0)))
    calibrated_output_kw = output_kw * calibration if output_kw is not None else None
    return {
        "heating_input_energy_kwh": round(state["heating_input_kwh"], 4),
        "dhw_input_energy_kwh": round(state["dhw_input_kwh"], 4),
        "boiler_generated_heat_kwh": round(state["generated_heat_kwh"], 4),
        "boiler_allocated_heat_kwh": round(state["allocated_heat_kwh"], 4),
        "boiler_unallocated_heat_kwh": round(state["unallocated_heat_kwh"], 4),
        "boiler_meter_interval_input_kwh": round(heating_input, 4),
        "boiler_meter_interval_useful_heat_kwh": round(useful_heat, 4),
        "boiler_room_allocations_kwh": {k: round(v, 6) for k, v in interval_allocations.items()},
        "boiler_room_interval_average_power_w": {k: round(v, 2) for k, v in avg_power.items()},
        "boiler_room_energy_kwh": {k: round(float(v), 6) for k, v in room_energy.items()},
        "boiler_daily_room_energy_kwh": daily_rooms,
        "boiler_daily_generated_heat_kwh": state.get("daily_generated_heat_kwh", {}),
        "boiler_daily_allocated_heat_kwh": state.get("daily_allocated_heat_kwh", {}),
        "boiler_daily_unallocated_heat_kwh": state.get("daily_unallocated_heat_kwh", {}),
        "boiler_allocation_exposure": round(sum(sum(v.values()) for v in exposures.values()), 3),
        "boiler_allocation_status": (
            "allocated" if interval_allocated and not interval_unallocated else
            "partially_unallocated" if interval_allocated else
            "unallocated_no_confirmed_room_exposure" if useful_heat else
            "waiting_for_heating_gas_meter"
        ),
        "boiler_meter_sync_count": int(state.get("meter_sync_count", 0)),
        "boiler_last_meter_sync": state.get("last_meter_sync"),
        "boiler_meter_interval_seconds": round(last_seconds, 1),
        "boiler_meter_reset_detected": heating_reset or dhw_reset,
        "boiler_meter_reset_count": int(state.get("meter_reset_count", 0)),
        "boiler_efficiency_assumption": efficiency,
        "boiler_gas_energy_factor_kwh_m3": factor,
        "boiler_rated_output_kw": DEFAULT_BOILER_RATED_OUTPUT_KW,
        "boiler_minimum_output_kw": DEFAULT_BOILER_MIN_OUTPUT_KW,
        "boiler_modulation_percent": round(float(modulation_percent), 2) if modulation_percent is not None else None,
        "boiler_modulation_estimated_output_kw": round(output_kw, 3) if output_kw is not None else None,
        "boiler_modulation_calibrated_output_kw": round(calibrated_output_kw, 3) if calibrated_output_kw is not None else None,
        "boiler_modulation_estimated_energy_kwh": round(float(state["modulation_estimated_energy_kwh"]), 4),
        "boiler_modulation_room_energy_kwh": {k: round(float(v), 6) for k, v in state.get("modulation_room_energy_kwh", {}).items()},
        "boiler_modulation_daily_room_energy_kwh": {
            k: round(float(v), 6) for k, v in today_modulation_rooms.items()
            if isinstance(v, (int, float))
        },
        "boiler_modulation_weekly_room_energy_kwh": {k: round(float(v), 6) for k, v in weekly_modulation_rooms.items()},
        "boiler_modulation_monthly_room_energy_kwh": {k: round(float(v), 6) for k, v in monthly_modulation_rooms.items()},
        "boiler_daily_modulation_generated_heat_kwh": state.get("daily_modulation_generated_heat_kwh", {}),
        "boiler_daily_modulation_room_energy_kwh": state.get("daily_modulation_room_energy_kwh", {}),
        "boiler_last_meter_interval_modulation_energy_kwh": round(float(state["last_meter_interval_modulation_kwh"]), 4),
        "boiler_calibration_factor": round(calibration, 5),
        "boiler_calibration_reference_heat_kwh": round(float(state["calibration_reference_heat_kwh"]), 4),
        "boiler_calibration_reference_modulation_kwh": round(float(state["calibration_reference_modulation_kwh"]), 4),
        "boiler_modulation_calibration_status": (
            "calibrated" if float(state["calibration_reference_modulation_kwh"]) > 0 else "initial_25kw_estimate"
        ),
        "boiler_modulation_used_as_relative_weight": False,
        "boiler_energy_model_note": (
            "Modulation initially estimates output as 25 kW × modulation %, with a 3.2 kW minimum "
            "while firing. A cumulative calibration factor is learned from heating-gas-derived useful "
            "heat versus modulation-estimated energy. Gas-meter deltas remain the energy reference for "
            "room allocation; DHW is excluded. Modulation calibration is approximate when heating and "
            "DHW burner operation overlap within one meter interval."
        ),
    }
