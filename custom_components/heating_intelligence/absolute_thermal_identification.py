"""Anchored absolute RC parameter identification.

The existing multi-zone regression estimates G/C rates. This module converts
those rates into conditional W/K and Wh/K estimates only when a room's
configured envelope conductance is available as an explicit absolute anchor.
It never invents UFH slab capacity or promotes estimates to solver parameters.
"""
from __future__ import annotations

import math
from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def identify_absolute_room_parameters(
    response_identification: dict[str, Any] | None,
    rooms: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert normalized G/C rates to anchored absolute room RC estimates.

    A configured room-envelope conductance is the absolute scale anchor.
    The envelope regression coefficient must be positive, have a usable
    bootstrap range, and improve on the persistence baseline on chronological
    holdout. Other fitted G/C coefficients are multiplied by the resulting
    effective room capacity to produce conditional W/K estimates.

    These estimates remain provisional: the envelope anchor is configuration,
    not a new measurement, and free-coasting regressions can retain bias from
    hidden gains and unmodelled paths. No UFH slab capacity is inferred.
    """
    response_identification = response_identification or {}
    response_rooms = response_identification.get("rooms") or {}
    results: dict[str, Any] = {}
    identified = 0
    for room in rooms:
        room_id = str(room.get("id", "unknown"))
        fit = response_rooms.get(room_id) or {}
        rates = fit.get("normalized_rates_per_hour") or {}
        uncertainty = fit.get("coefficient_uncertainty") or {}
        envelope_g = _number(room.get("envelope_conductance_w_per_k"))
        envelope_rate = _number(rates.get("envelope"))
        envelope_range = uncertainty.get("envelope") or {}
        rate_low = _number(envelope_range.get("lower_90_percentile_1_per_hour"))
        rate_high = _number(envelope_range.get("upper_90_percentile_1_per_hour"))
        holdout_mae = _number(fit.get("holdout_mae_c"))
        baseline_mae = _number(fit.get("persistence_baseline_mae_c"))
        holdout_better = (
            holdout_mae is not None and baseline_mae is not None
            and holdout_mae < baseline_mae
        )
        gates = {
            "configured_positive_envelope_anchor": envelope_g is not None and envelope_g > 0,
            "positive_identifiable_envelope_rate": envelope_rate is not None and envelope_rate > 1e-8,
            "bootstrap_range_available": (
                rate_low is not None and rate_high is not None
                and rate_low > 0 and rate_high >= rate_low
                and int(envelope_range.get("successful_replicates") or 0) >= 40
            ),
            "chronological_holdout_beats_persistence": holdout_better,
            "fit_has_training_and_holdout": (
                int(fit.get("training_intervals") or 0) >= 8
                and int(fit.get("holdout_intervals") or 0) >= 4
            ),
        }
        capacity = capacity_low = capacity_high = None
        conductances: dict[str, Any] = {}
        if all(gates.values()):
            capacity = envelope_g / envelope_rate
            capacity_low = envelope_g / rate_high
            capacity_high = envelope_g / rate_low
            for path, rate_value in rates.items():
                rate = _number(rate_value)
                if rate is None or rate < 0:
                    continue
                path_range = uncertainty.get(path) or {}
                path_low = _number(path_range.get("lower_90_percentile_1_per_hour"))
                path_high = _number(path_range.get("upper_90_percentile_1_per_hour"))
                conductances[path] = {
                    "conductance_w_per_k": round(rate * capacity, 3),
                    "conditional_range_w_per_k": (
                        {
                            "lower": round(path_low * capacity_low, 3),
                            "upper": round(path_high * capacity_high, 3),
                        }
                        if path_low is not None and path_high is not None
                        and path_low >= 0 and path_high >= path_low else None
                    ),
                    "source": (
                        "configured_envelope_conductance_anchor_times_identified_normalized_rate"
                    ),
                }
            identified += 1
        results[room_id] = {
            "status": (
                "anchored_absolute_estimate_provisional"
                if capacity is not None else "absolute_parameters_withheld"
            ),
            "effective_room_capacity_wh_per_k": round(capacity, 2) if capacity is not None else None,
            "effective_room_capacity_range_wh_per_k": (
                {"lower": round(capacity_low, 2), "upper": round(capacity_high, 2)}
                if capacity_low is not None and capacity_high is not None else None
            ),
            "absolute_conductances": conductances,
            "envelope_anchor_w_per_k": envelope_g,
            "normalized_rates_per_hour": rates or None,
            "gates": gates,
            "holdout_mae_c": holdout_mae,
            "persistence_baseline_mae_c": baseline_mae,
            "training_intervals": int(fit.get("training_intervals") or 0),
            "holdout_intervals": int(fit.get("holdout_intervals") or 0),
            "interpretation": (
                "Conditional absolute RC estimate anchored to the configured envelope conductance. "
                "Bootstrap ranges propagate normalized-rate resampling only; they exclude uncertainty "
                "in the configured anchor, hidden gains and model structure. Not yet promoted for simulation."
                if capacity is not None else
                "Insufficient independent excitation, envelope anchor or holdout evidence. No capacity "
                "or absolute conductance is inferred."
            ),
        }
    return {
        "status": (
            "provisional_absolute_room_parameters_available"
            if identified else "collecting_evidence_absolute_parameters_withheld"
        ),
        "method": "configured_envelope_anchor_times_multizone_normalized_G_over_C",
        "rooms_with_provisional_absolute_parameters": identified,
        "configured_rooms": len(rooms),
        "rooms": results,
        "ufh_slab_capacities": {
            "ground_floor": {
                "capacity_wh_per_k": None,
                "status": "withheld_no_independent_slab_temperature_and_heat_input",
            },
            "upstairs": {
                "capacity_wh_per_k": None,
                "status": "withheld_no_independent_slab_temperature_and_heat_input",
            },
        },
        "solver_parameter_promotion": "withheld_pending_independent_physical_validation",
        "limitations": [
            "Room capacity is conditional on the configured envelope conductance being accurate.",
            "Normalized free-coasting rates may include unmodelled coupling and residual gains.",
            "Bootstrap ranges do not include anchor, sensor-bias or model-structure uncertainty.",
            "UFH slab capacities are not inferred from pump timing or room-air response alone.",
        ],
    }
