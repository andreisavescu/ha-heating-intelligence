"""Separated diagnostic audits for whole-house model readiness."""
from __future__ import annotations

from typing import Any

# Keep these audit builders independent of Home Assistant imports so their
# deterministic calculations can be unit-tested in a lightweight Python env.
CONF_ROOMS = "rooms"
CONF_HOUSE_ASSESSMENT = "house_assessment"
CONF_WINDOWS = "windows"
CONF_OUTDOOR_TEMPERATURE_ENTITY = "outdoor_temperature_entity"
CONF_SOLAR_RADIATION_ENTITY = "solar_radiation_entity"
CONF_ATTIC_TEMPERATURE_ENTITY = "attic_temperature_entity"
CONF_ATTIC_HUMIDITY_ENTITY = "attic_humidity_entity"
CONF_WATER_TEMPERATURE_ENTITY = "water_temperature_entity"
CONF_RETURN_WATER_TEMPERATURE_ENTITY = "return_water_temperature_entity"
CONF_WATER_FLOW_RATE_ENTITY = "water_flow_rate_entity"
CONF_HEATING_GAS_ENTITY = "heating_gas_entity"
CONF_BOILER_BURNER_ENTITY = "boiler_burner_entity"
CONF_BOILER_MODULATION_ENTITY = "boiler_modulation_entity"
CONF_AC_POWER_ENTITY = "ac_power_entity"
CONF_AC_ENERGY_ENTITY = "ac_energy_entity"
CONF_DELTA_T_ENTITY = "delta_t_entity"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def build_house_audit(options: dict, rooms: dict, windows: list[dict]) -> dict:
    """Summarise configured geometry and compare window records to house totals."""
    profile = options.get(CONF_HOUSE_ASSESSMENT, {}) or {}
    room_list = options.get(CONF_ROOMS, []) or []
    room_ids = {room.get("id") for room in room_list}
    by_orientation: dict[str, dict[str, Any]] = {
        key: {"glass_m2": 0.0, "known_frame_m2": 0.0, "gross_m2": 0.0, "window_count": 0, "frame_area_known_count": 0}
        for key in ("north", "east", "south", "west")
    }
    invalid_room_windows = []
    incomplete = []
    for item in windows:
        orientation = item.get("orientation")
        glass = _number(item.get("glass_area_m2"))
        frame = _number(item.get("frame_area_m2"))
        if item.get("room_id") not in room_ids:
            invalid_room_windows.append(item.get("name", item.get("id")))
        if orientation not in by_orientation or glass is None or glass <= 0:
            incomplete.append(item.get("name", item.get("id")))
            continue
        by_orientation[orientation]["glass_m2"] += glass
        by_orientation[orientation]["window_count"] += 1
        if frame is not None:
            by_orientation[orientation]["known_frame_m2"] += frame
            by_orientation[orientation]["frame_area_known_count"] += 1
            by_orientation[orientation]["gross_m2"] += glass + frame

    comparisons = {}
    for orientation, values in by_orientation.items():
        reference = _number(profile.get(f"window_area_{orientation}_m2"))
        comparisons[orientation] = {
            "glass_m2": round(values["glass_m2"], 3),
            "known_frame_area_m2": round(values["known_frame_m2"], 3),
            "gross_m2": round(values["gross_m2"], 3) if values["window_count"] == values["frame_area_known_count"] else None,
            "window_count": values["window_count"],
            "frame_area_known_count": values["frame_area_known_count"],
            "frame_area_complete": values["window_count"] == values["frame_area_known_count"],
            "house_assessment_reference_m2": reference,
            "glass_difference_m2": round(values["glass_m2"] - reference, 3) if reference is not None else None,
            "reference_area_basis": "glass",
        }
    known_references = [
        _number(profile.get(f"window_area_{orientation}_m2"))
        for orientation in by_orientation
    ]
    known_references = [value for value in known_references if value is not None]
    room_area_by_floor = {"ground_floor": 0.0, "upstairs": 0.0, "unassigned": 0.0}
    rooms_missing_area = []
    for room in room_list:
        area = _number(room.get("area_m2", room.get("area")))
        floor = room.get("floor") if room.get("floor") in ("ground_floor", "upstairs") else "unassigned"
        if area is None or area <= 0:
            rooms_missing_area.append(room.get("name", room.get("id")))
        else:
            room_area_by_floor[floor] += area
    discrepancies = [
        orientation for orientation, values in comparisons.items()
        if values["glass_difference_m2"] is not None and abs(values["glass_difference_m2"]) > 0.1
    ]
    orientations_without_complete_frame_area = [
        orientation for orientation, values in comparisons.items()
        if values["window_count"] and not values["frame_area_complete"]
    ]
    return {
        "status": (
            "needs_window_records" if not windows
            else "review_required" if incomplete or invalid_room_windows or discrepancies
            else "frame_areas_incomplete" if orientations_without_complete_frame_area
            else "configured"
        ),
        "configured_rooms": len(room_list),
        "rooms_missing_floor_area": rooms_missing_area,
        "configured_room_area_by_floor_m2": {key: round(value, 2) for key, value in room_area_by_floor.items()},
        "configured_room_area_total_m2": round(sum(room_area_by_floor.values()), 2),
        "configured_windows": len(windows),
        "windows_missing_geometry_or_orientation": incomplete,
        "windows_with_unknown_room": invalid_room_windows,
        "orientations_with_over_0_1_m2_reference_difference": discrepancies,
        "orientations_without_complete_frame_area": orientations_without_complete_frame_area,
        "window_area_by_orientation": comparisons,
        "individual_windows": [
            {key: item.get(key) for key in (
                "id", "name", "room_id", "orientation", "glass_area_m2",
                "frame_area_m2", "glazing", "frame_material", "shading",
                "shading_behaviour",
            )}
            for item in windows
        ],
        "building_profile": dict(profile),
        "individual_windows_glass_total_m2": round(sum(v["glass_m2"] for v in by_orientation.values()), 3),
        "individual_windows_known_frame_area_total_m2": round(sum(v["known_frame_m2"] for v in by_orientation.values()), 3),
        "individual_windows_frame_area_complete": sum(v["window_count"] for v in by_orientation.values()) == sum(v["frame_area_known_count"] for v in by_orientation.values()),
        "individual_windows_gross_total_m2": (
            round(sum(v["gross_m2"] for v in by_orientation.values()), 3)
            if sum(v["window_count"] for v in by_orientation.values()) == sum(v["frame_area_known_count"] for v in by_orientation.values())
            else None
        ),
        "house_assessment_reference_total_m2": round(sum(known_references), 3) if len(known_references) == 4 else None,
        "reference_semantics": "House-assessment orientation totals are compared with glass area only. Frame area is reported separately and is never added to the reference comparison.",
    }


