import CryptoKit
import Foundation

// MARK: - Reading a journal

/// A journal file that breaks integrity rule I1, I2, I3, I4, or I6.
public enum JournalError: Error, Hashable, Sendable {
    case empty
    /// The final line has no LF.
    case unterminated(line: Int)
    case decode(line: Int, DecodeError)
    case fold(FoldError)

    public var line: Int {
        switch self {
        case .empty: 0
        case .unterminated(let line), .decode(let line, _): line
        case .fold(let error): error.line
        }
    }
}

public enum Journal {
    /// Decodes and folds every line. Each line must end in LF.
    public static func read(_ data: Data) throws(JournalError) -> SessionState {
        let scan = scan(data)
        if let failure = scan.failure { throw failure }
        guard let state = scan.state else { throw .empty }
        guard scan.tailBytes == 0 else { throw .unterminated(line: scan.lines) }
        return state
    }

    /// What a relaunch finds in a journal. Only the final line can be torn by a crash, so a complete line that does
    /// not decode or fold is damage, and the bytes after the last LF are a torn tail, whatever they hold.
    public enum Recovery: Hashable, Sendable {
        case clean(SessionState)
        case truncated(SessionState, bytes: Int)
        /// The first complete line that does not decode or fold. Line 0 when no complete line exists.
        case damaged(line: Int)
    }

    public static func recover(_ data: Data) -> Recovery {
        let scan = scan(data)
        guard let state = scan.state, scan.failure == nil else { return .damaged(line: scan.failure?.line ?? 0) }
        return scan.tailBytes == 0 ? .clean(state) : .truncated(state, bytes: scan.tailBytes)
    }

    struct Scan {
        var state: SessionState?
        /// Complete lines that decoded and folded.
        var lines = 0
        /// Bytes after the last LF.
        var tailBytes = 0
        /// The first complete line that does not decode or fold.
        var failure: JournalError?
    }

    static func scan(_ data: Data) -> Scan {
        data.withUnsafeBytes { raw in
            let bytes = raw.bindMemory(to: UInt8.self)
            var scan = Scan()
            var start = 0
            while start < bytes.count, let found = memchr(raw.baseAddress! + start, 0x0A, bytes.count - start) {
                let end = raw.baseAddress!.distance(to: UnsafeRawPointer(found))
                let line = UnsafeBufferPointer(rebasing: bytes[start..<end])
                let entry: Entry
                do throws(DecodeError) {
                    entry = try LineCodec.decode(line)
                } catch {
                    scan.failure = .decode(line: scan.lines, error)
                    return scan
                }
                do throws(FoldError) {
                    if scan.state == nil {
                        scan.state = try SessionState(opening: entry)
                    } else {
                        try scan.state!.apply(entry)
                    }
                } catch {
                    scan.failure = .fold(error)
                    return scan
                }
                scan.lines += 1
                start = end + 1
            }
            scan.tailBytes = bytes.count - start
            return scan
        }
    }
}

// MARK: - Writer

