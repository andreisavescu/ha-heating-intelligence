"""Heating Intelligence sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
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
    CONF_ATTIC_TEMPERATURE_ENTITY, CONF_ATTIC_HUMIDITY_ENTITY, CONF_WEATHER_ENTITY,
    CONF_HEATING_GAS_ENTITY, CONF_DHW_GAS_ENTITY,
)
from .coordinator import HeatingIntelligenceCoordinator
from .devices import HouseDeviceMixin, RoomDeviceMixin, PumpDeviceMixin
from .insights import build_heating_insights


# Human-readable calculation metadata surfaced in Home Assistant entity details.
CALCULATION_DETAILS = {
    "heating_input_energy_kwh": ("Cumulative heating-gas meter delta converted to kWh and summed.", "Σ(max(0, current meter − previous meter) × unit conversion)", "Configured heating gas meter; gas conversion factor when the source unit is m³."),
    "dhw_input_energy_kwh": ("Cumulative DHW-gas meter delta converted to kWh and summed.", "Σ(max(0, current DHW meter − previous DHW meter) × unit conversion)", "Configured DHW gas meter; DHW is kept separate from space-heating allocation."),
    "boiler_generated_heat_kwh": ("Heating gas input multiplied by the configured boiler-efficiency assumption.", "heating gas input (kWh) × boiler efficiency assumption", "Default boiler efficiency assumption is 0.90; DHW is excluded."),
    "boiler_allocated_heat_kwh": ("Gas-derived useful heat allocated to rooms by burner-on exposure.", "interval useful heat × room exposure ÷ total room exposure", "Room exposure accumulates configured circuit flow × modulation-derived output × burner-on seconds."),
    "boiler_unallocated_heat_kwh": ("Gas-derived useful heat that has no confirmed room exposure.", "max(0, useful heat − allocated heat)", "Only confirmed active UFH room exposure is eligible."),
    "boiler_modulation_estimated_output_kw": ("Estimated live boiler output from burner modulation.", "if burner is on: max(3.2 kW, 25 kW × modulation % ÷ 100); otherwise 0 kW", "25 kW rated output and 3.2 kW minimum firing output assumptions; not a heat-meter reading."),
    "boiler_modulation_calibrated_output_kw": ("Modulation-estimated output multiplied by the cumulative gas-derived calibration factor.", "modulation-estimated output (kW) × calibration factor", "Calibration factor = cumulative gas-derived useful heat ÷ cumulative modulation-estimated energy; approximate if DHW overlaps."),
    "boiler_calibration_factor": ("Cumulative ratio between gas-derived useful heat and modulation-estimated energy.", "Σ gas-derived useful heat (kWh) ÷ Σ modulation-estimated energy (kWh)", "Diagnostic calibration only; not applied to the separate modulation room-energy estimates."),
    "boiler_modulation_energy_today_kwh": ("Integrated modulation-estimated boiler output during eligible active-flow intervals today.", "Σ(estimated boiler output (kW) × interval seconds ÷ 3600)", "Burner must be on and at least one configured UFH circuit must have confirmed active flow."),
    "boiler_modulation_energy_week_kwh": ("Sum of daily modulation-estimated energy from the current calendar week.", "Σ daily modulation-estimated energy (kWh), Monday–today", "Uses retained daily ledger."),
    "boiler_modulation_energy_month_kwh": ("Sum of daily modulation-estimated energy from the current calendar month.", "Σ daily modulation-estimated energy (kWh), month start–today", "Uses retained daily ledger."),
    "boiler_modulation_energy_total_kwh": ("Lifetime sum of eligible modulation-estimated energy.", "Σ(estimated boiler output (kW) × interval seconds ÷ 3600)", "Lifetime accumulator; only intervals with active configured UFH flow are counted."),
    "estimated_thermal_power_w": ("Configured active circuit thermal output divided equally among served rooms.", "flow (L/min) ÷ 60 × 1 kg/L × 4186 J/(kg·K) × 9°C ÷ number of served rooms", "Manual configured flow; water density 1 kg/L, heat capacity 4186 J/(kg·K), assumed water ΔT 9°C."),
    "thermal_energy_today_kwh": ("Accumulated estimated circuit heat allocated equally among served rooms today.", "Σ(circuit power (W) × elapsed seconds ÷ 3,600,000 ÷ served-room count)", "Only confirmed active circuits contribute; interval is capped at 90 seconds."),
    "thermal_energy_week_kwh": ("Accumulated estimated circuit heat allocated equally among served rooms this ISO week.", "Σ(circuit power (W) × elapsed seconds ÷ 3,600,000 ÷ served-room count)", "Resets at ISO week boundary; only confirmed active circuits contribute."),
    "thermal_energy_month_kwh": ("Accumulated estimated circuit heat allocated equally among served rooms this calendar month.", "Σ(circuit power (W) × elapsed seconds ÷ 3,600,000 ÷ served-room count)", "Resets at calendar month boundary; only confirmed active circuits contribute."),
    "thermal_energy_total_kwh": ("Lifetime accumulated estimated circuit heat allocated equally among served rooms.", "Σ(circuit power (W) × elapsed seconds ÷ 3,600,000 ÷ served-room count)", "Only confirmed active circuits contribute; estimated, not measured."),
    "demand_seconds_today": ("Room demand duration accumulated during the local calendar day.", "Σ elapsed seconds while configured room demand is on ÷ 3600", "Reported in hours; source demand entity state."),
    "demand_seconds_week": ("Room demand duration accumulated during the current calendar week.", "Σ elapsed seconds while configured room demand is on ÷ 3600", "Reported in hours; resets at week boundary."),
    "demand_seconds_month": ("Room demand duration accumulated during the current calendar month.", "Σ elapsed seconds while configured room demand is on ÷ 3600", "Reported in hours; resets at month boundary."),
    "average_cycle_seconds_week": ("Mean duration of recorded heating cycles in the current week.", "Σ completed cycle duration ÷ number of completed cycles ÷ 60", "Reported in minutes; based on observed demand transitions."),
    "shortest_cycle_seconds_week": ("Shortest recorded heating cycle duration in the current week.", "min(completed cycle durations) ÷ 60", "Reported in minutes."),
    "longest_cycle_seconds_week": ("Longest recorded heating cycle duration in the current week.", "max(completed cycle durations) ÷ 60", "Reported in minutes."),
    "active_cycle_seconds": ("Elapsed duration of the currently active room-demand cycle.", "current time − active cycle start time, in minutes", "Only present while demand is active."),
    "temperature_change_1h": ("Latest room temperature minus the latest retained sample at least one hour old.", "T(latest) − T(sample at or before now − 1 h)", "Sparse temperature samples; may be unavailable until a qualifying sample exists."),
    "temperature_change_6h": ("Latest room temperature minus the latest retained sample at least six hours old.", "T(latest) − T(sample at or before now − 6 h)", "Sparse temperature samples."),
    "temperature_change_24h": ("Latest room temperature minus the latest retained sample at least 24 hours old.", "T(latest) − T(sample at or before now − 24 h)", "Sparse temperature samples."),
    "heating_response_minutes": ("Average recent time from demand start to a 0.5°C rise above the preceding sample.", "mean(time to baseline temperature + 0.5°C) over up to 20 recent cycles", "Requires sampled demand cycles and sufficient temperature observations."),
    "cooling_rate_c_per_hour": ("Positive temperature drop over the most recent continuous no-demand, closed-window period of at least 30 minutes.", "(T(start) − T(end)) ÷ elapsed hours", "Observed trend, not a physical heat-loss coefficient."),
    "heat_retention_hours_per_degree": ("Inverse of the observed positive cooling rate.", "1 ÷ cooling rate (°C/h)", "Observed indicator; unavailable when cooling rate is zero or unknown."),
    "attic_to_outdoor_delta_c": ("Attic temperature minus outdoor temperature.", "T(attic) − T(outdoor)", "Uses measured temperature inputs."),
    "upstairs_to_attic_delta_c": ("Average upstairs temperature minus attic temperature.", "T(upstairs average) − T(attic)", "Uses measured temperature inputs."),
    "estimated_roof_heat_flow_w": ("Estimated conductive heat flow through the roof.", "roof U-value (W/m²K) × roof area (m²) × (T(attic) − T(outdoor))", "Requires explicitly configured roof U-value and area; positive is outward."),
    "estimated_ceiling_heat_flow_w": ("Estimated conductive heat flow through the upstairs ceiling.", "ceiling U-value (W/m²K) × ceiling area (m²) × (T(upstairs) − T(attic))", "Requires explicitly configured ceiling U-value and area; positive is toward attic."),
    "heating_degree_days": ("Daily heating degree days relative to an 18°C base temperature.", "max(0, 18°C − daily mean outdoor temperature)", "Uses the daily mean outdoor temperature."),
    "gas_kwh_per_degree_day": ("Daily gas energy divided by heating degree days.", "gas energy today (kWh) ÷ heating degree days today (°C·d)", "Unavailable when heating degree days are zero or missing."),
    "estimated_ac_delivered_heat_today_kwh": ("Estimated AC heat delivered from electrical energy and assumed heating COP.", "AC electricity today (kWh) × configured AC heating COP", "COP is an assumption, not measured thermal output."),
    "estimated_delivered_heat_today_kwh": ("Estimated useful heat from boiler gas plus estimated AC heat.", "gas energy today × boiler efficiency + AC electricity today × AC heating COP", "Default assumptions: boiler efficiency 0.90 and AC COP 3.0."),
    "heat_loss_coefficient_w_k": ("Previous completed day's estimated delivered heat divided by indoor–outdoor degree-hours.", "delivered heat (kWh) × 1000 ÷ degree-hours (K·h)", "Requires at least 24 degree-hours; includes configured boiler efficiency and AC COP assumptions."),
    "heat_loss_u_value_daily": ("Previous completed day's heat-loss coefficient divided by envelope area.", "heat-loss coefficient (W/K) ÷ envelope area (m²)", "Uses configured envelope area; daily result finalises after 02:00."),
    "heat_loss_u_value_7d": ("Aggregated delivered heat divided by aggregated degree-hours and envelope area over valid completed days.", "Σ delivered heat (kWh) × 1000 ÷ Σ degree-hours ÷ envelope area (m²)", "Requires at least 5 valid completed days and 120 degree-hours."),
    "heat_loss_u_value_30d": ("Aggregated delivered heat divided by aggregated degree-hours and envelope area over valid completed days.", "Σ delivered heat (kWh) × 1000 ÷ Σ degree-hours ÷ envelope area (m²)", "Requires at least 20 valid completed days and 400 degree-hours."),
}


def _calculation_attributes(metric: str) -> dict:
    details = CALCULATION_DETAILS.get(metric)
    if not details:
        return {}
    description, formula, assumptions = details
    return {
        "calculation_description": description,
        "calculation_formula": formula,
        "calculation_assumptions": assumptions,
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HeatingIntelligenceCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [HeatingOverviewSensor(coordinator)]
    if entry.options.get(CONF_HEATING_GAS_ENTITY):
        entities.extend(BoilerExperimentSensor(coordinator, metric) for metric in (
            "heating_input_energy_kwh", "dhw_input_energy_kwh",
            "boiler_generated_heat_kwh", "boiler_allocated_heat_kwh",
            "boiler_unallocated_heat_kwh"))
        entities.extend(BoilerExperimentDiagnosticSensor(coordinator, metric) for metric in (
            "boiler_modulation_estimated_output_kw",
            "boiler_modulation_calibrated_output_kw",
            "boiler_calibration_factor"))
    if entry.options.get(CONF_HEATING_GAS_ENTITY):
        for room in entry.options.get(CONF_ROOMS, []):
            entities.append(RoomBoilerExperimentEnergySensor(coordinator, room["id"], room["name"]))
    # Whole-boiler modulation-derived energy is independent of gas-meter availability.
    entities.extend(BoilerModulationEnergySensor(coordinator, period)
        for period in ("daily", "weekly", "monthly", "total"))
    # Per-room modulation-derived energy is also independent of gas-meter availability.
    for room in entry.options.get(CONF_ROOMS, []):
        entities.extend(ModulationRoomEnergySensor(coordinator, room["id"], room["name"], period)
            for period in ("daily", "weekly", "monthly", "total"))
    if entry.options.get(CONF_ATTIC_TEMPERATURE_ENTITY):
        entities.append(AtticTemperatureSensor(coordinator))
        entities.extend(AtticThermalMetricSensor(coordinator, metric) for metric in (
            "attic_to_outdoor_delta_c", "upstairs_to_attic_delta_c",
            "estimated_roof_heat_flow_w", "estimated_ceiling_heat_flow_w"))
    if entry.options.get(CONF_ATTIC_HUMIDITY_ENTITY):
        entities.append(AtticHumiditySensor(coordinator))
    if entry.options.get("water_temperature_entity"):
        entities.append(WaterTemperatureSensor(coordinator))
    for room in entry.options.get(CONF_ROOMS, []):
        if room.get("temperature_entity"):
            entities.append(RoomTemperatureSensor(coordinator, room["id"], room["name"]))
        entities.append(RoomThermalPowerSensor(coordinator, room["id"], room["name"]))
        entities.extend(
            RoomThermalEnergySensor(coordinator, room["id"], room["name"], metric)
            for metric in ROOM_THERMAL_ENERGY_METRICS
        )
        if room.get("humidity_entity"):
            entities.append(RoomAirQualitySensor(coordinator, room["id"], room["name"], "humidity"))
        if room.get("co2_entity"):
            entities.append(RoomAirQualitySensor(coordinator, room["id"], room["name"], "co2_ppm"))
        if room.get("demand_entity"):
            entities.extend(
                RoomAnalyticsSensor(coordinator, room["id"], room["name"], metric)
                for metric in ROOM_ANALYTICS_METRICS
            )
        thermal_metrics = []
        if room.get("temperature_entity"):
            thermal_metrics.extend(
                metric for metric in ROOM_THERMAL_METRICS
                if metric != "window_open_demand_seconds_today"
            )
        if room.get("temperature_entity") and room.get("demand_entity") and room.get("window_entity"):
            thermal_metrics.append("window_open_demand_seconds_today")
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
    if entry.options.get(CONF_OUTDOOR_TEMPERATURE_ENTITY) or entry.options.get(CONF_WEATHER_ENTITY):
        entities.append(EfficiencySensor(coordinator, "heating_degree_days"))
    if entry.options.get(CONF_PUMPS) or boiler_energy_configured:
        entities.append(EfficiencySensor(coordinator, "electricity_energy_today_kwh"))
    if entry.options.get(CONF_GAS_CONSUMPTION_ENTITY) and (entry.options.get(CONF_OUTDOOR_TEMPERATURE_ENTITY) or entry.options.get(CONF_WEATHER_ENTITY)):
        entities.append(EfficiencySensor(coordinator, "gas_kwh_per_degree_day"))
    if (entry.options.get(CONF_GAS_CONSUMPTION_ENTITY) or entry.options.get(CONF_AC_ENERGY_ENTITY)) and entry.options.get(CONF_DELTA_T_ENTITY):
        entities.extend(EfficiencySensor(coordinator, metric) for metric in (
            "estimated_delivered_heat_today_kwh", "heat_loss_coefficient_w_k",
            "heat_loss_u_value_daily", "heat_loss_u_value_7d", "heat_loss_u_value_30d"))
    async_add_entities(entities)



class BoilerExperimentSensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Gas-calibrated experimental boiler energy totals."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL

    LABELS = {
        "heating_input_energy_kwh": "Experimental Heating Gas Input",
        "dhw_input_energy_kwh": "Experimental DHW Gas Input",
        "boiler_generated_heat_kwh": "Experimental Boiler Generated Heat",
        "boiler_allocated_heat_kwh": "Experimental Boiler Heat Allocated",
        "boiler_unallocated_heat_kwh": "Experimental Boiler Heat Unallocated",
    }

    def __init__(self, coordinator, metric: str) -> None:
        super().__init__(coordinator)
        self.metric = metric
        self._attr_name = self.LABELS[metric]
        self._attr_unique_id = f"{DOMAIN}_{metric}"

    @property
    def native_value(self):
        return self.coordinator.data.get("boiler_energy_experiment", {}).get(self.metric)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data.get("boiler_energy_experiment", {})
        return {**data, **_calculation_attributes(self.metric)}


class BoilerExperimentDiagnosticSensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Live modulation output and gas-calibration diagnostics."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    METRICS = {
        "boiler_modulation_estimated_output_kw": ("Modulation Estimated Boiler Output", "kW"),
        "boiler_modulation_calibrated_output_kw": ("Gas-Calibrated Boiler Output", "kW"),
        "boiler_calibration_factor": ("Boiler Modulation Calibration Factor", None),
    }

    def __init__(self, coordinator, metric: str) -> None:
        super().__init__(coordinator)
        self.metric = metric
        self._attr_name, self._attr_native_unit_of_measurement = self.METRICS[metric]
        self._attr_unique_id = f"{DOMAIN}_{metric}"
        self._attr_icon = "mdi:chart-bell-curve"

    @property
    def native_value(self):
        return self.coordinator.data.get("boiler_energy_experiment", {}).get(self.metric)

    @property
    def extra_state_attributes(self):
        return _calculation_attributes(self.metric)


class BoilerModulationEnergySensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Whole-boiler modulation-estimated thermal energy by calendar period."""

    _attr_should_poll = False
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_icon = "mdi:heat-wave"

    PERIODS = {
        "daily": ("Today", SensorStateClass.TOTAL, "boiler_modulation_energy_today_kwh"),
        "weekly": ("This Week", SensorStateClass.TOTAL, "boiler_modulation_energy_week_kwh"),
        "monthly": ("This Month", SensorStateClass.TOTAL, "boiler_modulation_energy_month_kwh"),
        "total": ("Total", SensorStateClass.TOTAL_INCREASING, "boiler_modulation_energy_total_kwh"),
    }

    def __init__(self, coordinator, period: str) -> None:
        super().__init__(coordinator)
        label, state_class, metric = self.PERIODS[period]
        self.metric = metric
        self._attr_name = f"Boiler Estimated Modulation Thermal Energy {label}"
        self._attr_unique_id = f"{DOMAIN}_boiler_modulation_estimated_heat_{period}"
        self._attr_state_class = state_class

    @property
    def native_value(self):
        return self.coordinator.data.get("boiler_energy_experiment", {}).get(self.metric, 0.0)

    @property
    def extra_state_attributes(self):
        return {
            "estimate": True,
            "method": "Boiler output estimated from burner modulation and integrated over time; allocated total includes only intervals with active, configured UFH room flow.",
            "source": "boiler_modulation",
            "calibration_applied": False,
            **_calculation_attributes(self.metric),
        }


class ModulationRoomEnergySensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """Modulation-estimated boiler energy allocated to a room by period."""

    _attr_should_poll = False
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_icon = "mdi:heat-wave"

    PERIODS = {
        "daily": ("Estimated Modulation Thermal Energy Today", SensorStateClass.TOTAL, "boiler_modulation_daily_room_energy_kwh"),
        "weekly": ("Estimated Modulation Thermal Energy This Week", SensorStateClass.TOTAL, "boiler_modulation_weekly_room_energy_kwh"),
        "monthly": ("Estimated Modulation Thermal Energy This Month", SensorStateClass.TOTAL, "boiler_modulation_monthly_room_energy_kwh"),
        "total": ("Estimated Modulation Thermal Energy Total", SensorStateClass.TOTAL_INCREASING, "boiler_modulation_room_energy_kwh"),
    }

    def __init__(self, coordinator, room_id: str, room_name: str, period: str) -> None:
        super().__init__(coordinator)
        label, state_class, metric = self.PERIODS[period]
        self.room_id = room_id
        self.room_name = room_name
        self.metric = metric
        self._attr_name = f"{room_name} {label}"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_modulation_estimated_heat_{period}"
        self._attr_state_class = state_class

    @property
    def native_value(self):
        return self.coordinator.data.get("boiler_energy_experiment", {}).get(
            self.metric, {}
        ).get(self.room_id, 0.0)

    @property
    def extra_state_attributes(self):
        experiment = self.coordinator.data.get("boiler_energy_experiment", {})
        interval = experiment.get("boiler_modulation_room_interval_diagnostics", {}).get(self.room_id, {})
        return {
            "estimate": True,
            "method": "Boiler heat output estimated from burner modulation, then allocated to active UFH circuits by configured flow and split among served rooms.",
            "source": "boiler_modulation",
            **_calculation_attributes("boiler_modulation_energy_total_kwh"),
            "last_interval_burner_on": experiment.get("boiler_modulation_interval_burner_on"),
            "last_interval_modulation_percent": experiment.get("boiler_modulation_interval_percent"),
            "last_interval_boiler_output_kw": experiment.get("boiler_modulation_interval_output_kw"),
            "last_interval_duration_seconds": experiment.get("boiler_modulation_interval_seconds"),
            "last_interval_room_flow_active": interval.get("flow_active", False),
            "last_interval_allocation_weight_l_min": interval.get("allocation_weight_l_min", 0.0),
            "last_interval_allocation_share": interval.get("allocation_share", 0.0),
            "last_interval_allocated_energy_kwh": interval.get("interval_energy_kwh", 0.0),
            "last_interval_exposure_kw_seconds": interval.get("exposure_kw_seconds", 0.0),
        }


class RoomBoilerExperimentEnergySensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """Experimental gas-calibrated boiler heat allocated to a room."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, room_id: str, room_name: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self._attr_name = f"{room_name} Experimental Boiler Heat"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_boiler_experiment_energy"

    @property
    def native_value(self):
        return self.coordinator.data.get("boiler_energy_experiment", {}).get(
            "boiler_room_energy_kwh", {}
        ).get(self.room_id, 0.0)

    @property
    def extra_state_attributes(self):
        return {
            "calculation_description": "Gas-derived useful boiler heat allocated to rooms using their accumulated burner-on exposure.",
            "calculation_formula": "Σ(interval useful heat × room exposure ÷ total room exposure)",
            "calculation_assumptions": "Heating gas only; DHW excluded. Exposure is configured circuit flow × modulation-derived output × burner-on seconds.",
        }

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
            "indoor_air_quality": data.get("indoor_air_quality", {}),
            "thermal_model": data.get("thermal_model", {}),
            "weather": data.get("weather", {}),
            "attic_thermal": data.get("attic_thermal", {}),
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
            "boiler_context": data.get("boiler_context", {}),
            "pump_sequence": data.get("pump_sequence", {}),
            "ufh_hydraulics": data.get("ufh_hydraulics", {}),
            "boiler_energy_experiment": data.get("boiler_energy_experiment", {}),
            "algorithm_daily_comparison": data.get("algorithm_daily_comparison", []),
            "daily_heating": data.get("daily", {}),
            "floor_analysis": data.get("floor_analysis", {}),
            "floor_performance": data.get("floor_performance", {}),
            "efficiency": data.get("efficiency", {}),
            "insights": build_heating_insights(data),
            "advanced_diagnostics": data.get("advanced_diagnostics", {}),
            "predictions": data.get("predictions", {}),
            "energy_history": data.get("energy_history", []),
            "system_history": data.get("system_history", []),
        }



class AtticTemperatureSensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Temperature measured in the attic above the insulation."""

    _attr_native_unit_of_measurement = "°C"
    _attr_should_poll = False
    _attr_icon = "mdi:home-thermometer"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Attic Temperature"
        self._attr_unique_id = f"{DOMAIN}_attic_temperature"

    @property
    def native_value(self):
        return self.coordinator.data.get("attic_thermal", {}).get("attic_temperature")

    @property
    def available(self):
        return super().available and self.native_value is not None


class AtticHumiditySensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Relative humidity measured in the attic."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False
    _attr_icon = "mdi:water-percent"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Attic Humidity"
        self._attr_unique_id = f"{DOMAIN}_attic_humidity"

    @property
    def native_value(self):
        return self.coordinator.data.get("attic_thermal", {}).get("attic_humidity")

    @property
    def available(self):
        return super().available and self.native_value is not None


ATTIC_THERMAL_METRICS = {
    "attic_to_outdoor_delta_c": ("Attic–Outdoor Temperature Difference", "°C", "mdi:thermometer"),
    "upstairs_to_attic_delta_c": ("Upstairs–Attic Temperature Difference", "°C", "mdi:thermometer-chevron-down"),
    "estimated_roof_heat_flow_w": ("Estimated Attic Roof Heat Flow", "W", "mdi:home-thermometer"),
    "estimated_ceiling_heat_flow_w": ("Estimated Upstairs Ceiling Heat Flow", "W", "mdi:home-thermometer"),
}


class AtticThermalMetricSensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Measured attic temperature gradient or conditional heat-flow estimate."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, metric: str) -> None:
        super().__init__(coordinator)
        label, unit, icon = ATTIC_THERMAL_METRICS[metric]
        self.metric = metric
        self._attr_name = label
        self._attr_unique_id = f"{DOMAIN}_{metric}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

    @property
    def native_value(self):
        return self.coordinator.data.get("attic_thermal", {}).get(self.metric)

    @property
    def extra_state_attributes(self):
        return {**self.coordinator.data.get("attic_thermal", {}), **_calculation_attributes(self.metric)}


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


