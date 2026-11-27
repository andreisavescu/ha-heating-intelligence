"""Stage 3: room effective and UFH screed thermal-capacity estimates."""
from __future__ import annotations

import math
from typing import Any

from .absolute_thermal_identification import identify_absolute_room_parameters

DEFAULT_UFH_SCREED_THICKNESS_CM = 4.5
# Ordinary cementitious screed/concrete: deliberately broad material prior.
VOLUMETRIC_HEAT_CAPACITY_WH_M3K = 550.0
VOLUMETRIC_HEAT_CAPACITY_RANGE_WH_M3K = (450.0, 650.0)


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def build_stage3_thermal_capacity(
    response_identification: dict[str, Any] | None,
    rooms: list[dict[str, Any]],
    envelope_conductance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Stage 3 outputs, keeping inferred room C separate from slab prior.

    The regression's 'envelope' predictor is outdoor temperature. Therefore
    only the explicitly configured outdoor UA is used as its absolute anchor;
    ground, attic and adjacent-heated boundaries remain separate.
    """
    envelope_rooms = (envelope_conductance or {}).get("rooms") or {}
    anchored_rooms = []
    for room in rooms:
        rid = str(room.get("id", "unknown"))
        boundaries = (envelope_rooms.get(rid) or {}).get(
            "conductance_by_boundary_w_per_k"
        ) or {}
        outdoor_ua = _positive(boundaries.get("outdoor"))
        legacy_anchor = _positive(room.get("envelope_conductance_w_per_k"))
        surface_room = envelope_rooms.get(rid) or {}
        opaque_outdoor_count = int(
            ((surface_room.get("opaque_surface_counts") or {}).get("outdoor") or 0)
        )
        has_surface_inventory = bool(envelope_rooms)
        anchor_incomplete = has_surface_inventory and opaque_outdoor_count == 0
        # A room with a populated surface inventory must have an opaque
        # outdoor surface before its outdoor UA can anchor absolute capacity.
        # Do not substitute the legacy room total for intentionally incomplete
        # downstairs records. Rooms without any surface inventory retain the
        # legacy configured anchor for backward compatibility.
        anchor = (
            None if anchor_incomplete
            else outdoor_ua if has_surface_inventory
            else legacy_anchor
        )
        anchor_source = (
            "withheld_incomplete_surface_inventory_missing_opaque_outdoor_surface"
            if anchor_incomplete else
            "explicit_envelope_surface_outdoor_boundary"
            if has_surface_inventory and outdoor_ua is not None else
            "legacy_configured_room_envelope_conductance"
            if not has_surface_inventory and legacy_anchor is not None else "missing"
        )
        anchored_rooms.append({
            **room,
            # A populated but incomplete surface inventory is deliberately
            # withheld; do not infer a replacement from a partial record.
            "envelope_conductance_w_per_k": anchor,
            "envelope_anchor_source": anchor_source,
        })

    absolute = identify_absolute_room_parameters(response_identification, anchored_rooms)
    results = {}
    for room in anchored_rooms:
        rid = str(room.get("id", "unknown"))
        area = _positive(room.get("area_m2"))
        thickness = _positive(room.get("ufh_screed_thickness_cm"))
        thickness_source = "configured_per_room"
        if thickness is None:
            thickness = DEFAULT_UFH_SCREED_THICKNESS_CM
            thickness_source = "default_4.5_cm"
        volume = area * thickness / 100.0 if area is not None else None
        slab_capacity = (
            volume * VOLUMETRIC_HEAT_CAPACITY_WH_M3K
            if volume is not None else None
        )
        slab_low = (
            volume * VOLUMETRIC_HEAT_CAPACITY_RANGE_WH_M3K[0]
            if volume is not None else None
        )
        slab_high = (
            volume * VOLUMETRIC_HEAT_CAPACITY_RANGE_WH_M3K[1]
            if volume is not None else None
        )
        anchored = absolute.get("rooms", {}).get(rid, {})
        results[rid] = {
            "name": room.get("name", rid),
            "effective_room_capacity": anchored,
            "ufh_screed": {
                "status": "geometric_material_prior" if slab_capacity is not None else "withheld_missing_room_area",
                "floor_area_m2": area,
                "thickness_cm": round(thickness, 2),
                "thickness_source": thickness_source,
                "screed_volume_m3": round(volume, 4) if volume is not None else None,
                "estimated_capacity_wh_per_k": round(slab_capacity, 1) if slab_capacity is not None else None,
                "estimated_capacity_range_wh_per_k": (
                    {"lower": round(slab_low, 1), "upper": round(slab_high, 1)}
                    if slab_low is not None and slab_high is not None else None
                ),
                "volumetric_heat_capacity_wh_m3k": VOLUMETRIC_HEAT_CAPACITY_WH_M3K,
                "volumetric_heat_capacity_range_wh_m3k": {
                    "lower": VOLUMETRIC_HEAT_CAPACITY_RANGE_WH_M3K[0],
                    "upper": VOLUMETRIC_HEAT_CAPACITY_RANGE_WH_M3K[1],
                },
                "interpretation": (
                    "Geometry-based screed-only material prior; assumes the configured thickness "
                    "is the heated screed layer across the room. It is not an identified in-use "
                    "dynamic slab capacity and excludes tile, structural slab, pipe/water and "
                    "temperature-gradient effects."
                ),
            },
            "envelope_anchor_source": room["envelope_anchor_source"],
            "envelope_anchor_status": (
                "configured_anchor_inventory_incomplete"
                if anchor_incomplete and _positive(room.get("envelope_conductance_w_per_k")) is not None
                else "available"
                if _positive(room.get("envelope_conductance_w_per_k")) is not None
                else "missing"
            ),
        }

    return {
        "status": absolute.get("status", "collecting_evidence_absolute_parameters_withheld"),
        "stage": 3,
        "method": "effective_room_capacity_from_outdoor_UA_anchor_and_identified_envelope_G_over_C_rate; separate geometric UFH screed prior",
        "rooms_with_provisional_effective_capacity": absolute.get("rooms_with_provisional_absolute_parameters", 0),
        "configured_rooms": len(rooms),
        "rooms": results,
        "solver_parameter_promotion": "withheld_pending_independent_physical_validation",
        "limitations": [
            "Effective room capacity is conditional on the outdoor envelope UA anchor and normalized response fit.",
            "The bootstrap interval propagates response-rate resampling only; it excludes anchor uncertainty and model bias.",
            "UFH screed capacity is a geometry/material prior, not a measured or dynamically identified slab capacity.",
            "The screed estimate excludes other floor layers and assumes uniform thickness across each room.",
            "Ground, attic and adjacent-heated conductances are not folded into the outdoor-temperature regression coefficient.",
        ],
    }