/// Appends journal lines through one `AsyncStream` with one consumer, so line order equals call order.
/// It flushes at 64 KB, at 250 ms, or at `contactEnded`. It runs fsync at `trialEnded`, at `sessionClosed`,
/// and at `sync()`, which the app calls when the scene goes to the background.
public actor JournalWriter {
    public enum WriteError: Error, Hashable, Sendable {
        case nonFinite(String)
        case closed
        case io(Int32)
    }

    enum Durability: Int, Comparable {
        case buffer, flush, sync

        init(_ kind: LineKind) {
            switch kind {
            case .contactEnded: self = .flush
            case .trialEnded, .sessionClosed: self = .sync
            default: self = .buffer
            }
        }

        static func < (a: Durability, b: Durability) -> Bool { a.rawValue < b.rawValue }
    }

    private enum Item: Sendable {
        case lines(Data, Durability)
        case tick
        case barrier(CheckedContinuation<Void, any Error>, close: Bool)
    }

    private let continuation: AsyncStream<Item>.Continuation
    private let ticker: Task<Void, Never>
    private let descriptor: Int32
    private let flushBytes: Int
    private var buffer = Data()
    private var failure: WriteError?
    private var isClosed = false

    public init(
        appendingTo url: URL, flushBytes: Int = 64 * 1024, flushInterval: Duration = .milliseconds(250)
    ) throws(WriteError) {
        let descriptor = open(url.path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw .io(errno) }
        self.descriptor = descriptor
        self.flushBytes = flushBytes
        let (stream, continuation) = AsyncStream.makeStream(of: Item.self)
        self.continuation = continuation
        ticker = Task {
            while !Task.isCancelled {
                try? await Task.sleep(for: flushInterval)
                continuation.yield(.tick)
            }
        }
        Task { await self.consume(stream) }
    }

    /// Encodes the entries on the caller's thread and queues them. Nothing is queued when one value is not finite.
    public nonisolated func append(_ entries: [Entry]) throws(WriteError) {
        var data = Data()
        var durability = Durability.buffer
        for entry in entries {
            do throws(LineCodec.EncodeError) {
                data.append(try LineCodec.encode(entry))
            } catch {
                switch error {
                case .nonFinite(let key): throw .nonFinite(key)
                }
            }
            durability = max(durability, Durability(entry.event.kind))
        }
        if case .terminated = continuation.yield(.lines(data, durability)) { throw .closed }
    }

    /// Returns when every line queued before the call is written and synced to storage.
    public nonisolated func sync() async throws {
        try await barrier(close: false)
    }

    /// Writes and syncs every queued line, then closes the file. Later appends throw `closed`.
    public nonisolated func close() async throws {
        try await barrier(close: true)
    }

    private nonisolated func barrier(close: Bool) async throws {
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
            if case .terminated = continuation.yield(.barrier(k, close: close)) { k.resume(throwing: WriteError.closed) }
        }
    }

    private func consume(_ stream: AsyncStream<Item>) async {
        for await item in stream {
            switch item {
            case .lines(let data, let durability):
                guard !isClosed else { continue }
                buffer.append(data)
                if durability == .sync {
                    flush()
                    fullSync()
                } else if durability == .flush || buffer.count >= flushBytes {
                    flush()
                }
            case .tick:
                flush()
            case .barrier(let k, let close):
                guard !isClosed else {
                    k.resume(throwing: WriteError.closed)
                    continue
                }
                flush()
                fullSync()
                if close {
                    isClosed = true
                    ticker.cancel()
                    if Darwin.close(descriptor) != 0, failure == nil { failure = .io(errno) }
                    continuation.finish()
                }
                if let failure { k.resume(throwing: failure) } else { k.resume() }
            }
        }
    }

    private func flush() {
        guard !buffer.isEmpty, !isClosed else { return }
        if failure == nil, !buffer.withUnsafeBytes({ writeAll(descriptor, $0) }) { failure = .io(errno) }
        buffer.removeAll(keepingCapacity: true)
    }

    private func fullSync() {
        guard !isClosed, failure == nil else { return }
        if !syncDescriptor(descriptor) { failure = .io(errno) }
    }
}

// MARK: - Export format

/// How `publish` writes an export (docs/DESIGN.md section 17).
public enum ExportPolicy: Sendable {
    /// A plaintext `journal.jsonl`. Only the demo configuration writes plaintext exports.
    case demo
    /// `journal.jsonl.hpke`, sealed to the study's public key.
    case encrypted(recipient: Curve25519.KeyAgreement.PublicKey, keyID: String)

    public enum PolicyError: Error, Hashable, Sendable {
        case noExportKey
        case invalidKey
    }

    /// Encrypted when the configuration has an export key. Without a key, only a demo configuration may export.
    /// When `keyID` is null, the key ID is the SHA-256 of the raw public key, so the analyst can still pick the key.
    public init(configuration: StudyConfiguration) throws(PolicyError) {
        guard let raw = configuration.export.recipientPublicKey else {
            guard configuration.demo else { throw .noExportKey }
            self = .demo
            return
        }
        guard let key = try? Curve25519.KeyAgreement.PublicKey(rawRepresentation: raw) else { throw .invalidKey }
        self = .encrypted(recipient: key, keyID: configuration.export.keyID ?? SHA256Digest(of: raw).hex)
    }
}

