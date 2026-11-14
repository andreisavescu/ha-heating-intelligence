"""Electrical-signature state machine for manifold valve and pump stages.

The detector is deliberately conservative: it labels inferred electrical stages,
not measured water flow. It learns each manifold's idle baseline and the first
valve-actuator level, then requires a sustained higher level before declaring
the pump electrically active.
"""
from __future__ import annotations

from datetime import datetime


def update_pump_stage(history: dict, power_w: float | None, now: datetime) -> dict:
    """Update one manifold's inferred valve/pump stage from a power sample."""
    history.setdefault("stage", "awaiting_idle_baseline")
    history.setdefault("idle_baseline_w", None)
    history.setdefault("valve_level_w", None)
    history.setdefault("valve_opened_at", None)
    history.setdefault("pump_started_at", None)
    history.setdefault("pump_stopped_at", None)
    history.setdefault("above_pump_threshold_since", None)
    history.setdefault("below_idle_threshold_since", None)

    if power_w is None:
        return _snapshot(history)

    baseline = history.get("idle_baseline_w")
    stage = history["stage"]
    stamp = now.isoformat()

    # Learn a per-device idle level only while the detector considers the
    # manifold idle. A slow EMA tolerates small standby-power variations.
    if stage == "awaiting_idle_baseline":
        # Avoid learning an active pump/valve load as standby after startup.
        if power_w <= 5.0:
            history["idle_baseline_w"] = max(0.0, power_w)
            history["stage"] = "idle"
    elif stage == "idle":
        if baseline is None:
            history["stage"] = "awaiting_idle_baseline"
        else:
            history["idle_baseline_w"] = max(0.0, baseline * 0.9 + power_w * 0.1)
            baseline = history["idle_baseline_w"]
            rise = max(4.0, baseline * 1.5)
            if power_w >= baseline + rise:
                history["stage"] = "valve_opening"
                history["valve_opened_at"] = stamp
                history["valve_level_w"] = power_w
                history["above_pump_threshold_since"] = None
                history["below_idle_threshold_since"] = None
    elif stage == "valve_opening":
        valve_level = history.get("valve_level_w") or power_w
        # Pump operation should be a second, materially higher and sustained
        # electrical level than the initial valve-actuator signature.
        pump_threshold = max(
            (baseline or 0.0) + 10.0,
            (baseline or 0.0) + (valve_level - (baseline or 0.0)) * 1.5,
        )
        if power_w >= pump_threshold:
            history["above_pump_threshold_since"] = (
                history.get("above_pump_threshold_since") or stamp
            )
            since = datetime.fromisoformat(history["above_pump_threshold_since"])
            if (now - since).total_seconds() >= 20:
                history["stage"] = "pump_running"
                history["pump_started_at"] = history["above_pump_threshold_since"]
                history["above_pump_threshold_since"] = None
        else:
            history["above_pump_threshold_since"] = None
            if power_w > valve_level:
                history["valve_level_w"] = power_w
            # A short actuator pulse can finish without a pump start.
            if power_w <= (baseline or 0.0) + max(2.0, (baseline or 0.0) * 0.5):
                history["stage"] = "idle"
                history["valve_level_w"] = None
                history["valve_opened_at"] = None
    elif stage == "pump_running":
        stop_threshold = (baseline or 0.0) + max(
            3.0, ((history.get("valve_level_w") or baseline or 0.0) - (baseline or 0.0)) * 0.5
        )
        if power_w <= stop_threshold:
            history["below_idle_threshold_since"] = (
                history.get("below_idle_threshold_since") or stamp
            )
            since = datetime.fromisoformat(history["below_idle_threshold_since"])
            if (now - since).total_seconds() >= 30:
                history["stage"] = "idle"
                history["pump_stopped_at"] = history["below_idle_threshold_since"]
                history["valve_level_w"] = None
                history["valve_opened_at"] = None
                history["pump_started_at"] = None
                history["below_idle_threshold_since"] = None
        else:
            history["below_idle_threshold_since"] = None

    return _snapshot(history)


def _snapshot(history: dict) -> dict:
    """Return stable, serialisable stage diagnostics."""
    return {
        "stage": history.get("stage", "idle"),
        "valve_opened_at": history.get("valve_opened_at"),
        "pump_started_at": history.get("pump_started_at"),
        "pump_stopped_at": history.get("pump_stopped_at"),
        "idle_baseline_w": round(history["idle_baseline_w"], 2)
        if history.get("idle_baseline_w") is not None else None,
        "valve_level_w": round(history["valve_level_w"], 2)
        if history.get("valve_level_w") is not None else None,
        "detection_basis": "electrical_signature_estimate",
    }
