"""Writable text properties for Imou Life."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.text import TextEntity, TextMode
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
    """Set up writable non-sensitive text properties."""
    coordinator = entry.runtime_data.coordinator

    def build(device: ImouDevice) -> Iterable[Entity]:
        for prop in device.thing_model.exposed_properties(coordinator.max_properties):
            if prop.writable and prop.data_type == "text":
                yield ImouPropertyText(coordinator, device, prop)

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouPropertyText(ImouPropertyEntity, TextEntity):
    """Writable text thing-model property."""

    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 255

    def __init__(
        self, coordinator, device: ImouDevice, prop: ThingProperty
    ) -> None:
        """Initialize a text property."""
        super().__init__(coordinator, device, prop)

    @property
    def native_value(self) -> str | None:
        """Return the current text value."""
        value = self.property_value
        return None if value is None else str(value)

    async def async_set_value(self, value: str) -> None:
        """Write a text property."""
        await self.async_set_property(value[:255])
