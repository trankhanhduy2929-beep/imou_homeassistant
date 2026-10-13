from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from functools import cached_property
from time import monotonic

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import CONF_RTSP_TRANSPORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .const import DOMAIN
from .media import configured_local_cameras, local_rtsp_source

_LOGGER = logging.getLogger(__name__)
_SNAPSHOT_TIMEOUT = 9
_SNAPSHOT_CACHE_SECONDS = 1
_SNAPSHOT_RETRY_SECONDS = 2
_MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024
_MAX_SNAPSHOT_DIMENSION = 4096


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[ImouConnectLocalCamera] = []
    for key, config in configured_local_cameras(entry.options).items():
        device_id, channel_id = json.loads(key)
        device = coordinator.device(device_id)
        channel = next(
            (item for item in device.channels if item.channel_id == channel_id),
            None,
        ) if device is not None else None
        entities.append(
            ImouConnectLocalCamera(
                device_id,
                channel_id,
                device.name if device is not None else "Imou",
                channel.name if channel is not None else f"Camera {channel_id}",
                device.model if device is not None else None,
                local_rtsp_source(config),
            )
        )
    if entities:
        async_add_entities(entities)


class ImouConnectLocalCamera(Camera):
    _attr_brand = "Imou"
    _attr_content_type = "image/jpeg"
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:cctv"

    def __init__(
        self,
        device_id: str,
        channel_id: str,
        device_name: str,
        channel_name: str,
        model: str | None,
        source: str,
    ) -> None:
        super().__init__()
        self._source = source
        self._attr_unique_id = f"{device_id}_{channel_id}_camera"
        self._attr_name = channel_name
        self._attr_model = model
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer="Imou",
            name=device_name,
            model=model,
            serial_number=device_id,
        )
        self.stream_options[CONF_RTSP_TRANSPORT] = "tcp"
        self._snapshot_lock = asyncio.Lock()
        self._snapshot_removed = False
        self._snapshot: bytes | None = None
        self._snapshot_size: tuple[int | None, int | None] | None = None
        self._snapshot_expires = 0.0
        self._snapshot_retry_after = 0.0
        self._snapshot_task: asyncio.Task[bytes | None] | None = None

    @cached_property
    def use_stream_for_stills(self) -> bool:
        return False

    async def stream_source(self) -> str | None:
        return self._source

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        if self._snapshot_removed or any(
            value is not None
            and (type(value) is not int or not 1 <= value <= _MAX_SNAPSHOT_DIMENSION)
            for value in (width, height)
        ):
            return None
        deadline = monotonic() + _SNAPSHOT_TIMEOUT
        try:
            async with asyncio.timeout(_SNAPSHOT_TIMEOUT), self._snapshot_lock:
                if self._snapshot_task is not None and not self._snapshot_task.done():
                    await asyncio.shield(self._snapshot_task)
                if self._snapshot_removed or monotonic() < self._snapshot_retry_after:
                    return None
                if (
                    self._snapshot is not None
                    and self._snapshot_size == (width, height)
                    and monotonic() < self._snapshot_expires
                ):
                    return self._snapshot
                if monotonic() >= deadline:
                    return None
                self._snapshot_task = asyncio.create_task(
                    self._async_capture_snapshot(width, height, deadline),
                    name="Imou local snapshot",
                )
                image = await asyncio.shield(self._snapshot_task)
                if not self._snapshot_removed:
                    return image
        except TimeoutError:
            _LOGGER.debug("Local camera snapshot timed out")
        return None

    async def _async_capture_snapshot(
        self, width: int | None, height: int | None, deadline: float
    ) -> bytes | None:
        self._snapshot_expires = 0.0
        try:
            stream = self.stream
            if stream is None:
                stream = await self.async_create_stream()
                self.stream = stream
            if stream is not None:
                while not self._snapshot_removed and monotonic() < deadline:
                    image = await stream.async_get_image(
                        width=width, height=height, wait_for_next_keyframe=False
                    )
                    if self._snapshot_removed:
                        break
                    if image is None or image is self._snapshot:
                        await asyncio.sleep(min(0.25, max(0.0, deadline - monotonic())))
                        continue
                    if (
                        not isinstance(image, bytes)
                        or not 4 <= len(image) <= _MAX_SNAPSHOT_BYTES
                        or not image.startswith(b"\xff\xd8")
                        or not image.endswith(b"\xff\xd9")
                    ):
                        break
                    self._snapshot = image
                    if monotonic() >= deadline:
                        self._snapshot_size = None
                        break
                    self._snapshot_size = (width, height)
                    self._snapshot_expires = monotonic() + _SNAPSHOT_CACHE_SECONDS
                    self._snapshot_retry_after = 0.0
                    return image
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Local camera snapshot failed: %s", type(err).__name__)
        else:
            if not self._snapshot_removed:
                _LOGGER.debug("Local camera snapshot unavailable from HA stream")
        if not self._snapshot_removed:
            self._snapshot_retry_after = monotonic() + _SNAPSHOT_RETRY_SECONDS

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._snapshot_removed = False
        self._snapshot_retry_after = 0.0

    async def async_will_remove_from_hass(self) -> None:
        self._snapshot_removed = True
        self._snapshot = None
        self._snapshot_size = None
        self._snapshot_expires = 0.0
        try:
            async with self._snapshot_lock:
                if self._snapshot_task is not None:
                    try:
                        await asyncio.shield(self._snapshot_task)
                    except asyncio.CancelledError:
                        current = asyncio.current_task()
                        if current is not None and current.cancelling():
                            raise
                    self._snapshot_task = None
                if self.stream is not None:
                    self.stream.dynamic_stream_settings = replace(
                        self.stream.dynamic_stream_settings, preload_stream=False
                    )
                    await self.stream.stop()
                    self.stream = None
        finally:
            await super().async_will_remove_from_hass()

    @cached_property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"stream_source_type": "local_rtsp"}
