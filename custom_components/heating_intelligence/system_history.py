"""Compact persistent time-series snapshots for the system dashboard."""
from __future__ import annotations
from datetime import datetime, timedelta
import math
SAMPLE_INTERVAL_SECONDS = 300
RETENTION_DAYS = 30
MAX_SAMPLES = RETENTION_DAYS * 24 * 60
def _finite(value):
    try:
        number = float(value)
        return round(number, 2) if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
def record_system_sample(history: dict, now: datetime, snapshot: dict) -> list[dict]:
    """Store one compact system snapshot per minute and retain 30 days."""
    samples = history.setdefault("system_samples", [])
    if samples:
        try:
            previous = datetime.fromisoformat(samples[-1]["time"])
            if (now - previous).total_seconds() < SAMPLE_INTERVAL_SECONDS:
                return samples
        except (KeyError, TypeError, ValueError):
            pass
    sample = {
        "time": now.isoformat(),
        "thermal_power_w": _finite(snapshot.get("thermal_power_w")),
        "pump_power_w": _finite(snapshot.get("pump_power_w")),
        "boiler_power_w": _finite(snapshot.get("boiler_power_w")),
        "active_flow_l_min": _finite(snapshot.get("active_flow_l_min")),
        "water_supply_c": _finite(snapshot.get("water_supply_c")),
        "water_return_c": _finite(snapshot.get("water_return_c")),
        "indoor_average_c": _finite(snapshot.get("indoor_average_c")),
        "outdoor_c": _finite(snapshot.get("outdoor_c")),
        "attic_c": _finite(snapshot.get("attic_c")),
        "burner": snapshot.get("burner"),
        "modulation": _finite(snapshot.get("modulation")),
        "circuits": {str(k): {"name": str(v.get("name", k)), "active": bool(v.get("flow_active")), "flow_l_min": _finite(v.get("active_configured_flow_l_min")), "thermal_power_w": _finite(v.get("estimated_thermal_power_w")), "pump_id": v.get("pump_id")} for k, v in (snapshot.get("circuits") or {}).items()},
        "rooms": {str(k): {"name": str(v.get("name", k)), "temperature_c": _finite(v.get("temperature")), "thermal_power_w": _finite(v.get("estimated_thermal_power_w")), "demand": bool(v.get("demand"))} for k, v in (snapshot.get("rooms") or {}).items()},
    }
    samples.append(sample)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    retained = []
    for item in samples:
        try:
            if datetime.fromisoformat(item["time"]) >= cutoff:
                retained.append(item)
        except (KeyError, TypeError, ValueError):
            continue
    history["system_samples"] = retained[-MAX_SAMPLES:]
    return history["system_samples"]
