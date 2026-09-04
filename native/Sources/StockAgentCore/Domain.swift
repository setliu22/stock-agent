import Foundation

public enum AppSection: String, CaseIterable, Identifiable, Sendable {
    case research = "Research"
    case portfolio = "Portfolio"
    case market = "Market"
    case account = "Settings"

    public var id: String { rawValue }

    public var symbol: String {
        switch self {
        case .research: "sparkle.magnifyingglass"
        case .portfolio: "chart.line.uptrend.xyaxis"
        case .market: "waveform.path.ecg"
        case .account: "gearshape"
        }
    }
}

public struct Purchase: Codable, Hashable, Identifiable, Sendable {
    public let id: Int64
    public var ticker: String
    public var quantity: Double
    public var price: Double
    public var purchasedAt: Date
    public var note: String

    public init(
        id: Int64 = 0,
        ticker: String,
        quantity: Double,
        price: Double,
        purchasedAt: Date,
        note: String = ""
    ) {
        self.id = id
        self.ticker = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        self.quantity = quantity
        self.price = price
        self.purchasedAt = purchasedAt
        self.note = note.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public func validated() throws -> Purchase {
        guard !ticker.isEmpty else { throw StockAgentError.validation("Enter a ticker.") }
        guard quantity > 0 else {
            throw StockAgentError.validation("Shares must be greater than zero.")
        }
        guard price >= 0 else {
            throw StockAgentError.validation("Purchase price cannot be negative.")
        }
        return self
    }
}

public struct Holding: Hashable, Identifiable, Sendable {
    public let ticker: String
    public let quantity: Double
    public let totalCost: Double
    public let averageCost: Double
    public var currentPrice: Double?

    public var id: String { ticker }
    public var marketValue: Double? { currentPrice.map { $0 * quantity } }
    public var gainLoss: Double? { marketValue.map { $0 - totalCost } }
    public var returnPercent: Double? {
        guard let gainLoss, totalCost != 0 else { return nil }
        return gainLoss / totalCost * 100
    }
}

public struct PricePoint: Codable, Hashable, Identifiable, Sendable {
    public let date: Date
    public let close: Double

    public var id: Date { date }

    public init(date: Date, close: Double) {
        self.date = date
        self.close = close
    }
}

public struct PortfolioValuePoint: Hashable, Identifiable, Sendable {
    public let date: Date
    public let value: Double
    public var id: Date { date }
}

public enum ResearchMode: String, Codable, CaseIterable, Hashable, Sendable {
    case named
    case discovery
    case marketNews
}

public struct ProposedItem: Codable, Hashable, Identifiable, Sendable {
    public var id: String
    public var reason: String

    public init(id: String, reason: String) {
        self.id = id
        self.reason = reason
    }
}

public struct ResearchProposal: Codable, Hashable, Identifiable, Sendable {
    public let id: UUID
    public var question: String
    public var mode: ResearchMode
    public var securities: [String]
    public var universes: [String]
    public var universeReasons: [ProposedItem]
    public var theme: String?
    public var searchTerms: [String]
    public var resultCount: Int
    public var lookbackDays: Int
    public var benchmark: String?
    public var capabilityIDs: Set<String>
    public var analysisIDs: Set<String>
    public var selectionObjectives: [String]
    public var warning: String?

    public init(
        id: UUID = UUID(),
        question: String,
        mode: ResearchMode,
        securities: [String] = [],
        universes: [String] = [],
        universeReasons: [ProposedItem] = [],
        theme: String? = nil,
        searchTerms: [String] = [],
        resultCount: Int = 5,
        lookbackDays: Int = 365,
        benchmark: String? = nil,
        capabilityIDs: Set<String> = [],
        analysisIDs: Set<String> = [],
        selectionObjectives: [String] = [],
        warning: String? = nil
    ) {
        self.id = id
        self.question = question
        self.mode = mode
        self.securities = securities
        self.universes = universes
        self.universeReasons = universeReasons
        self.theme = theme
        self.searchTerms = searchTerms
        self.resultCount = resultCount
        self.lookbackDays = lookbackDays
        self.benchmark = benchmark
        self.capabilityIDs = capabilityIDs
        self.analysisIDs = analysisIDs
        self.selectionObjectives = selectionObjectives
        self.warning = warning
    }
}

public struct ResearchMessage: Identifiable, Hashable, Sendable {
    public enum Role: Sendable { case user, assistant, status }
    public let id: UUID
    public let role: Role
    public let text: String

    public init(id: UUID = UUID(), role: Role, text: String) {
        self.id = id
        self.role = role
        self.text = text
    }
}

public struct ResearchCapability: Identifiable, Hashable, Sendable {
    public let id: String
    public let label: String
    public let source: String
    public let modes: Set<ResearchMode>
    public let required: Bool

