import Foundation
import FoundationModels
import NaturalLanguage

private enum SemanticEvidence {
    static func passages(
        in text: String,
        closestTo theme: String,
        aliases: [String] = [],
        limit: Int = 3
    ) -> [String] {
        let tokenizer = NLTokenizer(unit: .sentence)
        tokenizer.string = text
        var sentences = [String]()
        tokenizer.enumerateTokens(in: text.startIndex..<text.endIndex) { range, _ in
            let sentence = String(text[range]).trimmingCharacters(in: .whitespacesAndNewlines)
            if sentence.count >= 20 { sentences.append(sentence) }
            return true
        }
        if sentences.isEmpty, !text.isEmpty { sentences = [text] }
        guard let embedding = NLEmbedding.sentenceEmbedding(for: .english) else {
            return Array(sentences.prefix(limit))
        }
        let queries = ([theme] + aliases).filter { !$0.isEmpty }
        let queryTokens = ResearchThemeLanguage.tokenSet(in: queries)
        var scored = [(index: Int, sentence: String, lexical: Int, distance: Double)]()
        for (index, sentence) in sentences.enumerated() {
            let sentenceTokens = ResearchThemeLanguage.tokenSet(in: sentence)
            let lexical = sentenceTokens.intersection(queryTokens).count
            let distance = queries.map { embedding.distance(between: $0, and: sentence) }.min() ?? 2
            scored.append((index, sentence, lexical, distance))
        }
        return scored.sorted { left, right in
            if left.lexical != right.lexical { return left.lexical > right.lexical }
            return left.distance == right.distance ? left.index < right.index : left.distance < right.distance
        }
        .prefix(limit)
        .map(\.sentence)
    }

    static func bestDistance(between theme: String, aliases: [String], and text: String) -> Double {
        guard let embedding = NLEmbedding.sentenceEmbedding(for: .english) else { return 2 }
        let queries = ([theme] + aliases).filter { !$0.isEmpty }
        let candidates = passages(in: text, closestTo: theme, aliases: aliases, limit: 3)
        return candidates.flatMap { candidate in
            queries.map { embedding.distance(between: $0, and: candidate) }
        }.min() ?? 2
    }

    static func quote(
        _ quote: String,
        isGroundedIn evidence: String,
        for theme: String,
        aliases: [String]
    ) -> Bool {
        func normalized(_ value: String) -> String {
            value.lowercased()
                .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
                .joined(separator: " ")
        }
        let needle = normalized(quote)
        guard needle.count >= 12, normalized(evidence).contains(needle) else { return false }
        let queryTokens = ResearchThemeLanguage.tokenSet(in: [theme] + aliases)
        let quoteTokens = ResearchThemeLanguage.tokenSet(in: quote)
        return !queryTokens.intersection(quoteTokens).isEmpty
    }
}

@Generable
enum GeneratedExposure {
    case direct
    case enabling
    case adjacent
    case incidental

    var value: ExposureStrength {
        switch self {
        case .direct: .direct
        case .enabling: .enabling
        case .adjacent: .adjacent
        case .incidental: .incidental
        }
    }
}

@Generable
struct GeneratedCompanyFit {
    @Guide(description: "How materially the company participates in the trend")
    var exposure: GeneratedExposure

    @Guide(description: "One or two evidence-bounded sentences explaining the commercial connection")
    var thesis: String

    @Guide(description: "An exact short contiguous quote from the evidence that proves the connection, or empty if none")
    var supportingQuote: String
}

@Generable
struct GeneratedCompanyFitRow {
    @Guide(description: "Copy the supplied company identifier exactly")
    var identifier: String

    @Guide(description: "How materially the company participates in the trend")
    var exposure: GeneratedExposure

    @Guide(description: "One or two evidence-bounded sentences explaining the commercial connection")
    var thesis: String

    @Guide(description: "An exact short contiguous quote from the evidence that proves the connection, or empty if none")
    var supportingQuote: String
}