ROOM_THERMAL_ENERGY_METRICS = {
    "thermal_energy_today_kwh": ("Estimated UFH Thermal Energy Today", SensorStateClass.TOTAL),
    "thermal_energy_week_kwh": ("Estimated UFH Thermal Energy This Week", SensorStateClass.TOTAL),
    "thermal_energy_month_kwh": ("Estimated UFH Thermal Energy This Month", SensorStateClass.TOTAL),
    "thermal_energy_total_kwh": ("Estimated UFH Thermal Energy Total", SensorStateClass.TOTAL_INCREASING),
}


class RoomThermalPowerSensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """Estimated instantaneous UFH thermal power allocated to a room."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"
    _attr_icon = "mdi:heat-wave"

    def __init__(self, coordinator, room_id: str, room_name: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self.room_name = room_name
        self._attr_name = f"{room_name} Estimated UFH Heating Power"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_estimated_thermal_power_w"

    @property
    def native_value(self):
        room = self.coordinator.data.get("rooms", {}).get(self.room_id, {})
        return room.get("estimated_thermal_power_w", 0.0)

    @property
    def extra_state_attributes(self):
        return {"estimate": True, "method": "Active configured circuit thermal power is split equally among served rooms; circuit power uses configured flow and an assumed 9°C water delta.", "assumed_water_delta_t_c": 9.0, **_calculation_attributes("estimated_thermal_power_w")}


class RoomThermalEnergySensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """Estimated UFH heat delivered to a room, allocated from active circuits."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:heat-wave"

    def __init__(self, coordinator, room_id: str, room_name: str, metric: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self.room_name = room_name
        self.metric = metric
        label, state_class = ROOM_THERMAL_ENERGY_METRICS[metric]
        self._attr_name = f"{room_name} {label}"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_{metric}"
        self._attr_state_class = state_class

    @property
    def native_value(self):
        room = self.coordinator.data.get("rooms", {}).get(self.room_id, {})
        return room.get(self.metric, 0.0)

    @property
    def extra_state_attributes(self):
        return {
            "estimate": True,
            "method": "Configured circuit flow × assumed 9°C water delta; active circuit energy is split equally among its assigned demand rooms.",
            "assumed_water_delta_t_c": 9.0,
            **_calculation_attributes(self.metric),
        }


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
            **_calculation_attributes(self.metric),
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

    @property
    def extra_state_attributes(self):
        return _calculation_attributes(self.metric)


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

    @property
    def extra_state_attributes(self):
        return _calculation_attributes(self.metric)

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
        self.room_name = room_name
        self.metric = metric
        self._attr_name = f"{room_name} {'Humidity' if metric == 'humidity' else 'CO2'}"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_{metric}"
        self._attr_native_unit_of_measurement = "%" if metric == "humidity" else "ppm"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:water-percent" if metric == "humidity" else "mdi:molecule-co2"

    @property
    def native_value(self):
        return self.coordinator.data.get("rooms", {}).get(self.room_id, {}).get(self.metric)

    @property
    def extra_state_attributes(self):
        room = self.coordinator.data.get("rooms", {}).get(self.room_id, {})
        key = "humidity_status" if self.metric == "humidity" else "co2_status"
        return {
            "air_quality_status": room.get(key),
            "room_air_assessment": room.get("air_quality_assessment", {}),
            "co2_ppm": room.get("co2_ppm"),
            "relative_humidity_percent": room.get("humidity"),
        }
