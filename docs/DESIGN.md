# SCRIPT design

SCRIPT is a research platform for one question: can drawing and handwriting movement support mental health screening?

It has three parts:

1. **SCRIPT Capture**, a native iPadOS app. A participant draws a battery of tasks with Apple Pencil. The app records every pen sample.
2. **scriptkin**, a Python library. It reads recordings, checks their quality, and calculates movement features.
3. **The evaluation harness**, inside scriptkin. It tests screening models and measurement reliability without leakage.

The platform makes no screening claim. The app never shows a result to the person who draws. A screening claim needs validation on suitable labelled data, and no such data is cleared for this project now.

## 1. Thesis

Each session has **one append-only journal**. The journal is the recording, the crash spool, and the only source of the app's session state.

- The app's session state is a pure **fold** over the journal lines. Live operation and crash recovery use the same function.
- The Python reader is a **second fold** over the same lines.
- Each shared fixture is a journal plus its expected fold summary. The Swift tests and the Python tests must both produce that summary.

After the reader, each stage accepts only a type that the previous stage built after a check. Features accept only segments that quality control (QC) built. The harness shows metrics only after its leakage witness and its null check pass.

## 2. Usage

### 2.1 Operator and participant

1. **Home** lists sessions as open, interrupted, closed, exported, or damaged. The operator taps New session.
2. **Setup** asks for three items: a participant code (pattern `^[A-Z0-9-]{3,16}$`), the Pencil model (Pro, 2nd generation, USB-C), and the battery. If the iPad model is not in the device table, Setup shows a bar of known length in points and asks the operator to measure it with a steel rule.
3. **Handoff** says "Please give the iPad to the participant."
4. **Task.** The participant reads one instruction, taps Begin, and draws. The task ends when the participant taps Done or the time limit occurs. The participant never sees a score, a replay, or a quality value.
5. **Thank you** says "Thank you. Please give the iPad back." A press-and-hold of 2 seconds with three fingers opens Review.
6. **Review** shows each trial with its duration, sample rate, and channels present. The operator can repeat a trial, add a mark from a fixed list, or close the session.
7. **Export** puts a verified folder in Files under On My iPad > SCRIPT > Exports.

After a crash, Home shows the session as interrupted. The open trial is marked interrupted. The operator repeats it, continues, or closes the session.

### 2.2 Analyst

```python
import scriptkin as sk

rec = sk.read_journal("Exports/P0042_3f2a9c1e")    # checks the manifest and integrity rules I1 to I7
for trial in rec.trials:
    match sk.assess(trial):                         # QC builds segments or refuses
        case sk.Accepted() as ok:
            cells = sk.extract(ok)                  # {"kinematic.speed_mean": 41.2, "spatial.trace_rmse": Refusal(...)}
        case sk.Rejected(refusals=r):
            print(trial.task.id, [x.code for x in r])

sk.feature_table([rec]).to_csv("features.csv")      # long format; each cell has a value or a refusal code
```

Cross-device reliability on a synthetic cohort:

```python
from scriptkin import synthetic as syn
from scriptkin.evaluate import reliability

cohort = syn.cohort(n=120, devices=(syn.IPAD_PRO_PENCIL_PRO, syn.IPAD_AIR_PENCIL_USBC), seed=7)
agreement = reliability(sk.feature_table(cohort.recordings), between="device")
agreement["kinematic.speed_mean"].icc               # Estimate(point, low, high)
```

Screening harness with known truth:

```python
from scriptkin.evaluate import screening, Screening, Refused, Prevalence, Missing, LOGISTIC_L2

truth = syn.Outcome(prevalence=0.08, effect={"tremor_mm": 0.6, "pause_rate_hz": 0.4})
cohort = syn.cohort(n=800, devices=(syn.IPAD_PRO_PENCIL_PRO,), outcome=truth, seed=11)
match screening(sk.feature_table(cohort.recordings), cohort.labels, model=LOGISTIC_L2,
                prevalence=Prevalence(0.08), missing=Missing.IMPUTE_IN_FOLD, seed=11):
    case Screening(auc=auc):
        assert auc.low <= cohort.oracle_auc
    case Refused(reason=why):
        raise SystemExit(why)
```

### 2.3 Capture call site

