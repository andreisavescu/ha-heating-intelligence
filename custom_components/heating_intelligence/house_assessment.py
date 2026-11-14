"""Explainable whole-house building profile and measured-performance assessment."""
from __future__ import annotations

from typing import Any

_SECTIONS = {
    "envelope": (
        "wall_construction", "roof_type", "floor_construction", "construction_year",
        "wall_insulation_cm", "wall_insulation_material", "roof_insulation_cm", "roof_insulation_material", "floor_insulation_cm", "floor_insulation_material",
        "window_glazing", "window_frame", "main_orientation", "shading",
        "window_area_north_m2", "window_area_south_m2",
        "window_area_east_m2", "window_area_west_m2",
    ),
    "heating": (
        "boiler_model", "heating_distribution", "thermal_mass", "boiler_dhw", "buffer_tank",
        "ufh_screed_cm", "ac_heating", "ac_units",
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
    "thermal_model_daily_date", "thermal_model_valid_days_7d",
    "thermal_model_valid_days_30d", "thermal_model_degree_hours_today",
)


def _known(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, str) and value.strip().lower() in {"unknown", "unspecified", "n/a", "none"}:
        return False
    return True


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


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
    """Build a transparent profile, room-coverage and measured-performance view."""
    profile = profile or {}
    efficiency = efficiency or {}
    rooms = rooms or {}

    sections = {name: _section_result(profile, fields) for name, fields in _SECTIONS.items()}
    all_fields = tuple(field for fields in _SECTIONS.values() for field in fields)
    known_count = sum(sections[name]["known_fields"] for name in sections)
    profile_percent = round(100 * known_count / len(all_fields)) if all_fields else 0

    observations: list[str] = []
    wall = _number(profile.get("wall_insulation_cm"))
    roof = _number(profile.get("roof_insulation_cm"))
    floor = _number(profile.get("floor_insulation_cm"))
    wall_material = profile.get("wall_insulation_material")
    roof_material = profile.get("roof_insulation_material")
    floor_material = profile.get("floor_insulation_material")
    if _known(wall_material):
        observations.append(f"Wall insulation material recorded as {wall_material}.")
    if _known(roof_material):
        observations.append(f"Roof insulation material recorded as {roof_material}.")
    if _known(floor_material):
        observations.append(f"Floor insulation material recorded as {floor_material}.")
    if wall is not None:
        observations.append(
            f"Recorded wall insulation thickness: {wall:g} cm. Actual wall U-value also depends on material layers, thermal bridges and workmanship."
        )
    if roof is not None:
        observations.append(f"Recorded roof insulation thickness: {roof:g} cm.")
    else:
        observations.append("Roof insulation thickness is not recorded; the envelope profile is incomplete.")
    if floor is not None:
        observations.append(f"Recorded floor insulation thickness: {floor:g} cm.")
    else:
        observations.append("Floor insulation thickness is not recorded; the envelope profile is incomplete.")
    if _known(profile.get("window_glazing")):
        observations.append(f"Window glazing: {profile['window_glazing']}; this is descriptive and is not converted into a window U-value.")
    if _known(profile.get("main_orientation")):
        observations.append(f"Main glazing orientation recorded as {profile['main_orientation']}.")
    if _known(profile.get("shading")):
        observations.append(f"Solar shading recorded as {profile['shading']}.")
    if _known(profile.get("heating_distribution")):
        observations.append("Heat distribution recorded as " + str(profile["heating_distribution"]) + ".")
    if _known(profile.get("thermal_mass")):
        observations.append("Building thermal mass recorded as " + str(profile["thermal_mass"]) + ".")
    screed = _number(profile.get("ufh_screed_cm"))
    if screed is not None:
        observations.append(f"Underfloor-heating screed thickness recorded as {screed:g} cm.")
    if _known(profile.get("ventilation_type")):
        observations.append("Ventilation arrangement recorded as " + str(profile["ventilation_type"]) + ".")
    if _known(profile.get("internal_gains")):
        observations.append("Internal heat gains classified as " + str(profile["internal_gains"]) + ".")
    occupants = _number(profile.get("occupants"))
    if occupants is not None:
        observations.append(f"Typical occupancy recorded as {int(occupants)} people.")
    construction_year = _number(profile.get("construction_year"))
    if construction_year is not None:
        observations.append(f"Construction year recorded as {int(construction_year)}; no regulatory or retrofit standard is assumed from year alone.")
    if _known(profile.get("staircase_layout")):
        observations.append(f"Inter-floor staircase connection recorded as {profile['staircase_layout']}.")

    window_areas = {
        direction: _number(profile.get(f"window_area_{direction}_m2"))
        for direction in ("north", "south", "east", "west")
    }
    total_window_area = round(sum(value for value in window_areas.values() if value is not None and value > 0), 2)
    positive_window_areas = {key: value for key, value in window_areas.items() if value is not None and value > 0}
    largest_window_orientation = max(positive_window_areas, key=positive_window_areas.get) if positive_window_areas else None
    if largest_window_orientation:
        observations.append(
            f"Largest configured window area faces {largest_window_orientation} ({positive_window_areas[largest_window_orientation]:g} m²). "
            "This indicates orientation only; actual solar gain depends on glazing properties, shading and weather."
        )

    room_areas = [
        area for room in rooms.values()
        if (area := _number(room.get("area_m2", room.get("area")))) is not None and area > 0
    ]
    configured_area = round(sum(room_areas), 2)
    room_count = len(rooms)
    ach_values = [
        ach for room in rooms.values()
        if (ach := _number(room.get("air_changes_per_hour"))) is not None and ach > 0
    ]
    average_ach = round(sum(ach_values) / len(ach_values), 3) if ach_values else None
    if ach_values:
        observations.append(
            f"User-entered room air-change measurements are available for {len(ach_values)} rooms; "
            f"their unweighted average is {average_ach:g} air changes per hour."
        )

    performance = {key: efficiency.get(key) for key in _PERFORMANCE_KEYS if efficiency.get(key) is not None}
    if efficiency.get("heat_loss_u_value_30d") is not None:
        performance_status = "30_day_estimate_available"
    elif efficiency.get("heat_loss_u_value_7d") is not None:
        performance_status = "7_day_estimate_available"
    elif efficiency.get("heat_loss_u_value_daily") is not None:
        performance_status = "daily_estimate_available"
    else:
        performance_status = "collecting_data"

    return {
        "schema_version": 2,
        "profile_completeness_percent": profile_percent,
        "profile_known_fields": known_count,
        "profile_total_fields": len(all_fields),
        "sections": sections,
        "observations": observations,
        "building_characteristics": {
            "wall_insulation_cm": wall,
            "wall_insulation_material": profile.get("wall_insulation_material"),
            "roof_insulation_material": profile.get("roof_insulation_material"),
            "floor_insulation_material": profile.get("floor_insulation_material"),
            "roof_insulation_cm": roof,
            "floor_insulation_cm": floor,
            "window_glazing": profile.get("window_glazing"),
            "main_orientation": profile.get("main_orientation"),
            "shading": profile.get("shading"),
            "ventilation_type": profile.get("ventilation_type"),
            "heating_distribution": profile.get("heating_distribution"),
            "thermal_mass": profile.get("thermal_mass"),
            "ufh_screed_cm": _number(profile.get("ufh_screed_cm")),
            "occupants": _number(profile.get("occupants")),
            "internal_gains": profile.get("internal_gains"),
            "boiler_model": profile.get("boiler_model"),
            "boiler_dhw": profile.get("boiler_dhw"),
            "buffer_tank": profile.get("buffer_tank"),
            "ac_heating": profile.get("ac_heating"),
            "ac_units": _number(profile.get("ac_units")),
            "daily_window_ventilation": profile.get("daily_window_ventilation"),
            "occupancy_pattern": profile.get("occupancy_pattern"),
            "staircase_layout": profile.get("staircase_layout"),
            "window_area_by_orientation_m2": window_areas,
            "total_configured_window_area_m2": total_window_area,
            "largest_window_orientation": largest_window_orientation,
        },
        "room_coverage": {
            "configured_rooms": room_count,
            "rooms_with_area": len(room_areas),
            "room_area_coverage_percent": round(100 * len(room_areas) / room_count) if room_count else None,
            "configured_room_area_m2": configured_area,
            "rooms_with_measured_ach": len(ach_values),
            "average_measured_ach": average_ach,
            "minimum_measured_ach": min(ach_values) if ach_values else None,
            "maximum_measured_ach": max(ach_values) if ach_values else None,
        },
        "measured_performance": performance,
        "measurement_assumptions": {
            "boiler_efficiency": efficiency.get("thermal_model_boiler_efficiency_assumption"),
            "ac_heating_cop": efficiency.get("thermal_model_ac_heating_cop_assumption"),
            "envelope_area_m2": efficiency.get("thermal_model_envelope_area_m2"),
            "valid_days_7d": efficiency.get("thermal_model_valid_days_7d"),
            "valid_days_30d": efficiency.get("thermal_model_valid_days_30d"),
        },
        "performance_status": performance_status,
        "performance_data_quality": (
            "30_day" if efficiency.get("heat_loss_u_value_30d") is not None
            else "7_day" if efficiency.get("heat_loss_u_value_7d") is not None
            else "daily" if efficiency.get("heat_loss_u_value_daily") is not None
            else "insufficient_history"
        ),
        "interpretation": (
            "Profile completeness measures how much building information is known; it is not an efficiency score. "
            "Thermal performance is estimated independently from completed-day energy and temperature observations, "
            "using configured boiler-efficiency and AC-COP assumptions. Insulation thickness and construction labels "
            "are not enough to derive certified U-values or an EPC rating."
        ),
    }
