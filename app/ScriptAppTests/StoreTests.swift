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

@Test @MainActor func aFlagInASupervisedSessionChangesNothingThatTheParticipantSees() async throws {
    // Detects a supervised flow that differs when a safety item is flagged, so the participant could infer the result.
    func run(flag: Bool) async throws -> (stages: [Stage], flags: Int, lists: Int) {
        let root = try temporaryFolder()
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try makeStore(root: root)
        openSupervised(store)
        store.start()
        store.runTasks()
        var stages: [Stage] = []
        while let item = store.currentItem, stages.count < 40 {
            await store.answer(flag && item.item.id == "phq9-9" ? 1 : 0)
            stages.append(store.live!.stage)
        }
        let safety = store.state!.session.safety
        return (stages, safety.flags, safety.crisisListShown)
    }
    let plain = try await run(flag: false)
    let flagged = try await run(flag: true)
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
    await store.answerBlocks(["phq9-9": 2])
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
        await store.answer(0)
    }
    await store.answer(1)
    #expect(store.live?.stage == .flagCrisisList)
    #expect(store.state?.session.safety.crisisListShown == 1)

    store.continueAfterCrisisList()
    #expect(store.currentItem?.item.id == "difficulty")
    await store.answer(nil)
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
        await first.answerBlocks()
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
    #expect(second.live?.stage == .confirmCode)
    #expect(second.confirmCode("t-home"))
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
    let unconfirmed = PixelDensity(model: "iPad16,3", name: "test", ppi: 264, ppiConfirmed: false)
    #expect(facts("iPad16,3").tableScale(density: { _ in unconfirmed }) == nil)
    #expect(facts("iPad16,3").tableScale != nil)
    #expect(facts("iPad99,9").tableScale == nil)
    #expect(facts("iPad17,2").tableScale == MillimetreScale.lookup(model: "iPad17,2", nativeScale: 2))
}

/// A self-administered session under the remote-review configuration, imported into the store, at its first item.
@MainActor
private func openSelfAtFirstItem(_ store: SessionStore, participant: ParticipantCode = "T-0001") throws {
    let config = try store.importConfiguration(try remoteReviewConfigurationData()).get()
    store.open(opened(.selfAdministered, participant: participant, configuration: config, battery: store.contract.battery),
               configuration: config, dominant: .left)
    store.start()
    store.runTasks()
    store.acknowledgeDisclosure()
}

@MainActor
private func answerUntil(_ item: ItemID, in store: SessionStore) async {
    var guardCount = 0
    while let current = store.currentItem, current.item.id != item, guardCount < 20 {
        await store.answer(0)
        guardCount += 1
    }
}

@Test @MainActor func aSelfAdministeredFlagStillClosesAndExportsWhenTheOperatorEndsTheSession() async throws {
    // Detects a self-administered session with a flag that Review cannot close or export, because Review waits for
    // an operator action that a self-administered session never takes.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    try openSelfAtFirstItem(store)
    let id = store.live!.opened.sessionID
    await answerUntil("phq9-9", in: store)
    await store.answer(1)
    store.continueAfterCrisisList()
    store.operatorTookOver()
    await store.endEarly()
    #expect(store.live?.stage == .review)
    store.recordAction("clinicianAssessment")
    #expect(store.state?.session.safety.actions == 0)
    store.showSupportList()
    #expect(store.live?.stage == .closingCrisisList)
    store.continueAfterCrisisList()
    #expect(store.live?.stage == .finished)

    let result = try #require(await store.export(id))
    let saved = try Journal.read(try Data(contentsOf: result.url.appendingPathComponent("journal.jsonl")))
    #expect(saved.status == .closed)
    let safety = saved.summary.safety
    #expect([safety.flags, safety.crisisListShown, safety.operatorAlerts, safety.actions] == [Int]([1, 2, 0, 0]))
}

@Test @MainActor func anOperatorEndOfASelfAdministeredBlockBeforeItsSafetyItemFlagsItAndShowsTheList() async throws {
    // Detects an operator end of a self-administered PHQ-9 block before item 9 that leaves no flag, or a crisis-list
    // display in the journal that the iPad never shows.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    try openSelfAtFirstItem(store)
    await store.answer(0)
    await store.answer(0)
    store.operatorTookOver()
    await store.endEarly()
    #expect(store.live?.stage == .flagCrisisList)
    let safety = try #require(store.state?.session.safety)
    #expect(safety.flags == 1 && safety.crisisListShown == 1)
    #expect(store.state?.summary.questionnaires.map(\.outcome) == [.declined])
    store.continueAfterCrisisList()
    #expect(store.live?.stage == .review)
}

