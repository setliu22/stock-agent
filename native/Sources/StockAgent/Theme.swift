import SwiftUI

enum StockTheme {
    static let background = Color(red: 25 / 255, green: 26 / 255, blue: 32 / 255)
    static let sidebar = Color(red: 32 / 255, green: 33 / 255, blue: 41 / 255)
    static let surface = Color(red: 35 / 255, green: 36 / 255, blue: 43 / 255)
    static let surfaceRaised = Color(red: 48 / 255, green: 49 / 255, blue: 58 / 255)
    static let border = Color(red: 56 / 255, green: 58 / 255, blue: 68 / 255)
    static let text = Color(red: 244 / 255, green: 244 / 255, blue: 246 / 255)
    static let muted = Color(red: 165 / 255, green: 166 / 255, blue: 174 / 255)
    static let accent = Color(red: 130 / 255, green: 145 / 255, blue: 255 / 255)
    static let accentBright = Color(red: 155 / 255, green: 167 / 255, blue: 255 / 255)
    static let positive = Color(red: 76 / 255, green: 208 / 255, blue: 160 / 255)
    static let negative = Color(red: 255 / 255, green: 112 / 255, blue: 126 / 255)
    static let warning = Color(red: 247 / 255, green: 190 / 255, blue: 83 / 255)
}

struct GlassPanel<Content: View>: View {
    var cornerRadius: CGFloat = 26
    var padding: CGFloat = 22
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(padding)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .background(
                LinearGradient(
                    colors: [StockTheme.surfaceRaised.opacity(0.42), StockTheme.surface.opacity(0.24)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(
                        LinearGradient(
                            colors: [Color.white.opacity(0.10), StockTheme.border.opacity(0.45)],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ),
                        lineWidth: 0.8
                    )
            )
            .shadow(color: .black.opacity(0.24), radius: 22, y: 12)
    }
}

struct PageHeader: View {
    let eyebrow: String
    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            if !eyebrow.isEmpty {
                Text(eyebrow.uppercased())
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .tracking(1.3)
                    .foregroundStyle(StockTheme.accentBright)
            }
            Text(title)
                .font(.system(size: 34, weight: .bold, design: .rounded))
                .foregroundStyle(StockTheme.text)
            if !subtitle.isEmpty {
                Text(subtitle)
                    .font(.system(size: 15))
                    .foregroundStyle(StockTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct VectorCheck: View {
    let selected: Bool
    let locked: Bool

    var body: some View {
        Image(systemName: selected ? "checkmark.circle.fill" : "circle")
            .font(.system(size: 21, weight: .semibold))
            .symbolRenderingMode(.palette)
            .foregroundStyle(selected ? Color.white : StockTheme.muted, selected ? StockTheme.accent : StockTheme.surfaceRaised)
            .opacity(locked ? 0.62 : 1)
            .accessibilityLabel(selected ? "Selected" : "Not selected")
    }
}

struct CapsuleLabel: View {
    let text: String
    var color: Color = StockTheme.accent

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold, design: .rounded))
            .foregroundStyle(color)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(color.opacity(0.12), in: Capsule())
            .overlay(Capsule().stroke(color.opacity(0.22), lineWidth: 0.7))
    }
}

extension View {
    func stockTextField() -> some View {
        self
            .textFieldStyle(.plain)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(StockTheme.surfaceRaised.opacity(0.56), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 13, style: .continuous).stroke(StockTheme.border.opacity(0.7), lineWidth: 0.7))
    }
}