```swift
override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
    for touch in touches where accepts(touch) {
        let measured = (event?.coalescedTouches(for: touch) ?? [touch])
            .map { PenReading($0, in: self, key: TouchKey(touch)) }
        store.ink(recorder.pen(measured))
        inkLayer.preview((event?.predictedTouches(for: touch) ?? [])
            .map { PreviewPoint($0.preciseLocation(in: self)) })   // PreviewPoint has no time and no channels
    }
}
```

## 3. Recording format, version 1.0

This section is the contract between the app and the library. `contract/journal.schema.json` is the machine-readable authority for line shapes.

### 3.1 Files

An export is the folder `Exports/<participant>_<first 8 hex digits of the session UUID>/`. It contains two files:

- `journal.jsonl`: the journal.
- `manifest.json`: the facts that cannot be inside the journal.

```json
{"format": "script.journal", "version": "1.0", "sessionID": "3f2a9c1e-...",
 "journal": {"bytes": 18234112, "lines": 131207, "sha256": "..."}}
```

### 3.2 Line rules

- UTF-8, LF line ends, one JSON object per line.
- Each line has `seq` (an integer equal to the 0-based line index) and `kind`. All other keys are payload, in camelCase.
- Doubles are written as the shortest decimal that round-trips. Python `float()` reads back the same bits. `contract/fixtures/doubles.json` proves this on every check run.
- An absent channel is an absent key. It is never `null` and never `0`.
- Values are native. Positions are points in the capture view (UIKit frame, origin top-left, y down). Angles are radians as UIKit reports them. `force` is in UIKit units.
- Times are uptime seconds since boot. Samples use `UITouch.timestamp`. App events use `ProcessInfo.systemUptime`, which is the same clock. Uptime values are compared only inside one `boot`.
- The bit sets `est` and `expect` use force 1, azimuth 2, altitude 4, location 8, roll 16.

### 3.3 Line kinds

| kind | written when | fields |
|---|---|---|
| `sessionOpened` | line 0 | `format` (`"1.0"`), `purpose`, `sessionID`, `participant`, `openedAt` (ISO 8601 UTC), `openedUptime`, `boot` (boot-session UUID), `app {version, build, sourceRevision}`, `device {model, os, nativeScale, asset}`, `surface {protector, nib}`, `qualification`?, `scale`, `pencil {declared}`, `channels`, `input`, `administration`, `study`, `visit {id, number}`, `ageBand`, `jurisdiction`, `locale`, `consent`, `ethics`, `battery {id, version, sha256}` |
| `handoff` | the iPad passes to another person | `t`, `to` (`participant`, `operator`) |
| `trialStarted` | the stimulus becomes visible | `t`, `trial {index, task, attempt}`, `spec` (full task specification), `practice`, `position`, `hand`, `instruction {id, version, locale}`, `accommodations`, `view {width, height}`, `stimulusOrigin {x, y}` |
| `trialBegan` | the participant taps Begin | `t`, `trial` (index) |
| `contactBegan` | Pencil touches down | `t`, `contact`, `maxForce`? |
| `sample` | each coalesced Pencil touch | `contact`, `t`, `x`, `y`, `force`?, `altitude`?, `azimuth`?, `roll`?, `phase`, `est`, `pending {expect, update}`? |
| `correction` | `touchesEstimatedPropertiesUpdated` | `contact` (or absent when unmatched), `update`, `arrived`, any of `x`, `y`, `force`, `altitude`, `azimuth`, `roll`, then `expect` (bits still pending) |
| `contactEnded` | lift or cancel | `t`, `contact`, `cancelled` |
| `hover` | a hover recognizer change | `t`, `clock` (`callback`), `x`, `y`, `z`, `altitude`?, `azimuth`?, `roll`? |
| `finger` | a finger contact begins or ends | `t`, `phase`, `x`, `y` |
| `trialEnded` | the trial ends | `t`, `trial` (index), `reason` |
| `questionnaireStarted` | an instrument appears | `t`, `block`, `instrument {id, version, locale}` |
| `response` | the participant answers or skips an item | `t`, `block`, `item`, `value`? (absent when skipped), `skipped` |
| `questionnaireEnded` | the instrument closes | `t`, `block`, `reason` (`completed`, `declined`, `interrupted`) |
| `safetyFlagged` | a safety item is endorsed or skipped | `t`, `block`, `item`, `value`? , `skipped` |
| `safetyShown` | the app shows safety content | `t`, `what` (`crisisList`, `operatorAlert`), `listVersion`? |
| `safetyAction` | the operator records the response to a flag | `t`, `code`, `role` |
| `mark` | operator action | `t`, `trial` (index), `code` (`repeatRequested`, `disturbance`, `deviceProblem`, `exclude`) |
| `recovered` | relaunch adopts an open journal | `t`, `at`, `boot`, `truncatedBytes`, `interrupted`? (trial index), `interruptedBlock`? |
| `sessionClosed` | close | `t`, `counts` (number of lines of each other kind before this line; every kind is present, zero included) |

