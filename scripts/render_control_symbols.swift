import AppKit

let outputDirectory = URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? "assets/control-symbols")
try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)

struct SymbolAsset {
    let filename: String
    let symbol: String
    let color: NSColor
}

let accent = NSColor(calibratedRed: 0.51, green: 0.57, blue: 1.0, alpha: 1)
let idle = NSColor(calibratedRed: 0.66, green: 0.67, blue: 0.72, alpha: 1)
let disabled = NSColor(calibratedRed: 0.36, green: 0.37, blue: 0.41, alpha: 1)
let success = NSColor(calibratedRed: 0.19, green: 0.82, blue: 0.35, alpha: 1)
let assets = [
    SymbolAsset(filename: "checkbox-off.png", symbol: "square", color: idle),
    SymbolAsset(filename: "checkbox-on.png", symbol: "checkmark.square.fill", color: accent),
    SymbolAsset(filename: "checkbox-disabled.png", symbol: "square", color: disabled),
    SymbolAsset(filename: "checkbox-disabled-on.png", symbol: "checkmark.square.fill", color: disabled),
    SymbolAsset(filename: "radio-off.png", symbol: "circle", color: idle),
    SymbolAsset(filename: "radio-on.png", symbol: "record.circle.fill", color: accent),
    SymbolAsset(filename: "radio-disabled.png", symbol: "circle", color: disabled),
    SymbolAsset(filename: "radio-disabled-on.png", symbol: "record.circle.fill", color: disabled),
    SymbolAsset(filename: "copy.png", symbol: "doc.on.doc", color: idle),
    SymbolAsset(filename: "copied.png", symbol: "checkmark", color: success),
]

for asset in assets {
    let canvasSize = NSSize(width: 24, height: 24)
    let canvas = NSImage(size: canvasSize)
    canvas.lockFocus()
    NSGraphicsContext.current?.imageInterpolation = .high
    guard let symbol = NSImage(
        systemSymbolName: asset.symbol,
        accessibilityDescription: nil
    )?.withSymbolConfiguration(
        NSImage.SymbolConfiguration(pointSize: 18, weight: .medium)
            .applying(
                asset.filename.contains("-on")
                    ? NSImage.SymbolConfiguration(
                        paletteColors: [
                            NSColor.white.withAlphaComponent(
                                asset.filename.contains("disabled") ? 0.55 : 1
                            ),
                            asset.color,
                        ]
                    )
                    : NSImage.SymbolConfiguration(hierarchicalColor: asset.color)
            )
    ) else { fatalError("Missing SF Symbol: \(asset.symbol)") }
    symbol.draw(
        in: NSRect(x: 3, y: 3, width: 18, height: 18),
        from: .zero,
        operation: .sourceOver,
        fraction: 1
    )
    canvas.unlockFocus()
    guard
        let tiff = canvas.tiffRepresentation,
        let bitmap = NSBitmapImageRep(data: tiff),
        let png = bitmap.representation(using: .png, properties: [:])
    else { fatalError("Could not encode \(asset.filename)") }
    try png.write(to: outputDirectory.appendingPathComponent(asset.filename))
}
