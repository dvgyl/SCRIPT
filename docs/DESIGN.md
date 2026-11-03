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

1. **Home** lists sessions as open, interrupted, closed, exported, or damaged. A participant code shows only its last 4 characters until the operator unlocks with the PIN in this launch, so the demo configuration always masks it. The operator taps New session.
2. **Setup** asks for the participant code (pattern `^[A-Z0-9-]{3,16}$`), the age band, jurisdiction, locale, dominant hand, visit number, consent attestation, device asset code, screen protector, nib state, and Pencil model (Pro, 2nd generation, USB-C). If the iPad model is not in the device table, or its table density is not confirmed, Setup asks for a reference measurement with a steel rule or an ID-1 card. Setup refuses to start a session when the device has less than 500 MB of free storage.
3. **Handoff** says "Please give the iPad to the participant."
4. **Task.** One screen shows the instruction, the stimulus at true millimetre scale, and Begin. Begin enables ink. The task ends when the participant taps Done or the time limit occurs; no countdown is shown. The participant never sees a score, a replay, or a quality value.
5. **Handoff** says "Please give the iPad back to the researcher." A visible Operator control (press and hold for 2 seconds, then the PIN when the configuration has one) opens the operator screens. The flow is the same whatever the answers were.
6. **Review** shows each safety flag with its instrument and item (never a score), then shows each trial with its duration, sample count, rate, and channels. In a supervised session, each flag needs an action code. In a self-administered session, each flag reads "Reviewed by the study reviewer after export" and needs no action. The participant then sees the crisis list. Marks and repeat buttons in Review are planned, not built.
7. **Export** puts a verified folder in Files under On My iPad > SCRIPT Capture > Exports. The Export screen shows the folder name.

After a crash, Home shows the session as interrupted. The open trial is marked interrupted. The operator repeats it, continues, or closes the session. An interrupted self-administered session opens again at launch for its participant, who types their code first; after its resume window it closes instead (section 14). The operator can export a damaged journal from Home as a damaged package (section 17).

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
from scriptkin.evaluate import AnalysisPlan, Refused, Screening, Target, screening

cohort = syn.cohort(n=300, seed=11, scenario="signal")       # PHQ-9 answers come from a latent score
plan = AnalysisPlan(target=Target(instrument="phq9", cut=10.0),
                    features=("spiral-trace/kinematic.speed_mean", "spiral-trace/spectral.tremor_power"),
                    prevalence=0.08)                          # the report records plan_digest(plan)
match screening(sk.feature_table(cohort.recordings), cohort.labels, plan=plan, seed=11):
    case Screening(auc=auc):
        print(auc.point, auc.low, auc.high, auc.p_value)     # significance comes from the permutation p-value
    case Refused(reason=why):
        raise SystemExit(why)
```

The report records the plan digest, but the digest does not show when the plan was fixed. To show that the plan was fixed before the labels joined, register the output of `scriptkin plan-digest plan.json` first. Then run `scriptkin evaluate --expect-plan-digest <digest>`. It refuses a plan with another digest before it reads the labels. The Python function `screening` does not check a registered digest.

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

An export is the folder `Exports/SCRIPT_<studyID>_<siteCode>_<first 8 hex digits of the session UUID, lowercase>/`. The participant code is not in the name; it stays inside the journal, and inside the ciphertext when the export is encrypted. It contains two files:

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
| `safetyFlagged` | a safety item is endorsed or skipped, or its block ends without a response to it | `t`, `block`, `item`, `value`? , `skipped` |
| `safetyShown` | the app shows safety content | `t`, `what` (`crisisList`, `operatorAlert`), `listVersion`? |
| `safetyAction` | the operator records the response to a flag | `t`, `code`, `role` |
| `mark` | operator action | `t`, `trial` (index), `code` (`repeatRequested`, `disturbance`, `deviceProblem`, `exclude`) |
| `recovered` | relaunch adopts an open journal | `t`, `at`, `boot`, `truncatedBytes`, `interrupted`? (trial index), `interruptedBlock`? |
| `sessionClosed` | close | `t`, `counts` (number of lines of each other kind before this line; every kind is present, zero included) |

Field notes:

- `purpose` is `study` or `qualification` (section 16).
- `app.sourceRevision` is never empty. A build that `./check` did not stamp records `unknown`.
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
- An unreached safety item is a flag. When a block whose instrument has a safety item ends (`completed`, `declined`, or `interrupted`) without a `response` to that item, a `safetyFlagged` line with `skipped: true` and no `value` comes inside the block: before `questionnaireEnded`, or, for a block that a crash interrupted, before `recovered`. The flag before `recovered` carries the time of the block's latest `questionnaireStarted` or `response` line, so it stays in the boot of its block. In a self-administered session, a `safetyShown` line with the crisis list follows the flag at once; after a recovery, the app shows the list when the session resumes.
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

`app/Package.swift` defines `ScriptCore`. It uses only system frameworks (Foundation, CryptoKit, CommonCrypto, Security, Synchronization) and no UIKit, so `swift test` runs it on the Mac in seconds. The app target `ScriptApp` holds the SwiftUI screens and one UIKit view.

```
app/Sources/ScriptCore/
  Journal.swift             line types and the flat line codec
  JSON.swift                the JSON value, parser, and writer
  Session.swift             SessionState, Command, decide, apply, fold, summary
  Battery.swift             Battery, TaskSpec, Stimulus, Completion
  Ink.swift                 PenReading, PreviewPoint, ChannelPlan, InkRecorder
  Storage.swift             JournalWriter, recover, reconcile, publish, publishDamaged, withdraw
  Scale.swift               MillimetreScale and the device table
  Study.swift               instruments, StudyConfiguration, validate, SessionRules, OperatorPin
