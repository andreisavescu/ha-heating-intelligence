"""Home Assistant device grouping and registry repair for Heating Intelligence."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_PUMPS, CONF_ROOMS, DOMAIN


def _house_identifier(coordinator) -> tuple[str, str]:
    return (DOMAIN, f"{coordinator.entry.entry_id}_house")


def house_device_info(coordinator) -> dr.DeviceInfo:
    """Return the shared house-level device information."""
    return dr.DeviceInfo(
        identifiers={_house_identifier(coordinator)},
        name="Heating Intelligence",
        manufacturer="Heating Intelligence",
        model="House overview",
    )


def room_device_info(coordinator, room_id: str, room_name: str) -> dr.DeviceInfo:
    """Return one device per configured room, linked to the house device."""
    return dr.DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_room_{room_id}")},
        name=room_name,
        manufacturer="Heating Intelligence",
        model="Heating room",
        via_device=_house_identifier(coordinator),
    )


def pump_device_info(coordinator, pump_id: str, pump_name: str) -> dr.DeviceInfo:
    """Return one device per configured manifold pump, linked to the house."""
    return dr.DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_pump_{pump_id}")},
        name=pump_name,
        manufacturer="Heating Intelligence",
        model="Manifold pump",
        via_device=_house_identifier(coordinator),
    )


class HouseDeviceMixin:
    """Attach an entity to the central Heating Intelligence device."""

    @property
    def device_info(self) -> dr.DeviceInfo:
        return house_device_info(self.coordinator)


class RoomDeviceMixin:
    """Attach an entity to its configured room device."""

    @property
    def device_info(self) -> dr.DeviceInfo:
        return room_device_info(self.coordinator, self.room_id, self.room_name)


class PumpDeviceMixin:
    """Attach an entity to its configured pump device."""

    @property
    def device_info(self) -> dr.DeviceInfo:
        return pump_device_info(self.coordinator, self.pump_id, self.pump_name)


def async_repair_entity_device_associations(hass, coordinator) -> int:
    """Repair device assignments for entities already present in the entity registry.

    Older entities can remain unassigned even after their device_info is corrected.
    Re-associate them by their existing unique IDs; this does not recreate entities
    or alter their entity IDs, unique IDs, state, or recorder history.
    """
    entry = coordinator.entry
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    house = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={_house_identifier(coordinator)},
        name="Heating Intelligence",
        manufacturer="Heating Intelligence",
        model="House overview",
    )

    room_devices = {}
    for room in entry.options.get(CONF_ROOMS, []):
        room_id = room["id"]
        room_devices[room_id] = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_room_{room_id}")},
            name=room["name"],
            manufacturer="Heating Intelligence",
            model="Heating room",
            via_device=_house_identifier(coordinator),
        )

    pump_devices = {}
    for pump in entry.options.get(CONF_PUMPS, []):
        pump_id = pump["id"]
        pump_devices[pump_id] = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_pump_{pump_id}")},
            name=pump["name"],
            manufacturer="Heating Intelligence",
            model="Manifold pump",
            via_device=_house_identifier(coordinator),
        )

    repaired = 0
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.platform != DOMAIN:
            continue

        target = house
        for room_id, device in room_devices.items():
            if entity.unique_id.startswith(f"{DOMAIN}_{room_id}_"):
                target = device
                break
        else:
            for pump_id, device in pump_devices.items():
                if entity.unique_id.startswith(f"{DOMAIN}_{pump_id}_"):
                    target = device
                    break

        if entity.device_id != target.id:
            entity_registry.async_update_entity(entity.entity_id, device_id=target.id)
            repaired += 1

    return repaired
