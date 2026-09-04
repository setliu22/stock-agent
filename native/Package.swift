// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "StockAgentNative",
    platforms: [
        .macOS(.v26)
    ],
    products: [
        .executable(name: "StockAgent", targets: ["StockAgent"]),
        .executable(name: "StockAgentDiagnostics", targets: ["StockAgentDiagnostics"]),
    ],
    targets: [
        .systemLibrary(
            name: "CSQLite",
            path: "Sources/CSQLite"
        ),
        .target(
            name: "StockAgentCore",
            dependencies: ["CSQLite"],
            path: "Sources/StockAgentCore"
        ),
        .executableTarget(
            name: "StockAgent",
            dependencies: ["StockAgentCore"],
            path: "Sources/StockAgent"
        ),
        .executableTarget(
            name: "StockAgentDiagnostics",
            dependencies: ["StockAgentCore"],
            path: "Sources/StockAgentDiagnostics"
        ),
        .testTarget(
            name: "StockAgentCoreTests",
            dependencies: ["StockAgentCore"],
            path: "Tests/StockAgentCoreTests"
        )
    ]
)
