import Foundation
import ScriptCore
import Testing
@testable import ScriptApp

@MainActor
private func openSupervised(_ store: SessionStore, participant: ParticipantCode = "T-0001") {
    let demo = store.contract.demo
    store.open(opened(participant: participant, configuration: demo, battery: store.contract.battery),
               configuration: demo, dominant: .right)
}

@MainActor
private func journal(_ store: SessionStore, _ id: SessionID) throws -> SessionState {
    try Journal.read(try Data(contentsOf: store.storage.journalURL(id)))
}

@Test @MainActor func aFlagInASupervisedSessionChangesNothingThatTheParticipantSees() throws {
    // Detects a supervised flow that differs when a safety item is flagged, so the participant could infer the result.
    func run(flag: Bool) throws -> (stages: [Stage], flags: Int, lists: Int) {
        let root = try temporaryFolder()
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try makeStore(root: root)
        openSupervised(store)
        store.start()
        store.runTasks()
        var stages: [Stage] = []
        while let item = store.currentItem, stages.count < 40 {
            store.answer(flag && item.item.id == "phq9-9" ? 1 : 0)
            stages.append(store.live!.stage)
        }
        let safety = store.state!.session.safety
        return (stages, safety.flags, safety.crisisListShown)
    }
    let plain = try run(flag: false)
    let flagged = try run(flag: true)
    #expect(flagged.flags == 1 && plain.flags == 0)
    #expect(flagged.stages == plain.stages)
    #expect(flagged.stages.last == .handBack)
    #expect(flagged.lists == 0)
}

@Test @MainActor func aSupervisedFlagNeedsOneActionCodeBeforeTheCrisisListAndTheClose() async throws {
    // Detects a supervised session that reaches the crisis list or the close while a flag has no operator action.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    openSupervised(store)
    let id = store.live!.opened.sessionID
    store.start()
    store.runTasks()
    store.answerBlocks(["phq9-9": 2])
    #expect(store.live?.stage == .handBack)

    store.operatorTookOver()
    #expect(store.live?.stage == .review)
    store.showSupportList()
    #expect(store.live?.stage == .review)
    store.recordAction("clinicianAssessment")
    store.showSupportList()
    #expect(store.live?.stage == .closingCrisisList)
    store.continueAfterCrisisList()
    #expect(store.live?.stage == .finished)

    await store.live?.closing?.value
    let saved = try journal(store, id)
    #expect(saved.status == .closed)
    let safety = saved.session.safety
    #expect([safety.flags, safety.operatorAlerts, safety.actions, safety.crisisListShown] == [Int](repeating: 1, count: 4))
    #expect(saved.session.counts[.handoff] == 3)
}

@Test @MainActor func aSelfAdministeredFlagShowsTheCrisisListAtOnceAndAgainAtTheEnd() async throws {
    // Detects a self-administered flag whose crisis list waits for the end, or a session that skips the closing list.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    let config = try remoteReviewConfiguration()
    store.open(opened(.selfAdministered, configuration: config, battery: store.contract.battery), configuration: config,
               dominant: .left)
    store.start()
    store.runTasks()
    #expect(store.live?.stage == .disclosure)
    store.acknowledgeDisclosure()
    for _ in 0..<20 {
        guard let item = store.currentItem, item.item.id != "phq9-9" else { break }
        store.answer(0)
    }
    store.answer(1)
    #expect(store.live?.stage == .flagCrisisList)
    #expect(store.state?.session.safety.crisisListShown == 1)

    store.continueAfterCrisisList()
    #expect(store.currentItem?.item.id == "difficulty")
    store.answer(nil)
    #expect(store.live?.stage == .closingCrisisList)
    #expect(store.state?.session.safety.crisisListShown == 2)
    store.continueAfterCrisisList()
    #expect(store.live?.stage == .finished)
    #expect(store.state?.session.safety.operatorAlerts == 0)
    #expect(store.state?.summary.questionnaires.map(\.skipped) == [1])
}

@Test @MainActor func aTaskRecordsItsTrueScaleGeometryAndOnlyInkAfterBegin() throws {
    // Detects ink recorded before the Begin tap, or a stimulus origin that is not centred at true millimetre scale.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    openSupervised(store)
    store.start()
    #expect(store.currentTask?.step.task == "practice")
    func stroke(_ key: UInt) -> [PenReading] {
        let t = ProcessInfo.processInfo.systemUptime
        return [PenReading(key: TouchKey(key), t: t, x: 400, y: 500, phase: .began),
                PenReading(key: TouchKey(key), t: t + 0.01, x: 410, y: 505, phase: .ended)]
    }
    store.startTrial(view: ViewSize(width: 834, height: 1000)!)
    store.pen(stroke(1))
    #expect(store.state?.session.counts[.contactBegan] == 0)
    // 100 mm by 40 mm at 25.4 * 2 / 264 mm per point, centred in 834 by 1000 points.
    #expect(store.currentTask?.origin == Point(x: 157, y: 396))

    store.begin()
    store.pen(stroke(2))
    #expect(store.state?.session.counts[.contactBegan] == 1)
    #expect(store.state?.session.counts[.sample] == 2)
    store.done()
    #expect(store.state?.summary.trials.first?.outcome == .completed)
    #expect(store.currentTask?.step.task == "spiral-trace")
}