Field notes:

- `purpose` is `study` or `qualification` (section 16).
- `scale` is one of `{"kind": "modelTable", "mmPerPoint", "ppi", "nativeScale"}`, `{"kind": "reference", "object", "mmPerPoint", "lengthMM", "drawnPoints"}`, or `{"kind": "unknown"}`. `object` is `steelRule` or `idCard` (an ISO/IEC 7810 ID-1 card, 85.60 mm wide). The model-table formula is `mmPerPoint = 25.4 × nativeScale / ppi`. An unknown scale makes the library refuse every feature that needs millimetres.
- `channels` is the planned channel set: `{"force": bool, "altitude": bool, "azimuth": bool, "roll": bool, "hover": bool}`. It comes from the declared Pencil model and the hover support of the device. QC compares the plan with the data.
- `input` is `pencil` in normal use. It is `directTouchAsPen` only in simulator tests, because the simulator cannot make Pencil touches. The library marks such recordings as simulated, and QC refuses every feature that needs a Pencil-only channel.
- `device.asset` is a pseudonymous asset code that the site assigns to the iPad. `surface.protector` is `none`, `glossy`, `matte`, `paperlike`, or `unknown`; `surface.nib` is `new`, `used`, or `unknown`. `qualification` is `{"sessionID", "at"}` of the last qualification session on this device and Pencil, or absent.
- `administration` is `supervised` or `self`. `study` is `{id, site, configID, configVersion, configSHA256, protocolVersion}`. `visit` links repeated sessions of one participant: `id` is a study visit code, `number` starts at 1.
- `ageBand` is `13-17` or `18-24`. `jurisdiction` is an ISO 3166-1 alpha-2 country code. `locale` is a BCP 47 tag.
- `consent` and `ethics` hold attestations and document versions only (section 15.4). The journal never holds a name, a date of birth, a signature, or contact details.
- `trialStarted.position` is the 1-based position of the task in the session order. `hand` is `left`, `right`, or `unknown`. `practice` is true for a practice task; the library excludes practice trials from features. `accommodations` is a list of codes: `readAloud`, `largerText`, `extraTime`, `positionAssist`, `other`.
- `trialEnded.reason` is `participantDone`, `timeLimit`, `declined`, `unable`, `operatorAbort`, `backgrounded`, or `technicalFailure`. `declined` means that the participant chose not to do the task. `unable` means that the participant could not do it.
- `contact` is an integer, unique in the session, that starts at 1. A correction that arrives after the next trial starts is therefore still unambiguous.
- `update` is `estimationUpdateIndex`. A correction never edits a sample. The library joins them by `(contact, update)`. When two samples share a key, a correction with that key applies to both.
- `z` in `hover` is normalized: 0 at contact, 1 at the maximum range. It has no unit.
- Finger contacts never produce samples. They exist for QC only.
- `block` numbers questionnaire blocks from 0 in session order. `item` is the item identifier from `contract/instruments.json`. `value` is the response value of the chosen option.
- Responses are in the journal, because the journal is the single source of session state and crash recovery. `scriptkin` can write a pen-only copy of a journal for analysis that must stay blind to the labels.

### 3.4 Integrity rules

The reader raises `JournalError` when a rule fails.

- **I1.** Each line ends in LF and parses to an object with a known `kind`.
- **I2.** `seq` equals the line index.
- **I3.** Line 0 is `sessionOpened`. The last line is `sessionClosed`. The major version matches the reader.
- **I4.** `sessionClosed.counts` equals a recount.
- **I5.** The manifest byte count, line count, and SHA-256 match the file.
- **I6.** Nesting holds. Samples and `contactEnded` are inside an open contact. Contacts are inside an open trial. `trialBegan` follows its `trialStarted` inside the same trial. Responses and safety flags are inside an open questionnaire block. Trials and questionnaire blocks do not overlap.
- **I7.** Uptime values are compared only inside one `boot`.

