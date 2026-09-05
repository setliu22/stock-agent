import Foundation
import Testing
@testable import StockAgentCore

private struct DroneThemeMapper: ThemeMapping {
    func map(theme: String, question: String) async throws -> MappedTheme {
        MappedTheme(
            theme: "autonomous drones",
            matches: [
                ProposedItem(
                    id: "Aerospace & Defense",
                    reason: "Autonomous aircraft and mission systems are sold into defense end markets."
                ),
                ProposedItem(
                    id: "Semiconductors",
                    reason: "Specialized processors can enable onboard autonomy."
                ),
            ]
        )
    }
}

private struct FixtureFetcher: DataFetching {
    let data: Data
    let statusCode: Int

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: statusCode,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        return (data, response)
    }
}

@Test
func purchaseNormalizesAndValidates() throws {
    let purchase = Purchase(
        ticker: " aapl ",
        quantity: 2,
        price: 100,
        purchasedAt: .now
    )
    #expect(try purchase.validated().ticker == "AAPL")
}

@Test
func autonomousDronesUseSemanticMapperWithoutTrendRules() async throws {
    let proposal = try await ResearchPlanner(themeMapper: DroneThemeMapper()).propose(
        question: "Find public companies positioned for autonomous drones"
    )
    #expect(proposal.mode == .discovery)
    #expect(proposal.theme == "autonomous drones")
    #expect(proposal.universes.first == "Aerospace & Defense")
    #expect(proposal.universes.contains("Semiconductors"))
}

@Test
func themeLanguageRemovesRequestFramingAndBroadUniverseWords() {
    #expect(
        ResearchThemeLanguage.conciseTheme(
            "Find public companies with material exposure to autonomous drones"
        ) == "autonomous drones"
    )
    let terms = ResearchThemeLanguage.cleanSearchTerms(
        ["Aerospace", "Drone Technology", "Unmanned Aerial Vehicles (UAVs)"],
        theme: "autonomous drones",
        universes: ["Aerospace & Defense"]
    )
    #expect(!terms.contains("Aerospace"))
    #expect(terms.contains("Drone"))
    #expect(terms.contains(where: { $0.contains("Unmanned Aerial Vehicles") }))
    #expect(ResearchThemeLanguage.tokenSet(in: "autonomous systems").contains("autonomous"))
}

@Test
func uppercaseTickerCreatesARealNamedCompanyProposal() async throws {
    let proposal = try await ResearchPlanner(themeMapper: DroneThemeMapper()).propose(
        question: "What are META's biggest risks?"
    )
    #expect(proposal.mode == .named)
    #expect(proposal.securities == ["META"])
    #expect(proposal.resultCount == 1)
}

@Test
func dollarPrefixedTickerIsCaseInsensitive() async throws {
    let proposal = try await ResearchPlanner(themeMapper: DroneThemeMapper()).propose(
        question: "Summarize the balance sheet for $meta"
    )
    #expect(proposal.mode == .named)
    #expect(proposal.securities == ["META"])
}

@Test
func riskFallbackUsesExistingFilingBullets() {
    let answer = NamedResearchQuery.sourceBoundFallback(
        question: "What are META's biggest risks?",
        companyName: "Meta Platforms Inc",
        evidence: [
            "SEC filing excerpt: Summary Risk Factors • user engagement may decline; • cybersecurity incidents may disrupt services; Risks Related to Our Business"
        ]
    )
    #expect(answer?.contains("User engagement may decline.") == true)
    #expect(answer?.contains("Cybersecurity incidents may disrupt services.") == true)
}

@Test
func yahooChartDecoderKeepsValidDailyCloses() async throws {
    let fixture = Data(
        """
        {"chart":{"result":[{"timestamp":[1700000000,1700086400,1700172800],"indicators":{"quote":[{"close":[10.5,null,12.25]}]}}],"error":null}}
        """.utf8
    )
    let points = try await YahooPriceService(
        fetcher: FixtureFetcher(data: fixture, statusCode: 200)
    ).dailyPrices(ticker: "meta")
    #expect(points.count == 2)
    #expect(points.map(\.close) == [10.5, 12.25])
    #expect(points[0].date < points[1].date)
}

@Test
func lsegDefenseScreenUsesTheIndustryTaxonomy() {
    let screen = ResearchRegistry.lsegScreenDefinition(for: "Aerospace & Defense")
    #expect(screen?.field == "TR.TRBCIndustryCode")
    #expect(screen?.codes == ["52101010"])
    #expect(screen?.screenBody(top: 12).contains("Equity(active,public,primary)") == true)
}

