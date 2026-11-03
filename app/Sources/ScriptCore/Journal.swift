import CryptoKit
import Foundation

/// The journal format this build writes and reads. See docs/DESIGN.md section 3.
public enum JournalFormat {
    public static let version = "1.0"

    /// A minor version adds optional keys only, so this build reads every 1.x journal.
    static func isReadable(_ version: String) -> Bool {
        version.split(separator: ".", omittingEmptySubsequences: false).first == "1"
    }
}

/// A JSON value that does not decode to its type. The text names the key path, for example `trial.attempt`.
public enum DecodeError: Error, Hashable, Sendable {
    /// The text is not one UTF-8 JSON value. The value is the byte offset.
    case syntax(offset: Int)
    case notAnObject
    case unknownKind(String)
    case missing(String)
    /// A value has the wrong type, is out of range, or does not match its pattern.
    case invalid(String)
    /// A key that the schema does not allow. Only study configurations refuse unknown keys.
    case unexpected(String)
}

// MARK: - Identifiers

/// Pattern checks from `contract/journal.schema.json` and `contract/config.schema.json`.
enum Pattern {
    static func isDigit(_ c: UInt8) -> Bool { (0x30...0x39).contains(c) }
    static func isUpper(_ c: UInt8) -> Bool { (0x41...0x5A).contains(c) }
    static func isLower(_ c: UInt8) -> Bool { (0x61...0x7A).contains(c) }
    static let dash = UInt8(ascii: "-")

    /// `^[a-z0-9-]+$`
    static func isSlug(_ s: String) -> Bool {
        !s.isEmpty && s.utf8.allSatisfy { isLower($0) || isDigit($0) || $0 == dash }
    }

    /// `^[A-Za-z0-9-]{1,32}$`
    static func isCode(_ s: String) -> Bool {
        (1...32).contains(s.utf8.count) && s.utf8.allSatisfy { isUpper($0) || isLower($0) || isDigit($0) || $0 == dash }
    }

    /// `^[A-Z0-9-]{3,16}$`
    static func isParticipant(_ s: String) -> Bool {
        (3...16).contains(s.utf8.count) && s.utf8.allSatisfy { isUpper($0) || isDigit($0) || $0 == dash }
    }

    /// `^[A-Z]{2}$`
    static func isJurisdiction(_ s: String) -> Bool { s.utf8.count == 2 && s.utf8.allSatisfy(isUpper) }

    /// `^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$`
    static func isLocale(_ s: String) -> Bool {
        let parts = s.utf8.split(separator: dash, omittingEmptySubsequences: false)
        guard let language = parts.first, (2...3).contains(language.count), language.allSatisfy(isLower) else {
            return false
        }
        return parts.dropFirst().allSatisfy { part in
            (2...8).contains(part.count) && part.allSatisfy { isUpper($0) || isLower($0) || isDigit($0) }
        }
    }

    /// `^[0-9a-f]{64}$`
    static func isSHA256(_ s: String) -> Bool {
        s.utf8.count == 64 && s.utf8.allSatisfy { isDigit($0) || (0x61...0x66).contains($0) }
    }
}

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

/// A 0-based position: a trial index or a questionnaire block number.
public struct ZeroBased<Of>: Hashable, Comparable, Sendable, CustomStringConvertible {
    public let rawValue: Int
    public init?(_ rawValue: Int) {
        guard rawValue >= 0 else { return nil }
        self.rawValue = rawValue
    }
    init(count: Int) { rawValue = count }
    public static func < (a: ZeroBased, b: ZeroBased) -> Bool { a.rawValue < b.rawValue }
    public var description: String { String(rawValue) }
}

public enum TrialTag {}
public enum BlockTag {}
public typealias TrialIndex = ZeroBased<TrialTag>
public typealias BlockIndex = ZeroBased<BlockTag>

/// `UITouch.estimationUpdateIndex`.
public struct UpdateIndex: Hashable, Sendable {
    public let rawValue: Int
    public init(_ rawValue: Int) { self.rawValue = rawValue }
}

/// A text value that must match one schema pattern.
public protocol PatternText: Hashable, Sendable, CustomStringConvertible, ExpressibleByStringLiteral {
    var rawValue: String { get }
    init?(_ rawValue: String)
}

extension PatternText {
    public var description: String { rawValue }
    /// A literal is source code, so a literal that breaks the pattern is a programming error.
    public init(stringLiteral value: String) {
        guard let valid = Self(value) else { preconditionFailure("\(Self.self) refuses \(value)") }
        self = valid
    }
}

/// A participant code that matches `^[A-Z0-9-]{3,16}$`.
public struct ParticipantCode: PatternText {
    public let rawValue: String
    public init?(_ rawValue: String) {
        guard Pattern.isParticipant(rawValue) else { return nil }
        self.rawValue = rawValue
    }
}

/// A short code that matches `^[A-Za-z0-9-]{1,32}$`: an asset, a study, a site, a visit, or an action code.
public struct Code: PatternText {
    public let rawValue: String
    public init?(_ rawValue: String) {
        guard Pattern.isCode(rawValue) else { return nil }
        self.rawValue = rawValue
    }
}

/// A task identifier from the battery, for example `spiral-trace`.
public struct TaskID: PatternText {
    public let rawValue: String
    public init?(_ rawValue: String) {
        guard Pattern.isSlug(rawValue) else { return nil }
        self.rawValue = rawValue
    }
}

