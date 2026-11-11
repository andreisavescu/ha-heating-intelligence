"""Coordinator for read-only heating state snapshots."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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


class HeatingIntelligenceCoordinator(DataUpdateCoordinator):
    """Collect current room, pump and heating-water states."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self):
        """Return a snapshot of configured room, pump and global states."""
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

        for pump in self.entry.options.get(CONF_PUMPS, []):
            power_state = self.hass.states.get(pump["power_entity"])
            energy_state = self.hass.states.get(pump["energy_entity"])
            power = _as_float(power_state.state) if power_state else None
            energy = _as_float(energy_state.state) if energy_state else None
            result["pumps"][pump["id"]] = {
                "id": pump["id"],
                "name": pump["name"],
                "power_entity": pump["power_entity"],
                "power": power,
                "energy_entity": pump["energy_entity"],
                "energy": energy,
                "running": power is not None and power > 5,
                "power_source_state": power_state.state if power_state else "missing",
                "energy_source_state": energy_state.state if energy_state else "missing",
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
