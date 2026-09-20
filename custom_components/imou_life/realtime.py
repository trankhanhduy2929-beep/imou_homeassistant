"""Bounded realtime MQTT support recovered from the Imou Android app."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import ssl
import string
import time
from base64 import b64encode
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

from .api import (
    SUCCESS_CODES,
    ImouApiClient,
    ImouApiError,
    ImouAuthError,
    ImouMqttConfig,
    ImouMqttUnavailable,
    compact_json,
)

_LOGGER = logging.getLogger(__name__)

MQTT_CONNECT_USERNAME = "Authorization: x-pcs-signature"
MQTT_REQUEST_TOPIC = "iot_request"
MQTT_RESPONSE_TOPIC = "iot_response"
MQTT_TOPICS = ("android_iot_property", MQTT_RESPONSE_TOPIC, MQTT_REQUEST_TOPIC)
MQTT_NONCE_ALPHABET = string.ascii_letters + string.digits
MAX_MQTT_PAYLOAD_BYTES = 1024 * 1024
RECONNECT_COOLDOWN_AFTER_FAILURES = 8
_MOTION_EVENT_REFS = frozenset(
    {
        "304200",
        "304900",
        "305000",
        "312000",
        "312100",
        "312200",
        "312300",
        "312400",
        "312600",
        "32000",
        "32100",
        "329700",
        "33000",
        "331500",
        "331600",
        "332000",
        "34500",
        "34600",
        "35400",
    }
)
_HUMAN_EVENT_REFS = frozenset(
    {
        "304200",
        "304900",
        "312000",
        "32100",
        "329700",
        "33000",
        "332000",
        "34500",
    }
)
_NESTED_EVENT_KEYS = frozenset(
    {
        "alert",
        "alarm",
        "body",
        "content",
        "data",
        "event",
        "forcepop",
        "message",
        "outputdata",
        "payload",
        "properties",
        "skip",
    }
)


class ImouMqttConnectionError(Exception):
    """The realtime broker connection failed."""


@dataclass(slots=True, frozen=True)
class ImouRealtimeEvent:
    """Normalized property or alarm event received through MQTT."""

    topic: str
    product_id: str = ""
    device_id: str = ""
    channel_id: str = "-1"
    ap_id: str = ""
    timestamp: str = ""
    alert: str = ""
    event: str = ""
    title: str = ""
    name: str = ""
    alarm_type: str = ""
    object_type: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)
    output_data: Mapping[str, Any] = field(default_factory=dict)
    image_url: str | None = None

    @property
    def event_data(self) -> dict[str, Any]:
        """Return a safe Home Assistant event payload without signed media URLs."""
        return {
            "topic": self.topic,
            "product_id": self.product_id,
            "device_id": self.device_id,
            "channel_id": self.channel_id,
            "ap_id": self.ap_id,
            "time": self.timestamp,
            "alert": self.alert,
            "event": self.event,
            "title": self.title,
            "name": self.name,
            "alarm_type": self.alarm_type,
            "object_type": self.object_type,
            "properties": _redact_url_values(self.properties),
            "output_data": _redact_url_values(self.output_data),
            "has_image": self.image_url is not None,
        }


def mqtt_date_string(clock_offset: float = 0.0) -> str:
    """Return the UTC date format used by MQTTHead in the Android SDK."""
    return datetime.fromtimestamp(time.time() + clock_offset, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def build_mqtt_password(
    config: ImouMqttConfig,
    *,
    nonce: str | None = None,
    date: str | None = None,
) -> str:
    """Build the exact main-connection password JSON used by MQTTHead."""
    nonce = nonce or "".join(secrets.choice(MQTT_NONCE_ALPHABET) for _ in range(32))
    date = date or mqtt_date_string(config.clock_offset)
    canonical = (
        f"x-pcs-client-ua:{config.client_ua}\n"
        f"x-pcs-date:{date}\n"
        f"x-pcs-nonce:{nonce}\n"
        f"x-pcs-username:{config.identity}\n"
    )
    signature = b64encode(
        hmac.new(config.secret.encode(), canonical.encode(), "sha256").digest()
    ).decode("ascii")
    return compact_json(
        {
            "x-pcs-username": config.identity,
            "x-pcs-nonce": nonce,
            "x-pcs-date": date,
            "x-pcs-client-ua": config.client_ua,
            "x-pcs-signature": signature,
            "x-pcs-conn-type": "main",
        }
    )


def parse_broker_address(address: str, *, secure: bool) -> tuple[str, int]:
    """Parse the host and port returned in mqttServer."""
    value = address.strip()
    if not value:
        raise ValueError("Empty MQTT broker address")
    if "://" not in value:
        value = f"{'ssl' if secure else 'tcp'}://{value}"
    parsed = urlparse(value)
    if not parsed.hostname:
        raise ValueError("Invalid MQTT broker address")
    try:
        port = parsed.port or (8883 if secure else 1883)
    except ValueError as err:
        raise ValueError("Invalid MQTT broker port") from err
    if not 1 <= port <= 65535:
        raise ValueError("Invalid MQTT broker port")
    return parsed.hostname, port


def parse_mqtt_response(
    payload: bytes | str,
) -> tuple[int, int, Any, str] | None:
    """Decode the nested MQTTResponse envelope used by the APK SDK."""
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        text = payload
    raw = _decode_mapping(text)
    if not isinstance(raw, Mapping):
        return None
    try:
        sequence = int(raw.get("seq"))
    except (TypeError, ValueError):
        return None
    inner: Mapping[str, Any] = raw
    params = raw.get("params")
    if isinstance(params, str):
        try:
            decoded = json.loads(params)
        except json.JSONDecodeError:
            decoded = _decode_json_value(params)
        if isinstance(decoded, Mapping):
            inner = decoded
    elif isinstance(params, Mapping):
        inner = params
    try:
        code = int(
            inner.get(
                "code",
                raw.get("apiCode", raw.get("statusCode", raw.get("code", 0))),
            )
        )
    except (TypeError, ValueError):
        code = 0
    data = inner.get("data")
    if isinstance(data, str):
        decoded_data = _decode_json_value(data)
        if decoded_data != data:
            data = decoded_data
    description = str(
        inner.get("desc")
        or inner.get("msg")
        or inner.get("errorDesc")
        or raw.get("desc")
        or ""
    )
    return sequence, code, data, description


def parse_realtime_event(topic: str, payload: bytes | str) -> ImouRealtimeEvent | None:
    """Decode direct JSON and URL/query encoded Imou event envelopes."""
    if isinstance(payload, bytes):
        if len(payload) > MAX_MQTT_PAYLOAD_BYTES:
            return None
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        text = payload
        if len(text.encode("utf-8")) > MAX_MQTT_PAYLOAD_BYTES:
            return None
    raw = _decode_mapping(text)
    if raw is None:
        return None
    mappings = _event_mappings(raw)
    event_mappings = mappings[1:] + mappings[:1] if len(mappings) > 1 else mappings
    properties = _first_mapping_value(mappings, "properties") or {}
    output_data = _first_mapping_value(mappings, "outputData", "output_data") or {}
    channel = _first_value(
        mappings,
        "cid",
        "channelId",
        "channel",
        "channelNo",
        default=-1,
    )
    image_url = _find_image_url(tuple(mappings) + (output_data, properties))
    alert = _string_value(
        _first_value(event_mappings, "alert", "alarm", "alarmName", default="")
    )
    title = _string_value(_first_value(event_mappings, "title", default=""))
    name = _string_value(_first_value(event_mappings, "name", default=""))
    alarm_type = _string_value(
        _first_value(event_mappings, "type", "msgType", default="")
    )
    object_type = _string_value(
        _first_value(event_mappings, "objectType", "object_type", default="")
    )
    event = _string_value(
        _first_value(
            event_mappings,
            "event",
            "eventType",
            "alarmType",
            default=alarm_type or object_type or name,
        )
    )
    properties = _bounded_mapping(properties)
    output_data = _bounded_mapping(output_data)
    return ImouRealtimeEvent(
        topic=topic,
        product_id=_string_value(
            _first_value(
                mappings,
                "pid",
                "productId",
                "devicePid",
                "product",
                default="",
            )
        ),
        device_id=_string_value(
            _first_value(
                mappings,
                "did",
                "deviceId",
                "deviceSn",
                "deviceSN",
                "sn",
                default="",
            )
        ),
        channel_id=str(channel if channel not in (None, "") else -1),
        ap_id=_string_value(_first_value(mappings, "apid", "apId", default="")),
        timestamp=_string_value(
            _first_value(
                mappings,
                "time",
                "timestamp",
                "ts",
                "eventTime",
                default="",
            )
        ),
        alert=alert,
        event=event,
        title=title,
        name=name,
        alarm_type=alarm_type,
        object_type=object_type,
        properties=dict(properties),
        output_data=dict(output_data),
        image_url=image_url,
    )


def alarm_identity(alarm: Mapping[str, Any]) -> str:
    """Return a stable bounded identity for one cloud alarm record."""
    device_id = str(_first_alarm_value(alarm, "deviceId", "did", default=""))
    channel_id = str(
        _first_alarm_value(alarm, "channelId", "cid", default="-1")
    )
    alarm_id = str(
        _first_alarm_value(alarm, "alarmIdStr", "alarmId", default="")
    )
    timestamp = str(
        _first_alarm_value(alarm, "time", "timestamp", default="")
    )
    alarm_type = str(
        alarm.get("type")
        or alarm.get("objectType")
        or alarm.get("title")
        or alarm.get("name")
        or "alarm"
    )
    return f"{device_id}|{channel_id}|{alarm_id}|{timestamp}|{alarm_type}"[:512]


def alarm_to_realtime_event(alarm: Mapping[str, Any]) -> ImouRealtimeEvent:
    """Convert a cloud latest-alarm record to the common event shape."""
    pic_urls = alarm.get("picUrl") or alarm.get("picUrls") or ()
    image_url = alarm.get("thumbUrl") or alarm.get("imageUrl")
    if not image_url and isinstance(pic_urls, list):
        image_url = next(
            (item for item in pic_urls if isinstance(item, str) and item.strip()),
            None,
        )
    alarm_type = str(alarm.get("type") or alarm.get("typeInt") or "")
    object_type = str(alarm.get("objectType") or "")
    title = str(alarm.get("title") or "")
    name = str(alarm.get("name") or "")
    return ImouRealtimeEvent(
        topic="cloud.message.GetDeviceLatestAlarmMixMessage",
        product_id=str(
            _first_alarm_value(alarm, "productId", "pid", default="")
        ),
        device_id=str(
            _first_alarm_value(alarm, "deviceId", "did", default="")
        ),
        channel_id=str(
            _first_alarm_value(alarm, "channelId", "cid", default="-1")
        ),
        timestamp=str(
            _first_alarm_value(alarm, "time", "timestamp", default="")
        ),
        alert=title or name or alarm_type,
        event=alarm_type
        or object_type
        or str(alarm.get("refId") or "")
        or name
        or title,
        title=title,
        name=name,
        alarm_type=alarm_type,
        object_type=object_type,
        output_data=dict(alarm),
        image_url=str(image_url) if image_url else None,
    )


def _first_alarm_value(
    alarm: Mapping[str, Any], *keys: str, default: Any = None
) -> Any:
    """Return the first alarm field while preserving numeric zero."""
    for key in keys:
        value = alarm.get(key)
        if value is not None and value != "":
            return value
    return default


def is_motion_event(event: ImouRealtimeEvent) -> bool | None:
    """Recognize common Imou motion/person alarm identifiers."""
    keywords = (
        "motion",
        "movement",
        "human",
        "person",
        "people",
        "pedestrian",
        "intrusion",
        "crossline",
        "pir",
        "移动侦测",
        "移动检测",
        "移动",
        "人体",
        "人形",
        "人员",
        "入侵",
        "越界",
        "区域入侵",
        "周界",
    )
    text = _event_text(event)
    event_refs = _event_refs(event)
    matched = bool(event_refs & (_MOTION_EVENT_REFS | _HUMAN_EVENT_REFS)) or any(
        keyword in text for keyword in keywords
    )
    alarm_scope = str(event.output_data.get("_alarmScope") or "")
    if event.topic in {
        "cloud.message.GetDeviceLatestAlarmMessage",
        "cloud.message.GetDeviceLatestAlarmMixMessage",
    } and (alarm_scope == "channel" or (not alarm_scope and event.channel_id != "-1")):
        matched = True
    for key, value in event.properties.items():
        normalized_key = str(key).casefold()
        if not any(keyword in normalized_key for keyword in keywords):
            continue
        if any(part in normalized_key for part in ("enable", "sensitivity", "switch")):
            continue
        matched = True
        if _value_is_false(value):
            return False
    if not matched:
        return None
    return not _event_is_clear(_event_state_text(event))


def is_human_event(event: ImouRealtimeEvent) -> bool | None:
    """Recognize common Imou human/person alarm identifiers."""
    keywords = (
        "human",
        "person",
        "people",
        "pedestrian",
        "humanoid",
        "人体",
        "人形",
        "人员",
        "行人",
        "人脸",
    )
    text = _event_text(event)
    event_refs = _event_refs(event)
    matched = bool(event_refs & _HUMAN_EVENT_REFS) or any(
        keyword in text for keyword in keywords
    )
    for key, value in event.properties.items():
        normalized_key = str(key).casefold()
        if not any(keyword in normalized_key for keyword in keywords):
            continue
        if any(part in normalized_key for part in ("enable", "sensitivity", "switch")):
            continue
        matched = True
        if _value_is_false(value):
            return False
    if not matched:
        return None
    return not _event_is_clear(_event_state_text(event))


def _event_mappings(raw: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Collect direct and nested APK event mappings."""
    mappings: list[Mapping[str, Any]] = [raw]
    queue: list[Any] = []
    for key, value in raw.items():
        if str(key).casefold().replace("_", "") in _NESTED_EVENT_KEYS:
            queue.append(value)
    while queue and len(mappings) < 24:
        value = queue.pop(0)
        if isinstance(value, Mapping):
            mapping = dict(value)
        elif isinstance(value, str):
            mapping = _decode_mapping(value)
        else:
            mapping = None
        if not isinstance(mapping, Mapping):
            continue
        mappings.append(mapping)
        for key, child in mapping.items():
            if str(key).casefold().replace("_", "") in _NESTED_EVENT_KEYS:
                queue.append(child)
    return tuple(mappings)


