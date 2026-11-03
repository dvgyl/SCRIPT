import ScriptCore
import SwiftUI

@main
struct ScriptApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @State private var store: SessionStore?
    @State private var failure: String?

    var body: some Scene {
        WindowGroup {
            Group {
                if let store {
                    RootView(store: store)
                } else if let failure {
                    ContentUnavailableView("SCRIPT Capture cannot start", systemImage: "exclamationmark.triangle",
                                           description: Text(failure))
                } else {
                    ProgressView()
                }
            }
            .task { start() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { store?.enterBackground() }
        }
    }

    private func start() {
        guard store == nil, !Self.isHostingUnitTests else { return }
        do {
            let contract = try BundledContract()
            let files = FileManager.default
            let support = try files.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil,
                                        create: true)
            let documents = try files.url(for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            let store = SessionStore(
                contract: contract,
                storage: Storage(applicationSupport: support, documents: documents, deviceSecret: KeychainDeviceSecret()),
                device: DeviceFacts.current(),
                configurations: ConfigurationLibrary(folder: support.appendingPathComponent("Configurations"),
                                                     demo: contract.demo)
            )
            store.launch()
            self.store = store
        } catch {
            failure = String(describing: error)
        }
    }

    /// Unit tests build their own stores in temporary folders, so the host app leaves the real container alone.
    private static var isHostingUnitTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
    }
}
