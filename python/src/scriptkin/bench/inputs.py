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

`bench/order/block-<bb>.csv` is the run order of block bb (section 6.3), with the columns `position`,
`program`, and `repeat`: one row per planned run of the block. The reruns of section 6.4 are not in
it. A block is complete when `bench/runs.csv` holds a row for every run label of its order file.

`bench/registry.json` holds much more than the analysis reads (section 2). The analysis reads three
keys and ignores the rest. `studyStore` is the store path, absolute or relative to the root. `mountings`
is one record per mounting: `device`, `mounting`, `block`, `originMM` with `x` and `y`,
`calibrationSHA256`, and the optional `qualificationSession`. `originMM` is the position of the stimulus
origin of the programs in the marker plane, which `scriptkin bench start-point` adds to the start point
of a move list. The marker plane has y up and the stimulus frame has y down. `calibrationSHA256` is the
SHA-256 of the calibration file of the mounting (section 7.2); a track whose facts name another
calibration is refused. `constants` holds `bandPowerPrograms`, the line programs that
pass section 6.5, `excludedPrograms`, the main programs that section 4.2 step 7 dropped, and
`faultDwellGapsOnP`, true when the pilot showed that a still Pencil gives a `gaps` finding (section 5.3).

`bench/BENCH_LOCK.json` holds the protocol version, the lock commit, the `app.build` and
`app.sourceRevision` of each capture tool by device role, and the SHA-256 of each locked file by its path
relative to the root. `write_lock` hashes the files of section 14 step 5, with the translation script of
section 4.1, every file of the analysis code under `LOCK_CODE`, and any extra file. It refuses a programs
file that does not record the digest of each controller file (`check_programs_file`). The analysis code is the
`scriptkin` package that runs, which must be the one under the root (`code_folder`). `check_lock` refuses
a lock that does not hold every file that it must hold, and a changed or missing file. `check_build`
refuses a journal whose build is not the build of its device. The lock skips the `._` files that macOS
writes on an exFAT drive, the `.DS_Store` files of Finder, and `__pycache__`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import scriptkin
from scriptkin import bench
from scriptkin.bench import MOUNTINGS, RUNS
from scriptkin.bench.controller import ControllerError, controller_digests
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
    "python/studies/bench_controller.py",
    "bench/results/bench_precision.json",
)
LOCK_FOLDERS = ("bench/programs", "bench/order")
LOCK_CODE = "python/src/scriptkin"
"""The analysis code. Section 14 says that the lock commit covers it; the lock also hashes every file
under it, so that a change of the code after the lock fails the check without Git. The files are the
files of the package that runs (`code_folder`)."""
_IGNORED_NAMES = frozenset({".DS_Store"})
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
PLANNED_RUNS = 5
"""The run indices 1 to 5 are the planned runs; 6 and 7 are the reruns of section 6.4."""
ORDER_COLUMNS = ("position", "program", "repeat")
_ORDER_FILE = re.compile(r"block-(\d{2})\.csv")


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
        return self.run_index > PLANNED_RUNS

    @property
    def fault(self) -> bool:
        return bench.program(self.program).fault is not None


@dataclass(frozen=True)
class RunLabel:
    """One planned run of a block order file: its position in the block, its program, and its repeat,
    which is the run index of its row."""

    position: int
    program: str
    repeat: int


@dataclass(frozen=True)
class MountingRecord:
    device: BenchRole
    mounting: str
    block: int
    origin_mm: tuple[float, float]
    calibration_sha256: str
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


def _order(path: Path) -> tuple[RunLabel, ...]:
    labels: list[RunLabel] = []
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != ORDER_COLUMNS:
                raise InputError(f"{path.name}: the header must be {','.join(ORDER_COLUMNS)}.")
            for line, record in enumerate(reader, start=2):
                if None in record or any(v is None for v in record.values()):
                    raise InputError(f"{path.name}, line {line}: the row has the wrong number of fields.")
                try:
                    repeat = _int(record["repeat"], "repeat", minimum=1)
                    if repeat > PLANNED_RUNS:
                        raise ValueError(f"repeat must be 1 to {PLANNED_RUNS}")
                    label = RunLabel(
                        _int(record["position"], "position", minimum=1),
                        bench.program(record["program"]).id,
                        repeat,
                    )
                except (KeyError, ValueError) as e:
                    raise InputError(f"{path.name}, line {line}: {e}") from e
                if label.position != line - 1:
                    raise InputError(f"{path.name}, line {line}: the position must be {line - 1}.")
                labels.append(label)
    except OSError as e:
        raise InputError(f"{path.name}: {e}") from e
    runs = [(label.program, label.repeat) for label in labels]
    if len(set(runs)) != len(runs):
        program, repeat = next(r for r in runs if runs.count(r) > 1)
        raise InputError(f"{path.name}: {program} repeat {repeat} appears more than once.")
    return tuple(labels)


