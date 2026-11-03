import CryptoKit
import Foundation
import Testing
@testable import ScriptCore

private func problems(_ config: StudyConfiguration, instruments: InstrumentSet? = nil) throws -> [ConfigProblem] {
    validate(config: config, battery: try Contract.battery(), instruments: try instruments ?? Contract.instruments())
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
    #expect(try problems(config, instruments: try Contract.instrumentsWithProbe(textStatus: "unverified", minimumAge: 13))
        == [.unverifiedInstrument("probe")])
}

@Test func anInstrumentOutsideItsValidatedAgesIsRefused() throws {
    // Detects a configuration that gives an instrument to an age band below its validated range.
    let config = withProbeBlock(try Contract.demoConfiguration())
    #expect(try problems(config, instruments: try Contract.instrumentsWithProbe(textStatus: "verified", minimumAge: 14))
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
    (#""keyID": null"#, #""keyID": "study-key-1""#, .invalid("export.keyID")),
    (#""resumeWithinHours": 24"#, #""resumeWithinHours": 0"#, .invalid("selfAdministered.resumeWithinHours")),
    (#""resumeWithinHours": 24"#, #""resumeWithinHours": 24, "hours": 1"#, .unexpected("selfAdministered.hours")),
    (#""operatorPin": null"#, pinJSON(salt: "AAECAwQFBgcICQoLDA0ODw==", iterations: 599_999), .invalid("operatorPin.iterations")),
    (#""operatorPin": null"#, pinJSON(salt: "AAECAwQFBgcICQoLDA0O", iterations: 600_000), .invalid("operatorPin.salt")),
])
func configurationImportAppliesTheSchemaRules(original: String, replacement: String, expected: DecodeError) throws {
    // Detects an import that accepts a configuration which contract/config.schema.json refuses.
    let text = String(decoding: try Contract.data("configs/demo.json"), as: UTF8.self)
    let range = try #require(text.range(of: original))
    let changed = text.replacingCharacters(in: range, with: replacement)
    #expect(throws: expected) { try StudyConfiguration(data: Data(changed.utf8)) }
}

/// An `operatorPin` member whose key is PBKDF2-HMAC-SHA256 of "4821" with salt bytes 0 to 15 and 600,000
/// iterations, computed with Python `hashlib.pbkdf2_hmac`.
private func pinJSON(salt: String, iterations: Int) -> String {
    #""operatorPin": {"salt": "\#(salt)", "iterations": \#(iterations), "#
        + #""pbkdf2Sha256": "41e970f647a127ad1149029f290f489446ee62e4dde83ddf9e3d7e632ff56362"}"#
}

@Test func theOperatorPinIsASlowKeyThatMatchesOnlyItsPin() throws {
    // Detects a PIN stored with a fast hash that a search of every PIN reverses, or a check that accepts a wrong PIN.
    let text = String(decoding: try Contract.data("configs/demo.json"), as: UTF8.self)
        .replacingOccurrences(of: #""operatorPin": null"#,
                              with: pinJSON(salt: "AAECAwQFBgcICQoLDA0ODw==", iterations: 600_000))
    let stored = try #require(try StudyConfiguration(data: Data(text.utf8)).operatorPin)
    #expect(stored.matches("4821"))
    #expect(!stored.matches("4822"))
    #expect(!stored.matches(""))
    let salt = Data((0..<16).map { UInt8($0) })
    #expect(OperatorPin(pin: "4821", salt: salt) == stored)
    #expect(OperatorPin(pin: "4821", salt: salt.dropLast()) == nil)
    #expect(OperatorPin(pin: "4821", salt: salt, iterations: 599_999) == nil)
}

@Test func aStatedKeyIDMustBeTheSHA256OfTheExportKey() throws {
    // Detects a configuration whose keyID names another key, so an analyst would pick the wrong private key.
    var config = try Contract.demoConfiguration()
    config.export.recipientPublicKey = Data(base64Encoded: "B6N8vBQgk8i3VdwbEOhstCY3StFqqFPtC9/AsrhtHHw=")
    // The SHA-256 of that raw key, computed with Python `hashlib`.
    config.export.keyID = SHA256Digest(hex: "aaa8fff703b50b2297f4f6e13508f72420d96fd01ebb84cb074449caaef64041")
    #expect(try problems(config) == [])
    config.export.keyID = SHA256Digest(of: Data("another key".utf8))
    #expect(try problems(config) == [.keyIDMismatch])
    config.export.recipientPublicKey = nil
    #expect(try problems(config) == [.keyIDMismatch])
}

@Test func theModelTableScaleMatchesTheContractFormula() throws {
    // Detects a model-table scale that differs from 25.4 × nativeScale / ppi as the contract records it.
    let line = Contract.lines(try Contract.data("fixtures/journal/basic.jsonl"))[0]
    guard case .sessionOpened(let opened) = try LineCodec.decode(line).event else { throw DecodeError.notAnObject }
    #expect(MillimetreScale.lookup(model: "iPad16,3", nativeScale: 2) == opened.scale)
    #expect(DeviceTable.density(model: "iPad16,3")?.ppiConfirmed == true)
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

@Test func aConfiguredJurisdictionWithoutACrisisListIsRefused() throws {
    // Detects a configuration that runs questionnaires in a jurisdiction for which no crisis list exists.
    var config = try Contract.demoConfiguration()
    config.jurisdictions.append("FR")
    #expect(try problems(config) == [.noCrisisList("FR")])
}

@Test(arguments: [(25.0, 1.0, ConfigProblem.imminentReviewTooLong), (24.0, 2.0, .routineReviewTooLong)])
func aRemoteReviewSlowerThanTheProtocolIsRefused(imminentHours: Double, routineWeekdays: Double,
                                                  expected: ConfigProblem) throws {
    // Detects a self-administered safety item whose flags a reviewer may leave longer than 24 hours when imminent,
    // or longer than 1 weekday otherwise.
    var config = try Contract.remoteReviewConfiguration()
    config.safety.remoteReview?.imminentReviewWithinHours = imminentHours
    config.safety.remoteReview?.routineReviewWithinWeekdays = routineWeekdays
    #expect(try problems(config) == [expected])
}

@Test func theResumeWindowDefaultsTo24HoursAndMustNotOutlastTheReview() throws {
    // Detects a configuration without a resume window whose interrupted sessions never expire, or a window in which a
    // flag can wait on the device past the review interval.
    #expect(try Contract.demoConfiguration().selfAdministered.resumeWithinHours == 24)
    var root = try JSONSerialization.jsonObject(with: try Contract.data("configs/demo.json")) as! [String: Any]
    root["selfAdministered"] = nil
    let without = try StudyConfiguration(data: try JSONSerialization.data(withJSONObject: root))
    #expect(without.selfAdministered.resumeWithinHours == 24)

    var config = try Contract.remoteReviewConfiguration()
    config.safety.remoteReview?.imminentReviewWithinHours = 12
    config.safety.remoteReview?.exportEveryHours = 12
    #expect(try problems(config) == [.resumeWindowTooLong])
    config.selfAdministered.resumeWithinHours = 12
    #expect(try problems(config) == [])
}