/// An instrument identifier from `contract/instruments.json`, for example `phq9`.
public struct InstrumentID: PatternText {
    public let rawValue: String
    public init?(_ rawValue: String) {
        guard Pattern.isSlug(rawValue) else { return nil }
        self.rawValue = rawValue
    }
}

/// An item identifier from `contract/instruments.json`, for example `phq9-9`.
public struct ItemID: PatternText {
    public let rawValue: String
    public init?(_ rawValue: String) {
        guard Pattern.isSlug(rawValue) else { return nil }
        self.rawValue = rawValue
    }
}

/// An ISO 3166-1 alpha-2 country code.
public struct Jurisdiction: PatternText {
    public let rawValue: String
    public init?(_ rawValue: String) {
        guard Pattern.isJurisdiction(rawValue) else { return nil }
        self.rawValue = rawValue
    }
}

/// A BCP 47 tag in the form that the schema allows, for example `en` or `en-GB`.
public struct LocaleTag: PatternText {
    public let rawValue: String
    public init?(_ rawValue: String) {
        guard Pattern.isLocale(rawValue) else { return nil }
        self.rawValue = rawValue
    }
}

/// A SHA-256 value as 64 lowercase hex digits.
public struct SHA256Digest: Hashable, Sendable, CustomStringConvertible {
    public let hex: String
    public init?(hex: String) {
        guard Pattern.isSHA256(hex) else { return nil }
        self.hex = hex
    }
    public init(of data: some DataProtocol) { hex = SHA256.hash(data: data).hex }
    public var description: String { hex }

    var bytes: [UInt8] {
        var out: [UInt8] = []
        out.reserveCapacity(32)
        var high: UInt8?
        for c in hex.utf8 {
            let nibble = Pattern.isDigit(c) ? c - 0x30 : c - 0x61 + 10
            if let h = high {
                out.append(h << 4 | nibble)
                high = nil
            } else {
                high = nibble
            }
        }
        return out
    }
}

extension Sequence<UInt8> {
    var hex: String {
        let digits = Array("0123456789abcdef".utf8)
        var out: [UInt8] = []
        for byte in self {
            out.append(digits[Int(byte >> 4)])
            out.append(digits[Int(byte & 0xF)])
        }
        return String(decoding: out, as: UTF8.self)
    }
}

/// An ISO 8601 UTC wall-clock time, kept as the recorded text.
public struct WallTime: Hashable, Sendable, CustomStringConvertible {
    public let text: String
    public init(_ date: Date) { text = date.formatted(.iso8601) }
    init(text: String) { self.text = text }
    public var description: String { text }
    /// The time, or nil when the text is not ISO 8601.
    public var date: Date? { try? Date(text, strategy: .iso8601) }
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
    public let width: Double
    public let height: Double
    /// Nil unless both sides are finite and positive, as the schema requires.
    public init?(width: Double, height: Double) {
        guard width.isFinite, height.isFinite, width > 0, height > 0 else { return nil }
        self.width = width
        self.height = height
    }
}

public enum Purpose: String, Hashable, Sendable { case study, qualification }
public enum Administration: String, Hashable, Sendable, CaseIterable {
    case supervised
    case selfAdministered = "self"
}
public enum AgeBand: String, Hashable, Sendable, CaseIterable {
    case adolescent = "13-17"
    case youngAdult = "18-24"

    /// The lowest age in the band.
    public var lowestAge: Int {
        switch self {
        case .adolescent: 13
        case .youngAdult: 18
        }
    }
}
public enum Protector: String, Hashable, Sendable { case none, glossy, matte, paperlike, unknown }
public enum Nib: String, Hashable, Sendable { case new, used, unknown }
public enum ConsentModel: String, Hashable, Sendable { case adult, guardianAndAssent, waiver }
public enum AttestorRole: String, Hashable, Sendable { case `operator`, enrolment }
public enum InputKind: String, Hashable, Sendable { case pencil, directTouchAsPen }
public enum HandoffTarget: String, Hashable, Sendable { case participant, `operator` }
public enum Hand: String, Hashable, Sendable { case left, right, unknown }
public enum Accommodation: String, Hashable, Sendable { case readAloud, largerText, extraTime, positionAssist, other }
public enum PenPhase: String, Hashable, Sendable { case began, moved, stationary, ended, cancelled }
public enum FingerPhase: String, Hashable, Sendable { case began, ended, cancelled }
public enum HoverClock: String, Hashable, Sendable { case callback }
public enum TrialEndReason: String, Hashable, Sendable {
    case participantDone, timeLimit, declined, unable, operatorAbort, backgrounded, technicalFailure
}
public enum QuestionnaireEndReason: String, Hashable, Sendable { case completed, declined, interrupted }
public enum SafetyDisplay: String, Hashable, Sendable { case crisisList, operatorAlert }
public enum ActionRole: String, Hashable, Sendable { case `operator` }
public enum MarkCode: String, Hashable, Sendable { case repeatRequested, disturbance, deviceProblem, exclude }

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
    /// The `hw.machine` identifier, for example `iPad16,3`.
    public var model: String
    public var os: String
    public var nativeScale: Double
    /// The pseudonymous asset code that the site gives the iPad.
    public var asset: Code
    public init(model: String, os: String, nativeScale: Double, asset: Code) {
        self.model = model
        self.os = os
        self.nativeScale = nativeScale
        self.asset = asset
    }
}

