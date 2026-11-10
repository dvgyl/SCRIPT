import Foundation
import Testing
@testable import ScriptCore

private let pro = ChannelPlan.plan(pencil: .pro, hoverSupported: true)

private func reading(_ key: UInt, _ t: Double, _ phase: PenPhase, pending: TouchProperties = [], update: Int? = nil,
                     force: Double? = 0.3) -> PenReading {
    PenReading(key: TouchKey(key), t: t, x: 10, y: 20, force: force, maxForce: 4, altitude: 1, azimuth: 2, roll: 0.5,
               phase: phase, estimated: pending, expectingUpdates: pending, update: update.map(UpdateIndex.init))
}

private func contacts(_ events: [Event]) -> [Int] {
    events.compactMap { if case .contactBegan(let e) = $0 { e.contact.rawValue } else { nil } }
}

private func corrections(_ events: [Event]) -> [Correction] {
    events.compactMap { if case .correction(let c) = $0 { c } else { nil } }
}

@Test func contactsAreUniqueInTheSessionAndStartAtOne() throws {
    // Detects contact numbers that restart per trial or after a relaunch, which makes late corrections ambiguous.
    var recorder = InkRecorder(plan: pro)
    let first = recorder.pen([reading(7, 1, .began), reading(7, 1.01, .ended)])
    let second = recorder.pen([reading(7, 2, .began), reading(8, 2, .began), reading(7, 2.01, .ended), reading(8, 2.01, .ended)])
    #expect(contacts(first) == [1])
    #expect(contacts(second) == [2, 3])

    let config = try Contract.demoConfiguration()
    let opened = makeOpened(configuration: config, battery: try Contract.battery())
    var d = try Driver(opened: opened, rules: try rules(for: opened, configuration: config))
    try d.run(.startTrial(Driver.plan("spiral-trace"), t: 101))
    try d.write(first + second)
    var resumed = InkRecorder(resuming: d.state.session)
    #expect(contacts(resumed.pen([reading(9, 3, .began)])) == [4])
}

@Test func aLateCorrectionAfterTheLiftFindsItsContact() {
    // Detects a correction that loses its contact once the pen lifts, so the library cannot join it to its sample.
    var recorder = InkRecorder(plan: pro)
    _ = recorder.pen([reading(5, 1, .began, pending: [.force], update: 41), reading(5, 1.01, .ended)])
    _ = recorder.pen([reading(6, 2, .began), reading(6, 2.01, .ended)])
    let late = recorder.estimatesUpdated([reading(5, 1, .moved, update: 41, force: 0.42)], arrived: 2.5)
    #expect(corrections(late) == [Correction(contact: .first, update: UpdateIndex(41), arrived: 2.5, x: nil, y: nil,
                                             force: 0.42, altitude: nil, azimuth: nil, roll: nil, expect: [])])
}

@Test func oneCorrectionResolvesEverySampleThatSharesItsKey() throws {
    // Detects a correction that clears only one of the samples with its (contact, update) key.
    let config = try Contract.demoConfiguration()
    let opened = makeOpened(configuration: config, battery: try Contract.battery())
    var d = try Driver(opened: opened, rules: try rules(for: opened, configuration: config))
    try d.run(.startTrial(Driver.plan("loops"), t: 101))
    try d.write(d.recorder.pen([
        reading(3, 1, .began, pending: [.force, .altitude], update: 5),
        reading(3, 1.01, .moved, pending: [.force], update: 5),
        reading(3, 1.02, .ended),
    ]))
    #expect(d.state.summary.trials[0].unresolved == 2)
    let fix = d.recorder.estimatesUpdated([reading(3, 1, .moved, update: 5)], arrived: 1.5)
    #expect(corrections(fix).map(\.expect) == [[]])
    #expect(corrections(fix).first?.altitude == 1)
    try d.write(fix)
    #expect(d.state.summary.trials[0].unresolved == 0)
    #expect(d.state.summary.trials[0].corrections == 1)
}

@Test func aSampleAfterACorrectionOfItsKeyKeepsItsOwnEstimate() throws {
    // Detects an unresolved count that also counts the samples that a correction resolved, when a later sample has
    // the same (contact, update) key. The Python fold counts each sample by its own pending bits.
    let config = try Contract.demoConfiguration()
    let opened = makeOpened(configuration: config, battery: try Contract.battery())
    var d = try Driver(opened: opened, rules: try rules(for: opened, configuration: config))
    try d.run(.startTrial(Driver.plan("loops"), t: 101))
    try d.write(d.recorder.pen([reading(3, 1, .began, pending: [.force], update: 5)]))
    try d.write(d.recorder.estimatesUpdated([reading(3, 1, .moved, update: 5)], arrived: 1.005))
    #expect(d.state.summary.trials[0].unresolved == 0)
    try d.write(d.recorder.pen([reading(3, 1.01, .moved, pending: [.force], update: 5), reading(3, 1.02, .ended)]))
    #expect(d.state.summary.trials[0].unresolved == 1)
    #expect(d.state.summary.trials[0].corrections == 1)
}

