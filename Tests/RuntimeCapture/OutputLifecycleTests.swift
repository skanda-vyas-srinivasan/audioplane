import Foundation
import XCTest
@testable import SonexisRuntime

private final class LifecycleOutputSession: RuntimeBackendOutputSession, @unchecked Sendable {
    let inputFormat: RuntimePCMFormatDTO
    let destination: RuntimeOutputDestinationDTO
    let onEnded: @Sendable (RuntimeErrorDTO?) -> Void
    let onEvent: @Sendable (RuntimeOutputBackendEvent) -> Void
    private let lock = NSLock()
    private var writes = 0
    private var stopped = false

    init(destinationID: String, format: RuntimePCMFormatDTO,
         onEvent: @escaping @Sendable (RuntimeOutputBackendEvent) -> Void,
         onEnded: @escaping @Sendable (RuntimeErrorDTO?) -> Void) {
        inputFormat = format
        destination = RuntimeOutputDestinationDTO(id: destinationID, kind: .playback,
            name: destinationID, isAvailable: true, isDefault: destinationID == "default",
            followsSystemDefault: destinationID == "default")
        self.onEvent = onEvent
        self.onEnded = onEnded
    }

    func write(_ frame: RuntimePCMFrame) throws {
        lock.lock(); defer { lock.unlock() }
        writes += 1
    }
    func finish() { onEnded(nil) }
    func flush() throws {}
    func stop() { lock.lock(); stopped = true; lock.unlock() }
    func metrics() -> RuntimeOutputMetricsDTO { RuntimeOutputMetricsDTO(lateFrames: 42) }
    var writeCount: Int { lock.lock(); defer { lock.unlock() }; return writes }
    var isStopped: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
}

private final class LifecycleOutputBackend: RuntimeOutputBackend, @unchecked Sendable {
    private let lock = NSLock()
    private var created: [LifecycleOutputSession] = []
    var sessions: [LifecycleOutputSession] {
        lock.lock(); defer { lock.unlock() }; return created
    }
    func availableOutputDestinations() throws -> [RuntimeOutputDestinationDTO] { [] }
    func startOutput(destinationID: String, format: RuntimePCMFormatDTO,
                     targetBufferMilliseconds: UInt32,
                     onEvent: @escaping @Sendable (RuntimeOutputBackendEvent) -> Void,
                     onEnded: @escaping @Sendable (RuntimeErrorDTO?) -> Void)
        throws -> RuntimeBackendOutputSession {
        let session = LifecycleOutputSession(destinationID: destinationID, format: format,
            onEvent: onEvent, onEnded: onEnded)
        lock.lock(); created.append(session); lock.unlock()
        return session
    }
}

private final class LifecycleEvents: @unchecked Sendable {
    private let lock = NSLock()
    private var events: [RuntimeEventDTO] = []
    func append(_ event: RuntimeEventDTO) { lock.lock(); events.append(event); lock.unlock() }
    var values: [RuntimeEventDTO] { lock.lock(); defer { lock.unlock() }; return events }
}

final class OutputLifecycleTests: XCTestCase {
    private func eventually(_ condition: () throws -> Bool) throws {
        let deadline = Date().addingTimeInterval(2)
        while try !condition() {
            guard Date() < deadline else {
                XCTFail("output lifecycle did not settle")
                throw NSError(domain: "OutputLifecycleTests", code: 1)
            }
            Thread.sleep(forTimeInterval: 0.005)
        }
    }

