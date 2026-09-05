import Foundation
import FoundationModels

@Generable
private struct GeneratedInvestmentCaseRow {
    @Guide(description: "Copy the supplied company identifier exactly")
    var identifier: String

    @Guide(description: "A balanced one- or two-sentence synthesis under 70 words; do not recommend buying or selling")
    var summary: String
}

@Generable
private struct GeneratedInvestmentCaseBatch {
    @Guide(description: "One investment research case per supplied company, in order", .minimumCount(1), .maximumCount(5))
    var companies: [GeneratedInvestmentCaseRow]
}

struct InvestmentCaseInput: Sendable {
    let company: CompanyCandidate
    let evidence: [InvestmentEvidenceItem]
    let fallback: InvestmentCase
}

protocol InvestmentCaseGenerating: Sendable {
    func generate(_ inputs: [InvestmentCaseInput]) async throws -> [String: InvestmentCase]
}

struct OnDeviceInvestmentCaseGenerator: InvestmentCaseGenerating {
    func generate(_ inputs: [InvestmentCaseInput]) async throws -> [String: InvestmentCase] {
        guard SystemLanguageModel.default.isAvailable else {
            throw StockAgentError.unavailable("Apple Intelligence is unavailable for investment-case synthesis.")
        }
        var output = [String: InvestmentCase]()
        for offset in stride(from: 0, to: inputs.count, by: 5) {
            let group = Array(inputs[offset..<min(offset + 5, inputs.count)])
            let session = LanguageModelSession(
                model: .default,
                instructions: """
                Act as a source-disciplined equity research editor. Explain why each stock may or may not
                deserve further research using only the supplied evidence lines. A thematic connection is
                not by itself proof of an attractive investment. Weigh valuation, profitability, balance
                sheet, expectations, and data limitations when available. Return only a short balanced
                synthesis; the app computes the stance and displays validated evidence points separately. Never
                invent revenue exposure, growth, contracts, market share, forecasts, risks,
                recommendations, or numbers. Do not tell the user to buy or sell.
                """
            )
            let companyText = group.enumerated().map { index, input in
                let lines = input.evidence.map { item in
                    "\(item.id) | \(item.label) | \(item.detail) | Source: \(item.source)"
                }.joined(separator: "\n")
                return """
                Company \(index + 1)
                Identifier: \(input.company.id)
                Name: \(input.company.name) (\(input.company.ticker))
                Evidence:
                \(lines)
                """
            }.joined(separator: "\n\n")
            let response = try await session.respond(
                to: companyText,
                generating: GeneratedInvestmentCaseBatch.self
            )
            for (index, row) in response.content.companies.enumerated() {
                guard let input = group.first(where: { $0.company.id == row.identifier })
                        ?? (group.indices.contains(index) ? group[index] : nil) else { continue }
                output[input.company.id] = Self.validated(row, for: input)
            }
        }
        return output
    }

    private static func validated(
        _ generated: GeneratedInvestmentCaseRow,
        for input: InvestmentCaseInput
    ) -> InvestmentCase {
        let byID = Dictionary(uniqueKeysWithValues: input.evidence.map { ($0.id, $0) })
        let allEvidence = byID.values.map(\.detail).joined(separator: " ")
        let proposedSummary = generated.summary.trimmingCharacters(in: .whitespacesAndNewlines)
        let summary = !proposedSummary.isEmpty && numbers(in: proposedSummary).isSubset(of: numbers(in: allEvidence))
            ? String(proposedSummary.prefix(520))
            : input.fallback.summary
        return InvestmentCase(
            stance: input.fallback.stance,
            summary: summary,
            reasons: input.fallback.reasons,
            watchouts: input.fallback.watchouts
        )
    }

    private static func numbers(in text: String) -> Set<String> {
        let pattern = #"-?\d+(?:[,.]\d+)?%?"#
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return [] }
        let range = NSRange(text.startIndex..., in: text)
        return Set(expression.matches(in: text, range: range).compactMap { match in
            guard let range = Range(match.range, in: text) else { return nil }
            return text[range].replacingOccurrences(of: ",", with: "")
        })
    }
}

