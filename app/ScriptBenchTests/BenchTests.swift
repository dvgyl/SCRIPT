import AppKit
import ScriptCore
import Testing
@testable import ScriptBench

private let wacom = TabletTable.geometry(model: "Wacom Intuos Pro Medium (2025)")!
private let loops: RunChoice = .bench(BenchRunPlan(program: "loops-l-v20", run: 1))

/// The closed export of a session, read back as the analyst reads it.
@MainActor
private func export(_ bench: TestBench, _ opened: SessionOpened) throws -> (folder: URL, state: SessionState, lines: [[String: Any]]) {
    let folder = bench.store.storage.exports.appendingPathComponent(opened.exportName, isDirectory: true)
    let data = try Data(contentsOf: folder.appendingPathComponent(Storage.Export.journal))
    let lines = try data.split(separator: 0x0A).map { try JSONSerialization.jsonObject(with: Data($0)) as! [String: Any] }
    return (folder, try Journal.read(data), lines)
}

@Test @MainActor func aScriptedWacomSessionWritesTheContractExport() async throws {
    // Detects a Mac export that the Python reader refuses or folds to a summary other than the Swift one.
    let bench = try TestBench()
    defer { bench.remove() }
    let store = bench.store
    bench.enter()
    let opened = try bench.opened(.bench)
    #expect(store.open(opened))
    bench.hover(x: 10000, y: 21600)
    store.setOrigin()
    #expect(store.runID(loops) == "W-M3-loops-l-v20-r1")

    store.begin(loops)
    for x in [10000, 10040, 10080] { bench.hover(x: x, y: 21600) }
    bench.point(.down, x: 10100, y: 21600)
    bench.point(.dragged, x: 10140, y: 21600)
    bench.leave()
    bench.enter()
    for x in [10180, 10200] { bench.hover(x: x, y: 21600) }
    bench.point(.down, x: 10220, y: 21600)
    bench.point(.dragged, x: 10260, y: 21600)
    bench.point(.tabletPoint, x: 10260, y: 21600)
    bench.point(.dragged, x: 10300, y: 21600)
    bench.point(.up, x: 10340, y: 21600, pressure: 0)
    #expect(store.mark(.disturbance))
    store.done()

    let lift: RunChoice = .bench(BenchRunPlan(program: "fault-lift", run: 1))
    #expect(store.runID(lift) == "W-M3-fault-lift-r1")
    store.begin(lift)
    bench.stroke(x0: 12000, y: 20000)
    for x in [12200, 12240] { bench.hover(x: x, y: 20000) }
    bench.stroke(x0: 12280, y: 20000)
    store.done()
    await store.close()

    #expect(store.live == nil)
    let (folder, state, lines) = try export(bench, opened)
    let summary = state.summary
    #expect(summary.purpose == .bench && summary.administration == nil)
    #expect(summary.trials.map(\.task) == ["bench-loops-l", "bench-loops-l"])
    #expect(summary.trials.map(\.outcome) == [.completed, .completed])
    #expect(summary.trials.map(\.contacts) == [2, 2])
    #expect(summary.counts[.hover] == 7 && summary.counts[.mark] == 1 && summary.counts[.correction] == 0)
    #expect(lines.of("contactEnded").map { $0["cancelled"] as? Bool } == [true, false, false, false])
    #expect(lines.of("sample").map { $0["phase"] as! String }.contains("stationary"))
    let runs = lines.of("trialStarted").map { $0["bench"] as! [String: Any] }
    #expect(runs.map { $0["clip"] as? String } == ["W-M3-loops-l-v20-r1", "W-M3-fault-lift-r1"])
    #expect(runs.map { $0["trajectorySHA256"] as? String } == ["loops-l-v20", "fault-lift"].map {
        store.contract.programs.program(ProgramID($0)!)!.trajectorySHA256.hex
    })
    for trial in lines.of("trialStarted") {
        #expect(trial["stimulusOrigin"] as? [String: Double] == ["x": 10000, "y": 8000])
        #expect(trial["view"] as? [String: Double] == ["width": 52600, "height": 29600])
    }
    try writeContractExport(folder, summary: summary)
}

