import Foundation
import Testing
@testable import StockAgentCore

private func financialSnapshot(_ id: String = "SUBJECT", facts: [FinancialFact]) -> CompanySnapshot {
    CompanySnapshot(cik: id, ticker: id, name: id, description: "Test company", facts: facts, recentFilings: [])
}

private func financialInput(_ snapshot: CompanySnapshot, peers: [CompanySnapshot] = []) -> InvestmentCaseInput {
    InvestmentCaseEvidence.makeInput(
        for: ResearchCompanyResult(
            candidate: CompanyCandidate(
                cik: snapshot.cik, ticker: snapshot.ticker, name: snapshot.name,
                filingDate: nil, filingURL: nil, relevance: 1
            ),
            exposure: .profile,
            thesis: "Company research",
            evidence: [],
            snapshot: snapshot,
            sources: ["SEC EDGAR"]
        ),
        peerSnapshots: peers
    )
}

@Test
func profitabilityRequiresMatchingKnownDurationAndCurrency() {
    let end = Date(timeIntervalSince1970: 1_767_225_600)
    let annualStart = end.addingTimeInterval(-364 * 86_400)
    let revenue = FinancialFact(label: "Revenue", value: 100, unit: "USD", periodEnd: end, periodStart: annualStart)
    let incompatibleIncome = [
        FinancialFact(label: "Net income", value: 20, unit: "USD", periodEnd: end, periodStart: end.addingTimeInterval(-90 * 86_400)),
        FinancialFact(label: "Net income", value: 20, unit: "EUR", periodEnd: end, periodStart: annualStart),
        FinancialFact(label: "Net income", value: 20, unit: "USD", periodEnd: end),
        FinancialFact(label: "Net income", value: 20, unit: "USD", periodEnd: end.addingTimeInterval(-86_400), periodStart: annualStart),
    ]
    for income in incompatibleIncome {
        let input = financialInput(financialSnapshot(facts: [revenue, income]))
        #expect(!input.evidence.contains(where: { $0.id == "reported_profitability" }))
    }
    let income = FinancialFact(label: "Net income", value: 20, unit: "USD", periodEnd: end, periodStart: annualStart)
    let valid = financialInput(financialSnapshot(facts: [revenue, income]))
    #expect(valid.evidence.contains(where: { $0.id == "reported_profitability" && $0.detail.contains("20.0%") }))
}

@Test
func partialDebtNeverBecomesTotalDebtAndComparisonsRequireMatchingDates() {
    let end = Date(timeIntervalSince1970: 1_767_225_600)
    let cash = FinancialFact(label: "Cash and equivalents", value: 50, unit: "USD", periodEnd: end)
    let incomplete = financialInput(financialSnapshot(facts: [
        cash,
        FinancialFact(label: "Current debt", value: 5, unit: "USD", periodEnd: end),
        FinancialFact(label: "Long-term debt", value: 15, unit: "USD", periodEnd: end),
    ]))
    #expect(!incomplete.evidence.contains(where: { $0.id == "balance_sheet" }))
    let incompatibleDebt = [
        FinancialFact(label: "Total debt", value: 20, unit: "EUR", periodEnd: end),
        FinancialFact(label: "Total debt", value: 20, unit: "USD", periodEnd: nil),
        FinancialFact(label: "Total debt", value: 20, unit: "USD", periodEnd: end.addingTimeInterval(-86_400)),
    ]
    for debt in incompatibleDebt {
        #expect(!financialInput(financialSnapshot(facts: [cash, debt])).evidence.contains(where: { $0.id == "balance_sheet" }))
    }
}

@Test
func nonpositivePEAndDuplicatePeersCannotCreateFavorableValuation() {
    func peer(_ id: String, pe: Double) -> CompanySnapshot {
        financialSnapshot(id, facts: [FinancialFact(label: "Trailing P/E", value: pe, unit: "x", periodEnd: nil, source: "LSEG Workspace")])
    }
    let peers = [peer("TWO", pe: 20), peer("THREE", pe: 30), peer("FOUR", pe: 40)]
    for pe in [-10.0, 0] {
        #expect(!financialInput(peer("SUBJECT", pe: pe), peers: peers).evidence.contains(where: { $0.id == "trailing_pe" }))
    }
    let subject = peer("SUBJECT", pe: 10)
    let duplicates = [subject, peers[0], peers[0], peers[1]]
    #expect(!financialInput(subject, peers: duplicates).evidence.contains(where: { $0.id == "trailing_pe" }))
    let valid = financialInput(subject, peers: [subject] + peers)
    #expect(valid.evidence.contains(where: { $0.id == "trailing_pe" && $0.detail.contains("30.0x") && $0.detail.contains("3 companies") }))
}