These are **QC findings**, not integrity failures: duplicate timestamps, gaps, a low sample rate, unresolved estimates, unmatched corrections, finger contacts, and a planned channel that the data does not show. A minor version adds optional keys only.

### 3.5 Fold summary

The fold summary is the shared test oracle. Both languages produce it from a journal:

```json
{"sessionID": "...", "purpose": "study", "administration": "supervised",
 "state": "ready | drawing | answering | suspended | closed",
 "counts": {"sample": 812, "correction": 37, "...": 0},
 "trials": [{"index": 0, "task": "spiral-trace", "attempt": 1, "practice": false, "began": true,
             "outcome": "completed", "contacts": 3, "samples": 640, "corrections": 30, "unresolved": 2}],
 "questionnaires": [{"block": 0, "instrument": "phq9", "answered": 9, "skipped": 0, "outcome": "completed"}],
 "safety": {"flags": 1, "crisisListShown": 1, "operatorAlerts": 1, "actions": 1},
 "unmatchedCorrections": 0, "truncatedBytes": 0}
```

`outcome` for a trial is `completed`, `timeLimit`, `declined`, `unable`, `aborted`, `interrupted`, `technicalFailure`, or `open`. `participantDone` maps to `completed`, `operatorAbort` to `aborted`, and `backgrounded` to `interrupted`. A questionnaire outcome is `completed`, `declined`, `interrupted`, or `open`. `unresolved` counts samples whose `pending` bits never all cleared. The summary holds counts only, never a questionnaire score.

## 4. Task battery

`contract/battery.json` holds the battery. The Python module `scriptkin.battery` generates it, and `./check` fails when the checked-in file differs. Geometry is stored as polylines in millimetres, with the origin at the top-left of the stimulus frame and y down. Swift draws a stimulus with one multiply and one add. Swift has no spiral mathematics. `trialStarted` embeds the full task specification, so each journal is self-contained.

Battery version 1 has a practice task and six measured tasks:

| id | mode | stimulus | source datasets |
|---|---|---|---|
| `practice` | trace | a straight line and a circle | none; excluded from features |
| `spiral-trace` | trace | two-turn Archimedean spiral | PaHaW, ParkinsonHW, the earlier SCRIPT |
| `loops` | copy | a row of connected "l" loops | EMOTHAW, Crespo cohort, EMBC bench |
| `pentagons` | copy | two overlapping pentagons | EMOTHAW |
| `house` | copy | a line drawing of a house | EMOTHAW |
| `clock` | free | none | EMOTHAW |
| `sentence` | copy | fixed text, system font, size in millimetres | EMOTHAW, PaHaW |

The sentence stimulus is text, not geometry. Spatial-error features return `not_applicable` for `clock` and `sentence`. The schema reserves `targets` for a later trail-making task.

## 5. Swift shape

`app/Package.swift` defines `ScriptCore`. It uses Foundation only, so `swift test` runs it on the Mac in seconds. The app target `ScriptApp` holds the SwiftUI screens and one UIKit view.

```
app/Sources/ScriptCore/
  Journal.swift    line types and the flat line codec
  Session.swift    SessionState, Command, decide, apply, fold, summary
  Battery.swift    Battery, TaskSpec, Stimulus, Completion
  Ink.swift        PenReading, PreviewPoint, ChannelPlan, InkRecorder
  Storage.swift    JournalWriter, recover, reconcile, publish
  Scale.swift      MillimetreScale and the device table
app/ScriptApp/
  ScriptApp.swift  @main and scene phase
  SessionStore.swift
  CaptureView.swift
  Screens.swift    Home, Setup, Task, Review, Export
```

Key decisions:

