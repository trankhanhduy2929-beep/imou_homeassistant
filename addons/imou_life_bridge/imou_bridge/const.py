"""Constants for the Imou Life integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "imou_life"

CONF_ACCOUNT: Final = "account"
CONF_PASSWORD: Final = "password"
CONF_TERMINAL_ID: Final = "terminal_id"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_REQUEST_TIMEOUT: Final = "request_timeout"
CONF_MAX_CONCURRENT_REQUESTS: Final = "max_concurrent_requests"
CONF_MAX_PROPERTIES: Final = "max_properties"
CONF_GATEWAY_STREAM_TEMPLATE: Final = "gateway_stream_template"

DEFAULT_POLL_INTERVAL: Final = 30
DEFAULT_REQUEST_TIMEOUT: Final = 15
DEFAULT_MAX_CONCURRENT_REQUESTS: Final = 4
DEFAULT_MAX_PROPERTIES: Final = 80

MIN_POLL_INTERVAL: Final = 15
MAX_POLL_INTERVAL: Final = 300
MIN_REQUEST_TIMEOUT: Final = 5
MAX_REQUEST_TIMEOUT: Final = 60
MIN_MAX_PROPERTIES: Final = 10
MAX_MAX_PROPERTIES: Final = 200

ENTRY_HOST: Final = "app-v3.easy4ipcloud.com:443"
PC_ENTRY_HOST: Final = "app-v2.easy4ipcloud.com:443"
PC_ENTRY_HOST_FALLBACK: Final = "app.easy4ipcloud.com:443"
APP_ID: Final = "easy4ipbaseapp"
PROJECT_ID: Final = "Base"
APP_VERSION: Final = "10.1.6"
PROTOCOL_VERSION: Final = "V9.7.4"
SIGNATURE_REVISION: Final = "191204"

PLATFORMS: Final = (
    "binary_sensor",
    "camera",
    "number",
    "select",
    "sensor",
    "switch",
    "text",
)

SERVICE_GET_LIVE_URL: Final = "get_live_url"
SERVICE_INVOKE: Final = "invoke_service"
SERVICE_PTZ_MOVE: Final = "ptz_move"
SERVICE_REFRESH: Final = "refresh"
SERVICE_SET_PROPERTY: Final = "set_property"

DATA_RUNTIME: Final = "runtime"
