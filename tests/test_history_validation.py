"""Tests for recorder-backed calibration history coverage."""
from datetime import datetime, timedelta, timezone
import math
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
    assert result["capacity_resampling_sensitivity"]["status"] == "insufficient_history_for_resampling_sensitivity"
    assert result["capacity_resampling_sensitivity"]["successful_replicates"] == 0


def test_house_effective_rc_capacity_reports_block_resampling_sensitivity():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    history = {
        "sensor.outdoor": [],
        "sensor.indoor": [],
        "binary_sensor.demand": [{"state": "off", "last_changed": (end - timedelta(hours=60)).isoformat()}],
        "binary_sensor.window": [{"state": "off", "last_changed": (end - timedelta(hours=60)).isoformat()}],
        "sensor.solar": [],
    }
    temp = 35.0
    for index in range(61):
        stamp = end - timedelta(hours=60 - index)
        history["sensor.outdoor"].append({"state": "5", "last_changed": stamp.isoformat()})
        history["sensor.indoor"].append({"state": str(temp), "last_changed": stamp.isoformat()})
        history["sensor.solar"].append({"state": "0", "last_changed": stamp.isoformat()})
        temp = 5 + (temp - 5) * math.exp(-1 / 30)
    result = MODULE.build_house_effective_rc_calibration(
        history,
        [{
            "id": "whole_house", "area_m2": 200,
            "temperature_entity": "sensor.indoor",
            "demand_entity": "binary_sensor.demand",
            "window_entity": "binary_sensor.window",
        }],
        "sensor.outdoor", 300, "sensor.solar",
    )
    sensitivity = result["capacity_resampling_sensitivity"]
    assert result["valid_intervals"] >= 40
    assert sensitivity["status"] == "resampling_sensitivity_available"
    assert sensitivity["successful_replicates"] >= 40
    assert sensitivity["lower_90_percentile_wh_per_k"] <= sensitivity["upper_90_percentile_wh_per_k"]


def test_house_effective_rc_combines_heat_loss_and_time_constant_sensitivity():
    end = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    history = {
        "sensor.outdoor": [], "sensor.indoor": [], "sensor.solar": [],
        "binary_sensor.demand": [{"state": "off", "last_changed": (end - timedelta(hours=60)).isoformat()}],
        "binary_sensor.window": [{"state": "off", "last_changed": (end - timedelta(hours=60)).isoformat()}],
    }
    temp = 21.0
    for index in range(60):
        stamp = end - timedelta(hours=60 - index)
        history["sensor.outdoor"].append({"state": "5", "last_changed": stamp.isoformat()})
        history["sensor.indoor"].append({"state": str(temp), "last_changed": stamp.isoformat()})
        history["sensor.solar"].append({"state": "0", "last_changed": stamp.isoformat()})
        temp = 5 + (temp - 5) * math.exp(-1 / 30)
    result = MODULE.build_house_effective_rc_calibration(
        history,
        [{
            "id": "whole_house", "area_m2": 200,
            "temperature_entity": "sensor.indoor",
            "demand_entity": "binary_sensor.demand",
            "window_entity": "binary_sensor.window",
        }],
        "sensor.outdoor", 300, "sensor.solar",
        heat_loss_sensitivity_range_w_k={"minimum": 250, "maximum": 350},
    )
    sensitivity = result["capacity_assumption_sensitivity"]
    assert sensitivity["status"] == "combined_heat_loss_and_time_constant_sensitivity_available"
    assert sensitivity["lower_wh_per_k"] < sensitivity["upper_wh_per_k"]
    assert sensitivity["heat_loss_range_w_k"]["minimum"] == 250


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
    assert result["complete_room_state_sample_count"] == 2
    assert result["samples"][1]["rooms"]["living"]["temperature"] == 20.5
    assert result["samples"][2]["channels"]["outdoor_temperature"] == 6.0
    assert result["channel_coverage"]["water_supply_temperature"]["coverage_percent"] == 66.7
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


