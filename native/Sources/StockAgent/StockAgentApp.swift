import SwiftUI

@main
struct StockAgentApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup("Stock Agent") {
            RootView()
                .environment(model)
                .preferredColorScheme(.dark)
                .frame(minWidth: 860, minHeight: 560)
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unifiedCompact(showsTitle: false))
        .windowResizability(.contentMinSize)
        .defaultSize(width: 960, height: 600)
        .defaultPosition(.center)
        .restorationBehavior(.disabled)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandMenu("Research") {
                Button("Focus Research Question") {
                    NotificationCenter.default.post(name: .focusResearchComposer, object: nil)
                }
                .keyboardShortcut("k", modifiers: .command)
            }
        }
    }
}

extension Notification.Name {
    static let focusResearchComposer = Notification.Name("StockAgent.focusResearchComposer")
}
