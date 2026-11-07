import CryptoKit
import Foundation
import ScriptCore
import Synchronization
@testable import ScriptApp

/// A store over new temporary folders. The caller removes `root`.
@MainActor
func makeStore(root: URL, makeSink: SinkFactory? = nil, freeBytes: Int64 = 50_000_000_000,
               input: InputKind = .directTouchAsPen, clock: ParityClock? = nil) throws -> SessionStore {
    let contract = try BundledContract()
    let defaults = UserDefaults(suiteName: "ScriptAppTests-\(UUID().uuidString)")!
    let storage = Storage(applicationSupport: root.appendingPathComponent("Support"),
                          documents: root.appendingPathComponent("Documents"), deviceSecret: MemorySecret())
    let library = ConfigurationLibrary(folder: root.appendingPathComponent("Support/Configurations"), demo: contract.demo,
                                       defaults: defaults)
    let device = DeviceFacts(model: "iPad17,2", os: "iPadOS 27.0", nativeScale: 2,
                             app: AppInfo(version: "0.1.0", build: "1", sourceRevision: "test"), input: input)
    if let makeSink {
        return SessionStore(contract: contract, storage: storage, device: device, configurations: library,
                            qualifications: QualificationRegistry(defaults: defaults), makeSink: makeSink,
                            uptime: { clock?.now ?? Clock.uptime }, freeSpace: { freeBytes })
    }
    return SessionStore(contract: contract, storage: storage, device: device, configurations: library,
                        qualifications: QualificationRegistry(defaults: defaults),
                        uptime: { clock?.now ?? Clock.uptime }, freeSpace: { freeBytes })
}

