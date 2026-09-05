import Foundation
import FoundationModels

@Generable
enum GeneratedUniverse: String, CaseIterable {
    case allPublicEquities
    case energy
    case basicMaterials
    case industrials
    case consumerCyclicals
    case consumerNonCyclicals
    case financials
    case healthcare
    case technology
    case telecommunicationsServices
    case utilities
    case realEstate
    case aerospaceAndDefense
    case automobilesAndAutoParts
    case banks
    case biotechnologyAndMedicalResearch
    case insurance
    case medicalEquipmentAndSupplies
    case oilAndGas
    case pharmaceuticals
    case semiconductorEquipment
    case semiconductors
    case semiconductorsAndSemiconductorEquipment
    case software

    var canonicalName: String {
        switch self {
        case .allPublicEquities: "All public equities"
        case .energy: "Energy"
        case .basicMaterials: "Basic Materials"
        case .industrials: "Industrials"
        case .consumerCyclicals: "Consumer Cyclicals"
        case .consumerNonCyclicals: "Consumer Non-Cyclicals"
        case .financials: "Financials"
        case .healthcare: "Healthcare"
        case .technology: "Technology"
        case .telecommunicationsServices: "Telecommunications Services"
        case .utilities: "Utilities"
        case .realEstate: "Real Estate"
        case .aerospaceAndDefense: "Aerospace & Defense"
        case .automobilesAndAutoParts: "Automobiles & Auto Parts"
        case .banks: "Banks"
        case .biotechnologyAndMedicalResearch: "Biotechnology & Medical Research"
        case .insurance: "Insurance"
        case .medicalEquipmentAndSupplies: "Medical Equipment & Supplies"
        case .oilAndGas: "Oil & Gas"
        case .pharmaceuticals: "Pharmaceuticals"
        case .semiconductorEquipment: "Semiconductor Equipment"
        case .semiconductors: "Semiconductors"
        case .semiconductorsAndSemiconductorEquipment:
            "Semiconductors & Semiconductor Equipment"
        case .software: "Software"
        }
    }
}

@Generable
enum GeneratedUniverseRole {
    case primaryProductOrEndMarket
    case enablingTechnologyOrInput
    case adjacentApplication

    var rank: Int {
        switch self {
        case .primaryProductOrEndMarket: 0
        case .enablingTechnologyOrInput: 1
        case .adjacentApplication: 2
        }
    }
}

@Generable
struct GeneratedUniverseReason {
    @Guide(description: "The supported market universe")
    var universe: GeneratedUniverse

    @Guide(description: "One concise commercial relationship between the theme and universe")
    var reason: String

    @Guide(description: "Whether this universe is the primary product/end market, an enabling input, or only adjacent")
    var role: GeneratedUniverseRole
}

@Generable
struct GeneratedThemeAudit {
    @Guide(description: "Two to eight words naming only the investable product, service, or business trend; omit request wording and sector names")
    var theme: String

    @Guide(description: "Most directly exposed universes first", .minimumCount(1), .maximumCount(6))
    var matches: [GeneratedUniverseReason]

    @Guide(description: "Product-level aliases, acronyms, and formal filing terminology for the trend; exclude sector names and generic words such as technology or industry", .minimumCount(3), .maximumCount(8))
    var searchTerms: [String]
}

enum ResearchThemeLanguage {
    private static let requestWords = Set([
        "a", "about", "an", "and", "are", "business", "businesses", "company", "companies",
        "connected", "exposure", "exposed", "find", "for", "from", "identify", "in", "invest",
        "investment", "material", "most", "of", "or", "positioned", "public", "research", "sector",
        "stock", "stocks", "the", "theme", "to", "trend", "which", "with",
    ])

    private static let genericEvidenceWords = requestWords.union([
        "advanced", "application", "applications", "commercial", "global", "group", "industry",
        "industries", "market", "markets", "platform", "platforms", "product", "products", "service",
        "services", "solution", "solutions", "system", "systems", "technology", "technologies",
    ])