@Test @MainActor func aFailedWriteStopsTheSessionAtOnceAndRebuildsItFromTheFile() async throws {
    // Detects a store that goes on after a write failure, so its state runs ahead of the journal that a relaunch reads.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root) { url, onFailure in FailingSink(url: url, failAfter: 3, onFailure: onFailure) }
    openSupervised(store)
    let url = store.live!.url
    store.start()
    store.startTrial(view: ViewSize(width: 834, height: 1000)!)
    store.begin()
    store.done()
    var waits = 0
    while store.live?.stage != .stopped, waits < 100 {
        await Task.yield()
        waits += 1
    }
    #expect(store.live?.stage == .stopped)
    #expect(store.notice == .storageFailure)
    await store.rebuild?.value

    guard case .truncated(let saved, _) = Journal.recover(try Data(contentsOf: url)) else {
        Issue.record("the failed append did not leave a torn tail")
        return
    }
    #expect(saved.session.lines == 3)
    #expect(store.state?.session.lines == 3)
    let size = try Data(contentsOf: url).count
    #expect(!store.perform(.handoff(to: .operator, t: ProcessInfo.processInfo.systemUptime)))
    #expect(try Data(contentsOf: url).count == size)
}

@Test @MainActor func launchListsEverySessionByItsStateAndReopensASelfAdministeredOne() async throws {
    // Detects a launch that loses a closed, exported, or damaged session, or leaves an interrupted self-administered
    // participant at the operator's Home.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = try makeStore(root: root)
    for participant: ParticipantCode in ["T-CLOSED", "T-EXPORT"] {
        openSupervised(first, participant: participant)
        let id = first.live!.opened.sessionID
        first.start()
        first.runTasks()
        first.answerBlocks()
        first.operatorTookOver()
        first.showSupportList()
        first.continueAfterCrisisList()
        if participant == "T-EXPORT" {
            #expect(await first.export(id) != nil)
        }
        first.leave()
        await Task.yield()
    }
    let demo = first.contract.demo
    first.open(opened(.selfAdministered, participant: "T-HOME", configuration: demo, battery: first.contract.battery),
               configuration: demo, dominant: .left)
    first.start()
    first.startTrial(view: ViewSize(width: 834, height: 1000)!)
    first.begin()
    first.done()
    first.startTrial(view: ViewSize(width: 834, height: 1000)!)
    try await first.live!.sink.close()
    try Data("not a journal line\n".utf8).write(to: first.storage.sessions.appendingPathComponent(
        "\(UUID().uuidString).jsonl"))

    let second = try makeStore(root: root)
    second.launch()
    let statuses = Dictionary(grouping: second.listing, by: \.status).mapValues { $0.map(\.title).sorted() }
    #expect(statuses[.open] == ["T-HOME"])
    #expect(statuses[.closed] == ["T-CLOSED"])
    #expect(statuses[.exported]?.count == 1 && statuses[.exported]?.first?.hasPrefix("SCRIPT_DEMO_DEMO_") == true)
    #expect(statuses[.damaged]?.count == 1)
    #expect(second.live?.stage == .ready)
    #expect(second.state?.summary.trials.map(\.outcome) == [.completed, .interrupted])
    second.start()
    #expect(second.currentTask?.step.task == "spiral-trace")
    second.startTrial(view: ViewSize(width: 834, height: 1000)!)
    #expect(second.state?.summary.trials.last?.attempt == 2)
}

@Test @MainActor func aSessionDoesNotStartWithLessThan500MBFree() throws {
    // Detects a session that starts on a nearly full iPad, where the journal would fail part way through a task.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root, freeBytes: 499_999_999)
    openSupervised(store)
    #expect(store.live == nil)
    #expect(store.notice == .lowStorage)
    let journals = (try? FileManager.default.contentsOfDirectory(atPath: store.storage.sessions.path)) ?? []
    #expect(journals.filter { !$0.hasPrefix("._") }.isEmpty)
}

@Test func aModelWhoseDensityIsNotConfirmedNeedsAReferenceMeasurement() {
    // Detects Setup that uses an unconfirmed pixel density from the device table instead of a measurement.
    func facts(_ model: String) -> DeviceFacts {
        DeviceFacts(model: model, os: "iPadOS 27.0", nativeScale: 2,
                    app: AppInfo(version: "0", build: "0", sourceRevision: "test"), input: .pencil)
    }
    #expect(DeviceTable.density(model: "iPad16,3")?.ppiConfirmed == false)
    #expect(facts("iPad16,3").tableScale == nil)
    #expect(facts("iPad99,9").tableScale == nil)
    #expect(facts("iPad17,2").tableScale == MillimetreScale.lookup(model: "iPad17,2", nativeScale: 2))
}
