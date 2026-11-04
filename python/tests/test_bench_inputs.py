from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import fixture_lines, write_export

from scriptkin import bench
from scriptkin.bench.inputs import (
    LOCK_FILES,
    RUN_COLUMNS,
    Build,
    InputError,
    Lock,
    RunStatus,
    check_build,
    check_lock,
    lock_json,
    parse_run_id,
    read_bench_journal,
    read_lock,
    read_registry,
    read_runs,
    write_lock,
)
from scriptkin.recording import BenchRole

COMMIT = "0123456789abcdef0123456789abcdef01234567"
BUILDS: dict[BenchRole, Build] = {"P": Build("42", "abc1234"), "W": Build("7", "abc1234")}


def runs_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    text = ",".join(RUN_COLUMNS) + "\n"
    for row in rows:
        text += ",".join(str(row.get(c, "")) for c in RUN_COLUMNS) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def run_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run_id": "P-M1-loops-s-v40-r2",
        "device": "P",
        "mounting": "M1",
        "block": 2,
        "position": 7,
        "program": "loops-s-v40",
        "run_index": 2,
        "session_id": "3F2A9C1E-0000-4000-8000-000000000013",
        "trial_index": 6,
        "clip": "P-M1-loops-s-v40-r2",
        "status": "recorded",
        "deviation_row": "",
    }
    row.update(changes)
    return row


def registry_json(path: Path, **changes: Any) -> Path:
    document: dict[str, Any] = {
        "studyStore": "store",
        "devices": {"P": {"model": "iPad16,3"}},
        "mountings": [
            {
                "device": "W",
                "mounting": "M1",
                "block": 1,
                "originMM": {"x": 20.0, "y": 90.0},
                "calibrationSHA256": "c" * 64,
                "level": 0.2,
            },
            {
                "device": "P",
                "mounting": "M1",
                "block": 2,
                "originMM": {"x": 25.5, "y": 95.0},
                "calibrationSHA256": "d" * 64,
                "qualificationSession": "3F2A9C1E-0000-4000-8000-000000000099",
            },
        ],
        "constants": {
            "bandPowerPrograms": ["line-osc5", "line-osc8"],
            "excludedPrograms": ["loops-l-v80"],
            "faultDwellGapsOnP": False,
            "noiseFloor": {"P": 0.01},
        },
    }
    document.update(changes)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


CODE_FILE = "python/src/scriptkin/bench/stats.py"
"""The one file that stands for the analysis code in a test root."""


def lock_root(root: Path, extra: tuple[str, ...] = ()) -> Path:
    """A root with every file of protocol section 14 step 5 and one code file, with short content."""
    folders = ("bench/programs/loops-s-v40.json", "bench/order/block-01.csv")
    for rel in (*LOCK_FILES, *folders, CODE_FILE, *extra):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content of {rel}\n", encoding="utf-8")
    return root


def test_the_runs_file_reads_its_rows_and_rejects_each_damaged_row(tmp_path: Path) -> None:
    # Detects a reader that accepts a run id, clip, status, or planned row that disagrees with itself,
    # a duplicate run id, an unknown program, or a void row without its deviation row.
    good = read_runs(
        runs_csv(
            tmp_path / "runs.csv",
            [
                run_row(),
                run_row(
                    run_id="P-M1-loops-s-v40-r6",
                    run_index=6,
                    clip="P-M1-loops-s-v40-r6",
                    status="void",
                    deviation_row="3",
                ),
            ],
        )
    )
    assert [r.run_id for r in good] == ["P-M1-loops-s-v40-r2", "P-M1-loops-s-v40-r6"]
    assert good[0].status is RunStatus.RECORDED and good[0].trial_index == 6 and not good[0].rerun
    assert good[1].status is RunStatus.VOID and good[1].rerun and good[1].deviation_row == "3"
    assert not good[0].fault
    planned = read_runs(
        runs_csv(
            tmp_path / "planned.csv", [run_row(session_id="", trial_index="", clip="", status="planned")]
        )
    )
    assert planned[0].status is RunStatus.PLANNED and planned[0].trial_index == 0
    for message, changes in [
        ("run id must be", {"run_id": "P-M1-loops-s-v40-r3"}),
        ("clip must be", {"clip": "P-M1-loops-s-v40-r3"}),
        ("planned row", {"status": "planned"}),
        ("planned row", {"session_id": ""}),
        ("deviation row", {"status": "void"}),
        ("'nope'", {"status": "nope"}),
        ("loops-x", {"program": "loops-x", "run_id": "P-M1-loops-x-r2", "clip": "P-M1-loops-x-r2"}),
        ("1 to 7", {"run_index": 8, "run_id": "P-M1-loops-s-v40-r8", "clip": "P-M1-loops-s-v40-r8"}),
        ("device must be", {"device": "X", "run_id": "X-M1-loops-s-v40-r2", "clip": "X-M1-loops-s-v40-r2"}),
    ]:
        with pytest.raises(InputError, match=message):
            read_runs(runs_csv(tmp_path / "bad.csv", [run_row(**changes)]))
    with pytest.raises(InputError, match="more than once"):
        read_runs(runs_csv(tmp_path / "twice.csv", [run_row(), run_row()]))
    (tmp_path / "header.csv").write_text("run_id,device\nP-M1-loops-s-v40-r2,P\n", encoding="utf-8")
    with pytest.raises(InputError, match="header must be"):
        read_runs(tmp_path / "header.csv")
    with pytest.raises(InputError, match="wrong number of fields"):
        (tmp_path / "short.csv").write_text(",".join(RUN_COLUMNS) + "\nP-M1-loops-s-v40-r2,P\n")
        read_runs(tmp_path / "short.csv")


