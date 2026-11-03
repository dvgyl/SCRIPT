import Foundation

/// The task battery from `contract/battery.json`. Geometry is in millimetres, origin top-left, y down.
public struct Battery: Hashable, Sendable {
    public let id: String
    public let version: String
    public let tasks: [TaskSpec]
    /// SHA-256 of the file bytes, recorded in `sessionOpened.battery`.
    public let sha256: String

    public enum DecodeError: Error, Equatable, Sendable {
        case syntax(Int)
        case field(LineCodec.DecodeError)
        case duplicateTask(TaskID)
    }

    public init(data: Data) throws(DecodeError) {
        let json: JSON
        do throws(JSONSyntaxError) {
            json = try JSON.parse(data)
        } catch {
            throw .syntax(error.offset)
        }
        do throws(LineCodec.DecodeError) {
            let f = try Fields(json, path: "")
            id = try f.string("id")
            version = try f.string("version")
            var tasks: [TaskSpec] = []
            for (n, item) in try f.array("tasks").enumerated() {
                tasks.append(try TaskSpec(try Fields(item, path: "tasks[\(n)]")))
            }
            self.tasks = tasks
        } catch {
            throw .field(error)
        }
        var seen = Set<TaskID>()
        for task in tasks where !seen.insert(task.id).inserted { throw .duplicateTask(task.id) }
        sha256 = SHA256.hex(data)
    }

    public func task(_ id: TaskID) -> TaskSpec? { tasks.first { $0.id == id } }

    public var reference: BatteryRef { BatteryRef(id: id, version: version, sha256: sha256) }
}

public enum TaskMode: String, Hashable, Sendable { case trace, copy, free }

public struct PointMM: Hashable, Sendable {
    public var x: Double
    public var y: Double
}

/// The full task specification. `trialStarted` embeds it, so each journal is self-contained.
public struct TaskSpec: Hashable, Sendable {
    public var id: TaskID
    public var mode: TaskMode
    public var instruction: String
    /// Nil for a free task, which shows no stimulus.
    public var stimulus: Stimulus?
    public var completion: Completion
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

extension TaskSpec {
    init(_ f: Fields) throws(LineCodec.DecodeError) {
        id = TaskID(try f.string("id"))
        mode = try f.enumeration("mode")
        instruction = try f.string("instruction")
        let stimulus = try f.read("stimulus") { $0 }
        if case .null = stimulus {
            self.stimulus = nil
        } else {
            self.stimulus = try Stimulus(try f.object("stimulus"))
        }
        completion = try Completion(try f.object("completion"))
    }

    func json() throws(LineCodec.EncodeError) -> JSON {
        var o = ObjectBuilder()
        o.add("id", id.rawValue)
        o.add("mode", mode.rawValue)
        o.add("instruction", instruction)
        o.add("stimulus", try stimulus?.json() ?? .null)
        o.add("completion", try completion.json())
        return o.json
    }
}

extension Stimulus {
    init(_ f: Fields) throws(LineCodec.DecodeError) {
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
            self = .text(frameMM: frame, text: try f.string("text"), fontSizeMM: try f.positive("fontSizeMM"))
        default:
            throw .invalid(f.keyPath("kind"))
        }
    }

    func json() throws(LineCodec.EncodeError) -> JSON {
        var o = ObjectBuilder()
        switch self {
        case .paths(let frame, let stroke, let start, let paths):
            o.add("kind", "paths")
            o.add("frameMM", try frame.json())
            try o.add("strokeMM", stroke)
            if let start { o.add("start", try start.json()) }
            var lines: [JSON] = []
            for path in paths {
                var points: [JSON] = []
                for point in path { points.append(try point.json()) }
                lines.append(.array(points))
            }
            o.add("paths", .array(lines))
        case .text(let frame, let text, let size):
            o.add("kind", "text")
            o.add("frameMM", try frame.json())
            o.add("text", text)
            try o.add("fontSizeMM", size)
        }
        return o.json
    }
}

extension Completion {
    init(_ f: Fields) throws(LineCodec.DecodeError) {
        switch try f.string("kind") {
        case "participantDone": self = .participantDone(maxSeconds: try f.positive("maxSeconds"))
        case "timed": self = .timed(seconds: try f.positive("seconds"))
        default: throw .invalid(f.keyPath("kind"))
        }
    }

    func json() throws(LineCodec.EncodeError) -> JSON {
        var o = ObjectBuilder()
        switch self {
        case .participantDone(let maxSeconds):
            o.add("kind", "participantDone")
            try o.add("maxSeconds", maxSeconds)
        case .timed(let seconds):
            o.add("kind", "timed")
            try o.add("seconds", seconds)
        }
        return o.json
    }
}

extension PointMM {
    init(_ json: JSON, path: String) throws(LineCodec.DecodeError) {
        guard case .array(let pair) = json, pair.count == 2, let x = pair[0].doubleValue, let y = pair[1].doubleValue
        else { throw .invalid(path) }
        self.init(x: x, y: y)
    }

    func json() throws(LineCodec.EncodeError) -> JSON {
        guard let x = JSON.double(x), let y = JSON.double(y) else { throw .nonFinite("point") }
        return .array([x, y])
    }
}

extension Fields {
    func positive(_ key: String) throws(LineCodec.DecodeError) -> Double {
        try read(key) { $0.doubleValue.flatMap { $0 > 0 ? $0 : nil } }
    }
}
