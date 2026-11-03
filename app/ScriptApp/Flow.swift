import Foundation
import ScriptCore

/// The ordered steps of one study or qualification session, from its frozen configuration. The fold says which steps
/// are done, so the same plan drives a live session and a resumed one. A bench session has no plan: its operator picks
/// each run on the run screen until End session (`Stage.bench`).
struct SessionPlan: Hashable, Sendable {
    struct TaskStep: Hashable, Sendable {
        let task: TaskID
        let practice: Bool
        /// The maximum number of attempts. A trial that a crash or the background interrupts is repeated until then.
        let attempts: Int
        /// The 1-based position of the task in the configured order.
        let position: Int
        let hand: Hand
        /// The neutral step text: nil for practice, else the number among the measured tasks and their count.
        let number: Int?
        let count: Int
    }

    enum Step: Hashable, Sendable {
        case task(TaskStep)
        case block(InstrumentID)
    }

    /// A block that an interruption ends is given once more, so the participant always reaches its safety item.
    static let blockAdministrations = 2

    let steps: [Step]

    init(steps: [Step]) { self.steps = steps }

    /// Blocks before the tasks, the tasks, and blocks after the tasks, for the session's administration and age band.
    init(configuration: StudyConfiguration, opened: SessionOpened, dominant: Hand) {
        if opened.purpose == .qualification {
            let ids: [TaskID] = ["qual-reference", "qual-hold", "qual-line"]
            steps = ids.enumerated().map { n, id in
                .task(TaskStep(task: id, practice: false, attempts: 2, position: n + 1, hand: dominant, number: n + 1,
                               count: ids.count))
            }
            return
        }
        let person = opened.person
        let blocks = configuration.questionnaires.filter {
            $0.administration == person?.administration && $0.ageBand == person?.ageBand
        }
        func blockSteps(_ position: StudyConfiguration.BlockPosition) -> [Step] {
            blocks.filter { $0.position == position }.flatMap(\.instruments).map(Step.block)
        }
        let measured = configuration.tasks.filter { !$0.practice }.count
        var number = 0
        var tasks: [Step] = []
        for (n, entry) in configuration.tasks.enumerated() {
            if !entry.practice { number += 1 }
            for hand in Self.hands(entry.hands, dominant: dominant) {
                tasks.append(.task(TaskStep(task: entry.task, practice: entry.practice, attempts: entry.attempts,
                                            position: n + 1, hand: hand, number: entry.practice ? nil : number,
                                            count: measured)))
            }
        }
        steps = blockSteps(.beforeTasks) + tasks + blockSteps(.afterTasks)
    }

    private static func hands(_ hands: StudyConfiguration.Hands, dominant: Hand) -> [Hand] {
        let other: Hand = switch dominant {
        case .left: .right
        case .right: .left
        case .unknown: .unknown
        }
        return switch hands {
        case .dominant: [dominant]
        case .left: [.left]
        case .right: [.right]
        case .both: [dominant, other]
        }
    }

    enum Next: Hashable, Sendable {
        /// A trial to start, or the open trial when `open` is true.
        case task(TaskStep, open: Bool)
        /// A block to start, or the open block when `open` is true.
        case block(InstrumentID, open: Bool)
        case done
    }

    /// The first step that the session has not finished. Trials and blocks are matched to steps in order.
    func next(_ session: Session) -> Next {
        var trials = session.trials[...]
        var blocks = session.blocks[...]
        for step in steps {
            switch step {
            case .task(let task):
                var tries = 0
                var finished = false
                while !finished, let record = trials.first, record.task == task.task {
                    if record.outcome == .open { return .task(task, open: true) }
                    trials.removeFirst()
                    tries += 1
                    finished = record.outcome.isFinal
                }
                if finished || tries >= task.attempts { continue }
                return .task(task, open: false)
            case .block(let id):
                var tries = 0
                var finished = false
                while !finished, let record = blocks.first, record.instrument == id {
                    if record.outcome == .open { return .block(id, open: true) }
                    blocks.removeFirst()
                    tries += 1
                    finished = record.outcome != .interrupted
                }
                if finished || tries >= Self.blockAdministrations { continue }
                return .block(id, open: false)
            }
        }
        return .done
    }

    /// True when a block of this plan has a safety item, so a self-administered session shows the monitoring
    /// disclosure before its first block.
    func hasSafetyItem(in instruments: InstrumentSet) -> Bool {
        steps.contains { step in
            if case .block(let id) = step { instruments.instrument(id)?.hasSafetyItem ?? false } else { false }
        }
    }
}

extension TrialOutcome {
    /// An outcome that ends the step. An interruption or a technical failure lets the participant try again.
    var isFinal: Bool {
        switch self {
        case .completed, .timeLimit, .declined, .unable, .aborted: true
        case .interrupted, .technicalFailure, .open: false
        }
    }
}

// MARK: - Bench

extension BenchPrograms {
    /// The programs in the order of the run screen: the main programs, then the fault programs, each by identifier.
    var listed: [BenchProgram] {
        programs.values.sorted { a, b in
            if (a.fault == nil) != (b.fault == nil) { return a.fault == nil }
            return a.id.rawValue.localizedStandardCompare(b.id.rawValue) == .orderedAscending
        }
    }
}

