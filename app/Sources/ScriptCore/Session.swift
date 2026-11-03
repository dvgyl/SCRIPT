import Foundation

/// The app's session state: a pure fold over the journal lines. Live operation and recovery share `apply`.
public struct SessionState: Hashable, Sendable {
    public enum Phase: Hashable, Sendable {
        case ready
        case drawing(OpenTrial)
        case answering(OpenBlock)
        /// The last state-changing line is `recovered`.
        case suspended
        case closed
    }

    public enum Status: String, Hashable, Sendable { case ready, drawing, answering, suspended, closed }

    public private(set) var phase: Phase
    public private(set) var session: Session

    /// The state after line 0, which must be `sessionOpened`.
    public init(opening entry: Entry) throws(FoldError) {
        guard entry.seq == 0, case .sessionOpened(let opened) = entry.event else {
            throw FoldError(line: entry.seq, violation: .firstLineNotOpened)
        }
        guard JournalFormat.isReadable(opened.format) else {
            throw FoldError(line: 0, violation: .unsupportedFormat(opened.format))
        }
        phase = .ready
        session = Session(opened: opened)
        session.counts[.sessionOpened] = 1
        session.lines = 1
    }

    /// Applies every entry in order.
    public static func fold(_ entries: some Sequence<Entry>) throws(FoldError) -> SessionState {
        var iterator = entries.makeIterator()
        guard let first = iterator.next() else { throw FoldError(line: 0, violation: .firstLineNotOpened) }
        var state = try SessionState(opening: first)
        while let entry = iterator.next() { try state.apply(entry) }
        return state
    }

    public var status: Status {
        switch phase {
        case .ready: .ready
        case .drawing: .drawing
        case .answering: .answering
        case .suspended: .suspended
        case .closed: .closed
        }
    }

    /// Numbers the events from the next `seq` and applies them. Nothing changes when one event breaks a rule.
    public mutating func record(_ events: [Event]) throws(FoldError) -> [Entry] {
        let entries = events.enumerated().map { Entry(seq: session.lines + $0.offset, event: $0.element) }
        if entries.count > 1 {
            // A trial run on a copy checks the batch. It skips the estimate table, which never refuses a line and is
            // the one structure that grows with every sample, so the copy stays small.
            var trial = self
            for entry in entries { try trial.apply(entry, estimates: false) }
        }
        for entry in entries { try apply(entry, estimates: true) }
        return entries
    }
}

public struct OpenTrial: Hashable, Sendable {
    public let index: TrialIndex
    public let task: TaskID
    /// The uptime at which the time limit ends the trial.
    public let deadline: Double
}

public struct OpenBlock: Hashable, Sendable {
    public let block: BlockIndex
    public let instrument: InstrumentID
}

public enum TrialOutcome: String, Hashable, Sendable {
    case completed, timeLimit, declined, unable, aborted, interrupted, technicalFailure, open

    init(_ reason: TrialEndReason) {
        switch reason {
        case .participantDone: self = .completed
        case .timeLimit: self = .timeLimit
        case .declined: self = .declined
        case .unable: self = .unable
        case .operatorAbort: self = .aborted
        case .backgrounded: self = .interrupted
        case .technicalFailure: self = .technicalFailure
        }
    }
}

public enum BlockOutcome: String, Hashable, Sendable { case completed, declined, interrupted, open }

public struct TrialRecord: Hashable, Sendable {
    public let index: TrialIndex
    public let task: TaskID
    public let attempt: Int
    public let practice: Bool
    public internal(set) var began = false
    public internal(set) var outcome: TrialOutcome = .open
    public internal(set) var contacts = 0
    public internal(set) var samples = 0
    /// Matched corrections, counted in the trial of the first sample with their key.
    public internal(set) var corrections = 0
}

public struct BlockRecord: Hashable, Sendable {
    public let block: BlockIndex
    public let instrument: InstrumentID
    public internal(set) var answered = 0
    public internal(set) var skipped = 0
    public internal(set) var outcome: BlockOutcome = .open
}

