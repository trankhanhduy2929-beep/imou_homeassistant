"""Standalone Imou cloud client, MQTT Discovery publisher, and Ingress UI."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import signal
import time
from base64 import b64decode
from binascii import Error as BinasciiError
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiohttp import ClientSession, web

from .api import (
    ImouApiClient,
    ImouApiError,
    ImouAuthError,
    ImouCaptchaChallenge,
    ImouCaptchaRequired,
    ImouConnectionError,
    ImouCredentialError,
    ImouQrLogin,
    ImouQrLoginRequired,
    ImouTwoStepVerificationRequired,
)
from .captcha_ui import render_captcha_page
from .discovery import (
    DEFAULT_TOPIC_PREFIX,
    DiscoveryEntity,
    DiscoveryPlan,
    build_device_plan,
    decode_command,
    encode_state,
    normalize_event_type,
    topic_segment,
)
from .ha_mqtt import BRIDGE_AVAILABILITY, HomeAssistantMqtt
from .models import ImouDevice, normalize_property_values, parse_thing_model
from .realtime import (
    ImouCloudMqttClient,
    ImouRealtimeEvent,
    alarm_identity,
    alarm_to_realtime_event,
    is_human_event,
    is_motion_event,
)

_LOGGER = logging.getLogger(__name__)

INGRESS_PROXY_ADDRESS = "172.30.32.2"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8099
QR_LOGIN_TTL_SECONDS = 300
QR_LOGIN_POLL_SECONDS = 0.2
QR_LOGIN_MAX_POLL_ERRORS = 5
QR_LOGIN_REQUEST_FORMAT_ERROR = 11001
QR_LOGIN_POST_SCAN_EXPIRE_GRACE_POLLS = 75
QR_LOGIN_ACCOUNT_RETRY_SECONDS = 2.0
MOTION_DURATION_SECONDS = 30
MAX_QR_IMAGE_BYTES = 1024 * 1024
MAX_CAPTCHA_IMAGE_BYTES = 1024 * 1024
CAPTCHA_SUBMIT_MAX_BYTES = 64 * 1024
GEETEST_ASSET_PATH = Path(__file__).with_name("web") / "gl4.js"


def _two_step_delivery_text(response: Any) -> str:
    """Return a safe, non-identifying description of the OTP destination."""
    if isinstance(response, dict):
        phone_keys = {"bindphone", "phone", "mobile", "mobilephone"}
        email_keys = {"bindemail", "email"}
        for key, value in response.items():
            normalized_key = str(key).casefold().replace("_", "")
            if value in (None, ""):
                continue
            if normalized_key in phone_keys:
                return "SMS đến số điện thoại đã liên kết"
            if normalized_key in email_keys:
                return "email đã liên kết"
    return "kênh bảo mật đã liên kết"


@dataclass(slots=True, frozen=True)
class BridgeSettings:
    """Runtime settings supplied by the Supervisor add-on options."""

    account: str
    password: str
    terminal_id: str
    request_timeout: float = 15
    poll_interval: int = 30
    max_properties: int = 1000
    snapshot_interval: int = 300
    publish_event_images: bool = True
    topic_prefix: str = DEFAULT_TOPIC_PREFIX

    @classmethod
    def from_environment(cls) -> BridgeSettings:
        """Read options without logging the account or password."""
        data_dir = Path(os.environ.get("IMOU_DATA_DIR", "/data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        terminal_path = data_dir / "terminal_id"
        try:
            terminal_id = terminal_path.read_text().strip()
        except OSError:
            terminal_id = ""
        if not terminal_id:
            terminal_id = uuid4().hex
            try:
                terminal_path.write_text(terminal_id)
                terminal_path.chmod(0o600)
            except OSError:
                _LOGGER.warning("Could not persist the add-on terminal ID")

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                return max(minimum, min(maximum, int(os.environ.get(name, default))))
            except (TypeError, ValueError):
                return default

        try:
            request_timeout = max(
                5.0, min(60.0, float(os.environ.get("IMOU_REQUEST_TIMEOUT", "15")))
            )
        except (TypeError, ValueError):
            request_timeout = 15
        return cls(
            account=os.environ.get("IMOU_ACCOUNT", "").strip(),
            password=os.environ.get("IMOU_PASSWORD", ""),
            terminal_id=terminal_id,
            request_timeout=request_timeout,
            poll_interval=integer("IMOU_POLL_INTERVAL", 30, 15, 300),
            max_properties=integer("IMOU_MAX_PROPERTIES", 1000, 10, 2000),
            snapshot_interval=integer("IMOU_SNAPSHOT_INTERVAL", 300, 0, 3600),
            publish_event_images=os.environ.get("IMOU_EVENT_IMAGES", "true")
            .strip()
            .casefold()
            in {"1", "true", "yes", "on"},
        )


def _decode_qr_image(value: str) -> bytes:
    encoded = value.strip()
    if encoded.startswith("data:") and "," in encoded:
        encoded = encoded.split(",", 1)[1]
    if len(encoded) > MAX_QR_IMAGE_BYTES * 2:
        raise ValueError("Dữ liệu ảnh QR quá lớn.")
    encoded += "=" * (-len(encoded) % 4)
    try:
        image = b64decode(encoded)
    except (BinasciiError, ValueError) as error:
        raise ValueError("Ảnh QR không hợp lệ.") from error
    if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > MAX_QR_IMAGE_BYTES:
        raise ValueError("Ảnh QR không hợp lệ.")
    return image


def _decode_captcha_image(value: str) -> tuple[bytes, str]:
    encoded = value.strip()
    declared_type = ""
    if encoded.startswith("data:") and "," in encoded:
        header, encoded = encoded.split(",", 1)
        declared_type = header.removeprefix("data:").split(";", 1)[0]
    encoded = "".join(encoded.split())
    if len(encoded) > MAX_CAPTCHA_IMAGE_BYTES * 2:
        raise ValueError("Dữ liệu ảnh CAPTCHA quá lớn.")
    encoded += "=" * (-len(encoded) % 4)
    try:
        image = b64decode(encoded, validate=True)
    except (BinasciiError, ValueError) as error:
        raise ValueError("Ảnh CAPTCHA không hợp lệ.") from error
    if len(image) > MAX_CAPTCHA_IMAGE_BYTES:
        raise ValueError("Ảnh CAPTCHA quá lớn.")
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        content_type = "image/png"
    elif image.startswith(b"\xff\xd8\xff"):
        content_type = "image/jpeg"
    elif image.startswith((b"GIF87a", b"GIF89a")):
        content_type = "image/gif"
    elif image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        content_type = "image/webp"
    else:
        raise ValueError("Định dạng ảnh CAPTCHA không được hỗ trợ.")
    if declared_type.startswith("image/") and declared_type != content_type:
        raise ValueError("Loại ảnh CAPTCHA không khớp dữ liệu.")
    return image, content_type


class ImouBridge:
    """Own login, discovery, cloud events, and MQTT state publishing."""

    def __init__(self, settings: BridgeSettings) -> None:
        self.settings = settings
        self.http_session: ClientSession | None = None
        self.api: ImouApiClient | None = None
        self.hass_mqtt = HomeAssistantMqtt(
            terminal_id=settings.terminal_id,
            on_connected=self.async_publish_all,
            on_command=self.async_handle_command,
        )
        self.stop = asyncio.Event()
        self.captcha_flow_lock = asyncio.Lock()
        self.captcha_lock = asyncio.Lock()
        self.captcha_complete = asyncio.Event()
        self.captcha_challenge: ImouCaptchaChallenge | None = None
        self.captcha_generation = 0
        self.geetest_fingerprint = sha256(
            f"geetest:{settings.terminal_id}".encode()
        ).hexdigest()
        self.geetest_first_seen = int(time.time() * 1000)
        self.two_step_flow_lock = asyncio.Lock()
        self.two_step_lock = asyncio.Lock()
        self.two_step_complete = asyncio.Event()
        self.two_step_required = False
        self.two_step_generation = 0
        self.two_step_code_sent = False
        self.two_step_delivery: str | None = None
        self.two_step_pending_captcha = False
        self.qr_flow_lock = asyncio.Lock()
        self.qr_lock = asyncio.Lock()
        self.qr_login: ImouQrLogin | None = None
        self.qr_generation = 0
        self.qr_poll_count = 0
        self.qr_last_poll: float | None = None
        self.qr_last_status: str | None = None
        self.qr_last_error_code: int | None = None
        self.devices: dict[str, ImouDevice] = {}
        self.plans: dict[str, DiscoveryPlan] = {}
        self.commands: dict[str, Any] = {}
        self.discovery_fingerprints: dict[str, str] = {}
        self.motion_state: dict[tuple[str, str], bool] = {}
        self.motion_clear_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self.human_state: dict[tuple[str, str], bool] = {}
        self.human_clear_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self.alarm_identities: set[str] = set()
        self.alarm_baseline_initialized = False
        self.alarm_auth_generation: int | None = None
        self.last_snapshot: dict[tuple[str, str], float] = {}
        self.status = "starting"
        self.status_message = "Đang khởi động add-on…"
        self.last_error: str | None = None
        self.last_login: float | None = None
        self.last_refresh: float | None = None
        self.cloud_connected = False
        self.auth_retry_blocked = False
        self._data_lock = asyncio.Lock()

    @property
    def authenticated(self) -> bool:
        """Return whether the Imou API currently has token credentials."""
        return self.api is not None and self.api.authenticated

    @property
    def entity_count(self) -> int:
        """Return the number of MQTT entities currently discovered."""
        return sum(len(plan.entities) for plan in self.plans.values())

    async def run(self) -> None:
        """Start HTTP, HA MQTT, and Imou cloud tasks."""
        if not self.settings.account or not self.settings.password:
            self.status = "configuration_required"
            self.status_message = "Nhập account/password trong cấu hình add-on."
        self.http_session = ClientSession(headers={"User-Agent": "ImouLifeBridge/1.0"})
        self.api = ImouApiClient(
            self.http_session,
            self.settings.account,
            self.settings.password,
            self.settings.terminal_id,
            request_timeout=self.settings.request_timeout,
            max_concurrent_requests=4,
        )
        runner = await self._start_web_server()
        mqtt_task = asyncio.create_task(self.hass_mqtt.run(self.stop))
        cloud_task = asyncio.create_task(self._cloud_loop())
        try:
            await self.stop.wait()
        finally:
            mqtt_task.cancel()
            cloud_task.cancel()
            await asyncio.gather(mqtt_task, cloud_task, return_exceptions=True)
            await runner.cleanup()
            await self.http_session.close()

    async def _cloud_loop(self) -> None:
        """Authenticate, refresh entities, and maintain Imou MQTT events."""
        assert self.api is not None
        while not self.stop.is_set():
            cloud_stop = asyncio.Event()
            realtime: ImouCloudMqttClient | None = None
            realtime_task: asyncio.Task[None] | None = None
            try:
                if not self.settings.account or not self.settings.password:
                    await self._wait_or_stop(60)
                    continue
                if self.auth_retry_blocked:
                    await self._wait_or_stop(60)
                    continue
                self.status = "logging_in"
                self.status_message = "Đang đăng nhập Imou Life…"
                self.last_error = None
                await self.api.async_authenticate(use_pc_client=False)
                self.auth_retry_blocked = False
                self.captcha_challenge = None
                self._clear_two_step_state()
                self.last_login = time.time()
                self.status = "discovering"
                self.status_message = "Đang lấy thiết bị và thing-model…"
                await self.async_refresh()
                realtime = ImouCloudMqttClient(self.api, self.async_handle_event)
                self.api.set_mqtt_request(realtime.async_request)
                realtime_task = asyncio.create_task(realtime.run(cloud_stop))
                self.status = "ready"
                self.status_message = "Đã đăng nhập và đang đồng bộ MQTT."
                while not self.stop.is_set():
                    await self._wait_or_stop(self.settings.poll_interval)
                    if self.stop.is_set():
                        break
                    await self.async_refresh()
                    self.cloud_connected = realtime.connected
            except ImouCaptchaRequired as error:
                await self._wait_for_captcha(error.challenge)
            except ImouTwoStepVerificationRequired:
                await self._wait_for_two_step()
            except ImouQrLoginRequired:
                await self._wait_for_qr_login()
            except ImouCredentialError as error:
                self._set_credential_failure(error, captcha_accepted=False)
                await self._wait_or_stop(60)
            except ImouAuthError as error:
                self.status = "invalid_auth"
                self.status_message = "Imou từ chối account/password."
                self.last_error = str(error)
                await self._wait_or_stop(60)
            except ImouConnectionError as error:
                self.status = "connection_error"
                self.status_message = "Không kết nối được Imou cloud."
                self.last_error = str(error)
                await self._wait_or_stop(30)
            except ImouApiError as error:
                self.status = "api_error"
                self.status_message = "Imou trả về lỗi API."
                self.last_error = str(error)
                await self._wait_or_stop(30)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.status = "error"
                self.status_message = "Add-on gặp lỗi không xác định."
                self.last_error = f"{type(error).__name__}: {error}"
                _LOGGER.exception("Bridge loop failed")
                await self._wait_or_stop(30)
            finally:
                if self.api is not None:
                    self.api.set_mqtt_request(None)
                cloud_stop.set()
                if realtime_task is not None:
                    realtime_task.cancel()
                    await asyncio.gather(realtime_task, return_exceptions=True)
                motion_tasks = tuple(self.motion_clear_tasks.values())
                self.motion_clear_tasks.clear()
                for task in motion_tasks:
                    task.cancel()
                if motion_tasks:
                    await asyncio.gather(*motion_tasks, return_exceptions=True)
                human_tasks = tuple(self.human_clear_tasks.values())
                self.human_clear_tasks.clear()
                for task in human_tasks:
                    task.cancel()
                if human_tasks:
                    await asyncio.gather(*human_tasks, return_exceptions=True)
                self.cloud_connected = False

    async def _wait_for_captcha(self, challenge: ImouCaptchaChallenge) -> None:
        """Expose the APK CAPTCHA flow and wait for browser verification."""
        assert self.api is not None
        async with self.captcha_flow_lock:
            if self.api.authenticated:
                return
            self.captcha_complete = asyncio.Event()
            try:
                async with self.captcha_lock:
                    await self._activate_captcha_locked(challenge)
            except (ImouApiError, ValueError) as error:
                self.captcha_challenge = None
                self.status = "captcha_failed"
                self.status_message = "Không khởi tạo được CAPTCHA từ Imou."
                self.last_error = str(error)
                await self._wait_or_stop(15)
                return
            while not self.stop.is_set() and not self.captcha_complete.is_set():
                await self._wait_or_stop(0.5)

    async def _activate_captcha_locked(self, challenge: ImouCaptchaChallenge) -> None:
        assert self.api is not None
        prepared = await self.api.async_prepare_captcha(challenge)
        self.captcha_challenge = prepared
        self.captcha_generation += 1
        self.last_error = None
        self.status = "captcha_required"
        if prepared.is_image:
            self.status_message = (
                "Nhập bốn ký tự trong ảnh CAPTCHA để tiếp tục đăng nhập."
            )
        else:
            self.status_message = (
                "Hoàn tất CAPTCHA hình ảnh GeeTest để tiếp tục đăng nhập."
            )
        _LOGGER.info(
            "Imou CAPTCHA challenge ready mode=%s generation=%s",
            "image" if prepared.is_image else "geetest4",
            self.captcha_generation,
        )

    async def _refresh_captcha_locked(self) -> None:
        assert self.api is not None
        try:
            await self.api.async_authenticate(force=True, use_pc_client=False)
        except ImouCaptchaRequired as error:
            await self._activate_captcha_locked(error.challenge)
            return
        except ImouTwoStepVerificationRequired:
            self._transition_from_captcha_to_two_step()
            return
        if not self.api.authenticated:
            raise ImouAuthError("Imou did not complete account login")
        self.captcha_challenge = None
        self.last_login = time.time()
        self.status = "logging_in"
        self.status_message = "Đăng nhập thành công, đang lấy thiết bị…"
        self.last_error = None
        if self.two_step_required:
            self._clear_two_step_state()
            self.two_step_complete.set()
        self.captcha_complete.set()

    async def async_refresh_captcha(self) -> None:
        """Request a fresh CAPTCHA without exposing its opaque tokens."""
        if self.captcha_challenge is None or self.api is None:
            raise ValueError("Không có CAPTCHA đang chờ.")
        async with self.captcha_lock:
            await self._refresh_captcha_locked()

    async def async_submit_captcha(
        self,
        generation: int,
        result: dict[str, Any],
    ) -> None:
        """Validate a browser result, then complete GetToken and Login."""
        assert self.api is not None
        async with self.captcha_lock:
            challenge = self.captcha_challenge
            if challenge is None:
                raise ValueError("CAPTCHA không còn hiệu lực.")
            if generation != self.captcha_generation:
                raise ValueError("CAPTCHA đã được làm mới; hãy tải lại trang.")
            self.status = "captcha_validating"
            self.status_message = "Đang gửi kết quả CAPTCHA tới Imou…"
            try:
                if challenge.is_image:
                    await self.api.async_validate_image_captcha(
                        challenge,
                        str(result.get("code") or ""),
                    )
                else:
                    await self.api.async_validate_geetest(challenge, result)
            except ImouTwoStepVerificationRequired:
                self._transition_from_captcha_to_two_step()
                return
            except ImouConnectionError as error:
                self.status = "captcha_connection_error"
                self.status_message = "Không gửi được CAPTCHA tới Imou."
                self.last_error = str(error)
                raise
            except ImouApiError as error:
                error_code = error.code
                try:
                    await self._refresh_captcha_locked()
                except (ImouApiError, ValueError) as refresh_error:
                    self.status = "captcha_failed"
                    self.status_message = "Imou từ chối CAPTCHA; không tạo được mã mới."
                    self.last_error = str(refresh_error)
                else:
                    if self.api.authenticated:
                        _LOGGER.info(
                            "Imou account login completed while refreshing after "
                            "CAPTCHA validation code=%s",
                            error_code,
                        )
                        return
                    if self.two_step_required:
                        _LOGGER.info(
                            "Imou CAPTCHA refresh advanced to two-step verification "
                            "after validation code=%s",
                            error_code,
                        )
                        return
                    self.status = "captcha_rejected"
                    self.status_message = (
                        "Imou từ chối kết quả CAPTCHA"
                        f"{f' (mã {error_code})' if error_code is not None else ''}. "
                        "CAPTCHA mới đã được tạo."
                    )
                    self.last_error = str(error)
                raise
            self.status = "captcha_accepted"
            self.status_message = "CAPTCHA hợp lệ; đang hoàn tất đăng nhập…"
            _LOGGER.info(
                "Imou CAPTCHA accepted mode=%s",
                "image" if challenge.is_image else "geetest4",
            )
            try:
                await self.api.async_authenticate(force=True, use_pc_client=False)
            except ImouCaptchaRequired as error:
                await self._activate_captcha_locked(error.challenge)
                self.status = "captcha_rejected"
                self.status_message = (
                    "Imou yêu cầu một CAPTCHA mới sau bước kiểm tra; hãy xác minh lại."
                )
                raise ImouApiError(
                    "Imou did not accept the completed CAPTCHA",
                    error.code,
                ) from error
            except ImouTwoStepVerificationRequired:
                self._transition_from_captcha_to_two_step()
                return
            except ImouCredentialError as error:
                self.captcha_challenge = None
                self._set_credential_failure(error, captcha_accepted=True)
                self.captcha_complete.set()
                raise
            except ImouAuthError as error:
                self.captcha_challenge = None
                self.auth_retry_blocked = True
                self.status = "invalid_auth"
                self.status_message = (
                    "CAPTCHA đã được chấp nhận, nhưng Imou không hoàn tất đăng nhập. "
                    "Kiểm tra account/password rồi khởi động lại add-on."
                )
                self.last_error = str(error)
                self.captcha_complete.set()
                raise
            if not self.api.authenticated:
                raise ImouAuthError("Imou did not complete account login")
            self.captcha_challenge = None
            self.last_error = None
            self.last_login = time.time()
            self.status = "logging_in"
            self.status_message = "Đăng nhập thành công, đang lấy thiết bị…"
            if self.two_step_required:
                self._clear_two_step_state()
                self.two_step_complete.set()
            self.captcha_complete.set()

    def _activate_two_step_state(self) -> None:
        """Enter the terminal-trust OTP state without exposing account details."""
        if self.two_step_required:
            return
        self.two_step_required = True
        self.two_step_generation += 1
        self.two_step_code_sent = False
        self.two_step_delivery = None
        self.two_step_pending_captcha = False
        self.two_step_complete = asyncio.Event()
        self.auth_retry_blocked = False
        self.status = "two_step_required"
        self.status_message = (
            "Imou yêu cầu xác minh hai bước cho thiết bị này. "
            "Đang gửi mã xác minh…"
        )
        self.last_error = None
        _LOGGER.info(
            "Imou two-step verification required generation=%s",
            self.two_step_generation,
        )

    def _clear_two_step_state(self) -> None:
        """Clear browser-visible OTP state after a successful login."""
        self.two_step_required = False
        self.two_step_code_sent = False
        self.two_step_delivery = None
        self.two_step_pending_captcha = False

    def _transition_from_captcha_to_two_step(self) -> None:
        """Complete the CAPTCHA page and continue with terminal verification."""
        self.captcha_challenge = None
        self._activate_two_step_state()
        self.two_step_pending_captcha = False
        self.status = "two_step_required"
        self.status_message = (
            "CAPTCHA đã được chấp nhận. Imou yêu cầu thêm mã xác minh sáu số."
        )
        self.last_error = None
        self.captcha_complete.set()

    async def _activate_two_step_captcha(
        self,
        challenge: ImouCaptchaChallenge,
    ) -> None:
        """Show a CAPTCHA required while requesting or granting the OTP."""
        self.two_step_pending_captcha = True
        self.captcha_complete = asyncio.Event()
        async with self.captcha_lock:
            await self._activate_captcha_locked(challenge)

    async def _wait_for_two_step_captcha(self) -> None:
        while (
            not self.stop.is_set()
            and self.captcha_challenge is not None
            and not self.captcha_complete.is_set()
        ):
            await self._wait_or_stop(0.5)

    async def _wait_for_two_step(self) -> None:
        """Request a GrantingCredit code and wait for browser submission."""
        assert self.api is not None
        async with self.two_step_flow_lock:
            if self.api.authenticated:
                return
            self._activate_two_step_state()
            while not self.stop.is_set() and not self.two_step_complete.is_set():
                if self.captcha_challenge is not None:
                    await self._wait_for_two_step_captcha()
                    if self.captcha_challenge is not None:
                        return
                    continue
                if not self.two_step_code_sent:
                    try:
                        await self.async_request_two_step_code()
                    except ImouCaptchaRequired:
                        await self._wait_for_two_step_captcha()
                        continue
                    except (ImouApiError, ValueError) as error:
                        self.status = "two_step_send_error"
                        self.status_message = (
                            "Không gửi được mã xác minh. Bấm Gửi lại mã trong Web UI."
                        )
                        self.last_error = str(error)
                break
        while not self.stop.is_set() and not self.two_step_complete.is_set():
            await self._wait_or_stop(0.5)

    async def async_request_two_step_code(self) -> None:
        """Send or resend the six-digit terminal verification code."""
        if self.api is None:
            raise ValueError("Imou API chưa sẵn sàng.")
        async with self.two_step_lock:
            self._activate_two_step_state()
            if self.captcha_challenge is not None:
                raise ValueError("Hãy hoàn tất CAPTCHA trước khi gửi mã xác minh.")
            self.status = "two_step_sending"
            self.status_message = "Đang yêu cầu Imou gửi mã xác minh…"
            self.last_error = None
            try:
                response = await self.api.async_request_two_step_code()
            except ImouCaptchaRequired as error:
                await self._activate_two_step_captcha(error.challenge)
                raise
            except ImouConnectionError as error:
                self.status = "two_step_send_error"
                self.status_message = "Không kết nối được Imou để gửi mã xác minh."
                self.last_error = str(error)
                raise
            except ImouApiError as error:
                self.status = "two_step_send_error"
                self.status_message = "Imou không gửi được mã xác minh."
                self.last_error = str(error)
                raise
            self.two_step_code_sent = True
            self.two_step_delivery = _two_step_delivery_text(response)
            self.status = "two_step_required"
            self.status_message = (
                f"Mã xác minh sáu số đã được gửi qua {self.two_step_delivery}."
            )
            _LOGGER.info(
                "Imou two-step verification code requested generation=%s",
                self.two_step_generation,
            )

    async def async_submit_two_step_code(
        self,
        generation: int,
        code: str,
    ) -> None:
        """Grant this terminal, then repeat GetToken and Login."""
        if self.api is None:
            raise ValueError("Imou API chưa sẵn sàng.")
        async with self.two_step_lock:
            if not self.two_step_required:
                raise ValueError("Không có xác minh hai bước đang chờ.")
            if generation != self.two_step_generation:
                raise ValueError("Phiên xác minh đã thay đổi; hãy tải lại trang.")
            if self.captcha_challenge is not None:
                raise ValueError("Hãy hoàn tất CAPTCHA trước khi nhập mã xác minh.")
            normalized_code = code.strip()
            if len(normalized_code) != 6 or not normalized_code.isdigit():
                raise ValueError("Mã xác minh phải gồm đúng sáu chữ số.")
            self.status = "two_step_validating"
            self.status_message = "Đang kiểm tra mã xác minh với Imou…"
            self.last_error = None
            try:
                await self.api.async_grant_two_step_code(normalized_code)
                await self.api.async_authenticate(force=True, use_pc_client=False)
            except ImouCaptchaRequired as error:
                await self._activate_two_step_captcha(error.challenge)
                raise
            except ImouTwoStepVerificationRequired as error:
                self.status = "two_step_rejected"
                self.status_message = (
                    "Imou chưa chấp nhận mã xác minh. Kiểm tra mã hoặc gửi lại mã mới."
                )
                self.last_error = str(error)
                raise
            except ImouCredentialError as error:
                self._clear_two_step_state()
                self._set_credential_failure(error, captcha_accepted=False)
                self.two_step_complete.set()
                raise
            except ImouConnectionError as error:
                self.status = "two_step_connection_error"
                self.status_message = "Không kết nối được Imou để kiểm tra mã xác minh."
                self.last_error = str(error)
                raise
            except ImouAuthError as error:
                self.status = "two_step_auth_error"
                self.status_message = (
                    "Mã đã được gửi nhưng Imou chưa hoàn tất đăng nhập. "
                    "Hãy gửi lại mã và thử lại."
                )
                self.last_error = str(error)
                raise
            except ImouApiError as error:
                self.status = "two_step_rejected"
                self.status_message = (
                    "Mã xác minh không đúng hoặc đã hết hạn. Hãy gửi lại mã mới."
                )
                self.last_error = str(error)
                raise
            if not self.api.authenticated:
                raise ImouAuthError("Imou did not complete account login")
            self.captcha_challenge = None
            self._clear_two_step_state()
            self.last_login = time.time()
            self.status = "logging_in"
            self.status_message = "Xác minh thành công, đang lấy thiết bị…"
            self.last_error = None
            self.two_step_complete.set()
            _LOGGER.info("Imou two-step verification completed")

    def _set_credential_failure(
        self,
        error: ImouCredentialError,
        *,
        captcha_accepted: bool,
    ) -> None:
        """Expose the APK's failNum meaning without retrying into a lockout."""
        prefix = "CAPTCHA đã được chấp nhận. " if captcha_accepted else ""
        failure_count = error.failure_count
        if failure_count is None or failure_count < 2:
            detail = "Imou báo account hoặc password không đúng."
        elif failure_count < 5:
            detail = f"Imou báo password đã sai {failure_count} lần."
        else:
            detail = (
                f"Imou báo tài khoản đã bị khóa sau {failure_count} lần "
                "đăng nhập sai."
            )
        if failure_count is not None and failure_count >= 5:
            action = (
                " Mở Imou Life hoặc đặt lại password, sau đó khởi động lại add-on."
            )
        else:
            action = (
                " Add-on đã dừng tự thử để tránh khóa tài khoản. Kiểm tra đăng nhập "
                "trong Imou Life, lưu lại cấu hình rồi khởi động lại add-on."
            )
        self.auth_retry_blocked = True
        self.status = "invalid_auth"
        self.status_message = prefix + detail + action
        self.last_error = str(error)

    async def _wait_for_qr_login(self) -> None:
        """Create and poll an official Imou Life login QR code."""
        assert self.api is not None
        async with self.qr_flow_lock:
            if self.api.authenticated:
                return
            try:
                await self._create_qr_login()
            except ImouApiError as error:
                self.qr_login = None
                self.status = "qr_failed"
                self.status_message = "Không tạo được mã QR đăng nhập từ Imou."
                self.last_error = str(error)
                await self._wait_or_stop(30)
                return

            deadline = time.monotonic() + QR_LOGIN_TTL_SECONDS
            poll_errors = 0
            post_scan_format_errors = 0
            post_scan_expire_polls = 0
            next_account_retry = 0.0
            first_poll = True
            while not self.stop.is_set() and time.monotonic() < deadline:
                if not first_poll:
                    await self._wait_or_stop(QR_LOGIN_POLL_SECONDS)
                first_poll = False
                if self.stop.is_set():
                    return
                current = self.qr_login
                if current is None:
                    return
                monotonic_now = time.monotonic()
                if (
                    current.status in {"scanned", "login"}
                    and monotonic_now >= next_account_retry
                ):
                    next_account_retry = monotonic_now + QR_LOGIN_ACCOUNT_RETRY_SECONDS
                    if await self._try_complete_qr_account_login():
                        return
                try:
                    result = await self.api.async_query_qr_login(
                        current.login_code,
                        recover_across_hosts=current.status in {"scanned", "login"},
                    )
                except ImouApiError as error:
                    self.qr_poll_count += 1
                    self.qr_last_poll = time.time()
                    self.qr_last_error_code = error.code
                    if (
                        error.code == QR_LOGIN_REQUEST_FORMAT_ERROR
                        and current.status in {"scanned", "login"}
                    ):
                        post_scan_format_errors += 1
                        self.status = "qr_finalizing"
                        self.status_message = (
                            "Imou đã nhận QR; đang chờ máy chủ hoàn tất phiên PC…"
                        )
                        if post_scan_format_errors == 1 or not (
                            post_scan_format_errors % 15
                        ):
                            _LOGGER.warning(
                                "Imou QR query is still propagating after scan "
                                "code=%s attempt=%s",
                                error.code,
                                post_scan_format_errors,
                            )
                        continue
                    poll_errors += 1
                    self.status = "qr_retrying"
                    self.status_message = "Đang thử đọc lại trạng thái QR từ Imou…"
                    _LOGGER.warning(
                        "Imou QR poll failed code=%s attempt=%s",
                        error.code,
                        poll_errors,
                    )
                    if poll_errors < QR_LOGIN_MAX_POLL_ERRORS:
                        continue
                    self.qr_login = None
                    self.status = "qr_failed"
                    self.status_message = "Imou không đọc được trạng thái mã QR."
                    self.last_error = str(error)
                    return
                if (
                    self.qr_login is None
                    or self.qr_login.login_code != current.login_code
                ):
                    continue

                poll_errors = 0
                post_scan_format_errors = 0
                self.qr_poll_count += 1
                self.qr_last_poll = time.time()
                self.qr_last_error_code = None
                effective_status = result.status
                if (
                    current.status in {"scanned", "login"}
                    and effective_status == "unscan"
                ):
                    effective_status = current.status
                if (
                    current.status in {"scanned", "login"}
                    and effective_status == "expire"
                ):
                    post_scan_expire_polls += 1
                    if post_scan_expire_polls <= QR_LOGIN_POST_SCAN_EXPIRE_GRACE_POLLS:
                        self.status = "qr_finalizing"
                        self.status_message = (
                            "Imou đã nhận QR; đang chờ máy chủ hoàn tất phiên PC…"
                        )
                        if post_scan_expire_polls == 1:
                            _LOGGER.warning(
                                "Imou QR temporarily reported expire after scan; "
                                "keeping the approved code during propagation"
                            )
                        continue
                else:
                    post_scan_expire_polls = 0
                if effective_status != self.qr_last_status:
                    _LOGGER.info(
                        "Imou QR status changed status=%s credentials_ready=%s",
                        effective_status,
                        result.credentials_ready,
                    )
                self.qr_last_status = effective_status
                self.qr_login = replace(current, status=effective_status)
                if effective_status == "unscan":
                    self.status = "qr_required"
                    self.status_message = (
                        "Quét QR bằng Imou Life. Trước khi xác nhận, bắt buộc tích "
                        "'Tự động đăng nhập trong 30 ngày tới'."
                    )
                    continue
                if effective_status == "scanned":
                    self.status = "qr_scanned"
                    self.status_message = (
                        "Đã quét QR. Hãy tích 'Tự động đăng nhập trong 30 ngày tới' "
                        "rồi mới bấm xác nhận."
                    )
                    continue
                if effective_status == "cancel":
                    self.qr_login = None
                    self.status = "qr_cancelled"
                    self.status_message = "Bạn đã hủy xác nhận QR trong Imou Life."
                    return
                if effective_status == "expire":
                    self.status = "qr_expired"
                    self.status_message = "Mã QR đã hết hạn, đang tạo mã mới…"
                    await self._create_qr_login()
                    deadline = time.monotonic() + QR_LOGIN_TTL_SECONDS
                    poll_errors = 0
                    post_scan_format_errors = 0
                    post_scan_expire_polls = 0
                    next_account_retry = 0.0
                    continue
                if effective_status != "login":
                    self.status = "qr_waiting"
                    self.status_message = "Đang chờ Imou Life xác nhận mã QR…"
                    continue
                if (
                    not result.credentials_ready
                    and result.support_pc_auto_login is False
                ):
                    _LOGGER.warning(
                        "Imou QR approved without PC auto-login; replacing QR"
                    )
                    await self._create_qr_login()
                    deadline = time.monotonic() + QR_LOGIN_TTL_SECONDS
                    poll_errors = 0
                    post_scan_format_errors = 0
                    next_account_retry = 0.0
                    self.status = "qr_auto_login_required"
                    self.status_message = (
                        "Imou không cấp token vì chưa bật 'Tự động đăng nhập trong "
                        "30 ngày tới'. Mã QR mới đã được tạo; hãy quét lại và tích ô "
                        "này trước khi xác nhận."
                    )
                    continue
                if not result.credentials_ready:
                    self.status = "qr_approval_waiting"
                    self.status_message = (
                        "QR đã được xác nhận; đang hoàn tất GetToken của Imou…"
                    )
                else:
                    self.status = "qr_authorizing"
                    self.status_message = (
                        "Đã xác nhận QR. Đang hoàn tất đăng nhập Imou…"
                    )
                authenticated = False
                try:
                    for attempt in range(3):
                        try:
                            await self.api.async_complete_qr_login(result)
                            authenticated = True
                            break
                        except ImouQrLoginRequired:
                            if attempt < 2:
                                await self._wait_or_stop(0.5)
                                continue
                            self.status = "qr_approval_waiting"
                            self.status_message = (
                                "QR đã được xác nhận; đang chờ GetToken của Imou…"
                            )
                except ImouApiError as error:
                    self.qr_login = None
                    self.status = "qr_failed"
                    self.status_message = "Imou từ chối token đăng nhập QR."
                    self.last_error = str(error)
                    return
                if not authenticated:
                    continue
                self.qr_login = None
                self.last_error = None
                self.status = "logging_in"
                self.status_message = "Đăng nhập QR thành công, đang lấy thiết bị…"
                return

            self.qr_login = None
            self.status = "qr_expired"
            self.status_message = "Mã QR đã hết hạn; add-on sẽ tạo mã mới."

    async def _try_complete_qr_account_login(self) -> bool:
        assert self.api is not None
        self.status = "qr_finalizing"
        self.status_message = "QR đã được quét; đang kiểm tra quyền đăng nhập…"
        try:
            await self.api.async_authenticate(
                force=True,
                expected_qr_required=True,
                use_pc_client=True,
            )
        except ImouQrLoginRequired:
            return False
        if not self.api.authenticated:
            return False
        _LOGGER.info("Imou QR authorization completed through account GetToken")
        self.qr_login = None
        self.last_error = None
        self.status = "logging_in"
        self.status_message = "Đăng nhập QR thành công, đang lấy thiết bị…"
        return True

    async def _create_qr_login(self) -> None:
        assert self.api is not None
        async with self.qr_lock:
            self.status = "qr_loading"
            self.status_message = "Đang tạo mã QR đăng nhập Imou Life…"
            qr_login = await self.api.async_create_qr_login(
                width=300,
                height=300,
            )
            self.qr_login = qr_login
            self.qr_generation += 1
            self.qr_poll_count = 0
            self.qr_last_poll = None
            self.qr_last_status = qr_login.status
            self.qr_last_error_code = None
            self.status = "qr_required"
            self.status_message = (
                "Quét QR bằng Imou Life. Trước khi xác nhận, bắt buộc tích "
                "'Tự động đăng nhập trong 30 ngày tới'."
            )

    async def async_refresh_qr_login(self) -> None:
        """Replace the current QR authorization request."""
        if self.qr_login is None or self.api is None:
            raise ValueError("Không có mã QR đang chờ.")
        await self._create_qr_login()

    async def async_refresh(self) -> None:
        """Discover devices, models, properties, entities, and states."""
        assert self.api is not None
        discovered = await self.api.async_list_devices()
        previous_devices = dict(self.devices)
        previous_models = {
            device.product_id: device.thing_model for device in self.devices.values()
        }

        async def enrich(device: ImouDevice) -> ImouDevice:
            model = previous_models.get(device.product_id)
            if model is None or not model.properties:
                try:
                    response = await self.api.async_query_model(device.product_id)
                    model = parse_thing_model(
                        response.get("modelJson") or {},
                        str(response.get("md5")) if response.get("md5") else None,
                    )
                except ImouApiError as error:
                    _LOGGER.warning(
                        "Thing-model unavailable for %s: %s", device.product_id, error
                    )
                    model = device.thing_model
            properties = model.exposed_properties(self.settings.max_properties)
            readable_properties = tuple(prop for prop in properties if prop.readable)
            refs = [prop.ref for prop in readable_properties]
            values = await self.api.async_get_properties(
                device.product_id,
                device.device_id,
                refs,
                group_control_flag=device.group_control_flag,
                channel_id=device.channels[0].channel_id if device.channels else None,
                fallback_identifiers={
                    prop.ref: prop.identifier for prop in readable_properties
                },
            )
            normalized = normalize_property_values(model.properties, values)
            previous = previous_devices.get(device.device_id)
            if previous is not None:
                normalized = {**previous.properties, **normalized}
            return device.with_thing_model(model).with_properties(normalized)

        enriched = await asyncio.gather(*(enrich(device) for device in discovered))
        async with self._data_lock:
            self.devices = {device.device_id: device for device in enriched}
            self.last_refresh = time.time()
        try:
            await self._async_poll_latest_alarms()
        except ImouAuthError:
            raise
        except ImouApiError as error:
            _LOGGER.debug(
                "Latest Imou alarm polling unavailable code=%s", error.code
            )
        await self.async_publish_all()

    async def _async_poll_latest_alarms(self) -> None:
        """Poll cloud alarms as a fallback for missing MQTT push events."""
        assert self.api is not None
        auth_generation = self.api.auth_generation
        if self.alarm_auth_generation != auth_generation:
            self.alarm_auth_generation = auth_generation
            self.alarm_identities.clear()
            self.alarm_baseline_initialized = False
        alarms = await self.api.async_get_latest_alarms(list(self.devices.values()))
        current_identities = {
            identity
            for alarm in alarms
            if (identity := alarm_identity(alarm))
        }
        if not self.alarm_baseline_initialized:
            self.alarm_identities.update(current_identities)
            self.alarm_baseline_initialized = True
            return
        for alarm in alarms:
            identity = alarm_identity(alarm)
            if not identity or identity in self.alarm_identities:
                continue
            await self.async_handle_event(alarm_to_realtime_event(alarm))
        self.alarm_identities.update(current_identities)
        if len(self.alarm_identities) > 2048:
            self.alarm_identities = set(tuple(self.alarm_identities)[-1024:])

    async def async_publish_all(self) -> None:
        """Publish retained MQTT Discovery and current state."""
        if not self.hass_mqtt.connected:
            return
        await self.hass_mqtt.publish(BRIDGE_AVAILABILITY, "online", retain=True)
        new_plans: dict[str, DiscoveryPlan] = {}
        new_commands: dict[str, Any] = {}
        new_fingerprints: dict[str, str] = {}
        for device in self.devices.values():
            plan = build_device_plan(
                device,
                max_properties=self.settings.max_properties,
                topic_prefix=self.settings.topic_prefix,
            )
            new_plans[device.device_id] = plan
            new_commands.update(plan.commands)
            for entity in plan.entities:
                fingerprint = json.dumps(
                    entity.config,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                new_fingerprints[entity.config_topic] = fingerprint
                if self.discovery_fingerprints.get(entity.config_topic) != fingerprint:
                    await self.hass_mqtt.publish_json(
                        entity.config_topic, entity.config, retain=True
                    )

        for topic in set(self.discovery_fingerprints) - set(new_fingerprints):
            await self.hass_mqtt.publish(topic, b"", retain=True)
        self.plans = new_plans
        self.commands = new_commands
        self.discovery_fingerprints = new_fingerprints

        for device in self.devices.values():
            device_key = topic_segment(device.device_id)
            await self.hass_mqtt.publish(
                f"{self.settings.topic_prefix}/{device_key}/availability",
                "online" if device.online is not False else "offline",
                retain=True,
            )
            for entity in self.plans[device.device_id].entities:
                await self._publish_entity_state(device, entity)
        await self._publish_periodic_images()

    async def _publish_periodic_images(self) -> None:
        """Publish bounded channel thumbnails at the configured interval."""
        if self.settings.snapshot_interval <= 0 or self.api is None:
            return
        now = time.monotonic()
        for device in self.devices.values():
            plan = self.plans.get(device.device_id)
            if plan is None:
                continue
            for channel in device.channels:
                if not channel.picture_url:
                    continue
                key = (device.device_id, channel.channel_id)
                if (
                    now - self.last_snapshot.get(key, 0)
                    < self.settings.snapshot_interval
                ):
                    continue
                self.last_snapshot[key] = now
                await self._publish_image(
                    device.device_id, channel.channel_id, channel.picture_url
                )

    async def _publish_entity_state(
        self, device: ImouDevice, entity: DiscoveryEntity
    ) -> None:
        if entity.state_topic is None or entity.kind == "camera":
            return
        if entity.kind == "property":
            value = device.properties.get(entity.property_id or "")
        elif entity.kind == "device_online":
            value = device.online is True
        elif entity.kind == "channel_online":
            value = next(
                (
                    channel.status
                    for channel in device.channels
                    if channel.channel_id == entity.channel_id
                ),
                None,
            )
        elif entity.kind == "motion":
            value = self.motion_state.get(
                (device.device_id, entity.channel_id or "-1"), False
            )
        elif entity.kind == "human":
            value = self.human_state.get(
                (device.device_id, entity.channel_id or "-1"), False
            )
        else:
            return
        await self.hass_mqtt.publish(
            entity.state_topic, encode_state(entity, value), retain=True
        )

    async def async_handle_event(self, event: ImouRealtimeEvent) -> None:
        """Apply cloud event properties and publish a sanitized event."""
        device = self.devices.get(event.device_id)
        if device is None and event.ap_id:
            device = self.devices.get(event.ap_id)
        if device is None and not event.device_id and len(self.devices) == 1:
            device = next(iter(self.devices.values()))
        if device is None:
            return
        normalized = normalize_property_values(
            device.thing_model.properties, event.properties
        )
        if normalized:
            updated = device.with_properties({**device.properties, **normalized})
            device = updated
            self.devices[device.device_id] = updated
            plan = self.plans.get(device.device_id)
            if plan:
                for entity in plan.entities:
                    if entity.kind == "property" and entity.property_id in normalized:
                        await self._publish_entity_state(updated, entity)
        channel_id = self._resolve_event_channel(device, event.channel_id)
        event = replace(
            event,
            device_id=device.device_id,
            channel_id=channel_id if channel_id is not None else event.channel_id,
        )
        motion = is_motion_event(event)
        human = is_human_event(event)
        if human is True:
            motion = True
        if channel_id is not None:
            key = (device.device_id, channel_id)
            if motion is not None:
                clear_task = self.motion_clear_tasks.pop(key, None)
                if clear_task is not None:
                    clear_task.cancel()
                self.motion_state[key] = motion
                if motion:
                    self.motion_clear_tasks[key] = asyncio.create_task(
                        self._clear_motion(key),
                        name=f"imou clear motion {device.device_id} {channel_id}",
                    )
            if human is not None:
                clear_task = self.human_clear_tasks.pop(key, None)
                if clear_task is not None:
                    clear_task.cancel()
                self.human_state[key] = human
                if human:
                    self.human_clear_tasks[key] = asyncio.create_task(
                        self._clear_human(key),
                        name=f"imou clear human {device.device_id} {channel_id}",
                    )
            plan = self.plans.get(device.device_id)
            if plan:
                for entity in plan.entities:
                    if (
                        entity.kind in {"motion", "human"}
                        and entity.channel_id == channel_id
                    ):
                        await self._publish_entity_state(
                            self.devices[device.device_id], entity
                        )
        event_data = event.event_data
        event_data["received_at"] = time.time()
        await self.hass_mqtt.publish_json(
            f"{self.settings.topic_prefix}/events", event_data
        )
        plan = self.plans.get(device.device_id)
        if plan:
            event_entity = next(
                (entity for entity in plan.entities if entity.kind == "event"), None
            )
            if event_entity and event_entity.state_topic:
                await self.hass_mqtt.publish_json(
                    event_entity.state_topic,
                    {
                        **event_data,
                        "event_type": normalize_event_type(event.event, event.alert),
                    },
                )
        if (
            self.settings.publish_event_images
            and event.image_url
            and channel_id is not None
        ):
            await self._publish_image(
                device.device_id, channel_id, event.image_url
            )

    async def _clear_motion(self, key: tuple[str, str]) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(MOTION_DURATION_SECONDS)
        except asyncio.CancelledError:
            return
        if self.motion_clear_tasks.get(key) is not current_task:
            return
        self.motion_clear_tasks.pop(key, None)
        self.motion_state[key] = False
        device = self.devices.get(key[0])
        plan = self.plans.get(key[0])
        if device and plan:
            for entity in plan.entities:
                if entity.kind == "motion" and entity.channel_id == key[1]:
                    await self._publish_entity_state(device, entity)

    async def _clear_human(self, key: tuple[str, str]) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(MOTION_DURATION_SECONDS)
        except asyncio.CancelledError:
            return
        if self.human_clear_tasks.get(key) is not current_task:
            return
        self.human_clear_tasks.pop(key, None)
        self.human_state[key] = False
        device = self.devices.get(key[0])
        plan = self.plans.get(key[0])
        if device and plan:
            for entity in plan.entities:
                if entity.kind == "human" and entity.channel_id == key[1]:
                    await self._publish_entity_state(device, entity)

    @staticmethod
    def _resolve_event_channel(device: ImouDevice, channel_id: str) -> str | None:
        channel_ids = {channel.channel_id for channel in device.channels}
        if channel_id in channel_ids:
            return channel_id
        if channel_id == "-1" and len(channel_ids) == 1:
            return next(iter(channel_ids))
        return None

    async def _publish_image(self, device_id: str, channel_id: str, url: str) -> None:
        assert self.api is not None
        try:
            image = await self.api.async_fetch_bytes(url)
        except ImouApiError as error:
            _LOGGER.debug("Could not fetch Imou event image: %s", error)
            return
        if len(image) > 1024 * 1024:
            _LOGGER.debug("Skipping Imou image larger than 1 MiB")
            return
        plan = self.plans.get(device_id)
        if not plan:
            return
        entity = next(
            (
                item
                for item in plan.entities
                if item.kind == "camera" and item.channel_id == channel_id
            ),
            None,
        )
        if entity and entity.state_topic:
            await self.hass_mqtt.publish(entity.state_topic, image, retain=True)

    async def async_handle_command(self, topic: str, payload: bytes) -> None:
        """Handle an MQTT command and refresh the affected device."""
        target = self.commands.get(topic)
        if target is None or self.api is None:
            return
        try:
            value = decode_command(target, payload)
            if target.kind == "service":
                await self.api.async_invoke_service(
                    target.product_id,
                    target.device_id,
                    target.ref,
                    value,
                    group_control_flag=(
                        self.devices[target.device_id].group_control_flag
                        if target.device_id in self.devices
                        else "0"
                    ),
                    channel_id=target.channel_id,
                )
            else:
                await self.api.async_set_properties(
                    target.product_id,
                    target.device_id,
                    {target.ref: value},
                    group_control_flag=(
                        self.devices[target.device_id].group_control_flag
                        if target.device_id in self.devices
                        else "0"
                    ),
                    channel_id=target.channel_id,
                    sync_local_cache=True,
                    fallback_identifiers={target.property_id or target.ref: value},
                )
            await self.async_refresh()
            if target.kind == "property" and target.device_id in self.devices:
                device = self.devices[target.device_id]
                property_id = target.property_id or target.ref
                if device.properties.get(property_id) != value:
                    updated_properties = dict(device.properties)
                    updated_properties[property_id] = value
                    updated = device.with_properties(updated_properties)
                    self.devices[target.device_id] = updated
                    plan = self.plans.get(target.device_id)
                    if plan is not None:
                        entity = next(
                            (
                                item
                                for item in plan.entities
                                if item.kind == "property"
                                and item.property_id == property_id
                            ),
                            None,
                        )
                        if entity is not None:
                            await self._publish_entity_state(updated, entity)
        except ImouQrLoginRequired:
            await self._wait_for_qr_login()
        except (ImouApiError, ValueError) as error:
            self.last_error = str(error)
            await self.hass_mqtt.publish_json(
                f"{self.settings.topic_prefix}/errors",
                {"topic": topic, "error": str(error), "time": time.time()},
            )

    def status_payload(self) -> dict[str, Any]:
        """Return UI-safe status without account, password, or tokens."""
        alarm_diagnostic = getattr(self.api, "latest_alarm_diagnostic", None)
        devices = []
        for device in self.devices.values():
            plan = self.plans.get(device.device_id)
            entities = []
            if plan:
                for entity in plan.entities:
                    state = self._status_entity_value(device, entity)
                    entities.append(
                        {
                            "component": entity.component,
                            "name": str(entity.config.get("name") or entity.object_id),
                            "unique_id": str(entity.config.get("unique_id") or ""),
                            "state": state,
                        }
                    )
            devices.append(
                {
                    "name": device.name,
                    "model": device.model or device.product_id,
                    "online": device.online,
                    "entity_count": len(entities),
                    "entities": entities,
                }
            )
        return {
            "status": self.status,
            "message": self.status_message,
            "authenticated": self.authenticated,
            "ha_mqtt_connected": self.hass_mqtt.connected,
            "imou_mqtt_connected": self.cloud_connected,
            "alarm_poll": (
                alarm_diagnostic()
                if callable(alarm_diagnostic)
                else "not_requested"
            ),
            "device_count": len(self.devices),
            "entity_count": self.entity_count,
            "last_login": self.last_login,
            "last_refresh": self.last_refresh,
            "last_error": self.last_error,
            "auth_retry_blocked": self.auth_retry_blocked,
            "captcha_required": self.captcha_challenge is not None,
            "captcha_mode": (
                "image"
                if self.captcha_challenge is not None
                and self.captcha_challenge.is_image
                else "geetest4"
                if self.captcha_challenge is not None
                else None
            ),
            "captcha_generation": self.captcha_generation,
            "two_step_required": self.two_step_required,
            "two_step_generation": self.two_step_generation,
            "two_step_code_sent": self.two_step_code_sent,
            "two_step_delivery": self.two_step_delivery,
            "two_step_complete": self.two_step_complete.is_set(),
            "qr_login_required": self.qr_login is not None,
            "qr_status": self.qr_login.status if self.qr_login is not None else None,
            "qr_generation": self.qr_generation,
            "qr_poll_count": self.qr_poll_count,
            "qr_last_poll": self.qr_last_poll,
            "qr_last_error_code": self.qr_last_error_code,
            "devices": devices,
        }

    def _status_entity_value(
        self, device: ImouDevice, entity: DiscoveryEntity
    ) -> str | None:
        if entity.kind == "property":
            return encode_state(entity, device.properties.get(entity.property_id or ""))
        if entity.kind == "device_online":
            return encode_state(entity, device.online is True)
        if entity.kind == "channel_online":
            value = next(
                (
                    channel.status
                    for channel in device.channels
                    if channel.channel_id == entity.channel_id
                ),
                None,
            )
            return encode_state(entity, value)
        if entity.kind == "motion":
            return encode_state(
                entity,
                self.motion_state.get(
                    (device.device_id, entity.channel_id or "-1"), False
                ),
            )
        if entity.kind == "human":
            return encode_state(
                entity,
                self.human_state.get(
                    (device.device_id, entity.channel_id or "-1"), False
                ),
            )
        return None

    async def _start_web_server(self) -> web.AppRunner:
        app = web.Application(client_max_size=CAPTCHA_SUBMIT_MAX_BYTES)
        app["bridge"] = self
        app.router.add_get("/", _web_index)
        app.router.add_get("/api/status", _web_status)
        app.router.add_get("/api/captcha/gl4.js", _web_captcha_script)
        app.router.add_get("/api/captcha/image", _web_captcha_image)
        app.router.add_post("/api/captcha", _web_captcha_submit)
        app.router.add_post("/api/captcha/refresh", _web_captcha_refresh)
        app.router.add_post("/api/two-step/request", _web_two_step_request)
        app.router.add_post("/api/two-step", _web_two_step_submit)
        app.router.add_get("/api/qr/image", _web_qr_image)
        app.router.add_post("/api/qr/refresh", _web_qr_refresh)
        runner = web.AppRunner(app, access_log=_LOGGER)
        await runner.setup()
        site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
        await site.start()
        return runner

    @staticmethod
    async def _wait_or_stop(delay: float) -> None:
        await asyncio.sleep(delay)


def _allowed_ingress(request: web.Request) -> bool:
    if request.remote == INGRESS_PROXY_ADDRESS:
        return True
    return os.environ.get("IMOU_BRIDGE_ALLOW_LOCALHOST") == "1" and request.remote in {
        "127.0.0.1",
        "::1",
    }


def _no_store() -> dict[str, str]:
    return {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _security_headers() -> dict[str, str]:
    return {
        **_no_store(),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "SAMEORIGIN",
        "Content-Security-Policy": (
            "default-src 'none'; base-uri 'self'; connect-src 'self'; "
            "img-src 'self' data:; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'"
        ),
    }


def _captcha_security_headers() -> dict[str, str]:
    return {
        **_no_store(),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "SAMEORIGIN",
        "Content-Security-Policy": (
            "default-src 'none'; base-uri 'self'; connect-src 'self' https: wss:; "
            "font-src data: https:; frame-src https:; img-src 'self' data: blob: "
            "https:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: https:; "
            "style-src 'self' 'unsafe-inline' https:; worker-src blob:"
        ),
    }


def _two_step_page(
    base_path: str,
    generation: int = 0,
    delivery: str | None = None,
) -> str:
    """Render the account-safe six-digit terminal verification page."""
    safe_delivery = html.escape(
        delivery or "kênh bảo mật đã liên kết",
        quote=True,
    )
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="{html.escape(base_path, quote=True)}"><title>Xác minh hai bước Imou Life</title>
<style>:root{{color-scheme:light dark;font-family:system-ui,sans-serif}}body{{padding:28px;text-align:center}}main{{max-width:560px;margin:auto}}button{{border:0;border-radius:8px;padding:12px 20px;font:inherit;font-weight:600;background:#687078;color:#fff;cursor:pointer}}button:disabled{{cursor:wait;opacity:.55}}#status{{white-space:pre-wrap;min-height:52px;margin:20px 0}}form{{display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap}}input{{font:inherit;font-size:1.5rem;letter-spacing:.35rem;text-align:center;width:12rem;padding:12px;border:1px solid #777;border-radius:8px}}.hint{{font-size:.9rem;opacity:.75}}</style></head>
<body><main><h2>Xác minh Imou Life</h2>
<p>Imou yêu cầu xác minh thiết bị trước khi cho phép add-on đăng nhập.</p>
<p>Mã sáu số đã được gửi qua <strong id="delivery">{safe_delivery}</strong>.</p>
<form id="two-step-form"><input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" minlength="6" maxlength="6" pattern="[0-9]{{6}}" required aria-label="Mã xác minh sáu chữ số"><button id="submit" type="submit">Xác minh</button></form>
<p><button id="resend" type="button">Gửi lại mã</button></p>
<div id="status" role="status" aria-live="polite">Nhập mã xác minh sáu số từ Imou.</div>
<p class="hint">Account, password, mã OTP và token chỉ được xử lý trong add-on; không được đưa vào URL hoặc MQTT.</p>
<script>
const generation = {generation};
const status = document.getElementById("status");
const code = document.getElementById("code");
const submit = document.getElementById("submit");
const resend = document.getElementById("resend");
async function refreshStatus() {{
  try {{
    const response = await fetch("api/status", {{cache:"no-store"}});
    const data = await response.json();
    if (data.captcha_required || !data.two_step_required) {{ window.location.reload(); return; }}
    if (data.two_step_generation !== generation) {{ window.location.reload(); return; }}
    if (data.two_step_delivery) document.getElementById("delivery").textContent = data.two_step_delivery;
    status.textContent = data.message || "Nhập mã xác minh sáu số từ Imou.";
  }} catch (error) {{ status.textContent = "Không đọc được trạng thái xác minh từ add-on."; }}
}}
document.getElementById("two-step-form").addEventListener("submit", async function(event) {{
  event.preventDefault();
  const value = code.value.trim();
  if (!/^[0-9]{{6}}$/.test(value)) {{ status.textContent = "Mã xác minh phải gồm đúng sáu chữ số."; return; }}
  submit.disabled = true; resend.disabled = true; status.textContent = "Đang kiểm tra mã xác minh với Imou…";
  try {{
    const response = await fetch("api/two-step", {{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{generation: generation, code: value}})}});
    const data = await response.json();
    if (data.captcha_required) {{ window.location.reload(); return; }}
    if (!response.ok || !data.ok) throw new Error(data.error || "Imou không chấp nhận mã xác minh.");
    status.textContent = "Xác minh thành công. Đang lấy thiết bị…";
  }} catch (error) {{ status.textContent = error.message; submit.disabled = false; resend.disabled = false; }}
}});
resend.addEventListener("click", async function() {{
  resend.disabled = true; submit.disabled = true; status.textContent = "Đang yêu cầu mã mới…";
  try {{
    const response = await fetch("api/two-step/request", {{method:"POST"}});
    const data = await response.json();
    if (data.captcha_required) {{ window.location.reload(); return; }}
    if (!response.ok || !data.ok) throw new Error(data.error || "Không gửi được mã mới.");
    if (data.delivery) document.getElementById("delivery").textContent = data.delivery;
    status.textContent = "Mã mới đã được gửi. Nhập mã sáu số mới nhất.";
  }} catch (error) {{ status.textContent = error.message; }}
  finally {{ submit.disabled = false; resend.disabled = false; }}
}});
refreshStatus(); setInterval(refreshStatus, 2000);
</script></main></body></html>"""


def _qr_page(base_path: str, generation: int = 0) -> str:
    """Render the official Imou Life QR authorization page."""
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="{html.escape(base_path, quote=True)}"><title>Imou Life QR Login</title>
<style>:root{{color-scheme:light dark;font-family:system-ui,sans-serif}}body{{padding:24px;text-align:center}}main{{max-width:560px;margin:auto}}ol{{display:inline-block;text-align:left;line-height:1.6}}img{{width:300px;max-width:100%;height:auto;image-rendering:auto;border:12px solid #fff;border-radius:12px;background:#fff}}button{{border:0;border-radius:8px;padding:12px 20px;font:inherit;font-weight:600;background:#687078;color:#fff;cursor:pointer}}button:disabled{{cursor:wait;opacity:.55}}#status{{white-space:pre-wrap;min-height:48px;margin:20px 0}}.hint{{font-size:.9rem;opacity:.75}}</style></head>
<body><main><h2>Cho phép Imou Life Bridge</h2>
<ol><li>Mở ứng dụng <strong>Imou Life</strong> trên điện thoại đã đăng nhập.</li><li>Mở chức năng quét QR trong ứng dụng.</li><li>Quét mã bên dưới.</li><li><strong>Bắt buộc tích “Tự động đăng nhập trong 30 ngày tới”</strong>, sau đó mới bấm xác nhận đăng nhập.</li></ol>
<p><img id="qr-image" src="api/qr/image?g={generation}" alt="Mã QR đăng nhập Imou Life"></p>
<button id="qr-refresh" type="button">Tạo mã QR mới</button>
<div id="status" role="status" aria-live="polite">Đang chờ quét mã QR…</div>
<p class="hint">Add-on không hiển thị hoặc gửi account, password, token hay mã đăng nhập vào trình duyệt.</p>
<script>
const status = document.getElementById("status");
const image = document.getElementById("qr-image");
const refresh = document.getElementById("qr-refresh");
let qrGeneration = {generation};
async function refreshStatus() {{
  try {{
    const response = await fetch("api/status", {{cache:"no-store"}});
    const result = await response.json();
    if (!result.qr_login_required) {{ window.location.reload(); return; }}
    if (result.qr_generation !== qrGeneration) {{
      qrGeneration = result.qr_generation;
      image.src = "api/qr/image?g=" + qrGeneration + "&t=" + Date.now();
      status.textContent = "Mã QR đã được làm mới. Hãy quét mã mới này.";
      return;
    }}
    if (result.qr_status === "scanned") status.textContent = "Đã quét QR. Hãy tích 'Tự động đăng nhập trong 30 ngày tới' rồi mới bấm xác nhận.";
    else if (result.qr_status === "login") status.textContent = "Đã xác nhận. Add-on đang hoàn tất đăng nhập…";
    else status.textContent = result.message || "Đang chờ quét mã QR…";
  }} catch (error) {{ status.textContent = "Không đọc được trạng thái QR từ add-on."; }}
}}
refresh.addEventListener("click", async function() {{
  refresh.disabled = true; status.textContent = "Đang tạo mã QR mới…";
  try {{
    const response = await fetch("api/qr/refresh", {{method:"POST"}});
    const result = await response.json();
    if (!response.ok || !result.ok) {{ status.textContent = result.error || "Không tạo được mã QR mới."; return; }}
    await refreshStatus();
  }} catch (error) {{
    status.textContent = "Không tạo được mã QR mới.";
  }}
  finally {{ refresh.disabled = false; }}
}});
refreshStatus(); setInterval(refreshStatus, 2000);
</script></main></body></html>"""


async def _web_index(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    bridge: ImouBridge = request.app["bridge"]
    ingress_path = request.headers.get("X-Ingress-Path", "")
    base_path = f"{ingress_path.rstrip('/')}/" if ingress_path else "./"
    if bridge.captcha_challenge is not None:
        body = render_captcha_page(
            base_path,
            bridge.captcha_challenge,
            bridge.captcha_generation,
            bridge.geetest_fingerprint,
            bridge.geetest_first_seen,
        )
        return web.Response(
            text=body,
            content_type="text/html",
            headers=_captcha_security_headers(),
        )
    if bridge.two_step_required:
        body = _two_step_page(
            base_path,
            bridge.two_step_generation,
            bridge.two_step_delivery,
        )
        return web.Response(
            text=body,
            content_type="text/html",
            headers=_security_headers(),
        )
    if bridge.qr_login is None:
        body = f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="{html.escape(base_path, quote=True)}"><title>Imou Life Bridge</title>
<style>:root{{color-scheme:light dark;font-family:system-ui,sans-serif}}body{{padding:28px;text-align:center}}main{{max-width:900px;margin:auto}}#status{{white-space:pre-wrap;min-height:80px}}.device{{text-align:left;border:1px solid #777;border-radius:8px;padding:12px;margin:12px 0}}.entity{{display:inline-block;margin:3px 6px 3px 0;padding:3px 6px;border-radius:4px;background:#7772;font-size:.85rem}}</style></head>
<body><main><h2>Imou Life Bridge</h2><p id="status">Đang đọc trạng thái…</p>
<p>Add-on tự đăng nhập Imou và tạo thực thể MQTT Discovery cho Home Assistant.</p><div id="devices"></div>
<script>
function renderDevices(data) {{
  const root = document.getElementById("devices"); root.textContent = "";
  for (const device of data.devices || []) {{
    const card = document.createElement("section"); card.className = "device";
    const heading = document.createElement("h3"); heading.textContent = device.name + " (" + (device.model || "Imou") + ")"; card.appendChild(heading);
    const summary = document.createElement("p"); summary.textContent = "Online: " + device.online + " · Thực thể: " + device.entity_count; card.appendChild(summary);
    for (const entity of device.entities || []) {{ const item = document.createElement("span"); item.className = "entity"; item.textContent = entity.component + ": " + entity.name + " = " + entity.state; card.appendChild(item); }}
    root.appendChild(card);
  }}
}}
async function refresh() {{
  try {{ const response = await fetch("api/status", {{cache:"no-store"}}); const data = await response.json();
    document.getElementById("status").textContent = data.message + "\\n\\nThiết bị: " + data.device_count + "\\nThực thể: " + data.entity_count;
    renderDevices(data);
    if (data.captcha_required || data.two_step_required || data.qr_login_required) window.location.reload();
  }} catch (error) {{ document.getElementById("status").textContent = "Không đọc được trạng thái add-on."; }}
}}
refresh(); setInterval(refresh, 4000);
</script></main></body></html>"""
        return web.Response(
            text=body, content_type="text/html", headers=_security_headers()
        )
    body = _qr_page(base_path, bridge.qr_generation)
    return web.Response(
        text=body, content_type="text/html", headers=_security_headers()
    )


async def _web_captcha_script(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    try:
        script = GEETEST_ASSET_PATH.read_bytes()
    except OSError:
        return web.Response(
            text="GeeTest asset is missing.",
            status=500,
            content_type="text/plain",
            headers=_no_store(),
        )
    return web.Response(
        body=script,
        content_type="application/javascript",
        headers={**_no_store(), "X-Content-Type-Options": "nosniff"},
    )


async def _web_captcha_image(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    bridge: ImouBridge = request.app["bridge"]
    challenge = bridge.captcha_challenge
    if challenge is None or not challenge.is_image:
        raise web.HTTPNotFound()
    try:
        requested_generation = int(request.query.get("g", "-1"))
    except ValueError:
        raise web.HTTPNotFound() from None
    if requested_generation != bridge.captcha_generation:
        raise web.HTTPNotFound()
    try:
        image, content_type = _decode_captcha_image(challenge.image)
    except ValueError as error:
        return web.Response(
            text=str(error),
            status=422,
            content_type="text/plain",
            headers=_security_headers(),
        )
    return web.Response(
        body=image,
        content_type=content_type,
        headers=_security_headers(),
    )


async def _web_captcha_submit(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    bridge: ImouBridge = request.app["bridge"]
    try:
        raw = await request.read()
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return web.json_response(
            {"ok": False, "error": "Dữ liệu CAPTCHA không hợp lệ."},
            status=400,
            headers=_no_store(),
        )
    if not isinstance(payload, dict):
        return web.json_response(
            {"ok": False, "error": "Dữ liệu CAPTCHA không hợp lệ."},
            status=400,
            headers=_no_store(),
        )
    generation_value = payload.pop("generation", None)
    if isinstance(generation_value, bool):
        generation_value = None
    try:
        generation = int(generation_value)
    except (TypeError, ValueError):
        return web.json_response(
            {"ok": False, "error": "Thiếu thế hệ CAPTCHA."},
            status=400,
            headers=_no_store(),
        )
    try:
        await bridge.async_submit_captcha(generation, payload)
    except ValueError as error:
        return web.json_response(
            {
                "ok": False,
                "error": str(error),
                "generation": bridge.captcha_generation,
            },
            status=400,
            headers=_no_store(),
        )
    except ImouConnectionError:
        return web.json_response(
            {
                "ok": False,
                "error": "Không kết nối được Imou để kiểm tra CAPTCHA.",
                "generation": bridge.captcha_generation,
            },
            status=502,
            headers=_no_store(),
        )
    except ImouCredentialError:
        return web.json_response(
            {
                "ok": False,
                "error": bridge.status_message,
                "generation": bridge.captcha_generation,
            },
            status=401,
            headers=_no_store(),
        )
    except ImouAuthError:
        return web.json_response(
            {
                "ok": False,
                "error": bridge.status_message,
                "generation": bridge.captcha_generation,
            },
            status=401,
            headers=_no_store(),
        )
    except ImouApiError as error:
        code = f" (mã {error.code})" if error.code is not None else ""
        return web.json_response(
            {
                "ok": False,
                "error": f"Imou từ chối kết quả CAPTCHA{code}.",
                "generation": bridge.captcha_generation,
            },
            status=400,
            headers=_no_store(),
        )
    return web.json_response(
        {"ok": True, "generation": bridge.captcha_generation},
        headers=_no_store(),
    )


async def _web_captcha_refresh(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    bridge: ImouBridge = request.app["bridge"]
    try:
        await bridge.async_refresh_captcha()
    except ValueError as error:
        return web.json_response(
            {"ok": False, "error": str(error)},
            status=400,
            headers=_no_store(),
        )
    except ImouConnectionError:
        return web.json_response(
            {"ok": False, "error": "Không kết nối được Imou để tạo CAPTCHA mới."},
            status=502,
            headers=_no_store(),
        )
    except ImouApiError as error:
        code = f" (mã {error.code})" if error.code is not None else ""
        return web.json_response(
            {"ok": False, "error": f"Không tạo được CAPTCHA mới{code}."},
            status=502,
            headers=_no_store(),
        )
    return web.json_response(
        {"ok": True, "generation": bridge.captcha_generation},
        headers=_no_store(),
    )


async def _web_two_step_request(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    bridge: ImouBridge = request.app["bridge"]
    try:
        await bridge.async_request_two_step_code()
    except ImouCaptchaRequired:
        return web.json_response(
            {
                "ok": False,
                "captcha_required": True,
                "error": "Hãy hoàn tất CAPTCHA để Imou gửi mã xác minh.",
            },
            status=409,
            headers=_no_store(),
        )
    except ValueError as error:
        return web.json_response(
            {"ok": False, "error": str(error)},
            status=400,
            headers=_no_store(),
        )
    except ImouConnectionError:
        return web.json_response(
            {"ok": False, "error": "Không kết nối được Imou để gửi mã xác minh."},
            status=502,
            headers=_no_store(),
        )
    except ImouApiError as error:
        code = f" (mã {error.code})" if error.code is not None else ""
        return web.json_response(
            {"ok": False, "error": f"Imou không gửi được mã xác minh{code}."},
            status=400,
            headers=_no_store(),
        )
    return web.json_response(
        {
            "ok": True,
            "generation": bridge.two_step_generation,
            "delivery": bridge.two_step_delivery,
        },
        headers=_no_store(),
    )


async def _web_two_step_submit(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    bridge: ImouBridge = request.app["bridge"]
    try:
        raw = await request.read()
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return web.json_response(
            {"ok": False, "error": "Dữ liệu xác minh không hợp lệ."},
            status=400,
            headers=_no_store(),
        )
    if not isinstance(payload, dict):
        return web.json_response(
            {"ok": False, "error": "Dữ liệu xác minh không hợp lệ."},
            status=400,
            headers=_no_store(),
        )
    generation_value = payload.get("generation")
    if isinstance(generation_value, bool):
        generation_value = None
    try:
        generation = int(generation_value)
    except (TypeError, ValueError):
        return web.json_response(
            {"ok": False, "error": "Thiếu phiên xác minh hai bước."},
            status=400,
            headers=_no_store(),
        )
    code = payload.get("code")
    if not isinstance(code, str):
        return web.json_response(
            {"ok": False, "error": "Thiếu mã xác minh sáu số."},
            status=400,
            headers=_no_store(),
        )
    try:
        await bridge.async_submit_two_step_code(generation, code)
    except ImouCaptchaRequired:
        return web.json_response(
            {
                "ok": False,
                "captcha_required": True,
                "error": "Hãy hoàn tất CAPTCHA rồi nhập lại mã xác minh.",
            },
            status=409,
            headers=_no_store(),
        )
    except ValueError as error:
        return web.json_response(
            {
                "ok": False,
                "error": str(error),
                "generation": bridge.two_step_generation,
            },
            status=400,
            headers=_no_store(),
        )
    except ImouTwoStepVerificationRequired:
        return web.json_response(
            {
                "ok": False,
                "error": bridge.status_message,
                "generation": bridge.two_step_generation,
            },
            status=400,
            headers=_no_store(),
        )
    except ImouCredentialError:
        return web.json_response(
            {"ok": False, "error": bridge.status_message},
            status=401,
            headers=_no_store(),
        )
    except ImouConnectionError:
        return web.json_response(
            {"ok": False, "error": bridge.status_message},
            status=502,
            headers=_no_store(),
        )
    except ImouAuthError:
        return web.json_response(
            {"ok": False, "error": bridge.status_message},
            status=401,
            headers=_no_store(),
        )
    except ImouApiError as error:
        error_code = f" (mã {error.code})" if error.code is not None else ""
        return web.json_response(
            {
                "ok": False,
                "error": f"Imou không chấp nhận mã xác minh{error_code}.",
                "generation": bridge.two_step_generation,
            },
            status=400,
            headers=_no_store(),
        )
    return web.json_response(
        {
            "ok": True,
            "generation": bridge.two_step_generation,
            "authenticated": bridge.authenticated,
        },
        headers=_no_store(),
    )


async def _web_qr_image(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    bridge: ImouBridge = request.app["bridge"]
    qr_login = bridge.qr_login
    if qr_login is None:
        raise web.HTTPNotFound()
    try:
        image = _decode_qr_image(qr_login.image)
    except ValueError as error:
        return web.Response(
            text=str(error),
            status=422,
            content_type="text/plain",
            headers=_security_headers(),
        )
    return web.Response(
        body=image,
        content_type="image/png",
        headers=_security_headers(),
    )


async def _web_qr_refresh(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    bridge: ImouBridge = request.app["bridge"]
    try:
        await bridge.async_refresh_qr_login()
    except ValueError as error:
        return web.json_response(
            {"ok": False, "error": str(error)}, status=400, headers=_no_store()
        )
    except ImouApiError as error:
        code = f" (mã {error.code})" if error.code is not None else ""
        return web.json_response(
            {"ok": False, "error": f"Không tạo được mã QR mới{code}."},
            status=502,
            headers=_no_store(),
        )
    return web.json_response({"ok": True}, headers=_no_store())


async def _web_status(request: web.Request) -> web.Response:
    if not _allowed_ingress(request):
        raise web.HTTPForbidden()
    bridge: ImouBridge = request.app["bridge"]
    return web.json_response(bridge.status_payload(), headers=_no_store())


def main() -> None:
    """Run the bridge with a signal-aware asyncio loop."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bridge = ImouBridge(BridgeSettings.from_environment())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def stop() -> None:
        bridge.stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        loop.run_until_complete(bridge.run())
    finally:
        loop.close()
