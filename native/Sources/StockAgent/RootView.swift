import StockAgentCore
import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        ZStack {
            LiquidBackground()

            NavigationSplitView {
                Sidebar(selection: $model.selectedSection)
                    .navigationSplitViewColumnWidth(min: 210, ideal: 232, max: 260)
                    .toolbar(removing: .sidebarToggle)
            } detail: {
                Group {
                    switch model.selectedSection {
                    case .research: ResearchView()
                    case .portfolio: PortfolioView()
                    case .market: MarketView()
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.clear)
            }
            .navigationSplitViewStyle(.balanced)
            .toolbar(removing: .sidebarToggle)
            .background(Color.clear)

            if model.proposal != nil {
                InlineProposalReview()
                    .transition(.opacity.combined(with: .scale(scale: 0.975)))
                    .zIndex(20)
            }

            if model.overlay != nil {
                PortfolioOverlay()
                    .transition(.opacity.combined(with: .scale(scale: 0.98)))
                    .zIndex(18)
            }

            if let notice = model.notice {
                NoticeToast(notice: notice) { model.notice = nil }
                    .padding(.trailing, 24)
                    .padding(.bottom, 22)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
                    .zIndex(30)
            }
        }
        .tint(StockTheme.accent)
        .foregroundStyle(StockTheme.text)
        .animation(.snappy(duration: 0.28), value: model.proposal?.id)
        .animation(.snappy(duration: 0.25), value: model.overlay)
        .animation(.snappy(duration: 0.25), value: model.notice?.id)
    }
}

private struct Sidebar: View {
    @Binding var selection: AppSection

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("LIBRARY")
                .font(.system(size: 10, weight: .bold, design: .rounded))
                .tracking(1.3)
                .foregroundStyle(StockTheme.muted.opacity(0.8))
                .padding(.horizontal, 21)
                .padding(.top, 34)
                .padding(.bottom, 9)

            VStack(spacing: 4) {
                ForEach(AppSection.allCases) { section in
                    Button {
                        selection = section
                    } label: {
                        HStack(spacing: 11) {
                            Image(systemName: section.symbol)
                                .font(.system(size: 14, weight: .semibold))
                                .frame(width: 21)
                            Text(section.rawValue)
                                .font(.system(size: 13, weight: selection == section ? .semibold : .medium))
                            Spacer()
                        }
                        .foregroundStyle(selection == section ? StockTheme.text : StockTheme.muted)
                        .padding(.horizontal, 13)
                        .frame(height: 37)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .background(
                        selection == section ? StockTheme.accent.opacity(0.16) : .clear,
                        in: RoundedRectangle(cornerRadius: 10, style: .continuous)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .stroke(selection == section ? StockTheme.accent.opacity(0.3) : .clear, lineWidth: 0.7)
                    )
                    .help("Open \(section.rawValue)")
                }
            }
            .padding(.horizontal, 10)

            Spacer()

        }
        .background(StockTheme.sidebar.opacity(0.76))
    }
}

private struct LiquidBackground: View {
    var body: some View {
        ZStack {
            StockTheme.background
            Circle()
                .fill(StockTheme.accent.opacity(0.09))
                .frame(width: 620, height: 620)
                .blur(radius: 90)
                .offset(x: 390, y: -310)
            Ellipse()
                .fill(Color.indigo.opacity(0.08))
                .frame(width: 700, height: 430)
                .blur(radius: 110)
                .offset(x: 130, y: 410)
        }
        .ignoresSafeArea()
    }
}

private struct NoticeToast: View {
    let notice: AppNotice
    let dismiss: () -> Void

    private var color: Color {
        switch notice.style {
        case .info: StockTheme.accent
        case .success: StockTheme.positive
        case .warning: StockTheme.warning
        case .error: StockTheme.negative
        }
    }

    private var icon: String {
        switch notice.style {
        case .info: "info.circle.fill"
        case .success: "checkmark.circle.fill"
        case .warning: "exclamationmark.triangle.fill"
        case .error: "xmark.octagon.fill"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(color)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 4) {
                Text(notice.title)
                    .font(.system(size: 13, weight: .semibold))
                Text(notice.message)
                    .font(.system(size: 12))
                    .foregroundStyle(StockTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .lineLimit(5)
            }
            Button(action: dismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 10, weight: .bold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(StockTheme.muted)
        }
        .padding(15)
        .frame(width: 350, alignment: .leading)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(color.opacity(0.18), lineWidth: 0.7))
        .shadow(color: .black.opacity(0.35), radius: 24, y: 12)
    }
}