def test_multizone_identification_recovers_normalized_passive_response_and_holds_out_late_data():
    import math

    samples = []
    room_a, room_b = 22.0, 19.0
    for index in range(151):
        outside = 5.0 + 4.0 * math.sin(index * 0.19) + 1.5 * math.sin(index * 0.047)
        samples.append({
            "timestamp": (datetime(2026, 11, 1, tzinfo=timezone.utc) + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "outdoor_temperature": outside,
                "solar_radiation": 0.0,
                "ac_power": 0.0,
            },
            "rooms": {
                "living": {"temperature": room_a, "demand": "off", "window": "off"},
                "bedroom": {"temperature": room_b, "demand": "off", "window": "off"},
            },
        })
        if index < 150:
            next_a = room_a + 0.5 * (0.035 * (outside - room_a) + 0.018 * (room_b - room_a))
            next_b = room_b + 0.5 * (0.028 * (outside - room_b) + 0.012 * (room_a - room_b))
            room_a, room_b = next_a, next_b

    result = MODULE.build_multizone_response_identification(
        samples,
        [{"id": "living"}, {"id": "bedroom"}],
        interval_minutes=30,
        minimum_train_intervals=24,
        minimum_holdout_intervals=8,
    )
    assert result["status"] == "room_response_identification_available"
    house_validation = result["house_level_holdout_validation"]
    assert house_validation["rooms_validated"] == 2
    assert house_validation["holdout_room_intervals"] > 0
    assert house_validation["weighted_mae_c"] < house_validation["persistence_baseline_weighted_mae_c"]
    for room_id in ("living", "bedroom"):
        room = result["rooms"][room_id]
        assert room["normalized_rates_per_hour"] is not None
        assert room["training_intervals"] >= 24
        assert room["holdout_intervals"] >= 8
        assert room["normalized_rates_per_hour"]["envelope"] >= 0
        assert room["normalized_rates_per_hour"]["bedroom" if room_id == "living" else "living"] >= 0
        assert room["holdout_mae_c"] < room["persistence_baseline_mae_c"]
        assert "not physical conductances" in room["interpretation"]


def test_multizone_identification_withholds_when_solar_or_room_state_is_unavailable():
    samples = []
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for index in range(80):
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {"outdoor_temperature": 5.0, "solar_radiation": None},
            "rooms": {
                "living": {"temperature": 21.0 - index * 0.01, "demand": "off", "window": "off"},
                "bedroom": {"temperature": 20.0 - index * 0.01, "demand": "off", "window": "off"},
            },
        })
    result = MODULE.build_multizone_response_identification(
        samples, [{"id": "living"}, {"id": "bedroom"}],
    )
    assert result["status"] == "insufficient_or_non_identifiable_history"
    assert result["rooms"]["living"]["eligible_intervals"] == 0
    assert result["rooms"]["living"]["excluded_intervals"]["solar_missing_or_high"] > 0


def test_multizone_identification_requires_multiple_rooms():
    result = MODULE.build_multizone_response_identification([], [{"id": "only_room"}])
    assert result["status"] == "insufficient_zones"


def test_multizone_identification_requires_configured_ac_and_boiler_off_telemetry():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(40):
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "outdoor_temperature": 5.0,
                "solar_radiation": 0.0,
                "ac_power": None,
                "boiler_burner": "on",
                "boiler_modulation": 20.0,
            },
            "rooms": {
                "living": {"temperature": 21.0 - index * 0.02, "demand": "off", "window": "off"},
                "bedroom": {"temperature": 20.0 - index * 0.01, "demand": "off", "window": "off"},
            },
        })
    result = MODULE.build_multizone_response_identification(
        samples,
        [{"id": "living"}, {"id": "bedroom"}],
        require_ac_power=True,
        require_boiler_burner_off=True,
        require_boiler_modulation_zero=True,
    )
    room = result["rooms"]["living"]
    assert room["eligible_intervals"] == 0
    assert room["excluded_intervals"]["ac_heating_active_or_unavailable"] > 0
    assert room["excluded_intervals"]["boiler_active_or_unavailable"] == 0
    boiler_only = MODULE.build_multizone_response_identification(
        samples,
        [{"id": "living"}, {"id": "bedroom"}],
        require_boiler_burner_off=True,
        require_boiler_modulation_zero=True,
    )
    assert boiler_only["rooms"]["living"]["excluded_intervals"]["boiler_active_or_unavailable"] > 0


def test_multizone_global_candidate_count_is_not_multiplied_by_room_count():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(5):
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {"outdoor_temperature": 5.0, "solar_radiation": 0.0},
            "rooms": {
                "living": {"temperature": 21.0 - index * 0.1, "demand": "off", "window": "off"},
                "bedroom": {"temperature": 20.0 - index * 0.1, "demand": "off", "window": "off"},
            },
        })
    result = MODULE.build_multizone_response_identification(
        samples, [{"id": "living"}, {"id": "bedroom"}],
    )
    assert result["global_interval_counts"]["candidate"] == 4


