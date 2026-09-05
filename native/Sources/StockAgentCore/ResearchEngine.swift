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

public struct GroundedCompanyFitEvaluator: CompanyFitEvaluating {
    public init() {}

    public func evaluate(
        company: CompanyCandidate,
        theme: String,
        searchTerms: [String],
        evidence: [String],
        snapshot: CompanySnapshot?
    ) async throws -> (ExposureStrength, String) {
        let aliases = ([theme] + searchTerms)
            .map(ResearchThemeLanguage.normalizedPhrase)
            .filter { !$0.isEmpty }
        let queryTokens = ResearchThemeLanguage.tokenSet(in: [theme] + searchTerms)
        guard !queryTokens.isEmpty else {
            return (.incidental, "The research theme did not contain enough product language to verify exposure.")
        }

        let scored = evidence.map { excerpt -> (String, Int, Int) in
            let normalized = ResearchThemeLanguage.normalizedPhrase(excerpt)
            let phraseMatches = aliases.filter { normalized.contains($0) }.count
            let tokenMatches = ResearchThemeLanguage.tokenSet(in: excerpt).intersection(queryTokens).count
            return (excerpt, phraseMatches, tokenMatches)
        }.sorted { left, right in
            let leftScore = left.1 * 10 + left.2
            let rightScore = right.1 * 10 + right.2
            return leftScore > rightScore
        }
        guard let best = scored.first, best.1 > 0 || best.2 >= 2 else {
            return (.incidental, "The retrieved description does not state a concrete commercial connection to the theme.")
        }
        if best.0.hasPrefix("SEC filing excerpt:"),
           !Self.secExcerptNamesFiler(best.0, company: company) {
            return (.incidental, "The filing language did not clearly describe the filer’s own business.")
        }

        let words = ResearchThemeLanguage.normalizedPhrase(best.0).split(separator: " ").map(String.init)
        let matchIndexes = words.indices.filter { queryTokens.contains(words[$0]) }
        let enablingPrefixes = [
            "component", "engine", "infrastructure", "material", "propulsion", "semiconductor",
            "sensor", "software", "suppl", "power", "communication",
        ]
        let actionPrefixes = [
            "build", "deliver", "design", "develop", "engag", "manufactur", "operat", "offer",
            "produc", "provid", "sell",
        ]
        func hasNearbyPrefix(_ prefixes: [String], distance: Int) -> Bool {
            words.indices.contains { index in
                prefixes.contains(where: { words[index].hasPrefix($0) })
                    && matchIndexes.contains(where: { abs($0 - index) <= distance })
            }
        }
        let hasEnablingLanguage = hasNearbyPrefix(enablingPrefixes, distance: 10)
        let hasCommercialAction = hasNearbyPrefix(actionPrefixes + ["focus", "specializ"], distance: 14)
        let exposure: ExposureStrength
        if hasEnablingLanguage && hasCommercialAction {
            exposure = .enabling
        } else if hasCommercialAction {
            exposure = .direct
        } else {
            exposure = .adjacent
        }
        let excerpt = Self.focusedExcerpt(
            best.0,
            terms: [theme] + searchTerms + Array(queryTokens)
        )
        return (
            exposure,
            "\(company.name) is marked \(exposure.rawValue.lowercased()) from this source description: “\(excerpt)”"
        )
    }

