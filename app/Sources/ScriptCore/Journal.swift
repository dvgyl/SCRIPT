import Foundation

/// The journal format this build writes and reads. See docs/DESIGN.md section 3.
public enum JournalFormat {
    public static let version = "1.0"

    /// A minor version adds optional keys only, so this build reads every 1.x journal.
    static func isReadable(_ version: String) -> Bool {
        version.split(separator: ".", maxSplits: 1).first == "1"
    }
}

// MARK: - Identifiers

public struct SessionID: Hashable, Sendable, CustomStringConvertible {
    public let uuid: UUID
    public init(_ uuid: UUID = UUID()) { self.uuid = uuid }
    public var description: String { uuid.uuidString }
}

/// The boot-session UUID. Uptime values compare only inside one boot.
public struct BootID: Hashable, Sendable, CustomStringConvertible {
    public let uuid: UUID
    public init(_ uuid: UUID) { self.uuid = uuid }
    public var description: String { uuid.uuidString }
}

/// A Pencil contact. Unique in the session, starting at 1.
public struct ContactID: Hashable, Comparable, Sendable, CustomStringConvertible {
    public let rawValue: Int
    public init?(_ rawValue: Int) {
        guard rawValue >= 1 else { return nil }
        self.rawValue = rawValue
    }
    private init(unchecked rawValue: Int) { self.rawValue = rawValue }
    public static let first = ContactID(unchecked: 1)
    public var next: ContactID { ContactID(unchecked: rawValue + 1) }
    public static func < (a: ContactID, b: ContactID) -> Bool { a.rawValue < b.rawValue }
    public var description: String { String(rawValue) }
}

public struct TrialIndex: Hashable, Comparable, Sendable, ExpressibleByIntegerLiteral, CustomStringConvertible {
    public let rawValue: Int
    public init?(_ rawValue: Int) {
        guard rawValue >= 0 else { return nil }
        self.rawValue = rawValue
    }
    public init(integerLiteral value: Int) {
        precondition(value >= 0, "a trial index is not negative")
        rawValue = value
    }
    public static func < (a: TrialIndex, b: TrialIndex) -> Bool { a.rawValue < b.rawValue }
    public var description: String { String(rawValue) }
}

/// `UITouch.estimationUpdateIndex`.
public struct UpdateIndex: Hashable, Sendable, ExpressibleByIntegerLiteral {
    public let rawValue: Int
    public init(_ rawValue: Int) { self.rawValue = rawValue }
    public init(integerLiteral value: Int) { rawValue = value }
}

/// A participant code that matches `^[A-Z0-9-]{3,16}$`.
public struct ParticipantCode: Hashable, Sendable, CustomStringConvertible {
    public let rawValue: String
    public init?(_ rawValue: String) {
        let allowed = rawValue.utf8.allSatisfy { (0x41...0x5A).contains($0) || (0x30...0x39).contains($0) || $0 == 0x2D }
        guard allowed, (3...16).contains(rawValue.utf8.count) else { return nil }
        self.rawValue = rawValue
    }
    public var description: String { rawValue }
}

/// A task identifier from the battery, for example `spiral-trace`.
public struct TaskID: Hashable, Sendable, ExpressibleByStringLiteral, CustomStringConvertible {
    public let rawValue: String
    public init(_ rawValue: String) { self.rawValue = rawValue }
    public init(stringLiteral value: String) { rawValue = value }
    public var description: String { rawValue }
}

/// An ISO 8601 UTC wall-clock time, kept as the recorded text.
public struct WallTime: Hashable, Sendable {
    public let text: String
    public init(_ date: Date) { text = date.formatted(.iso8601) }
    init(text: String) { self.text = text }
}

// MARK: - Line values

/// The `est` and `expect` bit sets. The raw values equal `UITouch.Properties`.
public struct TouchProperties: OptionSet, Hashable, Sendable {
    public let rawValue: Int
    public init(rawValue: Int) { self.rawValue = rawValue }
    public static let force = TouchProperties(rawValue: 1)
    public static let azimuth = TouchProperties(rawValue: 2)
    public static let altitude = TouchProperties(rawValue: 4)
    public static let location = TouchProperties(rawValue: 8)
    public static let roll = TouchProperties(rawValue: 16)
}

