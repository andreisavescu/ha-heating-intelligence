"""Stage 5: floor-level UFH slab priors and hydraulic return estimates.

The slab is represented as one latent thermal node per floor. Screed capacity
is a geometry/material prior; slab-to-room and slab-to-boundary conductances
remain withheld until independently identifiable. Return temperatures are
diagnostic estimates based on calibrated modulation allocation and live
flowmeter settings, never measurements.
"""
from __future__ import annotations

from typing import Any

WATER_HEAT_CAPACITY_J_KG_K = 4180.0
WATER_DENSITY_KG_L = 0.997
DEFAULT_SCREED_THICKNESS_CM = 4.5
DEFAULT_VOLUMETRIC_HEAT_CAPACITY_WH_M3K = 550.0
SCREED_CAPACITY_RANGE_WH_M3K = (450.0, 650.0)
DEFAULT_PIPE_INNER_DIAMETER_MM = 12.0


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < float("inf") else None


def build_stage5_ufh_slab_model(
    rooms: list[dict[str, Any]],
    circuits: list[dict[str, Any]],
    hydraulics: dict[str, Any],
    boiler_model: dict[str, Any],
    supply_temperature_c: float | None,
) -> dict[str, Any]:
    """Build two floor slab nodes and estimate each manifold return temperature.

    Boiler modulation power is allocated to rooms by the existing, calibrated
    modulation ledger. Floor totals are inferred allocations, not water-side
    measurements. Circuit flow settings are user-entered analog flowmeter
    readings restored by the existing number entities.
    """
    room_by_id = {str(room.get("id")): room for room in rooms if room.get("id")}
    circuit_live = (hydraulics or {}).get("circuits") or {}
    diagnostics = (boiler_model or {}).get("last_modulation_room_diagnostics") or {}
    calibrated_output_kw = _positive((boiler_model or {}).get("last_modulation_interval_calibrated_output_kw"))
    floor_results: dict[str, dict[str, Any]] = {}

    for floor_id in ("ground_floor", "upstairs"):
        floor_rooms = [
            room for room in rooms
            if room.get("floor", "ground_floor") == floor_id
        ]
        area_total = 0.0
        capacity_wh_k = 0.0
        capacity_low = 0.0
        capacity_high = 0.0
        room_capacity = {}
        for room in floor_rooms:
            area = _positive(room.get("area_m2"))
            thickness = _positive(room.get("ufh_screed_thickness_cm")) or DEFAULT_SCREED_THICKNESS_CM
            if area is None:
                room_capacity[str(room.get("id"))] = {
                    "status": "withheld_missing_area",
                    "thickness_cm": thickness,
                    "capacity_wh_per_k": None,
                }
                continue
            volume = area * thickness / 100.0
            central = volume * DEFAULT_VOLUMETRIC_HEAT_CAPACITY_WH_M3K
            low = volume * SCREED_CAPACITY_RANGE_WH_M3K[0]
            high = volume * SCREED_CAPACITY_RANGE_WH_M3K[1]
            area_total += area
            capacity_wh_k += central
            capacity_low += low
            capacity_high += high
            room_capacity[str(room.get("id"))] = {
                "status": "geometry_material_prior",
                "area_m2": round(area, 3),
                "thickness_cm": round(thickness, 2),
                "thickness_source": (
                    "configured_per_room" if _positive(room.get("ufh_screed_thickness_cm")) else "default_4.5_cm"
                ),
                "screed_volume_m3": round(volume, 4),
                "capacity_wh_per_k": round(central, 1),
                "capacity_range_wh_per_k": {"lower": round(low, 1), "upper": round(high, 1)},
            }

        floor_flow = 0.0
        circuit_records = []
        floor_circuits = [
            circuit for circuit in circuits
            if circuit.get("floor", "ground_floor") == floor_id
        ]
        for circuit in floor_circuits:
            cid = str(circuit.get("id", ""))
            live = circuit_live.get(cid) or {}
            flow = _positive(live.get("active_configured_flow_l_min"))
            if flow is None:
                flow = 0.0
            floor_flow += flow
            length = _positive(circuit.get("pipe_length_m"))
            served = list(circuit.get("served_rooms") or circuit.get("demand_rooms") or [])
            circuit_records.append({
                "id": cid,
                "name": circuit.get("name", cid),
                "pipe_length_m": length,
                "flow_l_min": round(flow, 3),
                "circulation_confirmed": bool(live.get("circulation_confirmed")),
                "served_rooms": served,
                "water_volume_l_assuming_12mm_internal_diameter": (
                    round(3.141592653589793 * (DEFAULT_PIPE_INNER_DIAMETER_MM / 2000.0) ** 2 * length * 1000.0, 3)
                    if length is not None else None
                ),
            })

        floor_power_kw = 0.0
        allocated_rooms = {}
        if calibrated_output_kw is not None:
            for room_id, record in diagnostics.items():
                room = room_by_id.get(str(room_id))
                if not room or room.get("floor", "ground_floor") != floor_id:
                    continue
                share = _positive(record.get("allocation_share")) or 0.0
                power_kw = calibrated_output_kw * share
                if power_kw > 0:
                    allocated_rooms[str(room_id)] = round(power_kw * 1000.0, 1)
                    floor_power_kw += power_kw

        return_temperature = None
        delta_t = None
        status = "awaiting_valid_flow_and_power"
        if floor_flow > 0 and floor_power_kw > 0 and supply_temperature_c is not None:
            mass_flow_kg_s = floor_flow / 60.0 * WATER_DENSITY_KG_L
            delta_t = floor_power_kw * 1000.0 / (mass_flow_kg_s * WATER_HEAT_CAPACITY_J_KG_K)
            return_temperature = supply_temperature_c - delta_t
            status = "estimated_from_modulation_allocation_and_flowmeter_readings"

        floor_results[floor_id] = {
            "status": status,
            "floor_area_m2": round(area_total, 2),
            "screed_capacity_wh_per_k": round(capacity_wh_k, 1) if capacity_wh_k else None,
            "screed_capacity_range_wh_per_k": (
                {"lower": round(capacity_low, 1), "upper": round(capacity_high, 1)}
                if capacity_wh_k else None
            ),
            "room_screed_capacity": room_capacity,
            "slab_node": "one_latent_ufh_slab_node_per_floor",
            "slab_temperature_c": None,
            "slab_to_room_conductance_w_per_k": None,
            "slab_to_lower_boundary_conductance_w_per_k": None,
            "transfer_coefficients_status": "not_identifiable_without_slab_temperature_or_independent_heat_input",
            "active_total_flow_l_min": round(floor_flow, 3),
            "allocated_useful_heat_power_w": round(floor_power_kw * 1000.0, 1) if floor_power_kw else None,
            "estimated_supply_return_delta_t_c": round(delta_t, 3) if delta_t is not None else None,
            "estimated_return_temperature_c": round(return_temperature, 2) if return_temperature is not None else None,
            "allocated_room_power_w": allocated_rooms,
            "circuits": circuit_records,
        }

    return {
        "status": "available" if any(
            item["estimated_return_temperature_c"] is not None for item in floor_results.values()
        ) else "collecting_hydraulic_and_modulation_evidence",
        "stage": 5,
        "floors": floor_results,
        "boiler_modulation_calibrated_output_kw": calibrated_output_kw,
        "supply_temperature_c": supply_temperature_c,
        "water_density_kg_l_assumed": WATER_DENSITY_KG_L,
        "water_specific_heat_j_kg_k": WATER_HEAT_CAPACITY_J_KG_K,
        "pipe_inner_diameter_mm_assumed_for_volume_and_delay": DEFAULT_PIPE_INNER_DIAMETER_MM,
        "method": (
            "Manifold return estimate = supply temperature - floor-allocated calibrated modulation "
            "power / (measured configured volume flow × water specific heat). Floor allocation "
            "reuses the existing modulation room allocation shares; it is not independent water-side calorimetry."
        ),
        "limitations": [
            "Existing flow number entities hold user-entered readings from the analog manifold flowmeters; they are not automatically sampled analog sensors.",
            "Only circuits currently marked circulation_confirmed contribute active flow.",
            "Boiler modulation output is calibrated but remains an inferred useful-output estimate; transient boiler and pipe losses may bias return temperature.",
            "No individual circuit return temperatures are claimed because circuit-level heat absorption is not independently known.",
            "The 12 mm pipe internal diameter is an explicit volume/delay assumption, not a measured pipe dimension.",
            "Slab capacity is a screed-only geometry/material prior; it excludes structural slab, finish, water and temperature gradients.",
            "Slab-to-room and slab-to-lower-boundary conductances remain unidentified until the latent slab state can be constrained by independent measurements or sufficiently informative excitation and input data.",
        ],
    }
