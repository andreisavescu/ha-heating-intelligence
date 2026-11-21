from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "thermal_energy.py"
SPEC = spec_from_file_location("thermal_energy", MODULE_PATH)
THERMAL_ENERGY = module_from_spec(SPEC)
SPEC.loader.exec_module(THERMAL_ENERGY)
update_room_thermal_energy = THERMAL_ENERGY.update_room_thermal_energy


def test_room_energy_accumulates_and_splits_shared_circuit_without_double_counting():
    history = {}
    rooms = {"living": {}, "kitchen": {}}
    circuits = {
        "c1": {
            "flow_active": True,
            "estimated_thermal_power_w": 627.9,
            "demand_rooms": ["living", "kitchen"],
        }
    }
    result = update_room_thermal_energy(
        history, circuits, rooms, 90,
        datetime(2026, 11, 21, 12, tzinfo=timezone.utc),
    )
    assert result["living"]["thermal_energy_today_kwh"] == 0.007849
    assert result["kitchen"]["thermal_energy_today_kwh"] == 0.007849
    assert round(sum(v["thermal_energy_total_kwh"] for v in result.values()), 3) == 0.016


def test_room_energy_persists_total_and_rolls_period_counters():
    history = {}
    rooms = {"living": {}}
    circuits = {"c1": {"flow_active": True, "estimated_thermal_power_w": 1000, "demand_rooms": ["living"]}}
    update_room_thermal_energy(history, circuits, rooms, 90, datetime(2026, 11, 21, 23, 59, tzinfo=timezone.utc))
    next_day = update_room_thermal_energy(
        history, circuits, rooms, 0, datetime(2026, 11, 22, 0, 1, tzinfo=timezone.utc)
    )
    assert next_day["living"]["thermal_energy_today_kwh"] == 0
    assert next_day["living"]["thermal_energy_total_kwh"] == 0.025
    next_week = update_room_thermal_energy(
        history, {}, rooms, 0, datetime(2026, 11, 23, 0, 1, tzinfo=timezone.utc)
    )
    assert next_week["living"]["thermal_energy_week_kwh"] == 0
    assert next_week["living"]["thermal_energy_total_kwh"] == 0.025


def test_inactive_or_unassigned_circuit_does_not_accumulate():
    history = {}
    rooms = {"living": {}}
    circuits = {"c1": {"flow_active": False, "estimated_thermal_power_w": 500, "demand_rooms": ["living"]}}
    result = update_room_thermal_energy(
        history, circuits, rooms, 60, datetime(2026, 11, 21, tzinfo=timezone.utc)
    )
    assert result["living"]["thermal_energy_total_kwh"] == 0

def test_sensorless_served_room_receives_energy_independently_of_demand_trigger():
    history = {}
    rooms = {"bathroom": {}, "bedroom": {}}
    circuits = {
        "bathroom_loop": {
            "flow_active": True,
            "estimated_thermal_power_w": 627.9,
            "demand_rooms": ["bedroom"],
            "served_rooms": ["bathroom"],
        }
    }
    result = update_room_thermal_energy(
        history, circuits, rooms, 90,
        datetime(2026, 11, 21, 12, tzinfo=timezone.utc),
    )
    assert result["bathroom"]["thermal_energy_total_kwh"] == 0.015697
    assert result["bedroom"]["thermal_energy_total_kwh"] == 0


def test_room_power_is_instantaneous_and_splits_shared_circuit():
    history = {}
    rooms = {"living": {}, "kitchen": {}}
    circuits = {
        "c1": {"flow_active": True, "estimated_thermal_power_w": 800, "demand_rooms": ["living", "kitchen"]},
        "c2": {"flow_active": True, "estimated_thermal_power_w": 300, "served_rooms": ["living"]},
    }
    result = update_room_thermal_energy(history, circuits, rooms, 90, datetime(2026, 11, 21, 12, tzinfo=timezone.utc))
    assert result["living"]["estimated_thermal_power_w"] == 700
    assert result["kitchen"]["estimated_thermal_power_w"] == 400


def test_room_power_returns_zero_when_circuit_stops():
    history = {}
    rooms = {"living": {}}
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    active = {"c1": {"flow_active": True, "estimated_thermal_power_w": 500, "served_rooms": ["living"]}}
    stopped = {"c1": {"flow_active": False, "estimated_thermal_power_w": 500, "served_rooms": ["living"]}}
    assert update_room_thermal_energy(history, active, rooms, 90, now)["living"]["estimated_thermal_power_w"] == 500
    assert update_room_thermal_energy(history, stopped, rooms, 90, now)["living"]["estimated_thermal_power_w"] == 0
