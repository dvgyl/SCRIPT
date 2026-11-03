import CommonCrypto
import CryptoKit
import Foundation

// MARK: - Instruments

/// The questionnaire instruments from `contract/instruments.json`. The app shows item text and records values only;
/// it reads no scoring rule, so it cannot compute a score.
public struct InstrumentSet: Hashable, Sendable {
    public let instruments: [Instrument]

    public init(data: Data) throws(DecodeError) {
        let f = try Fields(try JSON.decodeDocument(data), path: "")
        var instruments: [Instrument] = []
        for (n, item) in try f.array("instruments").enumerated() {
            instruments.append(try Instrument(try Fields(item, path: "instruments[\(n)]")))
        }
        self.instruments = instruments
    }

    public func instrument(_ id: InstrumentID) -> Instrument? { instruments.first { $0.id == id } }
}

public struct Instrument: Hashable, Sendable {
    public struct Option: Hashable, Sendable {
        public let value: Int
        public let label: String
    }

    public struct Item: Hashable, Sendable {
        public let id: ItemID
        public let text: String
        public let options: [Option]
        /// False for an item outside the scoring rule, such as `difficulty`. The app shows such an item without
        /// the instrument stem, as the published form does.
        public let scored: Bool
        public let safety: Bool
    }

    public let id: InstrumentID
    public let version: String
    public let title: String
    /// `verified` when the text matches the published form word for word.
    public let textStatus: String
    /// The lowest validated age.
    public let minimumAge: Int
    public let locale: LocaleTag
    public let stem: String
    public let items: [Item]

    /// The app refuses an instrument whose text is not verified.
    public var isVerified: Bool { textStatus == "verified" }
    public var hasSafetyItem: Bool { items.contains(where: \.safety) }
    public func item(_ id: ItemID) -> Item? { items.first { $0.id == id } }

    init(_ f: Fields) throws(DecodeError) {
        id = try f.text("id", InstrumentID.init(_:))
        version = try f.string("version")
        title = try f.string("title")
        textStatus = try f.string("textStatus")
        minimumAge = try f.object("ages").int("min")
        locale = try f.text("locale", LocaleTag.init(_:))
        stem = try f.string("stem")
        var items: [Item] = []
        for (n, json) in try f.array("items").enumerated() {
            let item = try Fields(json, path: f.keyPath("items[\(n)]"))
            var options: [Option] = []
            for (k, option) in try item.array("options").enumerated() {
                let o = try Fields(option, path: item.keyPath("options[\(k)]"))
                options.append(Option(value: try o.int("value"), label: try o.string("label")))
            }
            items.append(Item(id: try item.text("id", ItemID.init(_:)), text: try item.string("text"), options: options,
                              scored: try item.bool("scored"), safety: try item.bool("safety")))
        }
        self.items = items
    }
}

// MARK: - Study configuration

/// A study configuration (docs/DESIGN.md section 14). `init(data:)` applies the schema rules from
/// `contract/config.schema.json`; `validate(config:battery:instruments:)` applies the rules that a schema cannot express.
public struct StudyConfiguration: Hashable, Sendable {
    public struct Study: Hashable, Sendable {
        public var id: Code
        public var version: String
        public var protocolVersion: String
    }

    public enum Hands: String, Hashable, Sendable { case dominant, left, right, both }

    public struct TaskEntry: Hashable, Sendable {
        public var task: TaskID
        public var practice: Bool
        /// The maximum number of attempts, 1 to 5.
        public var attempts: Int
        public var hands: Hands
    }

    public enum BlockPosition: String, Hashable, Sendable { case beforeTasks, afterTasks }

    public struct QuestionnaireEntry: Hashable, Sendable {
        public var administration: Administration
        public var ageBand: AgeBand
        public var position: BlockPosition
        public var instruments: [InstrumentID]
    }

    public struct LocaleTexts: Hashable, Sendable {
        public var instructionVersion: String
        public var instructions: [String: String]
    }

