from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import fixture_lines, write_export

import scriptkin
from scriptkin import bench
from scriptkin.bench.inputs import (
    LOCK_CODE,
    LOCK_FILES,
    ORDER_COLUMNS,
    RUN_COLUMNS,
    Build,
    InputError,
    Lock,
    RunLabel,
    RunStatus,
    check_build,
    check_lock,
    lock_json,
    parse_run_id,
    read_bench_journal,
    read_lock,
    read_orders,
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
"""One file of the analysis code, by its locked path."""


def lock_root(root: Path, extra: tuple[str, ...] = ()) -> Path:
    """A root with every file of protocol section 14 step 5 with short content, and the analysis code.

    The lock covers the package that runs, so the code folder of the root is a symbolic link to it.
    The tests never change a file of the package; they change the lock entries instead."""
    folders = ("bench/programs/loops-s-v40.json", "bench/order/block-01.csv")
    for rel in (*LOCK_FILES, *folders, *extra):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content of {rel}\n", encoding="utf-8")
    code = root / LOCK_CODE
    code.parent.mkdir(parents=True, exist_ok=True)
    code.symlink_to(Path(scriptkin.__file__).resolve().parent, target_is_directory=True)
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
    expected = {
        *LOCK_FILES,
        "bench/programs/loops-s-v40.json",
        "bench/order/block-01.csv",
        "tools/translate.py",
    }
    code = set(lock.files) - expected
    assert expected <= set(lock.files) and CODE_FILE in code
    assert all(p.startswith(f"{LOCK_CODE}/") and p.endswith(".py") for p in code)
    assert f"{LOCK_CODE}/__init__.py" in code and f"{LOCK_CODE}/bench/__init__.py" in code
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
    with pytest.raises(InputError, match=r"does not hold these files: bench/programs/loops-s-v40~edited"):
        check_lock(lock, root, post_hoc=True)
    added.unlink()
    files = {k: v for k, v in lock.files.items() if k != "bench/registry.json"}
    with pytest.raises(InputError, match=r"does not hold these files: bench/registry\.json"):
        check_lock(Lock(lock.protocol_version, lock.commit, lock.builds, files), root)
    # The lock entry of a code file with another digest: the file changed after the lock.
    changed = Lock(lock.protocol_version, lock.commit, lock.builds, {**lock.files, CODE_FILE: "0" * 64})
    with pytest.raises(InputError, match=f"changed or are missing: {re.escape(CODE_FILE)}"):
        check_lock(changed, root)
    assert check_lock(changed, root, post_hoc=True) == [CODE_FILE]
    (root / "bench" / "registry.json").write_text("changed\n", encoding="utf-8")
    with pytest.raises(InputError, match=r"changed or are missing: bench/registry\.json\."):
        check_lock(changed, root, post_hoc=True)


def test_a_post_hoc_check_reports_an_added_or_gone_code_file_and_skips_finder_files(tmp_path: Path) -> None:
    # Detects a post hoc check that refuses a code file that the lock does not hold or a locked code
    # file that is gone, instead of reporting them as changed code, a check that refuses a .DS_Store
    # file that Finder wrote into a locked folder, and a post hoc check that passes a file outside the
    # code that the lock does not hold.
    root = lock_root(tmp_path)
    lock = write_lock(root, BUILDS, COMMIT)
    gone = f"{LOCK_CODE}/bench/gone.py"
    with_gone = Lock(lock.protocol_version, lock.commit, lock.builds, {**lock.files, gone: "1" * 64})
    with pytest.raises(InputError, match=f"changed or are missing: {re.escape(gone)}"):
        check_lock(with_gone, root)
    assert check_lock(with_gone, root, post_hoc=True) == [gone]
    without = Lock(
        lock.protocol_version,
        lock.commit,
        lock.builds,
        {k: v for k, v in lock.files.items() if k != CODE_FILE},
    )
    with pytest.raises(InputError, match=f"does not hold these files: {re.escape(CODE_FILE)}"):
        check_lock(without, root)
    assert check_lock(without, root, post_hoc=True) == [CODE_FILE]
    (root / "bench" / "programs" / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
    (root / "bench" / "programs" / "._loops-s-v40.json").write_bytes(b"\0\5\x16\7")
    assert check_lock(lock, root) == []
    assert "bench/programs/.DS_Store" not in write_lock(root, BUILDS, COMMIT).files
    (root / "bench" / "programs" / "extra.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(InputError, match=r"does not hold these files: bench/programs/extra\.json"):
        check_lock(lock, root, post_hoc=True)


def test_the_lock_covers_the_package_that_runs(tmp_path: Path) -> None:
    # Detects a lock that hashes the code files under the root while the analysis runs the package of
    # another place, so that a change of the code that runs passes the lock check.
    root = lock_root(tmp_path)
    lock = write_lock(root, BUILDS, COMMIT)
    running = Path(scriptkin.__file__).resolve().parent
    assert lock.files[CODE_FILE] == hashlib.sha256((running / "bench" / "stats.py").read_bytes()).hexdigest()
    other = tmp_path / "other"
    for rel in (*LOCK_FILES, "bench/programs/loops-s-v40.json", "bench/order/block-01.csv", CODE_FILE):
        path = other / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content of {rel}\n", encoding="utf-8")
    with pytest.raises(InputError, match=f"runs scriptkin from {re.escape(str(running))}, not from"):
        write_lock(other, BUILDS, COMMIT)
    with pytest.raises(InputError, match="runs scriptkin from"):
        check_lock(lock, other)
    with pytest.raises(InputError, match="runs scriptkin from"):
        check_lock(lock, other, post_hoc=True)


def test_the_order_files_read_their_labels_and_reject_each_damaged_file(tmp_path: Path) -> None:
    # Detects a reader that misses a block file or reads a `._` file, accepts a position out of file
    # order, a repeat above 5, an unknown program, or a repeated run label, or that fails without the
    # folder, which the pilot runs before.
    folder = tmp_path / "order"
    folder.mkdir()

    def write(name: str, rows: list[str], header: str = ",".join(ORDER_COLUMNS)) -> Path:
        path = folder / name
        path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
        return path

    write("block-01.csv", ["1,loops-l-v20,1", "2,fault-lift,1", "3,loops-l-v20,2"])
    write("block-12.csv", ["1,line-osc8,5"])
    write("._block-01.csv", ["garbage"], header="\0\5\x16\7")
    write("notes.txt", ["1,loops-l-v20,1"])
    orders = read_orders(folder)
    assert set(orders) == {1, 12}
    assert orders[1] == (
        RunLabel(1, "loops-l-v20", 1),
        RunLabel(2, "fault-lift", 1),
        RunLabel(3, "loops-l-v20", 2),
    )
    assert orders[12] == (RunLabel(1, "line-osc8", 5),)
    assert read_orders(tmp_path / "absent") == {}
    for message, rows, header in [
        ("position must be 2", ["1,loops-l-v20,1", "3,loops-l-v20,2"], None),
        ("repeat must be 1 to 5", ["1,loops-l-v20,6"], None),
        ("'loops-x'", ["1,loops-x,1"], None),
        ("loops-l-v20 repeat 1 appears more than once", ["1,loops-l-v20,1", "2,loops-l-v20,1"], None),
        ("header must be", ["1,loops-l-v20"], "position,program"),
        ("wrong number of fields", ["1,loops-l-v20"], None),
    ]:
        write("block-02.csv", rows, *([] if header is None else [header]))
        with pytest.raises(InputError, match=message):
            read_orders(folder)


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
