"""Diagnostics support for the Imou Connect integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import ImouConfigEntry

REDACTED = "**REDACTED**"
_SENSITIVE_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "accesskey",
    "access_key",
    "privatekey",
    "session",
    "account",
    "username",
    "p2p",
    "mqtt",
    "awskey",
    "receipt",
)
_MAX_DEPTH = 6


def _is_sensitive(key: Any) -> bool:
    normalized = str(key).replace("_", "").replace("-", "").casefold()
    return any(part.replace("_", "") in normalized for part in _SENSITIVE_PARTS)


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return "<max-depth>"
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _is_sensitive(key) else _redact(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://", "rtsp://")):
        return REDACTED
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ImouConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for one config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    data = coordinator.data if isinstance(coordinator.data, dict) else {}
    devices: list[dict[str, Any]] = []
    for device in data.values():
        model = device.thing_model
        devices.append(
            {
                "device_id": device.device_id,
                "product_id": device.product_id,
                "name": device.name,
                "model": device.model,
                "catalog": device.catalog,
                "online": device.online,
                "channels": [
                    {
                        "channel_id": channel.channel_id,
                        "name": channel.name,
                        "status": channel.status,
                    }
                    for channel in device.channels
                ],
                "supports_ptz": device.supports_ptz,
                "raw": _redact(device.raw),
                "thing_model": {
                    "properties": [prop.identifier for prop in model.properties],
                    "services": [
                        {
                            "identifier": service.identifier,
                            "ref": service.ref,
                            "inputs": [item.identifier for item in service.input_data],
                        }
                        for service in model.services
                    ],
                },
            }
        )
    return {
        "entry": {
            "options": _redact(dict(entry.options)),
            "device_count": len(devices),
        },
        "devices": devices,
    }