def test_multizone_identification_withholds_collinear_thermal_drivers():
    samples = []
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for index in range(100):
        variation = 2.0 * math.sin(index / 5.0) + 0.5 * math.sin(index / 2.7)
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "outdoor_temperature": 5.0 + variation,
                "solar_radiation": 0.0,
            },
            "rooms": {
                "living": {
                    "temperature": 20.0,
                    "demand": "off",
                    "window": "off",
                },
                "bedroom": {
                    "temperature": 20.0 + 2.0 * variation,
                    "demand": "off",
                    "window": "off",
                },
            },
        })
    result = MODULE.build_multizone_response_identification(
        samples,
        [{"id": "living"}, {"id": "bedroom"}],
        minimum_train_intervals=20,
        minimum_holdout_intervals=5,
    )
    living = result["rooms"]["living"]
    assert living["status"] == "non_identifiable_predictor_collinearity"
    assert living["normalized_rates_per_hour"] is None
    assert living["max_abs_predictor_correlation"] >= 0.985
    assert living["predictor_correlations"]
    assert living["predictor_excitation"]["bedroom"]["positive_gradient_intervals"] > 0
    assert living["predictor_excitation"]["bedroom"]["negative_gradient_intervals"] > 0
    assert living["room_link_excitation_status"] in (
        "all_room_links_bidirectionally_excited",
        "one_or_more_room_links_weakly_or_one_sided_excited",
    )


def test_multizone_identification_reports_one_sided_room_link_excitation():
    samples = []
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for index in range(100):
        outside = 5.0 + 2.0 * math.sin(index / 8.0)
        # Keep the neighbour consistently warmer: its gradient never reverses.
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {"outdoor_temperature": outside, "solar_radiation": 0.0},
            "rooms": {
                "living": {"temperature": 19.0, "demand": "off", "window": "off"},
                "bedroom": {"temperature": 20.0 + 0.05 * math.sin(index / 6.0), "demand": "off", "window": "off"},
            },
        })
    result = MODULE.build_multizone_response_identification(
        samples,
        [{"id": "living"}, {"id": "bedroom"}],
        minimum_train_intervals=20,
        minimum_holdout_intervals=5,
    )
    living = result["rooms"]["living"]
    if living["predictor_excitation"]:
        link = living["predictor_excitation"]["bedroom"]
        assert link["negative_gradient_intervals"] == 0
        assert link["bidirectional_excitation"] is False
        assert "bedroom" in living["weakly_excited_room_links"]


def test_multizone_identification_reports_correlation_diagnostics_when_fit_is_usable():
    samples = []
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    living, bedroom = 21.0, 20.0
    for index in range(180):
        outside = 4.0 + 2.5 * math.sin(index / 13.0) + 0.8 * math.sin(index / 4.1)
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {"outdoor_temperature": outside, "solar_radiation": 0.0},
            "rooms": {
                "living": {"temperature": living, "demand": "off", "window": "off"},
                "bedroom": {"temperature": bedroom, "demand": "off", "window": "off"},
            },
        })
        if index < 179:
            next_living = living + 0.5 * (0.035 * (outside - living) + 0.018 * (bedroom - living))
            next_bedroom = bedroom + 0.5 * (0.028 * (outside - bedroom) + 0.012 * (living - bedroom))
            living, bedroom = next_living, next_bedroom
    result = MODULE.build_multizone_response_identification(
        samples,
        [{"id": "living"}, {"id": "bedroom"}],
        minimum_train_intervals=24,
        minimum_holdout_intervals=8,
    )
    for room_id in ("living", "bedroom"):
        room = result["rooms"][room_id]
        assert room["max_abs_predictor_correlation"] is not None
        assert room["predictor_correlations"]
        assert room["identifiability_threshold"] == 0.985
        assert room["temporal_stability_method"] == "three_contiguous_training_periods_with_full_rank_gate"
        assert len(room["temporal_training_windows"]) == 3
        assert room["temporal_coefficient_stability"]["envelope"]["total_windows"] == 3
        assert all(
            window["training_interval_end"] is None or window["status"] != "fit_available"
            or window["normalized_rates_per_hour"] is not None
            for window in room["temporal_training_windows"]
        )
        assert room["coefficient_uncertainty_method"] == "deterministic_moving_block_bootstrap_90_percentile_range"
        uncertainty = room["coefficient_uncertainty"]["envelope"]
        assert uncertainty["successful_replicates"] >= 40
        assert uncertainty["lower_90_percentile_1_per_hour"] is not None
        assert uncertainty["upper_90_percentile_1_per_hour"] >= uncertainty["lower_90_percentile_1_per_hour"]



