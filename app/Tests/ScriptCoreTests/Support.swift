import CryptoKit
import Foundation
import Synchronization
import Testing
@testable import ScriptCore

/// The shared contract folder and the files the tests read from it.
enum Contract {
    static let repository = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .appendingPathComponent("../../..")
        .standardizedFileURL
    static let root = repository.appendingPathComponent("contract")

    static func url(_ path: String) -> URL { root.appendingPathComponent(path) }
    static func data(_ path: String) throws -> Data { try Data(contentsOf: url(path)) }
    static func json(_ path: String) throws -> JSON { try JSON.decodeDocument(try data(path)) }

    /// The stems of the files in a fixture folder that end in `suffix`, without the `._` files of exFAT drives.
    static func stems(in folder: String, suffix: String) -> [String] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: url(folder).path)) ?? []
        return names.filter { !$0.hasPrefix("._") && $0.hasSuffix(suffix) }.map { String($0.dropLast(suffix.count)) }.sorted()
    }

    static let journalFixtures = stems(in: "fixtures/journal", suffix: ".jsonl")
    static let recoveryFixtures = stems(in: "fixtures/recovery", suffix: ".jsonl")
    static let exportFixture = "fixtures/export/SCRIPT_DEMO_DEMO_3f2a9c1e"

    static func battery() throws -> Battery { try Battery(data: try data("battery.json")) }
    static func instruments() throws -> InstrumentSet { try InstrumentSet(data: try data("instruments.json")) }
    static func demoConfiguration() throws -> StudyConfiguration { try StudyConfiguration(data: try data("configs/demo.json")) }
    static func benchPrograms() throws -> BenchPrograms { try BenchPrograms(data: try data("bench_programs.json")) }

    /// The shipped instruments plus one test instrument, so a test never depends on the status of a shipped instrument.
    static func instrumentsWithProbe(textStatus: String, minimumAge: Int) throws -> InstrumentSet {
        var root = try JSONSerialization.jsonObject(with: try data("instruments.json")) as! [String: Any]
        var list = root["instruments"] as! [[String: Any]]
        list.append([
            "id": "probe", "version": "1.0", "title": "Probe", "textStatus": textStatus,
            "ages": ["min": minimumAge, "max": NSNull()], "locale": "en", "stem": "Probe stem",
            "items": [["id": "probe-1", "text": "Probe item", "options": [["value": 0, "label": "No"]],
                       "scored": true, "safety": false]],
        ])
        root["instruments"] = list
        return try InstrumentSet(data: try JSONSerialization.data(withJSONObject: root))
    }

    /// The demo configuration plus a self-administered PHQ-9 block with a remote review that `validate` accepts.
    static func remoteReviewConfiguration() throws -> StudyConfiguration {
        var config = try demoConfiguration()
        config.questionnaires.append(.init(administration: .selfAdministered, ageBand: .youngAdult, position: .afterTasks,
                                           instruments: ["phq9"]))
        config.safety.remoteReview = .init(reviewerRole: "study clinician", imminentReviewWithinHours: 24,
                                           routineReviewWithinWeekdays: 1, exportEveryHours: 12,
                                           monitoringDisclosureVersion: "1")
        return config
    }

    /// Every line of a journal file that ends in LF.
    static func lines(_ data: Data) -> [Data] {
        var lines = data.split(separator: 0x0A, omittingEmptySubsequences: false).map { Data($0) }
        lines.removeLast()
        return lines
    }
}

enum Fixed {
    static let boot = BootID(UUID(uuidString: "0B0B0B0B-0000-4000-8000-0000000000A1")!)
    static let laterBoot = BootID(UUID(uuidString: "0B0B0B0B-0000-4000-8000-0000000000A2")!)
    static let at = WallTime(text: "2026-11-03T10:00:00Z")
    static let launch = Storage.Launch(boot: laterBoot, uptime: 5.0, at: WallTime(text: "2026-11-03T10:30:00Z"))
}

