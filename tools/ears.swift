// ears: play a WAV out of one CoreAudio device while recording another, both picked by name and direction
// (the USB Audio CODEC shows up as two devices with the same name, which sox cannot tell apart).
//   ears list
//   ears <device name> <play.wav | -> <record.wav> <seconds>
// play.wav: 16-bit PCM, 48 kHz, stereo (make it with sox). record.wav: 16-bit, 48 kHz, stereo.
import AudioToolbox
import CoreAudio
import Foundation

func prop<T>(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope, _ v: inout T) -> OSStatus {
    var a = AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    return AudioObjectGetPropertyData(obj, &a, 0, nil, &size, &v)
}
func channels(_ dev: AudioObjectID, _ scope: AudioObjectPropertyScope) -> Int {
    var a = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(dev, &a, 0, nil, &size) == noErr, size > 0 else { return 0 }
    let buf = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16); defer { buf.deallocate() }
    guard AudioObjectGetPropertyData(dev, &a, 0, nil, &size, buf) == noErr else { return 0 }
    let list = UnsafeMutableAudioBufferListPointer(buf.assumingMemoryBound(to: AudioBufferList.self))
    return list.reduce(0) { $0 + Int($1.mNumberChannels) }
}
struct Dev { let id: AudioObjectID; let name: String; let uid: String; let ins: Int; let outs: Int }
func devices() -> [Dev] {
    var a = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size)
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, &ids)
    return ids.map { id in
        var name: CFString = "" as CFString, uid: CFString = "" as CFString
        _ = prop(id, kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal, &name)
        _ = prop(id, kAudioDevicePropertyDeviceUID, kAudioObjectPropertyScopeGlobal, &uid)
        return Dev(id: id, name: name as String, uid: uid as String,
                   ins: channels(id, kAudioObjectPropertyScopeInput), outs: channels(id, kAudioObjectPropertyScopeOutput))
    }
}
func fail(_ s: String) -> Never { FileHandle.standardError.write((s + "\n").data(using: .utf8)!); exit(1) }

let args = CommandLine.arguments
if args.count == 2 && args[1] == "list" {
    for d in devices() { print("\(d.name)\tin \(d.ins)\tout \(d.outs)\tuid \(d.uid)") }
    exit(0)
}
guard args.count == 5, let seconds = Double(args[4]) else { fail("usage: ears list | ears <device> <play.wav|-> <record.wav> <seconds>") }
let all = devices()
guard let inDev = all.first(where: { $0.name == args[1] && $0.ins >= 2 }) else { fail("no input device named \(args[1])") }
guard let outDev = all.first(where: { $0.name == args[1] && $0.outs >= 2 }) else { fail("no output device named \(args[1])") }

let rate = 48000.0
var fmt = AudioStreamBasicDescription(mSampleRate: rate, mFormatID: kAudioFormatLinearPCM,
    mFormatFlags: kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked,
    mBytesPerPacket: 4, mFramesPerPacket: 1, mBytesPerFrame: 4, mChannelsPerFrame: 2, mBitsPerChannel: 16, mReserved: 0)

// playback data: the WAV's data chunk (expects 16-bit stereo 48 kHz)
var playData = Data()
if args[2] != "-" {
    guard let w = FileManager.default.contents(atPath: args[2]) else { fail("cannot read \(args[2])") }
    var p = 12
    while p + 8 <= w.count {
        let id = String(data: w[p..<p + 4], encoding: .ascii) ?? ""
        let n = Int(w[p + 4]) | Int(w[p + 5]) << 8 | Int(w[p + 6]) << 16 | Int(w[p + 7]) << 24
        if id == "fmt " {
            let ch = Int(w[p + 10]) | Int(w[p + 11]) << 8
            let sr = Int(w[p + 12]) | Int(w[p + 13]) << 8 | Int(w[p + 14]) << 16
            let bits = Int(w[p + 22]) | Int(w[p + 23]) << 8
            if ch != 2 || sr != 48000 || bits != 16 { fail("play.wav must be 16-bit stereo 48 kHz (got \(bits)-bit \(ch) ch \(sr) Hz)") }
        }
        if id == "data" { playData = w[(p + 8)..<min(w.count, p + 8 + n)]; break }
        p += 8 + n + (n & 1)
    }
}

