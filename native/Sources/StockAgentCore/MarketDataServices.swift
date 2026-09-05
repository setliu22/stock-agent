import Foundation

public struct StockSplit: Codable, Sendable {
    public let date: Date
    public let ratio: Double
    public init(date: Date, ratio: Double) { self.date = date; self.ratio = ratio }
}

public struct MarketPriceHistory: Sendable {
    public let prices: [PricePoint]
    public let splits: [StockSplit]
}

public actor YahooPriceService {
    private let fetcher: any DataFetching

    public init(fetcher: any DataFetching = URLSessionDataFetcher()) {
        self.fetcher = fetcher
    }

    public func dailyPrices(ticker rawTicker: String, starting startDate: Date? = nil) async throws -> [PricePoint] {
        try await history(ticker: rawTicker, starting: startDate).prices
    }

    public func history(ticker rawTicker: String, starting startDate: Date? = nil) async throws -> MarketPriceHistory {
        let ticker = rawTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else { throw StockAgentError.validation("Enter a ticker.") }

        let providerTicker = ticker.replacingOccurrences(of: ".", with: "-")
        let encodedTicker = providerTicker.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed)
            ?? providerTicker
        var components = URLComponents(
            string: "https://query2.finance.yahoo.com/v8/finance/chart/\(encodedTicker)"
        )!
        if let startDate {
            let paddedStart = Calendar(identifier: .gregorian).date(
                byAdding: .day,
                value: -7,
                to: startDate
            ) ?? startDate
            components.queryItems = [
                URLQueryItem(name: "period1", value: String(Int(paddedStart.timeIntervalSince1970))),
                URLQueryItem(name: "period2", value: String(Int(Date.now.addingTimeInterval(86_400).timeIntervalSince1970))),
                URLQueryItem(name: "interval", value: "1d"),
                URLQueryItem(name: "events", value: "splits"),
                URLQueryItem(name: "includeAdjustedClose", value: "true"),
            ]
        } else {
            components.queryItems = [
                URLQueryItem(name: "range", value: "5y"),
                URLQueryItem(name: "interval", value: "1d"),
                URLQueryItem(name: "events", value: "splits"),
                URLQueryItem(name: "includeAdjustedClose", value: "true"),
            ]
        }
        var request = URLRequest(url: components.url!, timeoutInterval: 30)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
            forHTTPHeaderField: "User-Agent"
        )
        let (data, response): (Data, HTTPURLResponse)
        do {
            (data, response) = try await fetcher.data(for: request)
        } catch {
            throw StockAgentError.network("Could not download prices for \(ticker): \(error.localizedDescription)")
        }
        guard (200..<300).contains(response.statusCode) else {
            throw StockAgentError.network("The price service returned HTTP \(response.statusCode) for \(ticker).")
        }
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw StockAgentError.malformedResponse("The price response was unreadable.")
        }
        guard let chart = root["chart"] as? [String: Any] else {
            throw StockAgentError.malformedResponse("The price response did not contain chart data.")
        }
        if let error = chart["error"] as? [String: Any] {
            let message = error["description"] as? String ?? "No price history was returned."
            throw StockAgentError.unavailable(message)
        }
        guard let result = (chart["result"] as? [[String: Any]])?.first,
              let timestamps = result["timestamp"] as? [NSNumber],
              let indicators = result["indicators"] as? [String: Any],
              let quote = (indicators["quote"] as? [[String: Any]])?.first,
              let closes = quote["close"] as? [Any] else {
            throw StockAgentError.malformedResponse("No daily prices were returned for \(ticker).")
        }
        let count = min(timestamps.count, closes.count)
        let points = (0..<count).compactMap { index -> PricePoint? in
            guard let close = closes[index] as? NSNumber,
                  close.doubleValue.isFinite,
                  close.doubleValue > 0 else { return nil }
            return PricePoint(
                date: Date(timeIntervalSince1970: timestamps[index].doubleValue),
                close: close.doubleValue
            )
        }
        guard !points.isEmpty else {
            throw StockAgentError.unavailable("No usable daily prices were returned for \(ticker).")
        }
        let events = result["events"] as? [String: Any]
        let rawSplits = events?["splits"] as? [String: [String: Any]] ?? [:]
        let splits = try rawSplits.values.map { event -> StockSplit in
            guard let date = event["date"] as? NSNumber,
                  let numerator = event["numerator"] as? NSNumber,
                  let denominator = event["denominator"] as? NSNumber,
                  denominator.doubleValue > 0 else {
                throw StockAgentError.malformedResponse("Stock split data for \(ticker) was incomplete; prices were not applied.")
            }
            let ratio = numerator.doubleValue / denominator.doubleValue
            guard ratio.isFinite, ratio > 0 else { throw StockAgentError.malformedResponse("Invalid stock split ratio for \(ticker).") }
            return StockSplit(date: Date(timeIntervalSince1970: date.doubleValue), ratio: ratio)
        }
        return MarketPriceHistory(prices: points.sorted { $0.date < $1.date }, splits: splits)
    }
}

