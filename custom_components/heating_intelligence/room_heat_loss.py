"""Room-level transmission heat-flow estimates from explicit envelope surfaces.

Ground temperature is a provisional lagged-outdoor proxy. Ground heat flow is
only calculated when an explicit ground-boundary UA surface is configured.
"""
from __future__ import annotations

import math
from typing import Any

GROUND_TEMPERATURE_TIME_CONSTANT_DAYS = 30.0


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def update_ground_temperature_model(
    outdoor_temperature_c: float | None,
    previous_state: dict | None,
    elapsed_seconds: float,
) -> tuple[dict, dict]:
    """Update a provisional 30-day low-pass outdoor proxy.

    This is not a soil-temperature measurement or a validated ISO 13370 model.
    It provides a lagged boundary estimate only; the UA of the ground assembly
    must still be explicitly configured before any ground heat flow is reported.
    """
    outside = _number(outdoor_temperature_c)
    state = dict(previous_state or {})
    previous = _number(state.get("filtered_outdoor_temperature_c"))
    accumulated = max(0.0, _number(state.get("observed_seconds")) or 0.0)
    dt = max(0.0, min(_number(elapsed_seconds) or 0.0, 3600.0))
    if outside is not None:
        if previous is None:
            filtered = outside
        else:
            tau_seconds = GROUND_TEMPERATURE_TIME_CONSTANT_DAYS * 86400.0
            alpha = 1.0 - math.exp(-dt / tau_seconds) if dt > 0 else 0.0
            filtered = previous + alpha * (outside - previous)
        accumulated += dt
        state = {
            "filtered_outdoor_temperature_c": round(filtered, 4),
            "observed_seconds": round(accumulated, 1),
            "time_constant_days": GROUND_TEMPERATURE_TIME_CONSTANT_DAYS,
            "last_outdoor_temperature_c": outside,
        }
    else:
        filtered = previous
    warmed = accumulated >= GROUND_TEMPERATURE_TIME_CONSTANT_DAYS * 86400.0
    model = {
        "estimated_ground_boundary_temperature_c": round(filtered, 2) if filtered is not None else None,
        "status": "provisional_lagged_outdoor_proxy" if warmed else
            "warming_up_lagged_outdoor_proxy" if filtered is not None else
            "unavailable_missing_outdoor_temperature",
        "observed_duration_days": round(accumulated / 86400.0, 2),
        "time_constant_days": GROUND_TEMPERATURE_TIME_CONSTANT_DAYS,
        "method": "30-day first-order low-pass of measured outdoor temperature; provisional boundary proxy, not a measured soil temperature or validated ground heat-transfer standard",
    }
    return state, model


