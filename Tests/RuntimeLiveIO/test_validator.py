"""Offline acceptance-validator checks; no HAL, recording, or provider access."""
import contextlib
import io
import math
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from check import NativeAcceptance


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


if __name__ == '__main__':
    unittest.main()