@Test @MainActor func aTabletQualificationBecomesTheLastQualificationOfItsTabletAndPen() async throws {
    // Detects a qualification session on the tablet that the next bench header does not name, or a qualification
    // header that names a person or a consent document.
    let bench = try TestBench()
    defer { bench.remove() }
    let store = bench.store
    bench.enter()
    let qualification = try bench.opened(.qualification)
    #expect(store.open(qualification))
    store.begin(.qualification("qual-reference"))
    bench.stroke(x0: 6000, y: 15000, n: 40)
    store.done()
    store.begin(.qualification("qual-hold"))
    bench.point(.down, x: 9000, y: 15000)
    for _ in 0..<20 { bench.point(.tabletPoint, x: 9000, y: 15000) }
    bench.point(.up, x: 9000, y: 15000, pressure: 0)
    store.done()
    store.begin(.qualification("qual-line"))
    bench.stroke(x0: 6000, y: 18000, n: 40)
    store.done()
    await store.close()

    #expect(store.qualifications.last(asset: "WACOM-01", pen: .proPen3)?.sessionID == qualification.sessionID)
    #expect(store.qualifications.last(asset: "WACOM-01", pen: .unknown) == nil)
    #expect(try bench.opened(.bench).qualification?.sessionID == qualification.sessionID)

    let (folder, state, lines) = try export(bench, qualification)
    let header = lines[0]
    #expect(header["participant"] as? String == "QUAL" && header["input"] as? String == "tablet")
    let consent = header["consent"] as! [String: Any]
    #expect((consent["documents"] as! [Any]).isEmpty && consent["model"] as? String == "adult")
    #expect(consent["attestedBy"] as? [String: String] == ["code": "OP1", "role": "operator"])
    #expect(state.summary.trials.map(\.task) == ["qual-reference", "qual-hold", "qual-line"])
    try writeContractExport(folder, summary: state.summary)
}

@Test @MainActor func theLaunchClockCheckRefusesAnEventClockThatIsNotUptime() throws {
    // Detects a capture that opens a session before the clock check, or while the tablet's event clock differs from
    // the uptime of the app events, so the sample times and the app event times would not compare.
    let bench = try TestBench()
    defer { bench.remove() }
    let store = bench.store
    let opened = try bench.opened(.bench)
    #expect(!store.open(opened))
    #expect(store.notice == .clock(.waiting))

    store.receive(.proximity(TabletProximity(t: 100, device: 3, entering: true, pointingDevice: .pen)), uptime: 103.5)
    #expect(store.clock == .failed(difference: 3.5))
    store.receive(.proximity(TabletProximity(t: 104, device: 3, entering: false, pointingDevice: .pen)), uptime: 104.01)
    #expect(store.clock == .failed(difference: 3.5))
    #expect(!store.open(opened))
    #expect(store.live == nil)
    #expect(!FileManager.default.fileExists(atPath: store.storage.journalURL(opened.sessionID).path))

    let other = try TestBench()
    defer { other.remove() }
    other.store.receive(.proximity(TabletProximity(t: 100, device: 3, entering: true, pointingDevice: .pen)), uptime: 100.02)
    #expect(other.store.clock == .passed)
    #expect(other.store.open(try other.opened(.bench)))
}