public struct Point: Hashable, Sendable {
    public var x: Double
    public var y: Double
    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }
}

public struct ViewSize: Hashable, Sendable {
    public var width: Double
    public var height: Double
    public init(width: Double, height: Double) {
        self.width = width
        self.height = height
    }
}

public enum PenPhase: String, Sendable { case began, moved, stationary, ended, cancelled }
public enum FingerPhase: String, Sendable { case began, ended, cancelled }
public enum InputKind: String, Sendable { case pencil, directTouchAsPen }
public enum HoverClock: String, Sendable { case callback }
public enum TrialEndReason: String, Sendable { case participantDone, timeLimit, operatorAbort, backgrounded }
public enum MarkCode: String, Sendable { case repeatRequested, disturbance, deviceProblem, exclude }

public struct AppInfo: Hashable, Sendable {
    public var version: String
    public var build: String
    public var sourceRevision: String
    public init(version: String, build: String, sourceRevision: String) {
        self.version = version
        self.build = build
        self.sourceRevision = sourceRevision
    }
}

public struct DeviceInfo: Hashable, Sendable {
    public var model: String
    public var os: String
    public var nativeScale: Double
    public init(model: String, os: String, nativeScale: Double) {
        self.model = model
        self.os = os
        self.nativeScale = nativeScale
    }
}

public struct BatteryRef: Hashable, Sendable {
    public var id: String
    public var version: String
    public var sha256: String
    public init(id: String, version: String, sha256: String) {
        self.id = id
        self.version = version
        self.sha256 = sha256
    }
}

// MARK: - Events

public struct SessionOpened: Hashable, Sendable {
    public internal(set) var format = JournalFormat.version
    public var sessionID: SessionID
    public var participant: ParticipantCode
    public var openedAt: WallTime
    public var openedUptime: Double
    public var boot: BootID
    public var app: AppInfo
    public var device: DeviceInfo
    public var scale: MillimetreScale
    public var pencil: PencilModel
    public var channels: ChannelPlan
    public var input: InputKind
    public var battery: BatteryRef

    public init(
        sessionID: SessionID, participant: ParticipantCode, openedAt: WallTime, openedUptime: Double, boot: BootID,
        app: AppInfo, device: DeviceInfo, scale: MillimetreScale, pencil: PencilModel, channels: ChannelPlan,
        input: InputKind, battery: BatteryRef
    ) {
        self.sessionID = sessionID
        self.participant = participant
        self.openedAt = openedAt
        self.openedUptime = openedUptime
        self.boot = boot
        self.app = app
        self.device = device
        self.scale = scale
        self.pencil = pencil
        self.channels = channels
        self.input = input
        self.battery = battery
    }

    /// `<participant>_<first 8 hex digits of the session UUID>`, the export folder name.
    public var exportName: String {
        "\(participant.rawValue)_\(sessionID.uuid.uuidString.prefix(8).lowercased())"
    }
}

public struct TrialRef: Hashable, Sendable {
    public var index: TrialIndex
    public var task: TaskID
    public var attempt: Int
}

public struct TrialStarted: Hashable, Sendable {
    public var t: Double
    public var trial: TrialRef
    public var spec: TaskSpec
    public var view: ViewSize
    public var stimulusOrigin: Point
}

public struct ContactBegan: Hashable, Sendable {
    public var t: Double
    public var contact: ContactID
    public var maxForce: Double?
}

/// Estimated properties that UIKit will update later, keyed by `estimationUpdateIndex`.
public struct Pending: Hashable, Sendable {
    public var expect: TouchProperties
    public var update: UpdateIndex
}

public struct Sample: Hashable, Sendable {
    public var contact: ContactID
    public var t: Double
    public var x: Double
    public var y: Double
    public var force: Double?
    public var altitude: Double?
    public var azimuth: Double?
    public var roll: Double?
    public var phase: PenPhase
    public var est: TouchProperties
    public var pending: Pending?
}

