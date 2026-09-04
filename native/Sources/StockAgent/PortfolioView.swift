import AppKit
import Charts
import StockAgentCore
import SwiftUI
import UniformTypeIdentifiers

struct PortfolioView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 25) {
                HStack(alignment: .bottom) {
                    PageHeader(
                        eyebrow: "",
                        title: "Portfolio",
                        subtitle: "Track holdings, purchase lots, and price history."
                    )
                    Spacer()
                    portfolioActions
                }

                PortfolioSummary()

                PortfolioChart()

                HoldingsList(importPriceCSV: choosePriceCSV)
            }
            .padding(.horizontal, 38)
            .padding(.top, 32)
            .padding(.bottom, 46)
            .frame(maxWidth: 1180, alignment: .leading)
        }
        .scrollIndicators(.never)
    }

    private var portfolioActions: some View {
        GlassEffectContainer(spacing: 8) {
            HStack(spacing: 8) {
                Button {
                    model.overlay = .importPortfolio
                } label: {
                    Label("Import", systemImage: "square.and.arrow.down")
                }
                .buttonStyle(.glass)
                .help("Import purchase lots from JSON")

                Button {
                    Task { await model.refreshPrices() }
                } label: {
                    if model.isRefreshingPrices {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }
                }
                .buttonStyle(.glass)
                .disabled(model.isRefreshingPrices || model.holdings.isEmpty)
                .help("Refresh daily prices for every holding")

                Button {
                    model.overlay = .addPurchase
                } label: {
                    Label("Add lot", systemImage: "plus")
                }
                .buttonStyle(.glassProminent)
                .help("Record a purchase lot")
            }
        }
    }

    private func choosePriceCSV(_ ticker: String) {
        let panel = NSOpenPanel()
        panel.title = "Import daily prices for \(ticker)"
        panel.message = "Choose a CSV containing Date and Close columns."
        panel.allowedContentTypes = [.commaSeparatedText, .plainText]
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url, let data = try? Data(contentsOf: url) else { return }
        Task { await model.importPriceCSV(ticker: ticker, data: data) }
    }
}

private struct PortfolioSummary: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        GlassPanel(cornerRadius: 28, padding: 0) {
            HStack(spacing: 0) {
                summaryValue("COST BASIS", value: money(model.totalCost), detail: "Across \(model.holdings.count) positions")
                separator
                summaryValue("MARKET VALUE", value: model.marketValue.map(money) ?? "—", detail: model.marketValue == nil ? "Add a current price or price history" : "Latest available prices")
                separator
                summaryValue("UNREALIZED", value: model.portfolioGain.map(signedMoney) ?? "—", detail: gainDetail, color: gainColor)
            }
            .frame(minHeight: 112)
        }
    }

    private var separator: some View {
        Rectangle().fill(StockTheme.border.opacity(0.45)).frame(width: 0.7, height: 54)
    }

    private var gainDetail: String {
        guard let gain = model.portfolioGain, model.totalCost > 0 else { return "Waiting for prices" }
        return (gain / model.totalCost * 100).formatted(.percent.scale(1).precision(.fractionLength(1)))
    }

    private var gainColor: Color {
        guard let gain = model.portfolioGain else { return StockTheme.text }
        return gain >= 0 ? StockTheme.positive : StockTheme.negative
    }

    private func summaryValue(_ label: String, value: String, detail: String, color: Color = StockTheme.text) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.system(size: 9, weight: .bold, design: .rounded))
                .tracking(1.0)
                .foregroundStyle(StockTheme.muted)
            Text(value)
                .font(.system(size: 22, weight: .bold, design: .rounded))
                .foregroundStyle(color)
                .contentTransition(.numericText())
            Text(detail)
                .font(.system(size: 10))
                .foregroundStyle(StockTheme.muted)
        }
        .padding(.horizontal, 24)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func money(_ value: Double) -> String {
        value.formatted(.currency(code: "USD").precision(.fractionLength(0...2)))
    }

    private func signedMoney(_ value: Double) -> String {
        (value >= 0 ? "+" : "") + money(value)
    }
}

private struct PortfolioChart: View {
    @Environment(AppModel.self) private var model
    @State private var range: ChartRange = .oneYear

