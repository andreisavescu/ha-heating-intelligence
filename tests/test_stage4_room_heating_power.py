"""Tests for Stage 4 inferred room heating power."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "stage4_room_heating_power",
    Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "stage4_room_heating_power.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_stage4_reports_gas_reconciled_room_average_power():
    result = module.build_stage4_room_heating_power(
        {
            "boiler_room_interval_average_power_w": {"living": 820.5},
            "boiler_room_allocations_kwh": {"living": 0.041},
            "boiler_meter_interval_seconds": 180.0,
        },
        {"living": {"name": "Living"}},
    )
    room = result["rooms"]["living"]
    assert room["gas_reconciled_interval_average_power_w"] == 820.5
    assert room["gas_reconciled_interval_energy_kwh"] == 0.041
    assert room["gas_meter_interval_seconds"] == 180.0
    assert room["status"] == "estimated_power_available"
    assert result["room_level_heat_delivery_measured"] is False


def test_stage4_withholds_power_when_no_allocation_interval_exists():
    result = module.build_stage4_room_heating_power({}, {"bedroom": {"name": "Bedroom"}})
    assert result["rooms"]["bedroom"]["gas_reconciled_interval_average_power_w"] is None
    assert result["rooms"]["bedroom"]["status"] == "awaiting_room_heat_allocation_interval"
    assert result["status"] == "awaiting_room_heat_allocation_interval"


def test_stage4_keeps_modulation_and_gas_ledgers_separate():
    result = module.build_stage4_room_heating_power(
        {
            "boiler_room_interval_average_power_w": {"room": 500.0},
            "boiler_modulation_room_interval_diagnostics": {
                "room": {"estimated_power_w": 460.0}
            },
            "boiler_meter_interval_seconds": 120.0,
            "boiler_modulation_interval_seconds": 30.0,
        },
        {"room": {}},
    )
    room = result["rooms"]["room"]
    assert room["gas_reconciled_interval_average_power_w"] == 500.0
    assert room["modulation_interval_estimated_power_w"] == 460.0
    assert room["gas_meter_interval_seconds"] != room["modulation_interval_seconds"]
