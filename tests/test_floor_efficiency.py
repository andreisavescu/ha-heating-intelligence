"""Tests for floor comparisons and weather-normalised efficiency."""
from pathlib import Path
import importlib.util

PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "floor_efficiency.py"
SPEC = importlib.util.spec_from_file_location("floor_efficiency", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_floor_performance_is_area_weighted_and_reports_coverage():
    rooms = {
        "small": {
            "floor": "ground_floor", "area_m2": 20, "temperature": 20,
            "cooling_rate_c_per_hour": 0.5, "heating_response_minutes": 60,
            "daily_demand_seconds": 3600,
        },
        "large": {
            "floor": "ground_floor", "area_m2": 80, "temperature": 22,
            "cooling_rate_c_per_hour": 1.0, "heating_response_minutes": 120,
            "daily_demand_seconds": 7200,
        },
        "unknown": {"floor": "ground_floor", "area_m2": None, "temperature": 19},
        "up": {"floor": "upstairs", "area_m2": 50, "temperature": 21},
    }
    result = MODULE.build_floor_performance(rooms)
    ground = result["ground_floor"]
    assert ground["configured_area_m2"] == 100
    assert ground["area_coverage_percent"] == 66.7
    assert ground["area_weighted_temperature_c"] == 21.6
    assert ground["area_weighted_cooling_c_per_hour"] == 0.9
    assert ground["area_weighted_heating_response_minutes"] == 108.0
    assert ground["area_weighted_demand_hours_today"] == 1.8
    assert result["upstairs"]["area_weighted_temperature_c"] == 21


def test_weather_model_requires_minimum_complete_days():
    assert MODULE.weather_normalised_efficiency({}, "2026-11-12")["status"] == "insufficient_data"


def test_weather_model_produces_weather_adjusted_daily_gas():
    daily = {}
    for day in range(1, 31):
        hdd = 4 + (day % 12)
        delta_t = 3 + (day % 8)
        solar = 40 + ((day * 37) % 240)
        gas = 2.0 + 0.65 * hdd + 0.08 * delta_t - 0.004 * solar
        daily[f"2026-10-{day:02d}"] = {
            "outdoor_sum": (18 - hdd), "outdoor_count": 1,
            "delta_t_sum": delta_t, "delta_t_count": 1,
            "solar_sum": solar, "solar_count": 1,
            "gas_kwh": max(0.2, gas),
        }
    result = MODULE.weather_normalised_efficiency(daily, "2026-11-01")
    assert result["status"] == "available"
    assert result["days_used"] == 30
    assert len(result["recent_days"]) == 30
    assert result["reference_expected_gas_kwh"] > 0
    assert all(item["weather_normalised_gas_kwh"] >= 0 for item in result["recent_days"])
    assert "solar_radiation_w_m2" in result["reference_conditions"]
