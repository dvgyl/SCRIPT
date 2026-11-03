import Foundation

// MARK: - Reading a journal

/// A journal file that breaks integrity rule I1, I2, I3, I4, or I6.
public enum JournalError: Error, Hashable, Sendable {
    case empty
    /// The final line has no LF.
    case unterminated(line: Int)
    case decode(line: Int, LineCodec.DecodeError)
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
            while start < bytes.count {
                guard let found = memchr(raw.baseAddress! + start, 0x0A, bytes.count - start) else { break }
                let end = raw.baseAddress!.distance(to: UnsafeRawPointer(found))
                let line = UnsafeBufferPointer(rebasing: bytes[start..<end])
                let entry: Entry
                do throws(LineCodec.DecodeError) {
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
        let descriptor = open(url.path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0o644)
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

// MARK: - Storage

/// The app container layout and the operations that keep it consistent across crashes.
///
///     <applicationSupport>/Sessions/<uuid>.jsonl   journals not yet exported
///     <applicationSupport>/Staging/<name>/         publication in progress
///     <documents>/Exports/<name>/                  verified exports, visible in Files
///     <documents>/Damaged/<uuid>.jsonl             journals with damage before the last line
public struct Storage: Sendable {
    public let sessions: URL
    public let staging: URL
    public let exports: URL
    public let damaged: URL

    /// The fault-injection seam. A test returns true for a step to stop the operation there, as a crash would.
    let stops: @Sendable (Step) -> Bool

    enum Step: Hashable, Sendable {
        case stagingCleared, journalCloned, manifestWritten, stagedSynced, verified, renamed, sourceRemoved
        case tailTruncated, movedToDamaged, recoveredAppended, staleStagingRemoved
    }

    struct InjectedCrash: Error {}

    public enum StorageError: Error, Hashable, Sendable {
        case noJournal(String)
        case journal(String, JournalError)
        case notClosed(SessionState.Status)
        /// Another session already holds this export name.
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
        exports = documents.appendingPathComponent("Exports", isDirectory: true)
        damaged = documents.appendingPathComponent("Damaged", isDirectory: true)
        self.stops = stops
    }

    public func journalURL(_ id: SessionID) -> URL {
        sessions.appendingPathComponent("\(id.uuid.uuidString).jsonl")
    }

    public func createDirectories() throws {
        for folder in [sessions, staging, exports, damaged] {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        }
    }

    private func checkpoint(_ step: Step) throws {
        if stops(step) { throw InjectedCrash() }
    }

    // MARK: Recover

    public enum Recovery: Hashable, Sendable {
        /// Every complete line folds. `truncatedBytes` is the torn final line that was removed.
        case intact(SessionState, truncatedBytes: Int)
        /// A complete line does not decode or fold. The file moved unchanged to `Damaged/`.
        case damaged(line: Int)
    }

    /// Removes a torn final line with `ftruncate`. A journal with damage before the final line moves unchanged
    /// to `Damaged/`, because only the final line can be torn by a crash.
    public func recover(_ journal: URL) throws -> Recovery {
        let data = try Data(contentsOf: journal)
        let scan = Journal.scan(data)
        guard let state = scan.state, scan.failure == nil else {
            let line = scan.failure?.line ?? 0
            try FileManager.default.createDirectory(at: damaged, withIntermediateDirectories: true)
            let target = damaged.appendingPathComponent(journal.lastPathComponent)
            if !FileManager.default.fileExists(atPath: target.path) {
                try move(journal, to: target)
                try syncPath(journal.deletingLastPathComponent())
                try syncPath(damaged)
            }
            try checkpoint(.movedToDamaged)
            return .damaged(line: line)
        }
        if scan.tailBytes > 0 {
            guard truncate(journal.path, off_t(data.count - scan.tailBytes)) == 0 else {
                throw StorageError.io("truncate \(journal.lastPathComponent)", errno)
            }
            try syncPath(journal)
            try checkpoint(.tailTruncated)
        }
        return .intact(state, truncatedBytes: scan.tailBytes)
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
    /// An open journal gets a `recovered` line. A closed journal with a staging folder or an export finishes its
    /// publication. A staging folder whose export exists and whose journal is gone is removed.
    public func reconcile(_ launch: Launch) throws -> [Reconciled] {
        try createDirectories()
        var results: [Reconciled] = []
        var names = Set<String>()
        let journals = try FileManager.default.contentsOfDirectory(at: sessions, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "jsonl" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        for journal in journals {
            switch try recover(journal) {
            case .damaged(let line):
                results.append(.damaged(file: journal.lastPathComponent, line: line))
            case .intact(var state, let truncatedBytes):
                let name = state.session.opened.exportName
                names.insert(name)
                if state.phase == .closed {
                    let started = [exports, staging].contains {
                        FileManager.default.fileExists(atPath: $0.appendingPathComponent(name).path)
                    }
                    guard started else {
                        results.append(.closed(state))
                        continue
                    }
                    do {
                        results.append(.published(try publish(journal: journal)))
                    } catch let error as StorageError {
                        results.append(.publishFailed(file: journal.lastPathComponent, error))
                    }
                    continue
                }
                let command = Command.recover(t: launch.uptime, at: launch.at, boot: launch.boot,
                                              truncatedBytes: truncatedBytes)
                let events = try state.decide(command).get()
                if !events.isEmpty {
                    var lines = Data()
                    for event in events {
                        let entry = Entry(seq: state.session.lines, event: event)
                        lines.append(try LineCodec.encode(entry))
                        try state.apply(entry)
                    }
                    try appendAndSync(lines, to: journal)
                    try checkpoint(.recoveredAppended)
                }
                results.append(.interrupted(state))
            }
        }
        let folders = try FileManager.default.contentsOfDirectory(at: staging, includingPropertiesForKeys: nil)
        for folder in folders.sorted(by: { $0.lastPathComponent < $1.lastPathComponent })
        where !names.contains(folder.lastPathComponent)
            && FileManager.default.fileExists(atPath: exports.appendingPathComponent(folder.lastPathComponent).path) {
            try FileManager.default.removeItem(at: folder)
            try syncPath(staging)
            try checkpoint(.staleStagingRemoved)
        }
        return results
    }

    // MARK: Publish

    /// Clones the closed journal into `Staging/<name>/`, writes the manifest, syncs, verifies the staged copy
    /// (decode, fold, counts, SHA-256), and renames the folder into `Exports/`. Only then does it delete the source.
    @discardableResult
    public func publish(_ id: SessionID) throws -> URL {
        try publish(journal: journalURL(id))
    }

    func publish(journal source: URL) throws -> URL {
        guard FileManager.default.fileExists(atPath: source.path) else {
            throw StorageError.noJournal(source.lastPathComponent)
        }
        let data = try Data(contentsOf: source)
        let state: SessionState
        do throws(JournalError) {
            state = try Journal.read(data)
        } catch {
            throw StorageError.journal(source.lastPathComponent, error)
        }
        guard state.phase == .closed else { throw StorageError.notClosed(state.status) }
        let name = state.session.opened.exportName
        let manifest = Manifest(sessionID: state.session.opened.sessionID.description, bytes: data.count,
                                lines: state.session.lines, sha256: SHA256.hex(data))
        let target = exports.appendingPathComponent(name, isDirectory: true)

        if FileManager.default.fileExists(atPath: target.path) {
            guard (try? Self.verify(target).manifest) == manifest else { throw StorageError.exportTaken(name) }
        } else {
            let stage = staging.appendingPathComponent(name, isDirectory: true)
            if FileManager.default.fileExists(atPath: stage.path) { try FileManager.default.removeItem(at: stage) }
            try checkpoint(.stagingCleared)

            try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: source, to: stage.appendingPathComponent(Export.journal))
            try checkpoint(.journalCloned)

            try manifest.data.write(to: stage.appendingPathComponent(Export.manifest), options: .atomic)
            try checkpoint(.manifestWritten)

            try syncPath(stage.appendingPathComponent(Export.journal))
            try syncPath(stage.appendingPathComponent(Export.manifest))
            try syncPath(stage)
            try syncPath(staging)
            try checkpoint(.stagedSynced)

            guard try Self.verify(stage).manifest == manifest else {
                throw StorageError.verification("the staged copy of \(name) differs from its source")
            }
            try checkpoint(.verified)

            try FileManager.default.createDirectory(at: exports, withIntermediateDirectories: true)
            guard rename(stage.path, target.path) == 0 else { throw StorageError.io("rename \(name)", errno) }
            try syncPath(exports)
            try syncPath(staging)
            try checkpoint(.renamed)
        }
        try FileManager.default.removeItem(at: source)
        try syncPath(source.deletingLastPathComponent())
        try checkpoint(.sourceRemoved)
        return target
    }

    // MARK: Export

    public enum Export {
        public static let journal = "journal.jsonl"
        public static let manifest = "manifest.json"
    }

    /// Checks an export folder: the manifest (rule I5) and every journal rule. The session must be closed.
    public static func verifyExport(_ folder: URL) throws -> SessionState {
        try verify(folder).state
    }

    static func verify(_ folder: URL) throws -> (state: SessionState, manifest: Manifest) {
        let manifestData = try Data(contentsOf: folder.appendingPathComponent(Export.manifest))
        let manifest: Manifest
        do {
            manifest = try Manifest(json: try JSON.parse(manifestData))
        } catch {
            throw StorageError.verification("\(folder.lastPathComponent): the manifest does not parse")
        }
        let data = try Data(contentsOf: folder.appendingPathComponent(Export.journal), options: .alwaysMapped)
        let state: SessionState
        do throws(JournalError) {
            state = try Journal.read(data)
        } catch {
            throw StorageError.journal(folder.lastPathComponent, error)
        }
        guard state.phase == .closed else { throw StorageError.notClosed(state.status) }
        let actual = Manifest(sessionID: state.session.opened.sessionID.description, bytes: data.count,
                              lines: state.session.lines, sha256: SHA256.hex(data))
        guard actual == manifest else {
            throw StorageError.verification("\(folder.lastPathComponent): the journal does not match its manifest")
        }
        return (state, manifest)
    }

    // MARK: Files

    private func move(_ from: URL, to: URL) throws {
        guard rename(from.path, to.path) == 0 else { throw StorageError.io("rename \(from.lastPathComponent)", errno) }
    }

    private func appendAndSync(_ data: Data, to url: URL) throws {
        let descriptor = open(url.path, O_WRONLY | O_APPEND | O_CLOEXEC)
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

/// `manifest.json`: the facts that cannot be inside the journal.
struct Manifest: Hashable, Sendable {
    var format = "script.journal"
    var version = JournalFormat.version
    var sessionID: String
    var bytes: Int
    var lines: Int
    var sha256: String

    init(sessionID: String, bytes: Int, lines: Int, sha256: String) {
        self.sessionID = sessionID
        self.bytes = bytes
        self.lines = lines
        self.sha256 = sha256
    }

    init(json: JSON) throws(LineCodec.DecodeError) {
        let f = try Fields(json, path: "")
        let journal = try f.object("journal")
        format = try f.string("format")
        version = try f.string("version")
        guard format == "script.journal", JournalFormat.isReadable(version) else { throw .invalid("version") }
        sessionID = try f.string("sessionID")
        bytes = try journal.int("bytes")
        lines = try journal.int("lines")
        sha256 = try journal.string("sha256")
    }

    /// Indented like Python `json.dumps(indent=1)`, so a Swift export and a Python export match byte for byte.
    var data: Data {
        JSON.object([
            ("format", .string(format)),
            ("version", .string(version)),
            ("sessionID", .string(sessionID)),
            ("journal", .object([("bytes", .int(bytes)), ("lines", .int(lines)), ("sha256", .string(sha256))])),
        ]).prettyData()
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
