"""Run a two-turn voice scenario with a deterministic PCM responder."""

import asyncio
import json
import math
import random
import struct
import time
import wave
from contextlib import ExitStack
from pathlib import Path
from typing import AsyncGenerator, Optional

from audioplane import ActivityDetectionConfig, AudioFormat, AudioPlane, AudioSession, ReplayStream

INPUT_RATE = 16_000
OUTPUT_RATE = 48_000
FIRST_HZ = 400
REPLACEMENT_HZ = 800


def tone(frequency: int, seconds: float) -> bytes:
    return b''.join(struct.pack('<h', round(4000 * math.sin(math.tau * frequency * index / OUTPUT_RATE)))
                   for index in range(round(OUTPUT_RATE * seconds)))


def classify_response(pcm: bytes) -> str:
    samples = [value for (value,) in struct.iter_unpack('<h', pcm)]
    if len(samples) < OUTPUT_RATE // 200:
        return 'other'
    if max(map(abs, samples)) < 200:
        return 'quiet'

    def strength(frequency: int) -> float:
        phase_step = math.tau * frequency / OUTPUT_RATE
        components = [sum(value * oscillator(phase_step * index) for index, value in enumerate(samples))
                      for oscillator in (math.cos, math.sin)]
        return 2 * math.hypot(*components) / len(samples)

    first, replacement = strength(FIRST_HZ), strength(REPLACEMENT_HZ)
    if first > 1500 and first > 2 * replacement:
        return 'first'
    if replacement > 1500 and replacement > 2 * first:
        return 'replacement'
    return 'other'


