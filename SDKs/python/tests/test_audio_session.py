"""Regression coverage for the public activity session and stream ownership."""

import asyncio
import struct
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from audioplane import (ActivityDetectionConfig, AudioFormat, AudioPlane, AudioSession,
                       CallbackQueueFullError, ConversationHistory, ReplayStream)
from audioplane.providers import MacOSSpeechSynthesizer, OpenAITranscriber


class OutputRecorder:
    def __init__(self):
        self.info = SimpleNamespace(format=AudioFormat.speech_16k())
        self.write = mock.AsyncMock()
        self.flush = mock.AsyncMock()
        self.exit = mock.AsyncMock(return_value=None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.exit(exc_type, exc, traceback)


class ReplayWithGap(ReplayStream):
    async def __anext__(self):
        frame = await super().__anext__()
        return replace(frame, discontinuity=True) if frame.sequence == 2 else frame


class MacOSSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_rate_reaches_the_local_speech_renderer(self):
        format = AudioFormat.speech_16k()
        voice = MacOSSpeechSynthesizer(voice="Samantha", rate=130, format=format)
        pcm = b"\0\0" * 160
        with mock.patch("audioplane.providers.synthesize_speech", return_value=pcm) as render:
            self.assertEqual([chunk async for chunk in voice.synthesize("Hello", format=format)], [pcm])
            render.assert_called_once_with("Hello", "Samantha", 130, sample_rate=16000)
            with self.assertRaisesRegex(ValueError, "format must match"):
                await voice.synthesize("Hello", format=AudioFormat(48000, 1)).__anext__()
            render.assert_called_once()


class ConversationTests(unittest.TestCase):
    def test_bounds_context_without_mutating_prior_snapshots(self):
        history = ConversationHistory(max_turns=2, max_characters=8)
        history.append("user", "hello")
        snapshot = history.turns
        history.append("assistant", "hi")
        history.append("user", "world")
        self.assertEqual([turn.text for turn in history.turns], ["hi", "world"])
        self.assertEqual([turn.text for turn in snapshot], ["hello"])
        with self.assertRaises(ValueError):
            history.append("user", "123456789")
        self.assertEqual(len(history.turns), 2)
        history.clear()
        self.assertEqual(history.turns, ())


class AudioSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.wav = Path(directory.name) / "activity.wav"
        self.write_packets([4000, 4000, 0, 0])
        self.config = ActivityDetectionConfig(minimum_activity_ms=20, silence_duration_ms=20)

    async def test_replay_dispatches_ordered_edges_without_runtime(self):
        replay = ReplayStream.from_wav(self.wav, chunk_frames=160)
        session = AudioSession(replay, activity_config=self.config)
        handled = []

        @session.on_activity
        def sync_handler(event):
            handled.append(("sync", event))

        @session.on_activity
        async def async_handler(event):
            await asyncio.sleep(0)
            handled.append(("async", event))

        with mock.patch("audioplane.session.Sonexis") as new_client:
            await session.run()
        new_client.assert_not_called()
        self.assertEqual([(kind, event.type, event.sequence) for kind, event in handled], [
            ("sync", "activity_started", 1), ("async", "activity_started", 1),
            ("sync", "activity_ended", 3), ("async", "activity_ended", 3),
        ])
        for _, event in handled:
            self.assertEqual(event.source_id, replay.source.id)
            self.assertEqual(event.stream_id, replay.stream_id)
            self.assertEqual(event.timestamp_ns, event.sequence * 10_000_000)
        with self.assertRaisesRegex(RuntimeError, "only be called once"):
            await session.run()

    async def test_raw_callbacks_see_all_frames_including_silence_and_discontinuity(self):
        replay = ReplayWithGap.from_wav(self.wav, chunk_frames=160)
        session = AudioSession(replay, activity_config=self.config, callback_queue_size=1)
        frames = []
        session.on_frame(frames.append)

        await session.run()

        self.assertEqual([frame.sequence for frame in frames], [0, 1, 2, 3])
        self.assertTrue(frames[2].discontinuity)
        self.assertEqual(frames[3].data, b"\0\0" * 160)

    async def test_live_callback_can_write_pcm_and_shared_client_stays_open(self):
        capture = ReplayStream.from_wav(self.wav, chunk_frames=160)
        client = mock.create_autospec(AudioPlane, instance=True)
        output = OutputRecorder()
        client.capture.return_value = capture
        client.playback.return_value = output
        format = AudioFormat.speech_16k()
        session = AudioSession("Discord", speak="Virtual Microphone", client=client,
                               activity_config=self.config)
        pcm = b"\0\0" * 160

        @session.on_activity
        async def respond(event):
            if event.type == "activity_started":
                await session.play_pcm(pcm)

        await session.run()
        client.capture.assert_awaited_once_with("Discord", format=format)
        client.playback.assert_awaited_once_with(destination="Virtual Microphone", format=format)
        client.close.assert_not_awaited()
        output.write.assert_awaited_once_with(pcm)
        output.exit.assert_awaited_once_with(None, None, None)
        with self.assertRaisesRegex(RuntimeError, "running AudioSession"):
            await session.play_pcm(pcm)

    async def test_cancelled_handler_closes_owned_capture_output_and_connection(self):
        capture = ReplayStream.from_wav(self.wav, chunk_frames=160)
        with mock.patch.object(capture, "aclose", wraps=capture.aclose) as close_capture:
            client = mock.create_autospec(AudioPlane, instance=True)
            output = OutputRecorder()
            client.capture.return_value = capture
            client.playback.return_value = output
            session = AudioSession("Discord", speak="default", activity_config=self.config)
            entered = asyncio.Event()

            @session.on_activity
            async def block(event):
                entered.set()
                await asyncio.Future()

            with mock.patch("audioplane.session.Sonexis", return_value=client):
                task = asyncio.create_task(session.run())
                try:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

        client.connect.assert_awaited_once()
        client.close.assert_awaited_once()
        close_capture.assert_awaited_once()
        output.exit.assert_awaited_once()
        self.assertIs(output.exit.await_args.args[0], asyncio.CancelledError)

    async def test_output_open_failure_closes_capture_and_propagates(self):
        capture = ReplayStream.from_wav(self.wav, chunk_frames=160)
        client = mock.create_autospec(AudioPlane, instance=True)
        client.capture.return_value = capture
        client.playback.side_effect = OSError("output disappeared")
        session = AudioSession("Discord", speak="default", client=client)
        with mock.patch.object(capture, "aclose", wraps=capture.aclose) as close_capture:
            with self.assertRaisesRegex(OSError, "output disappeared"):
                await session.run()
        close_capture.assert_awaited_once()
        client.close.assert_not_awaited()

    def write_packets(self, amplitudes):
        with wave.open(str(self.wav), "wb") as writer:
            writer.setparams((1, 2, 16000, 0, "NONE", ""))
            writer.writeframes(b"".join(struct.pack("<h", value) * 160 for value in amplitudes))

    async def test_utterance_retains_confirmed_onset_and_calls_handlers_after_silence_edge(self):
        self.write_packets([0, 4000, 5000, 0, 0])
        replay = ReplayStream.from_wav(self.wav, chunk_frames=160)
        session = AudioSession(replay, activity_config=self.config)
        handled = []

        @session.on_activity
        def edge(event):
            handled.append(event.type)

        @session.on_utterance
        async def segment(event):
            await asyncio.sleep(0)
            handled.append(event)

        await session.run()
        self.assertEqual(handled[:2], ["activity_started", "activity_ended"])
        self.assertEqual(len(handled), 3)
        event = handled[-1]
        self.assertEqual(event.end_reason, "silence")
        self.assertEqual((event.start_timestamp_ns, event.end_timestamp_ns), (10_000_000, 50_000_000))
        self.assertEqual(event.data, struct.pack("<h", 4000) * 160 + struct.pack("<h", 5000) * 160 + b"\0\0" * 320)
        self.assertEqual(event.frame_count, 640)
        self.assertEqual(event.format, replay.format)
        self.assertEqual((event.source_id, event.session_id, event.stream_id),
                         (replay.source.id, replay.session_id, replay.stream_id))

    async def test_duration_limit_splits_inside_packet_and_flushes_partial_at_eof(self):
        self.write_packets([4000, 5000, 6000, 7000])
        replay = ReplayStream.from_wav(self.wav, chunk_frames=160)
        session = AudioSession(replay, activity_config=self.config, max_utterance_ms=13)
        segments = []
        session.on_utterance(segments.append)

        await session.run()

        self.assertEqual([item.end_reason for item in segments], ["duration_limit"] * 3 + ["end_of_input"])
        self.assertEqual([item.frame_count for item in segments], [208, 208, 208, 16])
        self.assertEqual([item.start_timestamp_ns for item in segments], [0, 13_000_000, 26_000_000, 39_000_000])
        self.assertEqual([item.end_timestamp_ns for item in segments], [13_000_000, 26_000_000, 39_000_000, 40_000_000])
        self.assertEqual(b"".join(item.data for item in segments),
                         b"".join(struct.pack("<h", value) * 160 for value in (4000, 5000, 6000, 7000)))

    async def test_capture_gap_ends_segment_and_excludes_unconfirmed_activity(self):
        self.write_packets([4000, 5000, 9000, 0])
        replay = ReplayWithGap.from_wav(self.wav, chunk_frames=160)
        session = AudioSession(replay, activity_config=self.config)
        segments = []
        session.on_utterance(segments.append)

        await session.run()

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].end_reason, "discontinuity")
        self.assertEqual(segments[0].data, struct.pack("<h", 4000) * 160 + struct.pack("<h", 5000) * 160)

    async def test_duration_limit_does_not_emit_a_silence_only_continuation(self):
        session = AudioSession(ReplayStream.from_wav(self.wav, chunk_frames=160),
                               activity_config=self.config, max_utterance_ms=20)
        segments = []
        session.on_utterance(segments.append)

        await session.run()

        self.assertEqual([item.end_reason for item in segments], ["duration_limit"])
        self.assertEqual(segments[0].frame_count, 320)

    async def test_transcript_callback_can_say_using_caller_supplied_providers(self):
        received = []

        async def transcribe(*, model, file):
            name, recording, content_type = file
            self.assertEqual((name, content_type, model), ("utterance.wav", "audio/wav", "my-model"))
            with wave.open(recording, "rb") as waveform:
                self.assertEqual((waveform.getframerate(), waveform.getnchannels(), waveform.getnframes()),
                                 (16000, 1, 640))
                self.assertEqual(waveform.readframes(160), struct.pack("<h", 4000) * 160)
            received.append("uploaded")
            return SimpleNamespace(text="Test transcript")

        async def synthesize(text, *, format):
            self.assertEqual((text, format), ("Echo: Test transcript", AudioFormat.speech_16k()))
            yield b"\0\0" * 160
            yield b"\1\0" * 160

        provider = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe)))
        client = mock.create_autospec(AudioPlane, instance=True)
        output = OutputRecorder()
        client.playback.return_value = output
        session = AudioSession(ReplayStream.from_wav(self.wav, chunk_frames=160), speak="default",
                               client=client, activity_config=self.config,
                               transcriber=OpenAITranscriber(provider, model="my-model"),
                               synthesizer=SimpleNamespace(synthesize=synthesize))
        utterances = []
        session.on_utterance(utterances.append)

        @session.on_transcript
        async def reply(event):
            self.assertIs(event.utterance, utterances[0])
            self.assertEqual(received, ["uploaded"])
            await session.say("Echo: " + event.text)

        published = []
        session.on_transcript(published.append)
        await session.run()

        self.assertEqual([event.text for event in published], ["Test transcript"])
        self.assertEqual(output.write.await_args_list, [mock.call(b"\0\0" * 160), mock.call(b"\1\0" * 160)])
        client.capture.assert_not_awaited()

    async def test_transcription_requires_registration_and_suppresses_blank_results(self):
        transcribe = mock.AsyncMock(return_value=" \n")
        for subscribe in (False, True):
            replay = ReplayStream.from_wav(self.wav, chunk_frames=160)
            session = AudioSession(replay, activity_config=self.config,
                                   transcriber=SimpleNamespace(transcribe=transcribe))
            callback = mock.Mock()
            if subscribe:
                session.on_transcript(callback)
            await session.run()
            self.assertEqual(transcribe.await_count, int(subscribe))
            callback.assert_not_called()

    async def test_playback_failure_closes_synthesis_generator(self):
        released = asyncio.Event()

        async def synthesize(text, *, format):
            try:
                yield b"\0\0" * 160
                self.fail("synthesis continued after the playback failure")
            finally:
                released.set()

        client = mock.create_autospec(AudioPlane, instance=True)
        output = OutputRecorder()
        output.write.side_effect = OSError("output gone")
        client.playback.return_value = output
        session = AudioSession(ReplayStream.from_wav(self.wav, chunk_frames=160), speak="default",
                               client=client, activity_config=self.config,
                               synthesizer=SimpleNamespace(synthesize=synthesize))

        @session.on_utterance
        async def reply(event):
            await session.say("Test reply")

        with self.assertRaisesRegex(OSError, "output gone"):
            await session.run()
        self.assertTrue(released.is_set())
        self.assertIs(output.exit.await_args.args[0], OSError)

    async def test_callback_worker_drains_in_order_at_end_of_input(self):
        replay = ReplayStream.from_wav(self.wav, chunk_frames=160)
        session = AudioSession(replay, activity_config=self.config, callback_queue_size=4)
        handled = []

        @session.on_activity
        async def edge(event):
            await asyncio.sleep(0)
            handled.append(event.type)

        @session.on_utterance
        def utterance(event):
            handled.append(event.end_reason)

        await session.run()
        self.assertEqual(handled, ["activity_started", "activity_ended", "silence"])

    async def test_callback_overload_fails_and_cancels_the_blocked_handler(self):
        self.write_packets([4000, 4000, 0, 0, 5000, 5000, 0, 0])
        replay = ReplayStream.from_wav(self.wav, chunk_frames=160)
        session = AudioSession(replay, activity_config=self.config, callback_queue_size=1)
        cancelled = asyncio.Event()

        @session.on_activity
        async def block(event):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        with self.assertRaisesRegex(CallbackQueueFullError, "1 pending callback events"):
            await asyncio.wait_for(session.run(), 2)
        self.assertTrue(cancelled.is_set())

    async def test_cancellation_awaits_callback_cleanup_after_capture_ends(self):
        replay = ReplayStream.from_wav(self.wav, chunk_frames=160)
        session = AudioSession(replay, activity_config=self.config, callback_queue_size=4)
        entered = asyncio.Event()
        cleaned = asyncio.Event()

        @session.on_utterance
        async def block(event):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        with mock.patch.object(replay, "aclose", wraps=replay.aclose) as close:
            running = asyncio.create_task(session.run())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                for _ in range(100):
                    if close.await_count:
                        break
                    await asyncio.sleep(.01)
                close.assert_awaited()
                await asyncio.sleep(0)
            finally:
                running.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await running
        self.assertTrue(cleaned.is_set())

    async def test_barge_in_cancels_provider_flushes_and_allows_a_second_reply(self):
        self.write_packets([4000, 4000, 0, 0, 5000, 5000, 0, 0])
        waiting = asyncio.Event()
        closed = asyncio.Event()

        class PacedReplay(ReplayStream):
            async def __anext__(self):
                frame = await super().__anext__()
                if frame.sequence == 4:
                    await waiting.wait()
                return frame

        async def synthesize(text, *, format):
            if text == "first":
                try:
                    yield b"\1\0" * 160
                    waiting.set()
                    await asyncio.Future()
                finally:
                    closed.set()
            else:
                yield b"\2\0" * 160

        output = OutputRecorder()
        client = mock.create_autospec(AudioPlane, instance=True)
        client.playback.return_value = output
        session = AudioSession(PacedReplay.from_wav(self.wav, chunk_frames=160), speak="default",
                               client=client, activity_config=self.config, interrupt_on_activity=True,
                               synthesizer=SimpleNamespace(synthesize=synthesize))
        replies = iter(("first", "second"))

        @session.on_utterance
        async def reply(event):
            await session.say(next(replies))

        await asyncio.wait_for(session.run(), 2)
        self.assertTrue(closed.is_set())
        output.flush.assert_awaited_once()
        self.assertEqual(output.write.await_args_list, [mock.call(b"\1\0" * 160), mock.call(b"\2\0" * 160)])

    async def test_manual_interrupt_waits_for_in_flight_pcm_before_flushing(self):
        entered = asyncio.Event()
        complete_write = asyncio.Event()
        fragments = []

        async def write(data):
            fragments.append(data)
            entered.set()
            await complete_write.wait()

        async def synthesize(text, *, format):
            yield b"\1\0" * 1600

        output = OutputRecorder()
        output.write.side_effect = write
        client = mock.create_autospec(AudioPlane, instance=True)
        client.playback.return_value = output
        session = AudioSession(ReplayStream.from_wav(self.wav, chunk_frames=160), speak="default",
                               client=client, activity_config=self.config,
                               synthesizer=SimpleNamespace(synthesize=synthesize))

        @session.on_utterance
        async def interrupt(event):
            speaking = asyncio.create_task(session.say("first"))
            try:
                await entered.wait()
                flushing = asyncio.create_task(session.interrupt_playback())
                await asyncio.sleep(0)
                output.flush.assert_not_awaited()
                complete_write.set()
                await flushing
            finally:
                complete_write.set()
                await speaking

        await asyncio.wait_for(session.run(), 2)
        output.flush.assert_awaited_once()
        self.assertEqual(fragments, [b"\1\0" * 640])
