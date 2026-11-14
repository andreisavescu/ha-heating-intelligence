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



def test_room_volume_provides_air_only_capacity_and_ventilation_conductance():
    result = MODEL.build_multizone_observation_model(
        {
            "living": {
                "name": "Living room",
                "temperature": 21.0,
                "volume_m3": 100.0,
                "air_changes_per_hour": 0.5,
            },
            "bedroom": {
                "name": "Bedroom",
                "temperature": 20.0,
                "volume_m3": 50.0,
                "air_changes_per_hour": 0.8,
            },
        },
        weather={"outdoor_temperature": {"value": 5.0}},
    )
    living = result["nodes"]["room:living"]
    assert living["air_thermal_capacity_wh_per_k"] == 33.5
    assert len(result["ventilation_paths"]) == 2
    living_path = next(
        item for item in result["ventilation_paths"] if item["from"] == "room:living"
    )
    assert living_path["conductance_w_per_k"] == 16.75
    assert living_path["sensitivity_ach_minus_10_percent_w_per_k"] == 15.075
    assert living_path["sensitivity_ach_plus_10_percent_w_per_k"] == 18.425
    assert "not a measured uncertainty" in living_path["sensitivity_note"]
    inventory = result["air_only_thermal_inventory"]
    assert inventory["status"] == "available"
    assert inventory["rooms_with_volume"] == 2
    assert inventory["total_configured_room_volume_m3"] == 150.0
    assert inventory["air_only_capacity_wh_per_k"] == 50.25
    assert inventory["known_ventilation_conductance_w_per_k"] == 30.15
    assert "excludes walls" in inventory["interpretation"]


def test_air_only_capacity_inventory_marks_partial_volume_coverage():
    result = MODEL.build_multizone_observation_model(
        {
            "living": {"temperature": 21.0, "volume_m3": 100.0},
            "bedroom": {"temperature": 20.0},
        },
        weather={"outdoor_temperature": {"value": 5.0}},
    )
    inventory = result["air_only_thermal_inventory"]
    assert inventory["status"] == "partial_configured_volume"
    assert inventory["rooms_with_volume"] == 1
    assert inventory["volume_coverage_percent"] == 50
    assert inventory["air_only_capacity_wh_per_k"] == 33.5
