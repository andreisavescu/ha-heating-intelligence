"""Stage 5: room-level UFH slab nodes and manifold hydraulic diagnostics.

Each room owns a separate latent UFH slab node. Floor-level values are
aggregations for manifold reporting only; they are not shared slab states.
Screed and finish capacities remain separate material priors. Return
temperatures are estimates only when active flow and calibrated output exist.
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


def _finish_properties(zones: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float, float, float]:
    records: list[dict[str, Any]] = []
    capacity = 0.0
    conductance_area = 0.0
    area_total = 0.0
    for zone in zones:
        area = _positive(zone.get("area_m2"))
        thickness = _positive(zone.get("thickness_mm"))
        conductivity = _positive(zone.get("conductivity_w_mk"))
        density = _positive(zone.get("density_kg_m3"))
        specific_heat = _positive(zone.get("specific_heat_j_kgk"))
        valid = all(v is not None for v in (area, thickness, conductivity, density, specific_heat))
        resistance = thickness / 1000.0 / conductivity if valid else None
        zone_capacity = (
            area * thickness / 1000.0 * density * specific_heat / 3600.0
            if valid else None
        )
        if valid:
            area_total += area
            capacity += zone_capacity
            conductance_area += area / resistance
        records.append({
            "id": zone.get("id"),
            "name": zone.get("name", zone.get("id")),
            "material": zone.get("material", "custom"),
            "area_m2": round(area, 3) if area is not None else None,
            "thickness_mm": round(thickness, 2) if thickness is not None else None,
            "conductivity_w_mk": conductivity,
            "density_kg_m3": density,
            "specific_heat_j_kgk": specific_heat,
            "thermal_resistance_m2k_w": round(resistance, 5) if resistance is not None else None,
            "thermal_capacity_wh_per_k": round(zone_capacity, 3) if zone_capacity is not None else None,
            "status": "configured" if valid else "withheld_invalid_or_incomplete_zone",
        })
    equivalent_resistance = area_total / conductance_area if conductance_area > 0 else None
    return records, capacity, area_total, equivalent_resistance


def build_stage5_ufh_slab_model(
    rooms: list[dict[str, Any]],
    circuits: list[dict[str, Any]],
    hydraulics: dict[str, Any],
    boiler_model: dict[str, Any],
    supply_temperature_c: float | None,
) -> dict[str, Any]:
    """Build one latent slab node per room plus floor-level hydraulic totals."""
    room_by_id = {str(room.get("id")): room for room in rooms if room.get("id")}
    circuit_live = (hydraulics or {}).get("circuits") or {}
    diagnostics = (boiler_model or {}).get("last_modulation_room_diagnostics") or {}
    calibrated_output_kw = _positive(
        (boiler_model or {}).get("last_modulation_interval_calibrated_output_kw")
    )

    circuit_records_by_floor: dict[str, list[dict[str, Any]]] = {
        "ground_floor": [], "upstairs": [],
    }
    circuit_records_by_room: dict[str, list[dict[str, Any]]] = {
        room_id: [] for room_id in room_by_id
    }
    for circuit in circuits:
        cid = str(circuit.get("id", ""))
        floor_id = circuit.get("floor", "ground_floor")
        if floor_id not in circuit_records_by_floor:
            continue
        live = circuit_live.get(cid) or {}
        configured_flow = _positive(live.get("configured_flow_l_min"))
        active_flow = _positive(live.get("active_configured_flow_l_min")) or 0.0
        length = _positive(circuit.get("pipe_length_m"))
        served = list(circuit.get("served_rooms") or circuit.get("demand_rooms") or [])
        record = {
            "id": cid,
            "name": circuit.get("name", cid),
            "pipe_length_m": length,
            "flow_l_min": round(active_flow, 3),
            "configured_flow_l_min": round(configured_flow, 3) if configured_flow is not None else None,
            "active_flow_l_min": round(active_flow, 3),
            "flow_is_manual_input": bool(live.get("flow_is_manual_input", configured_flow is not None)),
            "circulation_confirmed": bool(live.get("circulation_confirmed")),
            "served_rooms": served,
            "water_volume_l_assuming_12mm_internal_diameter": (
                round(3.141592653589793 * (DEFAULT_PIPE_INNER_DIAMETER_MM / 2000.0) ** 2 * length * 1000.0, 3)
                if length is not None else None
            ),
        }
        circuit_records_by_floor[floor_id].append(record)
        for room_id in served:
            if room_id in circuit_records_by_room:
                circuit_records_by_room[room_id].append(record)

    room_nodes: dict[str, dict[str, Any]] = {}
    floor_results: dict[str, dict[str, Any]] = {}
    for room_id, room in room_by_id.items():
        area = _positive(room.get("area_m2"))
        thickness = _positive(room.get("ufh_screed_thickness_cm")) or DEFAULT_SCREED_THICKNESS_CM
        volume = area * thickness / 100.0 if area is not None else None
        screed_capacity = volume * DEFAULT_VOLUMETRIC_HEAT_CAPACITY_WH_M3K if volume is not None else None
        screed_low = volume * SCREED_CAPACITY_RANGE_WH_M3K[0] if volume is not None else None
        screed_high = volume * SCREED_CAPACITY_RANGE_WH_M3K[1] if volume is not None else None
        finish_records, finish_capacity, finish_area, finish_resistance = _finish_properties(
            room.get("floor_finish_zones") or []
        )
        assigned_circuits = circuit_records_by_room.get(room_id, [])
        configured_flow = sum(c.get("configured_flow_l_min") or 0.0 for c in assigned_circuits)
        active_flow = sum(c.get("active_flow_l_min") or 0.0 for c in assigned_circuits)
        output_share = _positive((diagnostics.get(room_id) or {}).get("allocation_share")) or 0.0
        allocated_power_w = (
            calibrated_output_kw * 1000.0 * output_share
            if calibrated_output_kw is not None and output_share > 0 else None
        )
        room_nodes[room_id] = {
            "name": room.get("name", room_id),
            "floor": room.get("floor", "ground_floor"),
            "slab_node": "one_latent_ufh_slab_node_per_room",
            "node_status": "room_level_node_configured",
            "floor_area_m2": area,
            "screed": {
                "status": "geometry_material_prior" if screed_capacity is not None else "withheld_missing_area",
                "thickness_cm": round(thickness, 2),
                "thickness_source": "configured_per_room" if _positive(room.get("ufh_screed_thickness_cm")) else "default_4.5_cm",
                "volume_m3": round(volume, 4) if volume is not None else None,
                "capacity_wh_per_k": round(screed_capacity, 2) if screed_capacity is not None else None,
                "capacity_range_wh_per_k": (
                    {"lower": round(screed_low, 2), "upper": round(screed_high, 2)}
                    if screed_low is not None and screed_high is not None else None
                ),
            },
            "floor_finish": {
                "status": "configured_material_estimate_separate_from_screed" if finish_records else "not_configured",
                "zones": finish_records,
                "configured_area_m2": round(finish_area, 3),
                "equivalent_parallel_resistance_m2k_w": round(finish_resistance, 5) if finish_resistance is not None else None,
                "capacity_wh_per_k": round(finish_capacity, 3),
            },
            "combined_screed_and_finish_capacity_wh_per_k": (
                round(screed_capacity + finish_capacity, 2) if screed_capacity is not None else None
            ),
            "circuits": [c["id"] for c in assigned_circuits],
            "configured_flow_l_min": round(configured_flow, 3),
            "active_flow_l_min": round(active_flow, 3),
            "allocated_useful_heat_power_w": round(allocated_power_w, 2) if allocated_power_w is not None else None,
            "slab_temperature_c": None,
            "slab_to_room_conductance_w_per_k": None,
            "slab_to_lower_boundary_conductance_w_per_k": None,
            "transfer_coefficients_status": "not_identifiable_without_room_slab_temperature_or_independent_room_heat_input",
            "interpretation": (
                "Room-specific latent UFH slab node. Screed and finish capacities are material priors; "
                "neither is a measured dynamic state or calibrated heat-transfer coefficient."
            ),
        }

    for floor_id in ("ground_floor", "upstairs"):
        floor_rooms = [
            (rid, room) for rid, room in room_by_id.items()
            if room.get("floor", "ground_floor") == floor_id
        ]
        area_total = 0.0
        screed_capacity = screed_low = screed_high = finish_capacity = 0.0
        room_screed_capacity = {}
        room_finish_zones = {}
        room_power = {}
        for room_id, _room in floor_rooms:
            node = room_nodes[room_id]
            area_total += node.get("floor_area_m2") or 0.0
            screed = node["screed"]
            if screed["capacity_wh_per_k"] is not None:
                screed_capacity += screed["capacity_wh_per_k"]
                screed_low += screed["capacity_range_wh_per_k"]["lower"]
                screed_high += screed["capacity_range_wh_per_k"]["upper"]
            room_screed_capacity[room_id] = {
                "status": screed["status"],
                "area_m2": node["floor_area_m2"],
                "thickness_cm": screed["thickness_cm"],
                "thickness_source": screed["thickness_source"],
                "screed_volume_m3": screed["volume_m3"],
                "capacity_wh_per_k": screed["capacity_wh_per_k"],
                "capacity_range_wh_per_k": screed["capacity_range_wh_per_k"],
            }
            finish = node["floor_finish"]
            finish_capacity += finish["capacity_wh_per_k"]
            room_finish_zones[room_id] = finish["zones"]
            if node["allocated_useful_heat_power_w"] is not None:
                room_power[room_id] = node["allocated_useful_heat_power_w"]

        floor_circuits = circuit_records_by_floor[floor_id]
        configured_flow = sum(c.get("configured_flow_l_min") or 0.0 for c in floor_circuits)
        active_flow = sum(c.get("active_flow_l_min") or 0.0 for c in floor_circuits)
        floor_power_w = sum(room_power.values())
        delta_t = return_temperature = None
        status = "awaiting_valid_flow_and_power"
        if active_flow > 0 and floor_power_w > 0 and supply_temperature_c is not None:
            mass_flow_kg_s = active_flow / 60.0 * WATER_DENSITY_KG_L
            delta_t = floor_power_w / (mass_flow_kg_s * WATER_HEAT_CAPACITY_J_KG_K)
            return_temperature = supply_temperature_c - delta_t
            status = "estimated_from_modulation_allocation_and_active_flow"
        floor_results[floor_id] = {
            "status": status,
            "floor_area_m2": round(area_total, 2),
            "screed_capacity_wh_per_k": round(screed_capacity, 1) if screed_capacity else None,
            "screed_capacity_range_wh_per_k": (
                {"lower": round(screed_low, 1), "upper": round(screed_high, 1)}
                if screed_capacity else None
            ),
            "room_screed_capacity": room_screed_capacity,
            "finish_capacity_wh_per_k": round(finish_capacity, 2),
            "room_finish_zones": room_finish_zones,
            "combined_screed_and_finish_capacity_wh_per_k": round(screed_capacity + finish_capacity, 2),
            "finish_storage_status": "configured_material_estimate_separate_from_screed" if finish_capacity else "no_finish_zones_configured",
            "slab_node": "room_level_nodes_aggregated_for_floor_reporting",
            "slab_temperature_c": None,
            "slab_to_room_conductance_w_per_k": None,
            "slab_to_lower_boundary_conductance_w_per_k": None,
            "transfer_coefficients_status": "room_level_transfer_coefficients_not_identifiable_without_independent_slab_state_and_heat_input",
            "configured_total_flow_l_min": round(configured_flow, 3),
            "active_total_flow_l_min": round(active_flow, 3),
            "allocated_useful_heat_power_w": round(floor_power_w, 1) if floor_power_w else None,
            "estimated_supply_return_delta_t_c": round(delta_t, 3) if delta_t is not None else None,
            "estimated_return_temperature_c": round(return_temperature, 2) if return_temperature is not None else None,
            "allocated_room_power_w": room_power,
            "circuits": floor_circuits,
        }

    return {
        "status": "available" if any(
            item["estimated_return_temperature_c"] is not None for item in floor_results.values()
        ) else "collecting_hydraulic_and_modulation_evidence",
        "stage": 5,
        "slab_representation": "one_latent_ufh_slab_node_per_room",
        "rooms": room_nodes,
        "floors": floor_results,
        "boiler_modulation_calibrated_output_kw": calibrated_output_kw,
        "supply_temperature_c": supply_temperature_c,
        "water_density_kg_l_assumed": WATER_DENSITY_KG_L,
        "water_specific_heat_j_kg_k": WATER_HEAT_CAPACITY_J_KG_K,
        "pipe_inner_diameter_mm_assumed_for_volume_and_delay": DEFAULT_PIPE_INNER_DIAMETER_MM,
        "method": (
            "Room-specific latent slab nodes with separate screed and finish material priors. "
            "Floor manifold return estimate = supply temperature - floor-allocated calibrated "
            "modulation power / (active measured flow × water specific heat)."
        ),
        "limitations": [
            "Floor-level capacities are sums of room capacities for reporting; no shared floor slab state is created.",
            "Finish properties are editable material priors, not measured values; finish storage remains separate from screed storage.",
            "Configured manual flow is an allocation weight while pumps are stopped; only confirmed active flow contributes to return-temperature estimates.",
            "Boiler modulation output is an inferred useful-output estimate; transient boiler and pipe losses may bias return temperature.",
            "No individual circuit return temperatures are claimed because circuit-level heat absorption is not independently known.",
            "The 12 mm pipe internal diameter is an explicit volume/delay assumption, not a measured pipe dimension.",
            "Room slab-to-room and slab-to-lower-boundary conductances remain unidentified until independently constrained.",
        ],
    }
