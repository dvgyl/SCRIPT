import Foundation
import Observation
import os
import ScriptCore
import UIKit

/// Where journal lines go. `JournalWriter` is the app's sink; a test can stand in a sink that fails.
protocol JournalSink: Sendable {
    func append(_ entries: [Entry]) throws
    func sync() async throws
    func close() async throws
}

extension JournalWriter: JournalSink {}

typealias SinkFactory = @Sendable (URL, @escaping @Sendable (JournalWriter.WriteError) -> Void) throws -> any JournalSink

/// What the session shows now. `working` takes its screen from the plan and the fold.
enum Stage: Hashable, Sendable {
    /// The participant taps Start.
    case ready
    /// The participant names the hand they write with, when a resumed session does not record it.
    case hand
    /// The monitoring disclosure before the first block of a self-administered session with a safety item.
    case disclosure
    case working
    /// The crisis list at once after a flagged item in a self-administered session.
    case flagCrisisList
    /// "Please give the iPad back to the researcher."
    case handBack
    /// The operator took over in the middle of the session.
    case paused
    /// The operator review after the hand-back: safety alerts, action codes, and trials.
    case review
    case closingCrisisList
    /// The session is closed. The participant sees the thank-you screen.
    case finished
    /// The qualification session is closed. The operator sees the sample timing.
    case qualified
    /// A write failed. The session stopped, and the operator sees the storage alert.
    case stopped
}

/// One open session: its frozen configuration and rules, its plan, its writer, and where the flow is.
struct LiveSession {
    let opened: SessionOpened
    let configuration: StudyConfiguration
    let rules: SessionRules
    var plan: SessionPlan
    let url: URL
    let sink: any JournalSink
    var stage: Stage
    var dominant: Hand?
    var disclosureShown = false
    /// The stage to go back to when the operator returns the iPad.
    var participantStage: Stage = .working
    /// The view and stimulus origin of the open trial, as `trialStarted` recorded them.
    var trialGeometry: (index: TrialIndex, view: ViewSize, origin: Point)?
    /// True while the operator is on Home and the session stays open.
    var away = false
    /// The close of the writer. Reconcile waits for it, because it must never read a journal that is open.
    var closing: Task<Void, Never>?
}

/// The part of the session that ink changes hundreds of times a second. It is not observed, so a sample costs no
/// copy; the store bumps `revision` after each decided command instead.
private struct Core {
    var state: SessionState
    var recorder: InkRecorder
    var digest: JournalDigest
}

/// A row on Home.
struct Listed: Identifiable, Hashable {
    enum Status: Int, Hashable, Comparable {
        case open, interrupted, closed, exported, damaged
        static func < (a: Status, b: Status) -> Bool { a.rawValue < b.rawValue }
    }

    let id: String
    let status: Status
    let title: String
    let sessionID: SessionID?
    let purpose: Purpose?
    let administration: Administration?
    let openedAt: String?
    let encrypted: Bool?
}

/// An operator notice. It never names a participant or holds a response.
enum Notice: Hashable, Identifiable {
    case storageFailure
    case lowStorage
    case configurationMissing
    case sessionOpen
    case exportFailed
    case failed(String)

    var id: Self { self }
}

/// The result of a publish, for the export screen.
struct ExportResult: Hashable, Identifiable {
    let url: URL
    let encrypted: Bool
    var name: String { url.lastPathComponent }
    var id: String { name }
}

/// Owns storage, the writer, the frozen rules, and the folded state. Every user action goes through `decide`; the
/// store appends the events, applies them, and moves the session to its next stage.
@MainActor @Observable
final class SessionStore {
    let contract: BundledContract
    let storage: Storage
    let device: DeviceFacts
    @ObservationIgnored let configurations: ConfigurationLibrary
    @ObservationIgnored let qualifications: QualificationRegistry
    @ObservationIgnored private let makeSink: SinkFactory
    /// The free bytes on the volume of the journals, or nil when the system does not say.
    @ObservationIgnored private let freeSpace: @Sendable () -> Int64?
    /// A session does not start with less free storage than this, because a full disk stops it part way.
    static let minimumFreeBytes: Int64 = 500_000_000
    @ObservationIgnored private let log = Logger(subsystem: "com.dvgyl.script.capture", category: "session")