app/ScriptApp/
  ScriptApp.swift           @main and scene phase
  SessionStore.swift        the store: storage, writer, frozen rules, folded state, and the stage
  Flow.swift                SessionPlan, the stimulus layout, and the operator digest
  Environment.swift         the bundled contract, the configuration library, device facts, and the clocks
  Components.swift          the operator control, the PIN sheet, and shared styles
  HomeScreen.swift          Home, configuration import, and withdrawal
  SetupScreen.swift         Setup and the reference measurement
  ParticipantScreens.swift  code confirmation, Ready, hand, disclosure, task, item, crisis list, and thank-you
  OperatorScreens.swift     Paused, Review, qualification result, stopped, and Export
  CaptureView.swift         the UIKit capture view
```

Key decisions:

- **Decider.** `decide(state, command, rules) -> Result<[Event], Rejection>` turns an intent into events. `rules` is the frozen study configuration for the session (instruments, safety items, crisis-list version, action codes, battery), because the fold does not hold it. `apply(state, entry) -> SessionState` turns a fact into the next state. `fold` applies every entry. Live operation and replay share `apply`.
- **States.** `ready(Session)`, `drawing(Session, OpenTrial)`, `answering(Session, OpenBlock)`, `suspended(Session)` (the last line is `recovered`), `closed(Session)`. `sessionOpened` gives `ready`. `trialStarted` moves `ready` or `suspended` to `drawing`. `trialEnded` moves `drawing` to `ready`. `questionnaireStarted` moves `ready` or `suspended` to `answering`, and `questionnaireEnded` moves it back to `ready`. `recovered` moves `ready`, `drawing`, or `answering` to `suspended`, and an open trial or block becomes interrupted. `sessionClosed` moves `ready` or `suspended` to `closed`. A correction is legal in every state before `closed`.
- **Further refusals in `decide`.** An operator alert or action in a self-administered session; an action with no open flag (each action answers the oldest open flag); a block start without a crisis list for the session's jurisdiction; an unverified instrument; a self-administered safety item without `remoteReview`; an `expire` of a supervised session, of a session that is not interrupted, or of a session inside its resume window.
- **Unreached safety items in `decide`.** `endQuestionnaire` flags each safety item of the block that has no response before it ends the block, and a self-administered session shows the crisis list with the flag. `recovery` does the same for a block that a crash interrupted (section 3.3), and it needs the instrument set to know the safety items.
- **Safety rules in `decide`.** In a supervised session, `close` is rejected while a safety flag has no `safetyAction`. In a session with at least one questionnaire block, `close` is rejected until the crisis list has been shown after the last block. `expire` closes an interrupted self-administered session after its resume window with no crisis list, because no participant is present. Section 15 gives the full protocol.
- **InkRecorder** hides the contact lifecycle, the coalesced iteration, and the update-index map. It lives for the whole session, so a correction that arrives after a lift or after a trial still finds its contact. It is UIKit-free and testable on the Mac.
- **JournalWriter** is an actor with one `AsyncStream` and one consumer, so line order equals call order on the main actor. It flushes at 64 KB, at 250 ms, or at `contactEnded`. It runs fsync at `trialEnded`, `questionnaireEnded`, `safetyFlagged`, `safetyAction`, `sessionClosed`, and when the scene goes to the background, so that no safety line can be lost in a crash. After a command that appends `safetyFlagged`, the store awaits the writer's `sync()` before the stage changes, so no screen that follows a flag shows before the flag is on storage.
- **recover** keeps the complete lines, appends the `recovered` line (after the flags of the unreached safety items of an open block), writes the result to a temporary file, and renames it over the journal. A crash at any point leaves either the old file or the new one, so a second launch writes identical bytes. Recovery is a fact about the launch (`SessionState.recovery`), not a `Command`. A `recovered` line is legal in `suspended` too, because a second crash on a new boot must record its boot. Damage before the final line is not repaired. The file moves unchanged to `Damaged/`.
- **publish** clones the journal into `Staging/<name>/`, writes the manifest, runs fsync, verifies the staged copy (decode, fold, counts, SHA-256), and renames the folder into `Exports/`. Only then does it delete the source journal.
- **reconcile** runs at launch and converges from any crash point. Two launches in a row give identical bytes.
- UIKit types stop at `CaptureView.swift`. The ink layer is a `CAShapeLayer` path for each contact plus one temporary layer for predicted points. The app does not use PencilKit, because a PencilKit canvas takes the touches itself.

App container layout:

```
Library/Application Support/Sessions/<uuid>.jsonl    journals not yet exported
Library/Application Support/Staging/<name>/          publication in progress
Documents/Exports/<name>/                            verified exports, visible in Files
Library/Application Support/Damaged/<uuid>.jsonl    journals with damage before the last line; they leave only as damaged packages
Library/Application Support/ExportIndex.jsonl       {name, participantHMAC} per export, so that withdrawal finds encrypted exports
Library/Application Support/Deletions.jsonl         the deletion log
Library/Application Support/Configurations/         imported configurations, by SHA-256
```

## 6. Python shape

Package `scriptkin`. Runtime dependencies: numpy, scipy, scikit-learn, cryptography, jsonschema. Python 3.12 or later.

```
python/src/scriptkin/
  __init__.py        public surface
  recording.py       Recording, Trial, Stroke, Channel, Device, TaskRef, session and trial context, questionnaire blocks
  battery.py         stimulus geometry and battery.json
  instruments.py     questionnaire text and scoring rules, instruments.json
  studyconfig.py     the demo configuration and the configuration rules
  formats/journal.py fold, summarize: line checks and the session state machine
  formats/export.py  verify_export, read_journal (manifest and encrypted exports), pen_only
  formats/checks.py  value checks for the journal and manifest readers
  formats/svc.py     SvcReader
  formats/parkinsonhw.py  read_parkinsonhw, load_parkinsonhw
  hpke.py            HPKE base mode for the export suite (RFC 9180)
  quality.py         QcPolicy, Refusal, Segment, Accepted, Rejected, assess
  signal.py          resampling, kinematics, Welch band power, entropy counts, micro-movement spikes, Gamma fit
  features.py        the feature catalog and extract, feature_table
  labels.py          questionnaire scores or refusals, and the safety flag count for audit
  qualification.py   the device qualification report
  intake.py          intake into a study store: verify, keep one copy of each session, quarantine, receipts
  qcreport.py        the QC report of a study store: counts and rates, never a label or a score
  studytools.py      the configuration tools: skeleton, export key pair, PIN record, check
  synthetic.py       participants with latent traits and scores, device models, sites, scenarios, cohorts
  evaluate/          plan (digests), screening, participants, metrics, report, reliability, norms, planning
  contract.py        regenerate and verify the shared contract
  cli.py             contract, evaluate, plan-digest, plan-size, intake, qc-report, config
