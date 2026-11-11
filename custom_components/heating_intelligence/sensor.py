"""Room temperature entities."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ROOMS, DOMAIN
from .coordinator import HeatingIntelligenceCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HeatingIntelligenceCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoomTemperatureSensor(coordinator, room["id"], room["name"])
        for room in entry.options.get(CONF_ROOMS, [])
    )


class RoomTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Expose a configured room temperature."""

    _attr_native_unit_of_measurement = "°C"
    _attr_should_poll = False

    def __init__(self, coordinator, room_id: str, room_name: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self._attr_name = f"{room_name} Temperature"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_temperature"

    @property
    def native_value(self):
        room = self.coordinator.data.get(self.room_id, {})
        return room.get("temperature")

    @property
    def available(self):
        return super().available and self.coordinator.data.get(self.room_id, {}).get("temperature") is not None
