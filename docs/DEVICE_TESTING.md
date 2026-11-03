# How to test SCRIPT on a real iPad and a real tablet

This guide tells the owner how to run SCRIPT Capture on an iPad Pro (M4 or M5) with Apple Pencil Pro. It also tells how to check what the app records. `./check` cannot do this test. The simulator cannot make Pencil touches, so the app tests record direct touch as `directTouchAsPen`, with no Pencil channel. The [last part](#test-script-bench-on-a-mac-with-the-wacom-tablet) tests SCRIPT Bench on a Mac with a Wacom tablet.

The iPad test has seven parts. You install the app with a personal team. You run a qualification session, a supervised dry run, and a bench session. Then you take the three exports into a study store and read the journals. Last, you compare the results with the [checks that only a device can show](#what-the-device-test-must-confirm).

## What you need

- An iPad Pro 11-inch or 13-inch (M4 or M5) with iPadOS 18 or later, and an Apple Pencil Pro.
- A Mac with Xcode 27.2 or later, the SCRIPT repository, and [uv](https://docs.astral.sh/uv/).
- An Apple ID. Xcode makes a free personal team for it.
- A USB-C cable between the Mac and the iPad.
- A steel rule of 100 mm or longer.
- A test participant code that names no person, for example `TEST-01`.

The test uses the demo configuration that the app ships. Every operator screen then shows "Demo, not for research". The demo configuration has no PIN, and its exports are not encrypted. Use it only with a test participant code.

In this guide, `scriptkin` stands for `uv run --project <repository>/python --frozen scriptkin`, as in the [study guide](STUDY_GUIDE.md#run-the-scriptkin-commands).

## Install the app

1. Connect the iPad to the Mac with the cable. On the iPad, tap **Trust**.
2. In Xcode, open **Settings**, then **Accounts**, and add your Apple ID.
3. Open `app/ScriptApp.xcodeproj` in Xcode.
4. Select the **ScriptApp** target, and open **Signing & Capabilities**.
5. Select your personal team. Keep **Automatically manage signing** on.
6. If Xcode reports that the bundle identifier is not available, change it to an identifier of your own.
7. On the iPad, turn on **Developer Mode** in **Settings** > **Privacy & Security**. The iPad restarts.
8. In Terminal, go to the repository folder, and run the commands below. Replace `<iPad name>` with the name of the iPad.
9. On the iPad, open **Settings** > **General** > **VPN & Device Management**, and trust your developer app.

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

The commands write the build outside the repository. `SCRIPT_SOURCE_REVISION` puts the commit into each journal as `app.sourceRevision`. Your team selection changes the project file, so the revision ends with `-dirty`. A build from **Product** > **Run** in Xcode records `unknown` in place of the revision.

Do not commit your team or a changed bundle identifier. An app from a personal team stops working when its provisioning profile expires after 7 days. Install it again to continue.

## Run a qualification session

1. Open SCRIPT Capture. Check that Home shows the demo configuration.
2. Tap **Device qualification**.
3. Enter an operator code and a device asset code, for example `OWNER` and `IPAD-TEST-01`.
4. Select the screen protector, the Pencil tip state, and **Apple Pencil Pro**.
5. Check that the **Scale** section shows the iPad model from the device table, with 264 ppi.
6. Tap **Start the qualification**.
7. In step 1, tap **Begin**. Measure the bar with the steel rule, and record the length. Tap **Done**.
8. In step 2, tap **Begin**. Hold the Pencil tip still on the dot for 10 seconds. Tap **Done**.
9. In step 3, tap **Begin**. Draw along the line at an even speed. Tap **Done**.
10. Record the median sample interval, the sample rate, and the share of intervals longer than 30 ms.
11. Tap **Export**. Record the export name, and tap **Done**.
12. Tap **Go to Home**.

The bar is 100 mm long when the scale is correct.

## Run a supervised dry run

1. On Home, tap **New session**.
2. Enter the test participant code, and select the age band 18-24.
3. Tick the consent document, and enter the operator code and the device asset code.
4. Select **Apple Pencil Pro**, and tap **Start the session**.
5. On the screen "Please give the iPad to the participant.", tap **Start**.
6. Do the practice task and the six tasks with the Pencil. Tap **Begin** before and **Done** after each task.
7. In one task, hold the Pencil tip just above the screen between strokes for a few seconds.
8. In one task, rest your palm on the screen while you draw, and touch the screen once with a finger.
9. Answer the PHQ-9 and the GAD-7. For item 9 of the PHQ-9, select **Prefer not to answer**.
10. On the screen "Please give the iPad back to the researcher.", press and hold **Operator** for 2 seconds.
11. In Review, check that the safety alert shows. Select an action, and tap **Record action**.
12. Check that each trial row shows a sample rate and the channels force, tilt, roll, and hover.
13. Tap **Show the support list to the participant**, and then tap **Continue**.
14. On the thank-you screen, press and hold **Operator** for 2 seconds. Home opens.
15. Tap **Export** on the closed session. Record the export name, and tap **Done**.

Item 9 of the PHQ-9 is the safety item, so step 9 raises a flag. The flag lets you check the operator alert on the device.

## Run a bench session

A bench session records the motion system of the [bench protocol](BENCH_PROTOCOL.md). This check needs no motion system. Draw each run by hand with the Pencil.

1. On Home, tap **Bench session**.
2. Select the mounting **M1**. Enter the motion system `nextdraw-8511` and the version `test`.
3. Enter the device asset code. Select the screen protector, the Pencil tip, and **Apple Pencil Pro**.
4. Tap **Start the bench session**.
5. Select the program `loops-l-v20` and the run 1. Check that **Run ID** shows `P-M1-loops-l-v20-r1`.
6. Tap **Start the run**. Tap **Begin**, trace the loops, and tap **Done**.
7. Select the program `fault-lift`, and tap **Start the run**. Check that the task screen shows no **Exclude** mark.
8. Tap **Begin**, trace the loops, and tap **Done**.
9. Tap **End the session**, and confirm with **End the session**.
10. On the sample timing screen, tap **Export**.
11. Check that the export screen shows "Not encrypted (bench session)". Record the export name, and tap **Done**.
12. Tap **Go to Home**.

The export name starts with `SCRIPT_BENCH_PM1_`. A bench session has no participant step, so no screen asks you to give the iPad to another person.

## Take the exports into a study store

1. Make a new folder for the test on the Mac, with an `incoming` folder in it.
2. Connect the iPad. In Finder, select the iPad, and open **Files**.
3. Open **SCRIPT Capture**, and drag the `Exports` folder into `incoming`.
4. In the test folder, run `scriptkin intake incoming --store store --out intake-report.json`.
5. Check that the command exits with 0, and that the three receipts have the result `accepted`.
6. Run `scriptkin qc-report store --out qc-report.json --markdown qc-report.md`.
7. Read `qc-report.md`. Check that it shows 0 refused trials and no planned channel that the data do not show.

## Read the journals

Run each code below from the test folder with `uv run --project <repository>/python --frozen python <file>`. Find the session identifiers in `intake-report.json`.

Save this code as `qualification_report.py`, and give it the session identifier of the qualification session:

```python
import scriptkin as sk
from scriptkin import qualification

recording = sk.read_journal("store/sessions/<session identifier>")
report = qualification.qualify(recording)
print(report.intervals, report.hold_jitter_mm, report.line_deviation_mm, report.scale, report.observed_channels)
```

Save this code as `device_facts.py`. Run it on the journal of each session, for example `device_facts.py store/sessions/<session identifier>/journal.jsonl`:

```python
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

lines = [json.loads(text) for text in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()]
head = lines[0]
kinds = Counter(line["kind"] for line in lines)
samples = [line for line in lines if line["kind"] == "sample"]
print("source revision:", head["app"]["sourceRevision"])
print("device:", head["device"]["model"], head["device"]["os"], "Pencil:", head["pencil"]["declared"])
print("planned channels:", head["channels"])
for channel in ("force", "altitude", "azimuth", "roll"):
    print(f"{channel}: in {sum(channel in s for s in samples)} of {len(samples)} samples")
print("samples with an estimated value:", sum(s["est"] != 0 for s in samples))
print("corrections:", kinds["correction"], "unmatched:", sum("contact" not in l for l in lines if l["kind"] == "correction"))
contacts: dict[int, list[float]] = {}
for s in samples:
    contacts.setdefault(s["contact"], []).append(s["t"])
intervals = [b - a for times in contacts.values() for a, b in zip(times, times[1:])]
if intervals:
    print(f"median sample interval: {1000 * statistics.median(intervals):.2f} ms")
hovers = [line["t"] for line in lines if line["kind"] == "hover"]
print("hover lines:", len(hovers))
if len(hovers) > 1:
    print(f"median hover interval: {1000 * statistics.median(b - a for a, b in zip(hovers, hovers[1:])):.2f} ms")
timed = [line for line in lines if line["kind"] in ("sample", "hover")]
print("hover lines earlier than the line before them:",
      sum(b["kind"] == "hover" and b["t"] < a["t"] for a, b in zip(timed, timed[1:])))
print("finger lines:", kinds["finger"])
```

## What the device test must confirm

The simulator cannot show any of these results. Record each value, and mark each check as passed or failed.

| Check | What to confirm | Where to read it |
| --- | --- | --- |
| Pencil channels | Every sample has force, altitude, azimuth, and roll. The journal records `input` as `pencil`. | `device_facts.py`, the qualification report, and the channel table of the QC report |
| Hover | Hover lines appear when the tip is just above the screen, and `z` stays between 0 and 1. | `device_facts.py` and the `hover` lines of the journal |
| Estimated-property corrections | Some samples have estimated values, and `correction` lines follow them. Record the unmatched corrections and the share of unresolved estimates. | `device_facts.py` and the QC report |
| Hover timing | Hover lines carry the callback time. Record the median hover interval, and the hover lines that are earlier than the line before them. | `device_facts.py` |
| Sample rate | Record the median sample interval and its 5th and 95th percentiles. Quality control refuses no measured trial. | The qualification screen, the qualification report, and the QC report |
| Finger rejection | The finger touch and the palm made `finger` lines and no ink on the screen. | `device_facts.py` and your notes from the dry run |
| Scale | The bar measured 100 mm, and the journal records the model-table scale. | Your measurement and the qualification report |
| Source revision | `app.sourceRevision` is the revision that you built. | `device_facts.py` |
| Bench mode | The bench export is not encrypted, and its header has no participant code. Each run records its run id as `trialStarted.bench.clip`. The fault run offered no exclude mark. | The export screen, the `sessionOpened` and `trialStarted` lines of the bench journal, and your notes |

The hover timing answers an open question of [the design](DESIGN.md#12-open-questions). Put your result into that section of the design, with the device and the date.

When you also have an Apple Pencil (USB-C), run one more qualification session with it. Its channel plan has no force channel until a device test shows what the Pencil reports. Record whether the samples have a force value.

## Add the journals as fixtures

The journals of a device test are the first real-device evidence for the repository. Add them only when they come from a test participant code and the demo configuration.

`scriptkin contract verify` refuses every file in `contract/fixtures/` that the generator does not make. So a device journal cannot go into that folder, and adding it needs a change to the code and the tests.

1. Keep the qualification session and one short dry run. A 10-minute session makes a journal of 20 to 30 MB.
2. Check that no line names a person. The participant code is the test code.
3. Copy each export folder from `store/sessions/` to a new folder outside `contract/fixtures/`. Name the folder by the iPad, the Pencil, and the date.
4. Add a Python test that reads each export with `read_journal` and checks each line against `contract/journal.schema.json`.
5. Make the test check each confirmed result, such as the channels, the hover lines, and the corrections.
6. Make the Swift fold and the Python fold agree on each device journal, as `AGENTS.md` asks for every fixture.
7. Run `./check`, and commit the folder with its tests as one unit.

`test_app_exports_read_and_match_the_schema` in `python/tests/test_app_export.py` reads the exports of the app tests in the same way. Use it as the model for the new test.

## Test SCRIPT Bench on a Mac with the Wacom tablet

SCRIPT Bench is the Mac app of the bench study. Its tests drive the app with tablet events that they make themselves. So only a real tablet can show what the Wacom driver reports. The bench protocol requires these checks before its pilot (`docs/BENCH_PROTOCOL.md` section 2.2).

### What you need for the tablet test

- A Mac with macOS 15 or later, Xcode 27.2 or later, the SCRIPT repository, and [uv](https://docs.astral.sh/uv/).
- A Wacom Intuos Pro Medium (2025) with the Wacom Pro Pen 3, and the Wacom driver installed.
- The USB cable of the tablet. Do not use Bluetooth.
- A steel rule of 100 mm or longer.

Turn off every driver feature that changes the pen reports, and record the driver settings. Read the driver version in the window of the Wacom driver.

### Build and start SCRIPT Bench

1. Connect the tablet to the Mac with the USB cable.
2. In Terminal, go to the repository folder, and run the commands below.
3. On Home, read **Clock check**. It shows "Bring the pen to the tablet".
4. Bring the pen near the tablet. Check that **Clock check** shows **Passed**.

```sh
revision=$(git rev-parse --short HEAD)
if [ -n "$(git status --porcelain)" ]; then revision="$revision-dirty"; fi
xcodebuild build -project app/ScriptApp.xcodeproj -scheme ScriptBench -configuration Release \
  -destination "platform=macOS,arch=$(uname -m)" \
  -derivedDataPath "$HOME/Library/Caches/SCRIPT/bench-test" SCRIPT_SOURCE_REVISION="$revision"
open "$HOME/Library/Caches/SCRIPT/bench-test/Build/Products/Release/ScriptBench.app"
```

The commands write the build outside the repository. The Release build has no launch argument that skips the clock check. SCRIPT Bench keeps its data in `~/Library/Application Support/SCRIPT Bench/`.

If the clock check fails, Home shows the difference between the two clocks, and no session can start in this launch. Record the difference.

During a session, the pen cannot press a control of SCRIPT Bench. Use the mouse or the trackpad for every control, and use the pen on the tablet only.

### Run a tablet qualification session

1. On Home, click **Qualification session**.
2. Enter an operator code, a tablet asset code, the driver version, and a Mac asset code. Use test codes, for example `OWNER` for the operator, `WACOM-01` for the tablet, and `MAC-01` for the Mac.
3. Select **Wacom Pro Pen 3**, the surface **None**, and the nib state.
4. Click **Start the qualification**.
5. Select the task `qual-reference`, and click **Begin**.
6. Lay the steel rule on the tablet with its 0 mm mark at the left. Draw one stroke along the rule from the 0 mm mark to the 100 mm mark.
7. Lay the rule with its 0 mm mark at the top. Draw one stroke along the rule from the 0 mm mark to the 100 mm mark.
8. Click **Done**.
9. Select `qual-hold`, and click **Begin**. Hold the pen tip still on the tablet for 10 seconds. Click **Done**.
10. Select `qual-line`, and click **Begin**. Draw a straight stroke from left to right at an even speed. Click **Done**.
11. Click **Close the session**. SCRIPT Bench closes the session and exports it.

The Mac screen shows the tablet area at a reduced size, so a steel rule on the screen cannot check the scale. The two strokes along the rule check the scale and the direction of each axis.

### Run a bench session on the Mac

1. On Home, click **Bench session**.
2. Select the mounting **M1**. Enter the motion system `nextdraw-8511` and the version `test`.
3. Enter the tablet asset code, the driver version, and the Mac asset code of the qualification session.
4. Select **Wacom Pro Pen 3**. Check that **Last qualification** names the qualification session.
5. Click **Start the bench session**.
6. Hold the pen just above the tablet, and click **Set origin**. Check that **Origin** shows a position in lines.
7. Select the program `loops-l-v20` and the run 1. Check that **Run id** shows `W-M1-loops-l-v20-r1`.
8. Click **Begin**. Hold the pen just above the tablet for 2 seconds. Then draw the loops.
9. In one loop, press the pen as hard as you can. Lift the pen, and click **Done**.
10. Select the program `fault-lift`, and click **Begin**. Check that the **Mark** menu has no **Exclude**.
11. Draw a stroke, and click **Done**.
12. Click **Close the session**.
13. On Home, click **Show the exports in Finder**. Record the two export names.

The export of the bench session starts with `SCRIPT_BENCH_WM1_`. The qualification export takes the name of the demo study, for example `SCRIPT_DEMO_DEMO_f19ef0ee`.

### Read the tablet journals

1. Use the test folder of the iPad test, or make a new test folder.
2. Copy the `Exports` folder of SCRIPT Bench into a new folder `incoming-tablet` in the test folder.
3. In the test folder, run `scriptkin intake incoming-tablet --store store --out intake-tablet.json`.
4. Check that both receipts have the result `accepted`.
5. Run `qualification_report.py` of the iPad test with the session identifier of the tablet qualification session.
6. Run `tablet_facts.py` on the journal of each tablet session.

Save this code as `tablet_facts.py`. Run it as `device_facts.py` runs, for example `tablet_facts.py store/sessions/<session identifier>/journal.jsonl`:

```python
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

lines = [json.loads(text) for text in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()]
head = lines[0]
tablet = head["tablet"]
print("source revision:", head["app"]["sourceRevision"], "driver:", tablet["driver"])
print("tablet:", tablet["model"], "pen:", tablet["pen"]["declared"], "scale:", head["scale"])
print("planned channels:", head["channels"])
samples = [line for line in lines if line["kind"] == "sample"]
print("x from", min(s["x"] for s in samples), "to", max(s["x"] for s in samples), "lines")
print("y from", min(s["y"] for s in samples), "to", max(s["y"] for s in samples), "lines")
print("largest force:", max(s.get("force", 0) for s in samples))
print("samples by phase:", dict(Counter(s["phase"] for s in samples)))
mm = head["scale"]["mmPerPoint"]
contacts: dict[int, list[dict]] = {}
for s in samples:
    contacts.setdefault(s["contact"], []).append(s)
for contact, points in contacts.items():
    dx = (points[-1]["x"] - points[0]["x"]) * mm
    dy = (points[-1]["y"] - points[0]["y"]) * mm
    print(f"contact {contact}: dx {dx:.1f} mm, dy {dy:.1f} mm, start to end {(dx * dx + dy * dy) ** 0.5:.1f} mm")
intervals = [b["t"] - a["t"] for points in contacts.values() for a, b in zip(points, points[1:])]
if intervals:
    print(f"median sample interval: {1000 * statistics.median(intervals):.2f} ms")
hovers = [line for line in lines if line["kind"] == "hover"]
print("hover lines:", len(hovers), "clocks:", dict(Counter(h["clock"] for h in hovers)),
      "with z:", sum("z" in h for h in hovers))
```

In the qualification journal, contacts 1 and 2 are the two strokes along the rule, unless the pen touched the tablet before them.

### What the tablet test must confirm

Record each value, and mark each check as passed or failed.

| Check | What to confirm | Where to read it |
| --- | --- | --- |
| Clock check | **Clock check** shows **Passed** after the first pen event. | Home |
| Tablet position | The driver fills `absoluteX` and `absoluteY`. The values of x and y change along the strokes and stay inside 0 to 52600 and 0 to 29600 lines. | `tablet_facts.py` |
| Direction | The left-to-right stroke along the rule has a positive dx. The top-to-bottom stroke has a positive dy, so the journal is y down. The ink on the screen follows the pen. | `tablet_facts.py` and the run screen |
| Scale | Each stroke along the rule measures 100 mm from start to end. | `tablet_facts.py` |
| Pressure maximum | The largest force reaches 1.0 when you press as hard as you can. Record the value. | `tablet_facts.py` |
| Report rate | Record the median sample interval and its 5th and 95th percentiles, with mouse coalescing off. SCRIPT Bench turns coalescing off at launch. | The qualification report |
| Tablet-point events | Record the stationary samples. Each one comes from a `tabletPoint` event during a contact, beside the mouse events. The still hold of `qual-hold` can give them. | `tablet_facts.py` |
| Hover | Hover lines have `clock: event` and no `z`. | `tablet_facts.py` |
| Rotation | SCRIPT Bench plans no rotation channel, so the journal cannot show whether the driver reports rotation. Record this check as open. | None |
| Driver version | `tablet.driver` is the version that the window of the Wacom driver shows. | `tablet_facts.py` |
| Source revision | `app.sourceRevision` is the revision that you built. | `tablet_facts.py` |

### Record the tablet results

Put each result into [the open questions of the design](DESIGN.md#12-open-questions), with the Mac model, the macOS version, the driver version, and the date. Then compare the results with the entry of the Intuos Pro Medium (2025) in the tablet table, `TabletTable` in `app/Sources/ScriptCore/Scale.swift`.

- If the top-to-bottom stroke has a negative dy, the `absoluteY` of the tablet grows downwards. Set `yDown` of the entry to true.
- If the largest force stays below 1.0, the journal keeps `maxForce` 1. Record the largest force, so that an analysis can correct the force.
- If a stroke along the rule does not measure 100 mm, the resolution of the entry is wrong. Do not run the bench study until the entry has the correct resolution and its source.
- If `absoluteX` and `absoluteY` stay at 0, every sample has x 0 and y 29600, and the journal has no tablet position. Record the result. SCRIPT Bench then needs another source of position before the bench study can use it.

After a change to the entry, run `./check`, and commit the change with the test record.