def test_the_registry_reads_what_the_analysis_uses_and_ignores_the_rest(tmp_path: Path) -> None:
    # Detects a reader that fails on the operator's extra keys, drops the qualification session, keeps a
    # relative store path, or accepts a missing key, a fault program as excluded, or a loops program as
    # a band-power program.
    registry = read_registry(registry_json(tmp_path / "registry.json"), tmp_path)
    assert registry.study_store == tmp_path / "store"
    assert [m.mounting for m in registry.mountings_of("P")] == ["M1"]
    assert registry.mounting("P", "M1").origin_mm == (25.5, 95.0)
    assert registry.mounting("P", "M1").calibration_sha256 == "d" * 64
    assert registry.mounting("P", "M1").qualification_session == "3F2A9C1E-0000-4000-8000-000000000099"
    assert registry.mounting("W", "M1").qualification_session is None
    assert registry.blocks == {1, 2}
    assert registry.constants.band_power_programs == ("line-osc5", "line-osc8")
    assert registry.constants.excluded_programs == ("loops-l-v80",)
    assert registry.constants.fault_dwell_gaps_on_p is False
    assert registry.journal_folder("S1") == tmp_path / "store" / "sessions" / "S1"
    row = read_runs(runs_csv(tmp_path / "runs.csv", [run_row()]))[0]
    assert (
        registry.track_file(row) == tmp_path / "store" / "camera" / "P-M1" / "P-M1-loops-s-v40-r2.track.csv"
    )
    with pytest.raises(InputError, match="M2"):
        registry.mounting("P", "M2")
    absolute = read_registry(registry_json(tmp_path / "abs.json", studyStore=str(tmp_path / "s")), tmp_path)
    assert absolute.study_store == tmp_path / "s"
    mounting = {"device": "P", "mounting": "M1", "block": 2, "originMM": {"x": 0, "y": 0}}
    for message, changes in [
        ("constants must be an object", {"constants": None}),
        ("'originMM'", {"mountings": [{"device": "P"}]}),
        ("'calibrationSHA256'", {"mountings": [mounting]}),
        ("lower-case SHA-256", {"mountings": [{**mounting, "calibrationSHA256": "ABC"}]}),
        (
            "main program",
            {
                "constants": {
                    "bandPowerPrograms": [],
                    "excludedPrograms": ["fault-lift"],
                    "faultDwellGapsOnP": True,
                }
            },
        ),
        (
            "line programs only",
            {
                "constants": {
                    "bandPowerPrograms": ["loops-s-v40"],
                    "excludedPrograms": [],
                    "faultDwellGapsOnP": True,
                }
            },
        ),
        (
            "true or false",
            {"constants": {"bandPowerPrograms": [], "excludedPrograms": [], "faultDwellGapsOnP": 1}},
        ),
    ]:
        with pytest.raises(InputError, match=message):
            read_registry(registry_json(tmp_path / "bad.json", **changes), tmp_path)
    same_block = registry_json(
        tmp_path / "blocks.json",
        mountings=[
            {**mounting, "device": "W", "block": 1, "calibrationSHA256": "c" * 64},
            {**mounting, "block": 1, "calibrationSHA256": "c" * 64},
        ],
    )
    with pytest.raises(InputError, match="own device, mounting, and block"):
        read_registry(same_block, tmp_path)