    private(set) var live: LiveSession?
    @ObservationIgnored private var core: Core?
    /// Bumped after each decided command, so views that read the state update.
    private(set) var revision = 0
    private(set) var listing: [Listed] = []
    private(set) var activeConfiguration: StudyConfiguration
    var notice: Notice?
    /// The states of the journals that the last reconcile found, for resume and export.
    @ObservationIgnored private var reconciled: [SessionID: SessionState] = [:]
    /// The rebuild after a write failure, so a test can wait for it.
    @ObservationIgnored private(set) var rebuild: Task<Void, Never>?

    init(contract: BundledContract, storage: Storage, device: DeviceFacts, configurations: ConfigurationLibrary,
         qualifications: QualificationRegistry = QualificationRegistry(),
         makeSink: @escaping SinkFactory = { try JournalWriter(appendingTo: $0, onFailure: $1) },
         freeSpace: (@Sendable () -> Int64?)? = nil) {
        self.contract = contract
        self.storage = storage
        self.device = device
        self.configurations = configurations
        self.qualifications = qualifications
        self.makeSink = makeSink
        let volume = storage.sessions.deletingLastPathComponent()
        self.freeSpace = freeSpace ?? {
            try? volume.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
                .volumeAvailableCapacityForImportantUsage
        }
        activeConfiguration = configurations.active
    }

    /// The folded state of the open session.
    var state: SessionState? {
        _ = revision
        return core?.state
    }

    var digest: JournalDigest? {
        _ = revision
        return core?.digest
    }

    // MARK: Launch and Home

    /// Reconciles every journal and lists the sessions. A self-administered session that was interrupted opens
    /// again for the participant, who has no operator to resume it.
    func launch(at launch: Storage.Launch = Clock.launch) {
        refresh(at: launch)
        let waiting = listing.filter { $0.status == .interrupted && $0.administration == .selfAdministered }
        if let latest = waiting.max(by: { ($0.openedAt ?? "") < ($1.openedAt ?? "") }), let id = latest.sessionID {
            resume(id)
        }
    }

    /// Lists the sessions again. Reconcile never runs while a journal is open for writing.
    func refresh(at launch: Storage.Launch = Clock.launch) {
        var rows: [Listed] = []
        if let live, let state = core?.state {
            rows.append(row(state, status: .open))
            rows += reconciled.values.filter { $0.session.opened.sessionID != live.opened.sessionID }
                .map { row($0, status: $0.session.isClosed ? .closed : .interrupted) }
        } else {
            reconciled = [:]
            do {
                let results = try storage.reconcile(launch) { [configurations] opened in
                    guard let config = configurations.configuration(sha: opened.study.configSHA256.hex) else {
                        throw ConfigurationLibrary.Refusal.unreadable
                    }
                    return try ExportPolicy(configuration: config)
                }
                for result in results {
                    switch result {
                    case .interrupted(let state), .closed(let state):
                        reconciled[state.session.opened.sessionID] = state
                        rows.append(row(state, status: state.session.isClosed ? .closed : .interrupted))
                    case .publishFailed(let file, _):
                        if let data = try? Data(contentsOf: storage.sessions.appendingPathComponent(file)),
                           let state = try? Journal.read(data) {
                            reconciled[state.session.opened.sessionID] = state
                            rows.append(row(state, status: .closed))
                        }
                    case .unreadable(let file, _):
                        rows.append(Listed(id: file, status: .damaged, title: file, sessionID: nil, purpose: nil,
                                           administration: nil, openedAt: nil, encrypted: nil))
                    case .published, .damaged:
                        break
                    }
                }
            } catch {
                log.error("reconcile failed: \(String(describing: error), privacy: .public)")
                notice = .failed(String(localized: "The sessions could not be read."))
            }
        }
        rows += exportedRows() + damagedRows()
        listing = rows.sorted { a, b in
            a.status != b.status ? a.status < b.status : (a.openedAt ?? a.title) > (b.openedAt ?? b.title)
        }
    }

