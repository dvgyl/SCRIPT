import Foundation
import Testing
@testable import ScriptCore

@Test func formatVersionMatchesTheSchema() throws {
    // Detects a Swift format constant that drifts from the schema's sessionOpened.format.
    let schema = try Fields(try Contract.json("journal.schema.json"), path: "")
    let format = try schema.object("$defs").object("sessionOpened").object("properties").object("format")
    #expect(try format.string("const") == JournalFormat.version)
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
])
func decodeErrorsNameTheKeyPath(line: String, expected: DecodeError) {
    // Detects a decoder that accepts a malformed line or reports the wrong key, which hides the damaged field.
    #expect(throws: expected) { try LineCodec.decode(Data(line.utf8)) }
}

extension String {
    func leftPadded(_ width: Int) -> String { String(repeating: "0", count: max(0, width - count)) + self }
}