def test_the_lock_hashes_every_file_and_the_check_finds_a_change(tmp_path: Path) -> None:
    # Detects a lock that misses a file of section 14 step 5 or an extra file, a round trip that loses a
    # build, a check that passes a changed or missing file, or a build check that passes another build.
    root = lock_root(tmp_path, ("tools/translate.py",))
    lock = write_lock(root, BUILDS, COMMIT, ["tools/translate.py"])
    assert set(lock.files) == {
        *LOCK_FILES,
        "bench/programs/loops-s-v40.json",
        "bench/order/block-01.csv",
        CODE_FILE,
        "tools/translate.py",
    }
    assert lock.protocol_version == bench.PROTOCOL_VERSION and lock.commit == COMMIT
    (root / "bench" / "BENCH_LOCK.json").write_bytes(lock_json(lock))
    assert read_lock(root / "bench" / "BENCH_LOCK.json") == lock
    assert check_lock(lock, root) == []
    (root / "bench" / "registry.json").write_text("changed\n", encoding="utf-8")
    with pytest.raises(InputError, match=r"bench/registry\.json"):
        check_lock(lock, root)
    (root / "bench" / "registry.json").write_text("content of bench/registry.json\n", encoding="utf-8")
    (root / "tools" / "translate.py").unlink()
    with pytest.raises(InputError, match=r"tools/translate\.py"):
        check_lock(lock, root)
    with pytest.raises(InputError, match=r"protocol 0\.9"):
        check_lock(Lock("0.9", COMMIT, BUILDS, {}), root)
    (root / "python" / "uv.lock").unlink()
    with pytest.raises(InputError, match=r"python/uv\.lock"):
        write_lock(root, BUILDS, COMMIT)
    with pytest.raises(InputError, match="build of device P"):
        write_lock(root, {"P": BUILDS["P"]}, COMMIT)
    with pytest.raises(InputError, match="Git hash"):
        write_lock(root, BUILDS, "abc")
    for damaged in ('{"format": "other"}', '{"format": "script-bench-lock", "version": 1, "builds": {}}'):
        (root / "bad.json").write_text(damaged, encoding="utf-8")
        with pytest.raises(InputError):
            read_lock(root / "bad.json")


def test_the_lock_check_needs_every_file_and_allows_changed_code_post_hoc_only(tmp_path: Path) -> None:
    # Detects a lock check that passes a file added under a locked folder after the lock, a lock file
    # that lost an entry, or analysis code that changed after the lock, and a post hoc check that
    # passes a changed file outside the code.
    root = lock_root(tmp_path)
    lock = write_lock(root, BUILDS, COMMIT)
    added = root / "bench" / "programs" / "loops-s-v40~edited.json"
    added.write_text("edited\n", encoding="utf-8")
    with pytest.raises(InputError, match=r"does not hold these files: bench/programs/loops-s-v40~edited"):
        check_lock(lock, root)
    added.unlink()
    files = {k: v for k, v in lock.files.items() if k != "bench/registry.json"}
    with pytest.raises(InputError, match=r"does not hold these files: bench/registry\.json"):
        check_lock(Lock(lock.protocol_version, lock.commit, lock.builds, files), root)
    (root / CODE_FILE).write_text("changed\n", encoding="utf-8")
    with pytest.raises(InputError, match=f"changed or are missing: {re.escape(CODE_FILE)}"):
        check_lock(lock, root)
    assert check_lock(lock, root, post_hoc=True) == [CODE_FILE]
    (root / "bench" / "registry.json").write_text("changed\n", encoding="utf-8")
    with pytest.raises(InputError, match=r"changed or are missing: bench/registry\.json\."):
        check_lock(lock, root, post_hoc=True)


def test_a_bench_journal_gives_its_role_mounting_and_build(tmp_path: Path) -> None:
    # Detects a reader that loses the header build, misreads the role or the mounting, accepts a study
    # session, or passes a journal whose build the lock does not list.
    write_export(tmp_path / "ipad", fixture_lines("bench_ipad"))
    journal = read_bench_journal(tmp_path / "ipad")
    assert (journal.role, journal.mounting) == ("P", "M1")
    assert journal.build == Build("1", "fixture")
    assert journal.recording.session_id == "3F2A9C1E-0000-4000-8000-000000000013"
    lock = Lock(bench.PROTOCOL_VERSION, COMMIT, {"P": Build("1", "fixture"), "W": Build("1", "other")}, {})
    check_build(lock, journal)
    other = Lock(bench.PROTOCOL_VERSION, COMMIT, {"P": Build("2", "fixture"), "W": Build("1", "other")}, {})
    with pytest.raises(InputError, match="build 1 fixture"):
        check_build(other, journal)
    write_export(tmp_path / "study", fixture_lines("basic"))
    with pytest.raises(InputError, match="not a bench session"):
        read_bench_journal(tmp_path / "study")
    with pytest.raises(InputError):
        read_bench_journal(tmp_path / "absent")


def test_a_run_id_parses_into_its_parts_and_a_malformed_one_is_an_error() -> None:
    # Detects a parser that accepts another shape, a run index outside 1 to 7, or an unregistered
    # program, or that returns the parts in another order.
    assert parse_run_id("W-M3-loops-s-v40-r2") == ("W", "M3", "loops-s-v40", 2)
    assert parse_run_id("P-M8-fault-lift-r7") == ("P", "M8", "fault-lift", 7)
    for bad in (
        "X-M3-loops-s-v40-r2",
        "W-M9-loops-s-v40-r2",
        "W-M3-loops-s-v40-r8",
        "W-M3-loops-x-r2",
        "nonsense",
    ):
        with pytest.raises(InputError):
            parse_run_id(bad)
