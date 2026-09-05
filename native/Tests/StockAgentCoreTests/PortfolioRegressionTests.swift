import Foundation
import Testing
@testable import StockAgentCore

private func portfolioDate(_ day: Int, month: Int = 1, hour: Int = 12) -> Date {
    Calendar(identifier: .gregorian).date(
        from: DateComponents(year: 2025, month: month, day: day, hour: hour)
    )!
}


@Test(arguments: [
    #"{"ticker":"ABC","shares":true,"price":100,"date":"2025-01-02"}"#,
    #"{"ticker":"ABC","shares":1,"price":false,"date":"2025-01-02"}"#,
    #"{"ticker":"ABC","shares":1,"price":"Infinity","date":"2025-01-02"}"#,
    #"{"ticker":"ABC","shares":1,"price":100,"date":"2025-02-30"}"#,
    #"{"ticker":"ABC","shares":1,"price":100,"date":"2025-13-01"}"#,
    #"{"ticker":"ABC","shares":1,"Shares":2,"price":100,"date":"2025-01-02"}"#,
    #"{"ticker":"ABC","shares":2,"costBasis":200,"date":"2025-01-02"}"#,
])
func portfolioImportRejectsAmbiguousOrInvalidPositions(_ json: String) {
    #expect(throws: StockAgentError.self) {
        try PortfolioImporter.parseJSON(json)
    }
}

@Test
func invalidRefreshDoesNotReplaceSessionPrices() async throws {
    let folder = FileManager.default.temporaryDirectory
        .appendingPathComponent("stock-agent-price-override-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = try PortfolioStore(databaseURL: folder.appendingPathComponent("portfolio.db"))
    _ = try await store.record(Purchase(ticker: "ABC", quantity: 1, price: 90, purchasedAt: portfolioDate(2)))
    try await store.importPriceHistory(ticker: "ABC", points: [PricePoint(date: portfolioDate(2), close: 100)], source: "Test")
    let manual = try await store.holdings()
    #expect(manual.first?.currentPrice == 100)

    await #expect(throws: StockAgentError.self) {
        try await store.importPriceHistory(ticker: "ABC", points: [PricePoint(date: portfolioDate(3), close: .infinity)], source: "Test")
    }
    let afterInvalidImport = try await store.holdings()
    #expect(afterInvalidImport.first?.currentPrice == 100)

    try await store.importPriceHistory(ticker: "ABC", points: [PricePoint(date: portfolioDate(3), close: 110)], source: "Test")
    let refreshed = try await store.holdings()
    #expect(refreshed.first?.currentPrice == 110)
}
