import AppKit

let output = CommandLine.arguments.dropFirst().first ?? "assets/stock-agent-icon.png"
let size = NSSize(width: 1024, height: 1024)
guard let bitmap = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: 1024, pixelsHigh: 1024,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
    isPlanar: false, colorSpaceName: .deviceRGB,
    bytesPerRow: 0, bitsPerPixel: 0
) else { fatalError("Could not create icon canvas") }

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSColor.clear.setFill()
NSRect(origin: .zero, size: size).fill()

let tile = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824), xRadius: 186, yRadius: 186)
NSGraphicsContext.current?.saveGraphicsState()
tile.addClip()
let background = NSGradient(colorsAndLocations:
    (NSColor(calibratedRed: 0.32, green: 0.84, blue: 0.77, alpha: 1), 0.0),
    (NSColor(calibratedRed: 0.32, green: 0.49, blue: 0.92, alpha: 1), 0.50),
    (NSColor(calibratedRed: 0.24, green: 0.15, blue: 0.55, alpha: 1), 1.0)
)!
background.draw(in: tile, angle: -52)
NSColor.white.withAlphaComponent(0.09).setFill()
let sheen = NSBezierPath()
sheen.move(to: NSPoint(x: 100, y: 924))
sheen.line(to: NSPoint(x: 100, y: 485))
sheen.curve(to: NSPoint(x: 924, y: 700), controlPoint1: NSPoint(x: 280, y: 860), controlPoint2: NSPoint(x: 690, y: 918))
sheen.line(to: NSPoint(x: 924, y: 924))
sheen.close()
sheen.fill()
NSGraphicsContext.current?.restoreGraphicsState()

let lensRect = NSRect(x: 252, y: 310, width: 480, height: 480)
NSColor.white.withAlphaComponent(0.15).setFill()
NSBezierPath(ovalIn: lensRect).fill()
NSColor.white.withAlphaComponent(0.72).setStroke()
let lens = NSBezierPath(ovalIn: lensRect)
lens.lineWidth = 16
lens.stroke()
NSColor.white.withAlphaComponent(0.18).setStroke()
let inner = NSBezierPath(ovalIn: lensRect.insetBy(dx: 29, dy: 29))
inner.lineWidth = 5
inner.stroke()

func strokeLine(_ points: [NSPoint], width: CGFloat, color: NSColor) {
    let path = NSBezierPath()
    path.lineCapStyle = .round
    path.lineJoinStyle = .round
    path.lineWidth = width
    path.move(to: points[0])
    for point in points.dropFirst() { path.line(to: point) }
    color.setStroke()
    path.stroke()
}

strokeLine([NSPoint(x: 651, y: 370), NSPoint(x: 776, y: 245)], width: 72, color: .white)
strokeLine([NSPoint(x: 651, y: 370), NSPoint(x: 776, y: 245)], width: 34, color: NSColor(calibratedRed: 0.79, green: 1, blue: 0.97, alpha: 0.42))

let signal = [NSPoint(x: 320, y: 438), NSPoint(x: 421, y: 533), NSPoint(x: 501, y: 475), NSPoint(x: 652, y: 643)]
strokeLine(signal, width: 43, color: .white)
for point in signal {
    NSColor.white.setFill()
    NSBezierPath(ovalIn: NSRect(x: point.x - 24, y: point.y - 24, width: 48, height: 48)).fill()
}
strokeLine([NSPoint(x: 599, y: 643), NSPoint(x: 652, y: 645), NSPoint(x: 650, y: 591)], width: 32, color: .white)

NSGraphicsContext.restoreGraphicsState()
guard let data = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Could not encode icon")
}
try data.write(to: URL(fileURLWithPath: output))
