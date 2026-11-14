"""Tests for recorder-backed calibration history coverage."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "history_validation",
    ROOT / "custom_components" / "heating_intelligence" / "history_validation.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_summarises_numeric_and_state_history_without_retaining_samples():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    start = end - timedelta(days=2)
    history = {
        "sensor.room_temperature": [
            {"state": "20.1", "last_changed": (end - timedelta(hours=2)).isoformat()},
            {"state": "20.4", "last_changed": (end - timedelta(hours=1)).isoformat()},
            {"state": "unknown", "last_changed": end.isoformat()},
        ],
        "binary_sensor.room_window": [
            {"state": "off", "last_changed": (end - timedelta(hours=1)).isoformat()},
            {"state": "on", "last_changed": end.isoformat()},
        ],
    }
    result = MODULE.build_recorder_history_summary(history, start, end)
    room = result["entities"]["sensor.room_temperature"]
    assert result["status"] == "available"
    assert result["total_samples"] == 5
    assert room["samples"] == 3
    assert room["numeric_samples"] == 2
    assert room["span_hours"] == 2.0
    assert room["status"] == "available"
    assert result["entities"]["binary_sensor.room_window"]["numeric_samples"] == 0
    assert result["raw_history_retained"] is False


def test_ignores_invalid_timestamps_and_out_of_window_samples():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    start = end - timedelta(days=1)
    result = MODULE.build_recorder_history_summary(
        {
            "sensor.room": [
                {"state": "20", "last_changed": "not-a-date"},
                {"state": "21", "last_changed": (start - timedelta(seconds=1)).isoformat()},
            ]
        },
        start,
        end,
    )
    assert result["status"] == "no_recorder_history"
    assert result["entities"]["sensor.room"]["samples"] == 0
    assert result["entities"]["sensor.room"]["status"] == "insufficient_history"