- **Decider.** `decide(state, command) -> Result<[Event], Rejection>` turns an intent into events. `apply(state, entry) -> SessionState` turns a fact into the next state. `fold` applies every entry. Live operation and replay share `apply`.
- **States.** `ready(Session)`, `drawing(Session, OpenTrial)`, `answering(Session, OpenBlock)`, `suspended(Session)` (the last line is `recovered`), `closed(Session)`. `sessionOpened` gives `ready`. `trialStarted` moves `ready` or `suspended` to `drawing`. `trialEnded` moves `drawing` to `ready`. `questionnaireStarted` moves `ready` or `suspended` to `answering`, and `questionnaireEnded` moves it back to `ready`. `recovered` moves `ready`, `drawing`, or `answering` to `suspended`, and an open trial or block becomes interrupted. `sessionClosed` moves `ready` or `suspended` to `closed`. A correction is legal in every state before `closed`.
- **Safety rules in `decide`.** In a supervised session, `close` is rejected while a safety flag has no `safetyAction`. In a session with at least one questionnaire block, `close` is rejected until the crisis list has been shown after the last block. Section 15 gives the full protocol.
- **InkRecorder** hides the contact lifecycle, the coalesced iteration, and the update-index map. It lives for the whole session, so a correction that arrives after a lift or after a trial still finds its contact. It is UIKit-free and testable on the Mac.
- **JournalWriter** is an actor with one `AsyncStream` and one consumer, so line order equals call order on the main actor. It flushes at 64 KB, at 250 ms, or at `contactEnded`. It runs fsync at `trialEnded`, at `sessionClosed`, and when the scene goes to the background.
- **recover** removes a torn final line with `ftruncate` and reports the removed bytes. Damage before the final line is not repaired. The file moves unchanged to `Damaged/`.
- **publish** clones the journal into `Staging/<name>/`, writes the manifest, runs fsync, verifies the staged copy (decode, fold, counts, SHA-256), and renames the folder into `Exports/`. Only then does it delete the source journal.
- **reconcile** runs at launch and converges from any crash point. Two launches in a row give identical bytes.
- UIKit types stop at `CaptureView.swift`. The ink layer is a `CAShapeLayer` path for each contact plus one temporary layer for predicted points. The app does not use PencilKit, because a PencilKit canvas takes the touches itself.

App container layout:

```
Library/Application Support/Sessions/<uuid>.jsonl    journals not yet exported
Library/Application Support/Staging/<name>/          publication in progress
Documents/Exports/<name>/                            verified exports, visible in Files
Documents/Damaged/<uuid>.jsonl                       journals with damage before the last line
```

## 6. Python shape

Package `scriptkin`. Runtime dependencies: numpy, scipy, scikit-learn. Python 3.12 or later.

```
python/src/scriptkin/
  __init__.py        public surface
  recording.py       Recording, Trial, Stroke, Channel, Device, TaskRef
  battery.py         stimulus geometry and battery.json
  formats/journal.py read_journal, fold, summarize
  formats/svc.py     SvcReader
  formats/parkinsonhw.py
  quality.py         QcPolicy, Refusal, Segment, Accepted, Rejected, assess
  signal.py          resampling, kinematics, Welch band power, entropy counts, micro-movement spikes, Gamma fit
  features.py        the feature catalog and extract, feature_table
  synthetic.py       traits, device models, journals, cohorts
  evaluate/          screening, metrics, reliability, report
  contract.py        regenerate and verify the shared contract
  cli.py             inspect, features, contract
```

Key decisions:

- **Library frame.** Millimetres, x to the right, y up. Readers convert units and flip y at the boundary. When the source scale is unknown, coordinates stay in source units, `Device.spatial_unit` is `"source"`, and features that need millimetres refuse with `unknown_scale`.
- **Readers** parse one source into a `Recording`. The journal reader applies matched corrections to the last value received and keeps a mask of unresolved estimates. The journal keeps the originals.
- **QC.** `assess` is the only function that builds a `Segment`. A segment is a run inside one stroke with strictly increasing time, no step longer than `max_gap_s`, finite positions, and at least `min_segment_s` duration. A gap splits a stroke. Nothing bridges a gap. `Accepted` has a private constructor.
- **Refusals.** A failed requirement returns `Refusal(code, detail)`, never a silent number. Structural errors in a file raise `JournalError` or `FormatError`.
- **Kernels** work on one segment, or count across segments without joining them. Sample entropy sums template matches over all segments. Templates never cross a segment end, and self-matches are excluded. Coarse-graining stays inside each segment.
- **Pooling.** Each per-segment feature declares a pool rule and `min_values`. Fewer finite segment values give `Refusal(too_few_values)`.
- **Feature families.** Kinematic, timing, pressure, tilt and roll, spatial error, spectral band power, entropy, and micro-movement spikes with a Gamma fit (Torres method: speed peaks normalized by the peak plus the mean speed between its neighbouring minima; location fixed at 0; maximum likelihood; intervals from the observed information).
- **Provenance.** A `FeatureTable` carries the library version, the QC policy, the preprocessing settings, the catalog digest, and the source hashes.