def build_room_boundary_heat_loss(
    rooms: dict[str, dict],
    envelope_audit: dict,
    outdoor_temperature_c: float | None,
    attic_temperature_c: float | None,
    ground_temperature_model: dict,
) -> dict:
    """Calculate signed room-to-boundary heat flow from explicit surface UA.

    Positive values mean heat flows out of the room; negative values mean the
    boundary is warmer and transfers heat into the room. Known totals are partial
    whenever a boundary inventory is missing or incomplete.
    """
    audited_rooms = (envelope_audit or {}).get("rooms") or {}
    ground_temperature = (
        _number((ground_temperature_model or {}).get("estimated_ground_boundary_temperature_c"))
        if (ground_temperature_model or {}).get("status") == "provisional_lagged_outdoor_proxy"
        else None
    )
    outside = _number(outdoor_temperature_c)
    attic = _number(attic_temperature_c)
    results = {}
    for room_id, room in (rooms or {}).items():
        surface = audited_rooms.get(room_id) or {}
        conductances = surface.get("conductance_by_boundary_w_per_k") or {}
        outdoor_ua = _number(conductances.get("outdoor"))
        attic_ua = _number(conductances.get("attic"))
        ground_ua = _number(conductances.get("ground"))
        measured_room_temp = _number(room.get("temperature"))
        # Heat-flow estimates always use the actual measured room temperature.
        # If it is unavailable, the boundary flow remains unavailable rather
        # than substituting a design/reference temperature.
        room_temp = measured_room_temp
        reference_temperature_source = "measured_room_temperature"

        def flow(ua: float | None, boundary_temp: float | None):
            if ua is None or ua < 0 or room_temp is None or boundary_temp is None:
                return None
            return round(ua * (room_temp - boundary_temp), 2)

        outdoor_flow = flow(outdoor_ua, outside)
        attic_flow = flow(attic_ua, attic)
        ground_flow = flow(ground_ua, ground_temperature)
        known = [value for value in (outdoor_flow, attic_flow, ground_flow) if value is not None]
        opaque_count = int((surface.get("opaque_surface_counts") or {}).get("outdoor") or 0)
        window_count = int(surface.get("window_record_count") or 0)
        outdoor_inventory_complete = opaque_count > 0
        ground_inventory_configured = ground_ua is not None
        attic_inventory_configured = attic_ua is not None
        # Boundary expectations follow the actual room position in the house.
        # Upstairs rooms face outdoors and the attic; downstairs rooms face
        # outdoors and the ground. Bucătărie is the one downstairs exception:
        # its configured ceiling also borders the attic.
        room_name = str(room.get("name") or "").strip().casefold()
        is_kitchen = room_name in {"bucătărie", "bucatarie", "kitchen"} or room_id.casefold() in {"bucatarie", "bucătărie", "kitchen"}
        if room.get("floor") == "upstairs":
            expected_boundaries = ["outdoor", "attic"]
        elif is_kitchen:
            expected_boundaries = ["outdoor", "ground", "attic"]
        else:
            expected_boundaries = ["outdoor", "ground"]
        boundary_complete = (
            outdoor_inventory_complete
            and all(
                ground_inventory_configured if boundary == "ground" else attic_inventory_configured
                for boundary in expected_boundaries if boundary != "outdoor"
            )
        )
        results[room_id] = {
            "room_name": room.get("name", room_id),
            "room_temperature_c": room_temp,
            "measured_room_temperature_c": measured_room_temp,
            "reference_temperature_source": reference_temperature_source,
            "outdoor": {
                "conductance_w_per_k": outdoor_ua,
                "boundary_temperature_c": outside,
                "heat_flow_w": outdoor_flow,
                "status": "calculated" if outdoor_flow is not None and outdoor_inventory_complete
                    else "partial_surface_inventory" if outdoor_flow is not None
                    else "unavailable_missing_ua_or_temperature",
                "opaque_surface_count": opaque_count,
                "window_record_count": window_count,
            },
            "attic": {
                "conductance_w_per_k": attic_ua,
                "boundary_temperature_c": attic,
                "heat_flow_w": attic_flow,
                "status": "calculated" if attic_flow is not None
                    else "not_applicable" if "attic" not in expected_boundaries
                    else "unavailable_missing_ua_or_temperature",
            },
            "ground": {
                "conductance_w_per_k": ground_ua,
                "boundary_temperature_c": ground_temperature,
                "heat_flow_w": ground_flow,
                "status": "calculated" if ground_flow is not None
                    else "unavailable_missing_explicit_ground_ua" if ground_ua is None
                    else "unavailable_ground_temperature_model_warming_up" if ground_temperature is None
                    else "unavailable_missing_room_temperature",
                "temperature_model_status": (ground_temperature_model or {}).get("status"),
            },
            "known_boundary_heat_flow_w": round(sum(known), 2) if known else None,
            "known_boundary_heat_flow_is_complete": bool(boundary_complete),
            "missing_or_incomplete_boundaries": [
                key for key, complete in (
                    ("outdoor_surface_inventory", outdoor_inventory_complete),
                    ("ground_surface_ua", ground_inventory_configured if "ground" in expected_boundaries else True),
                    ("attic_surface_ua", attic_inventory_configured if "attic" in expected_boundaries else True),
                ) if not complete
            ],
            "interpretation": "Signed conductive transmission heat flow only; positive is outward. Known total is partial when any required boundary inventory is missing. It excludes ventilation, inter-room transfer, solar/internal gains and UFH slab storage.",
        }
    return {
        "status": "available" if any(item["known_boundary_heat_flow_w"] is not None for item in results.values()) else "awaiting_room_temperatures_or_boundary_data",
        "method": "surface-derived UA × measured room-to-boundary temperature difference",
        "sign_convention": "positive outward heat loss; negative inward heat gain",
        "rooms": results,
        "ground_temperature_model": ground_temperature_model or {},
        "limitations": [
            "Conductances come only from explicit accepted surface records and whole-window Uw records.",
            "The Baie Cada hallway/staircase attic ceiling is included through its user-confirmed legacy allocation.",
            "The ground boundary temperature is a provisional lagged-outdoor proxy, not a soil sensor or a validated ground-coupling model.",
            "No ground heat flow is calculated until an explicit ground-boundary surface UA is configured.",
            "Outdoor heat flow is marked partial where opaque exterior surfaces are missing, even if window UA is known.",
        ],
    }
