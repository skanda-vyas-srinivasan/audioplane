"""Operator-driven checks for a removable Core Audio input and optional output."""

import asyncio
import time
from contextlib import AsyncExitStack
from typing import Optional

from audioplane import AudioPlane, CaptureFailedError, SourceNotFoundError


async def verify_default_route(client: AudioPlane) -> dict:
    """Verify that one output stream follows a default switch and switch back."""
    default = await client.get_output_destination('default')
    original_device = default.active_device_id
    if original_device is None:
        raise ValueError('The Runtime has no active default playback device')
    output = await client.playback(destination=default)
    original = output.info

    async def wait_for_route(*, returning, after_changes):
        while True:
            destination = await client.get_output_destination('default')
            active = destination.active_device_id
            info = await client.output_status(original.id)
            if info.state in ('failed', 'stopped', 'cancelled') or info.stream_id != original.stream_id:
                raise AssertionError('The default output lost its original stream during the switch')
            if active and (active == original_device) == returning and info.metrics.route_changes > after_changes:
                return active, info.metrics.route_changes
            await asyncio.sleep(.1)

    try:
        print('Select a different default macOS output now. No PCM will be played.', flush=True)
        alternate, changes = await asyncio.wait_for(wait_for_route(
            returning=False, after_changes=original.metrics.route_changes), 45)
        print('Restore the original default macOS output now.', flush=True)
        restored, final_changes = await asyncio.wait_for(wait_for_route(
            returning=True, after_changes=changes), 45)
        return dict(original_device=original_device, alternate_device=alternate, restored_device=restored,
                    stream_id=original.stream_id, route_changes=final_changes - original.metrics.route_changes)
    finally:
        await output.aclose(drain=False)


async def verify_hotplug(client: AudioPlane, source_id: str, output_id: Optional[str]) -> dict:
    """Measure reconnection using the same IDs and a different stream epoch."""
    source = await client.get_source(source_id)
    if source.id != source_id or source.kind != 'microphone':
        raise ValueError('Use the exact Runtime microphone ID of the removable device')
    destination = await client.get_output_destination(output_id) if output_id is not None else None
    if destination is not None and (destination.id != output_id or destination.kind != 'playback'):
        raise ValueError('Use the exact Runtime playback ID of the removable device')

    async with AsyncExitStack() as streams:
        capture = await streams.enter_async_context(await client.capture(source))
        original = await asyncio.wait_for(capture.__anext__(), 3)
        output = None
        if destination is not None:
            output = await client.playback(destination=destination)
            streams.push_async_callback(output.aclose, drain=False)

        print(f'Disconnect {source.name} now. The capture samples are discarded.', flush=True)

        async def wait_for_failure():
            try:
                async for _ in capture:
                    pass
            except CaptureFailedError as error:
                return error.code
            raise AssertionError('The capture ended without a device failure')

        capture_error = await asyncio.wait_for(wait_for_failure(), 45)

        async def wait_for_removal():
            while True:
                try:
                    await client.get_source(source)
                except SourceNotFoundError:
                    return
                await asyncio.sleep(.1)

        await asyncio.wait_for(wait_for_removal(), 5)
        if output is not None:
            async def output_failure():
                while True:
                    info = await client.output_status(output.info.id)
                    if info.state in ('failed', 'stopped', 'cancelled'):
                        return info.error.code if info.error is not None else None
                    await asyncio.sleep(.1)

            if await asyncio.wait_for(output_failure(), 3) != 'output_destination_disconnected':
                raise AssertionError('The selected output did not report a pinned-device disconnect')

    print(f'Reconnect {source.name} now.', flush=True)
    current = await client.wait_for_source(source, timeout=45, poll_interval=.1)
    seen_ns = time.monotonic_ns()
    async with await client.capture(current) as capture:
        replacement = await asyncio.wait_for(capture.__anext__(), 3)
        resumed_ns = time.monotonic_ns()
        if replacement.source_id != original.source_id or replacement.stream_id == original.stream_id:
            raise AssertionError('Reconnected capture did not open a new stream for the same input')
    result = dict(source_id=source.id, capture_failure_code=capture_error,
                  rediscovery_to_pcm_ms=round((resumed_ns - seen_ns) / 1_000_000, 2),
                  old_capture_stream=original.stream_id, new_capture_stream=replacement.stream_id)
    if destination is not None:
        current_output = await client.wait_for_output_destination(destination, timeout=45, poll_interval=.1)
        output_after = await client.playback(destination=current_output)
        try:
            if output_after.info.stream_id == output.info.stream_id:
                raise AssertionError('Reconnected output reused the previous stream')
            result.update(destination_id=destination.id, old_output_stream=output.info.stream_id,
                          new_output_stream=output_after.info.stream_id)
        finally:
            await output_after.aclose(drain=False)
    return result