    public struct RemoteReview: Hashable, Sendable {
        public var reviewerRole: String
        public var imminentReviewWithinHours: Double
        public var routineReviewWithinWeekdays: Double
        public var exportEveryHours: Double
        public var monitoringDisclosureVersion: String
    }

    public struct Safety: Hashable, Sendable {
        public var actionCodes: [Code]
        public var remoteReview: RemoteReview?
        public var crisisLists: [CrisisList]
    }

    public struct ConsentEntry: Hashable, Sendable {
        public var model: ConsentModel
        public var documents: [DocumentRef]
        public var waiverReference: String?
    }

    public struct SelfAdministered: Hashable, Sendable {
        /// The hours after `openedAt` in which an interrupted self-administered session can resume.
        public var resumeWithinHours: Double
        /// The window when the configuration has no `selfAdministered` member.
        public static let defaultResumeWithinHours = 24.0
    }

    public struct Export: Hashable, Sendable {
        /// The raw 32-byte X25519 public key of the study, or nil for the demo configuration.
        public var recipientPublicKey: Data?
        /// The SHA-256 of `recipientPublicKey`, when the configuration states it. `validate` checks that it matches.
        public var keyID: SHA256Digest?
    }

    public var demo: Bool
    public var study: Study
    public var sites: [Code]
    public var jurisdictions: [Jurisdiction]
    public var administration: [Administration]
    public var battery: BatteryRef
    public var tasks: [TaskEntry]
    public var questionnaires: [QuestionnaireEntry]
    public var locales: [String: LocaleTexts]
    public var safety: Safety
    /// Keyed by age band text, for example `13-17`.
    public var consentModel: [String: ConsentEntry]
    public var ethics: Ethics
    public var export: Export
    public var operatorPin: OperatorPin?
    public var selfAdministered: SelfAdministered
    /// SHA-256 of the imported file, recorded as `study.configSHA256` in every session.
    public let sha256: SHA256Digest

