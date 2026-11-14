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



def test_recorder_room_response_uses_closed_window_known_demand_and_excludes_ac():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    history = {
        "sensor.room": [
            {"state": str(20 + index * 0.1), "last_changed": (end - timedelta(minutes=10 * (6-index))).isoformat()}
            for index in range(7)
        ],
        "binary_sensor.room_demand": [
            {"state": "on", "last_changed": (end - timedelta(hours=2)).isoformat()}
        ],
        "binary_sensor.room_window": [
            {"state": "off", "last_changed": (end - timedelta(hours=2)).isoformat()}
        ],
        "sensor.ac_power": [
            {"state": "0", "last_changed": (end - timedelta(hours=2)).isoformat()}
        ],
    }
    result = MODULE.build_recorder_room_response(
        history,
        [{
            "id": "living",
            "temperature_entity": "sensor.room",
            "demand_entity": "binary_sensor.room_demand",
            "window_entity": "binary_sensor.room_window",
        }],
        "sensor.ac_power",
    )
    room = result["living"]
    assert room["heating"]["status"] == "available"
    assert room["heating"]["rate_c_per_hour"] == 0.6
    assert room["heating"]["valid_intervals"] == 6
    assert room["coasting"]["status"] == "learning"


def test_recorder_room_response_withholds_unknown_window_or_demand():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    history = {
        "sensor.room": [
            {"state": str(20 + index * 0.1), "last_changed": (end - timedelta(minutes=10 * (6-index))).isoformat()}
            for index in range(7)
        ],
        "binary_sensor.room_demand": [
            {"state": "on", "last_changed": (end - timedelta(hours=2)).isoformat()}
        ],
    }
    result = MODULE.build_recorder_room_response(
        history,
        [{
            "id": "bedroom",
            "temperature_entity": "sensor.room",
            "demand_entity": "binary_sensor.room_demand",
            "window_entity": "binary_sensor.missing_window",
        }],
    )
    assert result["bedroom"]["heating"]["status"] == "learning"
    assert result["bedroom"]["heating"]["valid_intervals"] == 0
    assert result["bedroom"]["excluded_samples"]["unknown_mode"] > 0



def test_preliminary_calibration_estimates_apparent_cooling_time_constant():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    history = {
        "sensor.outdoor": [{"state": "5", "last_changed": (end - timedelta(hours=10)).isoformat()}],
        "sensor.room": [],
        "binary_sensor.demand": [{"state": "off", "last_changed": (end - timedelta(hours=10)).isoformat()}],
        "binary_sensor.window": [{"state": "off", "last_changed": (end - timedelta(hours=10)).isoformat()}],
    }
    for index in range(21):
        hours = index * 0.5
        temp = 5 + 15 * (2.718281828 ** (-hours / 12))
        stamp = end - timedelta(hours=10 - hours)
        history["sensor.room"].append({"state": str(temp), "last_changed": stamp.isoformat()})
    result = MODULE.build_preliminary_room_calibration(
        history,
        [{"id": "living", "temperature_entity": "sensor.room",
          "demand_entity": "binary_sensor.demand", "window_entity": "binary_sensor.window"}],
        "sensor.outdoor",
    )
    room = result["rooms"]["living"]
    assert room["status"] == "preliminary_estimate"
    assert room["valid_intervals"] >= 8
    assert 8 <= room["apparent_time_constant_hours"] <= 16
    assert "not" in room["interpretation"]


def test_preliminary_calibration_withholds_when_window_state_is_unknown():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    history = {
        "sensor.outdoor": [{"state": "5", "last_changed": (end - timedelta(hours=2)).isoformat()}],
        "sensor.room": [
            {"state": str(20 - i * 0.1), "last_changed": (end - timedelta(hours=2) + timedelta(minutes=30*i)).isoformat()}
            for i in range(5)
        ],
        "binary_sensor.demand": [{"state": "off", "last_changed": (end - timedelta(hours=2)).isoformat()}],
        "binary_sensor.window": [],
    }
    result = MODULE.build_preliminary_room_calibration(
        history,
        [{"id": "room", "temperature_entity": "sensor.room",
          "demand_entity": "binary_sensor.demand", "window_entity": "binary_sensor.window"}],
        "sensor.outdoor",
    )
    assert result["rooms"]["room"]["status"] == "insufficient_or_non_identifiable_data"


