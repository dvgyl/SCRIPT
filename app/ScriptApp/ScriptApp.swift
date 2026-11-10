import ScriptCore
import SwiftUI
import UIKit

@main
struct ScriptApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @State private var store: SessionStore?
    @State private var failure: String?

    var body: some Scene {
        WindowGroup {
            Group {
                if let store {
                    // The system keeps a picture of the app for the app switcher. The cover keeps a participant
                    // code, an answer, or a safety alert out of that picture.
                    RootView(store: store).overlay { if scenePhase != .active { PrivacyCover() } }
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
            if phase == .active { store?.becomeActive() }
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
            #if DEBUG
            if let pin = LaunchMode.testPIN { store.setTestPIN(pin) }
            #endif
            store.launch()
            self.store = store
        } catch {
            failure = String(describing: error)
        }
    }

    /// What the app shows while it is not the active app: its name and nothing else.
    private struct PrivacyCover: View {
        var body: some View {
            ZStack {
                Color(UIColor.systemBackground).ignoresSafeArea()
                Text("SCRIPT Capture").font(.title.weight(.semibold)).foregroundStyle(.secondary)
            }
            .accessibilityIdentifier("privacyCover")
        }
    }

    /// Unit tests build their own stores in temporary folders, so the host app leaves the real container alone.
    private static var isHostingUnitTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
    }
}