    public init(data: Data) throws(DecodeError) {
        let f = try Fields(try JSON.decodeDocument(data), path: "")
        try f.only(["format", "version", "demo", "study", "sites", "jurisdictions", "administration", "battery", "tasks",
                    "questionnaires", "locales", "safety", "consentModel", "ethics", "export", "operatorPin",
                    "selfAdministered"])
        guard try f.string("format") == "script.config" else { throw .invalid("format") }
        guard try f.string("version") == "1.0" else { throw .invalid("version") }
        demo = try f.bool("demo")

        let study = try f.object("study")
        try study.only(["id", "version", "protocolVersion"])
        self.study = Study(id: try study.text("id", Code.init(_:)), version: try study.string("version"),
                           protocolVersion: try study.string("protocolVersion"))
        sites = try f.uniqueList("sites") { $0.stringValue.flatMap(Code.init(_:)) }
        jurisdictions = try f.uniqueList("jurisdictions") { $0.stringValue.flatMap(Jurisdiction.init(_:)) }
        administration = try f.uniqueList("administration") { $0.stringValue.flatMap(Administration.init(rawValue:)) }

        let battery = try f.object("battery")
        try battery.only(["id", "version", "sha256"])
        self.battery = BatteryRef(id: try battery.string("id"), version: try battery.string("version"),
                                  sha256: try battery.text("sha256", SHA256Digest.init(hex:)))

        tasks = try f.objects("tasks", minimum: 1) { t throws(DecodeError) in
            try t.only(["task", "practice", "attempts", "hands"])
            let attempts = try t.int("attempts")
            guard (1...5).contains(attempts) else { throw .invalid(t.keyPath("attempts")) }
            return TaskEntry(task: try t.text("task", TaskID.init(_:)), practice: try t.bool("practice"),
                             attempts: attempts, hands: try t.enumeration("hands"))
        }
        questionnaires = try f.objects("questionnaires", minimum: 0) { q throws(DecodeError) in
            try q.only(["administration", "ageBand", "position", "instruments"])
            let instruments: [InstrumentID] = try q.list("instruments", minimum: 1) {
                $0.stringValue.flatMap(InstrumentID.init(_:))
            }
            return QuestionnaireEntry(administration: try q.enumeration("administration"), ageBand: try q.enumeration("ageBand"),
                                      position: try q.enumeration("position"), instruments: instruments)
        }

        let locales = try f.object("locales")
        guard !locales.members.isEmpty else { throw .invalid("locales") }
        var texts: [String: LocaleTexts] = [:]
        for key in locales.members.keys {
            let l = try locales.object(key)
            try l.only(["instructionVersion", "instructions"])
            let instructions = try l.object("instructions")
            var map: [String: String] = [:]
            for name in instructions.members.keys {
                let text = try instructions.string(name)
                guard !text.isEmpty else { throw .invalid(instructions.keyPath(name)) }
                map[name] = text
            }
            texts[key] = LocaleTexts(instructionVersion: try l.string("instructionVersion"), instructions: map)
        }
        self.locales = texts

        let safety = try f.object("safety")
        try safety.only(["actionCodes", "remoteReview", "crisisLists"])
        var remoteReview: RemoteReview?
        if !safety.isNull("remoteReview") {
            let r = try safety.object("remoteReview")
            try r.only(["reviewerRole", "imminentReviewWithinHours", "routineReviewWithinWeekdays", "exportEveryHours",
                        "monitoringDisclosureVersion"])
            remoteReview = RemoteReview(
                reviewerRole: try r.nonEmptyString("reviewerRole"),
                imminentReviewWithinHours: try r.positive("imminentReviewWithinHours"),
                routineReviewWithinWeekdays: try r.positive("routineReviewWithinWeekdays"),
                exportEveryHours: try r.positive("exportEveryHours"),
                monitoringDisclosureVersion: try r.nonEmptyString("monitoringDisclosureVersion")
            )
        }
        self.safety = Safety(
            actionCodes: try safety.uniqueList("actionCodes") { $0.stringValue.flatMap(Code.init(_:)) },
            remoteReview: remoteReview,
            crisisLists: try safety.objects("crisisLists", minimum: 0) { c throws(DecodeError) in try CrisisList(c) }
        )

        let consent = try f.object("consentModel")
        var consentModel: [String: ConsentEntry] = [:]
        for key in consent.members.keys {
            let c = try consent.object(key)
            try c.only(["model", "documents", "waiverReference"])
            consentModel[key] = ConsentEntry(
                model: try c.enumeration("model"),
                documents: try c.objects("documents", minimum: 0) { d throws(DecodeError) in
                    try d.only(["id", "version"])
                    return try DocumentRef(d)
                },
                waiverReference: c.has("waiverReference") ? try c.string("waiverReference") : nil
            )
        }
        self.consentModel = consentModel

        let ethics = try f.object("ethics")
        try ethics.only(["approvalID", "protocolVersion", "safetyProtocolVersion", "monitoringDisclosureVersion"])
        self.ethics = try Ethics(ethics)

        let export = try f.object("export")
        try export.only(["recipientPublicKey", "keyID"])
        var key: Data?
        if !export.isNull("recipientPublicKey") {
            key = try export.read("recipientPublicKey") { json -> Data? in
                guard let text = json.stringValue, text.utf8.count == 44, text.hasSuffix("="),
                      let raw = Data(base64Encoded: text), raw.count == 32 else { return nil }
                return raw
            }
        }
        self.export = Export(recipientPublicKey: key,
                             keyID: export.isNull("keyID") ? nil : try export.text("keyID", SHA256Digest.init(hex:)))

        if f.isNull("operatorPin") {
            operatorPin = nil
        } else {
            let pin = try f.object("operatorPin")
            try pin.only(["salt", "iterations", "pbkdf2Sha256"])
            let salt = try pin.text("salt") { Data(base64Encoded: $0).flatMap { $0.count >= 16 ? $0 : nil } }
            let iterations = try pin.int("iterations")
            guard (OperatorPin.minimumIterations...Int(UInt32.max)).contains(iterations) else {
                throw .invalid(pin.keyPath("iterations"))
            }
            let key = try pin.text("pbkdf2Sha256", SHA256Digest.init(hex:))
            operatorPin = OperatorPin(salt: salt, iterations: iterations, key: key.bytes)
        }
        if f.has("selfAdministered") {
            let selfAdministered = try f.object("selfAdministered")
            try selfAdministered.only(["resumeWithinHours"])
            self.selfAdministered = SelfAdministered(resumeWithinHours: try selfAdministered.positive("resumeWithinHours"))
        } else {
            selfAdministered = SelfAdministered(resumeWithinHours: SelfAdministered.defaultResumeWithinHours)
        }
        sha256 = SHA256Digest(of: data)
    }

