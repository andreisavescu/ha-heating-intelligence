"""Constants for Heating Intelligence."""
from homeassistant.const import Platform

DOMAIN = "heating_intelligence"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONF_ROOMS = "rooms"
CONF_NAME = "name"
CONF_DEMAND_ENTITY = "demand_entity"
CONF_TEMPERATURE_ENTITY = "temperature_entity"
CONF_WINDOW_ENTITY = "window_entity"
