from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "ufh_hydraulics.py"
SPEC = spec_from_file_location("ufh_hydraulics", MODULE_PATH)
UFH = module_from_spec(SPEC)
SPEC.loader.exec_module(UFH)


def snapshot(demand_state, power, state, now):
    circuits = [{"id": "living_1", "pump_id": "ground_floor", "demand_rooms": ["living"]}]
    rooms = {"living": {"demand": demand_state == "on", "demand_state": demand_state}}
    pumps = {"ground_floor": {"power": power}}
    return UFH.build_ufh_hydraulic_snapshot(circuits, rooms, pumps, {"living_1": 1.0}, state, now)["circuits"]["living_1"]


def test_pump_timer_runs_independently_of_delayed_demand_relay():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    state = {}
    # Pump starts first; thermostat demand relay comes online only after 180 s.
    assert snapshot("unavailable", 8.0, state, now)["flow_active"] is False
    assert snapshot("unavailable", 8.0, state, now + timedelta(seconds=179))["flow_active"] is False
    result = snapshot("on", 8.0, state, now + timedelta(seconds=180))
    assert result["flow_active"] is True
    assert result["active_configured_flow_l_min"] == 1.0


def test_flow_stops_immediately_when_demand_becomes_unavailable():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    state = {}
    snapshot("on", 8.0, state, now)
    snapshot("on", 8.0, state, now + timedelta(seconds=180))
    result = snapshot("unavailable", 8.0, state, now + timedelta(seconds=181))
    assert result["flow_active"] is False
    assert result["active_configured_flow_l_min"] == 0.0


def test_pump_timer_resets_when_power_drops_to_seven_or_below():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    state = {}
    snapshot("on", 8.0, state, now)
    snapshot("on", 7.0, state, now + timedelta(seconds=120))
    result = snapshot("on", 8.0, state, now + timedelta(seconds=180))
    assert result["flow_active"] is False
    assert result["pump_above_threshold_seconds"] == 0


def test_thermal_power_uses_assumed_nine_degree_drop():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    state = {}
    snapshot("on", 8.0, state, now)
    result = snapshot("on", 8.0, state, now + timedelta(seconds=180))
    assert result["estimated_thermal_power_w"] == 627.9


def test_or_and_demand_logic_preserves_unknowns():
    assert UFH.evaluate_activation([True, False], "or") is True
    assert UFH.evaluate_activation([True, True], "and") is True
    assert UFH.evaluate_activation([False, None], "or") is None
    assert UFH.evaluate_activation([True, None], "and") is None


def test_active_flow_stops_on_demand_end_or_pump_stop():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    state = {}
    snapshot("on", 8.0, state, now)
    snapshot("on", 8.0, state, now + timedelta(seconds=180))
    still_active = snapshot("on", 8.0, state, now + timedelta(seconds=190))
    assert still_active["flow_active"] is True
    stopped = snapshot("off", 8.0, state, now + timedelta(seconds=200))
    assert stopped["flow_active"] is False
    snapshot("on", 8.0, state, now + timedelta(seconds=210))
    pump_stopped = snapshot("on", 0.0, state, now + timedelta(seconds=211))
    assert pump_stopped["flow_active"] is False



def test_floor_any_demand_activates_sensorless_bathroom_circuit():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    state = {}
    circuits = [{
        "id": "bathroom_upstairs",
        "name": "Bathroom",
        "floor": "upstairs",
        "demand_scope": "floor_any",
        "pump_id": "upstairs",
        "demand_rooms": [],
    }]
    rooms = {
        "bedroom": {"floor": "upstairs", "demand": True, "demand_state": "on"},
        "office": {"floor": "upstairs", "demand": False, "demand_state": "off"},
        "living": {"floor": "ground_floor", "demand": True, "demand_state": "on"},
    }
    pumps = {"upstairs": {"power": 8.0}}
    first = UFH.build_ufh_hydraulic_snapshot(
        circuits, rooms, pumps, {"bathroom_upstairs": 0.8}, state, now
    )["circuits"]["bathroom_upstairs"]
    assert first["demand_active"] is True
    assert first["flow_active"] is False
    active = UFH.build_ufh_hydraulic_snapshot(
        circuits, rooms, pumps, {"bathroom_upstairs": 0.8}, state,
        now + timedelta(seconds=180),
    )["circuits"]["bathroom_upstairs"]
    assert active["flow_active"] is True
    assert active["demand_rooms"] == ["bedroom", "office"]


def test_floor_any_demand_ignores_other_floor_and_handles_idle_floor():
    circuits = [{
        "id": "bathroom_downstairs",
        "floor": "ground_floor",
        "demand_scope": "floor_any",
        "pump_id": "ground_floor",
    }]
    rooms = {
        "upstairs_bedroom": {"floor": "upstairs", "demand": True, "demand_state": "on"},
        "downstairs_hall": {"floor": "ground_floor", "demand": False, "demand_state": "off"},
    }
    result = UFH.build_ufh_hydraulic_snapshot(
        circuits, rooms, {"ground_floor": {"power": 8.0}},
        {"bathroom_downstairs": 1.0}, {}, datetime(2026, 11, 21, tzinfo=timezone.utc)
    )["circuits"]["bathroom_downstairs"]
    assert result["demand_active"] is False
    assert result["flow_active"] is False



def test_selected_demand_sources_use_or_across_up_to_four_rooms():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    circuits = [{
        "id": "multi_room",
        "floor": "ground_floor",
        "pump_id": "ground_floor",
        "demand_scope": "selected_rooms",
        "demand_rooms": ["office", "living", "bathroom", "kitchen", "ignored_fifth"],
        "demand_logic": "and",  # Legacy setting is deliberately ignored.
    }]
    rooms = {
        "office": {"name": "Office", "floor": "ground_floor", "demand": False, "demand_state": "off"},
        "living": {"name": "Living", "floor": "ground_floor", "demand": True, "demand_state": "on"},
        "bathroom": {"name": "Bathroom", "floor": "ground_floor", "demand": False, "demand_state": "off"},
        "kitchen": {"name": "Kitchen", "floor": "ground_floor", "demand": False, "demand_state": "off"},
        "ignored_fifth": {"name": "Fifth", "floor": "ground_floor", "demand": True, "demand_state": "on"},
    }
    result = UFH.build_ufh_hydraulic_snapshot(
        circuits, rooms, {"ground_floor": {"power": 8.0}},
        {"multi_room": 1.2}, {}, now
    )["circuits"]["multi_room"]
    assert result["demand_active"] is True
    assert result["demand_logic"] == "or"
    assert result["demand_rooms"] == ["office", "living", "bathroom", "kitchen"]
    assert result["demand_room_names"] == ["Office", "Living", "Bathroom", "Kitchen"]
