"""Coordinator for read-only heating state snapshots."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEMAND_ENTITY,
    CONF_PUMPS,
    CONF_ROOMS,
    CONF_TEMPERATURE_ENTITY,
    CONF_WATER_TEMPERATURE_ENTITY,
    CONF_WINDOW_ENTITY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
RUNNING_THRESHOLD_W = 5
SYNC_TOLERANCE_SECONDS = 60


class HeatingIntelligenceCoordinator(DataUpdateCoordinator):
    """Collect current room, pump and global states, plus persisted pump cycles."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self._store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_pump_history")
        self._history: dict = {"pumps": {}, "sync_mismatch_count": 0, "last_sync_result": "unknown"}
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )

    async def async_load_history(self) -> None:
        """Restore pump cycle counters and active-cycle timestamps."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._history.update(stored)
            self._history.setdefault("pumps", {})
            self._history.setdefault("sync_mismatch_count", 0)
            self._history.setdefault("last_sync_result", "unknown")

    async def _async_update_data(self):
        """Return a snapshot and update pump cycle accounting on state transitions."""
        now = dt_util.utcnow()
        result = {
            "rooms": {},
            "pumps": {},
            "water_temperature": None,
            "water_temperature_entity": None,
        }
        water_entity = self.entry.options.get(CONF_WATER_TEMPERATURE_ENTITY)
        if water_entity:
            state = self.hass.states.get(water_entity)
            result["water_temperature_entity"] = water_entity
            result["water_temperature"] = _as_float(state.state) if state else None

        configured_pumps = self.entry.options.get(CONF_PUMPS, [])
        previous_running = {
            pump_id: bool(item.get("running", False))
            for pump_id, item in self._history.get("pumps", {}).items()
        }
        changed = False

        for pump in configured_pumps:
            pump_id = pump["id"]
            power_state = self.hass.states.get(pump["power_entity"])
            energy_state = self.hass.states.get(pump["energy_entity"])
            power = _as_float(power_state.state) if power_state else None
            energy = _as_float(energy_state.state) if energy_state else None
            running = power is not None and power > RUNNING_THRESHOLD_W
            history = self._history.setdefault("pumps", {}).setdefault(pump_id, {
                "running": False,
                "cycle_count": 0,
                "total_runtime_seconds": 0.0,
                "cycle_start": None,
                "last_cycle_start": None,
                "last_cycle_duration_seconds": None,
            })
            was_running = bool(history.get("running", False))

            if running and not was_running:
                history["running"] = True
                history["cycle_start"] = now.isoformat()
                history["last_cycle_start"] = now.isoformat()
                history["cycle_count"] = int(history.get("cycle_count", 0)) + 1
                changed = True

                other_ids = [p["id"] for p in configured_pumps if p["id"] != pump_id]
                other_running_id = next(
                    (other_id for other_id in other_ids
                     if self._history.get("pumps", {}).get(other_id, {}).get("running")),
                    None,
                )
                if other_running_id:
                    other_start = dt_util.parse_datetime(
                        self._history["pumps"][other_running_id].get("cycle_start") or ""
                    )
                    difference = abs((now - other_start).total_seconds()) if other_start else None
                    if difference is not None and difference <= SYNC_TOLERANCE_SECONDS:
                        self._history["last_sync_result"] = "synchronised"
                        self._history["last_sync_delta_seconds"] = round(difference)
                    else:
                        self._history["last_sync_result"] = "out_of_sync"
                        self._history["last_sync_delta_seconds"] = round(difference) if difference is not None else None
                        self._history["sync_mismatch_count"] = int(self._history.get("sync_mismatch_count", 0)) + 1
                    changed = True
                else:
                    self._history["last_sync_result"] = "single_pump_cycle"
                    self._history["last_sync_delta_seconds"] = None
                    changed = True

            elif not running and was_running:
                started = dt_util.parse_datetime(history.get("cycle_start") or "")
                duration = max(0.0, (now - started).total_seconds()) if started else 0.0
                history["running"] = False
                history["total_runtime_seconds"] = float(history.get("total_runtime_seconds", 0)) + duration
                history["last_cycle_duration_seconds"] = round(duration)
                history["cycle_start"] = None
                changed = True

            elif running and was_running and not history.get("cycle_start"):
                # Recover gracefully if an older stored record lacks an active start time.
                history["cycle_start"] = now.isoformat()
                changed = True

            cycle_start = dt_util.parse_datetime(history.get("cycle_start") or "")
            current_duration = max(0.0, (now - cycle_start).total_seconds()) if running and cycle_start else 0.0
            result["pumps"][pump_id] = {
                "id": pump_id,
                "name": pump["name"],
                "power_entity": pump["power_entity"],
                "power": power,
                "energy_entity": pump["energy_entity"],
                "energy": energy,
                "running": running,
                "power_source_state": power_state.state if power_state else "missing",
                "energy_source_state": energy_state.state if energy_state else "missing",
                "cycle_count": int(history.get("cycle_count", 0)),
                "total_runtime_seconds": round(float(history.get("total_runtime_seconds", 0))),
                "current_cycle_seconds": round(current_duration),
                "last_cycle_start": history.get("last_cycle_start"),
                "last_cycle_duration_seconds": history.get("last_cycle_duration_seconds"),
            }

        if changed:
            await self._store.async_save(self._history)

        result["sync"] = {
            "status": self._history.get("last_sync_result", "unknown"),
            "mismatch_count": int(self._history.get("sync_mismatch_count", 0)),
            "start_delta_seconds": self._history.get("last_sync_delta_seconds"),
            "tolerance_seconds": SYNC_TOLERANCE_SECONDS,
        }

        for room in self.entry.options.get(CONF_ROOMS, []):
            room_id = room["id"]
            demand = self.hass.states.get(room[CONF_DEMAND_ENTITY])
            temperature = self.hass.states.get(room[CONF_TEMPERATURE_ENTITY])
            window_id = room.get(CONF_WINDOW_ENTITY)
            window = self.hass.states.get(window_id) if window_id else None
            demand_state = demand.state if demand else None
            result["rooms"][room_id] = {
                "id": room_id,
                "name": room["name"],
                "demand_entity": room[CONF_DEMAND_ENTITY],
                "demand_state": demand_state,
                "demand": demand_state == "on",
                "temperature_entity": room[CONF_TEMPERATURE_ENTITY],
                "temperature": _as_float(temperature.state) if temperature else None,
                "window_entity": window_id,
                "window_state": window.state if window else None,
                "window_open": bool(window and window.state == "on"),
            }
        return result


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
