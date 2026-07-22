#!/usr/bin/env swift
// Сохраняет изображение из clipboard в файл.
// Usage: clipboard_img <output_path>
// Exit 0 = saved, exit 1 = no image in clipboard

import AppKit
import Foundation

guard let args = CommandLine.arguments.dropFirst().first else {
    exit(1)
}

let pb = NSPasteboard.general

// Проверяем PNG
if let data = pb.data(forType: .png) {
    let url = URL(fileURLWithPath: args)
    try? data.write(to: url)
    exit(0)
}

// Проверяем TIFF → конвертим в PNG
if let tiffData = pb.data(forType: .tiff),
   let image = NSImage(data: tiffData),
   let tiffRep = image.tiffRepresentation,
   let bitmap = NSBitmapImageRep(data: tiffRep),
   let pngData = bitmap.representation(using: .png, properties: [:]) {
    let url = URL(fileURLWithPath: args)
    try? pngData.write(to: url)
    exit(0)
}

exit(1)