"""Sensors for Imou Life thing-model properties."""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .entity import ImouEntity, ImouPropertyEntity, async_setup_dynamic_entities
from .models import ImouDevice, ThingProperty

DETAIL_SENSORS = {
    "wifi_signal": ("Tín hiệu Wi-Fi", "mdi:wifi", PERCENTAGE),
    "wifi_ssid": ("Tên Wi-Fi", "mdi:wifi", None),
    "wifi_band": ("Băng tần Wi-Fi", "mdi:wifi-arrow-up-down", None),
    "network_access_type": ("Kiểu kết nối mạng", "mdi:lan-connect", None),
    "ip_address": ("Địa chỉ IP", "mdi:ip-network", None),
    "mac_address": ("Địa chỉ MAC", "mdi:identifier", None),
    "device_model_name": ("Model thiết bị", "mdi:cctv", None),
    "sdcard_status": ("Thẻ nhớ", "mdi:sd", None),
    "storage_strategy_status": ("Lưu trữ đám mây", "mdi:cloud-alert", None),
    "cloud_storage_end": ("Hạn lưu trữ đám mây", "mdi:calendar-clock", None),
    "firmware_version": ("Phiên bản firmware", "mdi:chip", None),
    "last_offline": ("Lần offline cuối", "mdi:clock-outline", None),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up read-only numeric, enum, and text properties."""
    coordinator = entry.runtime_data.coordinator

    def build(device: ImouDevice) -> Iterable[Entity]:
        for prop in device.thing_model.exposed_properties(coordinator.max_properties):
            if not prop.writable and prop.data_type != "bool":
                yield ImouPropertySensor(coordinator, device, prop)
        if not device.product_id:
            for key, definition in DETAIL_SENSORS.items():
                yield ImouDetailSensor(coordinator, device, key, definition)

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouPropertySensor(ImouPropertyEntity, SensorEntity):
    """Read-only primitive thing-model property."""

    def __init__(self, coordinator, device: ImouDevice, prop: ThingProperty) -> None:
        """Initialize an Imou property sensor."""
        super().__init__(coordinator, device, prop)
        self._attr_native_unit_of_measurement = prop.unit
        self._attr_device_class = None
        normalized = prop.identifier.casefold().replace("_", "")
        if "battery" in normalized and prop.unit in {None, "%", PERCENTAGE}:
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif "temperature" in normalized and prop.unit in {
            "C",
            "°C",
            UnitOfTemperature.CELSIUS,
        }:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE

    def async_refresh_definition(self, updated: ImouPropertySensor) -> None:
        super().async_refresh_definition(updated)
        self._attr_native_unit_of_measurement = updated._attr_native_unit_of_measurement
        self._attr_device_class = updated._attr_device_class

    @property
    def native_value(self) -> Any:
        """Return a typed primitive value."""
        value = self.property_value
        if value is None:
            return None
        if self.property.data_type in {"int", "float", "double"}:
            try:
                numeric = float(value)
                if not isfinite(numeric):
                    return None
                return int(numeric) if self.property.data_type == "int" else numeric
            except (TypeError, ValueError, OverflowError):
                return None
        if self.property.data_type == "enum":
            raw = str(value)
            return self.property.enum_labels.get(raw, raw)
        return str(value)


class ImouDetailSensor(ImouEntity, SensorEntity):
    """Diagnostic sensor sourced from the cloud device detail record."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        device: ImouDevice,
        key: str,
        definition: tuple[str, str, str | None],
    ) -> None:
        """Initialize a cloud detail sensor."""
        label, icon, unit = definition
        super().__init__(
            coordinator,
            device,
            unique_suffix=f"detail_{key}",
            name=label,
        )
        self.detail_key = key
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        if key == "wifi_signal":
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key in {"last_offline", "cloud_storage_end"}:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> Any:
        """Return the latest cloud detail value."""
        device = self.device
        if device is None:
            return None
        return getattr(device, self.detail_key, None)
