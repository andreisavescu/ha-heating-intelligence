"""Attic temperature gradients and optional conductive heat-flow estimates."""
from __future__ import annotations


def build_attic_thermal_analysis(
    attic_temperature: float | None,
    attic_humidity: float | None,
    upstairs_temperature: float | None,
    outdoor_temperature: float | None,
    profile: dict | None = None,
) -> dict:
    """Calculate measured temperature differences and U*A*delta-T heat flow.

    Heat flow is positive from the warmer side toward the colder side.
    No U-value or area is inferred from insulation thickness.
    """
    profile = profile or {}
    attic_outdoor_delta = (
        attic_temperature - outdoor_temperature
        if attic_temperature is not None and outdoor_temperature is not None
        else None
    )
    upstairs_attic_delta = (
        upstairs_temperature - attic_temperature
        if upstairs_temperature is not None and attic_temperature is not None
        else None
    )

    def heat_flow(delta_t: float | None, u_key: str, area_key: str):
        try:
            u_value = float(profile.get(u_key))
            area = float(profile.get(area_key))
        except (TypeError, ValueError):
            return None
        if delta_t is None or u_value <= 0 or area <= 0:
            return None
        return round(u_value * area * delta_t, 1)

    roof_flow = heat_flow(attic_outdoor_delta, "roof_u_value", "roof_area_m2")
    ceiling_flow = heat_flow(upstairs_attic_delta, "attic_floor_u_value", "attic_floor_area_m2")
    return {
        "attic_temperature": attic_temperature,
        "attic_humidity": attic_humidity,
        "upstairs_average_temperature": round(upstairs_temperature, 2) if upstairs_temperature is not None else None,
        "outdoor_temperature": outdoor_temperature,
        "attic_to_outdoor_delta_c": round(attic_outdoor_delta, 2) if attic_outdoor_delta is not None else None,
        "upstairs_to_attic_delta_c": round(upstairs_attic_delta, 2) if upstairs_attic_delta is not None else None,
        "estimated_roof_heat_flow_w": roof_flow,
        "estimated_ceiling_heat_flow_w": ceiling_flow,
        "roof_u_value_w_m2k": profile.get("roof_u_value"),
        "roof_area_m2": profile.get("roof_area_m2"),
        "attic_floor_u_value_w_m2k": profile.get("attic_floor_u_value"),
        "attic_floor_area_m2": profile.get("attic_floor_area_m2"),
        "heat_flow_method": "U × area × temperature difference; positive values indicate outward heat flow",
        "heat_flow_status": (
            "estimates_available" if roof_flow is not None or ceiling_flow is not None
            else "temperature_gradients_only"
        ),
    }
