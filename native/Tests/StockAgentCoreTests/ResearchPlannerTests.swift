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
}

@Test
func uppercaseTickerCreatesARealNamedCompanyProposal() async throws {
    let proposal = try await ResearchPlanner(themeMapper: DroneThemeMapper()).propose(
        question: "What are META's biggest risks?"
    )
    #expect(proposal.mode == .named)
    #expect(proposal.securities == ["META"])
    #expect(proposal.resultCount == 1)
    #expect(proposal.capabilityIDs.contains("company_profile"))
    #expect(proposal.capabilityIDs.contains("regulatory_filings"))
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
func macroReferenceSetMatchesOriginalFiveSignals() {
    #expect(Set(MacroReferences.bySeriesID.keys) == Set([
        "DFF", "WALCL", "CPIAUCNS", "BAMLH0A0HYM2", "VIXCLS",
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
func sqlitePortfolioRoundTripAndFilteredHistory() async throws {
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
    let history = try await store.portfolioHistory()
    #expect(holdings.count == 1)
    #expect(holdings[0].quantity == 3)
    #expect(holdings[0].totalCost == 40)
    #expect(holdings[0].currentPrice == 18)
    #expect(history.last?.value == 54)
}
