"""Explainable whole-house building profile and performance assessment.

This module deliberately separates profile completeness from measured thermal
performance. It does not infer U-values or an EPC rating from incomplete inputs.
"""
from __future__ import annotations

from typing import Any

_SECTIONS = {
    "envelope": (
        "wall_construction", "roof_type", "floor_construction",
        "construction_year", "wall_insulation_cm", "roof_insulation_cm",
        "floor_insulation_cm", "window_glazing", "window_frame",
        "main_orientation", "shading", "window_area_north_m2",
        "window_area_south_m2", "window_area_east_m2", "window_area_west_m2",
    ),
    "heating": (
        "boiler_model", "heating_distribution", "boiler_dhw",
        "buffer_tank", "ufh_screed_cm", "ac_heating", "ac_units",
    ),
    "ventilation_occupancy": (
        "ventilation_type", "daily_window_ventilation", "occupants",
        "occupancy_pattern", "internal_gains", "staircase_layout",
    ),
}

_PERFORMANCE_KEYS = (
    "heat_loss_coefficient_w_k", "heat_loss_u_value_daily",
    "heat_loss_u_value_7d", "heat_loss_u_value_30d",
    "gas_kwh_per_degree_day", "weather_normalised",
)


def _known(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, str) and value.lower() in {"unknown", "unspecified", "n/a"}:
        return False
    return True


def _section_result(profile: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    known = [field for field in fields if _known(profile.get(field))]
    missing = [field for field in fields if field not in known]
    return {
        "known_fields": len(known),
        "total_fields": len(fields),
        "completeness_percent": round(100 * len(known) / len(fields)),
        "missing_fields": missing,
    }


def build_house_assessment(
    profile: dict[str, Any] | None,
    efficiency: dict[str, Any] | None,
    rooms: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build a transparent profile-completeness and measured-performance view."""
    profile = profile or {}
    efficiency = efficiency or {}
    rooms = rooms or {}

    sections = {
        name: _section_result(profile, fields)
        for name, fields in _SECTIONS.items()
    }
    all_fields = tuple(field for fields in _SECTIONS.values() for field in fields)
    known_count = sum(sections[name]["known_fields"] for name in sections)
    profile_percent = round(100 * known_count / len(all_fields)) if all_fields else 0

    # These are observations about entered inputs, not inferred material
    # properties. In particular insulation thickness is not converted to U.
    observations: list[str] = []
    wall_insulation = profile.get("wall_insulation_cm")
    if _known(wall_insulation):
        observations.append(
            f"Wall insulation thickness recorded as {float(wall_insulation):g} cm; "
            "thermal transmittance still depends on the complete wall build-up."
        )
    if _known(profile.get("window_glazing")):
        observations.append(
            f"Window glazing recorded as {profile['window_glazing']}."
        )
    if _known(profile.get("ventilation_type")):
        observations.append(
            f"Ventilation type recorded as {profile['ventilation_type']}."
        )
    if not _known(profile.get("roof_insulation_cm")):
        observations.append("Roof insulation is not specified; envelope assessment is incomplete.")
    if not _known(profile.get("floor_insulation_cm")):
        observations.append("Floor insulation is not specified; envelope assessment is incomplete.")

    configured_room_areas = [
        float(room["area"]) for room in rooms.values()
        if _known(room.get("area")) and float(room["area"]) > 0
    ]
    room_area_total = round(sum(configured_room_areas), 2)

    performance = {
        key: efficiency.get(key)
        for key in _PERFORMANCE_KEYS
        if efficiency.get(key) is not None
    }
    if efficiency.get("heat_loss_u_value_30d") is not None:
        performance_status = "30_day_estimate_available"
    elif efficiency.get("heat_loss_u_value_7d") is not None:
        performance_status = "7_day_estimate_available"
    elif efficiency.get("heat_loss_u_value_daily") is not None:
        performance_status = "daily_estimate_available"
    else:
        performance_status = "collecting_data"

    return {
        "schema_version": 1,
        "profile_completeness_percent": profile_percent,
        "profile_known_fields": known_count,
        "profile_total_fields": len(all_fields),
        "sections": sections,
        "observations": observations,
        "room_coverage": {
            "configured_rooms": len(rooms),
            "rooms_with_area": len(configured_room_areas),
            "configured_room_area_m2": room_area_total,
        },
        "measured_performance": performance,
        "performance_status": performance_status,
        "interpretation": (
            "Profile completeness describes how much building information is known; "
            "it is not an energy-efficiency score. Thermal-performance estimates use "
            "meter and temperature observations plus configured boiler-efficiency "
            "and AC-COP assumptions. They are not certified U-values or an EPC rating."
        ),
    }