def test_held_out_validation_uses_chronological_holdout_and_beats_persistence():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    history = {
        "sensor.outdoor": [
            {"state": "5", "last_changed": (end - timedelta(hours=24 - index)).isoformat()}
            for index in range(25)
        ],
        "sensor.room": [],
        "binary_sensor.demand": [{"state": "off", "last_changed": (end - timedelta(hours=24)).isoformat()}],
        "binary_sensor.window": [{"state": "off", "last_changed": (end - timedelta(hours=24)).isoformat()}],
    }
    temp = 25.0
    for index in range(25):
        stamp = end - timedelta(hours=24 - index)
        history["sensor.room"].append({"state": str(temp), "last_changed": stamp.isoformat()})
        temp = 5 + (temp - 5) * (2.718281828 ** (-1 / 12))
    result = MODULE.build_held_out_room_validation(
        history,
        [{"id": "living", "temperature_entity": "sensor.room",
          "demand_entity": "binary_sensor.demand", "window_entity": "binary_sensor.window"}],
        "sensor.outdoor",
    )
    room = result["rooms"]["living"]
    assert room["training_intervals"] >= 8
    assert room["holdout_intervals"] >= 4
    assert room["status"] == "improved_over_persistence"
    assert room["holdout_mae_c"] < room["persistence_baseline_mae_c"]
    assert "not a physical capacity" in room["interpretation"]


def test_held_out_validation_rejects_stale_outdoor_observations():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    history = {
        "sensor.outdoor": [{"state": "5", "last_changed": (end - timedelta(hours=24)).isoformat()}],
        "sensor.room": [
            {"state": str(22 - index * 0.1), "last_changed": (end - timedelta(hours=12) + timedelta(minutes=30 * index)).isoformat()}
            for index in range(25)
        ],
        "binary_sensor.demand": [{"state": "off", "last_changed": (end - timedelta(hours=12)).isoformat()}],
        "binary_sensor.window": [{"state": "off", "last_changed": (end - timedelta(hours=12)).isoformat()}],
    }
    result = MODULE.build_held_out_room_validation(
        history,
        [{"id": "room", "temperature_entity": "sensor.room",
          "demand_entity": "binary_sensor.demand", "window_entity": "binary_sensor.window"}],
        "sensor.outdoor",
    )
    room = result["rooms"]["room"]
    assert room["valid_intervals"] == 0
    assert room["excluded_intervals"]["outdoor_missing_or_stale"] > 0


def test_house_effective_rc_capacity_requires_independent_heat_loss_and_fits_tau():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    history = {
        "sensor.outdoor": [],
        "sensor.indoor": [],
        "binary_sensor.demand": [{"state": "off", "last_changed": (end - timedelta(hours=24)).isoformat()}],
        "binary_sensor.window": [{"state": "off", "last_changed": (end - timedelta(hours=24)).isoformat()}],
        "sensor.solar": [],
    }
    temp = 25.0
    for index in range(25):
        stamp = end - timedelta(hours=24 - index)
        history["sensor.outdoor"].append({"state": "5", "last_changed": stamp.isoformat()})
        history["sensor.indoor"].append({"state": str(temp), "last_changed": stamp.isoformat()})
        history["sensor.solar"].append({"state": "0", "last_changed": stamp.isoformat()})
        temp = 5 + (temp - 5) * (2.718281828 ** (-1 / 12))
    rooms = [{
        "id": "whole_house", "area_m2": 200,
        "temperature_entity": "sensor.indoor",
        "demand_entity": "binary_sensor.demand",
        "window_entity": "binary_sensor.window",
    }]
    result = MODULE.build_house_effective_rc_calibration(
        history, rooms, "sensor.outdoor", 300, "sensor.solar",
    )
    assert result["status"] == "effective_aggregate_capacity_estimate"
    assert 10 <= result["apparent_time_constant_hours"] <= 14
    assert 3000 <= result["effective_building_thermal_capacity_wh_per_k"] <= 4200


