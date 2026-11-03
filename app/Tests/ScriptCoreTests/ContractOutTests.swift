import CryptoKit
import Foundation
import Testing
@testable import ScriptCore

/// `$SCRIPT_CONTRACT_OUT`, or `contract-out` in the build cache folder that `./check` uses.
private var contractOut: URL {
    let environment = ProcessInfo.processInfo.environment
    if let path = environment["SCRIPT_CONTRACT_OUT"], !path.isEmpty { return URL(fileURLWithPath: path) }
    if let path = environment["SCRIPT_BUILD"], !path.isEmpty {
        return URL(fileURLWithPath: path).appendingPathComponent("contract-out")
    }
    let root = Contract.repository.path
    let hash = Insecure.SHA1.hash(data: Data(root.utf8)).hex.prefix(8)
    return FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Caches/SCRIPT/\(Contract.repository.lastPathComponent)-\(hash)/contract-out")
}

private func replace(_ destination: URL, with source: URL) throws {
    try? FileManager.default.removeItem(at: destination)
    try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
    try FileManager.default.copyItem(at: source, to: destination)
}

@Test func aScriptedSupervisedSessionWritesTheContractExport() async throws {
    // Detects a Swift-written export that the Python reader would refuse or fold to a summary other than the Swift one.
    let config = try Contract.demoConfiguration()
    let id = SessionID(UUID(uuidString: "5C2F7E57-0000-4000-8000-000000000001")!)
    let opened = makeOpened(id: id, participant: "SWIFT-0001", configuration: config, battery: try Contract.battery())
    let sessionRules = try rules(for: opened, configuration: config)
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let s = storage(in: root)
    try s.createDirectories()
    let url = s.journalURL(id)

    var first = try Driver(opened: opened, rules: sessionRules, writer: try JournalWriter(appendingTo: url, onFailure: { _ in }))
    try first.run(.handoff(to: .participant, t: 101))
    try first.run(.startTrial(Driver.plan("spiral-trace"), t: 102))
    try first.run(.beginTrial(t: 102.5))
    try first.stroke(key: 1, t0: 103, pending: [.force], update: 11)
    try first.write([first.recorder.hover(HoverReading(t: 103.2, x: 310, y: 405, z: 0.4, altitude: 0.9, azimuth: 2, roll: 0.3))!])
    try first.write(first.recorder.estimatesUpdated(
        [PenReading(key: TouchKey(1), t: 103, x: 300, y: 400, force: 0.42, phase: .moved, update: UpdateIndex(11))],
        arrived: 103.3))
    try first.stroke(key: 2, t0: 104, x0: 320, pending: [.force, .altitude], update: 12)
    try first.write([first.recorder.finger(FingerReading(t: 104.5, phase: .began, x: 50, y: 60))!,
                     first.recorder.finger(FingerReading(t: 104.6, phase: .ended, x: 50, y: 61))!])
    try first.run(.endTrial(reason: .participantDone, t: 105))
    try first.run(.mark(trial: TrialIndex(count: 0), code: .repeatRequested, t: 105.5))
    try first.run(.startTrial(Driver.plan("spiral-trace"), t: 106))
    try first.run(.beginTrial(t: 106.5))
    try first.write(first.recorder.estimatesUpdated(
        [PenReading(key: TouchKey(2), t: 104, x: 320, y: 400, force: 0.37, altitude: 1.1, phase: .moved,
                    update: UpdateIndex(12))],
        arrived: 106.6))
    try first.stroke(key: 3, t0: 107, lift: false)
    try await first.writer!.close()
    let torn = Data(#"{"seq":99,"kind":"sample","contact":3,"t":107.0"#.utf8)
    let handle = try FileHandle(forWritingTo: url)
    try handle.seekToEnd()
    try handle.write(contentsOf: torn)
    try handle.close()

    guard case .interrupted(let recovered) = try s.reconcile(Fixed.launch, policy: .demo).first else {
        Issue.record("the relaunch did not adopt the open journal")
        return
    }
    var second = Driver(resuming: recovered, rules: sessionRules, writer: try JournalWriter(appendingTo: url, onFailure: { _ in }))
    try second.run(.startTrial(Driver.plan("spiral-trace"), t: 10))
    try second.run(.beginTrial(t: 10.5))
    try second.stroke(key: 1, t0: 11)
    try second.run(.endTrial(reason: .participantDone, t: 12))
    try second.answerAll("phq9", t: 20, values: ["phq9-2": 2, "phq9-9": 1])
    try second.run(.handoff(to: .operator, t: 45))
    try second.run(.show(.operatorAlert, t: 46))
    try second.run(.recordAction("clinicianAssessment", t: 50))
    try second.run(.show(.crisisList, t: 51))
    try second.run(.close(t: 52))
    try await second.writer!.close()

    let summary = second.state.summary
    #expect(summary.trials.map(\.outcome) == [.completed, .interrupted, .completed])
    #expect(summary.trials.map(\.attempt) == [1, 2, 3])
    #expect(summary.trials.map(\.contacts) == [2, 1, 1])
    #expect(summary.trials[0].corrections == 2 && summary.trials[0].unresolved == 0)
    #expect(summary.unmatchedCorrections == 0 && summary.truncatedBytes == torn.count)
    #expect(summary.safety == SafetyRecord(flags: 1, crisisListShown: 1, operatorAlerts: 1, actions: 1))
    for kind in [LineKind.hover, .mark, .recovered] { #expect(summary.counts[kind] == 1, "\(kind)") }
    #expect(summary.counts[.finger] == 2)

    let copy = root.appendingPathComponent("second")
    let encryptedStorage = storage(in: copy)
    try encryptedStorage.createDirectories()
    try FileManager.default.copyItem(at: url, to: encryptedStorage.journalURL(id))

    let plaintext = try s.publish(id, policy: .demo)
    let exported = try Storage.readExport(plaintext)
    #expect(exported.state.summary == summary)
    let key = Curve25519.KeyAgreement.PrivateKey()
    let encrypted = try encryptedStorage.publish(id, policy: .encrypted(recipient: key.publicKey))
    #expect(try Storage.readExport(encrypted, privateKey: key).journal == exported.journal)

    let out = contractOut
    let name = opened.exportName
    try replace(out.appendingPathComponent(name), with: plaintext)
    try summary.jsonData().write(to: out.appendingPathComponent("\(name).summary.json"))
    try replace(out.appendingPathComponent("encrypted/\(name)"), with: encrypted)
    try Data((key.rawRepresentation.base64EncodedString() + "\n").utf8)
        .write(to: out.appendingPathComponent("encrypted/private_key.txt"))
}
