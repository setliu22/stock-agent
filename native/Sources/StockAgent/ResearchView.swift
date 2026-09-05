import StockAgentCore
import SwiftUI

struct ResearchView: View {
    @Environment(AppModel.self) private var model
    @FocusState private var composerFocused: Bool

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                PageHeader(
                    eyebrow: "",
                    title: "Research",
                    subtitle: "Research a ticker or investment theme."
                )

                ResearchComposer(question: $model.researchQuestion, focused: $composerFocused)

                if model.isResearching {
                    ResearchProgress(message: model.researchProgress)
                        .transition(.opacity.combined(with: .scale(scale: 0.985)))
                }

                if let report = model.report {
                    ResearchReportView(report: report)
                        .transition(.opacity)
                }
            }
            .padding(.horizontal, 38)
            .padding(.top, 32)
            .padding(.bottom, 46)
            .frame(maxWidth: 1120, alignment: .leading)
        }
        .scrollIndicators(.never)
        .defaultScrollAnchor(.top)
        .onReceive(NotificationCenter.default.publisher(for: .focusResearchComposer)) { _ in
            composerFocused = true
        }
    }

}

private struct ResearchComposer: View {
    @Environment(AppModel.self) private var model
    @Binding var question: String
    var focused: FocusState<Bool>.Binding

    var body: some View {
        GlassEffectContainer(spacing: 12) {
            GlassPanel(cornerRadius: 30, padding: 0) {
                VStack(spacing: 0) {
                    TextField(
                        "Enter a ticker question or an investment theme",
                        text: $question,
                        axis: .vertical
                    )
                    .textFieldStyle(.plain)
                    .font(.system(size: 17, weight: .regular))
                    .foregroundStyle(StockTheme.text)
                    .lineLimit(2...6)
                    .focused(focused)
                    .padding(.horizontal, 22)
                    .padding(.top, 20)
                    .padding(.bottom, 14)
                    .frame(minHeight: 92, alignment: .topLeading)

                    HStack(spacing: 12) {
                        Spacer()
                        Button {
                            Task { await model.createProposal() }
                        } label: {
                            HStack(spacing: 8) {
                                if model.isPlanning {
                                    ProgressView().controlSize(.small)
                                } else {
                                    Image(systemName: "doc.text.magnifyingglass")
                                }
                                Text(model.isPlanning ? "Building proposal…" : "Review proposal")
                            }
                            .font(.system(size: 13, weight: .semibold))
                            .padding(.horizontal, 4)
                        }
                        .buttonStyle(.glassProminent)
                        .help("Review the scope before running research")
                        .disabled(question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isPlanning)
                        .keyboardShortcut(.return, modifiers: .command)
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 14)
                }
            }
        }
    }
}

private struct ResearchProgress: View {
    let message: String

    var body: some View {
        HStack(spacing: 14) {
            ProgressView()
                .controlSize(.small)
                .tint(StockTheme.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text("Research in progress")
                    .font(.system(size: 13, weight: .semibold))
                Text(message)
                    .font(.system(size: 12))
                    .foregroundStyle(StockTheme.muted)
            }
            Spacer()
        }
        .padding(.horizontal, 18)
        .frame(height: 66)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 21, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 21, style: .continuous).stroke(StockTheme.border.opacity(0.5), lineWidth: 0.7))
    }
}

private struct ResearchReportView: View {
    let report: ResearchReport

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(report.title)
                        .font(.system(size: 25, weight: .bold, design: .rounded))
                    Text(report.question)
                        .font(.system(size: 12))
                        .foregroundStyle(StockTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                if !sourceLabel.isEmpty { CapsuleLabel(text: sourceLabel) }
            }

            if report.companies.isEmpty {
                GlassPanel {
                    Text(report.notes.first ?? "No companies were returned.")
                        .font(.system(size: 14))
                        .foregroundStyle(StockTheme.muted)
                }
            } else {
                LazyVStack(spacing: 13) {
                    ForEach(report.companies) { company in
                        CompanyResultView(company: company)
                    }
                }
            }