/// A late value for an estimated sample. `contact` is nil when no sample expects this update.
public struct Correction: Hashable, Sendable {
    public var contact: ContactID?
    public var update: UpdateIndex
    public var arrived: Double
    public var location: Point?
    public var force: Double?
    public var altitude: Double?
    public var azimuth: Double?
    public var roll: Double?
    public var expect: TouchProperties
}

public struct ContactEnded: Hashable, Sendable {
    public var t: Double
    public var contact: ContactID
    public var cancelled: Bool
}

public struct Hover: Hashable, Sendable {
    public var t: Double
    public var clock: HoverClock
    public var x: Double
    public var y: Double
    public var z: Double
    public var altitude: Double?
    public var azimuth: Double?
    public var roll: Double?
}

public struct Finger: Hashable, Sendable {
    public var t: Double
    public var phase: FingerPhase
    public var x: Double
    public var y: Double
}

public struct TrialEnded: Hashable, Sendable {
    public var t: Double
    public var trial: TrialIndex
    public var reason: TrialEndReason
}

public struct Mark: Hashable, Sendable {
    public var t: Double
    public var trial: TrialIndex
    public var code: MarkCode
}

public struct Recovered: Hashable, Sendable {
    public var t: Double
    public var at: WallTime
    public var boot: BootID
    public var truncatedBytes: Int
    public var interrupted: TrialIndex?
}

public struct SessionClosed: Hashable, Sendable {
    public var t: Double
    /// The number of lines of each other kind before this line.
    public var counts: KindCounts
}

public enum LineKind: String, CaseIterable, Sendable {
    case sessionOpened, trialStarted, contactBegan, sample, correction, contactEnded
    case hover, finger, trialEnded, mark, recovered, sessionClosed
}

public enum Event: Hashable, Sendable {
    case sessionOpened(SessionOpened)
    case trialStarted(TrialStarted)
    case contactBegan(ContactBegan)
    case sample(Sample)
    case correction(Correction)
    case contactEnded(ContactEnded)
    case hover(Hover)
    case finger(Finger)
    case trialEnded(TrialEnded)
    case mark(Mark)
    case recovered(Recovered)
    case sessionClosed(SessionClosed)

    public var kind: LineKind {
        switch self {
        case .sessionOpened: .sessionOpened
        case .trialStarted: .trialStarted
        case .contactBegan: .contactBegan
        case .sample: .sample
        case .correction: .correction
        case .contactEnded: .contactEnded
        case .hover: .hover
        case .finger: .finger
        case .trialEnded: .trialEnded
        case .mark: .mark
        case .recovered: .recovered
        case .sessionClosed: .sessionClosed
        }
    }
}

/// One journal line: its 0-based index and its event.
public struct Entry: Hashable, Sendable {
    public var seq: Int
    public var event: Event
    public init(seq: Int, event: Event) {
        self.seq = seq
        self.event = event
    }
}

/// A count for each line kind.
public struct KindCounts: Hashable, Sendable {
    private var values = [Int](repeating: 0, count: LineKind.allCases.count)
    public init() {}
    public subscript(kind: LineKind) -> Int {
        get { values[Self.slot[kind]!] }
        set { values[Self.slot[kind]!] = newValue }
    }
    private static let slot = Dictionary(uniqueKeysWithValues: LineKind.allCases.enumerated().map { ($1, $0) })

    func json(excluding excluded: LineKind? = nil) -> JSON {
        .object(LineKind.allCases.filter { $0 != excluded }.map { ($0.rawValue, .int(self[$0])) })
    }
}

// MARK: - Line codec

public enum LineCodec {
    public enum EncodeError: Error, Hashable, Sendable {
        /// JSON cannot hold NaN or an infinity. The value names the key.
        case nonFinite(String)
    }