@Generable
struct GeneratedCompanyFitBatch {
    @Guide(description: "One assessment for every supplied company, in the supplied order", .minimumCount(1), .maximumCount(5))
    var companies: [GeneratedCompanyFitRow]
}

@Generable
struct GeneratedCompanyAnswer {
    @Guide(description: "A direct, concise answer to the exact question, grounded only in supplied source evidence")
    var answer: String

    @Guide(description: "A short limitation only when the supplied evidence cannot resolve an important part of the question")
    var limitation: String
}

public protocol CompanyFitEvaluating: Sendable {
    func evaluate(
        company: CompanyCandidate,
        theme: String,
        searchTerms: [String],
        evidence: [String],
        snapshot: CompanySnapshot?
    ) async throws -> (ExposureStrength, String)

    func evaluateBatch(
        _ inputs: [CompanyFitInput],
        theme: String,
        searchTerms: [String]
    ) async throws -> [String: CompanyFitAssessment]
}

public struct CompanyFitInput: Sendable {
    public let company: CompanyCandidate
    public let evidence: [String]
    public let snapshot: CompanySnapshot?

    public init(company: CompanyCandidate, evidence: [String], snapshot: CompanySnapshot?) {
        self.company = company
        self.evidence = evidence
        self.snapshot = snapshot
    }
}

public struct CompanyFitAssessment: Sendable {
    public let exposure: ExposureStrength
    public let thesis: String

    public init(exposure: ExposureStrength, thesis: String) {
        self.exposure = exposure
        self.thesis = thesis
    }
}

public extension CompanyFitEvaluating {
    func evaluateBatch(
        _ inputs: [CompanyFitInput],
        theme: String,
        searchTerms: [String]
    ) async throws -> [String: CompanyFitAssessment] {
        var output = [String: CompanyFitAssessment]()
        for input in inputs {
            let result = try await evaluate(
                company: input.company,
                theme: theme,
                searchTerms: searchTerms,
                evidence: input.evidence,
                snapshot: input.snapshot
            )
            output[input.company.id] = .init(exposure: result.0, thesis: result.1)
        }
        return output
    }
}

public struct OnDeviceCompanyFitEvaluator: CompanyFitEvaluating {
    public init() {}

    private static func groundedThesis(
        company: CompanyCandidate,
        exposure: ExposureStrength,
        quote: String
    ) -> String {
        let excerpt = String(quote.trimmingCharacters(in: .whitespacesAndNewlines).prefix(420))
        return "\(company.name) is marked \(exposure.rawValue.lowercased()) from this source description: “\(excerpt)”"
    }

    public func evaluate(
        company: CompanyCandidate,
        theme: String,
        searchTerms: [String],
        evidence: [String],
        snapshot: CompanySnapshot?
    ) async throws -> (ExposureStrength, String) {
        guard SystemLanguageModel.default.isAvailable else {
            throw StockAgentError.unavailable("Apple Intelligence is unavailable for semantic company review.")
        }
        let session = LanguageModelSession(
            model: .default,
            instructions: """
            Judge whether a public company has a commercially meaningful relationship to an open-ended
            trend. Use only the supplied source evidence. A product, end market, or material enabling input
            can qualify. A generic word match, customer example, risk disclosure, or incidental mention
            does not. Do not infer products, contracts, revenue, valuation, or quality beyond the evidence.
            """
        )
        let evidenceText = evidence.isEmpty
            ? "No contextual source excerpt was recovered."
            : evidence.enumerated().map { "Excerpt \($0.offset + 1): \($0.element)" }.joined(separator: "\n")
        let prompt = """
        Trend: \(theme)
        Product-language aliases: \(searchTerms.joined(separator: ", "))
        Company: \(company.name) (\(company.ticker))
        Industry: \(snapshot?.description ?? "Unavailable")
        \(evidenceText)
        Classify the exposure and explain the evidence. If evidence is insufficient, use incidental.
        """
        let response = try await session.respond(to: prompt, generating: GeneratedCompanyFit.self)
        let evidenceSource = evidence.joined(separator: " ")
        if response.content.exposure.value == .incidental {
            return (.incidental, "The retrieved description does not state a concrete commercial connection to the theme.")
        }
        guard SemanticEvidence.quote(
                    response.content.supportingQuote,
                    isGroundedIn: evidenceSource,
                    for: theme,
                    aliases: searchTerms
                ) else {
            return (.incidental, "The retrieved description does not state a concrete commercial connection to the theme.")
        }
        return (
            response.content.exposure.value,
            Self.groundedThesis(
                company: company,
                exposure: response.content.exposure.value,
                quote: response.content.supportingQuote
            )
        )
    }

