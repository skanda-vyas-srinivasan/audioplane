"""Opt-in real HAL acceptance using public SDK APIs and synthetic audio only."""
import argparse
import asyncio
from collections import Counter
import json
import math
import os
from pathlib import Path
import plistlib
import shutil
import signal
import struct
import subprocess
import tempfile
import time
import uuid

from audioplane import AudioPlane, AudioFormat, CaptureFailedError, SonexisConnectionError


def report(check, **values):
    print(json.dumps(dict(check=check, **values)), flush=True)


class NativeAcceptance:
    def __init__(self, runtime, directory, loopbacks):
        self.runtime = runtime
        self.directory = Path(directory)
        self.loopbacks = loopbacks
        self.bundle_prefix = 'dev.audioplane.live-test.' + uuid.uuid4().hex
        self.pids = set()
        self.events = []
        self.processes = []
        self.socket_dir = self.directory / 's'
        self.socket_dir.mkdir()
        self.socket = str(self.socket_dir / 'control.sock')
        self.log = (self.directory / 'runtime.log').open('w')

    def build_apps(self):
        binary = self.directory / 'Tone'
        subprocess.run(['xcrun', 'swiftc', str(Path(__file__).with_name('Tone.swift')),
                        '-o', str(binary)], check=True)
        for label in ('a', 'b'):
            contents = self.directory / f'{label}.app' / 'Contents'
            (contents / 'MacOS').mkdir(parents=True)
            shutil.copy2(binary, contents / 'MacOS' / 'Tone')
            with (contents / 'Info.plist').open('wb') as handle:
                plistlib.dump(dict(CFBundleIdentifier=self.bundle_prefix + '.' + label,
                                  CFBundleExecutable='Tone', CFBundlePackageType='APPL',
                                  CFBundleName='AudioPlane Test ' + label), handle)

    async def start_runtime(self):
        process = subprocess.Popen([self.runtime, '--socket-dir', str(self.socket_dir)],
                                   stdout=self.log, stderr=self.log)
        self.processes.append(process)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError('signed Runtime exited before becoming ready')
            try:
                async with AudioPlane(self.socket):
                    return process
            except SonexisConnectionError:
                await asyncio.sleep(.05)
        raise RuntimeError('Runtime was not ready within five seconds')

    async def launch(self, client, label, frequency):
        pid_file = self.directory / (label + '.pid')
        previous = pid_file.read_text() if pid_file.exists() else None
        subprocess.run(['open', '-g', '-n', str(self.directory / (label + '.app')),
                        '--args', str(frequency), str(pid_file)], check=True)
        for _ in range(100):
            if pid_file.exists() and pid_file.read_text() != previous:
                self.pids.add(int(pid_file.read_text()))
                break
            await asyncio.sleep(.05)
        else:
            raise RuntimeError('synthetic application did not launch')
        source = await client.wait_for_source(self.bundle_prefix + '.' + label,
                                             timeout=5, poll_interval=.1)
        self.pids.update(source.process_ids)
        deadline = time.monotonic() + 5
        while not source.producing_audio:
            if time.monotonic() >= deadline:
                raise RuntimeError('synthetic application has no HAL audio process')
            await asyncio.sleep(.1)
            source = await client.get_source(source.id)
        return source

    @staticmethod
    async def read_frames(capture, count=30):
        # A HAL process object can precede the fixture's audible engine startup.
        # Measure (do not disguise) leading silence, then test steady signal.
        deadline = asyncio.get_running_loop().time() + 3
        silent_packets = 0
        silent_frames = 0
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise AssertionError('synthetic source did not produce a signal within three seconds')
            first = await asyncio.wait_for(capture.__anext__(), remaining)
            if max((abs(v[0]) for v in struct.iter_unpack('<h', first.data)), default=0) > 30:
                break
            silent_packets += 1
            silent_frames += first.frame_count
        if silent_packets:
            report('synthetic source startup silence', packets=silent_packets,
                   duration_ms=round(silent_frames * 1000 / first.format.sample_rate, 2))
        return [first] + [await asyncio.wait_for(capture.__anext__(), 3) for _ in range(count - 1)]

    @staticmethod
    def check_frames(frames, expected_hz):
        assert frames and all(a.timestamp_ns < b.timestamp_ns for a, b in zip(frames, frames[1:]))
        assert all(a.sequence + 1 == b.sequence for a, b in zip(frames, frames[1:]))
        samples = [value[0] for frame in frames for value in struct.iter_unpack('<h', frame.data)]
        peak = max(map(abs, samples))
        packet_peaks = [max(abs(v[0]) for v in struct.iter_unpack('<h', f.data)) for f in frames]
        # The first audible packet may start midway through a HAL callback.
        # Trim only quiet edges for frequency estimation, never internal gaps.
        first_signal = next((i for i, value in enumerate(samples) if abs(value) > 30), len(samples))
        last_signal = next((i for i in range(len(samples) - 1, -1, -1) if abs(samples[i]) > 30), -1)
        signal = samples[first_signal:last_signal + 1]
        frequency = (sum(a <= 0 < b for a, b in zip(signal, signal[1:]))
                     * frames[0].format.sample_rate / len(signal)) if signal else 0
        valid = all(value > 30 for value in packet_peaks) and abs(frequency - expected_hz) < 15
        valid = valid and all(f.dropped_frames_before == 0 for f in frames)
        if not valid:
            report('synthetic waveform diagnostic', expected_hz=expected_hz,
                   packet_peaks=packet_peaks,
                   packet_frames=[f.frame_count for f in frames],
                   dropped=[f.dropped_frames_before for f in frames])
        assert valid, (peak, frequency, expected_hz)
        return dict(packets=len(frames), peak=peak, measured_hz=round(frequency, 1),
                    leading_quiet_samples=first_signal,
                    source=frames[0].source_id, stream=frames[0].stream_id)

    @staticmethod
    async def no_sessions(client):
        for _ in range(100):
            state = await client.status()
            if state.active_sessions == 0 and state.active_output_sessions == 0:
                return
            await asyncio.sleep(.05)
        raise AssertionError('sessions did not clean up')

    async def loopback(self, client, uid):
        fmt = AudioFormat(48000, 1)
        async with await client.capture('microphone:' + uid, format=fmt) as capture:
            peaks = []

            async def read():
                async for frame in capture:
                    peaks.append(max((abs(v[0]) for v in struct.iter_unpack('<h', frame.data)), default=0))

            reading = asyncio.create_task(read())
            try:
                async with await client.playback(destination='coreaudio:' + uid, format=fmt) as output:
                    pcm = b''.join(struct.pack('<h', round(2500 * math.sin(2 * math.pi * 440 * i / 48000)))
                                   for i in range(14400))
                    await output.write(pcm)
                await asyncio.sleep(.15)
                assert max(peaks, default=0) > 100 and output.info.state == 'stopped'
                assert output.info.metrics.dropped_frames == 0
                report('actual HAL loopback', uid=uid, peak=max(peaks), dropped=0)
            finally:
                reading.cancel()
                await asyncio.gather(reading, return_exceptions=True)

    async def run(self):
        first_runtime = await self.start_runtime()
        async with AudioPlane(self.socket) as client:
            sources = await client.sources()
            destinations = await client.output_destinations()
            for uid in self.loopbacks:
                if not any(source.id == 'microphone:' + uid for source in sources):
                    raise RuntimeError('required installed loopback input missing: ' + uid)
                if not any(destination.id == 'coreaudio:' + uid and destination.kind == 'virtual_input'
                           and destination.available for destination in destinations):
                    raise RuntimeError('test requires a discovered virtual-input destination, not physical hardware: ' + uid)
            subscription = await client.events()

            async def events():
                async for event in subscription:
                    self.events.append(event)

            event_task = asyncio.create_task(events())
            try:
                a = await self.launch(client, 'a', 300)
                b = await self.launch(client, 'b', 700)
                async with AudioPlane(self.socket) as second_client:
                    async with await client.capture(a) as ca, await client.capture(b) as cb, await second_client.capture(a) as cc:
                        frames = await asyncio.gather(self.read_frames(ca), self.read_frames(cb), self.read_frames(cc))
                        results = [self.check_frames(f, hz) for f, hz in zip(frames, (300, 700, 300))]
                        assert len({f[0].stream_id for f in frames}) == 3
                        report('two apps / two clients / three independent captures', streams=results)
                async with client.session() as group:
                    await group.add('conversation', a)
                    await group.add('media', b)
                    collected = {'conversation': [], 'media': []}
                    iterator = group.frames()
                    try:
                        while any(len(v) < 20 for v in collected.values()):
                            labeled = await asyncio.wait_for(iterator.__anext__(), 3)
                            assert labeled.source.id == (a.id if labeled.label == 'conversation' else b.id)
                            if len(collected[labeled.label]) < 20:
                                collected[labeled.label].append(labeled.frame)
                    finally:
                        await iterator.aclose()
                    for label, hz in (('conversation', 300), ('media', 700)):
                        self.check_frames(collected[label], hz)
                    report('labeled multi-source SDK', passed=True)
                async with client.duplex(a, output_destination='coreaudio:' + self.loopbacks[0]) as duplex:
                    frames = await self.read_frames(duplex.input, 10)
                    await duplex.output.write(b''.join(f.data for f in frames))
                    old_stream = duplex.output.info.stream_id
                    await duplex.output.flush()
                    assert old_stream != duplex.output.info.stream_id
                    await duplex.output.write(b''.join(f.data for f in frames))
                    report('duplex and flush epoch rotation', passed=True)
                async with await client.capture(a) as capture:
                    await self.read_frames(capture, 5)
                    for pid in a.process_ids:
                        os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            await asyncio.wait_for(capture.__anext__(), 3)
                        except CaptureFailedError:
                            break
                        assert time.monotonic() < deadline, 'source exit did not terminate capture'
                for _ in range(50):
                    if not any(s.id == a.id for s in await client.sources()):
                        break
                    await asyncio.sleep(.1)
                else:
                    raise AssertionError('exited source stayed available')
                # Endpoint events are snapshots polled once per second, not a
                # lossless process audit. Keep the source absent until observed.
                for _ in range(50):
                    if any(event.type == 'source_removed' and event.source_id == a.id for event in self.events):
                        break
                    await asyncio.sleep(.1)
                else:
                    raise AssertionError('source removal event was not delivered')
                self.pids.difference_update(a.process_ids)
                relaunched = await self.launch(client, 'a', 300)
                assert relaunched.id == a.id and relaunched.process_ids != a.process_ids
                async with await client.capture(relaunched) as capture:
                    self.check_frames(await self.read_frames(capture), 300)
                report('source exit / removal / relaunch', stable_source_id=relaunched.id)
                for uid in self.loopbacks:
                    await self.loopback(client, uid)
                await self.no_sessions(client)
                await client.reconnect()
                await self.no_sessions(client)
                await asyncio.gather(event_task, return_exceptions=True)
                counts = Counter(event.type for event in self.events)
                source_counts = Counter(event.type for event in self.events if event.source_id in {a.id, b.id})
                assert source_counts['source_added'] >= 2 and source_counts['source_removed'] >= 1
                assert counts['capture_started'] > 0 and counts['capture_stopped'] > 0
                report('reconnect / cleanup / event delivery', events=dict(counts))
            finally:
                event_task.cancel()
                await asyncio.gather(event_task, return_exceptions=True)
                await subscription.aclose()
            # Crash with live resources. Restart only this test's Runtime/socket directory.
            stale_capture = await client.capture(a.id)
            stale_output = await client.playback(destination='coreaudio:' + self.loopbacks[0])
            await stale_output.write(b'\0\0' * 3200)
            first_runtime.kill()
            await asyncio.to_thread(first_runtime.wait, 5)
            await asyncio.sleep(.1)
            try:
                await client.status()
            except SonexisConnectionError:
                pass
            else:
                raise AssertionError('client did not observe Runtime death')
            await self.start_runtime()
            await client.reconnect()
            await self.no_sessions(client)
            async with await client.capture(a.id) as fresh:
                assert fresh.info.id != stale_capture.info.id
                self.check_frames(await self.read_frames(fresh), 300)
            report('SIGKILL / stale sockets / explicit reconnect / fresh capture', passed=True)

    def close(self):
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                assert process.wait(5) == 0
        self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, help='Apple Development-signed Runtime binary')
    parser.add_argument('--loopback-uid', action='append', help='installed duplex loopback UID; repeatable')
    args = parser.parse_args()
    subprocess.run(['codesign', '--verify', '--strict', args.runtime], check=True)
    print('Requires a logged-in macOS GUI and Process Tap permission. Plays two quiet synthetic tones.', flush=True)
    # Short directory is required by Darwin's Unix socket path limit.
    with tempfile.TemporaryDirectory(prefix='ap-native-', dir='/tmp') as directory:
        test = NativeAcceptance(args.runtime, directory,
                                args.loopback_uid or ['com.audioplane.input.device'])
        try:
            test.build_apps()
            asyncio.run(asyncio.wait_for(test.run(), 55))
        except BaseException:
            test.log.flush()
            print((Path(directory) / 'runtime.log').read_text()[-4000:], flush=True)
            raise
        finally:
            test.close()


if __name__ == '__main__':
    main()
