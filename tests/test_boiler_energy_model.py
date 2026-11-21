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


def test_variable_meter_increment_is_reconciled_as_actual_delta():
    history = {}
    circuits = {"c": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["r"]}}
    rooms = {"r": {"demand": True}}
    MODEL.update_boiler_energy_model(history, 10.0, 4.0, True, 100, circuits, rooms, 60)
    result = MODEL.update_boiler_energy_model(history, 10.5, 4.2, False, 0, circuits, rooms, 60)
    assert result["boiler_meter_interval_input_kwh"] == 5.275
    assert result["boiler_meter_interval_useful_heat_kwh"] == 4.7475
    assert result["boiler_meter_sync_count"] == 1
    assert result["boiler_room_allocations_kwh"]["r"] == 4.7475


def test_daily_room_allocation_is_split_at_midnight():
    from datetime import datetime, timezone

    history = {}
    circuits = {"c": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["r"]}}
    rooms = {"r": {"demand": True}}
    MODEL.update_boiler_energy_model(
        history, 1.0, 0.0, True, 100, circuits, rooms, 60,
        now=datetime(2026, 11, 20, 23, 59, 0, tzinfo=timezone.utc),
    )
    MODEL.update_boiler_energy_model(
        history, 1.0, 0.0, True, 100, circuits, rooms, 60,
        now=datetime(2026, 11, 21, 0, 0, 0, tzinfo=timezone.utc),
    )
    result = MODEL.update_boiler_energy_model(
        history, 1.1, 0.0, False, 0, circuits, rooms, 60,
        now=datetime(2026, 11, 21, 0, 1, 0, tzinfo=timezone.utc),
    )
    daily = result["boiler_daily_room_energy_kwh"]
    assert set(daily) == {"2026-11-20", "2026-11-21"}
    assert daily["2026-11-20"]["r"] > 0
    assert daily["2026-11-21"]["r"] > 0
    assert abs(daily["2026-11-20"]["r"] + daily["2026-11-21"]["r"] - 0.9495) < 1e-8


def test_modulation_maps_to_25kw_with_minimum_firing_output():
    assert MODEL._modulation_output_kw(100) == 25.0
    assert MODEL._modulation_output_kw(50) == 12.5
    assert MODEL._modulation_output_kw(10) == 3.2
    assert MODEL._modulation_output_kw(0) == 0.0
    assert MODEL._modulation_output_kw(None) is None


def test_gas_meter_calibrates_modulation_output_cumulatively():
    history = {}
    circuits = {"c": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["r"]}}
    rooms = {"r": {"demand": True}}
    MODEL.update_boiler_energy_model(history, 10.0, 2.0, True, 100, circuits, rooms, 90)
    result = MODEL.update_boiler_energy_model(history, 10.1, 2.0, True, 100, circuits, rooms, 90)
    # The interval's raw modulation estimate is 25 kW × 90 s.
    assert result["boiler_last_meter_interval_modulation_energy_kwh"] == 0.625
    assert result["boiler_meter_interval_useful_heat_kwh"] == 0.9495
    assert abs(result["boiler_calibration_factor"] - (0.9495 / 0.625)) < 1e-5
    assert abs(result["boiler_modulation_calibrated_output_kw"] - 25 * (0.9495 / 0.625)) < 1e-3
    assert result["boiler_modulation_calibration_status"] == "calibrated"


def test_modulation_estimate_is_available_between_gas_meter_updates():
    history = {}
    first = MODEL.update_boiler_energy_model(history, 1.0, 0.0, True, 40, {}, {}, 10)
    second = MODEL.update_boiler_energy_model(history, 1.0, 0.0, True, 40, {}, {}, 10)
    assert first["boiler_modulation_estimated_output_kw"] == 10.0
    assert second["boiler_modulation_calibrated_output_kw"] == 10.0
    assert second["boiler_modulation_calibration_status"] == "initial_25kw_estimate"
