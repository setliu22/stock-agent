import Foundation
import Testing
@testable import StockAgentCore

private func day(_ n: Int) -> Date { Date(timeIntervalSince1970: 1_735_689_600 + Double(n) * 86_400) }

@Test func actualPurchaseCostAnchorsReturnAndIncludesExecutionGain() {
    let points = PortfolioAnalytics.returnsOnCost(purchases: [.init(ticker: "A", quantity: 2, price: 80, purchasedAt: day(1))],
        priceHistory: ["A": [.init(date: day(0), close: 90), .init(date: day(1), close: 100), .init(date: day(2), close: 120)]])
    #expect(points.first?.value == 0)
    #expect(points.last?.value == 50)
    #expect(points.count == 3)
}

@Test func differentDatesAndPartialAdditionsUseActualInvestedCost() {
    let points = PortfolioAnalytics.returnsOnCost(purchases: [
        .init(ticker: "A", quantity: 1, price: 100, purchasedAt: day(1)),
        .init(ticker: "B", quantity: 2, price: 25, purchasedAt: day(2)),
        .init(ticker: "A", quantity: 1, price: 120, purchasedAt: day(2)),
    ], priceHistory: ["A": [.init(date: day(1), close: 100), .init(date: day(2), close: 120)],
                      "B": [.init(date: day(2), close: 30)]])
    let expected: Double = (300.0 / 270.0 - 1.0) * 100.0
    #expect(abs((points.last?.value ?? 0) - expected) < 0.000001)
}

@Test func extremeGainsAndLossesAreCostWeightedNotAveraged() {
    let points = PortfolioAnalytics.returnsOnCost(purchases: [
        .init(ticker: "A", quantity: 1, price: 10, purchasedAt: day(1)),
        .init(ticker: "B", quantity: 1, price: 1000, purchasedAt: day(1)),
    ], priceHistory: ["A": [.init(date: day(1), close: 100)], "B": [.init(date: day(1), close: 100)]])
    let expected: Double = (200.0 / 1010.0 - 1.0) * 100.0
    #expect(abs((points.last?.value ?? 0) - expected) < 0.000001)
}

@Test func splitAdjustmentPreservesCostAndOriginalTransaction() async throws {
    let original = Purchase(ticker: "A", quantity: 2, price: 1000, purchasedAt: day(1))
    let adjusted = PortfolioAnalytics.adjusted(original, for: [.init(date: day(2), ratio: 10)])
    #expect(adjusted.quantity == 20)
    #expect(adjusted.price == 100)
    #expect(original.quantity == 2)
    let sameDay = PortfolioAnalytics.adjusted(original, for: [.init(date: day(1), ratio: 10)])
    #expect(sameDay == original)
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = try PortfolioStore(databaseURL: folder.appendingPathComponent("test.db"))
    _ = try await store.record(original)
    try await store.importPriceHistory(ticker: "A", points: [.init(date: day(2), close: 110)], source: "Fixture",
        splits: [.init(date: day(2), ratio: 10)])
    let holding = try await store.holdings().first!
    #expect(holding.quantity == 20)
    #expect(abs(holding.returnPercent! - 10) < 0.000001)
    #expect(try await store.purchases().first?.quantity == 2)
    let reopened = try PortfolioStore(databaseURL: folder.appendingPathComponent("test.db"))
    #expect(try await reopened.purchases().count == 1)
    #expect(await reopened.priceHistory(ticker: "A").isEmpty)
    #expect(try await reopened.holdings().first?.currentPrice == nil)
}

@Test func tickerCollisionDoesNotEstablishCompanyIdentity() {
    #expect(!CompanyIdentity.matches("Airbus SE", "AAR Corp"))
    #expect(CompanyIdentity.matches("Meta Platforms Inc", "META PLATFORMS, INC."))
    #expect(!CompanyIdentity.matches("General Dynamics", "General Electric"))
}

@Test func thematicInferenceRequiresAnActualCommercialCapability() {
    #expect(!ThematicCompanyFitEvaluator.statesCommercialCapability("Increasing use of AI in our internal systems may create new attack surfaces."))
    #expect(!ThematicCompanyFitEvaluator.statesCommercialCapability("Its product categories include phones, watches and home accessories."))
    #expect(ThematicCompanyFitEvaluator.statesCommercialCapability("The company develops embedded inference processors for autonomous industrial systems."))
    #expect(!ThematicCompanyFitEvaluator.statesCommercialCapability("We no longer manufacture navigation sensors."))
}
