"""Persistent, validation-gated storage for normalized Recorder calibration."""
from __future__ import annotations
import hashlib
import json
from typing import Any

def _finite_non_negative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return None
    return number

def build_validated_learning_state(previous: dict[str, Any] | None, identification: dict[str, Any] | None, checked_at: str) -> dict[str, Any]:
    """Promote only normalized fits that pass holdout and stability gates."""
    state = dict(previous) if isinstance(previous, dict) else {}
    state.setdefault("version", 0)
    accepted = dict(state.get("accepted") or {})
    rooms = identification.get("rooms", {}) if isinstance(identification, dict) else {}
    if not isinstance(rooms, dict):
        rooms = {}
    promoted, rejected = {}, {}
    for room_id, fit in rooms.items():
        if not isinstance(fit, dict):
            rejected[str(room_id)] = ["invalid_fit_record"]
            continue
        rates = fit.get("normalized_rates_per_hour")
        uncertainty = fit.get("coefficient_uncertainty")
        reasons = []
        if fit.get("status") != "holdout_better_than_persistence":
            reasons.append("chronological_holdout_not_better_than_persistence")
        if not isinstance(rates, dict) or not rates:
            reasons.append("normalized_coefficients_unavailable")
        else:
            for predictor, value in rates.items():
                if _finite_non_negative(value) is None:
                    reasons.append("invalid_normalized_coefficient:" + str(predictor))
        if not isinstance(uncertainty, dict) or set(uncertainty) != set(rates or {}):
            reasons.append("coefficient_stability_unavailable")
        else:
            for predictor, metrics in uncertainty.items():
                if not isinstance(metrics, dict) or metrics.get("stability") != "relatively_stable":
                    reasons.append("coefficient_not_stable:" + str(predictor))
                    continue
                if _finite_non_negative(metrics.get("median_1_per_hour")) is None:
                    reasons.append("stable_coefficient_median_missing:" + str(predictor))
        if fit.get("predictor_matrix_rank") is None or fit.get("predictor_condition_number") is None:
            reasons.append("predictor_identifiability_not_confirmed")
        if reasons:
            rejected[str(room_id)] = sorted(set(reasons))
            continue
        promoted[str(room_id)] = {
            "normalized_rates_per_hour": {key: float(value) for key, value in rates.items()},
            "intercept_c_per_hour": fit.get("intercept_c_per_hour"),
            "training_r_squared": fit.get("training_r_squared"),
            "holdout_mae_c": fit.get("holdout_mae_c"),
            "persistence_baseline_mae_c": fit.get("persistence_baseline_mae_c"),
            "predictor_matrix_rank": fit.get("predictor_matrix_rank"),
            "predictor_condition_number": fit.get("predictor_condition_number"),
            "coefficient_uncertainty": uncertainty,
            "training_intervals": fit.get("training_intervals"),
            "holdout_intervals": fit.get("holdout_intervals"),
            "accepted_at": checked_at,
            "evidence": "chronological_holdout_and_moving_block_stability",
            "parameter_scope": "normalized_G_over_C_per_hour_only",
        }
    fingerprint_payload = json.dumps({room: {key: value for key, value in data.items() if key != "accepted_at"} for room, data in promoted.items()}, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
    if promoted and fingerprint != state.get("accepted_fingerprint"):
        state["version"] = int(state.get("version", 0)) + 1
        accepted.update(promoted)
        state["accepted_at"] = checked_at
        state["accepted_fingerprint"] = fingerprint
        state["accepted"] = accepted
        state["last_promotion"] = "accepted_validated_normalized_fit"
    elif promoted:
        state["last_promotion"] = "unchanged_validated_fit"
    else:
        state["last_promotion"] = "no_candidate_passed_validation"
    state["accepted"] = accepted
    state["status"] = "validated_normalized_fit_available" if accepted else "collecting_validated_history"
    state["last_checked_at"] = checked_at
    state["last_candidate_status"] = identification.get("status") if isinstance(identification, dict) else "not_available"
    state["last_rejected_rooms"] = rejected
    state["accepted_room_count"] = len(accepted)
    state["physical_simulation_eligible"] = False
    state["note"] = "Accepted values are normalized G/C response rates in 1/hour only. They are not absolute W/K conductances, room capacities, delivered heat or room heat shares. Physical simulation remains independently gated."
    return state

def validated_learning_diagnostics(state: dict[str, Any]) -> dict[str, Any]:
    """Return a compact UI-safe view without raw Recorder samples."""
    accepted = state.get("accepted", {}) if isinstance(state, dict) else {}
    return {
        "status": state.get("status", "collecting_validated_history"),
        "version": int(state.get("version", 0)),
        "accepted_at": state.get("accepted_at"),
        "last_checked_at": state.get("last_checked_at"),
        "last_promotion": state.get("last_promotion"),
        "accepted_room_count": len(accepted) if isinstance(accepted, dict) else 0,
        "accepted_rooms": sorted(accepted) if isinstance(accepted, dict) else [],
        "rejected_rooms": state.get("last_rejected_rooms", {}),
        "physical_simulation_eligible": False,
        "note": state.get("note"),
    }