    public func evaluateBatch(
        _ inputs: [CompanyFitInput],
        theme: String,
        searchTerms: [String]
    ) async throws -> [String: CompanyFitAssessment] {
        guard SystemLanguageModel.default.isAvailable else {
            throw StockAgentError.unavailable("Apple Intelligence is unavailable for semantic company review.")
        }
        var output = [String: CompanyFitAssessment]()
        for offset in stride(from: 0, to: inputs.count, by: 5) {
            let group = Array(inputs[offset..<min(offset + 5, inputs.count)])
            let session = LanguageModelSession(
                model: .default,
                instructions: """
                Judge how materially each public company participates in the stated trend. Use only the
                supplied source evidence. A product, end market, or material enabling input can qualify.
                Direct means the company explicitly designs, makes, sells, or operates the named end
                product. Enabling means it explicitly supplies a component, propulsion, software, or
                infrastructure for that product. Adjacent means it is in the broad industry without an
                explicit product-level connection.
                Generic technology similarity or an incidental mention does not. A positive classification
                requires a short exact contiguous quote that explicitly supports the commercial connection;
                otherwise classify it as incidental and leave the quote empty. Return one row per company,
                copy each identifier exactly, and do not infer products, contracts, revenue, or quality.
                """
            )
            let companyText = group.enumerated().map { index, input in
                let evidence = input.evidence.joined(separator: " ")
                return """
                Company \(index + 1)
                Identifier: \(input.company.id)
                Name: \(input.company.name) (\(input.company.ticker))
                Industry: \(input.snapshot?.description ?? "Unavailable")
                Evidence: \(String(evidence.prefix(1_500)))
                """
            }.joined(separator: "\n\n")
            let response = try await session.respond(
                to: "Trend: \(theme)\nProduct-language aliases: \(searchTerms.joined(separator: ", "))\n\n\(companyText)",
                generating: GeneratedCompanyFitBatch.self
            )
            for (index, row) in response.content.companies.enumerated() {
                guard let input = group.first(where: { $0.company.id == row.identifier })
                        ?? (group.indices.contains(index) ? group[index] : nil) else { continue }
                let evidence = input.evidence.joined(separator: " ")
                if ProcessInfo.processInfo.environment["STOCK_AGENT_DEBUG_FIT"] == "1" {
                    print("FIT \(input.company.ticker) id=\(row.identifier) exposure=\(row.exposure) quote=\(row.supportingQuote)")
                }
                let exposure: ExposureStrength
                let thesis: String
                if row.exposure.value == .incidental {
                    exposure = .incidental
                    thesis = "The retrieved description does not state a concrete commercial connection to the theme."
                } else if SemanticEvidence.quote(
                        row.supportingQuote,
                        isGroundedIn: evidence,
                        for: theme,
                        aliases: searchTerms
                    ) {
                    exposure = row.exposure.value
                    thesis = Self.groundedThesis(
                        company: input.company,
                        exposure: exposure,
                        quote: row.supportingQuote
                    )
                } else {
                    exposure = .incidental
                    thesis = "The retrieved description does not state a concrete commercial connection to the theme."
                }
                output[input.company.id] = .init(
                    exposure: exposure,
                    thesis: thesis
                )
            }
        }
        return output
    }

}

