# SCRIPT third-party software

This list names the third-party software that SCRIPT uses, the version of each package, its licence, and its role. `python/uv.lock` locks the Python versions. The licence names come from the package metadata in the build environment that `./check` makes. The list was made on 3 November 2026 from Python 3.13 on macOS (Apple silicon).

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

## Python runtime

| Software | Version | Licence | Role |
| --- | --- | --- | --- |
| CPython | 3.13.15 in the build environment. `python/pyproject.toml` requires 3.12 or later. | Python Software Foundation License Version 2, from `LICENSE.txt` of the installed CPython | Runs scriptkin and its tests. uv installs it. |

## Python runtime dependencies

scriptkin declares four direct dependencies in `python/pyproject.toml`. The other packages in this table are their dependencies.

In the table, HPKE is Hybrid Public Key Encryption, and HKDF is the key derivation function based on HMAC (hash-based message authentication code).

| Package | Locked version | Licence | Required by | Role |
| --- | --- | --- | --- | --- |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | scriptkin, scipy, scikit-learn | Arrays for samples, strokes, and features |
| scipy | 1.18.1 | BSD License (classifier). Its `LICENSE.txt` also lists GCC (GNU Compiler Collection) runtime libraries under GPL-3.0-or-later WITH GCC-exception-3.1, and libquadmath under LGPL-2.1-or-later. | scriptkin, scikit-learn | Welch spectra, peak detection, smoothing filters, the Gamma fit, and nearest-neighbour search in `python/src/scriptkin/signal.py` and `python/src/scriptkin/features.py` |
| scikit-learn | 1.9.1 | BSD-3-Clause. Its `COPYING` file also covers the bundled libomp runtime library under the Apache License v2.0 with LLVM Exceptions. | scriptkin | Declared for the evaluation harness. No module in `python/src/scriptkin/` imports it. |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | scriptkin | X25519, HKDF, and ChaCha20-Poly1305 for the HPKE decryption in `python/src/scriptkin/hpke.py` |
| cffi | 2.1.1 | MIT-0 | cryptography, except on PyPy | Calls from Python into C libraries |
| pycparser | 3.12 | BSD-3-Clause | cffi, except on PyPy | C parser for cffi |
| joblib | 1.6.0 | BSD-3-Clause | scikit-learn | Parallel jobs and caching for scikit-learn |
| cloudpickle | 3.1.2 | BSD-3-Clause (`License` field) | joblib | Serialization of Python objects for joblib |
| narwhals | 2.28.0 | MIT | scikit-learn | Data frame compatibility layer for scikit-learn |
| threadpoolctl | 3.7.0 | BSD-3-Clause | scikit-learn | Control of native thread pools |

## Python development dependencies

The `dev` group in `python/pyproject.toml` declares four tools. `./check` uses them. A scriptkin user does not need them.

| Package | Locked version | Licence | Required by | Role |
| --- | --- | --- | --- | --- |
| pytest | 9.2.0 | MIT | scriptkin (dev) | Runs the Python tests |
| ruff | 0.17.3 | MIT | scriptkin (dev) | Lint and format check |
| pyright | 1.1.414 | MIT (`License` field) | scriptkin (dev) | Static type check. It runs a bundled copy of the pyright JavaScript package with Node.js. |
| jsonschema | 4.26.0 | MIT | scriptkin (dev) | Checks journal lines and configurations against the JSON Schema files in `contract/` |
| attrs | 26.1.0 | MIT | jsonschema, referencing | Class helpers |
| jsonschema-specifications | 2025.9.1 | MIT | jsonschema | The JSON Schema meta-schemas |
| referencing | 0.37.0 | MIT | jsonschema, jsonschema-specifications | Resolves schema references |
| rpds-py | 2026.9.1 | MIT | jsonschema, referencing | Persistent data structures |
| iniconfig | 2.3.1 | MIT | pytest | Reads configuration files |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | pytest | Version parsing |
| pluggy | 1.7.0 | MIT | pytest | Plugin system |
| pygments | 2.21.0 | BSD-2-Clause | pytest | Syntax colour in test output |
| colorama | 0.4.6 | Not verified. It is not installed on macOS. | pytest, on Windows only | Terminal colour on Windows |
| nodeenv | 1.12.0 | BSD License (classifier) | pyright | Installs Node.js when no `node` command is on the path |
| typing-extensions | 4.16.0 | PSF-2.0 | pyright, and referencing on Python 3.12 | Backports of typing features |

## Build tools that the lock file does not pin

| Software | Version | Licence | Role |
| --- | --- | --- | --- |
| hatchling | 1.26 or later, from `python/pyproject.toml`. The uv cache holds 1.32.4 and 1.33.0. | MIT for both cached versions | Build backend for the editable scriptkin package |
| Node.js | v22.23.2 was on the path of the build machine | Not in the package metadata | Runs pyright. If no `node` command is on the path, nodeenv downloads a Node.js version that the lock file does not pin. |

## Apple frameworks

The Apple frameworks are part of the iOS and macOS SDKs (software development kits) in Xcode. Apple's licence for Xcode and its SDKs covers them. Package metadata does not give their licence names. `ScriptCore` and the app need iOS 18 or later. `ScriptCore` also builds for macOS 15 or later, so that its tests run on a Mac.

| Framework | Used by | Role |
| --- | --- | --- |
| Foundation | `ScriptCore` and its tests | Files, data, URLs, and dates |
| CryptoKit | `ScriptCore` and its tests | HPKE export encryption, HMAC-SHA256 for the deletion log, and SHA-256 (Secure Hash Algorithm, 256-bit) digests |
| CommonCrypto | `ScriptCore` (`app/Sources/ScriptCore/Study.swift`) | PBKDF2 (Password-Based Key Derivation Function 2) for the operator PIN |
| Security | `ScriptCore` (`app/Sources/ScriptCore/Storage.swift`) | The Keychain item that holds the device secret of the deletion log |
| Synchronization | `ScriptCore` (`app/Sources/ScriptCore/Storage.swift`) and its tests | `Mutex` for the state of the journal writer |
| SwiftUI | The app (`app/ScriptApp/ScriptApp.swift`) | The app entry point and window. The screens are design only. |
| Testing | `app/Tests/ScriptCoreTests/` | The Swift test framework for `swift test` |

`ScriptCore` uses no UIKit, so its tests run on a Mac.

## Tools that `./check` uses

`./check` runs these tools. The versions are the versions on the build machine when this list was made. The repository does not pin them.

| Tool | Version | Role in `./check` |
| --- | --- | --- |
| bash | The system shell | Runs `./check` |
| uv | 0.12.3 | Syncs the Python environment from `python/uv.lock` and runs each Python tool |
| scriptkin | 0.1.0 | `scriptkin contract verify` checks that the generated contract files match the generator |
| ruff | 0.17.3 | `ruff check` and `ruff format --check` |
| pyright | 1.1.414 | Strict type check of `python/src` and `python/tests` |
| pytest | 9.2.0 | Python tests |
| Swift | 6.4.2, from Xcode | `swift test` runs the `ScriptCore` tests on the Mac |
| Xcode | 27.2 (build 27B52) | `xcodebuild` builds the app for the iOS simulator "iPad Pro 11-inch (M5)" |
| shasum | The system command | Makes the name of the build folder from the repository path |
