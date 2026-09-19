"""Server-side CAPTCHA session and HTTP views for config flows."""

from __future__ import annotations

import json
import secrets
import time
from base64 import b64decode
from binascii import Error as BinasciiError
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.http import HomeAssistantView

from .api import ImouCaptchaChallenge
from .captcha_ui import render_captcha_page
from .const import (
    CAPTCHA_BASE_PATH,
    CAPTCHA_SESSION_TTL,
    CAPTCHA_SUBMIT_MAX_BYTES,
    DATA_CAPTCHA_SESSIONS,
    DATA_CAPTCHA_VIEWS_REGISTERED,
    DOMAIN,
)

_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@dataclass(slots=True)
class CaptchaSession:
    """Public CAPTCHA state associated with one Home Assistant flow."""

    flow_id: str
    web_token: str
    challenge: ImouCaptchaChallenge
    generation: int
    fingerprint: str
    first_seen: int
    created_at: float
    status: str = "required"
    message: str = "Hoàn tất CAPTCHA để tiếp tục."
    complete: bool = False


def _domain_data(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {})


def _sessions(hass: HomeAssistant) -> dict[str, CaptchaSession]:
    return _domain_data(hass).setdefault(DATA_CAPTCHA_SESSIONS, {})


@callback
def async_set_captcha_session(
    hass: HomeAssistant,
    flow_id: str,
    challenge: ImouCaptchaChallenge,
    *,
    generation: int,
    fingerprint: str,
    first_seen: int,
    web_token: str | None = None,
) -> CaptchaSession:
    """Create or replace the browser-visible state for one flow."""
    token = web_token or secrets.token_urlsafe(32)
    sessions = _sessions(hass)
    for existing_token, existing in tuple(sessions.items()):
        if existing.flow_id == flow_id and existing_token != token:
            sessions.pop(existing_token, None)
    session = CaptchaSession(
        flow_id=flow_id,
        web_token=token,
        challenge=challenge,
        generation=generation,
        fingerprint=fingerprint,
        first_seen=first_seen,
        created_at=time.monotonic(),
        message=(
            "Nhập bốn ký tự trong ảnh CAPTCHA."
            if challenge.is_image
            else "Hoàn tất CAPTCHA hình ảnh GeeTest."
        ),
    )
    sessions[token] = session
    return session


@callback
def async_update_captcha_session(
    hass: HomeAssistant,
    flow_id: str,
    *,
    status: str,
    message: str,
    complete: bool = False,
) -> None:
    """Update only UI-safe CAPTCHA status fields."""
    for session in _sessions(hass).values():
        if session.flow_id == flow_id:
            session.status = status
            session.message = message
            session.complete = complete
            break


@callback
def async_remove_captcha_session(hass: HomeAssistant, web_token: str) -> None:
    """Remove a stale CAPTCHA session."""
    _sessions(hass).pop(web_token, None)


def captcha_url(web_token: str) -> str:
    """Return the same-origin CAPTCHA page path."""
    return f"{CAPTCHA_BASE_PATH}/{web_token}/"


@callback
def async_register_captcha_views(hass: HomeAssistant) -> None:
    """Register authenticated HTTP views exactly once."""
    data = _domain_data(hass)
    if data.get(DATA_CAPTCHA_VIEWS_REGISTERED):
        return
    data[DATA_CAPTCHA_VIEWS_REGISTERED] = True
    hass.http.register_view(ImouCaptchaPageView)
    hass.http.register_view(ImouCaptchaStatusView)
    hass.http.register_view(ImouCaptchaScriptView)
    hass.http.register_view(ImouCaptchaImageView)
    hass.http.register_view(ImouCaptchaSubmitView)
    hass.http.register_view(ImouCaptchaRefreshView)


def _get_session(request: web.Request) -> tuple[str, CaptchaSession]:
    hass: HomeAssistant = request.app["hass"]
    web_token = request.match_info.get("web_token", "")
    session = _sessions(hass).get(web_token)
    if session is None:
        raise web.HTTPNotFound(text="CAPTCHA session not found")
    if time.monotonic() - session.created_at > CAPTCHA_SESSION_TTL:
        async_remove_captcha_session(hass, web_token)
        raise web.HTTPGone(text="CAPTCHA session expired")
    return web_token, session


def _decode_captcha_image(value: str) -> tuple[bytes, str]:
    content_type = "image/jpeg"
    encoded = value.strip()
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("Invalid CAPTCHA image")
        candidate = header[5:].split(";", 1)[0].strip().lower()
        if candidate not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            raise ValueError("Unsupported CAPTCHA image type")
        content_type = candidate
    try:
        image = b64decode(encoded, validate=True)
    except (BinasciiError, ValueError) as err:
        raise ValueError("Invalid CAPTCHA image") from err
    if not image or len(image) > 2 * 1024 * 1024:
        raise ValueError("Invalid CAPTCHA image size")
    return image, content_type


@lru_cache(maxsize=1)
def _captcha_script() -> str:
    return (Path(__file__).with_name("web") / "gl4.js").read_text(encoding="utf-8")


class _TokenProtectedCaptchaView(HomeAssistantView):
    """A public endpoint protected by a high-entropy, short-lived URL token."""

    requires_auth = False


