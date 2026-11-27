"""Experimental gas-calibrated boiler heat allocation (v0.17.1)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Any

DEFAULT_GAS_KWH_PER_M3 = 10.55
DEFAULT_BOILER_EFFICIENCY = 0.90
DEFAULT_BOILER_RATED_OUTPUT_KW = 25.0
DEFAULT_BOILER_MIN_OUTPUT_KW = 3.2
CALIBRATION_OLD_WEIGHT = 0.66
CALIBRATION_DAILY_WEIGHT = 0.34


def _heating_season_key(day: date) -> str:
    """Return the September 1–May 1 heating-season label containing day."""
    start_year = day.year if day.month >= 9 else day.year - 1
    return f"{start_year}-09-01/{start_year + 1}-05-01"




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
    """Estimate output from modulation alone, then calibrate it against heating gas.

    Modulation is the sole live output signal; burner binary state is ignored
    because it can lag modulation transitions. Modulation initially maps
    linearly to the 25 kW rated output, with a 3.2 kW minimum while above zero. Actual
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
        "modulation_generated_heat_kwh": 0.0,
        "calibration_reference_heat_kwh": 0.0,
        "calibration_reference_modulation_kwh": 0.0,
        "modulation_energy_since_meter_kwh": 0.0,
        "calibration_factor": 1.0,
        "last_meter_interval_modulation_kwh": 0.0,
        "daily_modulation_room_energy_kwh": {},
        "modulation_room_energy_kwh": {},
        "daily_modulation_generated_heat_kwh": {},
        "heating_season_modulation_room_energy_kwh": {},
        "heating_season_modulation_energy_kwh": {},
        "last_modulation_room_diagnostics": {},
        "last_modulation_interval_seconds": 0.0,
        "last_modulation_interval_burner_on": None,
        "last_modulation_interval_percent": None,
        "last_modulation_interval_output_kw": None,
    })
    # Backfill defaults when restoring history written by an earlier version.
    # Normalize persisted room-energy ledgers before using them. Older or
    # interrupted writes may contain nested mappings where a numeric room
    # allocation is expected; discard only malformed entries, preserving all
    # valid historical values and the calibration state.
    daily_modulation_rooms = state.get("daily_modulation_room_energy_kwh")
    if not isinstance(daily_modulation_rooms, dict):
        daily_modulation_rooms = {}
        state["daily_modulation_room_energy_kwh"] = daily_modulation_rooms
    for day, room_values in list(daily_modulation_rooms.items()):
        if not isinstance(room_values, dict):
            daily_modulation_rooms.pop(day, None)
            continue
        daily_modulation_rooms[day] = {
            str(room_id): float(value)
            for room_id, value in room_values.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)) and float(value) >= 0
        }

    lifetime_modulation_rooms = state.get("modulation_room_energy_kwh")
    if not isinstance(lifetime_modulation_rooms, dict):
        lifetime_modulation_rooms = {}
        state["modulation_room_energy_kwh"] = lifetime_modulation_rooms
    state["modulation_room_energy_kwh"] = {
        str(room_id): float(value)
        for room_id, value in lifetime_modulation_rooms.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(float(value)) and float(value) >= 0
    }

    season_modulation_rooms = state.get("heating_season_modulation_room_energy_kwh")
    if not isinstance(season_modulation_rooms, dict):
        season_modulation_rooms = {}
        state["heating_season_modulation_room_energy_kwh"] = season_modulation_rooms
    for season, room_values in list(season_modulation_rooms.items()):
        if not isinstance(room_values, dict):
            season_modulation_rooms.pop(season, None)
            continue
        season_modulation_rooms[season] = {
            str(room_id): float(value)
            for room_id, value in room_values.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)) and float(value) >= 0
        }

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
        "heating_season_modulation_room_energy_kwh": {},
        "heating_season_modulation_energy_kwh": {},
        "last_modulation_room_diagnostics": {},
        "last_modulation_interval_seconds": 0.0,
        "last_modulation_interval_burner_on": None,
        "last_modulation_interval_percent": None,
        "last_modulation_interval_output_kw": None,
    }.items():
        state.setdefault(key, default)

    factor = max(0.1, float(gas_kwh_per_m3 or DEFAULT_GAS_KWH_PER_M3))
    efficiency = min(1.0, max(0.5, float(boiler_efficiency or DEFAULT_BOILER_EFFICIENCY)))
    interval = max(0.0, min(float(elapsed_seconds or 0.0), 90.0))
    exposures = state.setdefault("room_exposure_by_date", {})
    burner_seconds = state.setdefault("burner_seconds_by_date", {})
    room_energy = state.setdefault("room_energy_kwh", {})
    daily_rooms = state.setdefault("daily_room_energy_kwh", {})

    # A factor calculated from a completed day is staged for the following
    # calendar day, never applied retroactively to the day being measured.
    pending_effective_date = state.get("pending_calibration_effective_date")
    if pending_effective_date and pending_effective_date <= today:
        state["calibration_factor"] = float(state.get("pending_calibration_factor", state.get("calibration_factor", 1.0)))
        state["calibration_factor_old"] = float(state.get("pending_calibration_factor_old", state.get("calibration_factor", 1.0)))
        state["calibration_factor_observed_daily"] = state.get("pending_calibration_factor_observed")
        state["pending_calibration_factor"] = None
        state["pending_calibration_effective_date"] = None
    calibration = max(0.0, float(state.get("calibration_factor", 1.0)))
    # Modulation is the sole live firing/output signal. Burner status is deliberately
    # excluded because its state transition can lag modulation by ~30 seconds.
    output_kw = _modulation_output_kw(modulation_percent)
    calibrated_output_kw = output_kw * calibration if output_kw is not None else None
    modulation_energy = (
        output_kw * interval / 3600.0
        if output_kw is not None and output_kw > 0 and interval > 0
        else 0.0
    )
    calibrated_modulation_energy = (
        calibrated_output_kw * interval / 3600.0
        if calibrated_output_kw is not None and calibrated_output_kw > 0 and interval > 0
        else 0.0
    )
    # Capture the exact inputs used by modulation allocation on every update.
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
    interval_room_allocations = {
        rid: calibrated_modulation_energy * weight / total_weight
        for rid, weight in room_weights.items()
    } if total_weight > 0 and modulation_energy > 0 else {}
    state["last_modulation_room_diagnostics"] = {
        rid: {
            "flow_active": weight > 0,
            "allocation_weight_l_min": round(weight, 6),
            "allocation_share": round(weight / total_weight, 6) if total_weight > 0 else 0.0,
            "interval_energy_kwh": round(interval_room_allocations.get(rid, 0.0), 8),
            "exposure_kw_seconds": round(weight * (calibrated_output_kw or 0.0) * interval, 4),
        }
        for rid, weight in room_weights.items()
    }
    state["last_modulation_interval_seconds"] = interval
    state["last_modulation_interval_burner_on"] = burner_on
    state["last_modulation_interval_percent"] = modulation_percent
    state["last_modulation_interval_output_kw"] = output_kw
    state["last_modulation_interval_calibrated_output_kw"] = calibrated_output_kw
    if modulation_energy > 0:
        state["modulation_estimated_energy_kwh"] = (
            float(state.get("modulation_estimated_energy_kwh", 0.0)) + modulation_energy
        )
        state["modulation_energy_since_meter_kwh"] = (
            float(state.get("modulation_energy_since_meter_kwh", 0.0)) + modulation_energy
        )
        daily_raw = state.setdefault("daily_modulation_estimated_energy_kwh", {})
        daily_raw[today] = float(daily_raw.get(today, 0.0)) + modulation_energy
        if total_weight > 0:
            daily_generated = state.setdefault("daily_modulation_generated_heat_kwh", {})
            daily_generated[today] = float(daily_generated.get(today, 0.0)) + calibrated_modulation_energy
            state["modulation_generated_heat_kwh"] = float(state.get("modulation_generated_heat_kwh", 0.0)) + calibrated_modulation_energy
            daily_rooms = state.setdefault("daily_modulation_room_energy_kwh", {}).setdefault(today, {})
            lifetime_rooms = state.setdefault("modulation_room_energy_kwh", {})
            season_key = _heating_season_key(now.date())
            season_rooms = state.setdefault("heating_season_modulation_room_energy_kwh", {}).setdefault(season_key, {})
            season_energy = state.setdefault("heating_season_modulation_energy_kwh", {})
            season_energy[season_key] = float(season_energy.get(season_key, 0.0)) + calibrated_modulation_energy
            for rid, weight in room_weights.items():
                allocated = calibrated_modulation_energy * weight / total_weight
                daily_rooms[rid] = float(daily_rooms.get(rid, 0.0)) + allocated
                lifetime_rooms[rid] = float(lifetime_rooms.get(rid, 0.0)) + allocated
                season_rooms[rid] = float(season_rooms.get(rid, 0.0)) + allocated

    if output_kw is not None and output_kw > 0 and interval > 0:
        # Track active modulation time rather than the delayed burner binary.
        burner_seconds[today] = float(burner_seconds.get(today, 0.0)) + interval
        # Modulation-derived output is the exposure weight; no burner-state fallback.
        output_weight = calibrated_output_kw
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
        # Keep cumulative references as diagnostics only; the active factor
        # is updated from complete daily ratios with the 66/34 smoothing rule.
        if interval_modulation_energy > 0:
            state["calibration_reference_heat_kwh"] = (
                float(state.get("calibration_reference_heat_kwh", 0.0)) + useful_heat
            )
            state["calibration_reference_modulation_kwh"] = (
                float(state.get("calibration_reference_modulation_kwh", 0.0))
                + interval_modulation_energy
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
        season_rooms = state.setdefault("heating_season_room_energy_kwh", {})
        season_totals = state.setdefault("heating_season_energy_kwh", {})
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
                    season = _heating_season_key(datetime.fromisoformat(day).date())
                    season_room = season_rooms.setdefault(season, {})
                    season_room[rid] = float(season_room.get(rid, 0.0)) + amount
                    interval_allocations[rid] = interval_allocations.get(rid, 0.0) + amount
                    day_allocated += amount
            day_unallocated = max(0.0, day_heat - day_allocated)
            state.setdefault("daily_generated_heat_kwh", {})[day] = float(state["daily_generated_heat_kwh"].get(day, 0.0)) + day_heat
            state.setdefault("daily_allocated_heat_kwh", {})[day] = float(state["daily_allocated_heat_kwh"].get(day, 0.0)) + day_allocated
            state.setdefault("daily_unallocated_heat_kwh", {})[day] = float(state["daily_unallocated_heat_kwh"].get(day, 0.0)) + day_unallocated
            season = _heating_season_key(datetime.fromisoformat(day).date())
            season_record = season_totals.setdefault(season, {"generated": 0.0, "allocated": 0.0, "unallocated": 0.0})
            season_record["generated"] = float(season_record.get("generated", 0.0)) + day_heat
            season_record["allocated"] = float(season_record.get("allocated", 0.0)) + day_allocated
            season_record["unallocated"] = float(season_record.get("unallocated", 0.0)) + day_unallocated
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

    # Reconcile only a completed calendar day. The gas ledger is independent
    # of modulation; reject low-energy and extreme-ratio days before learning.
    previous_day = (now.date() - timedelta(days=1)).isoformat()
    daily_gas = state.setdefault("daily_generated_heat_kwh", {})
    daily_modulation = state.setdefault("daily_modulation_estimated_energy_kwh", {})
    if previous_day != state.get("last_daily_calibration_day"):
        gas_yesterday = max(0.0, float(daily_gas.get(previous_day, 0.0) or 0.0))
        modulation_yesterday = max(0.0, float(daily_modulation.get(previous_day, 0.0) or 0.0))
        if gas_yesterday > 0 or modulation_yesterday > 0:
            state["last_daily_calibration_day"] = previous_day
        state["calibration_last_daily_gas_kwh"] = gas_yesterday
        state["calibration_last_daily_modulation_kwh"] = modulation_yesterday
        state["calibration_last_daily_status"] = "withheld"
        state["calibration_last_daily_reason"] = "insufficient_complete_day_energy"
        if gas_yesterday >= 0.5 and modulation_yesterday >= 0.5:
            observed = gas_yesterday / modulation_yesterday
            state["calibration_factor_observed_daily"] = observed
            if 0.25 <= observed <= 2.5:
                old_factor = max(0.0, float(state.get("calibration_factor", 1.0)))
                smoothed = CALIBRATION_OLD_WEIGHT * old_factor + CALIBRATION_DAILY_WEIGHT * observed
                state["calibration_factor_old"] = old_factor
                state["pending_calibration_factor_old"] = old_factor
                state["pending_calibration_factor_observed"] = observed
                state["pending_calibration_factor"] = smoothed
                state["pending_calibration_effective_date"] = (now.date() + timedelta(days=1)).isoformat()
                state["calibration_last_daily_status"] = "accepted_pending_next_day"
                state["calibration_last_daily_reason"] = "complete_day_within_energy_and_ratio_limits"
            else:
                state["calibration_last_daily_reason"] = "daily_ratio_outside_0_25_to_2_5"
        elif gas_yesterday < 0.5:
            state["calibration_last_daily_reason"] = "gas_reconciled_energy_below_0_5_kwh"
        else:
            state["calibration_last_daily_reason"] = "modulation_energy_below_0_5_kwh"

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
    daily_generated_heat = state.get("daily_modulation_generated_heat_kwh", {})
    weekly_generated_heat = 0.0
    monthly_generated_heat = 0.0
    for day, generated in daily_generated_heat.items():
        try:
            date = datetime.fromisoformat(day).date()
        except (TypeError, ValueError):
            continue
        if date >= week_start:
            weekly_generated_heat += max(0.0, float(generated or 0.0))
        if date >= month_start:
            monthly_generated_heat += max(0.0, float(generated or 0.0))
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

    # Gas-reconciled room-energy views for rolling periods and the full heating season.
    daily_gas_rooms = state.get("daily_room_energy_kwh", {})
    weekly_gas_rooms: dict[str, float] = {}
    monthly_gas_rooms: dict[str, float] = {}
    weekly_gas_heat = 0.0
    monthly_gas_heat = 0.0
    for day, generated in state.get("daily_generated_heat_kwh", {}).items():
        try:
            day_date = datetime.fromisoformat(day).date()
        except (TypeError, ValueError):
            continue
        if day_date >= week_start:
            weekly_gas_heat += max(0.0, float(generated or 0.0))
        if day_date >= month_start:
            monthly_gas_heat += max(0.0, float(generated or 0.0))
    for day, allocations in daily_gas_rooms.items():
        try:
            day_date = datetime.fromisoformat(day).date()
        except (TypeError, ValueError):
            continue
        if not isinstance(allocations, dict):
            continue
        if day_date >= week_start:
            for rid, value in allocations.items():
                weekly_gas_rooms[rid] = weekly_gas_rooms.get(rid, 0.0) + float(value or 0.0)
        if day_date >= month_start:
            for rid, value in allocations.items():
                monthly_gas_rooms[rid] = monthly_gas_rooms.get(rid, 0.0) + float(value or 0.0)

    last_seconds = float(state.get("last_meter_interval_seconds") or 0.0)
    avg_power = {
        rid: (value * 1000.0 / (last_seconds / 3600.0)) if last_seconds > 0 else 0.0
        for rid, value in state.get("last_room_interval_allocations_kwh", {}).items()
    }
    latest_useful = float(state.get("last_meter_interval_useful_heat_kwh") or 0.0)
    latest_input = float(state.get("last_meter_interval_input_kwh") or 0.0)
    calibration = max(0.0, float(state.get("calibration_factor", 1.0)))
    calibrated_output_kw = output_kw * calibration if output_kw is not None else None
    current_season = _heating_season_key(now.date())
    season_room_totals = state.get("heating_season_room_energy_kwh", {}).get(current_season, {})
    season_total = state.get("heating_season_energy_kwh", {}).get(current_season, {})
    modulation_season_rooms = state.get("heating_season_modulation_room_energy_kwh", {}).get(current_season, {})
    modulation_season_energy = state.get("heating_season_modulation_energy_kwh", {}).get(current_season, 0.0)
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
        "boiler_gas_reconciled_daily_room_energy_kwh": {k: round(float(v), 6) for k, v in daily_rooms.get(today, {}).items() if isinstance(v, (int, float))},
        "boiler_gas_reconciled_weekly_room_energy_kwh": {k: round(v, 6) for k, v in weekly_gas_rooms.items()},
        "boiler_gas_reconciled_monthly_room_energy_kwh": {k: round(v, 6) for k, v in monthly_gas_rooms.items()},
        "boiler_gas_reconciled_heating_season_room_energy_kwh": {k: round(float(v), 6) for k, v in season_room_totals.items()},
        "boiler_gas_reconciled_energy_today_kwh": round(float(state.get("daily_generated_heat_kwh", {}).get(today, 0.0) or 0.0), 4),
        "boiler_gas_reconciled_energy_week_kwh": round(weekly_gas_heat, 4),
        "boiler_gas_reconciled_energy_month_kwh": round(monthly_gas_heat, 4),
        "boiler_gas_reconciled_energy_heating_season_kwh": round(float(season_total.get("generated", 0.0)), 4),
        "boiler_weekly_room_energy_kwh": {k: round(v, 6) for k, v in weekly_gas_rooms.items()},
        "boiler_monthly_room_energy_kwh": {k: round(v, 6) for k, v in monthly_gas_rooms.items()},
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
        "boiler_modulation_energy_today_kwh": round(float(daily_generated_heat.get(today, 0.0) or 0.0), 4),
        "boiler_modulation_energy_week_kwh": round(weekly_generated_heat, 4),
        "boiler_modulation_energy_month_kwh": round(monthly_generated_heat, 4),
        "boiler_modulation_energy_heating_season_kwh": round(float(modulation_season_energy or 0.0), 4),
        "boiler_modulation_energy_total_kwh": round(float(state.get("modulation_generated_heat_kwh", 0.0)), 4),
        "boiler_modulation_room_energy_kwh": {k: round(float(v), 6) for k, v in state.get("modulation_room_energy_kwh", {}).items()},
        "boiler_modulation_room_interval_diagnostics": state.get("last_modulation_room_diagnostics", {}),
        "boiler_modulation_interval_seconds": round(float(state.get("last_modulation_interval_seconds", 0.0)), 2),
        "boiler_modulation_interval_burner_on": state.get("last_modulation_interval_burner_on"),
        "boiler_modulation_interval_percent": state.get("last_modulation_interval_percent"),
        "boiler_modulation_interval_output_kw": state.get("last_modulation_interval_output_kw"),
        "boiler_modulation_interval_calibrated_output_kw": state.get("last_modulation_interval_calibrated_output_kw"),
        "boiler_modulation_daily_room_energy_kwh": {
            k: round(float(v), 6) for k, v in today_modulation_rooms.items()
            if isinstance(v, (int, float))
        },
        "boiler_modulation_weekly_room_energy_kwh": {k: round(float(v), 6) for k, v in weekly_modulation_rooms.items()},
        "boiler_modulation_monthly_room_energy_kwh": {k: round(float(v), 6) for k, v in monthly_modulation_rooms.items()},
        "boiler_modulation_heating_season_room_energy_kwh": {k: round(float(v), 6) for k, v in modulation_season_rooms.items()},
        "boiler_daily_modulation_generated_heat_kwh": state.get("daily_modulation_generated_heat_kwh", {}),
        "boiler_daily_modulation_room_energy_kwh": state.get("daily_modulation_room_energy_kwh", {}),
        "boiler_last_meter_interval_modulation_energy_kwh": round(float(state["last_meter_interval_modulation_kwh"]), 4),
        "boiler_calibration_factor": round(calibration, 5),
        "boiler_calibration_factor_old": round(float(state.get("calibration_factor_old", calibration)), 5),
        "boiler_calibration_factor_observed_daily": (
            round(float(state["calibration_factor_observed_daily"]), 5)
            if state.get("calibration_factor_observed_daily") is not None else None
        ),
        "boiler_calibration_factor_smoothed": round(calibration, 5),
        "boiler_calibration_last_completed_day": state.get("last_daily_calibration_day"),
        "boiler_calibration_daily_status": state.get("calibration_last_daily_status"),
        "boiler_calibration_daily_reason": state.get("calibration_last_daily_reason"),
        "boiler_calibration_daily_gas_kwh": round(float(state.get("calibration_last_daily_gas_kwh", 0.0)), 4),
        "boiler_calibration_daily_modulation_kwh": round(float(state.get("calibration_last_daily_modulation_kwh", 0.0)), 4),
        "boiler_calibration_daily_ratio": (round(float(state["calibration_factor_observed_daily"]), 5) if state.get("calibration_factor_observed_daily") is not None else None),
        "boiler_heating_season": current_season,
        "boiler_heating_season_room_energy_kwh": {
            k: round(float(v), 6) for k, v in season_room_totals.items()
        },
        "boiler_heating_season_generated_heat_kwh": round(float(season_total.get("generated", 0.0)), 4),
        "boiler_heating_season_allocated_heat_kwh": round(float(season_total.get("allocated", 0.0)), 4),
        "boiler_heating_season_unallocated_heat_kwh": round(float(season_total.get("unallocated", 0.0)), 4),
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