enum InvestmentCaseEvidence {
    static func makeInput(
        for result: ResearchCompanyResult,
        peerSnapshots: [CompanySnapshot]
    ) -> InvestmentCaseInput {
        var evidence = [InvestmentEvidenceItem]()
        var positive = [InvestmentCasePoint]()
        var watchouts = [InvestmentCasePoint]()

        if result.exposure != .profile {
            let theme = InvestmentEvidenceItem(
                id: "theme_fit",
                label: "Theme fit",
                detail: result.thesis,
                source: result.sources.joined(separator: " + ")
            )
            evidence.append(theme)
            positive.append(
                InvestmentCasePoint(
                    text: "The source description establishes \(result.exposure.rawValue.lowercased()) to the theme.",
                    evidence: [theme]
                )
            )
        }

        addPeerComparison(
            label: "Trailing P/E",
            id: "trailing_pe",
            lowerIsFavorable: true,
            result: result,
            peers: peerSnapshots,
            evidence: &evidence,
            positive: &positive,
            watchouts: &watchouts
        )
        addPeerComparison(
            label: "Forward P/E",
            id: "forward_pe",
            lowerIsFavorable: true,
            result: result,
            peers: peerSnapshots,
            evidence: &evidence,
            positive: &positive,
            watchouts: &watchouts
        )
        addPeerComparison(
            label: "Return on equity",
            id: "return_on_equity",
            lowerIsFavorable: false,
            result: result,
            peers: peerSnapshots,
            evidence: &evidence,
            positive: &positive,
            watchouts: &watchouts
        )

        if let snapshot = result.snapshot {
            addProfitabilityEvidence(
                snapshot: snapshot,
                evidence: &evidence,
                positive: &positive,
                watchouts: &watchouts
            )
            addBalanceEvidence(
                snapshot: snapshot,
                evidence: &evidence,
                positive: &positive,
                watchouts: &watchouts
            )
            addTargetEvidence(
                snapshot: snapshot,
                evidence: &evidence,
                positive: &positive,
                watchouts: &watchouts
            )
            addContextFact("Market cap", id: "market_cap", snapshot: snapshot, evidence: &evidence)
            addContextFact("LTM revenue", id: "revenue", snapshot: snapshot, evidence: &evidence)
            addContextFact("EV / EBITDA", id: "ev_ebitda", snapshot: snapshot, evidence: &evidence)
            addContextFact("FY1 EPS estimate", id: "fy1_eps", snapshot: snapshot, evidence: &evidence)
            addContextFact("FY1 revenue estimate", id: "fy1_revenue", snapshot: snapshot, evidence: &evidence)
        }

        let coverageDetail = result.exposure == .profile
            ? "The retrieved fields do not establish intrinsic value, a complete competitive position, or future returns."
            : "The retrieved fields do not quantify revenue attributable to this theme or establish future returns."
        let coverageText = result.exposure == .profile
            ? "A complete valuation and forward-return case is not available in this result."
            : "Theme-specific revenue and future return evidence are not available in this result."
        let limits = InvestmentEvidenceItem(
            id: "coverage_limit",
            label: "Coverage limit",
            detail: coverageDetail,
            source: "Coverage check"
        )
        evidence.append(limits)
        watchouts.append(
            InvestmentCasePoint(
                text: coverageText,
                evidence: [limits]
            )
        )

        let stance: InvestmentStance
        if evidence.count <= 2 {
            stance = .insufficient
        } else if positive.count >= 3 && positive.count > watchouts.count {
            stance = .constructive
        } else if watchouts.count > positive.count {
            stance = .cautious
        } else {
            stance = .mixed
        }
        let supportSummary = positive.prefix(2).map(\.text).joined(separator: " ")
        let cautionSummary = watchouts.prefix(1).map(\.text).joined(separator: " ")
        let summary: String
        switch stance {
        case .constructive:
            summary = "\(supportSummary) \(cautionSummary)"
        case .mixed:
            summary = "\(supportSummary) \(cautionSummary)"
        case .cautious:
            summary = "\(cautionSummary)\(supportSummary.isEmpty ? "" : " \(supportSummary)")"
        case .insufficient:
            summary = result.exposure == .profile
                ? "The available company data is not sufficient to judge the investment case."
                : "The business connection is visible, but there is not enough comparable financial evidence to judge the investment case."
        }
        let fallback = InvestmentCase(
            stance: stance,
            summary: summary,
            reasons: Array(positive.prefix(3)),
            watchouts: Array(watchouts.prefix(3))
        )
        return InvestmentCaseInput(company: result.candidate, evidence: evidence, fallback: fallback)
    }

