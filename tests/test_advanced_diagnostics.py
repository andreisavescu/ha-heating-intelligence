"""Tests for v0.7 advanced diagnostics."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "advanced_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("advanced_diagnostics", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_room_score_requires_minimum_observations():
    rooms = {"bedroom": {
        "name": "Bedroom",
        "heating_response_minutes": 45,
        "heating_response_samples": 4,
        "cooling_rate_c_per_hour": 0.2,
        "temperature_change_24h": 0.3,
        "temperature_sample_count": 20,
    }}
    result = MODULE.build_advanced_diagnostics(rooms, {}, {})
    diagnostic = result["room_diagnostics"]["bedroom"]
    assert diagnostic["score"] is not None
    assert diagnostic["score_status"] == "available"


def test_room_score_is_withheld_when_data_is_insufficient():
    rooms = {"bedroom": {
        "name": "Bedroom",
        "heating_response_minutes": 45,
        "heating_response_samples": 1,
        "cooling_rate_c_per_hour": None,
        "temperature_change_24h": None,
        "temperature_sample_count": 4,
    }}
    result = MODULE.build_advanced_diagnostics(rooms, {}, {})
    assert result["room_diagnostics"]["bedroom"]["score"] is None


def test_room_area_produces_per_area_demand_metric():
    rooms = {"bedroom": {"name": "Bedroom", "area_m2": 20, "daily_demand_seconds": 3600}}
    result = MODULE.build_advanced_diagnostics(rooms, {}, {})
    diagnostic = result["room_diagnostics"]["bedroom"]
    assert diagnostic["area_m2"] == 20
    assert diagnostic["heating_demand_hours_per_m2_today"] == 0.05
    assert result["configured_room_area_m2"] == 20


def test_missing_room_area_is_supported():
    result = MODULE.build_advanced_diagnostics({"bedroom": {"name": "Bedroom"}}, {}, {})
    diagnostic = result["room_diagnostics"]["bedroom"]
    assert diagnostic["area_m2"] is None
    assert diagnostic["heating_demand_hours_per_m2_today"] is None


def test_boiler_analysis_does_not_claim_combustion_efficiency():
    result = MODULE.build_advanced_diagnostics({}, {
        "gas_energy_today_kwh": 10,
        "electricity_energy_today_kwh": 1,
        "gas_kwh_per_degree_day": 2,
    }, {})
    boiler = result["boiler_analysis"]
    assert boiler["gas_to_electricity_ratio"] == 10
    assert "cannot be calculated" in boiler["note"]


def test_anomaly_detection_requires_a_historical_baseline():
    history = {"efficiency_daily": {
        "2026-11-01": {"gas_kwh": 2, "electricity_kwh": 1},
        "2026-11-02": {"gas_kwh": 2, "electricity_kwh": 1},
        "2026-11-03": {"gas_kwh": 2, "electricity_kwh": 1},
        "2026-11-04": {"gas_kwh": 5, "electricity_kwh": 1},
    }}
    result = MODULE.build_advanced_diagnostics({}, {}, history)
    assert any(item["id"] == "gas_kwh_above_baseline" for item in result["anomalies"])
