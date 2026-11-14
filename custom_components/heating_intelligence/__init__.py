"""Heating Intelligence integration."""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config: dict) -> bool:
    """Set up from YAML (not used)."""
    return True


async def async_setup_entry(hass, entry) -> bool:
    """Set up Heating Intelligence from a config entry.

    Home Assistant imports are intentionally local to setup. This keeps pure
    analysis modules importable by lightweight unit tests and offline tooling
    without installing the full Home Assistant runtime.
    """
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.components.lovelace import LOVELACE_DATA, MODE_STORAGE
    from homeassistant.loader import async_get_loaded_integration

    from .const import CARD_FILENAME, CARD_URL, DOMAIN, PLATFORMS
    from .coordinator import HeatingIntelligenceCoordinator
    from .devices import async_repair_entity_device_associations

    static_url = f"{CARD_URL}/{CARD_FILENAME}"
    static_file = Path(__file__).parent / "www" / CARD_FILENAME
    try:
        await hass.http.async_register_static_paths([
            StaticPathConfig(static_url, str(static_file), False)
        ])
    except RuntimeError:
        # The path is already registered, for example after an options reload.
        pass

    await _async_register_card_resource(
        hass, lovelace_data_key=LOVELACE_DATA, storage_mode=MODE_STORAGE,
        get_loaded_integration=async_get_loaded_integration,
    )

    coordinator = HeatingIntelligenceCoordinator(hass, entry)
    await coordinator.async_load_history()
    await coordinator.async_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_repair_entity_device_associations(hass, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_register_card_resource(
    hass,
    *,
    lovelace_data_key,
    storage_mode,
    get_loaded_integration,
) -> None:
    """Create or update the Lovelace resource with the installed integration version."""
    from .const import CARD_FILENAME, CARD_URL, DOMAIN

    version = get_loaded_integration(hass, DOMAIN).version
    lovelace = hass.data.get(lovelace_data_key)
    if lovelace is None:
        _LOGGER.warning("Lovelace is not available; cannot update the card resource")
        return

    # YAML-managed resources are intentionally left under the user's control.
    if lovelace.resource_mode != storage_mode:
        _LOGGER.info(
            "Lovelace resources are YAML-managed; update the Heating Intelligence "
            "resource URL to %s/%s?v=%s",
            CARD_URL,
            CARD_FILENAME,
            version,
        )
        return

    resources = lovelace.resources
    # Ensure the storage collection has loaded before inspecting or modifying it.
    await resources.async_get_info()

    resource_path = f"{CARD_URL}/{CARD_FILENAME}"
    versioned_url = f"{resource_path}?v={version}"
    existing = [
        item for item in resources.async_items()
        if urlsplit(item.get("url", "")).path == resource_path
    ]

    if existing:
        resource = existing[0]
        if resource.get("url") != versioned_url or resource.get("res_type") != "module":
            await resources.async_update_item(
                resource["id"],
                {"url": versioned_url, "res_type": "module"},
            )
            _LOGGER.info(
                "Updated Heating Intelligence Lovelace resource to version %s",
                version,
            )
        for duplicate in existing[1:]:
            await resources.async_delete_item(duplicate["id"])
            _LOGGER.warning(
                "Removed duplicate Heating Intelligence Lovelace resource %s",
                duplicate["id"],
            )
        return

    await resources.async_create_item(
        {"res_type": "module", "url": versioned_url}
    )
    _LOGGER.info(
        "Registered Heating Intelligence Lovelace resource version %s",
        version,
    )


async def _async_options_updated(hass, entry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry) -> bool:
    """Unload an entry."""
    from .const import DOMAIN, PLATFORMS

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
