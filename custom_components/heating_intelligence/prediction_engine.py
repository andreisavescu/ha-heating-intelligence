"""Explainable short-horizon room and daily energy forecasts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Any

COMFORT_TARGET_C = 21.0
MIN_SAMPLES = 6
MIN_SPAN_SECONDS = 3600
MAX_TREND_AGE_SECONDS = 1200


def _regression(samples: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Fit a linear temperature trend to recent, valid observations."""
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=3)
    points = []
    for sample in samples:
        try:
            stamp = datetime.fromisoformat(sample["time"]).astimezone(timezone.utc)
            temp = float(sample["temperature"])
        except (KeyError, TypeError, ValueError):
            continue
        if stamp >= cutoff and not sample.get("window_open"):
            points.append((stamp.timestamp(), temp))
    points.sort()
    if len(points) < MIN_SAMPLES:
        return None
    span = points[-1][0] - points[0][0]
    age = now.astimezone(timezone.utc).timestamp() - points[-1][0]
    if span < MIN_SPAN_SECONDS or age > MAX_TREND_AGE_SECONDS:
        return None
    x0 = points[0][0]
    xs = [(x - x0) / 3600 for x, _ in points]
    ys = [y for _, y in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    variance = sum((x - mx) ** 2 for x in xs)
    if variance <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / variance
    intercept = my - slope * mx
    residual = sqrt(sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys)) / len(xs))
    # Prevent implausible extrapolation from sensor spikes or brief transients.
    slope = max(-1.5, min(1.5, slope))
    r2_denominator = sum((y - my) ** 2 for y in ys)
    r2 = max(0.0, 1 - (residual ** 2 * len(ys)) / r2_denominator) if r2_denominator else 0.0
    coverage = min(1.0, span / 10800)
    confidence = round(100 * min(1.0, len(points) / 12) * coverage * (0.5 + 0.5 * r2))
    return {
        "slope_c_per_hour": round(slope, 3),
        "residual_c": round(residual, 3),
        "confidence": confidence,
        "sample_count": len(points),
        "span_minutes": round(span / 60),
        "latest_temperature": ys[-1],
        "latest_time": datetime.fromtimestamp(points[-1][0], timezone.utc).isoformat(),
    }


def room_forecast(room: dict[str, Any], now: datetime, comfort_target_c: float = COMFORT_TARGET_C) -> dict[str, Any]:
    """Forecast temperature and comfort crossings from the recent observed trend."""
    trend = _regression(room.get("temperature_samples", []), now)
    current = room.get("temperature")
    base = {
        "status": "insufficient_data",
        "confidence": 0,
        "comfort_target_c": comfort_target_c,
        "current_temperature": current,
        "temperature_1h": None,
        "temperature_3h": None,
        "temperature_6h": None,
        "time_to_comfort_minutes": None,
        "time_below_comfort_minutes": None,
        "trend_c_per_hour": None,
        "sample_count": 0,
        "note": "Collecting at least 6 recent samples spanning 1 hour with no open-window observations.",
    }
    if trend is None or current is None:
        return base
    slope = trend["slope_c_per_hour"]
    base.update({
        "status": "available",
        "confidence": trend["confidence"],
        "trend_c_per_hour": slope,
        "sample_count": trend["sample_count"],
        "temperature_1h": round(current + slope, 2),
        "temperature_3h": round(current + slope * 3, 2),
        "temperature_6h": round(current + slope * 6, 2),
        "note": "Short-term linear trend extrapolation; assumes recent conditions continue.",
    })
    if room.get("demand") and current < comfort_target_c and slope > 0.03:
        base["time_to_comfort_minutes"] = round((comfort_target_c - current) / slope * 60)
    if not room.get("demand") and current > comfort_target_c and slope < -0.03:
        base["time_below_comfort_minutes"] = round((current - comfort_target_c) / abs(slope) * 60)
    return base


def energy_forecast(efficiency: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Project end-of-day meter use using a conservative elapsed-day run rate."""
    seconds = now.hour * 3600 + now.minute * 60 + now.second
    elapsed_hours = seconds / 3600
    result = {"status": "insufficient_data", "confidence": 0, "gas_kwh": None,
              "electricity_kwh": None, "elapsed_hours": round(elapsed_hours, 2),
              "note": "At least 2 hours of today's meter history are required."}
    if elapsed_hours < 2:
        return result
    gas = efficiency.get("gas_energy_today_kwh")
    electricity = efficiency.get("electricity_energy_today_kwh")
    if gas is None and electricity is None:
        return result
    remaining_fraction = 24 / elapsed_hours
    result.update({
        "status": "available",
        "confidence": min(55, round(20 + elapsed_hours / 24 * 35)),
        "gas_kwh": round(gas * remaining_fraction, 2) if gas is not None else None,
        "electricity_kwh": round(electricity * remaining_fraction, 2) if electricity is not None else None,
        "note": "Run-rate projection only; weather, solar gains and occupancy changes are not yet modelled.",
    })
    return result


def build_predictions(rooms: dict[str, dict[str, Any]], efficiency: dict[str, Any], now: datetime, comfort_target_c: float = COMFORT_TARGET_C) -> dict[str, Any]:
    """Build all currently supported forecasts."""
    room_results = {room_id: room_forecast(room, now, comfort_target_c) for room_id, room in rooms.items()}
    available = [item["confidence"] for item in room_results.values() if item["status"] == "available"]
    return {
        "comfort_target_c": comfort_target_c,
        "rooms": room_results,
        "energy": energy_forecast(efficiency, now),
        "rooms_with_forecasts": len(available),
        "average_confidence": round(sum(available) / len(available)) if available else None,
        "generated_at": now.isoformat(),
    }
