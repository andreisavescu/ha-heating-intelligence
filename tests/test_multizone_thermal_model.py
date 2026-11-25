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



def test_simulation_readiness_lists_unidentified_room_parameters_and_links():
    result = MODEL.build_multizone_observation_model(
        {
            "living": {"temperature": 21.0, "volume_m3": 100.0, "air_changes_per_hour": 0.5},
            "bedroom": {"temperature": 20.0, "volume_m3": 50.0, "air_changes_per_hour": 0.6},
        },
        weather={"outdoor_temperature": {"value": 5.0}},
    )
    readiness = result["simulation_readiness"]
    assert readiness["status"] == "withheld_missing_physical_parameters"
    assert "effective_thermal_capacity_missing" in readiness["blockers"]
    assert "interzone_conductances_not_identified" in readiness["blockers"]
    assert len(readiness["unidentified_interzone_pairs"]) == 1
    assert readiness["rooms"]["room:living"]["capacity_status"] == "air_only_capacity_only"
    assert readiness["rooms"]["room:living"]["effective_room_capacity_wh_per_k"] is None


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



def test_ventilation_conductance_is_available_without_outdoor_temperature():
    result = MODEL.build_multizone_observation_model(
        {
            "living": {
                "temperature": 21.0,
                "volume_m3": 100.0,
                "air_changes_per_hour": 0.5,
            }
        }
    )
    assert result["ventilation_paths"][0]["conductance_w_per_k"] == 16.75
    assert result["nodes"]["boundary:outdoors"]["temperature_c"] is None
    assert result["nodes"]["boundary:outdoors"]["status"] == "temperature_unavailable"


def test_house_loss_decomposition_requires_complete_ventilation_coverage():
    rooms = {
        "living": {"temperature": 21.0, "volume_m3": 100.0, "air_changes_per_hour": 0.5},
        "bedroom": {"temperature": 20.0},
    }
    result = MODEL.build_multizone_observation_model(
        rooms, profile={"house_heat_loss_coefficient_w_k": 100.0}
    )
    decomposition = result["house_heat_loss_decomposition"]
    assert decomposition["status"] == "withheld_incomplete_ventilation_coverage"
    assert decomposition["residual_envelope_heat_loss_coefficient_w_k"] is None


def test_house_loss_decomposition_subtracts_ventilation_only_with_full_coverage():
    rooms = {
        "living": {"temperature": 21.0, "volume_m3": 100.0, "air_changes_per_hour": 0.5},
        "bedroom": {"temperature": 20.0, "volume_m3": 50.0, "air_changes_per_hour": 0.8},
    }
    result = MODEL.build_multizone_observation_model(
        rooms, profile={"house_heat_loss_coefficient_w_k": 100.0}
    )
    decomposition = result["house_heat_loss_decomposition"]
    assert decomposition["status"] == "conditional_residual_available"
    assert decomposition["ventilation_heat_loss_coefficient_w_k"] == 30.15
    assert decomposition["residual_envelope_heat_loss_coefficient_w_k"] == 69.85


def test_house_loss_decomposition_withholds_nonphysical_negative_residual():
    result = MODEL.build_multizone_observation_model(
        {"living": {"volume_m3": 100.0, "air_changes_per_hour": 5.0}},
        profile={"house_heat_loss_coefficient_w_k": 100.0},
    )
    decomposition = result["house_heat_loss_decomposition"]
    assert decomposition["status"] == "withheld_ventilation_not_below_total"
    assert decomposition["residual_envelope_heat_loss_coefficient_w_k"] is None



def test_aggregate_capacity_budget_reports_non_air_remainder_without_room_allocation():
    result = MODEL.build_multizone_observation_model(
        {
            "living": {"temperature": 21.0, "volume_m3": 100.0},
            "bedroom": {"temperature": 20.0, "volume_m3": 50.0},
        },
        profile={
            "house_effective_rc_calibration": {
                "effective_building_thermal_capacity_wh_per_k": 10000.0
            }
        },
    )
    budget = result["aggregate_capacity_budget"]
    assert budget["status"] == "aggregate_non_air_capacity_available"
    assert budget["configured_air_only_capacity_wh_per_k"] == 50.25
    assert budget["implied_non_air_capacity_wh_per_k"] == 9949.75
    assert budget["room_capacity_allocation"] is None


def test_aggregate_capacity_budget_withholds_partial_volume_coverage():
    result = MODEL.build_multizone_observation_model(
        {
            "living": {"volume_m3": 100.0},
            "bedroom": {"temperature": 20.0},
        },
        profile={
            "effective_building_thermal_capacity_wh_per_k": 10000.0
        },
    )
    budget = result["aggregate_capacity_budget"]
    assert budget["status"] == "withheld_incomplete_room_volume_coverage"
    assert budget["implied_non_air_capacity_wh_per_k"] is None


def test_aggregate_capacity_budget_flags_capacity_below_air_only_minimum():
    result = MODEL.build_multizone_observation_model(
        {"living": {"volume_m3": 100.0}},
        profile={"effective_building_thermal_capacity_wh_per_k": 10.0},
    )
    budget = result["aggregate_capacity_budget"]
    assert budget["status"] == "inconsistent_effective_capacity_below_air_only"
    assert budget["implied_non_air_capacity_wh_per_k"] is None