/// HPKE base mode, DHKEM(X25519, HKDF-SHA256), HKDF-SHA256, ChaCha20-Poly1305. The associated data is the session ID.
enum ExportCipher {
    static let info = Data("SCRIPT export 1.0".utf8)
    static let suite = HPKE.Ciphersuite.Curve25519_SHA256_ChachaPoly
    static let scheme = "hpke-base"
    static let suiteName = "X25519-HKDF-SHA256-ChaCha20Poly1305"

    static func seal(_ plaintext: Data, sessionID: String, recipient: Curve25519.KeyAgreement.PublicKey) throws
        -> (ciphertext: Data, encapsulatedKey: Data)
    {
        var sender = try HPKE.Sender(recipientKey: recipient, ciphersuite: suite, info: info)
        let ciphertext = try sender.seal(plaintext, authenticating: Data(sessionID.utf8))
        return (ciphertext, sender.encapsulatedKey)
    }

    static func open(_ ciphertext: Data, sessionID: String, encapsulatedKey: Data,
                     privateKey: Curve25519.KeyAgreement.PrivateKey) throws -> Data {
        var recipient = try HPKE.Recipient(privateKey: privateKey, ciphersuite: suite, info: info,
                                           encapsulatedKey: encapsulatedKey)
        return try recipient.open(ciphertext, authenticating: Data(sessionID.utf8))
    }
}

/// `manifest.json`: the facts that cannot be inside the journal.
struct Manifest: Hashable, Sendable {
    struct Facts: Hashable, Sendable {
        var bytes: Int
        var lines: Int
        var sha256: SHA256Digest
    }

    struct Encryption: Hashable, Sendable {
        var keyID: String
        var encapsulatedKey: Data
        var plaintext: Facts
        var ciphertextBytes: Int
        var ciphertextSHA256: SHA256Digest
    }

    enum Content: Hashable, Sendable {
        case plaintext(Facts)
        case encrypted(Encryption)
    }

    var sessionID: String
    var content: Content

    init(sessionID: String, content: Content) {
        self.sessionID = sessionID
        self.content = content
    }

    init(json: JSON) throws(DecodeError) {
        let f = try Fields(json, path: "")
        guard try f.string("format") == "script.journal" else { throw .invalid("format") }
        guard JournalFormat.isReadable(try f.string("version")) else { throw .invalid("version") }
        sessionID = try f.string("sessionID")
        if f.has("encryption") {
            let e = try f.object("encryption")
            guard try e.string("scheme") == ExportCipher.scheme else { throw .invalid("encryption.scheme") }
            guard try e.string("suite") == ExportCipher.suiteName else { throw .invalid("encryption.suite") }
            let ciphertext = try e.object("ciphertext")
            content = .encrypted(Encryption(
                keyID: try e.string("keyID"),
                encapsulatedKey: try e.read("encapsulatedKey") { $0.stringValue.flatMap { Data(base64Encoded: $0) } },
                plaintext: try Facts(try e.object("plaintext")),
                ciphertextBytes: try ciphertext.int("bytes"),
                ciphertextSHA256: try ciphertext.text("sha256", SHA256Digest.init(hex:))
            ))
        } else {
            if f.has("demo") { guard try f.bool("demo") else { throw .invalid("demo") } }
            content = .plaintext(try Facts(try f.object("journal")))
        }
    }