@Test func theTabletTableFlipsYForATabletThatReportsYUp() {
    // Detects a mirrored journal: a tablet that reports y up written without the flip, or a y-down tablet flipped.
    func y(_ geometry: TabletGeometry, absoluteY: Int) -> (pen: Double?, hover: Double?) {
        var mapper = TabletMapper(geometry: geometry)
        func point(_ source: TabletPoint.Source) -> TabletEvent {
            .point(TabletPoint(source: source, t: 1, device: 3, absoluteX: 500, absoluteY: absoluteY, pressure: 0.3,
                               tiltX: 0, tiltY: 0, rotation: 0))
        }
        guard case .hover(let hover)? = mapper.reading(point(.moved)),
              case .pen(let pen)? = mapper.reading(point(.down)) else { return (nil, nil) }
        return (pen.y, hover.y)
    }
    #expect(!wacom.yDown)
    #expect(y(wacom, absoluteY: 8000) == (21600, 21600))
    #expect(y(wacom, absoluteY: 29600) == (0, 0))
    let down = TabletGeometry(model: "Y down", productCode: "TEST", linesPerMM: 200, activeAreaLines: wacom.activeAreaLines,
                              yDown: true, confirmed: true)
    #expect(y(down, absoluteY: 8000) == (8000, 8000))
}

@Test func aNonPenDeviceAndAPlainMouseAreIgnored() {
    // Detects eraser or puck reports, or plain mouse or trackpad events, written as pen samples or hover lines.
    func unread() -> NSEvent.EventSubtype {
        Issue.record("the subtype of a tablet event was read")
        return .tabletPoint
    }
    #expect(TabletEvent.kind(type: .leftMouseDown, subtype: { .mouseEvent }) == .other)
    #expect(TabletEvent.kind(type: .mouseMoved, subtype: { .touch }) == .other)
    #expect(TabletEvent.kind(type: .scrollWheel, subtype: unread) == .other)
    #expect(TabletEvent.kind(type: .mouseMoved, subtype: { .tabletPoint }) == .point(.moved))
    #expect(TabletEvent.kind(type: .leftMouseDragged, subtype: { .tabletProximity }) == .proximity)
    #expect(TabletEvent.kind(type: .tabletPoint, subtype: unread) == .point(.tabletPoint))

    var mapper = TabletMapper(geometry: wacom)
    func point(_ source: TabletPoint.Source, device: Int) -> TabletEvent {
        .point(TabletPoint(source: source, t: 1, device: device, absoluteX: 500, absoluteY: 500, pressure: 0.3,
                           tiltX: 0, tiltY: 0, rotation: 0))
    }
    #expect(mapper.reading(.proximity(TabletProximity(t: 1, device: 7, entering: true, pointingDevice: .eraser))) == nil)
    for source in [TabletPoint.Source.moved, .down, .dragged, .tabletPoint, .up] {
        #expect(mapper.reading(point(source, device: 7)) == nil, "\(source)")
    }
    #expect(mapper.reading(.other) == nil)
    guard case .pen(let pen)? = mapper.reading(point(.down, device: 3)) else {
        Issue.record("the pen gave no reading")
        return
    }
    #expect(pen.phase == .began && pen.key == TouchKey(3))
}

@Test @MainActor func aProximityLeaveCancelsAnOpenContact() async throws {
    // Detects a contact left open when the pen leaves the tablet's range, so it ends late, the hover of the pen's
    // return is lost, or a later report joins it.
    let bench = try TestBench()
    defer { bench.remove() }
    bench.enter()
    #expect(bench.store.open(try bench.opened(.bench)))
    bench.store.begin(loops)
    bench.point(.down, x: 1000, y: 1000)
    bench.point(.dragged, x: 1040, y: 1000)
    bench.leave()
    let left = bench.clock.now
    bench.point(.dragged, x: 1080, y: 1000)
    bench.enter()
    bench.hover(x: 1500, y: 1000)
    bench.stroke(x0: 2000, y: 1000)
    bench.store.done()
    let lines = try await bench.lines()
    #expect(lines.of("contactBegan").map { $0["contact"] as? Int } == [1, 2])
    #expect(lines.of("contactEnded").map { ($0["contact"] as? Int, $0["cancelled"] as? Bool) }
        .elementsEqual([(1, true), (2, false)]) { $0 == $1 })
    #expect(lines.of("contactEnded").first?["t"] as? Double == left)
    #expect(lines.of("sample").filter { $0["contact"] as? Int == 1 }.count == 2)
    #expect(lines.of("hover").count == 1)
    #expect(lines.of("trialEnded").first?["reason"] as? String == "operatorDone")
}

