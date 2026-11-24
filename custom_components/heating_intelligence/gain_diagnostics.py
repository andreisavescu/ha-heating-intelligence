"""Diagnostic-only solar and internal-gain evidence.

This module deliberately reports data coverage and observational evidence only.
It does not estimate watts, alter room learning, or feed gains into the RC solver.
"""
from __future__ import annotations

import math
from typing import Any


ORIENTATIONS = ("north", "east", "south", "west")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _area_by_orientation(profile: dict[str, Any]) -> dict[str, float | None]:
    result = {}
    for orientation in ORIENTATIONS:
        value = _number(profile.get(f"window_area_{orientation}_m2"))
        result[orientation] = value if value is not None and value >= 0 else None
    return result


def build_gain_diagnostics(
    samples: list[dict[str, Any]] | None,
    rooms: list[dict[str, Any]],
    house_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarise solar configuration and room-level free-gain evidence.

    CO2 and humidity changes are treated as indirect occupancy evidence only.
    A single global irradiance sensor is not converted into façade irradiance.
    """
    samples = samples or []
    profile = house_assessment or {}
    areas = _area_by_orientation(profile)
    configured_area = sum(value for value in areas.values() if value is not None)
    known_orientations = sum(value is not None for value in areas.values())
    solar_configured = bool(profile.get("window_glazing") or profile.get("shading"))
    solar_status = (
        "orientation_areas_configured"
        if known_orientations == len(ORIENTATIONS)
        else "partial_orientation_areas"
        if known_orientations
        else "awaiting_window_geometry"
    )

    parsed_samples = []
    for sample in samples:
        channels = sample.get("channels") or {}
        parsed_samples.append((sample, channels))

    room_results: dict[str, dict[str, Any]] = {}
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        co2_values: list[float] = []
        humidity_values: list[float] = []
        temperature_values: list[float] = []
        co2_pairs = 0
        humidity_pairs = 0
        temperature_pairs = 0
        for sample, _channels in parsed_samples:
            state = (sample.get("rooms") or {}).get(room_id) or {}
            co2 = _number(state.get("co2"))
            humidity = _number(state.get("humidity"))
            temperature = _number(state.get("temperature"))
            if co2 is not None and 250 <= co2 <= 10000:
                co2_values.append(co2)
            if humidity is not None and 0 <= humidity <= 100:
                humidity_values.append(humidity)
            if temperature is not None and -20 <= temperature <= 60:
                temperature_values.append(temperature)
        co2_pairs = max(0, len(co2_values) - 1)
        humidity_pairs = max(0, len(humidity_values) - 1)
        temperature_pairs = max(0, len(temperature_values) - 1)
        co2_delta = (
            round(co2_values[-1] - co2_values[0], 1)
            if len(co2_values) >= 2 else None
        )
        humidity_delta = (
            round(humidity_values[-1] - humidity_values[0], 2)
            if len(humidity_values) >= 2 else None
        )
        temperature_delta = (
            round(temperature_values[-1] - temperature_values[0], 2)
            if len(temperature_values) >= 2 else None
        )
        co2_configured = bool(room.get("co2_entity"))
        humidity_configured = bool(room.get("humidity_entity"))
        evidence = []
        if co2_configured and co2_pairs:
            evidence.append("co2_trend_available")
        if humidity_configured and humidity_pairs:
            evidence.append("humidity_trend_available")
        if temperature_pairs:
            evidence.append("temperature_trend_available")
        if co2_configured and co2_pairs and humidity_configured and humidity_pairs:
            confidence = "medium"
        elif co2_configured and co2_pairs:
            confidence = "low"
        elif humidity_configured and humidity_pairs and temperature_pairs:
            confidence = "low"
        else:
            confidence = "unavailable"
        room_results[room_id] = {
            "co2_sensor_configured": co2_configured,
            "co2_samples": len(co2_values),
            "co2_delta_ppm_over_history": co2_delta,
            "humidity_sensor_configured": humidity_configured,
            "humidity_samples": len(humidity_values),
            "humidity_delta_percent_over_history": humidity_delta,
            "temperature_samples": len(temperature_values),
            "temperature_delta_c_over_history": temperature_delta,
            "indirect_occupancy_evidence": evidence,
            "confidence": confidence,
            "internal_gain_w": None,
            "status": "diagnostic_only_no_watt_estimate",
        }

    solar_sample_count = 0
    solar_units = set()
    for _sample, channels in parsed_samples:
        radiation = _number(channels.get("solar_radiation"))
        if radiation is not None and radiation >= 0:
            solar_sample_count += 1
            unit = str(channels.get("solar_radiation_unit") or "").strip()
            if unit:
                solar_units.add(unit)
    return {
        "status": "diagnostic_only",
        "solar": {
            "status": solar_status,
            "window_area_by_orientation_m2": areas,
            "configured_window_area_m2": round(configured_area, 3) if known_orientations else None,
            "known_orientations": known_orientations,
            "glazing": profile.get("window_glazing"),
            "shading": profile.get("shading"),
            "solar_radiation_samples": solar_sample_count,
            "solar_radiation_units_observed": sorted(solar_units),
            "solar_gain_w": None,
            "note": (
                "Global irradiance is not treated as façade irradiance. "
                "No solar watts are estimated until room/window mapping, "
                "orientation exposure and glazing/shading parameters are validated."
            ),
        },
        "internal_gains": {
            "status": "indirect_evidence_only",
            "rooms": room_results,
            "co2_sensor_rooms": [
                str(room.get("id", "unknown")) for room in rooms
                if room.get("co2_entity")
            ],
            "note": (
                "CO2 and humidity trends are indirect occupancy indicators, not "
                "calorimetry. Configured occupancy profiles are priors, not measured "
                "presence. Internal gain watts are intentionally withheld."
            ),
        },
        "solver_input_enabled": False,
        "room_learning_adjustment_enabled": False,
    }
