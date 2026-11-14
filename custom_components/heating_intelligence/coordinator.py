"""Coordinator for read-only heating state snapshots."""
from __future__ import annotations

import logging
import math
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .room_analytics import demand_is_active, room_statistics, update_room_history
from .thermal_analytics import record_temperature_sample, thermal_statistics
from .efficiency_analytics import update_efficiency_history, efficiency_statistics
from .advanced_diagnostics import build_advanced_diagnostics
from .prediction_engine import build_predictions
from .floor_efficiency import build_floor_performance, weather_normalised_efficiency
from .advanced_heating import update_advanced_history
from .house_assessment import build_house_assessment
from .indoor_air_quality import build_indoor_air_analysis
from .thermal_learning import build_room_thermal_model, build_house_thermal_model
from .attic_thermal import build_attic_thermal_analysis
from .pump_stages import update_pump_stage
from .multizone_thermal_model import build_multizone_observation_model

from .const import (
    CONF_DEMAND_ENTITY,
    CONF_COMFORT_TARGET_C,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WIND_SPEED_ENTITY,
    CONF_WIND_DIRECTION_ENTITY,
    CONF_WIND_GUST_ENTITY,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_DEW_POINT_ENTITY,
    CONF_OUTDOOR_PRESSURE_ENTITY,
    CONF_ATTIC_TEMPERATURE_ENTITY,
    CONF_ATTIC_HUMIDITY_ENTITY,
    CONF_DELTA_T_ENTITY,
    CONF_GAS_CONSUMPTION_ENTITY,
    CONF_ELECTRICITY_BOILER_POWER_ENTITY,
    CONF_ELECTRICITY_BOILER_ENERGY_ENTITY,
    CONF_ELECTRICITY_BOILER_ENTITY,
    CONF_GAS_ENERGY_FACTOR,
    CONF_BOILER_EFFICIENCY,
    CONF_ENVELOPE_AREA,
    CONF_AC_POWER_ENTITY,
    CONF_AC_ENERGY_ENTITY,
    CONF_AC_HEATING_COP,
    CONF_BOILER_BURNER_ENTITY,
    CONF_BOILER_MODULATION_ENTITY,
    CONF_FLOOR,
    CONF_ACH,
    CONF_PUMPS,
    CONF_ROOMS,
    CONF_TEMPERATURE_ENTITY,
    CONF_WATER_TEMPERATURE_ENTITY,
    CONF_RETURN_WATER_TEMPERATURE_ENTITY,
    CONF_WINDOW_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_CO2_ENTITY,
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
            update_interval=timedelta(seconds=10),
        )
        # Keep entities safe during startup or a failed first refresh.
        self.data = {
            "rooms": {},
            "pumps": {},
            "water_temperature": None,
            "water_temperature_entity": None,
            "water_return_temperature": None,
            "water_return_temperature_entity": None,
            "sync": {"status": "unknown", "mismatch_count": 0},
            "daily": {},
            "floor_analysis": {},
            "efficiency": {},
            "house_assessment": {},
            "indoor_air_quality": {},
            "thermal_model": {},
            "weather": {},
            "attic_thermal": {},
        }

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
        local_date = dt_util.now().date().isoformat()
        last_update = self._history.get("last_update")
        try:
            previous_update = (
                dt_util.parse_datetime(last_update)
                if isinstance(last_update, str) and last_update
                else None
            )
        except (TypeError, ValueError):
            _LOGGER.warning("Ignoring invalid stored last_update timestamp: %r", last_update)
            previous_update = None
        elapsed = (now - previous_update).total_seconds() if previous_update else 0.0
        continuity_gap = previous_update is None or elapsed < 0 or elapsed > 90
        if continuity_gap:
            elapsed = 0.0
        now_local = dt_util.now()
        interval_start_local = (
            dt_util.as_local(previous_update)
            if previous_update is not None and elapsed > 0
            else now_local
        )
        self._history["last_update"] = now.isoformat()
        daily = self._history.setdefault("daily", {})
        if daily.get("date") != local_date:
            daily.clear()
            daily.update({"date": local_date})
            for item in self._history.setdefault("pumps", {}).values():
                item["daily_runtime_seconds"] = 0.0
                item["daily_cycle_count"] = 0
                item["daily_energy_kwh"] = 0.0
        result = {
            "rooms": {},
            "pumps": {},
            "water_temperature": None,
            "water_temperature_entity": None,
            "efficiency": {},
            "weather": {},
            "attic_thermal": {},
        }
        water_entity = self.entry.options.get(CONF_WATER_TEMPERATURE_ENTITY)
        if water_entity:
            state = self.hass.states.get(water_entity)
            result["water_temperature_entity"] = water_entity
            result["water_temperature"] = _as_float(state.state) if state else None
        return_entity = self.entry.options.get(CONF_RETURN_WATER_TEMPERATURE_ENTITY)
        if return_entity:
            return_state = self.hass.states.get(return_entity)
            result["water_return_temperature_entity"] = return_entity
            result["water_return_temperature"] = _as_float(return_state.state) if return_state else None

        configured_pumps = self.entry.options.get(CONF_PUMPS, [])
        changed = False
        burner_entity = self.entry.options.get(CONF_BOILER_BURNER_ENTITY)
        burner_state = self.hass.states.get(burner_entity) if burner_entity else None
        modulation_entity = self.entry.options.get(CONF_BOILER_MODULATION_ENTITY)
        modulation_state = self.hass.states.get(modulation_entity) if modulation_entity else None
        boiler_context = {
            "burner_entity": burner_entity,
            "burner_state": burner_state.state if burner_state else None,
            "burner_on": burner_state.state == "on" if burner_state else None,
            "modulation_entity": modulation_entity,
            "modulation_percent": _as_float(modulation_state.state) if modulation_state else None,
        }

        for pump in configured_pumps:
            pump_id = pump["id"]
            power_state = self.hass.states.get(pump["power_entity"])
            energy_state = self.hass.states.get(pump["energy_entity"])
            power = _as_float(power_state.state) if power_state else None
            energy = _as_float(energy_state.state) if energy_state else None
            history = self._history.setdefault("pumps", {}).setdefault(pump_id, {
                "running": False,
                "cycle_count": 0,
                "total_runtime_seconds": 0.0,
                "cycle_start": None,
                "last_cycle_start": None,
                "last_cycle_duration_seconds": None,
                "daily_runtime_seconds": 0.0,
                "daily_cycle_count": 0,
                "daily_energy_kwh": 0.0,
                "last_energy_reading": None,
            })
            for key, default in (("daily_runtime_seconds", 0.0), ("daily_cycle_count", 0), ("daily_energy_kwh", 0.0), ("last_energy_reading", None)):
                history.setdefault(key, default)
            stage_history = history.setdefault("stage_history", {})
            stage_data = update_pump_stage(stage_history, power, now_local)
            candidate_start = stage_history.get("above_pump_threshold_since")
            if candidate_start and history.get("correlated_candidate_start") != candidate_start:
                history["correlated_candidate_start"] = candidate_start
                history["candidate_boiler_context"] = dict(boiler_context)
            observed_running = stage_data["stage"] == "pump_running"
            if stage_data.get("pump_started_at") and history.get("correlated_pump_started_at") != stage_data["pump_started_at"]:
                history["correlated_pump_started_at"] = stage_data["pump_started_at"]
                history["pump_start_boiler_context"] = dict(
                    history.get("candidate_boiler_context") or boiler_context
                )
            was_running = bool(history.get("running", False))

            if observed_running is True and not was_running:
                history["running"] = True
                history["cycle_start"] = now.isoformat()
                history["last_cycle_start"] = now.isoformat()
                history["cycle_count"] = int(history.get("cycle_count", 0)) + 1
                history["daily_cycle_count"] = int(history.get("daily_cycle_count", 0)) + 1
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

            elif observed_running is False and was_running:
                started = dt_util.parse_datetime(history.get("cycle_start") or "")
                duration = max(0.0, (now - started).total_seconds()) if started else 0.0
                history["running"] = False
                history["total_runtime_seconds"] = float(history.get("total_runtime_seconds", 0)) + duration
                history["last_cycle_duration_seconds"] = round(duration)
                history["cycle_start"] = None
                changed = True

            elif observed_running is True and was_running and not history.get("cycle_start"):
                # Recover gracefully if an older stored record lacks an active start time.
                history["cycle_start"] = now.isoformat()
                changed = True

            # Account runtime only across two consecutive valid running observations.
            if observed_running is True and was_running and elapsed:
                history["daily_runtime_seconds"] = float(history.get("daily_runtime_seconds", 0)) + elapsed

            # Convert the cumulative energy meter into a daily delta. A meter reset
            # establishes a fresh baseline and never creates negative consumption.
            previous_energy = _as_float(history.get("last_energy_reading"))
            if energy is not None:
                if previous_energy is not None and energy >= previous_energy:
                    history["daily_energy_kwh"] = float(history.get("daily_energy_kwh", 0)) + energy - previous_energy
                history["last_energy_reading"] = energy

            cycle_start = dt_util.parse_datetime(history.get("cycle_start") or "")
            running = bool(history.get("running", False))
            current_duration = max(0.0, (now - cycle_start).total_seconds()) if running and cycle_start else 0.0
            result["pumps"][pump_id] = {
                "id": pump_id,
                "name": pump["name"],
                "power_entity": pump["power_entity"],
                "power": power,
                "energy_entity": pump["energy_entity"],
                "energy": energy,
                "energy_unit": energy_state.attributes.get("unit_of_measurement") if energy_state else None,
                "running": running,
                "stage": stage_data["stage"],
                "valve_opened_at": stage_data["valve_opened_at"],
                "pump_started_at": stage_data["pump_started_at"],
                "pump_stopped_at": stage_data["pump_stopped_at"],
                "idle_baseline_w": stage_data["idle_baseline_w"],
                "valve_level_w": stage_data["valve_level_w"],
                "detection_basis": stage_data["detection_basis"],
                "boiler_context_at_pump_start": history.get("pump_start_boiler_context"),
                "power_source_state": power_state.state if power_state else "missing",
                "energy_source_state": energy_state.state if energy_state else "missing",
                "cycle_count": int(history.get("cycle_count", 0)),
                "total_runtime_seconds": round(float(history.get("total_runtime_seconds", 0))),
                "current_cycle_seconds": round(current_duration),
                "last_cycle_start": history.get("last_cycle_start"),
                "last_cycle_duration_seconds": history.get("last_cycle_duration_seconds"),
                "daily_runtime_seconds": round(float(history.get("daily_runtime_seconds", 0))),
                "daily_cycle_count": int(history.get("daily_cycle_count", 0)),
                "daily_energy_kwh": round(float(history.get("daily_energy_kwh", 0)), 4),
            }

        result["boiler_context"] = boiler_context
        pump_events = []
        for pump_id, pump_data in result["pumps"].items():
            for event_key, event_label in (
                ("valve_opened_at", "valve_opening"),
                ("pump_started_at", "pump_started"),
            ):
                event_time = pump_data.get(event_key)
                if event_time:
                    pump_events.append({
                        "pump_id": pump_id,
                        "event": event_label,
                        "timestamp": event_time,
                        "boiler_burner_on": (
                            pump_data.get("boiler_context_at_pump_start", {}).get("burner_on")
                            if event_label == "pump_started" else None
                        ),
                    })
        pump_events.sort(key=lambda item: item["timestamp"])
        def event_delay(pump_id, first_event, second_event):
            pump_data = result["pumps"].get(pump_id, {})
            first = dt_util.parse_datetime(pump_data.get(first_event) or "")
            second = dt_util.parse_datetime(pump_data.get(second_event) or "")
            return round((second - first).total_seconds()) if first and second else None
        downstairs_start = dt_util.parse_datetime(
            result["pumps"].get("ground_floor", {}).get("pump_started_at") or ""
        )
        upstairs_start = dt_util.parse_datetime(
            result["pumps"].get("upstairs", {}).get("pump_started_at") or ""
        )
        result["pump_sequence"] = {
            "events": pump_events,
            "downstairs_valve_to_pump_seconds": event_delay(
                "ground_floor", "valve_opened_at", "pump_started_at"
            ),
            "upstairs_valve_to_pump_seconds": event_delay(
                "upstairs", "valve_opened_at", "pump_started_at"
            ),
            "downstairs_to_upstairs_pump_seconds": (
                round((upstairs_start - downstairs_start).total_seconds())
                if downstairs_start and upstairs_start else None
            ),
            "sampling_interval_seconds": 10,
        }

        result["sync"] = {
            "status": self._history.get("last_sync_result", "unknown"),
            "mismatch_count": int(self._history.get("sync_mismatch_count", 0)),
            "start_delta_seconds": self._history.get("last_sync_delta_seconds"),
            "tolerance_seconds": SYNC_TOLERANCE_SECONDS,
        }

        result["daily"] = {
            "date": local_date,
            "pump_runtime_seconds": sum(p.get("daily_runtime_seconds", 0) for p in result["pumps"].values()),
            "pump_energy_kwh": round(sum(p.get("daily_energy_kwh", 0) for p in result["pumps"].values()), 4),
            "pump_cycles": sum(p.get("daily_cycle_count", 0) for p in result["pumps"].values()),
        }

        ac_power_entity = self.entry.options.get(CONF_AC_POWER_ENTITY)
        ac_power_state = self.hass.states.get(ac_power_entity) if ac_power_entity else None
        ac_power_value = _as_float(ac_power_state.state) if ac_power_state else None
        ac_power_unit = (ac_power_state.attributes.get("unit_of_measurement") or "") if ac_power_state else ""
        if ac_power_value is not None:
            if ac_power_unit.lower() == "kw":
                ac_power_value *= 1000
            elif ac_power_unit == "MW":
                ac_power_value *= 1_000_000
            elif ac_power_unit == "mW":
                ac_power_value /= 1000
        # Exclude room samples while the configured AC electrical input indicates active use.
        ac_heating_active = bool(ac_power_entity) and (ac_power_value is None or ac_power_value > 50)

        prediction_rooms = {}
        learned_room_models = {}
        floor_analysis = {
            "ground_floor": {"active_rooms": 0, "pump_running": bool(result["pumps"].get("ground_floor", {}).get("running")), "pump_power_w": result["pumps"].get("ground_floor", {}).get("power")},
            "upstairs": {"active_rooms": 0, "pump_running": bool(result["pumps"].get("upstairs", {}).get("running")), "pump_power_w": result["pumps"].get("upstairs", {}).get("power")},
            "unassigned": {"active_rooms": 0},
        }
        for room in self.entry.options.get(CONF_ROOMS, []):
            room_id = room["id"]
            demand = self.hass.states.get(room[CONF_DEMAND_ENTITY])
            temperature = self.hass.states.get(room[CONF_TEMPERATURE_ENTITY])
            window_id = room.get(CONF_WINDOW_ENTITY)
            window = self.hass.states.get(window_id) if window_id else None
            humidity_id = room.get(CONF_HUMIDITY_ENTITY)
            humidity_state = self.hass.states.get(humidity_id) if humidity_id else None
            co2_id = room.get(CONF_CO2_ENTITY)
            co2_state = self.hass.states.get(co2_id) if co2_id else None
            humidity_value = _as_float(humidity_state.state) if humidity_state else None
            co2_value = _as_float(co2_state.state) if co2_state else None
            if humidity_value is not None and not 0 <= humidity_value <= 100:
                humidity_value = None
            if co2_value is not None and co2_value < 0:
                co2_value = None
            humidity_status = ("low" if humidity_value < 30 else "high" if humidity_value > 60 else "comfortable") if humidity_value is not None else None
            co2_status = ("good" if co2_value < 800 else "elevated" if co2_value < 1200 else "high" if co2_value < 2000 else "very_high") if co2_value is not None else None
            demand_state = demand.state if demand else None
            demand_active = demand_is_active(demand_state)
            demand_state_known = bool(demand and demand_state != "unknown")
            room_history = update_room_history(
                self._history,
                room_id,
                demand_active,
                dt_util.as_local(demand.last_changed) if demand else None,
                interval_start_local,
                now_local,
                continuity_gap,
            )
            stats = room_statistics(room_history, now_local)
            window_open = bool(window and window.state == "on")
            window_state_known = bool(window_id and window and window.state not in ("unknown", "unavailable"))
            if room_history.get("window_open_demand_date") != local_date:
                room_history["window_open_demand_date"] = local_date
                room_history["window_open_demand_seconds_today"] = 0.0
            if demand_active and window_open and elapsed > 0:
                room_history["window_open_demand_seconds_today"] = float(
                    room_history.get("window_open_demand_seconds_today", 0.0)
                ) + elapsed
            temperature_value = _as_float(temperature.state) if temperature else None
            record_temperature_sample(
                room_history, now_local, temperature_value, demand_active, window_open,
                external_heating=ac_heating_active,
                window_state_known=window_state_known,
                demand_state_known=demand_state_known,
            )
            thermal = thermal_statistics(room_history, now_local)
            learned_model = build_room_thermal_model(room_history, now_local, temperature_value, demand_active, bool(ac_power_entity), ac_heating_active, window_open, window_state_known, bool(window_id), demand_state_known)
            learned_room_models[room_id] = learned_model
            floor = room.get(CONF_FLOOR, "unassigned")
            floor_data = floor_analysis.setdefault(floor, {"active_rooms": 0})
            daily_key = "room_demand_" + room_id
            demand_seconds = stats["demand_seconds_today"]
            daily[daily_key] = demand_seconds
            if demand_active:
                floor_data["active_rooms"] = int(floor_data.get("active_rooms", 0)) + 1
            prediction_rooms[room_id] = {
                "temperature": temperature_value,
                "demand": demand_active,
                "temperature_samples": room_history.get("temperature_samples", []),
            }
            result["rooms"][room_id] = {
                "floor": floor,
                "area_m2": _as_float(room.get("area_m2")),
                "volume_m3": _as_float(room.get("volume_m3")),
                "air_changes_per_hour": _as_float(room.get(CONF_ACH)),
                "floor_label": {"ground_floor": "Downstairs", "upstairs": "Upstairs", "unassigned": "Unassigned"}.get(floor, floor),
                "id": room_id,
                "name": room["name"],
                "demand_entity": room[CONF_DEMAND_ENTITY],
                "demand_state": demand_state,
                "demand": demand_active,
                **stats,
                **thermal,
                "thermal_model": learned_model,
                "window_open_demand_seconds_today": round(float(room_history.get("window_open_demand_seconds_today", 0))),
                "temperature_history": [
                    {"time": sample.get("time"), "temperature": sample.get("temperature"),
                     "demand": sample.get("demand", False), "window_open": sample.get("window_open", False)}
                    for sample in room_history.get("temperature_samples", [])[-145:]
                ],
                "temperature_entity": room[CONF_TEMPERATURE_ENTITY],
                "temperature": temperature_value,
                "humidity_entity": humidity_id,
                "humidity": humidity_value,
                "humidity_status": humidity_status,
                "co2_entity": co2_id,
                "co2_ppm": co2_value,
                "co2_status": co2_status,
                "window_entity": window_id,
                "window_state": window.state if window else None,
                "window_open": window_open,
                "window_state_known": window_state_known,
                "demand_state_known": demand_state_known,
                "daily_demand_seconds": round(demand_seconds),
                "demand_history": [{"start": item.get("start"), "end": item.get("end"), "duration_seconds": item.get("duration_seconds")} for item in room_history.get("cycles", []) if item.get("start") and item.get("end")][-500:] + ([{"start": room_history.get("cycle_start"), "end": now_local.isoformat(), "duration_seconds": max(0, (now_local - dt_util.parse_datetime(room_history["cycle_start"])).total_seconds()), "active": True}] if room_history.get("last_demand") is True and room_history.get("cycle_start") else []),
            }
        result["indoor_air_quality"] = build_indoor_air_analysis(result["rooms"])
        for room_id, air_assessment in result["indoor_air_quality"].get("rooms", {}).items():
            if room_id in result["rooms"]:
                result["rooms"][room_id]["air_quality_assessment"] = air_assessment
        for floor_id in ("ground_floor", "upstairs"):
            floor_data = floor_analysis[floor_id]
            floor_data["demand_hours_today"] = round(sum(float(daily.get("room_demand_" + room["id"], 0.0)) for room in self.entry.options.get(CONF_ROOMS, []) if room.get(CONF_FLOOR, "unassigned") == floor_id) / 3600, 2)
            floor_data["demand_pump_state"] = ("demand_without_pump" if floor_data["active_rooms"] > 0 and not floor_data["pump_running"] else "pump_without_demand" if floor_data["active_rooms"] == 0 and floor_data["pump_running"] else "demand_and_pump" if floor_data["active_rooms"] > 0 and floor_data["pump_running"] else "idle")
        result["floor_analysis"] = floor_analysis
        result["floor_performance"] = build_floor_performance(
            result["rooms"],
            float(self.entry.options.get(CONF_COMFORT_TARGET_C, 21.0)),
        )

        def read_sensor(option_key):
            entity_id = self.entry.options.get(option_key)
            state = self.hass.states.get(entity_id) if entity_id else None
            return {
                "value": _as_float(state.state) if state else None,
                "unit": state.attributes.get("unit_of_measurement") if state else None,
                "entity_id": entity_id,
            } if entity_id else None

        indoor_values = [
            room_data["temperature"] for room_data in result["rooms"].values()
            if room_data.get("temperature") is not None
        ]
        readings = {
            "outdoor_temperature": read_sensor(CONF_OUTDOOR_TEMPERATURE_ENTITY),
            "solar_radiation": read_sensor(CONF_SOLAR_RADIATION_ENTITY),
            "delta_t": read_sensor(CONF_DELTA_T_ENTITY),
            "gas": read_sensor(CONF_GAS_CONSUMPTION_ENTITY),
            # Reuse the cumulative energy meters already selected in Pump settings.
            "electricity": None,
            "electricity_meters": {
                pump_id: ({
                    "value": pump_data.get("energy"),
                    "unit": pump_data.get("energy_unit"),
                    "entity_id": pump_data.get("energy_entity"),
                } if pump_data.get("energy") is not None else None)
                for pump_id, pump_data in result["pumps"].items()
            } | {
                "boiler": (
                    read_sensor(CONF_ELECTRICITY_BOILER_ENERGY_ENTITY)
                    or read_sensor(CONF_ELECTRICITY_BOILER_ENTITY)
                ),
                "ac": read_sensor(CONF_AC_ENERGY_ENTITY),
            },
            "boiler_power": read_sensor(CONF_ELECTRICITY_BOILER_POWER_ENTITY),
            "ac_power": read_sensor(CONF_AC_POWER_ENTITY),
            "ac_energy": read_sensor(CONF_AC_ENERGY_ENTITY),
            "indoor_temperature": {
                "value": sum(indoor_values) / len(indoor_values),
                "unit": "°C",
            } if indoor_values else None,
        }
        attic_temperature_entity = self.entry.options.get(CONF_ATTIC_TEMPERATURE_ENTITY)
        attic_humidity_entity = self.entry.options.get(CONF_ATTIC_HUMIDITY_ENTITY)
        attic_temperature_state = self.hass.states.get(attic_temperature_entity) if attic_temperature_entity else None
        attic_humidity_state = self.hass.states.get(attic_humidity_entity) if attic_humidity_entity else None
        attic_temperature = _as_float(attic_temperature_state.state) if attic_temperature_state else None
        attic_humidity = _as_float(attic_humidity_state.state) if attic_humidity_state else None
        if attic_humidity is not None and not 0 <= attic_humidity <= 100:
            attic_humidity = None
        upstairs_rooms = [
            room_data["temperature"] for room_id, room_data in result["rooms"].items()
            if room_data.get("floor") == "upstairs" and room_data.get("temperature") is not None
        ]
        upstairs_average = sum(upstairs_rooms) / len(upstairs_rooms) if upstairs_rooms else None

        weather_entity_id = self.entry.options.get(CONF_WEATHER_ENTITY)
        weather_state = self.hass.states.get(weather_entity_id) if weather_entity_id else None

        def weather_value(option_key, attribute=None):
            configured = read_sensor(option_key)
            if configured and configured.get("value") is not None:
                return configured
            if weather_state and attribute:
                value = _as_float(weather_state.attributes.get(attribute))
                if value is not None:
                    return {
                        "value": value,
                        "unit": {
                            "temperature": weather_state.attributes.get("temperature_unit", "°C"),
                            "humidity": "%",
                            "pressure": weather_state.attributes.get("pressure_unit"),
                            "wind_speed": weather_state.attributes.get("wind_speed_unit"),
                            "wind_bearing": "°",
                        }.get(attribute),
                        "entity_id": weather_entity_id,
                        "source": "weather_entity_attribute",
                    }
            return configured

        result["weather"] = {
            "entity_id": weather_entity_id,
            "condition": weather_state.state if weather_state else None,
            "outdoor_temperature": weather_value(CONF_OUTDOOR_TEMPERATURE_ENTITY, "temperature"),
            "outdoor_humidity": weather_value(CONF_OUTDOOR_HUMIDITY_ENTITY, "humidity"),
            "pressure": weather_value(CONF_OUTDOOR_PRESSURE_ENTITY, "pressure"),
            "wind_speed": weather_value(CONF_WIND_SPEED_ENTITY, "wind_speed"),
            "wind_direction": weather_value(CONF_WIND_DIRECTION_ENTITY, "wind_bearing"),
            "wind_gust": weather_value(CONF_WIND_GUST_ENTITY),
            "dew_point": read_sensor(CONF_DEW_POINT_ENTITY),
            "solar_radiation": read_sensor(CONF_SOLAR_RADIATION_ENTITY),
        }
        if result["weather"].get("outdoor_temperature"):
            weather_outdoor = result["weather"]["outdoor_temperature"]
            if weather_outdoor.get("value") is not None:
                readings["outdoor_temperature"] = weather_outdoor
        if result["weather"].get("solar_radiation"):
            weather_solar = result["weather"]["solar_radiation"]
            if weather_solar.get("value") is not None:
                readings["solar_radiation"] = weather_solar

        update_efficiency_history(
            self._history,
            readings,
            now_local,
            elapsed,
            continuity_gap,
            float(self.entry.options.get(CONF_GAS_ENERGY_FACTOR, 10.55)),
        )
        result["efficiency"] = efficiency_statistics(self._history, now_local)
        result["efficiency"]["weather"] = result["weather"]
        outdoor_temperature_value = result["weather"].get("outdoor_temperature") or readings.get("outdoor_temperature")
        outdoor_temperature = outdoor_temperature_value.get("value") if outdoor_temperature_value else None
        result["attic_thermal"] = build_attic_thermal_analysis(
            attic_temperature,
            attic_humidity,
            upstairs_average,
            outdoor_temperature,
            self.entry.options.get("house_assessment", {}),
        )
        result["attic_thermal"]["temperature_entity"] = attic_temperature_entity
        result["attic_thermal"]["humidity_entity"] = attic_humidity_entity
        from .thermal_performance import thermal_performance_statistics
        result["efficiency"].update(thermal_performance_statistics(self._history, now_local,
            float(self.entry.options.get(CONF_BOILER_EFFICIENCY, 0.90)),
            float(self.entry.options.get(CONF_ENVELOPE_AREA, 361.26)),
            float(self.entry.options.get(CONF_AC_HEATING_COP, 3.0))))
        weather_model = weather_normalised_efficiency(
            self._history.get("efficiency_daily", {}),
            local_date,
        )
        result["efficiency"]["weather_normalised"] = weather_model
        result["thermal_model"] = build_house_thermal_model(learned_room_models, result["efficiency"])
        result["thermal_model"]["multi_zone"] = build_multizone_observation_model(
            result["rooms"],
            result["weather"],
            result["attic_thermal"],
            self.entry.options.get("house_assessment", {}),
            {
                "water_temperature": result.get("water_temperature"),
                "water_return_temperature": result.get("water_return_temperature"),
                "boiler_context": result.get("boiler_context", {}),
                "pumps": result.get("pumps", {}),
            },
        )
        result["thermal_model"]["ac_heating_filter"] = (
            "sensor_unavailable" if ac_power_entity and ac_power_value is None
            else "active" if ac_heating_active
            else "inactive" if ac_power_entity else "not_configured"
        )
        result["house_assessment"] = build_house_assessment(
            self.entry.options.get("house_assessment", {}),
            result["efficiency"],
            result["rooms"],
        )
        result["house_assessment"]["attic_thermal"] = result["attic_thermal"]
        result["house_assessment"]["cross_system_context"] = {
            "indoor_air_quality_status": result["indoor_air_quality"].get("co2", {}).get("status"),
            "humidity_status": result["indoor_air_quality"].get("humidity", {}).get("status"),
            "thermal_model_status": result["thermal_model"].get("status"),
            "thermal_model_rooms": result["thermal_model"].get("rooms_modelled", 0),
            "measured_performance_status": result["house_assessment"].get("performance_status"),
        }
        normalised_by_date = {
            item["date"]: item for item in weather_model.get("recent_days", [])
        }
        result["energy_history"] = [
            {
                "date": date_key,
                "gas_kwh": round(day.get("gas_kwh", 0.0), 3),
                "electricity_kwh": round(day.get("electricity_kwh", 0.0), 3),
                "weather_normalised_gas_kwh": normalised_by_date.get(date_key, {}).get("weather_normalised_gas_kwh"),
            }
            for date_key, day in sorted(self._history.get("efficiency_daily", {}).items())[-30:]
        ]
        boiler_power = readings.get("boiler_power")
        boiler_power_w = boiler_power.get("value") if boiler_power else None
        boiler_power_unit = (boiler_power.get("unit") or "").lower() if boiler_power else ""
        if boiler_power_w is not None:
            if boiler_power_unit == "kw":
                boiler_power_w *= 1000
            elif boiler_power_unit == "mw":
                boiler_power_w /= 1000
        result["efficiency"]["boiler_power_w"] = boiler_power_w
        ac_power = readings.get("ac_power")
        ac_power_w = ac_power.get("value") if ac_power else None
        ac_power_unit = (ac_power.get("unit") or "").lower() if ac_power else ""
        if ac_power_w is not None:
            if ac_power_unit == "kw":
                ac_power_w *= 1000
            elif ac_power_unit == "mw":
                ac_power_w /= 1000
        result["efficiency"]["ac_power_w"] = ac_power_w
        result["efficiency"]["sources"] = {
            key: value.get("entity_id") if value else None
            for key, value in readings.items() if key != "electricity_meters"
        }
        result["efficiency"]["electricity_meter_sources"] = {
            key: value.get("entity_id") if value else None
            for key, value in readings["electricity_meters"].items()
        }
        advanced = build_advanced_diagnostics(result["rooms"], result["efficiency"], self._history)
        for room_id, metrics in advanced["room_diagnostics"].items():
            result["rooms"][room_id].update(metrics)
        result["advanced_diagnostics"] = advanced
        result["predictions"] = build_predictions(prediction_rooms, result["efficiency"], now_local, float(self.entry.options.get(CONF_COMFORT_TARGET_C, 21.0)))
        result["advanced_heating"] = update_advanced_history(
            self._history,
            result.get("water_temperature"),
            result.get("water_return_temperature"),
            result["rooms"],
            result["predictions"],
            now_local,
        )
        result["predictions"]["validation"] = result["advanced_heating"]["forecast_validation"]
        await self._store.async_save(self._history)
        return result


def _as_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