def test_multizone_identification_rejects_joint_collinearity_not_visible_pairwise():
    samples = []
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    # Three predictors: x3 is the sum of x1 and x2. Pairwise correlations
    # remain below the 0.985 threshold, but the complete matrix is singular.
    for index in range(180):
        x1 = math.sin(index * 0.17) + 0.3 * math.sin(index * 0.071)
        x2 = math.sin(index * 0.31 + 0.8) + 0.2 * math.sin(index * 0.053)
        x3 = x1 + x2
        outside = 8.0 + x1
        living = 20.0 + 0.1 * x2
        bedroom = 19.0 + 0.1 * x3
        third = 18.0 + 0.1 * (x1 - x2)
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {"outdoor_temperature": outside, "solar_radiation": 0.0},
            "rooms": {
                "living": {"temperature": living, "demand": "off", "window": "off"},
                "bedroom": {"temperature": bedroom, "demand": "off", "window": "off"},
                "third": {"temperature": third, "demand": "off", "window": "off"},
            },
        })
    result = MODULE.build_multizone_response_identification(
        samples,
        [{"id": "living"}, {"id": "bedroom"}, {"id": "third"}],
        minimum_train_intervals=20,
        minimum_holdout_intervals=5,
        max_predictor_correlation=0.99999,
    )
    room = result["rooms"]["living"]
    assert room["status"] == "non_identifiable_predictor_matrix"
    assert room["normalized_rates_per_hour"] is None
    assert room["predictor_matrix_rank"] < len(room["predictors"])
    assert room["predictor_condition_number"] is None or room["predictor_condition_number"] > 1_000_000


def test_multizone_identification_reports_full_matrix_condition_diagnostics():
    samples = []
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    living, bedroom = 21.0, 20.0
    for index in range(180):
        outside = 4.0 + 2.5 * math.sin(index / 13.0) + 0.8 * math.sin(index / 4.1)
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {"outdoor_temperature": outside, "solar_radiation": 0.0},
            "rooms": {
                "living": {"temperature": living, "demand": "off", "window": "off"},
                "bedroom": {"temperature": bedroom, "demand": "off", "window": "off"},
            },
        })
        if index < 179:
            living, bedroom = (
                living + 0.5 * (0.035 * (outside - living) + 0.018 * (bedroom - living)),
                bedroom + 0.5 * (0.028 * (outside - bedroom) + 0.012 * (living - bedroom)),
            )
    result = MODULE.build_multizone_response_identification(
        samples, [{"id": "living"}, {"id": "bedroom"}],
        minimum_train_intervals=24, minimum_holdout_intervals=8,
    )
    room = result["rooms"]["living"]
    assert room["predictor_matrix_rank"] == len(room["predictors"])
    assert room["predictor_condition_number"] is not None


def test_ufh_observability_reports_excitation_without_claiming_capacity():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(12):
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "pump:ground_floor:power": 40.0 if index < 6 else 0.0,
                "pump:ground_floor:power_age_minutes": 0,
                "water_supply_temperature": 35.0,
                "water_supply_temperature_age_minutes": 0,
                "water_return_temperature": 30.0,
                "water_return_temperature_age_minutes": 0,
            },
            "rooms": {"living": {"temperature": 20.0 + index * 0.1}},
        })
    result = MODULE.build_ufh_slab_observability(
        samples, [{"id": "living", "floor": "ground_floor"}]
    )
    floor = result["floors"]["ground_floor"]
    assert floor["status"] == "pump_on_off_excitation_observed_but_capacity_unidentifiable"
    assert floor["pump_on_intervals"] == 5
    assert floor["pump_off_intervals"] == 6
    assert floor["median_supply_return_delta_t_pump_on_c"] == 5.0
    assert floor["thermal_output_w"] is None
    assert floor["slab_capacity_wh_per_k"] is None


def test_ufh_observability_withholds_when_pump_history_is_missing():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = [{
        "timestamp": start.isoformat(),
        "channels": {},
        "rooms": {"living": {"temperature": 20.0}},
    }]
    result = MODULE.build_ufh_slab_observability(
        samples, [{"id": "living", "floor": "ground_floor"}]
    )
    assert result["floors"]["ground_floor"]["status"] == "insufficient_pump_excitation_or_history"
    assert result["floors"]["ground_floor"]["slab_capacity_wh_per_k"] is None


