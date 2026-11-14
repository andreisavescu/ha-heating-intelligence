"""Whole-house interpretation of live room CO2 and relative-humidity readings."""
from __future__ import annotations

from statistics import mean, median
from typing import Any


def _co2_value(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number >= 0 else None


def _humidity_value(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and 0 <= number <= 100 else None


def build_indoor_air_analysis(rooms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Summarise configured live readings without treating them as health diagnoses."""
    rooms = rooms or {}
    configured_co2 = [r for r in rooms.values() if r.get("co2_entity")]
    valid_co2 = [
        (r.get("name", room_id), float(r["co2_ppm"]))
        for room_id, r in rooms.items()
        if r.get("co2_entity") and _co2_value(r.get("co2_ppm")) is not None
    ]
    configured_humidity = [r for r in rooms.values() if r.get("humidity_entity")]
    valid_humidity = [
        (r.get("name", room_id), float(r["humidity"]))
        for room_id, r in rooms.items()
        if r.get("humidity_entity") and _humidity_value(r.get("humidity")) is not None
    ]

    highest_co2 = max(valid_co2, key=lambda item: item[1], default=None)
    if not configured_co2:
        co2_status = "not_configured"
    elif not valid_co2:
        co2_status = "waiting_for_readings"
    elif highest_co2[1] < 800:
        co2_status = "good"
    elif highest_co2[1] < 1200:
        co2_status = "elevated"
    elif highest_co2[1] < 2000:
        co2_status = "high"
    else:
        co2_status = "very_high"

    high_humidity = [name for name, value in valid_humidity if value > 60]
    low_humidity = [name for name, value in valid_humidity if value < 30]
    if not configured_humidity:
        humidity_status = "not_configured"
    elif not valid_humidity:
        humidity_status = "waiting_for_readings"
    elif high_humidity:
        humidity_status = "elevated"
    elif low_humidity:
        humidity_status = "low"
    else:
        humidity_status = "comfortable"

    room_assessments = {}
    for room_id, room in rooms.items():
        co2 = _co2_value(room.get("co2_ppm")) if room.get("co2_entity") else None
        humidity = _humidity_value(room.get("humidity")) if room.get("humidity_entity") else None
        findings = []
        if co2 is not None and co2 >= 2000:
            findings.append("CO2 is very high; ventilate the room and verify the sensor.")
        elif co2 is not None and co2 >= 1200:
            findings.append("CO2 is high; ventilation is likely beneficial.")
        elif co2 is not None and co2 >= 800:
            findings.append("CO2 is elevated compared with the lower indicative band.")
        if humidity is not None and humidity > 60:
            findings.append("Relative humidity is above 60%; monitor the trend and ventilation.")
        elif humidity is not None and humidity < 30:
            findings.append("Relative humidity is below 30%; indoor air is dry.")
        has_reading = co2 is not None or humidity is not None
        status = "attention" if findings else "good" if has_reading else "no_readings"
        room_assessments[room_id] = {
            "room_name": room.get("name", room_id),
            "status": status,
            "co2_ppm": round(co2) if co2 is not None else None,
            "humidity_percent": round(humidity, 1) if humidity is not None else None,
            "measured_ach": _number(room.get("air_changes_per_hour")),
            "observations": findings,
            "recommendation": " ".join(findings) if findings else (
                "Configured readings are within the indicative bands." if has_reading
                else "Configured sensors are unavailable or not reporting numeric values; check their Home Assistant states."
                if room.get("co2_entity") or room.get("humidity_entity")
                else "Configure a CO2 or humidity sensor to enable room air analysis."
            ),
        }

    ventilation_rooms = [
        {"name": name, "co2_ppm": round(value)}
        for name, value in valid_co2 if value >= 1200
    ]
    observations = []
    if highest_co2 and highest_co2[1] >= 800:
        observations.append(
            f"Highest configured room CO2 is {round(highest_co2[1])} ppm in {highest_co2[0]}."
        )
    if high_humidity:
        observations.append("Relative humidity is above 60% in: " + ", ".join(high_humidity) + ".")
    if low_humidity:
        observations.append("Relative humidity is below 30% in: " + ", ".join(low_humidity) + ".")
    if not observations:
        observations.append("No configured room currently crosses the indicative CO2 or humidity bands.")

    floor_groups: dict[str, list[dict[str, Any]]] = {}
    for room in rooms.values():
        floor_groups.setdefault(room.get("floor", "unassigned"), []).append(room)
    by_floor = {}
    for floor, floor_rooms in floor_groups.items():
        floor_co2 = [_co2_value(room.get("co2_ppm")) for room in floor_rooms if room.get("co2_entity")]
        floor_humidity = [_humidity_value(room.get("humidity")) for room in floor_rooms if room.get("humidity_entity")]
        floor_ach = [_number(room.get("air_changes_per_hour")) for room in floor_rooms]
        floor_co2 = [value for value in floor_co2 if value is not None]
        floor_humidity = [value for value in floor_humidity if value is not None]
        floor_ach = [value for value in floor_ach if value is not None and value > 0]
        by_floor[floor] = {
            "rooms": len(floor_rooms),
            "co2_rooms_with_readings": len(floor_co2),
            "average_co2_ppm": round(mean(floor_co2)) if floor_co2 else None,
            "highest_co2_ppm": round(max(floor_co2)) if floor_co2 else None,
            "humidity_rooms_with_readings": len(floor_humidity),
            "average_humidity_percent": round(mean(floor_humidity), 1) if floor_humidity else None,
            "measured_ach_rooms": len(floor_ach),
            "average_measured_ach": round(mean(floor_ach), 3) if floor_ach else None,
        }

    return {
        "configured_rooms": len(rooms),
        "rooms": room_assessments,
        "by_floor": by_floor,
        "co2": {
            "configured_rooms": len(configured_co2),
            "rooms_with_readings": len(valid_co2),
            "coverage_percent": round(100 * len(valid_co2) / len(configured_co2)) if configured_co2 else None,
            "average_ppm": round(mean(value for _, value in valid_co2)) if valid_co2 else None,
            "median_ppm": round(median(value for _, value in valid_co2)) if valid_co2 else None,
            "highest_room": {"name": highest_co2[0], "ppm": round(highest_co2[1])} if highest_co2 else None,
            "status": co2_status,
            "rooms_requiring_ventilation_attention": ventilation_rooms,
        },
        "humidity": {
            "configured_rooms": len(configured_humidity),
            "rooms_with_readings": len(valid_humidity),
            "coverage_percent": round(100 * len(valid_humidity) / len(configured_humidity)) if configured_humidity else None,
            "average_percent": round(mean(value for _, value in valid_humidity), 1) if valid_humidity else None,
            "highest_percent": round(max((value for _, value in valid_humidity), default=0), 1) if valid_humidity else None,
            "lowest_percent": round(min((value for _, value in valid_humidity), default=0), 1) if valid_humidity else None,
            "rooms_above_60_percent": high_humidity,
            "rooms_below_30_percent": low_humidity,
            "status": humidity_status,
        },
        "observations": observations,
        "interpretation": (
            "CO2 is an indicative ventilation and occupancy proxy; relative humidity is a "
            "room-condition indicator. Whole-house averages can hide individual rooms, so "
            "the highest CO2 and room-level exceptions are shown separately. These readings "
            "are not health, mould, or building-damage diagnoses."
        ),
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