def read_orders(folder: str | Path) -> dict[int, tuple[RunLabel, ...]]:
    """Read every `block-<bb>.csv` of `bench/order/` (section 6.3), by block number.

    The positions of a file run from 1 in file order, and no run label repeats. Without the folder,
    which step 4 of section 14 writes, there is no order, and no block can be complete."""
    folder = Path(folder)
    orders: dict[int, tuple[RunLabel, ...]] = {}
    if not folder.is_dir():
        return orders
    for path in sorted(folder.iterdir()):
        match = _ORDER_FILE.fullmatch(path.name)
        if match is None or not _lockable(path):
            continue
        orders[int(match.group(1))] = _order(path)
    return orders


def _finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _digest(text: object, name: str) -> str:
    if not isinstance(text, str) or len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise ValueError(f"{name} must be a lower-case SHA-256")
    return text


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
        _digest(raw["calibrationSHA256"], "calibrationSHA256"),
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


def _lockable(path: Path) -> bool:
    return (
        path.is_file()
        and not path.name.startswith("._")
        and path.name not in _IGNORED_NAMES
        and "__pycache__" not in path.parts
    )


def _code(path: str) -> bool:
    """Whether a locked path is a file of the analysis code."""
    return path.startswith(f"{LOCK_CODE}/")


def code_folder(root: str | Path) -> Path:
    """The folder of the `scriptkin` package that runs, which must be the locked code under the root.

    The lock covers the code that the analysis runs (section 14). A process can import `scriptkin`
    from another place, for example the source of another checkout, and the files under the root then
    say nothing about the analysis. So the analysis refuses when the package that runs is not
    `<root>/python/src/scriptkin`. Both paths are resolved, so a root whose code folder is a symbolic
    link to the package that runs holds the same files and passes.
    """
    running = Path(scriptkin.__file__).resolve().parent
    locked = Path(root) / LOCK_CODE
    if running != locked.resolve():
        raise InputError(f"The analysis runs scriptkin from {running}, not from the locked code {locked}.")
    return running


def lock_files(root: str | Path, extra: Iterable[str | Path] = ()) -> list[str]:
    """The paths of section 14 step 5 and of the analysis code relative to the root, in path order.
    Every file must exist, and the analysis code is the package that runs (`code_folder`)."""
    root = Path(root).resolve()
    found: list[str] = list(LOCK_FILES)
    for folder in LOCK_FOLDERS:
        files = [p for p in sorted((root / folder).glob("*")) if _lockable(p)]
        if not files:
            raise InputError(f"{folder}/ has no file to lock.")
        found.extend(p.relative_to(root).as_posix() for p in files)
    code = code_folder(root)
    found.extend(
        f"{LOCK_CODE}/{p.relative_to(code).as_posix()}" for p in sorted(code.rglob("*")) if _lockable(p)
    )
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
    check_programs_file(root)
    return Lock(bench.PROTOCOL_VERSION, commit, dict(builds), files)


def check_programs_file(root: Path) -> None:
    """Section 14 step 4: `contract/bench_programs.json` records the SHA-256 of the controller file of each
    program with a move list under `bench/programs/`, so that the capture tools never lock a placeholder."""
    try:
        digests = controller_digests(root / "bench" / "programs")
        raw: Any = json.loads((root / "contract" / "bench_programs.json").read_text(encoding="utf-8"))
        recorded = {str(p["id"]): str(p["trajectorySHA256"]) for p in raw["programs"]}
    except ControllerError as e:
        raise InputError(str(e)) from e
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
        raise InputError(f"contract/bench_programs.json: {e}") from e
    stale = sorted(pid for pid, digest in digests.items() if recorded.get(pid) != digest)
    if stale:
        raise InputError(
            "contract/bench_programs.json does not record the controller file digest of"
            f" {', '.join(stale)}. Run scriptkin contract regenerate."
        )


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


