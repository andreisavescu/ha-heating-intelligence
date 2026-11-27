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
    CONF_CALIBRATION_HISTORY_DAYS,
    DEFAULT_CALIBRATION_HISTORY_DAYS,
    CONF_COMFORT_TARGET_C,
    CONF_FLOOR,
    CONF_AREA,
    CONF_ACH,
    CONF_UFH_SCREED_THICKNESS_CM,
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
    CONF_HEATING_GAS_ENTITY,
    CONF_DHW_GAS_ENTITY,
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
    CONF_WATER_FLOW_RATE_ENTITY,
    CONF_GROUND_FLOOR_THERMAL_OUTPUT_ENTITY,
    CONF_UPSTAIRS_THERMAL_OUTPUT_ENTITY,
    CONF_WINDOW_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_CO2_ENTITY,
    CONF_ENABLE_CO2,
    CONF_HOUSE_ASSESSMENT,
    CONF_WINDOWS,
    CONF_BOILER_BURNER_ENTITY,
    CONF_BOILER_MODULATION_ENTITY,
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
            menu_options=["house_assessment", "windows", "envelope_surfaces", "floor_finishes", "thermal_topology", "add_room", "edit_rooms", "remove_room", "global_settings", "pump_settings", "ufh_circuits"],
        )

    async def async_step_envelope_surfaces(self, user_input=None):
        """Add, edit or remove explicit opaque envelope surface records."""
        surfaces = list(self.config_entry.options.get("envelope_surfaces", []))
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        if not rooms:
            return self.async_abort(reason="no_rooms")
        actions = {"add": "Add a surface", "edit": "Edit a surface", "remove": "Remove a surface"}
        if user_input is not None:
            action = user_input["action"]
            if action == "add":
                self._surface_edit_id = None
                return await self.async_step_envelope_surface_details()
            self._surface_edit_id = user_input.get("surface")
            if not self._surface_edit_id or self._surface_edit_id not in {x.get("id") for x in surfaces}:
                return self.async_show_form(step_id="envelope_surfaces", data_schema=vol.Schema({
                    vol.Required("action", default=action): vol.In(actions),
                    vol.Required("surface"): vol.In({x["id"]: x.get("name", x["id"]) for x in surfaces}),
                }), errors={"surface": "required"})
            if action == "remove":
                data = dict(self.config_entry.options)
                data["envelope_surfaces"] = [x for x in surfaces if x.get("id") != self._surface_edit_id]
                return self.async_create_entry(title="", data=data)
            return await self.async_step_envelope_surface_details()
        fields = {vol.Required("action", default="add"): vol.In(actions)}
        if surfaces:
            fields[vol.Optional("surface")] = vol.In({x["id"]: x.get("name", x["id"]) for x in surfaces})
        return self.async_show_form(step_id="envelope_surfaces", data_schema=vol.Schema(fields))

    async def async_step_envelope_surface_details(self, user_input=None):
        """Store one measured surface with an explicit whole-assembly U-value."""
        surfaces = list(self.config_entry.options.get("envelope_surfaces", []))
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        existing = next((x for x in surfaces if x.get("id") == self._surface_edit_id), {}) if self._surface_edit_id else {}
        if user_input is not None:
            name = str(user_input["name"]).strip()
            slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "surface"
            surface_id = self._surface_edit_id or slug
            if not self._surface_edit_id:
                base, suffix = slug, 2
                ids = {x.get("id") for x in surfaces}
                while surface_id in ids:
                    surface_id = f"{base}_{suffix}"
                    suffix += 1
            record = {"id": surface_id, "name": name, "room_id": user_input["room_id"],
                      "boundary": user_input["boundary"], "area_m2": float(user_input["area_m2"]),
                      "u_value_w_m2k": float(user_input["u_value_w_m2k"])}
            data = dict(self.config_entry.options)
            data["envelope_surfaces"] = [x for x in surfaces if x.get("id") != surface_id] + [record]
            return self.async_create_entry(title="", data=data)
        def num(key, default, minimum, maximum, unit, step):
            marker = vol.Optional(key, default=default) if default is not None else vol.Required(key)
            return marker, selector.NumberSelector(selector.NumberSelectorConfig(min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX))
        area_marker, area_selector = num("area_m2", existing.get("area_m2"), 0.01, 1000, "m²", 0.01)
        u_marker, u_selector = num("u_value_w_m2k", existing.get("u_value_w_m2k"), 0.01, 10, "W/m²K", 0.001)
        fields = {
            vol.Required("name", default=existing.get("name", "")): str,
            vol.Required("room_id", default=existing.get("room_id", rooms[0]["id"])): vol.In({x["id"]: x[CONF_NAME] for x in rooms}),
            vol.Required("boundary", default=existing.get("boundary", "outdoor")): vol.In({"outdoor": "Outdoors", "ground": "Ground", "attic": "Unheated attic", "adjacent_heated": "Adjacent heated space"}),
            area_marker: area_selector,
            u_marker: u_selector,
        }
        return self.async_show_form(step_id="envelope_surface_details", data_schema=vol.Schema(fields))

    async def async_step_floor_finishes(self, user_input=None):
        """Add, edit or remove per-room floor-finish zones."""
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        zones = list(self.config_entry.options.get("floor_finish_zones", []))
        if not rooms:
            return self.async_abort(reason="no_rooms")
        actions = {"add": "Add a floor zone", "edit": "Edit a floor zone", "remove": "Remove a floor zone"}
        if user_input is not None:
            action = user_input["action"]
            if action == "add":
                self._floor_finish_edit_id = None
                return await self.async_step_floor_finish_details()
            self._floor_finish_edit_id = user_input.get("zone")
            if not self._floor_finish_edit_id or self._floor_finish_edit_id not in {z.get("id") for z in zones}:
                return self.async_show_form(step_id="floor_finishes", data_schema=vol.Schema({
                    vol.Required("action", default=action): vol.In(actions),
                    vol.Required("zone"): vol.In({z["id"]: f'{z.get("name", z["id"])} — {next((r[CONF_NAME] for r in rooms if r["id"] == z.get("room_id")), z.get("room_id"))}' for z in zones}),
                }), errors={"zone": "required"})
            if action == "remove":
                data = dict(self.config_entry.options)
                data["floor_finish_zones"] = [z for z in zones if z.get("id") != self._floor_finish_edit_id]
                return self.async_create_entry(title="", data=data)
            return await self.async_step_floor_finish_details()
        fields = {vol.Required("action", default="add"): vol.In(actions)}
        if zones:
            fields[vol.Optional("zone")] = vol.In({z["id"]: f'{z.get("name", z["id"])} — {next((r[CONF_NAME] for r in rooms if r["id"] == z.get("room_id")), z.get("room_id"))}' for z in zones})
        return self.async_show_form(step_id="floor_finishes", data_schema=vol.Schema(fields))

    async def async_step_floor_finish_details(self, user_input=None):
        """Store one independently editable finish zone over the UFH floor."""
        zones = list(self.config_entry.options.get("floor_finish_zones", []))
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        existing = next((z for z in zones if z.get("id") == self._floor_finish_edit_id), {}) if self._floor_finish_edit_id else {}
        presets = {
            "ceramic_tile": {"label": "Ceramic tile", "conductivity": 1.3, "density": 2300.0, "specific_heat": 840.0, "thickness": 10.0},
            "wood_parquet": {"label": "Wood / parquet", "conductivity": 0.15, "density": 700.0, "specific_heat": 1600.0, "thickness": 10.0},
            "custom": {"label": "Custom material", "conductivity": 0.5, "density": 1200.0, "specific_heat": 1000.0, "thickness": 10.0},
        }
        if user_input is not None:
            material = user_input["material"]
            preset = presets[material]
            name = str(user_input.get("name") or preset["label"]).strip()
            slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "floor_zone"
            zone_id = self._floor_finish_edit_id or slug
            if not self._floor_finish_edit_id:
                base, suffix = slug, 2
                ids = {z.get("id") for z in zones}
                while zone_id in ids:
                    zone_id = f"{base}_{suffix}"
                    suffix += 1
            record = {
                "id": zone_id, "name": name, "room_id": user_input["room_id"],
                "material": material, "area_m2": float(user_input["area_m2"]),
                "thickness_mm": float(user_input.get("thickness_mm") or preset["thickness"]),
                "conductivity_w_mk": float(user_input.get("conductivity_w_mk") or preset["conductivity"]),
                "density_kg_m3": float(user_input.get("density_kg_m3") or preset["density"]),
                "specific_heat_j_kgk": float(user_input.get("specific_heat_j_kgk") or preset["specific_heat"]),
            }
            data = dict(self.config_entry.options)
            data["floor_finish_zones"] = [z for z in zones if z.get("id") != zone_id] + [record]
            return self.async_create_entry(title="", data=data)

        default_room = next(
            (room for room in rooms if room["id"] == existing.get("room_id")),
            rooms[0],
        )
        floor_default = "wood_parquet" if default_room.get(CONF_FLOOR) == "upstairs" else "ceramic_tile"
        material = existing.get("material", floor_default)
        preset = presets.get(material, presets["custom"])
        def number_field(key, default, minimum, maximum, step, unit):
            return (vol.Optional(key, default=existing.get(key, default)),
                selector.NumberSelector(selector.NumberSelectorConfig(min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX)))
        fields = {
            vol.Required("room_id", default=existing.get("room_id", rooms[0]["id"])): vol.In({r["id"]: r[CONF_NAME] for r in rooms}),
            vol.Optional("name", default=existing.get("name", preset["label"])): str,
            vol.Required("material", default=material): vol.In({key: value["label"] for key, value in presets.items()}),
        }
        for key, default, minimum, maximum, step, unit in [
            ("area_m2", 1.0, 0.01, 500, 0.01, "m²"),
            ("thickness_mm", preset["thickness"], 1, 100, 0.5, "mm"),
            ("conductivity_w_mk", preset["conductivity"], 0.01, 10, 0.01, "W/m·K"),
            ("density_kg_m3", preset["density"], 50, 5000, 10, "kg/m³"),
            ("specific_heat_j_kgk", preset["specific_heat"], 100, 5000, 10, "J/kg·K"),
        ]:
            marker, control = number_field(key, default, minimum, maximum, step, unit)
            fields[marker] = control
        return self.async_show_form(step_id="floor_finish_details", data_schema=vol.Schema(fields))

    async def async_step_windows(self, user_input=None):
        """Add, edit or remove individual physical window records."""
        windows = list(self.config_entry.options.get(CONF_WINDOWS, []))
        if user_input is not None:
            action = user_input["action"]
            if action == "add":
                self._window_edit_id = None
                return await self.async_step_window_details()
            if not windows:
                return self.async_abort(reason="no_windows")
            self._window_edit_id = user_input.get("window")
            if not self._window_edit_id or self._window_edit_id not in {item.get("id") for item in windows}:
                return self.async_show_form(
                    step_id="windows",
                    data_schema=vol.Schema({
                        vol.Required("action", default=action): vol.In({"add": "Add a window", "edit": "Edit a window", "remove": "Remove a window"}),
                        vol.Required("window"): vol.In({item["id"]: item.get("name", item["id"]) for item in windows}),
                    }),
                    errors={"window": "required"},
                )
            if action == "remove":
                data = dict(self.config_entry.options)
                data[CONF_WINDOWS] = [item for item in windows if item.get("id") != self._window_edit_id]
                return self.async_create_entry(title="", data=data)
            return await self.async_step_window_details()
        choices = {item["id"]: item.get("name", item["id"]) for item in windows}
        fields = {vol.Required("action", default="add"): vol.In({"add": "Add a window", "edit": "Edit a window", "remove": "Remove a window"})}
        if choices:
            fields[vol.Optional("window")] = vol.In(choices)
        return self.async_show_form(step_id="windows", data_schema=vol.Schema(fields))

    async def async_step_window_details(self, user_input=None):
        """Store measured per-window geometry and descriptive optical metadata."""
        windows = list(self.config_entry.options.get(CONF_WINDOWS, []))
        existing = next((item for item in windows if item.get("id") == self._window_edit_id), {}) if self._window_edit_id else {}
        rooms = self.config_entry.options.get(CONF_ROOMS, [])
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            name = str(user_input["name"]).strip()
            slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "window"
            window_id = self._window_edit_id or slug
            if not self._window_edit_id:
                base, suffix = slug, 2
                existing_ids = {item.get("id") for item in windows}
                while window_id in existing_ids:
                    window_id = f"{base}_{suffix}"
                    suffix += 1
            record = {
                "id": window_id,
                "name": name,
                "room_id": user_input["room_id"],
                "orientation": user_input["orientation"],
                "glass_area_m2": float(user_input["glass_area_m2"]),
                "frame_area_m2": float(user_input["frame_area_m2"]) if user_input.get("frame_area_m2") not in (None, "") else None,
                "whole_window_u_value_w_m2k": float(user_input["whole_window_u_value_w_m2k"]) if user_input.get("whole_window_u_value_w_m2k") not in (None, "") else None,
                "glazing": user_input.get("glazing", "unknown"),
                "frame_material": user_input.get("frame_material", "unknown"),
                "shading": user_input.get("shading", "unknown"),
                "shading_behaviour": user_input.get("shading_behaviour", "unknown"),
            }
            data = dict(self.config_entry.options)
            data[CONF_WINDOWS] = [item for item in windows if item.get("id") != window_id] + [record]
            return self.async_create_entry(title="", data=data)

        def optional_number(key, minimum, maximum, unit):
            saved = existing.get(key)
            marker = vol.Optional(key, default=saved) if saved is not None else vol.Optional(key)
            return marker, selector.NumberSelector(selector.NumberSelectorConfig(min=minimum, max=maximum, step=0.01, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX))
        fields = {
            vol.Required("name", default=existing.get("name", "")): str,
            vol.Required("room_id", default=existing.get("room_id", rooms[0]["id"])): vol.In({item["id"]: item["name"] for item in rooms}),
            vol.Required("orientation", default=existing.get("orientation", "south")): vol.In({"north": "North", "east": "East", "south": "South", "west": "West"}),
            vol.Required("glass_area_m2", default=existing.get("glass_area_m2", 1.0)): selector.NumberSelector(selector.NumberSelectorConfig(min=0.01, max=100, step=0.01, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)),
            (vol.Optional("frame_area_m2", default=existing["frame_area_m2"]) if existing.get("frame_area_m2") is not None else vol.Optional("frame_area_m2")): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=30, step=0.01, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)),
            (vol.Optional("whole_window_u_value_w_m2k", default=existing["whole_window_u_value_w_m2k"]) if existing.get("whole_window_u_value_w_m2k") is not None else vol.Optional("whole_window_u_value_w_m2k")): selector.NumberSelector(selector.NumberSelectorConfig(min=0.05, max=5, step=0.01, unit_of_measurement="W/m²K", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional("glazing", default=existing.get("glazing", "unknown")): vol.In({"unknown": "Unknown", "double": "Double", "triple": "Triple"}),
            vol.Optional("frame_material", default=existing.get("frame_material", "unknown")): vol.In({"unknown": "Unknown", "uPVC": "uPVC", "wood": "Wood", "aluminium": "Aluminium", "composite": "Composite"}),
            vol.Optional("shading", default=existing.get("shading", "unknown")): vol.In({"unknown": "Unknown", "none": "None", "blinds": "Blinds", "shutters": "Shutters", "external": "External shading", "mixed": "Mixed"}),
            vol.Optional("shading_behaviour", default=existing.get("shading_behaviour", "unknown")): vol.In({"unknown": "Unknown", "raised_when_sunny": "Generally raised when sunny", "usually_closed": "Usually closed", "manual_variable": "Manually variable", "fixed": "Fixed"}),
        }
        return self.async_show_form(step_id="window_details", data_schema=vol.Schema(fields))

    async def async_step_thermal_topology(self, user_input=None):
        """Configure explicit room and attic thermal conductances."""
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        if not rooms:
            return self.async_abort(reason="no_rooms")
        room_options = {item["id"]: item[CONF_NAME] for item in rooms}
        if user_input is not None:
            data = dict(self.config_entry.options)
            updated_rooms = [dict(item) for item in rooms]
            envelope_room = user_input.get("envelope_room")
            envelope_value = user_input.get("room_envelope_conductance")
            if envelope_room and envelope_value is not None:
                value = float(envelope_value)
                if value <= 0:
                    return self.async_show_form(
                        step_id="thermal_topology",
                        data_schema=self._thermal_topology_schema(room_options, data),
                        errors={"room_envelope_conductance": "must_be_positive"},
                    )
                for room in updated_rooms:
                    if room["id"] == envelope_room:
                        room["envelope_conductance_w_per_k"] = value
            data[CONF_ROOMS] = updated_rooms

            source = user_input.get("from_room")
            target = user_input.get("to_room")
            link_value = user_input.get("inter_room_conductance")
            if source and target and link_value is not None:
                value = float(link_value)
                if source == target or value <= 0:
                    return self.async_show_form(
                        step_id="thermal_topology",
                        data_schema=self._thermal_topology_schema(room_options, data),
                        errors={"inter_room_conductance": "invalid_connection"},
                    )
                assessment = dict(data.get(CONF_HOUSE_ASSESSMENT, {}))
                links = list(assessment.get("room_connections", []))
                links = [item for item in links if {item.get("from_room"), item.get("to_room")} != {source, target}]
                links.append({"from_room": source, "to_room": target, "conductance_w_per_k": value})
                assessment["room_connections"] = links
                data[CONF_HOUSE_ASSESSMENT] = assessment

            attic_room = user_input.get("attic_floor_room")
            attic_value = user_input.get("attic_floor_conductance")
            if attic_room and attic_value is not None:
                value = float(attic_value)
                room = next((item for item in updated_rooms if item["id"] == attic_room), None)
                if room is None or room.get(CONF_FLOOR) != "upstairs" or value <= 0:
                    return self.async_show_form(
                        step_id="thermal_topology",
                        data_schema=self._thermal_topology_schema(room_options, data),
                        errors={"attic_floor_conductance": "invalid_upstairs_conductance"},
                    )
                assessment = dict(data.get(CONF_HOUSE_ASSESSMENT, {}))
                shares = dict(assessment.get("attic_floor_room_conductances_w_per_k", {}))
                shares[attic_room] = value
                assessment["attic_floor_room_conductances_w_per_k"] = shares
                data[CONF_HOUSE_ASSESSMENT] = assessment
            return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="thermal_topology",
            data_schema=self._thermal_topology_schema(room_options, self.config_entry.options),
        )

    @staticmethod
    def _thermal_topology_schema(room_options, options):
        """Build optional conductance fields without supplying physical defaults."""
        assessment = options.get(CONF_HOUSE_ASSESSMENT, {})
        return vol.Schema({
            vol.Optional("envelope_room"): vol.In(room_options),
            vol.Optional("room_envelope_conductance"): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.01, max=10000, step=0.1, unit_of_measurement="W/K", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional("from_room"): vol.In(room_options),
            vol.Optional("to_room"): vol.In(room_options),
            vol.Optional("inter_room_conductance"): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.01, max=10000, step=0.1, unit_of_measurement="W/K", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional("attic_floor_room"): vol.In({
                item["id"]: item[CONF_NAME] for item in options.get(CONF_ROOMS, [])
                if item.get(CONF_FLOOR) == "upstairs"
            }),
            vol.Optional("attic_floor_conductance"): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.01, max=10000, step=0.1, unit_of_measurement="W/K", mode=selector.NumberSelectorMode.BOX)
            ),
        })

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
            optional("roof_area_m2", selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=1000, step=0.5, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)), current.get("roof_area_m2")),
            optional("roof_u_value", selector.NumberSelector(selector.NumberSelectorConfig(min=0.05, max=5, step=0.01, unit_of_measurement="W/m²K", mode=selector.NumberSelectorMode.BOX)), current.get("roof_u_value")),
            optional("attic_floor_area_m2", selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=1000, step=0.5, unit_of_measurement="m²", mode=selector.NumberSelectorMode.BOX)), current.get("attic_floor_area_m2")),
            optional("attic_floor_u_value", selector.NumberSelector(selector.NumberSelectorConfig(min=0.05, max=5, step=0.01, unit_of_measurement="W/m²K", mode=selector.NumberSelectorMode.BOX)), current.get("attic_floor_u_value")),
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
            CONF_WATER_FLOW_RATE_ENTITY,
            CONF_GROUND_FLOOR_THERMAL_OUTPUT_ENTITY,
            CONF_UPSTAIRS_THERMAL_OUTPUT_ENTITY,
            CONF_OUTDOOR_TEMPERATURE_ENTITY,
            CONF_SOLAR_RADIATION_ENTITY,
            CONF_WIND_SPEED_ENTITY,
            CONF_WIND_DIRECTION_ENTITY,
            CONF_WIND_GUST_ENTITY,
            CONF_OUTDOOR_HUMIDITY_ENTITY,
            CONF_DEW_POINT_ENTITY,
            CONF_OUTDOOR_PRESSURE_ENTITY,
            CONF_ATTIC_TEMPERATURE_ENTITY,
            CONF_ATTIC_HUMIDITY_ENTITY,
            CONF_WEATHER_ENTITY,
            CONF_DELTA_T_ENTITY,
            CONF_GAS_CONSUMPTION_ENTITY,
            CONF_HEATING_GAS_ENTITY,
            CONF_DHW_GAS_ENTITY,
            CONF_ELECTRICITY_BOILER_POWER_ENTITY,
            CONF_ELECTRICITY_BOILER_ENERGY_ENTITY,
            CONF_AC_POWER_ENTITY,
            CONF_AC_ENERGY_ENTITY,
            CONF_BOILER_MODULATION_ENTITY,
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
            burner_entity = user_input.get(CONF_BOILER_BURNER_ENTITY)
            if burner_entity:
                data[CONF_BOILER_BURNER_ENTITY] = burner_entity
            else:
                data.pop(CONF_BOILER_BURNER_ENTITY, None)
            data[CONF_COMFORT_TARGET_C] = float(user_input.get(CONF_COMFORT_TARGET_C) or 21.0)
            data[CONF_GAS_ENERGY_FACTOR] = float(user_input.get(CONF_GAS_ENERGY_FACTOR) or 10.55)
            data[CONF_BOILER_EFFICIENCY] = float(user_input.get(CONF_BOILER_EFFICIENCY) or 0.90)
            data[CONF_ENVELOPE_AREA] = float(user_input.get(CONF_ENVELOPE_AREA) or 361.26)
            data[CONF_AC_HEATING_COP] = float(user_input.get(CONF_AC_HEATING_COP) or 3.0)
            data[CONF_CALIBRATION_HISTORY_DAYS] = int(user_input.get(CONF_CALIBRATION_HISTORY_DAYS) or self.config_entry.options.get(CONF_CALIBRATION_HISTORY_DAYS, DEFAULT_CALIBRATION_HISTORY_DAYS))
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
                CONF_WATER_FLOW_RATE_ENTITY,
                CONF_GROUND_FLOOR_THERMAL_OUTPUT_ENTITY,
                CONF_UPSTAIRS_THERMAL_OUTPUT_ENTITY,
                CONF_OUTDOOR_TEMPERATURE_ENTITY,
                CONF_SOLAR_RADIATION_ENTITY,
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
                CONF_HEATING_GAS_ENTITY,
                CONF_DHW_GAS_ENTITY,
                CONF_ELECTRICITY_BOILER_POWER_ENTITY,
                CONF_AC_POWER_ENTITY,
                CONF_AC_ENERGY_ENTITY,
                CONF_BOILER_MODULATION_ENTITY,
            )
        )
        burner_field = vol.Optional(CONF_BOILER_BURNER_ENTITY, default=current.get(CONF_BOILER_BURNER_ENTITY)) if current.get(CONF_BOILER_BURNER_ENTITY) else vol.Optional(CONF_BOILER_BURNER_ENTITY)
        schema_fields[burner_field] = selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))
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
            vol.Optional(CONF_CALIBRATION_HISTORY_DAYS, default=current.get(CONF_CALIBRATION_HISTORY_DAYS, DEFAULT_CALIBRATION_HISTORY_DAYS)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=7, max=365, step=1, unit_of_measurement="days", mode=selector.NumberSelectorMode.BOX)),
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

    async def async_step_ufh_circuits(self, user_input=None):
        """Select one of the 16 UFH circuits to configure."""
        rooms = self.config_entry.options.get(CONF_ROOMS, [])
        pumps = self.config_entry.options.get(CONF_PUMPS, [])
        if not pumps:
            return self.async_abort(reason="no_pumps")
        if user_input is not None:
            self._ufh_circuit_number = int(user_input["circuit_number"])
            return await self.async_step_ufh_circuit_details()

        configured = {
            item["id"]: item
            for item in self.config_entry.options.get("ufh_circuits", [])
        }
        return self.async_show_form(
            step_id="ufh_circuits",
            data_schema=vol.Schema({
                vol.Required("circuit_number", default=1): vol.In({
                    number: (
                        f"Circuit {number:02d}"
                        + (" — configured" if f"circuit_{number:02d}" in configured else "")
                    )
                    for number in range(1, 17)
                }),
            }),
        )

    async def async_step_ufh_circuit_details(self, user_input=None):
        """Configure a generic UFH circuit and its demand sources."""
        rooms = self.config_entry.options.get(CONF_ROOMS, [])
        pumps = self.config_entry.options.get(CONF_PUMPS, [])
        circuit_id = f"circuit_{self._ufh_circuit_number:02d}"
        existing = next(
            (item for item in self.config_entry.options.get("ufh_circuits", [])
             if item.get("id") == circuit_id),
            {},
        )
        room_options = {item["id"]: item[CONF_NAME] for item in rooms}
        demand_room_options = {
            item["id"]: item[CONF_NAME] for item in rooms
            if item.get(CONF_DEMAND_ENTITY)
        }
        pump_options = {item["id"]: item["name"] for item in pumps}
        floor_options = {"ground_floor": "Downstairs", "upstairs": "Upstairs"}

        if user_input is not None:
            floor = user_input["floor"]
            demand_scope = user_input.get("demand_scope", "selected_rooms")
            demand_rooms = user_input.get("demand_rooms") or []
            if not isinstance(demand_rooms, list):
                demand_rooms = [demand_rooms]
            if len(demand_rooms) > 4:
                return self.async_show_form(
                    step_id="ufh_circuit_details",
                    data_schema=self._ufh_circuit_schema(existing, demand_room_options, floor_options, room_options),
                    errors={"demand_rooms": "max_four_demands"},
                    description_placeholders={
                        "circuit": f"{self._ufh_circuit_number:02d}",
                        "flow_input": f"number.ufh_circuit_{self._ufh_circuit_number:02d}_flow",
                    },
                )
            # Manifold pump is selected by floor; do not allow a circuit to
            # accidentally bind to the other floor's pump.
            pump_id = floor
            if pump_id not in pump_options:
                return self.async_show_form(
                    step_id="ufh_circuit_details",
                    data_schema=self._ufh_circuit_schema(existing, demand_room_options, floor_options, room_options),
                    errors={"floor": "missing_floor_pump"},
                    description_placeholders={
                        "circuit": f"{self._ufh_circuit_number:02d}",
                        "flow_input": f"number.ufh_circuit_{self._ufh_circuit_number:02d}_flow",
                    },
                )
            circuit = {
                "id": circuit_id,
                "circuit_number": self._ufh_circuit_number,
                "name": (user_input.get("name") or f"Circuit {self._ufh_circuit_number}").strip(),
                "room_id": None,
                "floor": floor,
                "pipe_length_m": float(user_input["pipe_length_m"]),
                "pump_id": pump_id,
                "demand_scope": demand_scope,
                "demand_rooms": demand_rooms[:4],
                "served_rooms": list(user_input.get("served_rooms") or []),
                "demand_logic": "or",
            }
            updated = dict(self.config_entry.options)
            updated["ufh_circuits"] = [
                item for item in self.config_entry.options.get("ufh_circuits", [])
                if item.get("id") != circuit_id
            ] + [circuit]
            return self.async_create_entry(title="", data=updated)

        return self.async_show_form(
            step_id="ufh_circuit_details",
            data_schema=self._ufh_circuit_schema(existing, demand_room_options, floor_options, room_options),
            description_placeholders={
                "circuit": f"{self._ufh_circuit_number:02d}",
                "flow_input": f"number.ufh_circuit_{self._ufh_circuit_number:02d}_flow",
            },
        )

    @staticmethod
    def _ufh_circuit_schema(existing, demand_room_options, floor_options, room_options):
        """Build a circuit form shared by initial display and validation errors."""
        default_floor = existing.get("floor", "ground_floor")
        return vol.Schema({
            vol.Required("name", default=existing.get("name", "")): str,
            vol.Required("floor", default=default_floor): vol.In(floor_options),
            vol.Required("pipe_length_m", default=existing.get("pipe_length_m", 80.0)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=250, step=0.5, unit_of_measurement="m", mode=selector.NumberSelectorMode.BOX)),
            vol.Required("demand_scope", default=existing.get("demand_scope", "selected_rooms")):
                vol.In({
                    "selected_rooms": "Selected heating demands (up to 4, OR)",
                    "floor_any": "Any configured room on this floor (OR)",
                }),
            vol.Optional("demand_rooms", default=[
                room_id for room_id in existing.get("demand_rooms", [])
                if room_id in demand_room_options
            ]):
                selector.SelectSelector(selector.SelectSelectorConfig(options=[
                    {"value": room_id, "label": label} for room_id, label in demand_room_options.items()
                ], multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional("served_rooms", default=existing.get("served_rooms", existing.get("demand_rooms", []))):
                selector.SelectSelector(selector.SelectSelectorConfig(options=[
                    {"value": room_id, "label": label} for room_id, label in room_options.items()
                ], multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)),
        })

    async def async_step_edit_rooms(self, user_input=None):
        """Group all per-room editing actions in one submenu."""
        return self.async_show_menu(
            step_id="edit_rooms",
            menu_options=[
                "edit_room",
                "edit_room_demand",
                "room_air_quality",
                "room_area",
            ],
        )

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
            vol.Optional("volume_m3"): selector.NumberSelector(selector.NumberSelectorConfig(min=2, max=2000, step=0.1, unit_of_measurement="m³", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_ACH): selector.NumberSelector(selector.NumberSelectorConfig(min=0.01, max=20, step=0.01, unit_of_measurement="1/h", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_DEMAND_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig()),
            vol.Optional(CONF_TEMPERATURE_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
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
            "volume_m3": float(user_input["volume_m3"]) if user_input.get("volume_m3") not in (None, "") else None,
            CONF_ACH: float(user_input[CONF_ACH]) if user_input.get(CONF_ACH) not in (None, "") else None,
            CONF_UFH_SCREED_THICKNESS_CM: float(user_input.get(CONF_UFH_SCREED_THICKNESS_CM, 4.5) or 4.5),
            **({CONF_DEMAND_ENTITY: user_input[CONF_DEMAND_ENTITY]} if user_input.get(CONF_DEMAND_ENTITY) else {}),
            **({CONF_TEMPERATURE_ENTITY: user_input[CONF_TEMPERATURE_ENTITY]} if user_input.get(CONF_TEMPERATURE_ENTITY) else {}),
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
                    "volume_m3": float(user_input["volume_m3"]) if user_input.get("volume_m3") not in (None, "") else None,
                    CONF_ACH: float(ach) if ach not in (None, "") else None,
                    CONF_UFH_SCREED_THICKNESS_CM: float(user_input.get(CONF_UFH_SCREED_THICKNESS_CM, 4.5) or 4.5),
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
        current_volume = room.get("volume_m3")
        volume_field = (
            vol.Optional("volume_m3", default=current_volume)
            if current_volume is not None
            else vol.Optional("volume_m3")
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
        current_screed = room.get(CONF_UFH_SCREED_THICKNESS_CM, 4.5)
        screed_field = vol.Optional(CONF_UFH_SCREED_THICKNESS_CM, default=current_screed)
        screed_selector = selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=30, step=0.1, unit_of_measurement="cm", mode=selector.NumberSelectorMode.BOX))
        schema = vol.Schema({
            area_field: area_selector,
            volume_field: selector.NumberSelector(selector.NumberSelectorConfig(min=2, max=2000, step=0.1, unit_of_measurement="m³", mode=selector.NumberSelectorMode.BOX)),
            ach_field: ach_selector,
            window_field: window_selector,
            screed_field: screed_selector,
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

    async def async_step_edit_room_demand(self, user_input=None):
        """Select a room whose heating-demand entity should be changed."""
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            self._demand_room_id = user_input["room"]
            return await self.async_step_edit_room_demand_config()

        return self.async_show_form(
            step_id="edit_room_demand",
            data_schema=vol.Schema({
                vol.Required("room"): vol.In({
                    room["id"]: room[CONF_NAME] for room in rooms
                }),
            }),
        )

    async def async_step_edit_room_demand_config(self, user_input=None):
        """Set or clear the selected room's heating-demand entity."""
        rooms = list(self.config_entry.options.get(CONF_ROOMS, []))
        room = next(
            (item for item in rooms if item["id"] == self._demand_room_id),
            None,
        )
        if room is None:
            return self.async_abort(reason="no_rooms")

        if user_input is not None:
            entity_id = user_input.get(CONF_DEMAND_ENTITY)
            updated_rooms = []
            for item in rooms:
                if item["id"] == self._demand_room_id:
                    item = dict(item)
                    if entity_id:
                        item[CONF_DEMAND_ENTITY] = entity_id
                    else:
                        item.pop(CONF_DEMAND_ENTITY, None)
                updated_rooms.append(item)
            data = dict(self.config_entry.options)
            data[CONF_ROOMS] = updated_rooms
            return self.async_create_entry(title="", data=data)

        current_entity = room.get(CONF_DEMAND_ENTITY)
        entity_field = (
            vol.Optional(CONF_DEMAND_ENTITY, default=current_entity)
            if current_entity else vol.Optional(CONF_DEMAND_ENTITY)
        )
        return self.async_show_form(
            step_id="edit_room_demand_config",
            data_schema=vol.Schema({
                entity_field: selector.EntitySelector(),
            }),
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
