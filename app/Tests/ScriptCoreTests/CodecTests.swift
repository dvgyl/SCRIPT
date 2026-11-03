import Foundation
import Testing
@testable import ScriptCore

@Test func formatVersionMatchesTheSchema() throws {
    // Detects a Swift format constant that drifts from the schema's sessionOpened.format.
    let schema = try Fields(try Contract.json("journal.schema.json"), path: "")
    let format = try schema.object("$defs").object("sessionCommon").object("properties").object("format")
    #expect(try format.string("const") == JournalFormat.version)
}

/// The header of `basic.jsonl` with `format` replaced and, when `channels` is given, its `channels` object replaced.
private func header(format: String, channels: String? = nil) throws -> Data {
    var text = String(decoding: Contract.lines(try Contract.data("fixtures/journal/basic.jsonl"))[0], as: UTF8.self)
    let formatRange = try #require(text.range(of: #""format":"2.0""#))
    text.replaceSubrange(formatRange, with: #""format":"\#(format)""#)
    if let channels {
        let start = try #require(text.range(of: #""channels":{"#))
        let end = try #require(text.range(of: "}", range: start.upperBound..<text.endIndex))
        text.replaceSubrange(start.lowerBound..<end.upperBound, with: #""channels":\#(channels)"#)
    }
    return Data(text.utf8)
}

private func manifest(version: String) -> Data {
    Data(#"{"format":"script.journal","version":"\#(version)","sessionID":"3F2A9C1E-0000-4000-8000-000000000001","#.utf8)
        + Data(#""demo":true,"journal":{"bytes":1,"lines":1,"sha256":"\#(String(repeating: "0", count: 64))"}}"#.utf8)
}

@Test func aFormat1HeaderDecodesWithAbsentPlanKeysAsFalse() throws {
    // Detects a 2.0 reader that refuses the 1.0 journals of the first device tests, whose channels object has the
    // five Pencil keys only.
    let line = try header(format: "1.0", channels: #"{"force":true,"altitude":true,"azimuth":true,"roll":false,"hover":true}"#)
    let entry = try LineCodec.decode(line)
    guard case .sessionOpened(let opened) = entry.event else { throw DecodeError.notAnObject }
    #expect(opened.format == "1.0")
    #expect(opened.channels == ChannelPlan(force: true, altitude: true, azimuth: true, roll: false, hover: true,
                                           tiltX: false, tiltY: false, rotation: false))
    #expect(throws: Never.self) { try SessionState(opening: entry) }
    #expect(throws: Never.self) { try Manifest(json: try JSON.decodeDocument(manifest(version: "1.0"))) }
}

@Test func aFormat3HeaderIsRefused() throws {
    // Detects a reader that accepts a major version it does not know, in the journal or in the manifest.
    let entry = try LineCodec.decode(try header(format: "3.0"))
    #expect(throws: FoldError(line: 0, violation: .unsupportedFormat("3.0"))) { try SessionState(opening: entry) }
    #expect(throws: DecodeError.invalid("version")) { try Manifest(json: try JSON.decodeDocument(manifest(version: "3.0"))) }
}

@Test(arguments: Contract.journalFixtures.map { "fixtures/journal/\($0).jsonl" } + ["\(Contract.exportFixture)/journal.jsonl"])
func everyFixtureLineEncodesBackToItsBytes(path: String) throws {
    // Detects a codec that changes a line on the way through: a lost key, a null for an absent key, or new number text.
    for (number, line) in Contract.lines(try Contract.data(path)).enumerated() {
        let entry = try LineCodec.decode(line)
        #expect(try LineCodec.encode(entry) == line + Data([0x0A]), "\(path):\(number)")
    }
}

@Test func doublesParseToTheirBitsAndTheCodecTextRoundTrips() throws {
    // Detects a float text that does not parse to the recorded binary64 value, or codec text that loses bits.
    let rows = try Contract.json("fixtures/doubles.json")
    guard case .array(let items) = try Fields(rows, path: "").read("doubles", { $0 }) else { throw DecodeError.invalid("doubles") }
    #expect(items.count == 16)
    for item in items {
        let row = try Fields(item, path: "row")
        let text = try row.string("text")
        let value = try #require(Double(text))
        #expect(String(value.bitPattern, radix: 16).leftPadded(16) == (try row.string("bits")), "\(text)")

        let sample = Sample(contact: .first, t: value, x: value, y: -value, force: nil, altitude: nil, azimuth: nil,
                            roll: nil, phase: .moved, est: [], pending: nil)
        let line = try LineCodec.encode(Entry(seq: 0, event: .sample(sample)))
        guard case .sample(let back) = try LineCodec.decode(line.dropLast()).event else { Issue.record("not a sample"); continue }
        #expect(back.x.bitPattern == value.bitPattern && back.y.bitPattern == (-value).bitPattern, "\(text)")
    }
}

@Test func aNonFiniteValueIsRefused() {
    // Detects a codec that writes NaN or an infinity, which JSON cannot hold.
    let sample = Sample(contact: .first, t: 1, x: 1, y: 2, force: .nan, altitude: nil, azimuth: nil, roll: nil,
                        phase: .moved, est: [], pending: nil)
    #expect(throws: LineCodec.EncodeError.nonFinite("force")) { try LineCodec.encode(Entry(seq: 1, event: .sample(sample))) }
    let hover = Hover(t: .infinity, clock: .callback, x: 1, y: 2, z: 0.5, altitude: nil, azimuth: nil, roll: nil)
    #expect(throws: LineCodec.EncodeError.nonFinite("t")) { try LineCodec.encode(Entry(seq: 1, event: .hover(hover))) }
}

@Test func anAbsentChannelIsAnAbsentKey() throws {
    // Detects an absent channel written as null or as 0, which the contract forbids.
    let sample = Sample(contact: .first, t: 1, x: 1, y: 2, force: nil, altitude: nil, azimuth: nil, roll: nil,
                        phase: .moved, est: [], pending: nil)
    let text = String(decoding: try LineCodec.encode(Entry(seq: 3, event: .sample(sample))), as: UTF8.self)
    #expect(text == #"{"seq":3,"kind":"sample","contact":1,"t":1.0,"x":1.0,"y":2.0,"phase":"moved","est":0}"# + "\n")
}

@Test(arguments: [
    (#"{"seq":1,"kind":"handoff","to":"participant"}"#, DecodeError.missing("t")),
    (#"{"seq":1,"kind":"handoff","t":"soon","to":"participant"}"#, .invalid("t")),
    (#"{"seq":1,"kind":"handoff","t":-1.0,"to":"participant"}"#, .invalid("t")),
    (#"{"seq":1,"kind":"teleport","t":1.0}"#, .unknownKind("teleport")),
    (#"[1,2]"#, .notAnObject),
    (#"{"seq":1,"kind""#, .syntax(offset: 15)),
    (#"{"seq":1,"kind":"response","t":1.0,"block":0,"item":"phq9-1","skipped":false}"#, .invalid("value")),
    (#"{"seq":1,"kind":"response","t":1.0,"block":0,"item":"phq9-1","value":1,"skipped":true}"#, .invalid("value")),
    (#"{"seq":1,"kind":"sample","contact":0,"t":1.0,"x":1.0,"y":1.0,"phase":"moved","est":0}"#, .invalid("contact")),
    (#"{"seq":1,"kind":"sample","contact":1,"t":1.0,"x":1.0,"y":1.0,"phase":"moved","est":0,"pending":{"expect":0,"update":3}}"#,
     .invalid("pending.expect")),
    (#"{"seq":1,"kind":"recovered","t":1.0,"at":"x","boot":"not-a-uuid","truncatedBytes":0}"#, .invalid("boot")),
    (#"{"seq":1,"kind":"sample","contact":1,"t":1.0,"x":1.0,"y":1.0,"tiltX":1.5,"phase":"moved","est":0}"#, .invalid("tiltX")),
    (#"{"seq":1,"kind":"sample","contact":1,"t":1.0,"x":1.0,"y":1.0,"force":-0.1,"phase":"moved","est":0}"#, .invalid("force")),
    (#"{"seq":1,"kind":"hover","t":1.0,"clock":"event","x":1.0,"y":1.0,"z":-0.1}"#, .invalid("z")),
])
func decodeErrorsNameTheKeyPath(line: String, expected: DecodeError) {
    // Detects a decoder that accepts a malformed line or reports the wrong key, which hides the damaged field.
    #expect(throws: expected) { try LineCodec.decode(Data(line.utf8)) }
}

/// `basic.jsonl` lines 0 and 1 and `bench_wacom.jsonl` lines 0 and 1, as text, by the name a test gives them.
private func subjectLine(_ name: String) throws -> String {
    let participant = Contract.lines(try Contract.data("fixtures/journal/basic.jsonl"))
    let bench = Contract.lines(try Contract.data("fixtures/journal/bench_wacom.jsonl"))
    let lines = ["participant header": participant[0], "participant trial": participant[1],
                 "bench header": bench[0], "bench trial": bench[1]]
    return String(decoding: try #require(lines[name]), as: UTF8.self)
}

@Test(arguments: [
    ("participant header", #""purpose":"study","#, #""purpose":"study","bench":{"role":"P"},"#, DecodeError.unexpected("bench")),
    ("bench header", #""purpose":"bench","#, #""purpose":"bench","participant":"P0001","#, .unexpected("participant")),
    ("bench header", #""purpose":"bench","#, #""purpose":"bench","consent":{},"#, .unexpected("consent")),
    ("bench header", #""input":"tablet","#, #""input":"pencil","#, .unexpected("tablet")),
    ("participant header", #""input":"pencil","#, #""input":"tablet","#, .missing("tablet")),
    ("bench trial", #""bench":{"#, #""hand":"right","bench":{"#, .unexpected("hand")),
    ("bench trial", #""run":1,"#, #""run":0,"#, .invalid("bench.run")),
    ("bench trial", #""run":1,"#, #""run":1,"clip":"","#, .invalid("bench.clip")),
])
func theSubjectShapeFollowsThePurposeAndTheBenchKey(name: String, original: String, replacement: String,
                                                     expected: DecodeError) throws {
    // Detects a decoder that accepts a participant key on a bench line, a bench key on a participant line, or a
    // tablet object that disagrees with the input, which the Python reader refuses.
    let line = try subjectLine(name)
    let range = try #require(line.range(of: original))
    let changed = line.replacingCharacters(in: range, with: replacement)
    #expect(throws: expected) { try LineCodec.decode(Data(changed.utf8)) }
}

extension String {
    func leftPadded(_ width: Int) -> String { String(repeating: "0", count: max(0, width - count)) + self }
}
