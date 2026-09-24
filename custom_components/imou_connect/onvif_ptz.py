"""Minimal ONVIF PTZ client for local Imou cameras."""

from __future__ import annotations

import asyncio
import base64
import math
import os
from datetime import UTC, datetime
from hashlib import sha1
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from aiohttp import ClientError, ClientSession, ClientTimeout

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
_MEDIA_NS = "http://www.onvif.org/ver10/media/wsdl"
_PTZ_NS = "http://www.onvif.org/ver20/ptz/wsdl"
_SCHEMA_NS = "http://www.onvif.org/ver10/schema"
_PAN_TILT_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
_ZOOM_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace"
_REQUEST_TIMEOUT = 10
_MAX_RESPONSE_BYTES = 1024 * 1024


class OnvifPtzError(Exception):
    """Raised when an ONVIF PTZ request cannot be completed."""


class OnvifPtzUnsupported(OnvifPtzError):
    pass


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
        f'xmlns:tds="{_DEVICE_NS}" xmlns:trt="{_MEDIA_NS}" '
        f'xmlns:tptz="{_PTZ_NS}" xmlns:tt="{_SCHEMA_NS}">'
        f"{_wsse_header(username, password)}"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )


def _envelope_root(raw: bytes) -> ET.Element | None:
    declarations = raw.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        return None
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        return None
    return root if root.tag == f"{{{_SOAP_ENV}}}Envelope" else None


def _find_fault(root: ET.Element) -> ET.Element | None:
    body = root.find(f"{{{_SOAP_ENV}}}Body")
    if body is None:
        return None
    return body.find(f"{{{_SOAP_ENV}}}Fault")


def _fault_error(fault: ET.Element, operation: str) -> OnvifPtzError:
    codes = {
        (item.text or "").rsplit(":", 1)[-1]
        for item in fault.findall(f".//{{{_SOAP_ENV}}}Value")
    }
    if codes & {
        "NotAuthorized",
        "FailedAuthentication",
        "InvalidSecurity",
        "MessageExpired",
    }:
        return OnvifPtzError(
            f"ONVIF {operation} authentication failed; check the local "
            "camera credentials, ONVIF permissions and camera clock"
        )
    if "ActionNotSupported" in codes:
        return OnvifPtzError(f"ONVIF {operation} is not supported by the camera")
    return OnvifPtzError(f"ONVIF {operation} returned a SOAP fault")


def _parse_response(raw: bytes, namespace: str, operation: str) -> ET.Element:
    invalid = f"ONVIF {operation} returned an invalid SOAP response"
    root = _envelope_root(raw)
    if root is None:
        raise OnvifPtzError(invalid)
    body = root.find(f"{{{_SOAP_ENV}}}Body")
    if body is None or len(body) != 1:
        raise OnvifPtzError(invalid)
    fault = _find_fault(root)
    if fault is not None:
        raise _fault_error(fault, operation)
    response = body[0]
    if response.tag != f"{{{namespace}}}{operation}Response":
        raise OnvifPtzError(invalid)
    return response


def _select_profile(response: ET.Element, requested: str) -> str:
    profiles = response.findall(f"{{{_MEDIA_NS}}}Profiles")
    if not profiles:
        if any(child.tag.rsplit("}", 1)[-1] == "Profiles" for child in response):
            raise OnvifPtzError(
                "ONVIF GetProfiles returned profiles in an unexpected namespace"
            )
        raise OnvifPtzError("ONVIF GetProfiles returned no media profiles")
    candidates: list[tuple[str, str | None]] = []
    for profile in profiles:
        token = profile.get("token")
        if not token or profile.find(f"{{{_SCHEMA_NS}}}Name") is None:
            raise OnvifPtzError("ONVIF GetProfiles returned an invalid media profile")
        if profile.find(f"{{{_SCHEMA_NS}}}PTZConfiguration") is not None:
            source = profile.findtext(
                f"{{{_SCHEMA_NS}}}VideoSourceConfiguration/{{{_SCHEMA_NS}}}SourceToken"
            )
            candidates.append((token, source))
    if requested:
        if any(token == requested for token, _ in candidates):
            return requested
        raise OnvifPtzError("Configured ONVIF profile was not found or has no PTZ configuration")
    if not candidates:
        raise OnvifPtzUnsupported("ONVIF camera has no PTZ-enabled media profile")
    sources = {source for _, source in candidates}
    if len(sources) > 1 or len(candidates) > 1 and None in sources:
        raise OnvifPtzError(
            "ONVIF camera has multiple video sources; select an ONVIF profile token in Configure"
        )
    return candidates[0][0]