public actor FREDMarketService {
    private let fetcher: any DataFetching

    private struct Definition: Sendable {
        enum Calculation: Sendable { case change, percentChange, yearOverYear }
        enum Comparison: Sendable { case days(Int), previousMonth }
        let id: String
        let label: String
        let unit: String
        let comparison: Comparison
        let calculation: Calculation
    }

    private let definitions = [
        Definition(id: "DFF", label: "Effective federal funds rate", unit: "%", comparison: .days(90), calculation: .change),
        Definition(id: "DGS10", label: "10-year Treasury yield", unit: "%", comparison: .days(90), calculation: .change),
        Definition(id: "WALCL", label: "Federal Reserve assets", unit: "$T", comparison: .days(91), calculation: .percentChange),
        Definition(id: "CPIAUCNS", label: "Consumer Price Index inflation", unit: "% YoY", comparison: .previousMonth, calculation: .yearOverYear),
        Definition(id: "UNRATE", label: "Unemployment rate", unit: "%", comparison: .previousMonth, calculation: .change),
        Definition(id: "BAMLH0A0HYM2", label: "US high-yield option-adjusted spread", unit: "%", comparison: .days(90), calculation: .change),
        Definition(id: "VIXCLS", label: "CBOE Volatility Index", unit: "", comparison: .days(30), calculation: .change),
    ]

    public init(fetcher: any DataFetching = URLSessionDataFetcher()) {
        self.fetcher = fetcher
    }

    public func regime() async -> MarketRegime {
        var indicators = [MarketIndicator]()
        await withTaskGroup(of: MarketIndicator.self) { group in
            for definition in definitions {
                group.addTask { await self.indicator(definition) }
            }
            for await result in group { indicators.append(result) }
        }
        indicators.sort {
            let order = definitions.map(\.id)
            return (order.firstIndex(of: $0.id) ?? 99) < (order.firstIndex(of: $1.id) ?? 99)
        }
        guard let rateChange = indicators.first(where: { $0.id == "DFF" })?.change,
              let balanceChange = indicators.first(where: { $0.id == "WALCL" })?.change else {
            return MarketRegime(
                label: "Policy data incomplete",
                stance: .unavailable,
                summary: "The rate or Fed asset comparison is unavailable. Other signals are shown below.",
                indicators: indicators
            )
        }
        let rates = abs(rateChange) < 0.05 ? "Rates little changed" : rateChange > 0 ? "Rates rising" : "Rates falling"
        let assets = balanceChange > 0 ? "Fed assets expanding" : balanceChange < 0 ? "Fed assets shrinking" : "Fed assets unchanged"
        return MarketRegime(
            label: "\(rates) · \(assets)",
            stance: .neutral,
            summary: "Effective rates over 90 days and Fed assets over 13 weeks. Fed assets are only one part of liquidity; read these alongside inflation, labor and credit conditions below.",
            indicators: indicators
        )
    }

    private func indicator(_ definition: Definition) async -> MarketIndicator {
        var components = URLComponents(string: "https://fred.stlouisfed.org/graph/fredgraph.csv")!
        components.queryItems = [URLQueryItem(name: "id", value: definition.id)]
        var request = URLRequest(url: components.url!, timeoutInterval: 20)
        request.setValue("Stock Agent local research", forHTTPHeaderField: "User-Agent")
        do {
            let (data, response) = try await fetcher.data(for: request)
            guard (200..<300).contains(response.statusCode),
                  let text = String(data: data, encoding: .utf8) else {
                throw StockAgentError.network("FRED did not return data.")
            }
            var valuesByDate = [Date: Double]()
            for line in text.split(whereSeparator: \Character.isNewline).dropFirst() {
                let columns = line.split(separator: ",", omittingEmptySubsequences: false)
                guard columns.count >= 2, let value = Double(columns[1]), value.isFinite,
                      let date = Self.date(String(columns[0])) else { continue }
                valuesByDate[date] = value
            }
            var rows = valuesByDate.map { Observation(date: $0.key, value: $0.value) }.sorted { $0.date < $1.date }
            if definition.calculation == .yearOverYear { rows = Self.yearOverYear(rows) }
            guard let latest = rows.last else { throw StockAgentError.unavailable("No observations.") }
            let result = Self.observedChange(
                rows,
                comparison: definition.comparison,
                percent: definition.calculation == .percentChange
            )
            let suffix = definition.calculation == .percentChange ? "%" : definition.id == "VIXCLS" ? " points" : " pp"
            let changeDescription = result.map { result in
                let comparisonDate = definition.calculation == .yearOverYear
                    ? result.previous.date.formatted(.dateTime.month(.abbreviated).year())
                    : result.previous.date.formatted(.dateTime.month(.abbreviated).day().year())
                return "\(result.change.formatted(.number.sign(strategy: .always()).precision(.fractionLength(2))))\(suffix) · vs \(comparisonDate)"
            } ?? "Comparison unavailable"
            return MarketIndicator(
                id: definition.id,
                label: definition.label,
                latest: latest.value,
                previous: result?.previous.value,
                unit: definition.unit,
                asOf: latest.date,
                tilt: .neutral,
                changeDescription: changeDescription,
                source: "FRED \(definition.id)"
            )
        } catch {
            return MarketIndicator(
                id: definition.id,
                label: definition.label,
                latest: nil,
                previous: nil,
                unit: definition.unit,
                asOf: nil,
                tilt: .unavailable,
                changeDescription: "Could not refresh",
                source: "FRED \(definition.id)"
            )
        }
    }

    private struct Observation: Sendable {
        let date: Date
        let value: Double
    }

    private struct ChangeResult: Sendable {
        let previous: Observation
        let change: Double
    }

    private static func observedChange(
        _ observations: [Observation],
        comparison: Definition.Comparison,
        percent: Bool
    ) -> ChangeResult? {
        guard observations.count >= 2, let latest = observations.last else { return nil }
        let calendar = Calendar(identifier: .gregorian)
        let previous: Observation?
        switch comparison {
        case .days(let days):
            guard let target = calendar.date(byAdding: .day, value: -days, to: latest.date) else { return nil }
            previous = observations.dropLast().last(where: { $0.date <= target })
        case .previousMonth:
            guard let target = calendar.date(byAdding: .month, value: -1, to: latest.date) else { return nil }
            previous = observations.dropLast().last(where: { calendar.isDate($0.date, equalTo: target, toGranularity: .month) })
        }
        guard let previous, !percent || previous.value != 0 else { return nil }
        let change = percent ? (latest.value / previous.value - 1) * 100 : latest.value - previous.value
        return ChangeResult(previous: previous, change: change)
    }

    private static func yearOverYear(_ observations: [Observation]) -> [Observation] {
        let calendar = Calendar(identifier: .gregorian)
        let byMonth = Dictionary(uniqueKeysWithValues: observations.map {
            let components = calendar.dateComponents([.year, .month], from: $0.date)
            return ((components.year ?? 0) * 100 + (components.month ?? 0), $0)
        })
        return observations.compactMap { current in
            let components = calendar.dateComponents([.year, .month], from: current.date)
            let priorKey = ((components.year ?? 0) - 1) * 100 + (components.month ?? 0)
            guard let prior = byMonth[priorKey], prior.value != 0 else { return nil }
            return Observation(date: current.date, value: (current.value / prior.value - 1) * 100)
        }
    }

    private static func date(_ text: String) -> Date? {
        let parts = text.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return Calendar(identifier: .gregorian).date(
            from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12)
        )
    }
}