    /// Indented like Python `json.dumps(indent=1)`.
    var data: Data {
        var pairs: [(key: String, value: JSON)] = [
            ("format", .string("script.journal")), ("version", .string(JournalFormat.version)),
            ("sessionID", .string(sessionID)),
        ]
        switch content {
        case .plaintext(let facts):
            pairs.append(("demo", .bool(true)))
            pairs.append(("journal", facts.json))
        case .encrypted(let e):
            pairs.append(("encryption", .object([
                ("scheme", .string(ExportCipher.scheme)), ("suite", .string(ExportCipher.suiteName)),
                ("keyID", .string(e.keyID)), ("encapsulatedKey", .string(e.encapsulatedKey.base64EncodedString())),
                ("plaintext", e.plaintext.json),
                ("ciphertext", .object([("bytes", .int(e.ciphertextBytes)), ("sha256", .string(e.ciphertextSHA256.hex))])),
            ])))
        }
        return JSON.object(pairs).prettyData()
    }
}

extension Manifest.Facts {
    init(_ f: Fields) throws(DecodeError) {
        self.init(bytes: try f.int("bytes"), lines: try f.int("lines"), sha256: try f.text("sha256", SHA256Digest.init(hex:)))
    }

    init(journal data: Data, state: SessionState) {
        self.init(bytes: data.count, lines: state.session.lines, sha256: SHA256Digest(of: data))
    }

    var json: JSON { .object([("bytes", .int(bytes)), ("lines", .int(lines)), ("sha256", .string(sha256.hex))]) }
}

// MARK: - Storage

/// The app container layout and the operations that keep it consistent across crashes.
///
///     <applicationSupport>/Sessions/<uuid>.jsonl   journals not yet exported
///     <applicationSupport>/Staging/<name>/         publication in progress
///     <applicationSupport>/Deletions.jsonl         one line for each withdrawal
///     <documents>/Exports/<name>/                  verified exports, visible in Files
///     <documents>/Damaged/<uuid>.jsonl             journals with damage before the last line
public struct Storage: Sendable {
    public let sessions: URL
    public let staging: URL
    public let exports: URL
    public let damaged: URL
    public let deletionLog: URL

    /// The fault-injection seam. A test returns true for a step to stop the operation there, as a crash would.
    let stops: @Sendable (Step) -> Bool

    /// Each point between two durable writes of `publish` and `reconcile`.
    enum Step: String, CaseIterable, Sendable {
        case stagingCleared, journalStaged, manifestWritten, stagedSynced, stagedVerified, renamed, sourceRemoved
        case leftoverRemoved, movedToDamaged, recoveryStaged, recoveryCommitted, staleStagingRemoved
    }

    struct InjectedCrash: Error, Equatable {
        let step: Step
    }

    public enum StorageError: Error, Hashable, Sendable {
        case noJournal(String)
        case journal(String, JournalError)
        case notClosed(SessionState.Status)
        /// Another session, or a different copy of this one, already holds this export name.
        case exportTaken(String)
        /// The export does not match its manifest or its source.
        case verification(String)
        case io(String, Int32)
    }

    public init(applicationSupport: URL, documents: URL) {
        self.init(applicationSupport: applicationSupport, documents: documents, stops: { _ in false })
    }

    init(applicationSupport: URL, documents: URL, stops: @escaping @Sendable (Step) -> Bool) {
        sessions = applicationSupport.appendingPathComponent("Sessions", isDirectory: true)
        staging = applicationSupport.appendingPathComponent("Staging", isDirectory: true)
        deletionLog = applicationSupport.appendingPathComponent("Deletions.jsonl")
        exports = documents.appendingPathComponent("Exports", isDirectory: true)
        damaged = documents.appendingPathComponent("Damaged", isDirectory: true)
        self.stops = stops
    }

    public func journalURL(_ id: SessionID) -> URL {
        sessions.appendingPathComponent("\(id.uuid.uuidString).jsonl")
    }

    /// Creates the folders. On iOS, each folder has the file protection `completeUntilFirstUserAuthentication`,
    /// which every file created inside it inherits.
    public func createDirectories() throws {
        for folder in [sessions, staging, exports, damaged] {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try protect(folder)
        }
    }

    private func checkpoint(_ step: Step) throws {
        if stops(step) { throw InjectedCrash(step: step) }
    }

    // MARK: Reconcile

