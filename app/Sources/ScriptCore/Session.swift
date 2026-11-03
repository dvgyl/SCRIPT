import Foundation

/// The app's session state: a pure fold over the journal lines. Live operation and recovery share `apply`.
public struct SessionState: Hashable, Sendable {
    public enum Phase: Hashable, Sendable {
        case ready
        case drawing(OpenTrial)
        /// The last line is `recovered`.
        case suspended
        case closed
    }

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
        case .suspended: .suspended
        case .closed: .closed
        }
    }

    public enum Status: String, Hashable, Sendable { case ready, drawing, suspended, closed }
}

public struct OpenTrial: Hashable, Sendable {
    public let index: TrialIndex
    public let task: TaskID
    public let started: Double
    /// The uptime at which the time limit ends the trial.
    public let deadline: Double
}

public enum TrialOutcome: String, Hashable, Sendable {
    case completed, timeLimit, aborted, interrupted, open

    init(_ reason: TrialEndReason) {
        switch reason {
        case .participantDone: self = .completed
        case .timeLimit: self = .timeLimit
        case .operatorAbort: self = .aborted
        case .backgrounded: self = .interrupted
        }
    }
}

public struct TrialRecord: Hashable, Sendable {
    public let index: TrialIndex
    public let task: TaskID
    public let attempt: Int
    public internal(set) var outcome: TrialOutcome = .open
    public internal(set) var contacts = 0
    public internal(set) var samples = 0
    /// Matched corrections, counted in the trial of their sample.
    public internal(set) var corrections = 0
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
    public internal(set) var nextContact = ContactID.first
    public internal(set) var unmatchedCorrections = 0
    public internal(set) var truncatedBytes = 0
    var openContacts: Set<ContactID> = []
    var contactTrial: [ContactID: TrialIndex] = [:]
    /// Every `(contact, update)` that a sample left pending, with the bits still expected.
    var pending: [PendingKey: PendingEstimate] = [:]

    init(opened: SessionOpened) {
        self.opened = opened
        boot = opened.boot
    }

    struct PendingKey: Hashable, Sendable {
        let contact: ContactID
        let update: UpdateIndex
    }

    struct PendingEstimate: Hashable, Sendable {
        let trial: TrialIndex
        var expect: TouchProperties
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
        case taskMismatch
        case contactReused(ContactID)
        case contactNotDown(ContactID)
        case contactsDown
        case wrongTrial(TrialIndex)
        case unknownTrial(TrialIndex)
        case interruptedMismatch
        case countsMismatch
    }
}

// MARK: - Apply

extension SessionState {
    /// Applies one line. A line that breaks an integrity rule throws and leaves the state unchanged.
    public mutating func apply(_ entry: Entry) throws(FoldError) {
        func fail(_ violation: FoldError.Violation) -> FoldError { FoldError(line: entry.seq, violation: violation) }
        let kind = entry.event.kind
        guard entry.seq == session.lines else { throw fail(.seq(expected: session.lines)) }
        guard phase != .closed else { throw fail(.notAllowed(kind, in: status)) }

        switch entry.event {
        case .sessionOpened:
            throw fail(.notAllowed(kind, in: status))

        case .trialStarted(let e):
            guard phase == .ready || phase == .suspended else { throw fail(.notAllowed(kind, in: status)) }
            let expected = TrialIndex(integerLiteral: session.trials.count)
            guard e.trial.index == expected else { throw fail(.trialIndex(expected: expected)) }
            guard e.trial.task == e.spec.id else { throw fail(.taskMismatch) }
            session.trials.append(TrialRecord(index: e.trial.index, task: e.trial.task, attempt: e.trial.attempt))
            phase = .drawing(OpenTrial(index: e.trial.index, task: e.trial.task, started: e.t,
                                       deadline: e.spec.completion.deadline(startedAt: e.t)))

        case .contactBegan(let e):
            guard case .drawing(let open) = phase else { throw fail(.notAllowed(kind, in: status)) }
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
            if let p = e.pending {
                session.pending[Session.PendingKey(contact: e.contact, update: p.update)] =
                    Session.PendingEstimate(trial: trial, expect: p.expect)
            }

        case .correction(let e):
            let key = e.contact.map { Session.PendingKey(contact: $0, update: e.update) }
            if let key, let estimate = session.pending[key] {
                session.pending[key]?.expect = e.expect
                session.trials[estimate.trial.rawValue].corrections += 1
            } else {
                session.unmatchedCorrections += 1
            }

        case .contactEnded(let e):
            guard session.openContacts.contains(e.contact) else { throw fail(.contactNotDown(e.contact)) }
            session.openContacts.remove(e.contact)

        case .hover, .finger:
            break

        case .trialEnded(let e):
            guard case .drawing(let open) = phase else { throw fail(.notAllowed(kind, in: status)) }
            guard e.trial == open.index else { throw fail(.wrongTrial(e.trial)) }
            guard session.openContacts.isEmpty else { throw fail(.contactsDown) }
            session.trials[open.index.rawValue].outcome = TrialOutcome(e.reason)
            phase = .ready

        case .mark(let e):
            guard e.trial.rawValue < session.trials.count else { throw fail(.unknownTrial(e.trial)) }

        case .recovered(let e):
            let open: TrialIndex? = if case .drawing(let trial) = phase { trial.index } else { nil }
            guard e.interrupted == open else { throw fail(.interruptedMismatch) }
            if let open { session.trials[open.rawValue].outcome = .interrupted }
            session.openContacts.removeAll()
            session.truncatedBytes += e.truncatedBytes
            session.boot = e.boot
            phase = .suspended

        case .sessionClosed(let e):
            guard phase == .ready || phase == .suspended else { throw fail(.notAllowed(kind, in: status)) }
            guard e.counts == session.counts else { throw fail(.countsMismatch) }
            phase = .closed
        }
        session.counts[kind] += 1
        session.lines += 1
    }
}