## 7. Evaluation

- `screening(table, labels, *, model, prevalence, missing, plan, seed) -> Screening | Refused`.
- One row per participant. The median over accepted attempts gives each task and feature value.
- Outer `StratifiedGroupKFold` with repeats. Inner grouped search and grouped calibration use training participants only.
- **Leakage witness.** A `FitWitness` step at the head of every pipeline records the participants at each `fit` call. The harness checks the exact invariant: no test participant appears in any fit of its fold. A failure returns `Refused`.
- **Null check.** A permutation null runs the same plan. A mean null AUC outside 0.5 ± 0.03 returns `Refused`.
- **Report.** AUC with a participant bootstrap interval (stated as conditional on the fitted procedure), calibration intercept, slope, and curve, sensitivity, specificity, PPV and NPV at the stated prevalence, decision curves with treat-all and treat-none, and the TRIPOD+AI items that the harness knows.
- `reliability(table, between="device" | "session")` gives ICC(A,1) with an interval, Bland-Altman bias and limits, SEM, and MDC95.
- **Governance.** Only synthetic cohorts and cleared datasets enter `screening`. Public CC BY datasets test readers and features only. See `AGENTS.md`.

## 8. Synthetic cohorts

The generator follows each stimulus polyline with a minimum-jerk speed profile. It adds tremor at right angles to the path, micro-movement spikes from Gamma traits, and pen lifts at pauses. It samples through a device model with jitter, drops, late corrections, and absent channels. It writes real journal lines, so every synthetic cohort goes through the real `read_journal`, `assess`, and `extract`.

## 9. Repository and checks

```
SCRIPT/
  check                       the single check command
  README.md  AGENTS.md
  docs/DESIGN.md
  contract/
    journal.schema.json       JSON Schema 2020-12
    battery.json              generated, checked in
    fixtures/journal/*.jsonl and *.fold.json
    fixtures/svc/  fixtures/parkinsonhw/
    fixtures/doubles.json
  app/
    Package.swift  Sources/ScriptCore/  Tests/ScriptCoreTests/
    ScriptApp.xcodeproj  ScriptApp/  ScriptAppUITests/
  python/
    pyproject.toml  uv.lock  src/scriptkin/  tests/
```

`./check` stops at the first failure. It runs the contract verification, the Python lint, type check, and tests, the `ScriptCore` Swift tests on the Mac, and the app build and tests on the "iPad Pro 11-inch (M5)" simulator. Build output goes to `~/Library/Caches/SCRIPT/<checkout>-<hash>/`, outside the repository, so that the repository can sit on an exFAT drive.

The fixtures tie the two languages in three directions:

1. **Python to both.** `scriptkin contract regenerate` writes the journal fixtures and their `.fold.json` summaries. The generator writes each summary from what it built, not from a reader. Swift and Python both fold each fixture and compare with `.fold.json`.
2. **Swift to Python.** A Swift test drives `decide` and `InkRecorder`, writes through `JournalWriter`, and publishes through `Storage` into `$SCRIPT_CONTRACT_OUT`. pytest reads that export with `read_journal` and compares its summary with the Swift summary.
3. **Schema to both.** pytest validates each fixture line and each Swift output line against `journal.schema.json`.

Each test names the defect that it detects.

## 10. Synthesis decision

Three runners designed the platform independently: GPT-6.1 Sol, GPT-6 Astra, and Claude Opus 5.5. Their full designs are in the project records, outside this repository.

- **Base: Opus, "one journal, two folds".** It is the only design with one source of truth for session state. Sol and Astra kept a separate state, so crash recovery had to reconcile two sources. Opus also put geometry in one language, tested the core on the Mac without a simulator, and made leakage a checked invariant.
- **From Sol and Astra:** unknown physical scale is explicit and makes millimetre features refuse; unmatched corrections are QC findings, not file errors; task-feature combinations that do not apply return `not_applicable`; capability plans are recorded with their source; the evaluation report states that bootstrap intervals are conditional on the fitted procedure.
- **Added here:** the `input` field and `directTouchAsPen`, so a simulator test can drive the full app and produce an export that the library reads.
- **Rejected:** a type that admits only synthetic cohorts into `screening` (governance is a process rule, written in `AGENTS.md`); several stream files with a separate state file; SQLite as the export; parametric geometry in two languages; code generation from the schema.

