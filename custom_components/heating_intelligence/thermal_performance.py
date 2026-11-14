"""Gas-derived whole-house thermal performance estimates."""
from __future__ import annotations

from datetime import datetime, timedelta


def _daily_result(day: dict, boiler_efficiency: float, area: float, ac_cop: float) -> dict:
    """Calculate one completed local day's effective heat-loss estimate."""
    gas = max(0.0, float(day.get("gas_kwh", 0.0) or 0.0))
    ac_electricity = max(0.0, float(day.get("ac_electricity_kwh", 0.0) or 0.0))
    delivered = gas * boiler_efficiency + ac_electricity * ac_cop
    exposure = max(0.0, float(day.get("delta_t_degree_hours", 0.0) or 0.0))
    heat_loss = delivered * 1000.0 / exposure if exposure >= 24.0 and area > 0 else None
    return {
        "delivered_heat_kwh": round(delivered, 3),
        "degree_hours": round(exposure, 2),
        "heat_loss_coefficient_w_k": round(heat_loss, 2) if heat_loss is not None else None,
        "heat_loss_u_value": round(heat_loss / area, 4) if heat_loss is not None else None,
        "solar_radiation_average": (
            round(float(day.get("solar_sum", 0.0)) / int(day.get("solar_count", 0)), 2)
            if int(day.get("solar_count", 0) or 0) > 0 else None
        ),
    }


