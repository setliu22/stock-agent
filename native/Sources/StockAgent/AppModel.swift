import Foundation
import Observation
import StockAgentCore

@MainActor
@Observable
final class AppModel {
    var selectedSection: AppSection = .research
    var researchQuestion = ""
    var proposal: ResearchProposal?
    var report: ResearchReport?
    var isPlanning = false
    var isResearching = false
    var researchProgress = ""

    var purchases = [Purchase]()
    var holdings = [Holding]()
    var portfolioHistory = [PortfolioValuePoint]()
    var priceHistoryByTicker = [String: [PricePoint]]()
    var selectedTickers = Set<String>()
    var isLoadingPortfolio = false
    var isRefreshingPrices = false

    var marketRegime: MarketRegime?
    var isLoadingMarket = false
    var lsegConnected: Bool?

    var configuration: AccountConfiguration
    var notice: AppNotice?
    var overlay: AppOverlay?

    let databaseURL: URL
    private let portfolioStore: PortfolioStore
    private let planner = ResearchPlanner()
    private let priceService = YahooPriceService()
    private let marketService = FREDMarketService()
    private let lsegService: LSEGWorkspaceService
    private var researchEngine: ResearchEngine

    init() {
        let bundleParent = Bundle.main.bundleURL.pathExtension == "app"
            ? Bundle.main.bundleURL.deletingLastPathComponent()
            : URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        databaseURL = AppConfiguration.databaseURL()
        let environmentConfiguration = AppConfiguration.loadEnvironment(at: bundleParent)
        let initialConfiguration: AccountConfiguration
        if let saved = UserDefaults.standard.data(forKey: "StockAgent.configuration"),
           let decoded = try? JSONDecoder().decode(AccountConfiguration.self, from: saved) {
            initialConfiguration = decoded
        } else {
            initialConfiguration = environmentConfiguration
        }
        configuration = initialConfiguration
        lsegService = LSEGWorkspaceService(projectRoot: bundleParent)
        do {
            portfolioStore = try PortfolioStore(databaseURL: databaseURL)
        } catch {
            let fallback = FileManager.default.temporaryDirectory.appendingPathComponent("stock-agent-portfolio.db")
            portfolioStore = try! PortfolioStore(databaseURL: fallback)
        }
        researchEngine = ResearchEngine(
            sec: SECService(userAgent: initialConfiguration.secUserAgent),
            lseg: lsegService
        )
        applyPreviewArguments(CommandLine.arguments)
        Task { await checkLSEG() }
        Task {
            await loadPortfolio()
            await refreshPrices(showNotice: false, staleOnly: true)
            await loadMarket()
        }
    }

    private func applyPreviewArguments(_ arguments: [String]) {
        if let screenIndex = arguments.firstIndex(of: "--screen"), arguments.indices.contains(screenIndex + 1) {
            let value = arguments[screenIndex + 1].lowercased()
            selectedSection = AppSection.allCases.first { $0.rawValue.lowercased() == value } ?? selectedSection
        }
        if let selectionIndex = arguments.firstIndex(of: "--select"), arguments.indices.contains(selectionIndex + 1) {
            selectedTickers = Set(arguments[selectionIndex + 1].split(separator: ",").map { String($0).uppercased() })
        }
        if arguments.contains("--preview-proposal") {
            let question = "Which companies are positioned for autonomous drones?"
            researchQuestion = question
            proposal = ResearchProposal(
                question: question,
                mode: .discovery,
                universes: ["Aerospace & Defense", "Technology", "Semiconductors"],
                universeReasons: [
                    ProposedItem(id: "Aerospace & Defense", reason: "Direct exposure through aircraft, mission systems, sensors, and defense end markets."),
                    ProposedItem(id: "Technology", reason: "Enabling autonomy software and computing systems."),
                    ProposedItem(id: "Semiconductors", reason: "Processors and sensing components can enable onboard autonomy."),
                ],
                theme: "autonomous drones",
                capabilityIDs: ["macro_context", "candidate_discovery", "company_profile", "valuation_snapshot", "profitability_snapshot", "balance_sheet_snapshot"]
            )
        }
    }

