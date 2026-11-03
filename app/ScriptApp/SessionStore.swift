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
    /// The participant types their code again before a self-administered session resumes.
    case confirmCode
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
    /// The operator records bench runs: the run screen, or the task screen of the chosen or open run.
    case bench
    /// A qualification or bench session is closed. The operator sees the sample timing and exports the session.
    case sampleTiming
    /// A write failed. The session stopped, and the operator sees the storage alert.
    case stopped
}

/// What the participant flow of a study or qualification session needs: its frozen configuration, its plan, and
/// where the participant is.
struct ParticipantFlow {
    let configuration: StudyConfiguration
    var plan: SessionPlan
    var dominant: Hand?
    var disclosureShown = false
    /// The stage to go back to when the operator returns the iPad.
    var participantStage: Stage = .working
    /// True after the operator ended the session early. The review follows the crisis list.
    var endedEarly = false
}

/// One open session: its rules, its writer, where the flow is, and the participant flow when a person draws.
struct LiveSession {
    let opened: SessionOpened
    let rules: SessionRules
    /// Nil for a bench session, which has no configuration and no fixed plan: the operator picks each run.
    var participant: ParticipantFlow?
    let url: URL
    let sink: any JournalSink
    var stage: Stage
    /// The bench run that the operator picked. Its trial starts once the task view has its size.
    var chosenRun: BenchRunPlan?
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
    /// The participant code of a session row. Home masks it until the operator unlocks with the PIN.
    var participant: ParticipantCode?
    /// The file name of a journal in `Damaged/`, which the operator can export as a damaged package.
    var damagedFile: String?
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
    /// How the export holds its journal.
    enum Form: Hashable { case encrypted, demo, bench }

    let url: URL
    let form: Form
    var encrypted: Bool { form == .encrypted }
    var name: String { url.lastPathComponent }
    var id: String { name }

    /// A plaintext export of a bench session holds released data. Any other plaintext export is a demo.
    init(url: URL, policy: ExportPolicy, opened: SessionOpened?) {
        self.url = url
        form = if case .encrypted = policy { .encrypted } else if case .bench? = opened?.subject { .bench } else { .demo }
    }
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
    /// True after the operator unlocked with the PIN in this launch. Home then shows participant codes in full.
    private(set) var codesUnlocked = false

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

    /// Reconciles every journal and lists the sessions. An interrupted self-administered session whose resume window
    /// has passed closes, so the operator can export it. The latest other one opens again for its participant, who
    /// has no operator to resume it and confirms their code first.
    func launch(at launch: Storage.Launch = Clock.launch) {
        refresh(at: launch)
        var waiting: [Listed] { listing.filter { $0.status == .interrupted && $0.administration == .selfAdministered } }
        let expired = waiting.compactMap(\.sessionID).filter { expireIfDue($0, at: launch) }
        if !expired.isEmpty { refresh(at: launch) }
        if let latest = waiting.max(by: { ($0.openedAt ?? "") < ($1.openedAt ?? "") }), let id = latest.sessionID {
            resume(id, at: launch)
        }
    }

    /// Closes an interrupted self-administered session whose resume window has passed. True when the window has
    /// passed, so the session must not resume, even when the close could not be written; the next launch tries again.
    private func expireIfDue(_ id: SessionID, at launch: Storage.Launch) -> Bool {
        guard let state = reconciled[id], let person = state.session.opened.person,
              let configuration = configurations.configuration(of: state.session.opened) else { return false }
        let command = Command.expire(at: launch.at, t: launch.uptime)
        guard case .success(let events) = state.decide(command, rules: participantRules(person, configuration)) else {
            return false
        }
        var closed = state
        do {
            try storage.append(try closed.record(events), to: id)
            reconciled[id] = closed
        } catch {
            log.error("expiry failed: \(String(describing: error), privacy: .public)")
            notice = .failed(String(localized: "A session past its resume time could not be closed."))
        }
        return true
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
                let results = try storage.reconcile(launch, instruments: contract.instruments) { [configurations] opened in
                    try configurations.exportPolicy(opened)
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
        return Listed(id: opened.sessionID.description, status: status,
                      title: opened.participant?.rawValue ?? opened.benchLabel ?? "", sessionID: opened.sessionID,
                      purpose: opened.purpose, administration: opened.person?.administration,
                      openedAt: opened.openedAt.text, encrypted: nil, participant: opened.participant)
    }

    /// The name of a Home row. A participant code shows only its last 4 characters until the operator unlocks with
    /// the PIN in this launch, so a configuration without a PIN always masks it.
    func title(of row: Listed) -> String {
        guard let code = row.participant, !codesUnlocked else { return row.title }
        return "•••" + code.rawValue.suffix(4)
    }

    /// The operator entered the PIN of the configuration.
    func unlockCodes() {
        codesUnlocked = true
    }

    private func entries(in url: URL) -> [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix("._") }
    }

