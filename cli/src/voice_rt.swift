#!/usr/bin/env swift
// Real-time speech recognition via macOS Speech framework.
// Usage: voice_rt [seconds]
// Outputs JSON lines to stdout: {"text":"...","final":true/false}
// Errors to stderr.

import Foundation
import Speech
import AVFoundation

let duration = CommandLine.arguments.count > 1 ? (Int(CommandLine.arguments[1]) ?? 30) : 30

let sem = DispatchSemaphore(value: 0)
var authOk = false
SFSpeechRecognizer.requestAuthorization { status in
    authOk = (status == .authorized)
    sem.signal()
}
_ = sem.wait(timeout: .now() + 3)

guard authOk else {
    FileHandle.standardError.write("ERR: Speech not authorized. System Settings → Privacy → Speech Recognition\n".data(using: .utf8)!)
    exit(1)
}

guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "ru-RU")) else {
    FileHandle.standardError.write("ERR: No recognizer for ru-RU\n".data(using: .utf8)!)
    exit(1)
}

let audioEngine = AVAudioEngine()
let request = SFSpeechAudioBufferRecognitionRequest()
request.shouldReportPartialResults = true
request.requiresOnDeviceRecognition = true

func startRecording() {
    let node = audioEngine.inputNode
    let format = node.outputFormat(forBus: 0)
    node.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
        request.append(buffer)
    }
    do {
        audioEngine.prepare()
        try audioEngine.start()
    } catch {
        FileHandle.standardError.write("ERR: Audio engine: \(error.localizedDescription). Check Privacy → Microphone\n".data(using: .utf8)!)
        exit(1)
    }
}

startRecording()

let task = recognizer.recognitionTask(with: request) { result, error in
    if let result = result {
        let text = result.bestTranscription.formattedString
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: " ")
        let isFinal = result.isFinal
        let json = "{\"text\":\"\(text)\",\"final\":\(isFinal)}"
        if let data = (json + "\n").data(using: .utf8) {
            FileHandle.standardOutput.write(data)
        }
    }
    if error != nil && audioEngine.isRunning {
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        startRecording()
    }
}

DispatchQueue.main.asyncAfter(deadline: .now() + .seconds(duration)) {
    audioEngine.stop()
    audioEngine.inputNode.removeTap(onBus: 0)
    request.endAudio()
    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { exit(0) }
}

signal(SIGINT) { _ in
    audioEngine.stop()
    audioEngine.inputNode.removeTap(onBus: 0)
    request.endAudio()
    exit(0)
}

RunLoop.main.run()