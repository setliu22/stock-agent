import StockAgentCore
import SwiftUI

struct MarketView: View {
    @Environment(AppModel.self) private var model
    @State private var expandedSignal: String?

    init() {
        let arguments = CommandLine.arguments
        if let index = arguments.firstIndex(of: "--expand-signal"), arguments.indices.contains(index + 1) {
            _expandedSignal = State(initialValue: arguments[index + 1])
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                HStack(alignment: .bottom) {
                    PageHeader(
                        eyebrow: "",
                        title: "Market",
                        subtitle: ""
                    )
                    Spacer()
                    Button {
                        Task { await model.loadMarket() }
                    } label: {
                        if model.isLoadingMarket {
                            ProgressView().controlSize(.small)
                        } else {
                            Label("Refresh", systemImage: "arrow.clockwise")
                        }
                    }
                    .buttonStyle(.glass)
                    .disabled(model.isLoadingMarket)
                    .help("Refresh all five macro signals from FRED")
                }

                if let regime = model.marketRegime {
                    regimeHero(regime)
                    indicatorList(regime)
                } else {
                    GlassPanel {
                        HStack(spacing: 13) {
                            ProgressView().controlSize(.small)
                            Text("Loading public market indicators…")
                                .font(.system(size: 13))
                                .foregroundStyle(StockTheme.muted)
                        }
                    }
                }

                Label("Source: Federal Reserve Economic Data (FRED). Values can be delayed or revised.", systemImage: "building.columns")
                    .font(.system(size: 10))
                    .foregroundStyle(StockTheme.muted)
                    .padding(.leading, 5)
            }
            .padding(.horizontal, 38)
            .padding(.top, 32)
            .padding(.bottom, 46)
            .frame(maxWidth: 1100, alignment: .leading)
        }
        .scrollIndicators(.never)
    }

    private func regimeHero(_ regime: MarketRegime) -> some View {
        GlassPanel(cornerRadius: 30, padding: 26) {
            HStack(spacing: 22) {
                ZStack {
                    Circle()
                        .fill(stanceColor(regime.stance).opacity(0.14))
                        .frame(width: 76, height: 76)
                    Image(systemName: stanceIcon(regime.stance))
                        .font(.system(size: 28, weight: .medium))
                        .foregroundStyle(stanceColor(regime.stance))
                }
                VStack(alignment: .leading, spacing: 7) {
                    Text("CURRENT STANCE")
                        .font(.system(size: 9, weight: .bold, design: .rounded))
                        .tracking(1.0)
                        .foregroundStyle(StockTheme.muted)
                    Text(regime.label)
                        .font(.system(size: 27, weight: .bold, design: .rounded))
                    Text(regime.summary)
                        .font(.system(size: 13))
                        .foregroundStyle(StockTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
        }
    }

    private func indicatorList(_ regime: MarketRegime) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Signals")
                .font(.system(size: 19, weight: .bold, design: .rounded))

            GlassPanel(cornerRadius: 25, padding: 0) {
                VStack(spacing: 0) {
                    ForEach(Array(regime.indicators.enumerated()), id: \.element.id) { index, indicator in
                        if index > 0 { Divider().overlay(StockTheme.border.opacity(0.42)).padding(.horizontal, 18) }
                        Button {
                            withAnimation(.snappy(duration: 0.25)) {
                                expandedSignal = expandedSignal == indicator.id ? nil : indicator.id
                            }
                        } label: {
                            HStack(spacing: 16) {
                                Circle()
                                    .fill(stanceColor(indicator.tilt).opacity(0.16))
                                    .frame(width: 34, height: 34)
                                    .overlay(
                                        Circle()
                                            .fill(stanceColor(indicator.tilt))
                                            .frame(width: 8, height: 8)
                                    )
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(indicator.label)
                                        .font(.system(size: 13, weight: .semibold))
                                    Text(indicator.asOf?.formatted(date: .abbreviated, time: .omitted) ?? "Unavailable")
                                        .font(.system(size: 10))
                                        .foregroundStyle(StockTheme.muted)
                                }
                                Spacer()
                                VStack(alignment: .trailing, spacing: 3) {
                                    Text(indicator.latest.map { format($0, unit: indicator.unit) } ?? "—")
                                        .font(.system(size: 15, weight: .semibold, design: .rounded))
                                    Text(indicator.changeDescription)
                                        .font(.system(size: 10))
                                        .foregroundStyle(StockTheme.muted)
                                }
                                CapsuleLabel(text: indicator.tilt.rawValue, color: stanceColor(indicator.tilt))
                                    .frame(width: 112, alignment: .trailing)
                                Image(systemName: expandedSignal == indicator.id ? "chevron.up.circle.fill" : "info.circle")
                                    .font(.system(size: 16, weight: .medium))
                                    .foregroundStyle(StockTheme.accentBright)
                                    .frame(width: 23)
                            }
                            .padding(.horizontal, 20)
                            .frame(minHeight: 69)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .help("Show the explanation for \(indicator.label)")

                        if expandedSignal == indicator.id,
                           let reference = MacroReferences.bySeriesID[indicator.id] {
                            MacroExplanation(indicator: indicator, reference: reference)
                                .transition(.opacity)
                        }
                    }
                }
            }
        }
    }

    private func format(_ value: Double, unit: String) -> String {
        if unit == "$T" {
            return "$" + (value / 1_000_000).formatted(.number.precision(.fractionLength(2))) + "T"
        }
        if unit == "%" { return value.formatted(.number.precision(.fractionLength(2))) + "%" }
        if unit == "% YoY" { return value.formatted(.number.precision(.fractionLength(1))) + "% YoY" }
        return value.formatted(.number.precision(.fractionLength(2)))
    }

    private func stanceColor(_ tilt: MarketIndicator.Tilt) -> Color {
        switch tilt {
        case .defensive: StockTheme.warning
        case .neutral: StockTheme.accentBright
        case .tolerant: StockTheme.positive
        case .unavailable: StockTheme.muted
        }
    }

    private func stanceIcon(_ tilt: MarketIndicator.Tilt) -> String {
        switch tilt {
        case .defensive: "shield.lefthalf.filled"
        case .neutral: "equal.circle"
        case .tolerant: "wind"
        case .unavailable: "questionmark.circle"
        }
    }
}

private struct MacroExplanation: View {
    let indicator: MarketIndicator
    let reference: MacroReference

    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack(alignment: .top, spacing: 24) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("WHEN IT MATTERS")
                        .font(.system(size: 9, weight: .bold, design: .rounded))
                        .tracking(0.8)
                        .foregroundStyle(StockTheme.muted)
                    Text(reference.condition)
                        .font(.system(size: 12, weight: .semibold))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("COMPANIES TO EMPHASIZE")
                        .font(.system(size: 9, weight: .bold, design: .rounded))
                        .tracking(0.8)
                        .foregroundStyle(StockTheme.muted)
                    Text(reference.companyProfile)
                        .font(.system(size: 12, weight: .semibold))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Text(reference.explanation)
                .font(.system(size: 12))
                .foregroundStyle(StockTheme.text.opacity(0.86))
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            Text(indicator.source)
                .font(.system(size: 9, weight: .medium))
                .foregroundStyle(StockTheme.muted)
        }
        .padding(.leading, 70)
        .padding(.trailing, 24)
        .padding(.bottom, 20)
    }
}
