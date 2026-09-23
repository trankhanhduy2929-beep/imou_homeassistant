from __future__ import annotations

import json

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import CONF_RTSP_TRANSPORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .const import DOMAIN
from .media import configured_local_cameras, local_rtsp_source


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

    @property
    def use_stream_for_stills(self) -> bool:
        return True

    async def stream_source(self) -> str | None:
        return self._source

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"stream_source_type": "local_rtsp"}