def build_thermal_readiness_audit(
    options: dict, rooms: dict, house_audit: dict, attic: dict,
    envelope_audit: dict | None = None,
) -> dict:
    """Report thermal readiness using only explicit envelope surface records."""
    profile = options.get(CONF_HOUSE_ASSESSMENT, {}) or {}
    room_list = options.get(CONF_ROOMS, []) or []
    room_parameters = {}
    envelope_audit = envelope_audit or {}
    calculated_rooms = envelope_audit.get("rooms", {}) or {}
    connections = profile.get("room_connections", []) or []
    missing_area_or_volume = []
    missing_attic = []
    missing_outdoor = []

    for room in room_list:
        rid = room.get("id")
        calculated = calculated_rooms.get(rid, {}) or {}
        by_boundary = calculated.get("conductance_by_boundary_w_per_k", {}) or {}
        opaque_counts = calculated.get("opaque_surface_counts", {}) or {}
        outdoor_opaque_count = int(opaque_counts.get("outdoor") or 0)
        window_count = int(calculated.get("window_record_count") or 0)
        ground_count = int(opaque_counts.get("ground") or 0)
        attic_count = int(opaque_counts.get("attic") or 0)
        area = _number(room.get("area_m2", room.get("area")))
        volume = _number(room.get("volume_m3"))
        floor = room.get("floor")
        outdoor_g = _number(by_boundary.get("outdoor"))
        ground_g = _number(by_boundary.get("ground"))
        attic_g = _number(by_boundary.get("attic"))
        if area is None or volume is None:
            missing_area_or_volume.append(rid)
        if floor == "upstairs" and (attic_count == 0 or attic_g is None or attic_g <= 0):
            missing_attic.append(rid)
        if outdoor_opaque_count == 0 and window_count == 0:
            missing_outdoor.append(rid)

        room_parameters[rid] = {
            "name": room.get("name"),
            "floor": floor,
            "area_m2": area,
            "volume_m3": volume,
            "outdoor_opaque_surface_count": outdoor_opaque_count,
            "outdoor_window_record_count": window_count,
            "outdoor_surface_inventory_status": (
                "configured" if outdoor_g is not None and outdoor_g > 0
                else "no_outdoor_surface_records"
            ),
            "room_to_outdoors_surface_calculated_w_per_k": outdoor_g,
            "ground_surface_count": ground_count,
            "room_to_ground_surface_calculated_w_per_k": ground_g,
            "attic_surface_count": attic_count,
            "room_to_attic_surface_calculated_w_per_k": attic_g,
            "surface_conductance_by_boundary_w_per_k": by_boundary,
            "configured_surface_count": calculated.get("configured_surface_count", 0),
            "temperature_sensor_configured": bool(room.get("temperature_entity")),
            "missing": [
                key for key, value in (("area_m2", area), ("volume_m3", volume))
                if value is None
            ],
        }

    surface_review_required = envelope_audit.get("status") in ("review_required", "incomplete")
    return {
        "status": (
            "incomplete" if missing_area_or_volume or missing_attic
            else "review_required" if surface_review_required
            else "configured"
        ),
        "room_parameters": room_parameters,
        "rooms_missing_area_or_volume": missing_area_or_volume,
        "upstairs_rooms_missing_attic_surface_records": missing_attic,
        "rooms_without_outdoor_surface_records": missing_outdoor,
        "configured_inter_room_connections": connections,
        "inter_room_connection_status": "configured" if connections else "not_configured",
        "inter_room_connections_required_for_multizone_model": True,
        "envelope_surface_audit_status": envelope_audit.get("status", "not_available"),
        "envelope_surface_boundary_totals_by_room": calculated_rooms,
        "boundary_interpretation": {
            "outdoor": "Calculated exclusively from configured opaque outdoor surfaces and whole-window records.",
            "ground": "Calculated exclusively from configured ground-facing surface records.",
            "attic": "Calculated exclusively from configured attic-facing surface records.",
            "legacy_room_level_conductances": "Ignored. Room-level outdoor and attic conductance settings are no longer model inputs or fallbacks.",
        },
        "attic_measurements": {
            "temperature_sensor_configured": bool(options.get(CONF_ATTIC_TEMPERATURE_ENTITY)),
            "humidity_sensor_configured": bool(options.get(CONF_ATTIC_HUMIDITY_ENTITY)),
            "latest_analysis": {
                key: attic.get(key) for key in ("status", "attic_to_outdoor_delta_c", "upstairs_to_attic_delta_c")
                if attic.get(key) is not None
            },
        },
        "window_geometry_status": house_audit.get("status"),
        "model_parameter_policy": "External conductances are calculated only from explicit surface records and window records. Missing capacities and inter-room conductances remain unknown; no physical values are inferred.",
    }


