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
        }
        return proposal
    }
}