/// Builds the `sessionOpened` line for a session with a participant under a configuration.
func makeOpened(
    id: SessionID = SessionID(), participant: ParticipantCode = "P0001", purpose: Purpose = .study,
    administration: Administration = .supervised, ageBand: AgeBand = .youngAdult, jurisdiction: Jurisdiction = "US",
    pencil: PencilModel = .pro, configuration: StudyConfiguration, battery: Battery
) -> SessionOpened {
    let consent = configuration.consentModel[ageBand.rawValue]!
    let facts = ParticipantFacts(
        participant: participant, administration: administration, study: configuration.studyRef(site: "DEMO"),
        visit: Visit(id: "V1", number: 1)!, ageBand: ageBand, jurisdiction: jurisdiction, locale: "en",
        consent: Consent(model: consent.model,
                         attestedBy: Attestor(code: "OP1", role: administration == .supervised ? .operator : .enrolment),
                         attestedAt: Fixed.at, documents: consent.documents),
        ethics: configuration.ethics
    )
    return SessionOpened(
        purpose: purpose, sessionID: id, openedAt: Fixed.at, openedUptime: 100,
        boot: Fixed.boot, app: AppInfo(version: "1.0.0", build: "1", sourceRevision: "test"),
        device: DeviceInfo(model: "iPad16,3", os: "iPadOS 27.0", nativeScale: 2, asset: "IPAD-01"),
        surface: Surface(protector: .none, nib: .new), qualification: nil,
        scale: MillimetreScale.lookup(model: "iPad16,3", nativeScale: 2)!, pencil: pencil,
        channels: ChannelPlan.plan(pencil: pencil, hoverSupported: true), input: .pencil,
        subject: .participant(facts), battery: battery.reference
    )
}

/// Builds the `sessionOpened` line of a bench session: the iPad with the Pencil for role P, the Wacom tablet on the
/// Mac for role W.
func makeBenchOpened(id: SessionID = SessionID(), role: BenchRole = .tablet, programs: BenchPrograms,
                     battery: Battery) -> SessionOpened {
    let facts = BenchFacts(role: role, mounting: "M3", motion: DocumentRef(id: "nextdraw-8511", version: "unknown")!,
                           protocol: programs.protocol)
    let tablet = TabletTable.geometry(model: "Wacom Intuos Pro Medium (2025)")!
    return switch role {
    case .pencil:
        SessionOpened(
            purpose: .bench, sessionID: id, openedAt: Fixed.at, openedUptime: 100, boot: Fixed.boot,
            app: AppInfo(version: "1.0.0", build: "1", sourceRevision: "test"),
            device: DeviceInfo(model: "iPad16,3", os: "iPadOS 27.0", nativeScale: 2, asset: "IPAD-02"),
            surface: Surface(protector: .none, nib: .new), qualification: nil,
            scale: MillimetreScale.lookup(model: "iPad16,3", nativeScale: 2)!, pencil: .pro,
            channels: ChannelPlan.plan(pencil: .pro, hoverSupported: true), input: .pencil,
            subject: .bench(facts), battery: battery.reference
        )
    case .tablet:
        SessionOpened(
            purpose: .bench, sessionID: id, openedAt: Fixed.at, openedUptime: 100, boot: Fixed.boot,
            app: AppInfo(version: "1.0.0", build: "1", sourceRevision: "test"),
            device: DeviceInfo(model: "Mac16,1", os: "macOS 26.1", nativeScale: 2, asset: "MAC-01"),
            tablet: TabletInfo(model: tablet.model, asset: "WACOM-01", driver: "6.4.10-3", pen: .proPen3),
            surface: Surface(protector: .none, nib: .new), qualification: nil, scale: tablet.scale, pencil: .none,
            channels: ChannelPlan.plan(tabletPen: .proPen3), input: .tablet, subject: .bench(facts),
            battery: battery.reference
        )
    }
}

/// Drives a session as the app does: `decide` for intents, `InkRecorder` for ink, and an optional writer.
struct Driver {
    var state: SessionState
    var recorder: InkRecorder
    let rules: SessionRules
    var writer: JournalWriter?
    /// Every line this driver wrote, encoded.
    var journal = Data()

    init(opened: SessionOpened, rules: SessionRules, writer: JournalWriter? = nil) throws {
        let first = Entry(seq: 0, event: .sessionOpened(opened))
        state = try SessionState(opening: first)
        recorder = InkRecorder(plan: opened.channels)
        self.rules = rules
        self.writer = writer
        journal = try LineCodec.encode(first)
        try writer?.append([first])
    }

    /// Continues a recovered session with a new writer.
    init(resuming state: SessionState, rules: SessionRules, writer: JournalWriter?) {
        self.state = state
        recorder = InkRecorder(resuming: state.session)
        self.rules = rules
        self.writer = writer
    }

    @discardableResult
    mutating func run(_ command: Command) throws -> [Event] {
        let events = try state.decide(command, rules: rules).get()
        try write(events)
        return events
    }

    mutating func write(_ events: [Event]) throws {
        let entries = try state.record(events)
        for entry in entries { journal.append(try LineCodec.encode(entry)) }
        try writer?.append(entries)
    }

