"""Separated diagnostic audits for whole-house model readiness."""
from __future__ import annotations

from typing import Any

from .const import (
    CONF_ROOMS, CONF_PUMPS, CONF_HOUSE_ASSESSMENT, CONF_WINDOWS,
    CONF_OUTDOOR_TEMPERATURE_ENTITY, CONF_SOLAR_RADIATION_ENTITY,
    CONF_ATTIC_TEMPERATURE_ENTITY, CONF_ATTIC_HUMIDITY_ENTITY,
    CONF_WATER_TEMPERATURE_ENTITY, CONF_RETURN_WATER_TEMPERATURE_ENTITY,
    CONF_WATER_FLOW_RATE_ENTITY, CONF_HEATING_GAS_ENTITY,
    CONF_BOILER_BURNER_ENTITY, CONF_BOILER_MODULATION_ENTITY,
    CONF_AC_POWER_ENTITY, CONF_AC_ENERGY_ENTITY, CONF_DELTA_T_ENTITY,
)


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
            "gross_difference_m2": round(values["gross_m2"] - reference, 3) if reference is not None and values["window_count"] == values["frame_area_known_count"] else None,
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
        if values["gross_difference_m2"] is not None and abs(values["gross_difference_m2"]) > 0.1
    ]
    orientations_without_complete_frame_area = [
        orientation for orientation, values in comparisons.items()
        if values["window_count"] and not values["frame_area_complete"]
    ]
    return {
        "status": "needs_window_records" if not windows else "review_required" if incomplete or invalid_room_windows or discrepancies else "configured",
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
        "reference_semantics": "Existing house-assessment orientation totals are treated as gross/frame-inclusive reference areas; individual records separately report glass and frame areas. Differences are diagnostic only.",
    }


def build_thermal_readiness_audit(options: dict, rooms: dict, house_audit: dict, attic: dict) -> dict:
    """Report explicit thermal boundaries and model parameter gaps without filling them."""
    profile = options.get(CONF_HOUSE_ASSESSMENT, {}) or {}
    room_list = options.get(CONF_ROOMS, []) or []
    room_parameters = {}
    for room in room_list:
        rid = room.get("id")
        room_parameters[rid] = {
            "name": room.get("name"),
            "floor": room.get("floor"),
            "area_m2": _number(room.get("area_m2", room.get("area"))),
            "volume_m3": _number(room.get("volume_m3")),
            "envelope_conductance_w_per_k": _number(room.get("envelope_conductance_w_per_k")),
            "attic_floor_conductance_w_per_k": _number(room.get("attic_floor_conductance_w_per_k")),
            "temperature_sensor_configured": bool(room.get("temperature_entity")),
            "missing": [
                key for key, value in (
                    ("area_m2", _number(room.get("area_m2", room.get("area")))),
                    ("volume_m3", _number(room.get("volume_m3"))),
                ) if value is None
            ],
        }
    connections = profile.get("room_connections", []) or []
    attic_shares = profile.get("attic_floor_room_conductances_w_per_k", {}) or {}
    missing_envelope = [rid for rid, item in room_parameters.items() if item["envelope_conductance_w_per_k"] is None]
    upstairs_ids = [room.get("id") for room in room_list if room.get("floor") == "upstairs"]
    missing_attic = [rid for rid in upstairs_ids if rid not in attic_shares]
    return {
        "status": "incomplete" if missing_envelope or missing_attic or not connections else "configured",
        "room_parameters": room_parameters,
        "rooms_missing_envelope_conductance": missing_envelope,
        "configured_inter_room_connections": connections,
        "configured_attic_floor_conductances_w_per_k": attic_shares,
        "upstairs_rooms_without_attic_conductance": missing_attic,
        "attic_measurements": {
            "temperature_sensor_configured": bool(options.get(CONF_ATTIC_TEMPERATURE_ENTITY)),
            "humidity_sensor_configured": bool(options.get(CONF_ATTIC_HUMIDITY_ENTITY)),
            "latest_analysis": {
                key: attic.get(key) for key in ("status", "attic_to_outdoor_delta_c", "upstairs_to_attic_delta_c")
                if attic.get(key) is not None
            },
        },
        "window_geometry_status": house_audit.get("status"),
        "model_parameter_policy": "Missing capacities, conductances and U-values remain unknown; no physical values are inferred from descriptive construction labels.",
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
