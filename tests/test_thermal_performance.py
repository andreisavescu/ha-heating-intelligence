"""Tests for the gas-derived whole-house thermal model."""
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


def test_daily_heat_loss_uses_delivered_heat_and_degree_hours():
    now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
    history = {"efficiency_daily": {now.date().isoformat(): {
        "gas_kwh": 24.0, "delta_t_degree_hours": 24.0,
    }}}
    result = THERMAL.thermal_performance_statistics(history, now, 0.90, 361.26)
    assert result["estimated_delivered_heat_today_kwh"] == 21.6
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