@Test @MainActor func aHoverLineHasNoHeightAndTheEventClock() async throws {
    // Detects a tablet hover line with an invented height, the callback clock, or a time other than the event time.
    let bench = try TestBench()
    defer { bench.remove() }
    bench.enter()
    #expect(bench.store.open(try bench.opened(.bench)))
    bench.store.begin(loops)
    bench.hover(x: 3000, y: 4000)
    let eventTime = bench.clock.now
    let hover = try #require(try await bench.lines().of("hover").first)
    #expect(hover["z"] == nil)
    #expect(hover["clock"] as? String == "event")
    #expect(hover["t"] as? Double == eventTime)
    #expect(hover["x"] as? Double == 3000 && hover["y"] as? Double == 25600)
}

@Test @MainActor func aTabletSampleKeepsOnlyThePlannedChannels() async throws {
    // Detects rotation written for a Pro Pen 3, tilt written for a pen of unknown type, or an estimate on a tablet
    // sample.
    for (pen, keys) in [(TabletPen.proPen3, ["force", "tiltX", "tiltY"]), (.unknown, ["force"])] {
        let bench = try TestBench()
        defer { bench.remove() }
        bench.enter()
        #expect(bench.store.open(try bench.opened(.bench, pen: pen)))
        bench.store.begin(loops)
        bench.stroke(x0: 1000, y: 1000)
        bench.store.done()
        let lines = try await bench.lines()
        let samples = lines.of("sample")
        #expect(samples.count == 5)
        for sample in samples {
            let channels = Set(sample.keys).intersection(["force", "altitude", "azimuth", "roll", "tiltX", "tiltY", "rotation"])
            #expect(channels == Set(keys), "\(pen)")
            #expect(sample["est"] as? Int == 0 && sample["pending"] == nil)
        }
        #expect(lines.of("contactBegan").first?["maxForce"] as? Double == 1)
    }
}

@Test @MainActor func aFaultRunNeverOffersOrWritesExclude() async throws {
    // Detects an exclude mark on a fault run, which would take the run out of the quality gate (bench protocol
    // sections 2.4 and 5.3).
    let bench = try TestBench()
    defer { bench.remove() }
    let store = bench.store
    bench.enter()
    #expect(store.open(try bench.opened(.bench)))
    store.begin(.bench(BenchRunPlan(program: "fault-dwell", run: 1)))
    #expect(store.markCodes == [.disturbance, .deviceProblem])
    #expect(!store.mark(.exclude))
    #expect(store.mark(.deviceProblem))
    store.done()
    store.begin(loops)
    #expect(store.markCodes.contains(.exclude))
    let marks = try await bench.lines().of("mark")
    #expect(marks.map { $0["code"] as? String } == ["deviceProblem"])
}

@Test @MainActor func aRunIDOfTheSessionCannotBeginAgain() async throws {
    // Detects a run screen that begins a second run with the run id of an earlier run, for example when the run
    // index stays the same from one run to the next, so two runs share one camera file name.
    let bench = try TestBench()
    defer { bench.remove() }
    let store = bench.store
    bench.enter()
    #expect(store.open(try bench.opened(.bench)))
    #expect(store.refusal(loops) == nil)
    store.begin(loops)
    bench.stroke(x0: 1000, y: 1000)
    store.done()
    #expect(store.refusal(loops) == .repeatedRun("loops-l-v20", 1))
    #expect(store.runID(loops) == "W-M3-loops-l-v20-r1")
    store.begin(loops)
    #expect(store.state?.phase == .ready && store.state?.session.trials.count == 1)
    let rerun: RunChoice = .bench(BenchRunPlan(program: "loops-l-v20", run: 6))
    #expect(store.refusal(rerun) == nil)
    store.begin(rerun)
    store.done()
    #expect(try await bench.lines().of("trialStarted").map { ($0["bench"] as? [String: Any])?["clip"] as? String }
        == ["W-M3-loops-l-v20-r1", "W-M3-loops-l-v20-r6"])
}

