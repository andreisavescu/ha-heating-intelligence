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
    assert result["excluded_negative_intervals"] == 2
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
