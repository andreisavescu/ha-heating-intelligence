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


def test_ground_heat_flow_waits_until_lagged_temperature_proxy_has_warmed_up():
    result = MODULE.build_room_boundary_heat_loss(
        {"living": {"name": "Living", "floor": "ground_floor", "temperature": 22.0}},
        {"rooms": {"living": {
            "conductance_by_boundary_w_per_k": {"outdoor": 4.0, "ground": 3.0},
            "opaque_surface_counts": {"outdoor": 1},
            "window_record_count": 1,
        }}},
        2.0, None,
        {"estimated_ground_boundary_temperature_c": 8.0,
         "status": "warming_up_lagged_outdoor_proxy"},
    )
    assert result["rooms"]["living"]["ground"]["heat_flow_w"] is None
    assert result["rooms"]["living"]["ground"]["status"] == (
        "unavailable_ground_temperature_model_warming_up"
    )


def _room(room_id, name, floor):
    return {room_id: {"name": name, "floor": floor, "temperature": 19.0}}


def _audit(room_id, outdoor=2.0, attic=3.0, ground=4.0):
    return {"rooms": {room_id: {
        "conductance_by_boundary_w_per_k": {
            "outdoor": outdoor, "attic": attic, "ground": ground,
        },
        "opaque_surface_counts": {"outdoor": 1},
        "window_record_count": 1,
    }}}


def _model():
    return {"estimated_ground_boundary_temperature_c": 8.0,
            "status": "provisional_lagged_outdoor_proxy"}


def test_upstairs_requires_outdoor_and_attic_not_ground():
    room = MODULE.build_room_boundary_heat_loss(
        _room("bedroom", "Bedroom", "upstairs"),
        _audit("bedroom", ground=None), 2.0, 10.0, _model()
    )["rooms"]["bedroom"]
    assert room["known_boundary_heat_flow_is_complete"] is True
    assert room["attic"]["status"] == "calculated"
    assert room["ground"]["heat_flow_w"] is None
    assert "ground_surface_ua" not in room["missing_or_incomplete_boundaries"]


def test_downstairs_requires_outdoor_and_ground_not_attic():
    room = MODULE.build_room_boundary_heat_loss(
        _room("living", "Living", "ground_floor"),
        _audit("living", attic=None), 2.0, None, _model()
    )["rooms"]["living"]
    assert room["known_boundary_heat_flow_is_complete"] is True
    assert room["attic"]["status"] == "not_applicable"
    assert room["ground"]["status"] == "calculated"


def test_kitchen_requires_outdoor_ground_and_attic():
    room = MODULE.build_room_boundary_heat_loss(
        _room("bucatarie", "Bucătărie", "ground_floor"),
        _audit("bucatarie"), 2.0, 10.0, _model()
    )["rooms"]["bucatarie"]
    assert room["known_boundary_heat_flow_is_complete"] is True
    assert room["attic"]["status"] == "calculated"
    assert room["ground"]["status"] == "calculated"


def test_bathroom_reference_temperatures_only_override_heat_flow_temperature():
    rooms = {
        "baie_matr": {"name": "Baie Matr", "floor": "upstairs", "temperature": 19.0},
        "baie_cada": {"name": "Baie Parter", "floor": "ground_floor", "temperature": 19.0},
    }
    audit = {"rooms": {
        room_id: {
            "conductance_by_boundary_w_per_k": {"outdoor": 2.0, "attic": 1.0, "ground": 1.0},
            "opaque_surface_counts": {"outdoor": 1},
            "window_record_count": 0,
        } for room_id in rooms
    }}
    result = MODULE.build_room_boundary_heat_loss(rooms, audit, 0.0, 10.0, _model())["rooms"]
    assert result["baie_matr"]["room_temperature_c"] == 22.0
    assert result["baie_matr"]["measured_room_temperature_c"] == 19.0
    assert result["baie_matr"]["outdoor"]["heat_flow_w"] == 44.0
    assert result["baie_cada"]["room_temperature_c"] == 20.5
    assert result["baie_cada"]["measured_room_temperature_c"] == 19.0
    assert result["baie_cada"]["outdoor"]["heat_flow_w"] == 41.0