    private static func addPeerComparison(
        label: String,
        id: String,
        lowerIsFavorable: Bool,
        result: ResearchCompanyResult,
        peers: [CompanySnapshot],
        evidence: inout [InvestmentEvidenceItem],
        positive: inout [InvestmentCasePoint],
        watchouts: inout [InvestmentCasePoint]
    ) {
        guard let fact = result.snapshot?.facts.first(where: { $0.label == label }), fact.value.isFinite else { return }
        let values = peers.compactMap { snapshot in
            snapshot.facts.first(where: { $0.label == label })?.value
        }.filter { $0.isFinite && $0 > 0 }.sorted()
        guard values.count >= 3, let median = median(values), median > 0 else { return }
        let item = InvestmentEvidenceItem(
            id: id,
            label: label,
            detail: "\(label) is \(format(fact.value, unit: fact.unit)); the screened peer median is \(format(median, unit: fact.unit)) across \(values.count) companies with data.",
            source: fact.source
        )
        evidence.append(item)
        let relative = fact.value / median
        if (lowerIsFavorable && relative <= 0.9) || (!lowerIsFavorable && relative >= 1.1) {
            positive.append(
                InvestmentCasePoint(
                    text: lowerIsFavorable
                        ? "\(label) is below the screened peer median."
                        : "\(label) is above the screened peer median.",
                    evidence: [item]
                )
            )
        } else if (lowerIsFavorable && relative >= 1.1) || (!lowerIsFavorable && relative <= 0.9) {
            watchouts.append(
                InvestmentCasePoint(
                    text: lowerIsFavorable
                        ? "\(label) is above the screened peer median."
                        : "\(label) is below the screened peer median.",
                    evidence: [item]
                )
            )
        }
    }

    private static func addBalanceEvidence(
        snapshot: CompanySnapshot,
        evidence: inout [InvestmentEvidenceItem],
        positive: inout [InvestmentCasePoint],
        watchouts: inout [InvestmentCasePoint]
    ) {
        guard let cash = snapshot.facts.first(where: { $0.label == "Cash and equivalents" }) else { return }
        let debtFacts = snapshot.facts.filter {
            ["Total debt", "Current debt", "Long-term debt"].contains($0.label)
        }
        guard !debtFacts.isEmpty else { return }
        let totalDebt = debtFacts.first(where: { $0.label == "Total debt" })?.value
            ?? debtFacts.reduce(0) { $0 + $1.value }
        let debtSource = joinedSources(debtFacts.map(\.source))
        let item = InvestmentEvidenceItem(
            id: "balance_sheet",
            label: "Balance sheet",
            detail: "Cash and equivalents are \(format(cash.value, unit: cash.unit)); reported debt is \(format(totalDebt, unit: debtFacts[0].unit)).",
            source: joinedSources([cash.source, debtSource])
        )
        evidence.append(item)
        if cash.value >= totalDebt {
            positive.append(InvestmentCasePoint(text: "Reported cash is at least as large as reported debt.", evidence: [item]))
        } else if totalDebt > max(cash.value * 2, 1) {
            watchouts.append(InvestmentCasePoint(text: "Reported debt is more than twice reported cash.", evidence: [item]))
        }
    }