extension BenchProgram {
    /// The marks that the operator can add to a run. A fault run is never excluded, because the quality gate needs
    /// every fault run as an ordinary trial (bench protocol section 5.3).
    var markCodes: [MarkCode] {
        fault == nil ? [.disturbance, .deviceProblem, .exclude] : [.disturbance, .deviceProblem]
    }
}

// MARK: - Stimulus

enum StimulusLayout {
    /// The space above a copy stimulus, in points. The participant copies below it.
    static let copyMargin = 32.0

    /// The top-left of the stimulus frame in the capture view. A copy stimulus sits at the top, so the space below
    /// stays free; a trace stimulus sits in the middle. Without a scale, the stimulus cannot be drawn to size.
    static func origin(for spec: TaskSpec, in view: ViewSize, mmPerPoint: Double?) -> Point {
        guard let mmPerPoint, let frame = spec.stimulus?.frameMM else {
            return Point(x: 0, y: 0)
        }
        let width = frame.x / mmPerPoint
        let height = frame.y / mmPerPoint
        let x = ((view.width - width) / 2).rounded()
        let y = spec.mode == .copy ? copyMargin : ((view.height - height) / 2).rounded()
        return Point(x: x, y: y)
    }
}

extension Stimulus {
    var frameMM: PointMM {
        switch self {
        case .paths(let frame, _, _, _), .text(let frame, _, _): frame
        }
    }
}

// MARK: - Digest

/// What the operator screens show about a session, folded from the same entries as the journal. It holds times,
/// counts, and flag positions, never a response value or a score.
struct JournalDigest: Sendable {
    struct Trial: Sendable {
        let task: TaskID
        let attempt: Int
        let practice: Bool
        let subject: TrialSubject
        let started: Double
        var began: Double?
        var ended: Double?
        var reason: TrialEndReason?
        var samples = 0
        /// Times between consecutive samples of one contact.
        var intervals: [Double] = []
        var force = false
        var tilt = false
        var roll = false
        var hover = 0
        var marks: [MarkCode] = []

        /// The hand of a participant trial. A bench run has none.
        var hand: Hand? {
            if case .participant(let facts) = subject { facts.hand } else { nil }
        }

        /// The run facts of a bench trial.
        var run: BenchRun? {
            if case .bench(let run) = subject { run } else { nil }
        }

        /// The run id of a bench trial, else the task.
        var label: String { run.map { $0.clip?.rawValue ?? $0.program.rawValue } ?? task.rawValue }
    }

    struct Flag: Hashable, Sendable {
        let block: BlockIndex
        let instrument: InstrumentID
        let item: ItemID
    }

    private(set) var trials: [Trial] = []
    private(set) var flags: [Flag] = []
    private(set) var actions = 0
    private var blockInstruments: [BlockIndex: InstrumentID] = [:]
    private var lastSample: [ContactID: Double] = [:]

    init() {}

    init(_ entries: some Sequence<Entry>) {
        for entry in entries { add(entry) }
    }

    mutating func add(_ entries: some Sequence<Entry>) {
        for entry in entries { add(entry) }
    }

    mutating func add(_ entry: Entry) {
        switch entry.event {
        case .trialStarted(let e):
            trials.append(Trial(task: e.trial.task, attempt: e.trial.attempt, practice: e.practice, subject: e.subject,
                                started: e.t))
        case .trialBegan(let e):
            trials[e.trial.rawValue].began = e.t
        case .sample(let e):
            guard !trials.isEmpty else { return }
            let last = trials.count - 1
            trials[last].samples += 1
            if let previous = lastSample[e.contact] { trials[last].intervals.append(e.t - previous) }
            lastSample[e.contact] = e.t
            if e.force != nil { trials[last].force = true }
            if e.altitude != nil || e.azimuth != nil { trials[last].tilt = true }
            if e.roll != nil { trials[last].roll = true }
        case .contactEnded(let e):
            lastSample[e.contact] = nil
        case .hover:
            if !trials.isEmpty { trials[trials.count - 1].hover += 1 }
        case .trialEnded(let e):
            trials[e.trial.rawValue].ended = e.t
            trials[e.trial.rawValue].reason = e.reason
        case .mark(let e):
            trials[e.trial.rawValue].marks.append(e.code)
        case .questionnaireStarted(let e):
            blockInstruments[e.block] = e.instrument.id
        case .safetyFlagged(let e):
            if let instrument = blockInstruments[e.block] {
                flags.append(Flag(block: e.block, instrument: instrument, item: e.item))
            }
        case .safetyAction:
            actions += 1
        default:
            break
        }
    }
}

/// The timing of the recorded samples, as the qualification report defines it: intervals inside contacts, and the
/// share longer than 30 ms.
struct SampleTiming: Hashable, Sendable {
    static let gap = 0.030

    let intervals: Int
    let median: Double
    let gapShare: Double

    init?(_ intervals: [Double]) {
        let sorted = intervals.filter(\.isFinite).sorted()
        guard !sorted.isEmpty else { return nil }
        let middle = sorted.count / 2
        median = sorted.count.isMultiple(of: 2) ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle]
        gapShare = Double(sorted.count { $0 > Self.gap }) / Double(sorted.count)
        self.intervals = sorted.count
    }
}
