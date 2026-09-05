import Foundation
import Testing
@testable import StockAgentCore

private struct WorkflowMapper: ThemeMapping {
    func map(theme: String, question: String) async throws -> MappedTheme {
        MappedTheme(theme: theme, matches: [.init(id: "Aerospace & Defense", reason: "Fixture")])
    }
}

private struct UnavailableFetcher: DataFetching {
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        throw StockAgentError.unavailable("Fixture source unavailable")
    }
}

private struct WorkflowLSEG: LSEGResearchProviding {
    let records: [LSEGCompanyRecord]
    func isAvailable() async -> Bool { true }
    func discover(universes: [String], limit: Int) async throws -> [LSEGCompanyRecord] { records }
    func company(ticker: String) async throws -> LSEGCompanyRecord { records.first! }
}

private struct UnavailableAnswerer: CompanyQuestionAnswering {
    func answer(question: String, company: CompanyCandidate, evidence: [String], snapshot: CompanySnapshot) async throws -> String {
        throw StockAgentError.unavailable("Fixture model unavailable")
    }
}

private struct WorkflowEvaluator: CompanyFitEvaluating {
    let supportedTicker: String?
    func evaluate(company: CompanyCandidate, theme: String, searchTerms: [String], evidence: [String], snapshot: CompanySnapshot?) async throws -> (ExposureStrength, String) {
        (company.ticker == supportedTicker ? .direct : .incidental, "Fixture exposure explanation")
    }
}

private func workflowRecord(_ ticker: String, rank: Double = 1) -> LSEGCompanyRecord {
    LSEGCompanyRecord(ticker: ticker, ric: "\(ticker).N", name: ticker, industry: "Aerospace & Defense",
        businessSummary: "The company develops unmanned aircraft.", marketCap: rank, facts: [], universe: "Aerospace & Defense")
}

@Test
func explicitResearchModesAvoidAcronymRoutingAndAcceptBareLowercaseTickers() async throws {
    let planner = ResearchPlanner(themeMapper: WorkflowMapper())
    let discovery = try await planner.propose(question: "US UAV manufacturers", preferredMode: .discovery)
    #expect(discovery.mode == .discovery)
    #expect(discovery.securities.isEmpty)
    let company = try await planner.propose(question: "meta", preferredMode: .named)
    #expect(company.securities == ["META"])
    #expect(planner.groundedTickerSymbols(in: "Research $AI") == ["AI"])
    await #expect(throws: StockAgentError.self) {
        try await planner.propose(question: "explain this business", preferredMode: .named)
    }
}

@Test
func discoveryNeverFillsResultsWithIncidentalMatches() async throws {
    let engine = ResearchEngine(sec: SECService(fetcher: UnavailableFetcher()),
        lseg: WorkflowLSEG(records: [workflowRecord("ABC")]), evaluator: WorkflowEvaluator(supportedTicker: nil))
    let report = try await engine.run(ResearchProposal(question: "Unmanned aircraft", mode: .discovery,
        universes: ["Aerospace & Defense"], theme: "Unmanned aircraft", resultCount: 3))
    #expect(report.companies.isEmpty)
    #expect(report.notes.contains(where: { $0.contains("0 companies") }))
}

@Test
func discoveryReviewsBeyondRequestedCountBeforeRejectingATheme() async throws {
    let records = [workflowRecord("ONE", rank: 4), workflowRecord("TWO", rank: 3),
                   workflowRecord("THREE", rank: 2), workflowRecord("MATCH", rank: 1)]
    let engine = ResearchEngine(sec: SECService(fetcher: UnavailableFetcher()),
        lseg: WorkflowLSEG(records: records), evaluator: WorkflowEvaluator(supportedTicker: "MATCH"))
    let report = try await engine.run(ResearchProposal(question: "Unmanned aircraft", mode: .discovery,
        universes: ["Aerospace & Defense"], theme: "Unmanned aircraft", resultCount: 1))
    #expect(report.companies.map(\.candidate.ticker) == ["MATCH"])
    #expect(report.companies.first?.investmentCase == nil)
}

@Test
func unavailableCompanyAnswerDoesNotMasqueradeAsAnAnsweredQuestion() async throws {
    let engine = ResearchEngine(sec: SECService(fetcher: UnavailableFetcher()),
        lseg: WorkflowLSEG(records: [workflowRecord("META")]), companyAnswerer: UnavailableAnswerer())
    let report = try await engine.run(ResearchProposal(question: "What are META's risks?", mode: .named,
        securities: ["META"], resultCount: 1))
    let company = try #require(report.companies.first)
    #expect(company.thesis.contains("could not be generated"))
    #expect(company.investmentCase == nil)
    #expect(!company.thesis.contains("is classified as"))
}