public struct SafetyRecord: Hashable, Sendable {
    public internal(set) var flags = 0
    public internal(set) var crisisListShown = 0
    public internal(set) var operatorAlerts = 0
    public internal(set) var actions = 0
    /// Flags that no later `safetyAction` answers. Each action answers the oldest open flag.
    public internal(set) var unactionedFlags = 0
    /// True from the end of a questionnaire block until the next crisis-list display.
    public internal(set) var crisisListDue = false
}

/// Everything the fold knows about one session.
public struct Session: Hashable, Sendable {
    public let opened: SessionOpened
    /// The boot of the latest `sessionOpened` or `recovered` line.
    public internal(set) var boot: BootID
    /// The number of lines so far, which is also the next `seq`.
    public internal(set) var lines = 0
    public internal(set) var counts = KindCounts()
    public internal(set) var trials: [TrialRecord] = []
    public internal(set) var blocks: [BlockRecord] = []
    public internal(set) var safety = SafetyRecord()
    public internal(set) var nextContact = ContactID.first
    public internal(set) var unmatchedCorrections = 0
    public internal(set) var truncatedBytes = 0
    var openContacts: Set<ContactID> = []
    var contactTrial: [ContactID: TrialIndex] = [:]
    /// Every `(contact, update)` that a sample left pending.
    var pending: [PendingKey: PendingEstimate] = [:]

    init(opened: SessionOpened) {
        self.opened = opened
        boot = opened.boot
    }

    struct PendingKey: Hashable, Sendable {
        let contact: ContactID
        let update: UpdateIndex
    }

    /// The samples that share one key. A correction with the key sets the bits that stay pending for all of them.
    struct PendingEstimate: Hashable, Sendable {
        let trial: TrialIndex
        var remaining: TouchProperties
        var samples: Int
    }
}

/// A journal line that breaks an integrity rule (docs/DESIGN.md section 3.4).
public struct FoldError: Error, Hashable, Sendable {
    public let line: Int
    public let violation: Violation

    public enum Violation: Hashable, Sendable {
        case firstLineNotOpened
        case unsupportedFormat(String)
        case seq(expected: Int)
        case notAllowed(LineKind, in: SessionState.Status)
        case trialIndex(expected: TrialIndex)
        case blockIndex(expected: BlockIndex)
        case taskMismatch
        case wrongTrial(TrialIndex)
        case wrongBlock(BlockIndex)
        case alreadyBegan
        case contactReused(ContactID)
        case contactNotDown(ContactID)
        case contactsDown
        case unknownTrial(TrialIndex)
        case interruptedMismatch
        case countsMismatch
    }
}

// MARK: - Apply

extension SessionState {
    /// Applies one line. A line that breaks an integrity rule throws and leaves the state unchanged. The error names
    /// the line's position in the journal, which differs from its `seq` when rule I2 fails.
    public mutating func apply(_ entry: Entry) throws(FoldError) {
        try apply(entry, estimates: true)
    }

