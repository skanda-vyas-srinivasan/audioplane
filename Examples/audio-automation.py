#!/usr/bin/env python3
"""Emit a local JSON notification when the selected source plays a sustained tone."""

import argparse
import asyncio
import json
import math
import struct
import wave

from audioplane import AudioFormat, AudioSession, ReplayStream, SonexisError

FORMAT = AudioFormat.speech_16k()


async def run(args):
    source = args.source if args.wav is None else ReplayStream.from_wav(args.wav, chunk_frames=160)
    if isinstance(source, ReplayStream) and source.format != FORMAT:
        await source.aclose()
        raise ValueError("Replay requires a 16 kHz mono PCM16 WAV")
    session = AudioSession(source, input_format=FORMAT)
    phase = math.tau * args.frequency / FORMAT.sample_rate
    tone_ms = 0.0
    notified = False

    @session.on_frame
    def notify(frame):
        nonlocal tone_ms, notified
        if frame.discontinuity:
            tone_ms, notified = 0.0, False
        samples = [sample for (sample,) in struct.iter_unpack("<h", frame.data)]
        power = sum(sample * sample for sample in samples)
        correlation = sum(sum(sample * oscillator(phase * i) for i, sample in enumerate(samples)) ** 2
                          for oscillator in (math.cos, math.sin))
        if power < 500 ** 2 * len(samples) or correlation < .3 * power * len(samples):
            tone_ms, notified = 0.0, False
            return
        tone_ms += frame.frame_count * 1000 / FORMAT.sample_rate
        if tone_ms >= 80 and not notified:
            notified = True
            print(json.dumps(dict(event="tone_detected", frequency_hz=args.frequency,
                                  source_id=frame.source_id, stream_id=frame.stream_id,
                                  timestamp_ns=frame.timestamp_ns)), flush=True)

    await session.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", help="Runtime source selector")
    source.add_argument("--wav", help="16 kHz mono PCM16 WAV to replay")
    parser.add_argument("--frequency", type=int, default=440)
    args = parser.parse_args()
    if not 200 <= args.frequency <= 3000:
        parser.error("--frequency must be between 200 and 3000 Hz")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except (EOFError, OSError, SonexisError, ValueError, wave.Error) as error:
        parser.exit(1, f"Audio automation failed: {error}\n")


if __name__ == "__main__":
    main()
