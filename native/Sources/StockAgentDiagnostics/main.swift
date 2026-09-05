import Foundation
import StockAgentCore

@main
struct StockAgentDiagnostics {
    static func main() async {
        var arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.first == "--market" {
            let regime = await FREDMarketService().regime()
            print("Regime: \(regime.label)")
            for indicator in regime.indicators {
                let latest = indicator.latest.map { String($0) } ?? "unavailable"
                print("- \(indicator.id): \(latest) \(indicator.unit); \(indicator.changeDescription)")
            }
            return
        }
        let performsResearch = arguments.first == "--run"
        if performsResearch { arguments.removeFirst() }
        let question = arguments.joined(separator: " ")
        guard !question.isEmpty else {
            print("Usage: StockAgentDiagnostics [--run] <research question> | --market")
            return
        }
        do {
            let proposal = try await ResearchPlanner().propose(question: question)
            print("Mode: \(proposal.mode.rawValue)")
            print("Theme: \(proposal.theme ?? "—")")
            if !proposal.searchTerms.isEmpty {
                print("Search terms: \(proposal.searchTerms.joined(separator: ", "))")
            }
            print("Universes:")
            for universe in proposal.universes {
                let reason = proposal.universeReasons.first(where: { $0.id == universe })?.reason ?? ""
                print("- \(universe): \(reason)")
            }
            if let warning = proposal.warning { print("Warning: \(warning)") }
            if performsResearch {
                let current = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                let projectRoot = FileManager.default.fileExists(
                    atPath: current.appendingPathComponent("scripts/lseg_bridge.py").path
                ) ? current : current.deletingLastPathComponent()
                let configuration = AppConfiguration.loadEnvironment(at: projectRoot)
                let report = try await ResearchEngine(
                    sec: SECService(userAgent: configuration.secUserAgent),
                    lseg: LSEGWorkspaceService(projectRoot: projectRoot)
                ).run(proposal)
                print("Report: \(report.title)")
                for company in report.companies {
                    print("\n\(company.candidate.ticker) — \(company.candidate.name)")
                    print(company.thesis)
                    if let investmentCase = company.investmentCase {
                        print("Investment view [\(investmentCase.stance.rawValue)]: \(investmentCase.summary)")
                        for point in investmentCase.reasons { print("+ \(point.text)") }
                        for point in investmentCase.watchouts { print("! \(point.text)") }
                    }
                    print("Evidence excerpts: \(company.evidence.count)")
                    print("Filing: \(company.candidate.filingURL?.absoluteString ?? "unavailable")")
                }
                for note in report.notes { print("Note: \(note)") }
            }
        } catch {
            print("Error: \(error.localizedDescription)")
            Foundation.exit(1)
        }
    }
}
