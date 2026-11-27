"""Provider-neutral contracts for session transcription and synthesis."""

from dataclasses import dataclass, field
from typing import AsyncGenerator, Protocol

from sonexis import AudioFormat

from .utterance import UtteranceEvent


@dataclass(frozen=True)
class TranscriptEvent:
    """Final text associated with one buffered audio segment."""

    text: str
    utterance: UtteranceEvent = field(repr=False)


class Transcriber(Protocol):
    async def transcribe(self, utterance: UtteranceEvent) -> str:
        """Return final text, or blank text when no speech was recognized."""
        ...


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, *, format: AudioFormat) -> AsyncGenerator[bytes, None]:
        """Yield playback-ready, interleaved PCM in the requested format.

        Each nonempty chunk contains whole sample frames and at least 1 ms
        of audio. Providers should release any resources when the generator closes.
        """
        ...
