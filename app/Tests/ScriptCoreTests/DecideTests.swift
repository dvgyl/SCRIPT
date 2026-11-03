import Foundation
import Testing
@testable import ScriptCore

private func driver(administration: Administration = .supervised, configuration: StudyConfiguration? = nil) throws -> Driver {
    let config = try configuration ?? Contract.demoConfiguration()
    let opened = makeOpened(administration: administration, configuration: config, battery: try Contract.battery())
    return try Driver(opened: opened, rules: try rules(for: opened, configuration: config))
}

@Test func aTrialWhileDrawingIsRejected() throws {
    // Detects a decider that opens a second trial over an open one, which breaks nesting rule I6.
    var d = try driver()
    try d.run(.startTrial(Driver.plan("spiral-trace"), t: 101))
    #expect(d.state.decide(.startTrial(Driver.plan("loops"), t: 102), rules: d.rules).rejection == .trialOpen)
}

@Test func anUnknownTaskIsRejected() throws {
    // Detects a decider that starts a task that is not in the battery, or a qualification task in a study session.
    let d = try driver()
    #expect(d.state.decide(.startTrial(Driver.plan("tightrope"), t: 101), rules: d.rules).rejection == .unknownTask("tightrope"))
    #expect(d.state.decide(.startTrial(Driver.plan("qual-hold"), t: 101), rules: d.rules).rejection
        == .taskNotForPurpose("qual-hold"))
}

@Test func aSupervisedSessionCannotCloseWithAnUnactionedFlag() throws {
    // Detects a supervised session that closes while a safety flag has no operator action (DESIGN section 15.2).
    var d = try driver()
    try d.answerAll("phq9", t: 200, values: ["phq9-9": 2])
    try d.run(.handoff(to: .operator, t: 230))
    try d.run(.show(.crisisList, t: 231))
    #expect(d.state.decide(.close(t: 240), rules: d.rules).rejection == .unactionedFlag)

    try d.run(.show(.operatorAlert, t: 241))
    try d.run(.recordAction("clinicianAssessment", t: 250))
    #expect(throws: Never.self) { try d.state.decide(.close(t: 260), rules: d.rules).get() }
}

@Test func aSessionCannotCloseBeforeTheCrisisListFollowsTheLastBlock() throws {
    // Detects a close after a questionnaire block without the crisis list, or a mid-block display counted as the end one.
    var d = try driver()
    try d.answerAll("gad7", t: 200)
    #expect(d.state.decide(.close(t: 230), rules: d.rules).rejection == .crisisListNotShown)

    var remote = try driver(administration: .selfAdministered, configuration: try Contract.remoteReviewConfiguration())
    try remote.answerAll("phq9", t: 200, values: ["phq9-9": nil])
    #expect(remote.state.session.safety.crisisListShown == 1)
    #expect(remote.state.decide(.close(t: 230), rules: remote.rules).rejection == .crisisListNotShown)
    try remote.run(.show(.crisisList, t: 231))
    #expect(throws: Never.self) { try remote.state.decide(.close(t: 232), rules: remote.rules).get() }
}

@Test func aSelfAdministeredFlagShowsTheCrisisListAtOnce() throws {
    // Detects a self-administered session that records a flag without showing the crisis list immediately (section 15.3).
    var d = try driver(administration: .selfAdministered, configuration: try Contract.remoteReviewConfiguration())
    try d.run(.startQuestionnaire("phq9", t: 200))
    let block = BlockIndex(count: 0)
    let events = try d.state.decide(.respond(item: "phq9-9", value: nil, t: 209), rules: d.rules).get()
    #expect(events == [
        .response(Response(t: 209, block: block, item: "phq9-9", value: nil)),
        .safetyFlagged(SafetyFlagged(t: 209, block: block, item: "phq9-9", value: nil)),
        .safetyShown(SafetyShown(t: 209, what: .crisisList, listVersion: "US-2026-11-03")),
    ])
}

@Test func aSupervisedFlagChangesNothingThatTheParticipantSees() throws {
    // Detects a supervised flow that differs when an item is flagged, or a value of 0 that raises a flag.
    var d = try driver()
    try d.run(.startQuestionnaire("phq9", t: 200))
    let block = BlockIndex(count: 0)
    #expect(try d.state.decide(.respond(item: "phq9-9", value: 1, t: 209), rules: d.rules).get() == [
        .response(Response(t: 209, block: block, item: "phq9-9", value: 1)),
        .safetyFlagged(SafetyFlagged(t: 209, block: block, item: "phq9-9", value: 1)),
    ])
    #expect(try d.state.decide(.respond(item: "phq9-9", value: 0, t: 209), rules: d.rules).get() == [
        .response(Response(t: 209, block: block, item: "phq9-9", value: 0)),
    ])
    #expect(d.state.decide(.respond(item: "phq9-9", value: 7, t: 209), rules: d.rules).rejection == .invalidValue("phq9-9", 7))
}