public protocol CompanyQuestionAnswering: Sendable {
    func answer(
        question: String,
        company: CompanyCandidate,
        evidence: [String],
        snapshot: CompanySnapshot
    ) async throws -> String
}

public struct OnDeviceCompanyQuestionAnswerer: CompanyQuestionAnswering {
    public init() {}

    public func answer(
        question: String,
        company: CompanyCandidate,
        evidence: [String],
        snapshot: CompanySnapshot
    ) async throws -> String {
        guard SystemLanguageModel.default.isAvailable else {
            throw StockAgentError.unavailable("Apple Intelligence is unavailable for the company answer.")
        }
        let session = LanguageModelSession(
            model: .default,
            instructions: """
            Answer a company-specific research question using only the supplied LSEG/SEC excerpts and
            reported facts. Address the exact question and identify the source of material facts.
            Distinguish reported facts from
            interpretation, do not invent missing prices, forecasts, revenue exposure, contracts, or
            recommendations, and state when the evidence is insufficient. Keep the answer under 180 words.
            """
        )
        let facts = snapshot.facts.map { fact in
            let period = fact.periodEnd.map { ", period ending \($0.formatted(date: .abbreviated, time: .omitted))" } ?? ""
            return "\(fact.source) — \(fact.label): \(fact.value) \(fact.unit)\(period)"
        }.joined(separator: "\n")
        let excerpts = evidence.isEmpty
            ? "No question-specific excerpt was recovered."
            : evidence.enumerated().map { "Excerpt \($0.offset + 1): \($0.element)" }.joined(separator: "\n")
        let prompt = """
        Question: \(question)
        Company: \(snapshot.name) (\(company.ticker))
        Industry: \(snapshot.description)

        Reported facts:
        \(facts.isEmpty ? "No structured facts were available." : facts)

        Source evidence:
        \(excerpts)
        """
        let response = try await session.respond(to: prompt, generating: GeneratedCompanyAnswer.self)
        let answer = response.content.answer.trimmingCharacters(in: .whitespacesAndNewlines)
        let limitation = response.content.limitation.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !answer.isEmpty else {
            throw StockAgentError.malformedResponse("The on-device model returned an empty company answer.")
        }
        return limitation.isEmpty ? answer : "\(answer)\n\nLimitation: \(limitation)"
    }
}

public enum NamedResearchQuery {
    public static func filingFocus(question: String, ticker: String, companyName: String) -> String {
        let excluded = Set([
            "a", "an", "and", "are", "about", "company", "could", "does", "for", "from", "give",
            "how", "i", "in", "is", "its", "me", "of", "on", "please", "research", "should", "stock",
            "tell", "the", "this", "to", "what", "which", "who", "why", "would", "you",
        ])
        let identityWords = Set(
            ([ticker] + companyName.split(separator: " ").map(String.init))
                .map { $0.lowercased().filter { $0.isLetter || $0.isNumber } }
        )
        let terms = question.lowercased()
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .map(String.init)
            .filter { $0.count >= 2 && !excluded.contains($0) && !identityWords.contains($0) }
        return terms.isEmpty ? "business strategy risk factors" : terms.joined(separator: " ")
    }

    public static func sourceBoundFallback(
        question: String,
        companyName: String,
        evidence: [String]
    ) -> String? {
        guard question.lowercased().contains("risk") else { return nil }
        let filingText = evidence
            .filter { $0.hasPrefix("SEC filing excerpt:") }
            .map { $0.replacingOccurrences(of: "SEC filing excerpt:", with: "") }
            .joined(separator: " ")
        guard filingText.range(of: "Summary Risk Factors", options: .caseInsensitive) != nil else {
            return nil
        }
        let bullets = filingText.split(separator: "•").dropFirst().compactMap { fragment -> String? in
            var text = String(fragment)
                .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let boundaries = [
                text.firstIndex(of: ";"),
                text.range(of: " Risks Related to ", options: .caseInsensitive)?.lowerBound,
            ].compactMap { $0 }
            if let end = boundaries.min() { text = String(text[..<end]) }
            text = text.trimmingCharacters(in: .whitespacesAndNewlines)
            while let last = text.last, [";", ",", "."].contains(last) { text.removeLast() }
            guard text.count >= 12 else { return nil }
            return text.prefix(1).uppercased() + text.dropFirst() + "."
        }
        guard !bullets.isEmpty else { return nil }
        return """
        \(companyName)’s latest 10-K summary lists these principal risk factors:

        \(bullets.prefix(20).map { "• \($0)" }.joined(separator: "\n"))

        The filing presents these as a summary and does not rank their probability or financial impact.
        """
    }
}

