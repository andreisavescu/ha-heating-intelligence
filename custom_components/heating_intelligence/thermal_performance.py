"""Gas-derived whole-house thermal performance estimates."""
from __future__ import annotations

from datetime import datetime


def thermal_performance_statistics(history: dict, now: datetime,
                                   boiler_efficiency: float = 0.90,
                                   envelope_area_m2: float = 361.26,
                                   ac_heating_cop: float = 3.0) -> dict:
    """Estimate useful heat and effective heat-loss coefficient."""
    days = history.get("efficiency_daily", {})
    today_key = now.date().isoformat()
    # Final thermal metrics use completed local calendar days only.
    completed_keys = sorted(key for key in days if key < today_key)
    daily_key = completed_keys[-1] if completed_keys else None
    daily = days.get(daily_key, {}) if daily_key else {}
    today = days.get(today_key, {})
    efficiency = min(1.0, max(0.5, float(boiler_efficiency)))
    area = float(envelope_area_m2)
    gas_kwh = max(0.0, float(today.get("gas_kwh", 0.0) or 0.0))
    ac_cop = min(8.0, max(1.0, float(ac_heating_cop)))
    ac_electricity_kwh = max(0.0, float(today.get("ac_electricity_kwh", 0.0) or 0.0))
    ac_delivered_today = ac_electricity_kwh * ac_cop
    delivered_today = gas_kwh * efficiency + ac_delivered_today
    daily_gas_kwh = max(0.0, float(daily.get("gas_kwh", 0.0) or 0.0))
    daily_ac_kwh = max(0.0, float(daily.get("ac_electricity_kwh", 0.0) or 0.0))
    daily_delivered = daily_gas_kwh * efficiency + daily_ac_kwh * ac_cop
    daily_exposure = max(0.0, float(daily.get("delta_t_degree_hours", 0.0) or 0.0))
    daily_h = daily_delivered * 1000.0 / daily_exposure if daily_exposure >= 24.0 and area > 0 else None
    daily_u = daily_h / area if daily_h is not None else None

    def aggregate(keys):
        heat = exposure = 0.0
        valid = 0
        for key in keys:
            item = days.get(key, {})
            gas, degree_hours = item.get("gas_kwh"), item.get("delta_t_degree_hours")
            if gas is None or degree_hours is None:
                continue
            exposure_value = max(0.0, float(degree_hours))
            heat += max(0.0, float(gas)) * efficiency + max(0.0, float(item.get("ac_electricity_kwh", 0.0) or 0.0)) * ac_cop
            exposure += exposure_value
            valid += exposure_value > 0
        return heat, exposure, valid

    ordered = completed_keys
    keys7, keys30 = ordered[-7:], ordered[-30:]
    heat7, exposure7, valid7 = aggregate(keys7)
    heat30, exposure30, valid30 = aggregate(keys30)
    u7 = heat7 * 1000.0 / exposure7 / area if len(keys7) >= 5 and valid7 >= 5 and exposure7 >= 120 and area > 0 else None
    u30 = heat30 * 1000.0 / exposure30 / area if len(keys30) >= 20 and valid30 >= 20 and exposure30 >= 400 and area > 0 else None
    return {
        "estimated_delivered_heat_today_kwh": round(delivered_today, 3),
        "estimated_ac_delivered_heat_today_kwh": round(ac_delivered_today, 3),
        "ac_electricity_today_kwh": round(ac_electricity_kwh, 3),
        "thermal_model_ac_heating_cop_assumption": ac_cop,
        "heat_loss_coefficient_w_k": round(daily_h, 2) if daily_h is not None else None,
        "heat_loss_u_value_daily": round(daily_u, 4) if daily_u is not None else None,
        "thermal_model_daily_date": daily_key,
        "heat_loss_u_value_7d": round(u7, 4) if u7 is not None else None,
        "heat_loss_u_value_30d": round(u30, 4) if u30 is not None else None,
        "thermal_model_degree_hours_today": round(daily_exposure, 2),
        "thermal_model_valid_days_7d": valid7,
        "thermal_model_valid_days_30d": valid30,
        "thermal_model_boiler_efficiency_assumption": efficiency,
        "thermal_model_envelope_area_m2": area,
    }