final class State { var play = Data(); var pos = 0; var rec = Data(); let lock = NSLock() }
let st = State(); st.play = Data(playData)
let ctx = Unmanaged.passUnretained(st).toOpaque()
let bufBytes: UInt32 = 4 * 1024

var outQ: AudioQueueRef?
var s = AudioQueueNewOutput(&fmt, { user, q, buf in
    let st = Unmanaged<State>.fromOpaque(user!).takeUnretainedValue()
    let cap = Int(buf.pointee.mAudioDataBytesCapacity)
    let dst = buf.pointee.mAudioData.assumingMemoryBound(to: UInt8.self)
    let n = max(0, min(cap, st.play.count - st.pos))
    if n > 0 { st.play.withUnsafeBytes { src in memcpy(dst, src.baseAddress! + st.pos, n) } }
    if n < cap { memset(dst + n, 0, cap - n) }
    st.pos += n
    buf.pointee.mAudioDataByteSize = UInt32(cap)
    AudioQueueEnqueueBuffer(q, buf, 0, nil)
}, ctx, nil, nil, 0, &outQ)
if s != noErr { fail("AudioQueueNewOutput \(s)") }
var outUID = outDev.uid as CFString
s = AudioQueueSetProperty(outQ!, kAudioQueueProperty_CurrentDevice, &outUID, UInt32(MemoryLayout<CFString>.size))
if s != noErr { fail("set output device \(s)") }

var inQ: AudioQueueRef?
s = AudioQueueNewInput(&fmt, { user, q, buf, _, _, _ in
    let st = Unmanaged<State>.fromOpaque(user!).takeUnretainedValue()
    st.lock.lock(); st.rec.append(buf.pointee.mAudioData.assumingMemoryBound(to: UInt8.self), count: Int(buf.pointee.mAudioDataByteSize)); st.lock.unlock()
    AudioQueueEnqueueBuffer(q, buf, 0, nil)
}, ctx, nil, nil, 0, &inQ)
if s != noErr { fail("AudioQueueNewInput \(s)") }
var inUID = inDev.uid as CFString
s = AudioQueueSetProperty(inQ!, kAudioQueueProperty_CurrentDevice, &inUID, UInt32(MemoryLayout<CFString>.size))
if s != noErr { fail("set input device \(s)") }

for _ in 0..<4 {
    var b: AudioQueueBufferRef?
    AudioQueueAllocateBuffer(inQ!, bufBytes, &b); AudioQueueEnqueueBuffer(inQ!, b!, 0, nil)
}
s = AudioQueueStart(inQ!, nil); if s != noErr { fail("start input \(s)") }
for _ in 0..<4 {
    var b: AudioQueueBufferRef?
    AudioQueueAllocateBuffer(outQ!, bufBytes, &b)
    b!.pointee.mAudioDataByteSize = bufBytes; memset(b!.pointee.mAudioData, 0, Int(bufBytes))
    AudioQueueEnqueueBuffer(outQ!, b!, 0, nil)
}
s = AudioQueueStart(outQ!, nil); if s != noErr { fail("start output \(s)") }
CFRunLoopRunInMode(.defaultMode, seconds, false)
AudioQueueStop(outQ!, true); AudioQueueStop(inQ!, true)

st.lock.lock(); let rec = st.rec; st.lock.unlock()
var hdr = Data()
func u32(_ v: Int) { var x = UInt32(v).littleEndian; hdr.append(Data(bytes: &x, count: 4)) }
func u16(_ v: Int) { var x = UInt16(v).littleEndian; hdr.append(Data(bytes: &x, count: 2)) }
hdr.append("RIFF".data(using: .ascii)!); u32(36 + rec.count); hdr.append("WAVEfmt ".data(using: .ascii)!)
u32(16); u16(1); u16(2); u32(48000); u32(48000 * 4); u16(4); u16(16)
hdr.append("data".data(using: .ascii)!); u32(rec.count)
FileManager.default.createFile(atPath: args[3], contents: hdr + rec)
print("played \(st.pos) bytes on \(outDev.name) [\(outDev.uid)], recorded \(rec.count) bytes from \(inDev.name) [\(inDev.uid)]")