def test_thermal_input_energy_uses_only_monotonic_cumulative_kwh_meters():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(7):
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "gas_consumption": 100.0 + index * 0.2,
                "gas_consumption_age_minutes": 0,
                "gas_consumption_unit": "kWh",
                "ac_energy": 20.0 + index * 0.1,
                "ac_energy_age_minutes": 0,
                "ac_energy_unit": "kWh",
            },
        })
    result = MODULE.build_thermal_input_energy_observability(samples)
    assert result["status"] == "measured_energy_channels_available"
    assert result["meters"]["gas_consumption"]["measured_energy_kwh"] == 1.2
    assert result["meters"]["gas_consumption"]["useful_heat_sensitivity_kwh"]["boiler_efficiency_0_90"] == 1.08
    assert result["meters"]["ac_energy"]["measured_energy_kwh"] == 0.6
    assert result["meters"]["ac_energy"]["delivered_heat_sensitivity_kwh"]["cop_3"] == 1.8


def test_thermal_input_energy_withholds_gas_m3_and_meter_resets():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index, value in enumerate((10.0, 10.2, 10.4, 10.6, 10.8, 1.0, 1.2)):
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "gas_consumption": value,
                "gas_consumption_age_minutes": 0,
                "gas_consumption_unit": "m³",
            },
        })
    result = MODULE.build_thermal_input_energy_observability(samples)
    gas = result["meters"]["gas_consumption"]
    assert gas["measured_energy_kwh"] is None
    assert gas["invalid_intervals"]["unit_not_energy_or_unsupported"] == 7


def test_thermal_input_room_response_alignment_pairs_verified_energy_with_room_slopes():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(12):
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "gas_consumption": 100.0 + index * (0.1 + index * 0.01),
                "gas_consumption_age_minutes": 0,
                "gas_consumption_unit": "kWh",
                "ac_energy": 20.0 + index * 0.05,
                "ac_energy_age_minutes": 0,
                "ac_energy_unit": "kWh",
            },
            "rooms": {
                "living": {"temperature": 20.0 + index * index * 0.01},
                "bedroom": {"temperature": 19.0 + index * index * 0.005},
            },
        })
    result = MODULE.build_thermal_input_room_response_alignment(
        samples, [{"id": "living"}, {"id": "bedroom"}], minimum_intervals=8
    )
    gas = result["meters"]["gas_consumption"]
    assert gas["status"] == "lagged_input_response_pairs_available"
    assert gas["paired_intervals_at_zero_lag"] == 11
    assert gas["lag_scan"][0]["pearson_r"] is not None
    assert gas["lag_scan"][0]["confidence_interval_95"] is not None
    assert gas["lag_scan"][0]["split_half"]["status"] == "same_direction_reasonably_stable"
    assert gas["plant_input_excitation_status"] == "sufficient_variation_for_association_screen"
    assert gas["response_stability_status"] == "same_direction_across_history_halves"
    assert result["rooms"]["gas_consumption"]["living"]["paired_intervals_at_zero_lag"] == 11
    assert result["capacity_identification_status"] == "withheld_no_room_heat_allocation_or_independent_thermal_output"


def test_thermal_input_room_response_alignment_rejects_unsupported_gas_units():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(10):
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {"gas_consumption": 1.0 + index * 0.1, "gas_consumption_age_minutes": 0, "gas_consumption_unit": "m³"},
            "rooms": {"living": {"temperature": 20.0 + index * 0.1}},
        })
    result = MODULE.build_thermal_input_room_response_alignment(
        samples, [{"id": "living"}], minimum_intervals=4
    )
    assert result["meters"]["gas_consumption"]["paired_intervals_at_zero_lag"] == 0
    assert result["meters"]["gas_consumption"]["exclusions"]["unsupported_unit"] == 9

def test_thermal_input_room_response_alignment_requires_complete_room_coverage():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(10):
        rooms = {"living": {"temperature": 20.0 + index * 0.1}}
        if index != 5:
            rooms["bedroom"] = {"temperature": 19.0 + index * 0.05}
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "gas_consumption": 1.0 + index * 0.1,
                "gas_consumption_age_minutes": 0,
                "gas_consumption_unit": "kWh",
            },
            "rooms": rooms,
        })
    result = MODULE.build_thermal_input_room_response_alignment(
        samples, [{"id": "living"}, {"id": "bedroom"}], minimum_intervals=4
    )
    assert result["meters"]["gas_consumption"]["paired_intervals_at_zero_lag"] == 7
    assert result["meters"]["gas_consumption"]["exclusions"]["incomplete_room_temperature_coverage"] == 2
    assert result["maximum_lag_minutes"] == 120



