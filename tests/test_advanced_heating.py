"""Tests for advanced heating-water, solar and forecast analytics."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "advanced_heating.py"
SPEC = importlib.util.spec_from_file_location("advanced_heating", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_water_delta_and_24h_statistics():
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    history = {}
    rooms = {}
    predictions = {"rooms": {}}
    for i in range(15):
        MODULE.update_advanced_history(history, 35.0, 30.0, rooms, predictions, now - timedelta(minutes=5*(14-i)))
    result = MODULE.update_advanced_history(history, 35.0, 30.0, rooms, predictions, now)
    assert result["water"]["water_delta_t_c"] == 5.0
    assert result["water"]["average_delta_t_24h_c"] == 5.0
    assert result["water"]["samples_24h"] >= 15


def test_water_delta_requires_both_sensors():
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    result = MODULE.update_advanced_history({}, 35.0, None, {}, {"rooms": {}}, now)
    assert result["water"]["water_delta_t_c"] is None
    assert result["water"]["status"] == "collecting_data"


def test_solar_analysis_waits_for_enough_valid_days():
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    history = {"efficiency_daily": {
        (now.date() - timedelta(days=i)).isoformat(): {
            "solar_sum": float(i*100), "solar_count": 1,
            "outdoor_sum": 10.0, "outdoor_count": 1,
            "gas_kwh": float(20-i),
        } for i in range(1, 12)
    }}
    result = MODULE.update_advanced_history(history, None, None, {}, {"rooms": {}}, now)
    assert result["solar"]["status"] == "available"
    assert result["solar"]["days_used"] == 11
    assert result["solar"]["low_solar_gas_per_hdd"] is not None


def test_forecast_validation_compares_prediction_with_later_actual():
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    history = {"forecast_validation": {"living": {
        "pending": [{
            "target_time": (now - timedelta(minutes=5)).isoformat(),
            "horizon_h": 1, "predicted_c": 20.0,
        }],
        "errors": [],
    }}}
    rooms = {"living": {"temperature_history": [{
        "time": (now - timedelta(minutes=4)).isoformat(), "temperature": 20.4,
    }]}}
    result = MODULE.update_advanced_history(history, None, None, rooms, {"rooms": {}}, now)
    assert result["forecast_validation"]["1"]["sample_count"] == 1
    assert result["forecast_validation"]["1"]["mae_c"] == 0.4
