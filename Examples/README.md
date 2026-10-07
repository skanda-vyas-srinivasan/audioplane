# AudioPlane examples

All examples import only a public AudioPlane SDK. Install the local Python or
TypeScript package before running the matching example.

New here? Follow [getting started](../docs/getting-started.md), then choose
[realtime streaming or capture-then-analysis](../docs/developer-workflows.md).
All Python commands below assume an activated environment with the local SDK
installed; the native Runtime is a separate prerequisite for live audio.

| Concept | Example |
| --- | --- |
| one source | `python Examples/capture-one-source.py "Google Chrome"` |
| verify capture without a provider | `python Examples/capture-and-review.py --source "Google Chrome" --duration 5 --capture-only` |
| complete-clip AI review | `python Examples/capture-and-review.py --source "Google Chrome" --duration 205` |
| labeled independent sources | `python Examples/capture-multiple-sources.py conversation=Discord media=Spotify` |
| Runtime-owned playback | `python Examples/playback.py /path/to/response.wav --destination default` |
| input/output ownership | `python Examples/duplex.py Discord --destination default` |
| minimal provider response playback | `python Examples/provider-output.py Discord` |
| agent policy over two labeled sources | `python Examples/multi-source-agent.py Discord Spotify` |
| Gemini or OpenAI | `python Examples/audio-agent/audio_agent.py --help` |
| MCP control | `python -m sonexis.mcp_server --help` |
| TypeScript capture -> fake model -> output | `Examples/typescript-duplex.mts "Google Chrome"` |

The duplex example uses matching 16 kHz input/output and echoes captured audio
only to make data flow visible. Use headphones. Provider adapters declare their
own output format and should not blindly echo capture bytes.

The TypeScript example is a standalone ESM package consumer. In a Node 18+
project, install the packed SDK plus `typescript` and `@types/node`, then compile
the `.mts` file with `tsc --module NodeNext --moduleResolution NodeNext --target
ES2022`. Its identity fake model returns input bytes only to demonstrate the
public duplex contract.

```sh
npm init -y
npm install /absolute/path/to/sonexis-runtime-1.0.0.tgz
npm install --save-dev typescript @types/node
cp /absolute/path/to/audioplane/Examples/typescript-duplex.mts .
npx tsc --module NodeNext --moduleResolution NodeNext --target ES2022 typescript-duplex.mts
node typescript-duplex.mjs "Google Chrome"
```

Examples requiring live capture need macOS Screen & System Audio Recording
permission for the signed Runtime. Provider examples additionally need their
optional SDK dependency and an environment-provided API key. `--help`, import,
and mock-provider tests do not use credentials.

The review example needs Python 3.10+, an up-to-date `google-genai` dependency
and `GEMINI_API_KEY`. It sends only captured audio to Google, not video frames
or a fetched transcript. PCM is bounded to 300 seconds and kept in memory;
review text is printed. See `--help` for duration, model and prompt overrides.