    private func fixture() throws -> (RuntimeOutputCoordinator, LifecycleOutputBackend, LifecycleEvents) {
        // Darwin's ordinary long temporary directory cannot fit session UUID sockets.
        let directory = URL(fileURLWithPath: "/tmp/ap-output-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        let backend = LifecycleOutputBackend()
        let events = LifecycleEvents()
        let coordinator = RuntimeOutputCoordinator(backend: backend,
            socketDirectory: directory, eventHandler: { events.append($0) })
        addTeardownBlock {
            coordinator.stopAll()
            try self.eventually { coordinator.diagnostics().retainedTerminalRecords == 0 }
            try FileManager.default.removeItem(at: directory)
        }
        return (coordinator, backend, events)
    }

    private func send(_ output: RuntimeOutputSessionDTO, sequence: UInt64,
                      discontinuity: Bool = false, connection: UnixSocketConnection) throws {
        let payload = Data(repeating: 0, count: 320)
        let header = RuntimePCMFrameHeader(flags: discontinuity ? [.discontinuity] : [],
            payloadByteCount: 320, streamID: UUID(uuidString: output.streamID)!,
            sequence: sequence, timestampNanoseconds: sequence * 10_000_000,
            sampleRate: 16_000, frameCount: 160, channelCount: 1)
        try connection.write(RuntimePCMFrameCodec.encode(header: header, payload: payload))
    }

    func testDiscontinuityFlagsDoNotInflateLateSampleFramesAcrossFlush() throws {
        let (coordinator, backend, _) = try fixture()
        let first = try coordinator.startOutput(destinationID: "default",
            format: .runtimeDefault, ownerID: "owner")
        let oldWriter = try UnixSocketSystem.connect(path: first.dataSocketPath)
        defer { oldWriter.close() }
        try send(first, sequence: 0, discontinuity: true, connection: oldWriter)
        try send(first, sequence: 1, discontinuity: true, connection: oldWriter)
        try eventually { backend.sessions[0].writeCount == 2 }
        XCTAssertEqual(try coordinator.session(outputSessionID: first.id, ownerID: "owner").metrics.lateFrames, 42,
            "packet discontinuities are events, not late sample frames")
        let next = try coordinator.flush(outputSessionID: first.id, ownerID: "owner")
        let newWriter = try UnixSocketSystem.connect(path: next.dataSocketPath)
        defer { newWriter.close() }
        try send(next, sequence: 0, discontinuity: true, connection: newWriter)
        try eventually { backend.sessions[0].writeCount == 3 }
        let value = try coordinator.session(outputSessionID: next.id, ownerID: "owner")
        XCTAssertEqual(value.metrics.lateFrames, 42, "retired stream events inflated frame units")
        XCTAssertEqual(value.metrics.packetsReceived, 3)
        XCTAssertEqual(coordinator.diagnostics().late, 42)
        _ = try coordinator.stopOutput(outputSessionID: first.id, ownerID: "owner")
        XCTAssertEqual(coordinator.diagnostics().late, 42)
    }

    func testFixedDestinationDisconnectIsTerminalIsolatedAndDoesNotFallBack() throws {
        let (coordinator, backend, events) = try fixture()
        let pinned = try coordinator.startOutput(destinationID: "coreaudio:headphones",
            format: .runtimeDefault, ownerID: "first")
        let independent = try coordinator.startOutput(destinationID: "default",
            format: .runtimeDefault, ownerID: "second")
        let error = RuntimeErrorDTO(code: "output_destination_disconnected",
            message: "The selected output destination disconnected", retryable: true)
        backend.sessions[0].onEnded(error)
        backend.sessions[0].onEnded(error) // A duplicate HAL notification is harmless.
        try eventually { try coordinator.session(outputSessionID: pinned.id, ownerID: "first").state == .failed }
        try eventually { backend.sessions[0].isStopped }
        XCTAssertEqual(try coordinator.session(outputSessionID: pinned.id, ownerID: "first").error?.code,
            "output_destination_disconnected")
        XCTAssertTrue(try coordinator.session(outputSessionID: pinned.id, ownerID: "first").error?.retryable == true)
        XCTAssertEqual(try coordinator.session(outputSessionID: independent.id, ownerID: "second").state, .ready)
        XCTAssertEqual(backend.sessions.count, 2, "disconnect must not silently create a speaker route")
        XCTAssertFalse(FileManager.default.fileExists(atPath: pinned.dataSocketPath))
        XCTAssertEqual(events.values.filter { $0.sessionID == pinned.id && $0.type == .outputFailed }.count, 1)
        XCTAssertEqual(coordinator.diagnostics().activeSessions, 1)
    }

    func testDefaultRouteChangePreservesIdentityAndReportsDiscardedBuffer() throws {
        let (coordinator, backend, events) = try fixture()
        let output = try coordinator.startOutput(destinationID: "default",
            format: .runtimeDefault, ownerID: "owner")
        backend.sessions[0].onEvent(RuntimeOutputBackendEvent(kind: .destinationChanged,
            frames: 160, message: "Playback followed the current default output device"))
        try eventually { events.values.contains { $0.type == .outputDestinationChanged } }
        let event = try XCTUnwrap(events.values.first { $0.type == .outputDestinationChanged })
        XCTAssertEqual(event.sessionID, output.id)
        XCTAssertEqual(event.streamID, output.streamID)
        XCTAssertEqual(event.droppedFrames, 160)
        XCTAssertEqual(event.outputSession?.destinationID, "default")
        XCTAssertEqual(try coordinator.session(outputSessionID: output.id, ownerID: "owner").state, .ready)
    }

    func testFailedRouteRebuildCanBeStoppedRepeatedlyAndFreshOutputCreated() throws {
        let (coordinator, backend, events) = try fixture()
        let output = try coordinator.startOutput(destinationID: "default",
            format: .runtimeDefault, ownerID: "owner")
        backend.sessions[0].onEnded(RuntimeErrorDTO(code: "output_device_change_failed",
            message: "Output device could not be rebuilt", retryable: true))
        try eventually { backend.sessions[0].isStopped }
        for _ in 0..<3 {
            let terminal = try coordinator.stopOutput(outputSessionID: output.id, ownerID: "owner")
            XCTAssertEqual(terminal.state, .failed)
            XCTAssertEqual(terminal.error?.code, "output_device_change_failed")
        }
        XCTAssertEqual(events.values.filter { $0.type == .outputFailed }.count, 1)
        let fresh = try coordinator.startOutput(destinationID: "default",
            format: .runtimeDefault, ownerID: "owner")
        XCTAssertNotEqual(fresh.id, output.id)
        XCTAssertEqual(fresh.state, .ready)
        XCTAssertEqual(coordinator.diagnostics().activeSessions, 1)
    }
}
