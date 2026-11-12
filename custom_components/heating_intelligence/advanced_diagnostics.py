"""Advanced, explainable room and system diagnostics."""
from __future__ import annotations

from statistics import mean
from typing import Any


def build_advanced_diagnostics(
    rooms: dict[str, dict[str, Any]],
    efficiency: dict[str, Any],
    history: dict[str, Any],
) -> dict[str, Any]:
    """Derive conservative room scores, heat-loss indicators and anomalies.

    Scores are comparative heuristics, not engineering measurements. A physical
    heat-loss coefficient (W/K) cannot be calculated without room area and
    delivered heat output; this module therefore reports observed cooling only.
    """
    room_results: dict[str, dict[str, Any]] = {}
    anomalies: list[dict[str, str]] = []
    floor_groups: dict[str, list[tuple[float, float]]] = {}

    for room_id, room in rooms.items():
        response = room.get("heating_response_minutes")
        response_samples = int(room.get("heating_response_samples") or 0)
        cooling = room.get("cooling_rate_c_per_hour")
        temp_change = room.get("temperature_change_24h")
        window_seconds = float(room.get("window_open_demand_seconds_today") or 0)
        samples = int(room.get("temperature_sample_count") or 0)

        components: list[tuple[str, float]] = []
        if response is not None and response_samples >= 3:
            components.append(("heating_response", max(0.0, min(100.0, 100 - float(response) / 1.5))))
        if cooling is not None:
            components.append(("cooling_rate", max(0.0, min(100.0, 100 - float(cooling) * 100))))
        if temp_change is not None:
            components.append(("temperature_stability", max(0.0, min(100.0, 100 - abs(float(temp_change)) * 15))))
        if room.get("window_entity"):
            components.append(("window_use", max(0.0, 100 - min(100.0, window_seconds / 3600 * 40))))

        score = round(mean(value for _, value in components)) if len(components) >= 2 and samples >= 12 else None
        area = room.get("area_m2")
        area = float(area) if area not in (None, "") and float(area) > 0 else None
        if area is not None and cooling is not None:
            floor_groups.setdefault(str(room.get("floor", "unassigned")), []).append((area, float(cooling)))
        room_results[room_id] = {
            "area_m2": area,
            "cooling_rate_area_weighted_contribution": round(area * float(cooling), 3) if area is not None and cooling is not None else None,
            "score": score,
            "score_status": "available" if score is not None else "collecting_data",
            "score_components": {key: round(value) for key, value in components},
            "observed_heat_loss_c_per_hour": round(float(cooling), 3) if cooling is not None else None,
            "heat_loss_status": "observed_cooling_rate" if cooling is not None else "insufficient_closed_window_data",
            "temperature_samples": samples,
        }
        if cooling is not None and float(cooling) >= 0.8:
            anomalies.append({
                "id": f"high_cooling_{room_id}",
                "severity": "info",
                "title": f"{room.get('name', room_id)}: unusually fast cooling",
                "detail": f"Observed closed-window cooling is {float(cooling):.2f} °C/h; compare with this room's own history.",
            })
        if response is not None and response_samples >= 3 and float(response) >= 120:
            anomalies.append({
                "id": f"slow_heating_{room_id}",
                "severity": "info",
                "title": f"{room.get('name', room_id)}: slower heating response",
                "detail": f"Average observed 0.5 °C response is {float(response):.0f} minutes across {response_samples} samples.",
            })

    floor_results = {}
    for floor, values in floor_groups.items():
        total_area = sum(area for area, _ in values)
        floor_results[floor] = {
            "configured_area_m2": round(total_area, 2),
            "area_weighted_observed_cooling_c_per_hour": round(sum(area * rate for area, rate in values) / total_area, 3),
            "rooms_with_area_and_cooling_data": len(values),
        }

    daily = history.get("efficiency_daily", {})
    dated = sorted(
        (date, values) for date, values in daily.items()
        if isinstance(values, dict)
    )
    recent = dated[-1:] 
    baseline = dated[-8:-1]
    if recent and len(baseline) >= 3:
        _, today = recent[0]
        for metric, label in (("gas_kwh", "Gas consumption"), ("electricity_kwh", "Heating electricity")):
            current = today.get(metric)
            prior = [float(item.get(metric, 0)) for _, item in baseline if item.get(metric) is not None]
            if current is not None and len(prior) >= 3:
                avg = mean(prior)
                if avg > 0 and float(current) > avg * 1.5:
                    anomalies.append({
                        "id": f"{metric}_above_baseline",
                        "severity": "warning",
                        "title": f"{label} is above its recent baseline",
                        "detail": f"Today is {float(current):.2f} kWh versus a {avg:.2f} kWh average over {len(prior)} prior recorded days. Weather and occupancy can explain differences.",
                    })

    gas_hdd = efficiency.get("gas_kwh_per_degree_day")
    electricity = efficiency.get("electricity_energy_today_kwh")
    gas = efficiency.get("gas_energy_today_kwh")
    boiler = {
        "gas_kwh_per_degree_day": gas_hdd,
        "gas_energy_today_kwh": gas,
        "electricity_energy_today_kwh": electricity,
        "gas_to_electricity_ratio": round(float(gas) / float(electricity), 2) if gas is not None and electricity is not None and float(electricity) > 0 else None,
        "status": "proxy_only" if gas is not None or electricity is not None else "not_configured",
        "note": "Consumption indicators only. Boiler combustion efficiency cannot be calculated without measured heat output and fuel input over the same interval.",
    }
    return {
        "room_diagnostics": room_results,
        "floor_area_analysis": floor_results,
        "boiler_analysis": boiler,
        "anomalies": anomalies,
        "diagnostic_data_days": len(dated),
    }
