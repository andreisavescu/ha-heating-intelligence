"""Tests for Stage 6 gain-aware residual diagnostics."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage6_gain_model",
    ROOT / "custom_components" / "heating_intelligence" / "stage6_gain_model.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sample(timestamp, temperature, demand=False, window=False, outdoor=10.0, solar=0.0):
    return {
        "timestamp": timestamp,
        "channels": {
            "outdoor_temperature": outdoor,
            "solar_radiation": solar,
            "solar_radiation_unit": "W/m2",
            "ac_power": 0.0,
        },
        "rooms": {
            "living": {
                "temperature": temperature,
                "demand": demand,
                "window": window,
            }
        },
    }


def test_stage6_estimates_net_residual_but_never_splits_or_feeds_gains():
    samples = [
        _sample("2026-01-01T00:00:00+00:00", 20.0),
        _sample("2026-01-01T00:30:00+00:00", 20.5),
        _sample("2026-01-01T01:00:00+00:00", 21.0),
    ]
    result = MODULE.build_stage6_gain_model(
        samples,
        [{"id": "living", "window_entity": "binary_sensor.living_window",
          "envelope_conductance_w_per_k": 10.0}],
        {"rooms": {"living": {"effective_room_capacity_wh_per_k": 100.0}}},
        minimum_intervals=2,
    )
    room = result["rooms"]["living"]
    assert room["eligible_intervals"] == 2
    assert room["median_net_unattributed_gain_w"] == 200.0
    assert room["solar_gain_w"] is None
    assert room["internal_gain_w"] is None
    assert room["solver_gain_input_w"] is None
    assert result["solver_input_enabled"] is False


def test_stage6_excludes_heating_demand_and_open_windows():
    samples = [
        _sample("2026-01-01T00:00:00+00:00", 20.0),
        _sample("2026-01-01T00:30:00+00:00", 20.5, demand=True),
        _sample("2026-01-01T01:00:00+00:00", 21.0, window=True),
    ]
    result = MODULE.build_stage6_gain_model(
        samples,
        [{"id": "living", "window_entity": "binary_sensor.living_window",
          "envelope_conductance_w_per_k": 10.0}],
        {"rooms": {"living": {"effective_room_capacity_wh_per_k": 100.0}}},
        minimum_intervals=1,
    )
    room = result["rooms"]["living"]
    assert room["eligible_intervals"] == 0
    assert room["excluded_intervals"]["demand_not_confirmed_off"] == 1
    assert room["excluded_intervals"]["window_not_confirmed_closed"] == 1


def test_stage6_requires_known_ac_when_ac_sensor_is_configured():
    samples = [
        _sample("2026-01-01T00:00:00+00:00", 20.0),
        _sample("2026-01-01T00:30:00+00:00", 20.5),
    ]
    samples[0]["channels"].pop("ac_power")
    samples[1]["channels"].pop("ac_power")
    result = MODULE.build_stage6_gain_model(
        samples,
        [{"id": "living", "envelope_conductance_w_per_k": 10.0}],
        {"rooms": {"living": {"effective_room_capacity_wh_per_k": 100.0}}},
        minimum_intervals=1,
        require_ac_power=True,
    )
    assert result["rooms"]["living"]["eligible_intervals"] == 0
    assert result["rooms"]["living"]["excluded_intervals"]["ac_active_or_unknown"] == 1


def test_stage6_withholds_when_absolute_room_parameters_are_missing():
    result = MODULE.build_stage6_gain_model(
        [_sample("2026-01-01T00:00:00+00:00", 20.0)],
        [{"id": "living", "envelope_conductance_w_per_k": 10.0}],
        {"rooms": {}},
    )
    assert result["rooms"]["living"]["status"] == "insufficient_gated_coasting_intervals"
    assert result["rooms"]["living"]["excluded_intervals"]["missing_absolute_parameters"] == 1