    static func conciseTheme(_ value: String) -> String {
        let words = value
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber && $0 != "-" })
            .map(String.init)
        let meaningful = words.filter { !requestWords.contains($0.lowercased()) }
        return (meaningful.isEmpty ? words : meaningful)
            .prefix(10)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func cleanSearchTerms(
        _ terms: [String],
        theme: String,
        universes: [String]
    ) -> [String] {
        let normalizedTheme = normalizedPhrase(theme)
        let universeTokens = Set(
            universes.flatMap { rawTokens(in: $0).map { stem($0.lowercased()) } }
        )
        var seen = Set<String>()
        return terms.compactMap { raw -> String? in
            let words = rawTokens(in: raw).filter { word in
                let token = stem(word.lowercased())
                return !universeTokens.contains(token) && !genericEvidenceWords.contains(token)
            }
            let term = words.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
            let normalized = normalizedPhrase(term)
            guard !normalized.isEmpty,
                  normalized != normalizedTheme,
                  seen.insert(normalized).inserted else { return nil }
            let tokens = tokenSet(in: term)
            guard tokens.contains(where: { !genericEvidenceWords.contains($0) }) else { return nil }
            return String(term.prefix(80))
        }
    }

    static func tokenSet(in values: [String]) -> Set<String> {
        Set(values.flatMap { tokenSet(in: $0) })
    }

    static func tokenSet(in value: String) -> Set<String> {
        Set(
            value.lowercased()
                .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
                .map { stem(String($0)) }
                .filter { $0.count >= 3 && !genericEvidenceWords.contains($0) }
        )
    }

    static func normalizedPhrase(_ value: String) -> String {
        value.lowercased()
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .map { stem(String($0)) }
            .joined(separator: " ")
    }

    private static func rawTokens(in value: String) -> [String] {
        value.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init)
    }

    private static func stem(_ value: String) -> String {
        if value == "aerial" { return "air" }
        if value.count > 5, value.hasSuffix("ies") {
            return String(value.dropLast(3)) + "y"
        }
        if value.count > 4,
           value.hasSuffix("s"),
           !value.hasSuffix("ss"),
           !value.hasSuffix("us"),
           !value.hasSuffix("is"),
           !value.hasSuffix("ous") {
            return String(value.dropLast())
        }
        return value
    }
}

public struct MappedTheme: Hashable, Sendable {
    public let theme: String
    public let matches: [ProposedItem]
    public let searchTerms: [String]

    public init(theme: String, matches: [ProposedItem], searchTerms: [String] = []) {
        self.theme = theme
        self.matches = matches
        self.searchTerms = searchTerms
    }
}

public protocol ThemeMapping: Sendable {
    func map(theme: String, question: String) async throws -> MappedTheme
}

public struct OnDeviceThemeMapper: ThemeMapping {
    public init() {}

    public func map(theme: String, question: String) async throws -> MappedTheme {
        let model = SystemLanguageModel.default
        guard model.isAvailable else {
            throw StockAgentError.unavailable(
                "Apple Intelligence is unavailable. Select the proposal universes manually."
            )
        }
        let session = LanguageModelSession(
            model: model,
            instructions: """
            Semantically map an open-ended business trend to the supplied equity taxonomy. Infer the
            commercial meaning rather than matching words. Classify the industry that builds, operates,
            or buys the end product as primary. Technology, software, AI, chips, sensors, and infrastructure
            are enabling—not primary—when they merely make another industry's product possible. Order
            primary product/end-market industries first, then material enabling inputs, then adjacent
            applications. Prefer a specific industry over its broad sector. Never choose companies or
            assess valuation. Separately produce several product-level search aliases: include acronyms,
            formal names, older filing terminology, and close commercial synonyms. Search aliases must
            not be sector labels or vague words such as technology, solutions, or industry.
            """
        )
        let conciseInput = ResearchThemeLanguage.conciseTheme(theme)
        let prompt = """
            Question: \(question)
            Business trend: \(conciseInput)
            Return only supported universes represented by the generated enum. Put the most direct
            commercial exposure first and explain each relationship concretely. Restate the trend using
            only its product or commercial concept, then supply distinct filing-language aliases for it.
            """
        let response = try await session.respond(
            to: prompt,
            generating: GeneratedThemeAudit.self
        )
        var seen = Set<String>()
        let orderedMatches = response.content.matches.enumerated().sorted { left, right in
            if left.element.role.rank != right.element.role.rank {
                return left.element.role.rank < right.element.role.rank
            }
            let leftName = left.element.universe.canonicalName
            let rightName = right.element.universe.canonicalName
            let leftSpecific = ResearchRegistry.industries.contains(leftName)
            let rightSpecific = ResearchRegistry.industries.contains(rightName)
            return leftSpecific == rightSpecific ? left.offset < right.offset : leftSpecific
        }.map(\.element)
        let matches: [ProposedItem] = orderedMatches.compactMap { match -> ProposedItem? in
            let scope = match.universe.canonicalName
            guard seen.insert(scope).inserted else { return nil }
            return ProposedItem(id: scope, reason: String(match.reason.prefix(300)))
        }
        let generatedTheme = ResearchThemeLanguage.conciseTheme(response.content.theme)
        let mappedTheme = generatedTheme.isEmpty ? conciseInput : generatedTheme
        let terms = ResearchThemeLanguage.cleanSearchTerms(
            response.content.searchTerms,
            theme: mappedTheme,
            universes: matches.map(\.id)
        )
        return MappedTheme(
            theme: mappedTheme.isEmpty ? theme : mappedTheme,
            matches: matches,
            searchTerms: Array(terms.prefix(8))
        )
    }
}

