import Foundation

public actor YahooPriceService {
    private let fetcher: any DataFetching

    public init(fetcher: any DataFetching = URLSessionDataFetcher()) {
        self.fetcher = fetcher
    }

    public func dailyPrices(ticker rawTicker: String, starting startDate: Date? = nil) async throws -> [PricePoint] {
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
                URLQueryItem(name: "events", value: "history"),
                URLQueryItem(name: "includeAdjustedClose", value: "true"),
            ]
        } else {
            components.queryItems = [
                URLQueryItem(name: "range", value: "5y"),
                URLQueryItem(name: "interval", value: "1d"),
                URLQueryItem(name: "events", value: "history"),
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
        return points.sorted { $0.date < $1.date }
    }
}

public actor FREDMarketService {
    private let fetcher: any DataFetching

    private struct Definition: Sendable {
        enum Calculation: Sendable { case change, percentChange, yearOverYear }
        let id: String
        let label: String
        let unit: String
        let lookbackDays: Int
        let calculation: Calculation
    }

    private let definitions = [
        Definition(id: "DFF", label: "Effective federal funds rate", unit: "%", lookbackDays: 90, calculation: .change),
        Definition(id: "WALCL", label: "Federal Reserve assets", unit: "$T", lookbackDays: 91, calculation: .percentChange),
        Definition(id: "CPIAUCNS", label: "Consumer Price Index inflation", unit: "% YoY", lookbackDays: 35, calculation: .yearOverYear),
        Definition(id: "BAMLH0A0HYM2", label: "US high-yield option-adjusted spread", unit: "%", lookbackDays: 90, calculation: .change),
        Definition(id: "VIXCLS", label: "CBOE Volatility Index", unit: "", lookbackDays: 30, calculation: .change),
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
        let rate = indicators.first(where: { $0.id == "DFF" })?.tilt ?? .unavailable
        let balance = indicators.first(where: { $0.id == "WALCL" })?.tilt ?? .unavailable
        let label: String
        let stance: MarketIndicator.Tilt
        let summary: String
        if rate == .unavailable || balance == .unavailable {
            label = "Regime incomplete"
            stance = .unavailable
            summary = "Some rate or Fed data is missing, so the app cannot set a market view yet."
        } else if rate == .tolerant && balance == .tolerant {
            label = "Easing and expanding liquidity"
            stance = .tolerant
            summary = "Rates are falling and the Fed is adding liquidity. This usually helps growth stocks most."
        } else if rate == .defensive && balance == .defensive {
            label = "Tightening and contracting liquidity"
            stance = .defensive
            summary = "Rates are rising and the Fed is removing liquidity. Expensive or high-leverage growth stocks face more pressure."
        } else if rate == .neutral && balance == .neutral {
            label = "Neutral liquidity regime"
            stance = .neutral
            summary = "Rates and the Fed balance sheet are broadly stable. The market data does not favor growth or defense."
        } else {
            label = "Mixed liquidity regime"
            stance = .neutral
            summary = "Rates and Fed liquidity point in different directions. Neither growth nor defensive stocks has a clear advantage."
        }
        return MarketRegime(label: label, stance: stance, summary: summary, indicators: indicators)
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
            var rows = text.split(whereSeparator: \Character.isNewline).dropFirst().compactMap { line -> Observation? in
                let columns = line.split(separator: ",", omittingEmptySubsequences: false)
                guard columns.count >= 2, let value = Double(columns[1]),
                      let date = Self.date(String(columns[0])) else { return nil }
                return Observation(date: date, value: value)
            }
            if definition.calculation == .yearOverYear { rows = Self.yearOverYear(rows) }
            guard let latest = rows.last else { throw StockAgentError.unavailable("No observations.") }
            guard let result = Self.historicalChange(
                rows,
                lookbackDays: definition.lookbackDays,
                percent: definition.calculation == .percentChange
            ) else { throw StockAgentError.unavailable("Not enough observations.") }
            let percentile = Self.percentile(of: latest.value, in: rows)
            let tilt = Self.tilt(seriesID: definition.id, direction: result.direction, percentile: percentile)
            let verb: String
            if definition.calculation == .percentChange {
                verb = result.change > 0 ? "Expanding" : result.change < 0 ? "Contracting" : "Unchanged"
            } else {
                verb = result.change > 0 ? "Rising" : result.change < 0 ? "Falling" : "Unchanged"
            }
            let suffix = definition.calculation == .percentChange ? "%" : definition.id == "VIXCLS" ? " points" : " pp"
            return MarketIndicator(
                id: definition.id,
                label: definition.label,
                latest: latest.value,
                previous: result.previous.value,
                unit: definition.unit,
                asOf: latest.date,
                tilt: tilt,
                changeDescription: "\(verb) (\(result.change.formatted(.number.sign(strategy: .always()).precision(.fractionLength(2))))\(suffix))",
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
        let direction: Int
    }

    private static func historicalChange(
        _ observations: [Observation],
        lookbackDays: Int,
        percent: Bool
    ) -> ChangeResult? {
        guard observations.count >= 4, let latest = observations.last else { return nil }
        let cutoff = Calendar.current.date(byAdding: .year, value: -5, to: latest.date) ?? .distantPast
        var changes = [(Observation, Observation, Double)]()
        for (index, current) in observations.enumerated() where current.date >= cutoff {
            let target = Calendar.current.date(byAdding: .day, value: -lookbackDays, to: current.date) ?? current.date
            guard let previous = observations[..<index].last(where: { $0.date <= target }),
                  !percent || previous.value != 0 else { continue }
            let change = percent
                ? (current.value / previous.value - 1) * 100
                : current.value - previous.value
            changes.append((current, previous, change))
        }
        guard changes.count >= 4, let current = changes.last else { return nil }
        let distribution = changes.map(\.2).sorted()
        let lower = quantile(distribution, fraction: 0.25)
        let upper = quantile(distribution, fraction: 0.75)
        let direction: Int
        if current.2 < 0 && current.2 <= lower { direction = -1 }
        else if current.2 > 0 && current.2 >= upper { direction = 1 }
        else { direction = 0 }
        return ChangeResult(previous: current.1, change: current.2, direction: direction)
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

    private static func quantile(_ values: [Double], fraction: Double) -> Double {
        guard !values.isEmpty else { return 0 }
        let position = Double(values.count - 1) * fraction
        let lower = Int(position.rounded(.down))
        let upper = min(lower + 1, values.count - 1)
        let weight = position - Double(lower)
        return values[lower] * (1 - weight) + values[upper] * weight
    }

    private static func percentile(of latest: Double, in observations: [Observation]) -> Int? {
        guard let end = observations.last?.date else { return nil }
        let cutoff = Calendar.current.date(byAdding: .year, value: -5, to: end) ?? .distantPast
        let values = observations.filter { $0.date >= cutoff }.map(\.value)
        guard values.count >= 12 else { return nil }
        let below = values.filter { $0 < latest }.count
        let equal = values.filter { $0 == latest }.count
        return Int((100 * (Double(below) + Double(equal) / 2) / Double(values.count)).rounded())
    }

    private static func tilt(seriesID: String, direction: Int, percentile: Int?) -> MarketIndicator.Tilt {
        let elevated = (percentile ?? -1) >= 75
        let low = percentile.map { $0 <= 25 } ?? false
        switch seriesID {
        case "DFF":
            if elevated { return .defensive }
            if low { return .tolerant }
        case "WALCL":
            if direction < 0 { return .defensive }
            if direction > 0 { return .tolerant }
        case "CPIAUCNS":
            if elevated || direction > 0 { return .defensive }
            if low && direction < 0 { return .tolerant }
        case "BAMLH0A0HYM2", "VIXCLS":
            if elevated { return .defensive }
            if low { return .tolerant }
        default:
            break
        }
        return .neutral
    }

    private static func date(_ text: String) -> Date? {
        let parts = text.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return Calendar(identifier: .gregorian).date(
            from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12)
        )
    }
}
