"""Explicit room envelope conductance from measured areas and U-values."""
from __future__ import annotations

from typing import Any

BOUNDARIES = ("outdoor", "ground", "attic", "adjacent_heated")


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and number < float("inf") else None


def build_envelope_conductance(options: dict) -> dict:
    """Calculate UA by room and boundary; unknown geometry stays unknown."""
    rooms = options.get("rooms", []) or []
    surfaces = options.get("envelope_surfaces", []) or []
    windows = options.get("windows", []) or []
    room_ids = {room.get("id") for room in rooms}
    totals = {rid: {boundary: 0.0 for boundary in BOUNDARIES} for rid in room_ids}
    counts = {rid: {boundary: 0 for boundary in BOUNDARIES} for rid in room_ids}
    incomplete = []
    invalid = []
    records = []
    for item in surfaces:
        rid, boundary = item.get("room_id"), item.get("boundary")
        area, u_value = _positive(item.get("area_m2")), _positive(item.get("u_value_w_m2k"))
        if rid not in room_ids or boundary not in BOUNDARIES:
            invalid.append(item.get("name", item.get("id")))
            continue
        if area is None or u_value is None:
            incomplete.append(item.get("name", item.get("id")))
            continue
        ua = area * u_value
        totals[rid][boundary] += ua
        counts[rid][boundary] += 1
        records.append({"id": item.get("id"), "name": item.get("name"), "room_id": rid,
                        "boundary": boundary, "area_m2": area, "u_value_w_m2k": u_value,
                        "conductance_w_per_k": round(ua, 4)})
    window_records = []
    for item in windows:
        rid = item.get("room_id")
        glass, frame = _positive(item.get("glass_area_m2")), item.get("frame_area_m2")
        uw = _positive(item.get("whole_window_u_value_w_m2k"))
        frame = _positive(frame) if frame not in (None, "") else None
        if rid not in room_ids:
            invalid.append(item.get("name", item.get("id")))
            continue
        if glass is None or frame is None or uw is None:
            if uw is not None or item.get("whole_window_u_value_w_m2k") is not None:
                incomplete.append(item.get("name", item.get("id")))
            continue
        gross = glass + frame
        ua = gross * uw
        totals[rid]["outdoor"] += ua
        counts[rid]["outdoor"] += 1
        window_records.append({"id": item.get("id"), "name": item.get("name"), "room_id": rid,
                               "gross_area_m2": round(gross, 4), "whole_window_u_value_w_m2k": uw,
                               "conductance_w_per_k": round(ua, 4)})
    room_results = {}
    for rid in room_ids:
        room_results[rid] = {
            "conductance_by_boundary_w_per_k": {b: round(v, 4) if counts[rid][b] else None for b, v in totals[rid].items()},
            "known_conductance_w_per_k": round(sum(totals[rid].values()), 4) if any(counts[rid].values()) else None,
            "configured_surface_count": sum(counts[rid].values()),
            "boundary_surface_counts": counts[rid],
        }
    return {
        "status": "incomplete" if incomplete or invalid or not records and not window_records else "configured",
        "rooms": room_results,
        "surfaces": records,
        "windows": window_records,
        "incomplete_records": incomplete,
        "invalid_records": invalid,
        "known_surface_count": len(records) + len(window_records),
        "method": "UA = measured gross area × explicitly supplied whole-assembly U-value; boundary conductances remain separate. Missing U-values or areas are not inferred.",
        "limitations": ["Window contribution requires gross glass-plus-frame area and whole-window Uw; center-of-glass Ug is not accepted as Uw.",
                       "Adjacent-heated boundaries are reported separately and are not treated as outdoor heat loss.",
                       "This is an explicit geometry inventory, not a measurement of instantaneous heat flow."],
    }
