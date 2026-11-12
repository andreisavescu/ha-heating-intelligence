"""Config flow for Heating Intelligence."""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DEMAND_ENTITY,
    CONF_COMFORT_TARGET_C,
    CONF_FLOOR,
    CONF_AREA,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_DELTA_T_ENTITY,
    CONF_GAS_CONSUMPTION_ENTITY,
    CONF_ELECTRICITY_CONSUMPTION_ENTITY,
    CONF_ELECTRICITY_GROUND_FLOOR_ENTITY,
    CONF_ELECTRICITY_UPSTAIRS_ENTITY,
    CONF_ELECTRICITY_BOILER_ENTITY,
    CONF_ELECTRICITY_BOILER_POWER_ENTITY,
    CONF_ELECTRICITY_BOILER_ENERGY_ENTITY,
    CONF_GAS_ENERGY_FACTOR,
    CONF_BOILER_EFFICIENCY,
    CONF_ENVELOPE_AREA,
    CONF_AC_POWER_ENTITY,
    CONF_AC_ENERGY_ENTITY,
    CONF_AC_HEATING_COP,
    CONF_NAME,
    CONF_PUMPS,
    CONF_PUMP_ENERGY_ENTITY,
    CONF_PUMP_POWER_ENTITY,
    CONF_ROOMS,
    CONF_TEMPERATURE_ENTITY,
    CONF_WATER_TEMPERATURE_ENTITY,
    CONF_RETURN_WATER_TEMPERATURE_ENTITY,
    CONF_WINDOW_ENTITY,
    DOMAIN,
)


class HeatingIntelligenceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            return self.async_create_entry(title="Heating Intelligence", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HeatingIntelligenceOptionsFlow()


class HeatingIntelligenceOptionsFlow(config_entries.OptionsFlow):
    """Manage room, pump and system configuration."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_room", "edit_room", "room_area", "remove_room", "global_settings", "pump_settings"],
        )

    async def async_step_global_settings(self, user_input=None):
        """Configure optional whole-house weather and energy inputs."""
        entity_options = (
            CONF_WATER_TEMPERATURE_ENTITY,
            CONF_RETURN_WATER_TEMPERATURE_ENTITY,
            CONF_OUTDOOR_TEMPERATURE_ENTITY,
            CONF_SOLAR_RADIATION_ENTITY,
            CONF_DELTA_T_ENTITY,
            CONF_GAS_CONSUMPTION_ENTITY,
            CONF_ELECTRICITY_BOILER_POWER_ENTITY,
            CONF_ELECTRICITY_BOILER_ENERGY_ENTITY,
            CONF_AC_POWER_ENTITY,
            CONF_AC_ENERGY_ENTITY,
        )
        if user_input is not None:
            data = dict(self.config_entry.options)
            # Remove obsolete duplicate electricity selectors from older versions.
            for key in (
                CONF_ELECTRICITY_CONSUMPTION_ENTITY,
                CONF_ELECTRICITY_GROUND_FLOOR_ENTITY,
                CONF_ELECTRICITY_UPSTAIRS_ENTITY,
                CONF_ELECTRICITY_BOILER_ENTITY,
            ):
                data.pop(key, None)
            for key in entity_options:
                entity_id = user_input.get(key)
                if entity_id:
                    data[key] = entity_id
                else:
                    data.pop(key, None)
            data[CONF_COMFORT_TARGET_C] = float(user_input.get(CONF_COMFORT_TARGET_C) or 21.0)
            data[CONF_GAS_ENERGY_FACTOR] = float(user_input.get(CONF_GAS_ENERGY_FACTOR) or 10.55)
            data[CONF_BOILER_EFFICIENCY] = float(user_input.get(CONF_BOILER_EFFICIENCY) or 0.90)
            data[CONF_ENVELOPE_AREA] = float(user_input.get(CONF_ENVELOPE_AREA) or 361.26)
            data[CONF_AC_HEATING_COP] = float(user_input.get(CONF_AC_HEATING_COP) or 3.0)
            return self.async_create_entry(title="", data=data)

        current = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_WATER_TEMPERATURE_ENTITY, default=current.get(CONF_WATER_TEMPERATURE_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_RETURN_WATER_TEMPERATURE_ENTITY, default=current.get(CONF_RETURN_WATER_TEMPERATURE_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_OUTDOOR_TEMPERATURE_ENTITY, default=current.get(CONF_OUTDOOR_TEMPERATURE_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_SOLAR_RADIATION_ENTITY, default=current.get(CONF_SOLAR_RADIATION_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_DELTA_T_ENTITY, default=current.get(CONF_DELTA_T_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_GAS_CONSUMPTION_ENTITY, default=current.get(CONF_GAS_CONSUMPTION_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_ELECTRICITY_BOILER_POWER_ENTITY, default=current.get(CONF_ELECTRICITY_BOILER_POWER_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_AC_POWER_ENTITY, default=current.get(CONF_AC_POWER_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_AC_ENERGY_ENTITY, default=current.get(CONF_AC_ENERGY_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_ELECTRICITY_BOILER_ENERGY_ENTITY,
                default=current.get(CONF_ELECTRICITY_BOILER_ENERGY_ENTITY, current.get(CONF_ELECTRICITY_BOILER_ENTITY)),
            ):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_COMFORT_TARGET_C, default=current.get(CONF_COMFORT_TARGET_C, 21.0)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=16, max=25, step=0.5, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_GAS_ENERGY_FACTOR, default=current.get(CONF_GAS_ENERGY_FACTOR, 10.55)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=20, step=0.01, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_BOILER_EFFICIENCY, default=current.get(CONF_BOILER_EFFICIENCY, 0.90)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=0.5, max=1.0, step=0.01, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_ENVELOPE_AREA, default=current.get(CONF_ENVELOPE_AREA, 361.26)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=10, max=2000, step=0.01, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_AC_HEATING_COP, default=current.get(CONF_AC_HEATING_COP, 3.0)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=1.0, max=8.0, step=0.1, mode=selector.NumberSelectorMode.BOX)),
        })
        return self.async_show_form(step_id="global_settings", data_schema=schema)

    async def async_step_pump_settings(self, user_input=None):
        """Configure the two manifold pump power and energy sensors."""
        current = {
            pump["id"]: pump
            for pump in self.config_entry.options.get(CONF_PUMPS, [])
        }
        if user_input is not None:
            pumps = []
            for pump_id, label in (("ground_floor", "Ground-floor manifold"), ("upstairs", "Upstairs manifold")):
                power = user_input.get(f"{pump_id}_power")
                energy = user_input.get(f"{pump_id}_energy")
                if power and energy:
                    pumps.append({
                        "id": pump_id,
                        "name": label,
                        CONF_PUMP_POWER_ENTITY: power,
                        CONF_PUMP_ENERGY_ENTITY: energy,
                    })
            data = dict(self.config_entry.options)
            data[CONF_PUMPS] = pumps
            return self.async_create_entry(title="", data=data)

        schema = vol.Schema({
            vol.Optional("ground_floor_power", default=current.get("ground_floor", {}).get("power_entity")):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional("ground_floor_energy", default=current.get("ground_floor", {}).get("energy_entity")):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional("upstairs_power", default=current.get("upstairs", {}).get("power_entity")):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional("upstairs_energy", default=current.get("upstairs", {}).get("energy_entity")):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        })
        return self.async_show_form(step_id="pump_settings", data_schema=schema)

    async def async_step_add_room(self, user_input=None):
        if user_input is not None:
            rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
            slug = re.sub(r"[^a-z0-9_]+", "_", user_input[CONF_NAME].lower()).strip("_")
            if not slug:
                slug = f"room_{len(rooms) + 1}"
            base = slug
            suffix = 2
            existing = {room["id"] for room in rooms}
            while slug in existing:
                slug = f"{base}_{suffix}"
                suffix += 1
            rooms.append({
                "id": slug,
                CONF_NAME: user_input[CONF_NAME].strip(),
                CONF_FLOOR: user_input[CONF_FLOOR],
                CONF_AREA: float(user_input[CONF_AREA]) if user_input.get(CONF_AREA) not in (None, "") else None,
                CONF_DEMAND_ENTITY: user_input[CONF_DEMAND_ENTITY],
                CONF_TEMPERATURE_ENTITY: user_input[CONF_TEMPERATURE_ENTITY],
                CONF_WINDOW_ENTITY: user_input.get(CONF_WINDOW_ENTITY),
            })
            data = dict(self.config_entry.options)
            data[CONF_ROOMS] = rooms
            return self.async_create_entry(title="", data=data)

        schema = vol.Schema({
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_FLOOR): vol.In({"ground_floor": "Downstairs", "upstairs": "Upstairs"}),
            vol.Optional(CONF_AREA): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=500, step=0.01, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)),
            vol.Required(CONF_DEMAND_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig()),
            vol.Required(CONF_TEMPERATURE_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_WINDOW_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
        })
        return self.async_show_form(step_id="add_room", data_schema=schema)

    async def async_step_room_area(self, user_input=None):
        """Select a room and configure its optional floor area."""
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            self._area_room_id = user_input["room"]
            return await self.async_step_set_room_area()
        options = {room["id"]: room[CONF_NAME] for room in rooms}
        return self.async_show_form(
            step_id="room_area",
            data_schema=vol.Schema({vol.Required("room"): vol.In(options)}),
        )

    async def async_step_set_room_area(self, user_input=None):
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        room = next((item for item in rooms if item["id"] == self._area_room_id), None)
        if room is None:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            area = user_input.get(CONF_AREA)
            data = dict(self.config_entry.options)
            data[CONF_ROOMS] = [
                {**item, CONF_AREA: float(area) if area not in (None, "") else None}
                if item["id"] == self._area_room_id else item
                for item in rooms
            ]
            return self.async_create_entry(title="", data=data)
        area_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=500,
                step=0.01,
                unit_of_measurement="m²",
                mode=selector.NumberSelectorMode.BOX,
            )
        )
        # Do not serialise a null default into the selector schema. Some
        # frontend/config-flow combinations omit the control entirely when
        # an optional NumberSelector explicitly defaults to None.
        current_area = room.get(CONF_AREA)
        area_field = (
            vol.Optional(CONF_AREA, default=current_area)
            if current_area is not None
            else vol.Optional(CONF_AREA)
        )
        schema = vol.Schema({area_field: area_selector})
        return self.async_show_form(
            step_id="set_room_area",
            data_schema=schema,
            description_placeholders={"room_name": room[CONF_NAME]},
        )

    async def async_step_edit_room(self, user_input=None):
        """Select a room whose floor assignment should be changed."""
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            self._editing_room_id = user_input["room"]
            return await self.async_step_edit_room_floor()

        options = {room["id"]: room[CONF_NAME] for room in rooms}
        schema = vol.Schema({
            vol.Required("room"): vol.In(options),
        })
        return self.async_show_form(step_id="edit_room", data_schema=schema)

    async def async_step_edit_room_floor(self, user_input=None):
        """Change the selected room's floor, defaulting to its saved value."""
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        room = next(
            (item for item in rooms if item["id"] == self._editing_room_id),
            None,
        )
        if room is None:
            return self.async_abort(reason="no_rooms")

        if user_input is not None:
            data = dict(self.config_entry.options)
            data[CONF_ROOMS] = [
                {**item, CONF_FLOOR: user_input[CONF_FLOOR]}
                if item["id"] == self._editing_room_id else item
                for item in rooms
            ]
            return self.async_create_entry(title="", data=data)

        current_floor = room.get(CONF_FLOOR, "ground_floor")
        schema = vol.Schema({
            vol.Required(CONF_FLOOR, default=current_floor):
                vol.In({"ground_floor": "Downstairs", "upstairs": "Upstairs"}),
        })
        return self.async_show_form(
            step_id="edit_room_floor",
            data_schema=schema,
            description_placeholders={"room_name": room[CONF_NAME]},
        )

    async def async_step_remove_room(self, user_input=None):
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            remove_id = user_input["room"]
            data = dict(self.config_entry.options)
            data[CONF_ROOMS] = [room for room in rooms if room["id"] != remove_id]
            return self.async_create_entry(title="", data=data)
        options = {room["id"]: room[CONF_NAME] for room in rooms}
        return self.async_show_form(
            step_id="remove_room",
            data_schema=vol.Schema({vol.Required("room"): vol.In(options)}),
        )
