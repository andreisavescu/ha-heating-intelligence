from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "heating_intelligence"


def _load_module(name):
    spec = spec_from_file_location(name, ROOT / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


update_pump_stage = _load_module("pump_stages").update_pump_stage
thermal_performance_statistics = _load_module("thermal_performance").thermal_performance_statistics


def test_valve_opening_is_not_reported_as_pump_running():
    history = {}
    now = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    assert update_pump_stage(history, 2.0, now)["stage"] == "idle"
    valve = update_pump_stage(history, 10.0, now + timedelta(seconds=30))
    assert valve["stage"] == "valve_opening"
    assert valve["pump_started_at"] is None


def test_pump_requires_a_second_sustained_power_level():
    history = {}
    now = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    update_pump_stage(history, 2.0, now)
    update_pump_stage(history, 10.0, now + timedelta(seconds=30))
    first_high = now + timedelta(seconds=60)
    assert update_pump_stage(history, 45.0, first_high)["stage"] == "valve_opening"
    running = update_pump_stage(history, 42.0, first_high + timedelta(seconds=30))
    assert running["stage"] == "pump_running"
    assert running["pump_started_at"] == first_high.isoformat()


def test_pump_stops_after_power_returns_to_idle():
    history = {}
    now = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    update_pump_stage(history, 2.0, now)
    update_pump_stage(history, 10.0, now + timedelta(seconds=30))
    update_pump_stage(history, 45.0, now + timedelta(seconds=60))
    update_pump_stage(history, 42.0, now + timedelta(seconds=90))
    assert update_pump_stage(history, 2.0, now + timedelta(seconds=120))["stage"] == "pump_running"
    stopped = update_pump_stage(history, 2.0, now + timedelta(seconds=150))
    assert stopped["stage"] == "idle"
    assert stopped["pump_stopped_at"] == (now + timedelta(seconds=120)).isoformat()


def test_daily_heat_loss_waits_until_two_am_and_is_frozen():
    history = {
        "efficiency_daily": {
            "2026-11-13": {
                "gas_kwh": 10.0,
                "ac_electricity_kwh": 0.0,
                "delta_t_degree_hours": 100.0,
                "solar_sum": 1000.0,
                "solar_count": 10,
            }
        }
    }
    before = datetime(2026, 11, 14, 1, 59, tzinfo=timezone.utc)
    pending = thermal_performance_statistics(history, before)
    assert pending["thermal_model_daily_date"] is None
    after = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
    final = thermal_performance_statistics(history, after)
    assert final["thermal_model_daily_date"] == "2026-11-13"
    assert final["heat_loss_coefficient_w_k"] == 90.0
    history["efficiency_daily"]["2026-11-13"]["gas_kwh"] = 100.0
    again = thermal_performance_statistics(history, after + timedelta(minutes=30))
    assert again["heat_loss_coefficient_w_k"] == 90.0