class ImouCaptchaPageView(_TokenProtectedCaptchaView):
    """Render the active visual CAPTCHA."""

    url = f"{CAPTCHA_BASE_PATH}/{{web_token}}/"
    name = "api:imou_life:captcha"

    async def get(
        self, request: web.Request, web_token: str = ""
    ) -> web.Response:
        """Serve the CAPTCHA page."""
        web_token, session = _get_session(request)
        body = render_captcha_page(
            captcha_url(web_token),
            session.challenge,
            session.generation,
            session.fingerprint,
            session.first_seen,
        )
        return web.Response(
            text=body,
            content_type="text/html",
            headers=_NO_STORE_HEADERS,
        )


class ImouCaptchaStatusView(_TokenProtectedCaptchaView):
    """Expose sanitized CAPTCHA progress to its browser page."""

    url = f"{CAPTCHA_BASE_PATH}/{{web_token}}/status"
    name = "api:imou_life:captcha:status"

    async def get(
        self, request: web.Request, web_token: str = ""
    ) -> web.Response:
        """Return UI-safe status."""
        _, session = _get_session(request)
        return self.json(
            {
                "generation": session.generation,
                "status": session.status,
                "message": session.message,
                "complete": session.complete,
            },
            headers=_NO_STORE_HEADERS,
        )


class ImouCaptchaScriptView(_TokenProtectedCaptchaView):
    """Serve the GeeTest SDK asset recovered from the APK."""

    url = f"{CAPTCHA_BASE_PATH}/{{web_token}}/gl4.js"
    name = "api:imou_life:captcha:script"

    async def get(
        self, request: web.Request, web_token: str = ""
    ) -> web.Response:
        """Return the local GeeTest script."""
        _get_session(request)
        return web.Response(
            text=_captcha_script(),
            content_type="application/javascript",
            headers=_NO_STORE_HEADERS,
        )


class ImouCaptchaImageView(_TokenProtectedCaptchaView):
    """Serve a legacy four-character CAPTCHA image."""

    url = f"{CAPTCHA_BASE_PATH}/{{web_token}}/image"
    name = "api:imou_life:captcha:image"

    async def get(
        self, request: web.Request, web_token: str = ""
    ) -> web.Response:
        """Return the active image challenge."""
        _, session = _get_session(request)
        requested_generation = request.query.get("g")
        if requested_generation and requested_generation != str(session.generation):
            raise web.HTTPConflict(text="CAPTCHA generation changed")
        try:
            image, content_type = _decode_captcha_image(session.challenge.image)
        except ValueError as err:
            raise web.HTTPNotFound(text=str(err)) from err
        return web.Response(
            body=image,
            content_type=content_type,
            headers=_NO_STORE_HEADERS,
        )


class _CaptchaPostView(_TokenProtectedCaptchaView):
    """Shared bounded JSON and flow continuation helpers."""

    async def _read_json(self, request: web.Request) -> dict[str, Any]:
        if (
            request.content_length is not None
            and request.content_length > CAPTCHA_SUBMIT_MAX_BYTES
        ):
            raise web.HTTPRequestEntityTooLarge(
                max_size=CAPTCHA_SUBMIT_MAX_BYTES,
                actual_size=request.content_length,
            )
        try:
            payload = await request.json(loads=json.loads)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise web.HTTPBadRequest(text="Invalid JSON") from err
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Invalid request")
        return payload

    async def _configure(
        self,
        request: web.Request,
        web_token: str,
        user_input: dict[str, Any],
        *,
        allow_required: bool = False,
    ) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        session = _sessions(hass).get(web_token)
        if session is None:
            raise web.HTTPGone(text="CAPTCHA session expired")
        try:
            await hass.config_entries.flow.async_configure(
                session.flow_id, user_input
            )
        except UnknownFlow as err:
            raise web.HTTPGone(text="Configuration flow is no longer active") from err
        session = _sessions(hass).get(web_token)
        if session is None:
            return self.json(
                {"ok": True, "message": "Đã xác minh. Quay lại Home Assistant."},
                headers=_NO_STORE_HEADERS,
            )
        ok = session.status in {"accepted", "complete", "otp_required"} or (
            allow_required and session.status == "required"
        )
        return self.json(
            {
                "ok": ok,
                "generation": session.generation,
                "message" if ok else "error": session.message,
            },
            status_code=HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
            headers=_NO_STORE_HEADERS,
        )


class ImouCaptchaSubmitView(_CaptchaPostView):
    """Pass a browser CAPTCHA result back into the config flow."""

    url = f"{CAPTCHA_BASE_PATH}/{{web_token}}/submit"
    name = "api:imou_life:captcha:submit"

    async def post(
        self, request: web.Request, web_token: str = ""
    ) -> web.Response:
        """Continue the external config-flow step."""
        web_token, session = _get_session(request)
        payload = await self._read_json(request)
        try:
            generation = int(payload.pop("generation"))
        except (KeyError, TypeError, ValueError) as err:
            raise web.HTTPBadRequest(text="Invalid CAPTCHA generation") from err
        if generation != session.generation:
            raise web.HTTPConflict(text="CAPTCHA generation changed")
        return await self._configure(
            request,
            web_token,
            {"action": "submit", "generation": generation, "captcha": payload},
        )


class ImouCaptchaRefreshView(_CaptchaPostView):
    """Request a new challenge without exposing credentials to the browser."""

    url = f"{CAPTCHA_BASE_PATH}/{{web_token}}/refresh"
    name = "api:imou_life:captcha:refresh"

    async def post(
        self, request: web.Request, web_token: str = ""
    ) -> web.Response:
        """Restart the pending Imou operation and replace its CAPTCHA."""
        web_token, _ = _get_session(request)
        return await self._configure(
            request,
            web_token,
            {"action": "refresh"},
            allow_required=True,
        )
