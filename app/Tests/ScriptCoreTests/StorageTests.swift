import CryptoKit
import Foundation
import Testing
@testable import ScriptCore

/// A session of one short trial, closed when `close` is true and left with the pen down otherwise.
private func session(_ id: SessionID, participant: ParticipantCode = "P0001", close: Bool = true,
                     reason: TrialEndReason = .participantDone) throws -> Driver {
    let config = try Contract.demoConfiguration()
    let opened = makeOpened(id: id, participant: participant, configuration: config, battery: try Contract.battery())
    var d = try Driver(opened: opened, rules: try rules(for: opened, configuration: config))
    try d.run(.startTrial(Driver.plan("spiral-trace"), t: 101))
    try d.run(.beginTrial(t: 101.5))
    try d.stroke(key: 1, t0: 102, pending: [.force], update: 3, lift: close)
    if close {
        try d.run(.endTrial(reason: reason, t: 104))
        try d.run(.close(t: 105))
    }
    return d
}

private let publishSteps: [Storage.Step] = [
    .stagingCleared, .journalStaged, .manifestWritten, .stagedSynced, .stagedVerified, .renamed, .sourceRemoved,
]
private let reconcileSteps: [Storage.Step] = [
    .leftoverRemoved, .movedToDamaged, .recoveryStaged, .recoveryCommitted, .staleStagingRemoved,
]
/// The steps that the writer tests stop at.
private let writerSteps: [Storage.Step] = [.journalWrite, .journalSync]

@Test func everyFaultStepHasATestThatStopsAtIt() {
    // Detects a new crash point or write fault that no test stops at.
    #expect(Set(publishSteps + reconcileSteps + writerSteps) == Set(Storage.Step.allCases))
}

@Test func theWriterKeepsCallOrderAndFlushesAtContactEnded() async throws {
    // Detects a writer that reorders lines, or holds a lifted stroke in memory where a crash would lose it.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let url = root.appendingPathComponent("journal.jsonl")
    let d = try session(SessionID(), close: false)
    let lines = try Contract.lines(d.journal).map { try LineCodec.decode($0) }
    let writer = try JournalWriter(appendingTo: url, flushInterval: .seconds(3600), onFailure: { _ in })
    try writer.append(Array(lines.dropLast()))
    try await Task.sleep(for: .milliseconds(200))
    #expect(try Data(contentsOf: url).isEmpty)

    let lift = Entry(seq: lines.count, event: .contactEnded(ContactEnded(t: 103, contact: .first, cancelled: false)))
    try writer.append([lines.last!, lift])
    let expected = d.journal + (try LineCodec.encode(lift))
    var waits = 0
    while try Data(contentsOf: url).count < expected.count, waits < 200 {
        try await Task.sleep(for: .milliseconds(10))
        waits += 1
    }
    #expect(try Data(contentsOf: url) == expected)
    try await writer.close()
    #expect(throws: JournalWriter.WriteError.closed) { try writer.append([lift]) }
}

@Test func reconcileRemovesATornTailAndRecordsItsBytes() throws {
    // Detects a relaunch that keeps a torn line, loses its byte count, or repairs damage before the last line.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let s = storage(in: root)
    try s.createDirectories()
    let torn = s.sessions.appendingPathComponent("1A000000-0000-4000-8000-000000000001.jsonl")
    let broken = s.sessions.appendingPathComponent("1A000000-0000-4000-8000-000000000002.jsonl")
    try Contract.data("fixtures/recovery/torn_tail.jsonl").write(to: torn)
    let damage = try Contract.data("fixtures/recovery/damaged_interior.jsonl")
    try damage.write(to: broken)

    let results = try s.reconcile(Fixed.launch, policy: .demo)
    #expect(results.count == 2)
    let repaired = try Data(contentsOf: torn)
    let clean = try Contract.data("fixtures/recovery/clean.jsonl")
    #expect(repaired.prefix(clean.count) == clean)
    let last = try LineCodec.decode(Contract.lines(repaired).last!)
    #expect(last.event == .recovered(Recovered(t: 5, at: Fixed.launch.at, boot: Fixed.laterBoot, truncatedBytes: 47,
                                               interrupted: TrialIndex(count: 0), interruptedBlock: nil)))
    #expect(try Data(contentsOf: s.damaged.appendingPathComponent(broken.lastPathComponent)) == damage)
    #expect(!FileManager.default.fileExists(atPath: broken.path))
}

