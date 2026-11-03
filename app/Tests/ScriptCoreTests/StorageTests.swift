import CryptoKit
import Foundation
import Testing
@testable import ScriptCore

/// A session of one short trial, closed when `close` is true and left with the pen down otherwise.
private func session(_ id: SessionID, participant: ParticipantCode = "P0001", close: Bool = true) throws -> Driver {
    let config = try Contract.demoConfiguration()
    let opened = makeOpened(id: id, participant: participant, configuration: config, battery: try Contract.battery())
    var d = try Driver(opened: opened, rules: try rules(for: opened, configuration: config))
    try d.run(.startTrial(Driver.plan("spiral-trace"), t: 101))
    try d.run(.beginTrial(t: 101.5))
    try d.stroke(key: 1, t0: 102, pending: [.force], update: 3, lift: close)
    if close {
        try d.run(.endTrial(reason: .participantDone, t: 104))
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

@Test func publishAndReconcileStepsCoverEveryStep() {
    // Detects a new crash point that no convergence test stops at.
    #expect(Set(publishSteps + reconcileSteps) == Set(Storage.Step.allCases))
}

@Test func theWriterKeepsCallOrderAndFlushesAtContactEnded() async throws {
    // Detects a writer that reorders lines, or holds a lifted stroke in memory where a crash would lose it.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let url = root.appendingPathComponent("journal.jsonl")
    let d = try session(SessionID(), close: false)
    let lines = try Contract.lines(d.journal).map { try LineCodec.decode($0) }
    let writer = try JournalWriter(appendingTo: url, flushInterval: .seconds(3600))
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
    // Detects a publication that a crash at this step leaves half done, duplicated, or without its source journal.
    let key = Curve25519.KeyAgreement.PrivateKey()
    let exportPolicy: ExportPolicy = policy == .demo ? .demo : .encrypted(recipient: key.publicKey, keyID: "test")
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
    _ = try relaunched.reconcile(Fixed.launch, policy: exportPolicy)
    if FileManager.default.fileExists(atPath: relaunched.journalURL(id).path) {
        try relaunched.publish(id, policy: exportPolicy)
    }
    let converged = try snapshot(crashRoot)
    let expected = try snapshot(referenceRoot)
    switch policy {
    case .demo:
        #expect(converged == expected)
    case .encrypted:
        #expect(Set(converged.keys) == Set(expected.keys))
        let folder = relaunched.exports.appendingPathComponent("P0001_2b000000")
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

@Test func anEncryptedExportDecryptsWithTheStudyKeyAndMatchesItsManifest() throws {
    // Detects an encrypted export that the study's private key cannot open, or whose plaintext differs from the manifest.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let s = storage(in: root)
    let id = SessionID()
    let journal = try session(id).journal
    try s.createDirectories()
    try journal.write(to: s.journalURL(id))
    let key = Curve25519.KeyAgreement.PrivateKey()
    let folder = try s.publish(id, policy: .encrypted(recipient: key.publicKey, keyID: "study-key-1"))

    #expect(try FileManager.default.contentsOfDirectory(atPath: folder.path).filter { !$0.hasPrefix("._") }.sorted()
        == [Storage.Export.encryptedJournal, Storage.Export.manifest])
    let manifest = try Fields(try JSON.decodeDocument(try Data(contentsOf: folder.appendingPathComponent("manifest.json"))), path: "")
    #expect(!manifest.has("journal"))
    let plaintext = try manifest.object("encryption").object("plaintext")
    #expect(try plaintext.string("sha256") == SHA256Digest(of: journal).hex)
    #expect(try plaintext.int("bytes") == journal.count)
    #expect(try Storage.readExport(folder, privateKey: key).journal == journal)
    #expect(throws: (any Error).self) { try Storage.readExport(folder, privateKey: Curve25519.KeyAgreement.PrivateKey()) }
}

@Test func onlyADemoConfigurationExportsPlaintext() throws {
    // Detects a study configuration that would write a plaintext export, or a key that is not used when present.
    var config = try Contract.demoConfiguration()
    guard case .demo = try ExportPolicy(configuration: config) else { Issue.record("the demo configuration encrypts"); return }
    config.demo = false
    #expect(throws: ExportPolicy.PolicyError.noExportKey) { try ExportPolicy(configuration: config) }
    let raw = Curve25519.KeyAgreement.PrivateKey().publicKey.rawRepresentation
    config.export.recipientPublicKey = raw
    guard case .encrypted(let key, let keyID) = try ExportPolicy(configuration: config) else {
        Issue.record("a study configuration with a key exports plaintext")
        return
    }
    #expect(key.rawRepresentation == raw && keyID == SHA256Digest(of: raw).hex)
}

@Test func withdrawalDeletesOneParticipantAndLogsWithoutTheCode() throws {
    // Detects a withdrawal that leaves a journal or export behind, touches another participant, or logs the code.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let s = storage(in: root)
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
    let after = try snapshot(root)
    let log = "Support/Deletions.jsonl"
    #expect(Set(after.keys) == Set(before.keys.filter { !$0.contains("WD-ALPHA") && !$0.contains(id(2).description)
        && !$0.contains(id(3).description) }).union([log]))
    for (path, bytes) in after where path != log { #expect(before[path] == bytes, "\(path)") }

    let text = String(decoding: after[log]!, as: UTF8.self)
    #expect(!text.contains("WD-ALPHA"))
    #expect(Contract.lines(after[log]!).count == 1)
    let entry = try Fields(try JSON.decodeDocument(Data(text.utf8)), path: "")
    #expect(try entry.string("participantSHA256") == SHA256Digest(of: Data("WD-ALPHA".utf8)).hex)
    #expect(try entry.array("sessions") == [id(1), id(2), id(3)].map { .string($0.description) })
    #expect(try entry.string("at") == Fixed.at.text)
}
