#!/usr/bin/env python3
"""Report local activity edges and optionally play a tone on activity onset."""

import argparse
import asyncio
import math
import struct
import wave
from pathlib import Path

from audioplane import (ActivityEvent, AudioFormat, AudioSession, ReplayStream,
                       SonexisError, UtteranceEvent, run_recovering_session)


async def run(args: argparse.Namespace) -> None:
    output_format = AudioFormat.speech_16k()
    tone = b""
    if args.destination is not None:
        rate = output_format.sample_rate
        tone = b"".join(
            struct.pack("<h", round(4_000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(rate // 10)
        )

    def make_session(source, destination, client=None):
        session = AudioSession(source, speak=destination, client=client, output_format=output_format)

        @session.on_activity
        async def report(event: ActivityEvent) -> None:
            seconds = event.timestamp_ns / 1_000_000_000
            print(f"{seconds:.3f}s {event.type} {event.source_id}", flush=True)
            if tone and event.type == "activity_started":
                await session.play_pcm(tone)

        if args.utterances:
            @session.on_utterance
            def report_utterance(event: UtteranceEvent) -> None:
                duration = event.frame_count / event.format.sample_rate
                print(f"utterance {duration:.3f}s ({event.end_reason})", flush=True)
        return session

    if args.recover:
        await run_recovering_session(args.source, make_session, speak=args.destination)
        return
    listen = (ReplayStream.from_wav(args.wav, chunk_frames=160)
              if args.wav is not None else args.source)
    session = make_session(listen, args.destination)
    await session.run()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", help="Live AudioPlane source selector")
    source.add_argument("--wav", type=Path, help="Uncompressed PCM16 WAV to replay")
    parser.add_argument("--destination", help="AudioPlane output for the optional tone")
    parser.add_argument("--utterances", action="store_true", help="Report buffered PCM utterances")
    parser.add_argument("--recover", action="store_true", help="Retry unavailable live audio routes")
    args = parser.parse_args()
    if args.recover and args.wav is not None:
        parser.error("--recover requires --source")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        return
    except (EOFError, OSError, SonexisError, ValueError, wave.Error) as error:
        parser.exit(1, f"Audio session failed: {error}\n")


if __name__ == "__main__":
    main()
