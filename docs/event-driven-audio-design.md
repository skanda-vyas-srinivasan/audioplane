# Event-driven audio sessions

The first implementation adds a Python session that turns live or recorded audio into activity callbacks. A callback can write PCM to a selected output, so the same interface can demonstrate event-driven automation and audio interaction without a model or a cloud account.

## Baseline before the first slice

| Area | Existing capability | Gap at the start |
| --- | --- | --- |
| Capture and discovery | The macOS Swift Runtime discovers apps and devices, captures app audio through Core Audio process taps, and supports microphone capture. The SDK already resolves selectors and exposes Runtime lifecycle events. | Applications compose discovery, capture, and local detection themselves. Recovery depends on the route and failure. |
| Playback and injection | Playback targets Core Audio outputs or an installed virtual input such as AudioPlane Input. The Runtime and SDK already handle bounded buffering, drain, flush, and cancellation. `DuplexSession` pairs capture and playback. | Application input selection, feedback control, and interruption policy belong to the application. |
| Concurrency and timing | Independent capture and playback sessions, labeled Python multi-source capture, capture normalization, packet timestamps, discontinuity markers, and queue limits already exist. | Independent input and output clocks provide no shared synchronization guarantee. Real capture-to-action latency and multi-app capacity need signed live measurements. |
| Detection and replay | Python has a debounced energy detector, an optional WebRTC VAD, and source-aware PCM/WAV replay. TypeScript provides low-level capture and playback. | The Python primitives lack a common session that dispatches local activity events from either live or replay input. |
| Providers and actions | The CLI agent already contains provider-specific input forwarding, response playback, and interruption behavior. | That orchestration couples useful policies to a CLI workflow. A small SDK entry point lets callers supply their own actions before introducing any provider contract. |

The reuse points are the [public SDK contract](runtime-api-stability.md), the [runtime protocol](sonexis-runtime.md), and the existing [output APIs](output-audio.md). The [runtime benchmarks](runtime-benchmarks.md) establish synthetic conversion, framing, and lifecycle baselines. They leave live Core Audio latency and recovery behavior to signed validation.

## Architecture and first implementation

1. Add `audioplane.AudioSession` in `SDKs/python/src/audioplane/session.py`. It accepts one live source selector or `ReplayStream`, an optional output selector, and optional capture/output formats. A caller may supply an already connected `AudioPlane` client to share a Runtime connection. Independent sessions keep their own detectors and streams.
2. Use `AudioActivityDetector` for `activity_started` and `activity_ended`. Accept the existing `ActivityDetectionConfig` and `VoiceActivityDetector` extension point. Event edges retain their source, stream, sequence, and timestamp. A discontinuity resets detection through the existing detector. End of stream ends the session without inventing a silence edge.
3. Register sync or async callbacks with `@session.on_activity`. `await session.run()` consumes input and dispatches callbacks serially in registration order. The run is single-use, since detector and replay state belong to one stream. Callback and I/O errors reach the caller. The existing stream context managers close captures, drain output on normal completion, and discard it when input processing fails or is cancelled.
4. Expose `await session.play_pcm(data)` for caller-provided PCM in the configured output format. Open playback only when a destination is selected. Reuse `AudioOutput.write()` and its existing backpressure and format checks.
5. Add `Examples/event-driven-audio.py`. It reports activity onset and silence on either live input or WAV replay, and optionally plays a short tone on onset through the selected output. Verify the replay path and cover live resource ownership, PCM dispatch, and error/cancellation cleanup with SDK tests.

The Swift Runtime owns Core Audio and device lifecycle. The Python session composes its public capture and output APIs plus replay. The base session runs callbacks on the capture consumer. Applications can enable the bounded worker for activity and utterance callbacks.

```python
from audioplane import AudioSession

session = AudioSession(listen="Discord")

@session.on_activity
def handle_activity(event):
    print(event.type, event.source_id, event.timestamp_ns)

await session.run()
```

## Buffered utterances

Register `@session.on_utterance` to receive immutable `UtteranceEvent` values
containing interleaved PCM, the input format, source and stream identity, and
stream-relative timestamps. The event uses the same classifier as activity
edges. Pass a `VoiceActivityDetector` for speech classification. The default
energy detector also segments loud sounds.

The session retains the candidate onset while the detector confirms activity.
The emitted audio starts at that candidate and may include trailing silence
used to confirm the end. Each event contains at most `max_utterance_ms` of PCM
rounded down to whole sample frames, which defaults to 30 seconds. Continuous
activity at the limit yields consecutive segments with `duration_limit` as
the end reason. The other end reasons are `silence`, `discontinuity`, and
`end_of_input`. A gap ends the prior segment before the new PCM. Clean end of
input emits a confirmed remainder. A failed or cancelled run discards its
pending audio.

