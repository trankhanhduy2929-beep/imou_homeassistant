"""Binary sensors for Imou Life."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .entity import (
    ImouChannelEntity,
    ImouEntity,
    ImouPropertyEntity,
    async_setup_dynamic_entities,
)
from .models import ImouChannel, ImouDevice, ThingProperty, as_bool, is_online_status


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up connectivity and boolean property sensors."""
    coordinator = entry.runtime_data.coordinator

    def build(device: ImouDevice) -> Iterable[Entity]:
        yield ImouDeviceOnlineBinarySensor(coordinator, device)
        for channel in device.channels:
            yield ImouChannelOnlineBinarySensor(coordinator, device, channel)
            yield ImouChannelRealtimeBinarySensor(
                coordinator,
                device,
                channel,
                kind="motion",
                name=f"{channel.name} chuyển động",
                device_class=BinarySensorDeviceClass.MOTION,
            )
            yield ImouChannelRealtimeBinarySensor(
                coordinator,
                device,
                channel,
                kind="human",
                name=f"{channel.name} phát hiện người",
                device_class=BinarySensorDeviceClass.OCCUPANCY,
            )
        for prop in device.thing_model.exposed_properties(coordinator.max_properties):
            if prop.data_type == "bool" and not prop.writable:
                yield ImouPropertyBinarySensor(coordinator, device, prop)

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouDeviceOnlineBinarySensor(ImouEntity, BinarySensorEntity):
    """Cloud-reported device connectivity."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, device: ImouDevice) -> None:
        """Initialize the device connectivity sensor."""
        super().__init__(coordinator, device, unique_suffix="online", name="Online")

    @property
    def available(self) -> bool:
        """Keep the connectivity entity available while a device is offline."""
        return self.coordinator.last_update_success and self.device is not None

    @property
    def is_on(self) -> bool | None:
        """Return true when the device reports online."""
        device = self.device
        return None if device is None else device.online


class ImouChannelOnlineBinarySensor(ImouChannelEntity, BinarySensorEntity):
    """Cloud-reported channel connectivity."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self, coordinator, device: ImouDevice, channel: ImouChannel
    ) -> None:
        """Initialize the channel connectivity sensor."""
        super().__init__(
            coordinator,
            device,
            channel,
            unique_suffix="online",
            name=f"{channel.name} online",
        )

    @property
    def available(self) -> bool:
        """Keep connectivity visible when this channel is offline."""
        return self.coordinator.last_update_success and self.channel is not None

    @property
    def is_on(self) -> bool | None:
        """Return the normalized channel state."""
        channel = self.channel
        return None if channel is None else is_online_status(channel.status)


class ImouChannelRealtimeBinarySensor(ImouChannelEntity, BinarySensorEntity):
    """Realtime Imou cloud alarm state for one channel."""

    def __init__(
        self,
        coordinator,
        device: ImouDevice,
        channel: ImouChannel,
        *,
        kind: str,
        name: str,
        device_class: BinarySensorDeviceClass,
    ) -> None:
        """Initialize a channel motion/person sensor."""
        super().__init__(
            coordinator,
            device,
            channel,
            unique_suffix=kind,
            name=name,
        )
        self.kind = kind
        self._attr_device_class = device_class

    @property
    def available(self) -> bool:
        """Keep alarm state available while cloud polling can backfill MQTT."""
        return (
            self.coordinator.last_update_success
            and self.channel is not None
        )

    @property
    def is_on(self) -> bool:
        """Return the bounded motion/person alarm state."""
        return self.coordinator.realtime_state(
            self.device_id, self.channel_id, self.kind
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return safe metadata from the last realtime event."""
        attributes = self.coordinator.realtime_attributes(
            self.device_id, self.channel_id
        )
        attributes["mqtt_connected"] = self.coordinator.realtime_connected
        attributes["alarm_poll"] = self.coordinator.api.latest_alarm_diagnostic()
        return attributes


class ImouPropertyBinarySensor(ImouPropertyEntity, BinarySensorEntity):
    """Read-only boolean thing-model property."""

    def __init__(
        self, coordinator, device: ImouDevice, prop: ThingProperty
    ) -> None:
        """Initialize a boolean property sensor."""
        super().__init__(coordinator, device, prop)
        self._attr_device_class = _property_device_class(prop.identifier)

    def async_refresh_definition(self, updated: ImouPropertyBinarySensor) -> None:
        super().async_refresh_definition(updated)
        self._attr_device_class = updated._attr_device_class

    @property
    def is_on(self) -> bool | None:
        """Return the normalized boolean property value."""
        return as_bool(self.property_value)


def _property_device_class(identifier: str) -> BinarySensorDeviceClass | None:
    normalized = identifier.casefold().replace("_", "")
    if any(part in normalized for part in ("motion", "pir", "human")):
        return BinarySensorDeviceClass.MOTION
    if any(part in normalized for part in ("occupancy", "occupied", "presence")):
        return BinarySensorDeviceClass.OCCUPANCY
    if any(part in normalized for part in ("door", "window", "open")):
        return BinarySensorDeviceClass.OPENING
    if "smoke" in normalized:
        return BinarySensorDeviceClass.SMOKE
    if any(part in normalized for part in ("tamper", "sabotage")):
        return BinarySensorDeviceClass.TAMPER
    if any(part in normalized for part in ("sound", "cry")):
        return BinarySensorDeviceClass.SOUND
    if any(part in normalized for part in ("fault", "error", "alarm")):
        return BinarySensorDeviceClass.PROBLEM
    return None