public struct ResearchEngine: Sendable {
    private let sec: SECService
    private let lseg: (any LSEGResearchProviding)?
    private let evaluator: any CompanyFitEvaluating
    private let companyAnswerer: any CompanyQuestionAnswering

    public init(
        sec: SECService,
        lseg: (any LSEGResearchProviding)? = nil,
        evaluator: any CompanyFitEvaluating = OnDeviceCompanyFitEvaluator(),
        companyAnswerer: any CompanyQuestionAnswering = OnDeviceCompanyQuestionAnswerer()
    ) {
        self.sec = sec
        self.lseg = lseg
        self.evaluator = evaluator
        self.companyAnswerer = companyAnswerer
    }

    public func run(_ rawProposal: ResearchProposal) async throws -> ResearchReport {
        let proposal = try ResearchRegistry.validate(rawProposal)
        switch proposal.mode {
        case .discovery:
            return try await discovery(proposal)
        case .named:
            return try await named(proposal)
        case .marketNews:
            return ResearchReport(
                question: proposal.question,
                title: "Market research proposal",
                companies: [],
                notes: [
                    "Broad real-time news is not available from the current sources. Company filings, financial facts, portfolio tools, and macro data remain available."
                ]
            )
        }
    }

    private func discovery(_ proposal: ResearchProposal) async throws -> ResearchReport {
        let theme = proposal.theme ?? proposal.question
        let candidateLimit = max(proposal.resultCount * 3, 16)
        var inputs = [DiscoveryInput]()
        var providerNote: String?

        if let lseg {
            do {
                let records = try await lseg.discover(
                    universes: proposal.universes,
                    limit: candidateLimit
                )
                inputs = records.map(Self.discoveryInput)
            } catch {
                providerNote = "LSEG Workspace was unavailable, so this run used SEC EDGAR."
            }
        }

        if inputs.isEmpty {
            let candidates = try await sec.searchFilings(query: theme, limit: candidateLimit)
            for candidate in candidates {
                let snapshot = try? await sec.snapshot(for: candidate)
                let evidence: [String]
                if let url = candidate.filingURL {
                    evidence = (try? await sec.filingEvidence(url: url, query: theme)) ?? []
                } else {
                    evidence = []
                }
                inputs.append(
                    DiscoveryInput(
                        candidate: candidate,
                        snapshot: snapshot,
                        evidence: evidence,
                        sources: ["SEC EDGAR"]
                    )
                )
            }
        }

        guard !inputs.isEmpty else {
            return ResearchReport(
                question: proposal.question,
                title: "No matches for \(theme)",
                companies: [],
                notes: ["Try a broader theme phrase or edit the proposal before running it again."]
            )
        }
        let reviewLimit = min(candidateLimit, max(proposal.resultCount, 5))
        let reviewInputs = Self.semanticPrefilter(
            inputs,
            theme: theme,
            searchTerms: proposal.searchTerms,
            limit: reviewLimit
        )
        let distinctiveTerms = Self.distinctiveEvidenceTerms(
            in: inputs,
            theme: theme,
            searchTerms: proposal.searchTerms
        )
        let fitInputs = reviewInputs.map {
            let source = $0.evidence.joined(separator: " ")
            let passages = SemanticEvidence.passages(
                in: source,
                closestTo: theme,
                aliases: distinctiveTerms,
                limit: 3
            )
            return CompanyFitInput(company: $0.candidate, evidence: passages, snapshot: $0.snapshot)
        }
        let assessments: [String: CompanyFitAssessment]
        do {
            assessments = try await evaluator.evaluateBatch(
                fitInputs,
                theme: theme,
                searchTerms: distinctiveTerms
            )
        } catch {
            assessments = [:]
        }

        var evaluated = [ResearchCompanyResult]()
        for input in reviewInputs {
            let exposure: ExposureStrength
            let thesis: String
            if let assessment = assessments[input.candidate.id] {
                exposure = assessment.exposure
                thesis = assessment.thesis
            } else {
                exposure = .unreviewed
                thesis = input.evidence.isEmpty
                    ? "The source returned this company, but no contextual description was available."
                    : "Source evidence was loaded. Review it before treating the company as materially exposed to the theme."
            }
            evaluated.append(
                ResearchCompanyResult(
                    candidate: input.candidate,
                    exposure: exposure,
                    thesis: thesis,
                    evidence: input.evidence,
                    snapshot: input.snapshot,
                    sources: input.sources
                )
            )
        }
        let ranked = evaluated.sorted {
            let ranks: [ExposureStrength: Int] = [
                .direct: 0, .enabling: 1, .adjacent: 2, .unreviewed: 3, .incidental: 4,
                .profile: 3,
            ]
            let left = ranks[$0.exposure] ?? 5
            let right = ranks[$1.exposure] ?? 5
            return left == right
                ? $0.candidate.relevance > $1.candidate.relevance
                : left < right
        }
        let strongMatches = ranked.filter { $0.exposure == .direct || $0.exposure == .enabling }
        let reviewedMatches = ranked.filter { $0.exposure != .incidental }
        let selectionPool = !strongMatches.isEmpty
            ? strongMatches
            : (reviewedMatches.isEmpty ? ranked : reviewedMatches)
        let selected = Array(selectionPool.prefix(proposal.resultCount))
        var notes = [
            "Candidates came from the selected industry screens; semantic review used retrieved company descriptions to distinguish commercial exposure from incidental wording.",
            "This is research assistance, not investment advice. Verify material claims in the linked filings.",
        ]
        if let providerNote { notes.insert(providerNote, at: 0) }
        if selected.contains(where: { $0.exposure == .unreviewed }) {
            notes.insert(
                "On-device semantic review was unavailable, so filing matches are marked Needs review instead of being presented as conclusions.",
                at: 0
            )
        }
        return ResearchReport(
            question: proposal.question,
            title: "Companies connected to \(theme)",
            companies: selected,
            notes: notes
        )
    }

