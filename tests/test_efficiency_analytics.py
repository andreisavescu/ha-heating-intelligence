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
    assert len(history["meter_samples"]) >= 4


def test_meter_reset_does_not_create_negative_energy():
    history = {}
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    MODULE.update_efficiency_history(history, {"gas": {"value": 50, "unit": "m³"}}, now, 0, False)
    MODULE.update_efficiency_history(history, {"gas": {"value": 1, "unit": "m³"}}, now + timedelta(minutes=1), 60, False)
    assert history["efficiency_daily"]["2026-11-12"]["gas_kwh"] == 0


def test_correlations_are_calculated_for_gas_and_electricity():
    history = {"efficiency_daily": {}}
    for index, (outdoor, gas, electricity) in enumerate(((17, 1, 2), (16, 2, 4), (15, 3, 6)), start=10):
        history["efficiency_daily"][f"2026-11-{index}"] = {
            "outdoor_sum": outdoor, "outdoor_count": 1,
            "delta_t_sum": 20 - outdoor, "delta_t_count": 1,
            "solar_sum": 100 * index, "solar_count": 1,
            "gas_kwh": gas, "electricity_kwh": electricity,
        }
    stats = MODULE.efficiency_statistics(
        history, datetime(2026, 11, 12, tzinfo=timezone.utc)
    )
    assert stats["gas_hdd_correlation"] == 1.0
    assert stats["electricity_hdd_correlation"] == 1.0
    assert stats["gas_delta_t_correlation"] == 1.0
    assert stats["electricity_delta_t_correlation"] == 1.0


def test_three_pump_counters_are_summed_and_shares_calculated():
    history = {}
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    first = {
        "electricity_meters": {
            "ground_floor": {"value": 100.0, "unit": "kWh"},
            "upstairs": {"value": 200.0, "unit": "kWh"},
            "boiler": {"value": 50.0, "unit": "kWh"},
        }
    }
    MODULE.update_efficiency_history(history, first, now, 0, False)
    second = {
        "electricity_meters": {
            "ground_floor": {"value": 101.0, "unit": "kWh"},
            "upstairs": {"value": 202.0, "unit": "kWh"},
            "boiler": {"value": 50.5, "unit": "kWh"},
        }
    }
    MODULE.update_efficiency_history(history, second, now + timedelta(hours=1), 3600, False)
    stats = MODULE.efficiency_statistics(history, now + timedelta(hours=1))
    assert stats["electricity_energy_today_kwh"] == 3.5
    assert stats["electricity_ground_floor_today_kwh"] == 1.0
    assert stats["electricity_upstairs_today_kwh"] == 2.0
    assert stats["electricity_boiler_today_kwh"] == 0.5
    assert stats["electricity_ground_floor_share_percent"] == 28.6
    assert stats["electricity_upstairs_share_percent"] == 57.1
    assert stats["electricity_boiler_share_percent"] == 14.3


def test_three_pump_meter_reset_does_not_create_negative_energy():
    history = {}
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    MODULE.update_efficiency_history(history, {
        "electricity_meters": {"boiler": {"value": 50, "unit": "kWh"}}
    }, now, 0, False)
    MODULE.update_efficiency_history(history, {
        "electricity_meters": {"boiler": {"value": 1, "unit": "kWh"}}
    }, now + timedelta(minutes=1), 60, False)
    assert history["efficiency_daily"]["2026-11-12"]["electricity_boiler_kwh"] == 0


def test_existing_daily_record_is_backfilled_with_new_fields():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    history = {
        "efficiency_daily": {
            "2026-11-12": {
                "gas_kwh": 1.25,
                "electricity_kwh": 0.5,
                "delta_t_sum": 12.0,
                "delta_t_count": 1,
            }
        }
    }
    MODULE.update_efficiency_history(
        history,
        {"delta_t": {"value": 10.0, "unit": "°C"}},
        now,
        60,
        False,
    )
    day = history["efficiency_daily"]["2026-11-12"]
    assert day["gas_kwh"] == 1.25
    assert day["electricity_kwh"] == 0.5
    assert day["delta_t_degree_hours"] == 0.0
    assert day["ac_electricity_kwh"] == 0.0
    assert day["outdoor_count"] == 0
