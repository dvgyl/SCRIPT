import Foundation
import Testing
@testable import ScriptCore

@Test(arguments: Contract.journalFixtures)
func theFoldEqualsTheFixtureSummary(name: String) throws {
    // Detects a Swift fold that disagrees with the generator's summary for a fixture journal.
    let recovery = Journal.recover(try Contract.data("fixtures/journal/\(name).jsonl"))
    guard case .clean(let state) = recovery else {
        Issue.record("\(name) does not fold cleanly: \(recovery)")
        return
    }
    #expect(state.summary.json == (try Contract.json("fixtures/journal/\(name).fold.json")))
}

@Test func theExportFixtureReadsAndFoldsToItsSummary() throws {
    // Detects an export reader that rejects a valid manifest or folds the exported journal differently.
    let export = try Storage.readExport(Contract.url(Contract.exportFixture))
    #expect(export.state.summary.json == (try Contract.json("\(Contract.exportFixture).fold.json")))
}

@Test(arguments: Contract.recoveryFixtures)
func eachRecoveryFixtureGivesItsExpectedResult(name: String) throws {
    // Detects recovery that repairs interior damage, keeps a torn tail, or miscounts the removed bytes.
    let found: JSON = switch Journal.recover(try Contract.data("fixtures/recovery/\(name).jsonl")) {
    case .clean(let state):
        .object([("outcome", .string("clean")), ("lines", .int(state.session.lines)), ("truncatedBytes", .int(0))])
    case .truncated(let state, let bytes):
        .object([("outcome", .string("truncated")), ("lines", .int(state.session.lines)), ("truncatedBytes", .int(bytes))])
    case .damaged(let line):
        .object([("outcome", .string("damaged")), ("line", .int(line))])
    }
    #expect(found == (try Contract.json("fixtures/recovery/\(name).expected.json")))
}

@Test func theFoldRefusesLinesOutsideTheirNesting() throws {
    // Detects a fold that accepts ink outside a contact, a trial end with the pen down, or recounted totals (I4, I6).
    let lines = Contract.lines(try Contract.data("fixtures/journal/basic.jsonl"))
    let opening = try SessionState(opening: try LineCodec.decode(lines[0]))
    let started = try LineCodec.decode(lines[1])

    var state = opening
    try state.apply(started)
    let orphan = Sample(contact: .first, t: 1, x: 1, y: 1, force: nil, altitude: nil, azimuth: nil, roll: nil,
                        phase: .moved, est: [], pending: nil)
    #expect(throws: FoldError(line: 2, violation: .contactNotDown(.first))) {
        try state.apply(Entry(seq: 2, event: .sample(orphan)))
    }

    try state.apply(Entry(seq: 2, event: .contactBegan(ContactBegan(t: 1, contact: .first, maxForce: nil))))
    #expect(throws: FoldError(line: 3, violation: .contactsDown)) {
        try state.apply(Entry(seq: 3, event: .trialEnded(TrialEnded(t: 2, trial: TrialIndex(count: 0), reason: .participantDone))))
    }

    var ready = opening
    #expect(throws: FoldError(line: 1, violation: .notAllowed(.response, in: .ready))) {
        try ready.apply(Entry(seq: 1, event: .response(Response(t: 1, block: BlockIndex(count: 0), item: "phq9-1", value: 0))))
    }
    var wrongCounts = KindCounts()
    wrongCounts[.sessionOpened] = 2
    #expect(throws: FoldError(line: 1, violation: .countsMismatch)) {
        try ready.apply(Entry(seq: 1, event: .sessionClosed(SessionClosed(t: 1, counts: wrongCounts))))
    }
    #expect(throws: FoldError(line: 1, violation: .seq(expected: 1))) {
        try ready.apply(Entry(seq: 5, event: .handoff(Handoff(t: 1, to: .participant))))
    }
}