@Test
func companyAnswerRequiresExactIdentityKnownSourcesAndSupportedNumbers() {
    let sources = ["S1": "Reported revenue is 100 USD."]
    let valid = GeneratedCompanyAnswer(companyID: "META", answer: "Reported revenue is 100 USD.", limitation: "", sourceIDs: ["S1"])
    #expect(OnDeviceCompanyQuestionAnswerer.isGrounded(valid, companyID: "META", sources: sources))
    var wrong = valid
    wrong.companyID = "OTHER"
    #expect(!OnDeviceCompanyQuestionAnswerer.isGrounded(wrong, companyID: "META", sources: sources))
    wrong = valid
    wrong.sourceIDs = ["INVENTED"]
    #expect(!OnDeviceCompanyQuestionAnswerer.isGrounded(wrong, companyID: "META", sources: sources))
    wrong = valid
    wrong.answer = "Revenue will reach 200 USD."
    #expect(!OnDeviceCompanyQuestionAnswerer.isGrounded(wrong, companyID: "META", sources: sources))
}

@Test
func negatedBusinessActivityIsNotCurrentThemeExposure() async throws {
    let company = CompanyCandidate(cik: "ABC", ticker: "ABC", name: "ABC Corp", filingDate: nil, filingURL: nil, relevance: 1)
    let result = try await GroundedCompanyFitEvaluator().evaluate(company: company, theme: "autonomous drones",
        searchTerms: [], evidence: ["SEC filing excerpt: We do not manufacture autonomous drones."], snapshot: nil)
    #expect(result.0 == .incidental)
}

@Test
func genericWordsFromAnAliasDoNotEstablishProductExposure() async throws {
    let company = CompanyCandidate(cik: "ABC", ticker: "ABC", name: "ABC Corp", filingDate: nil, filingURL: nil, relevance: 1)
    let result = try await GroundedCompanyFitEvaluator().evaluate(company: company, theme: "autonomous drones",
        searchTerms: ["unmanned aerial vehicles"],
        evidence: ["LSEG business description: The company manufactures combat vehicles and provides air build and support activities."], snapshot: nil)
    #expect(result.0 == .incidental)
}

@Test
func purchaseRejectsNonfiniteValuesAndFutureDates() {
    for purchase in [
        Purchase(ticker: "ABC", quantity: .infinity, price: 10, purchasedAt: .now),
        Purchase(ticker: "ABC", quantity: 1, price: .infinity, purchasedAt: .now),
        Purchase(ticker: "ABC", quantity: .greatestFiniteMagnitude, price: 10, purchasedAt: .now),
        Purchase(ticker: "ABC", quantity: 1, price: 10, purchasedAt: .now.addingTimeInterval(172_800)),
    ] {
        #expect(throws: StockAgentError.self) { try purchase.validated() }
    }
}

@Test
func bridgeDrainsResponsesLargerThanPipeCapacity() async throws {
    let result = try await LSEGBridgeProcess.run(executable: URL(fileURLWithPath: "/bin/zsh"),
        arguments: ["-c", "head -c 200000 /dev/zero"], directory: FileManager.default.temporaryDirectory, input: Data(), timeout: .seconds(3))
    #expect(result.0 == 0)
    #expect(result.1.count == 200_000)
}

@Test
func bridgeTimeoutAndCancellationAreBounded() async throws {
    await #expect(throws: StockAgentError.self) {
        try await LSEGBridgeProcess.run(executable: URL(fileURLWithPath: "/bin/sleep"),
            arguments: ["10"], directory: FileManager.default.temporaryDirectory, input: Data(), timeout: .milliseconds(80))
    }
    let task = Task {
        try await LSEGBridgeProcess.run(executable: URL(fileURLWithPath: "/bin/sleep"),
            arguments: ["10"], directory: FileManager.default.temporaryDirectory, input: Data())
    }
    try await Task.sleep(for: .milliseconds(80))
    task.cancel()
    await #expect(throws: CancellationError.self) { try await task.value }
}

private struct FilingPassageFetcher: DataFetching {
    let text: String
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        (Data(text.utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

@Test
func filingSearchRanksTopicCoverageInsteadOfOnlyTheFirstKeywordOccurrences() async throws {
    let generic = String(repeating: "Revenue disclosure for this period appears with other general company information in this annual report. ", count: 35)
    let relevant = "Our advertising placements are the principal source of revenue and monetization across the company's applications and services."
    let service = SECService(fetcher: FilingPassageFetcher(text: "İstanbul corporate disclosure. " + generic + relevant))
    let passages = try await service.filingEvidence(url: URL(string: "https://example.test/filing")!,
        query: "revenue advertising monetization", limit: 1)
    #expect(passages.count == 1)
    #expect(passages[0].contains("advertising placements"))
}

@Test
func sourceFallbackQuotesRetrievedPassagesWithoutInventingAnAnswer() {
    let text = "We generate revenue by selling advertising placements across our applications."
    let answer = NamedResearchQuery.sourceBoundFallback(question: "How does the company make money?",
        companyName: "Company", evidence: ["SEC filing excerpt: " + text])
    #expect(answer == "Related filing passages:\n\n“\(text)”")
    let facts = [FinancialFact(label: "Revenue", value: 100, unit: "USD", periodEnd: nil)]
    #expect(NamedResearchQuery.relevantFacts(question: "Who are its competitors?", facts: facts).isEmpty)
    #expect(NamedResearchQuery.relevantFacts(question: "What is its revenue?", facts: facts).count == 1)
}
