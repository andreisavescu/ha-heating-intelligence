"""Room heating-demand and window entities."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ROOMS, DOMAIN
from .coordinator import HeatingIntelligenceCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HeatingIntelligenceCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for room in entry.options.get(CONF_ROOMS, []):
        entities.append(RoomDemandSensor(coordinator, room["id"], room["name"]))
        if room.get("window_entity"):
            entities.append(RoomWindowSensor(coordinator, room["id"], room["name"]))
    async_add_entities(entities)


class RoomDemandSensor(CoordinatorEntity, BinarySensorEntity):
    """Whether a room is currently requesting heat."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_should_poll = False

    def __init__(self, coordinator, room_id: str, room_name: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self._attr_name = f"{room_name} Heating Demand"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_heating_demand"

    @property
    def is_on(self):
        return self.coordinator.data.get(self.room_id, {}).get("demand", False)

    @property
    def available(self):
        room = self.coordinator.data.get(self.room_id, {})
        return super().available and room.get("demand_state") is not None

    @property
    def extra_state_attributes(self):
        room = self.coordinator.data.get(self.room_id, {})
        return {
            "source_entity": room.get("demand_entity"),
            "source_state": room.get("demand_state"),
            "interpretation": "on = heat requested; off/unavailable/unknown = no active request",
        }


class RoomWindowSensor(CoordinatorEntity, BinarySensorEntity):
    """Window contact state for a room."""

    _attr_device_class = BinarySensorDeviceClass.WINDOW
    _attr_should_poll = False

    def __init__(self, coordinator, room_id: str, room_name: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self._attr_name = f"{room_name} Window"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_window"

    @property
    def is_on(self):
        return self.coordinator.data.get(self.room_id, {}).get("window_open", False)

    @property
    def available(self):
        return (
            super().available
            and self.coordinator.data.get(self.room_id, {}).get("window_state") is not None
        )
