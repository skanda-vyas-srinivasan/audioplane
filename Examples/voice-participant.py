#!/usr/bin/env python3
"""Listen to one macOS app and speak a configured reply through a virtual microphone."""

import argparse
import asyncio
from contextlib import AsyncExitStack

from audioplane import (ActivityDetectionConfig, AudioSession, SonexisError,
                       WebRTCVoiceActivityDetector, run_recovering_session)
from audioplane.providers import MacOSSpeechSynthesizer, OpenAITranscriber, SpeechSynthesisError


async def run(args: argparse.Namespace) -> None:
    async with AsyncExitStack() as resources:
        transcriber = None
        if args.transcribe:
            try:
                from openai import AsyncOpenAI
            except ImportError as error:
                raise ValueError('Install the AudioPlane OpenAI extra to use --transcribe') from error
            client = await resources.enter_async_context(AsyncOpenAI())
            transcriber = OpenAITranscriber(client)

        voice = MacOSSpeechSynthesizer(voice=args.voice)

        def make_session(source, destination, client):
            if source.kind != 'application':
                raise ValueError('Choose a single application audio source')
            if destination is None or destination.kind != 'virtual_input':
                raise ValueError('Choose an explicit virtual microphone destination')
            vad = WebRTCVoiceActivityDetector() if args.speech_only else None
            session = AudioSession(
                source, client=client, speak=destination, output_format=voice.format,
                voice_activity_detector=vad, synthesizer=voice, transcriber=transcriber,
                activity_config=ActivityDetectionConfig(minimum_activity_ms=150, silence_duration_ms=500),
                interrupt_on_activity=True,
            )

            async def reply():
                print(f'Replying through {destination.name}', flush=True)
                await session.say(args.reply)

            if transcriber is None:
                @session.on_utterance
                async def on_utterance(event):
                    if event.end_reason == 'silence':
                        await reply()
            else:
                @session.on_transcript
                async def on_transcript(event):
                    if (event.utterance.end_reason == 'silence'
                            and (args.trigger is None or args.trigger.casefold() in event.text.casefold())):
                        await reply()

            print(f'Listening to {source.name} through {source.id}', flush=True)
            return session

        await run_recovering_session(args.source, make_session, speak=args.destination,
                                     max_restarts=args.max_restarts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Application name, bundle ID, or Runtime source ID')
    parser.add_argument('--destination', required=True, help='Virtual microphone output ID or exact name')
    parser.add_argument('--reply', default='I heard you.', help='Text to speak locally')
    parser.add_argument('--voice', help='Installed macOS speech voice')
    parser.add_argument('--speech-only', action='store_true', help='Use the optional WebRTC voice detector')
    parser.add_argument('--transcribe', action='store_true',
                        help='Send completed utterances to OpenAI for transcription')
    parser.add_argument('--trigger', help='Reply only when this phrase occurs in the transcript')
    parser.add_argument('--max-restarts', type=int, default=12, help='Recovery attempts for the whole run')
    args = parser.parse_args()
    if args.trigger is not None and (not args.transcribe or not args.trigger.strip()):
        parser.error('--trigger requires --transcribe and a nonblank phrase')
    if not args.reply.strip():
        parser.error('--reply must contain text')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        return
    except (ImportError, OSError, SonexisError, SpeechSynthesisError, ValueError) as error:
        parser.exit(1, f'Voice participant failed: {error}\n')


if __name__ == '__main__':
    main()