@Test @MainActor func aResumedBenchSessionOfAnotherProtocolOffersNoRun() async throws {
    // Detects a build that continues a bench session of another bench protocol, so one journal mixes the runs of two
    // registrations.
    let first = try TestBench()
    defer { first.remove() }
    first.enter()
    var opened = try first.opened(.bench)
    guard case .bench(var facts) = opened.subject else { throw CocoaError(.featureUnsupported) }
    facts.protocol = BenchProtocolRef(version: "1.0", sha256: facts.protocol.sha256)
    opened.subject = .bench(facts)
    #expect(first.store.open(opened))
    try await first.requireLive().writer.close()

    let second = try TestBench(root: first.root)
    second.clock.now = first.clock.now + 60
    second.enter()
    second.store.refresh()
    second.store.resume(opened.sessionID)
    #expect(second.store.live != nil)
    #expect(second.store.refusal(loops) == .otherProtocol)
    second.store.begin(loops)
    #expect(second.store.state?.session.trials.isEmpty == true)
    await second.store.close()
    #expect(second.store.live == nil)
}

@Test @MainActor func focusLossEndsAnOpenTrialAsBackgrounded() async throws {
    // Detects a trial that stays open after the window loses the keyboard focus, so a run could go on without the
    // capture (bench protocol section 2.4).
    let bench = try TestBench()
    defer { bench.remove() }
    let store = bench.store
    bench.enter()
    #expect(store.open(try bench.opened(.bench)))
    store.focusLost()
    store.begin(loops)
    bench.point(.down, x: 1000, y: 1000)
    bench.point(.dragged, x: 1040, y: 1000)
    store.focusLost()
    #expect(store.state?.phase == .ready)
    #expect(store.state?.summary.trials.map(\.outcome) == [.interrupted])
    let lines = try await bench.lines()
    #expect(lines.of("trialEnded").map { $0["reason"] as? String } == ["backgrounded"])
    #expect(lines.of("contactEnded").map { $0["cancelled"] as? Bool } == [true])
}

@Test @MainActor func anInterruptedBenchSessionResumesWithItsOriginAndItsNextContact() async throws {
    // Detects a relaunch that loses the stored origin, reuses a contact number, or cannot continue the block.
    let first = try TestBench()
    defer { first.remove() }
    first.enter()
    let opened = try first.opened(.bench)
    #expect(first.store.open(opened))
    first.hover(x: 7000, y: 20000)
    first.store.setOrigin()
    first.store.begin(loops)
    first.stroke(x0: 7000, y: 20000)
    first.store.done()
    first.store.begin(.bench(BenchRunPlan(program: "loops-l-v20", run: 2)))
    first.point(.down, x: 7000, y: 20000)
    try await first.requireLive().writer.close()

    let second = try TestBench(root: first.root)
    second.clock.now = first.clock.now + 60
    second.enter()
    second.store.refresh()
    #expect(second.store.listing.map(\.status) == [.interrupted])
    second.store.resume(opened.sessionID)
    let live = try second.requireLive()
    #expect(live.origin == Point(x: 7000, y: 9600))
    #expect(live.labels == ["W-M3-loops-l-v20-r1", "W-M3-loops-l-v20-r2"])
    second.store.begin(.bench(BenchRunPlan(program: "loops-l-v20", run: 3)))
    second.stroke(x0: 7000, y: 20000)
    second.store.done()
    #expect(try await second.lines().of("contactBegan").map { $0["contact"] as? Int } == [1, 2, 3])
    await second.store.close()
    let (_, state, _) = try export(second, opened)
    #expect(state.summary.trials.map(\.outcome) == [.completed, .interrupted, .completed])
}