            if !report.notes.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(report.notes, id: \.self) { note in
                        Label(note, systemImage: "info.circle")
                            .font(.system(size: 11))
                            .foregroundStyle(StockTheme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.horizontal, 5)
            }
        }
    }

    private var sourceLabel: String {
        let sources = Set(report.companies.flatMap(\.sources))
        if sources.contains("LSEG Workspace") && sources.contains("SEC EDGAR") { return "LSEG + SEC" }
        if sources.contains("LSEG Workspace") { return "LSEG" }
        if sources.contains("SEC EDGAR") { return "SEC" }
        return ""
    }
}

private struct CompanyResultView: View {
    let company: ResearchCompanyResult
    @State private var evidenceExpanded = false

    private var statusColor: Color {
        switch company.exposure {
        case .direct: StockTheme.positive
        case .enabling: StockTheme.accentBright
        case .adjacent: StockTheme.warning
        case .incidental: StockTheme.negative
        case .unreviewed: StockTheme.muted
        case .profile: StockTheme.accentBright
        }
    }

    var body: some View {
        GlassPanel(cornerRadius: 24, padding: 20) {
            VStack(alignment: .leading, spacing: 15) {
                HStack(alignment: .top, spacing: 15) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(StockTheme.accent.opacity(0.14))
                            .frame(width: 48, height: 48)
                        Text(String(company.candidate.ticker.prefix(2)))
                            .font(.system(size: 13, weight: .bold, design: .rounded))
                            .foregroundStyle(StockTheme.accentBright)
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 9) {
                            Text(company.candidate.ticker)
                                .font(.system(size: 17, weight: .bold, design: .rounded))
                            if company.exposure != .profile {
                                CapsuleLabel(text: company.exposure.rawValue, color: statusColor)
                            }
                        }
                        Text(company.candidate.name)
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(StockTheme.muted)
                    }
                    Spacer()
                    if let url = company.candidate.filingURL {
                        Link(destination: url) {
                            Label("Filing", systemImage: "arrow.up.right")
                                .font(.system(size: 11, weight: .semibold))
                        }
                        .stockSecondaryButton()
                    }
                }

                VStack(alignment: .leading, spacing: 7) {
                    Text(company.exposure == .profile ? "ANSWER" : "WHY IT SURFACED")
                        .font(.system(size: 9, weight: .bold, design: .rounded))
                        .tracking(0.9)
                        .foregroundStyle(StockTheme.muted)
                    Text(company.thesis)
                        .font(.system(size: 14))
                        .foregroundStyle(StockTheme.text.opacity(0.92))
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }

                if let investmentCase = company.investmentCase {
                    Divider().overlay(StockTheme.border.opacity(0.5))
                    InvestmentCaseView(investmentCase: investmentCase)
                }

                if let snapshot = company.snapshot, !snapshot.facts.isEmpty {
                    KeyMetricsView(facts: Array(snapshot.facts.prefix(8)))
                }

                if !company.evidence.isEmpty {
                    Button {
                        withAnimation(.snappy) { evidenceExpanded.toggle() }
                    } label: {
                        HStack {
                            Label("Source excerpts (\(company.evidence.count))", systemImage: "text.quote")
                            Spacer()
                            Image(systemName: evidenceExpanded ? "chevron.up" : "chevron.down")
                        }
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(StockTheme.muted)
                    }
                    .buttonStyle(.plain)
                    if evidenceExpanded {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(Array(company.evidence.enumerated()), id: \.offset) { _, excerpt in
                                SourceExcerptRow(excerpt: excerpt)
                            }
                        }
                        .transition(.opacity)
                    }
                }
            }
        }
    }

}

private struct InvestmentCaseView: View {
    let investmentCase: InvestmentCase

    private var stanceColor: Color {
        switch investmentCase.stance {
        case .constructive: StockTheme.positive
        case .mixed: StockTheme.accentBright
        case .cautious: StockTheme.warning
        case .insufficient: StockTheme.muted
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("INVESTMENT CASE")
                    .font(.system(size: 9, weight: .bold, design: .rounded))
                    .tracking(0.9)
                    .foregroundStyle(StockTheme.muted)
                Spacer()
                CapsuleLabel(text: investmentCase.stance.rawValue, color: stanceColor)
            }
            Text(investmentCase.summary)
                .font(.system(size: 14, weight: .medium))
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)