```

Key decisions:

- **Library frame.** Millimetres, x to the right, y up. Readers convert units and flip y at the boundary. When the source scale is unknown, coordinates stay in source units, `Device.spatial_unit` is `"source"`, and features that need millimetres refuse with `unknown_scale`.
- **Readers** parse one source into a `Recording`. The journal reader applies matched corrections to the last value received and keeps a mask of unresolved estimates. The journal keeps the originals. The journal reader also keeps the session context from the header, the trial context, the Begin time, and the end reason. Questionnaire blocks and their responses stay in `Recording.questionnaires`. They never enter strokes.
- **QC.** `assess` is the only function that builds a `Segment`. A segment is a run inside one stroke with strictly increasing time, no step longer than `max_gap_s`, finite positions, and at least `min_segment_s` duration. A gap splits a stroke. Nothing bridges a gap. `Accepted` has a private constructor.
- **Timing QC for each pen state.** Duplicate times, decreasing times, or a low rate in a contact stroke reject the trial. The same defects in an in-air stroke refuse only the in-air features, for example `timing.in_air_s`. The contact features stay.
- **Refusals.** A failed requirement returns `Refusal(code, detail)`, never a silent number. Structural errors in a file raise `JournalError` or `FormatError`.
- **Kernels** work on one segment, or count across segments without joining them. Sample entropy sums template matches over all segments. Templates never cross a segment end, and self-matches are excluded. Coarse-graining stays inside each segment.
- **Pooling.** Each per-segment feature declares a pool rule and `min_values`. Fewer finite segment values give `Refusal(too_few_values)`.
- **Spectral pooling by Welch windows.** The spectral feature averages the Welch spectra of all segments, weighted by the number of windows in each segment, and then integrates the band. Its `min_values` counts Welch windows, not segments. A segment shorter than one window adds no window. The window length never shrinks.
- **Feature families.** Kinematic, timing, pressure, tilt and roll, spatial error, spectral band power, entropy, and micro-movement spikes with a Gamma fit (Torres method: speed peaks normalized by the peak plus the mean speed between its neighbouring minima; location fixed at 0; maximum likelihood; intervals from the observed information). `timing.initiation_latency_s` is the time of the first contact sample minus the time of the Begin tap. A trial without a Begin tap gives a refusal.
- **Feature table.** Practice trials and qualification sessions never enter `feature_table`. Each row has `administration`, `hand`, and `accommodations`, so that an analysis can stratify. A source without this context gives `unknown` and no accommodations.
- **Provenance.** A `FeatureTable` carries the library version, the QC policy, the preprocessing settings, the catalog digest, the source hashes, and the reader profiles. A reader profile is canonical JSON that names the reader and its unit conversions (for example the tablet resolution of an SVC file). Two tables from different reader settings therefore have different provenance.
- **Labels.** `labels.labels(recording)` scores each questionnaire block with the scoring rule in `contract/instruments.json`. A `sum` rule adds the scored items. A DASS-21 subscale is its sum times 2. One missing item in a subscale is prorated (sum times 7/6). More missing items than the rule allows give a refusal, never a score of zero. Unscored items, such as `difficulty`, never enter a score. Each label has the instrument, version, locale, administration, block time, and score or refusal. `labels.safety_flag_count` counts safety flags for audit. It is never a label.
- **Pen-only copy.** `pen_only(export, target)` writes a copy without questionnaire, response, and safety lines, and without `recovered.interruptedBlock`. It also ends at the last `trialEnded`: every later line is dropped, and `sessionClosed.t` is set to the time of that `trialEnded`. The time after the last task depends on whether an operator handled a safety flag, so keeping it would leak the label. It renumbers `seq`, recounts `sessionClosed.counts`, writes a new manifest, and writes `derivation.json` with the source session identifier and the SHA-256 of the source journal. The copy must pass I1 to I7 before the function returns. It refuses a session with a questionnaire or safety line before the last `trialEnded`, for example a `beforeTasks` block. The time gap around the dropped lines would show how long the block took.
- **Encrypted exports.** `read_journal(path, private_key=...)` reads an export whose manifest has an `encryption` block (section 17). The key is the study's raw 32-byte X25519 private key. The reader checks the ciphertext size and SHA-256, opens the ciphertext, checks the plaintext size, line count, and SHA-256, and then applies I1 to I7. A ciphertext that does not open gives `JournalError` with rule I5.
- **Qualification.** `qualification.qualify(recording)` accepts only a session with `purpose: qualification`. It reports the sample intervals inside contact strokes (median, 5th and 95th percentile, and the share of intervals longer than 30 ms), the jitter during the still hold (root mean square distance from the mean position of each stroke, in millimetres), the straight-line deviation (root mean square orthogonal distance from the total-least-squares line of each stroke, in millimetres), the scale source, and the planned and observed channels. A value that the data cannot support is a refusal.
- **Intake.** `intake(sources, store, *, private_key, keep_plaintext)` checks each export folder under the sources with `verify_export`: the manifest, the decryption, and rules I1 to I7. It copies the export first into `store/.partial/` and checks that copy, so the bytes that it checks are the bytes that it keeps. The store holds:
  - `sessions/<sessionID>/`: a verified export as received. An encrypted export stays encrypted. The decrypted `journal.jsonl` is kept beside it only with `keep_plaintext`.
  - `quarantine/<first 16 hex digits of the export digest>/`: an export that failed a check or that conflicts, with `reason.json`.
  - `damaged/<first 16 hex digits of the export digest>/`: a damaged package (section 17) as received, with its decrypted `journal.jsonl`, so that a reviewer can read the safety flags in it. Intake checks the manifest facts and decrypts the journal, but it does not fold it, and a damaged package never enters `sessions/`. Its receipt has the result `damaged`.
  - `receipts/<sessionID>.json`: every intake of that session, with the source folder, the export and journal SHA-256, encryption, `keyID`, the result, and the time.
  - `intake.jsonl`: every intake, in order.
- **Intake results.** A session with one journal is `accepted` into `sessions/`. The same journal again is a `duplicate`, and intake copies nothing. A journal is the same when its plaintext bytes are the same, so a copy that was encrypted again is a duplicate. A different journal for a session is a `conflict`: every version goes to `quarantine/`, and `sessions/` keeps none, because the store cannot know which one is correct. A failed check is `quarantined`. An absent or wrong study key raises `StudyKeyError`. Intake then refuses that export and writes nothing, so a second run with the correct key can take it.
- **Idempotent intake.** The log records each export by its source folder and the SHA-256 of its files. Intake skips an export that the log already records, so a second run on the same sources changes no byte in the store. A folder or a receipt file gets its final name only when it is complete. The log is append-only. A crash leaves at most a torn last log line, and the next run removes it. Intake never writes into a source folder, and it never reads its own store as a source.
- **QC report.** `qc_report(store, *, private_key)` reads every session in `store/sessions/`. It counts sessions by site, device asset, configuration digest, app version, administration, and purpose. It counts the measured trials of study sessions that `assess` accepts or refuses, with the refusal codes. These are the trials that `feature_table` uses. For each device asset and declared Pencil, it gives the sample rate from the intervals inside contact strokes (median, 5th and 95th percentile) and the latest qualification session in the store. It also gives the planned and observed channels, the rates of unresolved estimates and unmatched corrections, the interrupted trials and blocks, the recovered sessions, and the safety counts of the fold summary, for audit. `qc_markdown` writes two short tables. No label, score, response, feature value, or participant code enters the report.
- **ParkinsonHW.** `read_parkinsonhw` reads the public UCI files for reader and feature tests only. A row is `X;Y;Z;Pressure;GripAngle;Timestamp;Test ID`. Time is in milliseconds; the real files have a median sample interval of 7 ms. A positive pressure is contact, and zero pressure is in-air. Z is not a pen state: it is nonzero in single samples inside contact. No source gives a unit for X, Y, Z, or the grip angle, so positions stay in source units, and Z and the grip angle are not channels. Each test ID is a trial named `parkinsonhw-static-spiral`, `parkinsonhw-dynamic-spiral`, or `parkinsonhw-circular-motion`, from the dataset readme. A file is known only by the SHA-256 of its bytes. Class folders and class file name prefixes never decide a value, an order, or an error message, and no label is read.

## 7. Evaluation

The harness is `scriptkin.evaluate`. It accepts synthetic cohorts and cleared datasets only (`AGENTS.md`).

### 7.1 Inputs and the analysis plan

- `screening(table, labels, *, plan, seed, reliability=None) -> Screening | Refused`.
- `table` is a `FeatureTable`. Each row also has `site`, `age_band`, `visit`, `opened_at` (the session `openedAt`), and `qc` (the codes that rejected the trial; empty for an accepted trial). `read_feature_table` reads the CSV that `to_csv` writes.
- `labels` are `labels.Label` values. `write_labels_csv` and `read_labels_csv` give them a file form.
- `AnalysisPlan` holds the target (`instrument`, `version`, `scale`, and `cut`: a case has a score of at least `cut`), the feature columns (`task/feature`), the model (`logistic_l2` or `knn`, with a grid), the reliability gate, an optional normative model, the risk thresholds, the stated prevalence, the holdout (`participant`, `site`, `device`, or `temporal`), the missing-data rule (`impute_in_fold` or `complete_case`), the folds, repeats, inner folds, permutations (at least 10, so that the null check can fail), and bootstrap samples. `AnalysisPlan.from_json` refuses an unknown key.
- `plan_digest(plan)` and `freeze_features(table)` are the SHA-256 of canonical JSON. Every report records both digests and the seed.

### 7.2 Analysis set

- One row per participant. Each column is the median over accepted, non-practice attempts.
- The label of a participant is the target label of its earliest session in the table, by `openedAt`. A missing or refused label excludes the participant. It is never a negative.
- A participant with no accepted trial, or with no value in any planned column, is excluded. `complete_case` also excludes a participant with any missing column. `impute_in_fold` uses training-fold medians.
- The report counts the excluded participants by reason, the excluded cases and controls whose label is known, and the trials that QC rejected, by code.

### 7.3 Splits, search, and calibration

- `participant`: stratified folds with whole participants, with repeats. `site` and `device`: leave one site or device out; a participant seen at two sites or on two devices refuses the plan. `temporal`: participants in date order are cut into `folds` blocks; each block after the first is tested with a model fitted on the earlier blocks only.
- Inside each outer training set, a grouped stratified search picks the first grid value with the best inner AUC. A logistic model minimizes the mean log loss plus `|w|^2 / (2c)` on standardized features. The penalty is per observation, so a value chosen on an inner training set keeps its scale on the outer training set.
- A Platt calibrator is fitted on the inner out-of-fold scores of the chosen value and applied to the final model. Calibration never sees the outer test fold.
- **Leakage witness.** A `FitWitness` is the first step of every pipeline: search, final, calibration, and normative fits. It records the participants of each fit. The harness checks the exact invariant: no test participant is in any fit of its fold, and every fold has a witnessed final fit. A failure returns `Refused`.

### 7.4 AUC and the null check

- The AUC uses only case-control pairs inside one test fold, averaged over repeats. A pair from two folds compares two models whose intercepts carry their training prevalence. That prevalence moves against the test prevalence, so a pooled AUC falls below 0.5 when nothing is learnt.
- **Interval.** The start is a participant bootstrap, stratified by class, of fixed out-of-fold predictions. This bootstrap omits the variation from refitting. In the coverage study below, it covers 0.5 in only 89% of null cohorts with 1 repeat and 79% with 5 repeats. The harness widens the interval to at least `AUC ± t(0.975, P − 1) · SE_null`. `SE_null` is the standard deviation of the P null AUCs of the same plan. Under the null, the observed AUC and the null AUCs are exchangeable, so the widened interval covers 0.5 at the nominal rate or more. In the coverage study, it covers 0.5 in 99% of null cohorts for both designs. The interval is not a test of chance. Significance comes from the permutation p-value. The report gives `se_null` and `p_value` next to the AUC.
- **Null check.** Each permutation run permutes the labels, draws its own split seed, and runs the full plan. A shared split seed would condition the null on one fold assignment. The check needs an exchangeable null, so it permutes between all participants, or inside each held-out site or device for a site or device holdout, where every scored pair shares one group. It passes when the mean null AUC is inside `0.5 ± t(0.995, P − 1) · sd / √P`. A mean outside the band returns `Refused`.
- **Band spread.** `sd` is the larger of the standard deviation of the null AUCs and `sigma0`, for every P. `sigma0` is the null standard deviation that the fold sizes imply for one repeat of fixed untied scores. The null AUCs of a multi-repeat plan are skewed to the right (skewness 0.80 at 5 repeats in the coverage study). A sample of them with a low mean then often has a small spread, and a band from that spread alone refuses sound plans. In the coverage study, the spread alone refused 4 of 160 null cohorts with 1 repeat and 1 of 160 with 5 repeats. The harness band refused 3 of 160 and 0 of 160. The cost is a looser check with 5 repeats. There, the true null spread is 0.91 `sigma0`, so the band is about 10% wider than that spread needs. A check that refuses sound studies teaches users to ignore it, so the harness accepts the looser band.
- **p-value.** A second set of runs permutes the labels between participants inside each site. Its distribution gives the p-value of the observed AUC, so a result that only reflects site prevalence is not significant. With one site, or with a site holdout, the two sets are the same. The report gives both null distributions.
- **p-value resolution.** P permutations cannot give a p-value below `1 / (P + 1)`. `NullCheck.resolution` gives this value. When it exceeds 0.05 (P < 19), a caution states that the p-value cannot reach 0.05.

The figures in this section come from `python/studies/coverage.py`. The script runs `screening` on 160 null matrix cohorts, seeds 0 to 159. Each cohort has 120 participants at one site, three independent standard normal features, and labels that are cases with probability 0.3, independent of the features. Cohort i draws its data from `numpy.random.default_rng(i)`, and `screening` gets seed i. The plan has 5 folds, 3 inner folds, a logistic model with the grid (10, 100), 20 permutations, and 300 bootstrap draws. The study runs the plan with 1 repeat and with 5 repeats on the same cohorts.

| Measure | 1 repeat | 5 repeats |
|---|---|---|
| Refused by the harness band | 3 of 160 | 0 of 160 |
| Refused by a band from the null spread alone | 4 of 160 | 1 of 160 |
| Bootstrap interval covers 0.5 | 89% (140 of 157) | 79% (127 of 160) |
| Widened interval covers 0.5 | 99% (156 of 157) | 99% (158 of 160) |
| Permutation p-value 0.05 or less | 3% (5 of 157) | 2% (3 of 160) |
| Skewness of the null AUCs | 0.42 | 0.80 |
| True null spread (standard deviation of the observed AUC) over `sigma0` | 1.28 | 0.91 |

Coverage, the p-value rate, the skewness, and the spread use the cohorts that the harness does not refuse. To reproduce the table, run this command from the repository root. A run takes 5 to 7 minutes on an 8-core Apple silicon Mac. The script also writes `studies/coverage.json` into the build folder of `./check`.

```sh
UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen python -I python/studies/coverage.py
```

### 7.5 Report

`Screening` holds, after the witness and the null check pass:

- AUC with its widened interval, `se_null`, and the permutation p-value; calibration intercept (logit offset model), slope, a ten-bin curve, and Brier score.
- Sensitivity and specificity with Wilson intervals at each plan threshold, and PPV and NPV at the stated prevalence by Bayes' rule. The PPV and NPV bounds combine the matching sensitivity and specificity bounds, so they are conservative. Before the thresholds apply, each out-of-fold risk moves to the stated prevalence by the prior-shift adjustment, `logit p' = logit p + logit π − logit π_f`. `π_f` is the training prevalence of the fold that scored the risk, because the calibrator of that fold learnt `π_f`. With repeats, the shifted risks are averaged over repeats. A pooled prevalence would move the risks of different folds by wrong amounts. Under drifting prevalence, for example in a temporal holdout, that error gives the thresholds spurious discrimination. When the sample prevalence differs from the stated prevalence, a caution states the assumption of the shift.
- Net benefit for the model, treat-all, and treat-none at thresholds 0.01 to 0.50 and the plan thresholds.
- Demographic-only (age band, administration), device-only, and site-only baselines on the same folds. Each baseline has its own permutation null, on the same permuted labels and splits as the model, and its interval follows the same rule. A baseline gives a caution that it can explain the result when its AUC is more than `t(0.975, P − 1) · SE_null` above 0.5 and reaches the model's lower bound. The bootstrap low bound is not the test, because it ignores the refit variance. A baseline whose design is the same for every participant ties every pair, so its null AUCs are all 0.5 and need no runs.
- Subgroup results by age band, administration, device, and site, with bootstrap intervals of fixed predictions that the null spread does not widen; QC attrition; the folds with the sites, devices, and dates that their fits saw, the training prevalence, and the count of test participants outside the range of the fold's norms; the reliability gate result of each column; cautions; and the TRIPOD+AI items. Items that the harness knows have content. The others are author-supplied.