    public enum DecodeError: Error, Hashable, Sendable {
        /// The text is not one UTF-8 JSON value. The value is the byte offset.
        case syntax(Int)
        case notAnObject
        case unknownKind(String)
        /// A required key is absent. The value is the key path.
        case missing(String)
        /// A value has the wrong type or is out of range. The value is the key path.
        case invalid(String)
    }

    /// One line with `seq` and `kind` first, ended by LF.
    public static func encode(_ entry: Entry) throws(EncodeError) -> Data {
        var line = ObjectBuilder()
        line.add("seq", entry.seq)
        line.add("kind", entry.event.kind.rawValue)
        try entry.event.encodePayload(into: &line)
        var out: [UInt8] = []
        out.reserveCapacity(256)
        line.json.write(into: &out)
        out.append(0x0A)
        return Data(out)
    }

    /// Decodes one line without its LF.
    public static func decode(_ line: Data) throws(DecodeError) -> Entry {
        let result: Result<Entry, DecodeError> = line.withUnsafeBytes { raw in
            Result { () throws(DecodeError) in try decode(raw.bindMemory(to: UInt8.self)) }
        }
        return try result.get()
    }

    static func decode(_ line: UnsafeBufferPointer<UInt8>) throws(DecodeError) -> Entry {
        let json: JSON
        do throws(JSONSyntaxError) {
            json = try JSON.parse(line)
        } catch {
            throw .syntax(error.offset)
        }
        let fields = try Fields(json, path: "")
        let seq = try fields.int("seq")
        guard seq >= 0 else { throw .invalid("seq") }
        let kindText = try fields.string("kind")
        guard let kind = LineKind(rawValue: kindText) else { throw .unknownKind(kindText) }
        return Entry(seq: seq, event: try Event.decode(kind, fields))
    }
}

// MARK: - Event payloads