            if investmentCase.reasons.isEmpty {
                ResearchPointColumn(
                    title: "What to watch",
                    icon: "eye",
                    color: StockTheme.warning,
                    points: investmentCase.watchouts
                )
            } else if investmentCase.watchouts.isEmpty {
                ResearchPointColumn(
                    title: "What supports it",
                    icon: "arrow.up.right",
                    color: StockTheme.positive,
                    points: investmentCase.reasons
                )
            } else {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 22) {
                        ResearchPointColumn(
                            title: "What supports it",
                            icon: "arrow.up.right",
                            color: StockTheme.positive,
                            points: investmentCase.reasons
                        )
                        Divider().overlay(StockTheme.border.opacity(0.55))
                        ResearchPointColumn(
                            title: "What to watch",
                            icon: "eye",
                            color: StockTheme.warning,
                            points: investmentCase.watchouts
                        )
                    }
                    VStack(alignment: .leading, spacing: 18) {
                        ResearchPointColumn(
                            title: "What supports it",
                            icon: "arrow.up.right",
                            color: StockTheme.positive,
                            points: investmentCase.reasons
                        )
                        Divider().overlay(StockTheme.border.opacity(0.55))
                        ResearchPointColumn(
                            title: "What to watch",
                            icon: "eye",
                            color: StockTheme.warning,
                            points: investmentCase.watchouts
                        )
                    }
                }
            }
        }
        .padding(.top, 2)
    }
}

private struct ResearchPointColumn: View {
    let title: String
    let icon: String
    let color: Color
    let points: [InvestmentCasePoint]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: icon)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(color)
            ForEach(points) { point in
                HStack(alignment: .top, spacing: 9) {
                    Circle()
                        .fill(color.opacity(0.9))
                        .frame(width: 5, height: 5)
                        .padding(.top, 6)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(point.text)
                            .font(.system(size: 12, weight: .medium))
                            .fixedSize(horizontal: false, vertical: true)
                        ForEach(point.evidence) { item in
                            Text(item.detail)
                                .font(.system(size: 10))
                                .foregroundStyle(StockTheme.muted)
                                .lineLimit(3)
                                .fixedSize(horizontal: false, vertical: true)
                                .help("\(item.source): \(item.detail)")
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct SourceExcerptRow: View {
    let excerpt: String

    private var source: String {
        if excerpt.hasPrefix("LSEG business description:") { return "LSEG" }
        if excerpt.hasPrefix("SEC filing excerpt:") { return "SEC" }
        return "Source"
    }

    private var content: String {
        excerpt
            .replacingOccurrences(of: "LSEG business description:", with: "")
            .replacingOccurrences(of: "SEC filing excerpt:", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Text(source)
                .font(.system(size: 8, weight: .bold, design: .rounded))
                .tracking(0.5)
                .foregroundStyle(StockTheme.accentBright)
                .padding(.horizontal, 7)
                .padding(.vertical, 4)
                .background(StockTheme.accent.opacity(0.1), in: Capsule())
            Text(content)
                .font(.system(size: 11))
                .foregroundStyle(StockTheme.muted)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
        .padding(12)
        .background(StockTheme.background.opacity(0.25), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

private struct KeyMetricsView: View {
    let facts: [FinancialFact]

    private let columns = [GridItem(.adaptive(minimum: 125), spacing: 18, alignment: .leading)]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("KEY METRICS")
                .font(.system(size: 9, weight: .bold, design: .rounded))
                .tracking(0.9)
                .foregroundStyle(StockTheme.muted)
            LazyVGrid(columns: columns, alignment: .leading, spacing: 13) {
                ForEach(facts, id: \.label) { fact in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(fact.label)
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(StockTheme.muted)
                            .lineLimit(1)
                        Text(format(fact))
                            .font(.system(size: 14, weight: .semibold, design: .rounded))
                            .lineLimit(1)
                    }
                    .help(fact.source)
                }
            }
        }
        .padding(.vertical, 13)
        .overlay(alignment: .top) {
            Divider().overlay(StockTheme.border.opacity(0.5))
        }
        .overlay(alignment: .bottom) {
            Divider().overlay(StockTheme.border.opacity(0.5))
        }
    }

    private func format(_ fact: FinancialFact) -> String {
        if fact.unit.uppercased() == "USD" {
            return fact.value.formatted(
                .currency(code: "USD").notation(.compactName).precision(.fractionLength(0...1))
            )
        }
        return fact.value.formatted(.number.notation(.compactName).precision(.fractionLength(0...1))) + " " + fact.unit
    }
}
