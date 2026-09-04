import Foundation

public enum PortfolioImporter {
    private static let containers = [
        "holdings", "positions", "purchases", "assets", "stocks", "securities", "equities",
        "portfolio", "data",
    ]
    private static let tickerKeys = ["ticker", "symbol", "ric"]
    private static let quantityKeys = ["quantity", "shares", "units", "qty"]
    private static let priceKeys = [
        "purchaseprice", "purchasepricepershare", "averagecost", "averagecostpershare",
        "avgcost", "costbasis", "entryprice", "price",
    ]
    private static let dateKeys = ["purchasedat", "purchasedate", "acquiredat", "date"]

    public static func parseJSON(_ rawText: String, now: Date = .now) throws -> [Purchase] {
        var text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("```") {
            let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
            if lines.count >= 3 {
                text = lines.dropFirst().dropLast().joined(separator: "\n")
            }
        }
        guard let jsonStart = text.firstIndex(where: { $0 == "{" || $0 == "[" }) else {
            throw StockAgentError.validation("Paste a JSON object or list of positions.")
        }
        let data = Data(text[jsonStart...].utf8)
        let payload: Any
        do {
            payload = try JSONSerialization.jsonObject(with: data)
        } catch {
            throw StockAgentError.validation("The portfolio JSON could not be read.")
        }
        let records = try positionRecords(payload)
        guard !records.isEmpty else {
            throw StockAgentError.validation("The portfolio JSON contains no positions.")
        }
        return try records.enumerated().map { index, record in
            let normalized = Dictionary(uniqueKeysWithValues: record.map { (key($0.key), $0.value) })
            guard let ticker = string(first(normalized, keys: tickerKeys)), !ticker.isEmpty else {
                throw StockAgentError.validation("Position \(index + 1): ticker or symbol is missing.")
            }
            guard let quantity = number(first(normalized, keys: quantityKeys)), quantity > 0 else {
                throw StockAgentError.validation("Position \(index + 1) (\(ticker)): shares must be greater than zero.")
            }
            guard let price = number(first(normalized, keys: priceKeys)), price >= 0 else {
                throw StockAgentError.validation("Position \(index + 1) (\(ticker)): purchase price is required.")
            }
            let rawDate = string(first(normalized, keys: dateKeys))
            let date = rawDate.flatMap(parseDate) ?? now
            var note = string(first(normalized, keys: ["note", "notes"])) ?? ""
            if rawDate == nil {
                note += note.isEmpty ? "Imported; purchase date unavailable" : " | Imported; purchase date unavailable"
            }
            return Purchase(
                ticker: ticker,
                quantity: quantity,
                price: price,
                purchasedAt: date,
                note: note
            )
        }
    }

    public static func parsePriceCSV(_ data: Data) throws -> [PricePoint] {
        guard let text = String(data: data, encoding: .utf8) else {
            throw StockAgentError.validation("The CSV must use UTF-8 text.")
        }
        let rows = text.split(whereSeparator: \Character.isNewline).map {
            $0.split(separator: ",", omittingEmptySubsequences: false).map {
                $0.trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: "\"")))
            }
        }
        guard let header = rows.first else {
            throw StockAgentError.validation("The CSV is empty.")
        }
        let normalized = header.map(key)
        guard let dateIndex = normalized.firstIndex(where: { ["date", "timestamp"].contains($0) }),
              let closeIndex = normalized.firstIndex(where: { ["close", "adjustedclose", "price"].contains($0) }) else {
            throw StockAgentError.validation("The CSV needs Date and Close columns.")
        }
        let points = rows.dropFirst().compactMap { row -> PricePoint? in
            guard dateIndex < row.count, closeIndex < row.count,
                  let date = parseDate(row[dateIndex]), let close = Double(row[closeIndex]), close >= 0 else {
                return nil
            }
            return PricePoint(date: date, close: close)
        }.sorted { $0.date < $1.date }
        guard !points.isEmpty else {
            throw StockAgentError.validation("The CSV contains no valid price rows.")
        }
        return points
    }

    private static func positionRecords(_ payload: Any) throws -> [[String: Any]] {
        if let records = payload as? [[String: Any]] { return records }
        guard let object = payload as? [String: Any] else {
            throw StockAgentError.validation("Portfolio positions must be JSON objects.")
        }
        let normalized = Dictionary(uniqueKeysWithValues: object.map { (key($0.key), $0.value) })
        for container in containers {
            if let records = normalized[container] as? [[String: Any]] { return records }
        }
        if tickerKeys.contains(where: { normalized[$0] != nil }) { return [object] }
        throw StockAgentError.validation("No portfolio positions were found in the JSON.")
    }

    private static func first(_ values: [String: Any], keys: [String]) -> Any? {
        keys.lazy.compactMap { values[$0] }.first
    }

    private static func key(_ value: String) -> String {
        String(value.lowercased().filter { $0.isLetter || $0.isNumber })
    }

    private static func string(_ value: Any?) -> String? {
        if let value = value as? String {
            let result = value.trimmingCharacters(in: .whitespacesAndNewlines)
            return result.isEmpty ? nil : result
        }
        return nil
    }

    private static func number(_ value: Any?) -> Double? {
        if let value = value as? NSNumber { return value.doubleValue }
        if let value = value as? String {
            return Double(value.replacingOccurrences(of: ",", with: "").replacingOccurrences(of: "$", with: ""))
        }
        return nil
    }

    private static func parseDate(_ value: String) -> Date? {
        let pieces = value.prefix(10).split(separator: "-").compactMap { Int($0) }
        guard pieces.count == 3 else { return nil }
        return Calendar(identifier: .gregorian).date(
            from: DateComponents(year: pieces[0], month: pieces[1], day: pieces[2], hour: 12)
        )
    }
}
