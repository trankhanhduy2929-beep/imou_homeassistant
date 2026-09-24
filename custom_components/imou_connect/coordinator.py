"""Cloud polling coordinator for Imou Life."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ImouApiClient,
    ImouApiError,
    ImouAuthError,
    ImouCaptchaRequired,
    ImouConnectionError,
    ImouCredentialError,
    ImouQrLoginRequired,
    ImouTwoStepVerificationRequired,
)
from .const import (
    CONF_LOCAL_CAMERAS,
    CONF_LOCAL_HOST,
    CONF_LOCAL_PASSWORD,
    CONF_LOCAL_USERNAME,
    CONF_ONVIF_PORT,
    CONF_ONVIF_PROFILE,
    CONF_ONVIF_PTZ,
    DEFAULT_ONVIF_PORT,
    DOMAIN,
    EVENT_REALTIME,
    REALTIME_HOLD_SECONDS,
    PTZ_NO_AUTHORITY_CODES,
    PTZ_STEP_DURATION_MS,
)
from .media import configured_local_cameras, local_camera_key
from .onvif_ptz import OnvifPtzClient, OnvifPtzError
from .models import (
    ImouDevice,
    ThingModel,
    ThingProperty,
    ThingService,
    normalize_property_values,
    parse_thing_model,
    stream_entry_host,
)
from .realtime import (
    ImouRealtimeEvent,
    alarm_identity,
    alarm_to_realtime_event,
    is_human_event,
    is_motion_event,
)

_LOGGER = logging.getLogger(__name__)
_MODEL_TTL = 300
_MODEL_RETRY_DELAY = 60


class ImouDataUpdateCoordinator(DataUpdateCoordinator[dict[str, ImouDevice]]):
    """Authenticate, discover devices, and update primitive properties."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: Any,
        api: ImouApiClient,
        *,
        poll_interval: int,
        max_properties: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self.api = api
        self.config_entry = config_entry
        self.max_properties = max_properties
        self._model_cache: dict[str, ThingModel] = {}
        self._model_cache_expires: dict[str, float] = {}
        self._model_locks: dict[str, asyncio.Lock] = {}
        self.realtime_connected = False
        self._realtime_states: dict[tuple[str, str, str], bool] = {}
        self._realtime_events: dict[tuple[str, str], dict[str, Any]] = {}
        self._realtime_clear_tasks: dict[
            tuple[str, str, str], asyncio.Task[None]
        ] = {}
        self._alarm_identities: set[str] = set()
        self._alarm_baseline_initialized = False
        self._alarm_auth_generation: int | None = None
        self._ptz_rejected: set[str] = set()
        self._ptz_host_cache: dict[str, str] = {}
        self._onvif_clients: dict[str, OnvifPtzClient] = {}

    async def _async_update_data(self) -> dict[str, ImouDevice]:
        """Fetch all devices, models, and exposed property values."""
        try:
            await self.api.async_authenticate()
            devices = await self.api.async_list_devices()
        except (
            ImouCaptchaRequired,
            ImouTwoStepVerificationRequired,
            ImouCredentialError,
            ImouQrLoginRequired,
            ImouAuthError,
        ) as err:
            raise ConfigEntryAuthFailed(
                "Imou Life requires account verification again"
            ) from err
        except ImouConnectionError as err:
            raise UpdateFailed(f"Cannot connect to Imou cloud: {err}") from err
        except ImouApiError as err:
            raise UpdateFailed(f"Imou device discovery failed: {err}") from err

        previous = self.data if isinstance(self.data, dict) else {}
        try:
            enriched = await asyncio.gather(
                *(
                    self._async_enrich_device(
                        device, previous.get(device.device_id)
                    )
                    for device in devices
                )
            )
        except ImouAuthError as err:
            raise ConfigEntryAuthFailed(
                "Imou Life requires account verification again"
            ) from err
        result = {device.device_id: device for device in enriched}
        try:
            result = await self._async_poll_latest_alarms(result)
        except ImouAuthError as err:
            raise ConfigEntryAuthFailed(
                "Imou Life requires account verification again"
            ) from err
        except ImouApiError as err:
            _LOGGER.debug(
                "Latest Imou alarm polling unavailable code=%s",
                err.code,
            )
        return result

    async def _async_enrich_device(
        self, device: ImouDevice, previous: ImouDevice | None
    ) -> ImouDevice:
        """Attach a cached thing model and current primitive property values."""
        if previous is not None and (
            previous.device_id != device.device_id
            or previous.product_id != device.product_id
        ):
            previous = None
        model = previous.thing_model if previous is not None else device.thing_model
        if device.product_id:
            model = await self._async_product_model(device.product_id, model)

        properties = model.exposed_properties(self.max_properties)
        readable_properties = tuple(prop for prop in properties if prop.readable)
        values = previous.properties if previous is not None else {}
        if readable_properties:
            try:
                raw_values = await self.api.async_get_properties(
                    device.product_id,
                    device.device_id,
                    [prop.ref for prop in readable_properties],
                    group_control_flag=device.group_control_flag,
                    channel_id=self._default_channel_id(device),
                    fallback_identifiers={
                        prop.ref: prop.identifier for prop in readable_properties
                    },
                )
                values = {
                    **values,
                    **normalize_property_values(model.properties, raw_values),
                }
            except ImouAuthError:
                raise
            except ImouApiError as err:
                _LOGGER.debug("Property refresh unavailable code=%s", err.code)
        enriched = device.with_thing_model(model).with_properties(values)
        return enriched

    async def _async_product_model(
        self, product_id: str, previous: ThingModel
    ) -> ThingModel:
        lock = self._model_locks.setdefault(product_id, asyncio.Lock())
        async with lock:
            model = self._model_cache.get(product_id, previous)
            if time.monotonic() < self._model_cache_expires.get(product_id, 0):
                return model
            try:
                response = await self.api.async_query_model(product_id)
                model = parse_thing_model(
                    response.get("modelJson")
                    or response.get("model")
                    or response.get("thingModel")
                    or response,
                    str(response.get("md5")) if response.get("md5") else None,
                )
            except ImouAuthError:
                raise
            except ImouApiError as err:
                self._model_cache[product_id] = model
                self._model_cache_expires[product_id] = (
                    time.monotonic() + _MODEL_RETRY_DELAY
                )
                _LOGGER.debug("Thing model unavailable code=%s", err.code)
                return model
            exposed = len(model.exposed_properties(self.max_properties)) + sum(
                service.zero_input for service in model.services
            )
            self._model_cache[product_id] = model
            self._model_cache_expires[product_id] = time.monotonic() + (
                _MODEL_TTL if exposed else _MODEL_RETRY_DELAY
            )
            _LOGGER.debug(
                "Thing model properties=%s services=%s exposed=%s",
                len(model.properties), len(model.services), exposed,
            )
            return model

    def device(self, device_id: str) -> ImouDevice | None:
        """Return the latest device snapshot."""
        return self.data.get(device_id) if isinstance(self.data, dict) else None

    def realtime_state(self, device_id: str, channel_id: str, kind: str) -> bool:
        """Return the latest bounded realtime state for one channel."""
        return self._realtime_states.get((device_id, channel_id, kind), False)

    def realtime_attributes(self, device_id: str, channel_id: str) -> dict[str, Any]:
        """Return safe metadata for the last realtime channel event."""
        return dict(self._realtime_events.get((device_id, channel_id), {}))

    async def async_set_realtime_connected(self, connected: bool) -> None:
        """Update MQTT availability and refresh realtime entities."""
        if self.realtime_connected == connected:
            return
        self.realtime_connected = connected
        self.async_update_listeners()

    async def async_handle_realtime_event(self, event: ImouRealtimeEvent) -> None:
        """Apply one MQTT property/alarm event to Home Assistant entities."""
        data = self.data if isinstance(self.data, dict) else {}
        await self._async_apply_realtime_event(event, data, notify=True)

    async def _async_apply_realtime_event(
        self,
        event: ImouRealtimeEvent,
        data: dict[str, ImouDevice],
        *,
        notify: bool,
    ) -> tuple[dict[str, ImouDevice], bool]:
        """Apply MQTT or polled alarm data to one coordinator snapshot."""
        device_key, device = self._resolve_event_device(data, event.device_id)
        if device is None and event.ap_id:
            device_key, device = self._resolve_event_device(data, event.ap_id)
        if device is None and not event.device_id and len(data) == 1:
            device_key, device = next(iter(data.items()))
        if device is None:
            return data, False

        updated_data = data
        data_changed = False
        normalized = normalize_property_values(
            device.thing_model.properties, event.properties
        )
        if normalized:
            device = device.with_properties({**device.properties, **normalized})
            updated_data = dict(data)
            updated_data[device_key or device.device_id] = device
            data_changed = True

        event_data = event.event_data
        event_data["resolved_device_id"] = device.device_id
        self.hass.bus.async_fire(EVENT_REALTIME, event_data)

        channel_id = self._resolve_event_channel(device, event.channel_id)
        if channel_id is None:
            if notify and data_changed:
                self.async_set_updated_data(updated_data)
            return updated_data, data_changed
        event_key = (device.device_id, channel_id)
        event_metadata = {
            "event": event.event,
            "alert": event.alert,
            "title": event.title,
            "name": event.name,
            "type": event.alarm_type,
            "object_type": event.object_type,
            "time": event.timestamp,
            "topic": event.topic,
            "has_image": event.image_url is not None,
            "mqtt_connected": self.realtime_connected,
        }
        event_changed = self._realtime_events.get(event_key) != event_metadata
        self._realtime_events[event_key] = event_metadata

        human = is_human_event(event)
        motion = is_motion_event(event)
        if human is True:
            motion = True
        state_changed = False
        if motion is not None:
            state_changed |= self._set_realtime_state(
                device.device_id, channel_id, "motion", motion
            )
        if human is not None:
            state_changed |= self._set_realtime_state(
                device.device_id, channel_id, "human", human
            )
        changed = data_changed or state_changed or event_changed
        if notify and data_changed:
            self.async_set_updated_data(updated_data)
        elif notify and changed:
            self.async_update_listeners()
        return updated_data, changed

    async def _async_poll_latest_alarms(
        self, data: dict[str, ImouDevice]
    ) -> dict[str, ImouDevice]:
        """Poll cloud alarms as a fallback when MQTT push registration is absent."""
        auth_generation = self.api.auth_generation
        if self._alarm_auth_generation != auth_generation:
            self._alarm_auth_generation = auth_generation
            self._alarm_identities.clear()
            self._alarm_baseline_initialized = False
        alarms = await self.api.async_get_latest_alarms(list(data.values()))
        current_identities = {
            identity
            for alarm in alarms
            if (identity := alarm_identity(alarm))
        }
        initial_poll = not self._alarm_baseline_initialized
        if initial_poll:
            self._alarm_baseline_initialized = True

        updated_data = data
        for alarm in alarms:
            identity = alarm_identity(alarm)
            if not identity:
                continue
            if initial_poll:
                if not self._alarm_is_recent(alarm):
                    continue
            elif identity in self._alarm_identities:
                continue
            updated_data, _ = await self._async_apply_realtime_event(
                alarm_to_realtime_event(alarm),
                updated_data,
                notify=False,
            )
        self._alarm_identities.update(current_identities)
        if len(self._alarm_identities) > 2048:
            self._alarm_identities = set(tuple(self._alarm_identities)[-1024:])
        return updated_data

    @staticmethod
    def _default_channel_id(device: ImouDevice) -> str | None:
        """Return the channel used by the APK for device-level IoT control."""
        return device.channels[0].channel_id if device.channels else None

    @staticmethod
    def _resolve_event_channel(device: ImouDevice, channel_id: str) -> str | None:
        channel_ids = tuple(channel.channel_id for channel in device.channels)
        normalized_channel = str(channel_id).strip()
        if normalized_channel in channel_ids:
            return normalized_channel
        if len(channel_ids) == 1:
            return channel_ids[0]
        if normalized_channel in {"", "-1"}:
            return None
        try:
            numeric_channel = int(normalized_channel)
        except (TypeError, ValueError):
            return None
        shifted = {
            candidate
            for candidate in (str(numeric_channel - 1), str(numeric_channel + 1))
            if candidate in channel_ids
        }
        if len(shifted) == 1:
            return shifted.pop()
        return None

    @staticmethod
    def _resolve_event_device(
        data: dict[str, ImouDevice], device_id: str
    ) -> tuple[str | None, ImouDevice | None]:
        """Resolve cloud IDs case-insensitively without changing entity IDs."""
        normalized = str(device_id).strip().casefold()
        if not normalized:
            return None, None
        direct = data.get(device_id)
        if direct is not None:
            return device_id, direct
        for key, device in data.items():
            if key.casefold() == normalized or device.device_id.casefold() == normalized:
                return key, device
        return None, None

    def _alarm_is_recent(self, alarm: dict[str, Any]) -> bool:
        """Allow a fresh alarm through the first-poll stale-record baseline."""
        raw_timestamp = alarm.get("time")
        if raw_timestamp in (None, ""):
            raw_timestamp = alarm.get("timestamp")
        timestamp = self._alarm_timestamp(raw_timestamp)
        if timestamp is None:
            return False
        poll_seconds = (
            self.update_interval.total_seconds()
            if self.update_interval is not None
            else REALTIME_HOLD_SECONDS
        )
        freshness = max(REALTIME_HOLD_SECONDS, poll_seconds * 2)
        return -5 <= time.time() - timestamp <= freshness

    def _alarm_timestamp(self, value: Any) -> float | None:
        """Parse epoch seconds/milliseconds and common Imou date strings."""
        if value in (None, ""):
            return None
        text = str(value).strip()
        try:
            numeric = float(text)
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None:
            while numeric > 100_000_000_000:
                numeric /= 1000
            return numeric if numeric > 0 else None
        normalized = text.replace("Z", "+00:00")
        for candidate in (
            normalized,
            normalized.replace("/", "-"),
        ):
            try:
                parsed = datetime.fromisoformat(candidate)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                timezone = UTC
                timezone_name = str(
                    getattr(getattr(self.hass, "config", None), "time_zone", "")
                    or ""
                )
                if timezone_name:
                    try:
                        timezone = ZoneInfo(timezone_name)
                    except ZoneInfoNotFoundError:
                        pass
                parsed = parsed.replace(tzinfo=timezone)
            return parsed.timestamp()
        return None

    def _set_realtime_state(
        self, device_id: str, channel_id: str, kind: str, active: bool
    ) -> bool:
        key = (device_id, channel_id, kind)
        previous = self._realtime_states.get(key, False)
        clear_task = self._realtime_clear_tasks.pop(key, None)
        if clear_task is not None:
            clear_task.cancel()
        if active:
            self._realtime_states[key] = True
            self._realtime_clear_tasks[key] = asyncio.create_task(
                self._async_clear_realtime_state(key),
                name=f"{DOMAIN} clear {kind} {device_id} {channel_id}",
            )
        else:
            self._realtime_states.pop(key, None)
        return previous != active

    async def _async_clear_realtime_state(
        self, key: tuple[str, str, str]
    ) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(REALTIME_HOLD_SECONDS)
        except asyncio.CancelledError:
            return
        if self._realtime_clear_tasks.get(key) is not current_task:
            return
        self._realtime_clear_tasks.pop(key, None)
        if self._realtime_states.pop(key, False):
            self.async_update_listeners()

    async def async_shutdown_realtime(self) -> None:
        """Cancel pending realtime state timers during config-entry unload."""
        tasks = tuple(self._realtime_clear_tasks.values())
        self._realtime_clear_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._realtime_states.clear()
        self._realtime_events.clear()
        self.realtime_connected = False

    async def async_set_property(
        self,
        device_id: str,
        prop: ThingProperty,
        value: Any,
    ) -> None:
        """Write one thing-model property and refresh coordinator state."""
        device = self.device(device_id)
        if device is None:
            raise UpdateFailed("Imou device is no longer available")
        if (
            not prop.writable
            or prop not in device.thing_model.exposed_properties(self.max_properties)
        ):
            raise UpdateFailed("Imou property is no longer writable")
        try:
            await self.api.async_set_properties(
                device.product_id,
                device.device_id,
                {prop.ref: value},
                group_control_flag=device.group_control_flag,
                channel_id=self._default_channel_id(device),
                sync_local_cache=True,
                fallback_identifiers={prop.identifier: value},
            )
        except ImouAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ImouApiError as err:
            raise UpdateFailed(f"Could not write Imou property: {err}") from err
        await self.async_request_refresh()
        current = self.data if isinstance(self.data, dict) else {}
        refreshed = current.get(device.device_id)
        if (
            refreshed is not None
            and refreshed.product_id == device.product_id
            and prop in refreshed.thing_model.exposed_properties(self.max_properties)
            and refreshed.properties.get(prop.identifier) != value
        ):
            optimistic = dict(refreshed.properties)
            optimistic[prop.identifier] = value
            updated = dict(current)
            updated[device.device_id] = refreshed.with_properties(optimistic)
            self.async_set_updated_data(updated)

    async def async_invoke_service(
        self,
        device_id: str,
        service: ThingService,
    ) -> None:
        """Invoke a zero-input thing-model service."""
        device = self.device(device_id)
        if device is None:
            raise UpdateFailed("Imou device is no longer available")
        if not service.zero_input or service not in device.thing_model.services:
            raise UpdateFailed("Imou service is no longer available without input")
        try:
            await self.api.async_invoke_service(
                device.product_id,
                device.device_id,
                service.ref,
                {},
                group_control_flag=device.group_control_flag,
                channel_id=self._default_channel_id(device),
            )
        except ImouAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ImouApiError as err:
            raise UpdateFailed(f"Could not invoke Imou service: {err}") from err
        await self.async_request_refresh()

    async def async_ptz_move(
        self,
        device_id: str,
        channel_id: str,
        *,
        horizontal: float,
        vertical: float,
        zoom: float = 0.0,
        duration: int = PTZ_STEP_DURATION_MS,
    ) -> None:
        """Move a PTZ camera using the recovered cloud API."""
        device = self.device(device_id)
        if device is None:
            raise UpdateFailed("Imou device is no longer available")
        if not any(channel.channel_id == str(channel_id) for channel in device.channels):
            raise UpdateFailed("Imou camera channel is no longer available")
        onvif = self._onvif_client(device_id, channel_id)
        if onvif is not None:
            try:
                if not await onvif.async_supported():
                    raise OnvifPtzError("Camera has no PTZ-enabled ONVIF media profile")
                await onvif.async_move(
                    pan=horizontal,
                    tilt=vertical,
                    zoom=-zoom,
                    duration_ms=duration,
                )
            except OnvifPtzError as err:
                raise UpdateFailed(
                    f"Could not move Imou PTZ camera over ONVIF: {err}. "
                    "Check this camera's ONVIF settings in Imou Connect Configure; "
                    "no cloud PTZ command was sent"
                ) from None
            return
        if device_id in self._ptz_rejected:
            raise UpdateFailed("Imou cloud PTZ was rejected; configure ONVIF LAN for this camera")
        discovered = [
            item
            for item in dict.fromkeys(
                (
                    stream_entry_host(device.raw),
                    self._ptz_host_cache.get(device.device_id),
                )
            )
            if item
        ]
        errors: list[ImouApiError] = []
        for host in discovered:
            error = await self._async_try_ptz(
                device, channel_id, horizontal, vertical, zoom, duration, host
            )
            if error is None:
                self._ptz_host_cache[device.device_id] = host
                return
            errors.append(error)
        fetched = await self._async_fetch_ptz_host(device.device_id)
        if fetched and fetched not in discovered:
            error = await self._async_try_ptz(
                device, channel_id, horizontal, vertical, zoom, duration, fetched
            )
            if error is None:
                self._ptz_host_cache[device.device_id] = fetched
                return
            errors.append(error)
        if not errors:
            # No device host was available at all; try the account entry once.
            error = await self._async_try_ptz(
                device, channel_id, horizontal, vertical, zoom, duration, None
            )
            if error is None:
                return
            errors.append(error)
        last_error = errors[-1]
        if last_error.code in PTZ_NO_AUTHORITY_CODES or last_error.code in (401, 403):
            self._ptz_rejected.add(device_id)
            _LOGGER.debug(
                "Imou PTZ not permitted device=%s code=%s", device_id, last_error.code
            )
        raise UpdateFailed(
            f"Imou cloud PTZ failed (code={last_error.code}); "
            "configure this camera's local IP, ONVIF port and credentials "
            "in Imou Connect Configure to use ONVIF LAN"
        ) from None

    async def _async_try_ptz(
        self,
        device: ImouDevice,
        channel_id: str,
        horizontal: float,
        vertical: float,
        zoom: float,
        duration: int,
        host: str | None,
    ) -> ImouApiError | None:
        """Attempt one PTZ call and return the API error on failure."""
        try:
            await self.api.async_ptz_move(
                device.device_id,
                str(channel_id),
                horizontal=horizontal,
                vertical=vertical,
                zoom=zoom,
                duration=duration,
                host=host,
            )
        except ImouAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ImouApiError as err:
            return err
        return None

    async def _async_fetch_ptz_host(self, device_id: str) -> str | None:
        try:
            return await self.api.async_get_stream_entry_host(device_id)
        except ImouAuthError:
            raise
        except ImouApiError as err:
            _LOGGER.debug("Imou stream entry unavailable code=%s", err.code)
            return None

    def _onvif_client(
        self, device_id: str, channel_id: str
    ) -> OnvifPtzClient | None:
        """Return a cached ONVIF PTZ client when the channel is local."""
        options = getattr(self.config_entry, "options", None)
        if not isinstance(options, Mapping):
            return None
        key = local_camera_key(device_id, channel_id)
        camera = configured_local_cameras(options).get(key)
        if camera is None:
            raw = options.get(CONF_LOCAL_CAMERAS)
            if isinstance(raw, Mapping) and key in raw:
                raise UpdateFailed("Invalid local camera configuration; edit ONVIF settings in Configure")
            return None
        if not camera.get(CONF_ONVIF_PTZ):
            return None
        client = self._onvif_clients.get(key)
        if client is None:
            client = OnvifPtzClient(
                async_get_clientsession(self.hass),
                camera[CONF_LOCAL_HOST],
                int(camera.get(CONF_ONVIF_PORT, DEFAULT_ONVIF_PORT)),
                camera[CONF_LOCAL_USERNAME],
                camera[CONF_LOCAL_PASSWORD],
                profile_token=camera.get(CONF_ONVIF_PROFILE, ""),
            )
            self._onvif_clients[key] = client
        return client

    def local_ptz_configured(self, device_id: str, channel_id: str) -> bool:
        options = getattr(self.config_entry, "options", None)
        if not isinstance(options, Mapping):
            return False
        camera = configured_local_cameras(options).get(local_camera_key(device_id, channel_id))
        return bool(camera and camera.get(CONF_ONVIF_PTZ))

    def ptz_available(self, device_id: str, channel_id: str | None = None) -> bool:
        """Return whether PTZ control is still expected to work for a device."""
        if channel_id is not None and self.local_ptz_configured(device_id, channel_id):
            return True
        return device_id not in self._ptz_rejected
