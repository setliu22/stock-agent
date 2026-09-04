import Foundation

public protocol DataFetching: Sendable {
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

public struct URLSessionDataFetcher: DataFetching {
    public init() {}

    public func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse else {
            throw StockAgentError.network("The data provider returned an unreadable response.")
        }
        return (data, response)
    }
}

public actor SECService {
    private let fetcher: any DataFetching
    private let userAgent: String
    private var lastRequest: ContinuousClock.Instant?
    private var tickerCache: [TickerRecord]?

    public init(
        userAgent: String = "Stock Agent local-research contact@example.com",
        fetcher: any DataFetching = URLSessionDataFetcher()
    ) {
        let trimmed = userAgent.trimmingCharacters(in: .whitespacesAndNewlines)
        self.userAgent = trimmed.isEmpty ? "Stock Agent local-research contact@example.com" : trimmed
        self.fetcher = fetcher
    }

    public nonisolated static func filingURL(cik: String, filing: Filing) -> URL? {
        let unpaddedCIK = String(Int(cik) ?? 0)
        let accession = filing.accessionNumber.replacingOccurrences(of: "-", with: "")
        guard !filing.primaryDocument.isEmpty else { return nil }
        return URL(
            string: "https://www.sec.gov/Archives/edgar/data/\(unpaddedCIK)/\(accession)/\(filing.primaryDocument)"
        )
    }

    public func searchFilings(query: String, limit: Int = 8) async throws -> [CompanyCandidate] {
        let clean = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { throw StockAgentError.validation("The research theme is empty.") }
        var components = URLComponents(string: "https://efts.sec.gov/LATEST/search-index")!
        components.queryItems = [
            URLQueryItem(name: "q", value: clean),
            URLQueryItem(name: "forms", value: "10-K,10-Q,8-K"),
            URLQueryItem(name: "from", value: "0"),
            URLQueryItem(name: "size", value: String(max(10, limit * 4))),
        ]
        guard let url = components.url else {
            throw StockAgentError.validation("The research theme could not be encoded.")
        }
        let data = try await get(url)
        let response: FilingSearchEnvelope
        do {
            response = try JSONDecoder().decode(FilingSearchEnvelope.self, from: data)
        } catch {
            throw StockAgentError.malformedResponse("SEC filing search returned an unexpected response.")
        }
        var seen = Set<String>()
        var candidates = [CompanyCandidate]()
        for hit in response.hits.hits.sorted(by: { $0.score > $1.score }) {
            guard let cik = hit.source.ciks.first else { continue }
            let display = hit.source.displayNames.first ?? "SEC registrant"
            let parsed = Self.parseDisplayName(display)
            let ticker = parsed.ticker
            guard !ticker.isEmpty, seen.insert(cik).inserted else { continue }
            let sourceID = hit.id.split(separator: ":", maxSplits: 1).map(String.init)
            let document = sourceID.count > 1 ? sourceID[1] : ""
            let accession = hit.source.accession.replacingOccurrences(of: "-", with: "")
            let unpaddedCIK = String(Int(cik) ?? 0)
            let filingURL = URL(
                string: "https://www.sec.gov/Archives/edgar/data/\(unpaddedCIK)/\(accession)/\(document)"
            )
            candidates.append(
                CompanyCandidate(
                    cik: cik,
                    ticker: ticker,
                    name: parsed.name,
                    filingDate: Self.date(hit.source.fileDate),
                    filingURL: filingURL,
                    relevance: hit.score
                )
            )
            if candidates.count >= limit { break }
        }
        return candidates
    }

    public func resolve(ticker rawTicker: String) async throws -> CompanyCandidate {
        let ticker = rawTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        let records = try await tickerRecords()
        guard let record = records.first(where: { $0.ticker.uppercased() == ticker }) else {
            throw StockAgentError.unavailable("SEC EDGAR could not resolve \(ticker).")
        }
        return CompanyCandidate(
            cik: String(format: "%010d", record.cik),
            ticker: record.ticker,
            name: record.name,
            filingDate: nil,
            filingURL: nil,
            relevance: 1
        )
    }

    public func snapshot(for candidate: CompanyCandidate) async throws -> CompanySnapshot {
        async let submissionsData = get(
            URL(string: "https://data.sec.gov/submissions/CIK\(candidate.cik).json")!
        )
        async let factsData = get(
            URL(string: "https://data.sec.gov/api/xbrl/companyfacts/CIK\(candidate.cik).json")!
        )
        let (submissionsRaw, factsRaw) = try await (submissionsData, factsData)
        let submissions: Submissions
        do {
            submissions = try JSONDecoder().decode(Submissions.self, from: submissionsRaw)
        } catch {
            throw StockAgentError.malformedResponse(
                "SEC filing metadata for \(candidate.ticker) was incomplete: \(Self.decodingMessage(error))"
            )
        }
        let companyFacts: CompanyFactsRoot
        do {
            companyFacts = try JSONDecoder().decode(CompanyFactsRoot.self, from: factsRaw)
        } catch {
            throw StockAgentError.malformedResponse(
                "SEC financial facts for \(candidate.ticker) were incomplete: \(Self.decodingMessage(error))"
            )
        }
        let filings = Self.recentFilings(from: submissions)
        let facts = Self.selectedFacts(from: companyFacts)
        let ticker = submissions.tickers.first ?? candidate.ticker
        let description = submissions.sicDescription.isEmpty
            ? "SEC reporting company"
            : submissions.sicDescription
        return CompanySnapshot(
            cik: candidate.cik,
            ticker: ticker,
            name: submissions.name.isEmpty ? candidate.name : submissions.name,
            description: description,
            facts: facts,
            recentFilings: filings
        )
    }

    public func filingEvidence(url: URL, query: String, limit: Int = 4) async throws -> [String] {
        let data = try await get(url)
        guard let html = String(data: data, encoding: .utf8) else { return [] }
        let text = Self.plainText(html)
        let terms = query.lowercased()
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .filter { $0.count >= 2 }
            .map(String.init)
        guard !terms.isEmpty else { return [] }
        if terms.contains(where: { $0.hasPrefix("risk") }),
           let section = Self.riskFactorSection(in: text) {
            return Self.sectionExcerpts(section, limit: limit)
        }
        let lower = text.lowercased()
        var ranges = [Range<String.Index>]()
        for term in terms {
            var cursor = lower.startIndex
            while cursor < lower.endIndex,
                  let range = lower.range(of: term, range: cursor..<lower.endIndex) {
                ranges.append(range)
                cursor = range.upperBound
                if ranges.count >= limit * 4 { break }
            }
        }
        let ordered = ranges.sorted { $0.lowerBound < $1.lowerBound }
        var snippets = [String]()
        var fingerprints = Set<String>()
        for range in ordered {
            let start = text.index(range.lowerBound, offsetBy: -220, limitedBy: text.startIndex) ?? text.startIndex
            let end = text.index(range.upperBound, offsetBy: 340, limitedBy: text.endIndex) ?? text.endIndex
            let snippet = String(text[start..<end])
                .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let fingerprint = String(snippet.lowercased().prefix(90))
            guard snippet.count > 80, fingerprints.insert(fingerprint).inserted else { continue }
            snippets.append(snippet)
            if snippets.count >= limit { break }
        }
        return snippets
    }

    private func tickerRecords() async throws -> [TickerRecord] {
        if let tickerCache { return tickerCache }
        let data = try await get(URL(string: "https://www.sec.gov/files/company_tickers_exchange.json")!)
        let envelope: TickerEnvelope
        do {
            envelope = try JSONDecoder().decode(TickerEnvelope.self, from: data)
        } catch {
            throw StockAgentError.malformedResponse("SEC ticker data was unreadable.")
        }
        let records = envelope.data.compactMap { row -> TickerRecord? in
            guard row.count >= 4, let cik = row[0].intValue else { return nil }
            return TickerRecord(
                cik: cik,
                name: row[1].stringValue ?? "",
                ticker: row[2].stringValue ?? "",
                exchange: row[3].stringValue ?? ""
            )
        }
        tickerCache = records
        return records
    }

    private func get(_ url: URL) async throws -> Data {
        if let lastRequest {
            let elapsed = lastRequest.duration(to: .now)
            if elapsed < .milliseconds(120) {
                try await Task.sleep(for: .milliseconds(120) - elapsed)
            }
        }
        var request = URLRequest(url: url, timeoutInterval: 25)
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("application/json,text/html;q=0.9,*/*;q=0.5", forHTTPHeaderField: "Accept")
        lastRequest = .now
        do {
            let (data, response) = try await fetcher.data(for: request)
            guard (200..<300).contains(response.statusCode) else {
                throw StockAgentError.network("SEC EDGAR returned HTTP \(response.statusCode).")
            }
            return data
        } catch let error as StockAgentError {
            throw error
        } catch {
            throw StockAgentError.network("Could not reach SEC EDGAR: \(error.localizedDescription)")
        }
    }

    private static func parseDisplayName(_ display: String) -> (name: String, ticker: String) {
        let pattern = #"^(.*?)\s+\(([A-Z0-9.\-]+)\)\s+\(CIK"#
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: display, range: NSRange(display.startIndex..., in: display)),
              let nameRange = Range(match.range(at: 1), in: display),
              let tickerRange = Range(match.range(at: 2), in: display) else {
            return (display, "")
        }
        return (
            String(display[nameRange]).trimmingCharacters(in: .whitespacesAndNewlines),
            String(display[tickerRange])
        )
    }

    private static func recentFilings(from submissions: Submissions) -> [Filing] {
        let recent = submissions.filings.recent
        let count = [recent.accessionNumber.count, recent.form.count, recent.filingDate.count,
                     recent.primaryDocument.count].min() ?? 0
        let filings = (0..<count).compactMap { index -> Filing? in
            let form = recent.form[index]
            guard ["10-K", "10-Q", "8-K", "DEF 14A"].contains(form) else { return nil }
            return Filing(
                accessionNumber: recent.accessionNumber[index],
                form: form,
                filedAt: date(recent.filingDate[index]),
                primaryDocument: recent.primaryDocument[index]
            )
        }
        var selected = [Filing]()
        for form in ["10-K", "10-Q", "DEF 14A"] {
            if let filing = filings.first(where: { $0.form == form }) { selected.append(filing) }
        }
        selected.append(contentsOf: filings.filter { $0.form == "8-K" }.prefix(8))
        return selected.sorted { ($0.filedAt ?? .distantPast) > ($1.filedAt ?? .distantPast) }
    }

    private static func selectedFacts(from root: CompanyFactsRoot) -> [FinancialFact] {
        let preferred = [
            "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
            "Revenues": "Revenue",
            "SalesRevenueNet": "Revenue",
            "NetIncomeLoss": "Net income",
            "Assets": "Total assets",
            "Liabilities": "Total liabilities",
            "CashAndCashEquivalentsAtCarryingValue": "Cash and equivalents",
            "LongTermDebtAndFinanceLeaseObligationsCurrent": "Current debt",
            "LongTermDebtNoncurrent": "Long-term debt",
            "OperatingIncomeLoss": "Operating income",
        ]
        let concepts = root.facts["us-gaap"] ?? [:]
        var labels = Set<String>()
        var output = [FinancialFact]()
        for (conceptID, preferredLabel) in preferred {
            guard let concept = concepts[conceptID], labels.insert(preferredLabel).inserted else { continue }
            var observations = concept.units.flatMap { unit, values in
                values.map { (unit, $0) }
            }
            observations = observations.filter { observation in
                ["10-K", "10-Q"].contains(observation.1.form ?? "")
                    && observation.1.value.isFinite
            }
            guard let latest = observations.max(by: {
                ($0.1.end ?? "") < ($1.1.end ?? "")
            }) else { continue }
            output.append(
                FinancialFact(
                    label: preferredLabel,
                    value: latest.1.value,
                    unit: latest.0,
                    periodEnd: latest.1.end.flatMap(date)
                )
            )
        }
        return output
    }

    private static func plainText(_ html: String) -> String {
        html
            .replacingOccurrences(of: #"(?is)<script.*?</script>"#, with: " ", options: .regularExpression)
            .replacingOccurrences(of: #"(?is)<style.*?</style>"#, with: " ", options: .regularExpression)
            .replacingOccurrences(of: #"(?s)<[^>]+>"#, with: " ", options: .regularExpression)
            .replacingOccurrences(of: "&nbsp;", with: " ")
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: "&#160;", with: " ")
            .replacingOccurrences(of: "&#8226;", with: "•")
            .replacingOccurrences(of: "&#8217;", with: "’")
            .replacingOccurrences(of: "&#8212;", with: "—")
            .replacingOccurrences(of: "&#8211;", with: "–")
            .replacingOccurrences(of: "&#8220;", with: "“")
            .replacingOccurrences(of: "&#8221;", with: "”")
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
    }

    private static func riskFactorSection(in text: String) -> String? {
        let source = text as NSString
        guard let startPattern = try? NSRegularExpression(
            pattern: #"(?i)item\s*1a[.\s:–—-]*risk\s*factors"#
        ), let endPattern = try? NSRegularExpression(
            pattern: #"(?i)item\s*1b[.\s:–—-]"#
        ) else { return nil }
        let fullRange = NSRange(location: 0, length: source.length)
        var best: NSRange?
        for match in startPattern.matches(in: text, range: fullRange) {
            let searchStart = NSMaxRange(match.range)
            guard searchStart < source.length,
                  let end = endPattern.firstMatch(
                    in: text,
                    range: NSRange(location: searchStart, length: source.length - searchStart)
                  ) else { continue }
            let candidate = NSRange(location: match.range.location, length: end.range.location - match.range.location)
            guard candidate.length > 1_000 else { continue }
            if best == nil || candidate.length > best!.length { best = candidate }
        }
        guard let best else { return nil }
        var section = source.substring(with: best)
        if let summary = section.range(of: "Summary Risk Factors", options: .caseInsensitive) {
            section = String(section[summary.lowerBound...])
            let heading = "Risks Related to Our Product Offerings"
            if let firstHeading = section.range(of: heading, options: .caseInsensitive),
               let secondHeading = section.range(
                of: heading,
                options: .caseInsensitive,
                range: firstHeading.upperBound..<section.endIndex
               ) {
                section = String(section[..<secondHeading.lowerBound])
            }
        }
        return section
    }

    private static func sectionExcerpts(_ section: String, limit: Int) -> [String] {
        let clean = section
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return [] }
        let useful = String(clean.prefix(5_000))
        var excerpts = [String]()
        var cursor = useful.startIndex
        while cursor < useful.endIndex, excerpts.count < max(1, limit) {
            let tentative = useful.index(cursor, offsetBy: 900, limitedBy: useful.endIndex) ?? useful.endIndex
            var end = tentative
            if tentative < useful.endIndex {
                let window = useful[cursor..<tentative]
                if let boundary = window.lastIndex(where: { $0 == ";" || $0 == "." || $0 == "•" }),
                   useful.distance(from: cursor, to: boundary) > 500 {
                    end = useful.index(after: boundary)
                }
            }
            let excerpt = String(useful[cursor..<end]).trimmingCharacters(in: .whitespacesAndNewlines)
            if excerpt.count > 80 { excerpts.append(excerpt) }
            cursor = end
        }
        return excerpts
    }

    private static func decodingMessage(_ error: Error) -> String {
        switch error {
        case DecodingError.keyNotFound(let key, let context):
            return "missing \(key.stringValue) at \(context.codingPath.map(\.stringValue).joined(separator: "."))"
        case DecodingError.typeMismatch(_, let context),
             DecodingError.valueNotFound(_, let context),
             DecodingError.dataCorrupted(let context):
            let path = context.codingPath.map(\.stringValue).joined(separator: ".")
            return path.isEmpty ? context.debugDescription : "\(context.debugDescription) at \(path)"
        default:
            return error.localizedDescription
        }
    }

    private static func date(_ text: String) -> Date? {
        let parts = text.prefix(10).split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return Calendar(identifier: .gregorian).date(
            from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12)
        )
    }
}