    var totalCost: Double { holdings.reduce(0) { $0 + $1.totalCost } }

    var marketValue: Double? {
        let values = holdings.compactMap(\.marketValue)
        guard !values.isEmpty else { return nil }
        return values.reduce(0, +)
    }

    var portfolioGain: Double? { marketValue.map { $0 - totalCost } }

    func createProposal() async {
        guard !isPlanning else { return }
        isPlanning = true
        notice = nil
        do { proposal = try await planner.propose(question: researchQuestion) }
        catch { show(error) }
        isPlanning = false
    }

    func toggleUniverse(_ universe: String) {
        guard var proposal else { return }
        if let index = proposal.universes.firstIndex(of: universe) {
            proposal.universes.remove(at: index)
        } else if proposal.universes.count < 6 {
            proposal.universes.append(universe)
        } else {
            notice = AppNotice(style: .warning, title: "Six-universe limit", message: "Remove one universe before adding another.")
        }
        self.proposal = proposal
    }

    func toggleCapability(_ id: String) {
        guard var proposal,
              let capability = ResearchRegistry.capability(id: id), !capability.required else { return }
        if proposal.capabilityIDs.contains(id) { proposal.capabilityIDs.remove(id) }
        else { proposal.capabilityIDs.insert(id) }
        self.proposal = proposal
    }

    func toggleAnalysis(_ id: String) {
        guard var proposal else { return }
        if proposal.analysisIDs.contains(id) { proposal.analysisIDs.remove(id) }
        else { proposal.analysisIDs.insert(id) }
        self.proposal = proposal
    }

    func updateResultCount(_ value: Int) {
        proposal?.resultCount = min(8, max(1, value))
    }

    func runProposal() async {
        guard let proposal, !isResearching else { return }
        do { _ = try ResearchRegistry.validate(proposal) }
        catch { show(error); return }
        self.proposal = nil
        isResearching = true
        researchProgress = "Retrieving company evidence…"
        do { report = try await researchEngine.run(proposal) }
        catch { show(error) }
        researchProgress = ""
        isResearching = false
    }

    func loadPortfolio() async {
        isLoadingPortfolio = true
        do {
            async let loadedPurchases = portfolioStore.purchases()
            async let loadedHoldings = portfolioStore.holdings()
            async let loadedHistory = portfolioStore.portfolioHistory()
            purchases = try await loadedPurchases
            holdings = try await loadedHoldings
            portfolioHistory = try await loadedHistory
            var histories = [String: [PricePoint]]()
            for ticker in Set(purchases.map(\.ticker)) {
                histories[ticker] = try await portfolioStore.priceHistory(ticker: ticker)
            }
            priceHistoryByTicker = histories
            selectedTickers.formIntersection(Set(holdings.map(\.ticker)))
        } catch { show(error) }
        isLoadingPortfolio = false
    }

    func addPurchase(ticker: String, quantity: Double, price: Double, date: Date, note: String) async -> Bool {
        do {
            _ = try await portfolioStore.record(Purchase(ticker: ticker, quantity: quantity, price: price, purchasedAt: date, note: note))
            overlay = nil
            await loadPortfolio()
            return true
        } catch { show(error); return false }
    }

    func deleteTicker(_ ticker: String) async {
        do {
            _ = try await portfolioStore.deleteTicker(ticker)
            await loadPortfolio()
        } catch { show(error) }
    }

    func toggleTickerSelection(_ ticker: String) {
        if selectedTickers.contains(ticker) { selectedTickers.remove(ticker) }
        else { selectedTickers.insert(ticker) }
    }

    func valueHistory(for tickers: Set<String>) -> [PortfolioValuePoint] {
        let included = tickers.isEmpty ? Set(purchases.map(\.ticker)) : tickers
        let lots = purchases.filter { included.contains($0.ticker) }
        let histories = priceHistoryByTicker.filter { included.contains($0.key) }
        return PortfolioAnalytics.valueHistory(purchases: lots, priceHistory: histories)
    }

