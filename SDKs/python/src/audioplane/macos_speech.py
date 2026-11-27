"""Local macOS speech rendering shared by the CLI and Python provider."""

from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Optional
import wave

from sonexis import AudioFormat, SampleFormat


MACOS_SPEECH_FORMAT = AudioFormat(
    sample_rate=48_000,
    channels=1,
    sample_format=SampleFormat.PCM_S16LE,
)


class SpeechSynthesisError(RuntimeError):
    """Raised when the local macOS speech synthesizer cannot produce PCM."""


def _command_failure(tool: str, result: subprocess.CompletedProcess) -> SpeechSynthesisError:
    message = (result.stderr or result.stdout or "unknown error").strip()
    return SpeechSynthesisError(f"{tool} failed: {message}")


def synthesize_speech(text: str, voice: Optional[str], rate: Optional[int], *,
                      sample_rate: int = MACOS_SPEECH_FORMAT.sample_rate) -> bytes:
    """Render text with macOS ``say`` as mono PCM16 at the requested sample rate."""
    say = shutil.which("say")
    afconvert = shutil.which("afconvert")
    if say is None or afconvert is None:
        missing = "say" if say is None else "afconvert"
        raise SpeechSynthesisError(
            f"macOS speech tool {missing!r} is unavailable; speak requires macOS")

    with tempfile.TemporaryDirectory(prefix="audioplane-speak-") as directory:
        script = Path(directory) / "speech.txt"
        source = Path(directory) / "speech.aiff"
        converted = Path(directory) / "speech.wav"
        say_command = [say]
        if voice:
            say_command.extend(["-v", voice])
        if rate is not None:
            say_command.extend(["-r", str(rate)])
        say_command.extend(["-o", str(source), "-f", str(script)])

        try:
            script.write_text(text, encoding="utf-8")
            result = subprocess.run(
                say_command, capture_output=True, text=True, check=False, timeout=60)
        except subprocess.TimeoutExpired as error:
            raise SpeechSynthesisError("macOS say timed out") from error
        except OSError as error:
            raise SpeechSynthesisError(f"could not run say: {error}") from error
        if result.returncode != 0:
            raise _command_failure("say", result)

        try:
            result = subprocess.run(
                [
                    afconvert,
                    "-f", "WAVE",
                    "-d", f"LEI16@{sample_rate}",
                    "-c", "1",
                    str(source),
                    str(converted),
                ],
                capture_output=True,
                text=True,
                check=False, timeout=30,
            )
        except subprocess.TimeoutExpired as error:
            raise SpeechSynthesisError("macOS afconvert timed out") from error
        except OSError as error:
            raise SpeechSynthesisError(f"could not run afconvert: {error}") from error
        if result.returncode != 0:
            raise _command_failure("afconvert", result)

        try:
            with wave.open(str(converted), "rb") as stream:
                actual = (
                    stream.getframerate(),
                    stream.getnchannels(),
                    stream.getsampwidth(),
                    stream.getcomptype(),
                )
                expected = (sample_rate, 1, 2, "NONE")
                if actual != expected:
                    raise SpeechSynthesisError(
                        "speech conversion produced an unexpected audio format "
                        f"(rate={actual[0]}, channels={actual[1]}, bytes={actual[2]}, "
                        f"compression={actual[3]})")
                pcm = stream.readframes(stream.getnframes())
        except (OSError, EOFError, wave.Error) as error:
            raise SpeechSynthesisError(f"could not read synthesized speech: {error}") from error
        if not pcm:
            raise SpeechSynthesisError("macOS speech synthesis produced no audio")
        return pcm
