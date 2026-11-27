"""AudioPlane namespace for optional speech and realtime provider adapters."""

import asyncio
import io
from typing import Any, AsyncGenerator, Optional
import wave

from sonexis import AudioFormat, SampleFormat

from sonexis.providers import *  # noqa: F401,F403
from sonexis.providers import __all__ as _realtime_all

from .macos_speech import MACOS_SPEECH_FORMAT, SpeechSynthesisError, synthesize_speech
from .utterance import UtteranceEvent


class OpenAITranscriber:
    """Upload buffered PCM16 utterances to a caller-owned async OpenAI client."""

    def __init__(self, client: Any, *, model: str = "gpt-4o-transcribe") -> None:
        self._client = client
        self._model = model

    async def transcribe(self, utterance: UtteranceEvent) -> str:
        format = utterance.format
        if format.sample_format is not SampleFormat.PCM_S16LE or not format.interleaved:
            raise ValueError("OpenAITranscriber requires interleaved PCM16 capture")
        with io.BytesIO() as recording:
            with wave.open(recording, "wb") as writer:
                writer.setnchannels(format.channels)
                writer.setframerate(format.sample_rate)
                writer.setsampwidth(2)
                writer.writeframes(utterance.data)
            recording.seek(0)
            response = await self._client.audio.transcriptions.create(
                model=self._model, file=("utterance.wav", recording, "audio/wav"))
        return response.text


class MacOSSpeechSynthesizer:
    """Render speech locally using the installed macOS voice and file converter."""

    format = MACOS_SPEECH_FORMAT

    def __init__(self, *, voice: Optional[str] = None, rate: Optional[int] = None,
                 format: AudioFormat = MACOS_SPEECH_FORMAT) -> None:
        if (format.channels != 1 or format.sample_format is not SampleFormat.PCM_S16LE
                or not format.interleaved or not isinstance(format.sample_rate, int)
                or not 8_000 <= format.sample_rate <= 192_000):
            raise ValueError("MacOSSpeechSynthesizer requires mono PCM16 at 8–192 kHz")
        self.format = format
        self._voice = voice
        self._rate = rate

    async def synthesize(self, text: str, *, format: AudioFormat) -> AsyncGenerator[bytes, None]:
        if format != self.format:
            raise ValueError("Playback format must match the format configured on MacOSSpeechSynthesizer")
        yield await asyncio.to_thread(synthesize_speech, text, self._voice, self._rate,
                                      sample_rate=format.sample_rate)


__all__ = [*_realtime_all, "MacOSSpeechSynthesizer", "OpenAITranscriber", "SpeechSynthesisError"]