    /// `estimates: false` skips the pending-estimate table and the correction counts. Those never refuse a line.
    mutating func apply(_ entry: Entry, estimates: Bool) throws(FoldError) {
        let position = session.lines
        func fail(_ violation: FoldError.Violation) -> FoldError { FoldError(line: position, violation: violation) }
        let kind = entry.event.kind
        let notAllowed = fail(.notAllowed(kind, in: status))
        guard entry.seq == session.lines else { throw fail(.seq(expected: session.lines)) }
        if case .closed = phase { throw notAllowed }

        switch entry.event {
        case .sessionOpened:
            throw notAllowed

        case .handoff, .hover, .finger:
            break

        case .trialStarted(let e):
            guard isBetweenActivities else { throw notAllowed }
            let expected = TrialIndex(count: session.trials.count)
            guard e.trial.index == expected else { throw fail(.trialIndex(expected: expected)) }
            guard e.trial.task == e.spec.id else { throw fail(.taskMismatch) }
            session.trials.append(TrialRecord(index: e.trial.index, task: e.trial.task, attempt: e.trial.attempt,
                                              practice: e.practice))
            phase = .drawing(OpenTrial(index: e.trial.index, task: e.trial.task,
                                       deadline: e.spec.completion.deadline(startedAt: e.t)))

        case .trialBegan(let e):
            guard case .drawing(let open) = phase else { throw notAllowed }
            guard e.trial == open.index else { throw fail(.wrongTrial(e.trial)) }
            guard !session.trials[open.index.rawValue].began else { throw fail(.alreadyBegan) }
            session.trials[open.index.rawValue].began = true

        case .contactBegan(let e):
            guard case .drawing(let open) = phase else { throw notAllowed }
            guard session.contactTrial[e.contact] == nil else { throw fail(.contactReused(e.contact)) }
            session.contactTrial[e.contact] = open.index
            session.openContacts.insert(e.contact)
            session.trials[open.index.rawValue].contacts += 1
            session.nextContact = max(session.nextContact, e.contact.next)

        case .sample(let e):
            guard session.openContacts.contains(e.contact), let trial = session.contactTrial[e.contact] else {
                throw fail(.contactNotDown(e.contact))
            }
            session.trials[trial.rawValue].samples += 1
            if estimates, let p = e.pending {
                let key = Session.PendingKey(contact: e.contact, update: p.update)
                if session.pending[key] == nil {
                    session.pending[key] = Session.PendingEstimate(trial: trial, remaining: p.expect, samples: 1)
                } else {
                    session.pending[key]!.remaining.formUnion(p.expect)
                    session.pending[key]!.samples += 1
                }
            }

        case .correction(let e):
            guard estimates else { break }
            let key = e.contact.map { Session.PendingKey(contact: $0, update: e.update) }
            if let key, let estimate = session.pending[key] {
                session.pending[key]!.remaining = e.expect
                session.trials[estimate.trial.rawValue].corrections += 1
            } else {
                session.unmatchedCorrections += 1
            }

        case .contactEnded(let e):
            guard session.openContacts.contains(e.contact) else { throw fail(.contactNotDown(e.contact)) }
            session.openContacts.remove(e.contact)

        case .trialEnded(let e):
            guard case .drawing(let open) = phase else { throw notAllowed }
            guard e.trial == open.index else { throw fail(.wrongTrial(e.trial)) }
            guard session.openContacts.isEmpty else { throw fail(.contactsDown) }
            session.trials[open.index.rawValue].outcome = TrialOutcome(e.reason)
            phase = .ready

        case .questionnaireStarted(let e):
            guard isBetweenActivities else { throw notAllowed }
            let expected = BlockIndex(count: session.blocks.count)
            guard e.block == expected else { throw fail(.blockIndex(expected: expected)) }
            session.blocks.append(BlockRecord(block: e.block, instrument: e.instrument.id))
            phase = .answering(OpenBlock(block: e.block, instrument: e.instrument.id))

        case .response(let e):
            let open = try openBlock(e.block, notAllowed: notAllowed, fail: fail)
            if e.skipped {
                session.blocks[open.block.rawValue].skipped += 1
            } else {
                session.blocks[open.block.rawValue].answered += 1
            }

        case .questionnaireEnded(let e):
            let open = try openBlock(e.block, notAllowed: notAllowed, fail: fail)
            session.blocks[open.block.rawValue].outcome = BlockOutcome(rawValue: e.reason.rawValue)!
            session.safety.crisisListDue = true
            phase = .ready

        case .safetyFlagged(let e):
            _ = try openBlock(e.block, notAllowed: notAllowed, fail: fail)
            session.safety.flags += 1
            session.safety.unactionedFlags += 1

        case .safetyShown(let e):
            switch e.what {
            case .crisisList:
                session.safety.crisisListShown += 1
                session.safety.crisisListDue = false
            case .operatorAlert:
                session.safety.operatorAlerts += 1
            }

        case .safetyAction:
            session.safety.actions += 1
            session.safety.unactionedFlags = max(0, session.safety.unactionedFlags - 1)

        case .mark(let e):
            guard e.trial.rawValue < session.trials.count else { throw fail(.unknownTrial(e.trial)) }

        case .recovered(let e):
            let openTrial: TrialIndex? = if case .drawing(let trial) = phase { trial.index } else { nil }
            let openBlock: BlockIndex? = if case .answering(let block) = phase { block.block } else { nil }
            guard e.interrupted == openTrial, e.interruptedBlock == openBlock else { throw fail(.interruptedMismatch) }
            if let openTrial { session.trials[openTrial.rawValue].outcome = .interrupted }
            if let openBlock {
                session.blocks[openBlock.rawValue].outcome = .interrupted
                session.safety.crisisListDue = true
            }
            session.openContacts.removeAll()
            session.truncatedBytes += e.truncatedBytes
            session.boot = e.boot
            phase = .suspended

        case .sessionClosed(let e):
            guard isBetweenActivities else { throw notAllowed }
            guard e.counts == session.counts else { throw fail(.countsMismatch) }
            phase = .closed
        }
        session.counts[kind] += 1
        session.lines += 1
    }

