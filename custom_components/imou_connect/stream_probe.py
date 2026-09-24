from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from threading import Event, Lock
from time import monotonic
from typing import Any

from .media import local_rtsp_source, normalize_local_camera

_LOGGER = logging.getLogger(__name__)

_STREAM_CHECK_TIMEOUT = 20.0
_STREAM_WORKER_LOCK = Lock()


def _open_and_check_stream(source: str, cancelled: Event, deadline: float) -> str:
    import av

    def expired() -> bool:
        return cancelled.is_set() or monotonic() >= deadline

    try:
        if expired():
            return "stream_timeout"
        read_timeout = min(_STREAM_CHECK_TIMEOUT, deadline - monotonic())
        if read_timeout <= 0:
            return "stream_timeout"
        with av.open(
            source,
            mode="r",
            format="rtsp",
            options={"rtsp_transport": "tcp"},
            timeout=(read_timeout, read_timeout),
        ) as container:
            if expired():
                return "stream_timeout"
            if not container.streams.video:
                return "stream_no_video"
            stream = container.streams.video[0]
            packets = iter(container.demux())
            while not expired():
                packet = next(packets, None)
                if expired():
                    return "stream_timeout"
                if packet is None:
                    return "stream_no_video"
                if packet.stream == stream:
                    for _ in packet.decode():
                        return "stream_timeout" if expired() else ""
            return "stream_timeout"
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


def _check_stream_worker(source: str, cancelled: Event, deadline: float) -> str:
    try:
        return _open_and_check_stream(source, cancelled, deadline)
    finally:
        _STREAM_WORKER_LOCK.release()


def _consume_worker_result(worker: asyncio.Future[str]) -> None:
    with suppress(Exception, asyncio.CancelledError):
        worker.result()


async def async_check_local_stream(value: Mapping[str, Any]) -> str:
    """Probe one local RTSP configuration. Returns an error key or empty string."""
    try:
        config = normalize_local_camera(value)
    except ValueError:
        return "stream_invalid"
    source = local_rtsp_source(config)
    deadline = monotonic() + _STREAM_CHECK_TIMEOUT * 2 + 5
    while not _STREAM_WORKER_LOCK.acquire(blocking=False):
        if monotonic() >= deadline:
            return "stream_timeout"
        await asyncio.sleep(0.05)
    cancelled = Event()
    try:
        worker = asyncio.get_running_loop().run_in_executor(
            None, _check_stream_worker, source, cancelled, deadline
        )
    except RuntimeError:
        _STREAM_WORKER_LOCK.release()
        _LOGGER.debug("Local RTSP probe could not start")
        return "stream_invalid"
    worker.add_done_callback(_consume_worker_result)
    try:
        return await asyncio.wait_for(
            asyncio.shield(worker), timeout=max(0, deadline - monotonic())
        )
    except (TimeoutError, asyncio.CancelledError) as err:
        cancelled.set()
        with suppress(Exception):
            await asyncio.wait_for(
                asyncio.shield(worker), timeout=_STREAM_CHECK_TIMEOUT + 5
            )
        if isinstance(err, asyncio.CancelledError):
            raise
        return "stream_timeout"
    except (OSError, ValueError, RuntimeError):
        _LOGGER.debug("Local RTSP probe failed unexpectedly")
        return "stream_invalid"
    finally:
        cancelled.set()