enum ExportKind: String, CaseIterable, Sendable {
    case demo, encrypted
}

@Test(arguments: publishSteps, ExportKind.allCases)
func publishConvergesFromEveryCrashPoint(step: Storage.Step, policy: ExportKind) throws {
    // Detects a relaunch that does not finish a publication that had started, or leaves it half done or duplicated.
    let key = Curve25519.KeyAgreement.PrivateKey()
    let exportPolicy: ExportPolicy = policy == .demo ? .demo : .encrypted(recipient: key.publicKey)
    let id = SessionID(UUID(uuidString: "2B000000-0000-4000-8000-000000000001")!)
    let journal = try session(id).journal

    func prepared(stops: @escaping @Sendable (Storage.Step) -> Bool = { _ in false }) throws -> (URL, Storage) {
        let root = try temporaryFolder()
        let s = storage(in: root, stops: stops)
        try s.createDirectories()
        try journal.write(to: s.journalURL(id))
        return (root, s)
    }
    let (referenceRoot, reference) = try prepared()
    let (crashRoot, crashing) = try prepared { $0 == step }
    defer {
        try? FileManager.default.removeItem(at: referenceRoot)
        try? FileManager.default.removeItem(at: crashRoot)
    }
    try reference.publish(id, policy: exportPolicy)
    #expect(throws: Storage.InjectedCrash(step: step)) { try crashing.publish(id, policy: exportPolicy) }

    let relaunched = storage(in: crashRoot)
    let results = try relaunched.reconcile(Fixed.launch, policy: exportPolicy)
    let folder = relaunched.exports.appendingPathComponent("P0001_2b000000", isDirectory: true)
    switch step {
    case .stagingCleared:
        // The crash came before the staging folder existed, so nothing shows that the publication had started.
        guard results.count == 1, case .closed? = results.first else {
            Issue.record("unexpected results \(results)")
            return
        }
        try relaunched.publish(id, policy: exportPolicy)
    case .sourceRemoved:
        // The publication was complete, so the relaunch has no journal to reconcile.
        #expect(results.isEmpty)
    default:
        #expect(results == [.published(folder)])
    }
    let converged = try snapshot(crashRoot)
    let expected = try snapshot(referenceRoot)
    switch policy {
    case .demo:
        #expect(converged == expected)
    case .encrypted:
        #expect(Set(converged.keys) == Set(expected.keys))
        #expect(try Storage.readExport(folder, privateKey: key).journal == journal)
    }
    _ = try relaunched.reconcile(Fixed.launch, policy: exportPolicy)
    #expect(try snapshot(crashRoot) == converged)
}

/// A launch that meets every reconcile step: a leftover temporary file, a damaged journal, a torn open journal,
/// an open journal without a tail, and a staging folder whose export exists.
private func prepareLaunch(_ s: Storage) throws {
    try s.createDirectories()
    try Contract.data("fixtures/recovery/torn_tail.jsonl")
        .write(to: s.sessions.appendingPathComponent("3C000000-0000-4000-8000-000000000001.jsonl"))
    try Data("partial".utf8).write(to: s.sessions.appendingPathComponent("3C000000-0000-4000-8000-000000000001.jsonl.recovering"))
    try Contract.data("fixtures/recovery/damaged_interior.jsonl")
        .write(to: s.sessions.appendingPathComponent("3C000000-0000-4000-8000-000000000002.jsonl"))
    let open = SessionID(UUID(uuidString: "3C000000-0000-4000-8000-000000000003")!)
    try session(open, participant: "P0003", close: false).journal.write(to: s.journalURL(open))
    for folder in [s.staging, s.exports] {
        let stale = folder.appendingPathComponent("P0099_deadbeef")
        try FileManager.default.createDirectory(at: stale, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: stale.appendingPathComponent("manifest.json"))
    }
}

