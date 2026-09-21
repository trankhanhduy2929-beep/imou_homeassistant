"""Writable numeric properties for Imou Life."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .entity import ImouPropertyEntity, async_setup_dynamic_entities
from .models import ImouDevice, ThingProperty


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up writable integer and floating-point properties."""
    coordinator = entry.runtime_data.coordinator

    def build(device: ImouDevice) -> Iterable[Entity]:
        for prop in device.thing_model.exposed_properties(coordinator.max_properties):
            if prop.writable and prop.data_type in {"int", "float", "double"}:
                yield ImouPropertyNumber(coordinator, device, prop)

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouPropertyNumber(ImouPropertyEntity, NumberEntity):
    """Writable numeric thing-model property."""

    _attr_mode = NumberMode.AUTO

    def __init__(
        self, coordinator, device: ImouDevice, prop: ThingProperty
    ) -> None:
        """Initialize a numeric property."""
        super().__init__(coordinator, device, prop)
        self._attr_native_min_value = prop.minimum if prop.minimum is not None else 0
        self._attr_native_max_value = prop.maximum if prop.maximum is not None else 100
        self._attr_native_step = prop.step if prop.step is not None else 1
        self._attr_native_unit_of_measurement = prop.unit

    def async_refresh_definition(self, updated: ImouPropertyNumber) -> None:
        super().async_refresh_definition(updated)
        self._attr_native_min_value = updated._attr_native_min_value
        self._attr_native_max_value = updated._attr_native_max_value
        self._attr_native_step = updated._attr_native_step
        self._attr_native_unit_of_measurement = updated._attr_native_unit_of_measurement

    @property
    def native_value(self) -> float | None:
        """Return the current numeric value."""
        try:
            return float(self.property_value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Write a numeric property."""
        output: int | float = int(value) if self.property.data_type == "int" else value
        await self.async_set_property(output)
