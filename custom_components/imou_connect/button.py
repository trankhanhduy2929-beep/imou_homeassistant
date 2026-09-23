"""Zero-input thing-model service buttons and PTZ controls for Imou Life."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .const import PTZ_DIRECTIONS, PTZ_STEP_DURATION_MS
from .entity import (
    ImouChannelEntity,
    ImouEntity,
    async_setup_dynamic_entities,
)
from .models import ImouChannel, ImouDevice, ThingService

PTZ_BUTTON_NAMES: dict[str, str] = {
    "up": "PTZ lên",
    "down": "PTZ xuống",
    "left": "PTZ trái",
    "right": "PTZ phải",
    "left_up": "PTZ trái lên",
    "left_down": "PTZ trái xuống",
    "right_up": "PTZ phải lên",
    "right_down": "PTZ phải xuống",
    "zoom_in": "PTZ zoom vào",
    "zoom_out": "PTZ zoom ra",
}

PTZ_BUTTON_ICONS: dict[str, str] = {
    "up": "mdi:arrow-up",
    "down": "mdi:arrow-down",
    "left": "mdi:arrow-left",
    "right": "mdi:arrow-right",
    "left_up": "mdi:arrow-top-left",
    "left_down": "mdi:arrow-bottom-left",
    "right_up": "mdi:arrow-top-right",
    "right_down": "mdi:arrow-bottom-right",
    "zoom_in": "mdi:magnify-plus-outline",
    "zoom_out": "mdi:magnify-minus-outline",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up services that require no input parameters."""
    coordinator = entry.runtime_data.coordinator

    def build(device: ImouDevice) -> Iterable[Entity]:
        for service in device.thing_model.services:
            if service.zero_input:
                yield ImouServiceButton(coordinator, device, service)
        if device.supports_ptz:
            for channel in device.channels:
                for direction in PTZ_DIRECTIONS:
                    yield ImouPtzButton(coordinator, device, channel, direction)

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouServiceButton(ImouEntity, ButtonEntity):
    """Button invoking a zero-input Imou thing-model service."""

    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, coordinator, device: ImouDevice, service: ThingService) -> None:
        """Initialize a thing-model service button."""
        super().__init__(
            coordinator,
            device,
            unique_suffix=f"service_{service.ref}",
            name=service.name,
        )
        self.service = service

    def async_refresh_definition(self, updated: ImouServiceButton) -> None:
        super().async_refresh_definition(updated)
        self.service = updated.service

    async def async_press(self) -> None:
        """Invoke the service."""
        if not self._definition_available:
            raise HomeAssistantError("Imou service is no longer available")
        await self.coordinator.async_invoke_service(self.device_id, self.service)


class ImouPtzButton(ImouChannelEntity, ButtonEntity):
    """Button that nudges one PTZ camera channel in a fixed direction."""

    def __init__(
        self,
        coordinator,
        device: ImouDevice,
        channel: ImouChannel,
        direction: str,
    ) -> None:
        """Initialize a PTZ direction button."""
        super().__init__(
            coordinator,
            device,
            channel,
            unique_suffix=f"ptz_{direction}",
            name=PTZ_BUTTON_NAMES[direction],
        )
        self.direction = direction
        self._attr_icon = PTZ_BUTTON_ICONS[direction]
        self._horizontal, self._vertical, self._zoom = PTZ_DIRECTIONS[direction]

    @property
    def available(self) -> bool:
        """Return availability, honoring a rejected PTZ capability."""
        return super().available and self.coordinator.ptz_available(self.device_id)

    async def async_press(self) -> None:
        """Send one short PTZ move for this direction."""
        if not self._definition_available:
            raise HomeAssistantError("Imou PTZ control is no longer available")
        await self.coordinator.async_ptz_move(
            self.device_id,
            self.channel_id,
            horizontal=self._horizontal,
            vertical=self._vertical,
            zoom=self._zoom,
            duration=PTZ_STEP_DURATION_MS,
        )
