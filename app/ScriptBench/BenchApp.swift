import AppKit
import ScriptCore
import SwiftUI

@main
struct BenchApp: App {
    @NSApplicationDelegateAdaptor(BenchDelegate.self) private var delegate

    var body: some Scene {
        Window("SCRIPT Bench", id: "main") {
            RootView(delegate: delegate)
                .frame(minWidth: 900, minHeight: 560)
        }
        .defaultSize(width: 1280, height: 800)
    }
}

/// Starts the store and reads every tablet event of the app through one local monitor, so a report reaches the
/// journal wherever the pointer is in the window.
@MainActor @Observable
final class BenchDelegate: NSObject, NSApplicationDelegate {
    enum Launch {
        case starting
        case running(BenchStore)
        case failed(String)
    }

    private(set) var launch = Launch.starting
    @ObservationIgnored private var monitor: Any?

    func applicationWillFinishLaunching(_ notification: Notification) {
        NSEvent.isMouseCoalescingEnabled = false
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Unit tests build their own stores in temporary folders, so the host app leaves the real folder alone.
        guard ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil else { return }
        do {
            let support = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                      appropriateFor: nil, create: true)
                .appendingPathComponent("SCRIPT Bench", isDirectory: true)
            let store = BenchStore(
                contract: try BenchContract(),
                storage: Storage(applicationSupport: support, documents: support,
                                 deviceSecret: FileDeviceSecret(file: support.appendingPathComponent("DeviceSecret"))),
                mac: MacFacts.current(),
                qualifications: QualificationRegistry(file: support.appendingPathComponent("Qualifications.json")),
                clock: LaunchMode.skipsClockCheck ? .passed : .waiting
            )
            store.refresh()
            launch = .running(store)
            monitor = NSEvent.addLocalMonitorForEvents(
                matching: [.leftMouseDown, .leftMouseDragged, .leftMouseUp, .mouseMoved, .tabletPoint, .tabletProximity]
            ) { [weak store] event in
                let tablet = TabletEvent(event)
                guard tablet != .other else { return event }
                let uptime = ProcessInfo.processInfo.systemUptime
                // During a session the pen never presses a control, so the operator works with the mouse alone.
                let capturing = MainActor.assumeIsolated { () -> Bool in
                    store?.receive(tablet, uptime: uptime)
                    return store?.live != nil
                }
                return capturing ? nil : event
            }
        } catch {
            launch = .failed(String(describing: error))
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    /// An open session ends its trial and closes its writer before the app quits.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard case .running(let store) = launch, store.live != nil else { return .terminateNow }
        Task {
            await store.quit()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

struct RootView: View {
    let delegate: BenchDelegate

    var body: some View {
        switch delegate.launch {
        case .starting:
            ProgressView()
        case .failed(let text):
            ContentUnavailableView("SCRIPT Bench cannot start", systemImage: "exclamationmark.triangle",
                                   description: Text(verbatim: text))
        case .running(let store):
            SessionRoot(store: store)
        }
    }

    /// Home and the run screen have separate navigation stacks, so a session that opens from Setup shows the run
    /// screen at once, and a closed session returns to Home.
    private struct SessionRoot: View {
        @Bindable var store: BenchStore

        var body: some View {
            Group {
                if let live = store.live {
                    NavigationStack { BenchRunScreen(store: store, live: live) }
                } else {
                    NavigationStack { HomeScreen(store: store) }
                }
            }
            .alert(store.notice.map { Text($0.title) } ?? Text(verbatim: ""), isPresented: Binding(
                get: { store.notice != nil }, set: { if !$0 { store.notice = nil } }
            ), presenting: store.notice) { _ in
                Button("OK") {}
            } message: { notice in
                notice.message
            }
        }
    }
}

struct HomeScreen: View {
    let store: BenchStore

    var body: some View {
        Form {
            Section("Tablet clock") {
                LabeledContent("Clock check") { Text(store.clock.name) }
            }
            Section("New session") {
                NavigationLink("Bench session") { BenchSetup(store: store, kind: .bench) }
                NavigationLink("Qualification session") { BenchSetup(store: store, kind: .qualification) }
            }
            if !store.listing.isEmpty {
                Section("Sessions on this Mac") {
                    ForEach(store.listing) { row in
                        LabeledContent {
                            action(row)
                        } label: {
                            Text(verbatim: row.title)
                            if let date = row.openedAt.flatMap({ try? Date($0, strategy: .iso8601) }) {
                                Text(date, format: .dateTime.day().month().year().hour().minute())
                            }
                        }
                    }
                }
            }
            Section {
                Button("Show the exports in Finder", action: store.showExports)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("SCRIPT Bench")
    }

    @ViewBuilder
    private func action(_ row: Listed) -> some View {
        switch row.status {
        case .interrupted:
            Button("Resume") { if let id = row.sessionID { store.resume(id) } }
                .disabled(store.clock != .passed)
        case .closed:
            Button("Export") { if let id = row.sessionID { Task { await store.export(id) } } }
        case .exported:
            Text("Exported")
        case .damaged:
            Text("Damaged")
        }
    }
}

extension ClockCheck {
    var name: LocalizedStringKey {
        switch self {
        case .waiting: "Bring the pen to the tablet"
        case .passed: "Passed"
        case .failed(let difference): "Failed: the tablet clock differs from the uptime by \(difference, format: .number.precision(.fractionLength(2))) s"
        }
    }
}

extension Notice {
    var title: LocalizedStringKey {
        switch self {
        case .clock: "A session cannot start yet"
        case .lowStorage: "Not enough free storage"
        case .storageFailure: "The journal could not be written"
        case .exportFailed: "The export failed"
        case .failed: "Something went wrong"
        }
    }

    var message: Text {
        switch self {
        case .clock(.failed(let difference)):
            Text("The tablet event clock differs from the system uptime by \(difference, format: .number.precision(.fractionLength(2))) s, so the sample times would not match the app events. No session can start in this launch.")
        case .clock:
            Text("Bring the pen to the tablet. The app compares the two clocks at the first pen event.")
        case .lowStorage: Text("Free at least 500 MB on this Mac, then start the session.")
        case .storageFailure: Text("The session stopped. Leave it, then resume it from Home.")
        case .exportFailed: Text("The session is closed and stays on this Mac. Export it again from Home.")
        case .failed(let text): Text(verbatim: text)
        }
    }
}
