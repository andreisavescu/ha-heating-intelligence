"""Tests for sparse room thermal analytics."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "thermal_analytics.py"
SPEC = importlib.util.spec_from_file_location("thermal_analytics", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_heating_response_and_temperature_trend():
    room = {}
    start = datetime(2026, 11, 12, 7, 0, tzinfo=timezone.utc)
    MODULE.record_temperature_sample(room, start, 20.0, False, False)
    MODULE.record_temperature_sample(room, start + timedelta(minutes=10), 20.0, True, False)
    MODULE.record_temperature_sample(room, start + timedelta(minutes=20), 20.5, True, False)
    stats = MODULE.thermal_statistics(room, start + timedelta(minutes=20))
    assert stats["heating_response_minutes"] == 10
    assert stats["temperature_change_1h"] is None
    assert stats["temperature_sample_count"] == 3


def test_cooling_rate_excludes_open_window_periods():
    room = {}
    start = datetime(2026, 11, 12, 7, 0, tzinfo=timezone.utc)
    MODULE.record_temperature_sample(room, start, 22.0, False, False)
    MODULE.record_temperature_sample(room, start + timedelta(minutes=30), 21.5, False, False)
    MODULE.record_temperature_sample(room, start + timedelta(minutes=40), 21.0, False, True)
    MODULE.record_temperature_sample(room, start + timedelta(minutes=50), 20.5, False, True)
    stats = MODULE.thermal_statistics(room, start + timedelta(minutes=50))
    assert stats["cooling_rate_c_per_hour"] == 1.0
    assert stats["heat_retention_hours_per_degree"] == 1.0


def test_temperature_samples_are_sparse_and_retained():
    room = {}
    start = datetime(2026, 11, 12, 7, 0, tzinfo=timezone.utc)
    MODULE.record_temperature_sample(room, start, 20.0, False, False)
    MODULE.record_temperature_sample(room, start + timedelta(minutes=1), 20.05, False, False)
    assert len(room["temperature_samples"]) == 1
