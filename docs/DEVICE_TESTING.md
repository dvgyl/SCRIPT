# Device test checklist

This checklist tells the owner how to test SCRIPT Capture on an iPad Pro with Apple Pencil Pro. It also tests SCRIPT Bench on a Mac with the Wacom tablet. `./check` cannot do this test. The simulator cannot make Pencil touches, and no test can make the events of the Wacom driver.

The test has two parts. [Part A](#part-a-the-ipad) takes about 40 minutes. [Part B](#part-b-the-wacom-tablet) takes about 30 minutes. Each part ends with one command, `scriptkin device-report`, which reads the exports and gives the result of each check. You send back the report and a short [result form](#result-form).

Do the steps in order. Each step that has a check names its check, for example **P2**. The checks P, W, and D come from the report. The checks M are things that only you can see, and you record them in the result form.

## The test records no participant

- You are the tester. You draw test strokes, and you tap test answers. No other person draws or answers.
- Use the test codes of this checklist. Use the demo configuration that the app ships. Every operator screen then shows "Demo, not for research".
- A tap on a questionnaire option in this test is test input. It is not your answer, and nobody scores it.
- The report holds counts, rates, and ranges only. It holds no pen sample, no response, and no participant code.
- The demo configuration has no operator PIN, so Home is not locked in this test. The Home lock and the delay after wrong PINs have automated tests only. Test them on the iPad with the study configuration before a study.
- Do not put a journal of this test into the repository. Delete the test folder and the exports on the devices after the maintainers confirm that they have the report ([last step](#after-the-test)).

## What you need

| Item | For |
| --- | --- |
| iPad Pro 11-inch or 13-inch (M4 or M5), iPadOS 18 or later, Apple Pencil Pro | Part A |
| Mac with Xcode 27.2 or later, the SCRIPT repository, and [uv](https://docs.astral.sh/uv/) | Parts A and B |
| Apple ID. Xcode makes a free personal team for it. | Part A |
| USB-C cable between the Mac and the iPad | Part A |
| Steel rule of 100 mm or longer | Parts A and B |
| Wacom Intuos Pro Medium (2025), Wacom Pro Pen 3, its USB cable, and the Wacom driver | Part B |

In this checklist, `scriptkin` stands for `uv run --project <repository>/python --frozen scriptkin`, as in the [study guide](STUDY_GUIDE.md#run-the-scriptkin-commands).

Make one test folder on the Mac, outside the repository, with a folder `incoming` in it. The commands of steps A7 and B5 run in the test folder.

## Part A: the iPad

### A1. Build and install the app

1. Connect the iPad to the Mac with the cable. On the iPad, tap **Trust**.
2. In Xcode, open **Settings**, then **Accounts**, and add your Apple ID.
3. Open `app/ScriptApp.xcodeproj` in Xcode. Select the **ScriptApp** target, and open **Signing & Capabilities**.
4. Select your personal team. Keep **Automatically manage signing** on.
5. If Xcode reports that the bundle identifier is not available, change it to an identifier of your own.
6. On the iPad, turn on **Developer Mode** in **Settings** > **Privacy & Security**. The iPad restarts.
7. In Terminal, go to the repository folder, and run the commands below. Replace `<iPad name>` with the name of the iPad.
8. On the iPad, open **Settings** > **General** > **VPN & Device Management**, and trust your developer app.

```sh
revision=$(git rev-parse --short HEAD)
if [ -n "$(git status --porcelain)" ]; then revision="$revision-dirty"; fi
xcodebuild build -project app/ScriptApp.xcodeproj -scheme ScriptApp -configuration Debug \
  -destination "platform=iOS,name=<iPad name>" -allowProvisioningUpdates \
  -derivedDataPath "$HOME/Library/Caches/SCRIPT/device-test" SCRIPT_SOURCE_REVISION="$revision"
xcrun devicectl list devices
xcrun devicectl device install app --device <device identifier from the list> \
  "$HOME/Library/Caches/SCRIPT/device-test/Build/Products/Debug-iphoneos/ScriptApp.app"
```

Expected result: SCRIPT Capture opens on the iPad, and Home shows the demo configuration.

Your team selection changes the project file, so the revision ends with `-dirty`. This is correct for the test. Do not commit your team or a changed bundle identifier. An app from a personal team stops after 7 days. Install it again to continue.

### A2. Save the device diagnostics

1. On Home, scroll to **Device**, and tap **Save the device diagnostics**.
2. Tap **OK**.

Expected result: the alert names a file `SCRIPT_DIAGNOSTICS_<time>.json`. The file holds the device model, the build, and the storage settings. It holds no session. It gives the checks **D1** to **D4**.

### A3. Run a qualification session

1. On Home, tap **Device qualification**.
2. Enter the operator code `OWNER` and the device asset code `IPAD-TEST-01`.
3. Select the screen protector, the Pencil tip state, and **Apple Pencil Pro**.
4. Read the **Scale** section. Expected result: it shows the iPad model from the device table, with 264 ppi.
5. Tap **Start the qualification**.
6. In step 1, tap **Begin**. Measure the bar on the screen with the steel rule. Write the length in the result form (**M1**). Tap **Done**.
7. In step 2, tap **Begin**. Hold the Pencil tip still on the dot for 10 seconds. Tap **Done**.
8. In step 3, tap **Begin**. Draw along the line at an even speed. Tap **Done**.
9. Tap **Export**, and then **Done**. Tap **Go to Home**.

Expected result: the bar is 100 mm long. Home lists one exported session.

### A4. Run a supervised dry run

1. On Home, tap **New session**.
2. Enter the participant code `TEST-01`, and select the age band 18-24.
3. Tick the consent document. Enter the operator code `OWNER` and the device asset code `IPAD-TEST-01`.
4. Select **Apple Pencil Pro**, and tap **Start the session**.
5. On the screen "Please give the iPad to the participant.", tap **Start**.
6. Practice task: tap **Begin**, draw the line and the circle, and tap **Done**.
7. Task 1: tap **Begin**, and draw the spiral. Between two strokes, hold the Pencil tip just above the screen for 3 seconds. Tap **Done**. Expected result: the hover makes no ink (**M2**).
8. Task 2: tap **Begin**. Rest your palm on the screen while you draw. Touch the screen one time with a finger. Tap **Done**. Expected result: the palm and the finger make no ink (**M3**).
9. Tasks 3 to 6: in each task, tap **Begin**, draw one short stroke, and tap **Done**.
10. First questionnaire: select **Not at all** and tap **Next** for questions 1 to 8. For question 9, select **Prefer not to answer**, and tap **Next**. Answer question 10.
11. Second questionnaire: select **Not at all** and tap **Next** for each question.
12. On the screen "Please give the iPad back to the researcher.", press and hold **Operator** for 2 seconds.
13. In Review, read **Safety follow-up**. Expected result: one neutral alert shows. It shows no answer and no score (**M4**).
14. Select an action, and tap **Record action**.
15. Tap **Show the support list to the participant**, and then tap **Continue**.
16. On the thank-you screen, press and hold **Operator** for 2 seconds. Home opens.
17. Tap **Export** on the closed session, and then **Done**.

Question 9 of the first questionnaire is the safety item. The skip in step 10 raises a flag, so that steps 13 to 15 test the operator alert on the device. The report then checks the flow (**P11**).

### A5. Run a bench session

This check needs no motion system. Draw each run by hand with the Pencil.

1. On Home, tap **Bench session**.
2. Select the mounting **M1**. Enter the motion system `nextdraw-8511` and the version `test`.
3. Enter the device asset code `IPAD-TEST-01`. Select the screen protector, the Pencil tip, and **Apple Pencil Pro**.
4. Tap **Start the bench session**.
5. Select the program `loops-l-v20` and the run 1. Expected result: **Run ID** shows `P-M1-loops-l-v20-r1`.
6. Tap **Start the run**. Tap **Begin**, trace the loops, and tap **Done**.
7. Select the program `fault-lift`, and tap **Start the run**. Expected result: the task screen shows no **Exclude** mark (**M5**).
8. Tap **Begin**, trace the loops, and tap **Done**.
9. Tap **End the session**, and confirm with **End the session**.
10. On the sample timing screen, tap **Export**. Expected result: the export screen shows "Not encrypted (bench session)". Tap **Done**.
11. Tap **Go to Home**.

### A6. Check the screen in the app switcher

1. Tap **New session**. Enter `TEST-02` with the values of step A4, and tap **Start the session**.
2. Swipe up from the bottom edge and hold, so that the app switcher opens.
3. Read the card of SCRIPT Capture. Expected result: the card shows only the name "SCRIPT Capture", not the session screen (**M6**).
4. Tap the card. Press and hold **Operator** for 2 seconds. Tap **End the session**, and then **Close the session**. Home opens.

Optional, when you use VoiceOver: on a participant screen, select **Operator**, swipe up or down to the action **Open the operator screens**, and double-tap. Expected result: the operator screens open (**M7**).

### A7. Make the report on the Mac

1. Connect the iPad. In Finder, select the iPad, and open **Files**.
2. Open **SCRIPT Capture**. Drag the folders `Exports` and `Diagnostics` into `incoming` in the test folder.
3. In the test folder, run the command below.

```sh
scriptkin device-report incoming/Exports --diagnostics incoming/Diagnostics/*.json --markdown ipad-report.md
```

Expected result: the command prints a table. No check has the result `FAIL`, and no check of the groups iPad and diagnostics has `NOT RUN`. The tablet checks have `NOT RUN` until Part B.

| Check | What the report confirms |
| --- | --- |
| P1 Source revision | Every journal records the revision that you built. |
| P2 Pencil channels | Every sample has force, altitude, azimuth, and roll, and the input is `pencil`. |
| P3 Hover | Hover lines exist, and `z` stays from 0 to 1. |
| P4 Hover timing | The median hover interval, and the hover lines that are earlier than the line before them. This answers an open question of [the design](DESIGN.md#12-open-questions). |
| P5 Estimated properties | The samples with an estimated value, the corrections, the unmatched corrections, and the share of unresolved samples. |
| P6 Sample rate | The median sample interval with its 5th and 95th percentiles. Quality control refuses no drawn trial. |
| P7 Finger and palm | The finger touch and the palm made `finger` lines. |
| P8 Scale | The journal records the model-table scale. |
| P9 Qualification | The jitter of the still hold and the deviation of the straight line, in millimetres. |
| P10 Bench mode | The bench export is plaintext, its header has no participant, each run records its run id, and the fault run has no exclude mark. |
| P11 Safety flow | The dry run recorded a flag, an operator alert, an action, and the support list, and it closed. |
| D1 File protection | Each research folder and log on the iPad has a file protection class. |
| D2 Backup exclusion | Each research folder and log is outside device backups. |
| D3 Device secret | The Keychain item of the deletion log stays on this device. |
| D4 Device and display | The model, the system version, the display, and the build. |

## Part B: the Wacom tablet

SCRIPT Bench is the Mac app of the bench study. The bench protocol requires these checks before its pilot (`docs/BENCH_PROTOCOL.md` section 2.2).

### B1. Prepare the tablet

1. Connect the tablet to the Mac with the USB cable. Do not use Bluetooth.
2. In the Wacom driver, turn off every feature that changes the pen reports. Write the driver settings and the driver version in the result form.
3. Map the tablet to the display that shows SCRIPT Bench.

### B2. Build and start SCRIPT Bench

1. In Terminal, go to the repository folder, and run the commands below.
2. On Home, read **Clock check**. Expected result: it shows "Bring the pen to the tablet".
3. Bring the pen near the tablet. Expected result: **Clock check** shows **Passed** (**M8**).

```sh
revision=$(git rev-parse --short HEAD)
if [ -n "$(git status --porcelain)" ]; then revision="$revision-dirty"; fi
xcodebuild build -project app/ScriptApp.xcodeproj -scheme ScriptBench -configuration Release \
  -destination "platform=macOS,arch=$(uname -m)" \
  -derivedDataPath "$HOME/Library/Caches/SCRIPT/bench-test" SCRIPT_SOURCE_REVISION="$revision"
open "$HOME/Library/Caches/SCRIPT/bench-test/Build/Products/Release/ScriptBench.app"
```

If the clock check fails, Home shows the difference between the two clocks, and no session can start in this launch. Write the difference in the result form, and stop Part B.

During a session, the pen cannot press a control of SCRIPT Bench. Use the mouse or the trackpad for every control, and use the pen on the tablet only.

### B3. Run a tablet qualification session

1. On Home, click **Qualification session**.
2. Enter the operator code `OWNER`, the tablet asset code `WACOM-01`, and the driver version.
3. Enter the Mac asset code `MAC-01`.
4. Select **Wacom Pro Pen 3**, the surface **None**, and the nib state.
5. Click **Start the qualification**. Expected result: the window goes into full screen (**M9**).
6. Select the task `qual-reference`, and click **Begin**.
7. Lay the steel rule on the tablet with its 0 mm mark at the left. Draw one stroke along the rule from the 0 mm mark to the 100 mm mark.
8. Lay the rule with its 0 mm mark at the top. Draw one stroke along the rule from the 0 mm mark to the 100 mm mark.
9. Click **Done**. Make no other pen contact between **Begin** and **Done**.
10. Select `qual-hold`, and click **Begin**. Hold the pen tip still on the tablet for 10 seconds. Click **Done**.
11. Select `qual-line`, and click **Begin**. Draw a straight stroke from left to right at an even speed. Click **Done**.
12. Click **Close the session**.

The first two strokes of `qual-reference` give the direction and the scale of each axis (**W3**). The Mac screen shows the tablet area at a reduced size, so a rule on the screen cannot check the scale.

### B4. Run a bench session on the Mac

1. On Home, click **Bench session**.
2. Select the mounting **M1**. Enter the motion system `nextdraw-8511` and the version `test`.
3. Enter the tablet asset code, the driver version, and the Mac asset code of step B3.
4. Select **Wacom Pro Pen 3**. Expected result: **Last qualification** names the qualification session.
5. Click **Start the bench session**.
6. Leave full screen with the green window button. Expected result: **Begin** is disabled, and the screen shows "Begin needs full screen." (**M10**). Go back to full screen.
7. Move the pen to each corner of the tablet. Expected result: the pen pointer stays inside the window (**M11**).
8. Hold the pen just above the tablet, and click **Set origin**. Expected result: **Origin** shows a position in lines.
9. Select the program `loops-l-v20` and the run 1. Expected result: **Run id** shows `W-M1-loops-l-v20-r1`.
10. Click **Begin**. Hold the pen just above the tablet for 2 seconds. Then draw the loops. Expected result: the ink on the screen follows the pen (**M12**).
11. In one loop, press the pen as hard as you can. Lift the pen, and click **Done**.
12. Select the program `fault-lift`, and click **Begin**. Expected result: the **Mark** menu has no **Exclude** (**M13**).
13. Draw a stroke, and click **Done**.
14. Click **Close the session**. Expected result: the window leaves full screen.

### B5. Make the report on the Mac

1. On Home, click **Show the exports in Finder**.
2. Copy the `Exports` folder into `incoming` in the test folder, with the name `BenchExports`.
3. In the test folder, run the command below.

```sh
scriptkin device-report incoming/BenchExports --markdown tablet-report.md
```

Expected result: no tablet check has the result `FAIL` or `NOT RUN`.

| Check | What the report confirms |
| --- | --- |
| W1 Source revision and driver | The journal records the revision that you built and the driver version that you typed. |
| W2 Tablet position | The driver fills `absoluteX` and `absoluteY`. The values change along the strokes and stay inside the active area. |
| W3 Direction and scale | The left-to-right stroke has a positive dx. The top-to-bottom stroke has a positive dy. Each stroke measures 100 mm within 2 mm. |
| W4 Pressure maximum | The largest force when you press as hard as you can. |
| W5 Report rate | The median sample interval with its 5th and 95th percentiles, with mouse coalescing off. |
| W6 Tablet-point events | The stationary samples, which come from `tabletPoint` events during a contact. |
| W7 Hover | Hover lines have `clock: event` and no `z`. |
| W8 Bench mode | The bench export is plaintext, each run records its run id, and the fault run has no exclude mark. |

## Result form

Copy this form, fill it, and send it with the two reports.

```text
Date:
iPad model and iPadOS version:
Mac model and macOS version:
Wacom driver version and settings:
Commit that you built (git rev-parse --short HEAD):

M1  Bar length in qualification step 1 (mm):
M2  Hover made no ink (yes/no):
M3  Palm and finger made no ink (yes/no):
M4  Review showed one neutral alert, with no answer and no score (yes/no):
M5  iPad fault run showed no Exclude mark (yes/no):
M6  App switcher card showed only "SCRIPT Capture" (yes/no):
M7  VoiceOver action opened the operator screens (yes/no/not tested):
M8  Clock check showed Passed (yes/no; if no, the difference):
M9  Window went into full screen at the session start (yes/no):
M10 Begin was disabled outside full screen (yes/no):
M11 Pen pointer stayed inside the window at each corner (yes/no):
M12 Ink followed the pen (yes/no):
M13 Mac fault run had no Exclude in the Mark menu (yes/no):
Anything that did not match an expected result:
```

Send back:

1. `ipad-report.md` and `tablet-report.md`.
2. The diagnostics file from `incoming/Diagnostics`.
3. The filled result form.

Do not send a journal, unless a check failed and the maintainers ask for the journal of that check.

## What the maintainers do with the results

- They put each result into [the open questions of the design](DESIGN.md#12-open-questions), with the device, the system version, the driver version, and the date.
- **W3, direction.** If the top-to-bottom stroke has a negative dy, the report names the change to `yDown` of the tablet entry in `TabletTable` (`app/Sources/ScriptCore/Scale.swift`).
- **W3, scale.** If a stroke along the rule does not measure 100 mm, the resolution of the tablet entry is wrong. Do not run the bench study until the entry has the correct resolution and its source.
- **W2.** If `absoluteX` and `absoluteY` stay at 0, the journal has no tablet position. SCRIPT Bench then needs another source of position before the bench study can use it.
- **W4.** If the largest force stays below 1.0, the journal keeps `maxForce` 1. The recorded value lets an analysis correct the force.
- **P2 to P5 with an Apple Pencil (USB-C).** When you also have this Pencil, run step A3 again with it. Its channel plan has no force channel until a device test shows what the Pencil reports.
- After a change to a table entry, they run `./check` and commit the change with the test record.

## After the test

1. Wait until the maintainers confirm that they have the reports.
2. On the iPad, tap **Withdraw a participant** on Home, enter `TEST-01` in both fields, and delete. Do the same for `TEST-02`.
3. `QUAL` is the code of every qualification session. Withdraw it only when this iPad holds no qualification session of a study.
4. In Files on the iPad, open **SCRIPT Capture**. Delete the bench export in **Exports**, and delete **Diagnostics**.
5. On the Mac, delete the test folder, and delete `~/Library/Application Support/SCRIPT Bench/`.