public struct Surface: Hashable, Sendable {
    public var protector: Protector
    public var nib: Nib
    public init(protector: Protector, nib: Nib) {
        self.protector = protector
        self.nib = nib
    }
}

/// The last qualification session on this device and Pencil.
public struct QualificationRef: Hashable, Sendable {
    public var sessionID: SessionID
    public var at: WallTime
    public init(sessionID: SessionID, at: WallTime) {
        self.sessionID = sessionID
        self.at = at
    }
}

public struct StudyRef: Hashable, Sendable {
    public var id: Code
    public var site: Code
    public var configID: Code
    public var configVersion: String
    public var configSHA256: SHA256Digest
    public var protocolVersion: String
    public init(id: Code, site: Code, configID: Code, configVersion: String, configSHA256: SHA256Digest,
                protocolVersion: String) {
        self.id = id
        self.site = site
        self.configID = configID
        self.configVersion = configVersion
        self.configSHA256 = configSHA256
        self.protocolVersion = protocolVersion
    }
}

public struct Visit: Hashable, Sendable {
    public let id: Code
    public let number: Int
    /// Nil when `number` is below 1. Visits are numbered from 1.
    public init?(id: Code, number: Int) {
        guard number >= 1 else { return nil }
        self.id = id
        self.number = number
    }
}

public struct DocumentRef: Hashable, Sendable {
    public let id: String
    public let version: String
    /// Nil when the identifier or the version is empty, which the schema refuses.
    public init?(id: String, version: String) {
        guard !id.isEmpty, !version.isEmpty else { return nil }
        self.id = id
        self.version = version
    }
}

public struct Attestor: Hashable, Sendable {
    public var code: Code
    public var role: AttestorRole
    public init(code: Code, role: AttestorRole) {
        self.code = code
        self.role = role
    }
}

/// Attestations and document versions only. The journal never holds a name, a signature, or contact details.
public struct Consent: Hashable, Sendable {
    public var model: ConsentModel
    public var attestedBy: Attestor
    public var attestedAt: WallTime
    public var documents: [DocumentRef]
    public var waiverReference: String?
    public init(model: ConsentModel, attestedBy: Attestor, attestedAt: WallTime, documents: [DocumentRef],
                waiverReference: String? = nil) {
        self.model = model
        self.attestedBy = attestedBy
        self.attestedAt = attestedAt
        self.documents = documents
        self.waiverReference = waiverReference
    }
}

public struct Ethics: Hashable, Sendable {
    public var approvalID: String
    public var protocolVersion: String
    public var safetyProtocolVersion: String
    public var monitoringDisclosureVersion: String?
    public init(approvalID: String, protocolVersion: String, safetyProtocolVersion: String,
                monitoringDisclosureVersion: String? = nil) {
        self.approvalID = approvalID
        self.protocolVersion = protocolVersion
        self.safetyProtocolVersion = safetyProtocolVersion
        self.monitoringDisclosureVersion = monitoringDisclosureVersion
    }
}

public struct BatteryRef: Hashable, Sendable {
    public var id: String
    public var version: String
    public var sha256: SHA256Digest
    public init(id: String, version: String, sha256: SHA256Digest) {
        self.id = id
        self.version = version
        self.sha256 = sha256
    }
}

// MARK: - Line payloads

public struct SessionOpened: Hashable, Sendable {
    public internal(set) var format = JournalFormat.version
    public var purpose: Purpose
    public var sessionID: SessionID
    public var participant: ParticipantCode
    public var openedAt: WallTime
    public var openedUptime: Double
    public var boot: BootID
    public var app: AppInfo
    public var device: DeviceInfo
    public var surface: Surface
    public var qualification: QualificationRef?
    public var scale: MillimetreScale
    public var pencil: PencilModel
    public var channels: ChannelPlan
    public var input: InputKind
    public var administration: Administration
    public var study: StudyRef
    public var visit: Visit
    public var ageBand: AgeBand
    public var jurisdiction: Jurisdiction
    public var locale: LocaleTag
    public var consent: Consent
    public var ethics: Ethics
    public var battery: BatteryRef

    public init(
        purpose: Purpose, sessionID: SessionID, participant: ParticipantCode, openedAt: WallTime,
        openedUptime: Double, boot: BootID, app: AppInfo, device: DeviceInfo, surface: Surface,
        qualification: QualificationRef?, scale: MillimetreScale, pencil: PencilModel, channels: ChannelPlan,
        input: InputKind, administration: Administration, study: StudyRef, visit: Visit, ageBand: AgeBand,
        jurisdiction: Jurisdiction, locale: LocaleTag, consent: Consent, ethics: Ethics, battery: BatteryRef
    ) {
        self.purpose = purpose
        self.sessionID = sessionID
        self.participant = participant
        self.openedAt = openedAt
        self.openedUptime = openedUptime
        self.boot = boot
        self.app = app
        self.device = device
        self.surface = surface
        self.qualification = qualification
        self.scale = scale
        self.pencil = pencil
        self.channels = channels
        self.input = input
        self.administration = administration
        self.study = study
        self.visit = visit
        self.ageBand = ageBand
        self.jurisdiction = jurisdiction
        self.locale = locale
        self.consent = consent
        self.ethics = ethics
        self.battery = battery
    }

