"""Coordinator for read-only heating state snapshots."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .room_analytics import demand_is_active, room_statistics, update_room_history
from .thermal_analytics import record_temperature_sample, thermal_statistics
from .efficiency_analytics import update_efficiency_history, efficiency_statistics

from .const import (
    CONF_DEMAND_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_DELTA_T_ENTITY,
    CONF_GAS_CONSUMPTION_ENTITY,
    CONF_ELECTRICITY_CONSUMPTION_ENTITY,
    CONF_ELECTRICITY_GROUND_FLOOR_ENTITY,
    CONF_ELECTRICITY_UPSTAIRS_ENTITY,
    CONF_ELECTRICITY_BOILER_ENTITY,
    CONF_GAS_ENERGY_FACTOR,
    CONF_FLOOR,
    CONF_PUMPS,
    CONF_ROOMS,
    CONF_TEMPERATURE_ENTITY,
    CONF_WATER_TEMPERATURE_ENTITY,
    CONF_WINDOW_ENTITY,
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
            update_interval=timedelta(seconds=30),
        )
        # Keep entities safe during startup or a failed first refresh.
        self.data = {
            "rooms": {},
            "pumps": {},
            "water_temperature": None,
            "water_temperature_entity": None,
            "sync": {"status": "unknown", "mismatch_count": 0},
            "daily": {},
            "floor_analysis": {},
            "efficiency": {},
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
        }
        water_entity = self.entry.options.get(CONF_WATER_TEMPERATURE_ENTITY)
        if water_entity:
            state = self.hass.states.get(water_entity)
            result["water_temperature_entity"] = water_entity
            result["water_temperature"] = _as_float(state.state) if state else None

        configured_pumps = self.entry.options.get(CONF_PUMPS, [])
        changed = False

        for pump in configured_pumps:
            pump_id = pump["id"]
            power_state = self.hass.states.get(pump["power_entity"])
            energy_state = self.hass.states.get(pump["energy_entity"])
            power = _as_float(power_state.state) if power_state else None
            energy = _as_float(energy_state.state) if energy_state else None
            observed_running = None if power is None else power > RUNNING_THRESHOLD_W
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
                "running": running,
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
            demand_state = demand.state if demand else None
            demand_active = demand_is_active(demand_state)
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
            if room_history.get("window_open_demand_date") != local_date:
                room_history["window_open_demand_date"] = local_date
                room_history["window_open_demand_seconds_today"] = 0.0
            if demand_active and window_open and elapsed > 0:
                room_history["window_open_demand_seconds_today"] = float(
                    room_history.get("window_open_demand_seconds_today", 0.0)
                ) + elapsed
            temperature_value = _as_float(temperature.state) if temperature else None
            record_temperature_sample(
                room_history, now_local, temperature_value, demand_active, window_open
            )
            thermal = thermal_statistics(room_history, now_local)
            floor = room.get(CONF_FLOOR, "unassigned")
            floor_data = floor_analysis.setdefault(floor, {"active_rooms": 0})
            daily_key = "room_demand_" + room_id
            demand_seconds = stats["demand_seconds_today"]
            daily[daily_key] = demand_seconds
            if demand_active:
                floor_data["active_rooms"] = int(floor_data.get("active_rooms", 0)) + 1
            result["rooms"][room_id] = {
                "floor": floor,
                "floor_label": {"ground_floor": "Downstairs", "upstairs": "Upstairs", "unassigned": "Unassigned"}.get(floor, floor),
                "id": room_id,
                "name": room["name"],
                "demand_entity": room[CONF_DEMAND_ENTITY],
                "demand_state": demand_state,
                "demand": demand_active,
                **stats,
                **thermal,
                "window_open_demand_seconds_today": round(float(room_history.get("window_open_demand_seconds_today", 0))),
                "temperature_entity": room[CONF_TEMPERATURE_ENTITY],
                "temperature": temperature_value,
                "window_entity": window_id,
                "window_state": window.state if window else None,
                "window_open": window_open,
                "daily_demand_seconds": round(demand_seconds),
            }
        for floor_id in ("ground_floor", "upstairs"):
            floor_data = floor_analysis[floor_id]
            floor_data["demand_hours_today"] = round(sum(float(daily.get("room_demand_" + room["id"], 0.0)) for room in self.entry.options.get(CONF_ROOMS, []) if room.get(CONF_FLOOR, "unassigned") == floor_id) / 3600, 2)
            floor_data["demand_pump_state"] = ("demand_without_pump" if floor_data["active_rooms"] > 0 and not floor_data["pump_running"] else "pump_without_demand" if floor_data["active_rooms"] == 0 and floor_data["pump_running"] else "demand_and_pump" if floor_data["active_rooms"] > 0 and floor_data["pump_running"] else "idle")
        result["floor_analysis"] = floor_analysis

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
            "electricity": read_sensor(CONF_ELECTRICITY_CONSUMPTION_ENTITY),
            "electricity_meters": {
                "ground_floor": read_sensor(CONF_ELECTRICITY_GROUND_FLOOR_ENTITY),
                "upstairs": read_sensor(CONF_ELECTRICITY_UPSTAIRS_ENTITY),
                "boiler": read_sensor(CONF_ELECTRICITY_BOILER_ENTITY),
            },
            "indoor_temperature": {
                "value": sum(indoor_values) / len(indoor_values),
                "unit": "°C",
            } if indoor_values else None,
        }
        update_efficiency_history(
            self._history,
            readings,
            now_local,
            elapsed,
            continuity_gap,
            float(self.entry.options.get(CONF_GAS_ENERGY_FACTOR, 10.55)),
        )
        result["efficiency"] = efficiency_statistics(self._history, now_local)
        result["efficiency"]["sources"] = {
            key: value.get("entity_id") if value else None
            for key, value in readings.items() if key != "electricity_meters"
        }
        result["efficiency"]["electricity_meter_sources"] = {
            key: value.get("entity_id") if value else None
            for key, value in readings["electricity_meters"].items()
        }
        await self._store.async_save(self._history)
        return result


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
