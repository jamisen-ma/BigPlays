// Local OCR for timestamp alignment. No frames leave this machine.
import Foundation
import Vision
import ImageIO

guard CommandLine.arguments.count == 2,
      let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(2) }
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
try VNImageRequestHandler(cgImage: image).perform([request])
let rows: [[String: Any]] = (request.results ?? []).compactMap { item in
    guard let text = item.topCandidates(1).first else { return nil }
    return ["text": text.string, "confidence": text.confidence,
            "x": item.boundingBox.minX, "y": item.boundingBox.minY,
            "width": item.boundingBox.width, "height": item.boundingBox.height]
}
let data = try JSONSerialization.data(withJSONObject: rows)
FileHandle.standardOutput.write(data)
