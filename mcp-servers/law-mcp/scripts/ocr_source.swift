// Optional macOS collection helper. OCR is retrieval text, never a numeric rule source.
import Foundation
import Vision
import AppKit
import PDFKit
func recognize(_ image: CGImage) throws {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ko-KR", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: image).perform([request])
    for item in request.results ?? [] { if let text = item.topCandidates(1).first { print(text.string) } }
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
if url.pathExtension == "pdf", let doc = PDFDocument(url: url) {
    for n in 0..<doc.pageCount {
        guard let page = doc.page(at: n) else { continue }
        let size = page.bounds(for: .mediaBox).size
        let image = page.thumbnail(of: NSSize(width: size.width * 2, height: size.height * 2), for: .mediaBox)
        guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { fatalError("render failed") }
        print("\n[OCR page \(n+1)]")
        try recognize(cg)
    }
} else {
    guard let image = NSImage(contentsOf: url), let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { fatalError("invalid image") }
    try recognize(cg)
}
