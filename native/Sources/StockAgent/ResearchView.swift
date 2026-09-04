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
                    ZStack(alignment: .topLeading) {
                        if question.isEmpty {
                            Text("What do you want to research?")
                                .font(.system(size: 17, weight: .medium))
                                .foregroundStyle(StockTheme.muted.opacity(0.76))
                                .padding(.horizontal, 22)
                                .padding(.top, 20)
                                .allowsHitTesting(false)
                        }
                        TextEditor(text: $question)
                            .font(.system(size: 17, weight: .regular))
                            .foregroundStyle(StockTheme.text)
                            .scrollContentBackground(.hidden)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 12)
                            .focused(focused)
                            .frame(minHeight: 116, maxHeight: 176)
                    }

                    HStack(spacing: 12) {
                        Text("⌘↩")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(StockTheme.muted.opacity(0.75))
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
                        .help("Review the companies, scope, and sources before running research")
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
            CapsuleLabel(text: "Source review")
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
                    Text("Generated \(report.generatedAt.formatted(date: .abbreviated, time: .shortened))")
                        .font(.system(size: 11))
                        .foregroundStyle(StockTheme.muted)
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
                            CapsuleLabel(text: company.exposure.rawValue, color: statusColor)
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
                        .buttonStyle(.glass)
                    }
                }

                Text(company.thesis)
                    .font(.system(size: 14))
                    .foregroundStyle(StockTheme.text.opacity(0.92))
                    .fixedSize(horizontal: false, vertical: true)

                if let snapshot = company.snapshot, !snapshot.facts.isEmpty {
                    ScrollView(.horizontal) {
                        HStack(spacing: 24) {
                            ForEach(snapshot.facts.prefix(5), id: \.label) { fact in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(fact.label.uppercased())
                                        .font(.system(size: 9, weight: .bold))
                                        .tracking(0.7)
                                        .foregroundStyle(StockTheme.muted)
                                    Text(formatFact(fact))
                                        .font(.system(size: 14, weight: .semibold, design: .rounded))
                                }
                            }
                        }
                    }
                    .scrollIndicators(.never)
                }

                if !company.evidence.isEmpty {
                    Button {
                        withAnimation(.snappy) { evidenceExpanded.toggle() }
                    } label: {
                        HStack {
                            Label("Source excerpts", systemImage: "text.quote")
                            Spacer()
                            Image(systemName: evidenceExpanded ? "chevron.up" : "chevron.down")
                        }
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(StockTheme.muted)
                    }
                    .buttonStyle(.plain)
                    if evidenceExpanded {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(Array(company.evidence.enumerated()), id: \.offset) { index, excerpt in
                                Text("\(index + 1). \(excerpt)")
                                    .font(.system(size: 11))
                                    .foregroundStyle(StockTheme.muted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .transition(.opacity)
                    }
                }
            }
        }
    }

    private func formatFact(_ fact: FinancialFact) -> String {
        let value = fact.value
        if fact.unit.uppercased() == "USD" {
            return value.formatted(.currency(code: "USD").notation(.compactName).precision(.fractionLength(0...1)))
        }
        return value.formatted(.number.notation(.compactName).precision(.fractionLength(0...1))) + " " + fact.unit
    }

}
