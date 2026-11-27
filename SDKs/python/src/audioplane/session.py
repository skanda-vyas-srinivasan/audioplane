"""Compose local audio activity, caller callbacks, and optional playback."""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import asdict
from inspect import isawaitable
from typing import AsyncIterable, AsyncIterator, Awaitable, Callable, List, Optional, TypeVar, Union

from sonexis import (
    ActivityDetectionConfig,
    ActivityEvent,
    AudioActivityDetector,
    AudioFormat,
    AudioFrame,
    AudioOutput,
    OutputDestinationSelector,
    ReplayStream,
    Sonexis,
    SonexisError,
    SourceSelector,
    VoiceActivityDetector,
)

from .session_playback import _SessionPlayback
from .speech import SpeechSynthesizer, TranscriptEvent, Transcriber
from .utterance import UtteranceEvent, _UtteranceBuffer

_ActivityHandler = Callable[[ActivityEvent], Optional[Awaitable[None]]]
_Handler = TypeVar("_Handler", bound=_ActivityHandler)
_FrameHandler = Callable[[AudioFrame], Optional[Awaitable[None]]]
_PCMHandler = TypeVar("_PCMHandler", bound=_FrameHandler)
_UtteranceHandler = Callable[[UtteranceEvent], Optional[Awaitable[None]]]
_SegmentHandler = TypeVar("_SegmentHandler", bound=_UtteranceHandler)
_TranscriptHandler = Callable[[TranscriptEvent], Optional[Awaitable[None]]]
_TextHandler = TypeVar("_TextHandler", bound=_TranscriptHandler)
_SessionEvent = Union[ActivityEvent, UtteranceEvent]
_Result = TypeVar("_Result")


class CallbackQueueFullError(RuntimeError):
    """The session's callback worker fell behind its configured pending-event limit."""


