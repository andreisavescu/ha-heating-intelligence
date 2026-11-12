"""Tests for whole-house efficiency analytics."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "efficiency_analytics.py"
SPEC = importlib.util.spec_from_file_location("efficiency_analytics", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_cumulative_gas_and_electricity_deltas_and_degree_days():
    history = {}
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    first = {
        "gas": {"value": 100.0, "unit": "m³"},
        "electricity": {"value": 200.0, "unit": "kWh"},
        "outdoor_temperature": {"value": 8.0},
        "solar_radiation": {"value": 120.0},
        "delta_t": {"value": 12.0},
        "indoor_temperature": {"value": 20.0},
    }
    MODULE.update_efficiency_history(history, first, now, 0, False)
    second = {
        **first,
        "gas": {"value": 101.0, "unit": "m³"},
        "electricity": {"value": 202.0, "unit": "kWh"},
    }
    MODULE.update_efficiency_history(history, second, now + timedelta(hours=1), 3600, False)
    stats = MODULE.efficiency_statistics(history, now + timedelta(hours=1))
    assert stats["gas_energy_today_kwh"] == 10.55
    assert stats["electricity_energy_today_kwh"] == 2.0
    assert stats["heating_degree_days"] == 10.0
    assert stats["gas_kwh_per_degree_day"] == 1.055


def test_meter_reset_does_not_create_negative_energy():
    history = {}
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    MODULE.update_efficiency_history(history, {"gas": {"value": 50, "unit": "m³"}}, now, 0, False)
    MODULE.update_efficiency_history(history, {"gas": {"value": 1, "unit": "m³"}}, now + timedelta(minutes=1), 60, False)
    assert history["efficiency_daily"]["2026-11-12"]["gas_kwh"] == 0