    public init(
        id: String,
        label: String,
        source: String,
        modes: Set<ResearchMode>,
        required: Bool = false
    ) {
        self.id = id
        self.label = label
        self.source = source
        self.modes = modes
        self.required = required
    }
}

public struct ResearchAnalysis: Identifiable, Hashable, Sendable {
    public let id: String
    public let label: String
    public let requiredCapabilities: Set<String>

    public init(id: String, label: String, requiredCapabilities: Set<String>) {
        self.id = id
        self.label = label
        self.requiredCapabilities = requiredCapabilities
    }
}

public struct CompanyCandidate: Identifiable, Hashable, Sendable {
    public let cik: String
    public let ticker: String
    public let name: String
    public let filingDate: Date?
    public let filingURL: URL?
    public let relevance: Double

    public var id: String { cik }
}

public enum ExposureStrength: String, Codable, Hashable, Sendable {
    case direct = "Direct exposure"
    case enabling = "Enabling exposure"
    case adjacent = "Adjacent exposure"
    case incidental = "Incidental mention"
    case unreviewed = "Needs review"
    case profile = "SEC data"
}

public struct ResearchCompanyResult: Identifiable, Hashable, Sendable {
    public let candidate: CompanyCandidate
    public let exposure: ExposureStrength
    public let thesis: String
    public let evidence: [String]
    public let snapshot: CompanySnapshot?
    public let sources: [String]

    public var id: String { candidate.id }

    public init(
        candidate: CompanyCandidate,
        exposure: ExposureStrength,
        thesis: String,
        evidence: [String],
        snapshot: CompanySnapshot?,
        sources: [String] = []
    ) {
        self.candidate = candidate
        self.exposure = exposure
        self.thesis = thesis
        self.evidence = evidence
        self.snapshot = snapshot
        self.sources = sources
    }
}

public struct ResearchReport: Identifiable, Hashable, Sendable {
    public let id: UUID
    public let question: String
    public let title: String
    public let generatedAt: Date
    public let companies: [ResearchCompanyResult]
    public let notes: [String]

    public init(
        id: UUID = UUID(),
        question: String,
        title: String,
        generatedAt: Date = .now,
        companies: [ResearchCompanyResult],
        notes: [String] = []
    ) {
        self.id = id
        self.question = question
        self.title = title
        self.generatedAt = generatedAt
        self.companies = companies
        self.notes = notes
    }
}

public struct CompanySnapshot: Identifiable, Hashable, Sendable {
    public let cik: String
    public let ticker: String
    public let name: String
    public let description: String
    public let facts: [FinancialFact]
    public let recentFilings: [Filing]

    public var id: String { cik }
}

public struct FinancialFact: Hashable, Sendable {
    public let label: String
    public let value: Double
    public let unit: String
    public let periodEnd: Date?
    public let source: String

    public init(
        label: String,
        value: Double,
        unit: String,
        periodEnd: Date?,
        source: String = "SEC EDGAR"
    ) {
        self.label = label
        self.value = value
        self.unit = unit
        self.periodEnd = periodEnd
        self.source = source
    }
}

public struct Filing: Hashable, Identifiable, Sendable {
    public let accessionNumber: String
    public let form: String
    public let filedAt: Date?
    public let primaryDocument: String
    public var id: String { accessionNumber }
}

public struct MarketIndicator: Identifiable, Hashable, Sendable {
    public enum Tilt: String, Sendable {
        case defensive = "Defensive"
        case neutral = "Neutral"
        case tolerant = "Risk tolerant"
        case unavailable = "Unavailable"
    }

    public let id: String
    public let label: String
    public let latest: Double?
    public let previous: Double?
    public let unit: String
    public let asOf: Date?
    public let tilt: Tilt
    public let changeDescription: String
    public let source: String

    public var change: Double? {
        guard let latest, let previous else { return nil }
        return latest - previous
    }
}

public struct MarketRegime: Hashable, Sendable {
    public let asOf: Date
    public let label: String
    public let stance: MarketIndicator.Tilt
    public let summary: String
    public let indicators: [MarketIndicator]

    public init(
        asOf: Date = .now,
        label: String,
        stance: MarketIndicator.Tilt,
        summary: String,
        indicators: [MarketIndicator]
    ) {
        self.asOf = asOf
        self.label = label
        self.stance = stance
        self.summary = summary
        self.indicators = indicators
    }
}

public struct AccountConfiguration: Codable, Equatable, Sendable {
    public var secUserAgent: String

    public init(
        secUserAgent: String = "Stock Agent local-research contact@example.com"
    ) {
        self.secUserAgent = secUserAgent
    }
}

public enum StockAgentError: LocalizedError, Equatable {
    case validation(String)
    case storage(String)
    case network(String)
    case unavailable(String)
    case malformedResponse(String)

    public var errorDescription: String? {
        switch self {
        case .validation(let message), .storage(let message), .network(let message),
             .unavailable(let message), .malformedResponse(let message):
            message
        }
    }
}
