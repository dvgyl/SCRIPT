import Foundation
import Testing
@testable import ScriptCore

enum Contract {
    static let root = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .appendingPathComponent("../../../contract")
        .standardizedFileURL

    static func json(_ path: String) throws -> Any {
        try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent(path)))
    }
}

@Test func formatVersionMatchesTheSchema() throws {
    // Detects a Swift format constant that drifts from the schema's sessionOpened.format.
    let schema = try #require(Contract.json("journal.schema.json") as? [String: Any])
    let defs = try #require(schema["$defs"] as? [String: Any])
    let opened = try #require(defs["sessionOpened"] as? [String: Any])
    let properties = try #require(opened["properties"] as? [String: Any])
    let format = try #require(properties["format"] as? [String: Any])
    #expect(format["const"] as? String == JournalFormat.version)
}
