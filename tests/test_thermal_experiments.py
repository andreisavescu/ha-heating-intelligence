"""Tests for physical validation, forecasting and isolated what-if scenarios."""
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).parents[1]
PACKAGE = "heating_intelligence_test_package"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "custom_components" / "heating_intelligence")]
sys.modules.setdefault(PACKAGE, package)

def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{name}", ROOT / "custom_components" / "heating_intelligence" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

_load("multizone_thermal_model")
MODULE = _load("thermal_experiments")

def test_validation_withholds_without_accepted_absolute_parameters():
    result = MODULE.build_physical_model_validation(
        [], [{"id": "living", "envelope_conductance_w_per_k": 7.4}], {"rooms": {}})
    assert result["status"] == "awaiting_parameters_and_history"
    assert result["rooms"]["living"]["mae_c"] is None

def test_bathroom_temperatures_are_scenario_assumptions_only():
    rooms = [
        {"id": "baie_matr", "envelope_conductance_w_per_k": 2.5},
        {"id": "baie_parter", "envelope_conductance_w_per_k": 1.2},
    ]
    params = {"rooms": {
        "baie_matr": {"effective_room_capacity_wh_per_k": 1200},
        "baie_parter": {"effective_room_capacity_wh_per_k": 900},
    }}
    forecast = MODULE.build_predictive_forecast({}, rooms, params, 5.0)
    assert forecast["rooms"]["baie_matr"]["initial_temperature_c"] == 22.0
    assert forecast["rooms"]["baie_matr"]["initial_temperature_source"] == "fixed_scenario_assumption"
    assert forecast["rooms"]["baie_parter"]["initial_temperature_c"] == 20.5
    scenarios = MODULE.build_what_if_simulator({}, rooms, params, 5.0)
    assert scenarios["isolation"].startswith("read_only")
    assert scenarios["scenarios"]["baseline"]["rooms"]["baie_matr"]["initial_temperature_source"] == "fixed_scenario_assumption"

def test_validation_scores_only_measured_room_temperatures():
    samples = []
    for index, temp in enumerate((21.0, 20.9, 20.8, 20.7, 20.6)):
        samples.append({
            "timestamp": f"2026-11-01T{index:02d}:00:00+00:00",
            "channels": {"outdoor_temperature": 5.0},
            "rooms": {"living": {"temperature": temp}},
        })
    result = MODULE.build_physical_model_validation(
        samples, [{"id": "living", "envelope_conductance_w_per_k": 5.0}],
        {"rooms": {"living": {"effective_room_capacity_wh_per_k": 1000}}})
    assert result["rooms"]["living"]["valid_intervals"] == 4
    assert result["rooms"]["living"]["status"] == "scored_envelope_only_one_step_validation"


def test_simulation_audit_reports_room_readiness_and_keeps_validation_separate():
    rooms = [
        {"id": "living", "envelope_conductance_w_per_k": 7.4},
        {"id": "baie_matr", "envelope_conductance_w_per_k": 2.5},
    ]
    audit = MODULE.build_simulation_audit(
        rooms,
        {"living": {"temperature": 21.0}},
        {"rooms": {"living": {"effective_room_capacity_wh_per_k": 1200}}},
        {"rooms": {"living": {"valid_intervals": 0, "mae_c": None}}},
        {"outdoor_temperature_c": 5.0, "rooms": {
            "living": {"status": "forecast_available_experimental"},
            "baie_matr": {"status": "withheld_missing_temperature_weather_or_accepted_parameters"},
        }},
        {"status": "aligned_dataset_available", "aligned_sample_count": 100,
         "core_eligible_sample_count": 12, "complete_room_state_sample_count": 0},
        {"rooms": {}},
    )
    assert audit["rooms_configured"] == 2
    assert audit["rooms_simulated"] == 1
    assert audit["rooms_validated"] == 0
    assert audit["rooms"]["living"]["status"] == "experimental_simulation_available"
    assert audit["rooms"]["living"]["validation_intervals"] == 0
    assert "no_chronological_validation_intervals" in audit["rooms"]["living"]["blockers"]
    assert audit["rooms"]["baie_matr"]["temperature_source"] == "fixed_scenario_assumption_only"
    assert audit["complete_room_state_samples"] == 0



