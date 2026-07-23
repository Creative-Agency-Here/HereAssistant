#!/usr/bin/env swift
import Foundation
import Speech
import AVFoundation

let duration = CommandLine.arguments.count > 1 ? (Int(CommandLine.arguments[1]) ?? 30) : 30

func err(_ msg: String) {
    if let d = "[voice_rt] \(msg)\n".data(using: .utf8) { FileHandle.standardError.write(d) }
}
func out(_ json: String) {
    if let d = (json + "\n").data(using: .utf8) { FileHandle.standardOutput.write(d) }
}

guard let rec = SFSpeechRecognizer(locale: Locale(identifier: "ru-RU")) else { exit(1) }

let engine = AVAudioEngine()
let req = SFSpeechAudioBufferRecognitionRequest()
req.shouldReportPartialResults = true
req.requiresOnDeviceRecognition = false

let node = engine.inputNode
let format = node.outputFormat(forBus: 0)
node.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
    req.append(buffer)
}

do {
    engine.prepare()
    try engine.start()
} catch {
    err("\(error.localizedDescription)")
    exit(1)
}

let task = rec.recognitionTask(with: req) { result, error in
    if let r = result {
        let t = r.bestTranscription.formattedString
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: " ")
        out("{\"text\":\"\(t)\",\"final\":\(r.isFinal)}")
    }
}

DispatchQueue.main.asyncAfter(deadline: .now() + .seconds(duration)) {
    engine.stop(); node.removeTap(onBus: 0); req.endAudio(); task.cancel()
    exit(0)
}
signal(SIGINT) { _ in exit(0) }

while true {
    RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.5))
}