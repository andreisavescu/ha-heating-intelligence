"""Tests for recursive multi-zone historical replay."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
PATH = ROOT / "custom_components" / "heating_intelligence" / "multizone_historical_replay.py"
SPEC = importlib.util.spec_from_file_location("multizone_historical_replay", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_recursive_coupled_replay_scores_a_separate_holdout_without_teacher_forcing():
    start = datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc)
    temperatures = {"living": 22.0, "office": 19.0}
    rooms = [
        {"id": "living", "floor": "ground_floor"},
        {"id": "office", "floor": "upstairs"},
    ]
    models = {
        "living": {
            "predictors": ["envelope", "office"],
            "normalized_rates_per_hour": {"envelope": 0.02, "office": 0.01},
            "intercept_c_per_hour": 0.0,
            "training_end": start.isoformat(),
        },
        "office": {
            "predictors": ["envelope", "living"],
            "normalized_rates_per_hour": {"envelope": 0.025, "living": 0.012},
            "intercept_c_per_hour": 0.0,
            "training_end": start.isoformat(),
        },
    }
    samples = []
    for index in range(13):
        stamp = start + timedelta(minutes=30 * index)
        samples.append({
            "timestamp": stamp.isoformat(),
            "channels": {
                "outdoor_temperature": 5.0,
                "solar_radiation": 0.0,
                "solar_radiation_unit": "W/m²",
            },
            "rooms": {
                room_id: {"temperature": value, "demand": False, "window": False}
                for room_id, value in temperatures.items()
            },
        })
        if index < 12:
            previous = dict(temperatures)
            temperatures = {
                "living": previous["living"] + (
                    0.02 * (5.0 - previous["living"])
                    + 0.01 * (previous["office"] - previous["living"])
                ) * 0.5,
                "office": previous["office"] + (
                    0.025 * (5.0 - previous["office"])
                    + 0.012 * (previous["living"] - previous["office"])
                ) * 0.5,
            }

    result = MODULE.build_multizone_historical_replay(
        samples,
        rooms,
        {"rooms": models},
        interval_minutes=30,
    )
    assert result["status"] == "available"
    assert result["eligible_intervals"] == 12
    assert result["aggregate_mae_c"] == 0.0
    assert result["improved_over_persistence"] is True
    assert result["reinitialized_segments"] == 1
    assert result["raw_history_retained"] is False


def test_replay_uses_measured_attic_temperature_as_upstairs_boundary():
    start = datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc)
    temperature = 21.0
    samples = []
    for index in range(5):
        stamp = start + timedelta(minutes=30 * index)
        samples.append({
            "timestamp": stamp.isoformat(),
            "channels": {
                "outdoor_temperature": 5.0,
                "attic_temperature": 12.0,
                "solar_radiation": 0.0,
                "solar_radiation_unit": "W/m²",
            },
            "rooms": {
                "upstairs": {"temperature": temperature, "demand": "off", "window": "off"},
                "living": {"temperature": 20.0, "demand": "off", "window": "off"},
            },
        })
        temperature += 0.015 * (12.0 - temperature) * 0.5
    models = {
        "upstairs": {
            "predictors": ["envelope", "living", "attic"],
            "normalized_rates_per_hour": {"envelope": 0.0, "living": 0.0, "attic": 0.015},
            "intercept_c_per_hour": 0.0,
            "training_end": start.isoformat(),
        },
        "living": {
            "predictors": ["envelope", "upstairs"],
            "normalized_rates_per_hour": {"envelope": 0.0, "upstairs": 0.0},
            "intercept_c_per_hour": 0.0,
            "training_end": start.isoformat(),
        },
    }
    result = MODULE.build_multizone_historical_replay(
        samples,
        [{"id": "upstairs", "floor": "upstairs"}, {"id": "living", "floor": "ground_floor"}],
        {"rooms": models},
    )
    assert result["status"] == "available"
    assert result["attic_boundary_used"] is True
    assert result["aggregate_mae_c"] == 0.0


def test_replay_is_withheld_when_any_room_has_no_independent_training_fit():
    result = MODULE.build_multizone_historical_replay(
        [],
        [{"id": "living"}, {"id": "office"}],
        {"rooms": {"living": {"normalized_rates_per_hour": {"envelope": 0.1}, "predictors": ["envelope"], "training_end": "2026-11-24T00:00:00Z"}}},
    )
    assert result["status"] == "withheld_missing_room_response_fits"
    assert result["rooms_missing_fits"] == ["office"]
