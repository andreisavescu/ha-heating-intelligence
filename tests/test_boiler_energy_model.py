from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "boiler_energy_model.py"
SPEC = spec_from_file_location("boiler_energy_model", MODULE_PATH)
MODEL = module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


def test_heating_gas_allocates_only_to_active_demand_rooms_by_flow():
    history = {}
    circuits = {
        "a": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["bed", "office"]},
        "b": {"flow_active": True, "configured_flow_l_min": 2.0, "served_rooms": ["office"]},
    }
    rooms = {
        "bed": {"demand": True},
        "office": {"demand": False},
    }
    first = MODEL.update_boiler_energy_model(history, 10.0, 4.0, True, 50, circuits, rooms, 60)
    assert first["boiler_generated_heat_kwh"] == 0
    second = MODEL.update_boiler_energy_model(history, 10.1, 4.1, False, 0, circuits, rooms, 60)
    assert second["boiler_meter_interval_input_kwh"] == 1.055
    assert second["boiler_meter_interval_useful_heat_kwh"] == 0.9495
    assert second["boiler_room_allocations_kwh"]["bed"] == 0.9495
    assert second["boiler_unallocated_heat_kwh"] == 0


def test_shared_circuit_splits_only_between_eligible_served_rooms():
    history = {}
    circuits = {"shared": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["a", "b"]}}
    rooms = {"a": {"demand": True}, "b": {"demand": True}}
    MODEL.update_boiler_energy_model(history, 1.0, 0.0, True, 100, circuits, rooms, 60)
    result = MODEL.update_boiler_energy_model(history, 1.1, 0.0, False, 0, circuits, rooms, 60)
    assert abs(result["boiler_room_allocations_kwh"]["a"] - 0.47475) < 1e-8
    assert abs(result["boiler_room_allocations_kwh"]["b"] - 0.47475) < 1e-8


def test_burner_binary_does_not_gate_modulation_energy_or_room_exposure():
    history = {}
    circuits = {"a": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["a"]}}
    rooms = {"a": {"demand": True}}
    first = MODEL.update_boiler_energy_model(history, 1.0, 0.0, False, 40, circuits, rooms, 90)
    assert first["boiler_modulation_estimated_output_kw"] == 10.0
    assert first["boiler_modulation_energy_total_kwh"] > 0
    assert first["boiler_modulation_room_energy_kwh"]["a"] > 0
    second = MODEL.update_boiler_energy_model(history, 1.1, 0.0, True, 0, circuits, rooms, 90)
    assert second["boiler_modulation_estimated_output_kw"] == 0.0
    assert second["boiler_modulation_energy_total_kwh"] == first["boiler_modulation_energy_total_kwh"]


def test_dhw_meter_is_tracked_but_never_allocated_to_rooms():
    history = {}
    first = MODEL.update_boiler_energy_model(history, 2.0, 3.0, False, None, {}, {}, 60)
    result = MODEL.update_boiler_energy_model(history, 2.0, 3.1, False, None, {}, {}, 60)
    assert result["dhw_input_energy_kwh"] == 1.055
    assert result["boiler_generated_heat_kwh"] == 0


def test_monthly_meter_reset_never_produces_negative_energy():
    history = {}
    MODEL.update_boiler_energy_model(history, 29.9, 7.6, False, None, {}, {}, 60)
    result = MODEL.update_boiler_energy_model(history, 0.2, 0.1, False, None, {}, {}, 60)
    assert result["boiler_meter_interval_input_kwh"] == 2.11
    assert result["boiler_meter_reset_detected"] is True
    assert result["boiler_meter_reset_count"] == 2


def test_variable_meter_increment_is_reconciled_as_actual_delta():
    history = {}
    circuits = {"c": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["r"]}}
    rooms = {"r": {"demand": True}}
    MODEL.update_boiler_energy_model(history, 10.0, 4.0, True, 100, circuits, rooms, 60)
    result = MODEL.update_boiler_energy_model(history, 10.5, 4.2, False, 0, circuits, rooms, 60)
    assert result["boiler_meter_interval_input_kwh"] == 5.275
    assert result["boiler_meter_interval_useful_heat_kwh"] == 4.7475
    assert result["boiler_meter_sync_count"] == 1
    assert result["boiler_room_allocations_kwh"]["r"] == 4.7475


