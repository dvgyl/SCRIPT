import Foundation
import Synchronization
import Testing
@testable import ScriptCore

/// Every failure that a writer reports to the app.
private final class Reports: Sendable {
    private let errors = Mutex<[JournalWriter.WriteError]>([])

    func record(_ error: JournalWriter.WriteError) { errors.withLock { $0.append(error) } }

    var all: [JournalWriter.WriteError] { errors.withLock { $0 } }

    /// The first report, or nil when none arrives within two seconds.
    func first() async throws -> JournalWriter.WriteError? {
        for _ in 0..<200 {
            if let error = all.first { return error }
            try await Task.sleep(for: .milliseconds(10))
        }
        return nil
    }
}

/// A storage fault that a test turns on and off.
private final class Fault: Sendable {
    private let on: Mutex<Bool>

    init(on: Bool) { self.on = Mutex(on) }

    var isOn: Bool { on.withLock { $0 } }

    func clear() { on.withLock { $0 = false } }
}

private func handoff(_ seq: Int) -> Entry {
    Entry(seq: seq, event: .handoff(Handoff(t: Double(seq), to: .participant)))
}

/// A `contactEnded` line, which makes the writer flush.
private func lift(_ seq: Int) -> Entry {
    Entry(seq: seq, event: .contactEnded(ContactEnded(t: Double(seq), contact: .first, cancelled: false)))
}

private func encoded(_ entries: [Entry]) throws -> Data {
    var data = Data()
    for entry in entries { data.append(try LineCodec.encode(entry)) }
    return data
}

private func writer(in root: URL, reports: Reports, stops: @escaping @Sendable (Storage.Step) -> Bool) throws
    -> (JournalWriter, URL)
{
    let url = root.appendingPathComponent("journal.jsonl")
    let writer = try JournalWriter(appendingTo: url, flushInterval: .seconds(3600), onFailure: { reports.record($0) },
                                   stops: stops)
    return (writer, url)
}

@Test func aLineThatDoesNotDecodeIsNeverWritten() async throws {
    // Detects a writer that writes a line the decoder refuses, so the next launch moves the whole session to Damaged/.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let (writer, url) = try writer(in: root, reports: Reports(), stops: { _ in false })
    let negative = Entry(seq: 1, event: .handoff(Handoff(t: -1, to: .participant)))
    #expect(throws: JournalWriter.WriteError.undecodable(.invalid("t"))) { try writer.append([handoff(0), negative]) }
    try writer.append([handoff(0)])
    try await writer.close()
    #expect(try Data(contentsOf: url) == encoded([handoff(0)]))
}

@Test func aFailedWriteReachesTheAppAtOnceAndRefusesLaterAppends() async throws {
    // Detects a writer that drops lines silently after a failed write while `append` still returns success.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let reports = Reports()
    let (writer, url) = try writer(in: root, reports: reports, stops: { $0 == .journalWrite })
    let accepted = [handoff(0), lift(1)]
    try writer.append(accepted)
    #expect(try await reports.first() == .io(EIO))
    #expect(throws: JournalWriter.WriteError.io(EIO)) { try writer.append([handoff(2)]) }
    await #expect(throws: JournalWriter.WriteError.io(EIO)) { try await writer.close() }
    #expect(reports.all == [.io(EIO)])
    let total = try encoded(accepted).count
    #expect(try Data(contentsOf: url).count < total)
}

@Test func linesHeldAfterAFailedWriteAreWrittenOnceByTheNextFlush() async throws {
    // Detects a writer that discards its buffer after a failed write, or writes part of it twice when it retries.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let diskFull = Fault(on: true)
    let (writer, url) = try writer(in: root, reports: Reports(), stops: { $0 == .journalWrite && diskFull.isOn })
    let accepted = [handoff(0), handoff(1), lift(2)]
    try writer.append(accepted)
    await #expect(throws: JournalWriter.WriteError.io(EIO)) { try await writer.sync() }
    let written = try Data(contentsOf: url)
    let total = try encoded(accepted).count
    #expect(!written.isEmpty && written.count < total)

    diskFull.clear()
    try await writer.sync()
    #expect(try Data(contentsOf: url) == encoded(accepted))
    #expect(throws: JournalWriter.WriteError.io(EIO)) { try writer.append([handoff(3)]) }
    try await writer.close()
    #expect(try Data(contentsOf: url) == encoded(accepted))
}

private let syncedLines: [Event] = [
    .trialEnded(TrialEnded(t: 1, trial: TrialIndex(count: 0), reason: .participantDone)),
    .questionnaireEnded(QuestionnaireEnded(t: 1, block: BlockIndex(count: 0), reason: .completed)),
    .safetyFlagged(SafetyFlagged(t: 1, block: BlockIndex(count: 0), item: "phq9-9", value: 2)),
    .safetyAction(SafetyAction(t: 1, code: "clinicianAssessment", role: .operator)),
    .sessionClosed(SessionClosed(t: 1, counts: KindCounts())),
]

@Test(arguments: syncedLines)
func theWriterSyncsAtEverySafetyAndEndLine(event: Event) async throws {
    // Detects a safety or end line that waits in memory or in the drive cache, where a crash or power loss loses it.
    let root = try temporaryFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let reports = Reports()
    let (writer, url) = try writer(in: root, reports: reports, stops: { $0 == .journalSync })
    let entry = Entry(seq: 1, event: event)
    try writer.append([entry])
    #expect(try await reports.first() == .io(EIO))
    #expect(try Data(contentsOf: url) == encoded([entry]))
    _ = try? await writer.close()
}
