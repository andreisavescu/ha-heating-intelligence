"""Heating Intelligence integration."""
from __future__ import annotations

from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.http import StaticPathConfig

from .const import CARD_FILENAME, CARD_URL, DOMAIN, PLATFORMS
from .coordinator import HeatingIntelligenceCoordinator
from .devices import async_repair_entity_device_associations


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up from YAML (not used)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Heating Intelligence from a config entry."""
    static_url = f"{CARD_URL}/{CARD_FILENAME}"
    static_file = Path(__file__).parent / "www" / CARD_FILENAME
    try:
        await hass.http.async_register_static_paths([
            StaticPathConfig(static_url, str(static_file), False)
        ])
    except RuntimeError:
        pass

    coordinator = HeatingIntelligenceCoordinator(hass, entry)
    await coordinator.async_load_history()
    await coordinator.async_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_repair_entity_device_associations(hass, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
