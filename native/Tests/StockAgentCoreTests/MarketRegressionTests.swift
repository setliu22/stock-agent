import Foundation
import Testing
@testable import StockAgentCore

private struct MarketFixtureFetcher: DataFetching {
    var unavailable = false
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        if unavailable { throw StockAgentError.unavailable("Fixture unavailable") }
        let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
        let series = components.queryItems!.first(where: { $0.name == "id" })!.value!
        let csv: String
        switch series {
        case "DFF": csv = "DATE,DFF\n2025-01-01,5.0\n2025-04-01,4.75\n2025-04-02,4.50"
        case "WALCL": csv = "DATE,WALCL\n2025-01-01,7000000\n2025-04-02,6900000"
        case "CPIAUCNS": csv = "DATE,CPIAUCNS\n2024-01-01,100\n2024-02-01,100\n2025-01-01,104\n2025-02-01,103"
        default: csv = "DATE,VALUE\n2025-01-01,20\n2025-04-02,21"
        }
        return (Data(csv.utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

@Test
func policyDescriptionUsesObservedDirectionNotRateLevel() async throws {
    let regime = await FREDMarketService(fetcher: MarketFixtureFetcher()).regime()
    #expect(regime.label.contains("Rates falling"))
    #expect(regime.label.contains("Fed assets shrinking"))
    #expect(regime.indicators.count == 7)
    #expect(regime.indicators.allSatisfy { $0.tilt == .neutral })
}

@Test
func inflationComparesConsecutiveMonthlyYearOverYearReadings() async throws {
    let regime = await FREDMarketService(fetcher: MarketFixtureFetcher()).regime()
    let cpi = try #require(regime.indicators.first(where: { $0.id == "CPIAUCNS" }))
    #expect(abs((cpi.latest ?? 0) - 3) < 0.0001)
    #expect(abs((cpi.previous ?? 0) - 4) < 0.0001)
    #expect(cpi.changeDescription.contains("-1.00 pp"))
}

@Test
func unavailableMacroDataDoesNotProduceAStance() async {
    let regime = await FREDMarketService(fetcher: MarketFixtureFetcher(unavailable: true)).regime()
    #expect(regime.label == "Policy data incomplete")
    #expect(regime.indicators.count == 7)
    #expect(regime.indicators.allSatisfy { $0.latest == nil })
}