    /// `SCRIPT_<study>_<site>_<first 8 hex digits of the session UUID>`, the export folder name. It never holds the
    /// participant code, which stays inside the journal and, when encrypted, inside the ciphertext.
    public var exportName: String {
        "SCRIPT_\(study.id.rawValue)_\(study.site.rawValue)_\(sessionID.uuid.uuidString.prefix(8).lowercased())"
    }
}

public struct Handoff: Hashable, Sendable {
    public var t: Double
    public var to: HandoffTarget
}

public struct TrialRef: Hashable, Sendable {
    public var index: TrialIndex
    public var task: TaskID
    public var attempt: Int
}

public struct InstructionRef: Hashable, Sendable {
    public var id: String
    public var version: String
    public var locale: LocaleTag
    public init(id: String, version: String, locale: LocaleTag) {
        self.id = id
        self.version = version
        self.locale = locale
    }
}

public struct TrialStarted: Hashable, Sendable {
    public var t: Double
    public var trial: TrialRef
    public var spec: TaskSpec
    public var practice: Bool
    /// The 1-based position of the task in the session order.
    public var position: Int
    public var hand: Hand
    public var instruction: InstructionRef
    public var accommodations: [Accommodation]
    public var view: ViewSize
    public var stimulusOrigin: Point
}

public struct TrialBegan: Hashable, Sendable {
    public var t: Double
    public var trial: TrialIndex
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

/// A late value for an estimated sample. `contact` is nil when no recorded sample expects this update.
public struct Correction: Hashable, Sendable {
    public var contact: ContactID?
    public var update: UpdateIndex
    public var arrived: Double
    public var x: Double?
    public var y: Double?
    public var force: Double?
    public var altitude: Double?
    public var azimuth: Double?
    public var roll: Double?
    /// The bits still pending after this correction.
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
    /// 0 at contact, 1 at the maximum range.
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

public struct InstrumentRef: Hashable, Sendable {
    public var id: InstrumentID
    public var version: String
    public var locale: LocaleTag
}

public struct QuestionnaireStarted: Hashable, Sendable {
    public var t: Double
    public var block: BlockIndex
    public var instrument: InstrumentRef
}

/// An answer or a skip. `value` is nil when the participant skipped the item, so `skipped` cannot disagree.
public struct Response: Hashable, Sendable {
    public var t: Double
    public var block: BlockIndex
    public var item: ItemID
    public var value: Int?
    public var skipped: Bool { value == nil }
}

public struct QuestionnaireEnded: Hashable, Sendable {
    public var t: Double
    public var block: BlockIndex
    public var reason: QuestionnaireEndReason
}

/// A safety item that was endorsed (value at least 1) or skipped.
public struct SafetyFlagged: Hashable, Sendable {
    public var t: Double
    public var block: BlockIndex
    public var item: ItemID
    public var value: Int?
    public var skipped: Bool { value == nil }
}

public struct SafetyShown: Hashable, Sendable {
    public var t: Double
    public var what: SafetyDisplay
    public var listVersion: String?
}

public struct SafetyAction: Hashable, Sendable {
    public var t: Double
    public var code: Code
    public var role: ActionRole
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
    public var interruptedBlock: BlockIndex?
}

public struct SessionClosed: Hashable, Sendable {
    public var t: Double
    /// The number of lines of each other kind before this line.
    public var counts: KindCounts
}

/// The line kinds in contract order. `KindCounts` and the fold summary list them in this order.
public enum LineKind: String, CaseIterable, Hashable, Sendable {
    case sessionOpened, handoff, trialStarted, trialBegan, contactBegan, sample, correction, contactEnded
    case hover, finger, trialEnded, questionnaireStarted, response, questionnaireEnded, safetyFlagged
    case safetyShown, safetyAction, mark, recovered, sessionClosed
}

public enum Event: Hashable, Sendable {
    case sessionOpened(SessionOpened)
    case handoff(Handoff)
    case trialStarted(TrialStarted)
    case trialBegan(TrialBegan)
    case contactBegan(ContactBegan)
    case sample(Sample)
    case correction(Correction)
    case contactEnded(ContactEnded)
    case hover(Hover)
    case finger(Finger)
    case trialEnded(TrialEnded)
    case questionnaireStarted(QuestionnaireStarted)
    case response(Response)
    case questionnaireEnded(QuestionnaireEnded)
    case safetyFlagged(SafetyFlagged)
    case safetyShown(SafetyShown)
    case safetyAction(SafetyAction)
    case mark(Mark)
    case recovered(Recovered)
    case sessionClosed(SessionClosed)

