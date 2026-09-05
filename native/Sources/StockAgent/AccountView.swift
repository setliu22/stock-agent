import FoundationModels
import StockAgentCore
import SwiftUI

struct AccountView: View {
    @Environment(AppModel.self) private var model
    @State private var showAdvanced = false

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 27) {
                PageHeader(
                    eyebrow: "",
                    title: "Settings",
                    subtitle: "Connections used by research and portfolio views."
                )

                connections
                advancedSettings($model.configuration)
            }
            .padding(.horizontal, 38)
            .padding(.top, 32)
            .padding(.bottom, 46)
            .frame(maxWidth: 1040, alignment: .leading)
        }
        .scrollIndicators(.never)
        .defaultScrollAnchor(.top)
    }

    private func advancedSettings(_ configuration: Binding<AccountConfiguration>) -> some View {
        VStack(alignment: .leading, spacing: 13) {
            Text("Advanced")
                .font(.system(size: 19, weight: .bold, design: .rounded))
            GlassPanel(cornerRadius: 27, padding: 0) {
                VStack(spacing: 0) {
                    Button {
                        withAnimation(.snappy(duration: 0.22)) { showAdvanced.toggle() }
                    } label: {
                        HStack {
                            Text("Request identity and local data")
                                .font(.system(size: 13, weight: .semibold))
                            Spacer()
                            Image(systemName: showAdvanced ? "chevron.up" : "chevron.down")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(StockTheme.muted)
                        }
                        .padding(.horizontal, 22)
                        .frame(height: 58)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)

                    if showAdvanced {
                        Divider().overlay(StockTheme.border.opacity(0.45)).padding(.horizontal, 20)
                        VStack(alignment: .leading, spacing: 18) {
                    SettingsField(
                        title: "SEC CONTACT",
                        explanation: "Included in SEC requests for fair-access identification. Use an app name and contact email."
                    ) {
                        TextField("Stock Agent your@email.com", text: configuration.secUserAgent)
                            .stockTextField()
                    }
                            Text("Portfolio database: \(model.databaseURL.path)")
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(StockTheme.muted)
                                .lineLimit(2)
                                .textSelection(.enabled)
                            HStack {
                                Spacer()
                                Button("Save") { model.saveConfiguration() }
                                    .buttonStyle(.glassProminent)
                            }
                        }
                        .padding(22)
                    }
                }
            }
        }
    }

    private var connections: some View {
        VStack(alignment: .leading, spacing: 13) {
            Text("Connections")
                .font(.system(size: 19, weight: .bold, design: .rounded))
            GlassPanel(cornerRadius: 27, padding: 0) {
                VStack(spacing: 0) {
                    SourceRow(icon: "building.2.crop.circle", title: "LSEG Workspace", detail: "Company fundamentals and industry screening", status: lsegStatus)
                    Divider().overlay(StockTheme.border.opacity(0.45)).padding(.horizontal, 20)
                    SourceRow(icon: "brain.head.profile", title: "Apple Intelligence", detail: "On-device matching and research synthesis", status: modelAvailability)
                    Divider().overlay(StockTheme.border.opacity(0.45)).padding(.horizontal, 20)
                    SourceRow(icon: "network", title: "Public sources", detail: "SEC filings, macro signals, and portfolio prices", status: "Ready")
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
