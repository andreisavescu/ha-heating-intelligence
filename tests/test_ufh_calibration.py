from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "ufh_calibration.py"
SPEC = spec_from_file_location("ufh_calibration", MODULE_PATH)
CAL = module_from_spec(SPEC)
SPEC.loader.exec_module(CAL)


def test_audit_preserves_distinct_demand_and_served_room_mappings():
    circuits = [{
        "id": "bathroom",
        "name": "Bathroom",
        "floor": "upstairs",
        "pipe_length_m": 79,
        "pump_id": "upstairs",
        "demand_scope": "selected_rooms",
        "demand_rooms": ["bedroom_a", "bedroom_b", "bathroom"],
        "served_rooms": ["bathroom"],
    }]
    rooms = {
        key: {"floor": "upstairs"}
        for key in ("bedroom_a", "bedroom_b", "bathroom")
    }
    snapshot = {"circuits": {"bathroom": {
        "configured_flow_l_min": 1.2,
        "active_configured_flow_l_min": 0.0,
        "flow_is_manual_input": True,
    }}}
    result = CAL.build_ufh_topology_audit(
        circuits, rooms, {"upstairs": {}, "ground_floor": {}}, snapshot
    )
    item = result["circuits"]["bathroom"]
    assert item["status"] == "complete"
    assert item["demand_rooms"] == ["bedroom_a", "bedroom_b", "bathroom"]
    assert item["served_rooms"] == ["bathroom"]
    assert result["room_flow_weights"]["bathroom"]["flow_weight_l_min"] == 1.2
    assert result["room_flow_weights"]["bedroom_a"]["flow_weight_l_min"] == 0.0


def test_floor_any_circuit_is_valid_without_explicit_demand_rooms():
    circuits = [{
        "id": "bath_down",
        "floor": "ground_floor",
        "pipe_length_m": 89,
        "pump_id": "ground_floor",
        "demand_scope": "floor_any",
        "demand_rooms": [],
        "served_rooms": ["bath_down"],
    }]
    rooms = {"bath_down": {"floor": "ground_floor"}}
    snapshot = {"circuits": {"bath_down": {"configured_flow_l_min": 0.9}}}
    result = CAL.build_ufh_topology_audit(
        circuits, rooms, {"ground_floor": {}}, snapshot
    )
    assert result["circuits"]["bath_down"]["status"] == "complete"
    assert result["issue_count"] == 0


def test_shared_circuit_flow_is_split_equally_across_served_rooms():
    circuits = [{
        "id": "shared",
        "floor": "ground_floor",
        "pipe_length_m": 90,
        "pump_id": "ground_floor",
        "demand_scope": "selected_rooms",
        "demand_rooms": ["room_a"],
        "served_rooms": ["room_a", "room_b"],
    }]
    rooms = {"room_a": {"floor": "ground_floor"}, "room_b": {"floor": "ground_floor"}}
    snapshot = {"circuits": {"shared": {"configured_flow_l_min": 2.4}}}
    result = CAL.build_ufh_topology_audit(
        circuits, rooms, {"ground_floor": {}}, snapshot
    )
    assert result["room_flow_weights"]["room_a"]["flow_weight_l_min"] == 1.2
    assert result["room_flow_weights"]["room_b"]["flow_weight_l_min"] == 1.2


def test_audit_flags_unknown_served_room_and_missing_flow_without_rewriting_mapping():
    circuits = [{
        "id": "bad",
        "floor": "upstairs",
        "pipe_length_m": 70,
        "pump_id": "upstairs",
        "demand_scope": "selected_rooms",
        "demand_rooms": ["known"],
        "served_rooms": ["unknown"],
    }]
    result = CAL.build_ufh_topology_audit(
        circuits, {"known": {"floor": "upstairs"}},
        {"upstairs": {}}, {"circuits": {"bad": {"configured_flow_l_min": 0.0}}}
    )
    codes = {item["code"] for item in result["issues"]}
    assert "unknown_served_room" in codes
    assert "missing_configured_flow" in codes
    assert result["circuits"]["bad"]["served_rooms"] == ["unknown"]


def test_heat_delivery_comparison_is_explicitly_estimated_not_measured():
    topology = {"status": "complete", "issue_count": 0}
    result = CAL.build_heat_delivery_calibration(
        {"2026-11-20": 10.0},
        {"2026-11-20": 9.0},
        topology,
        return_temperature_available=False,
        supply_temperature_available=True,
        measured_flow_available=False,
    )
    assert result["comparison_days"] == 1
    assert result["daily_comparison"][0]["difference_kwh"] == -1.0
    assert result["water_side_measurement_status"].startswith("withheld")
    assert result["room_level_heat_delivery_measured"] is False
