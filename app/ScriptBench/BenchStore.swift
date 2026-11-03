import AppKit
import Foundation
import Observation
import os
import ScriptCore

/// What the operator runs next: a bench program at a run index, or a qualification task.
enum RunChoice: Hashable, Sendable {
    case bench(BenchRunPlan)
    case qualification(TaskID)
}

/// One open session: its rules, its writer, and the facts of its runs.
struct LiveSession {
    let opened: SessionOpened
    let rules: SessionRules
    let geometry: TabletGeometry
    let url: URL
    let writer: JournalWriter
    /// The stimulus origin of the next trials, in tablet lines. Set origin changes it.
    var origin: Point
    /// A label for each trial, in index order: the run id of a bench run, the task of a qualification trial.
    var labels: [String]
    /// The program of the open bench run.
    var openProgram: BenchProgram?
    /// True after a write failed. The session takes no more lines.
    var stopped = false
}

/// The part of the session that each tablet report changes. It is not observed, so a report costs no copy.
private struct Core {
    var state: SessionState
    var recorder: InkRecorder
    var mapper: TabletMapper
}

/// A row on Home.
struct Listed: Identifiable, Hashable {
    enum Status: Int, Hashable, Comparable {
        case interrupted, closed, exported, damaged
        static func < (a: Status, b: Status) -> Bool { a.rawValue < b.rawValue }
    }

    let id: String
    let status: Status
    let title: String
    let sessionID: SessionID?
    let openedAt: String?
}

/// An operator notice.
enum Notice: Hashable, Identifiable {
    case clock(ClockCheck)
    case lowStorage
    case storageFailure
    case exportFailed
    case failed(String)

    var id: Self { self }
}

/// Owns storage, the writer, the rules, and the folded state. Every operator action goes through `decide`, and
/// every tablet report goes through `TabletMapper` and `InkRecorder`.
@MainActor @Observable
final class BenchStore {
    let contract: BenchContract
    let storage: Storage
    let mac: MacFacts
    @ObservationIgnored let qualifications: QualificationRegistry
    @ObservationIgnored let ink = InkTrace()
    @ObservationIgnored private let uptime: @MainActor () -> Double
    @ObservationIgnored private let freeSpace: @Sendable () -> Int64?
    /// A session does not start with less free storage than this, because a full disk stops it part way.
    static let minimumFreeBytes: Int64 = 500_000_000
    @ObservationIgnored private let log = Logger(subsystem: "com.dvgyl.script.bench", category: "session")

    private(set) var clock: ClockCheck
    private(set) var live: LiveSession?
    @ObservationIgnored private var core: Core?
    /// Bumped after each decided command, so views that read the state update.
    private(set) var revision = 0
    /// True while the pen is in range above the tablet, so Set origin has a position.
    private(set) var penInRange = false
    /// True while the window is in full screen, as the window reports it. Begin needs it: a pen position outside the
    /// window would reach another app, and the run would end as backgrounded.
    var fullScreen = false
    /// Keeps the Mac and its display awake while a session is open (bench protocol section 2.2).
    @ObservationIgnored private var activity: (any NSObjectProtocol)?
    private(set) var listing: [Listed] = []
    @ObservationIgnored private var reconciled: [SessionID: SessionState] = [:]
    var notice: Notice?

    init(contract: BenchContract, storage: Storage, mac: MacFacts, qualifications: QualificationRegistry,
         clock: ClockCheck = .waiting, uptime: @escaping @MainActor () -> Double = { Clock.uptime },
         freeSpace: (@Sendable () -> Int64?)? = nil) {
        self.contract = contract
        self.storage = storage
        self.mac = mac
        self.qualifications = qualifications
        self.clock = clock
        self.uptime = uptime
        let volume = storage.sessions.deletingLastPathComponent()
        self.freeSpace = freeSpace ?? {
            try? volume.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
                .volumeAvailableCapacityForImportantUsage
        }
    }

    /// The folded state of the open session.
    var state: SessionState? {
        _ = revision
        return core?.state
    }

    // MARK: Tablet

    /// Every tablet event of the app. The first one runs the clock check. During a trial, the readings become lines.
    func receive(_ event: TabletEvent, uptime: Double) {
        guard let t = event.t else { return }
        // Only the first tablet event decides, and an unchanged result writes nothing, so no view redraws for it.
        if clock == .waiting { clock = ClockCheck(eventTime: t, uptime: uptime) }
        guard core != nil, live?.stopped == false else { return }
        let reading = core!.mapper.reading(event)
        let hover = core!.mapper.hover
        if penInRange != (hover != nil) { penInRange = hover != nil }
        ink.move(hover)
        guard let reading, isRecordingInk else { return }
        switch reading {
        case .pen(let r): record(core!.recorder.pen([r]))
        case .hover(let r): record(core!.recorder.hover(r).map { [$0] } ?? [])
        case .left(let t): record(core!.recorder.cancelAll(t: t))
        }
    }

