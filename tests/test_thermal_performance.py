"""Tests for the whole-house thermal model, including AC heating."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
def load_module(name):
    path = ROOT / "custom_components" / "heating_intelligence" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

THERMAL = load_module("thermal_performance")
EFFICIENCY = load_module("efficiency_analytics")


def test_daily_heat_loss_uses_previous_completed_day():
    now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
    history = {"efficiency_daily": {
        "2026-11-12": {"gas_kwh": 24.0, "delta_t_degree_hours": 24.0},
        "2026-11-13": {"gas_kwh": 5.0, "delta_t_degree_hours": 5.0},
    }}
    result = THERMAL.thermal_performance_statistics(history, now, 0.90, 361.26)
    assert result["estimated_delivered_heat_today_kwh"] == 4.5
    assert result["thermal_model_daily_date"] == "2026-11-12"
    assert result["heat_loss_coefficient_w_k"] == 900.0
    assert result["heat_loss_u_value_daily"] == round(900 / 361.26, 4)


def test_rolling_heat_loss_requires_sufficient_days():
    now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
    days = {}
    for offset in range(7):
        date = (now.date() - timedelta(days=6-offset)).isoformat()
        days[date] = {"gas_kwh": 10.0, "delta_t_degree_hours": 30.0}
    result = THERMAL.thermal_performance_statistics({"efficiency_daily": days}, now)
    assert result["heat_loss_u_value_7d"] is not None
    assert result["heat_loss_u_value_30d"] is None


def test_switching_gas_meter_establishes_new_baseline():
    history = {}
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    EFFICIENCY.update_efficiency_history(history, {
        "gas": {"value": 100.0, "unit": "m³", "entity_id": "sensor.combined_gas"}
    }, now, 0, False)
    EFFICIENCY.update_efficiency_history(history, {
        "gas": {"value": 12.0, "unit": "m³", "entity_id": "sensor.heating_gas"}
    }, now + timedelta(minutes=1), 60, False)
    assert history["efficiency_daily"]["2026-11-13"]["gas_kwh"] == 0
    EFFICIENCY.update_efficiency_history(history, {
        "gas": {"value": 13.0, "unit": "m³", "entity_id": "sensor.heating_gas"}
    }, now + timedelta(minutes=2), 60, False)
    assert history["efficiency_daily"]["2026-11-13"]["gas_kwh"] == 10.55


def test_delta_t_is_integrated_as_degree_hours():
    history = {}
    now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
    EFFICIENCY.update_efficiency_history(history, {"delta_t": {"value": 10.0}}, now, 0, False)
    EFFICIENCY.update_efficiency_history(history, {"delta_t": {"value": 10.0}}, now + timedelta(seconds=30), 30, False)
    assert history["efficiency_daily"]["2026-11-13"]["delta_t_degree_hours"] == 10 * 30 / 3600


def test_ac_heat_is_added_to_boiler_delivered_heat():
    now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
    history = {"efficiency_daily": {now.date().isoformat(): {
        "gas_kwh": 10.0, "ac_electricity_kwh": 4.0,
        "delta_t_degree_hours": 24.0,
    }}}
    result = THERMAL.thermal_performance_statistics(history, now, 0.90, 361.26, 3.0)
    assert result["estimated_ac_delivered_heat_today_kwh"] == 12.0
    assert result["estimated_delivered_heat_today_kwh"] == 21.0
    assert result["heat_loss_coefficient_w_k"] is None


def test_ac_energy_meter_source_change_establishes_baseline():
    history = {}
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    EFFICIENCY.update_efficiency_history(history, {
        "ac_energy": {"value": 120.0, "unit": "kWh", "entity_id": "sensor.ac_total"}
    }, now, 0, False)
    EFFICIENCY.update_efficiency_history(history, {
        "ac_energy": {"value": 8.0, "unit": "kWh", "entity_id": "sensor.ac_heating"}
    }, now + timedelta(minutes=1), 60, False)
    assert history["efficiency_daily"]["2026-11-13"]["ac_electricity_kwh"] == 0
    EFFICIENCY.update_efficiency_history(history, {
        "ac_energy": {"value": 9.5, "unit": "kWh", "entity_id": "sensor.ac_heating"}
    }, now + timedelta(minutes=2), 60, False)
    assert history["efficiency_daily"]["2026-11-13"]["ac_electricity_kwh"] == 1.5



def test_degree_hours_are_split_at_local_midnight():
    history = {}
    before_midnight = datetime(2026, 11, 12, 23, 59, 45, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 11, 13, 0, 0, 15, tzinfo=timezone.utc)
    EFFICIENCY.update_efficiency_history(history, {"delta_t": {"value": 10.0}}, before_midnight, 0, False)
    EFFICIENCY.update_efficiency_history(history, {"delta_t": {"value": 10.0}}, after_midnight, 30, False)
    assert history["efficiency_daily"]["2026-11-12"]["delta_t_degree_hours"] == 10 * 15 / 3600
    assert history["efficiency_daily"]["2026-11-13"]["delta_t_degree_hours"] == 10 * 15 / 3600


def test_daily_heat_loss_does_not_change_during_current_day():
    now = datetime(2026, 11, 13, 18, tzinfo=timezone.utc)
    history = {"efficiency_daily": {
        "2026-11-12": {"gas_kwh": 12.0, "delta_t_degree_hours": 24.0},
        "2026-11-13": {"gas_kwh": 100.0, "delta_t_degree_hours": 1.0},
    }}
    result = THERMAL.thermal_performance_statistics(history, now)
    assert result["thermal_model_daily_date"] == "2026-11-12"
    assert result["heat_loss_u_value_daily"] == round(450 / 361.26, 4)