def write_input(path: Path, noise: int) -> None:
    random_source = random.Random(0)
    # Two 20 ms activity bursts, each followed by 20 ms of configured silence.
    amplitudes = (4000, 4000, 0, 0, 5000, 5000, 0, 0)
    phase_step = math.tau * 300 / INPUT_RATE
    payload = b''.join(struct.pack('<h', round(amplitude * math.sin(phase_step * index))
                                   + random_source.randint(-noise, noise))
                       for amplitude in amplitudes for index in range(INPUT_RATE // 100))
    with wave.open(str(path), 'wb') as fixture:
        fixture.setparams((1, 2, INPUT_RATE, 0, 'NONE', ''))
        fixture.writeframes(payload)


class ScenarioSynthesizer:
    def __init__(self) -> None:
        self.first = tone(FIRST_HZ, .4)
        self.replacement = tone(REPLACEMENT_HZ, .15)
        self.first_closed = asyncio.Event()

    async def synthesize(self, text: str, *, format: AudioFormat) -> AsyncGenerator[bytes, None]:
        if text == 'first':
            try:
                yield self.first
                await asyncio.Future()
            finally:
                self.first_closed.set()
        else:
            yield self.replacement


async def run_voice_scenario(client: AudioPlane, uid: str, input_wav: Path, *,
                             noise: int, max_interruption_ms: float,
                             artifacts: Optional[Path] = None) -> dict:
    """Verify a response, barge-in, silence, and a replacement on real virtual I/O."""
    if artifacts is not None:
        artifacts.mkdir(parents=True)
        input_wav = artifacts / 'input.wav'
    write_input(input_wav, noise)

    heard_first = asyncio.Event()
    heard_quiet = asyncio.Event()
    heard_replacement = asyncio.Event()
    times = {}
    response_frames = 0
    turns = 0

    class StagedReplay(ReplayStream):
        async def __anext__(self):
            frame = await super().__anext__()
            if frame.sequence == 4:
                # The second turn begins only after the real loopback has
                # rendered the first reply. File I/O speed cannot win the race.
                await heard_first.wait()
                times['interrupt_frame'] = time.monotonic_ns()
            return frame

    format = AudioFormat(OUTPUT_RATE, 1)
    synthesizer = ScenarioSynthesizer()
    session = AudioSession(StagedReplay.from_wav(input_wav, chunk_frames=INPUT_RATE // 100),
                           client=client, speak='coreaudio:' + uid, output_format=format,
                           interrupt_on_activity=True, synthesizer=synthesizer,
                           activity_config=ActivityDetectionConfig(minimum_activity_ms=20,
                                                                   silence_duration_ms=20))

    @session.on_activity
    def record_onset(event):
        if event.type == 'activity_started' and 'interrupt_frame' in times:
            times['onset_after_flush'] = time.monotonic_ns()

    @session.on_utterance
    async def respond(event):
        nonlocal turns
        turns += 1
        if turns == 1:
            times['first_request'] = time.monotonic_ns()
            await session.say('first')
        elif turns == 2:
            await heard_quiet.wait()
            times['replacement_request'] = time.monotonic_ns()
            await session.say('replacement')
        else:
            raise AssertionError('the scenario produced an unexpected third turn')

    with ExitStack() as files:
        output_wav = None
        if artifacts is not None:
            output_wav = files.enter_context(wave.open(str(artifacts / 'captured-response.wav'), 'wb'))
            output_wav.setparams((1, 2, OUTPUT_RATE, 0, 'NONE', ''))

        async with await client.capture('microphone:' + uid, format=format) as capture:
            async def monitor() -> None:
                nonlocal response_frames
                quiet_packets = 0
                async for frame in capture:
                    if output_wav is not None:
                        output_wav.writeframesraw(frame.data)
                    classification = classify_response(frame.data)
                    if classification == 'first':
                        if heard_quiet.is_set():
                            raise AssertionError('first reply reappeared after confirmed silence')
                        if 'first_request' not in times:
                            raise AssertionError('first response was audible before it was requested')
                        response_frames += frame.frame_count
                        times.setdefault('first_audible', time.monotonic_ns())
                        heard_first.set()
                    elif classification == 'replacement' and 'replacement_request' in times:
                        times.setdefault('replacement_audible', time.monotonic_ns())
                        heard_replacement.set()
                    if 'interrupt_frame' in times and not heard_quiet.is_set():
                        quiet_packets = quiet_packets + 1 if classification == 'quiet' else 0
                        if quiet_packets == 3:
                            times['quiet_confirmed'] = time.monotonic_ns()
                            heard_quiet.set()

            async def run() -> None:
                await session.run()
                await heard_replacement.wait()

            reading = asyncio.create_task(monitor())
            running = asyncio.create_task(run())
            try:
                done, _ = await asyncio.wait((reading, running), timeout=8,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    raise AssertionError(f'voice scenario timed out after reaching {list(times)}')
                if reading in done:
                    reading.result()
                    raise AssertionError('loopback capture ended during the voice scenario')
                running.result()
            finally:
                for task in (reading, running):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(reading, running, return_exceptions=True)

    if not synthesizer.first_closed.is_set() or turns != 2 or 'onset_after_flush' not in times:
        raise AssertionError('the first reply was not interrupted before the second completed')
    observed_first_ms = response_frames * 1000 / OUTPUT_RATE
    interruption_ms = (times['quiet_confirmed'] - times['interrupt_frame']) / 1_000_000
    if observed_first_ms >= 250 or interruption_ms > max_interruption_ms:
        raise AssertionError(f'interruption exceeded budget: {interruption_ms:.2f} ms to quiet, '
                             f'{observed_first_ms:.2f} ms of first reply classified')
    result = dict(uid=uid, turns=turns, noise_amplitude=noise,
                  first_reply_latency_ms=round((times['first_audible'] - times['first_request']) / 1_000_000, 2),
                  replacement_reply_latency_ms=round(
                      (times['replacement_audible'] - times['replacement_request']) / 1_000_000, 2),
                  post_flush_onset_ms=round((times['onset_after_flush'] - times['interrupt_frame']) / 1_000_000, 2),
                  interruption_latency_ms=round(interruption_ms, 2),
                  observed_first_reply_ms=round(observed_first_ms, 2),
                  max_interruption_ms=max_interruption_ms)
    if artifacts is not None:
        (artifacts / 'report.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result
