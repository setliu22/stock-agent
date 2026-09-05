import AppKit
import Charts
import StockAgentCore
import SwiftUI
import UniformTypeIdentifiers

struct PortfolioView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        GeometryReader { geometry in
            let compact = geometry.size.width < 760
            ScrollView {
                VStack(alignment: .leading, spacing: 25) {
                    ViewThatFits(in: .horizontal) {
                        HStack(alignment: .bottom, spacing: 24) {
                            portfolioHeader
                            Spacer()
                            portfolioActions
                        }
                        VStack(alignment: .leading, spacing: 16) {
                            portfolioHeader
                            portfolioActions
                        }
                    }

                    PortfolioSummary()

                    PortfolioChart()

                    HoldingsList(compact: compact)
                }
                .padding(.horizontal, compact ? 24 : 38)
                .padding(.top, 32)
                .padding(.bottom, 46)
                .frame(maxWidth: 1180, alignment: .leading)
            }
            .scrollIndicators(.never)
            .defaultScrollAnchor(.top)
        }
    }

    private var portfolioHeader: some View {
        PageHeader(eyebrow: "", title: "Portfolio", subtitle: "")
    }

    private var portfolioActions: some View {
        GlassEffectContainer(spacing: 8) {
            HStack(spacing: 8) {
                Button {
                    model.overlay = .importPortfolio
                } label: {
                    Label("Import", systemImage: "square.and.arrow.down")
                }
                .stockSecondaryButton()
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
                .stockSecondaryButton()
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

}

private struct PortfolioSummary: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        GlassPanel(cornerRadius: 28, padding: 0) {
            HStack(spacing: 0) {
                summaryValue("COST BASIS", value: money(model.totalCost), detail: "Across \(model.holdings.count) positions")
                separator
                summaryValue("MARKET VALUE", value: model.marketValue.map(money) ?? "—", detail: model.marketValue == nil ? "Market prices unavailable" : "Latest available closes")
                separator
                summaryValue("RETURN ON COST", value: gainDetail, detail: model.portfolioGain.map(signedMoney) ?? "Waiting for prices", color: gainColor)
            }
            .frame(minHeight: 112)
        }
    }

    private var separator: some View {
        Rectangle().fill(StockTheme.border.opacity(0.45)).frame(width: 0.7, height: 54)
    }

    private var gainDetail: String {
        guard let gain = model.portfolioGain, model.totalCost > 0 else { return "Waiting for prices" }
        return (gain / model.totalCost).formatted(.percent.precision(.fractionLength(1)))
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
                .lineLimit(1)
                .minimumScaleFactor(0.7)
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
        case sixMonths = "6M"
        case oneYear = "1Y"
        case all = "All"

        var id: String { rawValue }
        var monthOffset: Int? {
            switch self {
            case .oneMonth: -1
            case .threeMonths: -3
            case .sixMonths: -6
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
        let plottedSeries = chartSeries
        let nonemptySeries = plottedSeries.filter { !$0.points.isEmpty }
        GlassPanel(cornerRadius: 28, padding: 21) {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(chartTitle)
                            .font(.system(size: 16, weight: .semibold, design: .rounded))
                        Text(chartSubtitle(for: plottedSeries))
                            .font(.system(size: 10))
                            .foregroundStyle(StockTheme.muted)
                    }
                    Spacer()
                    rangeSelector
                }
                .help("Unrealized gain divided by purchase cost. New lots change the cost-weighted return. Time ranges change the visible dates, not your cost basis. Excludes dividends, sales and fees.")
                if !model.selectedTickers.isEmpty {
                    Button("Show full portfolio") { model.selectedTickers.removeAll() }
                        .font(.system(size: 11, weight: .medium))
                        .buttonStyle(.plain)
                        .foregroundStyle(StockTheme.accentBright)
                }

                if nonemptySeries.isEmpty {
                    ContentUnavailableView(
                        model.isRefreshingPrices ? "Loading price history" : "No chart data yet",
                        systemImage: "chart.line.uptrend.xyaxis",
                        description: Text(emptyChartMessage)
                    )
                    .foregroundStyle(StockTheme.muted)
                    .frame(height: 220)
                } else {
                    if model.selectedTickers.count > 1 {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 65), alignment: .leading)], alignment: .leading, spacing: 8) {
                            ForEach(nonemptySeries) { series in
                                HStack(spacing: 5) {
                                    Circle().fill(series.color).frame(width: 7, height: 7)
                                    Text(series.id).font(.system(size: 10, weight: .semibold))
                                }
                            }
                        }
                    }
                    Chart {
                        RuleMark(y: .value("Starting return", 0))
                            .foregroundStyle(StockTheme.muted.opacity(0.3))
                            .lineStyle(StrokeStyle(lineWidth: 0.7, dash: [3, 4]))
                        ForEach(nonemptySeries) { series in
                            ForEach(series.points) { point in
                                if nonemptySeries.count == 1 {
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
                    .chartYScale(domain: yDomain(for: plottedSeries))
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
                            AxisValueLabel {
                                if let percentage = value.as(Double.self) {
                                    Text("\(percentage, specifier: "%+.1f")%")
                                }
                            }
                                .foregroundStyle(StockTheme.muted)
                        }
                    }
                    .frame(height: 250)
                    if !model.selectedTickers.isEmpty {
                        let unavailable = plottedSeries.filter { $0.points.isEmpty }.map(\.id)
                        if !unavailable.isEmpty {
                            Text("No price history for \(unavailable.joined(separator: ", ")) in this period.")
                                .font(.system(size: 10))
                                .foregroundStyle(StockTheme.muted)
                        }
                    }
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
        if model.selectedTickers.isEmpty { return "Portfolio performance" }
        if model.selectedTickers.count == 1 { return model.selectedTickers.first! }
        return "Selected positions"
    }

    private func chartSubtitle(for series: [Series]) -> String {
        guard let latest = series.flatMap({ $0.points.map(\.date) }).max() else {
            return "Return on purchase cost"
        }
        return "Return on cost · through \(latest.formatted(.dateTime.month(.abbreviated).day().year()))"
    }

    private var rawSeries: [Series] {
        if model.selectedTickers.isEmpty {
            return [Series(id: "Portfolio", points: model.portfolioPerformanceIndex(), color: StockTheme.accentBright)]
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
                points: PortfolioAnalytics.returnsOnCost(purchases: model.purchases.filter { $0.ticker == ticker }, priceHistory: model.priceHistoryByTicker),
                color: palette[index % palette.count]
            )
        }
    }

    private var emptyChartMessage: String {
        if model.isRefreshingPrices { return "Downloading daily closes for your holdings." }
        if model.holdings.isEmpty { return "Add a purchase lot to begin the portfolio history." }
        return "Verified market prices could not be loaded. Try Refresh; unavailable prices are not estimated."
    }

    private var chartSeries: [Series] {
        let all = rawSeries
        let cutoff = range.monthOffset.flatMap { offset in
            all.flatMap({ $0.points.map(\.date) }).max().flatMap {
                Calendar.current.date(byAdding: .month, value: offset, to: $0)
            }
        }
        return all.map { series in
            Series(
                id: series.id,
                points: series.points.filter { cutoff == nil || $0.date >= cutoff! },
                color: series.color
            )
        }
    }

    private func yDomain(for series: [Series]) -> ClosedRange<Double> {
        let values = series.flatMap { $0.points.map(\.value) }
        let lower = min(values.min() ?? 0, 0)
        let upper = max(values.max() ?? 0, 0)
        let padding = max((upper - lower) * 0.08, 1)
        return (lower - padding)...(upper + padding)
    }

    private func rangeDescription(_ range: ChartRange) -> String {
        switch range {
        case .oneMonth: "one month"
        case .threeMonths: "three months"
        case .sixMonths: "six months"
        case .oneYear: "one year"
        case .all: "all history"
        }
    }
}

