import CSQLite
import Foundation

private let sqliteTransient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

public actor PortfolioStore {
    public let databaseURL: URL

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
            CREATE TABLE IF NOT EXISTS price_history (
                ticker TEXT NOT NULL,
                price_date TEXT NOT NULL,
                close REAL NOT NULL CHECK(close >= 0),
                source TEXT NOT NULL DEFAULT 'Imported CSV',
                PRIMARY KEY(ticker, price_date)
            );
            CREATE TABLE IF NOT EXISTS manual_prices (
                ticker TEXT PRIMARY KEY,
                close REAL NOT NULL CHECK(close >= 0),
                updated_at TEXT NOT NULL
            );
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
        let prices = try currentPrices()
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        let statement = try Self.prepare(
            database,
            sql: """
            SELECT ticker, SUM(quantity), SUM(quantity * price)
            FROM purchases
            GROUP BY ticker
            HAVING SUM(quantity) > 0
            ORDER BY ticker
            """
        )
        defer { sqlite3_finalize(statement) }
        var output = [Holding]()
        while sqlite3_step(statement) == SQLITE_ROW {
            let ticker = Self.string(statement, column: 0)
            let quantity = sqlite3_column_double(statement, 1)
            let totalCost = sqlite3_column_double(statement, 2)
            output.append(
                Holding(
                    ticker: ticker,
                    quantity: quantity,
                    totalCost: totalCost,
                    averageCost: totalCost / quantity,
                    currentPrice: prices[ticker]
                )
            )
        }
        return output
    }

    @discardableResult
    public func deletePurchase(id: Int64) throws -> Int {
        try mutate("DELETE FROM purchases WHERE id = ?") { statement in
            sqlite3_bind_int64(statement, 1, id)
        }
    }

    @discardableResult
    public func deleteTicker(_ rawTicker: String) throws -> Int {
        let ticker = rawTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else { throw StockAgentError.validation("Enter a ticker.") }
        return try mutate("DELETE FROM purchases WHERE ticker = ?") { statement in
            Self.bind(ticker, to: 1, in: statement)
        }
    }

    @discardableResult
    public func clear() throws -> Int {
        try mutate("DELETE FROM purchases") { _ in }
    }

    public func setManualPrice(ticker rawTicker: String, close: Double) throws {
        let ticker = rawTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty, close >= 0 else {
            throw StockAgentError.validation("Enter a valid ticker and non-negative price.")
        }
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        let statement = try Self.prepare(
            database,
            sql: """
            INSERT INTO manual_prices(ticker, close, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET close=excluded.close, updated_at=excluded.updated_at
            """
        )
        defer { sqlite3_finalize(statement) }
        Self.bind(ticker, to: 1, in: statement)
        sqlite3_bind_double(statement, 2, close)
        Self.bind(ISO8601DateFormatter().string(from: .now), to: 3, in: statement)
        try Self.stepDone(statement, database: database)
    }

    public func importPriceHistory(ticker rawTicker: String, points: [PricePoint], source: String) throws {
        let ticker = rawTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else { throw StockAgentError.validation("Enter a ticker.") }
        guard !points.isEmpty else { throw StockAgentError.validation("No price rows were found.") }
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        try Self.execute(database, sql: "BEGIN IMMEDIATE")
        do {
            let statement = try Self.prepare(
                database,
                sql: """
                INSERT INTO price_history(ticker, price_date, close, source) VALUES (?, ?, ?, ?)
                ON CONFLICT(ticker, price_date) DO UPDATE
                SET close=excluded.close, source=excluded.source
                """
            )
            defer { sqlite3_finalize(statement) }
            for point in points where point.close >= 0 {
                sqlite3_reset(statement)
                sqlite3_clear_bindings(statement)
                Self.bind(ticker, to: 1, in: statement)
                Self.bind(Self.dayString(point.date), to: 2, in: statement)
                sqlite3_bind_double(statement, 3, point.close)
                Self.bind(source, to: 4, in: statement)
                try Self.stepDone(statement, database: database)
            }
            try Self.execute(database, sql: "COMMIT")
        } catch {
            try? Self.execute(database, sql: "ROLLBACK")
            throw error
        }
    }

    public func priceHistory(ticker rawTicker: String) throws -> [PricePoint] {
        let ticker = rawTicker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        let statement = try Self.prepare(
            database,
            sql: """
            SELECT price_date, close FROM price_history
            WHERE ticker = ? ORDER BY price_date
            """
        )
        defer { sqlite3_finalize(statement) }
        Self.bind(ticker, to: 1, in: statement)
        var output = [PricePoint]()
        while sqlite3_step(statement) == SQLITE_ROW {
            guard let date = Self.date(Self.string(statement, column: 0)) else { continue }
            output.append(.init(date: date, close: sqlite3_column_double(statement, 1)))
        }
        return output
    }

    private func currentPrices() throws -> [String: Double] {
        let database = try Self.open(databaseURL)
        defer { sqlite3_close(database) }
        let statement = try Self.prepare(
            database,
            sql: """
            WITH latest AS (
                SELECT p.ticker, p.close
                FROM price_history p
                JOIN (
                    SELECT ticker, MAX(price_date) AS max_date
                    FROM price_history GROUP BY ticker
                ) d ON d.ticker = p.ticker AND d.max_date = p.price_date
            )
            SELECT ticker, close FROM latest
            UNION ALL
            SELECT ticker, close FROM manual_prices
            """
        )
        defer { sqlite3_finalize(statement) }
        var output = [String: Double]()
        while sqlite3_step(statement) == SQLITE_ROW {
            output[Self.string(statement, column: 0)] = sqlite3_column_double(statement, 1)
        }
        return output
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
    public static func timeWeightedIndex(
        purchases: [Purchase],
        priceHistory: [String: [PricePoint]]
    ) -> [PortfolioValuePoint] {
        let sortedPurchases = purchases.sorted { $0.purchasedAt < $1.purchasedAt }
        let sortedHistory = priceHistory.mapValues { $0.sorted { $0.date < $1.date } }
        let dates = Set(
            sortedHistory.values.flatMap { $0.map(\.date) } + sortedPurchases.map(\.purchasedAt)
        ).sorted()
        guard !dates.isEmpty else { return [] }

        var previousDate: Date?
        var previousValue: Double?
        var index = 100.0
        var output = [PortfolioValuePoint]()
        for date in dates {
            let active = sortedPurchases.filter { $0.purchasedAt <= date }
            guard !active.isEmpty else { continue }
            let value = active.reduce(0.0) { total, purchase in
                let close = sortedHistory[purchase.ticker]?
                    .last(where: { $0.date <= date })?.close ?? purchase.price
                return total + purchase.quantity * close
            }
            if let previousDate, let previousValue, previousValue > 0 {
                let contributions = sortedPurchases
                    .filter { $0.purchasedAt > previousDate && $0.purchasedAt <= date }
                    .reduce(0.0) { $0 + $1.quantity * $1.price }
                let adjustedValue = value - contributions
                let periodFactor = adjustedValue / previousValue
                if periodFactor.isFinite, periodFactor >= 0 {
                    index *= periodFactor
                }
            }
            if index.isFinite {
                output.append(PortfolioValuePoint(date: date, value: index))
            }
            previousDate = date
            previousValue = value
        }
        return output
    }

}
