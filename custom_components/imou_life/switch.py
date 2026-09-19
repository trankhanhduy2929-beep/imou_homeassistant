"""Writable boolean properties for Imou Life."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .entity import ImouPropertyEntity, async_setup_dynamic_entities
from .models import ImouDevice, ThingProperty, as_bool


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up writable boolean properties."""
    coordinator = entry.runtime_data.coordinator

    def build(device: ImouDevice) -> Iterable[Entity]:
        for prop in device.thing_model.exposed_properties(coordinator.max_properties):
            if prop.writable and prop.data_type == "bool":
                yield ImouPropertySwitch(coordinator, device, prop)

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouPropertySwitch(ImouPropertyEntity, SwitchEntity):
    """Writable boolean thing-model property."""

    def __init__(
        self, coordinator, device: ImouDevice, prop: ThingProperty
    ) -> None:
        """Initialize a property switch."""
        super().__init__(coordinator, device, prop)

    @property
    def is_on(self) -> bool | None:
        """Return the normalized switch state."""
        return as_bool(self.property_value)

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the Imou property."""
        await self.async_set_property(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the Imou property."""
        await self.async_set_property(False)
