"""Heating Intelligence sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PUMPS, CONF_ROOMS, DOMAIN,
    CONF_OUTDOOR_TEMPERATURE_ENTITY, CONF_SOLAR_RADIATION_ENTITY,
    CONF_DELTA_T_ENTITY, CONF_GAS_CONSUMPTION_ENTITY,
    CONF_ELECTRICITY_BOILER_POWER_ENTITY,
    CONF_ELECTRICITY_BOILER_ENERGY_ENTITY,
    CONF_ELECTRICITY_BOILER_ENTITY,
    CONF_AC_POWER_ENTITY, CONF_AC_ENERGY_ENTITY,
)
from .coordinator import HeatingIntelligenceCoordinator
from .devices import HouseDeviceMixin, RoomDeviceMixin, PumpDeviceMixin
from .insights import build_heating_insights


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HeatingIntelligenceCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [HeatingOverviewSensor(coordinator)]
    if entry.options.get("water_temperature_entity"):
        entities.append(WaterTemperatureSensor(coordinator))
    for room in entry.options.get(CONF_ROOMS, []):
        entities.append(RoomTemperatureSensor(coordinator, room["id"], room["name"]))
        if room.get("humidity_entity"):
            entities.append(RoomAirQualitySensor(coordinator, room["id"], room["name"], "humidity"))
        if room.get("co2_entity"):
            entities.append(RoomAirQualitySensor(coordinator, room["id"], room["name"], "co2_ppm"))
        entities.extend(
            RoomAnalyticsSensor(coordinator, room["id"], room["name"], metric)
            for metric in ROOM_ANALYTICS_METRICS
        )
        thermal_metrics = [
            metric for metric in ROOM_THERMAL_METRICS
            if metric != "window_open_demand_seconds_today" or room.get("window_entity")
        ]
        entities.extend(
            RoomThermalAnalyticsSensor(coordinator, room["id"], room["name"], metric)
            for metric in thermal_metrics
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
    boiler_energy_configured = (
        entry.options.get(CONF_ELECTRICITY_BOILER_ENERGY_ENTITY)
        or entry.options.get(CONF_ELECTRICITY_BOILER_ENTITY)
    )
    for option, metric in (
        (CONF_OUTDOOR_TEMPERATURE_ENTITY, "outdoor_temperature"),
        (CONF_SOLAR_RADIATION_ENTITY, "solar_radiation_average"),
        (CONF_DELTA_T_ENTITY, "delta_t_average"),
        (CONF_GAS_CONSUMPTION_ENTITY, "gas_energy_today_kwh"),
    ):
        if entry.options.get(option):
            entities.append(EfficiencySensor(coordinator, metric))
    if boiler_energy_configured:
        entities.append(EfficiencySensor(coordinator, "electricity_boiler_today_kwh"))
    if entry.options.get(CONF_ELECTRICITY_BOILER_POWER_ENTITY):
        entities.append(EfficiencySensor(coordinator, "boiler_power_w"))
    if entry.options.get(CONF_AC_POWER_ENTITY):
        entities.append(EfficiencySensor(coordinator, "ac_power_w"))
    if entry.options.get(CONF_AC_ENERGY_ENTITY):
        entities.append(EfficiencySensor(coordinator, "ac_electricity_today_kwh"))
        entities.append(EfficiencySensor(coordinator, "estimated_ac_delivered_heat_today_kwh"))
    if entry.options.get(CONF_OUTDOOR_TEMPERATURE_ENTITY):
        entities.append(EfficiencySensor(coordinator, "heating_degree_days"))
    if entry.options.get(CONF_PUMPS) or boiler_energy_configured:
        entities.append(EfficiencySensor(coordinator, "electricity_energy_today_kwh"))
    if entry.options.get(CONF_GAS_CONSUMPTION_ENTITY) and entry.options.get(CONF_OUTDOOR_TEMPERATURE_ENTITY):
        entities.append(EfficiencySensor(coordinator, "gas_kwh_per_degree_day"))
    if (entry.options.get(CONF_GAS_CONSUMPTION_ENTITY) or entry.options.get(CONF_AC_ENERGY_ENTITY)) and entry.options.get(CONF_DELTA_T_ENTITY):
        entities.extend(EfficiencySensor(coordinator, metric) for metric in (
            "estimated_delivered_heat_today_kwh", "heat_loss_coefficient_w_k",
            "heat_loss_u_value_daily", "heat_loss_u_value_7d", "heat_loss_u_value_30d"))
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
            "house_assessment": data.get("house_assessment", {}),
            "room_count": len(rooms),
            "heating_rooms": sum(1 for room in rooms.values() if room.get("demand")),
            "average_room_temperature": round(sum(valid_temps) / len(valid_temps), 1) if valid_temps else None,
            "water_temperature": data.get("water_temperature"),
            "water_temperature_entity": data.get("water_temperature_entity"),
            "water_return_temperature": data.get("water_return_temperature"),
            "water_return_temperature_entity": data.get("water_return_temperature_entity"),
            "advanced_heating": data.get("advanced_heating", {}),
            "pumps": pumps,
            "running_pumps": sum(1 for pump in pumps.values() if pump.get("running")),
            "total_pump_power": round(sum(p["power"] for p in pumps.values() if p.get("power") is not None), 1),
            "total_pump_energy": round(sum(p["energy"] for p in pumps.values() if p.get("energy") is not None), 3),
            "pump_synchronization": data.get("sync", {}),
            "daily_heating": data.get("daily", {}),
            "floor_analysis": data.get("floor_analysis", {}),
            "floor_performance": data.get("floor_performance", {}),
            "efficiency": data.get("efficiency", {}),
            "insights": build_heating_insights(data),
            "advanced_diagnostics": data.get("advanced_diagnostics", {}),
            "predictions": data.get("predictions", {}),
            "energy_history": data.get("energy_history", []),
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



ROOM_THERMAL_METRICS = {
    "temperature_change_1h": ("Temperature Change 1h", "°C", 1, "mdi:thermometer-chevron-up"),
    "temperature_change_6h": ("Temperature Change 6h", "°C", 1, "mdi:thermometer"),
    "temperature_change_24h": ("Temperature Change 24h", "°C", 1, "mdi:thermometer"),
    "heating_response_minutes": ("Heating Response", "min", 1, "mdi:timer-play-outline"),
    "cooling_rate_c_per_hour": ("Cooling Rate", "°C/h", 1, "mdi:snowflake-thermometer"),
    "heat_retention_hours_per_degree": ("Heat Retention", "h/°C", 1, "mdi:home-thermometer"),
    "window_open_demand_seconds_today": ("Window Open During Demand Today", "h", 3600, "mdi:window-open"),
}


class RoomThermalAnalyticsSensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """Room thermal response and window impact metric."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = "measurement"

    def __init__(self, coordinator, room_id: str, room_name: str, metric: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self.room_name = room_name
        self.metric = metric
        label, unit, divisor, icon = ROOM_THERMAL_METRICS[metric]
        self.divisor = divisor
        self._attr_name = f"{room_name} {label}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_unique_id = f"{DOMAIN}_{room_id}_{metric}"

    @property
    def native_value(self):
        room = self.coordinator.data.get("rooms", {}).get(self.room_id, {})
        value = room.get(self.metric)
        return round(value / self.divisor, 2) if value is not None else None


EFFICIENCY_METRICS = {
    "outdoor_temperature": ("Outdoor Temperature", "°C", "mdi:thermometer"),
    "solar_radiation_average": ("Solar Radiation Average", "W/m²", "mdi:white-balance-sunny"),
    "delta_t_average": ("Indoor–Outdoor Delta T", "°C", "mdi:thermometer-lines"),
    "heating_degree_days": ("Heating Degree Days Today", "°C·d", "mdi:chart-bell-curve"),
    "gas_energy_today_kwh": ("Gas Energy Today", "kWh", "mdi:fire"),
    "electricity_energy_today_kwh": ("Heating System Electricity Today", "kWh", "mdi:lightning-bolt"),
    "electricity_ground_floor_today_kwh": ("Downstairs Pump Electricity Today", "kWh", "mdi:lightning-bolt"),
    "electricity_upstairs_today_kwh": ("Upstairs Pump Electricity Today", "kWh", "mdi:lightning-bolt"),
    "electricity_boiler_today_kwh": ("Boiler Electricity Today", "kWh", "mdi:lightning-bolt"),
    "boiler_power_w": ("Boiler Electrical Power", "W", "mdi:lightning-bolt"),
    "ac_power_w": ("AC Electrical Power", "W", "mdi:lightning-bolt"),
    "ac_electricity_today_kwh": ("AC Electricity Today", "kWh", "mdi:lightning-bolt"),
    "estimated_ac_delivered_heat_today_kwh": ("Estimated AC Heat Today", "kWh", "mdi:heat-wave"),
    "gas_kwh_per_degree_day": ("Gas Energy per Degree Day", "kWh/°C·d", "mdi:chart-line"),
    "estimated_delivered_heat_today_kwh": ("Estimated Delivered Heat Today", "kWh", "mdi:heat-wave"),
    "heat_loss_coefficient_w_k": ("Daily Heat Loss Coefficient", "W/K", "mdi:home-thermometer"),
    "heat_loss_u_value_daily": ("Previous Day Heat Loss per Envelope Area", "W/m²K", "mdi:home-thermometer"),
    "heat_loss_u_value_7d": ("7-day Heat Loss per Envelope Area", "W/m²K", "mdi:chart-line"),
    "heat_loss_u_value_30d": ("30-day Heat Loss per Envelope Area", "W/m²K", "mdi:chart-line"),
}


class EfficiencySensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Whole-house energy and environmental metric."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = "measurement"

    def __init__(self, coordinator, metric: str) -> None:
        super().__init__(coordinator)
        label, unit, icon = EFFICIENCY_METRICS[metric]
        self.metric = metric
        self._attr_name = label
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_unique_id = f"{DOMAIN}_{metric}"
        if metric == "estimated_delivered_heat_today_kwh":
            self._attr_device_class = "energy"
            self._attr_state_class = "total"

    @property
    def native_value(self):
        return self.coordinator.data.get("efficiency", {}).get(self.metric)

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


class RoomAirQualitySensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """Live room humidity or CO2 measurement."""

    _attr_should_poll = False

    def __init__(self, coordinator, room_id: str, room_name: str, metric: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self.metric = metric
        self._attr_name = f"{room_name} {'Humidity' if metric == 'humidity' else 'CO2'}"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_{metric}"
        self._attr_native_unit_of_measurement = "%" if metric == "humidity" else "ppm"
        self._attr_icon = "mdi:water-percent" if metric == "humidity" else "mdi:molecule-co2"

    @property
    def native_value(self):
        return self.coordinator.data.get("rooms", {}).get(self.room_id, {}).get(self.metric)

    @property
    def extra_state_attributes(self):
        room = self.coordinator.data.get("rooms", {}).get(self.room_id, {})
        key = "humidity_status" if self.metric == "humidity" else "co2_status"
        return {"air_quality_status": room.get(key)}