    /// The study block that a session records in `sessionOpened.study`.
    public func studyRef(site: Code) -> StudyRef {
        StudyRef(id: study.id, site: site, configID: study.id, configVersion: study.version, configSHA256: sha256,
                 protocolVersion: study.protocolVersion)
    }
}

/// One crisis list from the official pages of each service, with the date it was checked.
public struct CrisisList: Hashable, Sendable {
    public struct Entry: Hashable, Sendable {
        public let name: String
        public let contact: String
        public let url: String?
    }

    public let jurisdiction: Jurisdiction
    public let locale: String
    public let version: String
    public let verifiedOn: String
    public let entries: [Entry]

    init(_ f: Fields) throws(DecodeError) {
        try f.only(["jurisdiction", "locale", "version", "verifiedOn", "entries"])
        jurisdiction = try f.text("jurisdiction", Jurisdiction.init(_:))
        locale = try f.string("locale")
        version = try f.string("version")
        verifiedOn = try f.string("verifiedOn")
        entries = try f.objects("entries", minimum: 1) { e throws(DecodeError) in
            try e.only(["name", "contact", "url"])
            return Entry(name: try e.nonEmptyString("name"), contact: try e.nonEmptyString("contact"),
                         url: e.has("url") ? try e.string("url") : nil)
        }
    }
}

/// The operator PIN as PBKDF2-HMAC-SHA256 of the UTF-8 PIN with the decoded salt and a 32-byte key
/// (docs/DESIGN.md section 14). A search of every PIN finds a short PIN behind a fast hash, so the derivation is slow.
public struct OperatorPin: Hashable, Sendable {
    public static let minimumIterations = 600_000

    /// At least 16 bytes.
    public let salt: Data
    public let iterations: Int
    let key: [UInt8]

    init(salt: Data, iterations: Int, key: [UInt8]) {
        self.salt = salt
        self.iterations = iterations
        self.key = key
    }

    /// The stored form of a new PIN, or nil for a salt shorter than 16 bytes or fewer than 600,000 iterations.
    public init?(pin: String, salt: Data, iterations: Int = minimumIterations) {
        guard salt.count >= 16, (Self.minimumIterations...Int(UInt32.max)).contains(iterations),
              let key = Self.derive(pin, salt: salt, iterations: iterations)
        else { return nil }
        self.init(salt: salt, iterations: iterations, key: key)
    }

    /// Compares every byte of the two keys, so the time does not depend on where they differ.
    public func matches(_ pin: String) -> Bool {
        guard let candidate = Self.derive(pin, salt: salt, iterations: iterations), candidate.count == key.count else {
            return false
        }
        var difference: UInt8 = 0
        for i in candidate.indices { difference |= candidate[i] ^ key[i] }
        return difference == 0
    }

    private static func derive(_ pin: String, salt: Data, iterations: Int) -> [UInt8]? {
        var key = [UInt8](repeating: 0, count: 32)
        let status = pin.withCString { password in
            salt.withUnsafeBytes { saltBytes in
                CCKeyDerivationPBKDF(CCPBKDFAlgorithm(kCCPBKDF2), password, pin.utf8.count,
                                     saltBytes.bindMemory(to: UInt8.self).baseAddress, saltBytes.count,
                                     CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA256), UInt32(iterations), &key, key.count)
            }
        }
        return status == kCCSuccess ? key : nil
    }
}

