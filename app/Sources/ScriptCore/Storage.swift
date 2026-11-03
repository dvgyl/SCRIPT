import CryptoKit
import Foundation
import Security
import Synchronization

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
/// It flushes at 64 KB, at 250 ms, or at `contactEnded`. It runs fsync at `trialEnded`, `questionnaireEnded`,
/// `safetyFlagged`, `safetyAction`, and `sessionClosed`, and at `sync()`, which the app calls when the scene goes
/// to the background.
///
/// After the first failed write or fsync, the writer is failed for good, so the file never falls behind the live
/// state without the app knowing: `onFailure` runs once at that moment, and every later `append` throws. The bytes
/// that were not written stay in memory, and each later flush tries them again.
public actor JournalWriter {
    public enum WriteError: Error, Hashable, Sendable {
        case nonFinite(String)
        /// The encoded line does not decode, so a relaunch would find the journal damaged. Nothing was queued.
        case undecodable(DecodeError)
        case closed
        case io(Int32)
    }

    enum Durability: Int, Comparable {
        case buffer, flush, sync

        init(_ kind: LineKind) {
            switch kind {
            case .contactEnded: self = .flush
            case .trialEnded, .questionnaireEnded, .safetyFlagged, .safetyAction, .sessionClosed: self = .sync
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

    /// What `append` must know at once. The consumer sets `failure`; `close()` sets `closing`.
    private struct Gate {
        var failure: WriteError?
        var closing = false
    }

    private let gate = Mutex(Gate())
    private let continuation: AsyncStream<Item>.Continuation
    private let ticker: Task<Void, Never>
    private let descriptor: Int32
    private let flushBytes: Int
    private let onFailure: @Sendable (WriteError) -> Void
    private let stops: @Sendable (Storage.Step) -> Bool
    private var buffer = Data()
    private var isClosed = false

    /// `onFailure` runs once, on the writer's executor, when the first write or fsync fails.
    public init(
        appendingTo url: URL, flushBytes: Int = 64 * 1024, flushInterval: Duration = .milliseconds(250),
        onFailure: @escaping @Sendable (WriteError) -> Void
    ) throws(WriteError) {
        try self.init(appendingTo: url, flushBytes: flushBytes, flushInterval: flushInterval, onFailure: onFailure,
                      stops: { _ in false })
    }

    /// `stops` is the fault-injection seam of `Storage`. At `.journalWrite` a flush writes half of its bytes and then
    /// fails with `EIO`, as a full disk does. At `.journalSync` an fsync fails with `EIO`.
    init(
        appendingTo url: URL, flushBytes: Int = 64 * 1024, flushInterval: Duration = .milliseconds(250),
        onFailure: @escaping @Sendable (WriteError) -> Void, stops: @escaping @Sendable (Storage.Step) -> Bool
    ) throws(WriteError) {
        let descriptor = open(url.path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw .io(errno) }
        self.descriptor = descriptor
        self.flushBytes = flushBytes
        self.onFailure = onFailure
        self.stops = stops
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

    /// Encodes the entries on the caller's thread and queues them. Each encoded line must decode, so the writer
    /// never writes a line that a relaunch would call damage. Nothing is queued when one line fails.
    public nonisolated func append(_ entries: [Entry]) throws(WriteError) {
        var data = Data()
        var durability = Durability.buffer
        for entry in entries {
            let line: Data
            do throws(LineCodec.EncodeError) {
                line = try LineCodec.encode(entry)
            } catch {
                switch error {
                case .nonFinite(let key): throw .nonFinite(key)
                }
            }
            do throws(DecodeError) {
                _ = try LineCodec.decode(line.dropLast())
            } catch {
                throw .undecodable(error)
            }
            data.append(line)
            durability = max(durability, Durability(entry.event.kind))
        }
        try gate.withLock { gate throws(WriteError) in
            if let failure = gate.failure { throw failure }
            if gate.closing { throw .closed }
            continuation.yield(.lines(data, durability))
        }
    }

    /// Returns when every line that `append` accepted before the call is written and synced to storage.
    /// After a failure it throws until a retry has written and synced them all.
    public nonisolated func sync() async throws {
        try await barrier(close: false)
    }

    /// Writes and syncs every accepted line, then closes the file. Later appends throw `closed`.
    public nonisolated func close() async throws {
        try await barrier(close: true)
    }

    private nonisolated func barrier(close: Bool) async throws {
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
            let queued = gate.withLock { gate in
                guard !gate.closing else { return false }
                if close { gate.closing = true }
                continuation.yield(.barrier(k, close: close))
                return true
            }
            if !queued { k.resume(throwing: WriteError.closed) }
        }
    }

    private func consume(_ stream: AsyncStream<Item>) async {
        for await item in stream {
            switch item {
            case .lines(let data, let durability):
                buffer.append(data)
                if durability == .sync {
                    _ = flush() ?? fullSync()
                } else if durability == .flush || buffer.count >= flushBytes {
                    _ = flush()
                }
            case .tick:
                _ = flush()
            case .barrier(let k, let close):
                var error = flush() ?? fullSync()
                if close {
                    isClosed = true
                    ticker.cancel()
                    if Darwin.close(descriptor) != 0, error == nil { error = .io(errno) }
                    continuation.finish()
                }
                if let error { k.resume(throwing: error) } else { k.resume() }
            }
        }
    }

    /// Writes the buffer and keeps the bytes that a failed write left, so the next flush writes each byte once.
    private func flush() -> WriteError? {
        guard !buffer.isEmpty, !isClosed else { return nil }
        let result: (written: Int, error: Int32?)
        if stops(.journalWrite) {
            result = (buffer.prefix(buffer.count / 2).withUnsafeBytes { writeAll(descriptor, $0) }.written, EIO)
        } else {
            result = buffer.withUnsafeBytes { writeAll(descriptor, $0) }
        }
        if result.written == buffer.count {
            buffer.removeAll(keepingCapacity: true)
        } else {
            buffer.removeFirst(result.written)
        }
        return result.error.map { fail(.io($0)) }
    }

    private func fullSync() -> WriteError? {
        guard !isClosed else { return nil }
        if stops(.journalSync) { return fail(.io(EIO)) }
        return syncDescriptor(descriptor) ? nil : fail(.io(errno))
    }

    /// Marks the writer failed and tells the app, the first time only.
    private func fail(_ error: WriteError) -> WriteError {
        let first = gate.withLock { gate in
            guard gate.failure == nil else { return false }
            gate.failure = error
            return true
        }
        if first { onFailure(error) }
        return error
    }
}

// MARK: - Export format

/// How `publish` writes an export (docs/DESIGN.md section 17).
public enum ExportPolicy: Sendable {
    /// A plaintext `journal.jsonl` with `"demo": true` in its manifest. Only a demo configuration without an export
    /// key writes plaintext exports.
    case demo
    /// `journal.jsonl.hpke`, sealed to the study's public key. The manifest names the key by its SHA-256.
    case encrypted(recipient: Curve25519.KeyAgreement.PublicKey)

    public enum PolicyError: Error, Hashable, Sendable {
        case noExportKey
        case invalidKey
    }

    /// Encrypted whenever the configuration has an export key. Without a key, only a demo configuration may export.
    public init(configuration: StudyConfiguration) throws(PolicyError) {
        guard let raw = configuration.export.recipientPublicKey else {
            guard configuration.demo else { throw .noExportKey }
            self = .demo
            return
        }
        guard let key = try? Curve25519.KeyAgreement.PublicKey(rawRepresentation: raw) else { throw .invalidKey }
        self = .encrypted(recipient: key)
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
            guard try f.bool("demo") else { throw .invalid("demo") }
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
///     <applicationSupport>/Damaged/<uuid>.jsonl    journals with damage before the last line, never in Files
///     <applicationSupport>/Deletions.jsonl         one line for each withdrawal
///     <applicationSupport>/ExportIndex.jsonl       the keyed hash of the participant code of each export name
///     <documents>/Exports/<name>/                  verified exports, visible in Files
public struct Storage: Sendable {
    public let sessions: URL
    public let staging: URL
    public let exports: URL
    public let damaged: URL
    public let deletionLog: URL
    /// One line for each export: its name and `participantHMAC`. An export name holds no participant code, and an
    /// encrypted export cannot be read on the device, so withdrawal finds a participant's exports here.
    public let exportIndex: URL
    /// The key of `participantHMAC` in the deletion log.
    let deviceSecret: any DeviceSecret

    /// The fault-injection seam. A test returns true for a step to stop there: `publish` and `reconcile` throw as a
    /// crash would, and `JournalWriter` fails a write after half of its bytes, or an fsync, with `EIO`.
    let stops: @Sendable (Step) -> Bool

    /// Each point between two durable writes of `publish` and `reconcile`, and each write and fsync of `JournalWriter`.
    enum Step: String, CaseIterable, Sendable {
        case stagingCleared, journalStaged, manifestWritten, stagedSynced, stagedVerified, indexed, renamed, sourceRemoved
        case leftoverRemoved, movedToDamaged, recoveryStaged, recoveryCommitted, staleStagingRemoved
        case journalWrite, journalSync
    }

    struct InjectedCrash: Error, Equatable {
        let step: Step
    }

    /// A storage failure. It names a session by its identifier, never by its export name, which holds the
    /// participant code, so the app can log it.
    public enum StorageError: Error, Hashable, Sendable {
        case noJournal(SessionID)
        case journal(SessionID?, JournalError)
        case notClosed(SessionState.Status)
        /// Another session, or a different copy of this one, already holds this session's export name.
        case exportTaken(SessionID)
        /// The export does not match its manifest or its source.
        case verification(SessionID?, String)
        /// A system call failed. The text names the call, never the file.
        case io(String, Int32)
        /// A Foundation, CryptoKit, or Keychain error, kept as its domain and code only, because its description can
        /// hold a file path.
        case system(domain: String, code: Int)

        init(scrubbing error: any Error) {
            if let error = error as? StorageError {
                self = error
            } else {
                let error = error as NSError
                self = .system(domain: error.domain, code: error.code)
            }
        }
    }

    public init(applicationSupport: URL, documents: URL, deviceSecret: any DeviceSecret) {
        self.init(applicationSupport: applicationSupport, documents: documents, deviceSecret: deviceSecret,
                  stops: { _ in false })
    }

    init(applicationSupport: URL, documents: URL, deviceSecret: any DeviceSecret,
         stops: @escaping @Sendable (Step) -> Bool) {
        sessions = applicationSupport.appendingPathComponent("Sessions", isDirectory: true)
        staging = applicationSupport.appendingPathComponent("Staging", isDirectory: true)
        deletionLog = applicationSupport.appendingPathComponent("Deletions.jsonl")
        exportIndex = applicationSupport.appendingPathComponent("ExportIndex.jsonl")
        exports = documents.appendingPathComponent("Exports", isDirectory: true)
        damaged = applicationSupport.appendingPathComponent("Damaged", isDirectory: true)
        self.deviceSecret = deviceSecret
        self.stops = stops
    }

    public func journalURL(_ id: SessionID) -> URL {
        sessions.appendingPathComponent("\(id.uuid.uuidString).jsonl")
    }

    /// Creates the folders. On iOS, each folder has the file protection `completeUntilFirstUserAuthentication`,
    /// which every file created inside it inherits. Each folder is excluded from device backups, so no journal or
    /// export reaches iCloud or a computer backup.
    public func createDirectories() throws {
        try guarded { try makeDirectories() }
    }

    private func makeDirectories() throws {
        for folder in [sessions, staging, exports, damaged] {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try protect(folder)
            try excludeFromBackup(folder)
        }
    }

    private func checkpoint(_ step: Step) throws {
        if stops(step) { throw InjectedCrash(step: step) }
    }

    /// Runs one public operation. Every error leaves it as a `StorageError`, so no file path reaches the app.
    private func guarded<T>(_ body: () throws -> T) throws -> T {
        do {
            return try body()
        } catch let crash as InjectedCrash {
            throw crash
        } catch {
            throw StorageError(scrubbing: error)
        }
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
        /// The journal could not be read, moved, or recovered at this launch. It stays where it is, and the next
        /// launch tries again.
        case unreadable(file: String, StorageError)
    }

    /// Runs at launch and converges from any crash point. A second launch changes no byte.
    ///
    /// An open journal loses its torn tail and gets a `recovered` line in one atomic replacement. A journal with
    /// damage before its last line moves unchanged to `Damaged/`. A closed journal whose publication had started
    /// finishes it. A staging folder whose export exists and whose journal is gone is removed. An error in one
    /// journal becomes its result, so every other journal is still reconciled. A leftover `.recovering` file or
    /// staging folder that cannot be removed stays for the next launch.
    public func reconcile(_ launch: Launch, policy: ExportPolicy) throws -> [Reconciled] {
        try reconcile(launch, policy: { _ in policy })
    }

    /// `policy` gives the export format of each session, from the configuration that the session recorded, so a
    /// session never finishes its publication under the key of another configuration. When it throws, the result
    /// is `publishFailed` and the journal stays.
    public func reconcile(_ launch: Launch, policy: (SessionOpened) throws -> ExportPolicy) throws -> [Reconciled] {
        try guarded {
            try makeDirectories()
            for leftover in try contents(of: sessions) where leftover.pathExtension == "recovering" {
                guard (try? FileManager.default.removeItem(at: leftover)) != nil else { continue }
                try syncPath(sessions)
                try checkpoint(.leftoverRemoved)
            }
            var results: [Reconciled] = []
            var names = Set<String>()
            for journal in try contents(of: sessions) where journal.pathExtension == "jsonl" {
                do {
                    results.append(try reconcile(journal, launch: launch, policy: policy, names: &names))
                } catch let crash as InjectedCrash {
                    throw crash
                } catch {
                    results.append(.unreadable(file: journal.lastPathComponent, StorageError(scrubbing: error)))
                }
            }
            for folder in try contents(of: staging)
            where !names.contains(folder.lastPathComponent) && exists(exports.appendingPathComponent(folder.lastPathComponent)) {
                guard (try? FileManager.default.removeItem(at: folder)) != nil else { continue }
                try syncPath(staging)
                try checkpoint(.staleStagingRemoved)
            }
            return results
        }
    }

    /// Reconciles one journal and adds its export name to `names`.
    private func reconcile(_ journal: URL, launch: Launch, policy: (SessionOpened) throws -> ExportPolicy,
                           names: inout Set<String>) throws -> Reconciled {
        let data = try Data(contentsOf: journal)
        let scan = Journal.scan(data)
        guard var state = scan.state, scan.failure == nil else {
            try moveToDamaged(journal)
            return .damaged(file: journal.lastPathComponent, line: scan.failure?.line ?? 0)
        }
        let name = state.session.opened.exportName
        names.insert(name)
        var addition = Data()
        if let event = state.recovery(t: launch.uptime, at: launch.at, boot: launch.boot, truncatedBytes: scan.tailBytes) {
            for entry in try state.record([event]) { addition.append(try LineCodec.encode(entry)) }
        }
        if scan.tailBytes > 0 {
            try replace(journal, with: data.prefix(data.count - scan.tailBytes) + addition)
        } else if !addition.isEmpty {
            try appendAndSync(addition, to: journal)
            try checkpoint(.recoveryCommitted)
        }
        guard case .closed = state.phase else { return .interrupted(state) }
        guard [exports, staging].contains(where: { exists($0.appendingPathComponent(name)) }) else {
            return .closed(state)
        }
        do {
            return .published(try publish(journal: journal, policy: try policy(state.session.opened)))
        } catch let crash as InjectedCrash {
            throw crash
        } catch {
            return .publishFailed(file: journal.lastPathComponent, StorageError(scrubbing: error))
        }
    }

    /// Writes the new journal beside the old one and renames it over the old one, so the tail removal and the
    /// `recovered` line become durable together.
    private func replace(_ journal: URL, with data: Data) throws {
        let temporary = journal.appendingPathExtension("recovering")
        let descriptor = open(temporary.path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw StorageError.io("open", errno) }
        let error = data.withUnsafeBytes { writeAll(descriptor, $0) }.error ?? (syncDescriptor(descriptor) ? nil : errno)
        Darwin.close(descriptor)
        if let error { throw StorageError.io("write", error) }
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
    /// copy (decode, fold, counts, SHA-256), adds the name to the export index, and renames the folder into `Exports/`.
    /// Only then does it delete the source.
    @discardableResult
    public func publish(_ id: SessionID, policy: ExportPolicy) throws -> URL {
        try guarded {
            let source = journalURL(id)
            guard exists(source) else { throw StorageError.noJournal(id) }
            return try publish(journal: source, policy: policy)
        }
    }

    func publish(journal source: URL, policy: ExportPolicy) throws -> URL {
        try makeDirectories()
        let data = try Data(contentsOf: source)
        let state: SessionState
        do throws(JournalError) {
            state = try Journal.read(data)
        } catch {
            throw StorageError.journal(UUID(uuidString: source.deletingPathExtension().lastPathComponent).map(SessionID.init),
                                       error)
        }
        guard case .closed = state.phase else { throw StorageError.notClosed(state.status) }
        let id = state.session.opened.sessionID
        let name = state.session.opened.exportName
        let target = exports.appendingPathComponent(name, isDirectory: true)

        if exists(target) {
            do {
                try Self.verify(target, source: data, state: state)
            } catch {
                throw StorageError.exportTaken(id)
            }
        } else {
            let stage = staging.appendingPathComponent(name, isDirectory: true)
            if exists(stage) { try FileManager.default.removeItem(at: stage) }
            try checkpoint(.stagingCleared)

            try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: true)
            let facts = Manifest.Facts(journal: data, state: state)
            let sessionID = id.description
            let manifest: Manifest
            let journalFile: URL
            switch policy {
            case .demo:
                journalFile = stage.appendingPathComponent(Export.journal)
                try FileManager.default.copyItem(at: source, to: journalFile)
                manifest = Manifest(sessionID: sessionID, content: .plaintext(facts))
            case .encrypted(let recipient):
                journalFile = stage.appendingPathComponent(Export.encryptedJournal)
                let sealed = try ExportCipher.seal(data, sessionID: sessionID, recipient: recipient)
                try sealed.ciphertext.write(to: journalFile)
                manifest = Manifest(sessionID: sessionID, content: .encrypted(Manifest.Encryption(
                    keyID: SHA256Digest(of: recipient.rawRepresentation).hex, encapsulatedKey: sealed.encapsulatedKey,
                    plaintext: facts, ciphertextBytes: sealed.ciphertext.count,
                    ciphertextSHA256: SHA256Digest(of: sealed.ciphertext)
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

            try index(name, participant: state.session.opened.participant)
            try checkpoint(.indexed)

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
        let id = state.session.opened.sessionID
        func fail(_ what: String) -> StorageError { .verification(id, what) }
        let manifest = try readManifest(folder, session: id)
        guard manifest.sessionID == id.description else { throw fail("the manifest names another session") }
        let facts = Manifest.Facts(journal: source, state: state)
        switch manifest.content {
        case .plaintext(let recorded):
            guard recorded == facts else { throw fail("the manifest does not match the source journal") }
            let copy = try Data(contentsOf: folder.appendingPathComponent(Export.journal))
            let staged = try readJournal(copy, session: id)
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
        let manifest = try readManifest(folder, session: nil)
        let id = UUID(uuidString: manifest.sessionID).map(SessionID.init)
        let journal: Data
        let recorded: Manifest.Facts
        switch manifest.content {
        case .plaintext(let facts):
            journal = try Data(contentsOf: folder.appendingPathComponent(Export.journal))
            recorded = facts
        case .encrypted(let e):
            guard let privateKey else { throw StorageError.verification(id, "the export is encrypted") }
            let ciphertext = try Data(contentsOf: folder.appendingPathComponent(Export.encryptedJournal))
            journal = try ExportCipher.open(ciphertext, sessionID: manifest.sessionID, encapsulatedKey: e.encapsulatedKey,
                                            privateKey: privateKey)
            recorded = e.plaintext
        }
        let state = try readJournal(journal, session: id)
        guard Manifest.Facts(journal: journal, state: state) == recorded,
              manifest.sessionID == state.session.opened.sessionID.description
        else { throw StorageError.verification(id, "the journal does not match its manifest") }
        return (state, journal)
    }

    private static func readManifest(_ folder: URL, session: SessionID?) throws -> Manifest {
        let data = try Data(contentsOf: folder.appendingPathComponent(Export.manifest))
        do throws(DecodeError) {
            return try Manifest(json: try JSON.decodeDocument(data))
        } catch {
            throw StorageError.verification(session, "the manifest does not decode")
        }
    }

    private static func readJournal(_ data: Data, session: SessionID?) throws -> SessionState {
        let state: SessionState
        do throws(JournalError) {
            state = try Journal.read(data)
        } catch {
            throw StorageError.journal(session, error)
        }
        guard case .closed = state.phase else { throw StorageError.notClosed(state.status) }
        return state
    }

    // MARK: Withdraw

    public struct Withdrawal: Hashable, Sendable {
        /// The sessions whose journals or exports were deleted, in identifier order.
        public let sessions: [SessionID]
        /// Journals whose first line does not decode, so they name no participant. The operator decides what to do
        /// with them.
        public let unreadable: [URL]
    }

    /// Deletes every journal, staging folder, export, and damaged journal of one participant code on this device,
    /// and every `*.recovering` file, which is a disposable copy of a journal. It first appends one line to the
    /// deletion log: the time, the HMAC-SHA256 of the code with the device secret, and the session identifiers.
    /// The line never holds the code. A rerun after a crash deletes what is left and logs again, so the log never
    /// misses a deletion. Exports are found by the export index and by the names of the participant's journals.
    public func withdraw(participant: ParticipantCode, at: WallTime) throws -> Withdrawal {
        try guarded {
            try makeDirectories()
            let mac = try participantHMAC(participant).hex
            var ids = Set<SessionID>()
            var targets: [URL] = []
            var unreadable: [URL] = []
            var names = try indexedNames(participantHMAC: mac)
            for folder in [sessions, damaged] {
                for file in try contents(of: folder) {
                    switch file.pathExtension {
                    case "recovering":
                        targets.append(file)
                    case "jsonl":
                        guard let opened = Self.firstLine(of: file) else {
                            unreadable.append(file)
                            continue
                        }
                        guard opened.participant == participant else { continue }
                        ids.insert(opened.sessionID)
                        names.insert(opened.exportName)
                        targets.append(file)
                    default:
                        continue
                    }
                }
            }
            for folder in [staging, exports] {
                for export in try contents(of: folder) where names.contains(export.lastPathComponent) {
                    if let manifest = try? Self.readManifest(export, session: nil),
                       let uuid = UUID(uuidString: manifest.sessionID) {
                        ids.insert(SessionID(uuid))
                    }
                    targets.append(export)
                }
            }
            let sorted = ids.sorted { $0.description < $1.description }
            let line = JSON.object([
                ("at", .string(at.text)),
                ("participantHMAC", .string(mac)),
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
            try excludeFromBackup(deletionLog)
            for target in targets {
                try FileManager.default.removeItem(at: target)
                try syncPath(target.deletingLastPathComponent())
            }
            return Withdrawal(sessions: sorted, unreadable: unreadable)
        }
    }

    /// True when the deletion log has a line for this participant code. A site confirms a withdrawal this way on
    /// the device, because the log holds only the keyed hash of the code.
    public func verifyWithdrawal(participant: ParticipantCode) throws -> Bool {
        try guarded {
            guard exists(deletionLog) else { return false }
            let key = try deviceSecret.key()
            let code = Data(participant.rawValue.utf8)
            for line in try Data(contentsOf: deletionLog).split(separator: 0x0A) {
                guard let entry = try? Fields(JSON.decodeDocument(Data(line)), path: ""),
                      let mac = try? entry.text("participantHMAC", SHA256Digest.init(hex:))
                else { continue }
                if HMAC<SHA256>.isValidAuthenticationCode(mac.bytes, authenticating: code, using: key) { return true }
            }
            return false
        }
    }

    /// Appends the export's name and the keyed hash of its participant code to the export index, once.
    private func index(_ name: String, participant: ParticipantCode) throws {
        let mac = try participantHMAC(participant).hex
        guard try !indexedNames(participantHMAC: mac).contains(name) else { return }
        var bytes: [UInt8] = []
        JSON.object([("name", .string(name)), ("participantHMAC", .string(mac))]).write(into: &bytes)
        bytes.append(0x0A)
        let created = !exists(exportIndex)
        try appendAndSync(Data(bytes), to: exportIndex, create: true)
        if created {
            try protect(exportIndex)
            try syncPath(exportIndex.deletingLastPathComponent())
        }
        try excludeFromBackup(exportIndex)
    }

    /// The export names that the index gives for one keyed hash.
    private func indexedNames(participantHMAC mac: String) throws -> Set<String> {
        guard exists(exportIndex) else { return [] }
        var names = Set<String>()
        for line in try Data(contentsOf: exportIndex).split(separator: 0x0A) {
            guard let entry = try? Fields(JSON.decodeDocument(Data(line)), path: ""),
                  (try? entry.string("participantHMAC")) == mac, let name = try? entry.string("name") else { continue }
            names.insert(name)
        }
        return names
    }

    private func participantHMAC(_ participant: ParticipantCode) throws -> HashedAuthenticationCode<SHA256> {
        HMAC<SHA256>.authenticationCode(for: Data(participant.rawValue.utf8), using: try deviceSecret.key())
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

    /// Sets `isExcludedFromBackup` when it is not set yet, so a folder that is already excluded needs no write.
    private func excludeFromBackup(_ url: URL) throws {
        guard try url.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup != true else { return }
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var url = url
        try url.setResourceValues(values)
    }

    private func move(_ from: URL, to: URL) throws {
        guard rename(from.path, to.path) == 0 else { throw StorageError.io("rename", errno) }
    }

    private func appendAndSync(_ data: Data, to url: URL, create: Bool = false) throws {
        let descriptor = open(url.path, O_WRONLY | O_APPEND | O_CLOEXEC | (create ? O_CREAT : 0), 0o600)
        guard descriptor >= 0 else { throw StorageError.io("open", errno) }
        defer { Darwin.close(descriptor) }
        if let error = data.withUnsafeBytes({ writeAll(descriptor, $0) }).error { throw StorageError.io("append", error) }
        guard syncDescriptor(descriptor) else { throw StorageError.io("sync", errno) }
    }

    private func syncPath(_ url: URL) throws {
        let descriptor = open(url.path, O_RDONLY | O_CLOEXEC)
        guard descriptor >= 0 else { throw StorageError.io("open", errno) }
        defer { Darwin.close(descriptor) }
        guard syncDescriptor(descriptor) else { throw StorageError.io("sync", errno) }
    }
}

// MARK: - Device secret

/// The 32-byte key of `participantHMAC` in the deletion log. It stays on the device, so nobody elsewhere can
/// reverse the log by trying every participant code.
public protocol DeviceSecret: Sendable {
    /// The key. The first call creates and stores it.
    func key() throws -> SymmetricKey
}

/// The app's device secret: a generic-password Keychain item, readable after the first unlock, that never moves to
/// another device.
public struct KeychainDeviceSecret: DeviceSecret {
    private static let service = "SCRIPT deletion log"
    private static let account = "participantHMAC"

    public init() {}

    public func key() throws -> SymmetricKey {
        if let stored = try read() { return stored }
        let key = SymmetricKey(size: .bits256)
        var item = Self.query
        item[kSecValueData as String] = key.withUnsafeBytes { Data($0) }
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        // Another caller stored a key first, and the log must use one key only.
        if status == errSecDuplicateItem, let stored = try read() { return stored }
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
        return key
    }

    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: account, kSecUseDataProtectionKeychain as String: true]
    }

    private func read() throws -> SymmetricKey? {
        var query = Self.query
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
        guard let data = result as? Data, data.count == 32 else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecDecode))
        }
        return SymmetricKey(data: data)
    }
}

// MARK: - POSIX

/// Writes every byte unless a call fails. `written` counts the bytes written before the failure.
private func writeAll(_ descriptor: Int32, _ bytes: UnsafeRawBufferPointer) -> (written: Int, error: Int32?) {
    var offset = 0
    while offset < bytes.count {
        let written = write(descriptor, bytes.baseAddress! + offset, bytes.count - offset)
        if written < 0 {
            if errno == EINTR { continue }
            return (offset, errno)
        }
        offset += written
    }
    return (offset, nil)
}

/// `F_FULLFSYNC` asks the drive to write its cache too. Plain `fsync` is the fallback where it is not supported.
private func syncDescriptor(_ descriptor: Int32) -> Bool {
    fcntl(descriptor, F_FULLFSYNC) == 0 || fsync(descriptor) == 0
}
