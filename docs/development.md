# Developing and testing AudioPlane

For using AudioPlane in your own application, start with
[Getting started](getting-started.md) and the [Python SDK](../SDKs/python/README.md)
or [Node.js SDK](../SDKs/typescript/README.md). This page is for repository
contributors and release qualification.

## Repository components

| Component | Purpose |
| --- | --- |
| `Sources/SonexisRuntime` | Native Runtime: capture, playback, sessions, and local IPC |
| `Sources/Sonexisctl` | Native CLI: capture, playback, events, diagnostics, and file validation |
| `SDKs/python` | Async SDK, CLI, providers, replay, activity/VAD, duplex, and MCP |
| `SDKs/typescript` | Node.js client: capture, output, events, and duplex |
| `SonexisAudioEngine` | Repository-owned low-level Swift/C audio primitives |
| `AudioPlaneHALDriver` | Optional first-party AudioPlane Input virtual microphone |
| `Examples` | Public SDK consumers demonstrating individual workflows |

The repository does not require the Sonexis consumer application or a sibling
checkout. Some internal names retain the earlier prefix for compatibility.
The engine is a local build dependency, not a separate installed product.

## Build and install for development

Build the native products and engine package:

```sh
swift build -c release
swift test
swift test --package-path SonexisAudioEngine
```

For live capture, build and install a signed development Runtime using
`./Scripts/setup-runtime-dev.sh`. Then start it with
`./Scripts/runtime-dev.sh start`. See [Getting started](getting-started.md) for
signing prerequisites. Building alone does not establish capture permission.

For Python SDK changes, activate a virtual environment and install editable:

```sh
python -m pip install -e ./SDKs/python
```

Install the appropriate optional extras when testing provider or MCP code.
Never commit credentials, captured private audio, or generated packages.

## Runtime lifecycle

```sh
./Scripts/runtime-dev.sh status
./Scripts/runtime-dev.sh logs
./Scripts/runtime-dev.sh stop
./Scripts/runtime-dev.sh foreground
./Scripts/uninstall-runtime-dev.sh
```

The development install lives under
`~/Library/Application Support/SonexisRuntime/dev`. Startup is explicit; the
tooling does not silently install a persistent LaunchAgent.

## Automated tests

Focused SDK/package checks:

```sh
./Scripts/test-python-sdk.sh
./Scripts/test-agent-torture.sh
./Scripts/test-runtime-packages.sh
```

Complete automated release gate:

```sh
./Scripts/test-runtime-release.sh
```

The gate covers Runtime protocol/core/output/integration/fuzz/stress tests,
Thread Sanitizer, Python/Node tests and packaging, deterministic agent-pipeline
transitions, permission metadata, stable identifiers, development signing, and
virtual-driver contracts. Passing synthetic tests does not certify every audio
device or authenticated provider workflow.

In a logged-in macOS GUI session, `./Scripts/test-runtime-discovery.sh` checks
actual application launch, termination, and relaunch without capturing audio.
Set `AUDIOPLANE_TEST_GUI_DISCOVERY=1` to include it in the complete gate.
Headless gates leave this GUI check out.

## Native audio acceptance and soak

With a signed build, granted capture permission, and an installed loopback device:

```sh
./Scripts/build-signed-runtime-dev.sh
./Scripts/test-runtime-live-io.sh
# Or use an existing BlackHole device:
./Scripts/test-runtime-live-io.sh --loopback-uid BlackHole2ch_UID
# Increase synthetic capture/output lifecycle churn:
./Scripts/test-runtime-soak.sh
```

The native acceptance test starts its own signed Runtime and two temporary
quiet-tone applications. It checks independent captures, multiple clients,
labels, source exit/relaunch, duplex, flush, actual loopback, and SIGKILL/restart
recovery through public SDK APIs. It does not open a physical microphone,
persist captured PCM, call a provider, install a driver, or change selected
system devices.

Set `AUDIOPLANE_TEST_LIVE_IO=1` to opt this check into the complete gate when
AudioPlane Input is already installed. Do not install a driver as an implicit
test prerequisite. See [Output audio](output-audio.md) and
[AudioPlane Input validation](audioplane-input-manual-validation.md) for explicit
installation and receiving-application checks.

Physical output listening, device unplug/rate changes, and application microphone
interoperability still need human validation. Record what actually passed and
distinguish it from synthetic qualification. See
[workflow evidence](developer-workflows-validation.md) and the
[manual matrix](runtime-v1.0-manual-validation.md); older release reports describe
historical checkpoints, not current certification.

## Package releases

See [Python package publication](python-package-publication.md) for the published
candidate, validated hashes, and upload procedure. SDK packaging does not ship
the native Runtime or driver. Do not overwrite a published version or move its
source tag when updating documentation.