func temporaryFolder() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("ScriptAppTests-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

/// The `sessionOpened` line that Setup builds for a session under `configuration`.
func opened(_ administration: Administration = .supervised, participant: ParticipantCode = "T-0001",
            configuration: StudyConfiguration, battery: Battery, id: SessionID = SessionID(),
            openedAt: Date = Date()) -> SessionOpened {
    let consent = configuration.consentModel[AgeBand.youngAdult.rawValue]!
    let person = ParticipantFacts(
        participant: participant, administration: administration, study: configuration.studyRef(site: "DEMO"),
        visit: Visit(id: "V1", number: 1)!, ageBand: .youngAdult, jurisdiction: "GB", locale: "en",
        consent: Consent(model: consent.model,
                         attestedBy: Attestor(code: "OP1", role: administration == .supervised ? .operator : .enrolment),
                         attestedAt: WallTime(Date()), documents: consent.documents),
        ethics: configuration.ethics
    )
    return SessionOpened(
        purpose: .study, sessionID: id, openedAt: WallTime(openedAt),
        openedUptime: ProcessInfo.processInfo.systemUptime, boot: Clock.boot,
        app: AppInfo(version: "0.1.0", build: "1", sourceRevision: "test"),
        device: DeviceInfo(model: "iPad17,2", os: "iPadOS 27.0", nativeScale: 2, asset: "IPAD-01"),
        surface: Surface(protector: .none, nib: .new), qualification: nil,
        scale: MillimetreScale.lookup(model: "iPad17,2", nativeScale: 2)!, pencil: .none,
        channels: ChannelPlan.plan(pencil: .none, hoverSupported: false), input: .directTouchAsPen,
        subject: .participant(person), battery: battery.reference
    )
}

/// The demo configuration file with a self-administered PHQ-9 block and the remote review that it needs.
func remoteReviewConfiguration() throws -> StudyConfiguration {
    try StudyConfiguration(data: try remoteReviewConfigurationData())
}

/// The file of `remoteReviewConfiguration()`, for an import into a store.
func remoteReviewConfigurationData() throws -> Data {
    let url = Bundle.main.url(forResource: "demo", withExtension: "json")!
    var root = try JSONSerialization.jsonObject(with: try Data(contentsOf: url)) as! [String: Any]
    root["questionnaires"] = [["administration": "self", "ageBand": "18-24", "position": "afterTasks",
                               "instruments": ["phq9"]]]
    var safety = root["safety"] as! [String: Any]
    safety["remoteReview"] = ["reviewerRole": "study clinician", "imminentReviewWithinHours": 24,
                              "routineReviewWithinWeekdays": 1, "exportEveryHours": 12,
                              "monitoringDisclosureVersion": "1"]
    root["safety"] = safety
    return try JSONSerialization.data(withJSONObject: root)
}

extension SessionStore {
    /// Runs every task step with a Begin and a Done, as a participant who does not draw.
    func runTasks() {
        var guardCount = 0
        while currentTask != nil, guardCount < 50 {
            startTrial(view: ViewSize(width: 834, height: 1000)!)
            begin()
            done()
            guardCount += 1
        }
    }

    /// Answers every item of the open blocks: the value in `values`, else 0.
    func answerBlocks(_ values: [ItemID: Int?] = [:]) async {
        var guardCount = 0
        while let item = currentItem, guardCount < 100 {
            await answer(values[item.item.id] ?? 0)
            if live?.stage == .flagCrisisList { continueAfterCrisisList() }
            guardCount += 1
        }
    }
}

/// The device secret in memory.
final class MemorySecret: DeviceSecret {
    private let stored = Mutex<[UInt8]?>(nil)

    func key() throws -> SymmetricKey {
        stored.withLock { key in
            if key == nil { key = (0..<32).map { _ in UInt8.random(in: 0...255) } }
            return SymmetricKey(data: key!)
        }
    }
}

/// A sink that writes like the journal writer and then fails: the append after `failAfter` writes half of its
/// bytes, as a full disk does, and reports the failure; every later append throws.
final class FailingSink: JournalSink {
    private struct State {
        var appends = 0
        var failed = false
    }

    private let url: URL
    private let failAfter: Int
    private let onFailure: @Sendable (JournalWriter.WriteError) -> Void
    private let state = Mutex(State())

    init(url: URL, failAfter: Int, onFailure: @escaping @Sendable (JournalWriter.WriteError) -> Void) {
        self.url = url
        self.failAfter = failAfter
        self.onFailure = onFailure
        FileManager.default.createFile(atPath: url.path, contents: nil)
    }

    func append(_ entries: [Entry]) throws {
        var data = Data()
        for entry in entries { data.append(try LineCodec.encode(entry)) }
        let fails = try state.withLock { state -> Bool in
            if state.failed { throw JournalWriter.WriteError.io(EIO) }
            state.appends += 1
            state.failed = state.appends > failAfter
            return state.failed
        }
        let handle = try FileHandle(forWritingTo: url)
        try handle.seekToEnd()
        try handle.write(contentsOf: fails ? data.prefix(data.count / 2) : data)
        try handle.close()
        if fails { onFailure(.io(EIO)) }
    }

    func sync() async throws {}
    func close() async throws {}
}

/// Holds every `sync()` of a `GatedSink` until the test opens it, and records whether a `safetyFlagged` line was
/// appended before the first sync.
final class SyncGate: Sendable {
    private struct State {
        var open = false
        var syncs = 0
        var flagged = false
        var flaggedBeforeSync = false
        var waiting: [CheckedContinuation<Void, Never>] = []
    }

    private let state = Mutex(State())

    var flaggedBeforeSync: Bool { state.withLock { $0.flaggedBeforeSync } }

    func appended(_ kinds: [LineKind]) {
        state.withLock { state in
            if kinds.contains(.safetyFlagged) { state.flagged = true }
        }
    }

    func sync() async {
        await withCheckedContinuation { (k: CheckedContinuation<Void, Never>) in
            let passes = state.withLock { state -> Bool in
                if state.syncs == 0 { state.flaggedBeforeSync = state.flagged }
                state.syncs += 1
                if !state.open { state.waiting.append(k) }
                return state.open
            }
            if passes { k.resume() }
        }
    }

    func open() {
        let waiting = state.withLock { state in
            state.open = true
            defer { state.waiting = [] }
            return state.waiting
        }
        for k in waiting { k.resume() }
    }

    /// True once a sync has arrived. It gives up after many yields, so a store that never syncs fails the test.
    func waitForSync() async -> Bool {
        for _ in 0..<1000 {
            if state.withLock({ $0.syncs > 0 }) { return true }
            await Task.yield()
        }
        return false
    }
}

/// A sink that keeps no bytes and holds each sync at its gate.
final class GatedSink: JournalSink {
    let gate: SyncGate

    init(gate: SyncGate) { self.gate = gate }

    func append(_ entries: [Entry]) throws { gate.appended(entries.map(\.event.kind)) }
    func sync() async throws { await gate.sync() }
    func close() async throws {}
}

/// The app event and pen event clock of a synthetic bench run.
@MainActor
final class ParityClock {
    var now: Double

    init(now: Double) { self.now = now }
}


/// The parity fixture of bench protocol section 2.4. `./check` copies it to the build folder on the internal disk and
/// names it in `SCRIPT_PARITY_FIXTURE`, because a test host may not read the external drive of the repository.
/// Without the variable, as in Xcode, the test reads it from the repository.
func parityFixture(_ file: String = #filePath) throws -> Data {
    if let path = ProcessInfo.processInfo.environment["SCRIPT_PARITY_FIXTURE"], !path.isEmpty {
        return try Data(contentsOf: URL(fileURLWithPath: path))
    }
    let url = URL(fileURLWithPath: file).deletingLastPathComponent()
        .appendingPathComponent("../../contract/fixtures/parity/parity.json").standardizedFileURL
    return try Data(contentsOf: url)
}
