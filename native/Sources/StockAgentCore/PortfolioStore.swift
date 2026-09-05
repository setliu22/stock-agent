import CSQLite
import Foundation

private let sqliteTransient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

public actor PortfolioStore {
    public let databaseURL: URL
    private var sessionPrices = [String: [PricePoint]]()
    private var sessionSplits = [String: [StockSplit]]()
    private var verifiedTickers = Set<String>()

    public init(databaseURL: URL) throws {
        self.databaseURL = databaseURL
        try FileManager.default.createDirectory(
            at: databaseURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        try Self.execute(
            database,
            sql: """
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                quantity REAL NOT NULL CHECK(quantity > 0),
                price REAL NOT NULL CHECK(price >= 0),
                purchased_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_purchases_ticker ON purchases(ticker);
            DROP TABLE IF EXISTS price_history;
            DROP TABLE IF EXISTS manual_prices;
            DROP TABLE IF EXISTS stock_splits;
            DROP TABLE IF EXISTS research_profile_classifications;
            """
        )
    }

    public func record(_ purchase: Purchase) throws -> Int64 {
        let purchase = try purchase.validated()
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        let statement = try Self.prepare(
            database,
            sql: """
            INSERT INTO purchases(ticker, quantity, price, purchased_at, note)
            VALUES (?, ?, ?, ?, ?)
            """
        )
        defer { sqlite3_finalize(statement) }
        Self.bind(purchase.ticker, to: 1, in: statement)
        sqlite3_bind_double(statement, 2, purchase.quantity)
        sqlite3_bind_double(statement, 3, purchase.price)
        Self.bind(Self.dayString(purchase.purchasedAt), to: 4, in: statement)
        Self.bind(purchase.note, to: 5, in: statement)
        try Self.stepDone(statement, database: database)
        return sqlite3_last_insert_rowid(database)
    }

    @discardableResult
    public func record(_ purchases: [Purchase]) throws -> Int {
        let validated = try purchases.map { try $0.validated() }
        guard !validated.isEmpty else { return 0 }
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        try Self.execute(database, sql: "BEGIN IMMEDIATE")
        do {
            let statement = try Self.prepare(
                database,
                sql: """
                INSERT INTO purchases(ticker, quantity, price, purchased_at, note)
                VALUES (?, ?, ?, ?, ?)
                """
            )
            defer { sqlite3_finalize(statement) }
            for purchase in validated {
                sqlite3_reset(statement)
                sqlite3_clear_bindings(statement)
                Self.bind(purchase.ticker, to: 1, in: statement)
                sqlite3_bind_double(statement, 2, purchase.quantity)
                sqlite3_bind_double(statement, 3, purchase.price)
                Self.bind(Self.dayString(purchase.purchasedAt), to: 4, in: statement)
                Self.bind(purchase.note, to: 5, in: statement)
                try Self.stepDone(statement, database: database)
            }
            try Self.execute(database, sql: "COMMIT")
        } catch {
            try? Self.execute(database, sql: "ROLLBACK")
            throw error
        }
        return validated.count
    }

    public func purchases() throws -> [Purchase] {
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        let statement = try Self.prepare(
            database,
            sql: """
            SELECT id, ticker, quantity, price, purchased_at, note
            FROM purchases
            ORDER BY purchased_at, id
            """
        )
        defer { sqlite3_finalize(statement) }
        var output = [Purchase]()
        while sqlite3_step(statement) == SQLITE_ROW {
            let day = Self.string(statement, column: 4)
            output.append(
                Purchase(
                    id: sqlite3_column_int64(statement, 0),
                    ticker: Self.string(statement, column: 1),
                    quantity: sqlite3_column_double(statement, 2),
                    price: sqlite3_column_double(statement, 3),
                    purchasedAt: Self.date(day) ?? .now,
                    note: Self.string(statement, column: 5)
                )
            )
        }
        return output
    }

    public func holdings() throws -> [Holding] {
        let prices = currentPrices()
        let lots = try adjustedPurchases()
        return Dictionary(grouping: lots, by: \.ticker).sorted { $0.key < $1.key }.map { ticker, purchases in
            let quantity = purchases.reduce(0) { $0 + $1.quantity }
            let cost = purchases.reduce(0) { $0 + $1.quantity * $1.price }
            return Holding(ticker: ticker, quantity: quantity, totalCost: cost, averageCost: cost / quantity, currentPrice: prices[ticker])
        }
    }

    public func adjustedPurchases() throws -> [Purchase] {
        try purchases().map { PortfolioAnalytics.adjusted($0, for: sessionSplits[$0.ticker] ?? []) }
    }

    @discardableResult
    public func deleteTicker(_ rawTicker: String) throws -> Int {
        let ticker = rawTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else { throw StockAgentError.validation("Enter a ticker.") }
        return try mutate("DELETE FROM purchases WHERE ticker = ?") { statement in
            Self.bind(ticker, to: 1, in: statement)
        }
    }

    /// Market data lives only for this app session. Only original purchase records are persisted.
    public func importPriceHistory(ticker rawTicker: String, points: [PricePoint], source: String, splits: [StockSplit]? = nil) throws {
        let ticker = rawTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty, !points.isEmpty,
              points.allSatisfy({ $0.close.isFinite && $0.close > 0 && $0.date.timeIntervalSince1970.isFinite }),
              (splits ?? []).allSatisfy({ $0.ratio.isFinite && $0.ratio > 0 && $0.date.timeIntervalSince1970.isFinite }) else {
            throw StockAgentError.validation("Market history contained invalid prices or split events.")
        }
        sessionPrices[ticker] = points.sorted { $0.date < $1.date }
        sessionSplits[ticker] = splits ?? []
        if source == "Yahoo Finance (split-adjusted)" { verifiedTickers.insert(ticker) }
    }

    public func priceHistory(ticker: String) -> [PricePoint] {
        sessionPrices[ticker.uppercased()] ?? []
    }

    public func hasVerifiedPriceHistory(ticker: String) -> Bool {
        verifiedTickers.contains(ticker.uppercased())
    }

    private func currentPrices() -> [String: Double] {
        sessionPrices.compactMapValues { $0.last?.close }
    }

    private func mutate(
        _ sql: String,
        bindValues: (OpaquePointer) -> Void
    ) throws -> Int {
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        let statement = try Self.prepare(database, sql: sql)
        defer { sqlite3_finalize(statement) }
        bindValues(statement)
        try Self.stepDone(statement, database: database)
        return Int(sqlite3_changes(database))
    }

    private static func open(_ url: URL) throws -> OpaquePointer {
        var database: OpaquePointer?
        let status = sqlite3_open_v2(
            url.path,
            &database,
            SQLITE_OPEN_CREATE | SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX,
            nil
        )
        guard status == SQLITE_OK, let database else {
            if let database { sqlite3_close(database) }
            throw StockAgentError.storage("Could not open the portfolio database.")
        }
        sqlite3_busy_timeout(database, 3_000)
        return database
    }

    private static func execute(_ database: OpaquePointer, sql: String) throws {
        var errorPointer: UnsafeMutablePointer<CChar>?
        let status = sqlite3_exec(database, sql, nil, nil, &errorPointer)
        guard status == SQLITE_OK else {
            let message = errorPointer.map { String(cString: $0) } ?? "SQLite error \(status)."
            sqlite3_free(errorPointer)
            throw StockAgentError.storage(message)
        }
    }

    private static func prepare(_ database: OpaquePointer, sql: String) throws -> OpaquePointer {
        var statement: OpaquePointer?
        let status = sqlite3_prepare_v2(database, sql, -1, &statement, nil)
        guard status == SQLITE_OK, let statement else {
            throw StockAgentError.storage(String(cString: sqlite3_errmsg(database)))
        }
        return statement
    }

    private static func bind(_ value: String, to index: Int32, in statement: OpaquePointer) {
        sqlite3_bind_text(statement, index, value, -1, sqliteTransient)
    }

    private static func stepDone(_ statement: OpaquePointer, database: OpaquePointer) throws {
        guard sqlite3_step(statement) == SQLITE_DONE else {
            throw StockAgentError.storage(String(cString: sqlite3_errmsg(database)))
        }
    }

    private static func string(_ statement: OpaquePointer, column: Int32) -> String {
        guard let text = sqlite3_column_text(statement, column) else { return "" }
        return String(cString: text)
    }

    private static func dayString(_ date: Date) -> String {
        let parts = Calendar(identifier: .gregorian).dateComponents(in: .current, from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 1970, parts.month ?? 1, parts.day ?? 1)
    }

    private static func date(_ value: String) -> Date? {
        let pieces = value.prefix(10).split(separator: "-").compactMap { Int($0) }
        guard pieces.count == 3 else { return nil }
        return Calendar(identifier: .gregorian).date(
            from: DateComponents(year: pieces[0], month: pieces[1], day: pieces[2], hour: 12)
        )
    }
}

public enum PortfolioAnalytics {
    public static func adjusted(_ purchase: Purchase, for splits: [StockSplit]) -> Purchase {
        let calendar = Calendar(identifier: .gregorian)
        let purchaseDay = calendar.startOfDay(for: purchase.purchasedAt)
        let factor = splits.filter { calendar.startOfDay(for: $0.date) > purchaseDay && $0.date <= .now }
            .reduce(1.0) { $0 * $1.ratio }
        var adjusted = purchase
        adjusted.quantity *= factor
        adjusted.price /= factor
        return adjusted
    }

    /// Unrealized capital gain divided by the actual cost of active purchase lots.
    /// Ranges are viewports, not rebased price comparisons. Excludes dividends and sales.
    public static func returnsOnCost(purchases: [Purchase], priceHistory: [String: [PricePoint]]) -> [PortfolioValuePoint] {
        let calendar = Calendar(identifier: .gregorian)
        let lots = purchases.filter { $0.quantity.isFinite && $0.quantity > 0 && $0.price.isFinite && $0.price > 0 }
        guard let first = lots.map(\.purchasedAt).min() else { return [] }
        let firstDay = calendar.startOfDay(for: first)
        var history = [String: [PricePoint]]()
        for ticker in Set(lots.map(\.ticker)) {
            var daily = [Date: Double]()
            for point in (priceHistory[ticker] ?? []).sorted(by: { $0.date < $1.date })
                where point.close.isFinite && point.close > 0 {
                daily[calendar.startOfDay(for: point.date)] = point.close
            }
            guard !daily.isEmpty else { return [] }
            history[ticker] = daily.map { PricePoint(date: $0.key, close: $0.value) }.sorted { $0.date < $1.date }
        }
        let dates = Set(history.values.flatMap { $0.map(\.date) }).filter { $0 >= firstDay }.sorted()
        // The purchase anchor is actual transaction cost, not an invented market quote.
        var output = [PortfolioValuePoint(date: firstDay, value: 0)]
        for day in dates {
            let active = lots.filter { calendar.startOfDay(for: $0.purchasedAt) <= day }
            let cost = active.reduce(0) { $0 + $1.quantity * $1.price }
            var value = 0.0
            var complete = true
            for lot in active {
                guard let quote = history[lot.ticker]?.last(where: { $0.date <= day }),
                    quote.date >= calendar.startOfDay(for: lot.purchasedAt),
                    day.timeIntervalSince(quote.date) <= 7 * 86_400 else { complete = false; break }
                value += lot.quantity * quote.close
            }
            guard complete else { break }
            guard cost.isFinite, cost > 0, value.isFinite else { break }
            let percentage = (value / cost - 1) * 100
            guard percentage.isFinite else { break }
            // Daily closes follow the day's transactions. Distinct times preserve the initial zero.
            output.append(.init(date: day.addingTimeInterval(86_399), value: percentage))
        }
        return output.count > 1 ? output : []
    }

}
