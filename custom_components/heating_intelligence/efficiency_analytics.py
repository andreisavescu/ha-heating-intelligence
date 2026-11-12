"""Whole-house energy and weather analytics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Any

RETENTION_DAYS = 400
DEFAULT_GAS_KWH_PER_M3 = 10.55


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _energy_kwh(value: float, unit: str | None, gas: bool, gas_factor: float) -> float:
    normalized = (unit or "").lower().replace(" ", "")
    if normalized in ("wh",):
        return value / 1000
    if normalized in ("mwh",):
        return value * 1000
    if gas and normalized in ("m³", "m3", "㎥"):
        return value * gas_factor
    return value


def _correlation(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if not vx or not vy:
        return None
    return round(sum((x - mx) * (y - my) for x, y in pairs) / sqrt(vx * vy), 3)


def update_efficiency_history(
    history: dict[str, Any], readings: dict[str, dict[str, Any] | None],
    now: datetime, elapsed_seconds: float, continuity_gap: bool,
    gas_factor: float = DEFAULT_GAS_KWH_PER_M3,
) -> dict[str, Any]:
    """Accumulate daily meter deltas and sparse raw environmental observations."""
    date_key = now.date().isoformat()
    days = history.setdefault("efficiency_daily", {})
    day = days.setdefault(date_key, {
        "gas_kwh": 0.0, "electricity_kwh": 0.0,
        "outdoor_sum": 0.0, "outdoor_count": 0,
        "solar_sum": 0.0, "solar_count": 0,
        "delta_t_sum": 0.0, "delta_t_count": 0,
        "indoor_sum": 0.0, "indoor_count": 0,
    })
    previous = history.setdefault("efficiency_last_readings", {})
    for key, gas in (("gas", True), ("electricity", False)):
        reading = readings.get(key)
        value = _number(reading.get("value")) if reading else None
        if value is None:
            continue
        old = _number(previous.get(key, {}).get("value"))
        unit = reading.get("unit")
        if old is not None and value >= old:
            delta = value - old
            day["gas_kwh" if gas else "electricity_kwh"] += _energy_kwh(delta, unit, gas, gas_factor)
        # A meter reset establishes a new baseline, without negative energy.
        previous[key] = {"value": value, "unit": unit}

    env = {key: _number((readings.get(key) or {}).get("value"))
           for key in ("outdoor_temperature", "solar_radiation", "delta_t", "indoor_temperature")}
    for key, field in (("outdoor_temperature", "outdoor"), ("solar_radiation", "solar"),
                       ("delta_t", "delta_t"), ("indoor_temperature", "indoor")):
        value = env[key]
        if value is not None:
            day[field + "_sum"] += value
            day[field + "_count"] += 1

    samples = history.setdefault("environment_samples", [])
    last_time = datetime.fromisoformat(samples[-1]["time"]) if samples else None
    stamp = now.astimezone(timezone.utc)
    if not samples or last_time is None or (stamp - last_time.astimezone(timezone.utc)).total_seconds() >= 900:
        samples.append({"time": stamp.isoformat(), **env})
    cutoff = stamp - timedelta(days=RETENTION_DAYS)
    history["environment_samples"] = [
        item for item in samples
        if datetime.fromisoformat(item["time"]).astimezone(timezone.utc) >= cutoff
    ][-40000:]

    # Keep daily aggregates for the same retention horizon as room observations.
    history["efficiency_daily"] = {
        key: value for key, value in days.items()
        if (now.date() - datetime.fromisoformat(key).date()).days <= RETENTION_DAYS
    }
    return day


def efficiency_statistics(history: dict[str, Any], now: datetime, base_temperature: float = 18.0) -> dict[str, Any]:
    days = history.get("efficiency_daily", {})
    today = days.get(now.date().isoformat(), {})
    outdoor = today.get("outdoor_sum", 0) / today["outdoor_count"] if today.get("outdoor_count") else None
    solar = today.get("solar_sum", 0) / today["solar_count"] if today.get("solar_count") else None
    delta_t = today.get("delta_t_sum", 0) / today["delta_t_count"] if today.get("delta_t_count") else None
    hdd = max(0.0, base_temperature - outdoor) if outdoor is not None else None
    gas = round(today.get("gas_kwh", 0.0), 3)
    electricity = round(today.get("electricity_kwh", 0.0), 3)
    completed = [v for k, v in sorted(days.items()) if k <= now.date().isoformat()]
    gas_hdd = [(max(0.0, base_temperature - v["outdoor_sum"] / v["outdoor_count"]), v["gas_kwh"])
               for v in completed if v.get("outdoor_count") and v.get("gas_kwh") is not None]
    gas_delta = [(v["delta_t_sum"] / v["delta_t_count"], v["gas_kwh"])
                 for v in completed if v.get("delta_t_count") and v.get("gas_kwh") is not None]
    gas_solar = [(v["solar_sum"] / v["solar_count"], v["gas_kwh"])
                 for v in completed if v.get("solar_count") and v.get("gas_kwh") is not None]
    return {
        "outdoor_temperature": round(outdoor, 2) if outdoor is not None else None,
        "solar_radiation_average": round(solar, 1) if solar is not None else None,
        "delta_t_average": round(delta_t, 2) if delta_t is not None else None,
        "heating_degree_days": round(hdd, 3) if hdd is not None else None,
        "gas_energy_today_kwh": gas,
        "electricity_energy_today_kwh": electricity,
        "gas_kwh_per_degree_day": round(gas / hdd, 3) if hdd and hdd > 0 else None,
        "electricity_kwh_per_degree_day": round(electricity / hdd, 3) if hdd and hdd > 0 else None,
        "days_with_data": len(completed),
        "gas_hdd_correlation": _correlation(gas_hdd),
        "gas_delta_t_correlation": _correlation(gas_delta),
        "gas_solar_correlation": _correlation(gas_solar),
    }
