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
    anchor_audits: dict[str, dict[str, Any]] = {}
    for room in rooms:
        rid = str(room.get("id", "unknown"))
        surface_room = envelope_rooms.get(rid) or {}
        boundaries = surface_room.get("conductance_by_boundary_w_per_k") or {}
        outdoor_ua = _positive(boundaries.get("outdoor"))
        legacy_anchor = _positive(room.get("envelope_conductance_w_per_k"))
        opaque_outdoor_count = int(
            ((surface_room.get("opaque_surface_counts") or {}).get("outdoor") or 0)
        )
        # Inventory presence is room-specific. A global non-empty inventory
        # must not make rooms with no records appear to have complete geometry.
        has_surface_inventory = bool(
            surface_room.get("configured_surface_count")
            or surface_room.get("window_record_count")
            or any((surface_room.get("boundary_surface_counts") or {}).values())
            or any((surface_room.get("opaque_surface_counts") or {}).values())
        )
        anchor_incomplete = has_surface_inventory and opaque_outdoor_count == 0
        # A populated but incomplete room inventory cannot anchor absolute C.
        # Do not substitute the legacy room total for window-only records.
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
        withheld_reason = (
            "no_opaque_outdoor_surface_record" if anchor_incomplete else
            "missing_or_invalid_outdoor_surface_ua" if anchor is None else None
        )
        anchor_audits[rid] = {
            "outdoor_opaque_surface_count": opaque_outdoor_count,
            "outdoor_window_record_count": int(surface_room.get("window_record_count") or 0),
            "surface_inventory_present": has_surface_inventory,
            "outdoor_surface_ua_w_per_k": outdoor_ua,
            "anchor_used_w_per_k": anchor,
            "withheld_reason": withheld_reason,
        }
        anchored_rooms.append({
            **room,
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
        anchor_audit = anchor_audits.get(rid, {})
        finish_zones = []
        finish_conductance_area = 0.0
        finish_capacity = 0.0
        for zone in (room.get("floor_finish_zones") or []):
            zone_area = _positive(zone.get("area_m2"))
            thickness_mm = _positive(zone.get("thickness_mm"))
            conductivity = _positive(zone.get("conductivity_w_mk"))
            density = _positive(zone.get("density_kg_m3"))
            specific_heat = _positive(zone.get("specific_heat_j_kgk"))
            valid = all(value is not None for value in (zone_area, thickness_mm, conductivity, density, specific_heat))
            resistance = thickness_mm / 1000.0 / conductivity if valid else None
            capacity = zone_area * thickness_mm / 1000.0 * density * specific_heat / 3600.0 if valid else None
            if valid:
                finish_conductance_area += zone_area / resistance
                finish_capacity += capacity
            finish_zones.append({
                "id": zone.get("id"), "name": zone.get("name", zone.get("id")),
                "material": zone.get("material", "custom"), "area_m2": zone_area,
                "thickness_mm": thickness_mm, "conductivity_w_mk": conductivity,
                "density_kg_m3": density, "specific_heat_j_kgk": specific_heat,
                "thermal_resistance_m2k_w": round(resistance, 5) if resistance is not None else None,
                "thermal_capacity_wh_per_k": round(capacity, 3) if capacity is not None else None,
                "status": "configured" if valid else "withheld_invalid_or_incomplete_zone",
            })
        finish_area = sum(z["area_m2"] or 0 for z in finish_zones)
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
            "floor_finish": {
                "status": "configured" if finish_zones else "not_configured",
                "zones": finish_zones, "configured_area_m2": round(finish_area, 3),
                "equivalent_parallel_resistance_m2k_w": round(finish_area / finish_conductance_area, 5) if finish_conductance_area > 0 else None,
                "total_finish_capacity_wh_per_k": round(finish_capacity, 3) if finish_zones else None,
                "interpretation": "Per-zone finish resistance is in series between the UFH screed and room air; finish heat capacity is separate from screed capacity. These estimates do not imply an identified slab-to-room conductance.",
            },
            "envelope_anchor_source": room["envelope_anchor_source"],
            "envelope_anchor_status": (
                "available"
                if _positive(anchored.get("envelope_anchor_w_per_k")) is not None
                else "withheld_incomplete_surface_inventory"
                if anchor_audit.get("withheld_reason") == "no_opaque_outdoor_surface_record"
                else "missing"
            ),
            "envelope_anchor_audit": anchor_audit,
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
            "Floor-finish resistance and heat capacity are calculated per configured zone; they remain distinct from screed capacity and are not yet a calibrated dynamic coupling.",
            "Ground, attic and adjacent-heated conductances are not folded into the outdoor-temperature regression coefficient.",
        ],
    }
