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