    private enum ChartRange: String, CaseIterable, Identifiable {
        case oneMonth = "1M"
        case threeMonths = "3M"
        case oneYear = "1Y"
        case all = "All"

        var id: String { rawValue }
        var monthOffset: Int? {
            switch self {
            case .oneMonth: -1
            case .threeMonths: -3
            case .oneYear: -12
            case .all: nil
            }
        }
    }

    private struct Series: Identifiable {
        let id: String
        let points: [PortfolioValuePoint]
        let color: Color
    }

    var body: some View {
        GlassPanel(cornerRadius: 28, padding: 21) {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(chartTitle)
                            .font(.system(size: 16, weight: .semibold, design: .rounded))
                        Text(chartSubtitle)
                            .font(.system(size: 10))
                            .foregroundStyle(StockTheme.muted)
                    }
                    Spacer()
                    if !model.selectedTickers.isEmpty {
                        Button("Clear selection") { model.selectedTickers.removeAll() }
                            .buttonStyle(.glass)
                            .help("Show the full portfolio")
                    }
                    rangeSelector
                }

                if visibleSeries.allSatisfy({ $0.points.isEmpty }) {
                    ContentUnavailableView(
                        model.isRefreshingPrices ? "Loading price history" : "No chart data yet",
                        systemImage: "chart.line.uptrend.xyaxis",
                        description: Text(emptyChartMessage)
                    )
                    .foregroundStyle(StockTheme.muted)
                    .frame(height: 220)
                } else {
                    if model.selectedTickers.count > 1 {
                        HStack(spacing: 15) {
                            ForEach(visibleSeries) { series in
                                HStack(spacing: 5) {
                                    Circle().fill(series.color).frame(width: 7, height: 7)
                                    Text(series.id).font(.system(size: 10, weight: .semibold))
                                }
                            }
                        }
                    }
                    Chart {
                        ForEach(visibleSeries) { series in
                            ForEach(series.points) { point in
                                if visibleSeries.count == 1 {
                                    AreaMark(
                                        x: .value("Date", point.date),
                                        y: .value("Value", point.value)
                                    )
                                    .interpolationMethod(.monotone)
                                    .foregroundStyle(
                                        .linearGradient(
                                            colors: [series.color.opacity(0.24), series.color.opacity(0.01)],
                                            startPoint: .top,
                                            endPoint: .bottom
                                        )
                                    )
                                }
                                LineMark(
                                    x: .value("Date", point.date),
                                    y: .value("Value", point.value),
                                    series: .value("Position", series.id)
                                )
                                .interpolationMethod(.monotone)
                                .lineStyle(StrokeStyle(lineWidth: 3.0, lineCap: .round, lineJoin: .round))
                                .foregroundStyle(series.color)
                                if series.points.count == 1 {
                                    PointMark(
                                        x: .value("Date", point.date),
                                        y: .value("Value", point.value)
                                    )
                                    .foregroundStyle(series.color)
                                    .symbolSize(42)
                                }
                            }
                        }
                    }
                    .chartXAxis {
                        AxisMarks(values: .automatic(desiredCount: 5)) { value in
                            AxisGridLine().foregroundStyle(StockTheme.border.opacity(0.18))
                            AxisValueLabel(format: .dateTime.month(.abbreviated).day())
                                .foregroundStyle(StockTheme.muted)
                        }
                    }
                    .chartYAxis {
                        AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                            AxisGridLine().foregroundStyle(StockTheme.border.opacity(0.22))
                            AxisValueLabel()
                                .foregroundStyle(StockTheme.muted)
                        }
                    }
                    .frame(height: 250)
                }
            }
        }
    }

    private var rangeSelector: some View {
        HStack(spacing: 3) {
            ForEach(ChartRange.allCases) { candidate in
                Button(candidate.rawValue) { range = candidate }
                    .font(.system(size: 10, weight: .semibold, design: .rounded))
                    .buttonStyle(.plain)
                    .foregroundStyle(range == candidate ? StockTheme.text : StockTheme.muted)
                    .frame(width: 34, height: 27)
                    .background(
                        range == candidate ? StockTheme.accent.opacity(0.18) : .clear,
                        in: RoundedRectangle(cornerRadius: 8, style: .continuous)
                    )
                    .help("Show \(rangeDescription(candidate))")
            }
        }
        .padding(3)
        .background(StockTheme.background.opacity(0.35), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
    }

    private var chartTitle: String {
        if model.selectedTickers.isEmpty { return "Portfolio value" }
        if model.selectedTickers.count == 1 { return model.selectedTickers.first! }
        return "Selected positions"
    }

    private var chartSubtitle: String {
        if model.selectedTickers.isEmpty { return "Select holdings below to isolate them" }
        return model.selectedTickers.sorted().joined(separator: " · ")
    }

    private var rawSeries: [Series] {
        if model.selectedTickers.isEmpty {
            return [Series(id: "Portfolio", points: model.valueHistory(for: []), color: StockTheme.accentBright)]
        }
        let palette: [Color] = [
            StockTheme.accentBright,
            Color(red: 0.38, green: 0.82, blue: 0.86),
            Color(red: 0.96, green: 0.68, blue: 0.35),
            Color(red: 0.91, green: 0.49, blue: 0.74),
            Color(red: 0.47, green: 0.82, blue: 0.62),
        ]
        return model.selectedTickers.sorted().enumerated().compactMap { index, ticker in
            guard model.holdings.contains(where: { $0.ticker == ticker }) else { return nil }
            return Series(
                id: ticker,
                points: model.valueHistory(for: [ticker]),
                color: palette[index % palette.count]
            )
        }
    }

    private var emptyChartMessage: String {
        if model.isRefreshingPrices { return "Downloading daily closes for your holdings." }
        if model.holdings.isEmpty { return "Add a purchase lot to begin the portfolio history." }
        return "Daily prices could not be loaded. Try Refresh or import a price CSV."
    }

    private var visibleSeries: [Series] {
        let all = rawSeries
        guard let offset = range.monthOffset,
              let latest = all.flatMap({ $0.points.map(\.date) }).max(),
              let cutoff = Calendar.current.date(byAdding: .month, value: offset, to: latest) else {
            return all
        }
        return all.map { series in
            Series(id: series.id, points: series.points.filter { $0.date >= cutoff }, color: series.color)
        }
    }

    private func rangeDescription(_ range: ChartRange) -> String {
        switch range {
        case .oneMonth: "one month"
        case .threeMonths: "three months"
        case .oneYear: "one year"
        case .all: "all history"
        }
    }
}

