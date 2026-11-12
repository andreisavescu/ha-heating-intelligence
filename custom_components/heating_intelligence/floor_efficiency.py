"""Floor comparisons and weather-normalised efficiency analysis."""
from __future__ import annotations

from statistics import median
from typing import Any


def _valid_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def build_floor_performance(
    rooms: dict[str, dict[str, Any]], comfort_target_c: float = 21.0
) -> dict[str, dict[str, Any]]:
    """Compare configured floors using area-weighted room observations.

    Results are descriptive comparisons, not estimates of delivered heat or
    building heat-loss coefficients.
    """
    groups: dict[str, list[dict[str, Any]]] = {
        "ground_floor": [], "upstairs": []
    }
    for room in rooms.values():
        floor = room.get("floor", "unassigned")
        if floor in groups:
            groups[floor].append(room)

    result: dict[str, dict[str, Any]] = {}
    for floor, members in groups.items():
        configured_area = sum(
            area for room in members
            if (area := _valid_number(room.get("area_m2"))) is not None and area > 0
        )
        area_rooms = [
            room for room in members
            if (_valid_number(room.get("area_m2")) or 0) > 0
        ]
        observed_area = sum(float(room["area_m2"]) for room in area_rooms)
        area_coverage = len(area_rooms) / len(members) if members else 0.0

        def weighted(metric: str) -> float | None:
            pairs = [
                (float(room["area_m2"]), _valid_number(room.get(metric)))
                for room in area_rooms
            ]
            pairs = [(area, value) for area, value in pairs if value is not None]
            total = sum(area for area, _ in pairs)
            return round(sum(area * value for area, value in pairs) / total, 3) if total else None

        valid_temps = [
            (float(room["area_m2"]), _valid_number(room.get("temperature")))
            for room in area_rooms
        ]
        valid_temps = [(area, temp) for area, temp in valid_temps if temp is not None]
        temp_area = sum(area for area, _ in valid_temps)
        average_temp = (
            round(sum(area * temp for area, temp in valid_temps) / temp_area, 2)
            if temp_area else None
        )
        demand_pairs = [
            (float(room["area_m2"]), _valid_number(room.get("daily_demand_seconds")))
            for room in area_rooms
        ]
        demand_pairs = [(area, seconds) for area, seconds in demand_pairs if seconds is not None]
        demand_area = sum(area for area, _ in demand_pairs)
        area_weighted_demand_hours = (
            round(sum(area * seconds for area, seconds in demand_pairs) / demand_area / 3600, 2)
            if demand_area else None
        )
        result[floor] = {
            "room_count": len(members),
            "rooms_with_area": len(area_rooms),
            "area_coverage_percent": round(area_coverage * 100, 1),
            "configured_area_m2": round(configured_area, 2),
            "area_with_temperature_m2": round(temp_area, 2),
            "area_weighted_temperature_c": average_temp,
            "area_weighted_temperature_delta_c": (
                round(average_temp - comfort_target_c, 2) if average_temp is not None else None
            ),
            "area_weighted_cooling_c_per_hour": weighted("cooling_rate_c_per_hour"),
            "area_weighted_heating_response_minutes": weighted("heating_response_minutes"),
            "area_weighted_demand_hours_today": area_weighted_demand_hours,
            "note": (
                "Area-weighted comparison uses only rooms with configured area and valid metric data."
                if area_coverage < 1 else
                "Area-weighted comparison uses configured room areas."
            ),
        }
    return result


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Solve a small linear system with pivoted Gaussian elimination."""
    size = len(vector)
    augmented = [list(row) + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-9:
            return None
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [value / divisor for value in augmented[col]]
        for row in range(size):
            if row == col:
                continue
            factor = augmented[row][col]
            augmented[row] = [
                augmented[row][j] - factor * augmented[col][j]
                for j in range(size + 1)
            ]
    return [augmented[i][-1] for i in range(size)]


def weather_normalised_efficiency(
    daily: dict[str, dict[str, Any]], today: str, minimum_days: int = 21
) -> dict[str, Any]:
    """Fit a conservative daily gas model against HDD, delta-T and solar.

    The model uses completed days only. It reports residual-adjusted gas use
    at the dataset's median weather conditions; this is a comparative index,
    not a physical heat-loss or boiler-efficiency measurement.
    """
    records = []
    for date, values in sorted(daily.items()):
        if date >= today:
            continue
        try:
            if not values.get("outdoor_count") or not values.get("delta_t_count") or not values.get("solar_count"):
                continue
            outdoor = float(values["outdoor_sum"]) / float(values["outdoor_count"])
            delta_t = float(values["delta_t_sum"]) / float(values["delta_t_count"])
            solar = float(values["solar_sum"]) / float(values["solar_count"])
            gas = _valid_number(values.get("gas_kwh"))
            hdd = max(0.0, 18.0 - outdoor)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if gas is None or gas <= 0:
            continue
        records.append({"date": date, "hdd": hdd, "delta_t": delta_t, "solar": solar, "gas": gas})

    if len(records) < minimum_days:
        return {
            "status": "insufficient_data",
            "days_used": len(records),
            "minimum_days": minimum_days,
            "note": f"At least {minimum_days} completed days with gas, outdoor temperature, ΔT and solar readings are required.",
            "reference_conditions": None,
            "coefficients": None,
            "recent_days": [],
        }

    features = [
        [1.0, item["hdd"], item["delta_t"], item["solar"]]
        for item in records
    ]
    # Small ridge penalty on weather coefficients reduces instability when
    # HDD and delta-T move together; the intercept remains unpenalised.
    size = 4
    matrix = [[sum(row[i] * row[j] for row in features) for j in range(size)] for i in range(size)]
    vector = [sum(row[i] * item["gas"] for row, item in zip(features, records)) for i in range(size)]
    for index in range(1, size):
        matrix[index][index] += 0.05
    coefficients = _solve(matrix, vector)
    if coefficients is None:
        return {
            "status": "insufficient_variation", "days_used": len(records),
            "minimum_days": minimum_days, "note": "Weather variables do not vary enough to fit a stable comparison.",
            "reference_conditions": None, "coefficients": None, "recent_days": [],
        }

    reference = {
        "heating_degree_days": median(item["hdd"] for item in records),
        "delta_t_c": median(item["delta_t"] for item in records),
        "solar_radiation_w_m2": median(item["solar"] for item in records),
    }

    def predict(hdd: float, delta_t: float, solar: float) -> float:
        return max(0.0, coefficients[0] + coefficients[1] * hdd + coefficients[2] * delta_t + coefficients[3] * solar)

    expected_reference = predict(
        reference["heating_degree_days"], reference["delta_t_c"], reference["solar_radiation_w_m2"]
    )
    recent = []
    for item in records[-30:]:
        expected = predict(item["hdd"], item["delta_t"], item["solar"])
        adjusted = item["gas"] - expected + expected_reference
        recent.append({
            "date": item["date"],
            "actual_gas_kwh": round(item["gas"], 2),
            "weather_expected_gas_kwh": round(expected, 2),
            "weather_normalised_gas_kwh": round(max(0.0, adjusted), 2),
            "weather_effect_percent": round((item["gas"] - expected) / expected * 100, 1) if expected > 0 else None,
        })

    return {
        "status": "available",
        "days_used": len(records),
        "minimum_days": minimum_days,
        "reference_conditions": {
            key: round(value, 2) for key, value in reference.items()
        },
        "reference_expected_gas_kwh": round(expected_reference, 2),
        "coefficients": {
            "baseline_kwh": round(coefficients[0], 4),
            "per_degree_day": round(coefficients[1], 4),
            "per_delta_t_degree": round(coefficients[2], 4),
            "per_solar_w_m2": round(coefficients[3], 6),
        },
        "recent_days": recent,
        "note": "Comparative regression index adjusted to median observed weather conditions. Not a physical heat-loss coefficient or boiler-efficiency measurement.",
    }
