"""Recovery tests for endpoint identity, resource ownership, and retry boundaries."""

import asyncio
import struct
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from audioplane import (ActivityDetectionConfig, AudioFormat, AudioFrame, AudioOutputDestination,
                       AudioPlane, AudioSession, AudioSource, CaptureFailedError,
                       OutputUnavailableError, RuntimeErrorInfo, SonexisConnectionError, SourceNotFoundError,
                       run_recovering_session)


class CaptureStub:
    def __init__(self, frame, error=None):
        self.frame = frame
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.frame is not None:
            frame, self.frame = self.frame, None
            return frame
        if self.error is not None:
            raise self.error
        raise StopAsyncIteration


class SessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = ActivityDetectionConfig(minimum_activity_ms=10)
        self.source = AudioSource("app.com.example.voice", "Voice", "application", [101],
                                  "com.example.voice", "running", True, True, None)
        self.destination = AudioOutputDestination("coreaudio:device", "Device", "virtual_input",
                                                  True, False, False, None, None, None, [])

    def client(self, source, stream, *, error=None):
        client = mock.create_autospec(AudioPlane, instance=True)
        client.get_source.return_value = source
        client.get_output_destination.return_value = self.destination
        format = AudioFormat.speech_16k()
        frame = AudioFrame(stream, 0, 0, 160, format, struct.pack("<h", 4000) * 160, source=source)
        client.capture.return_value = CaptureStub(frame, error)
        return client

    async def test_restarts_with_new_stream_and_matches_original_source_identity(self):
        exited = CaptureFailedError("capture_failed", "process exited")
        first = self.client(self.source, "stream-a", error=exited)
        missing = mock.create_autospec(AudioPlane, instance=True)
        missing.get_source.side_effect = SourceNotFoundError("source_not_found", "app is down")
        new_source = replace(self.source, process_ids=[202])
        last = self.client(new_source, "stream-b")
        handled = []

        def make(source, destination, client):
            session = AudioSession(source, speak=destination, client=client, activity_config=self.config)
            session.on_activity(handled.append)
            return session

        with mock.patch("audioplane.recovery.Sonexis", side_effect=[first, missing, last]):
            await run_recovering_session("Voice", make, retry_delay=0, max_restarts=2)

        self.assertEqual([event.stream_id for event in handled], ["stream-a", "stream-b"])
        first.get_source.assert_awaited_once_with("Voice")
        missing.get_source.assert_awaited_once_with(self.source)
        last.get_source.assert_awaited_once_with(self.source)
        last.capture.assert_awaited_once_with(new_source, format=AudioFormat.speech_16k())
        for client in (first, missing, last):
            client.close.assert_awaited_once()

    async def test_reconnects_pinned_destination_after_runtime_write_failure(self):
        first = self.client(self.source, "stream-a")
        last = self.client(self.source, "stream-b")
        for client in (first, last):
            output = mock.MagicMock()
            output.__aenter__.return_value = output
            output.write = mock.AsyncMock()
            client.playback.return_value = output
        first.playback.return_value.write.side_effect = OutputUnavailableError(
            "output_destination_disconnected", "device removed", retryable=True)

        def make(source, destination, client):
            session = AudioSession(source, speak=destination, client=client, activity_config=self.config)

            @session.on_activity
            async def respond(event):
                await session.play_pcm(b"\0\0" * 160)

            return session

        with mock.patch("audioplane.recovery.Sonexis", side_effect=[first, last]):
            await run_recovering_session("Voice", make, speak="Device", retry_delay=0)

        first.get_output_destination.assert_awaited_once_with("Device")
        last.get_output_destination.assert_awaited_once_with(self.destination)
        for client in (first, last):
            client.playback.assert_awaited_once_with(destination=self.destination, format=AudioFormat.speech_16k())
            client.playback.return_value.write.assert_awaited_once()

    async def test_idle_output_failure_is_observed_and_restarted_without_a_write(self):
        first = self.client(self.source, "stream-a")
        last = self.client(self.source, "stream-b")

        class WaitingCapture(CaptureStub):
            async def __anext__(self):
                if self.frame is None:
                    await asyncio.Future()
                return await super().__anext__()

        first.capture.return_value = WaitingCapture(first.capture.return_value.frame)
        first.output_status.return_value = SimpleNamespace(
            state="failed", error=RuntimeErrorInfo("output_destination_disconnected", "unplugged", True))
        for client in (first, last):
            output = mock.MagicMock(info=SimpleNamespace(id="output"))
            output.__aenter__.return_value = output
            output.write = mock.AsyncMock()
            client.playback.return_value = output

        def make(source, destination, client):
            return AudioSession(source, speak=destination, client=client)

        with mock.patch("audioplane.recovery.Sonexis", side_effect=[first, last]):
            await asyncio.wait_for(run_recovering_session("Voice", make, speak="Device", retry_delay=0), 3)

        first.output_status.assert_awaited_once_with("output")
        last.get_output_destination.assert_awaited_once_with(self.destination)
        first.playback.return_value.write.assert_not_awaited()

    async def test_callback_error_cannot_trigger_runtime_recovery(self):
        client = self.client(self.source, "stream-a")
        callback_error = CaptureFailedError("capture_failed", "callback failed", retryable=True)

        def make(source, destination, client):
            session = AudioSession(source, speak=destination, client=client, activity_config=self.config)

            @session.on_activity
            def fail(event):
                raise callback_error

            return session

        with mock.patch("audioplane.recovery.Sonexis", return_value=client) as connect:
            with self.assertRaises(CaptureFailedError) as caught:
                await run_recovering_session("Voice", make, retry_delay=0)
        self.assertIs(caught.exception, callback_error)
        connect.assert_called_once()
        client.close.assert_awaited_once()

    async def test_retry_budget_reports_last_error_and_closes_each_connection(self):
        first = mock.create_autospec(AudioPlane, instance=True)
        last = mock.create_autospec(AudioPlane, instance=True)
        first.get_source.side_effect = SourceNotFoundError("source_not_found", "first lookup")
        terminal = SourceNotFoundError("source_not_found", "last lookup")
        last.get_source.side_effect = terminal
        make = mock.Mock()

        with mock.patch("audioplane.recovery.Sonexis", side_effect=[first, last]):
            with self.assertRaises(SourceNotFoundError) as caught:
                await run_recovering_session("Voice", make, retry_delay=0, max_restarts=1)

        self.assertIs(caught.exception, terminal)
        make.assert_not_called()
        for client in (first, last):
            client.close.assert_awaited_once()

    async def test_capture_closed_by_runtime_disconnect_restarts_even_after_iterator_eof(self):
        first = self.client(self.source, "stream-a")
        last = self.client(self.source, "stream-b")
        first.status.side_effect = SonexisConnectionError("not_connected", "control socket closed")
        streams = []

        def make(source, destination, client):
            session = AudioSession(source, speak=destination, client=client, activity_config=self.config)

            @session.on_activity
            def remember(event):
                streams.append(event.stream_id)

            return session

        with mock.patch("audioplane.recovery.Sonexis", side_effect=[first, last]):
            await run_recovering_session("Voice", make, retry_delay=0)

        self.assertEqual(streams, ["stream-a", "stream-b"])
        for client in (first, last):
            client.status.assert_awaited_once()
            client.close.assert_awaited_once()
