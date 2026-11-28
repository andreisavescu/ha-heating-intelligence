"""Home Assistant device grouping and registry repair for Heating Intelligence."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_PUMPS, CONF_ROOMS, DOMAIN


def _enable_config_entry_disabled_device(device_registry, device) -> bool:
    """Re-enable an integration-owned device disabled by its config entry.

    The config entry is actively setting up at this point, so a stale
    CONFIG_ENTRY disable marker must not keep the device and all its entities
    unavailable. User-disabled devices are deliberately left untouched.
    """
    if device.disabled_by != dr.DeviceEntryDisabler.CONFIG_ENTRY:
        return False
    device_registry.async_update_device(
        device.id,
        disabled_by=None,
    )
    return True


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


def _audit_identifier(coordinator) -> tuple[str, str]:
    return (DOMAIN, f"{coordinator.entry.entry_id}_audits")


def audit_device_info(coordinator) -> dr.DeviceInfo:
    """Return the dedicated diagnostics/audits device."""
    return dr.DeviceInfo(
        identifiers={_audit_identifier(coordinator)},
        name="Heating Intelligence — Audits",
        manufacturer="Heating Intelligence",
        model="Diagnostics and audits"
    )


def room_device_info(coordinator, room_id: str, room_name: str) -> dr.DeviceInfo:
    """Return one device per configured room, linked to the house device."""
    return dr.DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_room_{room_id}")},
        name=room_name,
        manufacturer="Heating Intelligence",
        model="Heating room",
        
    )


def pump_device_info(coordinator, pump_id: str, pump_name: str) -> dr.DeviceInfo:
    """Return one device per configured manifold pump, linked to the house."""
    return dr.DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_pump_{pump_id}")},
        name=pump_name,
        manufacturer="Heating Intelligence",
        model="Manifold pump",
        
    )


class HouseDeviceMixin:
    """Attach an entity to the central Heating Intelligence device."""

    @property
    def device_info(self) -> dr.DeviceInfo:
        return house_device_info(self.coordinator)


class AuditDeviceMixin:
    """Attach diagnostic and audit entities to their own child device."""

    @property
    def device_info(self) -> dr.DeviceInfo:
        return audit_device_info(self.coordinator)


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
            via_device_id=house.id,
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
            via_device_id=house.id,
        )

    # Remove obsolete electrical-stage diagnostics from the earlier manifold
    # experiment. Keep pump power, energy, cycle and runtime entities untouched.
    retired_stage_metrics = {
        "stage",
        "idle_baseline_w",
        "pump_started_at",
        "valve_level_w",
        "valve_opened_at",
        "valve_to_pump_delay",
    }
    retired_unique_ids = {
        f"{DOMAIN}_{pump_id}_{metric}"
        for pump_id in ("ground_floor", "upstairs")
        for metric in retired_stage_metrics
    }
    retired_entity_ids = {
        "sensor.heating_intelligence_downstairs_pump_electricity_today",
        "sensor.heating_intelligence_upstairs_pump_electricity_today",
    }
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.platform == DOMAIN and (
            entity.unique_id in retired_unique_ids or entity.entity_id in retired_entity_ids
        ):
            entity_registry.async_remove(entity.entity_id)

    audit_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={_audit_identifier(coordinator)},
        name="Heating Intelligence — Audits",
        manufacturer="Heating Intelligence",
        model="Diagnostics and audits",
        via_device_id=house.id,
    )
    # Clear stale config-entry disable markers on all devices owned by this
    # active entry. Home Assistant otherwise disables every entity attached
    # to those devices, and the entity-level enable switch cannot override it.
    for device in (house, *room_devices.values(), *pump_devices.values(), audit_device):
        _enable_config_entry_disabled_device(device_registry, device)

    audit_unique_ids = {
        f"{DOMAIN}_ufh_hydraulic_audit",
        f"{DOMAIN}_house_audit",
        f"{DOMAIN}_thermal_readiness_audit",
        f"{DOMAIN}_measurement_audit",
        f"{DOMAIN}_physical_model_experiments",
        f"{DOMAIN}_physical_model_experiments_multizone",
        f"{DOMAIN}_physical_model_experiments_multizone_part_2",
        f"{DOMAIN}_physical_model_experiments_diagnostics",
        f"{DOMAIN}_physical_model_experiments_dataset",
        f"{DOMAIN}_physical_model_experiments_exclusions",
        f"{DOMAIN}_physical_model_simulation_audit",
        f"{DOMAIN}_envelope_conductance",
        f"{DOMAIN}_stage3_thermal_capacity",
        f"{DOMAIN}_stage5_ufh_slab_model",
        f"{DOMAIN}_stage6_gain_model",
    }
    audit_unique_id_prefixes = (
        f"{DOMAIN}_physical_model_experiments_payload_part_",
        f"{DOMAIN}_physical_model_experiments_diagnostics_payload_part_",
        f"{DOMAIN}_stage3_thermal_capacity_payload_part_",
        f"{DOMAIN}_stage5_ufh_slab_model_payload_part_",
    )
    # Room-specific diagnostics are assigned to their room device below,
    # alongside the room's temperature, demand and heating-power entities.

    repaired = 0
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.platform != DOMAIN:
            continue

        unique_id = entity.unique_id
        is_audit = (
            unique_id in audit_unique_ids
            or unique_id.startswith(audit_unique_id_prefixes)
        )
        target = audit_device if is_audit else house
        if not is_audit:
            for room_id, device in room_devices.items():
                if unique_id.startswith(f"{DOMAIN}_{room_id}_"):
                    target = device
                    break
            else:
                for pump_id, device in pump_devices.items():
                    if unique_id.startswith(f"{DOMAIN}_{pump_id}_"):
                        target = device
                        break

        if entity.device_id != target.id:
            entity_registry.async_update_entity(entity.entity_id, device_id=target.id)
            repaired += 1

    return repaired
