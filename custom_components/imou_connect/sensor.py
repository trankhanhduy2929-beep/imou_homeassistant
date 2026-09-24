"""Sensors for Imou Life thing-model properties."""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE, UnitOfTemperature
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
    """Set up read-only numeric, enum, and text properties."""
    coordinator = entry.runtime_data.coordinator

    def build(device: ImouDevice) -> Iterable[Entity]:
        for prop in device.thing_model.exposed_properties(coordinator.max_properties):
            if not prop.writable and prop.data_type != "bool":
                yield ImouPropertySensor(coordinator, device, prop)

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouPropertySensor(ImouPropertyEntity, SensorEntity):
    """Read-only primitive thing-model property."""

    def __init__(
        self, coordinator, device: ImouDevice, prop: ThingProperty
    ) -> None:
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
