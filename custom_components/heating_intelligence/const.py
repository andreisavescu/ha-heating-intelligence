"""Constants for Heating Intelligence."""
from homeassistant.const import Platform

DOMAIN = "heating_intelligence"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONF_ROOMS = "rooms"
CONF_NAME = "name"
CONF_FLOOR = "floor"
CONF_DEMAND_ENTITY = "demand_entity"
CONF_TEMPERATURE_ENTITY = "temperature_entity"
CONF_WINDOW_ENTITY = "window_entity"
CONF_WATER_TEMPERATURE_ENTITY = "water_temperature_entity"
CONF_PUMPS = "pumps"
CONF_PUMP_POWER_ENTITY = "power_entity"
CONF_PUMP_ENERGY_ENTITY = "energy_entity"
CARD_URL = "/heating_intelligence"
CARD_FILENAME = "heating-intelligence-card.js"
