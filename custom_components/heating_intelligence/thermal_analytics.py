"""Room temperature and window-impact analytics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

RETENTION_DAYS = 400
SAMPLE_INTERVAL_SECONDS = 600
MAX_SAMPLES = 60000


def record_temperature_sample(
    room: dict[str, Any], now: datetime, temperature: float | None,
    demand: bool, window_open: bool, external_heating: bool = False,
    window_state_known: bool = True, demand_state_known: bool = True,
) -> None:
    """Retain sparse raw observations for later thermal analysis."""
    if temperature is None:
        return
    samples = room.setdefault("temperature_samples", [])
    stamp = now.astimezone(timezone.utc)
    last = samples[-1] if samples else None
    last_time = datetime.fromisoformat(last["time"]) if last else None
    changed_enough = last is None or abs(float(last["temperature"]) - temperature) >= 0.15
    due = last_time is None or (stamp - last_time).total_seconds() >= SAMPLE_INTERVAL_SECONDS
    if changed_enough or due or (last and (last["demand"] != demand or last["window_open"] != window_open or last.get("external_heating", False) != external_heating or last.get("window_state_known", True) != window_state_known or last.get("demand_state_known", True) != demand_state_known)):
        samples.append({
            "time": stamp.isoformat(),
            "temperature": round(temperature, 3),
            "demand": bool(demand),
            "window_open": bool(window_open),
            "external_heating": bool(external_heating),
            "window_state_known": bool(window_state_known),
            "demand_state_known": bool(demand_state_known),
        })
    cutoff = stamp - timedelta(days=RETENTION_DAYS)
    room["temperature_samples"] = [
        item for item in samples
        if datetime.fromisoformat(item["time"]).astimezone(timezone.utc) >= cutoff
    ][-MAX_SAMPLES:]


def _samples(room: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=RETENTION_DAYS)
    valid = []
    for item in room.get("temperature_samples", []):
        try:
            stamp = datetime.fromisoformat(item["time"]).astimezone(timezone.utc)
            value = float(item["temperature"])
        except (KeyError, TypeError, ValueError):
            continue
        if stamp >= cutoff:
            valid.append({**item, "_dt": stamp, "_temp": value})
    return valid


def thermal_statistics(room: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Calculate temperature trends, heating response and heat retention."""
    samples = _samples(room, now)
    now_utc = now.astimezone(timezone.utc)
    latest = samples[-1] if samples else None

    def change_over(seconds: int) -> float | None:
        if not latest:
            return None
        target = now_utc - timedelta(seconds=seconds)
        previous = next((item for item in reversed(samples[:-1]) if item["_dt"] <= target), None)
        if previous is None:
            return None
        return round(latest["_temp"] - previous["_temp"], 2)

    # Heating response: time from the first sampled demand observation to a
    # 0.5 C rise, using the preceding sample as the cycle's baseline.
    responses: list[float] = []
    index = 0
    while index < len(samples):
        item = samples[index]
        if item["demand"] and (index == 0 or not samples[index - 1]["demand"]):
            baseline = samples[index - 1] if index else item
            target = baseline["_temp"] + 0.5
            cursor = index + 1
            while cursor < len(samples) and samples[cursor]["demand"]:
                later = samples[cursor]
                if later["_temp"] >= target:
                    responses.append((later["_dt"] - item["_dt"]).total_seconds() / 60)
                    break
                cursor += 1
            index = cursor
        else:
            index += 1
    response = round(sum(responses[-20:]) / len(responses[-20:]), 1) if responses else None

    # Cooling rate uses the most recent continuous period with neither heat
    # demand nor an open window, avoiding obvious ventilation contamination.
    cooling_segment = []
    for item in reversed(samples):
        if item["demand"] or item["window_open"]:
            break
        cooling_segment.append(item)
    cooling_rate = None
    if len(cooling_segment) >= 2:
        end = cooling_segment[0]
        start = next((item for item in cooling_segment[1:]
                      if (end["_dt"] - item["_dt"]).total_seconds() >= 1800), None)
        if start:
            hours = (end["_dt"] - start["_dt"]).total_seconds() / 3600
            rate = (start["_temp"] - end["_temp"]) / hours
            if rate > 0:
                cooling_rate = round(rate, 3)

    return {
        "temperature_change_1h": change_over(3600),
        "temperature_change_6h": change_over(21600),
        "temperature_change_24h": change_over(86400),
        "heating_response_minutes": response,
        "heating_response_samples": len(responses),
        "cooling_rate_c_per_hour": cooling_rate,
        "heat_retention_hours_per_degree": round(1 / cooling_rate, 2) if cooling_rate else None,
        "temperature_sample_count": len(samples),
        "temperature_last_sample": latest["time"] if latest else None,
    }