## 11. Tradeoffs accepted

- We accept a text journal of about 150 bytes per sample (20 to 30 MB per 10-minute session) in exchange for self-describing lines, visible torn tails, exact float round-trips, and fixtures that a diff can read.
- We accept one writer for all streams in exchange for one total order across samples, corrections, hover, and lifecycle.
- We accept polylines in `battery.json` in exchange for one geometry implementation.
- We accept an operator declaration of the Pencil model, because no public API reports it. QC checks the declaration against the data.
- We accept one row per participant in screening. Within-participant variability must be an explicit feature.
- We accept that the UIKit capture adapter is verified on a real device, not in automated checks, because the simulator cannot make Pencil force or tilt.

## 12. Open questions

- Does `UIHoverGestureRecognizer` give an event timestamp, or only callback time?
- What does Pencil USB-C put in `force`? The plan omits the channel until a device test confirms it.
- Which iPad models go in the pixel-density table?
- Are the default QC thresholds and the 200 Hz analysis grid correct for cross-device comparison?
- Is the micro-movement normalization exactly the published Torres definition?

## 13. Build order

| unit | content | check that ends it |
|---|---|---|
| U0 | Scaffold, schema, battery generator, first fixture, `./check` | `./check` passes on the skeleton |
| U1 | Python readers, QC, signal kernels, features | Python fold matches every fixture; numeric tests pass |
| U2 | `ScriptCore`: codec, decider, InkRecorder, storage | Swift fold matches every fixture; crash-point tests converge |
| U3 | Synthetic cohorts and the evaluation harness | null cohort at chance; leakage witness refuses an ungrouped splitter |
| U4 | App screens, capture view, simulator test | a simulator battery export reads with zero errors |
| U5 | ParkinsonHW reader run; end-to-end report | public files read; features computed; no labels modelled |
| U6 | Independent review by three models; fixes | no blocker or major finding open |

## 14. Study configuration

A study team controls a session through one configuration file, not through code. `contract/config.schema.json` is the authority. `contract/configs/demo.json` is the default that the app ships with.

A configuration holds:

- `study {id, version, protocolVersion}` and the list of allowed `sites`.
- `tasks`: the tasks from the battery, in session order, with `practice` flags, `attempts` (the maximum number of attempts), and `hands` (`dominant`, `left`, `right`, or `both`).
- `questionnaires`: blocks by administration mode and age band, each naming an instrument from `contract/instruments.json`, and their position (`beforeTasks` or `afterTasks`).
- `locales`: the instruction texts for each locale, with an `instructionVersion`.
- `safety`: the crisis lists by jurisdiction and locale, each with `version` and `verifiedOn`; the operator action codes; and, only when a self-administered block contains a safety item, a `remoteReview` block (section 15.3).
- `consentModel`: which attestations the operator or the enrolment record must give for each age band (section 15.4).
- `export`: the recipient public key for encrypted exports, or `null` for the demo configuration.
- `operatorPin`: a salted SHA-256 hash of the operator PIN, or `null` for the demo configuration.

The app imports a configuration through Files. It checks the schema and the rules in this section, shows the study, version, and digest to the operator, and refuses a configuration that fails a check. Every session records `study.configSHA256`, so an analyst can prove which configuration produced it. A configuration is frozen for the life of a session.

## 15. Questionnaires and safety

### 15.1 Instruments

`contract/instruments.json` holds each instrument: its identifier, version, licence status with the source, validated age range, recall text, response options, items, safety items, and scoring rule. The `scriptkin.instruments` module generates it. The app shows the exact item text and records item identifiers and response values. The app never computes or shows a score.

| id | items | licence | validated ages | safety items |
|---|---|---|---|---|
| `phq9` | 9 | public domain (PHQ manual) | 13-17 (Richardson 2010), 18+ | item 9 |
| `phq8` | 8 | public domain (PHQ manual) | 18+; adolescent validation not found | none |
| `gad7` | 7 | public domain (PHQ manual) | 12-17 (exploratory), 15-24, 18+ | none |
| `dass21` | 21 | public domain; not in an app open to the public | 14+ | none |

The modified-for-teens PHQ-A form needs written permission from its owners and is not included. A study can add an instrument only with a licence reference in its configuration.