def build_measurement_audit(options: dict, rooms: dict, data: dict) -> dict:
    """Inventory configured measurement sources and observed availability."""
    room_list = options.get(CONF_ROOMS, []) or []
    configured = {
        "outdoor_temperature": options.get(CONF_OUTDOOR_TEMPERATURE_ENTITY),
        "solar_radiation": options.get(CONF_SOLAR_RADIATION_ENTITY),
        "attic_temperature": options.get(CONF_ATTIC_TEMPERATURE_ENTITY),
        "attic_humidity": options.get(CONF_ATTIC_HUMIDITY_ENTITY),
        "water_supply_temperature": options.get(CONF_WATER_TEMPERATURE_ENTITY),
        "water_return_temperature": options.get(CONF_RETURN_WATER_TEMPERATURE_ENTITY),
        "water_flow_rate": options.get(CONF_WATER_FLOW_RATE_ENTITY),
        "heating_gas": options.get(CONF_HEATING_GAS_ENTITY),
        "burner_state": options.get(CONF_BOILER_BURNER_ENTITY),
        "burner_modulation": options.get(CONF_BOILER_MODULATION_ENTITY),
        "ac_power": options.get(CONF_AC_POWER_ENTITY),
        "ac_energy": options.get(CONF_AC_ENERGY_ENTITY),
        "delta_t": options.get(CONF_DELTA_T_ENTITY),
    }
    room_sources = {
        room.get("id"): {
            "temperature": room.get("temperature_entity"),
            "humidity": room.get("humidity_entity"),
            "co2": room.get("co2_entity"),
            "demand": room.get("demand_entity"),
            "window_contact": room.get("window_entity"),
        } for room in room_list
    }
    calibration = data.get("heat_delivery_calibration", {}) or {}
    return {
        "status": "configured" if any(configured.values()) or any(any(v.values()) for v in room_sources.values()) else "sparse",
        "global_sources": configured,
        "configured_global_source_count": sum(bool(value) for value in configured.values()),
        "room_sources": room_sources,
        "rooms_with_temperature": sum(bool(room.get("temperature_entity")) for room in room_list),
        "rooms_with_humidity": sum(bool(room.get("humidity_entity")) for room in room_list),
        "rooms_with_co2": sum(bool(room.get("co2_entity")) for room in room_list),
        "rooms_with_demand": sum(bool(room.get("demand_entity")) for room in room_list),
        "rooms_with_window_contact": sum(bool(room.get("window_entity")) for room in room_list),
        "water_side_measurement": {
            "supply_temperature_configured": bool(configured["water_supply_temperature"]),
            "return_temperature_configured": bool(configured["water_return_temperature"]),
            "flow_rate_configured": bool(configured["water_flow_rate"]),
            "calibration_status": calibration.get("water_side_measurement_status"),
        },
        "measurement_interpretation": "Configured entity references are not proof of valid historical coverage. Return-water temperature and measured flow are reported as optional measurement gaps, not treated as prerequisites for continuing model identification.",
    }
