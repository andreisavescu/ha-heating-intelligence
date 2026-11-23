"""Tests for offline thermal experiment data analysis."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
PATH = ROOT / "custom_components" / "heating_intelligence" / "thermal_experiment.py"
SPEC = importlib.util.spec_from_file_location("thermal_experiment", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def row(entity, stamp, state):
    return {"entity_id": entity, "last_changed": stamp, "state": state}


def test_power_integration_uses_trapezoids_and_rejects_negative_and_long_gaps():
    rows = [
        row("sensor.ac", "2026-11-15T10:00:00Z", "100"),
        row("sensor.ac", "2026-11-15T10:01:00Z", "200"),
        row("sensor.ac", "2026-11-15T10:02:00Z", "-5"),
        row("sensor.ac", "2026-11-15T10:20:00Z", "300"),
        row("sensor.ac", "2026-11-15T10:21:00Z", "300"),
    ]
    result = MODULE.integrate_power_samples(rows, "sensor.ac")
    assert result["valid_interval_count"] == 2
    assert result["energy_wh"] == 7.5
    assert result["excluded_negative_intervals"] == 1
    assert result["excluded_long_or_invalid_gaps"] == 1


def test_power_integration_refuses_unknown_units():
    result = MODULE.integrate_power_samples(
        [row("sensor.ac", "2026-11-15T10:00:00Z", "100")],
        "sensor.ac", power_unit="kW",
    )
    assert result["status"] == "unsupported_power_unit"
    assert result["energy_wh"] is None


def test_temperature_response_uses_nearest_observed_endpoints_without_interpolation():
    rows = [
        row("room", "2026-11-15T10:00:00Z", "22.0"),
        row("room", "2026-11-15T11:00:00Z", "22.2"),
    ]
    result = MODULE.temperature_response(
        rows, "room", "2026-11-15T10:02:00Z", "2026-11-15T10:58:00Z"
    )
    assert result["status"] == "available"
    assert result["temperature_change_c"] == 0.2
    assert result["rate_c_per_hour"] == 0.2
    assert result["method"] == "nearest_observed_endpoints_no_interpolation"


def test_temperature_response_withholds_missing_endpoint_coverage():
    rows = [row("room", "2026-11-15T10:00:00Z", "22.0")]
    result = MODULE.temperature_response(
        rows, "room", "2026-11-15T10:00:00Z", "2026-11-15T12:00:00Z"
    )
    assert result["status"] == "insufficient_temperature_samples"


def test_climate_modes_are_reconstructed_as_utc_intervals():
    rows = [
        row("climate", "2026-11-15T10:00:00Z", "off"),
        row("climate", "2026-11-15T12:16:00Z", "heat"),
        row("climate", "2026-11-15T18:06:00Z", "off"),
    ]
    result = MODULE.climate_mode_intervals(rows, "climate")
    assert [item["mode"] for item in result] == ["off", "heat", "off"]
    assert result[0]["end"] == "2026-11-15T12:16:00+00:00"
    assert result[-1]["end"] is None
    assert result[-1]["end_is_open"] is True


def test_experiment_summary_marks_combined_ac_input_not_room_heat():
    rows = [
        row("bedroom", "2026-11-15T10:00:00Z", "22"),
        row("bedroom", "2026-11-15T11:00:00Z", "22.2"),
        row("ac", "2026-11-15T10:00:00Z", "100"),
        row("ac", "2026-11-15T10:01:00Z", "200"),
        row("climate", "2026-11-15T10:00:00Z", "off"),
    ]
    result = MODULE.build_experiment_summary(
        rows, room_temperature_entities={"bedroom": "bedroom"},
        ac_power_entity="ac", climate_entity="climate",
    )
    assert result["combined_ac_electrical_input"]["energy_wh"] == 2.5
    assert "not_room_level_heat_output" in result["combined_ac_electrical_input"]["scope"]
    assert any("not room-level thermal output" in item for item in result["interpretation_limits"])


def test_experiment_summary_keeps_manual_events_separate_and_reports_room_periods():
    rows = [
        row("bedroom", "2026-11-15T10:00:00Z", "22.0"),
        row("bedroom", "2026-11-15T11:00:00Z", "22.4"),
    ]
    result = MODULE.build_experiment_summary(
        rows, room_temperature_entities={"bedroom": "bedroom"},
        periods={"ac_off": {
            "start": "2026-11-15T10:00:00Z",
            "end": "2026-11-15T11:00:00Z",
        }},
        user_supplied_event_annotations=[{
            "event": "upstairs_ufh_pump_stopped",
            "time": "2026-11-15T10:00:00+02:00",
            "source": "user_report",
        }],
    )
    response = result["room_temperature_responses"]["ac_off"]["bedroom"]
    assert response["temperature_change_c"] == 0.4
    assert response["rate_c_per_hour"] == 0.4
    assert result["user_supplied_event_annotations"][0]["source"] == "user_report"
    assert "upstairs_ufh_pump_stopped" not in result["entities"]


def test_free_cooling_identifies_only_h_over_c_ratio():
    rows = []
    room = 25.0
    outside = 5.0
    for index in range(61):
        stamp = f"2026-11-15T{index // 60:02d}:{index % 60:02d}:00Z"
        rows.append(row("outside", stamp, str(outside)))
        rows.append(row("room", stamp, str(room)))
        rows.append(row("heating", stamp, "off"))
        room += -0.03 * (room - outside)
    result = MODULE.identify_free_cooling_h_over_c(
        rows, "room", "outside", heating_entity="heating", min_intervals=30,
        max_gap_seconds=90,
    )
    assert result["status"] == "candidate_estimate"
    assert result["identified_parameter"] == "H_over_C_per_hour"
    assert abs(result["estimate"] - 0.03) < 0.002
    assert any("not H or C separately" in item for item in result["limitations"])


def test_historical_replay_and_holdout_validation_do_not_reset_to_observations():
    rows = []
    room = 20.0
    outside = 5.0
    from datetime import datetime, timedelta, timezone
    start = datetime(2026, 11, 15, tzinfo=timezone.utc)
    for index in range(13):
        stamp = start + timedelta(minutes=5 * index)
        rows.append(row("outside", stamp.isoformat(), str(outside)))
        rows.append(row("room", stamp.isoformat(), f"{room:.8f}"))
        room += (10.0 * (outside - room)) * (5.0 / 60.0) / 1000.0
    simulation = MODULE.simulate_historical_room(
        rows, room_temperature_entity="room", outdoor_temperature_entity="outside",
        initial_temperature_c=20.0, capacity_wh_per_k=1000.0,
        conductance_w_per_k=10.0, max_gap_seconds=600,
    )
    assert simulation["status"] == "available"
    assert simulation["prediction_count"] == 13
    score = MODULE.validate_historical_simulation(simulation)
    assert score["status"] == "scored"
    assert score["metrics"]["rmse_c"] < 0.001
    assert score["calibration_data_reused"] is False


def test_validation_can_score_only_a_separate_holdout_window():
    simulation = {
        "status": "available",
        "predictions": [
            {"timestamp": "2026-11-15T00:00:00Z", "observed_temperature_c": 20, "predicted_temperature_c": 30},
            {"timestamp": "2026-11-15T01:00:00Z", "observed_temperature_c": 20, "predicted_temperature_c": 21},
            {"timestamp": "2026-11-15T02:00:00Z", "observed_temperature_c": 20, "predicted_temperature_c": 22},
        ],
    }
    score = MODULE.validate_historical_simulation(
        simulation, validation_start="2026-11-15T01:00:00Z",
        validation_end="2026-11-15T02:00:00Z",
    )
    assert score["sample_count"] == 2
    assert score["metrics"]["mae_c"] == 1.5
