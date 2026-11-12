"""Advanced heating-water, solar, forecast-validation and seasonal analytics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _correlation(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 5:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = mean(xs), mean(ys)
    vx, vy = sum((x-mx)**2 for x in xs), sum((y-my)**2 for y in ys)
    if not vx or not vy:
        return None
    return round(sum((x-mx)*(y-my) for x,y in pairs) / (vx*vy)**0.5, 3)


def update_advanced_history(
    history: dict[str, Any],
    flow_c: float | None,
    return_c: float | None,
    rooms: dict[str, dict[str, Any]],
    predictions: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Persist sparse water observations and validate prior room forecasts."""
    stamp = now.astimezone(timezone.utc)
    water = history.setdefault("water_samples", [])
    if flow_c is not None or return_c is not None:
        if not water or (stamp - datetime.fromisoformat(water[-1]["time"]).astimezone(timezone.utc)).total_seconds() >= 300:
            water.append({"time": stamp.isoformat(), "flow_c": flow_c, "return_c": return_c,
                          "delta_t_c": round(flow_c-return_c, 2) if flow_c is not None and return_c is not None else None})
    cutoff = stamp - timedelta(days=400)
    history["water_samples"] = [
        x for x in water if datetime.fromisoformat(x["time"]).astimezone(timezone.utc) >= cutoff
    ][-50000:]
    forecasts = history.setdefault("forecast_validation", {})
    for room_id, room in rooms.items():
        bucket = forecasts.setdefault(room_id, {"pending": [], "errors": []})
        samples = room.get("temperature_history", [])
        remaining = []
        for forecast in bucket.get("pending", []):
            target = datetime.fromisoformat(forecast["target_time"]).astimezone(timezone.utc)
            observed = []
            for sample in samples:
                try:
                    sample_time = datetime.fromisoformat(sample["time"]).astimezone(timezone.utc)
                    temp = _number(sample.get("temperature"))
                except (TypeError, ValueError):
                    continue
                if temp is not None and abs((sample_time-target).total_seconds()) <= 900:
                    observed.append((abs((sample_time-target).total_seconds()), temp))
            if observed:
                actual = min(observed)[1]
                bucket.setdefault("errors", []).append({
                    "time": stamp.isoformat(), "horizon_h": forecast["horizon_h"],
                    "predicted_c": forecast["predicted_c"], "actual_c": actual,
                    "absolute_error_c": round(abs(actual-forecast["predicted_c"]), 3),
                })
            elif stamp <= target + timedelta(minutes=15):
                remaining.append(forecast)
        bucket["pending"] = remaining
        last_created = bucket.get("last_created")
        last_dt = datetime.fromisoformat(last_created).astimezone(timezone.utc) if last_created else None
        prediction = predictions.get("rooms", {}).get(room_id, {})
        if prediction.get("status") == "available" and (last_dt is None or stamp-last_dt >= timedelta(hours=1)):
            for horizon in (1, 3, 6, 12):
                value = _number(prediction.get(f"temperature_{horizon}h"))
                if value is not None:
                    bucket["pending"].append({
                        "created": stamp.isoformat(),
                        "target_time": (stamp+timedelta(hours=horizon)).isoformat(),
                        "horizon_h": horizon, "predicted_c": value,
                    })
            bucket["last_created"] = stamp.isoformat()
        bucket["errors"] = bucket.get("errors", [])[-500:]
        bucket["pending"] = bucket.get("pending", [])[-100:]

    errors = [item for room in forecasts.values() for item in room.get("errors", [])]
    validation = {}
    for horizon in (1, 3, 6, 12):
        rows = [x for x in errors if x.get("horizon_h") == horizon]
        validation[str(horizon)] = {
            "sample_count": len(rows),
            "mae_c": round(mean(x["absolute_error_c"] for x in rows), 2) if rows else None,
            "latest": rows[-1] if rows else None,
        }

    daily = history.get("efficiency_daily", {})
    monthly: dict[str, dict[str, float]] = {}
    solar_pairs = []
    water_cutoff = stamp - timedelta(hours=24)
    water_day = [x for x in history["water_samples"] if x.get("delta_t_c") is not None and datetime.fromisoformat(x["time"]).astimezone(timezone.utc) >= water_cutoff]
    for date, day in sorted(daily.items()):
        month = date[:7]
        item = monthly.setdefault(month, {"gas_kwh": 0.0, "electricity_kwh": 0.0, "hdd": 0.0, "solar_sum": 0.0, "solar_count": 0, "days": 0})
        item["gas_kwh"] += float(day.get("gas_kwh", 0) or 0)
        item["electricity_kwh"] += float(day.get("electricity_kwh", 0) or 0)
        item["days"] += 1
        if day.get("outdoor_count"):
            hdd = max(0.0, 18.0-float(day["outdoor_sum"])/day["outdoor_count"])
            item["hdd"] += hdd
            if day.get("solar_count") and hdd > 0:
                solar = float(day["solar_sum"])/day["solar_count"]
                solar_pairs.append((solar, float(day.get("gas_kwh", 0))/hdd))
        if day.get("solar_count"):
            item["solar_sum"] += float(day["solar_sum"])
            item["solar_count"] += int(day["solar_count"])
    solar = {
        "status": "available" if len(solar_pairs) >= 10 else "collecting_data",
        "days_used": len(solar_pairs),
        "solar_vs_weather_normalised_gas_correlation": _correlation(solar_pairs),
        "high_solar_days": len([x for x in solar_pairs if x[0] >= sorted(p[0] for p in solar_pairs)[len(solar_pairs)//2]]),
        "low_solar_days": len([x for x in solar_pairs if x[0] < sorted(p[0] for p in solar_pairs)[len(solar_pairs)//2]]),
        "note": "Exploratory association between daily solar radiation and gas per heating degree day. It is not a measured solar heat-gain quantity; occupancy, shading, thermal storage and weather remain confounders.",
    }
    if len(solar_pairs) >= 10:
        ordered = sorted(solar_pairs, key=lambda x: x[0])
        split = len(ordered)//2
        solar["low_solar_gas_per_hdd"] = round(mean(x[1] for x in ordered[:split]), 3)
        solar["high_solar_gas_per_hdd"] = round(mean(x[1] for x in ordered[split:]), 3)
        low = solar["low_solar_gas_per_hdd"]
        solar["high_solar_reduction_percent"] = round((low-solar["high_solar_gas_per_hdd"])/low*100, 1) if low else None

    if water_day:
        values = [x["delta_t_c"] for x in water_day]
        flow_values = [x["flow_c"] for x in water_day if x.get("flow_c") is not None]
        return_values = [x["return_c"] for x in water_day if x.get("return_c") is not None]
        water_stats = {
            "status": "available",
            "flow_temperature_c": round(flow_values[-1], 2) if flow_values else None,
            "return_temperature_c": round(return_values[-1], 2) if return_values else None,
            "water_delta_t_c": round(values[-1], 2),
            "average_delta_t_24h_c": round(mean(values), 2),
            "minimum_delta_t_24h_c": round(min(values), 2),
            "maximum_delta_t_24h_c": round(max(values), 2),
            "samples_24h": len(values),
        }
    else:
        water_stats = {"status": "collecting_data", "flow_temperature_c": flow_c, "return_temperature_c": return_c, "water_delta_t_c": round(flow_c-return_c, 2) if flow_c is not None and return_c is not None else None, "samples_24h": 0}

    anomalies = []
    if water_stats.get("water_delta_t_c") is not None:
        delta = water_stats["water_delta_t_c"]
        if delta < 2:
            anomalies.append({"id": "water_delta_t_low", "severity": "info", "title": "Low heating-water ΔT", "detail": f"Current flow/return difference is {delta:.1f} °C. Compare it with normal operation and pump flow; this is an observation, not a fault diagnosis."})
        elif delta > 15:
            anomalies.append({"id": "water_delta_t_high", "severity": "warning", "title": "High heating-water ΔT", "detail": f"Current flow/return difference is {delta:.1f} °C. Compare it with your usual operating range and verify sensor placement."})
    from statistics import median
    dated = sorted(daily.items())
    if len(dated) >= 11:
        current_date, current_day = dated[-1]
        for metric, label in (("gas_kwh", "Gas"), ("electricity_kwh", "Heating electricity")):
            baseline = [float(day.get(metric, 0) or 0) for _, day in dated[-31:-1]]
            if len(baseline) < 10:
                continue
            center = median(baseline)
            mad = median([abs(value-center) for value in baseline])
            value = float(current_day.get(metric, 0) or 0)
            threshold = max(center * 1.5, center + 3 * 1.4826 * mad)
            if center > 0 and value > threshold:
                anomalies.append({"id": f"robust_{metric}_outlier", "severity": "warning", "title": f"{label} is unusually high", "detail": f"{current_date}: {value:.2f} kWh versus a historical median of {center:.2f} kWh across {len(baseline)} days. Weather, occupancy and partial-day readings can affect this comparison."})

    seasons = {}
    for month, values in sorted(monthly.items()):
        m = int(month[5:7])
        season = "winter" if m in (12,1,2) else "spring" if m in (3,4,5) else "summer" if m in (6,7,8) else "autumn"
        entry = seasons.setdefault(season, {"gas_kwh": 0.0, "electricity_kwh": 0.0, "hdd": 0.0, "days": 0})
        for key in ("gas_kwh", "electricity_kwh", "hdd", "days"):
            entry[key] += values[key]
    return {
        "water": water_stats,
        "solar": solar,
        "forecast_validation": validation,
        "anomalies": anomalies,
        "seasonal": {"months": {k: {**v, "gas_kwh": round(v["gas_kwh"], 2), "electricity_kwh": round(v["electricity_kwh"], 2), "hdd": round(v["hdd"], 2)} for k,v in monthly.items()}, "seasons": seasons},
    }