    private func named(_ proposal: ResearchProposal) async throws -> ResearchReport {
        var results = [ResearchCompanyResult]()
        var notes = [String]()
        for ticker in proposal.securities.prefix(proposal.resultCount) {
            do {
                var lsegRecord: LSEGCompanyRecord?
                var lsegFailure: Error?
                if let lseg {
                    do { lsegRecord = try await lseg.company(ticker: ticker) }
                    catch { lsegFailure = error }
                }

                var resolved: CompanyCandidate?
                var secSnapshot: CompanySnapshot?
                var secFailure: Error?
                do {
                    let value = try await sec.resolve(ticker: ticker)
                    resolved = value
                    secSnapshot = try await sec.snapshot(for: value)
                } catch {
                    secFailure = error
                }

                guard lsegRecord != nil || secSnapshot != nil else {
                    let detail = [lsegFailure, secFailure]
                        .compactMap { $0?.localizedDescription }
                        .joined(separator: " ")
                    throw StockAgentError.unavailable(detail.isEmpty ? "No company data was available." : detail)
                }

                let filings = secSnapshot?.recentFilings ?? []
                let filing = filings.first(where: { $0.form == "10-K" })
                    ?? filings.first(where: { $0.form == "10-Q" })
                let filingURL: URL? = if let filing, let resolved {
                    SECService.filingURL(cik: resolved.cik, filing: filing)
                } else {
                    nil
                }
                let candidate = CompanyCandidate(
                    cik: resolved?.cik ?? "lseg:\(lsegRecord?.ric ?? ticker)",
                    ticker: resolved?.ticker ?? lsegRecord?.ticker ?? ticker,
                    name: lsegRecord?.name ?? secSnapshot?.name ?? resolved?.name ?? ticker,
                    filingDate: filing?.filedAt,
                    filingURL: filingURL,
                    relevance: 1
                )

                var evidence = [String]()
                if let summary = lsegRecord?.businessSummary, !summary.isEmpty {
                    evidence.append("LSEG business description: \(summary)")
                }
                let focus = NamedResearchQuery.filingFocus(
                    question: proposal.question,
                    ticker: candidate.ticker,
                    companyName: candidate.name
                )
                if let filingURL {
                    let excerpts = (try? await sec.filingEvidence(url: filingURL, query: focus, limit: 5)) ?? []
                    evidence.append(contentsOf: excerpts.map { "SEC filing excerpt: \($0)" })
                }

                let snapshot = Self.mergedSnapshot(
                    candidate: candidate,
                    lseg: lsegRecord,
                    sec: secSnapshot
                )
                let thesis: String
                do {
                    thesis = try await companyAnswerer.answer(
                        question: proposal.question,
                        company: candidate,
                        evidence: evidence,
                        snapshot: snapshot
                    )
                } catch {
                    thesis = NamedResearchQuery.sourceBoundFallback(
                        question: proposal.question,
                        companyName: candidate.name,
                        evidence: evidence
                    ) ?? "Company data loaded, but the on-device answer was unavailable: \(error.localizedDescription)"
                }
                var sources = [String]()
                if lsegRecord != nil { sources.append("LSEG Workspace") }
                if secSnapshot != nil { sources.append("SEC EDGAR") }
                results.append(
                    ResearchCompanyResult(
                        candidate: candidate,
                        exposure: .profile,
                        thesis: thesis,
                        evidence: evidence,
                        snapshot: snapshot,
                        sources: sources
                    )
                )
            } catch {
                notes.append("\(ticker): \(error.localizedDescription)")
            }
        }
        return ResearchReport(
            question: proposal.question,
            title: results.count == 1 ? "\(results[0].candidate.name) research" : "Company research",
            companies: results,
            notes: notes + ["Values retain their source context: LSEG fields reflect the active Workspace session; SEC facts may cover different reporting periods."]
        )
    }

