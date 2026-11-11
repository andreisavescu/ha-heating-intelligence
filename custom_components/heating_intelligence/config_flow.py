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
    CONF_ROOMS,
    CONF_TEMPERATURE_ENTITY,
    CONF_WINDOW_ENTITY,
    DOMAIN,
)


class HeatingIntelligenceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Create the integration entry."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            return self.async_create_entry(title="Heating Intelligence", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HeatingIntelligenceOptionsFlow(config_entry)


class HeatingIntelligenceOptionsFlow(config_entries.OptionsFlow):
    """Manage rooms."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_room", "remove_room"],
        )

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
            return self.async_create_entry(title="", data={CONF_ROOMS: rooms})

        schema = vol.Schema({
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_DEMAND_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig()
            ),
            vol.Required(CONF_TEMPERATURE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig()
            ),
            vol.Optional(CONF_WINDOW_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig()
            ),
        })
        return self.async_show_form(step_id="add_room", data_schema=schema)

    async def async_step_remove_room(self, user_input=None):
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            remove_id = user_input["room"]
            rooms = [room for room in rooms if room["id"] != remove_id]
            return self.async_create_entry(title="", data={CONF_ROOMS: rooms})
        options = {room["id"]: room[CONF_NAME] for room in rooms}
        schema = vol.Schema({vol.Required("room"): vol.In(options)})
        return self.async_show_form(step_id="remove_room", data_schema=schema)
