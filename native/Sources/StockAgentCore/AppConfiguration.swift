import Foundation

public enum AppConfiguration {
    public static func databaseURL(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        currentDirectory: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        bundleURL: URL = Bundle.main.bundleURL
    ) -> URL {
        if let path = environment["STOCK_AGENT_DB"], !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        let bundleParent = bundleURL.deletingLastPathComponent()
        let candidates = [
            currentDirectory.appendingPathComponent("data/portfolio.db"),
            bundleParent.appendingPathComponent("data/portfolio.db"),
            bundleParent.deletingLastPathComponent().appendingPathComponent("data/portfolio.db"),
        ]
        if let existing = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) {
            return existing
        }
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return support.appendingPathComponent("Stock Agent/portfolio.db")
    }

    public static func loadEnvironment(at projectRoot: URL?) -> AccountConfiguration {
        var values = ProcessInfo.processInfo.environment
        if let projectRoot {
            let file = projectRoot.appendingPathComponent(".env")
            if let text = try? String(contentsOf: file, encoding: .utf8) {
                for line in text.split(whereSeparator: \Character.isNewline) {
                    let clean = line.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !clean.hasPrefix("#"), let divider = clean.firstIndex(of: "=") else { continue }
                    let key = String(clean[..<divider]).trimmingCharacters(in: .whitespaces)
                    var value = String(clean[clean.index(after: divider)...]).trimmingCharacters(in: .whitespaces)
                    if value.count >= 2, value.first == value.last, ["\"", "'"].contains(String(value.first!)) {
                        value.removeFirst()
                        value.removeLast()
                    }
                    if values[key] == nil { values[key] = value }
                }
            }
        }
        return AccountConfiguration(
            secUserAgent: values["SEC_USER_AGENT"] ?? "Stock Agent local-research contact@example.com"
        )
    }
}
