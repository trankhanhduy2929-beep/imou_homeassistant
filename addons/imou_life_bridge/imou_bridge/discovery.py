"""Home Assistant MQTT Discovery generation for Imou devices."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha1
from typing import Any

from . import VERSION
from .models import ImouDevice, ThingProperty, as_bool

DISCOVERY_PREFIX = "homeassistant"
DEFAULT_TOPIC_PREFIX = "imou_life"
EVENT_TYPES = (
    "motion",
    "human",
    "person",
    "intrusion",
    "doorbell",
    "alarm",
    "property",
    "other",
)


@dataclass(slots=True, frozen=True)
class DiscoveryEntity:
    """One MQTT-discovered Home Assistant entity."""

    component: str
    object_id: str
    config_topic: str
    state_topic: str | None
    config: Mapping[str, Any]
    kind: str
    device_id: str
    property_id: str | None = None
    channel_id: str | None = None


@dataclass(slots=True, frozen=True)
class CommandTarget:
    """A writable property or zero-input service command."""

    kind: str
    device_id: str
    product_id: str
    ref: str
    data_type: str = ""
    minimum: float | None = None
    maximum: float | None = None
    options: tuple[str, ...] = ()
    channel_id: str | None = None
    property_id: str | None = None


@dataclass(slots=True, frozen=True)
class DiscoveryPlan:
    """Discovery entities and command routing for one device."""

    entities: tuple[DiscoveryEntity, ...]
    commands: Mapping[str, CommandTarget]


def topic_segment(value: str, *, limit: int = 64) -> str:
    """Return a stable MQTT-safe topic segment."""
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if not normalized:
        normalized = "item"
    digest = sha1(value.encode(), usedforsecurity=False).hexdigest()[:8]
    return f"{normalized[:limit]}_{digest}"


def _device_payload(device: ImouDevice) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "identifiers": [f"imou_{device.device_id}"],
        "name": device.name,
        "manufacturer": "Imou",
        "model": device.model or device.product_id,
    }
    if device.catalog:
        payload["hw_version"] = device.catalog
    return payload


def _origin() -> dict[str, str]:
    return {
        "name": "Imou Life Bridge",
        "sw_version": VERSION,
        "support_url": "https://www.imoulife.com/",
    }


def _availability(
    topic_prefix: str, device_key: str, *, device_required: bool = True
) -> dict[str, Any]:
    topics = [
        {
            "topic": f"{topic_prefix}/bridge/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        }
    ]
    if device_required:
        topics.append(
            {
                "topic": f"{topic_prefix}/{device_key}/availability",
                "payload_available": "online",
                "payload_not_available": "offline",
            }
        )
    return {
        "availability": topics,
        "availability_mode": "all",
    }


def _common_config(
    device: ImouDevice,
    *,
    unique_id: str,
    name: str,
    state_topic: str | None,
    topic_prefix: str,
    device_key: str,
    device_required: bool = True,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "name": name,
        "unique_id": unique_id,
        "device": _device_payload(device),
        "origin": _origin(),
        **_availability(topic_prefix, device_key, device_required=device_required),
    }
    if state_topic:
        config["state_topic"] = state_topic
    return config


def _config_topic(component: str, object_id: str) -> str:
    return f"{DISCOVERY_PREFIX}/{component}/imou_life/{object_id}/config"


def _property_component(prop: ThingProperty) -> str:
    if prop.data_type == "bool":
        return "switch" if prop.writable else "binary_sensor"
    if prop.data_type == "enum":
        return "select" if prop.writable and prop.enum_options else "sensor"
    if prop.data_type in {"int", "float", "double"}:
        return "number" if prop.writable else "sensor"
    if prop.data_type == "text" and prop.writable:
        return "text"
    return "sensor"


def _property_metadata(prop: ThingProperty, component: str) -> dict[str, Any]:
    identifier = prop.identifier.replace("_", "").lower()
    metadata: dict[str, Any] = {}
    if prop.unit:
        metadata["unit_of_measurement"] = prop.unit
    if component == "binary_sensor":
        if any(word in identifier for word in ("motion", "human", "pir")):
            metadata["device_class"] = "motion"
        elif "tamper" in identifier:
            metadata["device_class"] = "tamper"
        elif "problem" in identifier or "fault" in identifier:
            metadata["device_class"] = "problem"
    elif component == "sensor":
        if "battery" in identifier:
            metadata.setdefault("unit_of_measurement", "%")
            metadata["device_class"] = "battery"
        elif "temperature" in identifier or identifier.startswith("temp"):
            metadata["device_class"] = "temperature"
        elif "humidity" in identifier:
            metadata.setdefault("unit_of_measurement", "%")
            metadata["device_class"] = "humidity"
        elif "rssi" in identifier or "signal" in identifier:
            metadata.setdefault("unit_of_measurement", "dBm")
            metadata["device_class"] = "signal_strength"
    return metadata


def build_device_plan(
    device: ImouDevice,
    *,
    max_properties: int,
    topic_prefix: str = DEFAULT_TOPIC_PREFIX,
) -> DiscoveryPlan:
    """Build all safe MQTT entities for an Imou device."""
    device_key = topic_segment(device.device_id)
    entities: list[DiscoveryEntity] = []
    commands: dict[str, CommandTarget] = {}

    online_object = f"{device_key}_online"
    online_state = f"{topic_prefix}/{device_key}/online/state"
    online_config = _common_config(
        device,
        unique_id=f"imou_{device.device_id}_online",
        name="Online",
        state_topic=online_state,
        topic_prefix=topic_prefix,
        device_key=device_key,
        device_required=False,
    )
    online_config.update(
        {"device_class": "connectivity", "payload_on": "ON", "payload_off": "OFF"}
    )
    entities.append(
        DiscoveryEntity(
            "binary_sensor",
            online_object,
            _config_topic("binary_sensor", online_object),
            online_state,
            online_config,
            "device_online",
            device.device_id,
        )
    )

    event_object = f"{device_key}_event"
    event_state = f"{topic_prefix}/{device_key}/event"
    event_config = _common_config(
        device,
        unique_id=f"imou_{device.device_id}_event",
        name="Event",
        state_topic=event_state,
        topic_prefix=topic_prefix,
        device_key=device_key,
    )
    event_config["event_types"] = list(EVENT_TYPES)
    entities.append(
        DiscoveryEntity(
            "event",
            event_object,
            _config_topic("event", event_object),
            event_state,
            event_config,
            "event",
            device.device_id,
        )
    )

    for channel in device.channels:
        channel_key = topic_segment(channel.channel_id)
        base_id = f"{device_key}_channel_{channel_key}"
        channel_state = f"{topic_prefix}/{device_key}/channel/{channel_key}/online"
        channel_config = _common_config(
            device,
            unique_id=f"imou_{device.device_id}_{channel.channel_id}_online",
            name=f"{channel.name} Online",
            state_topic=channel_state,
            topic_prefix=topic_prefix,
            device_key=device_key,
        )
        channel_config.update(
            {
                "device_class": "connectivity",
                "payload_on": "ON",
                "payload_off": "OFF",
            }
        )
        entities.append(
            DiscoveryEntity(
                "binary_sensor",
                f"{base_id}_online",
                _config_topic("binary_sensor", f"{base_id}_online"),
                channel_state,
                channel_config,
                "channel_online",
                device.device_id,
                channel_id=channel.channel_id,
            )
        )

        motion_state = f"{topic_prefix}/{device_key}/channel/{channel_key}/motion"
        motion_config = _common_config(
            device,
            unique_id=f"imou_{device.device_id}_{channel.channel_id}_motion",
            name=f"{channel.name} Motion",
            state_topic=motion_state,
            topic_prefix=topic_prefix,
            device_key=device_key,
        )
        motion_config.update(
            {"device_class": "motion", "payload_on": "ON", "payload_off": "OFF"}
        )
        entities.append(
            DiscoveryEntity(
                "binary_sensor",
                f"{base_id}_motion",
                _config_topic("binary_sensor", f"{base_id}_motion"),
                motion_state,
                motion_config,
                "motion",
                device.device_id,
                channel_id=channel.channel_id,
            )
        )

        human_state = f"{topic_prefix}/{device_key}/channel/{channel_key}/human"
        human_config = _common_config(
            device,
            unique_id=f"imou_{device.device_id}_{channel.channel_id}_human",
            name=f"{channel.name} Person",
            state_topic=human_state,
            topic_prefix=topic_prefix,
            device_key=device_key,
        )
        human_config.update(
            {
                "device_class": "occupancy",
                "payload_on": "ON",
                "payload_off": "OFF",
            }
        )
        entities.append(
            DiscoveryEntity(
                "binary_sensor",
                f"{base_id}_human",
                _config_topic("binary_sensor", f"{base_id}_human"),
                human_state,
                human_config,
                "human",
                device.device_id,
                channel_id=channel.channel_id,
            )
        )

        image_topic = f"{topic_prefix}/{device_key}/channel/{channel_key}/image"
        camera_config = _common_config(
            device,
            unique_id=f"imou_{device.device_id}_{channel.channel_id}_camera",
            name=channel.name,
            state_topic=None,
            topic_prefix=topic_prefix,
            device_key=device_key,
        )
        camera_config.update({"topic": image_topic, "image_encoding": ""})
        entities.append(
            DiscoveryEntity(
                "camera",
                f"{base_id}_camera",
                _config_topic("camera", f"{base_id}_camera"),
                image_topic,
                camera_config,
                "camera",
                device.device_id,
                channel_id=channel.channel_id,
            )
        )

    for prop in device.thing_model.exposed_properties(max_properties):
        property_key = topic_segment(f"{prop.identifier}_{prop.ref}")
        component = _property_component(prop)
        object_id = f"{device_key}_{property_key}"
        state_topic = f"{topic_prefix}/{device_key}/property/{property_key}/state"
        config = _common_config(
            device,
            unique_id=f"imou_{device.device_id}_{prop.ref}",
            name=prop.name,
            state_topic=state_topic,
            topic_prefix=topic_prefix,
            device_key=device_key,
        )
        config.update(_property_metadata(prop, component))
        command_topic: str | None = None
        if prop.writable:
            command_topic = f"{topic_prefix}/{device_key}/property/{property_key}/set"
            config["command_topic"] = command_topic
            commands[command_topic] = CommandTarget(
                "property",
                device.device_id,
                device.product_id,
                prop.ref,
                prop.data_type,
                prop.minimum,
                prop.maximum,
                prop.enum_options,
                device.channels[0].channel_id if device.channels else None,
                prop.identifier,
            )
        if component in {"binary_sensor", "switch"}:
            config.update(
                {"payload_on": "ON", "payload_off": "OFF"}
                if component == "binary_sensor"
                else {
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "state_on": "ON",
                    "state_off": "OFF",
                }
            )
        elif component == "number":
            if prop.minimum is not None:
                config["min"] = prop.minimum
            if prop.maximum is not None:
                config["max"] = prop.maximum
            if prop.step is not None:
                config["step"] = prop.step
        elif component == "select":
            config["options"] = list(prop.enum_options)
        elif component == "text":
            config.update({"mode": "text", "min": 0, "max": 255})
        entities.append(
            DiscoveryEntity(
                component,
                object_id,
                _config_topic(component, object_id),
                state_topic,
                config,
                "property",
                device.device_id,
                property_id=prop.identifier,
            )
        )

    for service in device.thing_model.services:
        if service.input_data:
            continue
        service_key = topic_segment(f"{service.identifier}_{service.ref}")
        object_id = f"{device_key}_service_{service_key}"
        command_topic = f"{topic_prefix}/{device_key}/service/{service_key}/set"
        config = _common_config(
            device,
            unique_id=f"imou_{device.device_id}_service_{service.ref}",
            name=service.name,
            state_topic=None,
            topic_prefix=topic_prefix,
            device_key=device_key,
        )
        config.update({"command_topic": command_topic, "payload_press": "PRESS"})
        commands[command_topic] = CommandTarget(
            "service",
            device.device_id,
            device.product_id,
            service.ref,
            channel_id=device.channels[0].channel_id if device.channels else None,
        )
        entities.append(
            DiscoveryEntity(
                "button",
                object_id,
                _config_topic("button", object_id),
                None,
                config,
                "service",
                device.device_id,
            )
        )

    return DiscoveryPlan(tuple(entities), commands)


def encode_state(entity: DiscoveryEntity, value: Any) -> str:
    """Encode a primitive Imou value as an MQTT state payload."""
    if entity.component in {"binary_sensor", "switch"}:
        return "ON" if as_bool(value) is True else "OFF"
    if value is None:
        return "unknown"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:255]
    return str(value)[:255]


def normalize_event_type(event: str, alert: str = "") -> str:
    """Map arbitrary Imou event names to configured HA MQTT event types."""
    value = f"{event} {alert}".casefold()
    for event_type in EVENT_TYPES[:-2]:
        if event_type in value:
            return event_type
    if "property" in value or "status" in value:
        return "property"
    return "other"


def decode_command(target: CommandTarget, payload: bytes | str) -> Any:
    """Validate and convert one Home Assistant MQTT command."""
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    text = text.strip()
    if len(text.encode()) > 1024:
        raise ValueError("Command payload too large")
    if target.kind == "service":
        if text != "PRESS":
            raise ValueError("Invalid button payload")
        return {}
    if target.data_type == "bool":
        if text.upper() in {"ON", "1", "TRUE"}:
            return True
        if text.upper() in {"OFF", "0", "FALSE"}:
            return False
        raise ValueError("Invalid boolean payload")
    if target.data_type == "int":
        value: Any = int(float(text))
    elif target.data_type in {"float", "double"}:
        value = float(text)
    elif target.data_type == "enum":
        if target.options and text not in target.options:
            raise ValueError("Invalid select option")
        return text
    else:
        return text[:255]
    if target.minimum is not None and value < target.minimum:
        raise ValueError("Value is below the minimum")
    if target.maximum is not None and value > target.maximum:
        raise ValueError("Value is above the maximum")
    return value