    public var kind: LineKind {
        switch self {
        case .sessionOpened: .sessionOpened
        case .handoff: .handoff
        case .trialStarted: .trialStarted
        case .trialBegan: .trialBegan
        case .contactBegan: .contactBegan
        case .sample: .sample
        case .correction: .correction
        case .contactEnded: .contactEnded
        case .hover: .hover
        case .finger: .finger
        case .trialEnded: .trialEnded
        case .questionnaireStarted: .questionnaireStarted
        case .response: .response
        case .questionnaireEnded: .questionnaireEnded
        case .safetyFlagged: .safetyFlagged
        case .safetyShown: .safetyShown
        case .safetyAction: .safetyAction
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

    /// One line with `seq` and `kind` first, ended by LF. An absent optional value is an absent key.
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
            throw .syntax(offset: error.offset)
        }
        guard case .object = json else { throw .notAnObject }
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
            o.add("purpose", e.purpose.rawValue)
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
            device.add("asset", e.device.asset.rawValue)
            o.add("device", device.json)
            o.add("surface", .object([
                ("protector", .string(e.surface.protector.rawValue)), ("nib", .string(e.surface.nib.rawValue)),
            ]))
            if let q = e.qualification {
                o.add("qualification", .object([("sessionID", .string(q.sessionID.description)), ("at", .string(q.at.text))]))
            }
            o.add("scale", try e.scale.json())
            o.add("pencil", .object([("declared", .string(e.pencil.rawValue))]))
            o.add("channels", e.channels.json)
            o.add("input", e.input.rawValue)
            o.add("administration", e.administration.rawValue)
            o.add("study", .object([
                ("id", .string(e.study.id.rawValue)), ("site", .string(e.study.site.rawValue)),
                ("configID", .string(e.study.configID.rawValue)), ("configVersion", .string(e.study.configVersion)),
                ("configSHA256", .string(e.study.configSHA256.hex)),
                ("protocolVersion", .string(e.study.protocolVersion)),
            ]))
            o.add("visit", .object([("id", .string(e.visit.id.rawValue)), ("number", .int(e.visit.number))]))
            o.add("ageBand", e.ageBand.rawValue)
            o.add("jurisdiction", e.jurisdiction.rawValue)
            o.add("locale", e.locale.rawValue)
            var consent = ObjectBuilder()
            consent.add("model", e.consent.model.rawValue)
            consent.add("attestedBy", .object([
                ("code", .string(e.consent.attestedBy.code.rawValue)),
                ("role", .string(e.consent.attestedBy.role.rawValue)),
            ]))
            consent.add("attestedAt", e.consent.attestedAt.text)
            consent.add("documents", .array(e.consent.documents.map(\.json)))
            consent.add("waiverReference", optional: e.consent.waiverReference)
            o.add("consent", consent.json)
            o.add("ethics", e.ethics.json)
            o.add("battery", .object([
                ("id", .string(e.battery.id)), ("version", .string(e.battery.version)),
                ("sha256", .string(e.battery.sha256.hex)),
            ]))
        case .handoff(let e):
            try o.add("t", e.t)
            o.add("to", e.to.rawValue)
        case .trialStarted(let e):
            try o.add("t", e.t)
            o.add("trial", .object([
                ("index", .int(e.trial.index.rawValue)), ("task", .string(e.trial.task.rawValue)),
                ("attempt", .int(e.trial.attempt)),
            ]))
            o.add("spec", e.spec.source)
            o.add("practice", e.practice)
            o.add("position", e.position)
            o.add("hand", e.hand.rawValue)
            o.add("instruction", .object([
                ("id", .string(e.instruction.id)), ("version", .string(e.instruction.version)),
                ("locale", .string(e.instruction.locale.rawValue)),
            ]))
            o.add("accommodations", .array(e.accommodations.map { .string($0.rawValue) }))
            var view = ObjectBuilder()
            try view.add("width", e.view.width)
            try view.add("height", e.view.height)
            o.add("view", view.json)
            var origin = ObjectBuilder()
            try origin.add("x", e.stimulusOrigin.x)
            try origin.add("y", e.stimulusOrigin.y)
            o.add("stimulusOrigin", origin.json)
        case .trialBegan(let e):
            try o.add("t", e.t)
            o.add("trial", e.trial.rawValue)
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
            if let p = e.pending {
                o.add("pending", .object([("expect", .int(p.expect.rawValue)), ("update", .int(p.update.rawValue))]))
            }
        case .correction(let e):
            if let contact = e.contact { o.add("contact", contact.rawValue) }
            o.add("update", e.update.rawValue)
            try o.add("arrived", e.arrived)
            try o.add("x", optional: e.x)
            try o.add("y", optional: e.y)
            try o.add("force", optional: e.force)
            try o.add("altitude", optional: e.altitude)
            try o.add("azimuth", optional: e.azimuth)
            try o.add("roll", optional: e.roll)
            o.add("expect", e.expect.rawValue)
        case .contactEnded(let e):
            try o.add("t", e.t)
            o.add("contact", e.contact.rawValue)
            o.add("cancelled", e.cancelled)
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
        case .questionnaireStarted(let e):
            try o.add("t", e.t)
            o.add("block", e.block.rawValue)
            o.add("instrument", .object([
                ("id", .string(e.instrument.id.rawValue)), ("version", .string(e.instrument.version)),
                ("locale", .string(e.instrument.locale.rawValue)),
            ]))
        case .response(let e):
            try o.add("t", e.t)
            o.add("block", e.block.rawValue)
            o.add("item", e.item.rawValue)
            if let value = e.value { o.add("value", value) }
            o.add("skipped", e.skipped)
        case .questionnaireEnded(let e):
            try o.add("t", e.t)
            o.add("block", e.block.rawValue)
            o.add("reason", e.reason.rawValue)
        case .safetyFlagged(let e):
            try o.add("t", e.t)
            o.add("block", e.block.rawValue)
            o.add("item", e.item.rawValue)
            if let value = e.value { o.add("value", value) }
            o.add("skipped", e.skipped)
        case .safetyShown(let e):
            try o.add("t", e.t)
            o.add("what", e.what.rawValue)
            o.add("listVersion", optional: e.listVersion)
        case .safetyAction(let e):
            try o.add("t", e.t)
            o.add("code", e.code.rawValue)
            o.add("role", e.role.rawValue)
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
            if let block = e.interruptedBlock { o.add("interruptedBlock", block.rawValue) }
        case .sessionClosed(let e):
            try o.add("t", e.t)
            o.add("counts", e.counts.json(excluding: .sessionClosed))
        }
    }