def check_lock(lock: Lock, root: str | Path, *, post_hoc: bool = False) -> list[str]:
    """Section 14: the lock holds every file that it must hold, every locked file still has its digest,
    and the protocol is the locked version.

    The files that the lock must hold are `lock_files(root)` without extras, so a file added under a
    locked folder after the lock, and an entry removed from the lock file, both fail the check. With
    `post_hoc`, the analysis code may differ from the lock: a code file whose digest changed, a code
    file that the lock does not hold, and a locked code file that is gone are the post hoc changes,
    which the function returns as paths. Section 13 calls an analysis change after the lock a
    deviation, reported as post hoc. Every other change refuses.
    """
    root = Path(root)
    if lock.protocol_version != bench.PROTOCOL_VERSION:
        raise InputError(
            f"The lock is for protocol {lock.protocol_version}."
            f" The library implements protocol {bench.PROTOCOL_VERSION}."
        )
    unlisted = sorted(set(lock_files(root)) - set(lock.files))
    if any(not _code(p) for p in unlisted) or (unlisted and not post_hoc):
        raise InputError(f"The lock does not hold these files: {', '.join(unlisted)}.")
    changed = sorted(
        p for p, digest in lock.files.items() if not (root / p).is_file() or _sha256(root / p) != digest
    )
    refused = [p for p in changed if not post_hoc or not _code(p)]
    if refused:
        raise InputError(f"These locked files changed or are missing: {', '.join(refused)}.")
    return sorted(unlisted + changed)


def check_build(lock: Lock, journal: Journal) -> None:
    """Section 2.4: the journal's build and source revision are the locked ones of its device."""
    if journal.build != lock.builds[journal.role]:
        raise InputError(
            f"Session {journal.recording.session_id} of device {journal.role} has build"
            f" {journal.build.build} {journal.build.source_revision}, which the lock does not list."
        )


def _read_export(folder: Path, purpose: str) -> tuple[Recording, Build]:
    """One verified export of the given purpose, with the build of its header."""
    try:
        export = verify_export(folder)
    except JournalError as e:
        raise InputError(f"{folder.name}: {e}") from e
    recording = export.to_recording()
    context = recording.context
    if context is None or context.purpose != purpose:
        raise InputError(f"{folder.name}: the session is not a {purpose} session.")
    app = export.rows[0]["app"]
    return recording, Build(str(app["build"]), str(app["sourceRevision"]))


def read_bench_journal(folder: str | Path) -> Journal:
    """Read one bench export with the build of its header. A session of another purpose is an error."""
    folder = Path(folder)
    recording, build = _read_export(folder, "bench")
    context = recording.context
    if context is None or not isinstance(context.subject, BenchContext):
        raise InputError(f"{folder.name}: the session is not a bench session.")
    return Journal(recording, context.subject.role, context.subject.mounting, build)


def read_qualification_journal(folder: str | Path, device: BenchRole, mounting: str) -> Journal:
    """Read the qualification export of a mounting (section 8.1) with the build of its header.

    A qualification session names no device role and no mounting, so the registry record of the
    mounting gives them, and `check_build` can then check the session against the lock."""
    folder = Path(folder)
    recording, build = _read_export(folder, "qualification")
    return Journal(recording, device, mounting, build)


def parse_run_id(run_id: str) -> tuple[BenchRole, str, str, int]:
    """The device, mounting, program, and run index of a run id of section 15."""
    match = re.fullmatch(r"(P|W)-(M[1-8])-(.+)-r([1-7])", run_id)
    if match is None:
        raise InputError(f"{run_id} is not a run id of the form <device>-<mounting>-<program>-r<run index>.")
    device, mounting, program, run_index = match.groups()
    try:
        bench.program(program)
    except KeyError as e:
        raise InputError(f"{run_id}: {program} is not a registered program.") from e
    return _role(device), mounting, program, int(run_index)
