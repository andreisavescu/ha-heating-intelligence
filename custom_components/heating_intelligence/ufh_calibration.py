"""UFH topology audit and estimated heat-allocation calibration diagnostics.

All energy allocation values remain estimates. Configured flow is an allocation
weight, not a measured thermal output; independent water-side output requires
fresh supply and return temperatures plus a measured flow source.
"""
from __future__ import annotations

import math
from typing import Any


FLOORS = ("ground_floor", "upstairs")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def build_ufh_topology_audit(
    circuits: list[dict[str, Any]],
    rooms: dict[str, dict[str, Any]],
    pumps: dict[str, dict[str, Any]],
    hydraulic_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit circuit identity, demand scope, served rooms, pumps and flow coverage."""
    live_circuits = (hydraulic_snapshot or {}).get("circuits", {})
    configured_ids: set[str] = set()
    circuit_results: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, str]] = []
    floor_totals = {floor: 0.0 for floor in FLOORS}
    floor_circuit_counts = {floor: 0 for floor in FLOORS}
    room_flow_weights = {room_id: 0.0 for room_id in rooms}
    seen_names: dict[str, str] = {}

    for circuit in circuits:
        circuit_id = str(circuit.get("id") or "").strip()
        if not circuit_id:
            issues.append({"code": "missing_circuit_id", "message": "A configured circuit has no ID."})
            continue
        if circuit_id in configured_ids:
            issues.append({"code": "duplicate_circuit_id", "circuit_id": circuit_id,
                           "message": f"{circuit_id} is configured more than once."})
        configured_ids.add(circuit_id)
        name = str(circuit.get("name") or circuit_id)
        floor = str(circuit.get("floor") or "unassigned")
        length = _number(circuit.get("pipe_length_m"))
        pump_id = str(circuit.get("pump_id") or "")
        demand_scope = str(circuit.get("demand_scope") or "selected_rooms")
        demand_rooms = list(circuit.get("demand_rooms") or [])
        served_rooms = list(circuit.get("served_rooms") or demand_rooms)
        live = live_circuits.get(circuit_id, {})
        flow = _number(live.get("configured_flow_l_min"))
        active_flow = _number(live.get("active_configured_flow_l_min"))
        circuit_issues: list[str] = []

        if floor not in FLOORS:
            circuit_issues.append("invalid_floor")
        else:
            floor_circuit_counts[floor] += 1
        if length is None or length <= 0:
            circuit_issues.append("missing_pipe_length")
        if not pump_id or pump_id not in pumps:
            circuit_issues.append("missing_or_invalid_pump")
        elif floor in FLOORS and pump_id != floor:
            circuit_issues.append("pump_floor_mismatch")
        if demand_scope not in ("selected_rooms", "floor_any"):
            circuit_issues.append("unknown_demand_scope")
        if demand_scope == "selected_rooms":
            if not demand_rooms:
                circuit_issues.append("empty_demand_mapping")
            for room_id in demand_rooms:
                if room_id not in rooms:
                    circuit_issues.append("unknown_demand_room")
                elif floor in FLOORS and rooms[room_id].get("floor") != floor:
                    circuit_issues.append("demand_room_floor_mismatch")
        # A broader demand list is intentional and independent of heat recipients.
        if not served_rooms:
            circuit_issues.append("empty_served_room_mapping")
        for room_id in served_rooms:
            if room_id not in rooms:
                circuit_issues.append("unknown_served_room")
                continue
            if floor in FLOORS and rooms[room_id].get("floor") != floor:
                circuit_issues.append("served_room_floor_mismatch")
        if flow is None or flow <= 0:
            circuit_issues.append("missing_configured_flow")
        elif floor in FLOORS:
            floor_totals[floor] += flow
            # Match the existing boiler allocator: shared circuits split flow
            # evenly among their explicitly served rooms.
            valid_recipients = [rid for rid in served_rooms if rid in rooms]
            if valid_recipients:
                share = flow / len(valid_recipients)
                for room_id in valid_recipients:
                    room_flow_weights[room_id] += share

        normalized_name = name.casefold()
        if normalized_name in seen_names:
            circuit_issues.append("duplicate_circuit_name")
        else:
            seen_names[normalized_name] = circuit_id
        for code in sorted(set(circuit_issues)):
            issues.append({"code": code, "circuit_id": circuit_id,
                           "message": f"{name}: {code.replace('_', ' ')}."})
        circuit_results[circuit_id] = {
            "name": name,
            "floor": floor,
            "pipe_length_m": length,
            "pump_id": pump_id or None,
            "demand_scope": demand_scope,
            "demand_rooms": demand_rooms,
            "served_rooms": served_rooms,
            "configured_flow_l_min": round(flow, 3) if flow is not None else None,
            "active_configured_flow_l_min": round(active_flow, 3) if active_flow is not None else None,
            "flow_is_manual_input": bool(live.get("flow_is_manual_input", False)),
            "issues": sorted(set(circuit_issues)),
            "status": "complete" if not circuit_issues else "needs_attention",
        }

    room_shares = {}
    total_room_weight = sum(room_flow_weights.values())
    for room_id, weight in room_flow_weights.items():
        room_shares[room_id] = {
            "flow_weight_l_min": round(weight, 4),
            "allocation_share_percent": round(100 * weight / total_room_weight, 2)
            if total_room_weight > 0 else None,
            "allocation_basis": "served_circuit_flow_split_equally_across_served_rooms",
        }

    configured_count = len(circuit_results)
    issue_count = len(issues)
    return {
        "status": "complete" if configured_count and issue_count == 0 else
                  "needs_attention" if configured_count else "no_circuits_configured",
        "configured_circuits": configured_count,
        "circuits_by_floor": floor_circuit_counts,
        "circuits": circuit_results,
        "floor_configured_flow_l_min": {key: round(value, 3) for key, value in floor_totals.items()},
        "room_flow_weights": room_shares,
        "issue_count": issue_count,
        "issues": issues,
        "demand_and_served_room_mappings_are_independent": True,
        "notes": [
            "demand_rooms identifies thermostat inputs; served_rooms identifies heat recipients.",
            "floor_any circuits intentionally respond to any configured room demand on their floor.",
            "Configured manual flow is an allocation weight, not independently measured thermal output.",
            "A null legacy room_id is not an error when served_rooms explicitly maps the recipients.",
        ],
    }


def build_heat_delivery_calibration(
    gas_reconciled_daily_kwh: dict[str, Any] | None,
    modulation_daily_kwh: dict[str, Any] | None,
    topology_audit: dict[str, Any],
    return_temperature_available: bool,
    supply_temperature_available: bool,
    measured_flow_available: bool,
) -> dict[str, Any]:
    """Compare independent estimation ledgers without labelling either a heat meter."""
    gas = gas_reconciled_daily_kwh or {}
    modulation = modulation_daily_kwh or {}
    days = []
    for day in sorted(set(gas) | set(modulation)):
        gas_value = _number(gas.get(day))
        modulation_value = _number(modulation.get(day))
        if gas_value is None or modulation_value is None:
            continue
        difference = modulation_value - gas_value
        percent = (100 * difference / gas_value) if gas_value > 0 else None
        days.append({
            "date": day,
            "gas_reconciled_estimate_kwh": round(gas_value, 4),
            "modulation_estimate_kwh": round(modulation_value, 4),
            "difference_kwh": round(difference, 4),
            "difference_percent_of_gas_estimate": round(percent, 2) if percent is not None else None,
        })
    recent = days[-30:]
    comparable = [item for item in recent if item["gas_reconciled_estimate_kwh"] > 0]
    mean_abs_percent = (
        sum(abs(item["difference_percent_of_gas_estimate"]) for item in comparable) / len(comparable)
        if comparable else None
    )
    water_side_ready = bool(return_temperature_available and supply_temperature_available and measured_flow_available)
    return {
        "status": "two_estimation_ledgers_comparable" if comparable else "awaiting_comparable_daily_energy",
        "daily_comparison": days,
        "comparison_days": len(comparable),
        "mean_absolute_daily_difference_percent": round(mean_abs_percent, 2) if mean_abs_percent is not None else None,
        "topology_status": topology_audit.get("status"),
        "topology_issue_count": topology_audit.get("issue_count", 0),
        "water_side_measurement_status": "available_inputs_configured" if water_side_ready else
            "withheld_missing_return_temperature_or_measured_flow",
        "return_temperature_available": bool(return_temperature_available),
        "supply_temperature_available": bool(supply_temperature_available),
        "measured_flow_available": bool(measured_flow_available),
        "room_level_heat_delivery_measured": False,
        "interpretation": (
            "Gas-reconciled and modulation-based room/floor energy remain estimates using different "
            "calibration paths. Agreement is a consistency check, not independent validation of delivered "
            "heat. Manual circuit flow values are allocation weights. Water-side heat transfer requires "
            "fresh measured flow and supply/return temperature difference."
        ),
    }