def test_room_level_excitation_identifiability_detects_independent_demand_patterns():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    patterns = [(True, False), (False, True), (True, True), (False, False)] * 4
    samples = []
    for index in range(len(patterns)):
        left, right = patterns[index]
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "pump:ground_floor:power": 30.0 if left else 0.0,
                "pump:ground_floor:power_age_minutes": 0,
                "pump:upstairs:power": 30.0 if right else 0.0,
                "pump:upstairs:power_age_minutes": 0,
            },
            "rooms": {
                "living": {"demand": "on" if left else "off", "demand_age_minutes": 0, "temperature": 20.0 + index * 0.02},
                "bedroom": {"demand": "on" if right else "off", "demand_age_minutes": 0, "temperature": 19.0 + index * 0.01},
            },
        })
    result = MODULE.build_room_level_excitation_identifiability(
        samples,
        [{"id": "living", "floor": "ground_floor"}, {"id": "bedroom", "floor": "upstairs"}],
        minimum_mode_intervals=3,
    )
    assert result["rooms_with_both_demand_modes"] == 2
    assert result["distinct_simultaneous_demand_patterns"] == 4
    assert result["demand_design_matrix_rank"] == 2
    assert result["demand_design_matrix_full_rank"] is True
    assert result["room_specific_input_separation"] == "potentially_excited_but_not_identified"
    assert result["floors"]["ground_floor"]["pump_transitions"] > 0


def test_room_level_excitation_identifiability_withholds_when_demands_are_locked_together():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(12):
        on = index % 2 == 0
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {},
            "rooms": {
                "living": {"demand": "on" if on else "off", "demand_age_minutes": 0, "temperature": 20.0 + index * 0.02},
                "bedroom": {"demand": "on" if on else "off", "demand_age_minutes": 0, "temperature": 19.0 + index * 0.01},
            },
        })
    result = MODULE.build_room_level_excitation_identifiability(
        samples,
        [{"id": "living"}, {"id": "bedroom"}],
        minimum_mode_intervals=3,
    )
    assert result["rooms_with_both_demand_modes"] == 2
    assert result["distinct_simultaneous_demand_patterns"] == 2
    assert result["demand_design_matrix_rank"] == 1
    assert result["demand_design_matrix_full_rank"] is False
    assert result["room_specific_input_separation"] == "insufficient_independent_room_demand_patterns"


def test_pearson_diagnostics_report_uncertainty_and_split_half_sign_reversal():
    stable = MODULE._pearson_diagnostics(
        [1, 2, 3, 4, 5, 6, 7, 8],
        [2, 4, 6, 8, 10, 12, 14, 16],
    )
    assert stable["confidence_interval_95"] is not None
    assert stable["split_half"]["status"] == "same_direction_reasonably_stable"

    reversing = MODULE._pearson_diagnostics(
        [1, 2, 3, 4, 5, 6, 7, 8],
        [1, 2, 3, 4, 4, 3, 2, 1],
    )
    assert reversing["split_half"]["status"] == "sign_reversal_between_halves"


def test_thermal_input_response_withholds_non_repeatable_history_halves():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(20):
        # First half: rising input and rising temperature response.
        # Second half: rising input but falling response.
        response = index * 0.02 if index < 10 else (20 - index) * 0.02
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "gas_consumption": 100.0 + index * index * 0.01,
                "gas_consumption_age_minutes": 0,
                "gas_consumption_unit": "kWh",
            },
            "rooms": {"living": {"temperature": 20.0 + response * index}},
        })
    result = MODULE.build_thermal_input_room_response_alignment(
        samples, [{"id": "living"}], minimum_intervals=6, maximum_lag_intervals=0,
    )
    gas = result["meters"]["gas_consumption"]
    assert gas["response_stability_status"] == "unstable_sign_between_history_halves"
    assert gas["evidence_status"] == "withheld_non_repeatable_response"


def test_thermal_input_response_flags_flat_input_variation():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(12):
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "gas_consumption": 100.0 + index * (0.1000 + (index % 2) * 0.00001),
                "gas_consumption_age_minutes": 0,
                "gas_consumption_unit": "kWh",
            },
            "rooms": {"living": {"temperature": 20.0 + index * index * 0.01}},
        })
    result = MODULE.build_thermal_input_room_response_alignment(
        samples, [{"id": "living"}], minimum_intervals=8, maximum_lag_intervals=0,
    )
    gas = result["meters"]["gas_consumption"]
    assert gas["plant_input_excitation_status"] == "weak_or_flat_input_variation"
    assert gas["evidence_status"] in (
        "withheld_weak_plant_input_excitation",
        "insufficient_verified_input_response_pairs",
    )


def test_pearson_diagnostics_marks_wide_nominal_interval_as_uncertain():
    diagnostics = MODULE._pearson_diagnostics(
        list(range(1, 13)),
        [1, -1, 1, -1, 1, -1, 1, -1, 1, -1, 1, -1],
    )
    interval = diagnostics["confidence_interval_95"]
    assert interval is not None
    assert interval[0] <= 0 <= interval[1]


