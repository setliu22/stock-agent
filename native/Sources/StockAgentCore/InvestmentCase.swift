import Foundation
import FoundationModels

@Generable
private struct GeneratedInvestmentCaseRow {
    @Guide(description: "Copy the supplied company identifier exactly")
    var identifier: String

    @Guide(description: "Copy one to three supplied statement IDs, choosing the most useful facts for further research", .minimumCount(1), .maximumCount(3))
    var statementIDs: [String]
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
    let summaryStatements: [String: String]
    let cautionStatementIDs: Set<String>
}

protocol InvestmentCaseGenerating: Sendable {
    func generate(_ inputs: [InvestmentCaseInput]) async throws -> [String: InvestmentCase]
}

struct OnDeviceInvestmentCaseGenerator: InvestmentCaseGenerating {
    func generate(_ inputs: [InvestmentCaseInput]) async throws -> [String: InvestmentCase] {
        let inputs = inputs.filter { $0.evidence.contains(where: { $0.id != "coverage_limit" }) }
        guard !inputs.isEmpty else { return [:] }
        guard SystemLanguageModel.default.isAvailable else {
            throw StockAgentError.unavailable("Apple Intelligence is unavailable for investment-case synthesis.")
        }
        var output = [String: InvestmentCase]()
        for offset in stride(from: 0, to: inputs.count, by: 5) {
            try Task.checkCancellation()
            let group = Array(inputs[offset..<min(offset + 5, inputs.count)])
            let session = LanguageModelSession(
                model: .default,
                instructions: """
                Select the most informative supplied statements for each company's research summary.
                Return only statement IDs and the exact company identifier. Prioritize a concrete
                business connection and comparable financial evidence when present. Include a relevant
                caution. A thematic connection alone does not establish an attractive investment.
                Do not create statements, recommendations, or company identifiers.
                """
            )
            let companyText = group.enumerated().map { index, input in
                let lines = input.summaryStatements.sorted { $0.key < $1.key }.map { id, statement in
                    "\(id) | \(statement)\(input.cautionStatementIDs.contains(id) ? " | Caution" : "")"
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
                generating: GeneratedInvestmentCaseBatch.self,
                options: GenerationOptions(sampling: .greedy, maximumResponseTokens: 500)
            )
            let selected = Self.validated(
                rows: response.content.companies.map { ($0.identifier, $0.statementIDs) },
                for: group
            )
            output.merge(selected, uniquingKeysWith: { _, latest in latest })
        }
        return output
    }

    static func validated(
        rows: [(identifier: String, statementIDs: [String])],
        for inputs: [InvestmentCaseInput]
    ) -> [String: InvestmentCase] {
        let rowsByID = Dictionary(grouping: rows, by: \.identifier)
        var output = [String: InvestmentCase]()
        for input in inputs {
            guard let matches = rowsByID[input.company.id], matches.count == 1,
                  let match = matches.first else { continue }
            output[input.company.id] = validated(statementIDs: match.statementIDs, for: input)
        }
        return output
    }

    static func validated(
        statementIDs: [String],
        for input: InvestmentCaseInput
    ) -> InvestmentCase {
        guard !statementIDs.isEmpty, statementIDs.count <= 3,
              Set(statementIDs).count == statementIDs.count,
              statementIDs.allSatisfy({ input.summaryStatements[$0] != nil }) else {
            return input.fallback
        }
        var selected = statementIDs
        if !input.cautionStatementIDs.isEmpty,
           input.cautionStatementIDs.isDisjoint(with: selected),
           let cautionID = input.cautionStatementIDs.sorted().first {
            selected = Array(selected.prefix(2)) + [cautionID]
        }
        let summary = selected.compactMap { input.summaryStatements[$0] }.joined(separator: " ")
        return InvestmentCase(
            summary: summary,
            reasons: input.fallback.reasons,
            watchouts: input.fallback.watchouts
        )
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

        addPeerComparison(
            label: "Trailing P/E",
            id: "trailing_pe",
            result: result,
            peers: peerSnapshots,
            evidence: &evidence,
            positive: &positive,
            watchouts: &watchouts
        )
        addPeerComparison(
            label: "Forward P/E",
            id: "forward_pe",
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

        let supportSummary = positive.prefix(2).map(\.text).joined(separator: " ")
        let cautionSummary = watchouts.prefix(1).map(\.text).joined(separator: " ")
        let summary = [supportSummary, cautionSummary].filter { !$0.isEmpty }.joined(separator: " ")
        let fallback = InvestmentCase(
            summary: summary,
            reasons: Array(positive.prefix(3)),
            watchouts: Array(watchouts.prefix(3))
        )
        let statements = (positive + watchouts).compactMap { point -> (String, String)? in
            guard let id = point.evidence.first?.id else { return nil }
            return (id, point.text)
        }
        return InvestmentCaseInput(
            company: result.candidate,
            evidence: evidence,
            fallback: fallback,
            summaryStatements: Dictionary(statements, uniquingKeysWith: { first, _ in first }),
            cautionStatementIDs: Set(watchouts.compactMap { $0.evidence.first?.id })
        )
    }

    private static func addPeerComparison(
        label: String,
        id: String,
        result: ResearchCompanyResult,
        peers: [CompanySnapshot],
        evidence: inout [InvestmentEvidenceItem],
        positive: inout [InvestmentCasePoint],
        watchouts: inout [InvestmentCasePoint]
    ) {
        guard let fact = result.snapshot?.facts.first(where: { $0.label == label }),
              fact.value.isFinite, fact.value > 0 else { return }
        var seen = Set<String>()
        let values = peers.compactMap { snapshot -> Double? in
            guard snapshot.cik != result.snapshot?.cik,
                  snapshot.description == result.snapshot?.description,
                  seen.insert(snapshot.cik).inserted,
                  let peerFact = snapshot.facts.first(where: { $0.label == label }),
                  peerFact.unit == fact.unit, peerFact.source == fact.source,
                  peerFact.periodStart == fact.periodStart,
                  peerFact.periodEnd == fact.periodEnd else { return nil }
            return peerFact.value
        }.filter { $0.isFinite && $0 > 0 }.sorted()
        guard values.count >= 3, let median = median(values), median > 0 else { return }
        let item = InvestmentEvidenceItem(
            id: id,
            label: label,
            detail: "\(label) is \(format(fact.value, unit: fact.unit)); the screened peer median is \(format(median, unit: fact.unit)) across \(values.count) companies with data.",
            source: fact.source
        )
        evidence.append(item)
        if fact.value < median {
            positive.append(
                InvestmentCasePoint(
                    text: "\(label) is below the screened peer median.",
                    evidence: [item]
                )
            )
        } else if fact.value > median {
            watchouts.append(
                InvestmentCasePoint(
                    text: "\(label) is above the screened peer median.",
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
        guard let cash = snapshot.facts.first(where: { $0.label == "Cash and equivalents" }),
              let debt = snapshot.facts.first(where: { $0.label == "Total debt" }),
              cash.value.isFinite, debt.value.isFinite,
              cash.value >= 0, debt.value >= 0,
              cash.unit == debt.unit,
              let period = cash.periodEnd, period == debt.periodEnd else { return }
        let item = InvestmentEvidenceItem(
            id: "balance_sheet",
            label: "Balance sheet",
            detail: "As of \(period.formatted(date: .abbreviated, time: .omitted)), cash and equivalents are \(format(cash.value, unit: cash.unit)); total debt is \(format(debt.value, unit: debt.unit)).",
            source: joinedSources([cash.source, debt.source])
        )
        evidence.append(item)
        if cash.value >= debt.value {
            positive.append(InvestmentCasePoint(text: "Reported cash is at least as large as total debt at the same reporting date.", evidence: [item]))
        } else {
            watchouts.append(InvestmentCasePoint(text: "Total debt exceeds cash and equivalents at the same reporting date.", evidence: [item]))
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
              revenue.value.isFinite, income.value.isFinite, revenue.value > 0,
              revenue.unit == income.unit,
              let start = revenue.periodStart, start == income.periodStart,
              let end = revenue.periodEnd, end == income.periodEnd,
              start < end else { return }
        let margin = income.value / revenue.value * 100
        guard margin.isFinite else { return }
        let period = " from \(start.formatted(date: .abbreviated, time: .omitted)) to \(end.formatted(date: .abbreviated, time: .omitted))"
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