def test_simulation_audit_counts_native_event_dataset_samples():
    audit = MODULE.build_simulation_audit(
        [],
        {},
        {"rooms": {}},
        {"rooms": {}},
        {"rooms": {}, "outdoor_temperature_c": None},
        {
            "status": "event_dataset_available",
            "event_sample_count": 29132,
        },
        {"rooms": {}},
    )
    assert audit["dataset_status"] == "event_dataset_available"
    assert audit["dataset_samples"] == 29132


def test_coupled_forecast_uses_inter_room_and_ventilation_paths_and_reports_unobserved_rooms():
    rooms = [
        {"id": "living", "floor": "ground_floor", "envelope_conductance_w_per_k": 5.0},
        {"id": "birou", "floor": "ground_floor", "envelope_conductance_w_per_k": 4.0},
        {"id": "baie_matr", "floor": "upstairs", "envelope_conductance_w_per_k": 2.0},
    ]
    params = {"rooms": {
        "living": {"effective_room_capacity_wh_per_k": 1200},
        "birou": {"effective_room_capacity_wh_per_k": 900},
        "baie_matr": {"effective_room_capacity_wh_per_k": 700},
    }}
    network = {
        "configured_connections": [
            {"from": "room:living", "to": "room:birou",
             "conductance_w_per_k": 8.0, "path": "inter_room"},
            {"from": "room:living", "to": "boundary:outdoors",
             "conductance_w_per_k": 5.0, "path": "room_envelope"},
            {"from": "room:birou", "to": "boundary:outdoors",
             "conductance_w_per_k": 4.0, "path": "room_envelope"},
        ],
        "ventilation_paths": [
            {"from": "room:living", "conductance_w_per_k": 1.0},
        ],
    }
    result = MODULE.build_coupled_network_forecast(
        {"living": {"temperature": 21.0}, "birou": {"temperature": 19.0}},
        rooms, params, 5.0, network, {"living": 100.0}, horizons_hours=(1,),
    )
    assert result["rooms_simulated"] == 2
    assert result["method"] == "coupled_multi_zone_rc_with_calibrated_room_heat_inputs"
    assert result["rooms"]["living"]["heat_input_w"] == 100.0
    assert result["rooms"]["baie_matr"]["status"] == "withheld_missing_live_room_temperature"
    assert result["network_paths_used"] == 4
    assert result["energy_balance_checks"][0]["status"] == "balanced_within_numerical_precision"


def test_coupled_validation_scores_measured_rooms_and_reports_missing_sensor_reason():
    rooms = [
        {"id": "living", "envelope_conductance_w_per_k": 5.0},
        {"id": "baie_matr", "envelope_conductance_w_per_k": 2.0},
    ]
    params = {"rooms": {
        "living": {"effective_room_capacity_wh_per_k": 1000},
        "baie_matr": {"effective_room_capacity_wh_per_k": 800},
    }}
    samples = [
        {"timestamp": "2026-11-01T00:00:00+00:00",
         "channels": {"outdoor_temperature": 5.0},
         "rooms": {"living": {"temperature": 21.0}}},
        {"timestamp": "2026-11-01T01:00:00+00:00",
         "channels": {"outdoor_temperature": 5.0},
         "rooms": {"living": {"temperature": 20.9}}},
    ]
    network = {"configured_connections": [
        {"from": "room:living", "to": "boundary:outdoors",
         "conductance_w_per_k": 5.0, "path": "room_envelope"},
    ]}
    result = MODULE.build_coupled_network_validation(samples, rooms, params, network)
    assert result["rooms_scored"] == 1
    assert result["rooms"]["living"]["status"] == "scored_coupled_multizone_one_step"
    assert result["rooms"]["baie_matr"]["valid_intervals"] == 0
    assert result["rooms"]["baie_matr"]["rejected_intervals"]["room_temperature_unobserved"] == 1
