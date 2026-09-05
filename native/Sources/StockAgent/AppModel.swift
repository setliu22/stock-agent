import Foundation
import FoundationModels
import Observation
import StockAgentCore

@MainActor
@Observable
final class AppModel {
    var selectedSection: AppSection = .research
    var researchQuestion = ""
    var researchMode: ResearchMode?
    var proposal: ResearchProposal?
    var report: ResearchReport?
    var isPlanning = false
    var isResearching = false
    var researchProgress = ""

    var purchases = [Purchase]()
    var holdings = [Holding]()
    var priceHistoryByTicker = [String: [PricePoint]]()
    var selectedTickers = Set<String>()
    var isLoadingPortfolio = false
    var isRefreshingPrices = false

    var marketRegime: MarketRegime?
    var isLoadingMarket = false
    var lsegConnected: Bool?
    var appleIntelligenceAvailable = false
    var isCheckingConnections = false

    var notice: AppNotice?
    var overlay: AppOverlay?

    let databaseURL: URL
    private let portfolioStore: PortfolioStore?
    private let planner = ResearchPlanner()
    private let priceService = YahooPriceService()
    private let marketService = FREDMarketService()
    private let lsegService: LSEGWorkspaceService
    private let researchEngine: ResearchEngine
    private var researchTask: Task<Void, Never>?
    private var planningTask: Task<Void, Never>?

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
        lsegService = LSEGWorkspaceService(projectRoot: bundleParent)
        do {
            portfolioStore = try PortfolioStore(databaseURL: databaseURL)
        } catch {
            portfolioStore = nil
            notice = AppNotice(style: .error, title: "Portfolio could not be opened", message: error.localizedDescription)
        }
        researchEngine = ResearchEngine(
            sec: SECService(userAgent: initialConfiguration.secUserAgent),
            lseg: lsegService
        )
        applyPreviewArguments(CommandLine.arguments)
        Task { await checkConnections() }
        Task {
            await loadPortfolio()
            await refreshPrices(showNotice: false, staleOnly: true)
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
                resultCount: 3
            )
        }
    }

    var totalCost: Double { holdings.reduce(0) { $0 + $1.totalCost } }

    var marketValue: Double? {
        let values = holdings.compactMap(\.marketValue)
        guard !values.isEmpty, values.count == holdings.count else { return nil }
        let total = values.reduce(0, +)
        return total.isFinite ? total : nil
    }

    var portfolioGain: Double? { marketValue.map { $0 - totalCost } }

    func createProposal() async {
        guard !isPlanning, !isResearching else { return }
        isPlanning = true
        notice = nil
        let question = researchQuestion
        let mode = researchMode
        planningTask = Task {
            defer { isPlanning = false; planningTask = nil }
            do {
                let value = try await planner.propose(question: question, preferredMode: mode)
                try Task.checkCancellation()
                proposal = value
            } catch is CancellationError { }
            catch { show(error) }
        }
        await planningTask?.value
    }

    func toggleUniverse(_ universe: String) {
        guard var proposal else { return }
        if let index = proposal.universes.firstIndex(of: universe) {
            proposal.universes.remove(at: index)
        } else {
            if universe == "All public equities" {
                proposal.universes = [universe]
            } else {
                proposal.universes.removeAll { $0 == "All public equities" }
                guard proposal.universes.count < 6 else {
                    notice = AppNotice(style: .warning, title: "Six-industry limit", message: "Remove one industry before adding another.")
                    return
                }
                proposal.universes.append(universe)
            }
        }
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
        report = nil
        researchTask = Task {
            defer { researchProgress = ""; isResearching = false; researchTask = nil }
            do {
                let value = try await researchEngine.run(proposal)
                try Task.checkCancellation()
                report = value
            } catch is CancellationError { }
            catch { show(error) }
        }
        await researchTask?.value
    }

    func cancelResearch() {
        planningTask?.cancel()
        researchTask?.cancel()
    }

    private func store() throws -> PortfolioStore {
        guard let portfolioStore else {
            throw StockAgentError.unavailable("The portfolio database could not be opened. Your existing data has not been replaced. Restart after checking disk access.")
        }
        return portfolioStore
    }

    func loadPortfolio() async {
        isLoadingPortfolio = true
        do {
            let portfolioStore = try store()
            async let loadedPurchases = portfolioStore.adjustedPurchases()
            async let loadedHoldings = portfolioStore.holdings()
            purchases = try await loadedPurchases
            holdings = try await loadedHoldings
            var histories = [String: [PricePoint]]()
            for ticker in Set(purchases.map(\.ticker)) {
                if await portfolioStore.hasVerifiedPriceHistory(ticker: ticker) {
                    histories[ticker] = await portfolioStore.priceHistory(ticker: ticker)
                }
            }
            priceHistoryByTicker = histories
            let freshnessCutoff = Date.now.addingTimeInterval(-4 * 86_400)
            for index in holdings.indices {
                if histories[holdings[index].ticker]?.last?.date ?? .distantPast < freshnessCutoff {
                    holdings[index].currentPrice = nil
                }
            }
            selectedTickers.formIntersection(Set(holdings.map(\.ticker)))
        } catch { show(error) }
        isLoadingPortfolio = false
    }

    func addPurchase(ticker: String, quantity: Double, price: Double, date: Date, note: String) async -> Bool {
        do {
            _ = try await store().record(Purchase(ticker: ticker, quantity: quantity, price: price, purchasedAt: date, note: note))
            overlay = nil
            await loadPortfolio()
            Task { await refreshPrices(showNotice: false, staleOnly: true) }
            return true
        } catch { show(error); return false }
    }

    func deleteTicker(_ ticker: String) async {
        do {
            _ = try await store().deleteTicker(ticker)
            await loadPortfolio()
        } catch { show(error) }
    }

    func toggleTickerSelection(_ ticker: String) {
        if selectedTickers.contains(ticker) { selectedTickers.remove(ticker) }
        else { selectedTickers.insert(ticker) }
    }

    func portfolioPerformanceIndex() -> [PortfolioValuePoint] {
        PortfolioAnalytics.returnsOnCost(
            purchases: purchases,
            priceHistory: priceHistoryByTicker
        )
    }

    func importPortfolioJSON(_ text: String) async -> Bool {
        do {
            let imported = try PortfolioImporter.parseJSON(text)
            _ = try await store().record(imported)
            overlay = nil
            await loadPortfolio()
            notice = AppNotice(style: .success, title: "Portfolio imported", message: "Added \(imported.count) purchase \(imported.count == 1 ? "lot" : "lots").")
            Task { await refreshPrices(showNotice: false, staleOnly: true) }
            return true
        } catch { show(error); return false }
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
                let history = try await priceService.history(
                    ticker: holding.ticker,
                    starting: firstPurchase
                )
                try await store().importPriceHistory(
                    ticker: holding.ticker,
                    points: history.prices,
                    source: "Yahoo Finance (split-adjusted)",
                    splits: history.splits
                )
            } catch { failures.append("\(holding.ticker): \(error.localizedDescription)") }
            if index < targets.count - 1 {
                try? await Task.sleep(for: .milliseconds(180))
            }
        }
        await loadPortfolio()
        isRefreshingPrices = false
        if showNotice || !failures.isEmpty {
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

    func checkConnections() async {
        guard !isCheckingConnections else { return }
        isCheckingConnections = true
        appleIntelligenceAvailable = await Task.detached(priority: .utility) {
            SystemLanguageModel.default.isAvailable
        }.value
        lsegConnected = await lsegService.isAvailable()
        isCheckingConnections = false
    }

    func show(_ error: Error) {
        notice = AppNotice(style: .error, title: "Couldn’t complete that", message: error.localizedDescription)
    }
}

enum AppOverlay: Equatable {
    case addPurchase
    case importPortfolio
}

struct AppNotice: Identifiable, Equatable {
    enum Style { case info, success, warning, error }
    let id = UUID()
    let style: Style
    let title: String
    let message: String
}
