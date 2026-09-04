import FoundationModels
import StockAgentCore
import SwiftUI

struct AccountView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 27) {
                PageHeader(
                    eyebrow: "",
                    title: "Settings",
                    subtitle: "Data sources and filing access."
                )

                configurationSection($model.configuration)

                dataSources

                HStack {
                    Text("Portfolio database: \(model.databaseURL.path)")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(StockTheme.muted)
                        .lineLimit(2)
                        .textSelection(.enabled)
                    Spacer()
                    Button("Save settings") { model.saveConfiguration() }
                        .buttonStyle(.glassProminent)
                }
            }
            .padding(.horizontal, 38)
            .padding(.top, 32)
            .padding(.bottom, 46)
            .frame(maxWidth: 1040, alignment: .leading)
        }
        .scrollIndicators(.never)
    }

    private func configurationSection(_ configuration: Binding<AccountConfiguration>) -> some View {
        VStack(alignment: .leading, spacing: 13) {
            Text("Filing access")
                .font(.system(size: 19, weight: .bold, design: .rounded))
            GlassPanel(cornerRadius: 27, padding: 23) {
                VStack(spacing: 19) {
                    SettingsField(
                        title: "SEC CONTACT",
                        explanation: "Included in SEC requests for fair-access identification. Use an app name and contact email."
                    ) {
                        TextField("Stock Agent your@email.com", text: configuration.secUserAgent)
                            .stockTextField()
                    }
                }
            }
        }
    }

    private var dataSources: some View {
        VStack(alignment: .leading, spacing: 13) {
            Text("Sources")
                .font(.system(size: 19, weight: .bold, design: .rounded))
            GlassPanel(cornerRadius: 27, padding: 0) {
                VStack(spacing: 0) {
                    SourceRow(icon: "building.2.crop.circle", title: "LSEG Workspace", detail: "Company data and industry screens", status: lsegStatus)
                    Divider().overlay(StockTheme.border.opacity(0.45)).padding(.horizontal, 20)
                    SourceRow(icon: "building.columns", title: "SEC EDGAR", detail: "Filings and company facts", status: "Ready")
                    Divider().overlay(StockTheme.border.opacity(0.45)).padding(.horizontal, 20)
                    SourceRow(icon: "waveform.path.ecg", title: "Federal Reserve data", detail: "Macro indicators", status: "Ready")
                    Divider().overlay(StockTheme.border.opacity(0.45)).padding(.horizontal, 20)
                    SourceRow(icon: "brain.head.profile", title: "Apple on-device model", detail: "Semantic trend matching · availability depends on Apple Intelligence", status: modelAvailability)
                    Divider().overlay(StockTheme.border.opacity(0.45)).padding(.horizontal, 20)
                    SourceRow(icon: "chart.line.uptrend.xyaxis", title: "Market prices", detail: "Daily portfolio history", status: "Ready")
                    Divider().overlay(StockTheme.border.opacity(0.45)).padding(.horizontal, 20)
                    SourceRow(icon: "doc.text", title: "CSV and manual prices", detail: "Offline fallback", status: "Ready")
                }
            }
        }
    }

    private var modelAvailability: String {
        SystemLanguageModel.default.isAvailable ? "Available" : "Manual fallback"
    }

    private var lsegStatus: String {
        switch model.lsegConnected {
        case true: "Connected"
        case false: "Unavailable"
        case nil: "Checking"
        }
    }
}

private struct SettingsField<Content: View>: View {
    let title: String
    let explanation: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 9, weight: .bold, design: .rounded))
                .tracking(0.9)
                .foregroundStyle(StockTheme.muted)
            content
            Text(explanation)
                .font(.system(size: 10))
                .foregroundStyle(StockTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct SourceRow: View {
    let icon: String
    let title: String
    let detail: String
    let status: String

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: icon)
                .font(.system(size: 17, weight: .medium))
                .foregroundStyle(StockTheme.accentBright)
                .frame(width: 30)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(detail).font(.system(size: 10)).foregroundStyle(StockTheme.muted)
            }
            Spacer()
            CapsuleLabel(
                text: status,
                color: ["Available", "Ready", "Connected"].contains(status)
                    ? StockTheme.positive
                    : StockTheme.warning
            )
        }
        .padding(.horizontal, 21)
        .frame(minHeight: 66)
    }
}
