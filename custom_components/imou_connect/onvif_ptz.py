"""Minimal ONVIF PTZ client for local Imou cameras."""

from __future__ import annotations

import asyncio
import base64
import os
import re
from datetime import UTC, datetime
from hashlib import sha1
from xml.sax.saxutils import escape

from aiohttp import ClientError, ClientSession

_SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
_WSSE = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-"
    "wssecurity-secext-1.0.xsd"
)
_WSU = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-"
    "wssecurity-utility-1.0.xsd"
)
_PASSWORD_DIGEST = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-"
    "username-token-profile-1.0#PasswordDigest"
)
_NONCE_B64 = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-"
    "soap-message-security-1.0#Base64Binary"
)
_DEVICE_NS = "http://www.onvif.org/ver10/device/wsdl"
_PTZ_NS = "http://www.onvif.org/ver20/ptz/wsdl"
_SCHEMA_NS = "http://www.onvif.org/ver10/schema"
_PAN_TILT_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
_ZOOM_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace"

_REQUEST_TIMEOUT = 10
_XADDR_PATTERN = re.compile(r"<[^>]*XAddr>([^<]+)</[^>]*XAddr>", re.IGNORECASE)
_PTZ_BLOCK_PATTERN = re.compile(
    r"<[^>]*:PTZ[^>]*>(.*?)</[^>]*:PTZ>", re.IGNORECASE | re.DOTALL
)
_PROFILE_PATTERN = re.compile(
    r"<[^>]*Profiles[^>]*token=\"([^\"]+)\"", re.IGNORECASE
)


class OnvifPtzError(Exception):
    """Raised when an ONVIF PTZ request cannot be completed."""


def _wsse_header(username: str, password: str) -> str:
    nonce = os.urandom(16)
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        sha1(nonce + created.encode() + password.encode()).digest()
    ).decode()
    return (
        '<s:Header><wsse:Security s:mustUnderstand="1" '
        f'xmlns:wsse="{_WSSE}" xmlns:wsu="{_WSU}">'
        f"<wsse:UsernameToken><wsse:Username>{escape(username)}</wsse:Username>"
        f'<wsse:Password Type="{_PASSWORD_DIGEST}">{digest}</wsse:Password>'
        f'<wsse:Nonce EncodingType="{_NONCE_B64}">'
        f"{base64.b64encode(nonce).decode()}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created></wsse:UsernameToken>"
        "</wsse:Security></s:Header>"
    )


def _envelope(body: str, username: str, password: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{_SOAP_ENV}" '
        f'xmlns:tds="{_DEVICE_NS}" xmlns:tptz="{_PTZ_NS}" '
        f'xmlns:tt="{_SCHEMA_NS}">'
        f"{_wsse_header(username, password)}"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )


def _extract_ptz_url(response: str) -> str | None:
    block = _PTZ_BLOCK_PATTERN.search(response)
    if block is not None:
        addresses = _XADDR_PATTERN.findall(block.group(1))
        if addresses:
            return addresses[0].strip()
    for address in _XADDR_PATTERN.findall(response):
        if "ptz" in address.casefold():
            return address.strip()
    return None


def _extract_profile(response: str) -> str | None:
    match = _PROFILE_PATTERN.search(response)
    return match.group(1) if match else None


class OnvifPtzClient:
    """Cache ONVIF PTZ capability and move a camera over the local network."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
    ) -> None:
        """Initialize an ONVIF PTZ client for one camera."""
        self._session = session
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._ptz_url: str | None = None
        self._profile_token: str | None = None
        self._supported: bool | None = None
        self._lock = asyncio.Lock()

    @property
    def device_url(self) -> str:
        return f"http://{self._format_host()}/onvif/device_service"

    def _format_host(self) -> str:
        host = self._host
        if ":" in host:
            host = f"[{host}]"
        return host if self._port == 80 else f"{host}:{self._port}"

    async def async_supported(self) -> bool:
        """Return whether the camera exposes an ONVIF PTZ service."""
        if self._supported is not None:
            return self._supported
        try:
            await self._async_ensure()
        except OnvifPtzError:
            # Only a missing PTZ service is cached as unsupported; connection
            # errors leave the capability unknown so the next press retries.
            return False
        return self._supported is True

    async def async_move(
        self,
        *,
        pan: float,
        tilt: float,
        zoom: float,
        duration_ms: int,
    ) -> None:
        """Move the camera for a short time and then stop."""
        await self._async_ensure()
        if not self._ptz_url or not self._profile_token:
            raise OnvifPtzError("ONVIF PTZ is not available")
        await self._async_post(
            self._ptz_url,
            _envelope(
                "<tptz:ContinuousMove>"
                f"<tptz:ProfileToken>{escape(self._profile_token)}</tptz:ProfileToken>"
                "<tptz:Velocity>"
                f'<tt:PanTilt x="{pan:.4f}" y="{tilt:.4f}" '
                f'space="{_PAN_TILT_SPACE}"/>'
                f'<tt:Zoom x="{zoom:.4f}" space="{_ZOOM_SPACE}"/>'
                "</tptz:Velocity>"
                "</tptz:ContinuousMove>",
                self._username,
                self._password,
            ),
        )
        await asyncio.sleep(max(0.1, duration_ms / 1000))
        await self._async_stop()

    async def _async_stop(self) -> None:
        if not self._ptz_url or not self._profile_token:
            return
        await self._async_post(
            self._ptz_url,
            _envelope(
                "<tptz:Stop>"
                f"<tptz:ProfileToken>{escape(self._profile_token)}</tptz:ProfileToken>"
                "<tptz:PanTilt>true</tptz:PanTilt>"
                "<tptz:Zoom>true</tptz:Zoom>"
                "</tptz:Stop>",
                self._username,
                self._password,
            ),
        )

    async def _async_ensure(self) -> None:
        async with self._lock:
            if self._ptz_url and self._profile_token:
                return
            if self._supported is False:
                raise OnvifPtzError("ONVIF PTZ is not available")
            response = await self._async_post(
                self.device_url,
                _envelope(
                    "<tds:GetCapabilities><tds:Category>All</tds:Category>"
                    "</tds:GetCapabilities>",
                    self._username,
                    self._password,
                ),
            )
            ptz_url = _extract_ptz_url(response)
            if not ptz_url:
                self._supported = False
                raise OnvifPtzError("ONVIF camera has no PTZ service")
            profiles = await self._async_post(
                ptz_url,
                _envelope(
                    "<tptz:GetProfiles/>",
                    self._username,
                    self._password,
                ),
            )
            token = _extract_profile(profiles)
            if not token:
                self._supported = False
                raise OnvifPtzError("ONVIF camera has no media profile")
            self._ptz_url = ptz_url
            self._profile_token = token
            self._supported = True

    async def _async_post(self, url: str, body: str) -> str:
        try:
            async with self._session.post(
                url,
                data=body.encode(),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                text = await response.text(errors="ignore")
        except (ClientError, asyncio.TimeoutError, OSError) as err:
            raise OnvifPtzError(f"ONVIF request failed: {err}") from err
        if "Fault" in text and "<" in text:
            raise OnvifPtzError("ONVIF fault returned by camera")
        if response.status >= 400 and not _XADDR_PATTERN.search(text):
            raise OnvifPtzError(f"ONVIF HTTP {response.status}")
        return text