    private func row(_ state: SessionState, status: Listed.Status) -> Listed {
        let opened = state.session.opened
        return Listed(id: opened.sessionID.description, status: status, title: opened.participant.rawValue,
                      sessionID: opened.sessionID, purpose: opened.purpose, administration: opened.administration,
                      openedAt: opened.openedAt.text, encrypted: nil)
    }

    private func entries(in url: URL) -> [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix("._") }
    }

    private func exportedRows() -> [Listed] {
        entries(in: storage.exports).map { folder in
            let manifest = (try? Data(contentsOf: folder.appendingPathComponent(Storage.Export.manifest)))
                .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            let id = (manifest?["sessionID"] as? String).flatMap(UUID.init(uuidString:)).map(SessionID.init)
            return Listed(id: folder.lastPathComponent, status: .exported, title: folder.lastPathComponent,
                          sessionID: id, purpose: nil, administration: nil, openedAt: nil,
                          encrypted: manifest?["encryption"] != nil)
        }
    }

    private func damagedRows() -> [Listed] {
        entries(in: storage.damaged).filter { $0.pathExtension == "jsonl" }.map { file in
            Listed(id: file.lastPathComponent, status: .damaged, title: file.lastPathComponent, sessionID: nil,
                   purpose: nil, administration: nil, openedAt: nil, encrypted: nil)
        }
    }

    func importConfiguration(_ data: Data) -> Result<StudyConfiguration, ConfigurationLibrary.Refusal> {
        let result = configurations.importConfiguration(data, battery: contract.battery, instruments: contract.instruments)
        activeConfiguration = configurations.active
        return result
    }

    func useDemoConfiguration() {
        configurations.useDemo()
        activeConfiguration = configurations.active
    }

    // MARK: Open and resume

    /// Writes line 0 and opens the session.
    func open(_ opened: SessionOpened, configuration: StudyConfiguration, dominant: Hand) {
        guard live == nil else {
            notice = .sessionOpen
            return
        }
        guard hasRoom() else { return }
        let entry = Entry(seq: 0, event: .sessionOpened(opened))
        let state: SessionState
        do throws(FoldError) {
            state = try SessionState(opening: entry)
        } catch {
            log.fault("line 0 does not fold: \(String(describing: error.violation), privacy: .public)")
            notice = .failed(String(localized: "The session could not start."))
            return
        }
        let url = storage.journalURL(opened.sessionID)
        do {
            try storage.createDirectories()
            let sink = try makeSink(url, failureHandler(url))
            try sink.append([entry])
            core = Core(state: state, recorder: InkRecorder(plan: opened.channels), digest: JournalDigest([entry]))
            live = LiveSession(
                opened: opened, configuration: configuration, rules: rules(opened, configuration),
                plan: SessionPlan(configuration: configuration, opened: opened, dominant: dominant),
                url: url, sink: sink, stage: opened.purpose == .qualification ? .working : .ready, dominant: dominant
            )
            revision += 1
        } catch {
            log.error("open failed: \(String(describing: error), privacy: .public)")
            notice = .storageFailure
        }
    }

    /// False, with the notice for the operator, when the free storage is below the minimum.
    private func hasRoom() -> Bool {
        guard let free = freeSpace(), free < Self.minimumFreeBytes else { return true }
        notice = .lowStorage
        return false
    }

    private func rules(_ opened: SessionOpened, _ configuration: StudyConfiguration) -> SessionRules {
        SessionRules(battery: contract.battery, instruments: contract.instruments, configuration: configuration,
                     jurisdiction: opened.jurisdiction, locale: opened.locale)
    }

    /// Opens an interrupted journal again with a new writer. The configuration that the session recorded must be on
    /// this device, because a configuration is frozen for the life of a session.
    func resume(_ id: SessionID) {
        guard live == nil, let state = reconciled[id], !state.session.isClosed, hasRoom() else { return }
        let opened = state.session.opened
        guard let configuration = configurations.configuration(sha: opened.study.configSHA256.hex) else {
            notice = .configurationMissing
            return
        }
        let url = storage.journalURL(id)
        do {
            let digest = JournalDigest(Self.entries(try Data(contentsOf: url)))
            let dominant = Self.dominantHand(configuration: configuration, digest: digest)
            let sink = try makeSink(url, failureHandler(url))
            let plan = SessionPlan(configuration: configuration, opened: opened, dominant: dominant ?? .unknown)
            var stage = Stage.ready
            if opened.purpose == .qualification {
                stage = .working
            } else if opened.administration == .supervised, plan.next(state.session) == .done {
                stage = .review
            }
            core = Core(state: state, recorder: InkRecorder(resuming: state.session), digest: digest)
            live = LiveSession(opened: opened, configuration: configuration, rules: rules(opened, configuration),
                               plan: plan, url: url, sink: sink, stage: stage, dominant: dominant)
            reconciled[id] = nil
            revision += 1
            refresh()
            if stage == .review { showOperatorAlert() }
            if stage == .working { advance() }
        } catch {
            log.error("resume failed: \(String(describing: error), privacy: .public)")
            notice = .storageFailure
        }
    }

    private static func entries(_ data: Data) -> [Entry] {
        data.split(separator: 0x0A).compactMap { try? LineCodec.decode(Data($0)) }
    }

    /// The hand of the first trial of a task that the configuration runs with the dominant hand.
    private static func dominantHand(configuration: StudyConfiguration, digest: JournalDigest) -> Hand? {
        let tasks = Set(configuration.tasks.filter { $0.hands == .dominant || $0.hands == .both }.map(\.task))
        return digest.trials.first { tasks.contains($0.task) && $0.hand != .unknown }?.hand
    }

    private func failureHandler(_ url: URL) -> @Sendable (JournalWriter.WriteError) -> Void {
        { [weak self] error in
            Task { @MainActor in self?.stop(url, error) }
        }
    }

    // MARK: Commands

    /// Decides one command against the frozen rules and records its events. False when `decide` refuses it or the
    /// session has stopped.
    @discardableResult
    func perform(_ command: Command) -> Bool {
        guard let live, live.stage != .stopped, let state = core?.state else { return false }
        switch state.decide(command, rules: live.rules) {
        case .success(let events):
            record(events)
            revision += 1
            return self.live?.stage != .stopped
        case .failure(let rejection):
            log.error("rejected: \(String(describing: rejection), privacy: .public)")
            return false
        }
    }

    /// Applies and appends ink or decided events. A failed append stops the session at once.
    private func record(_ events: [Event]) {
        guard !events.isEmpty, let live, live.stage != .stopped, core != nil else { return }
        let entries: [Entry]
        do throws(FoldError) {
            entries = try core!.state.record(events)
        } catch {
            log.fault("an event broke a fold rule: \(String(describing: error.violation), privacy: .public)")
            return
        }
        core!.digest.add(entries)
        do {
            try live.sink.append(entries)
        } catch {
            stop(live.url, error)
        }
    }

    /// Stops the session at once and shows the operator the storage alert. The state is then taken from the file,
    /// which is what a relaunch will find.
    private func stop(_ url: URL, _ error: any Error) {
        guard let live, live.url == url, live.stage != .stopped else { return }
        log.error("journal write failed: \(String(describing: error), privacy: .public)")
        self.live?.stage = .stopped
        self.live?.away = false
        notice = .storageFailure
        revision += 1
        let closing = closeSink()
        rebuild = Task {
            await closing.value
            let data = (try? Data(contentsOf: url)) ?? Data()
            switch Journal.recover(data) {
            case .clean(let state), .truncated(let state, _):
                core = Core(state: state, recorder: InkRecorder(resuming: state.session),
                            digest: JournalDigest(Self.entries(data)))
            case .damaged:
                break
            }
            revision += 1
        }
    }

    @discardableResult
    private func closeSink() -> Task<Void, Never> {
        if let closing = live?.closing { return closing }
        guard let sink = live?.sink else { return Task {} }
        let closing = Task { _ = try? await sink.close() }
        live?.closing = closing
        return closing
    }

    // MARK: Participant

    func start() {
        guard let live, live.stage == .ready else { return }
        guard perform(.handoff(to: .participant, t: Clock.uptime)) else { return }
        if live.dominant == nil, live.plan.usesUnknownHand {
            self.live?.stage = .hand
        } else {
            advance()
        }
    }

    func chooseHand(_ hand: Hand) {
        guard let live, live.stage == .hand else { return }
        self.live?.dominant = hand
        self.live?.plan = SessionPlan(configuration: live.configuration, opened: live.opened, dominant: hand)
        self.live?.stage = .working
        advance()
    }

    func acknowledgeDisclosure() {
        guard live?.stage == .disclosure else { return }
        live?.disclosureShown = true
        advance()
    }

    /// Starts the next trial once the capture view has its size, so `trialStarted` records the real geometry.
    func startTrial(view: ViewSize) {
        guard let live, live.stage == .working, let state = core?.state,
              case .task(let step, open: false) = live.plan.next(state.session),
              let spec = live.rules.battery.task(step.task) else { return }
        let origin = StimulusLayout.origin(for: spec, in: view, mmPerPoint: live.opened.scale.mmPerPoint)
        let version = live.configuration.locales[live.opened.locale.rawValue]?.instructionVersion
            ?? live.rules.battery.version
        let plan = TrialPlan(task: step.task, practice: step.practice, position: step.position, hand: step.hand,
                             instruction: InstructionRef(id: step.task.rawValue, version: version,
                                                         locale: live.opened.locale),
                             view: view, stimulusOrigin: origin)
        let index = TrialIndex(state.session.trials.count)!
        if perform(.startTrial(plan, t: Clock.uptime)) {
            self.live?.trialGeometry = (index, view, origin)
        }
    }

    func begin() {
        perform(.beginTrial(t: Clock.uptime))
    }

    func done() { endTrial(.participantDone) }

    func timeLimitReached() { endTrial(.timeLimit) }

    /// Ends the open trial. A contact still down ends as cancelled first, because a trial ends with no contact down.
    private func endTrial(_ reason: TrialEndReason) {
        guard case .drawing = core?.state.phase else { return }
        let t = Clock.uptime
        record(core!.recorder.cancelAll(t: t))
        guard perform(.endTrial(reason: reason, t: t)) else { return }
        live?.trialGeometry = nil
        advance()
    }

    /// Answers the current item, or skips it when `value` is nil. The last item ends the block.
    func answer(_ value: Int?) {
        guard live?.stage == .working, let current = currentItem, let before = core?.state.session.safety else { return }
        guard perform(.respond(item: current.item.id, value: value, t: Clock.uptime)) else { return }
        if current.number == current.count {
            guard perform(.endQuestionnaire(reason: .completed, t: Clock.uptime)) else { return }
        }
        if let after = core?.state.session.safety, after.crisisListShown > before.crisisListShown {
            live?.stage = .flagCrisisList
        } else {
            advance()
        }
    }

    func continueAfterCrisisList() {
        switch live?.stage {
        case .flagCrisisList:
            advance()
        case .closingCrisisList:
            close()
        default:
            break
        }
    }

    /// Moves to the next step of the plan: a trial, the disclosure, a block, or the end of the session.
    func advance() {
        guard let live, live.stage != .stopped, let state = core?.state else { return }
        switch live.plan.next(state.session) {
        case .task, .block(_, open: true):
            self.live?.stage = .working
        case .block(let id, open: false):
            if live.opened.administration == .selfAdministered, !live.disclosureShown,
               live.plan.hasSafetyItem(in: live.rules.instruments) {
                self.live?.stage = .disclosure
            } else if perform(.startQuestionnaire(id, t: Clock.uptime)) {
                self.live?.stage = .working
            }
        case .done:
            finish()
        }
    }

    private func finish() {
        guard let live, let state = core?.state else { return }
        if live.opened.purpose == .qualification {
            guard perform(.close(t: Clock.uptime)) else { return }
            qualifications.record(live.opened)
            self.live?.stage = .qualified
            closeSink()
        } else if live.opened.administration == .supervised {
            self.live?.stage = .handBack
        } else if !state.session.blocks.isEmpty {
            showClosingCrisisList()
        } else {
            close()
        }
    }

    private func showClosingCrisisList() {
        live?.stage = .closingCrisisList
        perform(.show(.crisisList, t: Clock.uptime))
    }

    private func close() {
        guard perform(.close(t: Clock.uptime)) else { return }
        live?.stage = .finished
        closeSink()
    }

    // MARK: Ink

    func pen(_ readings: [PenReading]) {
        guard isRecordingInk else { return }
        record(core!.recorder.pen(readings))
    }

    func estimatesUpdated(_ readings: [PenReading], arrived: Double) {
        guard core != nil, live?.stage != .stopped, core?.state.session.isClosed == false else { return }
        record(core!.recorder.estimatesUpdated(readings, arrived: arrived))
    }

    func hover(_ reading: HoverReading) {
        guard isRecordingInk, let event = core?.recorder.hover(reading) else { return }
        record([event])
    }

    func finger(_ reading: FingerReading) {
        guard isRecordingInk, let event = core?.recorder.finger(reading) else { return }
        record([event])
    }

    /// Ink is recorded from the Begin tap to the end of the trial.
    var isRecordingInk: Bool {
        guard live?.stage == .working, let state = core?.state, case .drawing(let open) = state.phase else {
            return false
        }
        return state.session.trials[open.index.rawValue].began
    }

    // MARK: Operator

    /// The operator passed the operator control. The participant stage is kept for the return.
    func operatorTookOver() {
        guard let live else { return }
        switch live.stage {
        case .finished, .qualified, .stopped:
            leave()
        case .paused, .review:
            break
        case .handBack:
            perform(.handoff(to: .operator, t: Clock.uptime))
            self.live?.stage = .review
            showOperatorAlert()
        default:
            perform(.handoff(to: .operator, t: Clock.uptime))
            self.live?.participantStage = live.stage
            self.live?.stage = .paused
        }
    }

    func returnToParticipant() {
        guard let live, live.stage == .paused else { return }
        guard perform(.handoff(to: .participant, t: Clock.uptime)) else { return }
        self.live?.stage = live.participantStage
    }

    /// Ends the open trial or block for the operator and opens the review, which leads to the close.
    func endEarly() {
        guard live?.stage == .paused, let state = core?.state else { return }
        switch state.phase {
        case .drawing:
            let t = Clock.uptime
            record(core!.recorder.cancelAll(t: t))
            perform(.endTrial(reason: .operatorAbort, t: t))
            live?.trialGeometry = nil
        case .answering:
            perform(.endQuestionnaire(reason: .declined, t: Clock.uptime))
        default:
            break
        }
        live?.stage = .review
        showOperatorAlert()
    }

    /// Records one neutral alert line when the review shows flags that have no action yet.
    private func showOperatorAlert() {
        guard live?.opened.administration == .supervised, let state = core?.state,
              state.session.safety.unactionedFlags > 0 else { return }
        perform(.show(.operatorAlert, t: Clock.uptime))
    }

    func recordAction(_ code: Code) {
        guard live?.stage == .review else { return }
        perform(.recordAction(code, t: Clock.uptime))
    }

    /// The operator gives the iPad back for the crisis list, which every session with a block ends with.
    func showSupportList() {
        guard live?.stage == .review, let state = core?.state, state.session.safety.unactionedFlags == 0 else { return }
        guard perform(.handoff(to: .participant, t: Clock.uptime)) else { return }
        showClosingCrisisList()
    }

    func closeFromReview() {
        guard live?.stage == .review, let state = core?.state, state.session.blocks.isEmpty else { return }
        guard perform(.close(t: Clock.uptime)) else { return }
        leave()
    }

    /// The operator goes to Home. An open session stays open and is listed as open. A closed or stopped session
    /// leaves memory once its writer has closed.
    func leave() {
        guard let live, let state = core?.state else { return }
        if state.session.isClosed || live.stage == .stopped {
            let closing = closeSink()
            self.live = nil
            core = nil
            revision += 1
            Task {
                await closing.value
                refresh()
            }
        } else {
            self.live?.away = true
            refresh()
        }
    }

    func returnToSession() {
        live?.away = false
    }

    // MARK: Scene

    /// The iPad left the app: an open trial ends as backgrounded, and every line reaches storage.
    func enterBackground() {
        if case .drawing = core?.state.phase, live?.stage == .working {
            endTrial(.backgrounded)
        }
        guard let live, live.closing == nil else { return }
        let task = UIApplication.shared.beginBackgroundTask(expirationHandler: nil)
        Task {
            try? await live.sink.sync()
            UIApplication.shared.endBackgroundTask(task)
        }
    }

    // MARK: Export and withdrawal

    /// Publishes a closed session with the export policy of its own configuration.
    func export(_ id: SessionID) async -> ExportResult? {
        let opened: SessionOpened?
        if let live, live.opened.sessionID == id {
            guard core?.state.session.isClosed == true else { return nil }
            await closeSink().value
            opened = live.opened
        } else {
            opened = reconciled[id]?.session.opened
        }
        guard let opened, let configuration = configurations.configuration(sha: opened.study.configSHA256.hex),
              let policy = try? ExportPolicy(configuration: configuration) else {
            notice = .configurationMissing
            return nil
        }
        let storage = storage
        do {
            let url = try await Task.detached { try storage.publish(id, policy: policy) }.value
            reconciled[id] = nil
            if live == nil || live?.away == true { refresh() }
            if case .encrypted = policy { return ExportResult(url: url, encrypted: true) }
            return ExportResult(url: url, encrypted: false)
        } catch {
            log.error("export failed: \(String(describing: error), privacy: .public)")
            notice = .exportFailed
            return nil
        }
    }

    func withdraw(_ code: ParticipantCode) -> Storage.Withdrawal? {
        guard live == nil else {
            notice = .sessionOpen
            return nil
        }
        do {
            let result = try storage.withdraw(participant: code, at: Clock.now)
            refresh()
            return result
        } catch {
            log.error("withdrawal failed: \(String(describing: error), privacy: .public)")
            notice = .failed(String(localized: "The withdrawal could not finish."))
            return nil
        }
    }

    func verifyWithdrawal(_ code: ParticipantCode) -> Bool? {
        do {
            return try storage.verifyWithdrawal(participant: code)
        } catch {
            notice = .failed(String(localized: "The deletion log could not be read."))
            return nil
        }
    }
}

