"""Tests for explainable v0.8 forecasts."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "prediction_engine.py"
SPEC = importlib.util.spec_from_file_location("prediction_engine", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def samples(start, slope=0.2, count=13, interval_minutes=10, window_open=False):
    return [
        {"time": (start + timedelta(minutes=i * interval_minutes)).isoformat(),
         "temperature": 20 + slope * i * interval_minutes / 60,
         "window_open": window_open}
        for i in range(count)
    ]


def test_room_forecast_projects_recent_temperature_trend():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    room = {"temperature": 20.4, "demand": True, "temperature_samples": samples(now - timedelta(hours=2))}
    result = MODULE.room_forecast(room, now)
    assert result["status"] == "available"
    assert result["temperature_1h"] == 20.6
    assert result["time_to_comfort_minutes"] is not None
    assert result["confidence"] > 0


def test_room_forecast_withholds_prediction_when_data_is_insufficient():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    room = {"temperature": 19.0, "demand": True, "temperature_samples": samples(now - timedelta(minutes=40), count=4)}
    result = MODULE.room_forecast(room, now)
    assert result["status"] == "insufficient_data"
    assert result["temperature_1h"] is None


def test_open_window_samples_are_excluded_from_trend():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    room = {"temperature": 19.0, "demand": False, "temperature_samples": samples(now - timedelta(hours=2), window_open=True)}
    assert MODULE.room_forecast(room, now)["status"] == "insufficient_data"


def test_energy_forecast_requires_two_hours_and_projects_run_rate():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    result = MODULE.energy_forecast({"gas_energy_today_kwh": 6, "electricity_energy_today_kwh": 1.2}, now)
    assert result["status"] == "available"
    assert result["gas_kwh"] == 12
    assert result["electricity_kwh"] == 2.4
    early = MODULE.energy_forecast({"gas_energy_today_kwh": 1}, now.replace(hour=1))
    assert early["status"] == "insufficient_data"


def test_room_forecast_estimates_time_until_below_comfort_when_cooling():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    cooling = samples(now - timedelta(hours=2), slope=-0.2)
    room = {"temperature": 22.0, "demand": False, "temperature_samples": cooling}
    result = MODULE.room_forecast(room, now)
    assert result["status"] == "available"
    assert result["time_below_comfort_minutes"] == 300


def test_flat_temperature_trend_does_not_claim_a_comfort_crossing():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    flat = samples(now - timedelta(hours=2), slope=0)
    room = {"temperature": 20.0, "demand": True, "temperature_samples": flat}
    result = MODULE.room_forecast(room, now)
    assert result["status"] == "available"
    assert result["time_to_comfort_minutes"] is None


def test_stale_samples_are_rejected():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    old = samples(now - timedelta(hours=4))
    room = {"temperature": 20.0, "demand": True, "temperature_samples": old}
    result = MODULE.room_forecast(room, now)
    assert result["status"] == "insufficient_data"


def test_energy_forecast_handles_missing_one_meter():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    result = MODULE.energy_forecast({"gas_energy_today_kwh": 4, "electricity_energy_today_kwh": None}, now)
    assert result["status"] == "available"
    assert result["gas_kwh"] == 8
    assert result["electricity_kwh"] is None


def test_configured_comfort_target_is_used_in_room_forecast():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    rising = samples(now - timedelta(hours=2), slope=0.2)
    room = {"temperature": 20.0, "demand": True, "temperature_samples": rising}
    result = MODULE.room_forecast(room, now, comfort_target_c=22.0)
    assert result["comfort_target_c"] == 22.0
    assert result["time_to_comfort_minutes"] == 600