extension Event {
    func encodePayload(into o: inout ObjectBuilder) throws(LineCodec.EncodeError) {
        switch self {
        case .sessionOpened(let e):
            o.add("format", e.format)
            o.add("sessionID", e.sessionID.description)
            o.add("participant", e.participant.rawValue)
            o.add("openedAt", e.openedAt.text)
            try o.add("openedUptime", e.openedUptime)
            o.add("boot", e.boot.description)
            o.add("app", .object([
                ("version", .string(e.app.version)), ("build", .string(e.app.build)),
                ("sourceRevision", .string(e.app.sourceRevision)),
            ]))
            var device = ObjectBuilder()
            device.add("model", e.device.model)
            device.add("os", e.device.os)
            try device.add("nativeScale", e.device.nativeScale)
            o.add("device", device.json)
            o.add("scale", try e.scale.json())
            o.add("pencil", .object([("declared", .string(e.pencil.rawValue))]))
            o.add("channels", e.channels.json)
            o.add("input", e.input.rawValue)
            o.add("battery", .object([
                ("id", .string(e.battery.id)), ("version", .string(e.battery.version)),
                ("sha256", .string(e.battery.sha256)),
            ]))
        case .trialStarted(let e):
            try o.add("t", e.t)
            o.add("trial", .object([
                ("index", .int(e.trial.index.rawValue)), ("task", .string(e.trial.task.rawValue)),
                ("attempt", .int(e.trial.attempt)),
            ]))
            o.add("spec", try e.spec.json())
            var view = ObjectBuilder()
            try view.add("width", e.view.width)
            try view.add("height", e.view.height)
            o.add("view", view.json)
            var origin = ObjectBuilder()
            try origin.add("x", e.stimulusOrigin.x)
            try origin.add("y", e.stimulusOrigin.y)
            o.add("stimulusOrigin", origin.json)
        case .contactBegan(let e):
            try o.add("t", e.t)
            o.add("contact", e.contact.rawValue)
            try o.add("maxForce", optional: e.maxForce)
        case .sample(let e):
            o.add("contact", e.contact.rawValue)
            try o.add("t", e.t)
            try o.add("x", e.x)
            try o.add("y", e.y)
            try o.add("force", optional: e.force)
            try o.add("altitude", optional: e.altitude)
            try o.add("azimuth", optional: e.azimuth)
            try o.add("roll", optional: e.roll)
            o.add("phase", e.phase.rawValue)
            o.add("est", e.est.rawValue)
            if let pending = e.pending {
                o.add("pending", .object([("expect", .int(pending.expect.rawValue)), ("update", .int(pending.update.rawValue))]))
            }
        case .correction(let e):
            if let contact = e.contact { o.add("contact", contact.rawValue) }
            o.add("update", e.update.rawValue)
            try o.add("arrived", e.arrived)
            try o.add("x", optional: e.location?.x)
            try o.add("y", optional: e.location?.y)
            try o.add("force", optional: e.force)
            try o.add("altitude", optional: e.altitude)
            try o.add("azimuth", optional: e.azimuth)
            try o.add("roll", optional: e.roll)
            o.add("expect", e.expect.rawValue)
        case .contactEnded(let e):
            try o.add("t", e.t)
            o.add("contact", e.contact.rawValue)
            o.add("cancelled", .bool(e.cancelled))
        case .hover(let e):
            try o.add("t", e.t)
            o.add("clock", e.clock.rawValue)
            try o.add("x", e.x)
            try o.add("y", e.y)
            try o.add("z", e.z)
            try o.add("altitude", optional: e.altitude)
            try o.add("azimuth", optional: e.azimuth)
            try o.add("roll", optional: e.roll)
        case .finger(let e):
            try o.add("t", e.t)
            o.add("phase", e.phase.rawValue)
            try o.add("x", e.x)
            try o.add("y", e.y)
        case .trialEnded(let e):
            try o.add("t", e.t)
            o.add("trial", e.trial.rawValue)
            o.add("reason", e.reason.rawValue)
        case .mark(let e):
            try o.add("t", e.t)
            o.add("trial", e.trial.rawValue)
            o.add("code", e.code.rawValue)
        case .recovered(let e):
            try o.add("t", e.t)
            o.add("at", e.at.text)
            o.add("boot", e.boot.description)
            o.add("truncatedBytes", e.truncatedBytes)
            if let interrupted = e.interrupted { o.add("interrupted", interrupted.rawValue) }
        case .sessionClosed(let e):
            try o.add("t", e.t)
            o.add("counts", e.counts.json(excluding: .sessionClosed))
        }
    }

