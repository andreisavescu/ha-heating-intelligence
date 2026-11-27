"""Tests for surface-derived room boundary heat-flow estimates."""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[1]
PACKAGE = "_heating_intelligence_room_heat_loss_test"
package = types.ModuleType(PACKAGE)
package.__path__ = []
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.room_heat_loss",
    ROOT / "custom_components" / "heating_intelligence" / "room_heat_loss.py",
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
MODULE = module


def test_heat_loss_uses_surface_ua_and_separates_outdoor_attic_and_ground():
    result = MODULE.build_room_boundary_heat_loss(
        {"bedroom": {"name": "Bedroom", "floor": "upstairs", "temperature": 22.0}},
        {"rooms": {"bedroom": {
            "conductance_by_boundary_w_per_k": {
                "outdoor": 4.0, "attic": 2.0, "ground": None,
            },
            "opaque_surface_counts": {"outdoor": 1},
            "window_record_count": 1,
        }}},
        2.0,
        10.0,
        {"estimated_ground_boundary_temperature_c": 8.0, "status": "provisional"},
    )
    room = result["rooms"]["bedroom"]
    assert room["outdoor"]["heat_flow_w"] == 80.0
    assert room["attic"]["heat_flow_w"] == 24.0
    assert room["ground"]["heat_flow_w"] is None
    assert room["known_boundary_heat_flow_w"] == 104.0
    assert room["known_boundary_heat_flow_is_complete"] is False
    assert "ground_surface_ua" in room["missing_or_incomplete_boundaries"]


def test_window_only_outdoor_ua_is_reported_as_partial_not_total_wall_loss():
    result = MODULE.build_room_boundary_heat_loss(
        {"living": {"name": "Living", "floor": "ground_floor", "temperature": 22.0}},
        {"rooms": {"living": {
            "conductance_by_boundary_w_per_k": {"outdoor": 3.5, "attic": None, "ground": None},
            "opaque_surface_counts": {"outdoor": 0},
            "window_record_count": 2,
        }}},
        2.0, None,
        {"estimated_ground_boundary_temperature_c": 8.0, "status": "warming_up"},
    )
    room = result["rooms"]["living"]
    assert room["outdoor"]["heat_flow_w"] == 70.0
    assert room["outdoor"]["status"] == "partial_surface_inventory"
    assert room["known_boundary_heat_flow_is_complete"] is False


def test_ground_temperature_model_is_lagged_and_explicitly_provisional():
    state, model = MODULE.update_ground_temperature_model(5.0, None, 3600.0)
    assert model["estimated_ground_boundary_temperature_c"] == 5.0
    assert model["status"] == "warming_up_lagged_outdoor_proxy"
    state, model = MODULE.update_ground_temperature_model(15.0, state, 3600.0)
    assert 5.0 < model["estimated_ground_boundary_temperature_c"] < 15.0
    assert model["method"].startswith("30-day first-order low-pass")
