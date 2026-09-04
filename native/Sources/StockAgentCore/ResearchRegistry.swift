import Foundation

public enum ResearchRegistry {
    public struct LSEGScreenDefinition: Hashable, Sendable {
        public let label: String
        public let field: String?
        public let codes: [String]

        public func screenBody(top: Int) -> String {
            var clauses = ["U(IN(Equity(active,public,primary)))/*UNV:Public*/"]
            if let field, !codes.isEmpty {
                let quoted = codes.map { "\"\($0)\"" }.joined(separator: ",")
                clauses.append("IN(\(field),\(quoted))")
            }
            clauses.append("TOP(TR.CompanyMarketCap,\(max(1, min(top, 50))),nnumber)")
            clauses.append("CURN=USD")
            return clauses.joined(separator: ", ")
        }
    }

    public static let sectors = [
        "Energy",
        "Basic Materials",
        "Industrials",
        "Consumer Cyclicals",
        "Consumer Non-Cyclicals",
        "Financials",
        "Healthcare",
        "Technology",
        "Telecommunications Services",
        "Utilities",
        "Real Estate",
    ]

    public static let industries = [
        "Aerospace & Defense",
        "Automobiles & Auto Parts",
        "Banks",
        "Biotechnology & Medical Research",
        "Insurance",
        "Medical Equipment & Supplies",
        "Oil & Gas",
        "Pharmaceuticals",
        "Semiconductor Equipment",
        "Semiconductors",
        "Semiconductors & Semiconductor Equipment",
        "Software",
    ]

    public static let universes = ["All public equities"] + sectors + industries

    private static let lsegScreens: [String: LSEGScreenDefinition] = {
        let sectorCodes = [
            "Energy": "50", "Basic Materials": "51", "Industrials": "52",
            "Consumer Cyclicals": "53", "Consumer Non-Cyclicals": "54",
            "Financials": "55", "Healthcare": "56", "Technology": "57",
            "Telecommunications Services": "58", "Utilities": "59", "Real Estate": "60",
        ]
        var output = Dictionary(uniqueKeysWithValues: sectorCodes.map { label, code in
            (label, LSEGScreenDefinition(label: label, field: "TR.TRBCEconSectorCode", codes: [code]))
        })
        output["All public equities"] = .init(label: "All public equities", field: nil, codes: [])
        let industries: [(String, String, [String])] = [
            ("Aerospace & Defense", "TR.TRBCIndustryCode", ["52101010"]),
            ("Automobiles & Auto Parts", "TR.TRBCIndustryGroupCode", ["531010"]),
            ("Banks", "TR.TRBCIndustryCode", ["55101010"]),
            ("Biotechnology & Medical Research", "TR.TRBCIndustryCode", ["56202010"]),
            ("Insurance", "TR.TRBCBusinessSectorCode", ["5530"]),
            ("Medical Equipment & Supplies", "TR.TRBCIndustryGroupCode", ["561010"]),
            ("Oil & Gas", "TR.TRBCIndustryGroupCode", ["501020"]),
            ("Pharmaceuticals", "TR.TRBCIndustryGroupCode", ["562010"]),
            ("Semiconductor Equipment", "TR.TRBCIndustryCode", ["57101020"]),
            ("Semiconductors", "TR.TRBCIndustryCode", ["57101010"]),
            ("Semiconductors & Semiconductor Equipment", "TR.TRBCIndustryGroupCode", ["571010"]),
            ("Software", "TR.TRBCIndustryCode", ["57201020"]),
        ]
        for (label, field, codes) in industries {
            output[label] = .init(label: label, field: field, codes: codes)
        }
        return output
    }()

    public static func lsegScreenDefinition(for universe: String) -> LSEGScreenDefinition? {
        lsegScreens[universe]
    }

    public static let exchangeMarkets = [
        "All exchanges",
        "United States",
        "Europe",
        "Canada",
        "United Kingdom",
        "Australia",
        "China",
        "Hong Kong",
        "India",
        "Japan",
        "South Korea",
        "Singapore",
        "Taiwan",
        "Brazil",
        "Mexico",
        "South Africa",
    ]

