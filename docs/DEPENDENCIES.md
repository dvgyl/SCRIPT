# SCRIPT third-party software and data

This list names the third-party software that SCRIPT uses, the version of each package, its licence, and its role. It also describes the public dataset that the reader tests use. `python/uv.lock` locks the Python versions. The licence names come from the package metadata in the build environment that `./check` makes. The list was made on 3 November 2026 from Python 3.13 on macOS (Apple silicon).

Check a licence against the package's own licence files before you distribute SCRIPT. A licence name in metadata is a summary, and it can be incomplete.

## Regenerate this list

Run `./check contract` first, so that the build environment exists. Then run these commands from the repository root to see the locked versions and the licence metadata.

```sh
export UV_PROJECT_ENVIRONMENT="$HOME/Library/Caches/SCRIPT/$(basename "$PWD")-$(printf %s "$PWD" | shasum | cut -c1-8)/venv"
uv tree --project python --frozen
uv run --project python --frozen python - <<'EOF'
from importlib.metadata import distributions

for dist in sorted(distributions(), key=lambda d: d.name.lower()):
	meta = dist.metadata
	classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
	licence = meta.get("License-Expression") or classifiers or (meta.get("License") or "").splitlines()[:1]
	print(dist.name, dist.version, licence, sep="\t")
EOF
```

The second command reads the `License-Expression` field first. When a package has no such field, it reads the licence classifiers, and then the `License` field.

## Controls on the dependencies