// MARK: - Rules

/// A rule from `scriptkin.studyconfig.validate` that a configuration breaks. The description is the Python text.
public enum ConfigProblem: Error, Hashable, Sendable, CustomStringConvertible {
    case batteryMismatch
    case unknownTask(TaskID)
    case qualificationTask(TaskID)
    case unknownInstrument(InstrumentID)
    case unverifiedInstrument(InstrumentID)
    case notValidatedForAgeBand(InstrumentID, AgeBand)
    case safetyItemWithoutRemoteReview(InstrumentID)
    case imminentReviewTooLong
    case routineReviewTooLong
    case exportIntervalTooLong
    case resumeWindowTooLong
    case noCrisisList(Jurisdiction)
    case noConsentModel(AgeBand)
    case noExportKey
    case noOperatorPin
    case keyIDMismatch

    public var description: String {
        switch self {
        case .batteryMismatch: "battery.sha256 does not match the battery that this build ships"
        case .unknownTask(let t): "task '\(t)' is not in the battery"
        case .qualificationTask(let t): "task '\(t)' belongs to qualification sessions, not study sessions"
        case .unknownInstrument(let i): "instrument '\(i)' is not in contract/instruments.json"
        case .unverifiedInstrument(let i): "instrument '\(i)' text is not verified"
        case .notValidatedForAgeBand(let i, let band): "instrument '\(i)' is not validated for age band \(band.rawValue)"
        case .safetyItemWithoutRemoteReview(let i):
            "self-administered block uses safety item in '\(i)' without safety.remoteReview"
        case .imminentReviewTooLong: "safety.remoteReview.imminentReviewWithinHours exceeds 24"
        case .routineReviewTooLong: "safety.remoteReview.routineReviewWithinWeekdays exceeds 1"
        case .exportIntervalTooLong: "safety.remoteReview.exportEveryHours is longer than the review interval"
        case .resumeWindowTooLong: "selfAdministered.resumeWithinHours is longer than the review interval"
        case .noCrisisList(let j): "no crisis list for jurisdiction \(j)"
        case .noConsentModel(let band): "consentModel has no entry for age band \(band.rawValue)"
        case .noExportKey: "a study configuration needs export.recipientPublicKey"
        case .noOperatorPin: "a study configuration needs operatorPin"
        case .keyIDMismatch: "export.keyID is not the SHA-256 of export.recipientPublicKey"
        }
    }
}

/// The rules of `scriptkin.studyconfig.validate`, in the same order, and then the `keyID` rule of docs/DESIGN.md
/// section 17. An empty list means the configuration passes.
public func validate(config: StudyConfiguration, battery: Battery, instruments: InstrumentSet) -> [ConfigProblem] {
    var problems: [ConfigProblem] = []
    if config.battery.sha256 != battery.sha256 { problems.append(.batteryMismatch) }
    for entry in config.tasks {
        if battery.task(entry.task) == nil {
            problems.append(.unknownTask(entry.task))
        } else if Battery.qualificationTasks.contains(entry.task) {
            problems.append(.qualificationTask(entry.task))
        }
    }
    for block in config.questionnaires {
        for id in block.instruments {
            guard let spec = instruments.instrument(id) else {
                problems.append(.unknownInstrument(id))
                continue
            }
            if !spec.isVerified { problems.append(.unverifiedInstrument(id)) }
            if block.ageBand.lowestAge < spec.minimumAge { problems.append(.notValidatedForAgeBand(id, block.ageBand)) }
            if spec.hasSafetyItem, block.administration == .selfAdministered {
                guard let review = config.safety.remoteReview else {
                    problems.append(.safetyItemWithoutRemoteReview(id))
                    continue
                }
                if review.imminentReviewWithinHours > 24 { problems.append(.imminentReviewTooLong) }
                if review.routineReviewWithinWeekdays > 1 { problems.append(.routineReviewTooLong) }
                if review.exportEveryHours > review.imminentReviewWithinHours { problems.append(.exportIntervalTooLong) }
                if config.selfAdministered.resumeWithinHours > review.imminentReviewWithinHours {
                    problems.append(.resumeWindowTooLong)
                }
            }
        }
    }
    let lists = Set(config.safety.crisisLists.map(\.jurisdiction))
    if !config.questionnaires.isEmpty {
        for jurisdiction in config.jurisdictions where !lists.contains(jurisdiction) {
            problems.append(.noCrisisList(jurisdiction))
        }
    }
    for band in AgeBand.allCases where config.consentModel[band.rawValue] == nil {
        problems.append(.noConsentModel(band))
    }
    if !config.demo {
        if config.export.recipientPublicKey == nil { problems.append(.noExportKey) }
        if config.operatorPin == nil { problems.append(.noOperatorPin) }
    }
    if let stated = config.export.keyID, stated != config.export.recipientPublicKey.map({ SHA256Digest(of: $0) }) {
        problems.append(.keyIDMismatch)
    }
    return problems
}

