#!/usr/bin/env swift
// Real-time speech recognition via macOS Speech framework.
// Usage: swift voice_rt.swift [seconds]
// Outputs recognized text to stdout in real-time (line per update).
// Exits after N seconds (default 30) or on SIGINT.

import Foundation
import Speech
import AVFoundation

let duration = CommandLine.arguments.count > 1 ? (Int(CommandLine.arguments[1]) ?? 30) : 30

// Request permissions
SFSpeechRecognizer.requestAuthorization { status in
    guard status == .authorized else {
        FileHandle.standardError.write("✗ Speech recognition not authorized\n".data(using: .utf8)!)
        exit(1)
    }
}

let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "ru-RU")) ?? SFSpeechRecognizer()
guard let rec = recognizer else {
    FileHandle.standardError.write("✗ Speech recognizer not available\n".data(using: .utf8)!)
    exit(1)
}

let audioEngine = AVAudioEngine()
let request = SFSpeechAudioBufferRecognitionRequest()
request.shouldReportPartialResults = true
request.requiresOnDeviceRecognition = true  // on-device, no API needed

var finalText = ""

let task = rec.recognitionTask(with: request) { result, error in
    if let result = result {
        let text = result.bestTranscription.formattedString
        // Output partial results as JSON lines for the CLI to parse
        let isFinal = result.isFinal
        let json = "{\"text\":\"\(text.replacingOccurrences(of: "\"", with: "\\\""))\",\"final\":\(isFinal)}"
        print(json)
        fflush(stdout)
        if isFinal {
            finalText = text
        }
    }
    if error != nil || (result?.isFinal ?? false) {
        // Restart recognition for continuous mode
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
            startRecording()
        }
    }
}

func startRecording() {
    let node = audioEngine.inputNode
    let format = node.outputFormat(forBus: 0)
    node.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
        request.append(buffer)
    }
    audioEngine.prepare()
    try? audioEngine.start()
}

startRecording()

// Auto-stop after duration
DispatchQueue.main.asyncAfter(deadline: .now() + .seconds(duration)) {
    audioEngine.stop()
    audioEngine.inputNode.removeTap(onBus: 0)
    request.endAudio()
    // Give recognition task time to finish
    DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
        exit(0)
    }
}

// Handle SIGINT
signal(SIGINT) { _ in
    audioEngine.stop()
    audioEngine.inputNode.removeTap(onBus: 0)
    request.endAudio()
    exit(0)
}

RunLoop.main.run()