def test_daily_room_allocation_is_split_at_midnight():
    from datetime import datetime, timezone

    history = {}
    circuits = {"c": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["r"]}}
    rooms = {"r": {"demand": True}}
    MODEL.update_boiler_energy_model(
        history, 1.0, 0.0, True, 100, circuits, rooms, 60,
        now=datetime(2026, 11, 20, 23, 59, 0, tzinfo=timezone.utc),
    )
    MODEL.update_boiler_energy_model(
        history, 1.0, 0.0, True, 100, circuits, rooms, 60,
        now=datetime(2026, 11, 21, 0, 0, 0, tzinfo=timezone.utc),
    )
    result = MODEL.update_boiler_energy_model(
        history, 1.1, 0.0, False, 0, circuits, rooms, 60,
        now=datetime(2026, 11, 21, 0, 1, 0, tzinfo=timezone.utc),
    )
    daily = result["boiler_daily_room_energy_kwh"]
    assert set(daily) == {"2026-11-20", "2026-11-21"}
    assert daily["2026-11-20"]["r"] > 0
    assert daily["2026-11-21"]["r"] > 0
    assert abs(daily["2026-11-20"]["r"] + daily["2026-11-21"]["r"] - 0.9495) < 1e-8


def test_modulation_maps_to_25kw_with_minimum_firing_output():
    assert MODEL._modulation_output_kw(100) == 25.0
    assert MODEL._modulation_output_kw(50) == 12.5
    assert MODEL._modulation_output_kw(10) == 3.2
    assert MODEL._modulation_output_kw(0) == 0.0
    assert MODEL._modulation_output_kw(None) is None


def test_daily_gas_reconciliation_smooths_calibration_for_following_day():
    from datetime import datetime, timezone

    history = {}
    MODEL.update_boiler_energy_model(history, 10.0, 2.0, False, 0, {}, {}, 0,
        now=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc))
    state = history["boiler_energy_experiment"]
    state["calibration_factor"] = 1.0
    state["daily_generated_heat_kwh"] = {"2026-11-20": 0.9495}
    state["daily_modulation_estimated_energy_kwh"] = {"2026-11-20": 0.625}
    # The completed-day ratio is staged on Nov 21 and applied on Nov 22.
    MODEL.update_boiler_energy_model(
        history, 10.0, 2.0, False, 0, {}, {}, 0,
        now=datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc),
    )
    result = MODEL.update_boiler_energy_model(
        history, 10.0, 2.0, True, 100, {}, {}, 60,
        now=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
    )
    observed = 0.9495 / 0.625
    expected = 0.66 * 1.0 + 0.34 * observed
    assert abs(result["boiler_calibration_factor_old"] - 1.0) < 1e-8
    assert abs(result["boiler_calibration_factor_observed_daily"] - observed) < 1e-8
    assert abs(result["boiler_calibration_factor_smoothed"] - expected) < 3e-6
    assert abs(result["boiler_modulation_calibrated_output_kw"] - 25 * expected) < 1e-3
    assert result["boiler_calibration_last_completed_day"] == "2026-11-20"


def test_modulation_estimate_is_available_between_gas_meter_updates():
    history = {}
    first = MODEL.update_boiler_energy_model(history, 1.0, 0.0, True, 40, {}, {}, 10)
    second = MODEL.update_boiler_energy_model(history, 1.0, 0.0, True, 40, {}, {}, 10)
    assert first["boiler_modulation_estimated_output_kw"] == 10.0
    assert second["boiler_modulation_calibrated_output_kw"] == 10.0
    assert second["boiler_modulation_calibration_status"] == "initial_25kw_estimate"