def test_energy_accounting_balances_internal_gains_and_fixed_boundary_exchange():
    result = MODEL.simulate_thermal_step(
        {
            "room": {"temperature_c": 20.0, "capacity_wh_per_k": 1000.0, "gains_w": 100.0},
            "outdoors": {"fixed_temperature_c": 5.0},
        },
        [{"from": "room", "to": "outdoors", "conductance_w_per_k": 5.0}],
        1800,
    )
    balance = result["energy_balance"]
    assert balance["status"] == "balanced_within_numerical_precision"
    assert abs(balance["error_wh"]) < 0.01
    assert balance["applied_gains_wh"] == 50.0
    assert balance["boundary_heat_exchange_wh"] < 0


def test_solver_rejects_pathological_substep_count():
    try:
        MODEL.simulate_thermal_step(
            {
                "room": {"temperature_c": 20.0, "capacity_wh_per_k": 0.000001},
                "outside": {"fixed_temperature_c": 0.0},
            },
            [{"from": "room", "to": "outside", "conductance_w_per_k": 1000.0}],
            3600,
        )
    except ValueError as error:
        assert "maximum is 100000" in str(error)
    else:
        raise AssertionError("pathological integration step count must be rejected")


def test_room_outdoor_conductance_uses_configured_total_without_adding_surface_inventory():
    result = MODEL.build_multizone_observation_model(
        {
            "bathroom": {
                "name": "Bathroom",
                "floor": "upstairs",
                "temperature": 21.0,
                "envelope_conductance_w_per_k": 1.388,
            }
        },
        weather={"outdoor_temperature": {"value": 5.0}},
        envelope_audit={
            "rooms": {
                "bathroom": {
                    "conductance_by_boundary_w_per_k": {
                        "outdoor": 1.126,
                        "attic": None,
                    }
                }
            }
        },
    )
    path = result["room_envelope_paths"][0]
    assert path["conductance_w_per_k"] == 1.388
    diagnostic = result["envelope_conductance_reconciliation"]["bathroom"]
    assert diagnostic["configured_room_to_outdoors_w_per_k"] == 1.388
    assert diagnostic["calculated_surface_inventory_outdoor_w_per_k"] == 1.126
    assert diagnostic["difference_configured_minus_inventory_w_per_k"] == 0.262
    assert diagnostic["status"] == "legacy_configuration_fallback"
    assert diagnostic["model_source"] == "legacy_room_configuration"


def test_room_outdoor_conductance_reconciliation_is_unavailable_without_complete_inventory():
    result = MODEL.build_multizone_observation_model(
        {
            "bedroom": {
                "temperature": 20.0,
                "envelope_conductance_w_per_k": 4.5,
            }
        },
        envelope_audit={"rooms": {}},
    )
    diagnostic = result["envelope_conductance_reconciliation"]["bedroom"]
    assert diagnostic["status"] == "legacy_configuration_fallback"
    assert diagnostic["configured_room_to_outdoors_w_per_k"] == 4.5
    assert diagnostic["calculated_surface_inventory_outdoor_w_per_k"] is None


def test_room_surface_inventory_replaces_legacy_outdoor_conductance_and_includes_windows_once():
    result = MODEL.build_multizone_observation_model(
        {
            "bathroom": {
                "name": "Bathroom",
                "floor": "upstairs",
                "temperature": 21.0,
                "envelope_conductance_w_per_k": 2.5,
            }
        },
        envelope_audit={
            "rooms": {
                "bathroom": {
                    "conductance_by_boundary_w_per_k": {"outdoor": 1.388, "attic": None},
                    "opaque_surface_counts": {"outdoor": 1, "ground": 0, "attic": 0, "adjacent_heated": 0},
                }
            }
        },
    )
    path = result["room_envelope_paths"][0]
    assert path["conductance_w_per_k"] == 1.388
    assert path["source"] == "per_room_surface_inventory_including_windows"
    assert result["envelope_conductance_reconciliation"]["bathroom"]["status"] == "surface_inventory_authoritative"


def test_per_room_attic_surface_replaces_legacy_attic_share():
    result = MODEL.build_multizone_observation_model(
        {"bathroom": {"floor": "upstairs", "temperature": 21.0}},
        attic={"temperature": {"value": 10.0}},
        profile={"attic_floor_room_conductances_w_per_k": {"bathroom": 0.88}},
        envelope_audit={
            "rooms": {
                "bathroom": {
                    "conductance_by_boundary_w_per_k": {"outdoor": None, "attic": 0.74},
                    "opaque_surface_counts": {"outdoor": 0, "ground": 0, "attic": 1, "adjacent_heated": 0},
                }
            }
        },
    )
    path = next(item for item in result["attic_floor_paths"] if item["to"] == "room:bathroom")
    assert path["conductance_w_per_k"] == 0.74
    assert path["source"] == "per_room_surface_inventory"


def test_per_room_ground_surface_creates_separate_ground_boundary_path():
    result = MODEL.build_multizone_observation_model(
        {"living": {"floor": "ground_floor", "temperature": 21.0}},
        envelope_audit={
            "rooms": {
                "living": {
                    "conductance_by_boundary_w_per_k": {"outdoor": None, "ground": 10.0},
                    "opaque_surface_counts": {"outdoor": 0, "ground": 1, "attic": 0, "adjacent_heated": 0},
                }
            }
        },
    )
    assert result["ground_paths"][0]["conductance_w_per_k"] == 10.0
    assert result["ground_paths"][0]["to"] == "boundary:ground"