    public struct Launch: Sendable {
        public var boot: BootID
        public var uptime: Double
        public var at: WallTime
        public init(boot: BootID, uptime: Double, at: WallTime) {
            self.boot = boot
            self.uptime = uptime
            self.at = at
        }
    }

    public enum Reconciled: Hashable, Sendable {
        /// The journal has no `sessionClosed` line. Its state is now `suspended`.
        case interrupted(SessionState)
        /// Closed and not yet exported.
        case closed(SessionState)
        case published(URL)
        /// A publication that started before the crash cannot finish. The journal stays in `Sessions/`.
        case publishFailed(file: String, StorageError)
        case damaged(file: String, line: Int)
    }

    /// Runs at launch and converges from any crash point. A second launch changes no byte.
    ///
    /// An open journal loses its torn tail and gets a `recovered` line in one atomic replacement. A journal with
    /// damage before its last line moves unchanged to `Damaged/`. A closed journal whose publication had started
    /// finishes it. A staging folder whose export exists and whose journal is gone is removed.
    public func reconcile(_ launch: Launch, policy: ExportPolicy) throws -> [Reconciled] {
        try createDirectories()
        for leftover in try contents(of: sessions) where leftover.pathExtension == "recovering" {
            try FileManager.default.removeItem(at: leftover)
            try syncPath(sessions)
            try checkpoint(.leftoverRemoved)
        }
        var results: [Reconciled] = []
        var names = Set<String>()
        for journal in try contents(of: sessions) where journal.pathExtension == "jsonl" {
            let data = try Data(contentsOf: journal)
            let scan = Journal.scan(data)
            guard var state = scan.state, scan.failure == nil else {
                try moveToDamaged(journal)
                results.append(.damaged(file: journal.lastPathComponent, line: scan.failure?.line ?? 0))
                continue
            }
            let name = state.session.opened.exportName
            names.insert(name)
            var addition = Data()
            if let event = state.recovery(t: launch.uptime, at: launch.at, boot: launch.boot,
                                          truncatedBytes: scan.tailBytes) {
                for entry in try state.record([event]) { addition.append(try LineCodec.encode(entry)) }
            }
            if scan.tailBytes > 0 {
                try replace(journal, with: data.prefix(data.count - scan.tailBytes) + addition)
            } else if !addition.isEmpty {
                try appendAndSync(addition, to: journal)
                try checkpoint(.recoveryCommitted)
            }
            guard case .closed = state.phase else {
                results.append(.interrupted(state))
                continue
            }
            let started = [exports, staging].contains { exists($0.appendingPathComponent(name)) }
            guard started else {
                results.append(.closed(state))
                continue
            }
            do {
                results.append(.published(try publish(journal: journal, policy: policy)))
            } catch let error as StorageError {
                results.append(.publishFailed(file: journal.lastPathComponent, error))
            }
        }
        for folder in try contents(of: staging)
        where !names.contains(folder.lastPathComponent) && exists(exports.appendingPathComponent(folder.lastPathComponent)) {
            try FileManager.default.removeItem(at: folder)
            try syncPath(staging)
            try checkpoint(.staleStagingRemoved)
        }
        return results
    }

    /// Writes the new journal beside the old one and renames it over the old one, so the tail removal and the
    /// `recovered` line become durable together.
    private func replace(_ journal: URL, with data: Data) throws {
        let temporary = journal.appendingPathExtension("recovering")
        let descriptor = open(temporary.path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw StorageError.io("open \(temporary.lastPathComponent)", errno) }
        let written = data.withUnsafeBytes { writeAll(descriptor, $0) } && syncDescriptor(descriptor)
        let error = errno
        Darwin.close(descriptor)
        guard written else { throw StorageError.io("write \(temporary.lastPathComponent)", error) }
        try checkpoint(.recoveryStaged)
        try move(temporary, to: journal)
        try syncPath(sessions)
        try checkpoint(.recoveryCommitted)
    }

