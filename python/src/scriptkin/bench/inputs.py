"""The bench files that the operator writes (protocol sections 14 and 15), parsed at the boundary.

Layout. The root is the repository folder that holds `bench/`. The bench folder holds `runs.csv`,
`registry.json`, `BENCH_LOCK.json`, `programs/`, and `results/`. The study store of the registry holds
the journals in `sessions/<session id>/`, as `scriptkin intake` places them, and the tracks in
`camera/<device>-<mounting>/<run id>.track.csv`.

`bench/runs.csv` has one row per run with these columns in this order: `run_id`, `device`, `mounting`,
`block`, `position`, `program`, `run_index`, `session_id`, `trial_index`, `clip`, `status`,
`deviation_row`. The run id is `<device>-<mounting>-<program>-r<run index>`. The status is `planned`
for a run without a recording, `recorded` for a run with a recording, or `void` for a recording that a
deviation removes from the study. A void row names its row of `bench/DEVIATIONS.md` in `deviation_row`.
A planned row has an empty session id, trial index, and clip. The analysis decides whether a recorded
run passes (section 6.4); the operator does not.

`bench/registry.json` holds much more than the analysis reads (section 2). The analysis reads three
keys and ignores the rest. `studyStore` is the store path, absolute or relative to the root. `mountings`
is one record per mounting: `device`, `mounting`, `block`, `originMM` with `x` and `y`, and the optional
`qualificationSession`. `originMM` is the position of the stimulus origin of the programs in the marker
plane, which `scriptkin bench start-point` adds to the start point of a move list. The marker plane has
y up and the stimulus frame has y down. `constants` holds `bandPowerPrograms`, the line programs that
pass section 6.5, `excludedPrograms`, the main programs that section 4.2 step 7 dropped, and
`faultDwellGapsOnP`, true when the pilot showed that a still Pencil gives a `gaps` finding (section 5.3).

`bench/BENCH_LOCK.json` holds the protocol version, the lock commit, the `app.build` and
`app.sourceRevision` of each capture tool by device role, and the SHA-256 of each locked file by its path
relative to the root. `write_lock` hashes the files of section 14 step 5 and any extra file, such as the
translation script. `check_lock` refuses a changed or missing file, and `check_build` refuses a journal
whose build is not the build of its device.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from scriptkin import bench
from scriptkin.bench import MOUNTINGS, RUNS
from scriptkin.formats import FormatError
from scriptkin.formats.export import verify_export
from scriptkin.formats.journal import JournalError
from scriptkin.recording import BenchContext, BenchRole, Recording

LOCK_FORMAT = "script-bench-lock"
LOCK_VERSION = 1
LOCK_FILES = (
    "docs/BENCH_PROTOCOL.md",
    "bench/registry.json",
    "contract/bench_programs.json",
    "python/uv.lock",
    "python/studies/bench_programs.py",
    "python/studies/bench_limits.py",
    "python/studies/bench_precision.py",
    "bench/results/bench_precision.json",
)
LOCK_FOLDERS = ("bench/programs", "bench/order")
RUN_COLUMNS = (
    "run_id",
    "device",
    "mounting",
    "block",
    "position",
    "program",
    "run_index",
    "session_id",
    "trial_index",
    "clip",
    "status",
    "deviation_row",
)
ROLES: tuple[BenchRole, ...] = ("P", "W")


class InputError(FormatError):
    pass


class RunStatus(StrEnum):
    PLANNED = "planned"
    RECORDED = "recorded"
    VOID = "void"


@dataclass(frozen=True)
class RunRow:
    """One row of `bench/runs.csv`."""

    run_id: str
    device: BenchRole
    mounting: str
    block: int
    position: int
    program: str
    run_index: int
    session_id: str
    trial_index: int
    clip: str
    status: RunStatus
    deviation_row: str

    @property
    def recorded(self) -> bool:
        return self.status is RunStatus.RECORDED

    @property
    def rerun(self) -> bool:
        return self.run_index > 5

    @property
    def fault(self) -> bool:
        return bench.program(self.program).fault is not None


@dataclass(frozen=True)
class MountingRecord:
    device: BenchRole
    mounting: str
    block: int
    origin_mm: tuple[float, float]
    qualification_session: str | None


@dataclass(frozen=True)
class Constants:
    band_power_programs: tuple[str, ...]
    excluded_programs: tuple[str, ...]
    fault_dwell_gaps_on_p: bool


@dataclass(frozen=True)
class Registry:
    study_store: Path
    mountings: tuple[MountingRecord, ...]
    constants: Constants

    def mountings_of(self, device: BenchRole) -> tuple[MountingRecord, ...]:
        """The mountings of one device in mounting order, M1 first."""
        return tuple(sorted((m for m in self.mountings if m.device == device), key=lambda m: m.mounting))

    def mounting(self, device: BenchRole, code: str) -> MountingRecord:
        for m in self.mountings:
            if m.device == device and m.mounting == code:
                return m
        raise InputError(f"The registry has no mounting {code} of device {device}.")

    @property
    def blocks(self) -> frozenset[int]:
        return frozenset(m.block for m in self.mountings)

    def journal_folder(self, session_id: str) -> Path:
        return self.study_store / "sessions" / session_id

    def track_file(self, row: RunRow) -> Path:
        return self.study_store / "camera" / f"{row.device}-{row.mounting}" / f"{row.run_id}.track.csv"


@dataclass(frozen=True)
class Build:
    build: str
    source_revision: str


@dataclass(frozen=True)
class Lock:
    protocol_version: str
    commit: str
    builds: dict[BenchRole, Build]
    files: dict[str, str]
    """The SHA-256 of each locked file by its path relative to the root."""


@dataclass(frozen=True)
class Journal:
    """One bench export: its recording with the facts of its header that the recording does not carry."""

    recording: Recording
    role: BenchRole
    mounting: str
    build: Build


def _int(text: str, name: str, *, minimum: int = 0) -> int:
    value = int(text)
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _role(text: object) -> BenchRole:
    if text not in ROLES:
        raise ValueError(f"the device must be P or W, not {text!r}")
    return text


def _object(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, Any], raw)


def _mounting(text: object) -> str:
    if text not in MOUNTINGS:
        raise ValueError(f"the mounting must be M1 to M8, not {text!r}")
    return str(text)


def _row(record: dict[str, str]) -> RunRow:
    status = RunStatus(record["status"])
    device, mounting = _role(record["device"]), _mounting(record["mounting"])
    program = bench.program(record["program"]).id
    run_index = _int(record["run_index"], "run_index", minimum=1)
    if run_index not in RUNS:
        raise ValueError(f"run_index must be 1 to {RUNS[-1]}")
    run_id = f"{device}-{mounting}-{program}-r{run_index}"
    if record["run_id"] != run_id:
        raise ValueError(f"the run id must be {run_id}")
    planned = status is RunStatus.PLANNED
    filled = [record[k] != "" for k in ("session_id", "trial_index", "clip")]
    if any(filled) if planned else not all(filled):
        raise ValueError("a planned row has no session, trial, or clip, and a recorded or void row has all")
    if not planned and record["clip"] != run_id:
        raise ValueError(f"the clip must be the run id {run_id}")
    if (status is RunStatus.VOID) and not record["deviation_row"]:
        raise ValueError("a void row names its deviation row")
    return RunRow(
        run_id,
        device,
        mounting,
        _int(record["block"], "block", minimum=1),
        _int(record["position"], "position", minimum=1),
        program,
        run_index,
        record["session_id"],
        0 if planned else _int(record["trial_index"], "trial_index"),
        record["clip"],
        status,
        record["deviation_row"],
    )


def read_runs(path: str | Path) -> tuple[RunRow, ...]:
    """Read `bench/runs.csv`. Every run id is unique, and every row has every column."""
    path = Path(path)
    rows: list[RunRow] = []
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != RUN_COLUMNS:
                raise InputError(f"{path.name}: the header must be {','.join(RUN_COLUMNS)}.")
            for line, record in enumerate(reader, start=2):
                if None in record or any(v is None for v in record.values()):
                    raise InputError(f"{path.name}, line {line}: the row has the wrong number of fields.")
                try:
                    rows.append(_row(record))
                except (KeyError, ValueError) as e:
                    raise InputError(f"{path.name}, line {line}: {e}") from e
    except OSError as e:
        raise InputError(f"{path.name}: {e}") from e
    ids = [r.run_id for r in rows]
    if len(set(ids)) != len(ids):
        duplicate = next(i for i in ids if ids.count(i) > 1)
        raise InputError(f"{path.name}: the run id {duplicate} appears more than once.")
    return tuple(rows)


def _finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _mounting_record(raw: Mapping[str, Any]) -> MountingRecord:
    origin = raw["originMM"]
    session = raw.get("qualificationSession")
    if session is not None and (not isinstance(session, str) or not session):
        raise ValueError("qualificationSession must be a session id")
    return MountingRecord(
        _role(raw["device"]),
        _mounting(raw["mounting"]),
        _int(str(raw["block"]), "block", minimum=1),
        (_finite(origin["x"], "originMM.x"), _finite(origin["y"], "originMM.y")),
        session,
    )


def _programs(raw: Any, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ValueError(f"{name} must be a list of program ids")
    return tuple(bench.program(str(p)).id for p in cast(list[Any], raw))


def read_registry(path: str | Path, root: str | Path) -> Registry:
    """Read the parts of `bench/registry.json` that the analysis uses. Unknown keys are allowed."""
    path = Path(path)
    try:
        raw = _object(json.loads(path.read_text(encoding="utf-8")), "the registry")
        store = Path(str(raw["studyStore"]))
        if not store.is_absolute():
            store = Path(root) / store
        mountings = tuple(_mounting_record(_object(m, "a mounting record")) for m in raw["mountings"])
        constants_raw = _object(raw["constants"], "constants")
        gaps = constants_raw["faultDwellGapsOnP"]
        if not isinstance(gaps, bool):
            raise ValueError("faultDwellGapsOnP must be true or false")
        constants = Constants(
            _programs(constants_raw["bandPowerPrograms"], "bandPowerPrograms"),
            _programs(constants_raw["excludedPrograms"], "excludedPrograms"),
            gaps,
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise InputError(f"{path.name}: {e}") from e
    keys = [(m.device, m.mounting) for m in mountings]
    blocks = [m.block for m in mountings]
    if len(set(keys)) != len(keys) or len(set(blocks)) != len(blocks):
        raise InputError(f"{path.name}: every mounting record needs its own device, mounting, and block.")
    if any(p.fault is not None for p in map(bench.program, constants.excluded_programs)):
        raise InputError(f"{path.name}: only a main program can be excluded.")
    if any(bench.program(p).geometry != "line" for p in constants.band_power_programs):
        raise InputError(f"{path.name}: the band-power program set holds line programs only.")
    return Registry(store, mountings, constants)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lock_files(root: str | Path, extra: Iterable[str | Path] = ()) -> list[str]:
    """The paths of section 14 step 5 relative to the root, in path order. Every file must exist."""
    root = Path(root).resolve()
    found: list[str] = list(LOCK_FILES)
    for folder in LOCK_FOLDERS:
        files = [p for p in sorted((root / folder).glob("*")) if p.is_file() and not p.name.startswith("._")]
        if not files:
            raise InputError(f"{folder}/ has no file to lock.")
        found.extend(p.relative_to(root).as_posix() for p in files)
    for path in extra:
        path = Path(path)
        if not path.is_absolute():
            path = root / path
        found.append(path.resolve().relative_to(root).as_posix())
    missing = [p for p in found if not (root / p).is_file()]
    if missing:
        raise InputError(f"These locked files are missing: {', '.join(missing)}.")
    return sorted(set(found))


def write_lock(
    root: str | Path, builds: Mapping[BenchRole, Build], commit: str, extra: Iterable[str | Path] = ()
) -> Lock:
    """The lock of the files under the root, the two builds, and the commit. The caller writes it."""
    root = Path(root).resolve()
    if set(builds) != set(ROLES):
        raise InputError("The lock needs the build of device P and the build of device W.")
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise InputError("The commit must be a full lower-case Git hash.")
    files = {p: _sha256(root / p) for p in lock_files(root, extra)}
    return Lock(bench.PROTOCOL_VERSION, commit, dict(builds), files)


def lock_json(lock: Lock) -> bytes:
    document = {
        "format": LOCK_FORMAT,
        "version": LOCK_VERSION,
        "protocol": {"version": lock.protocol_version},
        "commit": lock.commit,
        "builds": {
            role: {"build": b.build, "sourceRevision": b.source_revision}
            for role, b in sorted(lock.builds.items())
        },
        "files": dict(sorted(lock.files.items())),
    }
    return (json.dumps(document, indent=1) + "\n").encode()


def read_lock(path: str | Path) -> Lock:
    path = Path(path)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("format") != LOCK_FORMAT or raw.get("version") != LOCK_VERSION:
            raise ValueError(f"not a {LOCK_FORMAT} file of version {LOCK_VERSION}")
        builds: dict[BenchRole, Build] = {
            _role(role): Build(str(b["build"]), str(b["sourceRevision"])) for role, b in raw["builds"].items()
        }
        files = {str(k): str(v) for k, v in raw["files"].items()}
        lock = Lock(str(raw["protocol"]["version"]), str(raw["commit"]), builds, files)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as e:
        raise InputError(f"{path.name}: {e}") from e
    if set(lock.builds) != set(ROLES):
        raise InputError(f"{path.name}: the lock needs the builds of P and W.")
    if any(len(d) != 64 or any(c not in "0123456789abcdef" for c in d) for d in lock.files.values()):
        raise InputError(f"{path.name}: every file digest is a lower-case SHA-256.")
    return lock


def check_lock(lock: Lock, root: str | Path) -> None:
    """Section 14: every locked file still has its digest, and the protocol is the locked version."""
    root = Path(root)
    if lock.protocol_version != bench.PROTOCOL_VERSION:
        raise InputError(
            f"The lock is for protocol {lock.protocol_version}."
            f" The library implements protocol {bench.PROTOCOL_VERSION}."
        )
    changed = [
        p for p, digest in lock.files.items() if not (root / p).is_file() or _sha256(root / p) != digest
    ]
    if changed:
        raise InputError(f"These locked files changed or are missing: {', '.join(sorted(changed))}.")


def check_build(lock: Lock, journal: Journal) -> None:
    """Section 2.4: the journal's build and source revision are the locked ones of its device."""
    if journal.build != lock.builds[journal.role]:
        raise InputError(
            f"Session {journal.recording.session_id} of device {journal.role} has build"
            f" {journal.build.build} {journal.build.source_revision}, which the lock does not list."
        )


def read_bench_journal(folder: str | Path) -> Journal:
    """Read one bench export with the build of its header. A session of another purpose is an error."""
    folder = Path(folder)
    try:
        export = verify_export(folder)
    except JournalError as e:
        raise InputError(f"{folder.name}: {e}") from e
    recording = export.to_recording()
    context = recording.context
    if context is None or context.purpose != "bench" or not isinstance(context.subject, BenchContext):
        raise InputError(f"{folder.name}: the session is not a bench session.")
    app = export.rows[0]["app"]
    return Journal(
        recording,
        context.subject.role,
        context.subject.mounting,
        Build(str(app["build"]), str(app["sourceRevision"])),
    )
