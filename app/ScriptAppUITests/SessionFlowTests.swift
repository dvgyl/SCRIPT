import XCTest

/// The app on the simulator with `-ScriptUITest`, so direct touch records as Pencil input. Each test leaves its export
/// in the app container; `./check` copies the exports out after the run.
final class SessionFlowTests: XCTestCase {
    private var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = false
    }

    @MainActor private func launch(_ arguments: [String] = []) {
        app = XCUIApplication()
        app.launchArguments = ["-ScriptUITest"] + arguments
        app.launch()
    }

    @MainActor func testSupervisedAdultSessionWithAFlagEndsWithTheAlertActionAndCrisisList() {
        // Detects a supervised session that cannot reach a verified export through the operator alert, one action
        // code, and the crisis list after PHQ-9 item 9 is endorsed.
        launch()
        snap("01-home")
        setUpSession(code: "UIT-SUP-01", selfAdministered: false)
        tap("start", snapAs: "03-handoff")
        runTasks(drawn: 3)
        answer(flagging: "Question 9 of 10", items: 17)
        XCTAssertTrue(app.staticTexts["message"].waitForExistence(timeout: 10))
        snap("10-hand-back")

        app.buttons["operatorControl"].press(forDuration: 2.5)
        XCTAssertTrue(app.descendants(matching: .any)["safetyAlert"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertFalse(find("showSupportList").isEnabled)
        find("actionPicker").tap()
        app.buttons["Clinician assessment"].firstMatch.tap()
        snap("11-review")
        tap("recordAction")
        XCTAssertTrue(app.descendants(matching: .any)["actionRecorded"].firstMatch.waitForExistence(timeout: 10))
        tap("showSupportList")
        XCTAssertTrue(app.staticTexts["crisisList"].waitForExistence(timeout: 10))
        snap("12-crisis-list")
        tap("continue")
        XCTAssertTrue(app.staticTexts["thankYou"].waitForExistence(timeout: 10))
        snap("13-thank-you")

        app.buttons["operatorControl"].press(forDuration: 2.5)
        // Home shows only the last 4 characters of a participant code without the PIN.
        let export = app.buttons["export-•••P-01"]
        XCTAssertTrue(export.waitForExistence(timeout: 10))
        snap("14-home-closed")
        export.tap()
        XCTAssertTrue(app.staticTexts["exportName"].waitForExistence(timeout: 30))
        XCTAssertTrue(app.staticTexts["exportName"].label.hasPrefix("SCRIPT_DEMO_DEMO_"))
        snap("15-export")
        tap("exportDone")
    }

    @MainActor func testSelfAdministeredSessionEndsWithTheClosingCrisisList() {
        // Detects a self-administered session that cannot run PHQ-8 and GAD-7 alone and end with the crisis list.
        launch()
        setUpSession(code: "UIT-SELF-01", selfAdministered: true)
        tap("start", snapAs: "20-self-start")
        runTasks(drawn: 1)
        answer(flagging: nil, items: 16)
        XCTAssertTrue(app.staticTexts["crisisList"].waitForExistence(timeout: 10))
        snap("21-self-crisis-list")
        tap("continue")
        XCTAssertTrue(app.staticTexts["thankYou"].waitForExistence(timeout: 10))
        snap("22-self-thank-you")
        tap("saveToFiles")
        XCTAssertTrue(app.staticTexts["exportName"].waitForExistence(timeout: 30))
        XCTAssertTrue(app.staticTexts["exportName"].label.hasPrefix("SCRIPT_DEMO_DEMO_"))
        snap("23-self-export")
        tap("exportDone")
    }

    @MainActor func testAnInterruptedSelfAdministeredSessionResumesOnlyForItsCode() {
        // Detects a resumed self-administered session that a person without its code can continue, or a code screen
        // that hides its refusal.
        launch()
        setUpSession(code: "UIT-RES-01", selfAdministered: true)
        tap("start")
        XCTAssertTrue(app.buttons["begin"].waitForExistence(timeout: 10))
        app.terminate()
        launch()
        let field = app.textFields["confirmCode"]
        replace(field, with: "UIT-OTHER")
        tap("continue")
        XCTAssertTrue(app.staticTexts["codeWrong"].waitForExistence(timeout: 10))
        snap("50-code-wrong")
        replace(field, with: "UIT-RES-01")
        tap("continue")
        XCTAssertTrue(app.buttons["start"].waitForExistence(timeout: 10))

        app.buttons["operatorControl"].press(forDuration: 2.5)
        tap("endSession")
        tap("closeSession")
        XCTAssertTrue(app.buttons["newSession"].waitForExistence(timeout: 10))
    }

    @MainActor func testQualificationFromAMeasuredScaleShowsTheSampleTiming() {
        // Detects a qualification session that cannot start from a reference measurement, or that does not show the
        // operator the median sample interval and the gap share.
        launch(["-ScriptMeasureScale"])
        tap("newQualification")
        replace(app.textFields["attestor"], with: "OP1")
        replace(app.textFields["asset"], with: "IPAD-01")
        tap("measure", snapAs: nil)
        XCTAssertTrue(app.buttons["useMeasurement"].waitForExistence(timeout: 10))
        snap("30-measure-rule")
        app.segmentedControls.buttons["ID card"].tap()
        snap("31-measure-card")
        tap("useMeasurement")
        tap("startSession")
        for step in 0..<3 {
            tap("begin")
            XCTAssertTrue(app.buttons["done"].waitForExistence(timeout: 5))
            let area = app.descendants(matching: .any)["drawingArea"]
            if step == 1 {
                area.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).press(forDuration: 3)
            } else {
                draw(area)
            }
            if step == 0 { snap("32-qualification-task") }
            tap("done")
        }
        XCTAssertTrue(app.descendants(matching: .any)["medianInterval"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["gapShare"].firstMatch.exists)
        snap("33-qualification-result")
        tap("timingExport")
        XCTAssertTrue(app.staticTexts["exportName"].waitForExistence(timeout: 30))
        tap("exportDone")
    }

    @MainActor func testBenchSessionRecordsTwoRunsAndExportsPlaintext() {
        // Detects a bench session that cannot record a main run and a fault run without a participant step, that
        // offers exclude on a fault run, or that cannot reach a plaintext export.
        launch()
        tap("newBench")
        app.segmentedControls["mounting"].buttons["M2"].tap()
        replace(app.textFields["motionVersion"], with: "2.4")
        replace(app.textFields["asset"], with: "IPAD-01")
        snap("60-bench-setup")
        tap("startSession")

        startRun("loops-l-v20", snapAs: "61-bench-run")
        XCTAssertEqual(app.staticTexts["runID"].label, "P-M2-loops-l-v20-r1")
        XCTAssertTrue(app.buttons["mark-exclude"].exists)
        tap("begin")
        draw(app.descendants(matching: .any)["drawingArea"])
        tap("mark-disturbance")
        XCTAssertFalse(app.buttons["mark-disturbance"].isEnabled)
        snap("62-bench-task")
        tap("done")

        startRun("fault-lift", snapAs: nil)
        XCTAssertFalse(app.buttons["mark-exclude"].exists)
        tap("begin")
        draw(app.descendants(matching: .any)["drawingArea"])
        tap("done")

        XCTAssertTrue(app.buttons["startRun"].waitForExistence(timeout: 10))
        snap("63-bench-runs")
        tap("endBench")
        tap("confirmEndBench")
        tap("timingExport")
        XCTAssertTrue(app.staticTexts["exportName"].waitForExistence(timeout: 30))
        XCTAssertTrue(app.staticTexts["exportName"].label.hasPrefix("SCRIPT_BENCH_PM2_"))
        snap("64-bench-export")
        tap("exportDone")
    }

    @MainActor func testWithdrawalNeedsTheCodeTwiceAndTheLogConfirmsIt() {
        // Detects a withdrawal that runs without the second entry and the confirmation, or that the deletion log on the
        // device cannot confirm afterwards.
        launch()
        tap("withdraw")
        replace(app.textFields["withdrawCode"], with: "UIT-WD-01")
        XCTAssertFalse(app.buttons["withdrawDelete"].isEnabled)
        replace(app.textFields["withdrawCodeAgain"], with: "UIT-WD-01")
        snap("40-withdraw")
        tap("withdrawDelete")
        tap("withdrawConfirm")
        XCTAssertTrue(app.descendants(matching: .any)["withdrawResult"].firstMatch.waitForExistence(timeout: 10))
        snap("41-withdrawn")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        tap("verifyWithdrawal")
        replace(app.textFields["verifyCode"], with: "UIT-WD-01")
        tap("verifyCheck")
        XCTAssertTrue(app.descendants(matching: .any)["verifyFound"].firstMatch.waitForExistence(timeout: 10))
        snap("42-verified")
    }

    // MARK: Steps

    @MainActor private func setUpSession(code: String, selfAdministered: Bool) {
        tap("newSession")
        let participant = app.textFields["participantCode"]
        XCTAssertTrue(participant.waitForExistence(timeout: 10))
        if selfAdministered {
            app.segmentedControls["administration"].buttons["Self-administered"].tap()
        }
        replace(participant, with: code)
        tap("document-demo-consent")
        XCTAssertTrue(app.buttons["document-demo-consent"].isSelected)
        replace(app.textFields["attestor"], with: selfAdministered ? "ENROL-01" : "OP1")
        replace(app.textFields["asset"], with: "IPAD-01")
        snap(selfAdministered ? "02-self-setup" : "02-setup")
        tap("startSession")
    }

    /// Picks `program` with run index 1 on the run screen and starts the run.
    @MainActor private func startRun(_ program: String, snapAs name: String?) {
        tap("program")
        app.buttons[program].firstMatch.tap()
        XCTAssertEqual(app.staticTexts["runID"].label, "P-M2-\(program)-r1")
        tap("startRun", snapAs: name)
        XCTAssertTrue(app.buttons["begin"].waitForExistence(timeout: 10))
    }

    /// Runs every task. The first `drawn` tasks get synthesised drags; the rest end with Begin and Done.
    @MainActor private func runTasks(drawn: Int) {
        var count = 0
        while app.buttons["begin"].waitForExistence(timeout: 5) {
            if count == 0 { snap("04-task-instruction") }
            app.buttons["begin"].tap()
            XCTAssertTrue(app.buttons["done"].waitForExistence(timeout: 5))
            if count < drawn {
                draw(app.descendants(matching: .any)["drawingArea"])
                if count == 1 { snap("05-task-drawn") }
            }
            app.buttons["done"].tap()
            count += 1
        }
        XCTAssertGreaterThanOrEqual(count, 7)
    }

    @MainActor private func draw(_ area: XCUIElement) {
        for (from, to) in [((0.25, 0.45), (0.75, 0.5)), ((0.3, 0.6), (0.6, 0.7))] {
            let start = area.coordinate(withNormalizedOffset: CGVector(dx: from.0, dy: from.1))
            let end = area.coordinate(withNormalizedOffset: CGVector(dx: to.0, dy: to.1))
            start.press(forDuration: 0.05, thenDragTo: end)
        }
    }

    /// Answers every item "Not at all", and "Several days" for the item whose step text is `flagging`. `items` is
    /// the number of items of the blocks: PHQ-9 with its difficulty question and GAD-7 give 17, and PHQ-8 with its
    /// difficulty question and GAD-7 give 16.
    @MainActor private func answer(flagging: String?, items: Int) {
        var answered = 0
        while app.buttons["next"].waitForExistence(timeout: 5) {
            let step = app.staticTexts["stepIndicator"].label
            if answered == 0 { snap("06-item") }
            if answered == 1, flagging != nil {
                app.buttons["operatorControl"].press(forDuration: 2.5)
                tap("returnToParticipant", snapAs: "07-paused")
                XCTAssertEqual(app.staticTexts["stepIndicator"].label, step)
            }
            app.buttons[step == flagging ? "option-1" : "option-0"].tap()
            app.buttons["next"].tap()
            answered += 1
            // The item screen stays until the answer is on storage. The next item has another step text.
            let deadline = Date().addingTimeInterval(30)
            while Date() < deadline, app.buttons["next"].exists, app.staticTexts["stepIndicator"].label == step {
                RunLoop.current.run(until: Date().addingTimeInterval(0.1))
            }
        }
        XCTAssertEqual(answered, items)
    }

    /// A button. A list creates its rows only near the screen, so at large text sizes the test scrolls to find it:
    /// down the list first, then back up.
    @MainActor private func find(_ id: String) -> XCUIElement {
        let element = app.buttons[id].firstMatch
        if element.waitForExistence(timeout: 10) { return element }
        for up in [true, false] {
            for _ in 0..<8 {
                if up { app.swipeUp() } else { app.swipeDown() }
                if element.waitForExistence(timeout: 1) { return element }
            }
        }
        return element
    }

    @MainActor private func tap(_ id: String, snapAs name: String? = nil) {
        let element = find(id)
        XCTAssertTrue(element.exists, "no \(id)")
        if let name { snap(name) }
        element.tap()
    }

    /// Types `text` over the field's value. A triple tap selects the whole value, so typing replaces it. The simulator
    /// can drop keys while its keyboard starts, so the keyboard must show first, and a short value is typed again.
    @MainActor private func replace(_ field: XCUIElement, with text: String) {
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        for _ in 0..<3 {
            field.tap()
            XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 10))
            let old = field.value as? String ?? ""
            if !old.isEmpty, old != field.placeholderValue {
                field.tap(withNumberOfTaps: 3, numberOfTouches: 1)
            }
            field.typeText(text)
            if field.value as? String == text { return }
        }
        XCTAssertEqual(field.value as? String, text)
    }

    /// Saves a screenshot when `SCRIPT_SCREENSHOTS` names a folder. `SCRIPT_SCREENSHOT_SUFFIX` tells the runs apart.
    @MainActor private func snap(_ name: String) {
        let environment = ProcessInfo.processInfo.environment
        guard let folder = environment["SCRIPT_SCREENSHOTS"], !folder.isEmpty else { return }
        let suffix = environment["SCRIPT_SCREENSHOT_SUFFIX"].map { "-\($0)" } ?? ""
        let url = URL(fileURLWithPath: folder).appendingPathComponent("\(name)\(suffix).png")
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: url)
    }
}