    static func plan(_ task: TaskID, position: Int = 1, accommodations: [Accommodation] = []) -> TrialPlan {
        let facts = ParticipantTrialFacts(hand: .right, instruction: InstructionRef(id: task.rawValue, version: "1.0", locale: "en"),
                                          accommodations: accommodations)
        return TrialPlan(task: task, practice: task == "practice", position: position, subject: .participant(facts),
                         view: ViewSize(width: 834, height: 1210)!, stimulusOrigin: Point(x: 147.5, y: 200))
    }

    /// A bench run of `program` on `task`, in the tablet's active area.
    static func benchPlan(_ program: ProgramID, task: TaskID, run: Int = 1, position: Int = 1, clip: ClipName? = nil) -> TrialPlan {
        TrialPlan(task: task, practice: false, position: position,
                  subject: .bench(BenchRunPlan(program: program, run: run, clip: clip)),
                  view: ViewSize(width: 52600, height: 29600)!, stimulusOrigin: Point(x: 0, y: 0))
    }

    /// One Pencil contact of `n` samples. The first sample can leave estimates pending under `update`.
    mutating func stroke(key: UInt, t0: Double, n: Int = 5, x0: Double = 300, pending: TouchProperties = [],
                         update: Int? = nil, lift: Bool = true) throws {
        var readings: [PenReading] = []
        for i in 0..<n {
            let last = i == n - 1
            readings.append(PenReading(
                key: TouchKey(key), t: t0 + Double(i) / 240, x: x0 + 0.75 * Double(i), y: 400 + 0.02 * Double(i * i),
                force: 0.2 + 0.01 * Double(i), maxForce: 4.166666666666667, altitude: 1.05, azimuth: 2.2, roll: 0.4,
                phase: i == 0 ? .began : (last && lift ? .ended : .moved),
                estimated: i == 0 ? pending : [], expectingUpdates: i == 0 ? pending : [],
                update: i == 0 ? update.map(UpdateIndex.init) : nil
            ))
        }
        try write(recorder.pen(readings))
    }

    /// Answers every item of the open block. Items not in `values` get 0. A nil value skips the item.
    mutating func answerAll(_ instrument: InstrumentID, t: Double, values: [ItemID: Int?] = [:]) throws {
        try run(.startQuestionnaire(instrument, t: t))
        for (n, item) in rules.instruments.instrument(instrument)!.items.enumerated() {
            try run(.respond(item: item.id, value: values[item.id] ?? 0, t: t + Double(n + 1)))
        }
        try run(.endQuestionnaire(reason: .completed, t: t + 20))
    }
}

func rules(for opened: SessionOpened, configuration: StudyConfiguration) throws -> SessionRules {
    guard case .participant(let facts) = opened.subject else { throw DecodeError.missing("participant") }
    return SessionRules(battery: try Contract.battery(), instruments: try Contract.instruments(), configuration: configuration,
                        jurisdiction: facts.jurisdiction, locale: facts.locale)
}

func benchRules() throws -> SessionRules {
    SessionRules(battery: try Contract.battery(), instruments: try Contract.instruments(),
                 benchPrograms: try Contract.benchPrograms())
}

/// A new empty folder that the caller removes.
func temporaryFolder() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("ScriptCoreTests-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

/// Every regular file under `root`, keyed by its relative path, without `._` files.
func snapshot(_ root: URL) throws -> [String: Data] {
    var files: [String: Data] = [:]
    let base = root.standardizedFileURL.path
    guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey]) else {
        return files
    }
    for case let url as URL in walker where !url.lastPathComponent.hasPrefix("._") {
        guard try url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile == true else { continue }
        let path = url.standardizedFileURL.path
        files[String(path.dropFirst(base.count + 1))] = try Data(contentsOf: url)
    }
    return files
}

func storage(in root: URL, secret: any DeviceSecret = MemoryDeviceSecret(),
             stops: @escaping @Sendable (Storage.Step) -> Bool = { _ in false }) -> Storage {
    Storage(applicationSupport: root.appendingPathComponent("Support"), documents: root.appendingPathComponent("Documents"),
            deviceSecret: secret, stops: stops)
}

/// A device secret in memory. Like the Keychain item, it is created at the first call, or it starts as `key`.
final class MemoryDeviceSecret: DeviceSecret {
    private let stored: Mutex<SymmetricKey?>

    init(_ key: SymmetricKey? = nil) { stored = Mutex(key) }

    func key() -> SymmetricKey {
        stored.withLock { key in
            if key == nil { key = SymmetricKey(size: .bits256) }
            return key!
        }
    }
}

extension Result {
    var rejection: Failure? {
        if case .failure(let error) = self { error } else { nil }
    }
}
