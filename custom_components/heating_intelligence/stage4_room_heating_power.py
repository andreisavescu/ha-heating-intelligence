"""Stage 4 room-level heating power estimates."""
from __future__ import annotations

from typing import Any


def build_stage4_room_heating_power(
    boiler: dict[str, Any] | None,
    rooms: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Expose allocated room heat power with clear estimate provenance.

    Gas-reconciled interval-average power is the primary ledger when available.
    Modulation-derived interval power is reported independently as a cross-check.
    Neither is a water-side measurement of heat delivered to the room.
    """
    boiler = boiler or {}
    rooms = rooms or {}
    gas_power = boiler.get("boiler_room_interval_average_power_w") or {}
    gas_energy = boiler.get("boiler_room_allocations_kwh") or {}
    modulation_power = boiler.get("boiler_modulation_room_interval_diagnostics") or {}
    interval_seconds = boiler.get("boiler_meter_interval_seconds")
    modulation_seconds = boiler.get("boiler_modulation_interval_seconds")
    results = {}
    for room_id, room in rooms.items():
        gp = gas_power.get(room_id)
        mp_record = modulation_power.get(room_id, {})
        mp = mp_record.get("estimated_power_w") if isinstance(mp_record, dict) else None
        results[room_id] = {
            "room_name": room.get("name", room_id),
            "gas_reconciled_interval_average_power_w": round(float(gp), 2) if isinstance(gp, (int, float)) else None,
            "gas_reconciled_interval_energy_kwh": round(float(gas_energy.get(room_id)), 6) if isinstance(gas_energy.get(room_id), (int, float)) else None,
            "gas_meter_interval_seconds": interval_seconds,
            "modulation_interval_estimated_power_w": round(float(mp), 2) if isinstance(mp, (int, float)) else None,
            "modulation_interval_seconds": modulation_seconds,
            "status": "estimated_power_available" if gp is not None or mp is not None else "awaiting_room_heat_allocation_interval",
            "interpretation": "Allocated boiler useful heat divided by the associated interval duration; room allocation uses configured UFH circuit-flow and confirmed demand exposure. This is an inferred room input, not independently measured heat delivered through the floor.",
        }
    return {
        "stage": 4,
        "status": "room_power_estimates_available" if any(
            item["status"] == "estimated_power_available" for item in results.values()
        ) else "awaiting_room_heat_allocation_interval",
        "rooms": results,
        "water_side_measurement_status": boiler.get("heat_delivery_calibration", {}).get("water_side_measurement_status"),
        "room_level_heat_delivery_measured": False,
        "limitations": [
            "Room power is inferred from boiler energy ledgers and UFH allocation weights, not measured at each room.",
            "Configured circuit flow is an allocation weight; it is not a measured flow rate.",
            "Gas-reconciled values update at gas-meter synchronization intervals and are interval averages, not instantaneous room power.",
            "Independent water-side output requires measured flow and supply/return water temperatures.",
            "DHW is excluded from the heating-gas allocation ledger.",
        ],
    }