    private struct DiscoveryInput {
        let candidate: CompanyCandidate
        let snapshot: CompanySnapshot?
        let evidence: [String]
        let sources: [String]
    }

    private static func discoveryInput(_ record: LSEGCompanyRecord) -> DiscoveryInput {
        let candidate = CompanyCandidate(
            cik: "lseg:\(record.ric)",
            ticker: record.ticker,
            name: record.name,
            filingDate: nil,
            filingURL: nil,
            relevance: record.marketCap ?? 0
        )
        let snapshot = mergedSnapshot(candidate: candidate, lseg: record, sec: nil)
        let evidence = record.businessSummary.isEmpty
            ? []
            : ["LSEG business description: \(record.businessSummary)"]
        return DiscoveryInput(
            candidate: candidate,
            snapshot: snapshot,
            evidence: evidence,
            sources: ["LSEG Workspace"]
        )
    }

    private static func semanticPrefilter(
        _ inputs: [DiscoveryInput],
        theme: String,
        searchTerms: [String],
        limit: Int
    ) -> [DiscoveryInput] {
        guard inputs.count > limit,
              NLEmbedding.sentenceEmbedding(for: .english) != nil else {
            return Array(inputs.prefix(limit))
        }
        let normalizedTerms = ([theme] + searchTerms)
            .map(ResearchThemeLanguage.normalizedPhrase)
            .filter { $0.count >= 3 }
        let queryTokens = ResearchThemeLanguage.tokenSet(in: [theme] + searchTerms)
        var documentFrequency = [String: Int]()
        for input in inputs {
            let tokens = ResearchThemeLanguage.tokenSet(in: input.evidence)
            for token in tokens.intersection(queryTokens) {
                documentFrequency[token, default: 0] += 1
            }
        }
        var scored: [(input: DiscoveryInput, lexicalScore: Int, semanticDistance: Double)] = []
        scored.reserveCapacity(inputs.count)
        for input in inputs {
            let text = input.evidence.joined(separator: " ")
            let normalizedText = normalizedWords(text)
            var lexicalScore = 0
            for term in normalizedTerms where normalizedText.contains(term) {
                lexicalScore += max(2, term.split(separator: " ").count * 2)
            }
            let textTokens = ResearchThemeLanguage.tokenSet(in: text)
            for token in textTokens.intersection(queryTokens) {
                let frequency = documentFrequency[token, default: inputs.count]
                let rarityWeight = max(1, inputs.count - frequency + 1)
                lexicalScore += rarityWeight
            }
            let limitedText = String(text.prefix(3_000))
            let semanticDistance = SemanticEvidence.bestDistance(
                between: theme,
                aliases: searchTerms,
                and: limitedText
            )
            if ProcessInfo.processInfo.environment["STOCK_AGENT_DEBUG_FIT"] == "1" {
                print("PREFILTER \(input.candidate.ticker) lexical=\(lexicalScore) distance=\(semanticDistance)")
            }
            scored.append((input, lexicalScore, semanticDistance))
        }
        return scored.sorted { left, right in
            left.lexicalScore == right.lexicalScore
                ? left.semanticDistance < right.semanticDistance
                : left.lexicalScore > right.lexicalScore
        }
        .prefix(limit)
        .map(\.input)
    }

