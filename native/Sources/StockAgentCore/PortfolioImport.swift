import Foundation
import CoreFoundation

public enum PortfolioImporter {
    private static let containers = [
        "holdings", "positions", "purchases", "assets", "stocks", "securities", "equities",
        "portfolio", "data",
    ]
    private static let tickerKeys = ["ticker", "symbol", "ric"]
    private static let quantityKeys = ["quantity", "shares", "units", "qty"]
    private static let priceKeys = [
        "purchaseprice", "purchasepricepershare", "averagecost", "averagecostpershare",
        "avgcost", "entryprice", "price",
    ]
    private static let dateKeys = ["purchasedat", "purchasedate", "acquiredat", "date"]

    public static func parseJSON(_ rawText: String) throws -> [Purchase] {
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
            let normalized = try normalizedKeys(record)
            guard let ticker = string(first(normalized, keys: tickerKeys)), !ticker.isEmpty else {
                throw StockAgentError.validation("Position \(index + 1): ticker or symbol is missing.")
            }
            guard let quantity = number(first(normalized, keys: quantityKeys)), quantity > 0 else {
                throw StockAgentError.validation("Position \(index + 1) (\(ticker)): shares must be greater than zero.")
            }
            guard let price = number(first(normalized, keys: priceKeys)), price >= 0 else {
                throw StockAgentError.validation("Position \(index + 1) (\(ticker)): purchase price is required.")
            }
            guard let rawDate = string(first(normalized, keys: dateKeys)),
                  let date = parseDate(rawDate) else {
                throw StockAgentError.validation(
                    "Position \(index + 1) (\(ticker)): purchase date is required in YYYY-MM-DD format."
                )
            }
            let note = string(first(normalized, keys: ["note", "notes"])) ?? ""
            return try Purchase(
                ticker: ticker,
                quantity: quantity,
                price: price,
                purchasedAt: date,
                note: note
            ).validated()
        }
    }

    private static func positionRecords(_ payload: Any) throws -> [[String: Any]] {
        if let records = payload as? [[String: Any]] { return records }
        guard let object = payload as? [String: Any] else {
            throw StockAgentError.validation("Portfolio positions must be JSON objects.")
        }
        let normalized = try normalizedKeys(object)
        for container in containers {
            if let records = normalized[container] as? [[String: Any]] { return records }
        }
        if tickerKeys.contains(where: { normalized[$0] != nil }) { return [object] }
        throw StockAgentError.validation("No portfolio positions were found in the JSON.")
    }

    private static func first(_ values: [String: Any], keys: [String]) -> Any? {
        keys.lazy.compactMap { values[$0] }.first
    }

    private static func normalizedKeys(_ values: [String: Any]) throws -> [String: Any] {
        var result = [String: Any]()
        for (name, value) in values {
            let normalized = key(name)
            guard result[normalized] == nil else {
                throw StockAgentError.validation("The JSON has duplicate fields named \(name).")
            }
            result[normalized] = value
        }
        return result
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
        if let value = value as? NSNumber {
            guard CFGetTypeID(value) != CFBooleanGetTypeID(), value.doubleValue.isFinite else { return nil }
            return value.doubleValue
        }
        if let value = value as? String {
            guard let number = Double(value.replacingOccurrences(of: ",", with: "").replacingOccurrences(of: "$", with: "")),
                  number.isFinite else { return nil }
            return number
        }
        return nil
    }

    private static func parseDate(_ value: String) -> Date? {
        let rawDate = String(value.prefix(10))
        let tokens = rawDate.split(separator: "-", omittingEmptySubsequences: false)
        guard tokens.count == 3, tokens[0].count == 4, tokens[1].count == 2, tokens[2].count == 2 else { return nil }
        let pieces = tokens.compactMap { Int($0) }
        guard pieces.count == 3, pieces[0] >= 1900 else { return nil }
        let calendar = Calendar(identifier: .gregorian)
        guard let date = calendar.date(
            from: DateComponents(year: pieces[0], month: pieces[1], day: pieces[2], hour: 12)
        ) else { return nil }
        let actual = calendar.dateComponents([.year, .month, .day], from: date)
        guard actual.year == pieces[0], actual.month == pieces[1], actual.day == pieces[2] else { return nil }
        return date
    }
}