def _mapping_value(mapping: Mapping[str, Any], key: str) -> Any:
    normalized = key.casefold().replace("_", "")
    for candidate, value in mapping.items():
        if str(candidate).casefold().replace("_", "") == normalized:
            return value
    return None


def _first_value(
    mappings: tuple[Mapping[str, Any], ...], *keys: str, default: Any = None
) -> Any:
    for mapping in mappings:
        for key in keys:
            value = _mapping_value(mapping, key)
            if value not in (None, ""):
                return value
    return default


def _first_mapping_value(
    mappings: tuple[Mapping[str, Any], ...], *keys: str
) -> Mapping[str, Any] | None:
    for mapping in mappings:
        for key in keys:
            value = _mapping_value(mapping, key)
            if isinstance(value, str):
                value = _decode_mapping(value)
            if isinstance(value, Mapping) and value:
                return value
    return None


def _string_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(
            str(item)
            for key in ("event", "title", "name", "type")
            if (item := _mapping_value(value, key)) not in (None, "")
        )
    return str(value or "")


def _event_text(event: ImouRealtimeEvent) -> str:
    values = [
        event.event,
        event.alert,
        event.title,
        event.name,
        event.alarm_type,
        event.object_type,
    ]
    values.append(_nested_event_text(event.properties))
    values.append(_nested_event_text(event.output_data))
    return " ".join(values).casefold()