def thermal_performance_statistics(history: dict, now: datetime,
                                   boiler_efficiency: float = 0.90,
                                   envelope_area_m2: float = 361.26,
                                   ac_heating_cop: float = 3.0,
                                   gas_kwh_per_m3: float = 10.55) -> dict:
    """Estimate useful heat; freeze each daily result at 02:00 local time."""
    days = history.get("efficiency_daily", {})
    today_key = now.date().isoformat()
    efficiency = min(1.0, max(0.5, float(boiler_efficiency)))
    area = float(envelope_area_m2)
    ac_cop = min(8.0, max(1.0, float(ac_heating_cop)))

    # Finalise the previous local calendar day only after the 02:00
    # collection grace period. The date key makes this operation idempotent
    # across coordinator refreshes and Home Assistant restarts.
    finalised = history.setdefault("thermal_daily_results", {})
    if now.hour >= 2:
        # Backfill retained completed days once on upgrade, then finalise the
        # most recent completed day. All later refreshes are idempotent.
        for completed_key in sorted(key for key in days if key < today_key):
            if completed_key not in finalised:
                finalised[completed_key] = _daily_result(
                    days.get(completed_key, {}), efficiency, area, ac_cop
                )

    finalised_keys = sorted(key for key in finalised if key < today_key)
    daily_key = finalised_keys[-1] if finalised_keys else None
    daily = finalised.get(daily_key, {}) if daily_key else {}
    today = days.get(today_key, {})
    gas_kwh = max(0.0, float(today.get("gas_kwh", 0.0) or 0.0))
    ac_electricity_kwh = max(0.0, float(today.get("ac_electricity_kwh", 0.0) or 0.0))
    ac_delivered_today = ac_electricity_kwh * ac_cop
    delivered_today = gas_kwh * efficiency + ac_delivered_today

    def aggregate(keys):
        heat = exposure = 0.0
        valid = 0
        for key in keys:
            item = days.get(key, {})
            gas, degree_hours = item.get("gas_kwh"), item.get("delta_t_degree_hours")
            if gas is None or degree_hours is None:
                continue
            exposure_value = max(0.0, float(degree_hours))
            heat += max(0.0, float(gas)) * efficiency + max(
                0.0, float(item.get("ac_electricity_kwh", 0.0) or 0.0)
            ) * ac_cop
            exposure += exposure_value
            valid += exposure_value > 0
        return heat, exposure, valid

    # Recalculate the measured-performance H estimate across explicit
    # fuel-to-useful-heat assumptions. This is a sensitivity envelope, not
    # an assertion that any scenario is the true plant efficiency.
    def assumption_sensitivity(keys, minimum_days, minimum_exposure):
        selected = [days.get(key, {}) for key in keys]
        conversion_bases = [item.get("gas_meter_conversion_basis") for item in selected]
        conversion_basis = (
            conversion_bases[0] if conversion_bases and all(
                basis == conversion_bases[0] for basis in conversion_bases
            ) else "mixed_or_unknown"
        )
        conversion_observable = conversion_basis in ("volume_m3", "energy_kwh")

        selected = [days.get(key, {}) for key in keys]
        valid = [
            item for item in selected
            if item.get("gas_kwh") is not None
            and item.get("delta_t_degree_hours") is not None
            and float(item.get("delta_t_degree_hours") or 0) > 0
        ]
        exposure = sum(max(0.0, float(item.get("delta_t_degree_hours") or 0)) for item in valid)
        if len(keys) < minimum_days or len(valid) < minimum_days or exposure < minimum_exposure or area <= 0 or not conversion_observable:
            return {
                "status": "insufficient_completed_day_history",
                "valid_days": len(valid),
                "required_days": minimum_days,
                "scenario_count": 0,
                "gas_meter_conversion_basis": conversion_basis,
                "heat_loss_coefficient_range_w_k": None,
                "u_value_range_w_m2_k": None,
                "scenarios": [],
            }
        scenarios = []
        gas_factor_scenarios = (
            (gas_kwh_per_m3 * 0.95, gas_kwh_per_m3, gas_kwh_per_m3 * 1.05)
            if conversion_basis == "volume_m3" else (None,)
        )
        for gas_factor_assumption in gas_factor_scenarios:
          for boiler_assumption in (0.80, 0.90, 0.98):
            for cop_assumption in (2.0, 3.0, 5.0):
                delivered = sum(
                    (
                        max(0.0, float(item.get("gas_kwh", 0.0) or 0.0))
                        + (
                            float(item.get("gas_meter_volume_m3", 0.0) or 0.0)
                            * (gas_factor_assumption - gas_kwh_per_m3)
                            if gas_factor_assumption is not None else 0.0
                        )
                    ) * boiler_assumption
                    + max(0.0, float(item.get("ac_electricity_kwh", 0.0) or 0.0)) * cop_assumption
                    for item in valid
                )
                coefficient = delivered * 1000.0 / exposure
                scenarios.append({
                    "gas_kwh_per_m3_assumption": round(gas_factor_assumption, 4) if gas_factor_assumption is not None else None,
                    "boiler_efficiency_assumption": boiler_assumption,
                    "ac_cop_assumption": cop_assumption,
                    "heat_loss_coefficient_w_k": round(coefficient, 2),
                    "u_value_w_m2_k": round(coefficient / area, 4),
                })
        coefficients = [item["heat_loss_coefficient_w_k"] for item in scenarios]
        u_values = [item["u_value_w_m2_k"] for item in scenarios]
        return {
            "status": "assumption_sensitivity_available",
            "valid_days": len(valid),
            "required_days": minimum_days,
            "scenario_count": len(scenarios),
            "gas_meter_conversion_basis": conversion_basis,
            "gas_kwh_per_m3_reference": gas_kwh_per_m3 if conversion_basis == "volume_m3" else None,
            "heat_loss_coefficient_range_w_k": {
                "minimum": min(coefficients),
                "maximum": max(coefficients),
            },
            "u_value_range_w_m2_k": {
                "minimum": min(u_values),
                "maximum": max(u_values),
            },
            "scenarios": scenarios,
            "note": (
                "Scenarios vary boiler efficiency and AC COP; when the meter is in m³, gas conversion is also varied by ±5% around the configured factor. "
                "The range is conditional on gas meter energy conversion, measured degree-hours, "
                "configured envelope area and the assumption that AC energy was used for heating."
            ),
        }

    # Rolling windows continue to use completed calendar-day aggregates.
    completed_keys = finalised_keys
    keys7, keys30 = completed_keys[-7:], completed_keys[-30:]
    heat7, exposure7, valid7 = aggregate(keys7)
    heat30, exposure30, valid30 = aggregate(keys30)
    u7 = heat7 * 1000.0 / exposure7 / area if len(keys7) >= 5 and valid7 >= 5 and exposure7 >= 120 and area > 0 else None
    u30 = heat30 * 1000.0 / exposure30 / area if len(keys30) >= 20 and valid30 >= 20 and exposure30 >= 400 and area > 0 else None

    return {
        "estimated_delivered_heat_today_kwh": round(delivered_today, 3),
        "estimated_ac_delivered_heat_today_kwh": round(ac_delivered_today, 3),
        "ac_electricity_today_kwh": round(ac_electricity_kwh, 3),
        "thermal_model_ac_heating_cop_assumption": ac_cop,
        "heat_loss_coefficient_w_k": daily.get("heat_loss_coefficient_w_k"),
        "heat_loss_u_value_daily": daily.get("heat_loss_u_value"),
        "thermal_model_daily_date": daily_key,
        "thermal_model_daily_finalised_at": f"{daily_key}T02:00:00" if daily_key else None,
        "thermal_model_daily_solar_radiation_average": daily.get("solar_radiation_average"),
        "heat_loss_u_value_7d": round(u7, 4) if u7 is not None else None,
        "heat_loss_u_value_30d": round(u30, 4) if u30 is not None else None,
        "heat_loss_assumption_sensitivity_7d": assumption_sensitivity(keys7, 5, 120),
        "heat_loss_assumption_sensitivity_30d": assumption_sensitivity(keys30, 20, 400),
        "thermal_model_degree_hours_today": round(float(today.get("delta_t_degree_hours", 0.0) or 0.0), 2),
        "thermal_model_valid_days_7d": valid7,
        "thermal_model_valid_days_30d": valid30,
        "thermal_model_boiler_efficiency_assumption": efficiency,
        "thermal_model_envelope_area_m2": area,
        "thermal_model_daily_finalisation_hour": 2,
        "thermal_model_solar_used_as_correction": False,
    }
