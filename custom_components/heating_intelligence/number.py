"""Manual flowmeter inputs for underfloor-heating circuits."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.restore_state import RestoreNumber
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .devices import HouseDeviceMixin

UFH_CIRCUIT_COUNT = 16


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Create one adjustable flow input for each UFH circuit."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        UfhCircuitFlowNumber(coordinator, circuit_number)
        for circuit_number in range(1, UFH_CIRCUIT_COUNT + 1)
    )


class UfhCircuitFlowNumber(
    HouseDeviceMixin, CoordinatorEntity, RestoreNumber, NumberEntity
):
    """User-entered flowmeter reading for one UFH circuit."""

    _attr_native_min_value = 0.0
    _attr_native_max_value = 6.0
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "L/min"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:waves-arrow-right"
    _attr_should_poll = False

    def __init__(self, coordinator, circuit_number: int) -> None:
        super().__init__(coordinator)
        self.circuit_number = circuit_number
        self.circuit_id = f"circuit_{circuit_number:02d}"
        self._attr_name = f"UFH Circuit {circuit_number:02d} Flow"
        self._attr_unique_id = f"{DOMAIN}_ufh_circuit_{circuit_number:02d}_flow"
        self._attr_native_value = 0.0

    async def async_added_to_hass(self) -> None:
        """Restore the last manually entered flowmeter reading."""
        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if last_number_data is not None:
            self._attr_native_value = min(
                self._attr_native_max_value,
                max(self._attr_native_min_value, float(last_number_data.native_value)),
            )
        self.coordinator.set_ufh_flow_rate(self.circuit_id, self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Store a flowmeter reading without controlling any physical device."""
        self._attr_native_value = round(
            min(self._attr_native_max_value, max(self._attr_native_min_value, value)),
            2,
        )
        self.coordinator.set_ufh_flow_rate(self.circuit_id, self._attr_native_value)
        self.async_write_ha_state()
        self.coordinator.async_set_updated_data(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the circuit index until the room mapping is configured."""
        return {
            "circuit_number": self.circuit_number,
            "circuit_id": self.circuit_id,
            "measurement_type": "manual_flowmeter_setting",
            "note": "User-entered flowmeter reading; not an automatically measured sensor.",
        }
