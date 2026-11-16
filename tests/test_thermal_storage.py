"""Tests for passive thermal-storage response diagnostics."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
PATH = ROOT / "custom_components" / "heating_intelligence" / "thermal_storage.py"
SPEC = importlib.util.spec_from_file_location("thermal_storage", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_sample(index, *, ac=0, pump=0, temp=22.0, window="off", burner="off"):
    stamp = datetime(2026, 11, 15, tzinfo=timezone.utc) + timedelta(minutes=30 * index)
    return {
        "timestamp": stamp.isoformat(),
        "channels": {
            "ac_power": ac, "ac_power_age_minutes": 0,
            "pump:upstairs:power": pump, "pump:upstairs:power_age_minutes": 0,
            "boiler_burner": burner, "boiler_burner_age_minutes": 0,
        },
        "rooms": {"bedroom": {
            "temperature": temp, "temperature_age_minutes": 0,
            "window": window, "window_age_minutes": 0,
        }},
    }


def test_identifies_passive_room_air_coast_without_claiming_slab_capacity():
    samples = [make_sample(i, temp=22 - 0.1 * i) for i in range(6)]
    result = MODULE.build_thermal_storage_response_analysis(
        samples, [{"id": "bedroom"}], [{"id": "upstairs", "floor": "upstairs"}],
        interval_minutes=30,
    )
    assert result["status"] == "passive_room_response_observed"
    assert result["identified_passive_period_count"] == 1
    assert result["rooms"]["bedroom"]["median_coast_rate_c_per_hour"] == -0.2
    assert result["floor_storage"]["slab_capacity_wh_per_k"] is None


def test_ac_or_pump_activity_excludes_samples():
    samples = [make_sample(i, ac=100 if i == 2 else 0, pump=30 if i == 4 else 0) for i in range(6)]
    result = MODULE.build_thermal_storage_response_analysis(
        samples, [{"id": "bedroom"}], [{"id": "upstairs", "floor": "upstairs"}],
    )
    assert result["eligible_sample_count"] == 4
    assert result["excluded_samples"]["ac_active"] == 1
    assert result["excluded_samples"]["upstairs_pump_running"] == 1
    assert result["identified_passive_period_count"] == 0


def test_missing_upstairs_pump_is_not_assumed_off():
    result = MODULE.build_thermal_storage_response_analysis(
        [make_sample(i) for i in range(6)], [{"id": "bedroom"}], [],
    )
    assert result["status"] == "withheld_upstairs_pump_channel_missing"
    assert result["eligible_sample_count"] == 0


def test_open_window_or_unknown_burner_prevents_clean_passive_classification():
    samples = [make_sample(i, window="on" if i == 2 else "off") for i in range(6)]
    result = MODULE.build_thermal_storage_response_analysis(
        samples, [{"id": "bedroom"}], [{"id": "upstairs", "floor": "upstairs"}],
    )
    assert result["eligible_sample_count"] == 5
    assert result["identified_passive_period_count"] == 0