@Test
func modelSummaryRequiresExactCompanyAndStatementIdentifiers() {
    let first = financialInput(financialSnapshot("FIRST", facts: []))
    let second = financialInput(financialSnapshot("SECOND", facts: []))
    let unknownCompany = OnDeviceInvestmentCaseGenerator.validated(
        rows: [(identifier: "UNKNOWN", statementIDs: ["coverage_limit"])],
        for: [first, second]
    )
    #expect(unknownCompany.isEmpty)
    let duplicateCompany = OnDeviceInvestmentCaseGenerator.validated(
        rows: [("FIRST", ["coverage_limit"]), ("FIRST", ["coverage_limit"])],
        for: [first, second]
    )
    #expect(duplicateCompany.isEmpty)
    let reordered = OnDeviceInvestmentCaseGenerator.validated(
        rows: [("SECOND", ["coverage_limit"]), ("FIRST", ["coverage_limit"])],
        for: [first, second]
    )
    #expect(Set(reordered.keys) == Set(["FIRST", "SECOND"]))
    let invented = OnDeviceInvestmentCaseGenerator.validated(statementIDs: ["guaranteed_returns"], for: first)
    #expect(invented == first.fallback)
    let repeated = OnDeviceInvestmentCaseGenerator.validated(statementIDs: ["coverage_limit", "coverage_limit"], for: first)
    #expect(repeated == first.fallback)
}

private struct SECFinancialFixtureFetcher: DataFetching {
    let facts: Data

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let submissions = Data(#"{"name":"Test Company","tickers":["TEST"],"sicDescription":"Testing","filings":{"recent":{"accessionNumber":[],"filingDate":[],"form":[],"primaryDocument":[]}}}"#.utf8)
        let data = request.url!.path.contains("companyfacts") ? facts : submissions
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

private func secFixtureSnapshot(_ facts: [String: Any]) async throws -> CompanySnapshot {
    let data = try JSONSerialization.data(withJSONObject: ["facts": ["us-gaap": facts]])
    let service = SECService(fetcher: SECFinancialFixtureFetcher(facts: data))
    return try await service.snapshot(for: CompanyCandidate(
        cik: "0000000001", ticker: "TEST", name: "Test Company", filingDate: nil, filingURL: nil, relevance: 1
    ))
}

@Test
func secFactsSelectLatestFullDurationAndAmendmentIndependentlyOfJSONOrder() async throws {
    let observations: [[String: Any]] = [
        ["start": "2025-01-01", "end": "2025-12-31", "val": 100, "form": "10-K", "filed": "2026-02-01", "accn": "original"],
        ["start": "2025-10-01", "end": "2025-12-31", "val": 30, "form": "10-K", "filed": "2026-02-01", "accn": "original"],
        ["start": "2025-01-01", "end": "2025-12-31", "val": 110, "form": "10-K/A", "filed": "2026-03-01", "accn": "amended"],
        ["start": "2024-01-01", "end": "2024-12-31", "val": 80, "form": "10-K", "filed": "2026-03-01", "accn": "comparative"],
    ]
    func concepts(_ ordered: [[String: Any]]) -> [String: Any] {
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax": ["units": ["USD": ordered]],
            "Revenues": ["units": ["USD": [["start": "2025-01-01", "end": "2025-12-31", "val": 999, "form": "10-K/A", "filed": "2026-03-01", "accn": "amended"]]]],
        ]
    }
    let normal = try await secFixtureSnapshot(concepts(observations))
    let reversed = try await secFixtureSnapshot(concepts(Array(observations.reversed())))
    #expect(normal.facts == reversed.facts)
    let revenue = try #require(normal.facts.first(where: { $0.label == "Revenue" }))
    #expect(revenue.value == 110)
    #expect(revenue.periodStart != nil)
    #expect(revenue.periodEnd != nil)
    #expect(revenue.filedAt != nil)
    #expect(revenue.periodEnd!.timeIntervalSince(revenue.periodStart!) > 360 * 86_400)
}

@Test
func secFactsIgnoreInvalidPreferredConceptAndConflictingObservations() async throws {
    let snapshot = try await secFixtureSnapshot([
        "RevenueFromContractWithCustomerExcludingAssessedTax": ["units": ["USD": [["end": "2025-12-31", "val": 999, "form": "10-K", "filed": "2026-02-01"]]]],
        "Revenues": ["units": ["USD": [["start": "2025-01-01", "end": "2025-12-31", "val": 100, "form": "10-K", "filed": "2026-02-01"]]]],
        "Assets": ["units": ["USD": [
            ["end": "2025-12-31", "val": 10, "form": "10-K", "filed": "2026-02-01", "accn": "same"],
            ["end": "2025-12-31", "val": 20, "form": "10-K", "filed": "2026-02-01", "accn": "same"],
        ]]],
        "LongTermDebtNoncurrent": ["units": ["USD": [["end": "2025-12-31", "val": 5, "form": "10-K", "filed": "2026-02-01"]]]],
    ])
    #expect(snapshot.facts.first(where: { $0.label == "Revenue" })?.value == 100)
    #expect(!snapshot.facts.contains(where: { $0.label == "Total assets" }))
    #expect(snapshot.facts.contains(where: { $0.label == "Long-term debt, noncurrent" }))
    #expect(!snapshot.facts.contains(where: { $0.label == "Total debt" }))
}
