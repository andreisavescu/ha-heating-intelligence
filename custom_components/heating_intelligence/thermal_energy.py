"""Persistent room-level UFH thermal energy accounting."""
from __future__ import annotations

from datetime import datetime


def update_room_thermal_energy(
    history: dict,
    circuits: dict,
    rooms: dict,
    elapsed_seconds: float,
    now: datetime,
) -> dict:
    """Accumulate estimated circuit heat, split equally among assigned rooms.

    The source is configured manual flow and the hydraulic snapshot's confirmed
    active state. Shared circuits are divided equally to avoid double counting.
    """
    energy = history.setdefault("room_thermal_energy", {})
    date_key = now.date().isoformat()
    iso = now.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    month_key = f"{now.year}-{now.month:02d}"
    interval = max(0.0, min(float(elapsed_seconds or 0.0), 90.0))
    result = {}

    for room_id in rooms:
        item = energy.setdefault(room_id, {
            "date": date_key, "week": week_key, "month": month_key,
            "today_kwh": 0.0, "week_kwh": 0.0, "month_kwh": 0.0,
            "total_kwh": 0.0,
        })
        if item.get("date") != date_key:
            item["date"] = date_key
            item["today_kwh"] = 0.0
        if item.get("week") != week_key:
            item["week"] = week_key
            item["week_kwh"] = 0.0
        if item.get("month") != month_key:
            item["month"] = month_key
            item["month_kwh"] = 0.0

    for circuit in circuits.values():
        if not circuit.get("flow_active"):
            continue
        power_w = float(circuit.get("estimated_thermal_power_w") or 0.0)
        if power_w <= 0:
            continue
        targets = [room_id for room_id in circuit.get("demand_rooms", []) if room_id in rooms]
        if not targets:
            continue
        share_kwh = power_w * interval / 3_600_000.0 / len(targets)
        for room_id in targets:
            item = energy[room_id]
            item["today_kwh"] += share_kwh
            item["week_kwh"] += share_kwh
            item["month_kwh"] += share_kwh
            item["total_kwh"] += share_kwh

    for room_id in rooms:
        item = energy[room_id]
        result[room_id] = {
            "thermal_energy_today_kwh": round(item["today_kwh"], 6),
            "thermal_energy_week_kwh": round(item["week_kwh"], 6),
            "thermal_energy_month_kwh": round(item["month_kwh"], 6),
            "thermal_energy_total_kwh": round(item["total_kwh"], 6),
        }
    return result
