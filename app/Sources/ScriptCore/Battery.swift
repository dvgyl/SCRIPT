import Foundation

/// The task battery from `contract/battery.json`. Geometry is in millimetres, origin top-left, y down.
public struct Battery: Hashable, Sendable {
    public let id: String
    public let version: String
    public let tasks: [TaskSpec]
    /// SHA-256 of the file bytes, recorded in `sessionOpened.battery`.
    public let sha256: SHA256Digest

    /// The fixed task set of a qualification session (docs/DESIGN.md section 16), as in `scriptkin.battery`.
    public static let qualificationTasks: Set<TaskID> = ["qual-reference", "qual-hold", "qual-line"]

    public enum LoadError: Error, Hashable, Sendable {
        case decode(DecodeError)
        case duplicateTask(TaskID)
    }

    public init(data: Data) throws(LoadError) {
        do throws(DecodeError) {
            let f = try Fields(try JSON.decodeDocument(data), path: "")
            id = try f.string("id")
            version = try f.string("version")
            var tasks: [TaskSpec] = []
            for (n, item) in try f.array("tasks").enumerated() {
                tasks.append(try TaskSpec(item, path: "tasks[\(n)]"))
            }
            self.tasks = tasks
        } catch {
            throw .decode(error)
        }
        var seen = Set<TaskID>()
        for task in tasks where !seen.insert(task.id).inserted { throw .duplicateTask(task.id) }
        sha256 = SHA256Digest(of: data)
    }

    public func task(_ id: TaskID) -> TaskSpec? { tasks.first { $0.id == id } }

    public var reference: BatteryRef { BatteryRef(id: id, version: version, sha256: sha256) }
}

public enum TaskMode: String, Hashable, Sendable { case trace, copy, free }

public struct PointMM: Hashable, Sendable {
    public var x: Double
    public var y: Double
}

/// The full task specification. `trialStarted` embeds `source`, the exact JSON from the battery, so each journal
/// is self-contained and repeats the battery's numbers as written.
public struct TaskSpec: Hashable, Sendable {
    public let id: TaskID
    public let mode: TaskMode
    public let instruction: String
    /// Nil for a free task, which shows no stimulus.
    public let stimulus: Stimulus?
    public let completion: Completion
    let source: JSON

    init(_ json: JSON, path: String) throws(DecodeError) {
        let f = try Fields(json, path: path)
        id = try f.text("id", TaskID.init(_:))
        mode = try f.enumeration("mode")
        instruction = try f.string("instruction")
        guard !instruction.isEmpty else { throw .invalid(f.keyPath("instruction")) }
        stimulus = f.isNull("stimulus") ? nil : try Stimulus(try f.object("stimulus"))
        completion = try Completion(try f.object("completion"))
        source = json
    }
}

public enum Stimulus: Hashable, Sendable {
    case paths(frameMM: PointMM, strokeMM: Double, start: PointMM?, paths: [[PointMM]])
    case text(frameMM: PointMM, text: String, fontSizeMM: Double)
}

public enum Completion: Hashable, Sendable {
    /// The participant taps Done. The time limit ends the trial after `maxSeconds`.
    case participantDone(maxSeconds: Double)
    /// The trial runs for exactly `seconds`.
    case timed(seconds: Double)

    /// The uptime at which the trial ends with `timeLimit` if nothing ends it before.
    public func deadline(startedAt t: Double) -> Double {
        switch self {
        case .participantDone(let maxSeconds): t + maxSeconds
        case .timed(let seconds): t + seconds
        }
    }
}

// MARK: - JSON

extension Stimulus {
    init(_ f: Fields) throws(DecodeError) {
        let frame = try PointMM(try f.read("frameMM") { $0 }, path: f.keyPath("frameMM"))
        switch try f.string("kind") {
        case "paths":
            var paths: [[PointMM]] = []
            for (n, path) in try f.array("paths").enumerated() {
                guard case .array(let points) = path, points.count >= 2 else { throw .invalid(f.keyPath("paths[\(n)]")) }
                var line: [PointMM] = []
                line.reserveCapacity(points.count)
                for point in points { line.append(try PointMM(point, path: f.keyPath("paths[\(n)]"))) }
                paths.append(line)
            }
            guard !paths.isEmpty else { throw .invalid(f.keyPath("paths")) }
            let start = f.has("start") ? try PointMM(try f.read("start") { $0 }, path: f.keyPath("start")) : nil
            self = .paths(frameMM: frame, strokeMM: try f.positive("strokeMM"), start: start, paths: paths)
        case "text":
            let text = try f.string("text")
            guard !text.isEmpty else { throw .invalid(f.keyPath("text")) }
            self = .text(frameMM: frame, text: text, fontSizeMM: try f.positive("fontSizeMM"))
        default:
            throw .invalid(f.keyPath("kind"))
        }
    }
}

extension Completion {
    init(_ f: Fields) throws(DecodeError) {
        switch try f.string("kind") {
        case "participantDone": self = .participantDone(maxSeconds: try f.positive("maxSeconds"))
        case "timed": self = .timed(seconds: try f.positive("seconds"))
        default: throw .invalid(f.keyPath("kind"))
        }
    }
}

extension PointMM {
    init(_ json: JSON, path: String) throws(DecodeError) {
        guard case .array(let pair) = json, pair.count == 2, let x = pair[0].doubleValue, let y = pair[1].doubleValue
        else { throw .invalid(path) }
        self.init(x: x, y: y)
    }
}

extension JSON {
    /// Parses a whole file into a JSON value.
    static func decodeDocument(_ data: Data) throws(DecodeError) -> JSON {
        do throws(JSONSyntaxError) {
            return try parse(data)
        } catch {
            throw .syntax(offset: error.offset)
        }
    }
}