def test_daily_modulation_energy_is_allocated_across_active_ufh_rooms():
    from datetime import datetime, timezone

    history = {}
    circuits = {
        "downstairs": {"flow_active": True, "active_configured_flow_l_min": 1.0, "served_rooms": ["living"]},
        "upstairs": {"flow_active": True, "active_configured_flow_l_min": 3.0, "served_rooms": ["bedroom"]},
    }
    rooms = {"living": {"demand": True}, "bedroom": {"demand": False}}
    result = MODEL.update_boiler_energy_model(
        history, 1.0, 0.0, True, 40, circuits, rooms, 60,
        now=datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc),
    )
    daily = result["boiler_daily_modulation_room_energy_kwh"]["2026-11-21"]
    assert abs(daily["living"] - 10.0 / 60.0 / 4.0) < 1e-8
    assert abs(daily["bedroom"] - 3.0 * 10.0 / 60.0 / 4.0) < 1e-8
    assert abs(result["boiler_daily_modulation_generated_heat_kwh"]["2026-11-21"] - 10.0 / 60.0) < 1e-8
    assert abs(sum(daily.values()) - result["boiler_daily_modulation_generated_heat_kwh"]["2026-11-21"]) < 1e-8


def test_modulation_room_energy_exposes_daily_weekly_monthly_and_lifetime_totals():
    from datetime import datetime, timezone

    history = {}
    circuits = {"c": {"flow_active": True, "active_configured_flow_l_min": 2.0, "served_rooms": ["room"]}}
    rooms = {"room": {"demand": False}}
    first = MODEL.update_boiler_energy_model(
        history, None, None, True, 40, circuits, rooms, 60,
        now=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
    )
    second = MODEL.update_boiler_energy_model(
        history, None, None, True, 40, circuits, rooms, 60,
        now=datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc),
    )

    # 10 kW × one minute per day, all assigned to this room.
    per_day = 10.0 / 60.0
    assert abs(first["boiler_modulation_daily_room_energy_kwh"]["room"] - per_day) < 1e-6
    assert abs(second["boiler_modulation_daily_room_energy_kwh"]["room"] - per_day) < 1e-6
    assert abs(second["boiler_modulation_weekly_room_energy_kwh"]["room"] - 2 * per_day) < 1e-6
    assert abs(second["boiler_modulation_monthly_room_energy_kwh"]["room"] - 2 * per_day) < 1e-6
    assert abs(second["boiler_modulation_room_energy_kwh"]["room"] - 2 * per_day) < 1e-6


def test_modulation_room_period_totals_roll_over_at_calendar_boundaries():
    from datetime import datetime, timezone

    history = {}
    circuits = {"c": {"flow_active": True, "active_configured_flow_l_min": 1.0, "served_rooms": ["room"]}}
    rooms = {"room": {}}
    MODEL.update_boiler_energy_model(
        history, None, None, True, 40, circuits, rooms, 60,
        now=datetime(2026, 11, 30, 23, 59, tzinfo=timezone.utc),
    )
    result = MODEL.update_boiler_energy_model(
        history, None, None, True, 40, circuits, rooms, 60,
        now=datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc),
    )
    assert result["boiler_modulation_daily_room_energy_kwh"]["room"] > 0
    assert result["boiler_modulation_monthly_room_energy_kwh"]["room"] == result["boiler_modulation_daily_room_energy_kwh"]["room"]
    assert result["boiler_modulation_room_energy_kwh"]["room"] > result["boiler_modulation_daily_room_energy_kwh"]["room"]


def test_boiler_modulation_energy_exposes_daily_weekly_monthly_and_lifetime_totals():
    from datetime import datetime, timezone

    history = {}
    circuits = {"c": {"flow_active": True, "active_configured_flow_l_min": 1.0, "served_rooms": ["room"]}}
    rooms = {"room": {"demand": False}}
    first = MODEL.update_boiler_energy_model(
        history, None, None, True, 40, circuits, rooms, 60,
        now=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
    )
    second = MODEL.update_boiler_energy_model(
        history, None, None, True, 40, circuits, rooms, 60,
        now=datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc),
    )
    per_day = 10.0 / 60.0
    assert abs(first["boiler_modulation_energy_today_kwh"] - per_day) < 1e-4
    assert abs(second["boiler_modulation_energy_today_kwh"] - per_day) < 1e-4
    assert abs(second["boiler_modulation_energy_week_kwh"] - 2 * per_day) < 1e-4
    assert abs(second["boiler_modulation_energy_month_kwh"] - 2 * per_day) < 1e-4
    assert abs(second["boiler_modulation_energy_total_kwh"] - 2 * per_day) < 1e-4