    /// Ink is recorded from Begin to the end of the trial.
    var isRecordingInk: Bool {
        guard let state = core?.state, case .drawing(let open) = state.phase else { return false }
        return state.session.trials[open.index.rawValue].began
    }

    // MARK: Home

    /// Reconciles every journal and lists the sessions. It never runs while a journal is open for writing.
    func refresh() {
        guard live == nil else { return }
        reconciled = [:]
        var rows: [Listed] = []
        do {
            let launch = Storage.Launch(boot: Clock.boot, uptime: uptime(), at: Clock.now)
            for result in try storage.reconcile(launch, instruments: contract.instruments, policy: .demo) {
                switch result {
                case .interrupted(let state), .closed(let state):
                    reconciled[state.session.opened.sessionID] = state
                    rows.append(row(state))
                case .publishFailed(let file, _):
                    if let data = try? Data(contentsOf: storage.sessions.appendingPathComponent(file)),
                       let state = try? Journal.read(data) {
                        reconciled[state.session.opened.sessionID] = state
                        rows.append(row(state))
                    }
                case .unreadable(let file, _):
                    rows.append(Listed(id: file, status: .damaged, title: file, sessionID: nil, openedAt: nil))
                case .published, .damaged:
                    break
                }
            }
        } catch {
            log.error("reconcile failed: \(String(describing: error), privacy: .public)")
            notice = .failed(String(localized: "The sessions could not be read."))
        }
        rows += folders(storage.exports).map {
            Listed(id: $0.lastPathComponent, status: .exported, title: $0.lastPathComponent, sessionID: nil, openedAt: nil)
        }
        rows += folders(storage.damaged).filter { $0.pathExtension == "jsonl" }.map {
            Listed(id: $0.lastPathComponent, status: .damaged, title: $0.lastPathComponent, sessionID: nil, openedAt: nil)
        }
        listing = rows.sorted { a, b in
            a.status != b.status ? a.status < b.status : (a.openedAt ?? a.title) > (b.openedAt ?? b.title)
        }
    }

    private func row(_ state: SessionState) -> Listed {
        let opened = state.session.opened
        let title = switch opened.subject {
        case .bench(let facts): String(localized: "Bench \(facts.role.rawValue)-\(facts.mounting.rawValue)")
        case .participant: String(localized: "Qualification")
        }
        return Listed(id: opened.sessionID.description, status: state.session.isClosed ? .closed : .interrupted,
                      title: title, sessionID: opened.sessionID, openedAt: opened.openedAt.text)
    }