@Test func aSelfAdministeredSessionRefusesUnmonitoredSafetyItemsAndOperatorAlerts() throws {
    // Detects a home session that asks PHQ-9 item 9 without remote review, or shows the participant an operator alert.
    let d = try driver(administration: .selfAdministered)
    #expect(d.state.decide(.startQuestionnaire("phq9", t: 200), rules: d.rules).rejection
        == .safetyItemNeedsRemoteReview("phq9"))
    #expect(d.state.decide(.show(.operatorAlert, t: 200), rules: d.rules).rejection == .noOperator)
}

private func plan(position: Int = 1, accommodations: [Accommodation] = [],
                  origin: Point = Point(x: 147.5, y: 200)) -> TrialPlan {
    var plan = Driver.plan("spiral-trace", position: position)
    plan.accommodations = accommodations
    plan.stimulusOrigin = origin
    return plan
}

@Test(arguments: [
    (Command.startTrial(plan(position: 0), t: 101), Rejection.invalidPosition(0)),
    (.startTrial(plan(accommodations: [.readAloud, .extraTime, .readAloud]), t: 101), .repeatedAccommodation(.readAloud)),
    (.startTrial(plan(origin: Point(x: .nan, y: 200)), t: 101), .invalidStimulusOrigin),
    (.startTrial(plan(), t: -1), .invalidTime),
    (.handoff(to: .participant, t: .infinity), .invalidTime),
])
func decideRefusesAValueThatTheDecoderRefuses(command: Command, rejection: Rejection) throws {
    // Detects a decider that writes a line the decoder refuses, so the next launch moves the session to Damaged/.
    let d = try driver()
    #expect(d.state.decide(command, rules: d.rules).rejection == rejection)
}

@Test func sessionValuesThatTheDecoderRefusesCannotBeBuilt() {
    // Detects a visit number below 1, an empty document identifier or version, or an empty view in a line, which
    // the decoder refuses, so a relaunch would call the journal damaged.
    #expect(Visit(id: "V1", number: 0) == nil)
    #expect(Visit(id: "V1", number: 1)?.number == 1)
    #expect(DocumentRef(id: "", version: "1") == nil)
    #expect(DocumentRef(id: "consent", version: "") == nil)
    #expect(ViewSize(width: 0, height: 1210) == nil)
    #expect(ViewSize(width: 834, height: -1) == nil)
    #expect(ViewSize(width: .infinity, height: 1210) == nil)
}

@Test func aBlockBelowTheInstrumentsValidatedAgeIsRejected() throws {
    // Detects a decider that starts an instrument for an age band below its validated ages, which only import checked.
    let config = try Contract.demoConfiguration()
    let instruments = try Contract.instrumentsWithProbe(textStatus: "verified", minimumAge: 14)
    for (band, expected) in [(AgeBand.adolescent, Rejection.instrumentNotForAgeBand("probe")), (.youngAdult, nil)] {
        let opened = makeOpened(ageBand: band, configuration: config, battery: try Contract.battery())
        let rules = SessionRules(battery: try Contract.battery(), instruments: instruments, configuration: config,
                                 jurisdiction: opened.jurisdiction, locale: opened.locale)
        let d = try Driver(opened: opened, rules: rules)
        #expect(d.state.decide(.startQuestionnaire("probe", t: 200), rules: rules).rejection == expected, "\(band)")
    }
}

@Test func aSessionWithoutACrisisListForItsJurisdictionCannotStartABlockOrShowAList() throws {
    // Detects a questionnaire block, or a crisis-list display, in a jurisdiction for which the study has no crisis list.
    var config = try Contract.demoConfiguration()
    config.jurisdictions.append("FR")
    let opened = makeOpened(jurisdiction: "FR", configuration: config, battery: try Contract.battery())
    let d = try Driver(opened: opened, rules: try rules(for: opened, configuration: config))
    #expect(d.rules.crisisList == nil)
    #expect(d.state.decide(.startQuestionnaire("gad7", t: 200), rules: d.rules).rejection == .noCrisisList)
    #expect(d.state.decide(.show(.crisisList, t: 200), rules: d.rules).rejection == .noCrisisList)
}