### 7.6 Reliability and the gate

- `reliability(table, between="device" | "session")` gives an `Agreement` for each `task/feature` column. An occasion is a device model or a visit. Devices sort by name and visits by number, so visit 10 comes after visit 2. A participant counts only with a value on every occasion.
- `Agreement` holds ICC(A,1) (two-way random effects, absolute agreement, one measurement) with the McGraw and Wong F interval, Bland-Altman bias and 95% limits for each pair of occasions, the agreement SEM `sqrt(occasion variance + residual variance)`, MDC95 `1.96 · √2 · SEM`, the between-participant SD, and `n`.
- `reliability_gate(agreement, criteria)` gives `pass`, `fail`, or `not_established` with the reason. A feature passes when the ICC lower bound reaches `min_icc_low` and every bias is at most `max_bias_sd` between-participant SDs, with at least `min_participants`.
- `screening` uses only the columns that the gate passes. Without reliability data, the report states that reliability is not established and lists the columns used.

### 7.7 Normative models

- `fit_norms(rows, covariates)` fits, for each feature, a location and a log variance that are linear in the same design: an intercept, a restricted cubic spline of `age` in years when given, and indicators for `age_band`, `hand`, or `administration`.
- `centiles(norms, rows)` gives a centile for each feature, or a refusal. A row outside the fitted age range or with an unseen level gives `out_of_range`.
- `plan.norms` replaces each column by its deviation from norms fitted inside each training fold. The report then states that a deviation is not a diagnosis.
- **Reference sample.** The norms are fitted without labels on all participants of the training fold, cases included, as a general-population reference. Cases pull the location toward themselves and widen the scale, so their deviations shrink toward zero and the AUC tends to fall. The fit does not use labels, so it cannot raise the AUC by leakage. TRIPOD+AI item 12b states this choice.
- **Out-of-range test rows.** A test participant whose covariates are outside the range of the training-fold norms has no deviation. The training-fold median deviation replaces it. Each fold record counts these participants in `out_of_range`, and a caution gives the total.