def test_boiler_modulation_energy_total_only_counts_intervals_with_active_room_flow():
    history = {}
    active = {"c": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["room"]}}
    inactive = {"c": {"flow_active": False, "configured_flow_l_min": 1.0, "served_rooms": ["room"]}}
    rooms = {"room": {}}
    MODEL.update_boiler_energy_model(history, None, None, True, 40, inactive, rooms, 60)
    result = MODEL.update_boiler_energy_model(history, None, None, True, 40, active, rooms, 60)
    assert abs(result["boiler_modulation_energy_total_kwh"] - 10.0 / 60.0) < 1e-4


def test_modulation_room_interval_diagnostics_explain_allocation():
    history = {}
    circuits = {
        "shared": {
            "flow_active": True,
            "active_configured_flow_l_min": 2.0,
            "served_rooms": ["bathroom", "hall"],
        }
    }
    rooms = {"bathroom": {}, "hall": {}}
    result = MODEL.update_boiler_energy_model(
        history, None, None, True, 40, circuits, rooms, 60
    )
    assert result["boiler_modulation_interval_burner_on"] is True
    assert result["boiler_modulation_interval_percent"] == 40
    assert result["boiler_modulation_interval_output_kw"] == 10.0
    assert result["boiler_modulation_interval_seconds"] == 60.0
    diagnostics = result["boiler_modulation_room_interval_diagnostics"]
    assert diagnostics["bathroom"]["flow_active"] is True
    assert diagnostics["bathroom"]["allocation_weight_l_min"] == 1.0
    assert diagnostics["bathroom"]["allocation_share"] == 0.5
    assert abs(diagnostics["bathroom"]["interval_energy_kwh"] - (10 / 60 / 2)) < 1e-7
    assert diagnostics["bathroom"]["exposure_kw_seconds"] == 600.0


def test_modulation_room_interval_diagnostics_show_inactive_circuit():
    history = {}
    circuits = {
        "shared": {
            "flow_active": False,
            "configured_flow_l_min": 2.0,
            "served_rooms": ["bathroom"],
        }
    }
    result = MODEL.update_boiler_energy_model(
        history, None, None, True, 40, circuits, {"bathroom": {}}, 60
    )
    assert result["boiler_modulation_room_interval_diagnostics"] == {}
    assert result["boiler_modulation_interval_output_kw"] == 10.0



def test_heating_season_runs_from_september_first_to_may_first():
    from datetime import date

    assert MODEL._heating_season_key(date(2026, 9, 1)) == "2026-09-01/2027-05-01"
    assert MODEL._heating_season_key(date(2027, 4, 30)) == "2026-09-01/2027-05-01"
    assert MODEL._heating_season_key(date(2027, 5, 1)) == "2026-09-01/2027-05-01"
    assert MODEL._heating_season_key(date(2027, 9, 1)) == "2027-09-01/2028-05-01"


def test_gas_reconciled_heating_season_totals_survive_daily_ledger_cleanup():
    from datetime import datetime, timezone

    history = {}
    circuits = {"c": {"flow_active": True, "configured_flow_l_min": 1.0, "served_rooms": ["r"]}}
    rooms = {"r": {"demand": True}}
    MODEL.update_boiler_energy_model(
        history, 1.0, 0.0, True, 100, circuits, rooms, 60,
        now=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )
    result = MODEL.update_boiler_energy_model(
        history, 1.1, 0.0, False, 0, circuits, rooms, 60,
        now=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
    )
    experiment = history["boiler_energy_experiment"]
    assert "2026-09-01" not in experiment["daily_room_energy_kwh"]
    assert experiment["heating_season_room_energy_kwh"]["2026-09-01/2027-05-01"]["r"] > 0
    assert result["boiler_heating_season_allocated_heat_kwh"] > 0
