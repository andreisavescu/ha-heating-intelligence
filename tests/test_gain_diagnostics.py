"""Tests for diagnostic-only solar and internal-gain evidence."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "gain_diagnostics",
    ROOT / "custom_components" / "heating_intelligence" / "gain_diagnostics.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_gain_diagnostics_never_invents_watts_or_enables_solver_inputs():
    result = MODULE.build_gain_diagnostics(
        [],
        [{"id": "living", "co2_entity": "sensor.living_co2"}],
        {
            "window_area_north_m2": 2.01,
            "window_area_east_m2": 10.36,
            "window_area_south_m2": 5.70,
            "window_area_west_m2": 8.29,
            "window_glazing": "triple",
            "shading": "partial",
        },
    )
    assert result["solar"]["configured_window_area_m2"] == 26.36
    assert result["solar"]["solar_gain_w"] is None
    assert result["internal_gains"]["rooms"]["living"]["internal_gain_w"] is None
    assert result["solver_input_enabled"] is False
    assert result["room_learning_adjustment_enabled"] is False


def test_co2_coverage_is_room_specific_and_does_not_spread_to_other_rooms():
    result = MODULE.build_gain_diagnostics(
        [
            {"rooms": {"bedroom": {"co2": 700, "humidity": 55, "temperature": 20}}},
            {"rooms": {"bedroom": {"co2": 900, "humidity": 58, "temperature": 20.5}}},
        ],
        [
            {"id": "bedroom", "co2_entity": "sensor.bedroom_co2", "humidity_entity": "sensor.bedroom_humidity"},
            {"id": "living", "humidity_entity": "sensor.living_humidity"},
        ],
    )
    rooms = result["internal_gains"]["rooms"]
    assert rooms["bedroom"]["co2_delta_ppm_over_history"] == 200
    assert rooms["bedroom"]["confidence"] == "medium"
    assert rooms["living"]["co2_sensor_configured"] is False
    assert rooms["living"]["internal_gain_w"] is None


def test_missing_window_geometry_is_explicitly_withheld():
    result = MODULE.build_gain_diagnostics([], [{"id": "room"}], {})
    assert result["solar"]["status"] == "awaiting_window_geometry"
    assert result["solar"]["configured_window_area_m2"] is None


def test_invalid_values_are_ignored():
    result = MODULE.build_gain_diagnostics(
        [{"rooms": {"room": {"co2": "unknown", "humidity": 150, "temperature": float("nan")}}}],
        [{"id": "room", "co2_entity": "sensor.room_co2", "humidity_entity": "sensor.room_humidity"}],
    )
    room = result["internal_gains"]["rooms"]["room"]
    assert room["co2_samples"] == 0
    assert room["humidity_samples"] == 0
    assert room["temperature_samples"] == 0
