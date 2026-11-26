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
        {"rooms": {"room": {"conductance_by_boundary_w_per_k": {
            "outdoor": 4.0, "attic": 3.0, "ground": 2.0,
            "adjacent_heated": 1.0,
        }, "opaque_surface_counts": {"outdoor": 1}}}}},
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
        {"rooms": {"room": {"normalized_rates_per_hour": {"envelope": 0.1}}},
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
    assert room["effective_room_capacity"]["effective_room_capacity_wh_per_k"] is None