def _event_state_text(event: ImouRealtimeEvent) -> str:
    return f"{event.event} {event.alert} {event.title} {event.name} {event.alarm_type} {event.object_type}".casefold()


def _nested_event_text(value: Any, *, depth: int = 0) -> str:
    if depth > 4:
        return str(value)[:256]
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, child in value.items():
            normalized_key = str(key).casefold()
            if any(
                part in normalized_key
                for part in ("enable", "sensitivity", "switch")
            ):
                continue
            parts.extend((str(key), _nested_event_text(child, depth=depth + 1)))
        return " ".join(parts)
    if isinstance(value, (list, tuple)):
        return " ".join(
            _nested_event_text(child, depth=depth + 1) for child in value[:32]
        )
    return str(value)[:256]


def _event_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        return ""
    try:
        return str(int(text))
    except ValueError:
        return ""

def _event_refs(event: ImouRealtimeEvent) -> set[str]:
    refs = {
        ref
        for value in (
            event.event,
            event.alert,
            event.alarm_type,
            event.object_type,
        )
        if (ref := _event_ref(value))
    }
    refs.update(_nested_event_refs(event.properties))
    refs.update(_nested_event_refs(event.output_data))
    return refs

def _nested_event_refs(value: Any, *, depth: int = 0) -> set[str]:
    if depth > 4:
        return set()
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in tuple(value.items())[:64]:
            if ref := _event_ref(key):
                refs.add(ref)
            refs.update(_nested_event_refs(child, depth=depth + 1))
    elif isinstance(value, (list, tuple)):
        for child in value[:64]:
            refs.update(_nested_event_refs(child, depth=depth + 1))
    elif ref := _event_ref(value):
        refs.add(ref)
    return refs


