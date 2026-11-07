import Foundation
import ScriptCore
import Testing
@testable import ScriptApp

/// The header that Setup writes for a bench session: role P, mounting M3, the motion system `nextdraw-8511` version
/// `2.4`.
@MainActor
private func benchHeader(_ store: SessionStore) throws -> SessionOpened {
    var draft = SetupDraft(configuration: store.contract.demo, input: store.device.input)
    draft.asset = "IPAD-01"
    draft.mounting = "M3"
    draft.motionID = "nextdraw-8511"
    draft.motionVersion = "2.4"
    return try #require(draft.session(purpose: .bench, configuration: store.contract.demo, device: store.device,
                                      contract: store.contract, scale: store.device.tableScale,
                                      qualifications: store.qualifications))
}

@MainActor
private func openBench(_ store: SessionStore) throws {
    store.openBench(try benchHeader(store))
}

/// Records one run of `program`: the run screen picks it, the task view starts it, and the operator taps Begin, draws
/// one stroke, and taps Done.
@MainActor
private func record(_ program: ProgramID, run: Int = 1, in store: SessionStore) {
    store.chooseRun(BenchRunPlan(program: program, run: run))
    store.startRun(view: ViewSize(width: 834, height: 1000)!)
    store.begin()
    let t = ProcessInfo.processInfo.systemUptime
    store.pen([PenReading(key: TouchKey(1), t: t, x: 400, y: 500, phase: .began),
               PenReading(key: TouchKey(1), t: t + 0.01, x: 410, y: 505, phase: .ended)])
    store.done()
}

/// The journal lines of the session as JSON objects, once the writer has every line on storage.
@MainActor
private func lines(_ store: SessionStore) async throws -> [[String: Any]] {
    let live = try #require(store.live)
    if let closing = live.closing { await closing.value } else { try await live.sink.sync() }
    return try Data(contentsOf: live.url).split(separator: 0x0A).map {
        try #require(try JSONSerialization.jsonObject(with: Data($0)) as? [String: Any])
    }
}

@Test @MainActor func aBenchStoreWritesABenchHeaderWithTheQualificationReferenceAndNoParticipantKeys() async throws {
    // Detects a bench header that holds a participant key, such as the QUAL placeholder of a qualification session,
    // loses the facts of the mounting or the protocol, or drops the last qualification of the iPad and Pencil.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    let qualification = opened(configuration: store.contract.demo, battery: store.contract.battery)
    store.qualifications.record(qualification)
    try openBench(store)

    let header = try await lines(store)[0]
    for key in ["participant", "administration", "study", "visit", "ageBand", "jurisdiction", "locale", "consent",
                "ethics"] {
        #expect(header[key] == nil, "\(key)")
    }
    #expect(header["purpose"] as? String == "bench")
    let bench = try #require(header["bench"] as? [String: Any])
    let programs = store.contract.benchPrograms
    #expect(bench["role"] as? String == "P" && bench["mounting"] as? String == "M3")
    #expect(bench["motion"] as? [String: String] == ["id": "nextdraw-8511", "version": "2.4"])
    #expect(bench["protocol"] as? [String: String] == ["version": programs.protocol.version,
                                                       "sha256": programs.protocol.sha256.hex])
    #expect(header["qualification"] as? [String: String] == ["sessionID": qualification.sessionID.description,
                                                             "at": qualification.openedAt.text])
    #expect(store.live?.stage == .bench)
}