private struct HoldingsList: View {
    @Environment(AppModel.self) private var model
    let compact: Bool

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
                            HoldingRow(holding: holding, compact: compact)
                        }
                    }
                }
            }
        }
    }

    private var holdingsHeader: some View {
        HStack {
            Text("SYMBOL").frame(maxWidth: .infinity, alignment: .leading)
            if !compact {
                Text("SHARES").frame(width: 95, alignment: .trailing)
                Text("AVG COST").frame(width: 110, alignment: .trailing)
            }
            Text("VALUE").frame(width: compact ? 108 : 120, alignment: .trailing)
            Text("RETURN").frame(width: compact ? 76 : 95, alignment: .trailing)
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
    @State private var confirmingDeletion = false
    let holding: Holding
    let compact: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
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
                        VStack(alignment: .leading, spacing: 4) {
                            Text(holding.ticker)
                                .font(.system(size: 14, weight: .bold, design: .rounded))
                            if compact {
                                Text("\(holding.quantity.formatted(.number.precision(.fractionLength(0...4)))) shares · \(holding.averageCost.formatted(.currency(code: "USD").precision(.fractionLength(2)))) avg")
                                    .font(.system(size: 9))
                                    .foregroundStyle(StockTheme.muted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        if model.selectedTickers.contains(holding.ticker) {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(StockTheme.accentBright)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if !compact {
                        Text(holding.quantity.formatted(.number.precision(.fractionLength(0...4))))
                            .frame(width: 95, alignment: .trailing)
                        Text(holding.averageCost.formatted(.currency(code: "USD").precision(.fractionLength(2))))
                            .frame(width: 110, alignment: .trailing)
                    }
                    Text(holding.marketValue?.formatted(.currency(code: "USD").precision(.fractionLength(0...2))) ?? "—")
                        .frame(width: compact ? 108 : 120, alignment: .trailing)
                    Text(holding.returnPercent.map { ($0 / 100).formatted(.percent.precision(.fractionLength(1))) } ?? "—")
                        .foregroundStyle(returnColor)
                        .frame(width: compact ? 76 : 95, alignment: .trailing)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("\(model.selectedTickers.contains(holding.ticker) ? "Remove" : "Add") \(holding.ticker) \(model.selectedTickers.contains(holding.ticker) ? "from" : "to") the chart")
            Menu {
                Button("Delete position", role: .destructive) {
                    confirmingDeletion = true
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
        if confirmingDeletion {
            HStack(spacing: 12) {
                Text("Delete every purchase lot for \(holding.ticker)?")
                    .font(.system(size: 11))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Button("Cancel") { confirmingDeletion = false }.stockSecondaryButton()
                Button("Delete", role: .destructive) {
                    Task { await model.deleteTicker(holding.ticker) }
                }
                .stockSecondaryButton()
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 14)
        }
        }
    }

    private var returnColor: Color {
        guard let value = holding.returnPercent else { return StockTheme.muted }
        return value >= 0 ? StockTheme.positive : StockTheme.negative
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
        OverlayPanel(title: "Add purchase lot", subtitle: "") {
            VStack(spacing: 14) {
                HStack(spacing: 12) {
                    LabeledField(label: "TICKER") { TextField("AAPL", text: $ticker).stockTextField() }
                    LabeledField(label: "SHARES") { TextField("10", text: $shares).stockTextField() }
                    LabeledField(label: "PRICE") { TextField("185.00", text: $price).stockTextField() }
                }
                LabeledField(label: "PURCHASE DATE") {
                    DatePicker("", selection: $date, in: ...Date.now, displayedComponents: .date)
                        .labelsHidden()
                        .datePickerStyle(.field)
                }
                LabeledField(label: "NOTE") { TextField("Optional", text: $note).stockTextField() }
            }
        } footer: {
            Button("Cancel") { model.overlay = nil }.stockSecondaryButton()
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
        OverlayPanel(title: "Import portfolio JSON", subtitle: "Each lot needs a ticker, shares, price per share, and purchase date.") {
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
            Button("Cancel") { model.overlay = nil }.stockSecondaryButton()
            Button("Import") { Task { _ = await model.importPortfolioJSON(json) } }
                .buttonStyle(.glassProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(json.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
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
