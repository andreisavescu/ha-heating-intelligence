"""Home Assistant device grouping for Heating Intelligence entities."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def _house_identifier(coordinator) -> tuple[str, str]:
    return (DOMAIN, f"{coordinator.entry.entry_id}_house")


def house_device_info(coordinator) -> DeviceInfo:
    """Return the shared house-level device information."""
    return DeviceInfo(
        identifiers={_house_identifier(coordinator)},
        name="Heating Intelligence",
        manufacturer="Heating Intelligence",
        model="House overview",
    )


def room_device_info(coordinator, room_id: str, room_name: str) -> DeviceInfo:
    """Return one device per configured room, linked to the house device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_room_{room_id}")},
        name=room_name,
        manufacturer="Heating Intelligence",
        model="Heating room",
        via_device=_house_identifier(coordinator),
    )


def pump_device_info(coordinator, pump_id: str, pump_name: str) -> DeviceInfo:
    """Return one device per configured manifold pump, linked to the house."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_pump_{pump_id}")},
        name=pump_name,
        manufacturer="Heating Intelligence",
        model="Manifold pump",
        via_device=_house_identifier(coordinator),
    )


class HouseDeviceMixin:
    """Attach an entity to the central Heating Intelligence device."""

    @property
    def device_info(self) -> DeviceInfo:
        return house_device_info(self.coordinator)


class RoomDeviceMixin:
    """Attach an entity to its configured room device."""

    @property
    def device_info(self) -> DeviceInfo:
        return room_device_info(self.coordinator, self.room_id, self.room_name)


class PumpDeviceMixin:
    """Attach an entity to its configured pump device."""

    @property
    def device_info(self) -> DeviceInfo:
        return pump_device_info(self.coordinator, self.pump_id, self.pump_name)