@Test @MainActor func eachBenchTrialRecordsItsRunIDAsTheClip() async throws {
    // Detects a clip that differs from the run id that the run screen shows, a run without the digest of its
    // program, or a stimulus origin other than the origin of the task view.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    try openBench(store)
    let shown = [store.runID(BenchRunPlan(program: "loops-l-v20", run: 1)),
                 store.runID(BenchRunPlan(program: "spiral-s-v40", run: 6))]
    record("loops-l-v20", in: store)
    record("spiral-s-v40", run: 6, in: store)

    let started = try await lines(store).filter { $0["kind"] as? String == "trialStarted" }
    let runs = started.compactMap { $0["bench"] as? [String: Any] }
    #expect(runs.compactMap { $0["clip"] as? String } == ["P-M3-loops-l-v20-r1", "P-M3-spiral-s-v40-r6"])
    #expect(shown.map { $0?.rawValue } == ["P-M3-loops-l-v20-r1", "P-M3-spiral-s-v40-r6"])
    let programs = store.contract.benchPrograms
    #expect(runs.compactMap { $0["trajectorySHA256"] as? String }
        == ["loops-l-v20", "spiral-s-v40"].map { programs.program(ProgramID($0)!)!.trajectorySHA256.hex })
    #expect(started.map { $0["stimulusOrigin"] as? [String: Double] } == [["x": 0, "y": 0], ["x": 0, "y": 0]])
    #expect(started.map { $0["position"] as? Int } == [1, 2])
}

@Test @MainActor func aFaultRunOffersNoExcludeAndTheStoreRefusesIt() throws {
    // Detects an exclude mark on a fault run, which the quality gate of the bench protocol needs as an ordinary trial.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    try openBench(store)
    store.chooseRun(BenchRunPlan(program: "loops-l-v20", run: 1))
    store.startRun(view: ViewSize(width: 834, height: 1000)!)
    #expect(store.currentRun?.markCodes.contains(.exclude) == true)
    store.done()
    store.chooseRun(BenchRunPlan(program: "fault-lift", run: 1))
    store.startRun(view: ViewSize(width: 834, height: 1000)!)

    #expect(store.currentRun?.markCodes == [.disturbance, .deviceProblem])
    #expect(!store.mark(.exclude))
    #expect(store.state?.session.counts[.mark] == 0)
    #expect(store.mark(.disturbance))
    #expect(store.currentRun?.marks == [.disturbance])
}

@Test @MainActor func aRunIDOfTheSessionCannotStartAgain() async throws {
    // Detects a run screen that starts a run with the run id of an earlier run, for example after Stop the run ends
    // a wrong pick, so two runs share one camera file name.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    try openBench(store)
    let picked = BenchRunPlan(program: "loops-l-v20", run: 2)
    store.chooseRun(picked)
    store.startRun(view: ViewSize(width: 834, height: 1000)!)
    store.stopRun()
    #expect(store.runRefusal(picked) == .repeatedRun("loops-l-v20", 2))
    store.chooseRun(picked)
    #expect(store.currentRun == nil)
    store.startRun(view: ViewSize(width: 834, height: 1000)!)
    #expect(store.state?.session.trials.count == 1)

    #expect(store.runRefusal(BenchRunPlan(program: "loops-l-v20", run: 6)) == nil)
    record("loops-l-v20", run: 6, in: store)
    let started = try await lines(store).filter { $0["kind"] as? String == "trialStarted" }
    let clips = started.map { ($0["bench"] as? [String: Any])?["clip"] as? String }
    #expect(clips == ["P-M3-loops-l-v20-r2", "P-M3-loops-l-v20-r6"])
}