class AudioSession:
    """Run local activity callbacks from one live source or replay stream.

    The session owns the replay or capture stream for the duration of ``run``.
    Supply an already connected client to manage its connection yourself.
    Each session, including its optional voice detector, belongs to one run.
    """

    def __init__(
        self,
        listen: Union[SourceSelector, ReplayStream],
        *,
        speak: Optional[OutputDestinationSelector] = None,
        input_format: Optional[AudioFormat] = None,
        output_format: Optional[AudioFormat] = None,
        activity_config: ActivityDetectionConfig = ActivityDetectionConfig(),
        voice_activity_detector: Optional[VoiceActivityDetector] = None,
        client: Optional[Sonexis] = None,
        max_utterance_ms: int = 30_000,
        transcriber: Optional[Transcriber] = None,
        synthesizer: Optional[SpeechSynthesizer] = None,
        callback_queue_size: Optional[int] = None,
        interrupt_on_activity: bool = False,
    ) -> None:
        if (isinstance(max_utterance_ms, bool) or not isinstance(max_utterance_ms, int)
                or max_utterance_ms < 1):
            raise ValueError("max_utterance_ms must be a positive integer")
        if callback_queue_size is not None and (isinstance(callback_queue_size, bool)
                or not isinstance(callback_queue_size, int) or callback_queue_size < 1):
            raise ValueError("callback_queue_size must be a positive integer")
        if interrupt_on_activity and speak is None:
            raise ValueError("interrupt_on_activity requires an output destination")
        if isinstance(listen, ReplayStream):
            if input_format is not None and input_format != listen.format:
                raise ValueError("input_format must match the replay format")
            input_format = listen.format
        self._listen = listen
        self._speak = speak
        self._input_format = input_format or AudioFormat.speech_16k()
        self._output_format = output_format or self._input_format
        self._client = client
        self._activity = AudioActivityDetector(
            activity_config, voice_activity_detector=voice_activity_detector)
        self._handlers: List[_ActivityHandler] = []
        self._frame_handlers: List[_FrameHandler] = []
        self._utterance_handlers: List[_UtteranceHandler] = []
        self._transcript_handlers: List[_TranscriptHandler] = []
        self._transcriber = transcriber
        self._synthesizer = synthesizer
        self._callback_queue_size = callback_queue_size or (32 if interrupt_on_activity else None)
        self._interrupt_on_activity = interrupt_on_activity
        self._max_utterance_ms = max_utterance_ms
        self._playback: Optional[_SessionPlayback] = None
        self._runtime_failure: Optional[SonexisError] = None
        self._monitor_output = False
        self._started = False

    @property
    def output_format(self) -> AudioFormat:
        """The PCM format expected by :meth:`play_pcm`."""
        return self._output_format

    def on_activity(self, handler: _Handler) -> _Handler:
        """Register a sync or async handler before running the session."""
        if self._started:
            raise RuntimeError("Register activity handlers before running the session")
        self._handlers.append(handler)
        return handler

    def on_frame(self, handler: _PCMHandler) -> _PCMHandler:
        """Handle every PCM packet inline, before detection and queued callbacks."""
        if self._started:
            raise RuntimeError("Register frame handlers before running the session")
        self._frame_handlers.append(handler)
        return handler

    def on_utterance(self, handler: _SegmentHandler) -> _SegmentHandler:
        """Register a sync or async handler for PCM delimited by activity detection."""
        if self._started:
            raise RuntimeError("Register utterance handlers before running the session")
        self._utterance_handlers.append(handler)
        return handler

    def on_transcript(self, handler: _TextHandler) -> _TextHandler:
        """Register a sync or async handler for final text from a configured transcriber."""
        if self._started:
            raise RuntimeError("Register transcript handlers before running the session")
        if self._transcriber is None:
            raise RuntimeError("Transcript handlers require a configured transcriber")
        self._transcript_handlers.append(handler)
        return handler

    async def run(self) -> None:
        """Dispatch audio events in order until the input ends or the task is cancelled."""
        if self._started:
            raise RuntimeError("AudioSession.run() can only be called once")
        self._started = True
        async with AsyncExitStack() as resources:
            replay = self._listen if isinstance(self._listen, ReplayStream) else None
            if replay is not None:
                resources.push_async_callback(replay.aclose)

            client = self._client
            if client is None and (replay is None or self._speak is not None):
                client = Sonexis()
                resources.push_async_callback(client.close)
                await self._runtime_call(client.connect())

            if isinstance(self._listen, ReplayStream):
                frames = self._listen
            else:
                assert client is not None
                capture = await self._runtime_call(client.capture(self._listen, format=self._input_format))
                frames = self._capture_frames(await resources.enter_async_context(capture))

            output = None
            if self._speak is not None:
                assert client is not None
                output_context = await self._runtime_call(client.playback(
                    destination=self._speak, format=self.output_format))
                output = await self._runtime_call(output_context.__aenter__())

                async def close_output(*exc_info) -> None:
                    closing = output_context.__aexit__(*exc_info)
                    if exc_info[0] is None:
                        await self._runtime_call(closing)
                    else:
                        await closing

                resources.push_async_exit(close_output)
                self._playback = _SessionPlayback(output, self._mark_runtime_failure)

            try:
                if self._monitor_output and output is not None:
                    assert client is not None
                    await self._run_monitored(frames, client, output)
                else:
                    await self._consume(frames)
            finally:
                self._playback = None

    async def _consume(self, frames: AsyncIterable[AudioFrame]) -> None:
        if self._callback_queue_size is None:
            await self._read_frames(frames, self._dispatch_event)
        else:
            await self._run_queued(frames, self._callback_queue_size)

    async def _run_monitored(self, frames: AsyncIterable[AudioFrame], client: Sonexis,
                             output: AudioOutput) -> None:
        async def watch() -> None:
            while True:
                await asyncio.sleep(0.5)
                info = await client.output_status(output.info.id)
                if info.state == "failed":
                    if info.error is None:
                        raise SonexisError("output_failed", "Runtime output failed while idle")
                    raise SonexisError.from_response({"error": asdict(info.error)})

        consuming = asyncio.create_task(self._consume(frames))
        watching = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait((consuming, watching), return_when=asyncio.FIRST_COMPLETED)
            if consuming in done:
                consuming.result()
            else:
                await self._runtime_call(watching)
        finally:
            for task in (consuming, watching):
                if not task.done():
                    task.cancel()
            await asyncio.gather(consuming, watching, return_exceptions=True)

    def _mark_runtime_failure(self, error: SonexisError) -> None:
        self._runtime_failure = error

    async def _runtime_call(self, call: Awaitable[_Result]) -> _Result:
        try:
            return await call
        except SonexisError as error:
            self._mark_runtime_failure(error)
            raise

    async def _capture_frames(self, capture: AsyncIterator[AudioFrame]) -> AsyncIterator[AudioFrame]:
        try:
            async for frame in capture:
                yield frame
        except SonexisError as error:
            self._mark_runtime_failure(error)
            raise

    async def _run_queued(self, frames: AsyncIterable[AudioFrame], capacity: int) -> None:
        queue: asyncio.Queue[Optional[_SessionEvent]] = asyncio.Queue(maxsize=capacity)

        async def enqueue(event: _SessionEvent) -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                raise CallbackQueueFullError(
                    f"AudioSession exceeded its limit of {capacity} pending callback events") from None
            # Replay or already-buffered live reads can complete without yielding.
            await asyncio.sleep(0)

        async def read() -> None:
            await self._read_frames(frames, enqueue)
            await queue.put(None)

        async def dispatch() -> None:
            while True:
                event = await queue.get()
                if event is None:
                    return
                await self._dispatch_event(event)

        tasks = (asyncio.create_task(read(), name="audioplane-session-reader"),
                 asyncio.create_task(dispatch(), name="audioplane-session-callbacks"))
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if pending:
                await asyncio.wait(pending)
                for task in pending:
                    task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _read_frames(self, frames: AsyncIterable[AudioFrame],
                           emit: Callable[[_SessionEvent], Awaitable[None]]) -> None:
        utterances = None
        if self._utterance_handlers or self._transcript_handlers:
            utterances = _UtteranceBuffer(self._max_utterance_ms)
        detect_activity = bool(self._handlers or utterances is not None or self._interrupt_on_activity)
        async for frame in frames:
            for handler in self._frame_handlers:
                result = handler(frame)
                if isawaitable(result):
                    await result
            if not detect_activity:
                continue
            event = self._activity.observe(frame)
            if event is not None:
                if self._interrupt_on_activity and event.type == "activity_started":
                    assert self._playback is not None
                    await self._playback.interrupt(only_if_active=True)
                if self._handlers:
                    await emit(event)
            if utterances is not None:
                for utterance in utterances.observe(frame, self._activity, event):
                    await emit(utterance)
        if utterances is not None:
            final = utterances.finish("end_of_input")
            if final is not None:
                await emit(final)

    async def _dispatch_event(self, event: _SessionEvent) -> None:
        if isinstance(event, UtteranceEvent):
            await self._dispatch_utterance(event)
            return
        for handler in self._handlers:
            result = handler(event)
            if isawaitable(result):
                await result

    async def _dispatch_utterance(self, utterance: UtteranceEvent) -> None:
        for handler in self._utterance_handlers:
            result = handler(utterance)
            if isawaitable(result):
                await result
        if self._transcript_handlers:
            assert self._transcriber is not None
            text = await self._transcriber.transcribe(utterance)
            if text.strip():
                transcript = TranscriptEvent(text, utterance)
                for transcript_handler in self._transcript_handlers:
                    result = transcript_handler(transcript)
                    if isawaitable(result):
                        await result

    async def play_pcm(self, data: bytes) -> None:
        """Write interleaved PCM in ``output_format`` while the session is running."""
        playback = self._playback
        if playback is None:
            raise RuntimeError("PCM playback requires a running AudioSession with speak set")
        await playback.write(data)

    async def interrupt_playback(self) -> None:
        """Stop current synthesis and discard queued PCM while preserving the output route."""
        playback = self._playback
        if playback is None:
            raise RuntimeError("Playback interruption requires a running AudioSession with speak set")
        await playback.interrupt()

    async def say(self, text: str) -> None:
        """Synthesize text and enqueue PCM while the session's output is active."""
        if not text.strip():
            return
        if self._synthesizer is None:
            raise RuntimeError("Speech playback requires a configured synthesizer")
        playback = self._playback
        if playback is None:
            raise RuntimeError("Speech playback requires a running AudioSession with speak set")
        await playback.say(text, self._synthesizer)