    static func decode(_ kind: LineKind, _ f: Fields) throws(LineCodec.DecodeError) -> Event {
        switch kind {
        case .sessionOpened:
            let app = try f.object("app")
            let device = try f.object("device")
            let battery = try f.object("battery")
            var opened = SessionOpened(
                sessionID: SessionID(try f.text("sessionID", UUID.init(uuidString:))),
                participant: try f.text("participant", ParticipantCode.init),
                openedAt: WallTime(text: try f.string("openedAt")),
                openedUptime: try f.double("openedUptime"),
                boot: BootID(try f.text("boot", UUID.init(uuidString:))),
                app: AppInfo(version: try app.string("version"), build: try app.string("build"),
                             sourceRevision: try app.string("sourceRevision")),
                device: DeviceInfo(model: try device.string("model"), os: try device.string("os"),
                                   nativeScale: try device.double("nativeScale")),
                scale: try MillimetreScale(try f.object("scale")),
                pencil: try f.object("pencil").enumeration("declared"),
                channels: try ChannelPlan(try f.object("channels")),
                input: try f.enumeration("input"),
                battery: BatteryRef(id: try battery.string("id"), version: try battery.string("version"),
                                    sha256: try battery.string("sha256"))
            )
            opened.format = try f.string("format")
            return .sessionOpened(opened)
        case .trialStarted:
            let trial = try f.object("trial")
            let view = try f.object("view")
            let origin = try f.object("stimulusOrigin")
            let attempt = try trial.int("attempt")
            guard attempt >= 1 else { throw .invalid("trial.attempt") }
            return .trialStarted(TrialStarted(
                t: try f.double("t"),
                trial: TrialRef(index: try trial.trialIndex("index"), task: TaskID(try trial.string("task")),
                                attempt: attempt),
                spec: try TaskSpec(try f.object("spec")),
                view: ViewSize(width: try view.double("width"), height: try view.double("height")),
                stimulusOrigin: Point(x: try origin.double("x"), y: try origin.double("y"))
            ))
        case .contactBegan:
            return .contactBegan(ContactBegan(t: try f.double("t"), contact: try f.contact("contact"),
                                              maxForce: try f.optionalDouble("maxForce")))
        case .sample:
            var pending: Pending?
            if f.has("pending") {
                let p = try f.object("pending")
                let expect = try p.bits("expect")
                guard !expect.isEmpty else { throw .invalid("pending.expect") }
                pending = Pending(expect: expect, update: UpdateIndex(try p.int("update")))
            }
            return .sample(Sample(
                contact: try f.contact("contact"), t: try f.double("t"), x: try f.double("x"), y: try f.double("y"),
                force: try f.optionalDouble("force"), altitude: try f.optionalDouble("altitude"),
                azimuth: try f.optionalDouble("azimuth"), roll: try f.optionalDouble("roll"),
                phase: try f.enumeration("phase"), est: try f.bits("est"), pending: pending
            ))
        case .correction:
            let x = try f.optionalDouble("x")
            let y = try f.optionalDouble("y")
            var location: Point?
            switch (x, y) {
            case (nil, nil): location = nil
            case (let x?, let y?): location = Point(x: x, y: y)
            case (nil, _): throw .missing("x")
            case (_, nil): throw .missing("y")
            }
            return .correction(Correction(
                contact: f.has("contact") ? try f.contact("contact") : nil,
                update: UpdateIndex(try f.int("update")),
                arrived: try f.double("arrived"),
                location: location,
                force: try f.optionalDouble("force"), altitude: try f.optionalDouble("altitude"),
                azimuth: try f.optionalDouble("azimuth"), roll: try f.optionalDouble("roll"),
                expect: try f.bits("expect")
            ))
        case .contactEnded:
            return .contactEnded(ContactEnded(t: try f.double("t"), contact: try f.contact("contact"),
                                              cancelled: try f.bool("cancelled")))
        case .hover:
            return .hover(Hover(
                t: try f.double("t"), clock: try f.enumeration("clock"), x: try f.double("x"), y: try f.double("y"),
                z: try f.double("z"), altitude: try f.optionalDouble("altitude"),
                azimuth: try f.optionalDouble("azimuth"), roll: try f.optionalDouble("roll")
            ))
        case .finger:
            return .finger(Finger(t: try f.double("t"), phase: try f.enumeration("phase"),
                                  x: try f.double("x"), y: try f.double("y")))
        case .trialEnded:
            return .trialEnded(TrialEnded(t: try f.double("t"), trial: try f.trialIndex("trial"),
                                          reason: try f.enumeration("reason")))
        case .mark:
            return .mark(Mark(t: try f.double("t"), trial: try f.trialIndex("trial"), code: try f.enumeration("code")))
        case .recovered:
            let truncated = try f.int("truncatedBytes")
            guard truncated >= 0 else { throw .invalid("truncatedBytes") }
            return .recovered(Recovered(
                t: try f.double("t"), at: WallTime(text: try f.string("at")),
                boot: BootID(try f.text("boot", UUID.init(uuidString:))),
                truncatedBytes: truncated,
                interrupted: f.has("interrupted") ? try f.trialIndex("interrupted") : nil
            ))
        case .sessionClosed:
            let counts = try f.object("counts")
            var values = KindCounts()
            for kind in LineKind.allCases where kind != .sessionClosed {
                let n = try counts.int(kind.rawValue)
                guard n >= 0 else { throw .invalid("counts.\(kind.rawValue)") }
                values[kind] = n
            }
            return .sessionClosed(SessionClosed(t: try f.double("t"), counts: values))
        }
    }
}

// MARK: - Field access