@Test(arguments: [QuestionnaireEndReason.declined, .interrupted, .completed])
func aBlockThatEndsBeforeItsSafetyItemFlagsTheItemFirst(reason: QuestionnaireEndReason) throws {
    // Detects a PHQ-9 block that ends before item 9 without a flag, so a reviewer never follows up the unasked item.
    let block = BlockIndex(count: 0)
    let ended = Event.questionnaireEnded(QuestionnaireEnded(t: 210, block: block, reason: reason))
    let flag = Event.safetyFlagged(SafetyFlagged(t: 210, block: block, item: "phq9-9", value: nil))
    let list = Event.safetyShown(SafetyShown(t: 210, what: .crisisList, listVersion: "US-2026-11-03"))
    for administration in [Administration.supervised, .selfAdministered] {
        var d = try driver(administration: administration, configuration: try Contract.remoteReviewConfiguration())
        try d.run(.startQuestionnaire("phq9", t: 200))
        for n in 1...4 { try d.run(.respond(item: ItemID("phq9-\(n)")!, value: 1, t: 200 + Double(n))) }
        let expected = administration == .supervised ? [flag, ended] : [flag, list, ended]
        #expect(try d.state.decide(.endQuestionnaire(reason: reason, t: 210), rules: d.rules).get() == expected,
                "\(administration)")
        try d.run(.respond(item: "phq9-9", value: 0, t: 209))
        #expect(try d.state.decide(.endQuestionnaire(reason: reason, t: 210), rules: d.rules).get() == [ended],
                "\(administration)")
    }
    var gad = try driver()
    try gad.run(.startQuestionnaire("gad7", t: 200))
    #expect(try gad.state.decide(.endQuestionnaire(reason: reason, t: 210), rules: gad.rules).get() == [ended])
}

@Test func aRecoveryInsideABlockFlagsItsUnreachedSafetyItemInTheBoundsOfTheBlock() throws {
    // Detects a crash in a PHQ-9 block before item 9 that leaves no flag, or a flag timed in the clock of the relaunch.
    var d = try driver(administration: .selfAdministered, configuration: try Contract.remoteReviewConfiguration())
    try d.run(.startQuestionnaire("phq9", t: 200))
    try d.run(.respond(item: "phq9-1", value: 0, t: 201))
    try d.run(.respond(item: "phq9-2", value: 0, t: 202))
    let block = BlockIndex(count: 0)
    let events = d.state.recovery(t: 5, at: Fixed.launch.at, boot: Fixed.laterBoot, truncatedBytes: 0,
                                  instruments: d.rules.instruments)
    #expect(events == [
        .safetyFlagged(SafetyFlagged(t: 202, block: block, item: "phq9-9", value: nil)),
        .recovered(Recovered(t: 5, at: Fixed.launch.at, boot: Fixed.laterBoot, truncatedBytes: 0, interrupted: nil,
                             interruptedBlock: block)),
    ])
    try d.write(events)
    #expect(d.state.summary.safety.flags == 1)
    #expect(d.state.session.safety.flaggedSinceCrisisList)
    #expect(d.state.recovery(t: 6, at: Fixed.launch.at, boot: Fixed.laterBoot, truncatedBytes: 0,
                             instruments: d.rules.instruments) == [])
    try d.run(.show(.crisisList, t: 7))
    #expect(!d.state.session.safety.flaggedSinceCrisisList)
}

@Test func anInterruptedSelfAdministeredSessionClosesOnlyAfterItsResumeWindow() throws {
    // Detects a self-administered session that a later holder of the iPad can resume after the window, or a live or
    // supervised session that the window closes.
    var config = try Contract.demoConfiguration()
    config.selfAdministered.resumeWithinHours = 2
    var d = try driver(administration: .selfAdministered, configuration: config)
    try d.answerAll("gad7", t: 200)
    let inside = WallTime(text: "2026-11-03T11:59:59Z")
    let after = WallTime(text: "2026-11-03T12:00:00Z")
    #expect(d.state.decide(.expire(at: after, t: 300), rules: d.rules).rejection == .notInterrupted)
    try d.write(d.state.recovery(t: 5, at: Fixed.launch.at, boot: Fixed.laterBoot, truncatedBytes: 0,
                                 instruments: d.rules.instruments))
    #expect(d.state.decide(.expire(at: inside, t: 6), rules: d.rules).rejection == .resumeWindowOpen)
    #expect(d.state.decide(.close(t: 6), rules: d.rules).rejection == .crisisListNotShown)
    #expect(try d.state.decide(.expire(at: after, t: 6), rules: d.rules).get()
        == [.sessionClosed(SessionClosed(t: 6, counts: d.state.session.counts))])

    var supervised = try driver(configuration: config)
    try supervised.write(supervised.state.recovery(t: 5, at: Fixed.launch.at, boot: Fixed.laterBoot, truncatedBytes: 0,
                                                   instruments: supervised.rules.instruments))
    #expect(supervised.state.decide(.expire(at: after, t: 6), rules: supervised.rules).rejection == .notSelfAdministered)
}
