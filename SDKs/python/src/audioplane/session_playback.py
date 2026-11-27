"""Serialize PCM writes and interruption within one AudioSession output."""

import asyncio
from typing import Callable, Optional

from sonexis import AudioOutput, SonexisError

from .speech import SpeechSynthesizer


class _SessionPlayback:
    def __init__(self, output: AudioOutput, mark_runtime_failure: Callable[[SonexisError], None]) -> None:
        self._output = output
        self._mark_runtime_failure = mark_runtime_failure
        self._write_lock = asyncio.Lock()
        self._speaking = False
        self._interrupted = False
        self._submitted_pcm = False
        self._next_chunk: Optional[asyncio.Future] = None

    async def write(self, data: bytes) -> None:
        async with self._write_lock:
            await self._write_pcm(data)

    async def interrupt(self, *, only_if_active: bool = False) -> None:
        if only_if_active and not (self._speaking or self._submitted_pcm):
            return
        if self._speaking:
            self._interrupted = True
            if self._next_chunk is not None:
                self._next_chunk.cancel()
        # AudioOutput treats cancellation of an in-flight write as terminal.
        # Finish that write before rotating the output stream with flush.
        async with self._write_lock:
            try:
                await self._output.flush()
            except SonexisError as error:
                self._mark_runtime_failure(error)
                raise
            self._submitted_pcm = False

    async def say(self, text: str, synthesizer: SpeechSynthesizer) -> None:
        if self._speaking:
            raise RuntimeError("AudioSession.say() already has an active synthesis stream")
        self._speaking = True
        self._interrupted = False
        try:
            audio = synthesizer.synthesize(text, format=self._output.info.format)
            try:
                while not self._interrupted:
                    pending = asyncio.ensure_future(audio.__anext__())
                    self._next_chunk = pending
                    # Provider cancellation during barge-in ends this response.
                    # Cancellation of the caller itself still propagates.
                    chunk, = await asyncio.gather(pending, return_exceptions=True)
                    self._next_chunk = None
                    if isinstance(chunk, StopAsyncIteration):
                        return
                    if self._interrupted and isinstance(chunk, asyncio.CancelledError):
                        return
                    if isinstance(chunk, BaseException):
                        raise chunk
                    await self._write_speech(chunk)
            finally:
                self._next_chunk = None
                await audio.aclose()
        finally:
            self._speaking = False

    async def _write_speech(self, data: bytes) -> None:
        format = self._output.info.format
        frame_size = format.channels * format.sample_format.bytes_per_sample
        if len(data) % frame_size:
            raise ValueError("synthesized PCM must contain whole interleaved sample frames")
        # Give interruption a chance between short writes even when the
        # provider returns an entire response in a single chunk.
        packet_size = max(1, format.sample_rate * 40 // 1000) * frame_size
        minimum_size = max(1, format.sample_rate // 1000) * frame_size
        for offset in range(0, len(data), packet_size):
            end = min(offset + packet_size, len(data))
            if 0 < len(data) - end < minimum_size:
                end = len(data)
            async with self._write_lock:
                if self._interrupted:
                    return
                await self._write_pcm(data[offset:end])
            if end == len(data):
                return
            await asyncio.sleep(0)

    async def _write_pcm(self, data: bytes) -> None:
        if data:
            self._submitted_pcm = True
        try:
            await self._output.write(data)
        except SonexisError as error:
            self._mark_runtime_failure(error)
            raise
