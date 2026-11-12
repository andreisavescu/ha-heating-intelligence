"""Heating Intelligence sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PUMPS, CONF_ROOMS, DOMAIN
from .coordinator import HeatingIntelligenceCoordinator
from .devices import HouseDeviceMixin, RoomDeviceMixin, PumpDeviceMixin


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HeatingIntelligenceCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [HeatingOverviewSensor(coordinator)]
    if entry.options.get("water_temperature_entity"):
        entities.append(WaterTemperatureSensor(coordinator))
    for room in entry.options.get(CONF_ROOMS, []):
        entities.append(RoomTemperatureSensor(coordinator, room["id"], room["name"]))
        entities.extend(
            RoomAnalyticsSensor(coordinator, room["id"], room["name"], metric)
            for metric in ROOM_ANALYTICS_METRICS
        )
    for pump in entry.options.get(CONF_PUMPS, []):
        entities.extend([
            PumpPowerSensor(coordinator, pump["id"], pump["name"]),
            PumpEnergySensor(coordinator, pump["id"], pump["name"]),
            PumpCycleCountSensor(coordinator, pump["id"], pump["name"]),
            PumpRuntimeSensor(coordinator, pump["id"], pump["name"]),
            PumpDailyEnergySensor(coordinator, pump["id"], pump["name"]),
            PumpDailyRuntimeSensor(coordinator, pump["id"], pump["name"]),
            PumpDailyCycleCountSensor(coordinator, pump["id"], pump["name"]),
        ])
    if len(entry.options.get(CONF_PUMPS, [])) == 2:
        entities.append(PumpSynchronizationSensor(coordinator))
    async_add_entities(entities)


class HeatingOverviewSensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Master sensor used by the Lovelace overview card."""

    _attr_should_poll = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Heating Intelligence Overview"
        self._attr_unique_id = f"{DOMAIN}_overview"
        self._attr_icon = "mdi:home-thermometer"

    @property
    def native_value(self):
        rooms = self.coordinator.data.get("rooms", {})
        return sum(1 for room in rooms.values() if room.get("demand"))

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        rooms = data.get("rooms", {})
        valid_temps = [r["temperature"] for r in rooms.values() if r.get("temperature") is not None]
        pumps = data.get("pumps", {})
        return {
            "rooms": rooms,
            "room_count": len(rooms),
            "heating_rooms": sum(1 for room in rooms.values() if room.get("demand")),
            "average_room_temperature": round(sum(valid_temps) / len(valid_temps), 1) if valid_temps else None,
            "water_temperature": data.get("water_temperature"),
            "water_temperature_entity": data.get("water_temperature_entity"),
            "pumps": pumps,
            "running_pumps": sum(1 for pump in pumps.values() if pump.get("running")),
            "total_pump_power": round(sum(p["power"] for p in pumps.values() if p.get("power") is not None), 1),
            "total_pump_energy": round(sum(p["energy"] for p in pumps.values() if p.get("energy") is not None), 3),
            "pump_synchronization": data.get("sync", {}),
            "daily_heating": data.get("daily", {}),
            "floor_analysis": data.get("floor_analysis", {}),
        }


class WaterTemperatureSensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    _attr_native_unit_of_measurement = "°C"
    _attr_should_poll = False
    _attr_icon = "mdi:thermometer-water"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Heating Water Inlet Temperature"
        self._attr_unique_id = f"{DOMAIN}_water_inlet_temperature"

    @property
    def native_value(self):
        return self.coordinator.data.get("water_temperature")

    @property
    def available(self):
        return super().available and self.coordinator.data.get("water_temperature") is not None


class RoomTemperatureSensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    _attr_native_unit_of_measurement = "°C"
    _attr_should_poll = False

    def __init__(self, coordinator, room_id: str, room_name: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self.room_name = room_name
        self._attr_name = f"{room_name} Temperature"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_temperature"

    @property
    def native_value(self):
        return self.coordinator.data.get("rooms", {}).get(self.room_id, {}).get("temperature")

    @property
    def available(self):
        return super().available and self.coordinator.data.get("rooms", {}).get(self.room_id, {}).get("temperature") is not None


ROOM_ANALYTICS_METRICS = {
    "demand_seconds_today": ("Heating Demand Today", "h", 3600, "mdi:clock-outline"),
    "demand_seconds_week": ("Heating Demand This Week", "h", 3600, "mdi:calendar-week"),
    "demand_seconds_month": ("Heating Demand This Month", "h", 3600, "mdi:calendar-month"),
    "cycles_today": ("Heating Cycles Today", "cycles", 1, "mdi:counter"),
    "cycles_week": ("Heating Cycles This Week", "cycles", 1, "mdi:counter"),
    "cycles_month": ("Heating Cycles This Month", "cycles", 1, "mdi:counter"),
    "average_cycle_seconds_week": ("Average Heating Cycle This Week", "min", 60, "mdi:timer-outline"),
    "shortest_cycle_seconds_week": ("Shortest Heating Cycle This Week", "min", 60, "mdi:timer-sand"),
    "longest_cycle_seconds_week": ("Longest Heating Cycle This Week", "min", 60, "mdi:timer"),
    "active_cycle_seconds": ("Current Heating Cycle Duration", "min", 60, "mdi:timer-play-outline"),
    "total_cycles": ("Heating Cycles Total", "cycles", 1, "mdi:counter"),
}


class RoomAnalyticsSensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """One room-level demand or cycle statistic."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = "measurement"

    def __init__(self, coordinator, room_id: str, room_name: str, metric: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self.room_name = room_name
        self.metric = metric
        label, unit, divisor, icon = ROOM_ANALYTICS_METRICS[metric]
        self.divisor = divisor
        if metric == "total_cycles":
            self._attr_state_class = "total_increasing"
        self._attr_name = f"{room_name} {label}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_unique_id = f"{DOMAIN}_{room_id}_{metric}"

    @property
    def native_value(self):
        room = self.coordinator.data.get("rooms", {}).get(self.room_id, {})
        value = room.get(self.metric)
        return round(value / self.divisor, 2) if value is not None else None

    @property
    def extra_state_attributes(self):
        room = self.coordinator.data.get("rooms", {}).get(self.room_id, {})
        return {
            "shortest_cycle_seconds_this_week": room.get("shortest_cycle_seconds_week"),
            "longest_cycle_seconds_this_week": room.get("longest_cycle_seconds_week"),
            "active_cycle_seconds": room.get("active_cycle_seconds", 0),
            "source_entity": room.get("demand_entity"),
            "source_state": room.get("demand_state"),
        }


class PumpPowerSensor(PumpDeviceMixin, CoordinatorEntity, SensorEntity):
    _attr_native_unit_of_measurement = "W"
    _attr_should_poll = False
    _attr_device_class = "power"
    _attr_state_class = "measurement"

    def __init__(self, coordinator, pump_id: str, pump_name: str) -> None:
        super().__init__(coordinator)
        self.pump_id = pump_id
        self.pump_name = pump_name
        self._attr_name = f"{pump_name} Pump Power"
        self._attr_unique_id = f"{DOMAIN}_{pump_id}_pump_power"

    @property
    def native_value(self):
        return self.coordinator.data.get("pumps", {}).get(self.pump_id, {}).get("power")

    @property
    def available(self):
        return super().available and self.native_value is not None


class PumpEnergySensor(PumpDeviceMixin, CoordinatorEntity, SensorEntity):
    _attr_native_unit_of_measurement = "kWh"
    _attr_should_poll = False
    _attr_device_class = "energy"
    _attr_state_class = "total_increasing"

    def __init__(self, coordinator, pump_id: str, pump_name: str) -> None:
        super().__init__(coordinator)
        self.pump_id = pump_id
        self.pump_name = pump_name
        self._attr_name = f"{pump_name} Pump Energy"
        self._attr_unique_id = f"{DOMAIN}_{pump_id}_pump_energy"

    @property
    def native_value(self):
        return self.coordinator.data.get("pumps", {}).get(self.pump_id, {}).get("energy")

    @property
    def available(self):
        return super().available and self.native_value is not None


class PumpCycleCountSensor(PumpDeviceMixin, CoordinatorEntity, SensorEntity):
    """Number of on/off cycles observed for one pump."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "cycles"
    _attr_should_poll = False
    _attr_state_class = "total_increasing"
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator, pump_id: str, pump_name: str) -> None:
        super().__init__(coordinator)
        self.pump_id = pump_id
        self.pump_name = pump_name
        self._attr_name = f"{pump_name} Pump Cycle Count"
        self._attr_unique_id = f"{DOMAIN}_{pump_id}_pump_cycle_count"

    @property
    def native_value(self):
        return self.coordinator.data.get("pumps", {}).get(self.pump_id, {}).get("cycle_count", 0)