    /// `ready` or `suspended`: no trial and no questionnaire block is open.
    private var isBetweenActivities: Bool {
        switch phase {
        case .ready, .suspended: true
        case .drawing, .answering, .closed: false
        }
    }

    private func openBlock(
        _ block: BlockIndex, notAllowed: FoldError, fail: (FoldError.Violation) -> FoldError
    ) throws(FoldError) -> OpenBlock {
        guard case .answering(let open) = phase else { throw notAllowed }
        guard block == open.block else { throw fail(.wrongBlock(block)) }
        return open
    }
}

// MARK: - Decide

/// What the app plans for one trial. `decide` adds the index, the attempt, and the task specification.
public struct TrialPlan: Hashable, Sendable {
    public var task: TaskID
    public var practice: Bool
    /// The 1-based position of the task in the session order.
    public var position: Int
    public var hand: Hand
    public var instruction: InstructionRef
    public var accommodations: [Accommodation]
    public var view: ViewSize
    public var stimulusOrigin: Point

    public init(task: TaskID, practice: Bool, position: Int, hand: Hand, instruction: InstructionRef,
                accommodations: [Accommodation] = [], view: ViewSize, stimulusOrigin: Point) {
        self.task = task
        self.practice = practice
        self.position = position
        self.hand = hand
        self.instruction = instruction
        self.accommodations = accommodations
        self.view = view
        self.stimulusOrigin = stimulusOrigin
    }
}

/// An operator, participant, or app intent. Ink comes from `InkRecorder`, and a relaunch uses `recovery`.
public enum Command: Hashable, Sendable {
    case handoff(to: HandoffTarget, t: Double)
    case startTrial(TrialPlan, t: Double)
    /// The participant taps Begin.
    case beginTrial(t: Double)
    case endTrial(reason: TrialEndReason, t: Double)
    case startQuestionnaire(InstrumentID, t: Double)
    /// An answer, or a skip when `value` is nil.
    case respond(item: ItemID, value: Int?, t: Double)
    case endQuestionnaire(reason: QuestionnaireEndReason, t: Double)
    /// The app shows safety content.
    case show(SafetyDisplay, t: Double)
    /// The operator records the response to the oldest unanswered safety flag.
    case recordAction(Code, t: Double)
    case mark(trial: TrialIndex, code: MarkCode, t: Double)
    case close(t: Double)

    /// The uptime of the intent, which every event it gives carries.
    var t: Double {
        switch self {
        case .handoff(_, let t), .startTrial(_, let t), .beginTrial(let t), .endTrial(_, let t),
             .startQuestionnaire(_, let t), .respond(_, _, let t), .endQuestionnaire(_, let t), .show(_, let t),
             .recordAction(_, let t), .mark(_, _, let t), .close(let t):
            t
        }
    }
}