public struct ResearchPlanner: Sendable {
    private let themeMapper: any ThemeMapping

    public init(themeMapper: any ThemeMapping = OnDeviceThemeMapper()) {
        self.themeMapper = themeMapper
    }

    public func propose(question rawQuestion: String) async throws -> ResearchProposal {
        let question = rawQuestion.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else {
            throw StockAgentError.validation("Enter a research question.")
        }
        guard question.count <= 4_000 else {
            throw StockAgentError.validation("The research question is too long.")
        }

        let tickers = groundedTickerSymbols(in: question)
        let mode: ResearchMode = tickers.isEmpty ? .discovery : .named

        var universes = [String]()
        var reasons = [ProposedItem]()
        var warning: String?

        switch mode {
        case .named:
            break
        case .discovery:
            do {
                let mapping = try await themeMapper.map(theme: question, question: question)
                reasons = mapping.matches
                universes = reasons.map(\.id)
                warning = reasons.isEmpty ? "No automatic universe match was returned. Select one manually." : nil
                let normalizedTheme = mapping.theme.trimmingCharacters(in: .whitespacesAndNewlines)
                if !normalizedTheme.isEmpty {
                    // This phrase is model-derived from the request. It is not a trend dictionary.
                    return try ResearchRegistry.validate(
                        ResearchProposal(
                            question: question,
                            mode: mode,
                            securities: tickers,
                            universes: universes.isEmpty ? ["All public equities"] : universes,
                            universeReasons: reasons,
                            theme: normalizedTheme,
                            searchTerms: mapping.searchTerms,
                            resultCount: 3,
                            warning: warning
                        )
                    )
                }
            } catch {
                universes = ["All public equities"]
                warning = error.localizedDescription
            }
        }

        let proposal = ResearchProposal(
            question: question,
            mode: mode,
            securities: tickers,
            universes: universes,
            universeReasons: reasons,
            theme: mode == .discovery ? question : nil,
            resultCount: mode == .named ? max(1, tickers.count) : 3,
            warning: warning
        )
        return try ResearchRegistry.validate(proposal)
    }

    public func groundedTickerSymbols(in question: String) -> [String] {
        let pattern = #"(?<![A-Za-z0-9])(?:\$([A-Za-z]{1,5}(?:\.[A-Za-z])?)|([A-Z]{1,5}(?:\.[A-Z])?))(?![A-Za-z0-9])"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let range = NSRange(question.startIndex..., in: question)
        let excluded = Set(["A", "AI", "I", "US", "THE", "AND", "OR", "ETF", "CEO"])
        var seen = Set<String>()
        return regex.matches(in: question, range: range).compactMap { match in
            let capture = [1, 2].lazy.compactMap { index -> Range<String.Index>? in
                guard match.range(at: index).location != NSNotFound else { return nil }
                return Range(match.range(at: index), in: question)
            }.first
            guard let capture else { return nil }
            let symbol = String(question[capture]).uppercased()
            guard !excluded.contains(symbol), seen.insert(symbol).inserted else { return nil }
            return symbol
        }
    }

}
