"""Shared entity helpers for Imou Life."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_PRODUCT_ID, ATTR_PROPERTY_REF, DOMAIN
from .coordinator import ImouDataUpdateCoordinator
from .models import ImouChannel, ImouDevice, ThingProperty


class ImouEntity(CoordinatorEntity[ImouDataUpdateCoordinator]):
    """Base class for entities tied to one Imou device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        device: ImouDevice,
        *,
        unique_suffix: str,
        name: str,
    ) -> None:
        """Initialize a stable device entity."""
        super().__init__(coordinator)
        self.device_id = device.device_id
        self._device_snapshot = device
        self._attr_unique_id = f"{device.device_id}_{unique_suffix}"
        self._attr_name = name

    @property
    def device(self) -> ImouDevice | None:
        """Return the latest device data."""
        return self.coordinator.device(self.device_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Group all channel and thing-model entities under the cloud device."""
        device = self.device or self._device_snapshot
        return DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer="Imou",
            name=device.name,
            model=device.model or device.product_id,
            serial_number=device.device_id,
        )

    @property
    def available(self) -> bool:
        """Return entity availability from coordinator and device state."""
        device = self.device
        return (
            self.coordinator.last_update_success
            and device is not None
            and device.online is not False
        )


class ImouChannelEntity(ImouEntity):
    """Base class for one camera/recorder channel."""

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        device: ImouDevice,
        channel: ImouChannel,
        *,
        unique_suffix: str,
        name: str,
    ) -> None:
        """Initialize a channel entity."""
        super().__init__(
            coordinator,
            device,
            unique_suffix=f"{channel.channel_id}_{unique_suffix}",
            name=name,
        )
        self.channel_id = channel.channel_id
        self._channel_snapshot = channel

    @property
    def channel(self) -> ImouChannel | None:
        """Return the latest channel data."""
        device = self.device
        if device is None:
            return None
        return next(
            (
                channel
                for channel in device.channels
                if channel.channel_id == self.channel_id
            ),
            None,
        )


class ImouPropertyEntity(ImouEntity):
    """Base class for one primitive thing-model property."""

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        device: ImouDevice,
        prop: ThingProperty,
    ) -> None:
        """Initialize a thing-model property entity."""
        super().__init__(
            coordinator,
            device,
            unique_suffix=f"property_{prop.ref}",
            name=prop.name,
        )
        self.property = prop

    @property
    def property_value(self) -> Any:
        """Return the latest normalized property value."""
        device = self.device
        return None if device is None else device.properties.get(self.property.identifier)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose non-sensitive thing-model metadata."""
        device = self.device or self._device_snapshot
        return {
            ATTR_PRODUCT_ID: device.product_id,
            ATTR_PROPERTY_REF: self.property.ref,
        }

    async def async_set_property(self, value: Any) -> None:
        """Write the property through the coordinator."""
        await self.coordinator.async_set_property(
            self.device_id, self.property, value
        )


def async_setup_dynamic_entities(
    coordinator: ImouDataUpdateCoordinator,
    entry: Any,
    async_add_entities: AddEntitiesCallback,
    build_entities: Callable[[ImouDevice], Iterable[Entity]],
) -> None:
    """Add newly discovered devices/properties without duplicating entities."""
    known_unique_ids: set[str] = set()
    known_signatures: dict[str, tuple[Any, ...]] = {}

    @callback
    def _async_add_new_entities() -> None:
        entities: list[Entity] = []
        for device in coordinator.data.values():
            signature = (
                device.product_id,
                tuple(channel.channel_id for channel in device.channels),
                tuple(
                    (prop.ref, prop.access_mode, prop.data_type)
                    for prop in device.thing_model.properties
                ),
                tuple(service.ref for service in device.thing_model.services),
            )
            if known_signatures.get(device.device_id) == signature:
                continue
            known_signatures[device.device_id] = signature
            for entity in build_entities(device):
                unique_id = entity.unique_id
                if unique_id is None or unique_id in known_unique_ids:
                    continue
                known_unique_ids.add(unique_id)
                entities.append(entity)
        if entities:
            async_add_entities(entities)

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
