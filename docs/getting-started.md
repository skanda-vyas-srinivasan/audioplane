# Getting started with AudioPlane

AudioPlane is local audio I/O infrastructure for macOS. Your application chooses
a source, receives labeled PCM frames, and optionally sends generated PCM to an
output. Your code supplies transcription, analysis, or AI behavior.

There are two installations: the **native Runtime**, which owns macOS audio
permissions, and the **SDK**, which your application imports. Installing the
Python package alone does not install the Runtime. Neither needs the Sonexis app.

## 1. Build and start the native Runtime

Requirements: macOS 14.4+, Xcode command-line tools, and a trusted Apple
Development signing identity configured in Xcode Settings > Accounts.
Python 3.9+ supports core capture; use Python 3.10+ for provider integrations.
Node is needed only for the Node.js SDK, not these Python examples.

```sh
git clone --branch python-v1.0.0rc1 https://github.com/skanda-vyas-srinivasan/audioplane.git
cd audioplane
./Scripts/setup-runtime-dev.sh
./Scripts/runtime-dev.sh start
```

Setup signs and installs to a managed directory under your own
`~/Library/Application Support/SonexisRuntime`. It does not use sudo, install
a background LaunchAgent, or change your audio devices. Background startup is
explicit. To select an identity, set `SONEXIS_SIGNING_IDENTITY` as described in
the [Runtime guide](sonexis-runtime.md#zero-to-audio-development-quickstart).

## 2. Install the SDK in your application's environment

Run from the checkout root. Check that `python3` is 3.10+ if using Gemini/OpenAI:

```sh
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python -m pip install audioplane==1.0.0rc1
audioplane version
audioplane doctor
audioplane sources
```

The [1.0.0rc1 SDK](https://pypi.org/project/audioplane/1.0.0rc1/) is published
on PyPI. The clone above selects its matching source and examples. Use the
explicit version to select this prerelease. For checkout-local SDK development,
use `python -m pip install -e ./SDKs/python` instead.
Avoid confusing an older pipx CLI with
the executable in your activated environment (`command -v audioplane`).

If you want just the CLI in a separate pipx environment, use
`pipx install 'audioplane==1.0.0rc1'`. This does not install the SDK into your project's
Python interpreter; use the virtual environment above for Python application code.

## 3. Verify capture without an AI provider

Choose an exact source name from `audioplane sources`. Start audio in that app,
or start it immediately after the capture message:

```sh
python Examples/capture-and-review.py \
  --source "Google Chrome" --duration 5 --capture-only
```

On first capture, approve macOS Screen & System Audio Recording permission
for the signed Runtime (`com.sonexis.runtime`). Microphone sources separately
require Microphone permission. If permission changes, a Runtime restart may
be necessary. `audioplane doctor` confirms connectivity, not capture permission
or audible output.

The example checks for non-silent PCM and reported drops/discontinuities,
closes its capture, and does not contact a provider or save audio. Non-silent
PCM is not proof of intelligible speech. Chrome capture is **application-wide**:
mute/pause other Chrome tabs before analyzing one video.

To inspect frame metadata continuously:

```sh
python Examples/capture-one-source.py "Google Chrome" --frames 16000
```

## 4. Choose the application workflow

The Runtime always supplies live frames. These are application choices, not
different capture modes:

| Workflow | What your code does | Best fit |
| --- | --- | --- |
| Realtime | Forward frames as they arrive; receive responses concurrently | Voice interaction, live captions, monitoring |
| Capture then analyze | Retain a bounded clip; send it after capture closes | Presentation review, clip summary, complete-context analysis |

Both can use the same source. Doing both simultaneously requires your code to
fan out frames into separately bounded consumers; do not concurrently iterate
the same capture object. See [developer workflows](developer-workflows.md).

### Review a complete clip

Install optional Gemini dependencies with a 3.10+ interpreter:

```sh
python -m pip install 'audioplane[gemini]==1.0.0rc1'
```

In macOS's default **zsh** terminal, enter your key at a hidden prompt:

```zsh
read -rs "GEMINI_API_KEY?Gemini API key: "; printf '\n'
export GEMINI_API_KEY
```

This exports the key in this terminal only. Never paste keys into source code,
commit them, or share a terminal log containing them.

For a roughly three-minute clip, start this command, then replay it from the
beginning in Chrome. Include a little extra time for pressing play:

```sh
python Examples/capture-and-review.py \
  --source "Google Chrome" --duration 205
```

It prints a text review after capture, uses an in-memory WAV, and sends audio
directly to Google's Gemini API—not a website transcript or a video download.
There is no local PCM file. `--duration` is bounded to 1–300 seconds; use
`--prompt` for another analysis task and `--model` for an available Gemini
audio-understanding model. Model access, quotas, cost and service availability
are controlled by Google. Its Interactions request uses `store=False`; this
does not override Google's other data-processing policies.

### Stream to a live model

With the same environment and key:

```sh
audioplane agent --provider gemini \
  --gemini-model gemini-3.8-live \
  --source "Google Chrome" --response-output default
```

Use headphones for spoken response playback. For a microphone conversation,
choose the exact physical microphone name from `audioplane sources` and add
`--gemini-barge-in` to allow interruptions. Enter `q` to quit cleanly.
The model is explicit because the CLI retains an older compatibility default.
Current provider model names can change; verify account access if one fails.
See [AI integration](ai-integration.md) for VAD, formats, OpenAI and validation.

## 5. Verify output and stop

```sh
audioplane outputs
"$HOME/Library/Application Support/SonexisRuntime/dev/bin/sonexisctl" \
  play /path/to/pcm16-audio.wav --destination default
./Scripts/runtime-dev.sh status
./Scripts/runtime-dev.sh stop
```

Speakers/headphones need no virtual device. Sending audio into another app's
microphone requires an installed loopback/virtual device and selecting that
input in the receiving app. That is optional, not a capture prerequisite.

## Troubleshooting

- **`runtime_unavailable`:** start the Runtime; confirm `audioplane doctor`.
  For a custom Runtime, pass the matching `--socket` path.
- **Missing Python module:** activate the environment where you installed the
  SDK; check `command -v python` and `python -m pip show audioplane`.
- **No frames or silent clip:** play/unmute the source and check Runtime macOS
  permission. Source discovery alone does not prove capture readiness.
- **Source not found/ambiguous:** inspect `audioplane sources` and use an exact
  source ID. Sources can terminate between discovery and capture.
- **Gap reported:** the review example refuses incomplete audio. Reduce load
  and rerun. SDK frame metadata explains drops to more advanced consumers.
- **Provider error after capture:** AudioPlane and the cloud provider are
  separate. Check key, model access, quota and network; no review is fabricated.
- **No response during live capture:** check input level/VAD with `--debug`.
  This can print private transcription; avoid shared logs. A valid local end
  does not guarantee a provider response to every segment.

More: [Python SDK](../SDKs/python/README.md), [Node.js SDK](../SDKs/typescript/README.md),
[output](output-audio.md), [security and protocol](sonexis-runtime.md).
