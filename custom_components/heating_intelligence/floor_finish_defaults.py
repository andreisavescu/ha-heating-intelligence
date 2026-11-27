"""One-time room floor-finish defaults for existing installations."""
from __future__ import annotations

from typing import Any

PRESETS = {
    "ceramic_tile": {
        "conductivity_w_mk": 1.3,
        "density_kg_m3": 2300.0,
        "specific_heat_j_kgk": 840.0,
        "thickness_mm": 10.0,
        "name": "Ceramic tile",
    },
    "wood_parquet": {
        "conductivity_w_mk": 0.15,
        "density_kg_m3": 700.0,
        "specific_heat_j_kgk": 1600.0,
        "thickness_mm": 10.0,
        "name": "Wood / parquet",
    },
}


def default_floor_finish_zones(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create one editable full-room finish zone using the configured floor.

    These are explicit material priors, not measured properties. Existing
    installations receive them only when the floor_finish_zones option is
    absent; an explicitly empty list remains an intentional opt-out.
    """
    zones: list[dict[str, Any]] = []
    for room in rooms:
        room_id = str(room.get("id") or "").strip()
        try:
            area = float(room.get("area_m2"))
        except (TypeError, ValueError):
            continue
        if not room_id or area <= 0:
            continue
        material = "wood_parquet" if room.get("floor") == "upstairs" else "ceramic_tile"
        preset = PRESETS[material]
        zones.append({
            "id": f"default_{room_id}_floor_finish",
            "name": preset["name"],
            "room_id": room_id,
            "material": material,
            "area_m2": area,
            "thickness_mm": preset["thickness_mm"],
            "conductivity_w_mk": preset["conductivity_w_mk"],
            "density_kg_m3": preset["density_kg_m3"],
            "specific_heat_j_kgk": preset["specific_heat_j_kgk"],
            "source": "one_time_floor_based_default_material_prior",
        })
    return zones
