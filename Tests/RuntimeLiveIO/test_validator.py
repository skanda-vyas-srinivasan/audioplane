"""Offline acceptance-validator checks; no HAL, recording, or provider access."""
import contextlib
import io
import math
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

sys.path.insert(0, str(Path(__file__).parent))
from check import NativeAcceptance
from hotplug import verify_default_route, verify_hotplug
from audioplane import CaptureFailedError, SourceNotFoundError
from voice_scenario import FIRST_HZ, INPUT_RATE, REPLACEMENT_HZ, classify_response, tone, write_input


class WaveformValidatorTests(unittest.TestCase):
    def frames(self, frequency=700, leading_silence=0):
        values = ([0] * leading_silence
                  + [round(328 * math.sin(2 * math.pi * frequency * i / 16000))
                     for i in range(4800 - leading_silence)])
        return [SimpleNamespace(
            timestamp_ns=i * 10_000_000, sequence=i, frame_count=160,
            format=SimpleNamespace(sample_rate=16000),
            data=b''.join(struct.pack('<h', value) for value in values[i * 160:(i + 1) * 160]),
            dropped_frames_before=0, source_id='app.synthetic', stream_id='synthetic')
            for i in range(30)]

    def rejects(self, frames):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(AssertionError):
            NativeAcceptance.check_frames(frames, 700)

    def test_partial_startup_packet_does_not_change_signal_frequency(self):
        result = NativeAcceptance.check_frames(self.frames(leading_silence=129), 700)
        self.assertAlmostEqual(result['measured_hz'], 700, delta=5)
        self.assertGreaterEqual(result['leading_quiet_samples'], 129)

    def test_internal_silent_packet_is_not_trimmed_or_ignored(self):
        frames = self.frames()
        frames[10].data = b'\0\0' * 160
        self.rejects(frames)

    def test_wrong_source_tone_is_rejected(self):
        self.rejects(self.frames(frequency=300))

    def test_reported_capture_drop_is_rejected(self):
        frames = self.frames()
        frames[10].dropped_frames_before = 1
        self.rejects(frames)

    def test_sequence_gap_is_rejected(self):
        frames = self.frames()
        frames[10].sequence += 1
        self.rejects(frames)


class VoiceScenarioFixtureTests(unittest.TestCase):
    def test_response_classifier_distinguishes_replies_quiet_and_unrelated_signal(self):
        for data, classification in (
                (tone(FIRST_HZ, .01), 'first'), (tone(REPLACEMENT_HZ, .01), 'replacement'),
                (b'\0\0' * 480, 'quiet'), (tone(1200, .01), 'other'), (b'\0\0', 'other')):
            with self.subTest(classification=classification):
                self.assertEqual(classify_response(data), classification)

    def test_input_fixture_preserves_two_turns_with_repeatable_background_noise(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.wav'
            write_input(path, 200)
            first = path.read_bytes()
            write_input(path, 200)
            self.assertEqual(path.read_bytes(), first)
            with wave.open(str(path), 'rb') as reader:
                self.assertEqual((reader.getframerate(), reader.getnchannels()), (INPUT_RATE, 1))
                packet_size = INPUT_RATE // 100
                peaks = []
                for _ in range(8):
                    values = [sample for (sample,) in struct.iter_unpack('<h', reader.readframes(packet_size))]
                    peaks.append(max(map(abs, values)))
        self.assertEqual([peak > 200 for peak in peaks], [True, True, False, False] * 2)


class HotplugValidatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_output_keeps_stream_when_switching_and_restoring_route(self):
        default = SimpleNamespace(active_device_id='original')
        alternate = SimpleNamespace(active_device_id='alternate')

        def state(changes):
            return SimpleNamespace(id='output', stream_id='stream', state='ready',
                                   metrics=SimpleNamespace(route_changes=changes))

        output = SimpleNamespace(info=state(0), aclose=mock.AsyncMock())
        client = SimpleNamespace(
            get_output_destination=mock.AsyncMock(side_effect=[default, alternate, default]),
            playback=mock.AsyncMock(return_value=output),
            output_status=mock.AsyncMock(side_effect=[state(1), state(2)]),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            result = await verify_default_route(client)

        self.assertEqual(result['restored_device'], 'original')
        self.assertEqual(result['alternate_device'], 'alternate')
        self.assertEqual(result['route_changes'], 2)
        output.aclose.assert_awaited_once_with(drain=False)

    async def test_reopens_both_pinned_endpoints_and_verifies_new_streams(self):
        source = SimpleNamespace(id='microphone:removable', name='Removable', kind='microphone')
        destination = SimpleNamespace(id='coreaudio:removable', kind='playback')

        class Capture:
            def __init__(self, stream):
                self.stream = stream
                self.delivered = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                pass

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.delivered:
                    raise CaptureFailedError('microphone_device_changed', 'Device disconnected')
                self.delivered = True
                return SimpleNamespace(source_id=source.id, stream_id=self.stream)

        missing = SourceNotFoundError('source_not_found', 'The device is unplugged')
        old_output = SimpleNamespace(info=SimpleNamespace(id='output-a', stream_id='old-output'),
                                     aclose=mock.AsyncMock())
        new_output = SimpleNamespace(info=SimpleNamespace(stream_id='new-output'), aclose=mock.AsyncMock())
        client = SimpleNamespace(
            get_source=mock.AsyncMock(side_effect=[source, missing]),
            wait_for_source=mock.AsyncMock(return_value=source),
            capture=mock.AsyncMock(side_effect=[Capture('old-input'), Capture('new-input')]),
            get_output_destination=mock.AsyncMock(return_value=destination),
            wait_for_output_destination=mock.AsyncMock(return_value=destination),
            playback=mock.AsyncMock(side_effect=[old_output, new_output]),
            output_status=mock.AsyncMock(return_value=SimpleNamespace(
                state='failed', error=SimpleNamespace(code='output_destination_disconnected'))),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            report = await verify_hotplug(client, source.id, destination.id)

        self.assertEqual((report['old_capture_stream'], report['new_capture_stream']), ('old-input', 'new-input'))
        self.assertEqual((report['old_output_stream'], report['new_output_stream']), ('old-output', 'new-output'))
        client.wait_for_source.assert_awaited_once_with(source, timeout=45, poll_interval=.1)
        client.wait_for_output_destination.assert_awaited_once_with(destination, timeout=45, poll_interval=.1)
        old_output.aclose.assert_awaited_once_with(drain=False)
        new_output.aclose.assert_awaited_once_with(drain=False)


if __name__ == '__main__':
    unittest.main()