public enum Rejection: Error, Hashable, Sendable {
    case sessionClosed
    /// The command time is negative or not finite, which the journal schema refuses.
    case invalidTime
    /// A task position below 1. Positions start at 1.
    case invalidPosition(Int)
    case repeatedAccommodation(Accommodation)
    case invalidStimulusOrigin
    case trialOpen
    case blockOpen
    case noOpenTrial
    case alreadyBegan
    case contactsDown
    case noOpenBlock
    /// The rules hold a battery that differs from the one in `sessionOpened`.
    case otherBattery
    case unknownTask(TaskID)
    /// A qualification task in a study session, or a study task in a qualification session.
    case taskNotForPurpose(TaskID)
    case unknownInstrument(InstrumentID)
    case unverifiedInstrument(InstrumentID)
    /// The instrument's lowest validated age is above the lowest age of the session's age band.
    case instrumentNotForAgeBand(InstrumentID)
    /// A self-administered block with a safety item needs `safety.remoteReview` (docs/DESIGN.md section 15.3).
    case safetyItemNeedsRemoteReview(InstrumentID)
    case unknownItem(ItemID)
    case invalidValue(ItemID, Int)
    /// The configuration has no crisis list for the session's jurisdiction.
    case noCrisisList
    /// An operator alert or action in a self-administered session, where no operator is present.
    case noOperator
    case unknownActionCode(Code)
    case noUnactionedFlag
    case unknownTrial(TrialIndex)
    /// A supervised session has a safety flag without a `safetyAction`.
    case unactionedFlag
    /// The session had a questionnaire block, and the crisis list has not been shown since the last one.
    case crisisListNotShown
}

