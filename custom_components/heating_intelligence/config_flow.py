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
    CONF_ACH,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WIND_SPEED_ENTITY,
    CONF_WIND_DIRECTION_ENTITY,
    CONF_WIND_GUST_ENTITY,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_DEW_POINT_ENTITY,
    CONF_OUTDOOR_PRESSURE_ENTITY,
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
    CONF_HUMIDITY_ENTITY,
    CONF_CO2_ENTITY,
    CONF_ENABLE_CO2,
    CONF_HOUSE_ASSESSMENT,
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
            menu_options=["house_assessment", "add_room", "edit_room", "room_air_quality", "room_area", "remove_room", "global_settings", "pump_settings"],
        )

    async def async_step_house_assessment(self, user_input=None):
        """Start the optional, editable whole-house assessment."""
        self._assessment = dict(self.config_entry.options.get(CONF_HOUSE_ASSESSMENT, {}))
        if user_input is not None:
            self._assessment.update(user_input)
            return await self.async_step_assessment_heating()
        current = self._assessment

        def optional(key, selector_or_type, default=None):
            marker = vol.Optional(key, default=default) if default is not None else vol.Optional(key)
            return marker, selector_or_type

        fields = dict([
            optional("wall_construction", selector.SelectSelector(selector.SelectSelectorConfig(options=["unknown", "brick", "concrete", "timber", "mixed"], mode=selector.SelectSelectorMode.DROPDOWN)), current.get("wall_construction")),
            optional("roof_type", selector.SelectSelector(selector.SelectSelectorConfig(options=["unknown", "pitched", "flat"], mode=selector.SelectSelectorMode.DROPDOWN)), current.get("roof_type")),
            optional("floor_construction", selector.SelectSelector(selector.SelectSelectorConfig(options=["unknown", "slab_on_ground", "suspended", "mixed"], mode=selector.SelectSelectorMode.DROPDOWN)), current.get("floor_construction")),
            optional("construction_year", selector.NumberSelector(selector.NumberSelectorConfig(min=1850, max=2035, step=1, mode=selector.NumberSelectorMode.BOX)), current.get("construction_year")),
            optional("wall_insulation_cm", selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=60, step=0.5, unit_of_measurement="cm", mode=selector.NumberSelectorMode.BOX)), current.get("wall_insulation_cm", 15.0)),
            optional("wall_insulation_material", str, current.get("wall_insulation_material", "EPS 80")),
            optional("roof_insulation_material", str, current.get("roof_insulation_material")),
            optional("floor_insulation_material", str, current.get("floor_insulation_material")),
            optional("roof_insulation_cm", selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.5, unit_of_measurement="cm", mode=selector.NumberSelectorMode.BOX)), current.get("roof_insulation_cm")),
            optional("floor_insulation_cm", selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=60, step=0.5, unit_of_measurement="cm", mode=selector.NumberSelectorMode.BOX)), current.get("floor_insulation_cm")),
            optional("window_glazing", selector.SelectSelector(selector.SelectSelectorConfig(options=["unknown", "double", "triple"], mode=selector.SelectSelectorMode.DROPDOWN)), current.get("window_glazing")),
            optional("window_frame", selector.SelectSelector(selector.SelectSelectorConfig(options=["unknown", "uPVC", "wood", "aluminium", "composite"], mode=selector.SelectSelectorMode.DROPDOWN)), current.get("window_frame")),
            optional("main_orientation", selector.SelectSelector(selector.SelectSelectorConfig(options=["unknown", "north", "south", "east", "west"], mode=selector.SelectSelectorMode.DROPDOWN)), current.get("main_orientation", "unknown")),
            optional("shading", selector.SelectSelector(selector.SelectSelectorConfig(options=["unknown", "none", "partial", "significant"], mode=selector.SelectSelectorMode.DROPDOWN)), current.get("shading")),
            optional("window_area_north_m2", selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.5, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)), current.get("window_area_north_m2")),
            optional("window_area_south_m2", selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.5, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)), current.get("window_area_south_m2")),
            optional("window_area_east_m2", selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.5, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)), current.get("window_area_east_m2")),
            optional("window_area_west_m2", selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.5, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)), current.get("window_area_west_m2")),
        ])
        return self.async_show_form(step_id="house_assessment", data_schema=vol.Schema(fields))

    async def async_step_assessment_heating(self, user_input=None):
        """Collect heating plant and thermal-mass characteristics."""
        if user_input is not None:
            self._assessment.update(user_input)
            return await self.async_step_assessment_occupancy()
        current = self._assessment
        fields = {
            vol.Optional("boiler_model", default=current.get("boiler_model", "Viessmann Vitodens 100")): str,
            vol.Optional("heating_distribution", default=current.get("heating_distribution", "underfloor")): vol.In({"unknown": "Unknown", "underfloor": "Underfloor heating", "radiators": "Radiators", "mixed": "Mixed"}),
            vol.Optional("thermal_mass", default=current.get("thermal_mass", "unknown")): vol.In({"unknown": "Unknown", "light": "Lightweight", "medium": "Medium", "heavy": "High thermal mass"}),
            vol.Optional("boiler_dhw", default=current.get("boiler_dhw", "unknown")): vol.In({"unknown": "Unknown", "yes": "Yes", "no": "No"}),
            vol.Optional("buffer_tank", default=current.get("buffer_tank", "unknown")): vol.In({"unknown": "Unknown", "yes": "Yes", "no": "No"}),
            (vol.Optional("ufh_screed_cm", default=current["ufh_screed_cm"]) if current.get("ufh_screed_cm") is not None else vol.Optional("ufh_screed_cm")): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=20, step=0.5, unit_of_measurement="cm", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional("ac_heating", default=current.get("ac_heating", "yes")): vol.In({"unknown": "Unknown", "yes": "Yes", "no": "No"}),
            (vol.Optional("ac_units", default=current["ac_units"]) if current.get("ac_units") is not None else vol.Optional("ac_units")): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=20, step=1, mode=selector.NumberSelectorMode.BOX)),
        }
        return self.async_show_form(step_id="assessment_heating", data_schema=vol.Schema(fields))

    async def async_step_assessment_occupancy(self, user_input=None):
        """Collect ventilation, occupancy and internal-gain characteristics."""
        if user_input is not None:
            self._assessment.update(user_input)
            data = dict(self.config_entry.options)
            data[CONF_HOUSE_ASSESSMENT] = {key: value for key, value in self._assessment.items() if value not in (None, "")}
            return self.async_create_entry(title="", data=data)
        current = self._assessment
        fields = {
            vol.Optional("ventilation_type", default=current.get("ventilation_type", "unknown")): vol.In({"unknown": "Unknown", "natural": "Natural ventilation", "extractor": "Extractor fans", "mvhr": "Mechanical ventilation with heat recovery", "balanced": "Balanced mechanical ventilation"}),
            vol.Optional("daily_window_ventilation", default=current.get("daily_window_ventilation", "unknown")): vol.In({"unknown": "Unknown", "rare": "Rarely", "once": "Once daily", "several": "Several times daily"}),
            (vol.Optional("occupants", default=current["occupants"]) if current.get("occupants") is not None else vol.Optional("occupants")): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=20, step=1, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional("occupancy_pattern", default=current.get("occupancy_pattern", "unknown")): vol.In({"unknown": "Unknown", "home_day": "Usually occupied during daytime", "away_day": "Usually away during daytime", "variable": "Variable"}),
            vol.Optional("internal_gains", default=current.get("internal_gains", "unknown")): vol.In({"unknown": "Unknown", "low": "Low", "typical": "Typical", "high": "High (servers/PCs/appliances)"}),
            vol.Optional("staircase_layout", default=current.get("staircase_layout", "unknown")): vol.In({"unknown": "Unknown", "open": "Open between floors", "closed": "Separated"}),
        }
        return self.async_show_form(step_id="assessment_occupancy", data_schema=vol.Schema(fields))

    async def async_step_global_settings(self, user_input=None):
        """Configure optional whole-house weather and energy inputs."""
        entity_options = (
            CONF_WATER_TEMPERATURE_ENTITY,
            CONF_RETURN_WATER_TEMPERATURE_ENTITY,
            CONF_OUTDOOR_TEMPERATURE_ENTITY,
            CONF_SOLAR_RADIATION_ENTITY,
            CONF_WIND_SPEED_ENTITY,
            CONF_WIND_DIRECTION_ENTITY,
            CONF_WIND_GUST_ENTITY,
            CONF_OUTDOOR_HUMIDITY_ENTITY,
            CONF_DEW_POINT_ENTITY,
            CONF_OUTDOOR_PRESSURE_ENTITY,
            CONF_WEATHER_ENTITY,
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
        # Avoid explicit None defaults on optional entity selectors. Some
        # Home Assistant frontend versions render those as required fields.
        def optional_sensor_field(key, fallback=None):
            saved = current.get(key, fallback)
            field = vol.Optional(key, default=saved) if saved else vol.Optional(key)
            return field, selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

        schema_fields = dict(
            optional_sensor_field(key)
            for key in (
                CONF_WATER_TEMPERATURE_ENTITY,
                CONF_RETURN_WATER_TEMPERATURE_ENTITY,
                CONF_OUTDOOR_TEMPERATURE_ENTITY,
                CONF_SOLAR_RADIATION_ENTITY,
                CONF_WIND_SPEED_ENTITY,
                CONF_WIND_DIRECTION_ENTITY,
                CONF_WIND_GUST_ENTITY,
                CONF_OUTDOOR_HUMIDITY_ENTITY,
                CONF_DEW_POINT_ENTITY,
                CONF_OUTDOOR_PRESSURE_ENTITY,
                CONF_DELTA_T_ENTITY,
                CONF_GAS_CONSUMPTION_ENTITY,
                CONF_ELECTRICITY_BOILER_POWER_ENTITY,
                CONF_AC_POWER_ENTITY,
                CONF_AC_ENERGY_ENTITY,
            )
        )
        weather_field = vol.Optional(CONF_WEATHER_ENTITY, default=current.get(CONF_WEATHER_ENTITY)) if current.get(CONF_WEATHER_ENTITY) else vol.Optional(CONF_WEATHER_ENTITY)
        schema_fields[weather_field] = selector.EntitySelector(selector.EntitySelectorConfig(domain="weather"))
        boiler_energy_field = optional_sensor_field(
            CONF_ELECTRICITY_BOILER_ENERGY_ENTITY,
            current.get(CONF_ELECTRICITY_BOILER_ENTITY),
        )
        schema_fields[boiler_energy_field[0]] = boiler_energy_field[1]
        schema = vol.Schema({
            **schema_fields,
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
            self._new_room_input = dict(user_input)
            if user_input.get(CONF_ENABLE_CO2):
                return await self.async_step_add_room_co2()
            return self._save_new_room(None)

        schema = vol.Schema({
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_FLOOR): vol.In({"ground_floor": "Downstairs", "upstairs": "Upstairs"}),
            vol.Optional(CONF_AREA): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=500, step=0.01, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_ACH): selector.NumberSelector(selector.NumberSelectorConfig(min=0.01, max=20, step=0.01, unit_of_measurement="1/h", mode=selector.NumberSelectorMode.BOX)),
            vol.Required(CONF_DEMAND_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig()),
            vol.Required(CONF_TEMPERATURE_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_WINDOW_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            vol.Optional(CONF_HUMIDITY_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_ENABLE_CO2, default=False): selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="add_room", data_schema=schema)

    async def async_step_add_room_co2(self, user_input=None):
        if user_input is not None:
            return self._save_new_room(user_input.get(CONF_CO2_ENTITY))
        return self.async_show_form(
            step_id="add_room_co2",
            data_schema=vol.Schema({
                vol.Required(CONF_CO2_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            }),
            description_placeholders={"room_name": self._new_room_input[CONF_NAME]},
        )

    def _save_new_room(self, co2_entity):
        user_input = self._new_room_input
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
            CONF_ACH: float(user_input[CONF_ACH]) if user_input.get(CONF_ACH) not in (None, "") else None,
            CONF_DEMAND_ENTITY: user_input[CONF_DEMAND_ENTITY],
            CONF_TEMPERATURE_ENTITY: user_input[CONF_TEMPERATURE_ENTITY],
            CONF_WINDOW_ENTITY: user_input.get(CONF_WINDOW_ENTITY),
            CONF_HUMIDITY_ENTITY: user_input.get(CONF_HUMIDITY_ENTITY),
            CONF_CO2_ENTITY: co2_entity,
        })
        data = dict(self.config_entry.options)
        data[CONF_ROOMS] = rooms
        return self.async_create_entry(title="", data=data)

    async def async_step_room_air_quality(self, user_input=None):
        """Configure humidity and optional CO2 for an existing room."""
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            self._air_room_id = user_input["room"]
            return await self.async_step_room_air_quality_config()
        return self.async_show_form(
            step_id="room_air_quality",
            data_schema=vol.Schema({
                vol.Required("room"): vol.In({room["id"]: room[CONF_NAME] for room in rooms}),
            }),
        )

    async def async_step_room_air_quality_config(self, user_input=None):
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        room = next((item for item in rooms if item["id"] == self._air_room_id), None)
        if room is None:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            self._air_room_input = dict(user_input)
            if user_input.get(CONF_ENABLE_CO2):
                return await self.async_step_room_air_quality_co2()
            return self._save_room_air_quality(None)
        fields = {
            vol.Optional(CONF_HUMIDITY_ENTITY, default=room.get(CONF_HUMIDITY_ENTITY)) if room.get(CONF_HUMIDITY_ENTITY) else vol.Optional(CONF_HUMIDITY_ENTITY):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_ENABLE_CO2, default=bool(room.get(CONF_CO2_ENTITY))): selector.BooleanSelector(),
        }
        return self.async_show_form(
            step_id="room_air_quality_config",
            data_schema=vol.Schema(fields),
            description_placeholders={"room_name": room[CONF_NAME]},
        )

    async def async_step_room_air_quality_co2(self, user_input=None):
        if user_input is not None:
            return self._save_room_air_quality(user_input.get(CONF_CO2_ENTITY))
        return self.async_show_form(
            step_id="room_air_quality_co2",
            data_schema=vol.Schema({
                vol.Required(CONF_CO2_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            }),
        )

    def _save_room_air_quality(self, co2_entity):
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        updated = []
        for room in rooms:
            if room["id"] == self._air_room_id:
                room = dict(room)
                humidity = self._air_room_input.get(CONF_HUMIDITY_ENTITY)
                if humidity:
                    room[CONF_HUMIDITY_ENTITY] = humidity
                else:
                    room.pop(CONF_HUMIDITY_ENTITY, None)
                if co2_entity:
                    room[CONF_CO2_ENTITY] = co2_entity
                else:
                    room.pop(CONF_CO2_ENTITY, None)
            updated.append(room)
        data = dict(self.config_entry.options)
        data[CONF_ROOMS] = updated
        return self.async_create_entry(title="", data=data)

    async def async_step_room_area(self, user_input=None):
        """Select a room to configure floor area, measured ACH and window contact."""
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
        """Save the selected room area, measured ACH and window contact."""
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        room = next((item for item in rooms if item["id"] == self._area_room_id), None)
        if room is None:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            area = user_input.get(CONF_AREA)
            data = dict(self.config_entry.options)
            ach = user_input.get(CONF_ACH)
            data[CONF_ROOMS] = [
                {
                    **item,
                    CONF_AREA: float(area) if area not in (None, "") else None,
                    CONF_ACH: float(ach) if ach not in (None, "") else None,
                    CONF_WINDOW_ENTITY: user_input.get(CONF_WINDOW_ENTITY),
                }
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
        current_ach = room.get(CONF_ACH)
        ach_field = (
            vol.Optional(CONF_ACH, default=current_ach)
            if current_ach is not None
            else vol.Optional(CONF_ACH)
        )
        ach_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.01, max=20, step=0.01,
                unit_of_measurement="1/h",
                mode=selector.NumberSelectorMode.BOX,
            )
        )
        current_window = room.get(CONF_WINDOW_ENTITY)
        window_field = (
            vol.Optional(CONF_WINDOW_ENTITY, default=current_window)
            if current_window is not None
            else vol.Optional(CONF_WINDOW_ENTITY)
        )
        window_selector = selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))
        schema = vol.Schema({
            area_field: area_selector,
            ach_field: ach_selector,
            window_field: window_selector,
        })
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