@Test
func investmentCaseTurnsMetricsIntoComparableResearchEvidence() {
    func snapshot(cik: String, pe: Double, roe: Double) -> CompanySnapshot {
        CompanySnapshot(
            cik: cik,
            ticker: cik,
            name: cik,
            description: "Aerospace & Defense",
            facts: [
                FinancialFact(label: "Trailing P/E", value: pe, unit: "x", periodEnd: nil, source: "LSEG Workspace"),
                FinancialFact(label: "Return on equity", value: roe, unit: "%", periodEnd: nil, source: "LSEG Workspace"),
                FinancialFact(label: "Cash and equivalents", value: 12, unit: "USD", periodEnd: nil, source: "LSEG Workspace"),
                FinancialFact(label: "Total debt", value: 5, unit: "USD", periodEnd: nil, source: "LSEG Workspace"),
            ],
            recentFilings: []
        )
    }
    let companySnapshot = snapshot(cik: "ONE", pe: 10, roe: 30)
    let candidate = CompanyCandidate(
        cik: "ONE",
        ticker: "ONE",
        name: "One Aerospace",
        filingDate: nil,
        filingURL: nil,
        relevance: 1
    )
    let result = ResearchCompanyResult(
        candidate: candidate,
        exposure: .direct,
        thesis: "The source says the company makes unmanned aircraft.",
        evidence: [],
        snapshot: companySnapshot,
        sources: ["LSEG Workspace"]
    )
    let input = InvestmentCaseEvidence.makeInput(
        for: result,
        peerSnapshots: [
            companySnapshot,
            snapshot(cik: "TWO", pe: 20, roe: 20),
            snapshot(cik: "THREE", pe: 30, roe: 10),
            snapshot(cik: "FOUR", pe: 25, roe: 15),
        ]
    )
    #expect(input.evidence.contains(where: { $0.id == "trailing_pe" && $0.detail.contains("peer median") }))
    #expect(!input.evidence.contains(where: { $0.id == "return_on_equity" }))
    #expect(input.fallback.reasons.contains(where: { $0.text.contains("below the screened peer median") }))
    #expect(input.fallback.watchouts.contains(where: { $0.text.contains("Theme-specific revenue") }))
}

@Test
func groundedFitSeparatesProductEvidenceFromBroadSectorLanguage() async throws {
    let evaluator = GroundedCompanyFitEvaluator()
    let ge = CompanyCandidate(
        cik: "GE", ticker: "GE", name: "General Electric Company",
        filingDate: nil, filingURL: nil, relevance: 1
    )
    let lockheed = CompanyCandidate(
        cik: "LMT", ticker: "LMT", name: "Lockheed Martin Corp",
        filingDate: nil, filingURL: nil, relevance: 1
    )
    let safran = CompanyCandidate(
        cik: "SAF", ticker: "SAF", name: "Safran SA",
        filingDate: nil, filingURL: nil, relevance: 1
    )
    let terms = ["unmanned aerial vehicles", "UAV", "drone"]
    let geFit = try await evaluator.evaluate(
        company: ge,
        theme: "autonomous drones",
        searchTerms: terms,
        evidence: ["LSEG business description: GE manufactures jet engines for commercial and military aircraft."],
        snapshot: nil
    )
    let lockheedFit = try await evaluator.evaluate(
        company: lockheed,
        theme: "autonomous drones",
        searchTerms: terms,
        evidence: ["LSEG business description: Lockheed Martin designs and manufactures unmanned air vehicles and related technologies."],
        snapshot: nil
    )
    let safranFit = try await evaluator.evaluate(
        company: safran,
        theme: "autonomous drones",
        searchTerms: terms,
        evidence: ["LSEG business description: Safran develops propulsion systems for commercial and military aircraft, helicopters, satellites, and drones."],
        snapshot: nil
    )
    #expect(geFit.0 == .incidental)
    #expect(lockheedFit.0 == .direct)
    #expect(safranFit.0 == .enabling)
}

@Test
func secCounterpartyLanguageIsNotAttributedToTheFiler() async throws {
    let evaluator = GroundedCompanyFitEvaluator()
    let filer = CompanyCandidate(
        cik: "BRQL", ticker: "BRQL", name: "brooqLy, Inc.",
        filingDate: nil, filingURL: nil, relevance: 1
    )
    let fit = try await evaluator.evaluate(
        company: filer,
        theme: "autonomous drones",
        searchTerms: ["UAV", "drone"],
        evidence: [
            "SEC filing excerpt: Dynamic Aerospace Systems designs and manufactures advanced UAVs for logistics and delivery."
        ],
        snapshot: nil
    )
    #expect(fit.0 == .incidental)
}

@Test
func groundedFitCentersItsExplanationOnTheMatchingEvidence() async throws {
    let evaluator = GroundedCompanyFitEvaluator()
    let candidate = CompanyCandidate(
        cik: "UAV", ticker: "UAV", name: "Example Aircraft",
        filingDate: nil, filingURL: nil, relevance: 1
    )
    let fit = try await evaluator.evaluate(
        company: candidate,
        theme: "autonomous drones",
        searchTerms: ["unmanned aerial vehicle", "UAV"],
        evidence: [
            "LSEG business description: TABLE OF CONTENTS "
                + String(repeating: "general corporate information ", count: 24)
                + ". Example Aircraft designs and manufactures autonomous drones for inspection."
        ],
        snapshot: nil
    )
    #expect(fit.0 == .direct)
    #expect(fit.1.contains("autonomous drones"))
    #expect(!fit.1.contains("TABLE OF CONTENTS"))
}