### 7.8 Study size

`evaluate.planning` gives, in closed form, the expected positives and negatives and the Wilson half-widths of sensitivity, specificity, PPV, and NPV for `N` enrolled participants at a stated prevalence, assumed accuracy, and QC attrition. It also gives the smallest `N` that reaches a target half-width for each measure. The smallest count is the positive root of a quadratic.

### 7.9 Command line

`scriptkin evaluate --plan plan.json --features table.csv --labels labels.csv [--reliability retest.csv] [--seed N] [--out report.json]` and `scriptkin plan-size --prevalence P --sensitivity SE --specificity SP [--attrition A] [--enrolled N] [--half-width W]` write JSON reports. `scriptkin plan-digest plan.json` prints the digest of a plan, to register it before the labels join. With `--expect-plan-digest HEX`, `scriptkin evaluate` refuses a plan whose digest differs, before it reads the features and the labels.

## 8. Synthetic cohorts

`scriptkin.synthetic.cohort(n, *, seed, scenario, outcome, devices, sites, tasks, visits, crossed, session_noise)` returns a `Cohort` with the journals, the recordings, the truth, and the oracle AUC. `Cohort.labels` scores the `response` lines with `labels.labels`.

- **Traits.** Each participant has log-normal traits: speed, tremor amplitude and frequency, pause rate, pressure level and variability, and the Gamma shape and scale of micro-movement bumps. A latent depression score `d` is standard normal. `outcome.effect` maps a trait to its loading on `d`, in standard deviations of the log trait. The oracle risk score is `Σ β_i u_i` over the standardized log traits `u`, which orders participants as `E[d | traits]` does. The oracle AUC is its AUC against the case labels.
- **Labels.** `d` drives the PHQ-9 and GAD-7 items through a graded response model. The latent mean is set by Gauss-Hermite quadrature so that the PHQ-9 total reaches `outcome.threshold` at the stated prevalence. A case is the first-visit PHQ-9 total of at least the threshold. A skipped item makes the label refuse.
- **Strokes.** The pen follows each stimulus polyline. The speed is a chain of half-overlapping minimum-jerk bells of 160 ms whose amplitudes add Gamma bumps. Tremor moves the pen at right angles to the path. Pauses come at a Poisson rate and lift the pen. A copy is drawn below the stimulus with a small rotation and size change. `clock` and `sentence` have their own polylines.
- **Devices.** A `DeviceModel` sets the device model text, the declared Pencil, the sample rate, the timestamp jitter, the drop rate, the position noise, the planned channels, the pressure curve `force = max · pressure^γ`, the share of samples whose force arrives later in a `correction`, the correction delay, hover lines between contacts, and a scale bias. `IPAD_PRO_PENCIL_PRO`, `IPAD_PRO_PENCIL_2`, and `IPAD_AIR_PENCIL_USBC` are defined.
- **Sites and sessions.** A `Site` multiplies speed and pressure. Speed is 0.9 times as large for age band 13-17. Each session adds a small change to every log trait (`session_noise`). Session dates follow enrolment order; visits are 14 days apart.
- **Scenarios.** `null`: no effect. `signal`: the effect applies. `device_confounded`: no effect; cases use the last device and controls the first, each with probability 0.85. `fingerprint`: no effect, a stable participant quirk on every trait, and two visits. `missingness`: the effect applies; people with higher `d` decline more tasks and skip more items.
- **Journals.** `contract.JournalBuilder` writes every line. A session has a handoff, the tasks, PHQ-9 and GAD-7 blocks, an operator alert and action when item 9 is flagged, the crisis list, and the close. The recordings come from the same replay that `read_journal` uses. `Cohort.write` writes export folders with manifests.
- **Seeds.** One seed gives separate streams for the population, the answers, and each participant's drawing.

