"""Stage 6: gain-aware RC residual diagnostics.

The model estimates net unexplained thermal input during eligible room coasting
intervals. It does not claim to separate solar from internal gains: room coupling,
UFH slab release, sensor bias and unmodelled paths remain confounders.
"""
from __future__ import annotations

import math
from datetime import datetime
from statistics import median
from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _switch(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("on", "true", "1", "open", "heat", "heating"):
        return True
    if text in ("off", "false", "0", "closed", "idle"):
        return False
    return None


def _stamp(value: Any):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed
    except (TypeError, ValueError):
        return None


def build_stage6_gain_model(
    samples: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    absolute_parameters: dict[str, Any] | None,
    minimum_intervals: int = 12,
    maximum_interval_minutes: float = 45.0,
    require_ac_power: bool = False,
) -> dict[str, Any]:
    """Estimate net residual heat input in gated, demand-off intervals.

    Q_residual = C * dT/dt + G_envelope * (T_room - T_outdoor).
    Positive values indicate net heat entering the room beyond the configured
    outdoor-envelope path. They are not uniquely solar or internal gains.
    """
    fits = (absolute_parameters or {}).get("rooms") or {}
    parsed = []
    for sample in samples or []:
        stamp = _stamp(sample.get("timestamp"))
        if stamp is not None:
            parsed.append((stamp, sample))
    parsed.sort(key=lambda item: item[0])

    results = {}
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        fit = fits.get(room_id) or {}
        capacity = _number(fit.get("effective_room_capacity_wh_per_k"))
        envelope = _number(room.get("envelope_conductance_w_per_k"))
        eligible = []
        excluded = {
            "missing_temperature": 0,
            "demand_not_confirmed_off": 0,
            "window_not_confirmed_closed": 0,
            "missing_outdoor": 0,
            "ac_active_or_unknown": 0,
            "invalid_interval": 0,
            "missing_absolute_parameters": 0,
        }
        if capacity is None or capacity <= 0 or envelope is None or envelope <= 0:
            excluded["missing_absolute_parameters"] = max(0, len(parsed) - 1)
        else:
            for (t0, first), (t1, second) in zip(parsed, parsed[1:]):
                minutes = (t1 - t0).total_seconds() / 60
                if minutes <= 0 or minutes > maximum_interval_minutes:
                    excluded["invalid_interval"] += 1
                    continue
                left = ((first.get("rooms") or {}).get(room_id) or {})
                right = ((second.get("rooms") or {}).get(room_id) or {})
                temp0 = _number(left.get("temperature"))
                temp1 = _number(right.get("temperature"))
                if temp0 is None or temp1 is None:
                    excluded["missing_temperature"] += 1
                    continue
                if _switch(right.get("demand")) is not False:
                    excluded["demand_not_confirmed_off"] += 1
                    continue
                if room.get("window_entity") and (
                    _switch(left.get("window")) is not False or _switch(right.get("window")) is not False
                ):
                    excluded["window_not_confirmed_closed"] += 1
                    continue
                channels0, channels1 = first.get("channels") or {}, second.get("channels") or {}
                outside0 = _number(channels0.get("outdoor_temperature"))
                outside1 = _number(channels1.get("outdoor_temperature"))
                if outside0 is None or outside1 is None:
                    excluded["missing_outdoor"] += 1
                    continue
                ac0 = _number(channels0.get("ac_power"))
                ac1 = _number(channels1.get("ac_power"))
                ac_age0 = _number(channels0.get("ac_power_age_minutes"))
                ac_age1 = _number(channels1.get("ac_power_age_minutes"))
                if (
                    (ac0 is not None and ac0 > 20) or (ac1 is not None and ac1 > 20)
                    or (require_ac_power and (ac0 is None or ac1 is None))
                    or (ac_age0 is not None and ac_age0 > maximum_interval_minutes)
                    or (ac_age1 is not None and ac_age1 > maximum_interval_minutes)
                ):
                    excluded["ac_active_or_unknown"] += 1
                    continue
                if room.get("window_entity") and (
                    left.get("window") is None or right.get("window") is None
                ):
                    excluded["window_not_confirmed_closed"] += 1
                    continue
                mean_temp = (temp0 + temp1) / 2
                mean_outside = (outside0 + outside1) / 2
                slope_k_per_hour = (temp1 - temp0) / (minutes / 60)
                residual_w = capacity * slope_k_per_hour + envelope * (mean_temp - mean_outside)
                solar = _number(channels0.get("solar_radiation"))
                solar_unit = str(channels0.get("solar_radiation_unit") or "").strip().lower().replace(" ", "")
                if solar is not None and solar_unit in ("w/m2", "w/m²"):
                    solar = max(0.0, solar)
                else:
                    solar = None
                eligible.append({
                    "timestamp": t0.isoformat(),
                    "net_unattributed_gain_w": round(residual_w, 2),
                    "solar_irradiance_w_m2": solar,
                    "room_temperature_c": round(mean_temp, 3),
                    "outdoor_temperature_c": round(mean_outside, 3),
                    "interval_minutes": round(minutes, 2),
                })
        values = [item["net_unattributed_gain_w"] for item in eligible]
        solar_pairs = [item for item in eligible if item["solar_irradiance_w_m2"] is not None]
        median_gain = median(values) if values else None
        results[room_id] = {
            "status": "residual_gain_evidence_available" if len(values) >= minimum_intervals else "insufficient_gated_coasting_intervals",
            "eligible_intervals": len(values),
            "minimum_intervals_required": minimum_intervals,
            "median_net_unattributed_gain_w": round(median_gain, 2) if median_gain is not None else None,
            "p10_net_unattributed_gain_w": round(sorted(values)[max(0, int((len(values)-1)*0.1))], 2) if values else None,
            "p90_net_unattributed_gain_w": round(sorted(values)[min(len(values)-1, int((len(values)-1)*0.9))], 2) if values else None,
            "solar_irradiance_coverage_intervals": len(solar_pairs),
            "coasting_residual_samples": eligible[-96:],
            "excluded_intervals": excluded,
            "solar_gain_w": None,
            "internal_gain_w": None,
            "solver_gain_input_w": None,
            "interpretation": (
                "Net residual heat input after the configured outdoor-envelope term. "
                "Solar gain, internal gain, UFH slab release, inter-room transfer and model error "
                "are not separable with the current measurements."
            ),
        }
    supported = [key for key, value in results.items() if value["eligible_intervals"] >= minimum_intervals]
    return {
        "status": "stage6_residual_gain_evidence_available" if supported else "stage6_collecting_gated_coasting_data",
        "stage": 6,
        "method": "room_air_energy_balance_residual_during_demand_off_window_closed_coasting",
        "rooms_with_sufficient_evidence": supported,
        "rooms": results,
        "solar_internal_gains_separately_identified": False,
        "solver_input_enabled": False,
        "room_learning_adjustment_enabled": False,
        "limitations": [
            "Requires a provisional absolute room capacity and explicit outdoor-envelope conductance.",
            "Residual includes solar and internal gains plus UFH slab release, inter-room transfer, unmodelled paths and sensor/model error.",
            "Solar irradiance is retained as a covariate only; global irradiance is not converted to façade irradiance.",
            "CO2 and humidity remain indirect occupancy evidence, not heat measurements.",
            "No gain watts are fed back into parameter learning or the live solver.",
        ],
    }