    static func decode(_ kind: LineKind, _ f: Fields) throws(DecodeError) -> Event {
        switch kind {
        case .sessionOpened:
            return .sessionOpened(try SessionOpened(f))
        case .handoff:
            return .handoff(Handoff(t: try f.uptime("t"), to: try f.enumeration("to")))
        case .trialStarted:
            let trial = try f.object("trial")
            let view = try f.object("view")
            guard let size = ViewSize(width: try view.positive("width"), height: try view.positive("height")) else {
                throw .invalid("view")
            }
            let origin = try f.object("stimulusOrigin")
            let instruction = try f.object("instruction")
            let attempt = try trial.int("attempt")
            guard attempt >= 1 else { throw .invalid("trial.attempt") }
            let position = try f.int("position")
            guard position >= 1 else { throw .invalid("position") }
            var accommodations: [Accommodation] = []
            for (n, item) in try f.array("accommodations").enumerated() {
                guard case .string(let text) = item, let code = Accommodation(rawValue: text),
                      !accommodations.contains(code)
                else { throw .invalid("accommodations[\(n)]") }
                accommodations.append(code)
            }
            return .trialStarted(TrialStarted(
                t: try f.uptime("t"),
                trial: TrialRef(index: try trial.index("index"), task: try trial.text("task", TaskID.init(_:)),
                                attempt: attempt),
                spec: try TaskSpec(try f.read("spec") { $0 }, path: "spec"),
                practice: try f.bool("practice"),
                position: position,
                hand: try f.enumeration("hand"),
                instruction: InstructionRef(id: try instruction.string("id"), version: try instruction.string("version"),
                                            locale: try instruction.text("locale", LocaleTag.init(_:))),
                accommodations: accommodations,
                view: size,
                stimulusOrigin: Point(x: try origin.double("x"), y: try origin.double("y"))
            ))
        case .trialBegan:
            return .trialBegan(TrialBegan(t: try f.uptime("t"), trial: try f.index("trial")))
        case .contactBegan:
            var maxForce: Double?
            if f.has("maxForce") { maxForce = try f.positive("maxForce") }
            return .contactBegan(ContactBegan(t: try f.uptime("t"), contact: try f.contact("contact"), maxForce: maxForce))
        case .sample:
            var pending: Pending?
            if f.has("pending") {
                let p = try f.object("pending")
                let expect = try p.bits("expect")
                guard !expect.isEmpty else { throw .invalid("pending.expect") }
                pending = Pending(expect: expect, update: UpdateIndex(try p.int("update")))
            }
            return .sample(Sample(
                contact: try f.contact("contact"), t: try f.uptime("t"), x: try f.double("x"), y: try f.double("y"),
                force: try f.optionalForce("force"), altitude: try f.optionalDouble("altitude"),
                azimuth: try f.optionalDouble("azimuth"), roll: try f.optionalDouble("roll"),
                phase: try f.enumeration("phase"), est: try f.bits("est"), pending: pending
            ))
        case .correction:
            return .correction(Correction(
                contact: f.has("contact") ? try f.contact("contact") : nil,
                update: UpdateIndex(try f.int("update")),
                arrived: try f.uptime("arrived"),
                x: try f.optionalDouble("x"), y: try f.optionalDouble("y"),
                force: try f.optionalForce("force"), altitude: try f.optionalDouble("altitude"),
                azimuth: try f.optionalDouble("azimuth"), roll: try f.optionalDouble("roll"),
                expect: try f.bits("expect")
            ))
        case .contactEnded:
            return .contactEnded(ContactEnded(t: try f.uptime("t"), contact: try f.contact("contact"),
                                              cancelled: try f.bool("cancelled")))
        case .hover:
            let z = try f.double("z")
            guard z >= 0 else { throw .invalid("z") }
            return .hover(Hover(
                t: try f.uptime("t"), clock: try f.enumeration("clock"), x: try f.double("x"), y: try f.double("y"),
                z: z, altitude: try f.optionalDouble("altitude"), azimuth: try f.optionalDouble("azimuth"),
                roll: try f.optionalDouble("roll")
            ))
        case .finger:
            return .finger(Finger(t: try f.uptime("t"), phase: try f.enumeration("phase"),
                                  x: try f.double("x"), y: try f.double("y")))
        case .trialEnded:
            return .trialEnded(TrialEnded(t: try f.uptime("t"), trial: try f.index("trial"),
                                          reason: try f.enumeration("reason")))
        case .questionnaireStarted:
            let instrument = try f.object("instrument")
            return .questionnaireStarted(QuestionnaireStarted(
                t: try f.uptime("t"), block: try f.index("block"),
                instrument: InstrumentRef(id: try instrument.text("id", InstrumentID.init(_:)),
                                          version: try instrument.string("version"),
                                          locale: try instrument.text("locale", LocaleTag.init(_:)))
            ))
        case .response:
            return .response(Response(t: try f.uptime("t"), block: try f.index("block"),
                                      item: try f.text("item", ItemID.init(_:)), value: try f.answer()))
        case .questionnaireEnded:
            return .questionnaireEnded(QuestionnaireEnded(t: try f.uptime("t"), block: try f.index("block"),
                                                          reason: try f.enumeration("reason")))
        case .safetyFlagged:
            return .safetyFlagged(SafetyFlagged(t: try f.uptime("t"), block: try f.index("block"),
                                                item: try f.text("item", ItemID.init(_:)), value: try f.answer()))
        case .safetyShown:
            return .safetyShown(SafetyShown(t: try f.uptime("t"), what: try f.enumeration("what"),
                                            listVersion: f.has("listVersion") ? try f.string("listVersion") : nil))
        case .safetyAction:
            return .safetyAction(SafetyAction(t: try f.uptime("t"), code: try f.text("code", Code.init(_:)),
                                              role: try f.enumeration("role")))
        case .mark:
            return .mark(Mark(t: try f.uptime("t"), trial: try f.index("trial"), code: try f.enumeration("code")))
        case .recovered:
            let truncated = try f.int("truncatedBytes")
            guard truncated >= 0 else { throw .invalid("truncatedBytes") }
            return .recovered(Recovered(
                t: try f.uptime("t"), at: WallTime(text: try f.string("at")),
                boot: BootID(try f.text("boot", UUID.init(uuidString:))),
                truncatedBytes: truncated,
                interrupted: f.has("interrupted") ? try f.index("interrupted") : nil,
                interruptedBlock: f.has("interruptedBlock") ? try f.index("interruptedBlock") : nil
            ))
        case .sessionClosed:
            let counts = try f.object("counts")
            var values = KindCounts()
            for kind in LineKind.allCases where kind != .sessionClosed {
                let n = try counts.int(kind.rawValue)
                guard n >= 0 else { throw .invalid("counts.\(kind.rawValue)") }
                values[kind] = n
            }
            return .sessionClosed(SessionClosed(t: try f.uptime("t"), counts: values))
        }
    }
}

extension SessionOpened {
    init(_ f: Fields) throws(DecodeError) {
        let app = try f.object("app")
        let device = try f.object("device")
        let surface = try f.object("surface")
        let study = try f.object("study")
        let visit = try f.object("visit")
        let consent = try f.object("consent")
        let attestor = try consent.object("attestedBy")
        let battery = try f.object("battery")
        var qualification: QualificationRef?
        if f.has("qualification") {
            let q = try f.object("qualification")
            qualification = QualificationRef(sessionID: SessionID(try q.text("sessionID", UUID.init(uuidString:))),
                                             at: WallTime(text: try q.string("at")))
        }
        var documents: [DocumentRef] = []
        for (n, item) in try consent.array("documents").enumerated() {
            documents.append(try DocumentRef(try Fields(item, path: consent.keyPath("documents[\(n)]"))))
        }
        guard let visitRef = Visit(id: try visit.text("id", Code.init(_:)), number: try visit.int("number")) else {
            throw .invalid("visit.number")
        }
        self.init(
            purpose: try f.enumeration("purpose"),
            sessionID: SessionID(try f.text("sessionID", UUID.init(uuidString:))),
            participant: try f.text("participant", ParticipantCode.init(_:)),
            openedAt: WallTime(text: try f.string("openedAt")),
            openedUptime: try f.uptime("openedUptime"),
            boot: BootID(try f.text("boot", UUID.init(uuidString:))),
            app: AppInfo(version: try app.string("version"), build: try app.string("build"),
                         sourceRevision: try app.string("sourceRevision")),
            device: DeviceInfo(model: try device.string("model"), os: try device.string("os"),
                               nativeScale: try device.positive("nativeScale"), asset: try device.text("asset", Code.init(_:))),
            surface: Surface(protector: try surface.enumeration("protector"), nib: try surface.enumeration("nib")),
            qualification: qualification,
            scale: try MillimetreScale(try f.object("scale")),
            pencil: try f.object("pencil").enumeration("declared"),
            channels: try ChannelPlan(try f.object("channels")),
            input: try f.enumeration("input"),
            administration: try f.enumeration("administration"),
            study: StudyRef(id: try study.text("id", Code.init(_:)), site: try study.text("site", Code.init(_:)),
                            configID: try study.text("configID", Code.init(_:)),
                            configVersion: try study.string("configVersion"),
                            configSHA256: try study.text("configSHA256", SHA256Digest.init(hex:)),
                            protocolVersion: try study.string("protocolVersion")),
            visit: visitRef,
            ageBand: try f.enumeration("ageBand"),
            jurisdiction: try f.text("jurisdiction", Jurisdiction.init(_:)),
            locale: try f.text("locale", LocaleTag.init(_:)),
            consent: Consent(model: try consent.enumeration("model"),
                             attestedBy: Attestor(code: try attestor.text("code", Code.init(_:)),
                                                  role: try attestor.enumeration("role")),
                             attestedAt: WallTime(text: try consent.string("attestedAt")),
                             documents: documents,
                             waiverReference: consent.has("waiverReference") ? try consent.string("waiverReference") : nil),
            ethics: try Ethics(try f.object("ethics")),
            battery: BatteryRef(id: try battery.string("id"), version: try battery.string("version"),
                                sha256: try battery.text("sha256", SHA256Digest.init(hex:)))
        )
        format = try f.string("format")
    }
}

extension DocumentRef {
    init(_ f: Fields) throws(DecodeError) {
        let id = try f.string("id")
        guard !id.isEmpty else { throw .invalid(f.keyPath("id")) }
        guard let ref = DocumentRef(id: id, version: try f.string("version")) else {
            throw .invalid(f.keyPath("version"))
        }
        self = ref
    }

