"""Helpers for extracting signed media URLs from Imou responses."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

_STREAM_KEY_PRIORITY = (
    "hlsurl",
    "rtspurl",
    "rtmpurl",
    "flvurl",
    "playurl",
    "streamurl",
    "liveurl",
    "tlsresource",
    "resource",
    "url",
    "",
)
_IMAGE_KEY_PARTS = ("snapshot", "thumbnail", "picture", "image", "picurl")
_SUPPORTED_SCHEMES = frozenset(
    {"http", "https", "rtmp", "rtmps", "rtsp", "rtsps"}
)


def _normalized_key(value: Any) -> str:
    return "".join(
        character for character in str(value).casefold() if character.isalnum()
    )


def _decoded_json(value: str) -> Any:
    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _valid_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 16384:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme.casefold() not in _SUPPORTED_SCHEMES or not parsed.netloc:
        return None
    marker = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".casefold()
    if any(part in marker for part in ("rtsv1", "rtsv2", "lchttp")):
        return None
    return candidate


def _walk(value: Any, depth: int = 0) -> Iterable[tuple[str, str]]:
    if depth > 5:
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = _normalized_key(key)
            if url := _valid_url(child):
                yield key_text, url
                continue
            if isinstance(child, str) and (decoded := _decoded_json(child)) is not None:
                yield from _walk(decoded, depth + 1)
            elif isinstance(child, (Mapping, list, tuple)):
                yield from _walk(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            if url := _valid_url(child):
                yield "", url
            elif isinstance(child, str) and (decoded := _decoded_json(child)) is not None:
                yield from _walk(decoded, depth + 1)
            elif isinstance(child, (Mapping, list, tuple)):
                yield from _walk(child, depth + 1)
    elif url := _valid_url(value):
        yield "", url


def extract_stream_url(payload: Any) -> str | None:
    """Return the best HA-compatible stream URL from a cloud response."""
    urls = list(_walk(payload))
    for preferred_key in _STREAM_KEY_PRIORITY:
        for key, url in urls:
            if key == preferred_key:
                return url
    return None


def extract_image_url(payload: Any) -> str | None:
    """Return a URL explicitly identified as an image or snapshot."""
    for key, url in _walk(payload):
        if any(part in key for part in _IMAGE_KEY_PARTS):
            return url
    return None