@Test @MainActor func aResumedSelfAdministeredSessionShowsTheCrisisListForTheFlagOfItsInterruptedBlock() async throws {
    // Detects a crash in a self-administered PHQ-9 block before item 9 after which the session raises no flag, or
    // never shows the participant the crisis list for it.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = try makeStore(root: root)
    try openSelfAtFirstItem(first, participant: "T-RESUME")
    await first.answer(0)
    try await first.live!.sink.close()

    let second = try makeStore(root: root)
    second.launch()
    #expect(second.state?.summary.safety.flags == 1)
    #expect(second.state?.summary.questionnaires.map(\.outcome) == [.interrupted])
    #expect(second.confirmCode("T-RESUME"))
    second.start()
    #expect(second.live?.stage == .flagCrisisList)
    #expect(second.state?.session.safety.crisisListShown == 1)
    second.continueAfterCrisisList()
    second.acknowledgeDisclosure()
    #expect(second.currentItem?.item.id == "phq9-1")
}

@Test @MainActor func aResumedSelfAdministeredSessionWaitsForTheCodeOfItsParticipant() async throws {
    // Detects a self-administered session that reopens for whoever holds the iPad, so that a second person's answers
    // join the first person's code.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = try makeStore(root: root)
    let demo = first.contract.demo
    first.open(opened(.selfAdministered, participant: "T-OWNER", configuration: demo, battery: first.contract.battery),
               configuration: demo, dominant: .left)
    first.start()
    try await first.live!.sink.close()

    let second = try makeStore(root: root)
    second.launch()
    #expect(second.live?.stage == .confirmCode)
    let lines = second.state?.session.lines
    #expect(!second.confirmCode("T-OTHER"))
    second.start()
    #expect(second.live?.stage == .confirmCode)
    #expect(second.state?.session.lines == lines)
    #expect(second.confirmCode(" t-owner "))
    #expect(second.live?.stage == .ready)
}

@Test @MainActor func aSelfAdministeredSessionPastItsResumeWindowClosesForExport() async throws {
    // Detects an interrupted self-administered session that reopens a day after it started, or that stays
    // interrupted, where no export can take its flags to the study.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = try makeStore(root: root)
    let demo = first.contract.demo
    first.open(opened(.selfAdministered, configuration: demo, battery: first.contract.battery,
                      openedAt: Date(timeIntervalSinceNow: -25 * 3600)),
               configuration: demo, dominant: .left)
    let id = first.live!.opened.sessionID
    first.start()
    try await first.live!.sink.close()

    let second = try makeStore(root: root)
    second.launch()
    #expect(second.live == nil)
    #expect(second.listing.first { $0.sessionID == id }?.status == .closed)
    let result = try #require(await second.export(id))
    let saved = try Journal.read(try Data(contentsOf: result.url.appendingPathComponent("journal.jsonl")))
    #expect(saved.summary.counts[.recovered] == 1 && saved.status == .closed)
}

@Test @MainActor func aSelfAdministeredFlagIsOnStorageBeforeTheCrisisListShows() async throws {
    // Detects a crisis list that appears before the flag that caused it is synced to storage.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let gate = SyncGate(open: true)
    let store = try makeStore(root: root) { _, _ in GatedSink(gate: gate) }
    try openSelfAtFirstItem(store)
    await answerUntil("phq9-9", in: store)
    gate.close()
    let answering = Task { await store.answer(1) }
    #expect(await gate.waitForSync())
    #expect(gate.flaggedBeforeSync)
    #expect(store.live?.stage == .working)
    gate.open()
    await answering.value
    #expect(store.live?.stage == .flagCrisisList)
}

@Test @MainActor func nothingFollowsAnAnswerUntilItIsOnStorage() async throws {
    // Detects a screen after a flagged answer that shows while the flag is on its way to storage, a crisis-list
    // display that the journal records before the list shows, or a second tap that answers the next item unseen.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let gate = SyncGate(open: true)
    let store = try makeStore(root: root) { _, _ in GatedSink(gate: gate) }
    try openSelfAtFirstItem(store)
    await answerUntil("phq9-9", in: store)
    gate.close()
    let answering = Task { await store.answer("phq9-9", 1) }
    #expect(await gate.waitForSync())
    // The view asks the store to go on while the sync waits, and the participant taps Next again.
    store.advance()
    await store.answer("phq9-9", 1)
    await store.answer("difficulty", 0)
    #expect(store.currentItem?.item.id == "phq9-9")
    #expect(store.live?.stage == .working)
    #expect(gate.appendedWhileHeld.isEmpty)
    #expect(store.state?.session.safety.crisisListShown == 0)
    gate.open()
    await answering.value
    #expect(store.live?.stage == .flagCrisisList)
    let safety = try #require(store.state?.session.safety)
    #expect(safety.flags == 1 && safety.crisisListShown == 1)
    #expect(store.state?.summary.questionnaires.first?.answered == 9)
    store.continueAfterCrisisList()
    #expect(store.currentItem?.item.id == "difficulty")
}

