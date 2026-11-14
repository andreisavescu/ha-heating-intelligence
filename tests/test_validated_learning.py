"""Tests for persistent validation-gated Recorder learning."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
PATH = ROOT / "custom_components" / "heating_intelligence" / "validated_learning.py"
SPEC = importlib.util.spec_from_file_location("validated_learning", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

def fit(status="holdout_better_than_persistence", stability="relatively_stable"):
    return {
        "status": status,
        "normalized_rates_per_hour": {"envelope": 0.012, "room:hall": 0.004},
        "coefficient_uncertainty": {
            "envelope": {"stability": stability, "median_1_per_hour": 0.011},
            "room:hall": {"stability": stability, "median_1_per_hour": 0.004},
        },
        "predictor_matrix_rank": 2,
        "predictor_condition_number": 4.2,
        "intercept_c_per_hour": 0.001,
        "training_r_squared": 0.72,
        "holdout_mae_c": 0.08,
        "persistence_baseline_mae_c": 0.11,
        "training_intervals": 60,
        "holdout_intervals": 25,
    }

def test_promotes_only_holdout_better_stable_normalized_fit():
    state = MODULE.build_validated_learning_state(None, {"rooms": {"living": fit()}}, "2026-11-15T10:00:00+00:00")
    assert state["version"] == 1
    assert state["accepted_room_count"] == 1
    assert state["accepted"]["living"]["normalized_rates_per_hour"]["envelope"] == 0.012
    assert state["physical_simulation_eligible"] is False
    assert state["accepted"]["living"]["parameter_scope"] == "normalized_G_over_C_per_hour_only"

def test_rejects_fit_that_does_not_beat_persistence():
    state = MODULE.build_validated_learning_state(None, {"rooms": {"living": fit(status="holdout_not_better_than_persistence")}}, "2026-11-15T10:00:00+00:00")
    assert state["version"] == 0
    assert state["accepted"] == {}
    assert "chronological_holdout_not_better_than_persistence" in state["last_rejected_rooms"]["living"]

def test_rejects_unstable_coefficients():
    state = MODULE.build_validated_learning_state(None, {"rooms": {"living": fit(stability="high_uncertainty")}}, "2026-11-15T10:00:00+00:00")
    assert state["accepted_room_count"] == 0
    assert any(item.startswith("coefficient_not_stable") for item in state["last_rejected_rooms"]["living"])

def test_rejected_new_fit_preserves_last_accepted_model():
    accepted = MODULE.build_validated_learning_state(None, {"rooms": {"living": fit()}}, "2026-11-15T10:00:00+00:00")
    rejected = MODULE.build_validated_learning_state(accepted, {"rooms": {"living": fit(status="holdout_not_better_than_persistence")}}, "2026-11-15T16:00:00+00:00")
    assert rejected["version"] == 1
    assert rejected["accepted"] == accepted["accepted"]

def test_unchanged_fit_does_not_create_a_new_model_version():
    first = MODULE.build_validated_learning_state(None, {"rooms": {"living": fit()}}, "2026-11-15T10:00:00+00:00")
    second = MODULE.build_validated_learning_state(first, {"rooms": {"living": fit()}}, "2026-11-15T16:00:00+00:00")
    assert second["version"] == 1
    assert second["last_promotion"] == "unchanged_validated_fit"

def test_diagnostics_do_not_expose_full_parameter_snapshot():
    state = MODULE.build_validated_learning_state(None, {"rooms": {"living": fit()}}, "2026-11-15T10:00:00+00:00")
    diagnostics = MODULE.validated_learning_diagnostics(state)
    assert diagnostics["accepted_rooms"] == ["living"]
    assert "normalized_rates_per_hour" not in diagnostics
    assert diagnostics["physical_simulation_eligible"] is False
