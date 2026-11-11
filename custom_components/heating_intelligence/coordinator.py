"""Coordinator for read-only room state snapshots."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_DEMAND_ENTITY,
    CONF_ROOMS,
    CONF_TEMPERATURE_ENTITY,
    CONF_WINDOW_ENTITY,
    DOMAIN,
)


class HeatingIntelligenceCoordinator(DataUpdateCoordinator):
    """Collect current room entity states."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self):
        """Return a snapshot of configured room states."""
        rooms = self.entry.options.get(CONF_ROOMS, [])
        result = {}
        for room in rooms:
            room_id = room["id"]
            demand = self.hass.states.get(room[CONF_DEMAND_ENTITY])
            temperature = self.hass.states.get(room[CONF_TEMPERATURE_ENTITY])
            window_id = room.get(CONF_WINDOW_ENTITY)
            window = self.hass.states.get(window_id) if window_id else None
            demand_state = demand.state if demand else None
            result[room_id] = {
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
