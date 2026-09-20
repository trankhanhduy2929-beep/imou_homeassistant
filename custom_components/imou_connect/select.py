"""Writable enum properties for Imou Life."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.select import SelectEntity
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
    """Set up writable enum properties."""
    coordinator = entry.runtime_data.coordinator

    def build(device: ImouDevice) -> Iterable[Entity]:
        for prop in device.thing_model.exposed_properties(coordinator.max_properties):
            if prop.writable and prop.data_type == "enum" and prop.enum_options:
                yield ImouPropertySelect(coordinator, device, prop)

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouPropertySelect(ImouPropertyEntity, SelectEntity):
    """Writable enum thing-model property."""

    def __init__(
        self, coordinator, device: ImouDevice, prop: ThingProperty
    ) -> None:
        """Initialize an enum property."""
        super().__init__(coordinator, device, prop)
        self._raw_to_label: dict[str, str] = {}
        self._label_to_raw: dict[str, str] = {}
        for position, raw_option in enumerate(prop.enum_options, start=1):
            label = prop.enum_labels.get(raw_option, raw_option)
            if label in self._label_to_raw:
                label = f"{label} ({position})"
            self._raw_to_label[raw_option] = label
            self._label_to_raw[label] = raw_option
        self._attr_options = list(self._label_to_raw)

    @property
    def current_option(self) -> str | None:
        """Return the raw enum option expected by Imou."""
        value = self.property_value
        if value is None:
            return None
        raw_option = str(value)
        return self._raw_to_label.get(raw_option, raw_option)

    async def async_select_option(self, option: str) -> None:
        """Write an enum property."""
        await self.async_set_property(self._label_to_raw.get(option, option))