    var json: JSON { .object([("id", .string(id)), ("version", .string(version))]) }
}

extension Ethics {
    init(_ f: Fields) throws(DecodeError) {
        self.init(approvalID: try f.string("approvalID"), protocolVersion: try f.string("protocolVersion"),
                  safetyProtocolVersion: try f.string("safetyProtocolVersion"),
                  monitoringDisclosureVersion: f.has("monitoringDisclosureVersion")
                      ? try f.string("monitoringDisclosureVersion") : nil)
    }

    var json: JSON {
        var o = ObjectBuilder()
        o.add("approvalID", approvalID)
        o.add("protocolVersion", protocolVersion)
        o.add("safetyProtocolVersion", safetyProtocolVersion)
        o.add("monitoringDisclosureVersion", optional: monitoringDisclosureVersion)
        return o.json
    }
}

// MARK: - Field access

/// The members of one JSON object, with typed accessors that report the key path on failure.
struct Fields {
    let path: String
    let members: [String: JSON]

    init(_ json: JSON, path: String) throws(DecodeError) {
        guard case .object(let pairs) = json else {
            throw path.isEmpty ? .notAnObject : .invalid(path)
        }
        self.path = path
        members = JSON.members(pairs)
    }

    func keyPath(_ key: String) -> String { path.isEmpty ? key : "\(path).\(key)" }