class OnvifPtzClient:
    """Cache ONVIF PTZ capability and move a camera over the local network."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        profile_token: str = "",
    ) -> None:
        self._session = session
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._requested_profile = profile_token
        self._ptz_url: str | None = None
        self._profile_token: str | None = None
        self._supported: bool | None = None
        self._lock = asyncio.Lock()
        self._move_lock = asyncio.Lock()

    @property
    def device_url(self) -> str:
        return f"http://{self._format_host()}/onvif/device_service"

    def _format_host(self) -> str:
        host = f"[{self._host}]" if ":" in self._host else self._host
        return f"{host}:{self._port}"

    def _service_url(self, value: str | None) -> str:
        if not value or len(value) > 2048 or any(ord(char) <= 32 for char in value) or "\\" in value:
            raise OnvifPtzError("ONVIF camera returned an invalid service address")
        try:
            parts = urlsplit(value)
            valid = (
                parts.scheme in {"http", "https"}
                and parts.hostname
                and parts.username is None
                and parts.password is None
                and not parts.fragment
                and (parts.port is None or 1 <= parts.port <= 65535)
            )
        except ValueError:
            raise OnvifPtzError("ONVIF camera returned an invalid service address") from None
        if not valid:
            raise OnvifPtzError("ONVIF camera returned an invalid service address")
        return urlunsplit((parts.scheme, self._format_host(), parts.path or "/", parts.query, ""))

    async def async_supported(self) -> bool:
        if self._supported is not None:
            return self._supported
        try:
            await self._async_ensure()
        except OnvifPtzUnsupported:
            return False
        return True

    async def async_move(
        self,
        *,
        pan: float,
        tilt: float,
        zoom: float,
        duration_ms: int,
    ) -> None:
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not -1 <= value <= 1
            for value in (pan, tilt, zoom)
        ):
            raise OnvifPtzError("ONVIF velocity must be finite and between -1 and 1")
        if type(duration_ms) is not int or not 1 <= duration_ms <= 60000:
            raise OnvifPtzError("ONVIF duration must be an integer from 1 to 60000 ms")
        pan_tilt = bool(pan or tilt)
        zoom_active = bool(zoom)
        async with self._move_lock:
            await self._async_ensure()
            if not pan_tilt and not zoom_active:
                return
            if self._ptz_url is None or self._profile_token is None:
                raise OnvifPtzUnsupported("ONVIF PTZ is not available")
            velocity = ""
            if pan_tilt:
                velocity += (
                    f'<tt:PanTilt x="{pan:.4f}" y="{tilt:.4f}" '
                    f'space="{_PAN_TILT_SPACE}"/>'
                )
            if zoom_active:
                velocity += f'<tt:Zoom x="{zoom:.4f}" space="{_ZOOM_SPACE}"/>'
            body = (
                "<tptz:ContinuousMove>"
                f"<tptz:ProfileToken>{escape(self._profile_token)}</tptz:ProfileToken>"
                f"<tptz:Velocity>{velocity}</tptz:Velocity>"
                f"<tptz:Timeout>PT{max(1.0, duration_ms / 1000):.3f}S</tptz:Timeout>"
                "</tptz:ContinuousMove>"
            )
            failed = False
            try:
                await self._async_post(self._ptz_url, _PTZ_NS, "ContinuousMove", body)
                await asyncio.sleep(duration_ms / 1000)
            except BaseException:
                failed = True
                raise
            finally:
                try:
                    await self._async_finish_stop(pan_tilt, zoom_active)
                except OnvifPtzError:
                    if not failed:
                        raise

    async def _async_finish_stop(self, pan_tilt: bool, zoom: bool) -> None:
        task = asyncio.create_task(self._async_stop(pan_tilt, zoom))
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
            except OnvifPtzError:
                if not cancelled:
                    raise
                break
        if cancelled:
            raise asyncio.CancelledError

    async def _async_stop(self, pan_tilt: bool, zoom: bool) -> None:
        if self._ptz_url is None or self._profile_token is None:
            return
        await self._async_post(
            self._ptz_url,
            _PTZ_NS,
            "Stop",
            "<tptz:Stop>"
            f"<tptz:ProfileToken>{escape(self._profile_token)}</tptz:ProfileToken>"
            f"<tptz:PanTilt>{str(pan_tilt).lower()}</tptz:PanTilt>"
            f"<tptz:Zoom>{str(zoom).lower()}</tptz:Zoom>"
            "</tptz:Stop>",
        )

    async def _async_ensure(self) -> None:
        async with self._lock:
            if self._ptz_url and self._profile_token:
                return
            if self._supported is False:
                raise OnvifPtzUnsupported("ONVIF PTZ is not available")
            response = await self._async_post(
                self.device_url,
                _DEVICE_NS,
                "GetCapabilities",
                "<tds:GetCapabilities><tds:Category>All</tds:Category>"
                "</tds:GetCapabilities>",
            )
            caps = response.find(f"{{{_DEVICE_NS}}}Capabilities")
            if caps is None:
                raise OnvifPtzError("ONVIF GetCapabilities returned no capabilities")
            media = caps.find(f"{{{_SCHEMA_NS}}}Media")
            if media is None:
                raise OnvifPtzError("ONVIF camera did not advertise a Media service")
            media_url = self._service_url(media.findtext(f"{{{_SCHEMA_NS}}}XAddr"))
            ptz = caps.find(f"{{{_SCHEMA_NS}}}PTZ")
            if ptz is None:
                self._supported = False
                raise OnvifPtzUnsupported("ONVIF camera has no PTZ service")
            ptz_url = self._service_url(ptz.findtext(f"{{{_SCHEMA_NS}}}XAddr"))
            profiles = await self._async_post(media_url, _MEDIA_NS, "GetProfiles", "<trt:GetProfiles/>")
            try:
                token = _select_profile(profiles, self._requested_profile)
            except OnvifPtzUnsupported:
                self._supported = False
                raise
            self._ptz_url = ptz_url
            self._profile_token = token
            self._supported = True

    async def _async_post(
        self, url: str, namespace: str, operation: str, body: str
    ) -> ET.Element:
        action = f"{namespace}/{operation}"
        try:
            async with self._session.post(
                url,
                data=_envelope(body, self._username, self._password).encode(),
                headers={
                    "Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"',
                    "SOAPAction": f'"{action}"',
                },
                timeout=ClientTimeout(total=_REQUEST_TIMEOUT),
                allow_redirects=False,
            ) as response:
                status = response.status
                raw = bytearray()
                while len(raw) <= _MAX_RESPONSE_BYTES:
                    chunk = await response.content.read(min(65536, _MAX_RESPONSE_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise OnvifPtzError(f"ONVIF {operation} response exceeded size limit")
        except asyncio.TimeoutError:
            raise OnvifPtzError(
                f"ONVIF {operation} timed out; check camera connectivity and ONVIF port in Configure"
            ) from None
        except (ClientError, OSError):
            raise OnvifPtzError(
                f"ONVIF {operation} could not connect; check camera connectivity and ONVIF port in Configure"
            ) from None
        if status in {401, 403}:
            raise OnvifPtzError(
                f"ONVIF {operation} authentication failed; check the local "
                "camera credentials, ONVIF permissions and camera clock"
            )
        if not 200 <= status < 300:
            root = _envelope_root(bytes(raw))
            fault = _find_fault(root) if root is not None else None
            if fault is not None:
                raise _fault_error(fault, operation)
            raise OnvifPtzError(f"ONVIF {operation} failed (HTTP {status})")
        return _parse_response(bytes(raw), namespace, operation)