    private static func distinctiveEvidenceTerms(
        in inputs: [DiscoveryInput],
        theme: String,
        searchTerms: [String]
    ) -> [String] {
        let requestedTokens = ResearchThemeLanguage.tokenSet(in: [theme] + searchTerms)
        guard !requestedTokens.isEmpty else { return [] }
        var documentFrequency = [String: Int]()
        for input in inputs {
            let tokens = ResearchThemeLanguage.tokenSet(in: input.evidence)
            for token in tokens.intersection(requestedTokens) {
                documentFrequency[token, default: 0] += 1
            }
        }
        let maximumCommonCount = max(2, Int((Double(inputs.count) * 0.4).rounded(.up)))
        return requestedTokens
            .filter { documentFrequency[$0, default: 0] <= maximumCommonCount }
            .sorted()
    }

    private static func normalizedWords(_ value: String) -> String {
        value.lowercased()
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .joined(separator: " ")
    }

    private static func mergedSnapshot(
        candidate: CompanyCandidate,
        lseg: LSEGCompanyRecord?,
        sec: CompanySnapshot?
    ) -> CompanySnapshot {
        let lsegFacts = lseg?.facts.map {
            FinancialFact(
                label: $0.label,
                value: $0.value,
                unit: $0.unit,
                periodEnd: nil,
                source: "LSEG Workspace"
            )
        } ?? []
        var facts = lsegFacts
        let existing = Set(lsegFacts.map { $0.label.lowercased() })
        facts.append(contentsOf: (sec?.facts ?? []).filter { !existing.contains($0.label.lowercased()) })
        return CompanySnapshot(
            cik: sec?.cik ?? candidate.cik,
            ticker: candidate.ticker,
            name: lseg?.name ?? sec?.name ?? candidate.name,
            description: lseg?.industry.isEmpty == false ? lseg!.industry : (sec?.description ?? "Public company"),
            facts: facts,
            recentFilings: sec?.recentFilings ?? []
        )
    }
}