@Test @MainActor func anOperatorEndWaitsForStorageBeforeTheNextScreen() async throws {
    // Detects a second tap on End the session, or a return to the participant, that moves the session on while the
    // flag of the ended block is on its way to storage, so the crisis list that the block needs never shows.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let gate = SyncGate(open: true)
    let store = try makeStore(root: root) { _, _ in GatedSink(gate: gate) }
    try openSelfAtFirstItem(store)
    await store.answer(0)
    store.operatorTookOver()
    gate.close()
    let ending = Task { await store.endEarly() }
    #expect(await gate.waitForSync())
    store.returnToParticipant()
    store.leave()
    await store.endEarly()
    #expect(store.live?.stage == .paused && store.live?.away == false)
    #expect(store.state?.session.safety.crisisListShown == 0)
    gate.open()
    await ending.value
    #expect(store.live?.stage == .flagCrisisList)
    #expect(store.state?.session.safety.flags == 1 && store.state?.session.safety.crisisListShown == 1)
}

@Test @MainActor func aResumeEndsAfterFiveWrongCodesAndARelaunchGivesNoNewTry() async throws {
    // Detects a resume screen that takes any number of wrong codes, or a count that a relaunch resets, so that a
    // person without the code can try codes until one continues the session of another participant.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = try makeStore(root: root)
    let demo = first.contract.demo
    first.open(opened(.selfAdministered, participant: "T-OWNER", configuration: demo, battery: first.contract.battery),
               configuration: demo, dominant: .left)
    let id = first.live!.opened.sessionID
    first.start()
    try await first.live!.sink.close()

    let second = try makeStore(root: root)
    second.launch()
    #expect(second.codeAttemptsLeft == 5)
    for left in [4, 3, 2] {
        #expect(!second.confirmCode("T-OTHER"))
        #expect(second.codeAttemptsLeft == left && second.live?.stage == .confirmCode)
        #expect(String(second.wrongCodeMessage.characters).hasSuffix("You can try \(left) more times."))
    }
    try await second.live!.sink.close()

    let third = try makeStore(root: root)
    third.launch()
    #expect(third.live?.stage == .confirmCode && third.codeAttemptsLeft == 2)
    #expect(!third.confirmCode("T-OTHER"))
    #expect(String(third.wrongCodeMessage.characters).hasSuffix("You can try 1 more time."))
    #expect(!third.confirmCode("T-OTHER"))
    // The session had no questionnaire block, so it closes with no support list.
    #expect(third.live?.stage == .finished)
    #expect(!third.confirmCode("T-OWNER"))
    #expect(third.live?.stage == .finished)
    let result = try #require(await third.export(id))
    let saved = try Journal.read(try Data(contentsOf: result.url.appendingPathComponent("journal.jsonl")))
    #expect(saved.status == .closed && saved.summary.trials.isEmpty)
}

