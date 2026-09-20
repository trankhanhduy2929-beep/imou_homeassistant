"""Constants for the Imou Life integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "imou_connect"

CONF_ACCOUNT: Final = "account"
CONF_PASSWORD: Final = "password"
CONF_TERMINAL_ID: Final = "terminal_id"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_REQUEST_TIMEOUT: Final = "request_timeout"
CONF_MAX_CONCURRENT_REQUESTS: Final = "max_concurrent_requests"
CONF_MAX_PROPERTIES: Final = "max_properties"
CONF_VALID_CODE: Final = "valid_code"
CONF_RESEND_CODE: Final = "resend_code"
CONF_LOCAL_HOST: Final = "local_host"
CONF_LOCAL_CAMERAS: Final = "local_cameras"
CONF_CAMERA_ID: Final = "camera_id"
CONF_LOCAL_USERNAME: Final = "local_username"
CONF_LOCAL_PASSWORD: Final = "local_password"
CONF_RTSP_PORT: Final = "rtsp_port"
CONF_RTSP_PATH: Final = "rtsp_path"
CONF_REMOVE_CAMERA: Final = "remove_camera"

DEFAULT_POLL_INTERVAL: Final = 30
DEFAULT_REQUEST_TIMEOUT: Final = 15
DEFAULT_MAX_CONCURRENT_REQUESTS: Final = 4
DEFAULT_MAX_PROPERTIES: Final = 1000
REALTIME_HOLD_SECONDS: Final = 30

MIN_POLL_INTERVAL: Final = 15
MAX_POLL_INTERVAL: Final = 300
MIN_REQUEST_TIMEOUT: Final = 5
MAX_REQUEST_TIMEOUT: Final = 60
MIN_MAX_PROPERTIES: Final = 10
MAX_MAX_PROPERTIES: Final = 2000

ENTRY_HOST: Final = "app-v3.easy4ipcloud.com:443"
PC_ENTRY_HOST: Final = "app-v2.easy4ipcloud.com:443"
PC_ENTRY_HOST_FALLBACK: Final = "app.easy4ipcloud.com:443"
APP_ID: Final = "easy4ipbaseapp"
PROJECT_ID: Final = "Base"
APP_VERSION: Final = "10.1.6"
PROTOCOL_VERSION: Final = "V9.7.4"
SIGNATURE_REVISION: Final = "191204"

PLATFORM_NAMES: Final = (
    "binary_sensor",
    "button",
    "camera",
    "number",
    "select",
    "sensor",
    "switch",
    "text",
)

DATA_CAPTCHA_SESSIONS: Final = "captcha_sessions"
DATA_CAPTCHA_VIEWS_REGISTERED: Final = "captcha_views_registered"

CAPTCHA_BASE_PATH: Final = "/api/imou_connect/captcha"
CAPTCHA_SESSION_TTL: Final = 15 * 60
CAPTCHA_SUBMIT_MAX_BYTES: Final = 32 * 1024

CAPTCHA_RESUME_AUTHENTICATE: Final = "authenticate"
CAPTCHA_RESUME_SEND_OTP: Final = "send_otp"
CAPTCHA_RESUME_GRANT_OTP: Final = "grant_otp"

ATTR_PROPERTY_REF: Final = "property_ref"
ATTR_PRODUCT_ID: Final = "product_id"
ATTR_REALTIME_EVENT: Final = "realtime_event"
EVENT_REALTIME: Final = f"{DOMAIN}_event"