class PumpRuntimeSensor(PumpDeviceMixin, CoordinatorEntity, SensorEntity):
    """Accumulated pump runtime in hours."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "h"
    _attr_should_poll = False
    _attr_state_class = "total_increasing"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator, pump_id: str, pump_name: str) -> None:
        super().__init__(coordinator)
        self.pump_id = pump_id
        self.pump_name = pump_name
        self._attr_name = f"{pump_name} Pump Runtime"
        self._attr_unique_id = f"{DOMAIN}_{pump_id}_pump_runtime"

    @property
    def native_value(self):
        seconds = self.coordinator.data.get("pumps", {}).get(self.pump_id, {}).get("total_runtime_seconds", 0)
        return round(seconds / 3600, 2)


class PumpSynchronizationSensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Latest comparison of overlapping pump cycle starts."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_icon = "mdi:sync"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Pump Synchronization"
        self._attr_unique_id = f"{DOMAIN}_pump_synchronization"

    @property
    def native_value(self):
        return self.coordinator.data.get("sync", {}).get("status", "unknown")

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("sync", {})


class PumpDailyEnergySensor(PumpDeviceMixin, CoordinatorEntity, SensorEntity):
    """Pump energy consumed since local midnight."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "kWh"
    _attr_should_poll = False
    _attr_device_class = "energy"
    _attr_state_class = "total"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator, pump_id: str, pump_name: str) -> None:
        super().__init__(coordinator)
        self.pump_id = pump_id
        self.pump_name = pump_name
        self._attr_name = f"{pump_name} Pump Energy Today"
        self._attr_unique_id = f"{DOMAIN}_{pump_id}_pump_energy_today"

    @property
    def native_value(self):
        return self.coordinator.data.get("pumps", {}).get(self.pump_id, {}).get("daily_energy_kwh", 0)


class PumpDailyRuntimeSensor(PumpDeviceMixin, CoordinatorEntity, SensorEntity):
    """Pump runtime accumulated since local midnight."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "h"
    _attr_should_poll = False
    _attr_state_class = "measurement"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator, pump_id: str, pump_name: str) -> None:
        super().__init__(coordinator)
        self.pump_id = pump_id
        self.pump_name = pump_name
        self._attr_name = f"{pump_name} Pump Runtime Today"
        self._attr_unique_id = f"{DOMAIN}_{pump_id}_pump_runtime_today"

    @property
    def native_value(self):
        seconds = self.coordinator.data.get("pumps", {}).get(self.pump_id, {}).get("daily_runtime_seconds", 0)
        return round(seconds / 3600, 3)


class PumpDailyCycleCountSensor(PumpDeviceMixin, CoordinatorEntity, SensorEntity):
    """Pump cycles started since local midnight."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_state_class = "measurement"
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator, pump_id: str, pump_name: str) -> None:
        super().__init__(coordinator)
        self.pump_id = pump_id
        self.pump_name = pump_name
        self._attr_name = f"{pump_name} Pump Cycles Today"
        self._attr_unique_id = f"{DOMAIN}_{pump_id}_pump_cycles_today"

    @property
    def native_value(self):
        return self.coordinator.data.get("pumps", {}).get(self.pump_id, {}).get("daily_cycle_count", 0)
