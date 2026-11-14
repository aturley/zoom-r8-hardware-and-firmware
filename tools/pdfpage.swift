// pdfpage.swift: render PDF pages to PNG and/or OCR them with macOS PDFKit + Vision (no installs).
// usage: swift pdfpage.swift <pdf> <outdir> <first> [last] [--scale S] [--ocr]
//   writes <outdir>/page-NNN.png (1-based page numbers) and, with --ocr, page-NNN.txt
//   (recognized lines, top to bottom, each prefixed with its x,y position in points).
import Foundation
import PDFKit
import Vision
import AppKit

var args = Array(CommandLine.arguments.dropFirst())
var scale: CGFloat = 3
var ocr = false
if let i = args.firstIndex(of: "--scale") { scale = CGFloat(Double(args[i + 1])!); args.removeSubrange(i...i + 1) }
if let i = args.firstIndex(of: "--ocr") { ocr = true; args.remove(at: i) }
guard args.count >= 3, let doc = PDFDocument(url: URL(fileURLWithPath: args[0])) else {
    FileHandle.standardError.write("usage: pdfpage <pdf> <outdir> <first> [last] [--scale S] [--ocr]\n".data(using: .utf8)!)
    exit(1)
}
let out = URL(fileURLWithPath: args[1])
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
let first = Int(args[2])!, last = args.count > 3 ? Int(args[3])! : first

for n in first...min(last, doc.pageCount) {
    guard let page = doc.page(at: n - 1) else { continue }
    let box = page.bounds(for: .mediaBox)
    let w = Int(box.width * scale), h = Int(box.height * scale)
    let cs = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                              bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { continue }
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    ctx.scaleBy(x: scale, y: scale)
    page.draw(with: .mediaBox, to: ctx)
    guard let img = ctx.makeImage() else { continue }
    let name = String(format: "page-%03d", n)
    let rep = NSBitmapImageRep(cgImage: img)
    try rep.representation(using: .png, properties: [:])!.write(to: out.appendingPathComponent(name + ".png"))
    if ocr {
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
        let obs = (req.results ?? []).sorted {
            abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.008 ? $0.boundingBox.midY > $1.boundingBox.midY
                                                                   : $0.boundingBox.minX < $1.boundingBox.minX }
        var text = ""
        for o in obs {
            guard let s = o.topCandidates(1).first?.string else { continue }
            let x = Int(o.boundingBox.minX * box.width), y = Int((1 - o.boundingBox.maxY) * box.height)
            text += "\(x),\(y)\t\(s)\n"
        }
        try text.write(to: out.appendingPathComponent(name + ".txt"), atomically: true, encoding: .utf8)
    }
    print(name)
}
