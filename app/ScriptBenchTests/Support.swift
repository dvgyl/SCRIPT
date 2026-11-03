import AppKit
import IOKit.pwr_mgt
import ScriptCore
@testable import ScriptBench

/// The uptime of a test. Each read for an app event moves it on by 1 ms, so the lines keep their time order.
@MainActor
final class TestClock {
    var now = 5000.0

    func appEvent() -> Double {
        now += 0.001
        return now
    }

    func tabletEvent() -> Double {
        now += 0.005
        return now
    }
}

/// A bench store over a new temporary folder, with a clock that the test moves. The caller calls `remove()`.
@MainActor
final class TestBench {
    let root: URL
    let store: BenchStore
    let clock: TestClock
    private static let pen = 3

    /// A store over `root`, or over a new folder. A second store over one folder is a relaunch.
    init(root: URL? = nil) throws {
        self.root = root ?? FileManager.default.temporaryDirectory.appendingPathComponent("ScriptBenchTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: self.root, withIntermediateDirectories: true)
        let mac = MacFacts(model: "Mac16,1", os: "macOS 26.1", nativeScale: 2,
                           app: AppInfo(version: "0.1.0", build: "1", sourceRevision: "test"))
        let clock = TestClock()
        self.clock = clock
        store = BenchStore(
            contract: try BenchContract(),
            storage: Storage(applicationSupport: self.root, documents: self.root,
                             deviceSecret: FileDeviceSecret(file: self.root.appendingPathComponent("DeviceSecret"))),
            mac: mac, qualifications: QualificationRegistry(file: self.root.appendingPathComponent("Qualifications.json")),
            uptime: { clock.appEvent() }, freeSpace: { 50_000_000_000 }
        )
        store.fullScreen = true
    }

    func remove() { try? FileManager.default.removeItem(at: root) }

    /// The draft that an operator fills in for a session on the Wacom tablet.
    static var draft: BenchDraft {
        var draft = BenchDraft()
        draft.mounting = "M3"
        draft.motionID = "nextdraw-8511"
        draft.motionVersion = "unknown"
        draft.tabletAsset = "WACOM-01"
        draft.driver = "6.4.10-3"
        draft.macAsset = "MAC-01"
        draft.operatorCode = "OP1"
        return draft
    }

    /// Line 0 that Setup writes for `kind`.
    func opened(_ kind: SessionKind, pen: TabletPen = .proPen3) throws -> SessionOpened {
        var draft = Self.draft
        draft.pen = pen
        let qualification = store.qualifications.last(asset: "WACOM-01", pen: draft.pen)
        guard let opened = draft.session(kind, contract: store.contract, mac: store.mac, qualification: qualification,
                                         uptime: clock.now, now: WallTime(Date()), boot: Clock.boot) else {
            throw CocoaError(.validationMissingMandatoryProperty)
        }
        return opened
    }

    // MARK: Tablet events at the app clock

    /// The pen enters the range. The first tablet event also runs the clock check, which these clocks pass.
    func enter(device: Int = pen) {
        send(.proximity(TabletProximity(t: tick(), device: device, entering: true, pointingDevice: .pen)))
    }

    func leave(device: Int = pen) {
        send(.proximity(TabletProximity(t: tick(), device: device, entering: false, pointingDevice: .pen)))
    }

    func point(_ source: TabletPoint.Source, x: Int, y: Int, pressure: Double = 0.4, device: Int = pen,
               rotation: Double = 15) {
        send(.point(TabletPoint(source: source, t: tick(), device: device, absoluteX: x, absoluteY: y, pressure: pressure,
                                tiltX: 0.1, tiltY: -0.2, rotation: rotation)))
    }

    /// One contact of `n` reports along x, from `down` to `up`.
    func stroke(x0: Int, y: Int, n: Int = 5, device: Int = pen) {
        point(.down, x: x0, y: y, device: device)
        for i in 1..<(n - 1) { point(.dragged, x: x0 + 40 * i, y: y, device: device) }
        point(.up, x: x0 + 40 * (n - 1), y: y, pressure: 0, device: device)
    }

    func hover(x: Int, y: Int, device: Int = pen) {
        point(.moved, x: x, y: y, pressure: 0, device: device)
    }

    func send(_ event: TabletEvent) { store.receive(event, uptime: clock.now) }

    private func tick() -> Double { clock.tabletEvent() }

    // MARK: Journal

    /// Every line of the open session's journal, once the writer has written them all.
    func lines() async throws -> [[String: Any]] {
        let live = try requireLive()
        try await live.writer.sync()
        return try Data(contentsOf: live.url).split(separator: 0x0A).map {
            try JSONSerialization.jsonObject(with: Data($0)) as! [String: Any]
        }
    }

    func requireLive() throws -> LiveSession {
        guard let live = store.live else { throw CocoaError(.featureUnsupported) }
        return live
    }
}

/// The types of the power assertions that this process holds for the session `opened`.
func powerAssertions(of opened: SessionOpened) -> Set<String> {
    var all: Unmanaged<CFDictionary>?
    guard IOPMCopyAssertionsByProcess(&all) == kIOReturnSuccess,
          let byProcess = all?.takeRetainedValue() as? [NSNumber: [[String: Any]]] else { return [] }
    let mine = byProcess[NSNumber(value: getpid())] ?? []
    return Set(mine.filter { ($0[kIOPMAssertionNameKey] as? String)?.hasSuffix(opened.sessionID.description) == true }
        .compactMap { $0[kIOPMAssertionTypeKey] as? String })
}

extension [[String: Any]] {
    func of(_ kind: String) -> [[String: Any]] { filter { $0["kind"] as? String == kind } }
}

/// `$SCRIPT_CONTRACT_OUT/bench`, which `./check bench` passes to the test process as `TEST_RUNNER_SCRIPT_CONTRACT_OUT`.
var benchContractOut: URL? {
    guard let path = ProcessInfo.processInfo.environment["SCRIPT_CONTRACT_OUT"], !path.isEmpty else { return nil }
    return URL(fileURLWithPath: path).appendingPathComponent("bench", isDirectory: true)
}

/// Copies an export folder and its Swift fold summary into the bench contract output, when the check sets it.
func writeContractExport(_ folder: URL, summary: FoldSummary) throws {
    guard let out = benchContractOut else { return }
    let target = out.appendingPathComponent(folder.lastPathComponent, isDirectory: true)
    try? FileManager.default.removeItem(at: target)
    try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
    try FileManager.default.copyItem(at: folder, to: target)
    try summary.jsonData().write(to: out.appendingPathComponent("\(folder.lastPathComponent).summary.json"))
}