def _value_is_false(value: Any) -> bool:
    if value is False or value == 0:
        return True
    return isinstance(value, str) and value.strip().casefold() in {
        "0",
        "false",
        "off",
        "stop",
        "end",
    }


def _event_is_clear(text: str) -> bool:
    return any(
        word in text
        for word in (
            "stop",
            "end",
            "clear",
            "restore",
            "false",
            "off",
            "结束",
            "停止",
            "清除",
            "恢复",
            "关闭",
        )
    )


def _decode_mapping(value: str) -> dict[str, Any] | None:
    candidate = value.strip()
    for _ in range(3):
        if not candidate:
            return None
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, Mapping):
            return dict(decoded)
        if isinstance(decoded, str):
            candidate = decoded.strip()
            continue
        query_candidate = candidate.split("?", 1)[1] if "?" in candidate else candidate
        query = parse_qs(query_candidate, keep_blank_values=True)
        if query:
            result: dict[str, Any] = {}
            for key, values in query.items():
                raw_value = values[-1] if values else ""
                nested = _decode_json_value(raw_value)
                result[key] = nested
            content = result.get("content")
            if isinstance(content, Mapping):
                outer = {
                    key: child for key, child in result.items() if key != "content"
                }
                if outer:
                    outer.update(content)
                    return outer
                return dict(content)
            return result
        unquoted = unquote_plus(candidate)
        if unquoted == candidate:
            return None
        candidate = unquoted.strip()
    return None