@Test func anUnmatchedCorrectionHasNoContactKey() throws {
    // Detects an unmatched correction written with a guessed contact, which the library would join to a wrong sample.
    var recorder = InkRecorder(plan: pro)
    _ = recorder.pen([reading(5, 1, .began), reading(5, 1.01, .ended)])
    let stray = recorder.estimatesUpdated([reading(5, 1, .moved, update: 99, force: 0.5)], arrived: 2)
    let line = try LineCodec.encode(Entry(seq: 9, event: stray[0]))
    #expect(corrections(stray).first?.contact == nil)
    #expect(!String(decoding: line, as: UTF8.self).contains("contact"))
}

@Test func theChannelPlanFollowsTheDeclaredPencil() {
    // Detects a plan or a sample that records a channel the declared Pencil does not have.
    #expect(ChannelPlan.plan(pencil: .second, hoverSupported: false)
        == ChannelPlan(force: true, altitude: true, azimuth: true, roll: false, hover: false))
    #expect(ChannelPlan.plan(pencil: .usbC, hoverSupported: true)
        == ChannelPlan(force: false, altitude: true, azimuth: true, roll: false, hover: true))
    #expect(ChannelPlan.plan(pencil: .none, hoverSupported: true)
        == ChannelPlan(force: false, altitude: false, azimuth: false, roll: false, hover: false))

    var recorder = InkRecorder(plan: ChannelPlan.plan(pencil: .usbC, hoverSupported: false))
    let events = recorder.pen([reading(1, 1, .began), reading(1, 1.01, .ended)])
    guard case .contactBegan(let began) = events[0], case .sample(let sample) = events[1] else {
        Issue.record("unexpected events \(events)")
        return
    }
    #expect(began.maxForce == nil && sample.force == nil && sample.roll == nil && sample.altitude == 1)
    #expect(recorder.hover(HoverReading(t: 1, x: 1, y: 1, z: 0.5)) == nil)
}

private func ends(_ events: [Event]) -> [ContactEnded] {
    events.compactMap { if case .contactEnded(let e) = $0 { e } else { nil } }
}

@Test func aBeganOnAKeyThatIsDownEndsTheOldContactFirst() throws {
    // Detects two strokes merged into one contact when UIKit reuses a touch whose lift reading was lost.
    var recorder = InkRecorder(plan: pro)
    let first = recorder.pen([reading(7, 1, .began), reading(7, 1.01, .moved)])
    let second = recorder.pen([reading(7, 2, .began), reading(7, 2.01, .ended)])
    #expect(contacts(first) == [1])
    #expect(second.first == .contactEnded(ContactEnded(t: 1.01, contact: .first, cancelled: true)))
    #expect(contacts(second) == [2])

    let config = try Contract.demoConfiguration()
    let opened = makeOpened(configuration: config, battery: try Contract.battery())
    var d = try Driver(opened: opened, rules: try rules(for: opened, configuration: config))
    try d.run(.startTrial(Driver.plan("spiral-trace"), t: 0.5))
    try d.write(first + second)
    #expect(d.state.summary.trials[0].contacts == 2)
    try d.run(.endTrial(reason: .participantDone, t: 3))
}

@Test func aLiftWithoutAUsableReadingStillEndsItsContact() {
    // Detects a contact left down by a lift reading with a non-finite time, so the next stroke joins it.
    var recorder = InkRecorder(plan: pro)
    _ = recorder.pen([reading(7, 1, .began), reading(7, 1.01, .moved)])
    #expect(recorder.pen([reading(7, .nan, .ended)]) == [
        .contactEnded(ContactEnded(t: 1.01, contact: .first, cancelled: false)),
    ])
    let next = recorder.pen([reading(7, 2, .began)])
    #expect(contacts(next) == [2])
    #expect(ends(next).isEmpty)
}

private func tabletReading(_ t: Double, _ phase: PenPhase, tiltX: Double = 0.1, rotation: Double? = 15) -> PenReading {
    PenReading(key: TouchKey(1), t: t, x: 10000, y: 8000, force: 0.3, maxForce: 1, altitude: 1, tiltX: tiltX, tiltY: -0.2,
               rotation: rotation, phase: phase)
}

