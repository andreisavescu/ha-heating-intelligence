from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "ufh_hydraulics.py"
SPEC = spec_from_file_location("ufh_hydraulics", MODULE_PATH)
UFH = module_from_spec(SPEC)
SPEC.loader.exec_module(UFH)


def test_or_activation_supports_either_bathroom():
    assert UFH.evaluate_activation([True, False], "or") is True
    assert UFH.evaluate_activation([False, False], "or") is False


def test_and_activation_requires_all_demands():
    assert UFH.evaluate_activation([True, True], "and") is True
    assert UFH.evaluate_activation([True, False], "and") is False


def test_unknown_demand_is_not_assumed_off():
    assert UFH.evaluate_activation([False, None], "or") is None
    assert UFH.evaluate_activation([True, None], "and") is None


def test_circuit_flow_requires_demand_and_pump_confirmation():
    circuits = [{
        "id": "bathroom_shared",
        "name": "Shared bathroom circuits",
        "room_id": "bathroom_one",
        "pipe_length_m": 85,
        "pump_id": "upstairs",
        "demand_rooms": ["bathroom_one", "bathroom_two"],
        "demand_logic": "or",
    }]
    rooms = {
        "bathroom_one": {"demand": True, "demand_state_known": True},
        "bathroom_two": {"demand": False, "demand_state_known": True},
    }
    pumps = {"upstairs": {"power": 18.0, "running": True}}
    result = UFH.build_ufh_hydraulic_snapshot(
        circuits, rooms, pumps, {"bathroom_shared": 1.25}
    )
    item = result["circuits"]["bathroom_shared"]
    assert item["demand_active"] is True
    assert item["pump_running"] is True
    assert item["circulation_confirmed"] is True
    assert item["active_configured_flow_l_min"] == 1.25
    assert result["pump_configured_flow_totals_l_min"] == {"upstairs": 1.25}


def test_missing_pump_power_does_not_confirm_circulation():
    result = UFH.build_ufh_hydraulic_snapshot(
        [{
            "id": "living_1", "pump_id": "ground_floor",
            "demand_rooms": ["living"], "demand_logic": "or",
        }],
        {"living": {"demand": True, "demand_state_known": True}},
        {"ground_floor": {"power": None, "running": False}},
        {"living_1": 0.85},
    )
    item = result["circuits"]["living_1"]
    assert item["demand_active"] is True
    assert item["pump_running"] is None
    assert item["circulation_confirmed"] is False
    assert item["active_configured_flow_l_min"] == 0.0
