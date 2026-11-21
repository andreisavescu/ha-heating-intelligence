from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "boiler_energy_model.py"
SPEC = spec_from_file_location("boiler_energy_model", MODULE_PATH)
MODEL = module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


def test_heating_gas_allocates_only_to_active_demand_rooms_by_flow():
    history = {}
    circuits = {
        "a": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["bed", "office"]},
        "b": {"flow_active": True, "configured_flow_l_min": 2.0, "served_rooms": ["office"]},
    }
    rooms = {
        "bed": {"demand": True},
        "office": {"demand": False},
    }
    first = MODEL.update_boiler_energy_model(history, 10.0, 4.0, True, 50, circuits, rooms, 60)
    assert first["boiler_generated_heat_kwh"] == 0
    second = MODEL.update_boiler_energy_model(history, 10.1, 4.1, False, 0, circuits, rooms, 60)
    assert second["boiler_meter_interval_input_kwh"] == 1.055
    assert second["boiler_meter_interval_useful_heat_kwh"] == 0.9495
    assert second["boiler_room_allocations_kwh"]["bed"] == 0.9495
    assert second["boiler_unallocated_heat_kwh"] == 0


def test_shared_circuit_splits_only_between_eligible_served_rooms():
    history = {}
    circuits = {"shared": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["a", "b"]}}
    rooms = {"a": {"demand": True}, "b": {"demand": True}}
    MODEL.update_boiler_energy_model(history, 1.0, 0.0, True, 100, circuits, rooms, 60)
    result = MODEL.update_boiler_energy_model(history, 1.1, 0.0, False, 0, circuits, rooms, 60)
    assert abs(result["boiler_room_allocations_kwh"]["a"] - 0.47475) < 1e-8
    assert abs(result["boiler_room_allocations_kwh"]["b"] - 0.47475) < 1e-8


def test_burner_off_overrun_does_not_add_room_exposure():
    history = {}
    circuits = {"a": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["a"]}}
    rooms = {"a": {"demand": True}}
    MODEL.update_boiler_energy_model(history, 1.0, 0.0, False, 100, circuits, rooms, 90)
    result = MODEL.update_boiler_energy_model(history, 1.1, 0.0, False, 0, circuits, rooms, 90)
    assert result["boiler_unallocated_heat_kwh"] > 0
    assert result["boiler_room_energy_kwh"] == {}


def test_dhw_meter_is_tracked_but_never_allocated_to_rooms():
    history = {}
    first = MODEL.update_boiler_energy_model(history, 2.0, 3.0, False, None, {}, {}, 60)
    result = MODEL.update_boiler_energy_model(history, 2.0, 3.1, False, None, {}, {}, 60)
    assert result["dhw_input_energy_kwh"] == 1.055
    assert result["boiler_generated_heat_kwh"] == 0


def test_monthly_meter_reset_never_produces_negative_energy():
    history = {}
    MODEL.update_boiler_energy_model(history, 29.9, 7.6, False, None, {}, {}, 60)
    result = MODEL.update_boiler_energy_model(history, 0.2, 0.1, False, None, {}, {}, 60)
    assert result["boiler_meter_interval_input_kwh"] == 2.11
    assert result["boiler_meter_reset_detected"] is True
    assert result["boiler_meter_reset_count"] == 2
