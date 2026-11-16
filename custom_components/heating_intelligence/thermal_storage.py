"""Observed thermal-storage response diagnostics for the multi-zone model.

This module identifies clean passive/coasting periods from Recorder-aligned
samples. It reports room-air response only; it never fabricates a slab
temperature or converts electrical pump/AC power into delivered heat.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def _is_off(value: Any, threshold: float) -> bool | None:
    if value is None:
        return None
    numeric = _finite(value)
    if numeric is not None:
        return numeric <= threshold
    return str(value).strip().lower() in ("off", "false", "no", "0", "idle", "standby")


def _linear_rate(points: list[tuple[datetime, float]]) -> float | None:
    if len(points) < 3:
        return None
    x = [(stamp - points[0][0]).total_seconds() / 3600 for stamp, _ in points]
    y = [value for _, value in points]
    mean_x, mean_y = sum(x) / len(x), sum(y) / len(y)
    denominator = sum((value - mean_x) ** 2 for value in x)
    if denominator <= 0:
        return None
    return sum((xv - mean_x) * (yv - mean_y) for xv, yv in zip(x, y)) / denominator


def build_thermal_storage_response_analysis(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    pumps: list[dict[str, Any]] | None = None,
    *,
    interval_minutes: int = 30,
    max_age_minutes: float = 45.0,
    ac_active_threshold_w: float = 20.0,
    pump_off_threshold_w: float = 5.0,
    minimum_period_samples: int = 4,
) -> dict[str, Any]:
    """Find passive upstairs coast periods and quantify observed room-air slopes.

    A period is eligible only when AC input and the configured upstairs pump
    are both positively observed off, all configured room windows are known
    closed, and room/outdoor samples are fresh. If a burner channel is present,
    it must also be positively observed off. Solar/internal gains remain
    potential confounders and are surfaced in the result.
    """
    if interval_minutes <= 0 or max_age_minutes < 0 or minimum_period_samples < 3:
        raise ValueError("Invalid thermal-storage analysis thresholds")

    pump_items = pumps or []
    upstairs_pump = next((
        item for item in pump_items
        if str(item.get("floor", "")).lower() in ("upstairs", "upper")
        or "upstairs" in str(item.get("id", "")).lower()
    ), None)
    pump_channel = f"pump:{upstairs_pump.get('id')}:power" if upstairs_pump else None
    reasons = {
        "missing_or_stale_ac_power": 0,
        "ac_active": 0,
        "upstairs_pump_state_unknown": 0,
        "upstairs_pump_running": 0,
        "boiler_state_unknown_or_running": 0,
        "room_or_window_state_unknown": 0,
    }
    eligible: list[tuple[datetime, dict[str, float]]] = []
    for sample in samples:
        stamp = _stamp(sample.get("timestamp"))
        channels = sample.get("channels") or {}
        room_values: dict[str, float] = {}
        if stamp is None:
            continue
        ac = _finite(channels.get("ac_power"))
        ac_unit = str(channels.get("ac_power_unit") or "W").strip().lower()
        if ac is not None and ac_unit in ("kw", "kilowatt", "kilowatts"):
            ac *= 1000.0
        elif ac is not None and ac_unit not in ("w", "watt", "watts", ""):
            ac = None
        ac_age = _finite(channels.get("ac_power_age_minutes"))
        if ac is None or ac < 0 or (ac_age is not None and ac_age > max_age_minutes):
            reasons["missing_or_stale_ac_power"] += 1
            continue
        if ac > ac_active_threshold_w:
            reasons["ac_active"] += 1
            continue
        if not pump_channel:
            reasons["upstairs_pump_state_unknown"] += 1
            continue
        pump = _finite(channels.get(pump_channel))
        pump_unit = str(channels.get(pump_channel + "_unit") or "W").strip().lower()
        if pump is not None and pump_unit in ("kw", "kilowatt", "kilowatts"):
            pump *= 1000.0
        elif pump is not None and pump_unit not in ("w", "watt", "watts", ""):
            pump = None
        pump_age = _finite(channels.get(pump_channel + "_age_minutes"))
        if pump is None or pump < 0 or (pump_age is not None and pump_age > max_age_minutes):
            reasons["upstairs_pump_state_unknown"] += 1
            continue
        if pump > pump_off_threshold_w:
            reasons["upstairs_pump_running"] += 1
            continue
        if "boiler_burner" in channels:
            burner_age = _finite(channels.get("boiler_burner_age_minutes"))
            burner_off = _is_off(channels.get("boiler_burner"), 0.0)
            if burner_off is not True or (burner_age is not None and burner_age > max_age_minutes):
                reasons["boiler_state_unknown_or_running"] += 1
                continue
        room_ok = True
        for room in rooms:
            room_id = str(room.get("id", "unknown"))
            values = (sample.get("rooms") or {}).get(room_id) or {}
            temp = _finite(values.get("temperature"))
            temp_age = _finite(values.get("temperature_age_minutes"))
            window = values.get("window")
            window_age = _finite(values.get("window_age_minutes"))
            window_closed = _is_off(window, 0.0)
            if (temp is None or (temp_age is not None and temp_age > max_age_minutes)
                or window_closed is not True
                or (window_age is not None and window_age > max_age_minutes)):
                room_ok = False
                break
            room_values[room_id] = temp
        if not room_ok or not room_values:
            reasons["room_or_window_state_unknown"] += 1
            continue
        eligible.append((stamp, room_values))

    eligible.sort(key=lambda item: item[0])
    blocks: list[list[tuple[datetime, dict[str, float]]]] = []
    max_gap = interval_minutes * 60 * 1.5
    for item in eligible:
        if not blocks or (item[0] - blocks[-1][-1][0]).total_seconds() > max_gap:
            blocks.append([item])
        else:
            blocks[-1].append(item)
    periods = []
    room_rates: dict[str, list[float]] = {str(room.get("id", "unknown")): [] for room in rooms}
    for block in blocks:
        if len(block) < minimum_period_samples:
            continue
        room_results = {}
        for room_id in room_rates:
            points = [(stamp, values[room_id]) for stamp, values in block if room_id in values]
            rate = _linear_rate(points)
            if rate is not None:
                room_rates[room_id].append(rate)
                room_results[room_id] = {
                    "sample_count": len(points),
                    "start_temperature_c": round(points[0][1], 3),
                    "end_temperature_c": round(points[-1][1], 3),
                    "temperature_change_c": round(points[-1][1] - points[0][1], 3),
                    "linear_rate_c_per_hour": round(rate, 4),
                }
        periods.append({
            "start": block[0][0].isoformat(),
            "end": block[-1][0].isoformat(),
            "sample_count": len(block),
            "duration_hours": round((block[-1][0] - block[0][0]).total_seconds() / 3600, 2),
            "room_air_response": room_results,
        })

    room_summary = {}
    for room_id, rates in room_rates.items():
        room_summary[room_id] = {
            "valid_passive_periods": len(rates),
            "median_coast_rate_c_per_hour": round(sorted(rates)[len(rates)//2], 4) if rates else None,
        }
    return {
        "status": "passive_room_response_observed" if periods else (
            "withheld_upstairs_pump_channel_missing" if not pump_channel else "insufficient_clean_passive_periods"
        ),
        "method": "recorder_aligned_clean_coast_periods",
        "eligible_sample_count": len(eligible),
        "identified_passive_period_count": len(periods),
        "periods": periods,
        "rooms": room_summary,
        "excluded_samples": reasons,
        "operating_conditions": {
            "ac_power_threshold_w": ac_active_threshold_w,
            "upstairs_pump_off_threshold_w": pump_off_threshold_w,
            "upstairs_pump_channel": pump_channel,
            "boiler_off_verified": any("boiler_burner" in (sample.get("channels") or {}) for sample in samples),
            "closed_windows_required": True,
            "solar_gain_filter_applied": False,
        },
        "floor_storage": {
            "status": "indirect_response_only",
            "slab_temperature_c": None,
            "slab_capacity_wh_per_k": None,
            "interpretation": (
                "A slow room-air coast after verified plant and AC shutdown is compatible with "
                "release of stored building/floor heat, but room-air data alone cannot separate "
                "UFH screed storage from walls, furnishings, solar gains or internal gains."
            ),
        },
        "limitations": [
            "Combined AC electrical input is only used to classify AC activity; it is not converted to thermal output.",
            "Pump electrical power confirms pump state only; it is not thermal output.",
            "Room-level slopes are observational and may include solar and internal gains.",
            "No slab temperature or absolute slab thermal capacity is inferred.",
        ],
    }
