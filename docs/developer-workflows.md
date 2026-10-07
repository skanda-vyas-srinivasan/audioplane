# Realtime streaming and capture-then-analysis

AudioPlane exposes a live async stream of source-aware audio frames. It does
not decide whether your software reviews a recording, transcribes speech,
answers a question, or operates an agent. The Runtime never contacts a provider.

## Capture once, choose how to consume

```python
import asyncio
from audioplane import AudioPlane

async def main():
    async with AudioPlane() as audio:
        async with await audio.capture("Google Chrome") as stream:
            async for frame in stream:
                # Your code: forward, buffer, analyze, or discard this frame.
                print(frame.source_id, frame.sequence, len(frame.data))

asyncio.run(main())
```

The SDK keeps Core Audio, socket framing, resampling and capture cleanup out
of your application. Each frame retains its source/session/stream identity,
format, sequence, timestamp and known drops. Application capture is not
tab-specific, and does not supply screen/video frames or website transcripts.

## Realtime: process while the source plays

Forward frames to a model continuously and consume provider events in a
separate task. The example below is a minimal, directly runnable consumer;
the packaged agent adds bounded forwarding, playback and interruption policy.

```python
import asyncio
from audioplane import AudioFormat, AudioPlane
from audioplane.providers import GeminiLiveSink

async def show_responses(model):
    async for event in model.events():
        if event.text:
            print(event.text, end="", flush=True)

async def main():
    async with AudioPlane() as audio:
        async with await GeminiLiveSink.connect(
            model="gemini-3.8-live",
            system_instruction="Respond in English, briefly, to what you hear.",
        ) as model:
            receiver = asyncio.create_task(show_responses(model))
            try:
                async with await audio.capture(
                    "Google Chrome", format=AudioFormat.gemini_live()
                ) as stream:
                    while not receiver.done():
                        # A send is awaited: no unbounded application queue.
                        frame = await asyncio.wait_for(stream.__anext__(), 15)
                        await asyncio.wait_for(model.send_audio(frame), 10)
                    await receiver  # surface provider receive failures
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)

asyncio.run(main())
```

Direct forwarding can backpressure capture if the provider stalls. For the
tested conversational pipeline, use `audioplane agent --provider gemini` rather
than adding an unbounded queue. It decouples capture, provider sending, response
reception and Runtime playback with bounded queues and observable input shedding.

Realtime responses are governed by provider VAD and model behavior. They are
not necessarily one reply per locally detected segment. The adapter preserves
server automatic VAD; the packaged agent adds optional WebRTC speech detection
and barge-in. [AI integration](ai-integration.md) explains those policies.

## Capture then analyze: review the full context

Run the [capture-and-review example](../Examples/capture-and-review.py):

```sh
python Examples/capture-and-review.py \
  --source "Google Chrome" --duration 205
```

The example closes capture before making a provider request. It retains at
most 300 seconds of PCM16 mono 16 kHz in memory, wraps it in an in-memory WAV,
and uses Google's Interactions API to request a text review. It is not the
Gemini Live adapter and does not download media or fetch transcripts.

It rejects known capture gaps and near-silent clips instead of presenting a
misleading review. This signal check is not speech recognition: background
music can pass it. The requested duration includes any silence while playback
is starting. The first packet must arrive within the configurable timeout.

The cloud model can mishear speech or invent details. Treat quotations,
timestamps and technical assessments as suggestions to verify, not ground truth.
Audio-only review cannot establish visual presentation or code correctness.

Use `--capture-only` to exercise capture without a key or network call. It
discards the clip afterward. Provider calls require the optional dependency,
credentials and consent to transmit the selected source's audio.

## Both workflows at once

You can fan each frame out to a live consumer and a bounded recording/analysis
consumer. Iterate the capture **once**, then explicitly dispatch frames. Give
each consumer a bounded queue and an overflow policy. Do not spawn a new task
for every frame, silently drop audio, or grow a bytearray indefinitely.

For multiple sources, use `audio.session()` and labeled frames. They remain
independent streams—not an automatically synchronized or anonymous mix.

## Privacy and ownership

- Capture only authorized sources; Chrome includes other audible Chrome tabs.
- The review example stores no local PCM, uploads no persistent file, and uses
  `store=False` for Gemini Interactions. Google's other policies still apply.
- Printed model responses may contain sensitive content. Shell redirection or
  `tee` creates a text log; use a private directory if you choose to save it.
- Context managers close owned sessions on success, failure and cancellation.
- Source exit and Runtime disconnect are explicit failures. There is no hidden
  reconnect that resurrects a capture or changes source identity.

Start with [getting started](getting-started.md) for install, permissions and
exact commands. Provider references: [Google audio input](https://ai.google.dev/gemini-api/docs/audio),
[Google Interactions retention](https://ai.google.dev/gemini-api/docs/interactions-overview#data-storage-and-retention),
and [Gemini Live SDK](https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk).
