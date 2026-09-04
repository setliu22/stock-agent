import Foundation

public struct LSEGProviderFact: Codable, Hashable, Sendable {
    public let label: String
    public let value: Double
    public let unit: String
}

public struct LSEGCompanyRecord: Codable, Hashable, Sendable {
    public let ticker: String
    public let ric: String
    public let name: String
    public let industry: String
    public let businessSummary: String
    public let marketCap: Double?
    public let facts: [LSEGProviderFact]
}

public protocol LSEGResearchProviding: Sendable {
    func isAvailable() async -> Bool
    func company(ticker: String) async throws -> LSEGCompanyRecord
    func discover(universes: [String], limit: Int) async throws -> [LSEGCompanyRecord]
}

public actor LSEGWorkspaceService: LSEGResearchProviding {
    private let projectRoot: URL

    public init(projectRoot: URL) {
        self.projectRoot = projectRoot
    }

    public func isAvailable() async -> Bool {
        do {
            let response = try await request(.init(operation: "status"))
            return response.ok
        } catch {
            return false
        }
    }

    public func company(ticker: String) async throws -> LSEGCompanyRecord {
        let response = try await request(.init(operation: "company", tickers: [ticker]))
        guard let company = response.companies.first else {
            let detail = response.failures.first ?? "LSEG returned no company row for \(ticker)."
            throw StockAgentError.unavailable(detail)
        }
        return company
    }

    public func discover(universes: [String], limit: Int) async throws -> [LSEGCompanyRecord] {
        let mappings = universes.compactMap(ResearchRegistry.lsegScreenDefinition(for:))
        guard !mappings.isEmpty else {
            throw StockAgentError.validation("None of the selected universes can be screened in LSEG.")
        }
        let perScreen = max(10, min(30, Int(ceil(Double(max(limit, 1)) / Double(mappings.count))) * 2))
        let screens = mappings.map { mapping in
            LSEGScreenRequest(label: mapping.label, body: mapping.screenBody(top: perScreen))
        }
        let response = try await request(
            .init(operation: "screen", screens: screens, limit: max(limit, 1))
        )
        guard !response.companies.isEmpty else {
            let detail = response.failures.first ?? "LSEG returned no companies for the selected universes."
            throw StockAgentError.unavailable(detail)
        }
        return response.companies
    }

    private func request(_ request: LSEGBridgeRequest) async throws -> LSEGBridgeResponse {
        let root = projectRoot.standardizedFileURL
        let python = root.appendingPathComponent(".venv/bin/python")
        let bridge = root.appendingPathComponent("scripts/lseg_bridge.py")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.fileExists(atPath: bridge.path) else {
            throw StockAgentError.unavailable("The local LSEG Workspace adapter is not installed.")
        }
        let input = try JSONEncoder().encode(request)
        let output = try await Task.detached(priority: .utility) {
            let process = Process()
            let standardInput = Pipe()
            let standardOutput = Pipe()
            process.executableURL = python
            process.arguments = [bridge.path]
            process.currentDirectoryURL = root
            process.standardInput = standardInput
            process.standardOutput = standardOutput
            process.standardError = FileHandle.nullDevice
            try process.run()
            try standardInput.fileHandleForWriting.write(contentsOf: input)
            try standardInput.fileHandleForWriting.close()
            let deadline = Date.now.addingTimeInterval(45)
            while process.isRunning && Date.now < deadline {
                try await Task.sleep(for: .milliseconds(50))
            }
            let timedOut = process.isRunning
            if timedOut { process.terminate() }
            process.waitUntilExit()
            let data = standardOutput.fileHandleForReading.readDataToEndOfFile()
            return (process.terminationStatus, data, timedOut)
        }.value
        if output.2 {
            throw StockAgentError.unavailable("LSEG Workspace did not respond within 45 seconds.")
        }
        let decoded: LSEGBridgeResponse
        do {
            decoded = try JSONDecoder().decode(LSEGBridgeResponse.self, from: output.1)
        } catch {
            throw StockAgentError.malformedResponse("LSEG Workspace returned an unreadable response.")
        }
        guard output.0 == 0, decoded.ok else {
            throw StockAgentError.unavailable(decoded.error ?? "LSEG Workspace is unavailable.")
        }
        return decoded
    }
}

private struct LSEGBridgeRequest: Encodable, Sendable {
    let operation: String
    var tickers: [String] = []
    var screens: [LSEGScreenRequest] = []
    var limit: Int = 16
}

private struct LSEGScreenRequest: Codable, Sendable {
    let label: String
    let body: String
}

private struct LSEGBridgeResponse: Decodable, Sendable {
    let ok: Bool
    let error: String?
    let companies: [LSEGCompanyRecord]
    let failures: [String]
}
