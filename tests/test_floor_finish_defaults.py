"""Tests for one-time floor-finish defaults."""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[1]
PACKAGE = "_heating_intelligence_floor_finish_defaults_test"
package = types.ModuleType(PACKAGE)
package.__path__ = []
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.floor_finish_defaults",
    ROOT / "custom_components" / "heating_intelligence" / "floor_finish_defaults.py",
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_defaults_assign_parquet_upstairs_and_tile_downstairs_by_full_room_area():
    zones = module.default_floor_finish_zones([
        {"id": "bedroom", "floor": "upstairs", "area_m2": 12.5},
        {"id": "living", "floor": "ground_floor", "area_m2": 35.66},
    ])
    by_room = {zone["room_id"]: zone for zone in zones}
    assert by_room["bedroom"]["material"] == "wood_parquet"
    assert by_room["bedroom"]["area_m2"] == 12.5
    assert by_room["living"]["material"] == "ceramic_tile"
    assert by_room["living"]["area_m2"] == 35.66
    assert by_room["bedroom"]["thickness_mm"] == 10.0
    assert by_room["living"]["conductivity_w_mk"] == 1.3


def test_defaults_skip_rooms_without_valid_area():
    zones = module.default_floor_finish_zones([
        {"id": "unknown", "floor": "upstairs"},
        {"id": "zero", "floor": "ground_floor", "area_m2": 0},
    ])
    assert zones == []
