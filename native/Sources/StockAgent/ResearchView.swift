import StockAgentCore
import SwiftUI

struct ResearchView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @FocusState private var composerFocused: Bool

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                PageHeader(
                    eyebrow: "",
                    title: "Research",
                    subtitle: ""
                )

                ResearchConnections()
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
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await model.checkConnections() } }
        }
    }

}

private struct ResearchConnections: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 18) {
            Label {
                Text("LSEG · \(lsegStatus)")
            } icon: {
                Circle().fill(model.lsegConnected == true ? StockTheme.positive : StockTheme.muted)
                    .frame(width: 7, height: 7)
            }
            .help("Company fundamentals and industry screens. Open Workspace and sign in to connect; SEC filings remain available without it.")
            Label {
                Text("Apple Intelligence · \(model.isCheckingConnections ? "Checking…" : model.appleIntelligenceAvailable ? "Available" : "Unavailable")")
            } icon: {
                Image(systemName: "sparkles")
                    .foregroundStyle(model.appleIntelligenceAvailable ? StockTheme.accentBright : StockTheme.muted)
            }
            .help("On-device theme matching and source-based answers. Availability depends on this Mac's Apple Intelligence settings and model download.")
            Spacer(minLength: 0)
            Button {
                Task { await model.checkConnections() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.plain)
            .disabled(model.isCheckingConnections)
            .help("Check research connections")
            .accessibilityLabel("Check research connections")
        }
        .font(.system(size: 11, weight: .medium))
        .foregroundStyle(StockTheme.muted)
    }

    private var lsegStatus: String {
        if model.isCheckingConnections { return "Checking" }
        return model.lsegConnected == true ? "Activated" : "Unavailable"
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
                    HStack(spacing: 8) {
                        capability(.named, title: "Company Research", detail: "Business, filings and financials", icon: "building.2")
                        capability(.discovery, title: "Trend Discovery", detail: "Companies connected to a trend", icon: "sparkle.magnifyingglass")
                    }
                    .padding(12)
                    Divider().overlay(StockTheme.border.opacity(0.35)).padding(.horizontal, 22)
                    TextField(
                        placeholder,
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
                        if model.isPlanning {
                            Button("Cancel") { model.cancelResearch() }
                                .stockSecondaryButton()
                        }
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
                        .disabled(question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isPlanning || model.isResearching)
                        .keyboardShortcut(.return, modifiers: .command)
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 14)
                }
            }
        }
    }

    private var placeholder: String {
        switch model.researchMode {
        case .named: "Enter a ticker and what you want to know"
        case .discovery: "Describe the business trend you want to explore"
        case nil: "Enter a ticker question or an investment theme"
        }
    }

    private func capability(_ mode: ResearchMode, title: String, detail: String, icon: String) -> some View {
        Button {
            model.researchMode = model.researchMode == mode ? nil : mode
            focused.wrappedValue = true
        } label: {
            HStack(spacing: 10) {
                Image(systemName: icon)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(StockTheme.accentBright)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(StockTheme.text)
                    Text(detail).font(.system(size: 11))
                        .foregroundStyle(StockTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(model.researchMode == mode ? StockTheme.accent.opacity(0.13) : .clear,
                        in: RoundedRectangle(cornerRadius: 18))
            .contentShape(RoundedRectangle(cornerRadius: 18))
        }
        .buttonStyle(.plain)
        .disabled(model.isPlanning || model.isResearching)
        .accessibilityAddTraits(model.researchMode == mode ? .isSelected : [])
        .help("Use \(title). Click again to let the question choose the workflow.")
    }
}

private struct ResearchProgress: View {
    @Environment(AppModel.self) private var model
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
            Button("Cancel") { model.cancelResearch() }
                .stockSecondaryButton()
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
                    Text(report.notes.isEmpty ? "No companies were returned." : report.notes.joined(separator: "\n\n"))
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

            if !report.notes.isEmpty && !report.companies.isEmpty {
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
                    DisclosureGroup("Financials") {
                        KeyMetricsView(facts: snapshot.facts)
                    }
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(StockTheme.muted)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("INVESTMENT EVIDENCE")
                    .font(.system(size: 9, weight: .bold, design: .rounded))
                    .tracking(0.9)
                    .foregroundStyle(StockTheme.muted)
                Spacer()
            }
            Text(investmentCase.summary)
                .font(.system(size: 14, weight: .medium))
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)

            DisclosureGroup("Financial evidence") {
                financialEvidence
            }
            .font(.system(size: 11, weight: .medium))
        }
        .padding(.top, 2)
    }

    @ViewBuilder
    private var financialEvidence: some View {
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
            LazyVGrid(columns: columns, alignment: .leading, spacing: 13) {
                ForEach(facts, id: \.label) { fact in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(fact.label)
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(StockTheme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(format(fact))
                            .font(.system(size: 14, weight: .semibold, design: .rounded))
                            .lineLimit(1)
                            .foregroundStyle(StockTheme.text)
                        if let context = metricMeaning(fact) {
                            Text(context)
                                .font(.system(size: 10))
                                .foregroundStyle(StockTheme.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let end = fact.periodEnd {
                            Text((fact.periodStart.map { "\($0.formatted(.dateTime.month(.abbreviated).day())) – " } ?? "As of ")
                                 + end.formatted(.dateTime.month(.abbreviated).day().year()))
                                .font(.system(size: 9))
                                .foregroundStyle(StockTheme.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .help(factContext(fact))
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

    private func factContext(_ fact: FinancialFact) -> String {
        var parts = [fact.source]
        if let end = fact.periodEnd {
            let period = fact.periodStart.map { "\($0.formatted(date: .abbreviated, time: .omitted)) – " } ?? "As of "
            parts.append(period + end.formatted(date: .abbreviated, time: .omitted))
        }
        if let filed = fact.filedAt { parts.append("Filed \(filed.formatted(date: .abbreviated, time: .omitted))") }
        return parts.joined(separator: "\n")
    }

    private func metricMeaning(_ fact: FinancialFact) -> String? {
        let label = fact.label.lowercased()
        if label.contains("p/e") {
            if fact.value <= 0 { return "Not a useful earnings valuation multiple when earnings are nonpositive." }
            return label.contains("forward") ? "Price relative to forecast earnings; estimates can change." : "Price per dollar of past earnings. Compare similar businesses, not the whole market."
        }
        if label.contains("ebitda") { return "Enterprise value relative to operating earnings before interest, tax and depreciation; ignores capital spending." }
        if label.contains("target") { return "Analyst estimate, not a promised return or independently assessed fair value." }
        if label.contains("revenue") { return "Sales, not profit. Growth needs a comparable earlier period." }
        if label.contains("net income") { return fact.value < 0 ? "A reported loss for this period." : "Profit after expenses and tax; cash generation can differ." }
        if label.contains("cash") { return "Liquidity available to the business; assess alongside debt and spending needs." }
        if label.contains("debt") { return "Borrowing obligations. Refinancing costs and repayment dates matter as well as the balance." }
        if label.contains("market cap") { return "Equity market value; size alone does not establish investment value." }
        if label.contains("return on equity") { return "Profit relative to book equity; high leverage can inflate this ratio." }
        if label.contains("assets") { return "Resources on the balance sheet, not an estimate of shareholder value." }
        if label.contains("liabilities") { return "Amounts owed, including obligations other than borrowing." }
        if label.contains("equity") { return "Accounting residual after liabilities; not market value." }
        return nil
    }
}