    private static func focusedExcerpt(_ rawText: String, terms: [String]) -> String {
        var text = rawText
            .replacingOccurrences(of: "LSEG business description:", with: "")
            .replacingOccurrences(of: "SEC filing excerpt:", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        while let first = text.first, ["“", "”", "\"", ":", ";"].contains(first) {
            text.removeFirst()
            text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let candidates = terms
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { $0.count >= 3 }
            .sorted { $0.count > $1.count }
        guard let match = candidates.lazy.compactMap({ text.range(of: $0, options: .caseInsensitive) }).first else {
            return String(text.prefix(420))
        }
        let rawStart = text.index(match.lowerBound, offsetBy: -130, limitedBy: text.startIndex) ?? text.startIndex
        let rawEnd = text.index(match.upperBound, offsetBy: 270, limitedBy: text.endIndex) ?? text.endIndex
        let before = text[rawStart..<match.lowerBound]
        let start = before.lastIndex(where: { ".!?•".contains($0) })
            .map { text.index(after: $0) } ?? rawStart
        let after = text[match.upperBound..<rawEnd]
        let end = after.firstIndex(where: { ".!?•".contains($0) })
            .map { text.index(after: $0) } ?? rawEnd
        return String(text[start..<end])
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func secExcerptNamesFiler(_ excerpt: String, company: CompanyCandidate) -> Bool {
        let normalized = ResearchThemeLanguage.normalizedPhrase(excerpt)
        let identityWords = company.name.lowercased()
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .map(String.init)
            .filter { $0.count >= 4 && !["company", "corporation", "holdings", "incorporated"].contains($0) }
        if identityWords.contains(where: normalized.contains) { return true }
        let rawWords = Set(excerpt.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init))
        return rawWords.contains("we") || rawWords.contains("our")
            || rawWords.contains(company.ticker.lowercased())
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

    public static func companyDataFallback(
        companyName: String,
        evidence: [String],
        snapshot: CompanySnapshot
    ) -> String {
        var parts = ["\(companyName) is classified as \(snapshot.description)."]
        if let description = evidence.first(where: { $0.hasPrefix("LSEG business description:") }) {
            let cleaned = description
                .replacingOccurrences(of: "LSEG business description:", with: "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !cleaned.isEmpty { parts.append(String(cleaned.prefix(520))) }
        }
        let facts = snapshot.facts.prefix(5).map { fact in
            let value: String
            if fact.unit.uppercased() == "USD" {
                value = fact.value.formatted(
                    .currency(code: "USD").notation(.compactName).precision(.fractionLength(0...2))
                )
            } else {
                value = fact.value.formatted(
                    .number.notation(.compactName).precision(.fractionLength(0...2))
                ) + " " + fact.unit
            }
            return "\(fact.label): \(value)"
        }
        if !facts.isEmpty { parts.append("Reported facts include \(facts.joined(separator: "; ")).") }
        return parts.joined(separator: "\n\n")
    }
}

public struct ResearchEngine: Sendable {
    private let sec: SECService
    private let lseg: (any LSEGResearchProviding)?
    private let evaluator: any CompanyFitEvaluating
    private let companyAnswerer: any CompanyQuestionAnswering
    private let investmentCaseGenerator: any InvestmentCaseGenerating

    public init(
        sec: SECService,
        lseg: (any LSEGResearchProviding)? = nil,
        evaluator: any CompanyFitEvaluating = GroundedCompanyFitEvaluator(),
        companyAnswerer: any CompanyQuestionAnswering = OnDeviceCompanyQuestionAnswerer()
    ) {
        self.sec = sec
        self.lseg = lseg
        self.evaluator = evaluator
        self.companyAnswerer = companyAnswerer
        self.investmentCaseGenerator = OnDeviceInvestmentCaseGenerator()
    }

    public func run(_ rawProposal: ResearchProposal) async throws -> ResearchReport {
        let proposal = try ResearchRegistry.validate(rawProposal)
        switch proposal.mode {
        case .discovery:
            return try await discovery(proposal)
        case .named:
            return try await named(proposal)
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
            let secCandidateLimit = max(proposal.resultCount * 3, 9)
            let candidates = try await sec.searchFilings(query: theme, limit: secCandidateLimit)
            for candidate in candidates {
                guard let snapshot = try? await sec.snapshot(for: candidate),
                      let annualFiling = snapshot.recentFilings.first(where: { $0.form == "10-K" }),
                      let annualURL = SECService.filingURL(cik: candidate.cik, filing: annualFiling) else {
                    continue
                }
                let verificationQuery = ([theme] + proposal.searchTerms).joined(separator: " ")
                let excerpts = (try? await sec.filingEvidence(
                    url: annualURL,
                    query: verificationQuery,
                    limit: 4
                )) ?? []
                guard !excerpts.isEmpty else { continue }
                let verifiedCandidate = CompanyCandidate(
                    cik: candidate.cik,
                    ticker: candidate.ticker,
                    name: snapshot.name,
                    filingDate: annualFiling.filedAt,
                    filingURL: annualURL,
                    relevance: candidate.relevance
                )
                inputs.append(
                    DiscoveryInput(
                        candidate: verifiedCandidate,
                        snapshot: snapshot,
                        evidence: excerpts.map { "SEC filing excerpt: \($0)" },
                        sources: ["SEC EDGAR"],
                        universe: nil
                    )
                )
                if inputs.count >= max(proposal.resultCount * 2, 6) { break }
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
        let reviewLimit = min(candidateLimit, max(proposal.resultCount, 3))
        let reviewInputs = Self.semanticPrefilter(
            inputs,
            theme: theme,
            searchTerms: proposal.searchTerms,
            limit: reviewLimit
        )
        let fitInputs = reviewInputs.map {
            let source = $0.evidence.joined(separator: " ")
            var passages = SemanticEvidence.passages(
                in: source,
                closestTo: theme,
                aliases: proposal.searchTerms,
                limit: 3
            )
            if $0.sources.contains("SEC EDGAR") {
                passages = passages.map { passage in
                    passage.hasPrefix("SEC filing excerpt:")
                        ? passage
                        : "SEC filing excerpt: \(passage)"
                }
            }
            return CompanyFitInput(company: $0.candidate, evidence: passages, snapshot: $0.snapshot)
        }
        let assessments: [String: CompanyFitAssessment]
        do {
            assessments = try await evaluator.evaluateBatch(
                fitInputs,
                theme: theme,
                searchTerms: proposal.searchTerms
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
        let caseInputs = selected.map { result -> InvestmentCaseInput in
            let matchedInput = inputs.first { $0.candidate.id == result.candidate.id }
            let sameUniverse = inputs.filter { candidate in
                guard let universe = matchedInput?.universe else { return true }
                return candidate.universe == universe
            }.compactMap(\.snapshot)
            let peers = sameUniverse.count >= 3 ? sameUniverse : inputs.compactMap(\.snapshot)
            return InvestmentCaseEvidence.makeInput(for: result, peerSnapshots: peers)
        }
        let generatedCases: [String: InvestmentCase]
        if selected.contains(where: { $0.sources.contains("LSEG Workspace") }) {
            do {
            generatedCases = try await investmentCaseGenerator.generate(caseInputs)
            } catch {
                generatedCases = [:]
            }
        } else {
            generatedCases = [:]
        }
        let enriched = zip(selected, caseInputs).map { result, input in
            ResearchCompanyResult(
                candidate: result.candidate,
                exposure: result.exposure,
                thesis: result.thesis,
                evidence: result.evidence,
                snapshot: result.snapshot,
                sources: result.sources,
                investmentCase: generatedCases[result.candidate.id] ?? input.fallback
            )
        }
        var notes = [String]()
        if let providerNote { notes.insert(providerNote, at: 0) }
        if selected.contains(where: { $0.exposure == .unreviewed }) {
            notes.insert(
                "Some company evidence could not be verified, so those matches are marked Needs review.",
                at: 0
            )
        }
        return ResearchReport(
            question: proposal.question,
            title: "Companies connected to \(theme)",
            companies: enriched,
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
                    ) ?? NamedResearchQuery.companyDataFallback(
                        companyName: candidate.name,
                        evidence: evidence,
                        snapshot: snapshot
                    )
                }
                var sources = [String]()
                if lsegRecord != nil { sources.append("LSEG Workspace") }
                if secSnapshot != nil { sources.append("SEC EDGAR") }
                if lsegRecord == nil, lsegFailure != nil, secSnapshot != nil {
                    notes.append("\(ticker): LSEG Workspace was unavailable, so this result uses SEC EDGAR.")
                }
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
        let caseInputs = results.map {
            InvestmentCaseEvidence.makeInput(
                for: $0,
                peerSnapshots: results.compactMap(\.snapshot)
            )
        }
        let generatedCases: [String: InvestmentCase]
        if results.contains(where: { $0.sources.contains("LSEG Workspace") }) {
            do {
                generatedCases = try await investmentCaseGenerator.generate(caseInputs)
            } catch {
                generatedCases = [:]
            }
        } else {
            generatedCases = [:]
        }
        let enriched = zip(results, caseInputs).map { result, input in
            ResearchCompanyResult(
                candidate: result.candidate,
                exposure: result.exposure,
                thesis: result.thesis,
                evidence: result.evidence,
                snapshot: result.snapshot,
                sources: result.sources,
                investmentCase: generatedCases[result.candidate.id] ?? input.fallback
            )
        }
        return ResearchReport(
            question: proposal.question,
            title: enriched.count == 1 ? "\(enriched[0].candidate.name) research" : "Company research",
            companies: enriched,
            notes: notes + ["LSEG and SEC values can cover different reporting periods."]
        )
    }

    private struct DiscoveryInput {
        let candidate: CompanyCandidate
        let snapshot: CompanySnapshot?
        let evidence: [String]
        let sources: [String]
        let universe: String?
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
            sources: ["LSEG Workspace"],
            universe: record.universe
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