def test_house_effective_rc_capacity_is_withheld_without_independent_heat_loss():
    result = MODULE.build_house_effective_rc_calibration({}, [], "sensor.outdoor", None)
    assert result["status"] == "withheld_heat_loss_coefficient_unavailable"
    assert result["effective_building_thermal_capacity_wh_per_k"] is None


def test_calibration_dataset_aligns_channels_on_common_timeline_and_tracks_freshness():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    start = end - timedelta(hours=1)
    history = {
        "sensor.outdoor": [
            {"state": "5", "last_changed": start.isoformat()},
            {"state": "6", "last_changed": (start + timedelta(minutes=30)).isoformat()},
        ],
        "sensor.room_a": [
            {"state": "20", "last_changed": start.isoformat()},
            {"state": "20.5", "last_changed": (start + timedelta(minutes=30)).isoformat()},
        ],
        "binary_sensor.demand_a": [{"state": "off", "last_changed": start.isoformat()}],
        "binary_sensor.window_a": [{"state": "off", "last_changed": start.isoformat()}],
        "sensor.solar": [{"state": "0", "last_changed": start.isoformat()}],
        "sensor.boiler": [{"state": "45", "last_changed": start.isoformat()}],
    }
    result = MODULE.build_recorder_calibration_dataset(
        history,
        [{
            "id": "living", "temperature_entity": "sensor.room_a",
            "demand_entity": "binary_sensor.demand_a",
            "window_entity": "binary_sensor.window_a",
        }],
        {"outdoor_temperature": "sensor.outdoor", "solar_radiation": "sensor.solar",
         "water_supply_temperature": "sensor.boiler"},
        start, end, interval_minutes=30, max_age_minutes=45, include_samples=True,
    )
    assert result["status"] == "aligned_dataset_available"
    assert result["aligned_sample_count"] == 3
    assert result["core_eligible_sample_count"] == 3
    assert result["complete_room_state_sample_count"] == 3
    assert result["samples"][1]["rooms"]["living"]["temperature"] == 20.5
    assert result["samples"][2]["channels"]["outdoor_temperature"] == 6.0
    assert result["channel_coverage"]["water_supply_temperature"]["coverage_percent"] == 100.0
    assert result["raw_history_retained"] is False


def test_calibration_dataset_does_not_carry_forward_stale_or_unknown_values():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    start = end - timedelta(hours=2)
    history = {
        "sensor.outdoor": [{"state": "5", "last_changed": start.isoformat()}],
        "sensor.room": [{"state": "20", "last_changed": start.isoformat()}],
        "binary_sensor.demand": [
            {"state": "off", "last_changed": start.isoformat()},
            {"state": "unknown", "last_changed": (start + timedelta(minutes=30)).isoformat()},
        ],
        "binary_sensor.window": [{"state": "off", "last_changed": start.isoformat()}],
    }
    result = MODULE.build_recorder_calibration_dataset(
        history,
        [{"id": "room", "temperature_entity": "sensor.room",
          "demand_entity": "binary_sensor.demand", "window_entity": "binary_sensor.window"}],
        {"outdoor_temperature": "sensor.outdoor"},
        start, end, interval_minutes=30, max_age_minutes=45, include_samples=True,
    )
    assert result["core_eligible_sample_count"] == 2
    assert result["samples"][2]["channels"]["outdoor_temperature"] is None
    assert result["samples"][0]["rooms"]["room"]["demand"] == "off"
    assert result["samples"][1]["rooms"]["room"]["demand"] is None
    assert result["complete_room_state_sample_count"] == 1
    assert result["channel_coverage"]["outdoor_temperature"]["missing_or_stale_samples"] == 3


def test_calibration_dataset_rejects_invalid_cadence():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    try:
        MODULE.build_recorder_calibration_dataset({}, [], {}, end - timedelta(hours=1), end, interval_minutes=0)
    except ValueError as err:
        assert "Invalid calibration dataset" in str(err)
    else:
        raise AssertionError("Invalid cadence should be rejected")