def test_partial_correlation_removes_shared_weather_driver():
    shared = [float(index) for index in range(40)]
    noise_x = [1.0 if index % 2 else -1.0 for index in range(40)]
    noise_y = [1.0 if index % 4 < 2 else -1.0 for index in range(40)]
    xs = [value + noise for value, noise in zip(shared, noise_x)]
    ys = [2.0 * value + noise for value, noise in zip(shared, noise_y)]
    result = MODULE._partial_correlation_diagnostics(
        xs, ys, {"outdoor_temperature_c": shared}
    )
    assert result["status"] == "adjusted_association_available"
    assert result["design_rank"] == 2
    assert abs(result["partial_pearson_r"]) < 0.1
    assert result["confidence_interval_95"] is not None
    assert result["confidence_interval_degrees_of_freedom"] == 36


def test_partial_correlation_withholds_collinear_covariates():
    shared = [float(index) for index in range(20)]
    result = MODULE._partial_correlation_diagnostics(
        [value + 1 for value in shared],
        [value * 2 + 1 for value in shared],
        {"outdoor_temperature_c": shared, "duplicate_weather_driver": shared},
    )
    assert result["status"] == "rank_deficient_adjustment_design"
    assert result["partial_pearson_r"] is None


def test_thermal_input_alignment_reports_weather_and_demand_adjustment():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    samples = []
    cumulative = 100.0
    for index in range(36):
        outdoor = 5.0 + (index % 9) * 0.7
        solar = float((index * 13) % 80)
        demand = "on" if index % 3 else "off"
        cumulative += 0.12 + (index % 7) * 0.025
        samples.append({
            "timestamp": (start + timedelta(minutes=30 * index)).isoformat(),
            "channels": {
                "gas_consumption": cumulative,
                "gas_consumption_age_minutes": 0,
                "gas_consumption_unit": "kWh",
                "outdoor_temperature": outdoor,
                "outdoor_temperature_age_minutes": 0,
                "solar_radiation": solar,
                "solar_radiation_age_minutes": 0,
            },
            "rooms": {
                "living": {
                    "temperature": 20.0 + index * 0.015 + (index % 5) * 0.004,
                    "demand": demand,
                    "window": "off",
                },
            },
        })
    result = MODULE.build_thermal_input_room_response_alignment(
        samples, [{"id": "living"}], minimum_intervals=8, maximum_lag_intervals=1,
    )
    zero_lag = result["meters"]["gas_consumption"]["lag_scan"][0]
    adjustment = zero_lag["confounder_adjustment"]
    assert "outdoor_temperature_c" in adjustment["covariates"]
    assert "solar_radiation_w_m2" in adjustment["covariates"]
    assert "room_demand_fraction" in adjustment["covariates"]
    assert adjustment["paired_intervals"] >= 8
    assert adjustment["confidence_interval_degrees_of_freedom"] == (
        adjustment["paired_intervals"] - len(adjustment["covariates"]) - 3
    )
    assert result["meters"]["gas_consumption"]["adjusted_lag_peak_status"] in (
        "distinct_adjusted_descriptive_peak",
        "ambiguous_adjusted_neighbouring_lag_peaks",
        "insufficient_adjusted_lags",
    )


def test_partial_correlation_block_sensitivity_reports_resampling_range():
    shared = [float(index) for index in range(80)]
    xs = [0.2 * value + math.sin(index * 0.7) for index, value in enumerate(shared)]
    ys = [
        0.5 * value + 0.8 * math.sin(index * 0.7) + 0.15 * math.cos(index * 0.31)
        for index, value in enumerate(shared)
    ]
    result = MODULE._partial_correlation_block_sensitivity(
        xs, ys, {"outdoor_temperature_c": shared}, replicates=40,
    )
    assert result["status"] == "block_sensitivity_available"
    assert result["successful_replicates"] >= 20
    assert result["lower_5_percentile_partial_r"] <= result["median_partial_r"]
    assert result["median_partial_r"] <= result["upper_95_percentile_partial_r"]


