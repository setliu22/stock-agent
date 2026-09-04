import SwiftUI

@main
struct StockAgentApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup("Stock Agent") {
            RootView()
                .environment(model)
                .preferredColorScheme(.dark)
                .frame(minWidth: 980, minHeight: 680)
        }
        .windowStyle(.hiddenTitleBar)
        .windowToolbarStyle(.unifiedCompact(showsTitle: false))
        .windowResizability(.contentMinSize)
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