@Test(arguments: reconcileSteps)
func reconcileConvergesFromEveryCrashPointAndASecondLaunchChangesNoByte(step: Storage.Step) throws {
    // Detects a launch that a crash at this step leaves in a state that the next launch resolves differently.
    let referenceRoot = try temporaryFolder()
    let crashRoot = try temporaryFolder()
    defer {
        try? FileManager.default.removeItem(at: referenceRoot)
        try? FileManager.default.removeItem(at: crashRoot)
    }
    try prepareLaunch(storage(in: referenceRoot))
    _ = try storage(in: referenceRoot).reconcile(Fixed.launch, policy: .demo)
    let expected = try snapshot(referenceRoot)

    try prepareLaunch(storage(in: crashRoot))
    #expect(throws: Storage.InjectedCrash(step: step)) {
        try storage(in: crashRoot, stops: { $0 == step }).reconcile(Fixed.launch, policy: .demo)
    }
    _ = try storage(in: crashRoot).reconcile(Fixed.launch, policy: .demo)
    #expect(try snapshot(crashRoot) == expected)
    _ = try storage(in: crashRoot).reconcile(Fixed.launch, policy: .demo)
    #expect(try snapshot(crashRoot) == expected)
}

@Test func anEncryptedExportOpensWithTheContractSuiteInfoAndSessionID() throws {
    // Detects an encrypted export that Python cannot open or match: an info text, associated data, suite, keyID, or
    // manifest string that differs from docs/DESIGN.md section 17, which a test through the code's own constants misses.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let s = storage(in: root)
    let id = SessionID(UUID(uuidString: "6E000000-0000-4000-8000-000000000001")!)
    let journal = try session(id).journal
    try s.createDirectories()
    try journal.write(to: s.journalURL(id))
    let key = try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: Data((1...32).map { UInt8($0) }))
    let folder = try s.publish(id, policy: .encrypted(recipient: key.publicKey))

    #expect(try FileManager.default.contentsOfDirectory(atPath: folder.path).filter { !$0.hasPrefix("._") }.sorted()
        == ["journal.jsonl.hpke", "manifest.json"])
    let manifest = try Fields(try JSON.decodeDocument(try Data(contentsOf: folder.appendingPathComponent("manifest.json"))),
                              path: "")
    #expect(try manifest.string("format") == "script.journal")
    #expect(try manifest.string("version") == "1.0")
    #expect(try manifest.string("sessionID") == "6E000000-0000-4000-8000-000000000001")
    #expect(!manifest.has("journal") && !manifest.has("demo"))
    let encryption = try manifest.object("encryption")
    #expect(try encryption.string("scheme") == "hpke-base")
    #expect(try encryption.string("suite") == "X25519-HKDF-SHA256-ChaCha20Poly1305")
    // The SHA-256 of the raw public key, computed with the Python `cryptography` package.
    #expect(try encryption.string("keyID") == "aaa8fff703b50b2297f4f6e13508f72420d96fd01ebb84cb074449caaef64041")
    let plaintext = try encryption.object("plaintext")
    #expect(try plaintext.int("bytes") == journal.count)
    #expect(try plaintext.int("lines") == Contract.lines(journal).count)
    #expect(try plaintext.string("sha256") == SHA256.hash(data: journal).map { String(format: "%02x", $0) }.joined())

    let encapsulatedKey = try #require(Data(base64Encoded: try encryption.string("encapsulatedKey")))
    var recipient = try HPKE.Recipient(privateKey: key, ciphersuite: .Curve25519_SHA256_ChachaPoly,
                                       info: Data("SCRIPT export 1.0".utf8), encapsulatedKey: encapsulatedKey)
    let ciphertext = try Data(contentsOf: folder.appendingPathComponent("journal.jsonl.hpke"))
    #expect(try recipient.open(ciphertext, authenticating: Data("6E000000-0000-4000-8000-000000000001".utf8)) == journal)
    #expect(throws: (any Error).self) { try Storage.readExport(folder, privateKey: Curve25519.KeyAgreement.PrivateKey()) }
}

@Test func aPlaintextExportIsMarkedDemoAndAnyExportKeyEncrypts() throws {
    // Detects a plaintext export without `"demo": true`, or a configuration with an export key that writes plaintext.
    var config = try Contract.demoConfiguration()
    guard case .demo = try ExportPolicy(configuration: config) else { Issue.record("the demo configuration encrypts"); return }
    let raw = Curve25519.KeyAgreement.PrivateKey().publicKey.rawRepresentation
    for demo in [true, false] {
        config.demo = demo
        config.export.recipientPublicKey = raw
        guard case .encrypted(let key) = try ExportPolicy(configuration: config), key.rawRepresentation == raw else {
            Issue.record("a configuration with an export key exports plaintext (demo: \(demo))")
            return
        }
    }
    config.export.recipientPublicKey = nil
    #expect(throws: ExportPolicy.PolicyError.noExportKey) { try ExportPolicy(configuration: config) }

    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let s = storage(in: root)
    let id = SessionID()
    try s.createDirectories()
    try session(id).journal.write(to: s.journalURL(id))
    let folder = try s.publish(id, policy: .demo)
    let manifestURL = folder.appendingPathComponent("manifest.json")
    var manifest = try JSONSerialization.jsonObject(with: try Data(contentsOf: manifestURL)) as! [String: Any]
    #expect(manifest["demo"] as? Bool == true)
    manifest["demo"] = nil
    try JSONSerialization.data(withJSONObject: manifest).write(to: manifestURL)
    #expect(throws: Storage.StorageError.verification(nil, "the manifest does not decode")) {
        try Storage.readExport(folder)
    }
}

