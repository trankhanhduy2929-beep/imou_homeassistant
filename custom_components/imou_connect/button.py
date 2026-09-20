"""Zero-input thing-model service buttons for Imou Life."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .entity import ImouEntity, async_setup_dynamic_entities
from .models import ImouDevice, ThingService


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up services that require no input parameters."""
    coordinator = entry.runtime_data.coordinator

    def build(device: ImouDevice) -> Iterable[Entity]:
        for service in device.thing_model.services:
            if not service.input_data:
                yield ImouServiceButton(coordinator, device, service)

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouServiceButton(ImouEntity, ButtonEntity):
    """Button invoking a zero-input Imou thing-model service."""

    def __init__(self, coordinator, device: ImouDevice, service: ThingService) -> None:
        """Initialize a thing-model service button."""
        super().__init__(
            coordinator,
            device,
            unique_suffix=f"service_{service.ref}",
            name=service.name,
        )
        self.service = service

    async def async_press(self) -> None:
        """Invoke the service."""
        await self.coordinator.async_invoke_service(self.device_id, self.service)