    private func folders(_ url: URL) -> [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix("._") }
    }

    func showExports() {
        try? storage.createDirectories()
        NSWorkspace.shared.open(storage.exports)
    }

    // MARK: Open and resume

    /// Writes line 0 and opens the session. It refuses until the clock check has passed.
    @discardableResult
    func open(_ opened: SessionOpened) -> Bool {
        guard live == nil, canOpen, let geometry = geometry(opened), let rules = rules(opened) else { return false }
        let entry = Entry(seq: 0, event: .sessionOpened(opened))
        let state: SessionState
        do throws(FoldError) {
            state = try SessionState(opening: entry)
        } catch {
            log.fault("line 0 does not fold: \(String(describing: error.violation), privacy: .public)")
            notice = .failed(String(localized: "The session could not start."))
            return false
        }
        let url = storage.journalURL(opened.sessionID)
        do {
            try storage.createDirectories()
            let writer = try JournalWriter(appendingTo: url, onFailure: failureHandler(url))
            try writer.append([entry])
            core = Core(state: state, recorder: InkRecorder(plan: opened.channels), mapper: TabletMapper(geometry: geometry))
            live = LiveSession(opened: opened, rules: rules, geometry: geometry, url: url, writer: writer,
                               origin: Point(x: 0, y: 0), labels: [])
            started()
            return true
        } catch {
            log.error("open failed: \(String(describing: error), privacy: .public)")
            notice = .storageFailure
            return false
        }
    }

    /// Opens an interrupted journal again with a new writer. The origin of its last trial stays the origin.
    func resume(_ id: SessionID) {
        guard live == nil, let state = reconciled[id], !state.session.isClosed, canOpen else { return }
        let opened = state.session.opened
        guard let geometry = geometry(opened), let rules = rules(opened) else { return }
        let url = storage.journalURL(id)
        do {
            var labels: [String] = []
            var origin = Point(x: 0, y: 0)
            for line in try Data(contentsOf: url).split(separator: 0x0A) {
                guard case .trialStarted(let trial)? = try? LineCodec.decode(Data(line)).event else { continue }
                labels.append(label(trial))
                origin = trial.stimulusOrigin
            }
            let writer = try JournalWriter(appendingTo: url, onFailure: failureHandler(url))
            core = Core(state: state, recorder: InkRecorder(resuming: state.session), mapper: TabletMapper(geometry: geometry))
            live = LiveSession(opened: opened, rules: rules, geometry: geometry, url: url, writer: writer, origin: origin,
                               labels: labels)
            reconciled[id] = nil
            started()
        } catch {
            log.error("resume failed: \(String(describing: error), privacy: .public)")
            notice = .storageFailure
        }
    }

    private func started() {
        ink.clear()
        penInRange = false
        if let live, activity == nil {
            activity = ProcessInfo.processInfo.beginActivity(
                options: [.idleSystemSleepDisabled, .idleDisplaySleepDisabled],
                reason: "SCRIPT Bench session \(live.opened.sessionID)"
            )
        }
        revision += 1
    }

    /// False, with the notice for the operator, until the clock check has passed or while free storage is low.
    private var canOpen: Bool {
        guard clock == .passed else {
            notice = .clock(clock)
            return false
        }
        if let free = freeSpace(), free < Self.minimumFreeBytes {
            notice = .lowStorage
            return false
        }
        return true
    }

    private func geometry(_ opened: SessionOpened) -> TabletGeometry? {
        guard let model = opened.tablet?.model, let geometry = TabletTable.geometry(model: model) else {
            notice = .failed(String(localized: "The tablet of this session is not in the tablet table."))
            return nil
        }
        return geometry
    }

    /// The rules of a bench session hold the programs. A qualification session runs under the demo configuration.
    private func rules(_ opened: SessionOpened) -> SessionRules? {
        switch opened.subject {
        case .bench:
            SessionRules(battery: contract.battery, instruments: contract.instruments, benchPrograms: contract.programs)
        case .participant(let facts):
            SessionRules(battery: contract.battery, instruments: contract.instruments, configuration: contract.demo,
                         jurisdiction: facts.jurisdiction, locale: facts.locale)
        }
    }

    private func failureHandler(_ url: URL) -> @Sendable (JournalWriter.WriteError) -> Void {
        { [weak self] error in
            Task { @MainActor in self?.stop(url, error) }
        }
    }

    /// Stops the session at once and shows the operator the storage alert. A relaunch adopts the journal.
    private func stop(_ url: URL, _ error: any Error) {
        guard live?.url == url, live?.stopped == false else { return }
        log.error("journal write failed: \(String(describing: error), privacy: .public)")
        live?.stopped = true
        notice = .storageFailure
        revision += 1
    }

    // MARK: Runs

    /// Records the events of one command. False when `decide` refuses it or the session has stopped.
    @discardableResult
    private func perform(_ command: Command) -> Bool {
        guard let live, !live.stopped, let state = core?.state else { return false }
        switch state.decide(command, rules: live.rules) {
        case .success(let events):
            record(events)
            revision += 1
            return self.live?.stopped == false
        case .failure(let rejection):
            log.error("rejected: \(String(describing: rejection), privacy: .public)")
            return false
        }
    }

    /// Applies and appends events. A failed append stops the session.
    private func record(_ events: [Event]) {
        guard !events.isEmpty, let live, !live.stopped, core != nil else { return }
        let entries: [Entry]
        do throws(FoldError) {
            entries = try core!.state.record(events)
        } catch {
            log.fault("an event broke a fold rule: \(String(describing: error.violation), privacy: .public)")
            return
        }
        for entry in entries { note(entry.event) }
        do {
            try live.writer.append(entries)
        } catch {
            stop(live.url, error)
        }
    }

    /// Keeps the labels, the open program, and the ink in step with the recorded lines.
    private func note(_ event: Event) {
        switch event {
        case .trialStarted(let trial):
            live?.labels.append(label(trial))
            if case .bench(let run) = trial.subject { live?.openProgram = contract.programs.program(run.program) }
            ink.clear()
        case .trialEnded:
            live?.openProgram = nil
        case .contactBegan:
            ink.beginStroke()
        case .sample(let sample):
            ink.add(Point(x: sample.x, y: sample.y))
        default:
            break
        }
    }

    private func label(_ trial: TrialStarted) -> String {
        switch trial.subject {
        case .bench(let run): run.clip?.rawValue ?? run.program.rawValue
        case .participant: trial.trial.task.rawValue
        }
    }

    /// The plan of `choice` in the tablet's active area at the stored origin.
    func plan(_ choice: RunChoice) -> TrialPlan? {
        guard let live, let state = core?.state else { return nil }
        switch choice {
        case .bench(let run):
            guard let program = contract.programs.program(run.program) else { return nil }
            return TrialPlan(task: program.task, practice: false, position: state.session.trials.count + 1,
                             subject: .bench(run), view: live.geometry.activeAreaLines, stimulusOrigin: live.origin)
        case .qualification(let task):
            guard case .participant(let facts) = live.opened.subject,
                  let position = BenchContract.qualificationTasks.firstIndex(of: task) else { return nil }
            let version = contract.demo.locales[facts.locale.rawValue]?.instructionVersion ?? contract.battery.version
            let trial = ParticipantTrialFacts(hand: .unknown,
                                              instruction: InstructionRef(id: task.rawValue, version: version,
                                                                          locale: facts.locale))
            return TrialPlan(task: task, practice: false, position: position + 1, subject: .participant(trial),
                             view: live.geometry.activeAreaLines, stimulusOrigin: live.origin)
        }
    }

    /// The run id that `decide` writes as the clip of a bench run.
    func runID(_ choice: RunChoice) -> String? {
        guard case .bench(let run) = choice, case .bench(let facts)? = live?.opened.subject else { return nil }
        return ClipName(runOf: run.program, run: run.run, in: facts).rawValue
    }

    /// Why `decide` refuses the bench run of `choice` in this session, or nil when Begin can start it.
    func refusal(_ choice: RunChoice) -> Rejection? {
        guard case .bench(let run) = choice, let live, let state,
              case .failure(let rejection) = state.benchRun(run, rules: live.rules) else { return nil }
        return rejection
    }

    /// Stores the pen's hover position as the stimulus origin of the next trials.
    func setOrigin() {
        guard let hover = core?.mapper.hover else { return }
        live?.origin = hover
        revision += 1
    }

    /// Starts the trial and begins it, because the operator taps Begin before the stylus lowers.
    func begin(_ choice: RunChoice) {
        guard fullScreen, let plan = plan(choice), perform(.startTrial(plan, t: uptime())) else { return }
        perform(.beginTrial(t: uptime()))
    }

    func done() { endTrial(.operatorDone) }

    /// The window lost the keyboard focus. An open trial ends as backgrounded (bench protocol section 2.4), and every
    /// line reaches storage.
    func focusLost() {
        guard let writer = live?.writer else { return }
        endTrial(.backgrounded)
        Task { try? await writer.sync() }
    }

    /// Ends the open trial. A contact still down ends as cancelled first, because a trial ends with no contact down.
    private func endTrial(_ reason: TrialEndReason) {
        guard case .drawing = core?.state.phase else { return }
        let t = uptime()
        record(core!.recorder.cancelAll(t: t))
        perform(.endTrial(reason: reason, t: t))
    }

    /// The marks the operator can add to the open trial. A qualification trial has no program and takes every mark.
    var markCodes: [MarkCode] {
        guard case .drawing = state?.phase else { return [] }
        return live?.openProgram?.markCodes ?? [.disturbance, .deviceProblem, .exclude]
    }

    @discardableResult
    func mark(_ code: MarkCode) -> Bool {
        guard markCodes.contains(code), case .drawing(let open) = core?.state.phase else { return false }
        return perform(.mark(trial: open.index, code: code, t: uptime()))
    }

    // MARK: Close and export

    /// Closes the session and publishes it. A closed qualification session becomes the last qualification of its
    /// tablet and pen.
    func close() async {
        guard let live, perform(.close(t: uptime())) else { return }
        if live.opened.purpose == .qualification {
            do {
                try qualifications.record(live.opened)
            } catch {
                notice = .failed(String(localized: "The qualification could not be recorded."))
            }
        }
        await leave()
        await export(live.opened.sessionID)
    }

    /// Closes the writer and returns to Home. A session that stopped after a write failure stays for the relaunch.
    func leave() async {
        guard let live else { return }
        try? await live.writer.close()
        if let activity { ProcessInfo.processInfo.endActivity(activity) }
        activity = nil
        self.live = nil
        core = nil
        ink.clear()
        revision += 1
        refresh()
    }

    /// Publishes a closed session as a plaintext export with `demo: true`.
    func export(_ id: SessionID) async {
        guard live == nil else { return }
        let storage = storage
        do {
            _ = try await Task.detached { try storage.publish(id, policy: .demo) }.value
        } catch {
            log.error("export failed: \(String(describing: error), privacy: .public)")
            notice = .exportFailed
        }
        refresh()
    }

    /// The app quits. An open trial ends as backgrounded, and the writer closes, so a relaunch adopts the journal.
    func quit() async {
        endTrial(.backgrounded)
        try? await live?.writer.close()
    }
}

extension Session {
    var isClosed: Bool { counts[.sessionClosed] > 0 }
}
