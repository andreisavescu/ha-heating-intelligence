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
from .devices import AuditDeviceMixin, HouseDeviceMixin, RoomDeviceMixin, PumpDeviceMixin
from .insights import build_heating_insights


# Human-readable calculation metadata surfaced in Home Assistant entity details.
CALCULATION_DETAILS = {
    "heating_input_energy_kwh": ("Cumulative heating-gas meter delta converted to kWh and summed.", "Σ(max(0, current meter − previous meter) × unit conversion)", "Configured heating gas meter; gas conversion factor when the source unit is m³."),
    "dhw_input_energy_kwh": ("Cumulative DHW-gas meter delta converted to kWh and summed.", "Σ(max(0, current DHW meter − previous DHW meter) × unit conversion)", "Configured DHW gas meter; DHW is kept separate from space-heating allocation."),
    "boiler_generated_heat_kwh": ("Heating gas input multiplied by the configured boiler-efficiency assumption.", "heating gas input (kWh) × boiler efficiency assumption", "Default boiler efficiency assumption is 0.90; DHW is excluded."),
    "boiler_allocated_heat_kwh": ("Gas-derived useful heat allocated to rooms by burner-on exposure.", "interval useful heat × room exposure ÷ total room exposure", "Room exposure accumulates configured circuit flow × modulation-derived output × burner-on seconds."),
    "boiler_unallocated_heat_kwh": ("Gas-derived useful heat that has no confirmed room exposure.", "max(0, useful heat − allocated heat)", "Only confirmed active UFH room exposure is eligible."),
    "boiler_modulation_estimated_output_kw": ("Estimated live boiler output from burner modulation.", "max(3.2 kW, 25 kW × modulation % ÷ 100) when modulation is positive; otherwise 0 kW", "25 kW rated output and 3.2 kW minimum firing output assumptions; modulation is the live firing signal, not a heat-meter reading."),
    "boiler_modulation_calibrated_output_kw": ("Live boiler output estimated from modulation and the smoothed daily gas calibration factor.", "max(3.2 kW, 25 kW × modulation % ÷ 100) × (0.66 × previous factor + 0.34 × previous-day observed factor)", "The previous complete day's gas-reconciled useful heat is divided by its raw modulation estimate; the smoothed factor is applied from the following day. If either daily total is missing, the prior factor is retained."),
    "boiler_calibration_factor": ("Daily smoothed calibration factor applied to live modulation-derived boiler output.", "0.66 × previous factor + 0.34 × (previous-day gas-reconciled useful heat ÷ previous-day raw modulation energy)", "Updated once per completed day when both daily totals are positive; the previous factor is retained when data is missing."),
    "boiler_modulation_room_energy_kwh": ("Modulation-estimated boiler energy apportioned to this room by its share of active configured UFH circuit flow.", "interval modulation energy × room flow weight ÷ sum of all eligible room flow weights", "Only circuits with confirmed active flow and configured flow contribute; shared circuits split their flow equally among served rooms; the smoothed gas-derived calibration factor is applied to modulation output before room allocation."),
    "boiler_modulation_energy_today_kwh": ("Integrated modulation-estimated boiler output during eligible active-flow intervals today.", "Σ(calibrated modulation output (kW) × interval seconds ÷ 3600)", "Modulation must be positive and at least one configured UFH circuit must have confirmed active flow; the delayed burner binary does not gate integration."),
    "boiler_modulation_energy_week_kwh": ("Sum of daily modulation-estimated energy from the current calendar week.", "Σ daily modulation-estimated energy (kWh), Monday–today", "Uses retained daily ledger."),
    "boiler_modulation_energy_month_kwh": ("Sum of daily modulation-estimated energy from the current calendar month.", "Σ daily modulation-estimated energy (kWh), month start–today", "Uses retained daily ledger."),
    "boiler_modulation_energy_total_kwh": ("Lifetime sum of eligible modulation-estimated energy.", "Σ(estimated boiler output (kW) × interval seconds ÷ 3600)", "Lifetime accumulator; only intervals with active configured UFH flow are counted."),
    "demand_seconds_today": ("Room demand duration accumulated during the local calendar day.", "Σ elapsed seconds while configured room demand is on ÷ 3600", "Reported in hours; source demand entity state."),
    "demand_seconds_week": ("Room demand duration accumulated during the current calendar week.", "Σ elapsed seconds while configured room demand is on ÷ 3600", "Reported in hours; resets at week boundary."),
    "demand_seconds_month": ("Room demand duration accumulated during the current calendar month.", "Σ elapsed seconds while configured room demand is on ÷ 3600", "Reported in hours; resets at month boundary."),
    "average_cycle_seconds_week": ("Mean duration of recorded heating cycles in the current week.", "Σ completed cycle duration ÷ number of completed cycles ÷ 60", "Reported in minutes; based on observed demand transitions."),
    "shortest_cycle_seconds_week": ("Shortest recorded heating cycle duration in the current week.", "min(completed cycle durations) ÷ 60", "Reported in minutes."),
    "longest_cycle_seconds_week": ("Longest recorded heating cycle duration in the current week.", "max(completed cycle durations) ÷ 60", "Reported in minutes."),
    "active_cycle_seconds": ("Elapsed duration of the currently active room-demand cycle.", "current time − active cycle start time, in minutes", "Only present while demand is active."),
    "window_open_demand_seconds_today": ("Room demand duration while the configured window is open today.", "Σ elapsed seconds where demand is on AND window is open ÷ 3600", "Requires known demand and window states; reported in hours."),
    "pump_energy": ("Accumulated electrical energy consumed by this pump.", "Σ(power (W) × elapsed seconds ÷ 3,600,000)", "Integrated from sampled pump power; sampling gaps and the integration's elapsed-time cap affect the estimate."),
    "pump_runtime": ("Accumulated time the pump is classified as running.", "Σ elapsed seconds while pump running ÷ 3600", "Based on the pump power running threshold and coordinator observations."),
    "pump_cycles": ("Count of observed pump off-to-on transitions.", "count(pump state changes from off to on)", "Cycles are observed by the integration and may miss transitions during Home Assistant downtime."),
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
    entities = [HeatingOverviewSensor(coordinator), UFHHydraulicAuditSensor(coordinator), HouseAuditSensor(coordinator), ThermalReadinessAuditSensor(coordinator), MeasurementAuditSensor(coordinator), PhysicalModelExperimentsSensor(coordinator), PhysicalModelSimulationAuditSensor(coordinator), EnvelopeConductanceSensor(coordinator), Stage3ThermalCapacitySensor(coordinator), Stage5UfhSlabModelSensor(coordinator), Stage6GainModelSensor(coordinator)]
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
            entities.extend(GasReconciledRoomEnergySensor(coordinator, room["id"], room["name"], period)
                for period in ("daily", "weekly", "monthly", "season"))
    # Whole-boiler modulation-derived energy is independent of gas-meter availability.
    entities.extend(BoilerModulationEnergySensor(coordinator, period)
        for period in ("daily", "weekly", "monthly", "season"))
    if entry.options.get(CONF_HEATING_GAS_ENTITY):
        entities.extend(GasReconciledEnergySensor(coordinator, period)
            for period in ("daily", "weekly", "monthly", "season"))
    # Per-room modulation-derived energy is also independent of gas-meter availability.
    for room in entry.options.get(CONF_ROOMS, []):
        entities.extend(ModulationRoomEnergySensor(coordinator, room["id"], room["name"], period)
            for period in ("daily", "weekly", "monthly", "season"))
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
        entities.append(RoomModulationPowerSensor(coordinator, room["id"], room["name"]))
        entities.extend(
            RoomConductanceSensor(coordinator, room["id"], room["name"], metric)
            for metric in ("envelope_conductance_w_per_k", "attic_floor_conductance_w_per_k")
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
        entities.extend(
            PumpCountdownSensor(coordinator, floor_id, label)
            for floor_id, label in (
                ("ground_floor", "Downstairs"),
                ("upstairs", "Upstairs"),
            )
        )
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
        "season": ("This Heating Season", SensorStateClass.TOTAL, "boiler_modulation_energy_heating_season_kwh"),
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
        "season": ("Estimated Modulation Thermal Energy This Heating Season", SensorStateClass.TOTAL, "boiler_modulation_heating_season_room_energy_kwh"),
    }

    def __init__(self, coordinator, room_id: str, room_name: str, period: str) -> None:
        super().__init__(coordinator)
        label, state_class, metric = self.PERIODS[period]
        self.room_id = room_id
        self.room_name = room_name
        self.metric = metric
        self._attr_name = f"{room_name} {label}"
        # Reuse the former UFH energy entity unique IDs so existing entity
        # registry entries, dashboards and Recorder history continue under the
        # new burner-modulation calculation. Gas-reconciled entities keep their
        # separate unique IDs.
        legacy_period_ids = {
            "daily": "thermal_energy_today_kwh",
            "weekly": "thermal_energy_week_kwh",
            "monthly": "thermal_energy_month_kwh",
            "season": "thermal_energy_season_kwh",
        }
        self._attr_unique_id = f"{DOMAIN}_{room_id}_{legacy_period_ids[period]}"
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
            **_calculation_attributes("boiler_modulation_room_energy_kwh"),
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


class GasReconciledEnergySensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Whole-boiler useful heat reconciled to the cumulative heating-gas meter."""

    _attr_should_poll = False
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:fire"
    PERIODS = {
        "daily": ("Today", "boiler_gas_reconciled_energy_today_kwh"),
        "weekly": ("This Week", "boiler_gas_reconciled_energy_week_kwh"),
        "monthly": ("This Month", "boiler_gas_reconciled_energy_month_kwh"),
        "season": ("This Heating Season", "boiler_gas_reconciled_energy_heating_season_kwh"),
    }

    def __init__(self, coordinator, period: str) -> None:
        super().__init__(coordinator)
        label, self.metric = self.PERIODS[period]
        self._attr_name = f"Boiler Gas-Reconciled Thermal Energy {label}"
        self._attr_unique_id = f"{DOMAIN}_gas_reconciled_thermal_energy_{period}"

    @property
    def native_value(self):
        return self.coordinator.data.get("boiler_energy_experiment", {}).get(self.metric, 0.0)


class GasReconciledRoomEnergySensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """Gas-meter-reconciled useful heat allocated to a room by UFH flow exposure."""

    _attr_should_poll = False
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:fire"
    PERIODS = {
        "daily": ("Today", "boiler_gas_reconciled_daily_room_energy_kwh"),
        "weekly": ("This Week", "boiler_gas_reconciled_weekly_room_energy_kwh"),
        "monthly": ("This Month", "boiler_gas_reconciled_monthly_room_energy_kwh"),
        "season": ("This Heating Season", "boiler_gas_reconciled_heating_season_room_energy_kwh"),
    }

    def __init__(self, coordinator, room_id: str, room_name: str, period: str) -> None:
        super().__init__(coordinator)
        label, self.metric = self.PERIODS[period]
        self.room_id = room_id
        self._attr_name = f"{room_name} Gas-Reconciled Thermal Energy {label}"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_gas_reconciled_thermal_energy_{period}"

    @property
    def native_value(self):
        return self.coordinator.data.get("boiler_energy_experiment", {}).get(self.metric, {}).get(self.room_id, 0.0)

    @property
    def extra_state_attributes(self):
        return {
            "source": "cumulative_heating_gas_meter",
            "method": "Heating-gas meter delta × configured gas conversion × boiler-efficiency assumption; allocated by recorded active UFH flow exposure.",
            "calculation_description": "Gas-reconciled useful boiler heat allocated to this room using its share of measured-period UFH flow exposure.",
            "calculation_formula": "Σ(interval gas-derived useful heat × room flow exposure ÷ total eligible room exposure)",
            "calculation_assumptions": "Heating gas only; DHW excluded. Configured active UFH circuit flow determines room shares. No instantaneous gas-derived room power is calculated.",
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
        self.room_name = room_name
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

class UFHHydraulicAuditSensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Diagnostic summary of configured UFH circuit topology."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "issues"
    _attr_icon = "mdi:pipe"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "UFH Hydraulic Audit"
        self._attr_unique_id = f"{DOMAIN}_ufh_hydraulic_audit"

    @property
    def native_value(self):
        return self.coordinator.data.get("ufh_topology_audit", {}).get("issue_count", 0)

    @property
    def extra_state_attributes(self):
        return {
            "audit": self.coordinator.data.get("ufh_topology_audit", {}),
            "heat_delivery_calibration": self.coordinator.data.get("heat_delivery_calibration", {}),
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
            "pwm_timers": data.get("pwm_timers", {}),
            "running_pumps": sum(1 for pump in pumps.values() if pump.get("running")),
            "total_pump_power": round(sum(p["power"] for p in pumps.values() if p.get("power") is not None), 1),
            "total_pump_energy": round(sum(p["energy"] for p in pumps.values() if p.get("energy") is not None), 3),
            "pump_synchronization": data.get("sync", {}),
            "boiler_context": data.get("boiler_context", {}),
            "pump_sequence": data.get("pump_sequence", {}),
            "ufh_hydraulics": data.get("ufh_hydraulics", {}),
            "ufh_topology_audit": data.get("ufh_topology_audit", {}),
            "heat_delivery_calibration": data.get("heat_delivery_calibration", {}),
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


class RoomModulationPowerSensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """Live room heat-power estimate allocated from calibrated boiler modulation."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"
    _attr_icon = "mdi:heat-wave"

    def __init__(self, coordinator, room_id: str, room_name: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self.room_name = room_name
        self._attr_name = f"{room_name} Estimated Modulation Heating Power"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_modulation_heating_power_w"

    @property
    def native_value(self):
        data = self.coordinator.data
        experiment = data.get("boiler_energy_experiment", {})
        circuits = data.get("ufh_hydraulics", {}).get("circuits", {})
        rooms = data.get("rooms", {})
        output_kw = experiment.get(
            "boiler_modulation_calibrated_output_kw",
            experiment.get("boiler_modulation_estimated_output_kw"),
        )
        if output_kw is None or self.room_id not in rooms:
            return None
        output_w = max(0.0, float(output_kw)) * 1000.0
        eligible = []
        for circuit in circuits.values():
            flow = max(0.0, float(
                circuit.get("active_configured_flow_l_min")
                or circuit.get("configured_flow_l_min") or 0.0
            ))
            served = [
                room_id for room_id in (circuit.get("served_rooms") or [])
                if room_id in rooms
            ]
            if circuit.get("flow_active") and flow > 0 and served:
                eligible.append((flow, served))
        total_flow = sum(flow for flow, _ in eligible)
        if output_w <= 0 or total_flow <= 0:
            return 0.0
        room_power = 0.0
        for flow, served in eligible:
            if self.room_id in served:
                room_power += output_w * flow / total_flow / len(served)
        return round(room_power, 1)

    @property
    def extra_state_attributes(self):
        experiment = self.coordinator.data.get("boiler_energy_experiment", {})
        return {
            "estimate": True,
            "method": "Gas-calibrated boiler output from modulation, allocated by active configured UFH flow and split equally among each circuit's served rooms.",
            "source": "boiler_modulation",
            "modulation_percent": experiment.get("boiler_modulation_percent"),
            "boiler_output_kw": experiment.get("boiler_modulation_calibrated_output_kw")
                if experiment.get("boiler_modulation_calibrated_output_kw") is not None
                else experiment.get("boiler_modulation_estimated_output_kw"),
            "calibration_factor": experiment.get("boiler_calibration_factor"),
            **_calculation_attributes("boiler_modulation_calibrated_output_kw"),
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


class RoomConductanceSensor(RoomDeviceMixin, CoordinatorEntity, SensorEntity):
    """Explicitly configured room envelope or attic-floor conductance."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "W/K"
    _attr_state_class = SensorStateClass.MEASUREMENT

    METRICS = {
        "envelope_conductance_w_per_k": "External Envelope Conductance",
        "attic_floor_conductance_w_per_k": "Attic-Floor Conductance",
    }

    def __init__(self, coordinator, room_id: str, room_name: str, metric: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self.metric = metric
        self._attr_name = f"{room_name} {self.METRICS[metric]}"
        self._attr_unique_id = f"{DOMAIN}_{room_id}_{metric}"
        self._attr_icon = "mdi:home-thermometer"

    @property
    def native_value(self):
        room = self.coordinator.data.get("rooms", {}).get(self.room_id, {})
        value = room.get(self.metric)
        return round(float(value), 3) if value is not None else None

    @property
    def extra_state_attributes(self):
        return {
            "unit": "W/K",
            "source": (
                "explicit_room_configuration"
                if self.metric == "envelope_conductance_w_per_k"
                else "explicit_house_assessment_room_share"
            ),
            "note": "Configured conductance, not an automatically identified physical parameter.",
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
    def extra_state_attributes(self):
        return _calculation_attributes("pump_energy")

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

    @property
    def extra_state_attributes(self):
        return _calculation_attributes("pump_cycles")


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

    @property
    def extra_state_attributes(self):
        return _calculation_attributes("pump_runtime")


class PumpCountdownSensor(HouseDeviceMixin, CoordinatorEntity, SensorEntity):
    """Backend-owned fixed 25-minute manifold countdown."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator, floor_id: str, floor_label: str) -> None:
        super().__init__(coordinator)
        self.floor_id = floor_id
        self._attr_name = f"{floor_label} Manifold Countdown"
        self._attr_unique_id = f"{DOMAIN}_{floor_id}_manifold_countdown"

    @property
    def native_value(self):
        timer = self.coordinator.data.get("pwm_timers", {}).get(self.floor_id, {})
        return timer.get("remaining_seconds")

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("pwm_timers", {}).get(self.floor_id, {})


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

    @property
    def extra_state_attributes(self):
        return _calculation_attributes("pump_energy")


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

    @property
    def extra_state_attributes(self):
        return _calculation_attributes("pump_runtime")


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

    @property
    def extra_state_attributes(self):
        return _calculation_attributes("pump_cycles")


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



class HouseAuditSensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Whole-house geometry and configuration cross-check."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "issues"
    _attr_icon = "mdi:home-search"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "House Audit"
        self._attr_unique_id = f"{DOMAIN}_house_audit"

    @property
    def native_value(self):
        audit = self.coordinator.data.get("house_audit", {})
        return (len(audit.get("windows_missing_geometry_or_orientation", [])) + len(audit.get("windows_with_unknown_room", [])) + len(audit.get("orientations_with_over_0_1_m2_reference_difference", [])) + len(audit.get("orientations_without_complete_frame_area", [])) + len(audit.get("rooms_missing_floor_area", [])))

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("house_audit", {})


class ThermalReadinessAuditSensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Thermal boundary and physical model parameter readiness."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "gaps"
    _attr_icon = "mdi:thermometer-lines"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Thermal Readiness Audit"
        self._attr_unique_id = f"{DOMAIN}_thermal_readiness_audit"

    @property
    def native_value(self):
        audit = self.coordinator.data.get("thermal_readiness_audit", {})
        return (len(audit.get("rooms_missing_envelope_conductance", []))
                + len(audit.get("upstairs_rooms_without_attic_conductance", []))
                + len(audit.get("rooms_missing_area_or_volume", []))
                + int(not audit.get("configured_inter_room_connections", [])))

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("thermal_readiness_audit", {})


class EnvelopeConductanceSensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Explicit UA totals grouped by room and physical boundary."""
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "surfaces"
    _attr_icon = "mdi:home-thermometer-outline"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Envelope Conductance"
        self._attr_unique_id = f"{DOMAIN}_envelope_conductance"

    @property
    def native_value(self):
        return self.coordinator.data.get("envelope_conductance", {}).get("known_surface_count", 0)

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("envelope_conductance", {})


class Stage6GainModelSensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Stage 6 solar/internal gain residual diagnostics."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "rooms"
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Stage 6 Gain Model"
        self._attr_unique_id = f"{DOMAIN}_stage6_gain_model"

    @property
    def native_value(self):
        return len(self.coordinator.data.get("stage6_gain_model", {}).get("rooms_with_sufficient_evidence", []))

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("stage6_gain_model", {})


class Stage5UfhSlabModelSensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Stage 5 floor-level UFH slab capacity and inferred manifold return diagnostics."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "floors"
    _attr_icon = "mdi:heat-wave"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Stage 5 UFH Slab Model"
        self._attr_unique_id = f"{DOMAIN}_stage5_ufh_slab_model"

    @property
    def native_value(self):
        floors = self.coordinator.data.get("stage5_ufh_slab_model", {}).get("floors", {})
        return sum(1 for item in floors.values() if item.get("screed_capacity_wh_per_k") is not None)

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("stage5_ufh_slab_model", {})


class Stage3ThermalCapacitySensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Stage 3 effective room and per-room UFH screed capacity diagnostics."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "rooms"
    _attr_icon = "mdi:weight"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Stage 3 Thermal Capacity"
        self._attr_unique_id = f"{DOMAIN}_stage3_thermal_capacity"

    @property
    def native_value(self):
        return self.coordinator.data.get("stage3_thermal_capacity", {}).get(
            "rooms_with_provisional_effective_capacity", 0
        )

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("stage3_thermal_capacity", {})


class PhysicalModelExperimentsSensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Physical replay validation, experimental forecasts and isolated scenarios."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "rooms"
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Physical Model Experiments"
        self._attr_unique_id = f"{DOMAIN}_physical_model_experiments"

    @property
    def native_value(self):
        return self.coordinator.data.get("physical_model_validation", {}).get("rooms_scored", 0)

    @property
    def extra_state_attributes(self):
        return {
            "physical_model_simulation_audit": self.coordinator.data.get("physical_model_simulation_audit", {}),
            "physical_model_validation": self.coordinator.data.get("physical_model_validation", {}),
            "multizone_identification_dataset": self.coordinator.data.get("multizone_identification_dataset", {}),
            "multizone_response_identification": self.coordinator.data.get("multizone_response_identification", {}),
            "absolute_parameter_identification": self.coordinator.data.get("absolute_parameter_identification", {}),
            "recorder_diagnostics": self.coordinator.data.get("recorder_diagnostics", {}),
            "predictive_forecast": self.coordinator.data.get("predictive_forecast", {}),
            "what_if_simulator": self.coordinator.data.get("what_if_simulator", {}),
            "fixed_scenario_temperatures_c": {"baie_matr": 22.0, "baie_parter": 20.5},
            "fixed_temperature_policy": "scenario_initial_conditions_only_never_historical_measurements",
        }


class PhysicalModelSimulationAuditSensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Per-room physical simulation readiness and progress audit."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "rooms"
    _attr_icon = "mdi:clipboard-pulse-outline"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Physical Model Simulation Audit"
        self._attr_unique_id = f"{DOMAIN}_physical_model_simulation_audit"

    @property
    def native_value(self):
        audit = self.coordinator.data.get("physical_model_simulation_audit", {})
        return audit.get("rooms_simulated", 0)

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("physical_model_simulation_audit", {})


class MeasurementAuditSensor(AuditDeviceMixin, CoordinatorEntity, SensorEntity):
    """Configured measurement source and room sensor coverage inventory."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "sources"
    _attr_icon = "mdi:database-search"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Measurement Audit"
        self._attr_unique_id = f"{DOMAIN}_measurement_audit"

    @property
    def native_value(self):
        return self.coordinator.data.get("measurement_audit", {}).get("configured_global_source_count", 0)

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("measurement_audit", {})