@Test @MainActor func aResumedBenchSessionOfAnotherProtocolOffersNoRun() async throws {
    // Detects a build that continues a bench session of another bench protocol, so one journal mixes the runs of two
    // registrations.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = try makeStore(root: root)
    var opened = try benchHeader(first)
    guard case .bench(var facts) = opened.subject else { throw CocoaError(.featureUnsupported) }
    facts.protocol = BenchProtocolRef(version: "1.0", sha256: facts.protocol.sha256)
    opened.subject = .bench(facts)
    first.openBench(opened)
    try await first.live!.sink.close()

    let second = try makeStore(root: root)
    second.launch()
    second.resume(try #require(second.listing.first { $0.purpose == .bench }?.sessionID))
    #expect(second.live?.stage == .bench)
    let run = BenchRunPlan(program: "loops-l-v20", run: 1)
    #expect(second.runRefusal(run) == .otherProtocol)
    second.chooseRun(run)
    #expect(second.currentRun == nil)
    second.startRun(view: ViewSize(width: 834, height: 1000)!)
    #expect(second.state?.session.trials.isEmpty == true)
    second.endBench()
    #expect(second.live?.stage == .sampleTiming)
}

@Test @MainActor func aBenchSessionRefusesEveryQuestionnaireSafetyAndHandoffStep() async throws {
    // Detects a bench session that reaches a handoff, a questionnaire, a safety screen, or the crisis list, which
    // name a person who is not there.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    try openBench(store)
    let t = ProcessInfo.processInfo.systemUptime
    for command: Command in [.handoff(to: .participant, t: t), .startQuestionnaire("phq9", t: t),
                             .show(.crisisList, t: t), .show(.operatorAlert, t: t),
                             .recordAction("clinicianAssessment", t: t)] {
        #expect(!store.perform(command), "\(command)")
    }
    store.start()
    store.chooseHand(.left)
    store.acknowledgeDisclosure()
    store.advance()
    await store.answer(0)
    store.operatorTookOver()
    store.returnToParticipant()
    await store.endEarly()
    store.showSupportList()
    store.recordAction("clinicianAssessment")
    store.continueAfterCrisisList()
    #expect(store.live?.stage == .bench)
    #expect(store.currentTask == nil && store.currentItem == nil)

    record("loops-l-v20", in: store)
    store.endBench()
    #expect(store.live?.stage == .sampleTiming)
    let kinds = Set(try await lines(store).compactMap { $0["kind"] as? String })
    #expect(kinds.isDisjoint(with: ["handoff", "questionnaireStarted", "response", "questionnaireEnded",
                                    "safetyFlagged", "safetyShown", "safetyAction"]))
    #expect(kinds.contains("sessionClosed"))
}

@Test @MainActor func aBenchSessionKeepsTheScreenOnUntilItEnds() throws {
    // Detects a bench session that lets the iPad lock between runs, which ends the next run as backgrounded.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    #expect(!store.keepsScreenAwake)
    try openBench(store)
    #expect(store.keepsScreenAwake)
    record("loops-l-v20", in: store)
    #expect(store.keepsScreenAwake)
    store.endBench()
    #expect(!store.keepsScreenAwake)
}

@Test @MainActor func doneEndsABenchRunWithOperatorDone() async throws {
    // Detects a bench run that ends as participantDone, which names a person, or that ends without its stroke.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    try openBench(store)
    record("line-osc8", in: store)

    let ended = try await lines(store).filter { $0["kind"] as? String == "trialEnded" }
    #expect(ended.map { $0["reason"] as? String } == ["operatorDone"])
    #expect(store.state?.summary.trials.map(\.contacts) == [1])
    #expect(store.currentRun == nil && store.live?.stage == .bench)
}

