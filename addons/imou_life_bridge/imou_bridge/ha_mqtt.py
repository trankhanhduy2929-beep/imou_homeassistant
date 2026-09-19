"""MQTT client used to expose the bridge to Home Assistant."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

_LOGGER = logging.getLogger(__name__)

BRIDGE_AVAILABILITY = "imou_life/bridge/availability"
COMMAND_SUBSCRIPTION = "imou_life/#"


class HomeAssistantMqttError(Exception):
    """The Home Assistant MQTT connection failed."""


def _as_bool(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on"}


class HomeAssistantMqtt:
    """Reconnectable MQTT publisher/subscriber for the HA broker."""

    def __init__(
        self,
        *,
        terminal_id: str,
        on_connected: Callable[[], Awaitable[None]],
        on_command: Callable[[str, bytes], Awaitable[None]],
    ) -> None:
        self.host = os.environ.get("MQTT_HOST", "core-mosquitto")
        try:
            self.port = int(os.environ.get("MQTT_PORT", "1883"))
        except ValueError:
            self.port = 1883
        self.username = os.environ.get("MQTT_USERNAME", "")
        self.password = os.environ.get("MQTT_PASSWORD", "")
        self.tls = _as_bool(os.environ.get("MQTT_SSL", "false"))
        self.client_id = f"imou_life_bridge_{terminal_id[:20]}"
        self.on_connected = on_connected
        self.on_command = on_command
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()
        self._connected = asyncio.Event()

    @property
    def connected(self) -> bool:
        """Return whether the HA MQTT client is currently connected."""
        return self._connected.is_set()

    async def run(self, stop: asyncio.Event) -> None:
        """Maintain the HA broker connection until shutdown."""
        failures = 0
        while not stop.is_set():
            try:
                await self._connect_once(stop)
                failures = 0
            except asyncio.CancelledError:
                raise
            except (
                HomeAssistantMqttError,
                OSError,
                TimeoutError,
                ssl.SSLError,
            ) as error:
                self._connected.clear()
                self._client = None
                failures += 1
                _LOGGER.warning("Home Assistant MQTT disconnected: %s", error)
            delay = min(120, 2 ** min(failures, 6))
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _connect_once(self, stop: asyncio.Event) -> None:
        import aiomqtt

        tls_context = ssl.create_default_context() if self.tls else None
        kwargs: dict[str, Any] = {
            "identifier": self.client_id,
            "protocol": aiomqtt.ProtocolVersion.V311,
            "clean_session": True,
            "timeout": 15,
            "keepalive": 60,
            "max_queued_incoming_messages": 100,
            "max_queued_outgoing_messages": 100,
            "max_inflight_messages": 20,
            "max_concurrent_outgoing_calls": 20,
            "will": aiomqtt.Will(
                BRIDGE_AVAILABILITY,
                payload=b"offline",
                qos=1,
                retain=True,
            ),
        }
        if self.username:
            kwargs["username"] = self.username
            kwargs["password"] = self.password
        if tls_context is not None:
            kwargs["tls_context"] = tls_context
        try:
            async with aiomqtt.Client(self.host, self.port, **kwargs) as client:
                self._client = client
                await client.subscribe(COMMAND_SUBSCRIPTION, qos=0, timeout=15)
                self._connected.set()
                await self.publish(BRIDGE_AVAILABILITY, "online", retain=True)
                await self.on_connected()
                async for message in client.messages:
                    if stop.is_set():
                        return
                    topic = str(message.topic)
                    if not topic.endswith("/set"):
                        continue
                    await self.on_command(topic, bytes(message.payload))
        except aiomqtt.MqttError as error:
            raise HomeAssistantMqttError(str(error)) from error
        finally:
            self._connected.clear()
            self._client = None

    async def publish(
        self,
        topic: str,
        payload: str | bytes | Mapping[str, Any],
        *,
        retain: bool = False,
        qos: int = 0,
    ) -> None:
        """Publish one bounded MQTT message when connected."""
        if isinstance(payload, Mapping):
            payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if isinstance(payload, str):
            payload = payload.encode()
        if len(payload) > 1024 * 1024:
            raise ValueError("MQTT payload exceeds 1 MiB")
        if not self._connected.is_set():
            return
        async with self._client_lock:
            client = self._client
            if client is None:
                return
            await client.publish(topic, payload, qos=qos, retain=retain, timeout=15)

    async def publish_json(
        self, topic: str, payload: Mapping[str, Any], *, retain: bool = False
    ) -> None:
        """Publish a JSON MQTT message."""
        await self.publish(topic, payload, retain=retain)