    func importPortfolioJSON(_ text: String) async -> Bool {
        do {
            let imported = try PortfolioImporter.parseJSON(text)
            _ = try await portfolioStore.record(imported)
            overlay = nil
            await loadPortfolio()
            notice = AppNotice(style: .success, title: "Portfolio imported", message: "Added \(imported.count) purchase \(imported.count == 1 ? "lot" : "lots").")
            return true
        } catch { show(error); return false }
    }

    func setManualPrice(ticker: String, price: Double) async -> Bool {
        do {
            try await portfolioStore.setManualPrice(ticker: ticker, close: price)
            overlay = nil
            await loadPortfolio()
            return true
        } catch { show(error); return false }
    }

    func importPriceCSV(ticker: String, data: Data) async {
        do {
            let points = try PortfolioImporter.parsePriceCSV(data)
            try await portfolioStore.importPriceHistory(ticker: ticker, points: points, source: "Imported CSV")
            await loadPortfolio()
            notice = AppNotice(style: .success, title: "Prices imported", message: "Loaded \(points.count) daily closes for \(ticker.uppercased()).")
        } catch { show(error) }
    }

    func refreshPrices(showNotice: Bool = true, staleOnly: Bool = false) async {
        guard !isRefreshingPrices else { return }
        let targets = staleOnly ? holdings.filter(needsPriceRefresh) : holdings
        guard !targets.isEmpty else { return }
        isRefreshingPrices = true
        var failures = [String]()
        for (index, holding) in targets.enumerated() {
            do {
                let firstPurchase = purchases
                    .filter { $0.ticker == holding.ticker }
                    .map(\.purchasedAt)
                    .min()
                let points = try await priceService.dailyPrices(
                    ticker: holding.ticker,
                    starting: firstPurchase
                )
                try await portfolioStore.importPriceHistory(
                    ticker: holding.ticker,
                    points: points,
                    source: "Yahoo Finance"
                )
            } catch { failures.append("\(holding.ticker): \(error.localizedDescription)") }
            if index < targets.count - 1 {
                try? await Task.sleep(for: .milliseconds(180))
            }
        }
        await loadPortfolio()
        isRefreshingPrices = false
        if showNotice {
            if failures.isEmpty {
                notice = AppNotice(style: .success, title: "Prices refreshed", message: "Daily price history is up to date.")
            } else {
                notice = AppNotice(style: .warning, title: "Some prices were not refreshed", message: failures.joined(separator: "\n"))
            }
        }
    }

    private func needsPriceRefresh(_ holding: Holding) -> Bool {
        guard let points = priceHistoryByTicker[holding.ticker], points.count >= 2,
              let latest = points.last?.date,
              let freshnessCutoff = Calendar.current.date(byAdding: .day, value: -4, to: .now) else {
            return true
        }
        return latest < freshnessCutoff
    }

    func loadMarket() async {
        guard !isLoadingMarket else { return }
        isLoadingMarket = true
        marketRegime = await marketService.regime()
        isLoadingMarket = false
    }

    func checkLSEG() async {
        lsegConnected = await lsegService.isAvailable()
    }

    func saveConfiguration() {
        if let data = try? JSONEncoder().encode(configuration) {
            UserDefaults.standard.set(data, forKey: "StockAgent.configuration")
        }
        researchEngine = ResearchEngine(
            sec: SECService(userAgent: configuration.secUserAgent),
            lseg: lsegService
        )
        notice = AppNotice(style: .success, title: "Settings saved", message: "Provider settings were updated on this Mac.")
    }

    func show(_ error: Error) {
        notice = AppNotice(style: .error, title: "Couldn’t complete that", message: error.localizedDescription)
    }
}

enum AppOverlay: Equatable {
    case addPurchase
    case importPortfolio
    case manualPrice(String)
}

struct AppNotice: Identifiable, Equatable {
    enum Style { case info, success, warning, error }
    let id = UUID()
    let style: Style
    let title: String
    let message: String
}
