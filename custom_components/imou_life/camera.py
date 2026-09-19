"""Camera entities for Imou Life channels."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import CONF_RTSP_TRANSPORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImouConfigEntry
from .api import ImouApiError
from .entity import ImouChannelEntity, async_setup_dynamic_entities
from .media import extract_image_url, extract_stream_url
from .models import ImouChannel, ImouDevice, is_online_status
from .p2p import ImouP2PError, ImouP2PRelayManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one camera entity per discovered channel."""
    coordinator = entry.runtime_data.coordinator
    p2p = entry.runtime_data.p2p

    def build(device: ImouDevice) -> Iterable[Entity]:
        return (
            ImouLifeCamera(coordinator, p2p, device, channel)
            for channel in device.channels
        )

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, build)


class ImouLifeCamera(ImouChannelEntity, Camera):
    """An Imou cloud camera or recorder channel."""

    _attr_brand = "Imou"
    _attr_content_type = "image/jpeg"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator,
        p2p: ImouP2PRelayManager,
        device: ImouDevice,
        channel: ImouChannel,
    ) -> None:
        """Initialize a camera channel."""
        Camera.__init__(self)
        ImouChannelEntity.__init__(
            self,
            coordinator,
            device,
            channel,
            unique_suffix="camera",
            name=channel.name,
        )
        self._p2p = p2p
        self.stream_options[CONF_RTSP_TRANSPORT] = "tcp"
        self._attr_model = device.model or device.product_id

    @property
    def available(self) -> bool:
        """Require both device and channel to be usable."""
        if not super().available:
            return False
        channel = self.channel
        return channel is not None and is_online_status(channel.status) is not False

    @property
    def use_stream_for_stills(self) -> bool:
        """Prefer the cloud thumbnail and use stream frames only as fallback."""
        channel = self.channel
        return channel is None or not channel.picture_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Fetch the bounded cloud thumbnail when available."""
        channel = self.channel
        device = self.device
        if channel is None or device is None:
            return None
        image_url = channel.picture_url
        if not image_url:
            try:
                media = await self.coordinator.api.async_get_live_url(
                    channel.product_id or device.product_id,
                    device.device_id,
                    channel.channel_id,
                )
                image_url = extract_image_url(media)
            except ImouApiError as err:
                _LOGGER.debug(
                    "Snapshot URL unavailable device_id=%s channel_id=%s code=%s",
                    device.device_id,
                    channel.channel_id,
                    err.code,
                )
                return None
        if not image_url:
            return None
        try:
            return await self.coordinator.api.async_fetch_bytes(image_url)
        except ImouApiError as err:
            _LOGGER.debug(
                "Snapshot unavailable device_id=%s channel_id=%s code=%s",
                device.device_id,
                channel.channel_id,
                err.code,
            )
            return None

    async def stream_source(self) -> str | None:
        """Prefer a local P2P RTSP relay, then fall back to a cloud URL."""
        channel = self.channel
        device = self.device
        if channel is None or device is None:
            return None
        try:
            return await self._p2p.async_stream_url(device, channel)
        except ImouP2PError as err:
            _LOGGER.warning(
                "Imou P2P RTSP unavailable device_id=%s channel_id=%s stage=%s",
                device.device_id,
                channel.channel_id,
                err.stage,
            )
        try:
            media = await self.coordinator.api.async_get_live_url(
                channel.product_id or device.product_id,
                device.device_id,
                channel.channel_id,
            )
        except ImouApiError as err:
            _LOGGER.debug(
                "Live URL unavailable device_id=%s channel_id=%s code=%s",
                device.device_id,
                channel.channel_id,
                err.code,
            )
            return None
        stream_url = extract_stream_url(media)
        if stream_url is None:
            _LOGGER.warning(
                "Imou stream is not playable by Home Assistant "
                "device_id=%s channel_id=%s status=%s",
                device.device_id,
                channel.channel_id,
                self.coordinator.api.live_diagnostic(
                    channel.product_id or device.product_id,
                    device.device_id,
                    channel.channel_id,
                ),
            )
        return stream_url

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose safe stream negotiation diagnostics without signed URLs."""
        channel = self.channel
        device = self.device
        if channel is None or device is None:
            return {"stream_status": "unavailable"}
        return {
            "stream_status": self.coordinator.api.live_diagnostic(
                channel.product_id or device.product_id,
                device.device_id,
                channel.channel_id,
            ),
            "p2p_status": self._p2p.diagnostic(device.device_id),
        }