extension SessionState {
    /// Turns an intent into the events to append, or refuses it. It never changes the state, and it never gives a
    /// value that the line decoder refuses.
    public func decide(_ command: Command, rules: SessionRules) -> Result<[Event], Rejection> {
        if case .closed = phase { return .failure(.sessionClosed) }
        guard command.t.isFinite, command.t >= 0 else { return .failure(.invalidTime) }
        let opened = session.opened
        switch command {
        case .handoff(let to, let t):
            return .success([.handoff(Handoff(t: t, to: to))])

        case .startTrial(let plan, let t):
            if let busy = busyRejection { return .failure(busy) }
            guard rules.battery.sha256 == opened.battery.sha256 else { return .failure(.otherBattery) }
            guard let spec = rules.battery.task(plan.task) else { return .failure(.unknownTask(plan.task)) }
            let qualificationTask = Battery.qualificationTasks.contains(plan.task)
            guard qualificationTask == (opened.purpose == .qualification) else {
                return .failure(.taskNotForPurpose(plan.task))
            }
            guard plan.position >= 1 else { return .failure(.invalidPosition(plan.position)) }
            var seen = Set<Accommodation>()
            for accommodation in plan.accommodations where !seen.insert(accommodation).inserted {
                return .failure(.repeatedAccommodation(accommodation))
            }
            guard plan.stimulusOrigin.x.isFinite, plan.stimulusOrigin.y.isFinite else {
                return .failure(.invalidStimulusOrigin)
            }
            let attempt = session.trials.count { $0.task == plan.task } + 1
            let trial = TrialRef(index: TrialIndex(count: session.trials.count), task: plan.task, attempt: attempt)
            return .success([.trialStarted(TrialStarted(
                t: t, trial: trial, spec: spec, practice: plan.practice, position: plan.position, hand: plan.hand,
                instruction: plan.instruction, accommodations: plan.accommodations, view: plan.view,
                stimulusOrigin: plan.stimulusOrigin
            ))])

        case .beginTrial(let t):
            guard case .drawing(let open) = phase else { return .failure(.noOpenTrial) }
            guard !session.trials[open.index.rawValue].began else { return .failure(.alreadyBegan) }
            return .success([.trialBegan(TrialBegan(t: t, trial: open.index))])

        case .endTrial(let reason, let t):
            guard case .drawing(let open) = phase else { return .failure(.noOpenTrial) }
            guard session.openContacts.isEmpty else { return .failure(.contactsDown) }
            return .success([.trialEnded(TrialEnded(t: t, trial: open.index, reason: reason))])

        case .startQuestionnaire(let id, let t):
            if let busy = busyRejection { return .failure(busy) }
            guard let instrument = rules.instruments.instrument(id) else { return .failure(.unknownInstrument(id)) }
            guard instrument.isVerified else { return .failure(.unverifiedInstrument(id)) }
            guard opened.ageBand.lowestAge >= instrument.minimumAge else { return .failure(.instrumentNotForAgeBand(id)) }
            guard rules.crisisList != nil else { return .failure(.noCrisisList) }
            if opened.administration == .selfAdministered, instrument.hasSafetyItem, rules.remoteReview == nil {
                return .failure(.safetyItemNeedsRemoteReview(id))
            }
            let block = BlockIndex(count: session.blocks.count)
            return .success([.questionnaireStarted(QuestionnaireStarted(
                t: t, block: block,
                instrument: InstrumentRef(id: id, version: instrument.version, locale: instrument.locale)
            ))])

        case .respond(let itemID, let value, let t):
            guard case .answering(let open) = phase else { return .failure(.noOpenBlock) }
            guard let item = rules.instruments.instrument(open.instrument)?.item(itemID) else {
                return .failure(.unknownItem(itemID))
            }
            if let value, !item.options.contains(where: { $0.value == value }) {
                return .failure(.invalidValue(itemID, value))
            }
            var events: [Event] = [.response(Response(t: t, block: open.block, item: itemID, value: value))]
            if item.safety, value.map({ $0 >= 1 }) ?? true {
                events.append(.safetyFlagged(SafetyFlagged(t: t, block: open.block, item: itemID, value: value)))
                if opened.administration == .selfAdministered {
                    guard let list = rules.crisisList else { return .failure(.noCrisisList) }
                    events.append(.safetyShown(SafetyShown(t: t, what: .crisisList, listVersion: list.version)))
                }
            }
            return .success(events)

        case .endQuestionnaire(let reason, let t):
            guard case .answering(let open) = phase else { return .failure(.noOpenBlock) }
            return .success([.questionnaireEnded(QuestionnaireEnded(t: t, block: open.block, reason: reason))])

        case .show(.crisisList, let t):
            guard let list = rules.crisisList else { return .failure(.noCrisisList) }
            return .success([.safetyShown(SafetyShown(t: t, what: .crisisList, listVersion: list.version))])

        case .show(.operatorAlert, let t):
            guard opened.administration == .supervised else { return .failure(.noOperator) }
            return .success([.safetyShown(SafetyShown(t: t, what: .operatorAlert, listVersion: nil))])

        case .recordAction(let code, let t):
            guard opened.administration == .supervised else { return .failure(.noOperator) }
            guard rules.actionCodes.contains(code) else { return .failure(.unknownActionCode(code)) }
            guard session.safety.unactionedFlags > 0 else { return .failure(.noUnactionedFlag) }
            return .success([.safetyAction(SafetyAction(t: t, code: code, role: .operator))])

        case .mark(let trial, let code, let t):
            guard trial.rawValue < session.trials.count else { return .failure(.unknownTrial(trial)) }
            return .success([.mark(Mark(t: t, trial: trial, code: code))])

        case .close(let t):
            if let busy = busyRejection { return .failure(busy) }
            if opened.administration == .supervised, session.safety.unactionedFlags > 0 {
                return .failure(.unactionedFlag)
            }
            if session.safety.crisisListDue { return .failure(.crisisListNotShown) }
            return .success([.sessionClosed(SessionClosed(t: t, counts: session.counts))])
        }
    }

    private var busyRejection: Rejection? {
        switch phase {
        case .drawing: .trialOpen
        case .answering: .blockOpen
        case .ready, .suspended: nil
        case .closed: .sessionClosed
        }
    }

    /// The `recovered` line for a relaunch that adopts this open journal, or nil when there is nothing to record:
    /// the session is closed, or it is already suspended in this boot and no bytes were removed.
    public func recovery(t: Double, at: WallTime, boot: BootID, truncatedBytes: Int) -> Event? {
        switch phase {
        case .closed:
            return nil
        case .suspended where boot == session.boot && truncatedBytes == 0:
            return nil
        default:
            let trial: TrialIndex? = if case .drawing(let open) = phase { open.index } else { nil }
            let block: BlockIndex? = if case .answering(let open) = phase { open.block } else { nil }
            return .recovered(Recovered(t: t, at: at, boot: boot, truncatedBytes: truncatedBytes,
                                        interrupted: trial, interruptedBlock: block))
        }
    }
}

