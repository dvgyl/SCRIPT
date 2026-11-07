# SCRIPT

SCRIPT is a research platform that records drawing movement and tests whether it can support mental health screening.

- **SCRIPT Capture** is an iPad app. A participant draws six short tasks with Apple Pencil, and the app records every pen sample.
- **scriptkin** is a Python library. It reads the recordings, checks their quality, and calculates movement features.
- **The evaluation harness** in scriptkin tests screening models and measurement reliability without data leakage.
- **SCRIPT Bench** is a Mac app for the robotic bench study. It records a Wacom tablet in the journal format of SCRIPT Capture, which has a bench mode for the iPad.
- **The bench analysis** in scriptkin tracks the camera reference of each run and analyses the bench study by its protocol.

SCRIPT is not a screening or diagnostic tool. The app never shows a result to the person who draws.

## Requirements

- macOS with Xcode 27.2 or later, and the iOS simulator "iPad Pro 11-inch (M5)".
- [uv](https://docs.astral.sh/uv/) and Python 3.12 or later.
- For real recordings: an iPad Pro (M4 or M5) with Apple Pencil Pro. Other iPads with Apple Pencil also work and record fewer channels.
- For bench recordings: a Mac with macOS 15 or later, and a Wacom Intuos Pro Medium (2025) with the Pro Pen 3. The Mac needs the Wacom driver.

## Check everything

```sh
./check
```

This runs the contract check, the Swift core tests, and the SCRIPT Bench tests on the Mac. Then it runs the app unit and UI tests on the simulator and the Python checks. The Python checks are the lint, the type check, and the tests. The Python tests read the exports that the Swift, bench, and app tests write, so `bench` and `app` run before `python`. To run one part, name it:

```sh
./check python
```

The sections are `contract`, `core`, `bench`, `app`, and `python`, and `./check` with no argument runs them in this order. For example, `./check bench` runs the tests of the Mac target `ScriptBench` and checks its String Catalog. Build output goes to `~/Library/Caches/SCRIPT/`, outside the repository, because the repository can sit on an exFAT drive.

## Analyse the bench study

The bench protocol, `docs/BENCH_PROTOCOL.md`, defines every step. The commands below run from the repository root in the `./check` environment. Section 15 of the protocol gives their files.

| Command | Step |
| --- | --- |
| `scriptkin bench lens`, `plane`, `marker-offset`, `clock`, `readout` | Calibrate the camera (protocol section 7) |
| `scriptkin bench start-point ROOT RUN_ID` | Print the commanded start point of a run for the tracker |
| `scriptkin bench track CLIP` | Track the marker in one clip and write its track files |
| `scriptkin bench pilot ROOT` | Check each device against its own command (section 6.5) |
| `scriptkin bench lock ROOT` | Write the lock file (section 14) |
| `scriptkin bench interim ROOT` | Print the variance components of blocks 1 to 6 (section 6.6) |
| `scriptkin bench analyse ROOT [--post-hoc]` | Analyse the study and write `bench/results/` |

The camera steps need OpenCV, which `./check` installs with the `camera` extra, and `ffprobe` for the frames file of each clip.

## Find your way

| Path | Contents |
| --- | --- |
| [docs/DESIGN.md](docs/DESIGN.md) | The design and the recording format |
| [contract/](contract/) | The journal schema, the task battery, and shared test fixtures |
| [app/](app/) | The iPad app, the Mac app SCRIPT Bench, and their `ScriptCore` Swift package |
| [python/](python/) | The scriptkin library and its tests |

## Documentation

| Document | Contents |
| --- | --- |
| [docs/DESIGN.md](docs/DESIGN.md) | The design, the recording format, and the safety rules |
| [docs/INTENDED_USE.md](docs/INTENDED_USE.md) | What SCRIPT is for, what it is not for, and which claims it supports |
| [docs/STUDY_GUIDE.md](docs/STUDY_GUIDE.md) | How a site prepares and runs a study, and takes exports into a study store |
| [docs/DATA_GOVERNANCE.md](docs/DATA_GOVERNANCE.md) | The data flow, the threats, the controls, the session register, withdrawal, and the law checklist |
| [docs/HAZARDS.md](docs/HAZARDS.md) | The hazard log, with the controls and the tests for each hazard |
| [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) | The third-party software, with versions and licences, and the public test data |
| [docs/DEVICE_TESTING.md](docs/DEVICE_TESTING.md) | How to run the apps on a real iPad and a real tablet, and check what they record |
| [docs/RELEASE.md](docs/RELEASE.md) | How to release SCRIPT Capture, and how to build SCRIPT Bench for the bench study |
| [docs/PAPER_PLAN.md](docs/PAPER_PLAN.md) | The first paper: the venue, its quality bar, the design, and the schedule |
| [docs/BENCH_PROTOCOL.md](docs/BENCH_PROTOCOL.md) | The robotic bench study: devices, programs, camera reference, limits, statistics, and the lock |
| [docs/CONSEQUENCE_PROTOCOL.md](docs/CONSEQUENCE_PROTOCOL.md) | The synthetic consequence experiment and its fixed analysis |
