"""Public capture-and-review example: bounded PCM, privacy and cancellation."""

import asyncio
import base64
import importlib.util
import io
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import wave

from audioplane import AudioFrame, AudioFormat


EXAMPLE = Path(__file__).parents[3] / "Examples" / "capture-and-review.py"
spec = importlib.util.spec_from_file_location("capture_review_example", EXAMPLE)
example = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = example
spec.loader.exec_module(example)


def frame(samples=8000, value=1000, **changes):
    values = dict(stream_id="stream", sequence=0, timestamp_ns=0,
                  frame_count=samples, format=AudioFormat.speech_16k(),
                  data=struct.pack("<h", value) * samples)
    values.update(changes)
    return AudioFrame(**values)


class Frames:
    def __init__(self, values):
        self.values = iter(values)

    async def __anext__(self):
        try:
            return next(self.values)
        except StopIteration:
            raise StopAsyncIteration


class CaptureReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_bound_and_wav_format(self):
        clip = await example.collect_clip(Frames([frame(), frame(samples=9000)]), 1)
        self.assertEqual(len(clip.pcm), 32000)
        self.assertEqual(clip.packets, 2)
        with wave.open(io.BytesIO(clip.wav())) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()),
                             (1, 2, 16000))
            self.assertEqual(wav.getnframes(), 16000)

    async def test_loss_or_discontinuity_is_not_silently_reviewed(self):
        for changes in ({"dropped_frames_before": 5}, {"discontinuity": True}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "audio gap"):
                await example.collect_clip(Frames([frame(**changes)]), 1)

    async def test_silence_rejected(self):
        with self.assertRaisesRegex(ValueError, "silent"):
            await example.collect_clip(Frames([frame(samples=16000, value=0)]), 1)

    async def test_early_eos_and_invalid_format_rejected(self):
        with self.assertRaisesRegex(ValueError, "ended"):
            await example.collect_clip(Frames([frame()]), 1)
        with self.assertRaisesRegex(ValueError, "format"):
            await example.collect_clip(Frames([frame(format=AudioFormat(24000))]), 1)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            await example.collect_clip(Frames([frame(data=b"x")]), 1)

    async def test_first_frame_timeout_and_cancellation(self):
        class Blocked:
            async def __anext__(self):
                await asyncio.Event().wait()
        with self.assertRaisesRegex(ValueError, "No audio frames"):
            await example.collect_clip(Blocked(), 1, first_frame_timeout=0.01)
        task = asyncio.create_task(example.collect_clip(Blocked(), 1))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_inline_audio_stateless_request_and_output(self):
        create = AsyncMock(return_value=SimpleNamespace(output_text="Specific review"))
        client = SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=create)))
        clip = example.Clip(frame().data, 1)
        text = await example.review_clip(client, clip, "model", "review the pitch", 2)
        self.assertEqual(text, "Specific review")
        arguments = create.call_args.kwargs
        self.assertFalse(arguments["store"])
        self.assertEqual(arguments["input"][0]["text"], "review the pitch")
        self.assertEqual(base64.b64decode(arguments["input"][1]["data"]), clip.wav())

    async def test_provider_timeout_empty_text_and_missing_api(self):
        async def stalled(**kwargs):
            await asyncio.sleep(30)
        client = SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=stalled)))
        with self.assertRaises(asyncio.TimeoutError):
            await example.review_clip(client, example.Clip(b"xx", 1), "m", "p", .01)
        client.aio.interactions.create = AsyncMock(return_value=SimpleNamespace(output_text=""))
        with self.assertRaisesRegex(ValueError, "no readable review"):
            await example.review_clip(client, example.Clip(b"xx", 1), "m", "p", 1)
        with self.assertRaisesRegex(ValueError, "Upgrade"):
            await example.review_clip(SimpleNamespace(aio=object()), example.Clip(b"xx", 1), "m", "p", 1)

    async def test_capture_only_and_cancellation_close_owned_contexts(self):
        stream = AsyncMock()
        stream.source = SimpleNamespace(name="Fixture")
        capture_context = AsyncMock()
        capture_context.__aenter__.return_value = stream
        audio = AsyncMock()
        audio.capture.return_value = capture_context
        audio_context = AsyncMock()
        audio_context.__aenter__.return_value = audio
        args = example.parser().parse_args(["--capture-only", "--duration", "1"])
        with patch.object(example, "AudioPlane", return_value=audio_context), \
                patch.object(example, "collect_clip", return_value=example.Clip(frame().data, 1)), \
                patch.object(example, "provider_client") as provider:
            await example.run(args)
            provider.assert_not_called()
        capture_context.__aexit__.assert_awaited_once()
        audio_context.__aexit__.assert_awaited_once()
        with patch.object(example, "AudioPlane", return_value=audio_context), \
                patch.object(example, "collect_clip", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await example.run(args)
        self.assertEqual(capture_context.__aexit__.await_count, 2)

    def test_duration_is_bounded_and_credentials_missing_are_actionable(self):
        for value in ("0", "301", "nan", "inf", "-1"):
            with self.subTest(value=value), patch("sys.stderr", new=io.StringIO()), \
                    self.assertRaises(SystemExit):
                example.parser().parse_args(["--duration", value])
        with patch.object(example.sys, "version_info", (3, 10)), \
                patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            example.provider_client()

    async def test_provider_failure_does_not_expose_secret_and_closes_client(self):
        provider = SimpleNamespace(aio=SimpleNamespace(aclose=AsyncMock()), close=lambda: None)
        audio = AsyncMock()
        capture = AsyncMock()
        capture.__aenter__.return_value.source = SimpleNamespace(name="Fixture")
        audio.__aenter__.return_value.capture.return_value = capture
        args = example.parser().parse_args(["--duration", "1"])
        private_error = RuntimeError("secret-key-and-private-audio")
        with patch.object(example, "provider_client", return_value=provider), \
                patch.object(example, "AudioPlane", return_value=audio), \
                patch.object(example, "collect_clip", return_value=example.Clip(frame().data, 1)), \
                patch.object(example, "review_clip", side_effect=private_error):
            with self.assertRaisesRegex(ValueError, "Gemini review failed") as caught:
                await example.run(args)
        self.assertNotIn("secret", str(caught.exception))
        provider.aio.aclose.assert_awaited_once()
        capture.__aexit__.assert_awaited_once()

    def test_python_39_provider_error_is_actionable(self):
        with patch.object(example.sys, "version_info", (3, 9)), \
                self.assertRaisesRegex(ValueError, "Python 3.10"):
            example.provider_client()


if __name__ == "__main__":
    unittest.main()