@Test func withdrawalDeletesOneParticipantAndLogsOnlyAKeyedHashOfTheCode() throws {
    // Detects a withdrawal that leaves a journal or export behind, touches another participant, or logs the code or a
    // plain hash of it, which anyone can reverse by trying every code.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let s = storage(in: root, secret: MemoryDeviceSecret(SymmetricKey(data: Data(repeating: 7, count: 32))))
    try s.createDirectories()
    func id(_ n: Int) -> SessionID { SessionID(UUID(uuidString: "4D000000-0000-4000-8000-00000000000\(n)")!) }

    for (n, participant, closed) in [(1, "WD-ALPHA", true), (2, "WD-ALPHA", false), (4, "WD-BRAVO", true), (5, "WD-BRAVO", false)] {
        try session(id(n), participant: ParticipantCode(participant)!, close: closed).journal.write(to: s.journalURL(id(n)))
        if closed { try s.publish(id(n), policy: .demo) }
    }
    let damaged = Contract.lines(try session(id(3), participant: "WD-ALPHA").journal)[0] + Data("\n{not json\n".utf8)
    try damaged.write(to: s.damaged.appendingPathComponent("\(id(3)).jsonl"))
    let before = try snapshot(root)

    let withdrawal = try s.withdraw(participant: "WD-ALPHA", at: Fixed.at)
    #expect(withdrawal.sessions == [id(1), id(2), id(3)])
    #expect(withdrawal.unreadable.isEmpty)
    let after = try snapshot(root)
    let log = "Support/Deletions.jsonl"
    #expect(Set(after.keys) == Set(before.keys.filter { !$0.contains("WD-ALPHA") && !$0.contains(id(2).description)
        && !$0.contains(id(3).description) }).union([log]))
    for (path, bytes) in after where path != log { #expect(before[path] == bytes, "\(path)") }

    let text = String(decoding: after[log]!, as: UTF8.self)
    #expect(!text.contains("WD-ALPHA"))
    #expect(Contract.lines(after[log]!).count == 1)
    let entry = try Fields(try JSON.decodeDocument(Data(text.utf8)), path: "")
    // HMAC-SHA256 of the code with 32 bytes of 7, computed with Python `hmac`.
    #expect(try entry.string("participantHMAC") == "c06f7fb40214d41c0096bd0b5ca91c6477bc3b839414e8e8d6150bbc211eab13")
    #expect(!entry.has("participantSHA256"))
    #expect(try entry.array("sessions") == [id(1), id(2), id(3)].map { .string($0.description) })
    #expect(try entry.string("at") == Fixed.at.text)
}