private struct HoldingsList: View {
    @Environment(AppModel.self) private var model
    let importPriceCSV: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Holdings")
                    .font(.system(size: 19, weight: .bold, design: .rounded))
                Spacer()
                Text(model.selectedTickers.isEmpty ? "Select rows to update the chart" : "\(model.selectedTickers.count) selected")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(StockTheme.muted)
            }

            if model.holdings.isEmpty {
                GlassPanel {
                    HStack(spacing: 14) {
                        Image(systemName: "tray")
                            .font(.system(size: 20))
                            .foregroundStyle(StockTheme.accent)
                        Text("Add your first purchase lot or import portfolio JSON.")
                            .font(.system(size: 13))
                            .foregroundStyle(StockTheme.muted)
                    }
                }
            } else {
                GlassPanel(cornerRadius: 25, padding: 0) {
                    VStack(spacing: 0) {
                        holdingsHeader
                        ForEach(Array(model.holdings.enumerated()), id: \.element.id) { index, holding in
                            if index > 0 { Divider().overlay(StockTheme.border.opacity(0.4)).padding(.horizontal, 18) }
                            HoldingRow(holding: holding, importPriceCSV: importPriceCSV)
                        }
                    }
                }
            }
        }
    }

    private var holdingsHeader: some View {
        HStack {
            Text("SYMBOL").frame(maxWidth: .infinity, alignment: .leading)
            Text("SHARES").frame(width: 95, alignment: .trailing)
            Text("AVG COST").frame(width: 110, alignment: .trailing)
            Text("VALUE").frame(width: 120, alignment: .trailing)
            Text("RETURN").frame(width: 95, alignment: .trailing)
            Color.clear.frame(width: 30)
        }
        .font(.system(size: 9, weight: .bold, design: .rounded))
        .tracking(0.8)
        .foregroundStyle(StockTheme.muted)
        .padding(.horizontal, 20)
        .frame(height: 39)
    }
}

private struct HoldingRow: View {
    @Environment(AppModel.self) private var model
    let holding: Holding
    let importPriceCSV: (String) -> Void

