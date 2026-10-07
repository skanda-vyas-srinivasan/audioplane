# Developer workflow qualification — 2026-10-07

This is evidence for the onboarding/examples pass, not a guarantee across every
macOS version, audio device, cloud account or network condition. The Runtime
capture/data planes and provider adapters were not redesigned by this pass.

## Changes

- Canonical [getting started](getting-started.md): native Runtime vs. SDK,
  checkout-local install, permission/first-audio checks and provider setup.
- [Developer workflows](developer-workflows.md): live streaming vs. bounded
  capture-then-analysis, concurrent response consumption and source scope.
- Public `AudioPlane` imports and standalone repository paths in the primary
  SDK/provider guides. Compatibility binary/module/signing names are retained.
- [Capture-and-review](../Examples/capture-and-review.py): bounded 1–300 second
  PCM clip, in-memory WAV, explicit Gemini request, no-cloud verification,
  cancellation cleanup, timeout/gap/silence errors and safe provider diagnostics.
- The review example is included in installed-package example smoke tests.

Code checkpoints: `013b2b3` (example and tests) and `b32edb3` (provider-error
redaction). Documentation is versioned alongside those implementations.

## Automated evidence

On macOS 27 arm64, Swift 6.4, Python 3.9.6/3.14.7 and Node 26:

| Check | Result |
| --- | --- |
| New example unit tests on Python 3.9 and a fresh Python 3.14 environment | 11 passed |
| Fresh non-editable `pip install './SDKs/python[gemini]'` without PYTHONPATH | Passed |
| Fresh installed CLI: version, doctor, example syntax/import/help | Passed |
| Actual Process Tap capture using a controlled synthetic tone app and fresh installed SDK | Passed: bounded 2-second capture; no reported gaps |
| Existing single-source example using fresh installed SDK | Passed: at least 16,000 sample frames |
| Authenticated new review example using captured synthetic tone audio | Passed: Gemini reported no intelligible speech |
| Full optional Python suite with OpenAI/MCP installed, synthesized VAD speech and actual BlackHole playback, including onboarding checks | 195 passed; no skips |
| Full release gate, including real GUI app discovery | Passed |
| Swift Runtime / engine tests | 9 / 5 passed |
| Node.js SDK tests | 39 passed |
| Protocol, core, output, Unix IPC integration and fuzz corpus | Passed |
| Capture/output stress, callback concurrency and TSan gates | Passed |
| Agent torture | 1,009,497 deterministic transitions |
| Signed Runtime/CLI build, HAL driver contracts and temporary install/tamper checks | Passed |
| Python wheel/sdist and Node package consumer smoke | Passed |

The authenticated new example used `google-genai` 2.28.0 and
`gemini-3.5-flash-lite` with the Interactions API and `store=False`. It did not
use a fetched website transcript or bypass AudioPlane capture. The synthetic
test is proof of transport/API execution, not a speech-understanding benchmark.

A separate, earlier user-authorized Chrome presentation capture produced a
content-specific Gemini review from actual PCM, with zero reported capture
drops/discontinuities. The first cloud request timed out; another available
model completed the review. No private audio, transcript, URL, credentials or
review content from that run is included in this repository.

## Limits and remaining human checks

- Fresh environment here means isolated Python installation against this
  checkout on the current Mac. It is not a new Mac or minimum-platform test.
- Signing/build checks do not imply Developer ID notarization or package-store
  publication. Nothing is published by the quickstart scripts.
- The driver was built/contract-tested, not installed by this pass. The optional
  output test used an already installed BlackHole device.
- Newly documented `gemini-3.8-live` is explicit current Google SDK guidance;
  this pass did not perform a new authenticated Live conversation on that model.
- OpenAI's installed connection factory was tested without authenticated
  networking. MCP tool construction is not an external-host acceptance test.
- Physical headphone listening, device unplug/rate changes, permission-denial
  UI, Discord/Zoom reception and minimum OS/toolchain qualification remain in
  the [manual matrix](runtime-v1.0-manual-validation.md).
- Model names/access change; the review CLI exposes `--model` and the live
  CLI exposes `--gemini-model`. A provider error is not fabricated success.

## Reproduce

```sh
./Scripts/test-runtime-release.sh
# Optional real launch/exit/relaunch discovery:
AUDIOPLANE_TEST_GUI_DISCOVERY=1 ./Scripts/test-runtime-release.sh
# Installed-package example checks, with a fresh environment's interpreter:
PYTHON=/path/to/fresh-env/bin/python SONEXIS_EXAMPLES_USE_INSTALLED=1 \
  ./Scripts/test-runtime-examples.sh
python -m unittest discover -s SDKs/python/tests -p test_capture_review_example.py -v
```

Follow [getting started](getting-started.md) for the no-cloud capture and actual
review commands. Provider-network calls are explicit; no automated regression
requires real credentials. Test recordings, if generated, are synthetic only.
