"""Rolling, self-learning room thermal-response model."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from statistics import median
from typing import Any

LEARNING_DAYS = 30
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 45
MIN_INTERVALS = 6
MIN_SPAN_HOURS = 2.0
HORIZONS_HOURS = (1, 3, 6, 12)


def _learn_mode(
    samples: list[dict[str, Any]], now: datetime, demand: bool,
    require_external_flag: bool = False,
) -> dict[str, Any]:
    """Learn only from adjacent samples in the same mode with windows closed."""
    cutoff = now.astimezone(timezone.utc) - timedelta(days=LEARNING_DAYS)
    points = []
    for item in samples:
        try:
            stamp = datetime.fromisoformat(item["time"]).astimezone(timezone.utc)
            temperature = float(item["temperature"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(temperature):
            continue
        if cutoff <= stamp <= now.astimezone(timezone.utc):
            external_heating = bool(item.get("external_heating", False)) or (require_external_flag and "external_heating" not in item)
            points.append((stamp, temperature, bool(item.get("demand")), bool(item.get("window_open")), external_heating, bool(item.get("window_state_known", False))))
    points.sort(key=lambda point: point[0])

    eligible = [point for point in points if point[2] == demand and not point[3] and not point[4] and point[5]]
    excluded_external = sum(1 for point in points if point[4])
    rates = []
    index = 0
    while index < len(points) - 1:
        first = points[index]
        first_valid = first[2] == demand and not first[3] and not first[4] and first[5]
        if not first_valid:
            index += 1
            continue
        paired = False
        cursor = index + 1
        while cursor < len(points):
            second = points[cursor]
            second_valid = second[2] == demand and not second[3] and not second[4] and second[5]
            if not second_valid:
                break
            minutes = (second[0] - first[0]).total_seconds() / 60
            if minutes > MAX_INTERVAL_MINUTES:
                break
            if minutes >= MIN_INTERVAL_MINUTES:
                rates.append((second[1] - first[1]) / (minutes / 60))
                index = cursor
                paired = True
                break
            cursor += 1
        if not paired:
            index += 1
    span = (eligible[-1][0] - eligible[0][0]).total_seconds() / 3600 if len(eligible) > 1 else 0
    if len(rates) < MIN_INTERVALS or span < MIN_SPAN_HOURS:
        return {
            "status": "learning",
            "confidence": 0,
            "sample_count": len(eligible),
            "valid_intervals": len(rates),
            "excluded_external_heating_samples": excluded_external,
            "span_hours": round(span, 1),
            "rate_c_per_hour": None,
            "noise_c_per_hour": None,
            "note": f"Needs at least {MIN_INTERVALS} valid non-overlapping intervals spanning {MIN_SPAN_HOURS:g} hours, with a configured and available window contact confirming closed windows.",
        }
    rate = median(rates)
    noise = median([abs(value - rate) for value in rates])
    confidence = round(100 * min(1, len(rates) / 36) * min(1, span / 24) * max(.15, 1 - noise / .35))
    return {
        "status": "available",
        "confidence": max(5, min(95, confidence)),
        "sample_count": len(eligible),
        "valid_intervals": len(rates),
        "excluded_external_heating_samples": excluded_external,
        "span_hours": round(span, 1),
        "rate_c_per_hour": round(rate, 3),
        "noise_c_per_hour": round(noise, 3),
        "note": "Median temperature-change rate from consecutive observations with a configured closed-window contact and no detected or unclassified AC heating in this mode, learned over a rolling 30-day window.",
    }


def build_room_thermal_model(
    room_history: dict[str, Any], now: datetime, current_temperature: float | None,
    demand: bool, ac_power_sensor_configured: bool = False,
    external_heating_active: bool = False, window_open: bool = False,
    window_state_known: bool = True,
) -> dict[str, Any]:
    """Learn heating/coasting rates from persisted room observations and forecast."""
    samples = room_history.get("temperature_samples", [])
    heating = _learn_mode(samples, now, True, ac_power_sensor_configured)
    coasting = _learn_mode(samples, now, False, ac_power_sensor_configured)
    selected = heating if demand else coasting
    result: dict[str, Any] = {
        "status": "external_heat_filter_active" if external_heating_active else "window_state_unknown" if not window_state_known else "window_open" if window_open else selected["status"] if current_temperature is not None else "waiting_for_temperature",
        "model": "rolling_median_thermal_response",
        "learning_window_days": LEARNING_DAYS,
        "mode": "heating" if demand else "coasting",
        "current_temperature": current_temperature,
        "heating": heating,
        "coasting": coasting,
        "confidence": selected["confidence"] if current_temperature is not None and not external_heating_active else 0,
        "trend_c_per_hour": selected["rate_c_per_hour"] if current_temperature is not None and not external_heating_active else None,
        "generated_at": now.isoformat(),
        "forecasts": {},
    }
    for hours in HORIZONS_HOURS:
        result["forecasts"][f"{hours}h"] = None
    if external_heating_active:
        result["note"] = "AC electrical input is above the active-use threshold, or its configured power sensor is unavailable; forecast withheld to avoid mixing heating systems."
        return result
    if not window_state_known:
        result["note"] = "A configured window contact is unavailable; forecast withheld because the closed-window condition cannot be verified."
        return result
    if window_open:
        result["note"] = "The room window is currently open; forecast withheld because the learned model assumes closed windows."
        return result
    if current_temperature is None:
        result["note"] = "Current room temperature is unavailable."
        return result
    if selected["status"] != "available":
        result["note"] = selected.get("note", "The current demand mode is still learning.")
        return result
    rate = selected["rate_c_per_hour"]
    noise = selected["noise_c_per_hour"]
    for hours in HORIZONS_HOURS:
        estimate = current_temperature + rate * hours
        margin = .15 + noise * hours * .65 + hours * .03
        result["forecasts"][f"{hours}h"] = {
            "temperature_c": round(estimate, 2),
            "low_c": round(estimate - margin, 2),
            "high_c": round(estimate + margin, 2),
        }
    result["note"] = (
        "Forecast assumes the current heating-demand mode and closed windows continue. "
        "Confidence reflects the number, duration and consistency of learned observations."
    )
    return result


def build_house_thermal_model(
    room_models: dict[str, dict[str, Any]], efficiency: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarise room learning alongside the existing measured house heat-loss model."""
    learned = [
        model for model in room_models.values()
        if model.get("heating", {}).get("status") == "available"
        or model.get("coasting", {}).get("status") == "available"
    ]
    forecastable = [model for model in room_models.values() if model.get("status") == "available"]
    confidence_values = [
        max(model.get("heating", {}).get("confidence", 0), model.get("coasting", {}).get("confidence", 0))
        for model in learned
    ]
    efficiency = efficiency or {}
    whole_house = {
        key: efficiency.get(key) for key in (
            "heat_loss_coefficient_w_k", "heat_loss_u_value_daily",
            "heat_loss_u_value_7d", "heat_loss_u_value_30d",
            "gas_kwh_per_degree_day",
        ) if efficiency.get(key) is not None
    }
    whole_house["weather_normalised_status"] = efficiency.get("weather_normalised", {}).get("status")
    whole_house["status"] = "available" if any(
        key in whole_house for key in ("heat_loss_u_value_daily", "heat_loss_u_value_7d", "heat_loss_u_value_30d")
    ) else "collecting_data"
    return {
        "status": "available" if learned else "learning",
        "rooms_modelled": len(learned),
        "rooms_forecastable": len(forecastable),
        "rooms_learning": len(room_models) - len(learned),
        "average_confidence": round(sum(confidence_values) / len(confidence_values)) if confidence_values else None,
        "room_models": room_models,
        "whole_house_observed_performance": whole_house,
        "interpretation": (
            "Room response rates are learned independently from historical temperature samples. "
            "They describe observed response, not a calibrated whole-building heat-loss model. "
            "Solar gains, occupancy, water temperature and thermal storage can affect the rates."
        ),
    }
