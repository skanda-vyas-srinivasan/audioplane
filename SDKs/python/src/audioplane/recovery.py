"""Opt-in replacement of failed live audio sessions."""

import asyncio
import math
from typing import Callable, Optional

from sonexis import (AudioOutputDestination, AudioSource, OutputDestinationSelector,
                     Sonexis, SonexisConnectionError, SonexisError, SourceSelector)

from .session import AudioSession

_RECOVERABLE_ROUTE_ERRORS = frozenset({
    "source_not_found",
    "source_unavailable",
    "capture_failed",  # Older runtimes use this for an exited process tap.
    "capture_ended_during_start",
    "microphone_device_changed",
    "output_destination_not_found",
    "output_destination_unavailable",
    "output_destination_disconnected",
    "output_device_unavailable",
    "output_device_change_failed",
    "output_unavailable",
})


async def run_recovering_session(
    listen: SourceSelector,
    create_session: Callable[[AudioSource, Optional[AudioOutputDestination], Sonexis], AudioSession],
    *,
    speak: Optional[OutputDestinationSelector] = None,
    socket_path: Optional[str] = None,
    max_restarts: int = 12,
    retry_delay: float = 0.5,
) -> None:
    """Run a fresh live session after recoverable Runtime or endpoint failures.

    The factory receives the resolved input and output and an already connected
    client. Pass those objects to a new ``AudioSession`` and register its
    callbacks before returning it. Each attempt owns an independent connection.
    """
    if (isinstance(max_restarts, bool) or not isinstance(max_restarts, int)
            or max_restarts < 0):
        raise ValueError("max_restarts must be a nonnegative integer")
    if not math.isfinite(retry_delay) or retry_delay < 0:
        raise ValueError("retry_delay must be finite and nonnegative")

    # Retain the resolved objects so SDK lookups match by ID even if a
    # different application or device appears under the same display name.
    pinned_source: Optional[AudioSource] = None
    pinned_destination: Optional[AudioOutputDestination] = None
    for attempt in range(max_restarts + 1):
        client = Sonexis(socket_path)
        runtime_operation = True
        session = None
        try:
            await client.connect()
            pinned_source = await client.get_source(pinned_source or listen)
            if speak is not None:
                pinned_destination = await client.get_output_destination(pinned_destination or speak)

            runtime_operation = False
            session = create_session(pinned_source, pinned_destination, client)
            if (session._client is not client or session._listen != pinned_source
                    or session._speak != pinned_destination):
                raise ValueError("Create the recovery session with its supplied source, destination and client")
            session._monitor_output = pinned_destination is not None
            await session.run()
            # Control loss can close the capture iterator before it sees the
            # broken data socket. Confirm a clean EOF on the live connection.
            runtime_operation = True
            await client.status()
            return
        except SonexisError as error:
            if not runtime_operation and (session is None or session._runtime_failure is not error):
                raise
            if attempt == max_restarts or not await _can_restart(error, client):
                raise
        finally:
            await client.close()
        await asyncio.sleep(retry_delay)


async def _can_restart(error: SonexisError, client: Sonexis) -> bool:
    if isinstance(error, SonexisConnectionError):
        return error.retryable or error.code == "not_connected"
    if error.code in _RECOVERABLE_ROUTE_ERRORS:
        return True
    if error.code == "truncated_pcm_stream":
        # A broken PCM stream alone can indicate a slow consumer or malformed
        # data. Restart only when the corresponding control connection died.
        try:
            await client.status()
        except SonexisConnectionError:
            return True
    return False