def _decode_json_value(value: str) -> Any:
    candidate = unquote_plus(value).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return candidate


def _find_image_url(values: tuple[Mapping[str, Any], ...]) -> str | None:
    keys = ("image", "picture", "picurl", "snapshot", "thumbnail", "thumb", "cover")
    queue: list[tuple[str, Any]] = []
    for value in values:
        queue.extend((str(key), child) for key, child in value.items())
    visited = 0
    while queue and visited < 128:
        key, value = queue.pop(0)
        visited += 1
        if isinstance(value, Mapping):
            queue.extend((str(child_key), child) for child_key, child in value.items())
            continue
        if isinstance(value, list):
            queue.extend((key, child) for child in value[:16])
            continue
        if not isinstance(value, str) or not any(part in key.lower() for part in keys):
            continue
        if urlparse(value).scheme.lower() in {"http", "https"}:
            return value
    return None


def _bounded_mapping(
    value: Mapping[str, Any],
    *,
    depth: int = 0,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for index, (key, child) in enumerate(value.items()):
        if index >= 100:
            break
        result[str(key)[:128]] = _bounded_value(child, depth=depth + 1)
    return result


def _bounded_value(value: Any, *, depth: int) -> Any:
    if depth >= 4:
        return str(value)[:256]
    if isinstance(value, Mapping):
        return _bounded_mapping(value, depth=depth)
    if isinstance(value, list):
        return [_bounded_value(child, depth=depth + 1) for child in value[:16]]
    if isinstance(value, str):
        return value[:2048]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:256]