/// What `decide` needs from the frozen configuration and the shipped contract for one session.
public struct SessionRules: Sendable {
    public let battery: Battery
    public let instruments: InstrumentSet
    /// The list for the session's jurisdiction, in its locale when the configuration has one.
    public let crisisList: CrisisList?
    public let actionCodes: [Code]
    public let remoteReview: StudyConfiguration.RemoteReview?
    /// The hours after `openedAt` in which an interrupted self-administered session can resume.
    public let resumeWithinHours: Double

    public init(battery: Battery, instruments: InstrumentSet, configuration: StudyConfiguration,
                jurisdiction: Jurisdiction, locale: LocaleTag) {
        self.battery = battery
        self.instruments = instruments
        let lists = configuration.safety.crisisLists.filter { $0.jurisdiction == jurisdiction }
        crisisList = lists.first { $0.locale == locale.rawValue } ?? lists.first
        actionCodes = configuration.safety.actionCodes
        remoteReview = configuration.safety.remoteReview
        resumeWithinHours = configuration.selfAdministered.resumeWithinHours
    }
}

// MARK: - Field helpers

extension JSON {
    var stringValue: String? {
        if case .string(let s) = self { s } else { nil }
    }
}

extension Fields {
    func nonEmptyString(_ key: String) throws(DecodeError) -> String {
        try read(key) { $0.stringValue.flatMap { $0.isEmpty ? nil : $0 } }
    }

    /// An array whose items each convert, with at least `minimum` items.
    func list<T>(_ key: String, minimum: Int, _ convert: (JSON) -> T?) throws(DecodeError) -> [T] {
        let items = try array(key)
        guard items.count >= minimum else { throw .invalid(keyPath(key)) }
        var out: [T] = []
        for (n, item) in items.enumerated() {
            guard let value = convert(item) else { throw .invalid(keyPath("\(key)[\(n)]")) }
            out.append(value)
        }
        return out
    }

    /// A list with at least one item and no repeated item, as the schema keywords `minItems` and `uniqueItems` require.
    func uniqueList<T: Hashable>(_ key: String, _ convert: (JSON) -> T?) throws(DecodeError) -> [T] {
        let out = try list(key, minimum: 1, convert)
        guard Set(out).count == out.count else { throw .invalid(keyPath(key)) }
        return out
    }

    func objects<T>(_ key: String, minimum: Int, _ make: (Fields) throws(DecodeError) -> T) throws(DecodeError) -> [T] {
        let items = try array(key)
        guard items.count >= minimum else { throw .invalid(keyPath(key)) }
        var out: [T] = []
        for (n, item) in items.enumerated() {
            out.append(try make(try Fields(item, path: keyPath("\(key)[\(n)]"))))
        }
        return out
    }
}