## 9. Repository and checks

```
SCRIPT/
  check                       the single check command
  README.md  AGENTS.md
  docs/                       the design, the study documents, and the device test
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
    pyproject.toml  uv.lock  src/scriptkin/  tests/  studies/
```

`./check` stops at the first failure. It runs the contract check, the `ScriptCore` tests on the Mac, the app tests on the "iPad Pro 11-inch (M5)" simulator, and then the Python checks. The Python checks are the lint, the type check, and the tests. The tests read the exports of the Swift and app tests. The `core` and `app` sections first delete the exports of an earlier run. When the `app` section ran, the Python tests fail if the app exports are absent. Build output goes to `~/Library/Caches/SCRIPT/<checkout>-<hash>/`, outside the repository, so that the repository can sit on an exFAT drive.

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
- `operatorPin`: `{salt, iterations, pbkdf2Sha256}` for PBKDF2-HMAC-SHA256 of the UTF-8 PIN with the base64 salt (at least 16 bytes), at least 600,000 iterations, and a 32-byte key in hex; or `null` for the demo configuration. A short PIN with a fast hash can be found by trying every PIN, so the slow derivation is required.
- `selfAdministered`: `{resumeWithinHours}`, a positive number of hours after `openedAt`. Without this member the value is 24. An interrupted self-administered session can resume only inside this window, and only after the participant types their code again, which the app compares with the code in `sessionOpened`. After the window, the app closes the session at the next launch or resume: the journal ends with `recovered` and then `sessionClosed`, with no closing crisis list, and Home offers it for export. When a self-administered block has a safety item, the window must not be longer than `safety.remoteReview.imminentReviewWithinHours`, because the flags of an interrupted session wait on the device until it closes.