extension Session {
    var isClosed: Bool { counts[.sessionClosed] > 0 }
}

extension SessionPlan {
    /// True when a step runs with a hand that is not known, which a resumed session must ask for.
    var usesUnknownHand: Bool {
        steps.contains { step in
            if case .task(let task) = step { task.hand == .unknown } else { false }
        }
    }
}

// MARK: - Current screen

extension SessionStore {
    struct TaskModel: Hashable {
        let step: SessionPlan.TaskStep
        let spec: TaskSpec
        let instruction: String
        /// Nil until `trialStarted`.
        let trial: TrialIndex?
        let view: ViewSize?
        let origin: Point?
        let began: Bool
        let deadline: Double?
        let mmPerPoint: Double?
        let purpose: Purpose
    }

    struct ItemModel: Hashable {
        let block: BlockIndex
        let instrument: Instrument
        let item: Instrument.Item
        /// 1-based.
        let number: Int
        let count: Int
    }

    /// The task of the current step, open or about to start.
    var currentTask: TaskModel? {
        guard let live, let state, case .task(let step, let open) = live.plan.next(state.session),
              let spec = live.rules.battery.task(step.task) else { return nil }
        let text = live.configuration.locales[live.opened.locale.rawValue]?.instructions[step.task.rawValue]
            ?? spec.instruction
        var trial: TrialIndex?
        var deadline: Double?
        var began = false
        if open, case .drawing(let openTrial) = state.phase {
            trial = openTrial.index
            deadline = openTrial.deadline
            began = state.session.trials[openTrial.index.rawValue].began
        }
        let geometry = live.trialGeometry.flatMap { $0.index == trial ? $0 : nil }
        return TaskModel(step: step, spec: spec, instruction: text, trial: trial, view: geometry?.view,
                         origin: geometry?.origin, began: began, deadline: deadline,
                         mmPerPoint: live.opened.scale.mmPerPoint, purpose: live.opened.purpose)
    }

    /// The item to answer in the open block. Items are answered in order and once.
    var currentItem: ItemModel? {
        guard let live, let state, case .answering(let open) = state.phase,
              let instrument = live.rules.instruments.instrument(open.instrument) else { return nil }
        let record = state.session.blocks[open.block.rawValue]
        let index = record.answered + record.skipped
        guard index < instrument.items.count else { return nil }
        return ItemModel(block: open.block, instrument: instrument, item: instrument.items[index], number: index + 1,
                         count: instrument.items.count)
    }
}