def _redact_url_values(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, child in value.items():
        normalized_key = str(key).replace("_", "").lower()
        if any(
            part in normalized_key
            for part in ("password", "token", "secret", "credential", "accesskey")
        ):
            result[str(key)] = "<redacted>"
            continue
        if isinstance(child, Mapping):
            result[str(key)] = _redact_url_values(child)
        elif isinstance(child, list):
            result[str(key)] = [
                _redact_url_values(item)
                if isinstance(item, Mapping)
                else "<redacted_url>"
                if isinstance(item, str)
                and urlparse(item).scheme.lower() in {"http", "https"}
                else item
                for item in child[:16]
            ]
        elif isinstance(child, str) and urlparse(child).scheme.lower() in {
            "http",
            "https",
        }:
            result[str(key)] = "<redacted_url>"
        else:
            result[str(key)] = child
    return result


def _tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    certificate_dir = Path(__file__).with_name("certificates")
    if certificate_dir.is_dir():
        for certificate in sorted(certificate_dir.glob("*.crt")):
            try:
                context.load_verify_locations(cafile=str(certificate))
            except ssl.SSLError as err:
                _LOGGER.warning("Could not load MQTT CA %s: %s", certificate.name, err)
    return context


class ImouCloudMqttClient:
    """Maintain the Imou cloud MQTT connection and emit normalized events."""

    def __init__(
        self,
        api: ImouApiClient,
        on_event: Callable[[ImouRealtimeEvent], Awaitable[None]],
        on_connection_state: Callable[[bool], Awaitable[None]] | None = None,
    ) -> None:
        self.api = api
        self.on_event = on_event
        self.on_connection_state = on_connection_state
        self.connected = False
        self.last_error: str | None = None
        self.messages_received = 0
        self._client: Any | None = None
        self._publish_lock = asyncio.Lock()
        self._pending_requests: dict[
            int, asyncio.Future[tuple[int, int, Any, str]]
        ] = {}
        self._next_request_seq = 1

    async def async_request(
        self,
        api_name: str,
        params: Mapping[str, Any],
        timeout_ms: int = 10000,
    ) -> dict[str, Any]:
        """Send one API request through the APK MQTT request topic."""
        if not self.connected or self._client is None:
            raise ImouMqttUnavailable("Imou MQTT request transport is offline")
        sequence = self._next_request_seq
        self._next_request_seq = 1 if sequence >= 2_000_000_000 else sequence + 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[int, int, Any, str]] = loop.create_future()
        self._pending_requests[sequence] = future
        request_params = dict(params)
        try:
            qos = int(request_params.get("qos", 1))
        except (TypeError, ValueError):
            qos = 1
        for key in ("timeout", "host", "qos", "mqttHost", "keepAlive"):
            request_params.pop(key, None)
        request = {
            "api": api_name,
            "params": request_params,
            "seq": sequence,
        }
        try:
            async with self._publish_lock:
                client = self._client
                if client is None or not self.connected:
                    raise ImouMqttUnavailable(
                        "Imou MQTT request transport disconnected"
                    )
                await client.publish(
                    MQTT_REQUEST_TOPIC,
                    compact_json(request),
                    qos=max(0, min(qos, 2)),
                    timeout=max(5, min(int(timeout_ms), 60000)) / 1000,
                )
            try:
                response_sequence, code, data, description = await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=max(5, min(int(timeout_ms), 60000)) / 1000,
                )
            except TimeoutError as err:
                raise ImouMqttUnavailable(
                    f"Imou MQTT response timed out api={api_name}"
                ) from err
        except ImouApiError:
            raise
        except Exception as err:
            raise ImouMqttUnavailable(
                f"Imou MQTT request failed api={api_name}: {err}"
            ) from err
        finally:
            self._pending_requests.pop(sequence, None)
        if response_sequence != sequence:
            raise ImouMqttUnavailable("Imou MQTT response sequence mismatch")
        if code in {401, 403}:
            raise ImouAuthError(description or "Imou MQTT authentication failed", code)
        if code not in SUCCESS_CODES:
            raise ImouApiError(
                description or f"Imou MQTT API error {code}",
                code,
            )
        if isinstance(data, Mapping):
            return dict(data)
        if data is None:
            return {}
        return {"data": data}

    async def run(self, stop: asyncio.Event) -> None:
        """Reconnect until stopped or the task is cancelled."""
        failures = 0
        while not stop.is_set():
            config = self.api.mqtt_config()
            if config is None or not config.ssl_address:
                await self._set_connected(False)
                self.last_error = "Login did not provide a secure MQTT broker"
                if await self._wait_or_stop(stop, 60):
                    return
                continue
            started = time.monotonic()
            try:
                await self._connect_once(config, stop)
                failures = 0
            except asyncio.CancelledError:
                raise
            except (
                ImouMqttConnectionError,
                OSError,
                TimeoutError,
                ValueError,
                ssl.SSLError,
            ) as error:
                await self._set_connected(False)
                self.last_error = f"{type(error).__name__}: {error}"
                connected_for = time.monotonic() - started
                failures = 1 if connected_for >= 60 else failures + 1
                _LOGGER.warning("Imou MQTT disconnected: %s", error)
            delay = min(300, 5 * (2 ** min(max(failures - 1, 0), 6)))
            if failures >= RECONNECT_COOLDOWN_AFTER_FAILURES:
                delay = 900
            delay += secrets.randbelow(max(1, int(delay * 0.2) + 1))
            if await self._wait_or_stop(stop, delay):
                return

    async def _connect_once(self, config: ImouMqttConfig, stop: asyncio.Event) -> None:
        import aiomqtt

        host, port = parse_broker_address(config.ssl_address, secure=True)
        try:
            async with aiomqtt.Client(
                host,
                port,
                username=MQTT_CONNECT_USERNAME,
                password=build_mqtt_password(config),
                identifier=config.client_id,
                protocol=aiomqtt.ProtocolVersion.V311,
                clean_session=True,
                timeout=15,
                keepalive=60,
                max_queued_incoming_messages=100,
                max_queued_outgoing_messages=10,
                max_inflight_messages=10,
                max_concurrent_outgoing_calls=10,
                tls_context=_tls_context(),
            ) as client:
                self._client = client
                for topic in MQTT_TOPICS:
                    await client.subscribe(topic, qos=0, timeout=15)
                await self._set_connected(True)
                self.last_error = None
                _LOGGER.info(
                    "Imou realtime MQTT connected host=%s port=%s", host, port
                )
                await self._async_register_push(config)
                async for message in client.messages:
                    if stop.is_set():
                        return
                    payload = bytes(message.payload)
                    if len(payload) > MAX_MQTT_PAYLOAD_BYTES:
                        _LOGGER.warning("Discarded oversized Imou MQTT message")
                        continue
                    topic = str(message.topic)
                    if topic == MQTT_RESPONSE_TOPIC:
                        self._resolve_mqtt_response(payload)
                        continue
                    event = parse_realtime_event(topic, payload)
                    if event is None:
                        continue
                    self.messages_received += 1
                    await self.on_event(event)
        except aiomqtt.MqttError as error:
            raise ImouMqttConnectionError(str(error)) from error
        finally:
            self._client = None
            self._fail_pending_requests("Imou MQTT connection closed")
            await self._set_connected(False)

    async def _async_register_push(self, config: ImouMqttConfig) -> None:
        """Ask Imou to deliver alarm pushes to this MQTT client."""
        register = getattr(self.api, "async_set_client_push_config", None)
        if register is None:
            return
        try:
            await register(config.client_id, client_push_id=config.client_id)
        except (ImouApiError, ValueError) as error:
            self.last_error = f"push registration failed: {error}"
            _LOGGER.warning("Imou MQTT push registration failed: %s", error)

    def _resolve_mqtt_response(self, payload: bytes) -> None:
        """Complete the matching request future from an MQTT response."""
        parsed = parse_mqtt_response(payload)
        if parsed is None:
            _LOGGER.debug("Discarded malformed Imou MQTT response")
            return
        sequence, code, data, description = parsed
        future = self._pending_requests.get(sequence)
        if future is None or future.done():
            return
        future.set_result((sequence, code, data, description))

    def _fail_pending_requests(self, message: str) -> None:
        """Wake all callers when the broker connection disappears."""
        pending = tuple(self._pending_requests.values())
        self._pending_requests.clear()
        for future in pending:
            if not future.done():
                future.set_exception(ImouMqttUnavailable(message))

    async def _set_connected(self, connected: bool) -> None:
        if self.connected == connected:
            return
        self.connected = connected
        if self.on_connection_state is None:
            return
        await self.on_connection_state(connected)

    @staticmethod
    async def _wait_or_stop(stop: asyncio.Event, delay: float) -> bool:
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True
