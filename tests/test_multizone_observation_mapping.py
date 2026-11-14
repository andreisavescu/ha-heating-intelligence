"""Tests for Home Assistant observation mapping into the multi-zone model."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "multizone_thermal_model",
    ROOT / "custom_components" / "heating_intelligence" / "multizone_thermal_model.py",
)
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


def test_maps_room_air_quality_and_weather_without_inventing_capacity():
    result = MODEL.build_multizone_observation_model(
        {
            "living": {
                "name": "Living room",
                "floor": "ground_floor",
                "temperature": 21.4,
                "temperature_entity": "sensor.living_temperature",
                "demand": True,
                "demand_state_known": True,
                "window_open": False,
                "window_state_known": True,
                "area_m2": 35.0,
                "air_changes_per_hour": 0.45,
                "co2_ppm": 612,
                "humidity": 48.0,
            }
        },
        {"outdoor_temperature": {"value": 7.0, "entity_id": "sensor.outdoor"}},
    )
    room = result["nodes"]["room:living"]
    assert room["temperature_c"] == 21.4
    assert room["co2_ppm"] == 612
    assert room["humidity_percent"] == 48.0
    assert room["configured_ach"] == 0.45
    assert room["thermal_capacity_wh_per_k"] is None
    assert result["simulation_status"] == "withheld_missing_physical_parameters"
    assert result["coverage"]["room_temperature_coverage_percent"] == 100


def test_attic_roof_path_requires_explicit_u_value_and_area():
    result = MODEL.build_multizone_observation_model(
        {},
        {"outdoor_temperature": {"value": 5}},
        {"attic_temperature": 10, "temperature_entity": "sensor.attic"},
        {"roof_u_value": 0.18, "roof_area_m2": 120, "attic_floor_u_value": 0.25, "attic_floor_area_m2": 90},
    )
    assert result["nodes"]["attic:air"]["temperature_c"] == 10
    assert result["configured_connections"] == [{
        "from": "attic:air",
        "to": "boundary:outdoors",
        "path": "roof",
        "conductance_w_per_k": 21.6,
        "source": "configured_u_value_times_area",
        "status": "configured",
    }]
    assert result["pending_paths"][0]["status"] == "awaiting_upstairs_zone_coupling"


def test_missing_sensor_values_are_reported_not_replaced_with_defaults():
    result = MODEL.build_multizone_observation_model(
        {"bedroom": {"name": "Bedroom", "temperature": None, "co2_ppm": None, "humidity": None}},
        {"outdoor_temperature": {"value": None}},
    )
    assert result["nodes"]["room:bedroom"]["temperature_c"] is None
    assert result["coverage"]["room_temperature_coverage_percent"] == 0
    assert result["simulation_status"] == "withheld_missing_physical_parameters"


def test_maps_ufh_floor_and_heating_plant_without_inventing_slab_output():
    result = MODEL.build_multizone_observation_model(
        {
            "living": {"name": "Living", "floor": "ground_floor", "temperature": 21.0},
            "bedroom": {"name": "Bedroom", "floor": "upstairs", "temperature": 20.0},
        },
        {"outdoor_temperature": {"value": 6.0}},
        {},
        {"ufh_screed_cm": 6.0, "boiler_model": "Configured boiler"},
        {
            "water_temperature": 32.0,
            "water_return_temperature": 27.0,
            "boiler_context": {"burner_state": "on", "modulation": 35},
            "pumps": {
                "ground_floor": {"running": True, "power": 18.0, "stage": "pump_running"},
                "upstairs": {"running": False, "power": 0.0, "stage": "idle"},
            },
        },
    )
    assert result["floor_nodes"] == {
        "ground_floor": "floor:ground_floor",
        "upstairs": "floor:upstairs",
    }
    slab = result["nodes"]["floor:ground_floor"]
    assert slab["adjacent_room_average_temperature_c"] == 21.0
    assert slab["slab_temperature_c"] is None
    assert slab["status"] == "awaiting_slab_sensor_or_identifiable_capacity"
    plant = result["plant"]
    assert plant["supply_water_temperature_c"] == 32.0
    assert plant["return_water_temperature_c"] == 27.0
    assert plant["modulation_percent"] == 35
    assert plant["burner_on"] is None
    assert plant["thermal_output_w"] is None
    assert plant["pumps"]["ground_floor"]["running"] is True


def test_empty_boiler_context_does_not_claim_live_plant_telemetry():
    result = MODEL.build_multizone_observation_model(
        {"room": {"floor": "ground_floor", "temperature": 20}},
        {},
        {},
        {},
        {"boiler_context": {"burner_state": None, "burner_on": None, "modulation_percent": None}},
    )
    assert result["plant"]["status"] == "plant_sensors_unavailable"


def test_ventilation_conductance_requires_explicit_room_volume_and_ach():
    result = MODEL.build_multizone_observation_model(
        {
            "measured": {
                "name": "Measured room",
                "floor": "ground_floor",
                "temperature": 20.0,
                "volume_m3": 100.0,
                "air_changes_per_hour": 0.5,
            },
            "volume_missing": {
                "name": "No volume",
                "floor": "upstairs",
                "temperature": 20.0,
                "air_changes_per_hour": 0.5,
            },
        },
        {"outdoor_temperature": {"value": 5.0}},
    )
    assert len(result["ventilation_paths"]) == 1
    path = result["ventilation_paths"][0]
    assert path["from"] == "room:measured"
    assert path["to"] == "boundary:outdoors"
    assert path["conductance_w_per_k"] == 16.75
    assert path["source"] == "configured_room_volume_and_ach"
    assert result["coverage"]["rooms_with_volume_and_ach"] == 1


def test_solar_and_internal_gains_remain_context_without_calibrated_gain_watts():
    result = MODEL.build_multizone_observation_model(
        {},
        {"outdoor_temperature": {"value": 4}, "solar_radiation": {"value": 420}},
        {},
        {
            "window_area_south_m2": 12.0,
            "window_glazing": "double",
            "main_orientation": "south",
            "shading": "external blinds",
            "internal_gains": "normal",
        },
    )
    gains = result["gain_context"]
    assert gains["solar_radiation_w_m2"] == 420
    assert gains["window_area_by_orientation_m2"] == {"south": 12.0}
    assert gains["solar_gain_w"] is None
    assert gains["internal_gain_w"] is None
    assert gains["status"] == "context_only_missing_solar_transmittance_and_orientation_model"



def test_room_response_is_exposed_as_observation_not_physical_rc_parameter():
    result = MODEL.build_multizone_observation_model(
        {
            "living": {
                "name": "Living room",
                "temperature": 21.0,
                "thermal_model": {
                    "heating": {
                        "status": "available",
                        "rate_c_per_hour": 0.42,
                        "confidence": 76,
                        "valid_intervals": 24,
                    },
                    "coasting": {
                        "status": "available",
                        "rate_c_per_hour": -0.18,
                        "confidence": 83,
                        "valid_intervals": 31,
                    },
                },
            }
        },
    )
    evidence = result["nodes"]["room:living"]["thermal_response_evidence"]
    assert evidence["status"] == "observed_response_available"
    assert evidence["heating_rate_c_per_hour"] == 0.42
    assert evidence["coasting_rate_c_per_hour"] == -0.18
    assert evidence["heating_confidence"] == 76
    assert evidence["coasting_valid_intervals"] == 31
    assert result["nodes"]["room:living"]["thermal_capacity_wh_per_k"] is None
    assert "cannot identify room heat capacity" in evidence["note"]


def test_room_response_without_learning_remains_unavailable():
    result = MODEL.build_multizone_observation_model(
        {"bedroom": {"temperature": 20.0}},
    )
    evidence = result["nodes"]["room:bedroom"]["thermal_response_evidence"]
    assert evidence["status"] == "not_available"
    assert evidence["heating_rate_c_per_hour"] is None
    assert evidence["coasting_rate_c_per_hour"] is None
