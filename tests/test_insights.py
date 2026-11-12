"""Tests for explainable stage 4 operational insights."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "insights.py"
SPEC = importlib.util.spec_from_file_location("heating_insights", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_flags_floor_demand_without_pump():
    insights = MODULE.build_heating_insights({
        "floor_analysis": {"ground_floor": {"demand_pump_state": "demand_without_pump"}},
    })
    assert len(insights) == 1
    assert insights[0]["severity"] == "warning"
    assert "Downstairs" in insights[0]["title"]


def test_room_insights_require_thresholds_and_supporting_samples():
    insights = MODULE.build_heating_insights({
        "rooms": {
            "room_a": {
                "id": "room_a",
                "name": "Living",
                "window_open_demand_seconds_today": 1200,
                "cycles_today": 9,
                "heating_response_minutes": 110,
                "heating_response_samples": 3,
                "cooling_rate_c_per_hour": 0.7,
            },
            "room_b": {
                "id": "room_b",
                "name": "Bedroom",
                "window_open_demand_seconds_today": 60,
                "cycles_today": 2,
                "heating_response_minutes": 150,
                "heating_response_samples": 1,
                "cooling_rate_c_per_hour": 0.2,
            },
        },
    })
    assert len(insights) == 4
    assert all("Living" in item["title"] for item in insights)


def test_no_insights_when_data_is_missing_or_below_threshold():
    assert MODULE.build_heating_insights({}) == []
    assert MODULE.build_heating_insights({
        "rooms": {"room_a": {
            "id": "room_a",
            "name": "Living",
            "window_open_demand_seconds_today": 300,
            "cycles_today": 3,
            "heating_response_minutes": 120,
            "heating_response_samples": 2,
            "cooling_rate_c_per_hour": 0.3,
        }},
        "floor_analysis": {"upstairs": {"demand_pump_state": "idle"}},
    }) == []