// MARK: - Decide

/// An operator or app intent. Ink comes from `InkRecorder`, not from a command.
public enum Command: Sendable {
    case startTrial(TaskID, battery: Battery, t: Double, view: ViewSize, stimulusOrigin: Point)
    case endTrial(t: Double, reason: TrialEndReason)
    case mark(t: Double, trial: TrialIndex, code: MarkCode)
    /// A relaunch adopts a journal that has no `sessionClosed` line.
    case recover(t: Double, at: WallTime, boot: BootID, truncatedBytes: Int)
    case close(t: Double)
}

public enum Rejection: Error, Hashable, Sendable {
    case sessionClosed
    case trialOpen
    case noOpenTrial
    case contactsDown
    case otherBattery
    case unknownTask(TaskID)
    case unknownTrial(TrialIndex)
}

extension SessionState {
    /// Turns an intent into the events to append, or refuses it. It never changes the state.
    public func decide(_ command: Command) -> Result<[Event], Rejection> {
        if phase == .closed { return .failure(.sessionClosed) }
        switch command {
        case .startTrial(let task, let battery, let t, let view, let origin):
            if case .drawing = phase { return .failure(.trialOpen) }
            guard battery.sha256 == session.opened.battery.sha256 else { return .failure(.otherBattery) }
            guard let spec = battery.task(task) else { return .failure(.unknownTask(task)) }
            let attempt = session.trials.count { $0.task == task } + 1
            let trial = TrialRef(index: TrialIndex(integerLiteral: session.trials.count), task: task, attempt: attempt)
            return .success([.trialStarted(TrialStarted(t: t, trial: trial, spec: spec, view: view,
                                                        stimulusOrigin: origin))])

        case .endTrial(let t, let reason):
            guard case .drawing(let open) = phase else { return .failure(.noOpenTrial) }
            guard session.openContacts.isEmpty else { return .failure(.contactsDown) }
            return .success([.trialEnded(TrialEnded(t: t, trial: open.index, reason: reason))])

        case .mark(let t, let trial, let code):
            guard trial.rawValue < session.trials.count else { return .failure(.unknownTrial(trial)) }
            return .success([.mark(Mark(t: t, trial: trial, code: code))])

        case .recover(let t, let at, let boot, let truncatedBytes):
            let interrupted: TrialIndex? = if case .drawing(let open) = phase { open.index } else { nil }
            if phase == .suspended, boot == session.boot, truncatedBytes == 0 { return .success([]) }
            return .success([.recovered(Recovered(t: t, at: at, boot: boot, truncatedBytes: truncatedBytes,
                                                  interrupted: interrupted))])

        case .close(let t):
            if case .drawing = phase { return .failure(.trialOpen) }
            return .success([.sessionClosed(SessionClosed(t: t, counts: session.counts))])
        }
    }
}

// MARK: - Summary

/// The shared test oracle (docs/DESIGN.md section 3.5). It encodes to the same JSON object as a `.fold.json` file.
public struct FoldSummary: Hashable, Sendable {
    public struct Trial: Hashable, Sendable {
        public let index: Int
        public let task: String
        public let attempt: Int
        public let outcome: TrialOutcome
        public let contacts: Int
        public let samples: Int
        public let corrections: Int
        /// Samples whose pending bits never all cleared.
        public let unresolved: Int
    }

    public let sessionID: String
    public let state: SessionState.Status
    public let counts: KindCounts
    public let trials: [Trial]
    public let unmatchedCorrections: Int
    public let truncatedBytes: Int

    /// The summary as JSON text, indented like the `.fold.json` files.
    public func jsonData() -> Data {
        JSON.object([
            ("sessionID", .string(sessionID)),
            ("state", .string(state.rawValue)),
            ("counts", counts.json()),
            ("trials", .array(trials.map { t in
                .object([
                    ("index", .int(t.index)), ("task", .string(t.task)), ("attempt", .int(t.attempt)),
                    ("outcome", .string(t.outcome.rawValue)), ("contacts", .int(t.contacts)),
                    ("samples", .int(t.samples)), ("corrections", .int(t.corrections)),
                    ("unresolved", .int(t.unresolved)),
                ])
            })),
            ("unmatchedCorrections", .int(unmatchedCorrections)),
            ("truncatedBytes", .int(truncatedBytes)),
        ]).prettyData()
    }
}

extension SessionState {
    public var summary: FoldSummary {
        var unresolved = [Int](repeating: 0, count: session.trials.count)
        for estimate in session.pending.values where !estimate.expect.isEmpty {
            unresolved[estimate.trial.rawValue] += 1
        }
        return FoldSummary(
            sessionID: session.opened.sessionID.description,
            state: status,
            counts: session.counts,
            trials: session.trials.map { t in
                FoldSummary.Trial(index: t.index.rawValue, task: t.task.rawValue, attempt: t.attempt,
                                  outcome: t.outcome, contacts: t.contacts, samples: t.samples,
                                  corrections: t.corrections, unresolved: unresolved[t.index.rawValue])
            },
            unmatchedCorrections: session.unmatchedCorrections,
            truncatedBytes: session.truncatedBytes
        )
    }
}