@Test @MainActor func aCorrectCodeForgetsTheWrongCodesBeforeIt() async throws {
    // Detects a participant who loses the session after a few typing errors in each of several resumes.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    var store = try makeStore(root: root)
    let demo = store.contract.demo
    store.open(opened(.selfAdministered, participant: "T-OWNER", configuration: demo, battery: store.contract.battery),
               configuration: demo, dominant: .left)
    store.start()
    for _ in 1...3 {
        try await store.live!.sink.close()
        store = try makeStore(root: root)
        store.launch()
        for _ in 1...4 { #expect(!store.confirmCode("T-OWNERS")) }
        #expect(store.codeAttemptsLeft == 1)
        #expect(store.confirmCode("T-OWNER"))
        #expect(store.live?.stage == .ready && store.codeAttemptsLeft == 5)
    }
}

@Test @MainActor func aResumeThatEndsAfterWrongCodesShowsTheSupportListWhenTheSessionHadABlock() async throws {
    // Detects a session with an interrupted questionnaire block that closes after wrong codes with no support list,
    // or that cannot close because the flag of the block still waits for the list.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = try makeStore(root: root)
    try openSelfAtFirstItem(first, participant: "T-RESUME")
    let id = first.live!.opened.sessionID
    await first.answer(0)
    try await first.live!.sink.close()

    let second = try makeStore(root: root)
    second.launch()
    #expect(second.state?.summary.safety.flags == 1)
    for _ in 1...5 { #expect(!second.confirmCode("T-WRONG")) }
    #expect(second.live?.stage == .closingCrisisList)
    second.continueAfterCrisisList()
    #expect(second.live?.stage == .finished)
    let result = try #require(await second.export(id))
    #expect(result.encrypted == false)
    let saved = try Journal.read(try Data(contentsOf: result.url.appendingPathComponent("journal.jsonl")))
    #expect(saved.status == .closed)
    #expect(saved.summary.safety.flags == 1 && saved.summary.safety.crisisListShown == 1)
}

@Test @MainActor func noTrialStartsWhileTheAppIsInTheBackground() throws {
    // Detects a trial that starts while the app is in the background, so its time limit runs out before the
    // participant comes back and the task is lost.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    openSupervised(store)
    store.start()
    let view = ViewSize(width: 834, height: 1000)!
    store.startTrial(view: view)
    store.begin()
    store.enterBackground()
    #expect(store.state?.summary.trials.map(\.outcome) == [.interrupted])
    store.startTrial(view: view)
    #expect(store.state?.summary.trials.count == 1)
    store.becomeActive()
    store.startTrial(view: view)
    #expect(store.state?.summary.trials.map(\.outcome) == [.interrupted, .open])
}

@Test @MainActor func aSelfAdministeredSessionThatStopsAfterABlockStartedShowsTheSupportList() async throws {
    // Detects a write failure in a self-administered questionnaire block after which the participant, who is alone,
    // never sees the support list.
    func stopped(at kind: LineKind) async throws -> LiveSession? {
        let root = try temporaryFolder()
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try makeStore(root: root) { url, onFailure in FailingSink(url: url, failAt: kind, onFailure: onFailure) }
        let config = try store.importConfiguration(try remoteReviewConfigurationData()).get()
        store.open(opened(.selfAdministered, configuration: config, battery: store.contract.battery),
                   configuration: config, dominant: .left)
        store.start()
        store.runTasks()
        store.acknowledgeDisclosure()
        await store.answer(0)
        var waits = 0
        while store.live?.stage != .stopped, waits < 1000 {
            await Task.yield()
            waits += 1
        }
        await store.rebuild?.value
        return store.live
    }
    let inTask = try #require(await stopped(at: .trialBegan))
    #expect(inTask.stage == .stopped && !inTask.supportListOnStop)
    let inBlock = try #require(await stopped(at: .response))
    #expect(inBlock.stage == .stopped && inBlock.supportListOnStop)
}

@Test @MainActor func aShortParticipantCodeShowsNoCharacterUntilThePINUnlocks() throws {
    // Detects a mask that shows a code of 3 or 4 characters in full, or a paused screen that shows a code in full
    // before the PIN.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    #expect(store.shown("P42") == "•••")
    #expect(store.shown("AB12") == "•••")
    #expect(store.shown("AB123") == "•••3")
    #expect(store.shown("SITE-0042") == "•••0042")
    store.operatorTookOver(withPIN: true)
    #expect(store.shown("P42") == "P42")
}

@Test @MainActor func homeMasksAParticipantCodeUntilThePINUnlocks() throws {
    // Detects a Home list that shows each participant code in full to whoever holds the iPad without the PIN.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    openSupervised(store, participant: "T-0042")
    store.leave()
    let row = try #require(store.listing.first { $0.participant != nil })
    #expect(store.title(of: row) == "•••42")
    store.operatorTookOver()
    #expect(store.title(of: row) == "•••42")
    store.operatorTookOver(withPIN: true)
    #expect(store.title(of: row) == "T-0042")
}

@Test @MainActor func aDamagedJournalOnHomeLeavesTheIPadAsADamagedPackage() async throws {
    // Detects a damaged journal that Home lists with no way to take it, and the flags in it, off the iPad.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = try makeStore(root: root)
    openSupervised(first, participant: "T-DAMAGE")
    first.start()
    let url = first.live!.url
    try await first.live!.sink.close()
    var lines = try Data(contentsOf: url).split(separator: 0x0A, omittingEmptySubsequences: false)
    lines.insert(Data("{not json".utf8)[...], at: 1)
    try Data(lines.joined(separator: [0x0A])).write(to: url)

    let second = try makeStore(root: root)
    second.launch()
    let row = try #require(second.listing.first { $0.damagedFile != nil })
    let result = try #require(await second.exportDamaged(row.damagedFile!))
    #expect(result.name.hasPrefix("SCRIPT_DAMAGED_") && !result.encrypted)
    #expect(!second.listing.contains { $0.status == .damaged })
    #expect(second.listing.contains { $0.status == .exported && $0.title == result.name })
}

@Test func anEmptySourceRevisionIsRecordedAsUnknown() {
    // Detects a journal that records an empty source revision from a build that did not stamp one.
    #expect(DeviceFacts.sourceRevision("") == "unknown")
    #expect(DeviceFacts.sourceRevision(nil) == "unknown")
    #expect(DeviceFacts.sourceRevision("abc1234-dirty") == "abc1234-dirty")
}

@Test func theReviewIntervalReadsInWholeWorkingDays() throws {
    // Detects a disclosure that reads "0 working day" for half a working day, or a count without its plural.
    var config = try remoteReviewConfiguration()
    for (weekdays, words) in [(0.5, "1 working day."), (1.0, "1 working day."), (1.5, "2 working days.")] {
        config.safety.remoteReview?.routineReviewWithinWeekdays = weekdays
        let review = try #require(config.safety.remoteReview)
        #expect(String(review.disclosure.characters).hasSuffix("within \(words)"), "\(weekdays)")
    }
}

@Test @MainActor func theDeviceDiagnosticsHoldNoParticipantCodeAndNoSessionIdentifier() throws {
    // Detects diagnostics that take a participant code or a session identifier off the iPad, or that miss the
    // storage facts that the device test reads.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try makeStore(root: root)
    openSupervised(store, participant: "T-SECRET")
    let id = store.live!.opened.sessionID.description
    store.leave()
    let display = DeviceDiagnostics.Display(widthPoints: 834, heightPoints: 1210, scale: 2, maximumFramesPerSecond: 120,
                                            voiceOver: false, reduceMotion: false, contentSizeCategory: "L")
    let name = try DeviceDiagnostics.save(store: store, display: display, at: WallTime(Date(timeIntervalSince1970: 0)),
                                          freeBytes: 123)
    #expect(name.hasPrefix("SCRIPT_DIAGNOSTICS_1970") && name.hasSuffix(".json"))
    let data = try Data(contentsOf: root.appendingPathComponent("Documents/Diagnostics/\(name)"))
    let text = String(decoding: data, as: UTF8.self).lowercased()
    #expect(!text.contains("t-secret") && !text.contains(id.lowercased()) && !text.contains(id.prefix(8).lowercased()))
    let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    #expect(json["format"] as? String == "script.diagnostics")
    #expect(json["sessions"] as? [String: Int] == ["open": 1])
    #expect((json["app"] as? [String: String])?["sourceRevision"] == "test")
    let storage = try #require(json["storage"] as? [String: Any])
    #expect(storage["freeBytes"] as? Int == 123)
    let items = try #require(storage["items"] as? [[String: Any]])
    #expect(items.compactMap { $0["name"] as? String } == ["Sessions", "Staging", "Exports", "Damaged", "Deletions.jsonl",
                                                          "ExportIndex.jsonl", "ResumeAttempts.jsonl"])
    #expect(items.first?["excludedFromBackup"] as? Bool == true)
}

@Test @MainActor func aTakeoverDuringAFlaggedAnswerStillShowsTheCrisisListOnTheReturn() async throws {
    // Detects an operator takeover while a flagged answer is on its way to storage that leaves the participant, after
    // the return, at an item that takes no answer, because the crisis list for the flag never showed.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let gate = SyncGate(open: true)
    let store = try makeStore(root: root) { _, _ in GatedSink(gate: gate) }
    try openSelfAtFirstItem(store)
    await answerUntil("phq9-9", in: store)
    gate.close()
    let answering = Task { await store.answer("phq9-9", 2) }
    #expect(await gate.waitForSync())
    store.operatorTookOver()
    #expect(store.live?.stage == .paused)
    gate.open()
    await answering.value
    #expect(store.live?.stage == .paused)
    store.returnToParticipant()
    #expect(store.live?.stage == .flagCrisisList)
    #expect(store.state?.session.safety.crisisListShown == 1)
    store.continueAfterCrisisList()
    #expect(store.currentItem?.item.id == "difficulty")
    await store.answer(0)
    #expect(store.state?.summary.questionnaires.first?.outcome == .completed)
}
