"""Tests for the physics-based multi-zone thermal network core."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "multizone_thermal_model",
    ROOT / "custom_components" / "heating_intelligence" / "multizone_thermal_model.py",
)
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


def test_two_connected_zones_exchange_heat_and_approach_equilibrium():
    result = MODEL.simulate_thermal_step(
        {
            "living": {"temperature_c": 22, "capacity_wh_per_k": 500},
            "hall": {"temperature_c": 16, "capacity_wh_per_k": 500},
        },
        [{"from": "living", "to": "hall", "conductance_w_per_k": 20}],
        3600,
    )
    temps = result["temperatures_c"]
    assert temps["living"] < 22
    assert temps["hall"] > 16
    assert temps["living"] > temps["hall"]
    assert result["connections"][0]["heat_flow_w"] > 0


def test_fixed_outdoor_boundary_cools_a_room():
    result = MODEL.simulate_thermal_step(
        {
            "room": {"temperature_c": 21, "capacity_wh_per_k": 1000},
            "outdoors": {"fixed_temperature_c": 5},
        },
        [{"from": "room", "to": "outdoors", "conductance_w_per_k": 10}],
        3600,
    )
    assert result["temperatures_c"]["room"] < 21
    assert result["temperatures_c"]["outdoors"] == 5
    assert result["connections"][0]["heat_flow_w"] > 0


def test_internal_and_solar_gains_raise_node_temperature():
    result = MODEL.simulate_thermal_step(
        {"room": {"temperature_c": 20, "capacity_wh_per_k": 1000, "gains_w": 500}},
        [],
        3600,
    )
    assert result["temperatures_c"]["room"] == 20.5


def test_connected_network_conserves_energy_without_gains_or_boundaries():
    result = MODEL.simulate_thermal_step(
        {
            "a": {"temperature_c": 24, "capacity_wh_per_k": 300},
            "b": {"temperature_c": 18, "capacity_wh_per_k": 700},
        },
        [{"from": "a", "to": "b", "conductance_w_per_k": 15}],
        1800,
    )
    final = result["temperatures_c"]
    initial_energy = 300 * 24 + 700 * 18
    final_energy = 300 * final["a"] + 700 * final["b"]
    assert abs(final_energy - initial_energy) < 0.1


def test_rejects_invalid_capacity_and_connection():
    try:
        MODEL.simulate_thermal_step(
            {"room": {"temperature_c": 20, "capacity_wh_per_k": 0}}, [], 60
        )
    except ValueError:
        pass
    else:
        raise AssertionError("zero thermal capacity must be rejected")

    try:
        MODEL.simulate_thermal_step(
            {"room": {"temperature_c": 20, "capacity_wh_per_k": 100}},
            [{"from": "room", "to": "missing", "conductance_w_per_k": 1}],
            60,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unknown connection endpoint must be rejected")
