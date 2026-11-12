"""Conservative, explainable operational insights for heating performance."""
from __future__ import annotations

from typing import Any


def build_heating_insights(data: dict[str, Any]) -> list[dict[str, str]]:
    """Return diagnostic prompts supported by current room/floor observations.

    Thresholds are intentionally simple heuristics, not fault detection or
    claims of causation. Missing/insufficient observations produce no insight.
    """
    insights: list[dict[str, str]] = []
    floors = data.get("floor_analysis", {})
    floor_labels = {"ground_floor": "Downstairs", "upstairs": "Upstairs"}

    for floor_id, label in floor_labels.items():
        floor = floors.get(floor_id, {})
        if floor.get("demand_pump_state") == "demand_without_pump":
            insights.append({
                "id": f"{floor_id}_demand_without_pump",
                "severity": "warning",
                "title": f"{label}: heat demand without pump operation",
                "detail": "At least one room is requesting heat, but the manifold pump is reported stopped.",
                "recommendation": "Check the pump state and its power sensor. A brief transition delay can be normal.",
            })

    rooms = data.get("rooms", {})
    for room in rooms.values():
        name = str(room.get("name") or "Room")
        window_hours = float(room.get("window_open_demand_seconds_today") or 0) / 3600
        if window_hours >= 0.25:
            insights.append({
                "id": f"window_during_demand_{room.get('id', name)}",
                "severity": "warning",
                "title": f"{name}: window open while heating was requested",
                "detail": f"The window was reported open during {window_hours:.2f} hours of heating demand today.",
                "recommendation": "Review the window contact and avoid prolonged ventilation while heat is being requested.",
            })

        cycles = int(room.get("cycles_today") or 0)
        if cycles >= 8:
            insights.append({
                "id": f"frequent_cycles_{room.get('id', name)}",
                "severity": "info",
                "title": f"{name}: frequent heating cycles",
                "detail": f"{cycles} heating-demand cycles have been observed today; the prompt threshold is 8.",
                "recommendation": "Compare this room's cycle pattern over several days before changing thermostat settings.",
            })

        response = room.get("heating_response_minutes")
        response_samples = int(room.get("heating_response_samples") or 0)
        if response is not None and response_samples >= 3 and float(response) > 90:
            insights.append({
                "id": f"slow_response_{room.get('id', name)}",
                "severity": "info",
                "title": f"{name}: slow observed heating response",
                "detail": f"The average observed time to a 0.5 °C rise is {float(response):.0f} minutes across {response_samples} samples.",
                "recommendation": "Check that the room has enough valid samples and compare with rooms on the same floor.",
            })

        cooling = room.get("cooling_rate_c_per_hour")
        if cooling is not None and float(cooling) > 0.5:
            insights.append({
                "id": f"cooling_rate_{room.get('id', name)}",
                "severity": "info",
                "title": f"{name}: elevated observed cooling rate",
                "detail": f"The latest closed-window, no-demand cooling segment is {float(cooling):.2f} °C per hour.",
                "recommendation": "Compare this room with its own readings on similar outdoor-temperature days; one segment is not a building assessment.",
            })

    severity_order = {"warning": 0, "info": 1}
    return sorted(insights, key=lambda item: (severity_order.get(item["severity"], 2), item["title"]))
