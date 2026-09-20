"""Imou Life custom integration."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ImouApiClient
from .captcha import async_register_captcha_views
from .const import (
    CONF_ACCOUNT,
    CONF_MAX_CONCURRENT_REQUESTS,
    CONF_MAX_PROPERTIES,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_REQUEST_TIMEOUT,
    CONF_TERMINAL_ID,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    DEFAULT_MAX_PROPERTIES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_REQUEST_TIMEOUT,
    DOMAIN,
)
from .coordinator import ImouDataUpdateCoordinator
from .media import configured_local_cameras
from .realtime import ImouCloudMqttClient

if TYPE_CHECKING:
    from homeassistant.helpers.typing import ConfigType

PLATFORMS = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
)
CAMERA_PLATFORMS = (Platform.CAMERA,)


@dataclass(slots=True)
class ImouRuntime:
    """Runtime objects owned by one config entry."""

    api: ImouApiClient
    coordinator: ImouDataUpdateCoordinator
    realtime: ImouCloudMqttClient
    realtime_stop: asyncio.Event
    platforms: tuple[Platform, ...]
    realtime_task: asyncio.Task[None] | None = None


type ImouConfigEntry = ConfigEntry[ImouRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the server-side CAPTCHA endpoints."""
    async_register_captcha_views(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Set up Imou Life from a config entry."""
    async_register_captcha_views(hass)
    api = ImouApiClient(
        async_get_clientsession(hass),
        str(entry.data[CONF_ACCOUNT]),
        str(entry.data[CONF_PASSWORD]),
        str(entry.data[CONF_TERMINAL_ID]),
        request_timeout=float(
            entry.data.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)
        ),
        max_concurrent_requests=int(
            entry.data.get(
                CONF_MAX_CONCURRENT_REQUESTS, DEFAULT_MAX_CONCURRENT_REQUESTS
            )
        ),
        language=hass.config.language or "en_US",
    )
    coordinator = ImouDataUpdateCoordinator(
        hass,
        entry,
        api,
        poll_interval=int(entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
        max_properties=int(
            entry.data.get(CONF_MAX_PROPERTIES, DEFAULT_MAX_PROPERTIES)
        ),
    )
    realtime_stop = asyncio.Event()
    realtime = ImouCloudMqttClient(
        api,
        coordinator.async_handle_realtime_event,
        coordinator.async_set_realtime_connected,
    )
    platforms = PLATFORMS
    if configured_local_cameras(entry.options):
        platforms += CAMERA_PLATFORMS
    api.set_mqtt_request(realtime.async_request)
    entry.runtime_data = ImouRuntime(
        api=api,
        coordinator=coordinator,
        realtime=realtime,
        realtime_stop=realtime_stop,
        platforms=platforms,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, platforms)
    except BaseException:
        api.set_mqtt_request(None)
        realtime_stop.set()
        await coordinator.async_shutdown_realtime()
        await coordinator.async_shutdown()
        raise
    entry.runtime_data.realtime_task = asyncio.create_task(
        realtime.run(realtime_stop),
        name=f"{DOMAIN} realtime MQTT",
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ImouConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Unload an Imou Life config entry."""
    runtime = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, runtime.platforms)
    if not unloaded:
        return False
    runtime.api.set_mqtt_request(None)
    runtime.realtime_stop.set()
    if runtime.realtime_task is not None:
        runtime.realtime_task.cancel()
        with suppress(asyncio.CancelledError):
            await runtime.realtime_task
    await runtime.coordinator.async_shutdown_realtime()
    await runtime.coordinator.async_shutdown()
    return True
