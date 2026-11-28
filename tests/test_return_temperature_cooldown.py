from datetime import datetime, timedelta, timezone

from custom_components.heating_intelligence.stage5_ufh_slab_model import (
    update_global_return_temperature_estimate,
)


def test_global_return_temperature_cools_for_180_seconds_after_modulation_stops():
    start = datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc)
    history = {
        "return_temperature_last_modulation_percent": 25.0,
        "return_temperature_last_delta_c": 6.0,
    }
    no_active_flow = {"value_c": None, "floor_estimates": {}}

    at_stop = update_global_return_temperature_estimate(history, no_active_flow, 0.0, 40.0, start)
    assert at_stop["status"] == "modulation_off_cooldown"
    assert at_stop["countdown_seconds_remaining"] == 180
    assert at_stop["value_c"] == 34.0

    halfway = update_global_return_temperature_estimate(
        history, no_active_flow, 0.0, 40.0, start + timedelta(seconds=90)
    )
    assert halfway["countdown_seconds_remaining"] == 90
    assert halfway["value_c"] == 37.0

    early_end = update_global_return_temperature_estimate(
        history, no_active_flow, 0.0, 40.0, start + timedelta(seconds=160)
    )
    assert early_end["countdown_seconds_remaining"] == 20
    assert early_end["value_c"] == 39.33

    settled = update_global_return_temperature_estimate(
        history, no_active_flow, 0.0, 40.0, start + timedelta(seconds=180)
    )
    assert settled["status"] == "settled_assumption"
    assert settled["countdown_seconds_remaining"] == 0
    assert settled["value_c"] == 40.0


def test_positive_modulation_cancels_cooldown_and_restores_active_estimate():
    now = datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc)
    history = {
        "return_temperature_last_modulation_percent": 0.0,
        "return_temperature_cooldown_started_at": now.isoformat(),
        "return_temperature_cooldown_delta_c": 5.0,
    }
    active = {"value_c": 33.5, "active_flow_l_min": 8.0, "floor_estimates": {}}
    result = update_global_return_temperature_estimate(history, active, 20.0, 40.0, now)
    assert result["status"] == "active_modulation_estimate"
    assert result["value_c"] == 33.5
    assert result["countdown_seconds_remaining"] == 0
    assert history["return_temperature_cooldown_started_at"] is None