    /// The export folders. A UI test run also writes a summary file beside each folder, which is not an export.
    private func exportedRows() -> [Listed] {
        entries(in: storage.exports).filter(\.isFolder).map { folder in
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
                   purpose: nil, administration: nil, openedAt: nil, encrypted: nil, damagedFile: file.lastPathComponent)
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

    /// Writes line 0 of a study or qualification session and opens it under its frozen configuration.
    func open(_ opened: SessionOpened, configuration: StudyConfiguration, dominant: Hand) {
        guard let person = opened.person else {
            log.fault("a session without a participant needs openBench")
            return
        }
        let flow = ParticipantFlow(configuration: configuration,
                                   plan: SessionPlan(configuration: configuration, opened: opened, dominant: dominant),
                                   dominant: dominant)
        open(opened, rules: participantRules(person, configuration), participant: flow,
             stage: opened.purpose == .qualification ? .working : .ready)
    }

    /// Writes line 0 of a bench session and opens it at the run screen.
    func openBench(_ opened: SessionOpened) {
        guard case .bench = opened.subject else {
            log.fault("a session with a participant needs a configuration")
            return
        }
        open(opened, rules: benchRules, participant: nil, stage: .bench)
    }

    private func open(_ opened: SessionOpened, rules: SessionRules, participant: ParticipantFlow?, stage: Stage) {
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
            live = LiveSession(opened: opened, rules: rules, participant: participant, url: url, sink: sink, stage: stage)
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

    private func participantRules(_ person: ParticipantFacts, _ configuration: StudyConfiguration) -> SessionRules {
        SessionRules(battery: contract.battery, instruments: contract.instruments, configuration: configuration,
                     jurisdiction: person.jurisdiction, locale: person.locale)
    }

    /// A bench session runs under the bundled battery and programs file, without a configuration.
    private var benchRules: SessionRules {
        SessionRules(battery: contract.battery, instruments: contract.instruments, benchPrograms: contract.benchPrograms)
    }

    /// Opens an interrupted journal again with a new writer. A study or qualification session needs the configuration
    /// that it recorded on this device, because a configuration is frozen for the life of a session. A
    /// self-administered session past its resume window closes instead, and one inside it waits for its participant
    /// code. A bench session opens at the run screen.
    func resume(_ id: SessionID, at launch: Storage.Launch = Clock.launch) {
        guard live == nil, let state = reconciled[id], !state.session.isClosed else { return }
        let opened = state.session.opened
        if opened.person?.administration == .selfAdministered, expireIfDue(id, at: launch) {
            refresh(at: launch)
            return
        }
        guard hasRoom() else { return }
        let configuration = configurations.configuration(of: opened)
        if opened.person != nil, configuration == nil {
            notice = .configurationMissing
            return
        }
        let url = storage.journalURL(id)
        do {
            let digest = JournalDigest(Self.entries(try Data(contentsOf: url)))
            let sink = try makeSink(url, failureHandler(url))
            let rules: SessionRules
            var participant: ParticipantFlow?
            var stage = Stage.bench
            if let person = opened.person, let configuration {
                let dominant = Self.dominantHand(configuration: configuration, digest: digest)
                let plan = SessionPlan(configuration: configuration, opened: opened, dominant: dominant ?? .unknown)
                rules = participantRules(person, configuration)
                participant = ParticipantFlow(configuration: configuration, plan: plan, dominant: dominant)
                stage = .ready
                if opened.purpose == .qualification {
                    stage = .working
                } else if person.administration == .selfAdministered {
                    stage = .confirmCode
                } else if plan.next(state.session) == .done {
                    stage = .review
                }
            } else {
                rules = benchRules
            }
            core = Core(state: state, recorder: InkRecorder(resuming: state.session), digest: digest)
            live = LiveSession(opened: opened, rules: rules, participant: participant, url: url, sink: sink, stage: stage)
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
        return digest.trials.first { tasks.contains($0.task) && ($0.hand ?? .unknown) != .unknown }?.hand
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

    /// The participant types their code to resume a self-administered session. False when it is not the code of the
    /// session, and the screen asks again.
    @discardableResult
    func confirmCode(_ text: String) -> Bool {
        guard let live, live.stage == .confirmCode,
              ParticipantCode(text.trimmingCharacters(in: .whitespaces).uppercased()) == live.opened.participant
        else { return false }
        self.live?.stage = .ready
        return true
    }

    func start() {
        guard let flow = live?.participant, live?.stage == .ready else { return }
        guard perform(.handoff(to: .participant, t: Clock.uptime)) else { return }
        if flow.dominant == nil, flow.plan.usesUnknownHand {
            self.live?.stage = .hand
        } else {
            advance()
        }
    }

    func chooseHand(_ hand: Hand) {
        guard let live, let flow = live.participant, live.stage == .hand else { return }
        self.live?.participant?.dominant = hand
        self.live?.participant?.plan = SessionPlan(configuration: flow.configuration, opened: live.opened, dominant: hand)
        self.live?.stage = .working
        advance()
    }

    func acknowledgeDisclosure() {
        guard live?.stage == .disclosure else { return }
        live?.participant?.disclosureShown = true
        advance()
    }

    /// Starts the next trial once the capture view has its size, so `trialStarted` records the real geometry.
    func startTrial(view: ViewSize) {
        guard let live, live.stage == .working, let flow = live.participant, let person = live.opened.person,
              let state = core?.state, case .task(let step, open: false) = flow.plan.next(state.session),
              let spec = live.rules.battery.task(step.task) else { return }
        let origin = StimulusLayout.origin(for: spec, in: view, mmPerPoint: live.opened.scale.mmPerPoint)
        let version = flow.configuration.locales[person.locale.rawValue]?.instructionVersion
            ?? live.rules.battery.version
        let instruction = InstructionRef(id: step.task.rawValue, version: version, locale: person.locale)
        let plan = TrialPlan(task: step.task, practice: step.practice, position: step.position,
                             subject: .participant(ParticipantTrialFacts(hand: step.hand, instruction: instruction)),
                             view: view, stimulusOrigin: origin)
        let index = TrialIndex(state.session.trials.count)!
        if perform(.startTrial(plan, t: Clock.uptime)) {
            self.live?.trialGeometry = (index, view, origin)
        }
    }

    func begin() {
        perform(.beginTrial(t: Clock.uptime))
    }

    /// The one who draws taps Done: the participant, or the operator of a bench session.
    func done() { endTrial(live?.opened.person == nil ? .operatorDone : .participantDone) }

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
    func answer(_ value: Int?) async {
        guard live?.stage == .working, let current = currentItem, let before = core?.state.session.safety else { return }
        guard perform(.respond(item: current.item.id, value: value, t: Clock.uptime)) else { return }
        if current.number == current.count {
            guard perform(.endQuestionnaire(reason: .completed, t: Clock.uptime)) else { return }
        }
        guard await syncedFlags(since: before), live?.stage == .working,
              let after = core?.state.session.safety else { return }
        if after.crisisListShown > before.crisisListShown {
            live?.stage = .flagCrisisList
        } else {
            advance()
        }
    }

    /// Waits until every new safety flag is on storage, so that no screen that follows a flag shows before the flag
    /// is durable. False when the write failed, which stops the session.
    private func syncedFlags(since before: SafetyRecord) async -> Bool {
        guard let live, let after = core?.state.session.safety, after.flags > before.flags else { return true }
        do {
            try await live.sink.sync()
            return true
        } catch {
            return false
        }
    }

    func continueAfterCrisisList() {
        switch live?.stage {
        case .flagCrisisList:
            if live?.participant?.endedEarly == true { live?.stage = .review } else { advance() }
        case .closingCrisisList:
            close()
        default:
            break
        }
    }

    /// Moves to the next step of the plan: a trial, the disclosure, a block, or the end of the session. A
    /// self-administered session first shows the crisis list for a flag that has not had it, as after a recovery. A
    /// bench session has no plan, so it stays where it is.
    func advance() {
        guard let live, let flow = live.participant, live.stage != .stopped, let state = core?.state else { return }
        if live.opened.person?.administration == .selfAdministered, state.session.safety.flaggedSinceCrisisList {
            if perform(.show(.crisisList, t: Clock.uptime)) { self.live?.stage = .flagCrisisList }
            return
        }
        switch flow.plan.next(state.session) {
        case .task, .block(_, open: true):
            self.live?.stage = .working
        case .block(let id, open: false):
            if live.opened.person?.administration == .selfAdministered, !flow.disclosureShown,
               flow.plan.hasSafetyItem(in: live.rules.instruments) {
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
            self.live?.stage = .sampleTiming
            closeSink()
        } else if live.opened.person?.administration == .supervised {
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

    // MARK: Bench

    /// The run id that `decide` writes as the clip of `run` in this bench session.
    func runID(_ run: BenchRunPlan) -> ClipName? {
        guard case .bench(let facts) = live?.opened.subject else { return nil }
        return ClipName(runOf: run.program, run: run.run, in: facts)
    }

    /// Why `decide` refuses `run` in this bench session, or nil when the run screen can offer it.
    func runRefusal(_ run: BenchRunPlan) -> Rejection? {
        guard let live, let state, case .failure(let rejection) = state.benchRun(run, rules: live.rules) else {
            return nil
        }
        return rejection
    }

    /// The operator picked the next run on the run screen. The task screen starts it.
    func chooseRun(_ run: BenchRunPlan) {
        guard live?.stage == .bench, openTrial == nil, runRefusal(run) == nil else { return }
        live?.chosenRun = run
    }

    /// Starts the chosen run once the task view has its size, so `trialStarted` records the real geometry. The
    /// stimulus origin is the origin of the task view (bench protocol section 2.4). A run that `decide` refuses goes
    /// back to the run screen.
    func startRun(view: ViewSize) {
        guard let live, live.stage == .bench, let run = live.chosenRun, let state = core?.state else { return }
        self.live?.chosenRun = nil
        guard let program = live.rules.benchPrograms?.program(run.program) else { return }
        let origin = Point(x: 0, y: 0)
        let plan = TrialPlan(task: program.task, practice: false, position: state.session.trials.count + 1,
                             subject: .bench(run), view: view, stimulusOrigin: origin)
        let index = TrialIndex(state.session.trials.count)!
        if perform(.startTrial(plan, t: Clock.uptime)) {
            self.live?.trialGeometry = (index, view, origin)
        }
    }

    /// Adds a mark to the open run. False for a code that the run does not offer.
    @discardableResult
    func mark(_ code: MarkCode) -> Bool {
        guard let run = currentRun, let trial = run.trial, run.markCodes.contains(code) else { return false }
        return perform(.mark(trial: trial, code: code, t: Clock.uptime))
    }

    /// The operator stops the open run, for example after a wrong pick or a fault of the motion system.
    func stopRun() {
        guard live?.stage == .bench else { return }
        endTrial(.operatorAbort)
    }

    /// Closes a bench session between runs. The operator then exports it.
    func endBench() {
        guard live?.stage == .bench, perform(.close(t: Clock.uptime)) else { return }
        live?.chosenRun = nil
        live?.stage = .sampleTiming
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
        guard live?.stage.showsTrials == true, let state = core?.state, case .drawing(let open) = state.phase else {
            return false
        }
        return state.session.trials[open.index.rawValue].began
    }

    // MARK: Operator

    /// The operator passed the operator control, with the PIN when the configuration has one. The participant stage
    /// is kept for the return.
    func operatorTookOver(withPIN: Bool = false) {
        if withPIN { codesUnlocked = true }
        guard let live else { return }
        switch live.stage {
        case .finished, .sampleTiming, .stopped:
            leave()
        case .paused, .review, .bench:
            break
        case .handBack:
            perform(.handoff(to: .operator, t: Clock.uptime))
            self.live?.stage = .review
            showOperatorAlert()
        default:
            perform(.handoff(to: .operator, t: Clock.uptime))
            self.live?.participant?.participantStage = live.stage
            self.live?.stage = .paused
        }
    }

    func returnToParticipant() {
        guard let flow = live?.participant, live?.stage == .paused else { return }
        guard perform(.handoff(to: .participant, t: Clock.uptime)) else { return }
        self.live?.stage = flow.participantStage
    }

    /// Ends the open trial or block for the operator and opens the review, which leads to the close. A block that
    /// ends before its safety item flags the item; a self-administered session then shows the crisis list first.
    func endEarly() async {
        guard live?.stage == .paused, let state = core?.state else { return }
        let before = state.session.safety
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
        guard await syncedFlags(since: before), live?.stage == .paused,
              let after = core?.state.session.safety else { return }
        live?.participant?.endedEarly = true
        if after.crisisListShown > before.crisisListShown {
            live?.stage = .flagCrisisList
        } else {
            live?.stage = .review
            showOperatorAlert()
        }
    }

    /// Records one neutral alert line when the review shows flags that have no action yet.
    private func showOperatorAlert() {
        guard live?.opened.person?.administration == .supervised, let state = core?.state,
              state.session.safety.unactionedFlags > 0 else { return }
        perform(.show(.operatorAlert, t: Clock.uptime))
    }

    func recordAction(_ code: Code) {
        guard live?.stage == .review else { return }
        perform(.recordAction(code, t: Clock.uptime))
    }

    /// The operator gives the iPad back for the crisis list, which every session with a block ends with. A supervised
    /// flag needs its action first; a self-administered flag goes to the study reviewer after export.
    func showSupportList() {
        guard let live, live.stage == .review, let state = core?.state,
              state.session.safety.unactionedFlags == 0 || live.opened.person?.administration == .selfAdministered
        else { return }
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
        if case .drawing = core?.state.phase, live?.stage.showsTrials == true {
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

    /// Publishes a closed session with its export policy: plaintext for a bench session, else the policy of the
    /// session's own configuration.
    func export(_ id: SessionID) async -> ExportResult? {
        let opened: SessionOpened?
        if let live, live.opened.sessionID == id {
            guard core?.state.session.isClosed == true else { return nil }
            await closeSink().value
            opened = live.opened
        } else {
            opened = reconciled[id]?.session.opened
        }
        guard let opened, let policy = try? configurations.exportPolicy(opened) else {
            notice = .configurationMissing
            return nil
        }
        let storage = storage
        do {
            let url = try await Task.detached { try storage.publish(id, policy: policy) }.value
            if LaunchMode.writesFoldSummaries { writeFoldSummary(of: url) }
            reconciled[id] = nil
            if live == nil || live?.away == true { refresh() }
            return ExportResult(url: url, policy: policy, opened: opened)
        } catch {
            log.error("export failed: \(String(describing: error), privacy: .public)")
            notice = .exportFailed
            return nil
        }
    }

    /// Publishes a journal from `Damaged/` as found, so its flags reach the study, under the export policy of the
    /// session that its first line names, or of the active configuration when that line does not decode.
    func exportDamaged(_ file: String) async -> ExportResult? {
        let opened = storage.opened(damaged: file)
        let policy = if let opened {
            try? configurations.exportPolicy(opened)
        } else {
            try? ExportPolicy(configuration: activeConfiguration)
        }
        guard let policy else {
            notice = .configurationMissing
            return nil
        }
        let storage = storage
        do {
            let url = try await Task.detached { try storage.publishDamaged(file, policy: policy) }.value
            if live == nil || live?.away == true { refresh() }
            return ExportResult(url: url, policy: policy, opened: opened)
        } catch {
            log.error("damaged export failed: \(String(describing: error), privacy: .public)")
            notice = .exportFailed
            return nil
        }
    }

    /// Writes the Swift fold summary of a plaintext export beside its folder as `<name>.summary.json`.
    private func writeFoldSummary(of export: URL) {
        guard let data = try? Data(contentsOf: export.appendingPathComponent(Storage.Export.journal)),
              let state = try? Journal.read(data) else { return }
        let file = export.deletingLastPathComponent().appendingPathComponent("\(export.lastPathComponent).summary.json")
        try? state.summary.jsonData().write(to: file)
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

extension SessionOpened {
    /// The facts of the person in a study or qualification session. Nil for a bench session, which has no person.
    var person: ParticipantFacts? {
        if case .participant(let facts) = subject { facts } else { nil }
    }

    /// The role and the mounting of a bench session, for example `P-M3`, as each of its run ids begins.
    var benchLabel: String? {
        if case .bench(let facts) = subject { "\(facts.role.rawValue)-\(facts.mounting.rawValue)" } else { nil }
    }
}

extension Stage {
    /// A stage whose screen can be the task screen of the open trial.
    var showsTrials: Bool { self == .working || self == .bench }
}

fileprivate extension URL {
    var isFolder: Bool { (try? resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true }
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

    /// The index of the open trial, or nil when no trial is open.
    var openTrial: TrialIndex? {
        if case .drawing(let open) = state?.phase { open.index } else { nil }
    }

    /// The task of the current step, open or about to start.
    var currentTask: TaskModel? {
        guard let live, let flow = live.participant, let state, case .task(let step, let open) = flow.plan.next(state.session),
              let spec = live.rules.battery.task(step.task) else { return nil }
        let locale = live.opened.person.flatMap { flow.configuration.locales[$0.locale.rawValue] }
        let text = locale?.instructions[step.task.rawValue] ?? spec.instruction
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

    /// One bench run on the task screen: the run that the operator picked, then its open trial.
    struct RunModel: Hashable {
        let program: BenchProgram
        let spec: TaskSpec
        /// The run id. The open run shows the clip that its `trialStarted` line records.
        let runID: ClipName
        /// Nil until `trialStarted`.
        let trial: TrialIndex?
        let view: ViewSize?
        let origin: Point?
        let began: Bool
        let deadline: Double?
        let mmPerPoint: Double?
        let marks: [MarkCode]
        var markCodes: [MarkCode] { program.markCodes }
    }

    /// The open trial of a bench session, or the run that the operator picked before its trial starts. Nil between
    /// runs, when the run screen shows.
    var currentRun: RunModel? {
        guard let live, live.stage == .bench, let state, let programs = live.rules.benchPrograms else { return nil }
        let mm = live.opened.scale.mmPerPoint
        if case .drawing(let open) = state.phase {
            guard let digested = digest?.trials[open.index.rawValue], let run = digested.run,
                  let program = programs.program(run.program), let spec = live.rules.battery.task(program.task),
                  let runID = run.clip ?? self.runID(BenchRunPlan(program: run.program, run: run.run)) else { return nil }
            let geometry = live.trialGeometry.flatMap { $0.index == open.index ? $0 : nil }
            return RunModel(program: program, spec: spec, runID: runID, trial: open.index, view: geometry?.view,
                            origin: geometry?.origin, began: state.session.trials[open.index.rawValue].began,
                            deadline: open.deadline, mmPerPoint: mm, marks: digested.marks)
        }
        guard let chosen = live.chosenRun, let program = programs.program(chosen.program),
              let spec = live.rules.battery.task(program.task), let runID = self.runID(chosen) else { return nil }
        return RunModel(program: program, spec: spec, runID: runID, trial: nil, view: nil, origin: nil, began: false,
                        deadline: nil, mmPerPoint: mm, marks: [])
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