def test_multizone_holdout_diagnostics_report_bias_autocorrelation_and_trend():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    holdout = [
        (start + timedelta(minutes=30 * index), [0.0], value)
        for index, value in enumerate(
            [0.01, 0.02, 0.03, 0.05, 0.07, 0.10, 0.13, 0.17,
             0.20, 0.24, 0.28, 0.33, 0.38, 0.44, 0.50, 0.57,
             0.64, 0.72, 0.80, 0.89, 0.98, 1.08, 1.18, 1.29]
        )
    ]
    result = MODULE._multizone_holdout_diagnostics(
        holdout, [0.0], 0.0, 30,
    )
    assert result["status"] == "holdout_residuals_available"
    assert result["interval_count"] == 24
    assert result["mean_bias_c"] < 0
    assert result["lag_one_residual_correlation"] > 0.9
    assert result["residual_linear_trend_c_per_day"] < 0
    assert result["cadence_deviation_intervals"] == 0
    sensitivity = result["block_error_sensitivity"]
    assert sensitivity["successful_replicates"] == 80
    assert sensitivity["mae_5th_50th_95th_c"][0] <= sensitivity["mae_5th_50th_95th_c"][2]
    assert "not a confidence interval" in sensitivity["interpretation"]


def test_multizone_holdout_diagnostics_withhold_short_resampling_history():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    holdout = [
        (start + timedelta(minutes=30 * index), [1.0], 0.1 * index)
        for index in range(8)
    ]
    result = MODULE._multizone_holdout_diagnostics(
        holdout, [0.0], 0.0, 30,
    )
    assert result["status"] == "holdout_residuals_available"
    assert result["block_error_sensitivity"] is None


def test_multizone_physical_readiness_separates_normalized_fit_from_absolute_parameters():
    samples = [
        {
            "rooms": {
                "living": {"temperature": 20.0 + index * 0.01, "demand": "off", "window": "off"},
                "hall": {"temperature": 19.0 + index * 0.02, "demand": "off", "window": "off"},
            },
            "channels": {"gas_consumption": float(index), "gas_consumption_age_minutes": 0},
        }
        for index in range(12)
    ]
    response = {
        "rooms": {
            "living": {
                "status": "holdout_better_than_persistence",
                "normalized_rates_per_hour": {"envelope": 0.02, "hall": 0.01},
                "holdout_mae_c": 0.04,
                "persistence_baseline_mae_c": 0.08,
                "coefficient_uncertainty": {"envelope": {"stability": "relatively_stable"}},
                "temporal_coefficient_stability": {"envelope": {"valid_windows": 3}},
            },
            "hall": {"status": "insufficient_clean_intervals"},
        }
    }
    energy = {
        "meters": {
            "gas_consumption": {"measured_energy_kwh": 2.4},
            "ac_energy": {"measured_energy_kwh": None},
        }
    }
    result = MODULE.build_multizone_physical_calibration_readiness(
        samples,
        [
            {"id": "living", "volume_m3": 40, "air_changes_per_hour": 0.5},
            {"id": "hall", "volume_m3": 20, "air_changes_per_hour": 0.4},
        ],
        response,
        energy,
        {"status": "available"},
    )
    assert result["rooms_with_normalized_response_fit"] == 1
    assert result["system_energy_meters_with_verified_deltas"] == ["gas_consumption"]
    assert result["absolute_room_parameters_identified"] is False
    assert result["physical_simulation_ready"] is False
    assert "no_water_flow_measurement_for_water_side_output" in result["blockers"]
    assert "no_validated_room_level_heat_allocation" in result["blockers"]
    assert result["rooms"]["living"]["effective_room_capacity_wh_per_k"] is None


def test_multizone_physical_readiness_withholds_without_any_response_fit_or_energy():
    result = MODULE.build_multizone_physical_calibration_readiness(
        [],
        [{"id": "bedroom"}],
        {"rooms": {}},
        {"meters": {}},
    )
    assert result["status"] == "absolute_parameters_withheld_insufficient_response_evidence"
    assert result["rooms_with_normalized_response_fit"] == 0
    assert result["system_energy_meters_with_verified_deltas"] == []
    assert result["physical_simulation_ready"] is False


def test_multizone_readiness_does_not_mistake_pump_power_or_water_delta_t_for_heat_output():
    samples = [{
        "timestamp": "2026-11-14T12:00:00+00:00",
        "channels": {
            "pump:ground_floor:power": 35.0,
            "water_supply_temperature": 35.0,
            "water_return_temperature": 29.0,
        },
        "rooms": {
            "living": {"temperature": 20.0, "demand": "off", "window": "off"},
        },
    }]
    result = MODULE.build_multizone_physical_calibration_readiness(
        samples,
        [{"id": "living", "volume_m3": 100, "air_changes_per_hour": 0.5}],
    )
    assert result["water_flow_measurement_channels"] == []
    assert result["independent_room_heat_output_channels"] == []
    assert "no_water_flow_measurement_for_water_side_output" in result["blockers"]
    assert "no_independent_room_or_zone_heat_output_measurement" in result["blockers"]
    assert result["absolute_room_parameters_identified"] is False