    var body: some View {
        HStack {
            Button {
                model.toggleTickerSelection(holding.ticker)
            } label: {
                HStack {
                    HStack(spacing: 11) {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(ReturnColorScale.gradient(for: holding.returnPercent))
                            .frame(width: 32, height: 32)
                            .overlay(
                                Text(String(holding.ticker.prefix(1)))
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(.white.opacity(0.94))
                            )
                            .overlay(
                                RoundedRectangle(cornerRadius: 10, style: .continuous)
                                    .stroke(Color.white.opacity(0.13), lineWidth: 0.7)
                            )
                        Text(holding.ticker)
                            .font(.system(size: 14, weight: .bold, design: .rounded))
                        if model.selectedTickers.contains(holding.ticker) {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(StockTheme.accentBright)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Text(holding.quantity.formatted(.number.precision(.fractionLength(0...4))))
                        .frame(width: 95, alignment: .trailing)
                    Text(holding.averageCost.formatted(.currency(code: "USD").precision(.fractionLength(2))))
                        .frame(width: 110, alignment: .trailing)
                    Text(holding.marketValue?.formatted(.currency(code: "USD").precision(.fractionLength(0...2))) ?? "—")
                        .frame(width: 120, alignment: .trailing)
                    Text(holding.returnPercent.map { ($0 / 100).formatted(.percent.precision(.fractionLength(1))) } ?? "—")
                        .foregroundStyle(returnColor)
                        .frame(width: 95, alignment: .trailing)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("\(model.selectedTickers.contains(holding.ticker) ? "Remove" : "Add") \(holding.ticker) \(model.selectedTickers.contains(holding.ticker) ? "from" : "to") the chart")
            Menu {
                Button("Enter current price…") { model.overlay = .manualPrice(holding.ticker) }
                Button("Import price CSV…") { importPriceCSV(holding.ticker) }
                Divider()
                Button("Delete position", role: .destructive) {
                    Task { await model.deleteTicker(holding.ticker) }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .frame(width: 30, height: 30)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
        }
        .font(.system(size: 12, weight: .medium, design: .rounded))
        .padding(.horizontal, 20)
        .frame(height: 58)
        .contentShape(Rectangle())
        .background(
            model.selectedTickers.contains(holding.ticker) ? StockTheme.accent.opacity(0.055) : .clear
        )
    }

    private var returnColor: Color {
        ReturnColorScale.color(for: holding.returnPercent)
    }
}

private enum ReturnColorScale {
    static func color(for value: Double?) -> Color {
        guard let value else { return StockTheme.muted }
        if value >= 0 {
            return interpolate(
                from: (0.48, 0.78, 0.65),
                to: (0.0, 0.36, 0.23),
                amount: min(value / 50, 1)
            )
        }
        return interpolate(
            from: (0.94, 0.58, 0.62),
            to: (0.48, 0.04, 0.12),
            amount: min(abs(value) / 50, 1)
        )
    }

    static func gradient(for value: Double?) -> LinearGradient {
        let base = color(for: value)
        return LinearGradient(
            colors: [base.opacity(0.62), base],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    private static func interpolate(
        from: (Double, Double, Double),
        to: (Double, Double, Double),
        amount: Double
    ) -> Color {
        let amount = max(0, min(1, amount))
        return Color(
            red: from.0 + (to.0 - from.0) * amount,
            green: from.1 + (to.1 - from.1) * amount,
            blue: from.2 + (to.2 - from.2) * amount
        )
    }
}

struct PortfolioOverlay: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ZStack {
            Color.black.opacity(0.5)
                .background(.ultraThinMaterial.opacity(0.22))
                .ignoresSafeArea()
                .onTapGesture { model.overlay = nil }
            if let overlay = model.overlay {
                Group {
                    switch overlay {
                    case .addPurchase: AddPurchasePanel()
                    case .importPortfolio: ImportPortfolioPanel()
                    case .manualPrice(let ticker): ManualPricePanel(ticker: ticker)
                    }
                }
                .frame(maxWidth: 620)
                .padding(34)
            }
        }
        .accessibilityAddTraits(.isModal)
    }
}

private struct AddPurchasePanel: View {
    @Environment(AppModel.self) private var model
    @State private var ticker = ""
    @State private var shares = ""
    @State private var price = ""
    @State private var date = Date.now
    @State private var note = ""

    var body: some View {
        OverlayPanel(title: "Add purchase lot", subtitle: "Saved directly to your local portfolio database.") {
            VStack(spacing: 14) {
                HStack(spacing: 12) {
                    LabeledField(label: "TICKER") { TextField("AAPL", text: $ticker).stockTextField() }
                    LabeledField(label: "SHARES") { TextField("10", text: $shares).stockTextField() }
                    LabeledField(label: "PRICE") { TextField("185.00", text: $price).stockTextField() }
                }
                LabeledField(label: "PURCHASE DATE") {
                    DatePicker("", selection: $date, displayedComponents: .date)
                        .labelsHidden()
                        .datePickerStyle(.field)
                }
                LabeledField(label: "NOTE") { TextField("Optional", text: $note).stockTextField() }
            }
        } footer: {
            Button("Cancel") { model.overlay = nil }.buttonStyle(.glass)
            Button("Add lot") {
                Task {
                    _ = await model.addPurchase(
                        ticker: ticker,
                        quantity: Double(shares) ?? 0,
                        price: Double(price) ?? -1,
                        date: date,
                        note: note
                    )
                }
            }
            .buttonStyle(.glassProminent)
            .keyboardShortcut(.defaultAction)
        }
    }
}

private struct ImportPortfolioPanel: View {
    @Environment(AppModel.self) private var model
    @State private var json = ""

    var body: some View {
        OverlayPanel(title: "Import portfolio JSON", subtitle: "Accepts holdings, positions, purchases, assets, stocks, or securities arrays.") {
            ZStack(alignment: .topLeading) {
                if json.isEmpty {
                    Text("Paste portfolio JSON…")
                        .font(.system(size: 13))
                        .foregroundStyle(StockTheme.muted)
                        .padding(15)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $json)
                    .font(.system(size: 12, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .padding(8)
            }
            .frame(height: 240)
            .background(StockTheme.background.opacity(0.4), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(StockTheme.border.opacity(0.7), lineWidth: 0.7))
        } footer: {
            Button("Cancel") { model.overlay = nil }.buttonStyle(.glass)
            Button("Import") { Task { _ = await model.importPortfolioJSON(json) } }
                .buttonStyle(.glassProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(json.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
    }
}

private struct ManualPricePanel: View {
    @Environment(AppModel.self) private var model
    let ticker: String
    @State private var price = ""

    var body: some View {
        OverlayPanel(title: "Update \(ticker) price", subtitle: "Used until another price is imported.") {
            LabeledField(label: "CURRENT PRICE") {
                TextField("0.00", text: $price).stockTextField()
            }
        } footer: {
            Button("Cancel") { model.overlay = nil }.buttonStyle(.glass)
            Button("Save price") {
                Task { _ = await model.setManualPrice(ticker: ticker, price: Double(price) ?? -1) }
            }
            .buttonStyle(.glassProminent)
            .keyboardShortcut(.defaultAction)
        }
    }
}

private struct OverlayPanel<Content: View, Footer: View>: View {
    let title: String
    let subtitle: String
    @ViewBuilder let content: Content
    @ViewBuilder let footer: Footer

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(title).font(.system(size: 22, weight: .bold, design: .rounded))
                    Text(subtitle)
                        .font(.system(size: 11))
                        .foregroundStyle(StockTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
            .padding(24)
            Divider().overlay(StockTheme.border.opacity(0.6))
            content.padding(24)
            Divider().overlay(StockTheme.border.opacity(0.6))
            HStack { Spacer(); footer }
                .padding(.horizontal, 24)
                .padding(.vertical, 17)
        }
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 29, style: .continuous))
        .background(StockTheme.surface.opacity(0.68), in: RoundedRectangle(cornerRadius: 29, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 29, style: .continuous).stroke(Color.white.opacity(0.08), lineWidth: 0.8))
        .shadow(color: .black.opacity(0.46), radius: 38, y: 22)
    }
}

private struct LabeledField<Content: View>: View {
    let label: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(label)
                .font(.system(size: 9, weight: .bold, design: .rounded))
                .tracking(0.9)
                .foregroundStyle(StockTheme.muted)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