/// The members of one JSON object, with typed accessors that report the key path on failure.
struct Fields {
    let path: String
    let members: [String: JSON]

    init(_ json: JSON, path: String) throws(LineCodec.DecodeError) {
        guard case .object(let pairs) = json else {
            throw path.isEmpty ? .notAnObject : .invalid(path)
        }
        self.path = path
        members = Dictionary(pairs, uniquingKeysWith: { _, last in last })
    }

    func keyPath(_ key: String) -> String { path.isEmpty ? key : "\(path).\(key)" }

    func has(_ key: String) -> Bool { members[key] != nil }

    func read<T>(_ key: String, _ convert: (JSON) -> T?) throws(LineCodec.DecodeError) -> T {
        guard let json = members[key] else { throw .missing(keyPath(key)) }
        guard let value = convert(json) else { throw .invalid(keyPath(key)) }
        return value
    }

    func object(_ key: String) throws(LineCodec.DecodeError) -> Fields {
        guard let json = members[key] else { throw .missing(keyPath(key)) }
        return try Fields(json, path: keyPath(key))
    }

    func array(_ key: String) throws(LineCodec.DecodeError) -> [JSON] {
        try read(key) { if case .array(let items) = $0 { items } else { nil } }
    }

    func string(_ key: String) throws(LineCodec.DecodeError) -> String {
        try read(key) { if case .string(let s) = $0 { s } else { nil } }
    }

    func text<T>(_ key: String, _ make: (String) -> T?) throws(LineCodec.DecodeError) -> T {
        try read(key) { if case .string(let s) = $0 { make(s) } else { nil } }
    }

    func bool(_ key: String) throws(LineCodec.DecodeError) -> Bool {
        try read(key) { if case .bool(let b) = $0 { b } else { nil } }
    }

    func int(_ key: String) throws(LineCodec.DecodeError) -> Int {
        try read(key) { $0.intValue }
    }

    func double(_ key: String) throws(LineCodec.DecodeError) -> Double {
        try read(key) { $0.doubleValue }
    }

    func optionalDouble(_ key: String) throws(LineCodec.DecodeError) -> Double? {
        has(key) ? try double(key) : nil
    }

    func enumeration<T: RawRepresentable<String>>(_ key: String) throws(LineCodec.DecodeError) -> T {
        try text(key, T.init(rawValue:))
    }

    func contact(_ key: String) throws(LineCodec.DecodeError) -> ContactID {
        try read(key) { $0.intValue.flatMap(ContactID.init) }
    }

    func trialIndex(_ key: String) throws(LineCodec.DecodeError) -> TrialIndex {
        try read(key) { $0.intValue.flatMap(TrialIndex.init) }
    }

    func bits(_ key: String) throws(LineCodec.DecodeError) -> TouchProperties {
        try read(key) { $0.intValue.flatMap { (0...31).contains($0) ? TouchProperties(rawValue: $0) : nil } }
    }
}

extension JSON {
    /// An integer written without a fraction or an exponent.
    var intValue: Int? {
        if case .number(let text) = self { Int(text) } else { nil }
    }

    var doubleValue: Double? {
        if case .number(let text) = self, let value = Double(text), value.isFinite { value } else { nil }
    }
}

/// Builds one JSON object in key order. An absent optional is an absent key.
struct ObjectBuilder {
    private(set) var pairs: [(key: String, value: JSON)] = []

    var json: JSON { .object(pairs) }

    mutating func add(_ key: String, _ value: JSON) { pairs.append((key, value)) }
    mutating func add(_ key: String, _ value: String) { pairs.append((key, .string(value))) }
    mutating func add(_ key: String, _ value: Int) { pairs.append((key, .int(value))) }

    mutating func add(_ key: String, _ value: Double) throws(LineCodec.EncodeError) {
        guard let number = JSON.double(value) else { throw .nonFinite(key) }
        pairs.append((key, number))
    }

    mutating func add(_ key: String, optional value: Double?) throws(LineCodec.EncodeError) {
        if let value { try add(key, value) }
    }
}