    public static let capabilities: [ResearchCapability] = [
        .init(
            id: "macro_context",
            label: "Current macro regime",
            source: "Public macro data or configured provider",
            modes: [.named, .discovery, .marketNews]
        ),
        .init(
            id: "candidate_discovery",
            label: "Candidate discovery",
            source: "SEC filings or configured provider",
            modes: [.discovery],
            required: true
        ),
        .init(
            id: "company_profile",
            label: "Company profile",
            source: "SEC EDGAR or configured provider",
            modes: [.named, .discovery],
            required: true
        ),
        .init(
            id: "price_history",
            label: "Stock price history",
            source: "Automatic daily prices or imported CSV",
            modes: [.named, .discovery]
        ),
        .init(
            id: "benchmark_prices",
            label: "Benchmark price history",
            source: "Imported CSV",
            modes: [.named, .discovery]
        ),
        .init(
            id: "valuation_snapshot",
            label: "Valuation snapshot",
            source: "SEC filings plus market prices",
            modes: [.named, .discovery]
        ),
        .init(
            id: "profitability_snapshot",
            label: "Profitability snapshot",
            source: "SEC company facts",
            modes: [.named, .discovery],
            required: true
        ),
        .init(
            id: "balance_sheet_snapshot",
            label: "Cash flow and balance sheet",
            source: "SEC company facts",
            modes: [.named, .discovery],
            required: true
        ),
        .init(
            id: "earnings_estimates",
            label: "Earnings estimates",
            source: "Configured provider",
            modes: [.named, .discovery]
        ),
        .init(
            id: "analyst_opinion",
            label: "Analyst opinion and targets",
            source: "Configured provider",
            modes: [.named, .discovery]
        ),
        .init(
            id: "estimate_revisions",
            label: "Estimate revision history",
            source: "Configured provider",
            modes: [.named, .discovery]
        ),
        .init(
            id: "company_news",
            label: "Company news",
            source: "SEC filings or configured provider",
            modes: [.named, .discovery]
        ),
        .init(
            id: "corporate_events",
            label: "Upcoming corporate events",
            source: "SEC filings or configured provider",
            modes: [.named, .discovery]
        ),
        .init(
            id: "risk_snapshot",
            label: "Market and financing risk",
            source: "SEC facts plus configured prices",
            modes: [.named, .discovery]
        ),
        .init(
            id: "ownership_snapshot",
            label: "Institutional ownership snapshot",
            source: "SEC 13F filings or configured provider",
            modes: [.named]
        ),
        .init(
            id: "insider_activity",
            label: "Insider activity",
            source: "SEC Forms 3, 4, and 5",
            modes: [.named]
        ),
        .init(
            id: "regulatory_filings",
            label: "Regulatory filings",
            source: "SEC EDGAR",
            modes: [.named, .discovery]
        ),
        .init(
            id: "peer_context",
            label: "Peer context",
            source: "SEC industry classification or configured provider",
            modes: [.named, .discovery]
        ),
        .init(
            id: "supplier_context",
            label: "Supplier relationships",
            source: "Configured provider",
            modes: [.named, .discovery]
        ),
        .init(
            id: "customer_context",
            label: "Customer relationships",
            source: "Configured provider",
            modes: [.named, .discovery]
        ),
        .init(
            id: "market_news",
            label: "Market news",
            source: "SEC filings or configured provider",
            modes: [.marketNews],
            required: true
        ),
        .init(
            id: "fed_funds_history",
            label: "Federal funds history",
            source: "Federal Reserve public data",
            modes: [.named, .discovery]
        ),
        .init(
            id: "treasury_yield_history",
            label: "10-year Treasury yield history",
            source: "U.S. Treasury public data",
            modes: [.named, .discovery]
        ),
    ]

    public static let analyses: [ResearchAnalysis] = [
        .init(
            id: "return_comparison",
            label: "Period return comparison",
            requiredCapabilities: ["price_history"]
        ),
        .init(
            id: "benchmark_excess_return",
            label: "Excess return versus benchmark",
            requiredCapabilities: ["price_history", "benchmark_prices"]
        ),
        .init(
            id: "maximum_drawdown",
            label: "Maximum drawdown",
            requiredCapabilities: ["price_history"]
        ),
        .init(
            id: "annualized_volatility",
            label: "Annualized volatility",
            requiredCapabilities: ["price_history"]
        ),
        .init(
            id: "rate_change_correlation",
            label: "Rate-change correlation",
            requiredCapabilities: ["price_history"]
        ),
        .init(
            id: "falling_rate_comparison",
            label: "Falling-rate period comparison",
            requiredCapabilities: ["price_history"]
        ),
        .init(
            id: "estimate_revision_change",
            label: "Estimate revision comparison",
            requiredCapabilities: ["estimate_revisions"]
        ),
    ]

    public static func capability(id: String) -> ResearchCapability? {
        capabilities.first { $0.id == id }
    }

    public static func validate(_ proposal: ResearchProposal) throws -> ResearchProposal {
        let question = proposal.question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else {
            throw StockAgentError.validation("Enter a research question.")
        }
        guard question.count <= 4_000 else {
            throw StockAgentError.validation("The research question is too long.")
        }
        guard (1...8).contains(proposal.resultCount) else {
            throw StockAgentError.validation("Choose between one and eight results.")
        }
        guard proposal.lookbackDays > 0 else {
            throw StockAgentError.validation("Choose a positive research timeframe.")
        }
        let allowedCapabilities = Set(capabilities.map(\.id))
        let allowedAnalyses = Set(analyses.map(\.id))
        guard proposal.capabilityIDs.isSubset(of: allowedCapabilities) else {
            throw StockAgentError.validation("The proposal contains an unsupported data source.")
        }
        guard proposal.analysisIDs.isSubset(of: allowedAnalyses) else {
            throw StockAgentError.validation("The proposal contains an unsupported calculation.")
        }
        switch proposal.mode {
        case .named:
            guard !proposal.securities.isEmpty else {
                throw StockAgentError.validation("Add at least one company or ticker.")
            }
        case .discovery:
            guard !proposal.universes.isEmpty else {
                throw StockAgentError.validation("Select at least one industry or sector.")
            }
            guard proposal.universes.count <= 6,
                  Set(proposal.universes).isSubset(of: Set(universes)) else {
                throw StockAgentError.validation("The proposal contains an unsupported universe.")
            }
        case .marketNews:
            break
        }
        return proposal
    }
}