The app imports a configuration through Files. It checks the schema and the rules in this section, shows the study, version, and digest to the operator, and refuses a configuration that fails a check. Every session records `study.configSHA256`, so an analyst can prove which configuration produced it. A configuration is frozen for the life of a session.

## 15. Questionnaires and safety

### 15.1 Instruments

`contract/instruments.json` holds each instrument: its identifier, version, licence status with the source, validated age range, recall text, response options, items, safety items, and scoring rule. The `scriptkin.instruments` module generates it. The app shows the exact item text and records item identifiers and response values. The app never computes or shows a score.

| id | items | licence | validated ages | safety items |
|---|---|---|---|---|
| `phq9` | 9 | public domain (PHQ manual) | 13-17 (Richardson 2010), 18+ | item 9 |
| `phq8` | 8 | public domain (PHQ manual) | 13+: items 1-8 of the PHQ-9, which is validated at 13-17; a separate adolescent validation of the PHQ-8 was not found | none |
| `gad7` | 7 | public domain (PHQ manual) | 12-17 (exploratory), 15-24, 18+ | none |
| `dass21` | 21 | public domain; enrolled participants only, results to the researcher only, no modification | 14+ | none |

The modified-for-teens PHQ-A form needs written permission from its owners and is not included. A study can add an instrument only with a licence reference in its configuration.

### 15.2 Supervised sessions

- The default blocks are `phq9` and `gad7` after the tasks, for both age bands.
- A skipped safety item counts as a flag, as in the NIMH ASQ pathway. A safety item that its block never reached also counts as a flag: the block was declined, interrupted, or ended by the operator before the item (section 3.3).
- When a block ends, the app always shows the same handoff screen: "Please give the iPad back to the researcher." The flow is identical with and without a flag, so the participant cannot infer the result.
- The operator screen then shows a neutral alert for each flag. The operator must record one action code before the session can close. The default codes are `clinicianAssessment`, `referred`, `emergencyServices`, `guardianContacted`, and `participantDeclinedHelp`.
- Every participant sees the configured crisis list at the end of a session that had a questionnaire block.

### 15.3 Self-administered sessions

- The default blocks are `phq8` and `gad7`. They contain no safety item, because no one can respond in real time.
- A configuration may add a block with a safety item to a self-administered session only with a `remoteReview` block: the reviewer role, the review interval (at most 1 weekday; at most 24 hours for imminent risk), the export interval that makes this review possible, and the version of the monitoring disclosure that the participant saw at consent. The app refuses a configuration that breaks this rule.
- In such a session, the app shows the crisis list at once when a safety item is flagged, and again at the end. It records each display. A flag for an unreached item also shows the list at once; after a recovery, the list comes first when the session resumes.
- The app never tells the participant that a response was flagged.
- No operator responds to a flag in such a session. When an operator ends the session early, Review marks each flag "Reviewed by the study reviewer after export", asks for no action code, and allows the support list and the close, so the journal can be exported for the reviewer.
- An interrupted session resumes only for the participant who types its code, and only inside `selfAdministered.resumeWithinHours` (section 14).

