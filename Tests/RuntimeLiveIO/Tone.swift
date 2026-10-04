import AppKit
import AVFoundation

// Quiet synthetic application audio. Never opens a microphone or records PCM.
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let frequency = Double(CommandLine.arguments[1])!
try String(ProcessInfo.processInfo.processIdentifier).write(
    toFile: CommandLine.arguments[2], atomically: true, encoding: .utf8)
let engine = AVAudioEngine()
let format = engine.outputNode.inputFormat(forBus: 0)
var position: Int64 = 0
let source = AVAudioSourceNode { _, _, count, data -> OSStatus in
    let buffers = UnsafeMutableAudioBufferListPointer(data)
    for frame in 0..<Int(count) {
        let sample = Float(0.01 * sin(2 * Double.pi * frequency * Double(position) / format.sampleRate))
        for buffer in buffers {
            guard let pointer = buffer.mData?.assumingMemoryBound(to: Float.self) else { continue }
            for channel in 0..<Int(buffer.mNumberChannels) {
                pointer[frame * Int(buffer.mNumberChannels) + channel] = sample
            }
        }
        position += 1
    }
    return noErr
}
engine.attach(source)
engine.connect(source, to: engine.mainMixerNode, format: format)
try engine.start()
// Fail-safe for a killed test supervisor.
DispatchQueue.main.asyncAfter(deadline: .now() + 60) { engine.stop(); app.terminate(nil) }
app.run()