@Test @MainActor func anInterruptedBenchSessionResumesAtTheRunScreen() async throws {
    // Detects a bench session that a crash leaves without a way back, that resumes into a participant stage, or that
    // records its next run without the run of the interruption.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = try makeStore(root: root)
    try openBench(first)
    record("loops-l-v20", in: first)
    first.chooseRun(BenchRunPlan(program: "spiral-l-v20", run: 1))
    first.startRun(view: ViewSize(width: 834, height: 1000)!)
    try await first.live!.sink.close()

    let second = try makeStore(root: root)
    second.launch()
    let row = try #require(second.listing.first { $0.purpose == .bench })
    #expect(row.status == .interrupted && second.title(of: row) == "P-M3")
    second.resume(try #require(row.sessionID))
    #expect(second.live?.stage == .bench && second.live?.participant == nil)
    #expect(second.currentRun == nil)
    record("spiral-l-v20", run: 6, in: second)
    #expect(second.state?.summary.trials.map(\.outcome) == [.completed, .interrupted, .completed])
    second.endBench()
    #expect(second.live?.stage == .sampleTiming)
}

@Test @MainActor func syntheticBenchCaptureWritesTheParityExport() async throws {
    // Detects capture changes that alter the synthetic movement before Python computes its features.
    let fixtureURL = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .appendingPathComponent("../../contract/fixtures/parity/parity.json").standardizedFileURL
    let fixture = try #require(try JSONSerialization.jsonObject(with: Data(contentsOf: fixtureURL)) as? [String: Any])
    let tools = try #require(fixture["tools"] as? [String: [String: Any]])
    let events = try #require(tools["app"]?["events"] as? [[String: Any]])
    let mounting = try #require(fixture["mounting"] as? String)
    let base = try #require(fixture["base"] as? Double)
    let times = try #require(fixture["trial"] as? [String: Double])
    let started = try #require(times["started"])
    let began = try #require(times["began"])
    let ended = try #require(times["ended"])
    let closed = try #require(times["closed"])
    let clock = ParityClock(now: base)
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root, input: .pencil, clock: clock)
    var header = try benchHeader(store)
    header.openedUptime = base
    header.pencil = .pro
    header.channels = ChannelPlan.plan(pencil: .pro, hoverSupported: true)
    guard case .bench(let facts) = header.subject else { throw CocoaError(.coderInvalidValue) }
    #expect(facts.mounting.rawValue == mounting)
    store.openBench(header)
    let program = try #require(fixture["program"] as? String)
    store.chooseRun(BenchRunPlan(program: try #require(ProgramID(program)), run: 1))
    clock.now = base + started
    store.startRun(view: ViewSize(width: 834, height: 1000)!)
    clock.now = base + began
    store.begin()
    for row in events {
        let contact = try #require(row["contact"] as? Int)
        let offset = try #require(row["offset"] as? Double)
        let x = try #require(row["x"] as? Double)
        let y = try #require(row["y"] as? Double)
        let phaseName = try #require(row["phase"] as? String)
        clock.now = base + offset
        if contact == 0 {
            let z = try #require(row["z"] as? Double)
            let altitude = try #require(row["altitude"] as? Double)
            let azimuth = try #require(row["azimuth"] as? Double)
            let roll = try #require(row["roll"] as? Double)
            store.hover(HoverReading(t: clock.now, clock: .callback, x: x, y: y, z: z,
                                     altitude: altitude, azimuth: azimuth, roll: roll))
            continue
        }
        let phase = try #require(PenPhase(rawValue: phaseName))
        let reading = PenReading(key: TouchKey(UInt(contact)), t: base + offset, x: x, y: y,
                                 force: row["force"] as? Double, maxForce: row["maxForce"] as? Double,
                                 altitude: row["altitude"] as? Double, azimuth: row["azimuth"] as? Double,
                                 roll: row["roll"] as? Double, phase: phase)
        store.pen([reading])
    }
    clock.now = base + ended
    store.done()
    clock.now = base + closed
    store.endBench()
    let result = try #require(await store.export(header.sessionID))
    let data = try Data(contentsOf: result.url.appendingPathComponent(Storage.Export.journal))
    let state = try Journal.read(data)
    #expect(state.summary.trials.map(\.contacts) == [2])
    // The app check copies Documents/Exports from this simulator container.
    let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    let out = documents.appendingPathComponent("Exports/parity", isDirectory: true)
    try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
    let target = out.appendingPathComponent(result.url.lastPathComponent, isDirectory: true)
    try FileManager.default.copyItem(at: result.url, to: target)
    try state.summary.jsonData().write(to: out.appendingPathComponent("\(target.lastPathComponent).summary.json"))
    try JSONSerialization.data(withJSONObject: ["base": base]).write(to: out.appendingPathComponent("\(target.lastPathComponent).clock.json"))
}