// MARK: - Summary

/// The shared test oracle (docs/DESIGN.md section 3.5). It holds counts only, never a questionnaire score.
public struct FoldSummary: Hashable, Sendable {
    public struct Trial: Hashable, Sendable {
        public let index: Int
        public let task: String
        public let attempt: Int
        public let practice: Bool
        public let began: Bool
        public let outcome: TrialOutcome
        public let contacts: Int
        public let samples: Int
        public let corrections: Int
        /// Samples whose pending bits never all cleared.
        public let unresolved: Int
    }

    public struct Block: Hashable, Sendable {
        public let block: Int
        public let instrument: String
        public let answered: Int
        public let skipped: Int
        public let outcome: BlockOutcome
    }

    public let sessionID: String
    public let purpose: Purpose
    public let administration: Administration
    public let state: SessionState.Status
    public let counts: KindCounts
    public let trials: [Trial]
    public let questionnaires: [Block]
    public let safety: SafetyRecord
    public let unmatchedCorrections: Int
    public let truncatedBytes: Int

    var json: JSON {
        .object([
            ("sessionID", .string(sessionID)),
            ("purpose", .string(purpose.rawValue)),
            ("administration", .string(administration.rawValue)),
            ("state", .string(state.rawValue)),
            ("counts", counts.json()),
            ("trials", .array(trials.map { t in
                .object([
                    ("index", .int(t.index)), ("task", .string(t.task)), ("attempt", .int(t.attempt)),
                    ("practice", .bool(t.practice)), ("began", .bool(t.began)), ("outcome", .string(t.outcome.rawValue)),
                    ("contacts", .int(t.contacts)), ("samples", .int(t.samples)), ("corrections", .int(t.corrections)),
                    ("unresolved", .int(t.unresolved)),
                ])
            })),
            ("questionnaires", .array(questionnaires.map { b in
                .object([
                    ("block", .int(b.block)), ("instrument", .string(b.instrument)), ("answered", .int(b.answered)),
                    ("skipped", .int(b.skipped)), ("outcome", .string(b.outcome.rawValue)),
                ])
            })),
            ("safety", .object([
                ("flags", .int(safety.flags)), ("crisisListShown", .int(safety.crisisListShown)),
                ("operatorAlerts", .int(safety.operatorAlerts)), ("actions", .int(safety.actions)),
            ])),
            ("unmatchedCorrections", .int(unmatchedCorrections)),
            ("truncatedBytes", .int(truncatedBytes)),
        ])
    }

    /// The summary as JSON text, indented like the `.fold.json` files.
    public func jsonData() -> Data { json.prettyData() }
}

extension SessionState {
    public var summary: FoldSummary {
        var unresolved = [Int](repeating: 0, count: session.trials.count)
        for estimate in session.pending.values where !estimate.remaining.isEmpty {
            unresolved[estimate.trial.rawValue] += estimate.samples
        }
        return FoldSummary(
            sessionID: session.opened.sessionID.description,
            purpose: session.opened.purpose,
            administration: session.opened.administration,
            state: status,
            counts: session.counts,
            trials: session.trials.map { t in
                FoldSummary.Trial(index: t.index.rawValue, task: t.task.rawValue, attempt: t.attempt, practice: t.practice,
                                  began: t.began, outcome: t.outcome, contacts: t.contacts, samples: t.samples,
                                  corrections: t.corrections, unresolved: unresolved[t.index.rawValue])
            },
            questionnaires: session.blocks.map { b in
                FoldSummary.Block(block: b.block.rawValue, instrument: b.instrument.rawValue, answered: b.answered,
                                  skipped: b.skipped, outcome: b.outcome)
            },
            safety: session.safety,
            unmatchedCorrections: session.unmatchedCorrections,
            truncatedBytes: session.truncatedBytes
        )
    }
}