### 15.2 Supervised sessions

- The default blocks are `phq9` and `gad7` after the tasks, for both age bands.
- A skipped safety item counts as a flag, as in the NIMH ASQ pathway.
- When a block ends, the app always shows the same handoff screen: "Please give the iPad back to the researcher." The flow is identical with and without a flag, so the participant cannot infer the result.
- The operator screen then shows a neutral alert for each flag. The operator must record one action code before the session can close. The default codes are `clinicianAssessment`, `referred`, `emergencyServices`, `guardianContacted`, and `participantDeclinedHelp`.
- Every participant sees the configured crisis list at the end of a session that had a questionnaire block.

### 15.3 Self-administered sessions

- The default blocks are `phq8` and `gad7`. They contain no safety item, because no one can respond in real time.
- A configuration may add a block with a safety item to a self-administered session only with a `remoteReview` block: the reviewer role, the review interval (at most 1 weekday; at most 24 hours for imminent risk), the export interval that makes this review possible, and the version of the monitoring disclosure that the participant saw at consent. The app refuses a configuration that breaks this rule.
- In such a session, the app shows the crisis list at once when a safety item is flagged, and again at the end. It records each display.
- The app never tells the participant that a response was flagged.

### 15.4 Consent attestations

The site keeps identities, signed forms, and guardian contact details. The journal holds only attestations:

- `consent {model, attestedBy {code, role}, attestedAt, documents [{id, version}]}`. `model` is `adult`, `guardianAndAssent`, or `waiver` (with an approval reference). For `13-17`, the default model is `guardianAndAssent`.
- `ethics {approvalID, protocolVersion, safetyProtocolVersion, monitoringDisclosureVersion?}`.

The operator gives the attestation in supervised sessions. In self-administered sessions, the enrolment record of the study supplies it, and `attestedBy.role` is `enrolment`.

## 16. Device qualification

A qualification session has `purpose: qualification` and a fixed task set: a reference-length check with a steel rule or an ID-1 card, a 10-second still hold, a straight-line stroke along a guide, and a channel check. It writes an ordinary journal. `scriptkin` computes the sample-interval distribution, gaps, jitter, scale error, and channel presence from it. A study session records the identifier of the last qualification session on its device. "Can record" differs from "qualified for comparison"; only the analysis plan decides the second.

## 17. Privacy and withdrawal

- Journals and exports use iOS file protection `completeUntilFirstUserAuthentication`.
- When the configuration has an export key, the export is encrypted to the study's public key with HPKE (RFC 9180, base mode, DHKEM(X25519, HKDF-SHA256), HKDF-SHA256, ChaCha20-Poly1305). This is CryptoKit's `HPKE.Ciphersuite.Curve25519_SHA256_ChachaPoly`. The export folder then holds `journal.jsonl.hpke` in place of `journal.jsonl`. The `info` parameter is the UTF-8 text `SCRIPT export 1.0`, and the associated data is the UTF-8 session identifier. The manifest gains this block:

  ```json
  "encryption": {"scheme": "hpke-base", "suite": "X25519-HKDF-SHA256-ChaCha20Poly1305",
                 "keyID": "...", "encapsulatedKey": "<base64>",
                 "plaintext": {"bytes": 18234112, "lines": 131207, "sha256": "..."},
                 "ciphertext": {"bytes": 18234128, "sha256": "..."}}
  ```

  The `journal` block of the manifest is then absent. Only the demo configuration writes plaintext exports, and their manifest has `"demo": true`. `scriptkin` decrypts with the study's private key and then applies the normal integrity rules to the plaintext.
- Withdrawal: the operator enters a participant code, confirms twice, and the app deletes every journal and export for that code on the device. It appends one line to a deletion log: the time, the SHA-256 of the code, and the session identifiers. Copies outside the device are the site's duty; `docs/DATA_GOVERNANCE.md` gives the procedure.
- Pseudonymous data remain personal data under the GDPR. A participant code alone does not meet HIPAA de-identification.

## 18. Intended use

SCRIPT is a research platform. It records drawing movement and reference questionnaire responses in supervised and self-administered sessions with people aged 13 to 24, and it tests whether movement features relate to symptom scores. It does not diagnose, screen, triage, or advise. It shows no score or interpretation to anyone. Any screening claim needs a separate intended-use statement, a validation study in the intended population and setting, and the applicable regulatory review.