    private func moveToDamaged(_ journal: URL) throws {
        var target = damaged.appendingPathComponent(journal.lastPathComponent)
        var copy = 1
        while exists(target) {
            target = damaged.appendingPathComponent("\(journal.deletingPathExtension().lastPathComponent)-\(copy).jsonl")
            copy += 1
        }
        try move(journal, to: target)
        try syncPath(sessions)
        try syncPath(damaged)
        try checkpoint(.movedToDamaged)
    }

    // MARK: Publish

    /// Clones or encrypts the closed journal into `Staging/<name>/`, writes the manifest, syncs, verifies the staged
    /// copy (decode, fold, counts, SHA-256), and renames the folder into `Exports/`. Only then does it delete the source.
    @discardableResult
    public func publish(_ id: SessionID, policy: ExportPolicy) throws -> URL {
        try publish(journal: journalURL(id), policy: policy)
    }

    func publish(journal source: URL, policy: ExportPolicy) throws -> URL {
        guard exists(source) else { throw StorageError.noJournal(source.lastPathComponent) }
        try createDirectories()
        let data = try Data(contentsOf: source)
        let state: SessionState
        do throws(JournalError) {
            state = try Journal.read(data)
        } catch {
            throw StorageError.journal(source.lastPathComponent, error)
        }
        guard case .closed = state.phase else { throw StorageError.notClosed(state.status) }
        let name = state.session.opened.exportName
        let target = exports.appendingPathComponent(name, isDirectory: true)

        if exists(target) {
            do {
                try Self.verify(target, source: data, state: state)
            } catch is StorageError {
                throw StorageError.exportTaken(name)
            }
        } else {
            let stage = staging.appendingPathComponent(name, isDirectory: true)
            if exists(stage) { try FileManager.default.removeItem(at: stage) }
            try checkpoint(.stagingCleared)

            try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: true)
            let facts = Manifest.Facts(journal: data, state: state)
            let sessionID = state.session.opened.sessionID.description
            let manifest: Manifest
            let journalFile: URL
            switch policy {
            case .demo:
                journalFile = stage.appendingPathComponent(Export.journal)
                try FileManager.default.copyItem(at: source, to: journalFile)
                manifest = Manifest(sessionID: sessionID, content: .plaintext(facts))
            case .encrypted(let recipient, let keyID):
                journalFile = stage.appendingPathComponent(Export.encryptedJournal)
                let sealed = try ExportCipher.seal(data, sessionID: sessionID, recipient: recipient)
                try sealed.ciphertext.write(to: journalFile)
                manifest = Manifest(sessionID: sessionID, content: .encrypted(Manifest.Encryption(
                    keyID: keyID, encapsulatedKey: sealed.encapsulatedKey, plaintext: facts,
                    ciphertextBytes: sealed.ciphertext.count, ciphertextSHA256: SHA256Digest(of: sealed.ciphertext)
                )))
            }
            try checkpoint(.journalStaged)

            try manifest.data.write(to: stage.appendingPathComponent(Export.manifest))
            try checkpoint(.manifestWritten)

            try syncPath(journalFile)
            try syncPath(stage.appendingPathComponent(Export.manifest))
            try syncPath(stage)
            try syncPath(staging)
            try checkpoint(.stagedSynced)

            try Self.verify(stage, source: data, state: state)
            try checkpoint(.stagedVerified)

            try move(stage, to: target)
            try syncPath(exports)
            try syncPath(staging)
            try checkpoint(.renamed)
        }
        try FileManager.default.removeItem(at: source)
        try syncPath(sessions)
        try checkpoint(.sourceRemoved)
        return target
    }

    // MARK: Export

    public enum Export {
        public static let journal = "journal.jsonl"
        public static let encryptedJournal = "journal.jsonl.hpke"
        public static let manifest = "manifest.json"
    }

    /// Checks an export folder against the source journal that it must hold: the manifest facts (rule I5) and, for
    /// a plaintext export, every journal rule of the copy on disk. An encrypted copy is checked by its size and hash.
    static func verify(_ folder: URL, source: Data, state: SessionState) throws {
        func fail(_ what: String) -> StorageError { .verification("\(folder.lastPathComponent): \(what)") }
        let manifest = try readManifest(folder)
        guard manifest.sessionID == state.session.opened.sessionID.description else { throw fail("another session") }
        let facts = Manifest.Facts(journal: source, state: state)
        switch manifest.content {
        case .plaintext(let recorded):
            guard recorded == facts else { throw fail("the manifest does not match the source journal") }
            let copy = try Data(contentsOf: folder.appendingPathComponent(Export.journal))
            let staged = try readJournal(copy, name: folder.lastPathComponent)
            guard Manifest.Facts(journal: copy, state: staged) == recorded else {
                throw fail("the journal does not match its manifest")
            }
        case .encrypted(let e):
            guard e.plaintext == facts else { throw fail("the manifest does not match the source journal") }
            let ciphertext = try Data(contentsOf: folder.appendingPathComponent(Export.encryptedJournal))
            guard ciphertext.count == e.ciphertextBytes, SHA256Digest(of: ciphertext) == e.ciphertextSHA256 else {
                throw fail("the ciphertext does not match its manifest")
            }
        }
    }

    /// Reads an export folder as the analyst does. An encrypted export needs the study's private key. The plaintext
    /// must match the manifest and pass every journal rule, and the session must be closed.
    static func readExport(_ folder: URL, privateKey: Curve25519.KeyAgreement.PrivateKey? = nil) throws
        -> (state: SessionState, journal: Data)
    {
        let manifest = try readManifest(folder)
        let journal: Data
        let recorded: Manifest.Facts
        switch manifest.content {
        case .plaintext(let facts):
            journal = try Data(contentsOf: folder.appendingPathComponent(Export.journal))
            recorded = facts
        case .encrypted(let e):
            guard let privateKey else { throw StorageError.verification("\(folder.lastPathComponent) is encrypted") }
            let ciphertext = try Data(contentsOf: folder.appendingPathComponent(Export.encryptedJournal))
            journal = try ExportCipher.open(ciphertext, sessionID: manifest.sessionID, encapsulatedKey: e.encapsulatedKey,
                                            privateKey: privateKey)
            recorded = e.plaintext
        }
        let state = try readJournal(journal, name: folder.lastPathComponent)
        guard Manifest.Facts(journal: journal, state: state) == recorded,
              manifest.sessionID == state.session.opened.sessionID.description
        else { throw StorageError.verification("\(folder.lastPathComponent): the journal does not match its manifest") }
        return (state, journal)
    }

    private static func readManifest(_ folder: URL) throws -> Manifest {
        let data = try Data(contentsOf: folder.appendingPathComponent(Export.manifest))
        do throws(DecodeError) {
            return try Manifest(json: try JSON.decodeDocument(data))
        } catch {
            throw StorageError.verification("\(folder.lastPathComponent): the manifest does not decode")
        }
    }

    private static func readJournal(_ data: Data, name: String) throws -> SessionState {
        let state: SessionState
        do throws(JournalError) {
            state = try Journal.read(data)
        } catch {
            throw StorageError.journal(name, error)
        }
        guard case .closed = state.phase else { throw StorageError.notClosed(state.status) }
        return state
    }

    // MARK: Withdraw

    public struct Withdrawal: Hashable, Sendable {
        /// The sessions whose journals or exports were deleted, in identifier order.
        public let sessions: [SessionID]
    }

    /// Deletes every journal, staging folder, and export of one participant code on this device. It first appends
    /// one line to the deletion log: the time, the SHA-256 of the code, and the session identifiers. The line never
    /// holds the code. A rerun after a crash deletes what is left and logs again, so the log never misses a deletion.
    public func withdraw(participant: ParticipantCode, at: WallTime) throws -> Withdrawal {
        try createDirectories()
        var ids = Set<SessionID>()
        var targets: [URL] = []
        for folder in [sessions, damaged] {
            for file in try contents(of: folder) where file.pathExtension == "jsonl" {
                guard let opened = Self.firstLine(of: file), opened.participant == participant else { continue }
                ids.insert(opened.sessionID)
                targets.append(file)
            }
        }
        for folder in [staging, exports] {
            for export in try contents(of: folder) where export.lastPathComponent.hasPrefix("\(participant.rawValue)_") {
                if let manifest = try? Self.readManifest(export), let uuid = UUID(uuidString: manifest.sessionID) {
                    ids.insert(SessionID(uuid))
                }
                targets.append(export)
            }
        }
        let sorted = ids.sorted { $0.description < $1.description }
        let line = JSON.object([
            ("at", .string(at.text)),
            ("participantSHA256", .string(SHA256Digest(of: Data(participant.rawValue.utf8)).hex)),
            ("sessions", .array(sorted.map { .string($0.description) })),
        ])
        var bytes: [UInt8] = []
        line.write(into: &bytes)
        bytes.append(0x0A)
        let created = !exists(deletionLog)
        try appendAndSync(Data(bytes), to: deletionLog, create: true)
        if created {
            try protect(deletionLog)
            try syncPath(deletionLog.deletingLastPathComponent())
        }
        for target in targets {
            try FileManager.default.removeItem(at: target)
            try syncPath(target.deletingLastPathComponent())
        }
        return Withdrawal(sessions: sorted)
    }

    private static func firstLine(of file: URL) -> SessionOpened? {
        guard let data = try? Data(contentsOf: file, options: .alwaysMapped),
              let end = data.firstIndex(of: 0x0A),
              let entry = try? LineCodec.decode(data[data.startIndex..<end]),
              case .sessionOpened(let opened) = entry.event
        else { return nil }
        return opened
    }

    // MARK: Files

    /// The folder's entries in name order, without the `._` files that macOS writes on exFAT drives.
    private func contents(of folder: URL) throws -> [URL] {
        try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
            .filter { !$0.lastPathComponent.hasPrefix("._") }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    private func exists(_ url: URL) -> Bool { FileManager.default.fileExists(atPath: url.path) }

    private func protect(_ url: URL) throws {
        #if os(iOS)
        try FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: url.path)
        #endif
    }

    private func move(_ from: URL, to: URL) throws {
        guard rename(from.path, to.path) == 0 else { throw StorageError.io("rename \(from.lastPathComponent)", errno) }
    }

    private func appendAndSync(_ data: Data, to url: URL, create: Bool = false) throws {
        let descriptor = open(url.path, O_WRONLY | O_APPEND | O_CLOEXEC | (create ? O_CREAT : 0), 0o600)
        guard descriptor >= 0 else { throw StorageError.io("open \(url.lastPathComponent)", errno) }
        defer { Darwin.close(descriptor) }
        guard data.withUnsafeBytes({ writeAll(descriptor, $0) }), syncDescriptor(descriptor) else {
            throw StorageError.io("append \(url.lastPathComponent)", errno)
        }
    }

    private func syncPath(_ url: URL) throws {
        let descriptor = open(url.path, O_RDONLY | O_CLOEXEC)
        guard descriptor >= 0 else { throw StorageError.io("open \(url.lastPathComponent)", errno) }
        defer { Darwin.close(descriptor) }
        guard syncDescriptor(descriptor) else { throw StorageError.io("sync \(url.lastPathComponent)", errno) }
    }
}

// MARK: - POSIX

private func writeAll(_ descriptor: Int32, _ bytes: UnsafeRawBufferPointer) -> Bool {
    var offset = 0
    while offset < bytes.count {
        let written = write(descriptor, bytes.baseAddress! + offset, bytes.count - offset)
        if written < 0 {
            if errno == EINTR { continue }
            return false
        }
        offset += written
    }
    return true
}

/// `F_FULLFSYNC` asks the drive to write its cache too. Plain `fsync` is the fallback where it is not supported.
private func syncDescriptor(_ descriptor: Int32) -> Bool {
    fcntl(descriptor, F_FULLFSYNC) == 0 || fsync(descriptor) == 0
}
