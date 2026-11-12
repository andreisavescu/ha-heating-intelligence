"""Room demand and manifold pump state entities."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PUMPS, CONF_ROOMS, DOMAIN
from .coordinator import HeatingIntelligenceCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HeatingIntelligenceCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for room in entry.options.get(CONF_ROOMS, []):
        entities.append(RoomDemandSensor(coordinator, room["id"], room["name"]))
        if room.get("window_entity"):
            entities.append(RoomWindowSensor(coordinator, room["id"], room["name"]))
    for pump in entry.options.get(CONF_PUMPS, []):
        entities.append(PumpRunningSensor(coordinator, pump["id"], pump["name"]))
    async_add_entities(entities)


class RoomDemandSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_should_poll = False

    def __init__(self, coordinator, room_id: str, room_name: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self._attr_name = f"{room_name} Heating Demand"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_heating_demand"

    @property
    def is_on(self):
        return (self.coordinator.data or {}).get("rooms", {}).get(self.room_id, {}).get("demand", False)

    @property
    def available(self):
        room = (self.coordinator.data or {}).get("rooms", {}).get(self.room_id, {})
        return super().available and room.get("demand_state") not in (None, "unknown")

    @property
    def extra_state_attributes(self):
        room = (self.coordinator.data or {}).get("rooms", {}).get(self.room_id, {})
        return {
            "source_entity": room.get("demand_entity"),
            "source_state": room.get("demand_state"),
            "interpretation": "on = heat requested; unavailable/off = no active request",
        }


class RoomWindowSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.WINDOW
    _attr_should_poll = False

    def __init__(self, coordinator, room_id: str, room_name: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self._attr_name = f"{room_name} Window"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_window"

    @property
    def is_on(self):
        return (self.coordinator.data or {}).get("rooms", {}).get(self.room_id, {}).get("window_open", False)

    @property
    def available(self):
        room = (self.coordinator.data or {}).get("rooms", {}).get(self.room_id, {})
        return super().available and room.get("window_state") not in (None, "unavailable", "unknown")


class PumpRunningSensor(CoordinatorEntity, BinarySensorEntity):
    """Pump is considered running above a small power threshold."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_should_poll = False

    def __init__(self, coordinator, pump_id: str, pump_name: str) -> None:
        super().__init__(coordinator)
        self.pump_id = pump_id
        self._attr_name = f"{pump_name} Pump Running"
        self._attr_unique_id = f"{DOMAIN}_{pump_id}_pump_running"

    @property
    def is_on(self):
        return (self.coordinator.data or {}).get("pumps", {}).get(self.pump_id, {}).get("running", False)

    @property
    def available(self):
        pump = (self.coordinator.data or {}).get("pumps", {}).get(self.pump_id, {})
        return super().available and pump.get("power") is not None

    @property
    def extra_state_attributes(self):
        pump = (self.coordinator.data or {}).get("pumps", {}).get(self.pump_id, {})
        return {
            "power": pump.get("power"),
            "power_entity": pump.get("power_entity"),
            "running_threshold_w": 5,
        }
