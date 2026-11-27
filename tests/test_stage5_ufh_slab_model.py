"""Tests for Stage 5 UFH slab capacity and inferred manifold return temperatures."""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[1]
PACKAGE = "_heating_intelligence_stage5_test"
package = types.ModuleType(PACKAGE)
package.__path__ = []
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.stage5_ufh_slab_model",
    ROOT / "custom_components" / "heating_intelligence" / "stage5_ufh_slab_model.py",
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
MODULE = module


def test_stage5_builds_separate_floor_screed_nodes_and_estimates_manifold_returns():
    rooms = [
        {"id": "living", "floor": "ground_floor", "area_m2": 30, "ufh_screed_thickness_cm": 6},
        {"id": "bedroom", "floor": "upstairs", "area_m2": 12},
    ]
    circuits = [
        {"id": "circuit_01", "floor": "ground_floor", "name": "Living loop", "pipe_length_m": 90, "served_rooms": ["living"]},
        {"id": "circuit_02", "floor": "upstairs", "name": "Bedroom loop", "pipe_length_m": 80, "served_rooms": ["bedroom"]},
    ]
    hydraulics = {"circuits": {
        "circuit_01": {"active_configured_flow_l_min": 2.0, "circulation_confirmed": True},
        "circuit_02": {"active_configured_flow_l_min": 1.0, "circulation_confirmed": True},
    }}
    boiler = {
        "last_modulation_interval_calibrated_output_kw": 6.0,
        "last_modulation_room_diagnostics": {
            "living": {"allocation_share": 0.6},
            "bedroom": {"allocation_share": 0.4},
        },
    }
    result = MODULE.build_stage5_ufh_slab_model(rooms, circuits, hydraulics, boiler, 40.0)
    ground = result["floors"]["ground_floor"]
    upstairs = result["floors"]["upstairs"]
    assert ground["screed_capacity_wh_per_k"] == 990.0
    assert upstairs["screed_capacity_wh_per_k"] == 297.0
    assert ground["allocated_useful_heat_power_w"] == 3600.0
    assert upstairs["allocated_useful_heat_power_w"] == 2400.0
    assert ground["estimated_return_temperature_c"] < 40.0
    assert upstairs["estimated_return_temperature_c"] < 40.0
    assert ground["slab_temperature_c"] is None
    assert ground["slab_to_room_conductance_w_per_k"] is None


def test_stage5_withholds_return_when_no_confirmed_flow_or_modulation_allocation():
    result = MODULE.build_stage5_ufh_slab_model(
        [{"id": "room", "floor": "ground_floor", "area_m2": 10}],
        [{"id": "loop", "floor": "ground_floor", "pipe_length_m": 80}],
        {"circuits": {"loop": {"active_configured_flow_l_min": 0, "circulation_confirmed": False}}},
        {"last_modulation_interval_calibrated_output_kw": 5.0,
         "last_modulation_room_diagnostics": {}},
        35.0,
    )
    floor = result["floors"]["ground_floor"]
    assert floor["estimated_return_temperature_c"] is None
    assert floor["status"] == "awaiting_valid_flow_and_power"


def test_stage5_reports_missing_room_area_without_fabricating_capacity():
    result = MODULE.build_stage5_ufh_slab_model(
        [{"id": "unknown", "floor": "upstairs"}],
        [],
        {},
        {},
        None,
    )
    floor = result["floors"]["upstairs"]
    assert floor["screed_capacity_wh_per_k"] is None
    assert floor["room_screed_capacity"]["unknown"]["status"] == "withheld_missing_area"



def test_stage5_preserves_manual_configured_flow_when_pump_is_stopped():
    result = MODULE.build_stage5_ufh_slab_model(
        [{"id": "living", "floor": "ground_floor", "area_m2": 30}],
        [{"id": "loop", "floor": "ground_floor", "pipe_length_m": 90}],
        {"circuits": {"loop": {
            "configured_flow_l_min": 2.5,
            "active_configured_flow_l_min": 0.0,
            "flow_is_manual_input": True,
            "circulation_confirmed": False,
        }}},
        {"last_modulation_interval_calibrated_output_kw": 5.0,
         "last_modulation_room_diagnostics": {"living": {"allocation_share": 1.0}}},
        40.0,
    )
    floor = result["floors"]["ground_floor"]
    circuit = floor["circuits"][0]
    assert floor["configured_total_flow_l_min"] == 2.5
    assert floor["active_total_flow_l_min"] == 0.0
    assert circuit["configured_flow_l_min"] == 2.5
    assert circuit["active_flow_l_min"] == 0.0
    assert circuit["flow_is_manual_input"] is True
    assert floor["estimated_return_temperature_c"] is None



def test_stage5_creates_independent_room_level_slab_nodes_and_keeps_finish_storage_separate():
    result = MODULE.build_stage5_ufh_slab_model(
        [
            {"id": "bedroom", "name": "Bedroom", "floor": "upstairs", "area_m2": 12.0,
             "floor_finish_zones": [{
                 "id": "parquet", "material": "wood_parquet", "area_m2": 12.0,
                 "thickness_mm": 10, "conductivity_w_mk": 0.15,
                 "density_kg_m3": 700, "specific_heat_j_kgk": 1600,
             }]},
            {"id": "bathroom", "name": "Bathroom", "floor": "upstairs", "area_m2": 5.0,
             "floor_finish_zones": [{
                 "id": "tiles", "material": "ceramic_tile", "area_m2": 5.0,
                 "thickness_mm": 10, "conductivity_w_mk": 1.3,
                 "density_kg_m3": 2300, "specific_heat_j_kgk": 840,
             }]},
        ],
        [],
        {},
        {},
        None,
    )
    assert result["slab_representation"] == "one_latent_ufh_slab_node_per_room"
    assert set(result["rooms"]) == {"bedroom", "bathroom"}
    assert result["rooms"]["bedroom"]["slab_node"] == "one_latent_ufh_slab_node_per_room"
    assert result["rooms"]["bedroom"]["floor_finish"]["capacity_wh_per_k"] > 0
    assert result["rooms"]["bathroom"]["floor_finish"]["capacity_wh_per_k"] > 0
    assert result["floors"]["upstairs"]["slab_node"] == "room_level_nodes_aggregated_for_floor_reporting"
    assert result["floors"]["upstairs"]["finish_capacity_wh_per_k"] > 0
    assert result["rooms"]["bedroom"]["slab_temperature_c"] is None


def test_house_return_estimate_is_flow_weighted_and_separate_from_measured_input():
    estimate = MODULE.build_estimated_water_return_temperature({
        "floors": {
            "ground_floor": {"estimated_return_temperature_c": 30.0, "active_total_flow_l_min": 10.0, "allocated_useful_heat_power_w": 1000.0},
            "upstairs": {"estimated_return_temperature_c": 34.0, "active_total_flow_l_min": 5.0, "allocated_useful_heat_power_w": 500.0},
            "inactive": {"estimated_return_temperature_c": None, "active_total_flow_l_min": 0.0},
        }
    })
    assert estimate["value_c"] == 31.33
    assert estimate["active_flow_l_min"] == 15.0
    assert estimate["status"] == "estimated_from_modulation_power_and_configured_active_flow"
    assert "measured return temperature" in estimate["limitations"][2]


def test_house_return_estimate_withholds_value_when_no_floor_is_active():
    estimate = MODULE.build_estimated_water_return_temperature({"floors": {
        "ground_floor": {"estimated_return_temperature_c": 30.0, "active_total_flow_l_min": 0.0}
    }})
    assert estimate["value_c"] is None
    assert estimate["active_flow_l_min"] is None