@Test func aTabletReadingKeepsOnlyPlannedChannelsAndNoEstimates() throws {
    // Detects rotation written for a Pro Pen 3, a Pencil channel written for a tablet pen, or a nonzero est or a
    // pending estimate on a tablet sample, which has no estimated properties.
    #expect(ChannelPlan.plan(tabletPen: .proPen3)
        == ChannelPlan(force: true, altitude: false, azimuth: false, roll: false, hover: true, tiltX: true, tiltY: true, rotation: false))
    #expect(ChannelPlan.plan(tabletPen: .unknown)
        == ChannelPlan(force: true, altitude: false, azimuth: false, roll: false, hover: true))

    var recorder = InkRecorder(plan: ChannelPlan.plan(tabletPen: .proPen3))
    let events = recorder.pen([tabletReading(1, .began), tabletReading(1.01, .ended, tiltX: 1.5)])
    guard case .contactBegan(let began) = events[0], case .sample(let first) = events[1], case .sample(let second) = events[2] else {
        Issue.record("unexpected events \(events)")
        return
    }
    #expect(began.maxForce == 1)
    #expect(first == Sample(contact: .first, t: 1, x: 10000, y: 8000, force: 0.3, tiltX: 0.1, tiltY: -0.2, phase: .began, est: []))
    #expect(second.tiltX == nil && second.tiltY == -0.2 && second.rotation == nil && second.altitude == nil)
    #expect(second.est == [] && second.pending == nil)
    let line = String(decoding: try LineCodec.encode(Entry(seq: 1, event: events[1])), as: UTF8.self)
    #expect(line == #"{"seq":1,"kind":"sample","contact":1,"t":1.0,"x":10000.0,"y":8000.0,"force":0.3,"tiltX":0.1,"tiltY":-0.2,"phase":"began","est":0}"# + "\n")

    let hover = recorder.hover(HoverReading(t: 2, clock: .event, x: 9800, y: 8000, altitude: 0.9, tiltX: 0.05, tiltY: -0.1, rotation: 10))
    #expect(hover == .hover(Hover(t: 2, clock: .event, x: 9800, y: 8000, tiltX: 0.05, tiltY: -0.1)))
    let hoverLine = String(decoding: try LineCodec.encode(Entry(seq: 2, event: try #require(hover))), as: UTF8.self)
    #expect(hoverLine == #"{"seq":2,"kind":"hover","t":2.0,"clock":"event","x":9800.0,"y":8000.0,"tiltX":0.05,"tiltY":-0.1}"# + "\n")

    var unknownPen = InkRecorder(plan: ChannelPlan.plan(tabletPen: .unknown))
    guard case .sample(let plain) = unknownPen.pen([tabletReading(3, .began)])[1] else { Issue.record("no sample"); return }
    #expect(plain.tiltX == nil && plain.tiltY == nil && plain.force == 0.3)
}

@Test func aProximityLeaveEndsAnOpenContactAsCancelled() {
    // Detects a contact left open when the pen leaves the tablet's range without a lift event, so the next stroke
    // joins it or the trial cannot end.
    var recorder = InkRecorder(plan: ChannelPlan.plan(tabletPen: .proPen3))
    _ = recorder.pen([tabletReading(1, .began), tabletReading(1.01, .moved)])
    #expect(recorder.cancelAll(t: 1.5) == [.contactEnded(ContactEnded(t: 1.5, contact: .first, cancelled: true))])
    #expect(recorder.cancelAll(t: 1.6) == [])
    let next = recorder.pen([tabletReading(2, .began), tabletReading(2.01, .ended)])
    #expect(contacts(next) == [2])
    #expect(ends(next) == [ContactEnded(t: 2.01, contact: ContactID(2)!, cancelled: false)])
}

@Test func aNegativeForceOrTimeNeverReachesTheJournal() throws {
    // Detects a reading that the recorder turns into a line the decoder refuses, which the writer then refuses.
    var recorder = InkRecorder(plan: pro)
    let events = recorder.pen([reading(4, 1, .began, force: -0.1), reading(4, -1, .moved), reading(4, 1.02, .ended)])
    for (seq, event) in events.enumerated() {
        _ = try LineCodec.decode(try LineCodec.encode(Entry(seq: seq, event: event)).dropLast())
    }
    let samples = events.compactMap { if case .sample(let s) = $0 { s } else { nil } }
    #expect(samples.map(\.t) == [1, 1.02])
    #expect(samples.first?.force == nil)
}
