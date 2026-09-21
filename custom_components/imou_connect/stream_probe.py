from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from .media import local_rtsp_source, normalize_local_camera

_LOGGER = logging.getLogger(__name__)

_STREAM_CHECK_TIMEOUT = 20.0


def _open_and_check_stream(source: str) -> str:
    import av

    try:
        with av.open(
            source,
            mode="r",
            format="rtsp",
            options={"rtsp_transport": "tcp"},
            timeout=(_STREAM_CHECK_TIMEOUT, _STREAM_CHECK_TIMEOUT),
        ) as container:
            if not container.streams.video:
                return "stream_no_video"
            stream = container.streams.video[0]
            for _ in container.decode(stream):
                return ""
            return "stream_no_video"
    except av.error.HTTPUnauthorizedError:
        return "stream_unauthorized"
    except av.error.HTTPForbiddenError:
        return "stream_unauthorized"
    except av.error.HTTPNotFoundError:
        return "stream_not_found"
    except av.error.TimeoutError:
        return "stream_timeout"
    except av.error.ExitError:
        return "stream_timeout"
    except ConnectionRefusedError:
        return "stream_unreachable"
    except av.error.FFmpegError:
        return "stream_invalid"
    except OSError:
        return "stream_unreachable"


async def async_check_local_stream(value: Mapping[str, Any]) -> str:
    """Probe one local RTSP configuration. Returns an error key or empty string."""
    try:
        config = normalize_local_camera(value)
    except ValueError:
        return "stream_invalid"
    source = local_rtsp_source(config)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_open_and_check_stream, source),
            timeout=_STREAM_CHECK_TIMEOUT * 2 + 5,
        )
    except TimeoutError:
        return "stream_timeout"
    except (OSError, ValueError):
        _LOGGER.debug("Local RTSP probe failed unexpectedly")
        return "stream_invalid"