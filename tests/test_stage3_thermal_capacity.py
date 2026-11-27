"""Tests for Stage 3 effective room and UFH screed capacity estimates."""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[1]
PACKAGE = "_heating_intelligence_stage3_test"
package = types.ModuleType(PACKAGE)
package.__path__ = []
sys.modules[PACKAGE] = package

for module_name in ("absolute_thermal_identification", "stage3_thermal_capacity"):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{module_name}",
        ROOT / "custom_components" / "heating_intelligence" / f"{module_name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

MODULE = sys.modules[f"{PACKAGE}.stage3_thermal_capacity"]


def test_stage3_uses_per_room_screed_thickness_and_computes_material_prior():
    result = MODULE.build_stage3_thermal_capacity(
        {},
        [{"id": "living", "name": "Living", "area_m2": 35.66,
          "ufh_screed_thickness_cm": 6.0}],
    )
    slab = result["rooms"]["living"]["ufh_screed"]
    assert slab["thickness_cm"] == 6.0
    assert slab["thickness_source"] == "configured_per_room"
    assert slab["screed_volume_m3"] == 2.1396
    assert slab["estimated_capacity_wh_per_k"] == 1176.8
    assert slab["estimated_capacity_range_wh_per_k"] == {
        "lower": 962.8, "upper": 1390.7,
    }


def test_stage3_defaults_missing_room_screed_thickness_to_4_5_cm():
    result = MODULE.build_stage3_thermal_capacity(
        {},
        [{"id": "bedroom", "name": "Bedroom", "area_m2": 11.2}],
    )
    slab = result["rooms"]["bedroom"]["ufh_screed"]
    assert MODULE.DEFAULT_UFH_SCREED_THICKNESS_CM == 4.5
    assert slab["thickness_cm"] == 4.5
    assert slab["thickness_source"] == "default_4.5_cm"
    assert slab["estimated_capacity_wh_per_k"] == 277.2


def test_stage3_uses_explicit_outdoor_ua_not_other_boundaries_as_anchor():
    result = MODULE.build_stage3_thermal_capacity(
        {},
        [{"id": "room", "area_m2": 10, "envelope_conductance_w_per_k": 9.0}],
        {"rooms": {"room": {
            "conductance_by_boundary_w_per_k": {
                "outdoor": 4.0, "attic": 3.0, "ground": 2.0,
                "adjacent_heated": 1.0,
            },
            "opaque_surface_counts": {"outdoor": 1},
        }}},
    )
    assert result["rooms"]["room"]["envelope_anchor_source"] == (
        "explicit_envelope_surface_outdoor_boundary"
    )
    assert result["rooms"]["room"]["effective_room_capacity"]["envelope_anchor_w_per_k"] == 4.0


def test_stage3_withholds_screed_capacity_without_room_area():
    result = MODULE.build_stage3_thermal_capacity({}, [{"id": "unknown"}])
    slab = result["rooms"]["unknown"]["ufh_screed"]
    assert slab["status"] == "withheld_missing_room_area"
    assert slab["estimated_capacity_wh_per_k"] is None


def test_stage3_withholds_absolute_anchor_for_window_only_surface_inventory():
    result = MODULE.build_stage3_thermal_capacity(
        {"rooms": {"room": {"normalized_rates_per_hour": {"envelope": 0.1}}}},
        [{"id": "room", "area_m2": 10, "envelope_conductance_w_per_k": 8.0}],
        {"rooms": {"room": {
            "conductance_by_boundary_w_per_k": {"outdoor": 2.0},
            "opaque_surface_counts": {"outdoor": 0},
            "window_record_count": 1,
        }}},
    )
    room = result["rooms"]["room"]
    assert room["envelope_anchor_source"] == (
        "withheld_incomplete_surface_inventory_missing_opaque_outdoor_surface"
    )
    assert room["effective_room_capacity"]["envelope_anchor_w_per_k"] is None



def test_stage3_keeps_downstairs_incomplete_surface_inventory_withheld():
    result = MODULE.build_stage3_thermal_capacity(
        {},
        [{"id": "living", "area_m2": 35.66,
          "envelope_conductance_w_per_k": 7.42}],
        {"rooms": {"living": {
            "conductance_by_boundary_w_per_k": {"outdoor": 9.6945},
            "opaque_surface_counts": {"outdoor": 0},
            "window_record_count": 4,
        }}},
    )
    room = result["rooms"]["living"]
    assert room["effective_room_capacity"]["envelope_anchor_w_per_k"] is None
    assert room["envelope_anchor_source"] == (
        "withheld_incomplete_surface_inventory_missing_opaque_outdoor_surface"
    )
    assert room["envelope_anchor_status"] == "withheld_incomplete_surface_inventory"