### 15.4 Consent attestations

The site keeps identities, signed forms, and guardian contact details. The journal holds only attestations:

- `consent {model, attestedBy {code, role}, attestedAt, documents [{id, version}]}`. `model` is `adult`, `guardianAndAssent`, or `waiver` (with an approval reference). For `13-17`, the default model is `guardianAndAssent`.
- `ethics {approvalID, protocolVersion, safetyProtocolVersion, monitoringDisclosureVersion?}`.

The operator gives the attestation in supervised sessions. In self-administered sessions, the enrolment record of the study supplies it, and `attestedBy.role` is `enrolment`.

## 16. Device qualification

A qualification session has `purpose: qualification` and a fixed task set: a reference-length check with a steel rule or an ID-1 card, a 10-second still hold, and a straight-line stroke along a guide. Channel presence comes from the samples of these three tasks. It writes an ordinary journal. `scriptkin` computes the sample-interval distribution, gaps, jitter, scale error, and channel presence from it. The app records a qualification session with the participant code `QUAL` and an operator attestation without documents. A study session records the identifier of the last qualification session on its device and Pencil. "Can record" differs from "qualified for comparison"; only the analysis plan decides the second.

## 17. Privacy and withdrawal

- Journals and exports use iOS file protection `completeUntilFirstUserAuthentication`.
- Research data never enter a device backup. The app sets `isExcludedFromBackup` on `Sessions/`, `Staging/`, `Exports/`, `Damaged/`, the deletion log, and the export index. So no journal, export, or log reaches iCloud or a computer backup. Exports leave the device only by a person's action: through Files, or through file sharing in Finder on a connected Mac. The device secret for the deletion log is a `ThisDeviceOnly` Keychain item, so the log is checkable only on the device that wrote it.
- When the configuration has an export key, the export is encrypted to the study's public key with HPKE (RFC 9180, base mode, DHKEM(X25519, HKDF-SHA256), HKDF-SHA256, ChaCha20-Poly1305). This is CryptoKit's `HPKE.Ciphersuite.Curve25519_SHA256_ChachaPoly`. The export folder then holds `journal.jsonl.hpke` in place of `journal.jsonl`. The `info` parameter is the UTF-8 text `SCRIPT export 1.0`, and the associated data is the UTF-8 session identifier. The manifest gains this block:

  ```json
  "encryption": {"scheme": "hpke-base", "suite": "X25519-HKDF-SHA256-ChaCha20Poly1305",
                 "keyID": "...", "encapsulatedKey": "<base64>",
                 "plaintext": {"bytes": 18234112, "lines": 131207, "sha256": "..."},
                 "ciphertext": {"bytes": 18234128, "sha256": "..."}}
  ```

  The `journal` block of the manifest is then absent. `keyID` is the lowercase hex SHA-256 of the raw 32-byte X25519 public key; a configuration may state it, and then it must match. `scriptkin` refuses a private key whose public key does not give the manifest `keyID`. A plaintext export has `"demo": true` in its manifest; any configuration with an export key encrypts. `scriptkin` decrypts with the study's private key and then applies the normal integrity rules to the plaintext.
- **Damaged packages.** A journal in `Damaged/` can hold safety flags, so the operator action "Export damaged journal" on Home publishes it under the same export policy: the policy of the configuration that its `sessionOpened` line names, or of the active configuration when that line does not decode. A configuration with an export key encrypts. The folder is `Exports/SCRIPT_DAMAGED_<file name of the damaged journal, lowercase>/`, and the file name is the session identifier. The journal is the bytes as found. The manifest has the normal shape and one more member, after `sessionID`:

  ```json
  "damaged": {"line": 3}
  ```

  `line` is the 0-based index of the first line that does not decode or fold. The facts (`bytes`, `lines`, `sha256`) describe the bytes only: `lines` counts LF bytes, and the manifest claims no fold summary. The app verifies the staged package against the source bytes, adds its name to the export index when line 0 names a participant, and removes the source only after the rename, as `publish` does. The reader of exports refuses a damaged package, because its journal fails the integrity rules; `scriptkin.intake` files it under `store/damaged/` (section 6).
- Withdrawal: the operator enters a participant code two times and confirms. The app then deletes every journal, staging folder, export, damaged journal, and damaged package for that code on the device. Before it deletes, it appends one line to `Library/Application Support/Deletions.jsonl`: `{at, participantHMAC, sessions}`. `participantHMAC` is HMAC-SHA256 of the code with a 32-byte secret that the app creates at first launch and keeps in the Keychain. A plain hash of a short code can be reversed by trying every code; the keyed hash cannot without the device. A site confirms a deletion on the device by entering the code, which the app checks against the log. Copies outside the device are the site's duty; `docs/DATA_GOVERNANCE.md` gives the procedure.
- Pseudonymous data remain personal data under the GDPR. A participant code alone does not meet HIPAA de-identification.

## 18. Intended use

SCRIPT is a research platform. It records drawing movement and reference questionnaire responses in supervised and self-administered sessions with people aged 13 to 24, and it tests whether movement features relate to symptom scores. It does not diagnose, screen, triage, or advise. It shows no score or interpretation to anyone. Any screening claim needs a separate intended-use statement, a validation study in the intended population and setting, and the applicable regulatory review.