private struct FilingSearchEnvelope: Decodable {
    let hits: FilingHits
}

private struct FilingHits: Decodable {
    let hits: [FilingHit]
}

private struct FilingHit: Decodable {
    let id: String
    let score: Double
    let source: FilingSource

    enum CodingKeys: String, CodingKey {
        case id = "_id"
        case score = "_score"
        case source = "_source"
    }
}

private struct FilingSource: Decodable {
    let ciks: [String]
    let displayNames: [String]
    let fileDate: String
    let accession: String

    enum CodingKeys: String, CodingKey {
        case ciks
        case displayNames = "display_names"
        case fileDate = "file_date"
        case accession = "adsh"
    }
}

private struct TickerRecord: Sendable {
    let cik: Int
    let name: String
    let ticker: String
    let exchange: String
}

private struct TickerEnvelope: Decodable {
    let fields: [String]
    let data: [[JSONScalar]]
}

private enum JSONScalar: Decodable {
    case int(Int)
    case string(String)
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let int = try? container.decode(Int.self) { self = .int(int) }
        else { self = .string(try container.decode(String.self)) }
    }

    var intValue: Int? {
        if case .int(let value) = self { return value }
        return nil
    }

    var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }
}

private struct Submissions: Decodable {
    let name: String
    let tickers: [String]
    let sicDescription: String
    let filings: SubmissionFilings
}

private struct SubmissionFilings: Decodable {
    let recent: RecentFilings
}

private struct RecentFilings: Decodable {
    let accessionNumber: [String]
    let filingDate: [String]
    let form: [String]
    let primaryDocument: [String]
}

private struct CompanyFactsRoot: Decodable {
    let facts: [String: [String: CompanyFactConcept]]
}

private struct CompanyFactConcept: Decodable {
    let label: String?
    let units: [String: [CompanyFactObservation]]
}

private struct CompanyFactObservation: Decodable {
    let end: String?
    let value: Double
    let form: String?

    enum CodingKeys: String, CodingKey {
        case end
        case value = "val"
        case form
    }
}
