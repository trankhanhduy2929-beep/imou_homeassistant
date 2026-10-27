"""Writable boolean properties for Imou Life."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .entity import (
    ImouChannelEntity,
    ImouPropertyEntity,
    async_setup_dynamic_entities,
)
from .models import ImouChannel, ImouDevice, ThingProperty, as_bool


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
        for channel in device.channels:
            for ability, label, icon, _state in channel.ability_switches:
                yield ImouAbilitySwitch(
                    coordinator, device, channel, ability, label, icon
                )

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouPropertySwitch(ImouPropertyEntity, SwitchEntity):
    """Writable boolean thing-model property."""

    def __init__(self, coordinator, device: ImouDevice, prop: ThingProperty) -> None:
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


class ImouAbilitySwitch(ImouChannelEntity, SwitchEntity):
    """Cloud ability switch exposed by PaaS devices without a thing model."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator,
        device: ImouDevice,
        channel: ImouChannel,
        ability: str,
        label: str,
        icon: str,
    ) -> None:
        """Initialize an ability switch."""
        super().__init__(
            coordinator,
            device,
            channel,
            unique_suffix=f"ability_{ability}",
            name=f"{channel.name} {label}",
        )
        self.ability = ability
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        """Return the latest cloud ability state."""
        channel = self.channel
        return None if channel is None else channel.abilities.get(self.ability)

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the cloud ability."""
        await self.coordinator.async_set_ability(
            self.device_id, self.channel_id, self.ability, True
        )

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the cloud ability."""
        await self.coordinator.async_set_ability(
            self.device_id, self.channel_id, self.ability, False
        )
