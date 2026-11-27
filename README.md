# AudioPlane

**Capture desktop output, individual Mac apps, or microphones. Send audio back through the same SDK.**

AudioPlane gives Python and Node.js applications access to desktop audio without
implementing Core Audio themselves. Listen to Chrome, Discord, Spotify, or a
microphone; process the audio in your code; play generated audio through speakers,
headphones, or an optional virtual microphone.

Use it to build live captions, translation, audio analysis, or voice agents.
AudioPlane supplies the audio I/O. Your code or model supplies the intelligence.

**Developer preview:** the Python SDK is available as
[`audioplane 1.0.0rc1`](https://pypi.org/project/audioplane/1.0.0rc1/).
The native macOS Runtime currently requires a separate source build.

## What you install

There are two required pieces:

- **Runtime:** a local process that performs capture and playback and owns macOS
  audio permissions. It runs on the same Mac as the applications being captured.
- **SDK:** the Python or Node.js client your application uses to talk to that
  Runtime. The Python package also includes the `audioplane` CLI.

**`pip install` installs the SDK, not the Runtime.** Speakers/headphones and app
capture need no virtual driver. A virtual device is optional and only needed
when routing audio into another application's microphone input.

## Get your first audio stream

### 1. Build and start the Runtime

You need **macOS 14.4+, Xcode command-line tools, and an Apple Development signing
identity** configured in Xcode Settings > Accounts. The identity is used to sign
the native executable for macOS audio permissions.

```sh
git clone --branch python-v1.0.0rc1 https://github.com/skanda-vyas-srinivasan/audioplane.git
cd audioplane
./Scripts/setup-runtime-dev.sh
./Scripts/runtime-dev.sh start
```

This installs development tools under your user account. It does not use sudo,
install a persistent background agent, or change your selected audio devices.
See [Getting started](docs/getting-started.md) for signing and permissions help.

### 2. Install the Python SDK

Use Python **3.9+** for basic audio I/O, or **3.10+** for AI integrations.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install audioplane==1.0.0rc1
audioplane doctor
audioplane sources
```

`doctor` checks the Runtime connection. The first capture may ask you to approve
macOS Screen & System Audio Recording permission; microphone capture needs
Microphone permission separately.

### 3. Capture an application

Play some audio in Chrome. Save this as `capture.py`, then run `python capture.py`:

```python
import asyncio
from audioplane import AudioPlane

async def main():
    async with AudioPlane() as audio:
        async with await audio.capture("Google Chrome") as stream:
            async for frame in stream:
                print(frame.source.name, frame.timestamp_ns, len(frame.data))
                # Your code can consume frame.data here.

asyncio.run(main())
```

Each frame contains raw audio samples, source identity, format, timing, and drop
information. Press **Ctrl+C** to stop. Replace `"Google Chrome"` with an exact
name or ID from `audioplane sources`; ambiguous selections produce an error.

Capture is **application-wide, not browser-tab-specific**. Pause other audible
Chrome tabs if you want one video's audio. AudioPlane does not capture video
frames or fetch website transcripts.

To capture everything routed to the Mac's current default output, use the
`system_mix` source instead of an application name:

```python
async with AudioPlane() as audio:
    async with await audio.capture("system_mix") as stream:
        async for frame in stream:
            print(frame.timestamp_ns, len(frame.data))
```

`system_mix` excludes the Runtime's own playback and does not include the
microphone. The capture follows the default output device when it changes.

## What happens after capture?

Your application chooses how to consume the stream:

- **Realtime:** process or forward each frame as it arrives, for live captions,
  monitoring, or a realtime model.
- **Capture then analyze:** collect a bounded clip and send it for a complete
  review or summary after capture ends.
- **Output:** send generated audio back through AudioPlane for playback. Routing
  into another app requires a virtual input selected in that receiving app.

See [Developer workflows](docs/developer-workflows.md) for both input patterns,
[AI integration](docs/ai-integration.md) for Gemini/OpenAI examples, and
[Output audio](docs/output-audio.md) for playback and duplex APIs.

## What else is supported?

- Multiple independent sources with labels and source-aware frames.
- Application and microphone discovery, events, and structured errors.
- PCM16 and Float32 audio, supported mono/stereo formats, and format conversion.
- Bounded input/output buffering, backpressure, and observable drops.
- Playback, flush/cancel primitives, and optional virtual-microphone routing.
- Python and Node.js SDKs, diagnostics CLI, and optional control-only MCP.

## Important boundaries

- **Local macOS infrastructure:** not a browser SDK, cloud service, or ready-made
  AI assistant. The Node package is not published to npm yet.
- **No cloud required for basic I/O:** optional provider integrations send audio
  to the chosen provider. Audio is not recorded to disk by default.
- **Same-user trust:** other unsandboxed processes under your macOS account can
  use the Runtime's granted permissions. Do not run it as root or expose its
  sockets. Read the [security model](docs/sonexis-runtime.md#security-and-trust-model).
- **No built-in acoustic echo cancellation or sample-accurate cross-source
  synchronization.** Use headphones for voice-agent playback.
- **Preview distribution:** no notarized Runtime installer yet; some hardware,
  device-switching, and provider acceptance checks remain manual. See
  [qualification evidence](docs/developer-workflows-validation.md).

## Documentation

- [Getting started](docs/getting-started.md) — installation, permissions, troubleshooting.
- [Python SDK](SDKs/python/README.md) · [Node.js SDK](SDKs/typescript/README.md).
- [Examples](Examples/README.md) — capture, playback, multi-source, and AI demos.
- [Architecture and protocol](docs/sonexis-runtime.md) — sessions, sockets, framing, diagnostics.
- [Development and testing](docs/development.md) — builds, release gates, and native acceptance.
- [Full documentation index](docs/runtime-documentation.md).

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).