    private static func addProfitabilityEvidence(
        snapshot: CompanySnapshot,
        evidence: inout [InvestmentEvidenceItem],
        positive: inout [InvestmentCasePoint],
        watchouts: inout [InvestmentCasePoint]
    ) {
        guard let revenue = snapshot.facts.first(where: { $0.label == "Revenue" }),
              let income = snapshot.facts.first(where: { $0.label == "Net income" }),
              revenue.value > 0,
              revenue.unit == income.unit,
              revenue.periodEnd == income.periodEnd else { return }
        let margin = income.value / revenue.value * 100
        let period = income.periodEnd.map {
            " for the period ending \($0.formatted(date: .abbreviated, time: .omitted))"
        } ?? ""
        let item = InvestmentEvidenceItem(
            id: "reported_profitability",
            label: "Reported profitability",
            detail: "Net income is \(format(income.value, unit: income.unit)) on \(format(revenue.value, unit: revenue.unit)) of revenue\(period), a simple margin of \(format(margin, unit: "%")).",
            source: joinedSources([income.source, revenue.source])
        )
        evidence.append(item)
        if income.value > 0 {
            positive.append(InvestmentCasePoint(text: "The latest comparable revenue and net-income facts show a profit.", evidence: [item]))
        } else if income.value < 0 {
            watchouts.append(InvestmentCasePoint(text: "The latest comparable revenue and net-income facts show a loss.", evidence: [item]))
        }
    }

    private static func addTargetEvidence(
        snapshot: CompanySnapshot,
        evidence: inout [InvestmentEvidenceItem],
        positive: inout [InvestmentCasePoint],
        watchouts: inout [InvestmentCasePoint]
    ) {
        guard let price = snapshot.facts.first(where: { $0.label == "Share price" }), price.value > 0,
              let target = snapshot.facts.first(where: { $0.label == "Mean price target" }), target.value > 0 else { return }
        let upside = (target.value / price.value - 1) * 100
        let item = InvestmentEvidenceItem(
            id: "analyst_target",
            label: "Analyst target",
            detail: "The source share price is \(format(price.value, unit: price.unit)); the mean analyst target is \(format(target.value, unit: target.unit)), a \(format(upside, unit: "%")) difference. Targets are opinions and can change.",
            source: target.source
        )
        evidence.append(item)
        if upside >= 10 {
            positive.append(InvestmentCasePoint(text: "The mean analyst target is above the source share price, with normal target uncertainty.", evidence: [item]))
        } else if upside <= -5 {
            watchouts.append(InvestmentCasePoint(text: "The mean analyst target is below the source share price.", evidence: [item]))
        }
    }

    private static func addContextFact(
        _ label: String,
        id: String,
        snapshot: CompanySnapshot,
        evidence: inout [InvestmentEvidenceItem]
    ) {
        guard let fact = snapshot.facts.first(where: { $0.label == label }) else { return }
        evidence.append(
            InvestmentEvidenceItem(
                id: id,
                label: label,
                detail: "\(label) is \(format(fact.value, unit: fact.unit)).",
                source: fact.source
            )
        )
    }

    private static func median(_ values: [Double]) -> Double? {
        guard !values.isEmpty else { return nil }
        let middle = values.count / 2
        return values.count.isMultiple(of: 2)
            ? (values[middle - 1] + values[middle]) / 2
            : values[middle]
    }

    private static func joinedSources(_ values: [String]) -> String {
        var seen = Set<String>()
        return values
            .filter { !$0.isEmpty && seen.insert($0).inserted }
            .joined(separator: " + ")
    }

    private static func format(_ value: Double, unit: String) -> String {
        if unit.uppercased() == "USD" {
            return value.formatted(
                .currency(code: "USD").notation(.compactName).precision(.fractionLength(0...2))
            )
        }
        if unit == "%" { return value.formatted(.number.precision(.fractionLength(1))) + "%" }
        if unit == "x" { return value.formatted(.number.precision(.fractionLength(1))) + "x" }
        return value.formatted(.number.notation(.compactName).precision(.fractionLength(0...2))) + " \(unit)"
    }
}