    func has(_ key: String) -> Bool { members[key] != nil }

    func isNull(_ key: String) -> Bool {
        if case .null = members[key] { true } else { false }
    }

    /// Refuses a key outside `allowed`, as the schema keyword `additionalProperties: false` does.
    func only(_ allowed: Set<String>) throws(DecodeError) {
        if let extra = members.keys.sorted().first(where: { !allowed.contains($0) }) { throw .unexpected(keyPath(extra)) }
    }

    func read<T>(_ key: String, _ convert: (JSON) -> T?) throws(DecodeError) -> T {
        guard let json = members[key] else { throw .missing(keyPath(key)) }
        guard let value = convert(json) else { throw .invalid(keyPath(key)) }
        return value
    }

    func object(_ key: String) throws(DecodeError) -> Fields {
        guard let json = members[key] else { throw .missing(keyPath(key)) }
        return try Fields(json, path: keyPath(key))
    }

    func array(_ key: String) throws(DecodeError) -> [JSON] {
        try read(key) { if case .array(let items) = $0 { items } else { nil } }
    }

    func string(_ key: String) throws(DecodeError) -> String {
        try read(key) { if case .string(let s) = $0 { s } else { nil } }
    }

    func text<T>(_ key: String, _ make: (String) -> T?) throws(DecodeError) -> T {
        try read(key) { if case .string(let s) = $0 { make(s) } else { nil } }
    }

    func bool(_ key: String) throws(DecodeError) -> Bool {
        try read(key) { if case .bool(let b) = $0 { b } else { nil } }
    }

    func int(_ key: String) throws(DecodeError) -> Int {
        try read(key) { $0.intValue }
    }

    func double(_ key: String) throws(DecodeError) -> Double {
        try read(key) { $0.doubleValue }
    }

    /// Uptime seconds, which are never negative.
    func uptime(_ key: String) throws(DecodeError) -> Double {
        try read(key) { $0.doubleValue.flatMap { $0 >= 0 ? $0 : nil } }
    }

    func positive(_ key: String) throws(DecodeError) -> Double {
        try read(key) { $0.doubleValue.flatMap { $0 > 0 ? $0 : nil } }
    }

    func optionalDouble(_ key: String) throws(DecodeError) -> Double? {
        has(key) ? try double(key) : nil
    }

    /// `force` is never negative.
    func optionalForce(_ key: String) throws(DecodeError) -> Double? {
        has(key) ? try read(key) { $0.doubleValue.flatMap { $0 >= 0 ? $0 : nil } } : nil
    }

    func enumeration<T: RawRepresentable<String>>(_ key: String) throws(DecodeError) -> T {
        try text(key, T.init(rawValue:))
    }

    func contact(_ key: String) throws(DecodeError) -> ContactID {
        try read(key) { $0.intValue.flatMap(ContactID.init) }
    }

    func index<Of>(_ key: String) throws(DecodeError) -> ZeroBased<Of> {
        try read(key) { $0.intValue.flatMap(ZeroBased<Of>.init(_:)) }
    }

    func bits(_ key: String) throws(DecodeError) -> TouchProperties {
        try read(key) { $0.intValue.flatMap { (0...31).contains($0) ? TouchProperties(rawValue: $0) : nil } }
    }

    /// `value` and `skipped` of a response or a safety flag. A skip has no value, and an answer has one.
    func answer() throws(DecodeError) -> Int? {
        let skipped = try bool("skipped")
        let value: Int? = has("value") ? try int("value") : nil
        guard skipped == (value == nil) else { throw .invalid(keyPath("value")) }
        return value
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
    mutating func add(_ key: String, _ value: Bool) { pairs.append((key, .bool(value))) }

    mutating func add(_ key: String, optional value: String?) {
        if let value { add(key, value) }
    }

    mutating func add(_ key: String, _ value: Double) throws(LineCodec.EncodeError) {
        guard let number = JSON.double(value) else { throw .nonFinite(key) }
        pairs.append((key, number))
    }

    mutating func add(_ key: String, optional value: Double?) throws(LineCodec.EncodeError) {
        if let value { try add(key, value) }
    }
}