@Test func withdrawalDeletesRecoveringCopiesAndReportsJournalsWithoutAReadableCode() throws {
    // Detects a withdrawal that leaves a full journal copy in a `.recovering` file, or passes over a journal whose
    // first line cannot be read without telling the operator.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let s = storage(in: root)
    try s.createDirectories()
    let id = SessionID(UUID(uuidString: "4E000000-0000-4000-8000-000000000001")!)
    let journal = try session(id, participant: "WD-CHARLIE", close: false).journal
    try journal.write(to: s.journalURL(id))
    let copy = s.journalURL(id).appendingPathExtension("recovering")
    try journal.write(to: copy)
    let torn = s.damaged.appendingPathComponent("4E000000-0000-4000-8000-000000000002.jsonl")
    try Data(#"{"seq":0,"kind":"sessionOpe"#.utf8).write(to: torn)

    let withdrawal = try s.withdraw(participant: "WD-CHARLIE", at: Fixed.at)
    #expect(withdrawal.sessions == [id])
    #expect(withdrawal.unreadable.map(\.lastPathComponent) == [torn.lastPathComponent])
    #expect(!FileManager.default.fileExists(atPath: s.journalURL(id).path))
    #expect(!FileManager.default.fileExists(atPath: copy.path))
    #expect(FileManager.default.fileExists(atPath: torn.path))
}

@Test func onlyTheDeviceSecretConfirmsAWithdrawal() throws {
    // Detects a site that cannot confirm a withdrawal on the device, or a confirmation that works without the
    // device secret or for a code that was never withdrawn.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let secret = MemoryDeviceSecret()
    let s = storage(in: root, secret: secret)
    try s.createDirectories()
    #expect(try !s.verifyWithdrawal(participant: "WD-DELTA"))
    _ = try s.withdraw(participant: "WD-DELTA", at: Fixed.at)
    _ = try s.withdraw(participant: "WD-ECHO", at: Fixed.at)
    #expect(try s.verifyWithdrawal(participant: "WD-DELTA"))
    #expect(try storage(in: root, secret: secret).verifyWithdrawal(participant: "WD-ECHO"))
    #expect(try !s.verifyWithdrawal(participant: "WD-FOXTROT"))
    #expect(try !storage(in: root, secret: MemoryDeviceSecret()).verifyWithdrawal(participant: "WD-DELTA"))
}

@Test func oneJournalThatFailsDoesNotStopTheLaunch() throws {
    // Detects a launch that stops at the first journal that throws, so the journals after it get no `recovered` line.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let s = storage(in: root)
    try s.createDirectories()
    let unreadable = s.sessions.appendingPathComponent("0A000000-0000-4000-8000-000000000001.jsonl", isDirectory: true)
    try FileManager.default.createDirectory(at: unreadable, withIntermediateDirectories: true)
    let closed = SessionID(UUID(uuidString: "0A000000-0000-4000-8000-000000000002")!)
    try session(closed, participant: "P0002").journal.write(to: s.journalURL(closed))
    try FileManager.default.createDirectory(at: s.exports.appendingPathComponent("P0002_0a000000"),
                                            withIntermediateDirectories: true)
    let open = SessionID(UUID(uuidString: "0A000000-0000-4000-8000-000000000003")!)
    try session(open, participant: "P0003", close: false).journal.write(to: s.journalURL(open))

    let results = try s.reconcile(Fixed.launch, policy: .demo)
    #expect(results.count == 3)
    guard results.count == 3 else { return }
    guard case .unreadable(unreadable.lastPathComponent, .system) = results[0] else {
        Issue.record("unexpected result \(results[0])")
        return
    }
    #expect(results[1] == .publishFailed(file: s.journalURL(closed).lastPathComponent, .exportTaken(closed)))
    guard case .interrupted(let state) = results[2] else {
        Issue.record("unexpected result \(results[2])")
        return
    }
    #expect(state.status == .suspended)
    let last = try LineCodec.decode(Contract.lines(try Data(contentsOf: s.journalURL(open))).last!)
    #expect(last.event.kind == .recovered)
    #expect(FileManager.default.fileExists(atPath: s.journalURL(closed).path))
}

@Test func storageErrorsNameTheSessionAndNeverTheParticipant() throws {
    // Detects an error that holds an export name or a file path, which hold the participant code and would reach
    // the unified log when the app logs the error.
    let root = try temporaryFolder()
    let s = storage(in: root)
    defer { try? FileManager.default.removeItem(at: root) }
    defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: s.exports.path) }
    try s.createDirectories()
    let id = SessionID(UUID(uuidString: "7F000000-0000-4000-8000-000000000001")!)
    try session(id, participant: "WD-GOLF").journal.write(to: s.journalURL(id))
    try s.publish(id, policy: .demo)

    try session(id, participant: "WD-GOLF", reason: .declined).journal.write(to: s.journalURL(id))
    let taken = #expect(throws: Storage.StorageError.self) { try s.publish(id, policy: .demo) }
    #expect(taken == .exportTaken(id))

    try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: s.exports.path)
    let blocked = #expect(throws: Storage.StorageError.self) { try s.withdraw(participant: "WD-GOLF", at: Fixed.at) }
    guard case .system? = blocked else {
        Issue.record("unexpected error \(String(describing: blocked))")
        return
    }
    for error in [taken, blocked] { #expect(!String(describing: error).contains("WD-GOLF")) }
}