@Test
func namedInvestmentCaseUsesComparableProfitAndExplicitTotalDebt() {
    let period = Date(timeIntervalSince1970: 1_767_225_600)
    let start = period.addingTimeInterval(-364 * 86_400)
    let snapshot = CompanySnapshot(
        cik: "META",
        ticker: "META",
        name: "Meta Platforms",
        description: "Internet services",
        facts: [
            FinancialFact(label: "Revenue", value: 100, unit: "USD", periodEnd: period, periodStart: start),
            FinancialFact(label: "Net income", value: 20, unit: "USD", periodEnd: period, periodStart: start),
            FinancialFact(label: "Cash and equivalents", value: 50, unit: "USD", periodEnd: period),
            FinancialFact(label: "Total debt", value: 20, unit: "USD", periodEnd: period),
        ],
        recentFilings: []
    )
    let candidate = CompanyCandidate(
        cik: "META", ticker: "META", name: "Meta Platforms",
        filingDate: nil, filingURL: nil, relevance: 1
    )
    let result = ResearchCompanyResult(
        candidate: candidate,
        exposure: .profile,
        thesis: "Source-grounded answer.",
        evidence: [],
        snapshot: snapshot,
        sources: ["SEC EDGAR"]
    )
    let input = InvestmentCaseEvidence.makeInput(for: result, peerSnapshots: [snapshot])
    #expect(input.fallback.reasons.contains(where: { $0.text.contains("show a profit") }))
    #expect(input.fallback.reasons.contains(where: { $0.text.contains("cash is at least") }))
    #expect(input.evidence.contains(where: { $0.id == "balance_sheet" && $0.detail.contains("$20") }))
}

@Test
func macroReferencesPreserveOriginalSignalsAndCoverLaborAndBondYields() {
    #expect(Set(MacroReferences.bySeriesID.keys) == Set([
        "DFF", "WALCL", "CPIAUCNS", "BAMLH0A0HYM2", "VIXCLS",
        "DGS10", "UNRATE",
    ]))
    #expect(MacroReferences.bySeriesID.values.allSatisfy { !$0.explanation.isEmpty })
}

@Test
func portfolioJSONAliasesRemainDeterministic() throws {
    let purchases = try PortfolioImporter.parseJSON(
        """
        {"holdings":[{"symbol":"rdw","shares":4.5,"average_cost":10.25,"purchase_date":"2026-01-04"}]}
        """
    )
    #expect(purchases.count == 1)
    #expect(purchases[0].ticker == "RDW")
    #expect(purchases[0].quantity == 4.5)
    #expect(purchases[0].price == 10.25)
}

@Test
func portfolioJSONRequiresPurchaseDate() {
    do {
        _ = try PortfolioImporter.parseJSON(
            "{\"holdings\":[{\"symbol\":\"AAPL\",\"shares\":1,\"average_cost\":100}]}"
        )
        Issue.record("Expected an omitted purchase date to be rejected")
    } catch {
        #expect(String(describing: error).contains("purchase date is required"))
    }
}

@Test
func namedResearchFocusRemovesRequestAndCompanyWords() {
    #expect(
        NamedResearchQuery.filingFocus(
            question: "What are META's biggest risks?",
            ticker: "META",
            companyName: "Meta Platforms Inc"
        ) == "biggest risks"
    )
    #expect(
        NamedResearchQuery.filingFocus(
            question: "Tell me about META",
            ticker: "META",
            companyName: "Meta Platforms Inc"
        ) == "business strategy risk factors"
    )
}

@Test
func sqlitePortfolioRoundTripStoresPricesAndHoldings() async throws {
    let folder = FileManager.default.temporaryDirectory
        .appendingPathComponent("stock-agent-tests-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = try PortfolioStore(databaseURL: folder.appendingPathComponent("portfolio.db"))
    let calendar = Calendar(identifier: .gregorian)
    let firstDay = calendar.date(from: DateComponents(year: 2026, month: 1, day: 2, hour: 12))!
    let secondDay = calendar.date(from: DateComponents(year: 2026, month: 2, day: 2, hour: 12))!
    _ = try await store.record([
        Purchase(ticker: "ABC", quantity: 2, price: 10, purchasedAt: firstDay),
        Purchase(ticker: "ABC", quantity: 1, price: 20, purchasedAt: secondDay),
    ])
    try await store.importPriceHistory(
        ticker: "ABC",
        points: [PricePoint(date: firstDay, close: 12), PricePoint(date: secondDay, close: 18)],
        source: "Test"
    )
    let holdings = try await store.holdings()
    let prices = await store.priceHistory(ticker: "ABC")
    #expect(holdings.count == 1)
    #expect(holdings[0].quantity == 3)
    #expect(holdings[0].totalCost == 40)
    #expect(holdings[0].currentPrice == 18)
    #expect(prices.map(\.close) == [12, 18])
}
