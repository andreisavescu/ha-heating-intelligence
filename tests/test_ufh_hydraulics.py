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


def test_flow_waits_full_three_minutes_above_seven_watts():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    state = {}
    assert snapshot("on", 8.0, state, now)["flow_active"] is False
    assert snapshot("on", 8.0, state, now + timedelta(seconds=179))["flow_active"] is False
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


def test_flow_timer_resets_when_pump_power_drops_to_seven_or_below():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    state = {}
    snapshot("on", 8.0, state, now)
    snapshot("on", 7.0, state, now + timedelta(seconds=120))
    result = snapshot("on", 8.0, state, now + timedelta(seconds=180))
    assert result["flow_active"] is False
    assert result["flow_pending_seconds"] == 0


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
