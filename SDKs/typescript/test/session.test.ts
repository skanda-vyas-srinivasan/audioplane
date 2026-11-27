import assert from "node:assert/strict";
import test from "node:test";
import { AudioFormat, AudioFormats, AudioFrame, AudioOutput, AudioSession, AudioSource,
  CaptureStream, Sonexis, SonexisError } from "../src/index.js";

const source: AudioSource = {
  id: "app.voice", name: "Voice", kind: "application", process_ids: [101],
  bundle_identifier: "voice", process_state: "running", is_available: true,
};
const format = AudioFormats.speech16k();
const activity = { minimumActivityMs: 10, silenceDurationMs: 10 };

function frame(sequence: bigint, active: boolean): AudioFrame {
  const data = Buffer.alloc(320);
  if (active) for (let offset = 0; offset < data.length; offset += 2) data.writeInt16LE(4000, offset);
  return { source, sourceId: source.id, sourceName: source.name, sessionId: "session", streamId: "stream",
    sequence, timestampNs: sequence * 10_000_000n, receivedAtNs: 0n, frameCount: 160,
    format, data, discontinuity: false, droppedFramesBefore: 0, endOfStream: false };
}

function fixture() {
  const client = new Sonexis("/unused");
  const calls: string[] = [];
  const capture = {
    async *[Symbol.asyncIterator]() { yield frame(0n, true); yield frame(1n, false); },
    async close() { calls.push("close capture"); },
  };
  const output = {
    async write(data: Buffer) { calls.push(`write ${data.length}`); },
    async flush() { calls.push("flush"); },
    async close({ drain }: { drain: boolean }) { calls.push(`close output ${drain}`); },
  };
  client.capture = async (selector, requestedFormat?: AudioFormat) => {
    assert.equal(selector, source.id);
    assert.deepEqual(requestedFormat, format);
    return capture as unknown as CaptureStream;
  };
  client.playback = async (options) => {
    assert.deepEqual(options, { destination: "coreaudio:device", format });
    return output as unknown as AudioOutput;
  };
  client.close = async () => { assert.fail("AudioSession closed a caller-owned client"); };
  const session = new AudioSession(client, source.id, { activity, output: { destination: "coreaudio:device" } });
  return { client, session, capture, calls };
}

test("AudioSession dispatches ordered activity edges and drains its selected output", async () => {
  const { session, calls } = fixture();
  const pcm = Buffer.alloc(320);
  session.onFrame((frame) => { calls.push(`frame ${frame.sequence}`); });
  session.onActivity(async (event) => {
    calls.push(`${event.type} ${event.sourceId}`);
    if (event.type === "activity_started") await session.playPcm(pcm);
    else await session.interruptPlayback();
  });
  await session.run();
  assert.deepEqual(calls, ["frame 0", "activity_started app.voice", "write 320", "frame 1", "activity_ended app.voice",
    "flush", "close capture", "close output true"]);
  await assert.rejects(session.playPcm(pcm), (error: SonexisError) => error.code === "session_output_inactive");
  await assert.rejects(session.run(), (error: SonexisError) => error.code === "session_started");
});

test("AudioSession discards pending audio when a callback fails", async () => {
  const { session, calls } = fixture();
  const failure = new Error("callback failed");
  session.onActivity(() => { throw failure; });
  await assert.rejects(session.run(), (error) => error === failure);
  assert.deepEqual(calls, ["close capture", "close output false"]);
});

test("aborting AudioSession wakes a blocked read and cancels output", async () => {
  const { session, capture, calls } = fixture();
  const controller = new AbortController();
  let ready!: () => void;
  let releaseRead!: () => void;
  const reading = new Promise<void>((resolve) => { ready = resolve; });
  const closed = new Promise<void>((resolve) => { releaseRead = resolve; });
  capture[Symbol.asyncIterator] = async function* () { ready(); await closed; };
  capture.close = async () => { releaseRead(); };
  const running = session.run({ signal: controller.signal });
  await reading;
  controller.abort();
  await assert.rejects(running, (error: SonexisError) => error.code === "cancelled");
  assert.ok(calls.length > 0 && calls.every((call) => call === "close output false"));
});