A script checks that every file and test in this document exists. It also checks that each file in an "Imported by" column imports the package or framework of its row. The statuses have the same meaning as in the [hazard log](HAZARDS.md#how-to-read-and-update-this-log).

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| `./check` installs the Python versions that `python/uv.lock` names. | Implemented without a test | `check` | No test compares the installed versions with the lock file. `./check` runs uv with `--frozen`, which never changes the lock file. |
| `ScriptCore` uses system frameworks only. | Implemented without a test | `app/Package.swift` | No test reads the package dependencies. `app/Package.swift` declares none. |
| A licence check of each package against its licence files. | Not implemented | None | None |
| A check of the locked packages for known vulnerabilities. | Not implemented | None | None |

## Python runtime

| Software | Version | Licence | Role |
| --- | --- | --- | --- |
| CPython | 3.13.15 in the build environment. `python/pyproject.toml` requires 3.12 or later. | Python Software Foundation License Version 2, from `LICENSE.txt` of the installed CPython | Runs scriptkin and its tests. uv installs it. |

## Python runtime dependencies

scriptkin declares five direct dependencies in `python/pyproject.toml`. In the tables, HPKE is Hybrid Public Key Encryption, and HKDF is the key derivation function based on HMAC (hash-based message authentication code).

| Package | Locked version | Licence | Role | Imported by | Test that uses it |
| --- | --- | --- | --- | --- | --- |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Arrays for samples, strokes, and features | `python/src/scriptkin/recording.py`, `python/src/scriptkin/signal.py`, and `python/src/scriptkin/features.py` | `test_fold_matches_independent_builder` in `python/tests/test_journal.py` |
| scipy | 1.18.1 | BSD License (classifier). Its `LICENSE.txt` also lists GCC (GNU Compiler Collection) runtime libraries under GPL-3.0-or-later WITH GCC-exception-3.1, and libquadmath under LGPL-2.1-or-later. | Welch spectra, peak detection, smoothing filters, the Gamma fit, nearest-neighbour search, and the distributions of the harness | `python/src/scriptkin/signal.py`, `python/src/scriptkin/features.py`, and `python/src/scriptkin/evaluate/screening.py` | `test_welch_weights_segment_spectra_by_window_count` in `python/tests/test_signal.py` |
| scikit-learn | 1.9.1 | BSD-3-Clause. Its `COPYING` file also covers the bundled libomp runtime library under the Apache License v2.0 with LLVM Exceptions. | The logistic and nearest-neighbour models and the stratified group folds of the evaluation harness | `python/src/scriptkin/evaluate/screening.py` | `test_witness_refuses_an_ungrouped_splitter` in `python/tests/test_screening.py` |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | X25519, HKDF, and ChaCha20-Poly1305 for the HPKE decryption, and the study key pair | `python/src/scriptkin/hpke.py` and `python/src/scriptkin/studytools.py` | `test_rfc9180_vector_seals_and_opens` in `python/tests/test_export.py` |
| jsonschema | 4.26.0 | MIT | `scriptkin config check` checks a configuration against `contract/config.schema.json`. The tests also check journal lines against `contract/journal.schema.json`. | `python/src/scriptkin/studytools.py` | `test_config_check_lists_every_schema_problem_with_its_location` in `python/tests/test_studytools.py` |

The direct dependencies need these packages:

| Package | Locked version | Licence | Required by | Role |
| --- | --- | --- | --- | --- |
| cffi | 2.1.1 | MIT-0 | cryptography, except on PyPy | Calls from Python into C libraries |
| pycparser | 3.12 | BSD-3-Clause | cffi, except on PyPy | C parser for cffi |
| attrs | 26.1.0 | MIT | jsonschema, referencing | Class helpers |
| jsonschema-specifications | 2025.9.1 | MIT | jsonschema | The JSON Schema meta-schemas |
| referencing | 0.37.0 | MIT | jsonschema, jsonschema-specifications | Resolves schema references |
| rpds-py | 2026.9.1 | MIT | jsonschema, referencing | Persistent data structures |
| typing-extensions | 4.16.0 | PSF-2.0 | referencing on Python 3.12, and pyright | Backports of typing features |
| joblib | 1.6.0 | BSD-3-Clause | scikit-learn | Parallel jobs and caching for scikit-learn |
| cloudpickle | 3.1.2 | BSD-3-Clause (`License` field) | joblib | Serialization of Python objects for joblib |
| narwhals | 2.28.0 | MIT | scikit-learn | Data frame compatibility layer for scikit-learn |
| threadpoolctl | 3.7.0 | BSD-3-Clause | scikit-learn | Control of native thread pools |

## Python development dependencies

The `dev` group in `python/pyproject.toml` declares three tools. `./check` uses them. A scriptkin user does not need them.

| Package | Locked version | Licence | Required by | Role |
| --- | --- | --- | --- | --- |
| pytest | 9.2.0 | MIT | scriptkin (dev) | Runs the Python tests |
| ruff | 0.17.3 | MIT | scriptkin (dev) | Lint and format check |
| pyright | 1.1.414 | MIT (`License` field) | scriptkin (dev) | Static type check. It runs a bundled copy of the pyright JavaScript package with Node.js. |
| iniconfig | 2.3.1 | MIT | pytest | Reads configuration files |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | pytest | Version parsing |
| pluggy | 1.7.0 | MIT | pytest | Plugin system |
| pygments | 2.21.0 | BSD-2-Clause | pytest | Syntax colour in test output |
| colorama | 0.4.6 | Not verified. It is not installed on macOS. | pytest, on Windows only | Terminal colour on Windows |
| nodeenv | 1.12.0 | BSD License (classifier) | pyright | Installs Node.js when no `node` command is on the path |

## Build tools that the lock file does not pin

| Software | Version | Licence | Role |
| --- | --- | --- | --- |
| hatchling | 1.26 or later, from `python/pyproject.toml`. The uv cache holds 1.32.4 and 1.33.0. | MIT for both cached versions | Build backend for the editable scriptkin package |
| Node.js | v22.23.2 was on the path of the build machine | Not in the package metadata | Runs pyright. If no `node` command is on the path, nodeenv downloads a Node.js version that the lock file does not pin. |

## Apple frameworks

The Apple frameworks are part of the iOS and macOS SDKs (software development kits) in Xcode. Apple's licence for Xcode and its SDKs covers them. Package metadata does not give their licence names. `ScriptCore` and the app need iOS 18 or later. `ScriptCore` also builds for macOS 15 or later, so that its tests run on a Mac.

| Framework | Imported by | Role |
| --- | --- | --- |
| Foundation | `app/Sources/ScriptCore/Storage.swift`, every other `ScriptCore` file, and the app store | Files, data, URLs, and dates |
| CryptoKit | `app/Sources/ScriptCore/Storage.swift`, `app/Sources/ScriptCore/Study.swift`, and `app/Sources/ScriptCore/Journal.swift` | HPKE export encryption, HMAC-SHA256 for the deletion log and the export index, and SHA-256 (Secure Hash Algorithm, 256-bit) digests |
| CommonCrypto | `app/Sources/ScriptCore/Study.swift` | PBKDF2 (Password-Based Key Derivation Function 2) for the operator PIN |
| Security | `app/Sources/ScriptCore/Storage.swift` | The Keychain item that holds the device secret of the deletion log |
| Synchronization | `app/Sources/ScriptCore/Storage.swift` | `Mutex` for the state of the journal writer |
| SwiftUI | `app/ScriptApp/ScriptApp.swift`, `app/ScriptApp/HomeScreen.swift`, and the other screen files | The app entry point, the window, and every screen |
| UIKit | `app/ScriptApp/CaptureView.swift`, `app/ScriptApp/Environment.swift`, and `app/ScriptApp/SessionStore.swift` | The capture view, the device facts, and the background task at the scene change |
| Observation | `app/ScriptApp/SessionStore.swift` | The observable session store |
| os | `app/ScriptApp/SessionStore.swift` | `Logger` for error lines that name no participant |
| UniformTypeIdentifiers | `app/ScriptApp/HomeScreen.swift` | The JSON file type for the configuration import |
| Testing | `app/Tests/ScriptCoreTests/StorageTests.swift`, the other `ScriptCore` tests, and `app/ScriptAppTests/StoreTests.swift` | The Swift test framework for `swift test` and the app unit tests |
| XCTest | `app/ScriptAppUITests/SessionFlowTests.swift` | The UI tests on the simulator |

`ScriptCore` uses no UIKit, so its tests run on a Mac.

## Tools that `./check` uses

`./check` runs these tools. The versions are the versions on the build machine when this list was made. The repository does not pin them.

| Tool | Version | Role in `./check` |
| --- | --- | --- |
| bash | The system shell | Runs `./check` |
| uv | 0.12.3 | Syncs the Python environment from `python/uv.lock` and runs each Python tool |
| scriptkin | 0.1.0 | `scriptkin contract verify` checks that the generated contract files match the generator |
| ruff | 0.17.3 | `ruff check` and `ruff format --check` |
| pyright | 1.1.414 | Strict type check of `python/src`, `python/tests`, and `python/studies` |
| pytest | 9.2.0 | Python tests |
| Swift | 6.4.2, from Xcode | `swift test` runs the `ScriptCore` tests on the Mac |
| Xcode | 27.2 (build 27B52) | `xcodebuild` builds and tests the app on the iOS simulator "iPad Pro 11-inch (M5)" |
| xcstringstool | From Xcode | Checks that every UI string is in `app/ScriptApp/Localizable.xcstrings` |
| shasum | The system command | Makes the name of the build folder from the repository path |

## Public test data: ParkinsonHW

The reader tests use one public dataset: the UCI (University of California, Irvine) ParkinsonHW spiral drawings ([dataset 395](https://archive.ics.uci.edu/dataset/395/)). Its licence is CC BY 4.0. The reader also follows the acquisition description of [dataset 358](https://archive.ics.uci.edu/dataset/358/). `scriptkin.formats.parkinsonhw` reads it, and [the design](DESIGN.md#6-python-shape) describes the reader.

The data stay outside the repository. `./check python` sets `SCRIPT_PARKINSONHW` when the unpacked folder exists, and the tests in `python/tests/test_parkinsonhw_real.py` read it. Without the folder, those tests skip. Use the data to test readers and features. The first paper may also use the labels for one example: a participant-wise model with and without the measured device difference, with no clinical claim (owner decision, 3 November 2026; `docs/PAPER_PLAN.md` section 2.4). Do not train or evaluate any other model on them.

The archive that the tests used has the SHA-256 digest `4181243d64c29382a0035a9bca3ebcf446079b65c70a0b343fe7975372dc92e6`. These facts were measured on that archive on 3 November 2026 with scriptkin 0.1.0. The archive has 77 data files and 202 trials: 76 of test 0, 72 of test 1, and 54 of test 2.

- **Duplicated trials.** 11 of the 202 trials have the same rows as a trial in another file. They form 3 groups: one test-2 trial in 7 files, one test-2 trial in 2 files, and one test-1 trial in 2 files. No whole file is a copy of another. scriptkin does not remove the copies.
- **Backward time steps.** 14 trials have a timestamp that is lower than the one before it, 24 steps in all. 2 steps are inside contact strokes, so quality control rejects those 2 trials with `nonmonotonic_times`. 3 steps are inside in-air strokes, which refuse only the in-air features. The other 19 steps fall between strokes.
- **Unknown coordinate scale.** No source gives a unit for X, Y, Z, or the grip angle. X runs from −5 to 1007 and Y from −15 to 569. The reader keeps positions in source units, so the features in millimetres refuse with `unknown_scale`.
- **Test-2 naming conflict.** The readme in the archive calls test 2 a circular motion test, in which the person draws circles around a red point. The page of dataset 358 calls it a stability test on a point, held without touching the screen. 18 of the 54 test-2 trials have no row with a positive pressure. The reader names the trial `parkinsonhw-circular-motion`, from the readme. Do not treat test 2 as one task without a check of each trial.

An analysis must count each duplicated trial once. With duplicates counted once, the archive has 194 distinct trials. This code keeps the first trial of each group, in the order that `load_parkinsonhw` returns them:

```python
import hashlib

from scriptkin import load_parkinsonhw


def trial_digest(trial):
    h = hashlib.sha256()
    for stroke in trial.strokes:
        h.update(stroke.t.tobytes())
        h.update(stroke.xy.tobytes())
        for channel in sorted(stroke.channels):
            h.update(stroke.channels[channel].tobytes())
    return h.hexdigest()


seen = set()
distinct = []
for trial in (t for r in load_parkinsonhw("<unpacked folder>") for t in r.trials):
    digest = trial_digest(trial)
    if digest not in seen:
        seen.add(digest)
        distinct.append(trial)
```