```python
from audioplane import AudioSession, ReplayStream

session = AudioSession(ReplayStream.from_wav("recording.wav"), max_utterance_ms=10_000)

@session.on_utterance
def handle_utterance(event):
    print(event.frame_count, event.end_reason)

await session.run()
```

## Transcription and synthesized playback

Pass a `Transcriber` and register `@session.on_transcript` to receive final
text for each bounded utterance. The transcriber receives the utterance PCM
and format. The resulting `TranscriptEvent` references the original utterance.
Blank results produce no callback. Transcription starts only when a transcript
handler is registered, and it runs after utterance handlers for that segment.

Pass a `SpeechSynthesizer` and select an output to use `await session.say(text)`.
A synthesizer can yield PCM as it becomes available in the selected output
format. `say()` waits for the SDK to write those chunks and uses the Runtime's
existing buffering and drain behavior. Use one `say()` call at a time, and
await it inside a callback so the output stays open.

The optional `OpenAITranscriber` sends each utterance as a WAV to a caller-owned
async client. `MacOSSpeechSynthesizer` renders each reply using the installed
voice and outputs mono PCM16 at 48 kHz by default. Pass `format=AudioFormat.speech_16k()`
for a 16 kHz output such as Companion's local voice path. A local transcriber can
implement the same interface. For upload and authentication details, see the
[OpenAI transcription guide](https://developers.openai.com/api/docs/guides/speech-to-text).

```python
import asyncio

from openai import AsyncOpenAI

from audioplane import AudioSession, ReplayStream
from audioplane.providers import MacOSSpeechSynthesizer, OpenAITranscriber


async def main():
    async with AsyncOpenAI() as client:
        voice = MacOSSpeechSynthesizer()
        session = AudioSession(
            ReplayStream.from_wav("recording.wav", chunk_frames=160),
            speak="coreaudio:com.audioplane.input.device",
            output_format=voice.format,
            transcriber=OpenAITranscriber(client),
            synthesizer=voice,
        )

        @session.on_transcript
        async def respond(event):
            print(event.text)
            await session.say("I heard you.")

        await session.run()


asyncio.run(main())
```

## Bounded callbacks and playback interruption

Set `callback_queue_size` to let capture and activity detection continue while
an async callback or provider awaits I/O. One worker runs callbacks in event
order and drains queued events at clean end of input. The limit counts pending
activity and utterance events, in addition to the event the worker is handling.
An utterance can contain up to `max_utterance_ms` of PCM. A full queue raises
`CallbackQueueFullError` and ends the run. Keep synchronous callbacks short and
offload CPU-heavy work from the event loop. With the default configuration,
callbacks run directly on the capture consumer task.

Pass `interrupt_on_activity=True` with an output destination to enable barge-in.
This enables the callback worker with a default capacity of 32. A detected
onset interrupts active synthesis or flushes submitted audio before delivering
the onset callback. Use a VAD to trigger on speech, and route input and output
to avoid detecting the session’s own playback. `await session.interrupt_playback()`
also works on demand. The next reply can use the same destination.

The session cancels the pending synthesis read and waits for any current PCM
write before flushing. Synthesized audio is written in roughly 40 ms pieces
to give interruption a chance between writes. A custom synth can release its
resources when its generator closes. Some providers can continue work outside
the SDK after that point. Audio already rendered by the device remains audible.

## Live session recovery and stream timing

Use `run_recovering_session()` to opt into replacing a failed live session. The
runner resolves the source and optional output, then pins their Runtime IDs.
It opens a fresh connection per attempt and gives the factory the newly resolved
endpoint objects, so a session can register new callbacks and initialize a fresh
VAD or other stream-local state. Prefer bundle IDs for relaunchable apps and
Core Audio IDs for devices. A process-only source may have a different ID after
relaunch. Passing `default` explicitly selects the Runtime's system-default route.

```python
from audioplane import AudioSession, run_recovering_session


def make_session(source, destination, client):
    session = AudioSession(source, speak=destination, client=client)

    @session.on_activity
    def report(event):
        print(event.type, event.source_id, event.stream_id)

    return session


await run_recovering_session("Discord", make_session, max_restarts=20, retry_delay=0.5)
```

Each restart consumes one attempt in the run's budget. The runner handles
unavailable endpoints, terminal capture failures including older Runtime app
exit errors, and retryable connection failures. Clean end of input ends the run.
Callback and provider exceptions propagate. The new session begins with empty
audio buffers. The recovery runner checks the selected playback session's
Runtime status every half second so it also detects failure while output is idle.

Capture timestamps describe media position within one stream. A replacement
has a new stream ID and timeline. Output timestamps describe submitted PCM
order. Independent capture and playback paths can have different device clocks,
buffering, and resampling delays. Use host monotonic time for local callback and
control-operation measurements. To measure end-to-end latency or align across
streams, inject and detect a shared signal and account for discontinuities.

## Streaming processing and conversation state

`@session.on_frame` receives every source-aware PCM packet, including silence,
before detection and queued callbacks. Frame callbacks run inline. A slower
consumer can use the bounded provider forwarding pattern in `audioplane agent`,
or apply its own backpressure policy. [Local tone automation](../Examples/audio-automation.py)
shows how to turn frames into a deterministic action without a provider.

For live transcription, `audioplane agent --provider gemini --source Discord
--input-transcripts` uses the existing realtime transport and emits incoming
transcript updates separately from the provider's replies. SDK callers can
enable the same events with `GeminiLiveSink.connect(emit_input_transcripts=True)`
and consume `input_transcription` events. Batched `on_transcript` callbacks
continue to deliver the final text from each configured utterance transcriber.

`ConversationHistory` retains caller-selected text turns within a turn and
character budget. A caller can append a transcript and a reply, pass an immutable
snapshot to its model, and keep the same context across capture reconnections.
The session never adds transcripts to history on its own.

```python
from audioplane import ConversationHistory

history = ConversationHistory(max_turns=12, max_characters=6_000)

@session.on_transcript
async def respond(event):
    history.append("user", event.text)
    reply = await my_model.respond(history.turns)
    history.append("assistant", reply)
    await session.say(reply)
```

## Implementation coverage

| Roadmap area | Implementation and verification |
| --- | --- |
| Core audio | Signed acceptance exercises independent application taps, source discovery, duplex PCM, virtual injection, app relaunch, and Runtime restart. Recovery tests also simulate an idle output disconnect. |
| Event-driven runtime | Python sessions expose raw frames, VAD and silence events, bounded utterances, callbacks, streaming provider events, optional synthesis, and interruption. Conversation history and automation actions stay under application control. |
| Reference applications | Companion uses the session frame callback and shared speech adapter. The signed suite exercises a separate test app and virtual microphone with the voice participant. The two-turn scenario checks interruption, noise, latency, recording, and replay. Tone automation runs locally. |
| SDK and platform | The Python SDK is the full reference API. The TypeScript SDK offers activity and frame callbacks, PCM output, and cancellation. The backend interfaces isolate the implemented macOS audio I/O. |

## Remaining acceptance and optional extensions

1. Run the [hot-plug and default-route acceptance modes](runtime-benchmarks.md#external-participant-and-physical-hot-plug-checks) on removable audio hardware.
2. Measure feedback and interruption timing across physical and virtual routes, then define synchronization guarantees where the platform supports them.
3. Validate the [voice participant](../Examples/voice-participant.py) in a controlled call. The [two-turn scenario](runtime-benchmarks.md#reproducible-two-turn-voice-scenario) exercises the SDK against a synthetic app and a virtual microphone. Companion uses session frame callbacks for desktop capture and a separate output for replies, with the shared activity meter and macOS speech adapter.
4. Evaluate further TypeScript parity after the reference workflows run against their target apps. The [TypeScript activity session](../SDKs/typescript/README.md#activity-sessions) covers activity callbacks, PCM playback, manual interruption, and cancellation. Bounded callback delivery, utterances, speech providers, and recovery remain Python features.

## Platform and operational limits

- Live application capture depends on the macOS Runtime, OS audio permissions, and a source the process tap can capture. WAV replay can exercise the callback path without macOS capture permission or a Runtime connection.
- The Runtime capture and playback backend interfaces isolate device I/O from the protocol and these SDK features. macOS is the implemented live backend.
- Virtual microphone injection requires the receiving app to select the installed virtual input. Route audio to headphones for live microphone tests to avoid acoustic feedback.
- Default energy detection signals loudness and silence. Applications that need speech classification can pass a VAD and use a supported mono PCM16 format. Activity debounce, stream packet size, and callback execution time all affect when an action runs.
- Capture and output can stop independently. Standalone sessions propagate Runtime errors. The recovery runner creates replacement sessions for its configured routes.
- When sessions share a client, cancellation during a mutating Runtime request can reset that connection and end its other streams. Use the default per-session connection for independent failure handling.
