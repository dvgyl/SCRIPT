import CryptoKit
import Foundation
import Testing
@testable import ScriptCore

private func problems(_ config: StudyConfiguration, instruments: InstrumentSet? = nil) throws -> [ConfigProblem] {
    validate(config: config, battery: try Contract.battery(), instruments: try instruments ?? Contract.instruments())
}

/// The shipped instruments plus one test instrument, so a test never depends on the status of a shipped instrument.
private func instrumentsWithProbe(textStatus: String, minimumAge: Int) throws -> InstrumentSet {
    var root = try JSONSerialization.jsonObject(with: try Contract.data("instruments.json")) as! [String: Any]
    var list = root["instruments"] as! [[String: Any]]
    list.append([
        "id": "probe", "version": "1.0", "title": "Probe", "textStatus": textStatus,
        "ages": ["min": minimumAge, "max": NSNull()], "locale": "en", "stem": "Probe stem",
        "items": [["id": "probe-1", "text": "Probe item", "options": [["value": 0, "label": "No"]],
                   "scored": true, "safety": false]],
    ])
    root["instruments"] = list
    return try InstrumentSet(data: try JSONSerialization.data(withJSONObject: root))
}

private func withProbeBlock(_ config: StudyConfiguration) -> StudyConfiguration {
    var config = config
    config.questionnaires.append(.init(administration: .supervised, ageBand: .adolescent, position: .afterTasks,
                                       instruments: ["probe"]))
    return config
}

@Test func theDemoConfigurationPassesEveryRule() throws {
    // Detects a shipped demo configuration that the app would refuse to import.
    #expect(try problems(try Contract.demoConfiguration()) == [])
}

@Test func aSelfAdministeredSafetyItemNeedsRemoteReview() throws {
    // Detects a configuration that puts PHQ-9 item 9 in an unmonitored home session, or monitors it too rarely.
    var config = try Contract.demoConfiguration()
    config.questionnaires.append(.init(administration: .selfAdministered, ageBand: .youngAdult, position: .afterTasks,
                                       instruments: ["phq9"]))
    #expect(try problems(config) == [.safetyItemWithoutRemoteReview("phq9")])
    config = try Contract.remoteReviewConfiguration()
    #expect(try problems(config) == [])
    config.safety.remoteReview?.exportEveryHours = 48
    #expect(try problems(config) == [.exportIntervalTooLong])
}

@Test func anUnverifiedInstrumentIsRefused() throws {
    // Detects a configuration that administers instrument text that nobody checked against the published form.
    let config = withProbeBlock(try Contract.demoConfiguration())
    #expect(try problems(config, instruments: try instrumentsWithProbe(textStatus: "unverified", minimumAge: 13))
        == [.unverifiedInstrument("probe")])
}

@Test func anInstrumentOutsideItsValidatedAgesIsRefused() throws {
    // Detects a configuration that gives an instrument to an age band below its validated range.
    let config = withProbeBlock(try Contract.demoConfiguration())
    #expect(try problems(config, instruments: try instrumentsWithProbe(textStatus: "verified", minimumAge: 14))
        == [.notValidatedForAgeBand("probe", .adolescent)])
}

@Test func aStudyConfigurationWithoutAnExportKeyOrAPinIsRefused() throws {
    // Detects a real study configuration that would write plaintext exports or open operator screens freely.
    var config = try Contract.demoConfiguration()
    config.demo = false
    #expect(try problems(config) == [.noExportKey, .noOperatorPin])
}

@Test func aQualificationTaskCannotEnterAStudyConfiguration() throws {
    // Detects a study configuration that mixes device-qualification tasks into participant sessions.
    var config = try Contract.demoConfiguration()
    config.tasks.append(.init(task: "qual-hold", practice: false, attempts: 1, hands: .dominant))
    #expect(try problems(config) == [.qualificationTask("qual-hold")])
}

@Test(arguments: [
    (#""attempts": 2"#, #""attempts": 6"#, DecodeError.invalid("tasks[1].attempts")),
    (#""demo": true,"#, #""demo": true, "colour": "blue","#, .unexpected("colour")),
    (#""recipientPublicKey": null"#, #""recipientPublicKey": "c2hvcnQ=""#, .invalid("export.recipientPublicKey")),
    (#""sites": ["#, #""sites": ["DEMO", "#, .invalid("sites")),
    (#""remoteReview": null"#, #""remoteReview": {"reviewerRole": "x"}"#, .missing("safety.remoteReview.imminentReviewWithinHours")),
])
func configurationImportAppliesTheSchemaRules(original: String, replacement: String, expected: DecodeError) throws {
    // Detects an import that accepts a configuration which contract/config.schema.json refuses.
    let text = String(decoding: try Contract.data("configs/demo.json"), as: UTF8.self)
    let range = try #require(text.range(of: original))
    let changed = text.replacingCharacters(in: range, with: replacement)
    #expect(throws: expected) { try StudyConfiguration(data: Data(changed.utf8)) }
}

@Test func theOperatorPinMatchesOnlyItsPin() throws {
    // Detects a PIN check that accepts a wrong PIN or hashes the salt and the PIN in another order.
    let salt = "c0ffee00c0ffee00"
    let stored = try #require(OperatorPin(pin: "4821", salt: salt))
    #expect(stored.sha256.hex == SHA256.hash(data: Data("c0ffee00c0ffee004821".utf8)).hex)
    #expect(stored.matches("4821"))
    #expect(!stored.matches("4822"))
    #expect(!stored.matches(""))
    #expect(OperatorPin(pin: "4821", salt: "short") == nil)
}

@Test func theModelTableScaleMatchesTheContractFormula() throws {
    // Detects a model-table scale that differs from 25.4 × nativeScale / ppi as the contract records it.
    let line = Contract.lines(try Contract.data("fixtures/journal/basic.jsonl"))[0]
    guard case .sessionOpened(let opened) = try LineCodec.decode(line).event else { throw DecodeError.notAnObject }
    #expect(MillimetreScale.lookup(model: "iPad16,3", nativeScale: 2) == opened.scale)
    #expect(DeviceTable.density(model: "iPad16,3")?.ppiConfirmed == false)
    #expect(MillimetreScale.lookup(model: "iPad99,9", nativeScale: 2) == nil)
}

@Test func theIDCardScaleUsesTheCardWidth() throws {
    // Detects an ID-1 card scale that does not use the 85.60 mm card width.
    let line = Contract.lines(try Contract.data("fixtures/journal/self_administered.jsonl"))[0]
    guard case .sessionOpened(let opened) = try LineCodec.decode(line).event else { throw DecodeError.notAnObject }
    #expect(MillimetreScale.idCard(drawnPoints: 444.67532467532465) == opened.scale)
    #expect(MillimetreScale.steelRule(measuredMM: 50, drawnPoints: 250)?.mmPerPoint == 0.2)
    #expect(MillimetreScale.idCard(drawnPoints: 0) == nil)
}
