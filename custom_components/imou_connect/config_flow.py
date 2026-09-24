"""Config flow for the Imou Life integration."""

from __future__ import annotations

import time
from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import (
    CAPTCHA_REJECTED_CODE,
    ImouApiClient,
    ImouApiError,
    ImouAuthError,
    ImouCaptchaChallenge,
    ImouCaptchaRequired,
    ImouConnectionError,
    ImouCredentialError,
    ImouQrLoginRequired,
    ImouTwoStepVerificationRequired,
    normalize_login_account,
)
from .captcha import (
    async_register_captcha_views,
    async_set_captcha_session,
    async_update_captcha_session,
    captcha_url,
)
from .media import configured_local_cameras, local_camera_key, normalize_local_camera
from .const import (
    CONF_CAMERA_ID,
    CONF_LOCAL_CAMERAS,
    CONF_LOCAL_PASSWORD,
    CONF_LOCAL_USERNAME,
    CONF_ONVIF_PORT,
    CONF_ONVIF_PROFILE,
    CONF_ONVIF_PTZ,
    CONF_REMOVE_CAMERA,
    CONF_RTSP_PATH,
    CONF_RTSP_PORT,
    CONF_VALIDATE_STREAM,
    CAPTCHA_RESUME_AUTHENTICATE,
    CAPTCHA_RESUME_GRANT_OTP,
    CAPTCHA_RESUME_SEND_OTP,
    CONF_ACCOUNT,
    CONF_MAX_CONCURRENT_REQUESTS,
    CONF_MAX_PROPERTIES,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_REQUEST_TIMEOUT,
    CONF_RESEND_CODE,
    CONF_TERMINAL_ID,
    CONF_LOCAL_HOST,
    CONF_VALID_CODE,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    DEFAULT_MAX_PROPERTIES,
    DEFAULT_ONVIF_PORT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_REQUEST_TIMEOUT,
    DOMAIN,
)


def _account_unique_id(account: str) -> str:
    normalized, area_code = normalize_login_account(account)
    return sha256(f"{area_code}:{normalized.casefold()}".encode()).hexdigest()


def _account_title(account: str) -> str:
    normalized, _ = normalize_login_account(account)
    if "@" in normalized:
        local, _, domain = normalized.partition("@")
        visible = f"{local[:2]}***@{domain}" if domain else "email"
    elif len(normalized) >= 4:
        visible = f"••••{normalized[-4:]}"
    else:
        visible = "account"
    return f"Imou Connect ({visible})"


def _two_step_delivery_text(response: Any) -> str:
    if isinstance(response, Mapping):
        for key, value in response.items():
            if value in (None, ""):
                continue
            normalized_key = str(key).casefold().replace("_", "")
            if normalized_key in {"bindphone", "phone", "mobile", "mobilephone"}:
                return "SMS đến số điện thoại đã liên kết"
            if normalized_key in {"bindemail", "email"}:
                return "email đã liên kết"
    return "kênh bảo mật đã liên kết"


class ImouLifeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Imou Life account, CAPTCHA, and OTP authentication."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "ImouLifeOptionsFlow":
        return ImouLifeOptionsFlow()

    def __init__(self) -> None:
        """Initialize transient authentication state."""
        self._data: dict[str, Any] = {}
        self._api: ImouApiClient | None = None
        self._captcha_challenge: ImouCaptchaChallenge | None = None
        self._captcha_generation = 0
        self._captcha_resume = CAPTCHA_RESUME_AUTHENTICATE
        self._captcha_fingerprint = ""
        self._captcha_first_seen = int(time.time() * 1000)
        self._captcha_web_token = ""
        self._captcha_base_url = ""
        self._pending_otp_code: str | None = None
        self._two_step_delivery = "kênh bảo mật đã liên kết"
        self._external_next_step = "finish"
        self._external_error: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect account credentials and start Android-profile login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = {
                CONF_ACCOUNT: str(user_input[CONF_ACCOUNT]).strip(),
                CONF_PASSWORD: str(user_input[CONF_PASSWORD]),
                CONF_TERMINAL_ID: uuid4().hex,
                CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                CONF_REQUEST_TIMEOUT: DEFAULT_REQUEST_TIMEOUT,
                CONF_MAX_CONCURRENT_REQUESTS: DEFAULT_MAX_CONCURRENT_REQUESTS,
                CONF_MAX_PROPERTIES: DEFAULT_MAX_PROPERTIES,
            }
            if not self._data[CONF_ACCOUNT] or not self._data[CONF_PASSWORD]:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(
                    _account_unique_id(self._data[CONF_ACCOUNT])
                )
                self._abort_if_unique_id_configured()
                result = await self._async_start_authentication()
                if isinstance(result, dict):
                    return result
                errors["base"] = result
        return self._show_account_form("user", errors)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an existing entry."""
        self._data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a replacement password for an existing account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data[CONF_PASSWORD] = str(user_input[CONF_PASSWORD])
            self._data.setdefault(CONF_TERMINAL_ID, uuid4().hex)
            self._data.setdefault(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            self._data.setdefault(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)
            self._data.setdefault(
                CONF_MAX_CONCURRENT_REQUESTS, DEFAULT_MAX_CONCURRENT_REQUESTS
            )
            self._data.setdefault(CONF_MAX_PROPERTIES, DEFAULT_MAX_PROPERTIES)
            await self.async_set_unique_id(
                _account_unique_id(str(self._data[CONF_ACCOUNT]))
            )
            self._abort_if_unique_id_mismatch()
            result = await self._async_start_authentication()
            if isinstance(result, dict):
                return result
            errors["base"] = result
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_PASSWORD): _password_selector()}
            ),
            errors=errors,
            description_placeholders={
                "account": _account_title(str(self._data.get(CONF_ACCOUNT, "")))
            },
        )

    async def _async_start_authentication(self) -> ConfigFlowResult | str:
        self._api = ImouApiClient(
            async_get_clientsession(self.hass),
            str(self._data[CONF_ACCOUNT]),
            str(self._data[CONF_PASSWORD]),
            str(self._data[CONF_TERMINAL_ID]),
            request_timeout=float(self._data[CONF_REQUEST_TIMEOUT]),
            max_concurrent_requests=int(
                self._data[CONF_MAX_CONCURRENT_REQUESTS]
            ),
            language=(self.hass.config.language or "en_US").replace("-", "_"),
        )
        try:
            await self._api.async_authenticate(use_pc_client=False)
        except ImouCaptchaRequired as err:
            return await self._async_begin_captcha(
                err.challenge, CAPTCHA_RESUME_AUTHENTICATE
            )
        except ImouTwoStepVerificationRequired:
            return await self._async_begin_two_step()
        except ImouCredentialError:
            return "invalid_auth"
        except ImouQrLoginRequired:
            return "captcha_unavailable"
        except ImouConnectionError:
            return "cannot_connect"
        except ImouAuthError:
            return "invalid_auth"
        except ImouApiError:
            return "unknown"
        return self._async_finish_flow()

    async def _async_begin_captcha(
        self,
        challenge: ImouCaptchaChallenge,
        resume: str,
        *,
        external_context: bool = False,
        fallback_step: str = "user",
    ) -> ConfigFlowResult:
        assert self._api is not None
        async_register_captcha_views(self.hass)
        try:
            prepared = await self._api.async_prepare_captcha(challenge)
            captcha_base_url = get_url(
                self.hass, prefer_external=True, allow_ip=True
            )
        except (ImouApiError, NoURLAvailableError, ValueError):
            if external_context:
                return self._finish_external_with_error("captcha_unavailable")
            if fallback_step == "two_step":
                return self._show_two_step_form(
                    {"base": "captcha_unavailable"}
                )
            if self.source == config_entries.SOURCE_REAUTH:
                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=vol.Schema(
                        {vol.Required(CONF_PASSWORD): _password_selector()}
                    ),
                    errors={"base": "captcha_unavailable"},
                    description_placeholders={
                        "account": _account_title(
                            str(self._data.get(CONF_ACCOUNT, ""))
                        )
                    },
                )
            return self._show_account_form(
                "user", {"base": "captcha_unavailable"}
            )
        self._captcha_challenge = prepared
        self._captcha_resume = resume
        self._captcha_base_url = captcha_base_url
        self._captcha_generation += 1
        if not self._captcha_fingerprint:
            self._captcha_fingerprint = sha256(
                f"geetest:{self._data[CONF_TERMINAL_ID]}".encode()
            ).hexdigest()
        session = async_set_captcha_session(
            self.hass,
            self.flow_id,
            prepared,
            generation=self._captcha_generation,
            fingerprint=self._captcha_fingerprint,
            first_seen=self._captcha_first_seen,
            web_token=self._captcha_web_token or None,
        )
        self._captcha_web_token = session.web_token
        return self._show_captcha_external_step()

    @callback
    def _show_captcha_external_step(self) -> ConfigFlowResult:
        if not self._captcha_web_token or not self._captcha_base_url:
            return self.async_abort(reason="captcha_unavailable")
        return self.async_external_step(
            step_id="captcha",
            url=(
                f"{self._captcha_base_url.rstrip('/')}"
                f"{captcha_url(self._captcha_web_token)}"
                f"?g={self._captcha_generation}"
            ),
        )

    async def async_step_captcha(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate browser CAPTCHA data and resume the pending cloud action."""
        if user_input is None:
            return self._show_captcha_external_step()
        action = str(user_input.get("action") or "")
        if action == "refresh":
            return await self._async_repeat_pending_action()
        if action != "submit" or self._captcha_challenge is None:
            async_update_captcha_session(
                self.hass,
                self.flow_id,
                status="rejected",
                message="Dữ liệu CAPTCHA không hợp lệ.",
            )
            return self._show_captcha_external_step()
        try:
            generation = int(user_input.get("generation"))
        except (TypeError, ValueError):
            generation = -1
        if generation != self._captcha_generation:
            async_update_captcha_session(
                self.hass,
                self.flow_id,
                status="rejected",
                message="Phiên CAPTCHA đã thay đổi; trang sẽ tải lại.",
            )
            return self._show_captcha_external_step()
        captcha_result = user_input.get("captcha")
        if not isinstance(captcha_result, Mapping):
            return self._show_captcha_external_step()
        assert self._api is not None
        async_update_captcha_session(
            self.hass,
            self.flow_id,
            status="validating",
            message="Đang kiểm tra CAPTCHA với Imou…",
        )
        try:
            if self._captcha_challenge.is_image:
                await self._api.async_validate_image_captcha(
                    self._captcha_challenge,
                    str(captcha_result.get("code") or ""),
                )
            else:
                await self._api.async_validate_geetest(
                    self._captcha_challenge, captcha_result
                )
        except ValueError:
            async_update_captcha_session(
                self.hass,
                self.flow_id,
                status="rejected",
                message="Kết quả CAPTCHA không đầy đủ hoặc đã hết hạn.",
            )
            return self._show_captcha_external_step()
        except ImouApiError as err:
            if err.code == CAPTCHA_REJECTED_CODE:
                async_update_captcha_session(
                    self.hass,
                    self.flow_id,
                    status="rejected",
                    message="Imou từ chối CAPTCHA. Đang tạo thử thách mới…",
                )
                return await self._async_repeat_pending_action()
            return self._finish_external_with_error("cannot_connect")
        async_update_captcha_session(
            self.hass,
            self.flow_id,
            status="accepted",
            message="CAPTCHA hợp lệ. Đang tiếp tục đăng nhập…",
        )
        return await self._async_resume_after_captcha()

    async def _async_repeat_pending_action(self) -> ConfigFlowResult:
        """Repeat the request that produced the active challenge."""
        async_update_captcha_session(
            self.hass,
            self.flow_id,
            status="validating",
            message="Đang yêu cầu CAPTCHA mới từ Imou…",
        )
        return await self._async_resume_after_captcha()

    async def _async_resume_after_captcha(self) -> ConfigFlowResult:
        assert self._api is not None
        try:
            if self._captcha_resume == CAPTCHA_RESUME_SEND_OTP:
                response = await self._api.async_request_two_step_code()
                self._two_step_delivery = _two_step_delivery_text(response)
                self._external_next_step = "two_step"
                async_update_captcha_session(
                    self.hass,
                    self.flow_id,
                    status="otp_required",
                    message="Mã xác minh đã được gửi. Quay lại Home Assistant.",
                    complete=True,
                )
                return self.async_external_step_done(next_step_id="captcha_finish")
            if self._captcha_resume == CAPTCHA_RESUME_GRANT_OTP:
                if self._pending_otp_code is None:
                    return self._finish_external_with_error(
                        "invalid_code", next_step="two_step"
                    )
                await self._api.async_grant_two_step_code(self._pending_otp_code)
                self._pending_otp_code = None
                await self._api.async_authenticate_after_two_step()
            else:
                await self._api.async_authenticate(
                    force=True, use_pc_client=False
                )
        except ImouCaptchaRequired as err:
            resume = self._captcha_resume
            if (
                resume == CAPTCHA_RESUME_GRANT_OTP
                and self._pending_otp_code is None
            ):
                resume = CAPTCHA_RESUME_AUTHENTICATE
            return await self._async_begin_captcha(
                err.challenge,
                resume,
                external_context=True,
            )
        except ImouTwoStepVerificationRequired:
            if self._captcha_resume == CAPTCHA_RESUME_GRANT_OTP:
                error = (
                    "verification_pending"
                    if self._pending_otp_code is None
                    else "invalid_code"
                )
                return self._finish_external_with_error(
                    error, next_step="two_step"
                )
            return await self._async_begin_two_step(from_external=True)
        except ImouCredentialError:
            return self._finish_external_with_error("invalid_auth")
        except ImouQrLoginRequired:
            return self._finish_external_with_error("captcha_unavailable")
        except ImouConnectionError:
            return self._finish_external_with_error("cannot_connect")
        except ImouAuthError:
            return self._finish_external_with_error("invalid_auth")
        except ImouApiError:
            return self._finish_external_with_error(
                (
                    "verification_pending"
                    if self._captcha_resume == CAPTCHA_RESUME_GRANT_OTP
                    and self._pending_otp_code is None
                    else "invalid_code"
                )
                if self._captcha_resume == CAPTCHA_RESUME_GRANT_OTP
                else "unknown",
                next_step=(
                    "two_step"
                    if self._captcha_resume == CAPTCHA_RESUME_GRANT_OTP
                    else "account"
                ),
            )
        self._external_next_step = "finish"
        async_update_captcha_session(
            self.hass,
            self.flow_id,
            status="complete",
            message="Đăng nhập thành công. Quay lại Home Assistant.",
            complete=True,
        )
        return self.async_external_step_done(next_step_id="captcha_finish")

    @callback
    def _finish_external_with_error(
        self, error: str, *, next_step: str = "account"
    ) -> ConfigFlowResult:
        self._external_error = error
        self._external_next_step = next_step
        async_update_captcha_session(
            self.hass,
            self.flow_id,
            status="complete",
            message="Không hoàn tất được đăng nhập. Quay lại Home Assistant.",
            complete=True,
        )
        return self.async_external_step_done(next_step_id="captcha_finish")

    async def async_step_captcha_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Leave the external step and continue with form or entry creation."""
        if self._external_error:
            error = self._external_error
            self._external_error = None
            if self._external_next_step == "two_step":
                return self._show_two_step_form({"base": error})
            if self.source == config_entries.SOURCE_REAUTH:
                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=vol.Schema(
                        {vol.Required(CONF_PASSWORD): _password_selector()}
                    ),
                    errors={"base": error},
                    description_placeholders={
                        "account": _account_title(
                            str(self._data.get(CONF_ACCOUNT, ""))
                        )
                    },
                )
            return self._show_account_form("user", {"base": error})
        if self._external_next_step == "two_step":
            return await self.async_step_two_step()
        return self._async_finish_flow()

    async def _async_begin_two_step(
        self, *, from_external: bool = False
    ) -> ConfigFlowResult:
        assert self._api is not None
        try:
            response = await self._api.async_request_two_step_code()
        except ImouCaptchaRequired as err:
            return await self._async_begin_captcha(
                err.challenge,
                CAPTCHA_RESUME_SEND_OTP,
                external_context=from_external,
                fallback_step="two_step",
            )
        except ImouConnectionError:
            if from_external:
                return self._finish_external_with_error("cannot_connect")
            return self._show_two_step_form({"base": "code_send_failed"})
        except ImouApiError:
            if from_external:
                return self._finish_external_with_error("code_send_failed")
            return self._show_two_step_form({"base": "code_send_failed"})
        self._two_step_delivery = _two_step_delivery_text(response)
        if from_external:
            self._external_next_step = "two_step"
            async_update_captcha_session(
                self.hass,
                self.flow_id,
                status="otp_required",
                message="Mã xác minh đã được gửi. Quay lại Home Assistant.",
                complete=True,
            )
            return self.async_external_step_done(next_step_id="captcha_finish")
        return await self.async_step_two_step()

    async def async_step_two_step(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send, resend, and grant the six-digit terminal verification code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if bool(user_input.get(CONF_RESEND_CODE)):
                result = await self._async_resend_two_step_code()
                if isinstance(result, dict):
                    return result
                if result is not None:
                    errors["base"] = result
            else:
                code = str(user_input.get(CONF_VALID_CODE) or "").strip()
                if len(code) != 6 or not code.isdigit():
                    errors[CONF_VALID_CODE] = "invalid_code"
                else:
                    result = await self._async_submit_two_step_code(code)
                    if isinstance(result, dict):
                        return result
                    errors["base"] = result
        return self._show_two_step_form(errors)

    async def _async_resend_two_step_code(
        self,
    ) -> ConfigFlowResult | str | None:
        assert self._api is not None
        try:
            response = await self._api.async_request_two_step_code()
        except ImouCaptchaRequired as err:
            return await self._async_begin_captcha(
                err.challenge,
                CAPTCHA_RESUME_SEND_OTP,
                fallback_step="two_step",
            )
        except ImouConnectionError:
            return "cannot_connect"
        except ImouApiError:
            return "code_send_failed"
        self._two_step_delivery = _two_step_delivery_text(response)
        return None

    async def _async_submit_two_step_code(
        self, code: str
    ) -> ConfigFlowResult | str:
        assert self._api is not None
        self._pending_otp_code = code
        try:
            await self._api.async_grant_two_step_code(code)
            self._pending_otp_code = None
            await self._api.async_authenticate_after_two_step()
        except ImouCaptchaRequired as err:
            resume = (
                CAPTCHA_RESUME_GRANT_OTP
                if self._pending_otp_code is not None
                else CAPTCHA_RESUME_AUTHENTICATE
            )
            return await self._async_begin_captcha(
                err.challenge, resume, fallback_step="two_step"
            )
        except ImouTwoStepVerificationRequired:
            granted = self._pending_otp_code is None
            self._pending_otp_code = None
            return "verification_pending" if granted else "invalid_code"
        except ImouConnectionError:
            self._pending_otp_code = None
            return "cannot_connect"
        except ImouCredentialError:
            self._pending_otp_code = None
            return "invalid_auth"
        except ImouApiError:
            granted = self._pending_otp_code is None
            self._pending_otp_code = None
            return "verification_pending" if granted else "invalid_code"
        return self._async_finish_flow()

    @staticmethod
    def _two_step_schema() -> vol.Schema:
        return vol.Schema(
            {
                vol.Optional(CONF_VALID_CODE, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                        autocomplete="one-time-code",
                    )
                ),
                vol.Optional(CONF_RESEND_CODE, default=False): bool,
            }
        )

    @callback
    def _show_two_step_form(
        self, errors: dict[str, str]
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="two_step",
            data_schema=self._two_step_schema(),
            errors=errors,
            description_placeholders={"delivery": self._two_step_delivery},
        )

    @callback
    def _show_account_form(
        self, step_id: str, errors: dict[str, str]
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT): selector.TextSelector(
                        selector.TextSelectorConfig(autocomplete="username")
                    ),
                    vol.Required(CONF_PASSWORD): _password_selector(),
                }
            ),
            errors=errors,
        )

    @callback
    def _async_finish_flow(self) -> ConfigFlowResult:
        """Create or update the config entry after successful Login."""
        title = _account_title(str(self._data[CONF_ACCOUNT]))
        if self.source == config_entries.SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data=self._data,
                title=title,
            )
        return self.async_create_entry(title=title, data=self._data)


class ImouLifeOptionsFlow(config_entries.OptionsFlow):
    """Allow setting a local LAN host per Imou device for direct RTSP."""

    def __init__(self) -> None:
        self._camera_id: str | None = None
        self._choices: dict[str, tuple[str, str]] = {}

    def _available_cameras(self) -> dict[str, tuple[str, str]]:
        runtime = getattr(self.config_entry, "runtime_data", None)
        coordinator = getattr(runtime, "coordinator", None)
        data = getattr(coordinator, "data", None)
        choices: dict[str, tuple[str, str]] = {}
        if isinstance(data, Mapping):
            for device in data.values():
                for index, channel in enumerate(device.channels, 1):
                    try:
                        number = int(channel.channel_id)
                    except ValueError:
                        number = index
                    else:
                        if any(item.channel_id == "0" for item in device.channels):
                            number += 1
                    path = f"/cam/realmonitor?channel={max(1, number)}&subtype=0"
                    key = local_camera_key(device.device_id, channel.channel_id)
                    choices[key] = (f"{device.name} — {channel.name} [{index}]", path)
        for key, camera in configured_local_cameras(self.config_entry.options).items():
            choices.setdefault(key, (camera[CONF_LOCAL_HOST], camera[CONF_RTSP_PATH]))
        return choices

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._choices = self._available_cameras()
        if not self._choices:
            return self.async_abort(reason="no_devices")
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input.get(CONF_CAMERA_ID)
            if key in self._choices:
                self._camera_id = key
                return await self.async_step_local_camera()
            errors[CONF_CAMERA_ID] = "invalid_camera"
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_CAMERA_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": key, "label": choice[0]} for key, choice in self._choices.items()],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_local_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        key = self._camera_id
        if key is None or key not in self._choices:
            return await self.async_step_init()
        cameras = configured_local_cameras(self.config_entry.options)
        previous = cameras.get(key, {})
        errors: dict[str, str] = {}
        legacy_host = self.config_entry.options.get(CONF_LOCAL_HOST, "")
        if isinstance(legacy_host, Mapping):
            legacy_host = legacy_host.get("default", "")
        defaults = {
            CONF_LOCAL_HOST: previous.get(CONF_LOCAL_HOST, legacy_host if isinstance(legacy_host, str) else ""),
            CONF_RTSP_PORT: previous.get(CONF_RTSP_PORT, 554),
            CONF_LOCAL_USERNAME: previous.get(CONF_LOCAL_USERNAME, ""),
            CONF_RTSP_PATH: previous.get(CONF_RTSP_PATH, self._choices[key][1]),
            CONF_ONVIF_PORT: previous.get(CONF_ONVIF_PORT, DEFAULT_ONVIF_PORT),
            CONF_ONVIF_PROFILE: previous.get(CONF_ONVIF_PROFILE, ""),
            CONF_ONVIF_PTZ: previous.get(CONF_ONVIF_PTZ, True),
        }
        if user_input is not None:
            defaults.update({field: user_input[field] for field in defaults if field in user_input})
            if user_input.get(CONF_REMOVE_CAMERA):
                cameras.pop(key, None)
            else:
                values = dict(user_input)
                for field, default_value in (
                    (CONF_LOCAL_HOST, defaults.get(CONF_LOCAL_HOST)),
                    (CONF_RTSP_PORT, defaults.get(CONF_RTSP_PORT)),
                    (CONF_LOCAL_USERNAME, defaults.get(CONF_LOCAL_USERNAME)),
                    (CONF_RTSP_PATH, defaults.get(CONF_RTSP_PATH)),
                    (CONF_ONVIF_PORT, defaults.get(CONF_ONVIF_PORT)),
                    (CONF_ONVIF_PROFILE, defaults.get(CONF_ONVIF_PROFILE)),
                    (CONF_ONVIF_PTZ, defaults.get(CONF_ONVIF_PTZ)),
                ):
                    values.setdefault(field, default_value)
                if not values.get(CONF_LOCAL_PASSWORD) and previous:
                    values[CONF_LOCAL_PASSWORD] = previous.get(CONF_LOCAL_PASSWORD, "")
                try:
                    normalized = normalize_local_camera(values)
                except ValueError as err:
                    fields = {
                        "invalid_host": CONF_LOCAL_HOST,
                        "invalid_port": CONF_RTSP_PORT,
                        "invalid_onvif_port": CONF_ONVIF_PORT,
                        "invalid_onvif_profile": CONF_ONVIF_PROFILE,
                        "invalid_path": CONF_RTSP_PATH,
                        "invalid_credentials": CONF_LOCAL_USERNAME,
                    }
                    error_key = str(err)
                    errors[fields.get(error_key, "base")] = error_key
                else:
                    if values.get(CONF_VALIDATE_STREAM, True):
                        from .stream_probe import async_check_local_stream

                        stream_error = await async_check_local_stream(normalized)
                        if stream_error:
                            errors["base"] = stream_error
                    if not errors:
                        cameras[key] = normalized
            if not errors:
                options = dict(self.config_entry.options)
                options.pop(CONF_LOCAL_HOST, None)
                options[CONF_LOCAL_CAMERAS] = cameras
                return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="local_camera",
            data_schema=vol.Schema({
                vol.Required(CONF_LOCAL_HOST, default=defaults[CONF_LOCAL_HOST]): str,
                vol.Required(
                    CONF_RTSP_PORT,
                    default=defaults[CONF_RTSP_PORT],
                ): vol.All(
                    vol.Coerce(str),
                    vol.Coerce(int),
                    vol.Range(min=1, max=65535),
                ),
                vol.Optional(
                    CONF_LOCAL_USERNAME,
                    default=defaults[CONF_LOCAL_USERNAME],
                ): str,
                vol.Optional(CONF_LOCAL_PASSWORD, default=""): _password_selector(),
                vol.Required(CONF_RTSP_PATH, default=defaults[CONF_RTSP_PATH]): str,
                vol.Optional(
                    CONF_ONVIF_PORT,
                    default=defaults[CONF_ONVIF_PORT],
                ): vol.All(
                    vol.Coerce(str),
                    vol.Coerce(int),
                    vol.Range(min=1, max=65535),
                ),
                vol.Optional(
                    CONF_ONVIF_PROFILE,
                    default=defaults[CONF_ONVIF_PROFILE],
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_ONVIF_PTZ,
                    default=defaults[CONF_ONVIF_PTZ],
                ): bool,
                vol.Optional(CONF_VALIDATE_STREAM, default=True): bool,
                vol.Optional(CONF_REMOVE_CAMERA, default=False): bool,
            }),
            errors=errors,
            description_placeholders={"camera": self._choices[key][0]},
        )


def _password_selector() -> selector.TextSelector:
    return selector.TextSelector(
        selector.TextSelectorConfig(
            type=selector.TextSelectorType.PASSWORD,
            autocomplete="current-password",
        )
    )
