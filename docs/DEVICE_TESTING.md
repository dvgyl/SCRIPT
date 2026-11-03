# How to test SCRIPT Capture on a real iPad

This guide tells the owner how to run SCRIPT Capture on an iPad Pro (M4 or M5) with Apple Pencil Pro. It also tells how to check what the app records. `./check` cannot do this test. The simulator cannot make Pencil touches, so the app tests record direct touch as `directTouchAsPen`, with no Pencil channel.

The test has six parts. You install the app with a personal team, and you run a qualification session and a supervised dry run. Then you export both sessions, take the exports into a study store, and read the journals. Last, you compare the results with the [checks that only a device can show](#what-the-device-test-must-confirm).

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

## Take the exports into a study store

1. Make a new folder for the test on the Mac, with an `incoming` folder in it.
2. Connect the iPad. In Finder, select the iPad, and open **Files**.
3. Open **SCRIPT Capture**, and drag the `Exports` folder into `incoming`.
4. In the test folder, run `scriptkin intake incoming --store store --out intake-report.json`.
5. Check that the command exits with 0, and that both receipts have the result `accepted`.
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