def test_stage3_does_not_require_persistence_win_to_estimate_provisional_capacity():
    response = {"rooms": {"bedroom": {
        "normalized_rates_per_hour": {"envelope": 0.08},
        "coefficient_uncertainty": {"envelope": {
            "lower_90_percentile_1_per_hour": 0.06,
            "upper_90_percentile_1_per_hour": 0.10,
            "successful_replicates": 60,
        }},
        "training_intervals": 30,
        "holdout_intervals": 12,
        "holdout_mae_c": 0.03,
        "persistence_baseline_mae_c": 0.02,
    }}}
    result = MODULE.build_stage3_thermal_capacity(
        response, [{"id": "bedroom", "area_m2": 12.0,
                    "envelope_conductance_w_per_k": 4.8}],
    )
    capacity = result["rooms"]["bedroom"]["effective_room_capacity"]
    assert capacity["effective_room_capacity_wh_per_k"] == 60.0
    assert capacity["status"] == "anchored_absolute_estimate_provisional_unvalidated"
    assert capacity["gates"]["capacity_estimate_available"] is True
    assert capacity["gates"]["capacity_validation_passed"] is False


def test_stage3_calculates_mixed_floor_finish_zones_independently():
    result = MODULE.build_stage3_thermal_capacity(
        {},
        [{
            "id": "hallway", "area_m2": 12.0,
            "floor_finish_zones": [
                {"id": "bathroom_tiles", "name": "Bathroom tiles", "material": "ceramic_tile",
                 "area_m2": 4.8, "thickness_mm": 10, "conductivity_w_mk": 1.3,
                 "density_kg_m3": 2300, "specific_heat_j_kgk": 840},
                {"id": "hallway_parquet", "name": "Hallway parquet", "material": "wood_parquet",
                 "area_m2": 7.2, "thickness_mm": 10, "conductivity_w_mk": 0.15,
                 "density_kg_m3": 700, "specific_heat_j_kgk": 1600},
            ],
        }],
    )
    finish = result["rooms"]["hallway"]["floor_finish"]
    assert finish["configured_area_m2"] == 12.0
    assert finish["equivalent_parallel_resistance_m2k_w"] == 0.01639
    assert finish["zones"][0]["thermal_resistance_m2k_w"] == 0.00769
    assert finish["zones"][1]["thermal_resistance_m2k_w"] == 0.06667
    assert finish["zones"][0]["thermal_capacity_wh_per_k"] == 25.76
    assert finish["zones"][1]["thermal_capacity_wh_per_k"] == 22.4
    assert finish["total_finish_capacity_wh_per_k"] == 48.16


def test_stage3_reports_floor_finish_unconfigured_without_inventing_values():
    result = MODULE.build_stage3_thermal_capacity({}, [{"id": "room", "area_m2": 10}])
    finish = result["rooms"]["room"]["floor_finish"]
    assert finish["status"] == "not_configured"
    assert finish["equivalent_parallel_resistance_m2k_w"] is None
    assert finish["total_finish_capacity_wh_per_k"] is None



def test_stage3_anchor_audit_reports_complete_outdoor_boundary_independently():
    result = MODULE.build_stage3_thermal_capacity(
        {},
        [{"id": "bedroom", "area_m2": 12.0}],
        {"rooms": {"bedroom": {
            "conductance_by_boundary_w_per_k": {"outdoor": 5.2, "attic": 1.4},
            "opaque_surface_counts": {"outdoor": 1},
            "window_record_count": 1,
        }}},
    )
    room = result["rooms"]["bedroom"]
    assert room["envelope_anchor_status"] == "available"
    assert room["envelope_anchor_audit"]["outdoor_opaque_surface_count"] == 1
    assert room["envelope_anchor_audit"]["outdoor_surface_ua_w_per_k"] == 5.2
    assert room["envelope_anchor_audit"]["withheld_reason"] is None


def test_stage3_keeps_each_rooms_outdoor_anchor_and_audit_metadata_isolated():
    result = MODULE.build_stage3_thermal_capacity(
        {},
        [
            {"id": "bedroom_a", "area_m2": 12.0},
            {"id": "bedroom_b", "area_m2": 14.0},
            {"id": "window_only", "area_m2": 8.0, "envelope_conductance_w_per_k": 9.0},
        ],
        {"rooms": {
            "bedroom_a": {"conductance_by_boundary_w_per_k": {"outdoor": 5.2}, "opaque_surface_counts": {"outdoor": 1}, "configured_surface_count": 1, "window_record_count": 1},
            "bedroom_b": {"conductance_by_boundary_w_per_k": {"outdoor": 7.4}, "opaque_surface_counts": {"outdoor": 2}, "configured_surface_count": 2, "window_record_count": 0},
            "window_only": {"conductance_by_boundary_w_per_k": {"outdoor": 0.9158}, "opaque_surface_counts": {"outdoor": 0}, "configured_surface_count": 1, "window_record_count": 1},
        }},
    )
    rooms = result["rooms"]
    assert rooms["bedroom_a"]["envelope_anchor_audit"]["outdoor_surface_ua_w_per_k"] == 5.2
    assert rooms["bedroom_a"]["envelope_anchor_audit"]["anchor_used_w_per_k"] == 5.2
    assert rooms["bedroom_b"]["envelope_anchor_audit"]["outdoor_surface_ua_w_per_k"] == 7.4
    assert rooms["bedroom_b"]["envelope_anchor_audit"]["anchor_used_w_per_k"] == 7.4
    assert rooms["window_only"]["envelope_anchor_audit"]["outdoor_surface_ua_w_per_k"] == 0.9158
    assert rooms["window_only"]["envelope_anchor_audit"]["anchor_used_w_per_k"] is None
    assert rooms["window_only"]["envelope_anchor_status"] == "withheld_incomplete_surface_inventory"
