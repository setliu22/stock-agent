import StockAgentCore
import SwiftUI

struct InlineProposalReview: View {
    @Environment(AppModel.self) private var model
    @State private var tab: ProposalTab = .plan

    private enum ProposalTab: String, CaseIterable, Identifiable {
        case plan = "Plan"
        case data = "Data & calculations"
        var id: String { rawValue }
    }

    var body: some View {
        ZStack {
            Color.black.opacity(0.52)
                .background(.ultraThinMaterial.opacity(0.28))
                .ignoresSafeArea()
                .onTapGesture { model.proposal = nil }

            if let proposal = model.proposal {
                GlassEffectContainer(spacing: 14) {
                    VStack(spacing: 0) {
                        proposalHeader(proposal)
                        Divider().overlay(StockTheme.border.opacity(0.7))
                        proposalTabs
                        Divider().overlay(StockTheme.border.opacity(0.55))

                        ScrollView {
                            Group {
                                switch tab {
                                case .plan: planContent(proposal)
                                case .data: dataContent(proposal)
                                }
                            }
                            .padding(.horizontal, 28)
                            .padding(.vertical, 22)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .scrollIndicators(.automatic)

                        Divider().overlay(StockTheme.border.opacity(0.7))
                        proposalFooter(proposal)
                    }
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 32, style: .continuous))
                    .background(StockTheme.surface.opacity(0.68), in: RoundedRectangle(cornerRadius: 32, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 32, style: .continuous).stroke(Color.white.opacity(0.085), lineWidth: 0.8))
                    .shadow(color: .black.opacity(0.48), radius: 42, y: 24)
                }
                .frame(maxWidth: 900, maxHeight: 700)
                .padding(32)
                .onTapGesture {}
            }
        }
        .accessibilityAddTraits(.isModal)
    }

    private func proposalHeader(_ proposal: ResearchProposal) -> some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Review proposal")
                    .font(.system(size: 23, weight: .bold, design: .rounded))
                Text("Check the scope and sources.")
                    .font(.system(size: 12))
                    .foregroundStyle(StockTheme.muted)
            }
            Spacer()
            CapsuleLabel(text: modeName(proposal.mode))
            Button { model.proposal = nil } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .bold))
                    .frame(width: 20, height: 20)
            }
            .buttonStyle(.glass)
            .keyboardShortcut(.cancelAction)
        }
        .padding(.horizontal, 28)
        .padding(.top, 24)
        .padding(.bottom, 18)
    }

    private var proposalTabs: some View {
        HStack(spacing: 6) {
            ForEach(ProposalTab.allCases) { candidate in
                Button {
                    tab = candidate
                } label: {
                    Text(candidate.rawValue)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(tab == candidate ? StockTheme.text : StockTheme.muted)
                        .padding(.horizontal, 16)
                        .frame(height: 33)
                        .background {
                            if tab == candidate {
                                Capsule().fill(StockTheme.accent.opacity(0.14))
                            }
                        }
                }
                .buttonStyle(.plain)
            }
            Spacer()
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private func planContent(_ proposal: ResearchProposal) -> some View {
        VStack(alignment: .leading, spacing: 24) {
            ProposalSection(title: "Question", subtitle: "The exact request that will anchor the report") {
                Text(proposal.question)
                    .font(.system(size: 15, weight: .medium))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(StockTheme.background.opacity(0.34), in: RoundedRectangle(cornerRadius: 17, style: .continuous))
            }

            if let warning = proposal.warning {
                Label(warning, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12))
                    .foregroundStyle(StockTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(StockTheme.warning.opacity(0.08), in: RoundedRectangle(cornerRadius: 15, style: .continuous))
            }

            switch proposal.mode {
            case .named:
                ProposalSection(title: "Companies", subtitle: "Resolved ticker symbols") {
                    FlowLayout(items: proposal.securities)
                }
            case .discovery:
                ProposalSection(title: "Search universe", subtitle: "Select up to six. The semantic matches are shown first.") {
                    VStack(spacing: 7) {
                        ForEach(orderedUniverses(proposal), id: \.self) { universe in
                            let reason = proposal.universeReasons.first(where: { $0.id == universe })?.reason
                            ChoiceRow(
                                title: universe,
                                subtitle: reason,
                                selected: proposal.universes.contains(universe),
                                locked: false
                            ) { model.toggleUniverse(universe) }
                        }
                    }
                }
            case .marketNews:
                ProposalSection(title: "Scope", subtitle: "Broad market developments") {
                    CapsuleLabel(text: "Market-wide")
                }
            }

            if proposal.mode == .discovery {
                ProposalSection(title: "Result count", subtitle: "How many companies to return") {
                    HStack(spacing: 16) {
                        Stepper("\(proposal.resultCount) companies", value: Binding(
                            get: { proposal.resultCount },
                            set: { value in model.updateResultCount(value) }
                        ), in: 1...8)
                        .font(.system(size: 13, weight: .medium))
                        Spacer()
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func dataContent(_ proposal: ResearchProposal) -> some View {
        VStack(alignment: .leading, spacing: 26) {
            ProposalSection(title: "Data sources", subtitle: "Required sources are locked; optional sources can be changed.") {
                VStack(spacing: 7) {
                    ForEach(capabilities(for: proposal)) { capability in
                        let selected = proposal.capabilityIDs.contains(capability.id)
                        ChoiceRow(
                            title: capability.label,
                            subtitle: capability.source,
                            selected: selected,
                            locked: capability.required
                        ) { model.toggleCapability(capability.id) }
                    }
                }
            }

            ProposalSection(title: "Calculations", subtitle: "Calculations only run when their source data is available.") {
                VStack(spacing: 7) {
                    ForEach(ResearchRegistry.analyses) { analysis in
                        ChoiceRow(
                            title: analysis.label,
                            subtitle: analysis.requiredCapabilities.sorted().joined(separator: " · "),
                            selected: proposal.analysisIDs.contains(analysis.id),
                            locked: false
                        ) { model.toggleAnalysis(analysis.id) }
                    }
                }
            }
        }
    }

    private func proposalFooter(_ proposal: ResearchProposal) -> some View {
        HStack(spacing: 12) {
            Spacer()
            Button("Cancel") { model.proposal = nil }
                .buttonStyle(.glass)
            Button {
                Task { await model.runProposal() }
            } label: {
                Label("Run research", systemImage: "arrow.right")
                    .font(.system(size: 13, weight: .semibold))
            }
            .buttonStyle(.glassProminent)
            .keyboardShortcut(.defaultAction)
            .disabled(proposal.mode == .discovery && proposal.universes.isEmpty)
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 18)
    }

    private func orderedUniverses(_ proposal: ResearchProposal) -> [String] {
        let selected = proposal.universes
        return selected + ResearchRegistry.universes.filter { !selected.contains($0) }
    }

    private func capabilities(for proposal: ResearchProposal) -> [ResearchCapability] {
        ResearchRegistry.capabilities.filter { $0.modes.contains(proposal.mode) }
    }

    private func modeName(_ mode: ResearchMode) -> String {
        switch mode {
        case .named: "Company research"
        case .discovery: "Trend discovery"
        case .marketNews: "Market review"
        }
    }
}

private struct ProposalSection<Content: View>: View {
    let title: String
    let subtitle: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.system(size: 14, weight: .semibold))
                Text(subtitle)
                    .font(.system(size: 11))
                    .foregroundStyle(StockTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            content
        }
    }
}

private struct ChoiceRow: View {
    let title: String
    let subtitle: String?
    let selected: Bool
    let locked: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 13) {
                VectorCheck(selected: selected, locked: locked)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 7) {
                        Text(title)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(StockTheme.text)
                        if locked {
                            Text("REQUIRED")
                                .font(.system(size: 8, weight: .bold))
                                .tracking(0.6)
                                .foregroundStyle(StockTheme.muted)
                        }
                    }
                    if let subtitle, !subtitle.isEmpty {
                        Text(subtitle)
                            .font(.system(size: 11))
                            .foregroundStyle(StockTheme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                            .multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: 8)
            }
            .padding(.horizontal, 13)
            .padding(.vertical, 11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? StockTheme.accent.opacity(0.07) : StockTheme.background.opacity(0.22), in: RoundedRectangle(cornerRadius: 15, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 15, style: .continuous).stroke(selected ? StockTheme.accent.opacity(0.22) : StockTheme.border.opacity(0.45), lineWidth: 0.7))
        }
        .buttonStyle(.plain)
        .disabled(locked)
    }
}

private struct FlowLayout: View {
    let items: [String]

    var body: some View {
        HStack(spacing: 8) {
            ForEach(items, id: \.self) { item in CapsuleLabel(text: item) }
        }
    }
}
