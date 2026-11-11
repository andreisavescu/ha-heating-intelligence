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
    CONF_NAME,
    CONF_PUMPS,
    CONF_PUMP_ENERGY_ENTITY,
    CONF_PUMP_POWER_ENTITY,
    CONF_ROOMS,
    CONF_TEMPERATURE_ENTITY,
    CONF_WATER_TEMPERATURE_ENTITY,
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
            menu_options=["add_room", "remove_room", "global_settings", "pump_settings"],
        )

    async def async_step_global_settings(self, user_input=None):
        if user_input is not None:
            data = dict(self.config_entry.options)
            entity_id = user_input.get(CONF_WATER_TEMPERATURE_ENTITY)
            if entity_id:
                data[CONF_WATER_TEMPERATURE_ENTITY] = entity_id
            else:
                data.pop(CONF_WATER_TEMPERATURE_ENTITY, None)
            return self.async_create_entry(title="", data=data)

        current = self.config_entry.options.get(CONF_WATER_TEMPERATURE_ENTITY)
        schema = vol.Schema({
            vol.Optional(CONF_WATER_TEMPERATURE_ENTITY, default=current):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
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
                CONF_DEMAND_ENTITY: user_input[CONF_DEMAND_ENTITY],
                CONF_TEMPERATURE_ENTITY: user_input[CONF_TEMPERATURE_ENTITY],
                CONF_WINDOW_ENTITY: user_input.get(CONF_WINDOW_ENTITY),
            })
            data = dict(self.config_entry.options)
            data[CONF_ROOMS] = rooms
            return self.async_create_entry(title="", data=data)

        schema = vol.Schema({
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_DEMAND_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig()),
            vol.Required(CONF_TEMPERATURE_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_WINDOW_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
        })
        return self.async_show_form(step_id="add_room", data_schema=schema)

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
