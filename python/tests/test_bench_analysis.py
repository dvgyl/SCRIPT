"""The end-to-end tests of `scriptkin bench analyse`, `interim`, `pilot`, `lock`, and `start-point` on the
synthetic study of `bench_study.py`, run from the files as an operator runs them."""

from __future__ import annotations

import csv
import json
import math
import shutil
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import test_bench_moves as moves_tests
from bench_study import (
    BLOCKS,
    BUILD,
    COMMIT,
    DEFECTS,
    LINE_PROGRAMS,
    ORIGINS_MM,
    PROGRAMS,
    RATE_HZ,
    UNUSED_RERUNS,
    Truth,
    write_study,
)
from test_bench_inputs import CODE_FILE

from scriptkin import bench
from scriptkin.bench.analysis import LIMITS
from scriptkin.bench.inputs import Registry, RunStatus, read_registry
from scriptkin.bench.interim import INTERIM_COLUMNS, PILOT_COLUMNS
from scriptkin.bench.moves import MoveList
from scriptkin.bench.outputs import (
    ACCURACY_COLUMNS,
    AGREEMENT_COLUMNS,
    BENCH_COLUMNS,
    BLAND_ALTMAN_COLUMNS,
    CORRECTION_COLUMNS,
    GATE_COLUMNS,
    SAMPLING_COLUMNS,
    STATUS_COLUMNS,
)
from scriptkin.bench.runs import FEATURE_SET, Evaluated, evaluate
from scriptkin.cli import main
from scriptkin.features import read_feature_table
from scriptkin.quality import Refusal
from scriptkin.recording import Trial

programs_folder = moves_tests.programs_folder
move_lists = moves_tests.move_lists

Rows = list[dict[str, str]]


def read_csv(path: Path) -> Rows:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def lock_args(root: Path) -> list[str]:
    return [
        "bench",
        "lock",
        str(root),
        "--commit",
        COMMIT,
        "--build",
        "P",
        BUILD.build,
        BUILD.source_revision,
        "--build",
        "W",
        BUILD.build,
        BUILD.source_revision,
        "--out",
        str(root / "bench" / "BENCH_LOCK.json"),
    ]


@pytest.fixture(scope="module")
def study(
    tmp_path_factory: pytest.TempPathFactory, programs_folder: Path, move_lists: dict[str, MoveList]
) -> Path:
    """The synthetic study, locked, and analysed once. The tests read its outputs. The CPU time of the
    analysis, which the load of the machine does not change, guards its cost."""
    root = tmp_path_factory.mktemp("study")
    write_study(root, programs_folder, move_lists)
    assert main(lock_args(root)) == 0
    started = time.process_time()
    assert main(["bench", "analyse", str(root)]) == 0
    (root / "analyse_seconds.txt").write_text(f"{time.process_time() - started:.1f}")
    return root


def results(study: Path) -> Path:
    return study / "bench" / "results"


def test_every_output_of_section_15_exists_with_its_columns_and_no_nan_text(study: Path) -> None:
    # Detects a missing output file, a table with other columns, or the text nan in any output.
    expected = {
        "run_status.csv": STATUS_COLUMNS,
        "bench_features.csv": BENCH_COLUMNS,
        "agreement.csv": AGREEMENT_COLUMNS,
        "bland_altman.csv": BLAND_ALTMAN_COLUMNS,
        "correction.csv": CORRECTION_COLUMNS,
        "sampling.csv": SAMPLING_COLUMNS,
        "accuracy.csv": ACCURACY_COLUMNS,
        "gate.csv": GATE_COLUMNS,
    }
    for name, columns in expected.items():
        with (results(study) / name).open(encoding="utf-8", newline="") as stream:
            assert tuple(next(csv.reader(stream))) == tuple(columns), name
    for path in results(study).iterdir():
        text = path.read_text(encoding="utf-8")
        assert "nan" not in text.lower().replace("nanometre", ""), path.name
    table = read_feature_table(results(study) / "features.csv")
    rows = read_csv(study / "bench" / "runs.csv")
    assert len(table.rows) == len(rows)
    assert {r.participant for r in table.rows} == {r["run_id"] for r in rows}
    assert all(r.site == "bench" for r in table.rows)
    deliverables = json.loads((results(study) / "deliverables.json").read_text(encoding="utf-8"))
    assert set(deliverables["features"]) == set(FEATURE_SET)
    assert deliverables["lock"]["commit"] == COMMIT
    assert deliverables["postHoc"] is None
    path_length = deliverables["features"]["bench.path_length_mm"]
    assert set(path_length["programs"]) == set(PROGRAMS)
    assert path_length["correction"]["model"] == "covariates"
    assert path_length["range"]["speedsMMPerS"] == [20.0, 40.0]
    assert path_length["values"] == {"bias": "camera_referenced", "correction": "raw_device"}
    band_power = deliverables["features"]["bench.band_power_mm2_s2"]
    assert band_power["values"] == {"bias": "raw_device", "correction": "raw_device"}
    beta = band_power["correction"]["coefficients"]["beta"]
    assert beta == {"estimate": 1.0, "se": None, "fitted": False}
    assert path_length["correction"]["coefficients"]["beta"]["fitted"] is True
    assert all(r["post_hoc"] == "false" for r in read_csv(results(study) / "run_status.csv"))


def test_the_analysis_recovers_the_bias_and_decides_as_the_truth_implies(study: Path) -> None:
    # Detects a bias with the wrong sign or scale, a camera reference that drops the device scale
    # error, a decision that ignores the limit, or a Holm step that differs from the unadjusted one
    # when every p-value is far from 0.05.
    truth = Truth(1.06)
    rows = [r for r in read_csv(results(study) / "agreement.csv") if r["analysis"] == "main"]
    seen: set[str] = set()
    for row in rows:
        feature = row["feature"]
        limit = LIMITS[feature]
        expected = truth.bias(feature)
        if row["level"] == "program":
            assert row["estimable"] == "true", row
            bias = float(row["bias"])
            assert abs(bias - expected) < 0.05 * limit, (feature, row["program"], bias, expected)
            half = (float(row["upper"]) - float(row["lower"])) / 2
            assert half < 0.1 * limit, (feature, row["program"], half)
            assert (row["equivalent"] == "true") == (abs(expected) <= limit), (feature, row["program"])
        else:
            seen.add(feature)
            equivalent = abs(expected) <= limit
            assert (row["unadjusted"] == "true") == equivalent, (feature, row)
            assert (row["adjusted"] == "true") == equivalent, (feature, row)
            assert (float(row["feature_p"]) <= 0.05) == equivalent
    assert seen == set(FEATURE_SET)
    raw = [
        r
        for r in read_csv(results(study) / "agreement.csv")
        if r["analysis"] == "raw_device" and r["level"] == "program" and r["feature"] == "bench.motion_s"
    ]
    assert raw and all(abs(float(r["bias"])) < 0.01 for r in raw)
    residual = [
        r
        for r in read_csv(results(study) / "correction.csv")
        if r["analysis"] == "main" and r["kind"] == "residual" and r["feature"] == "bench.path_length_mm"
    ]
    assert len(residual) == len(PROGRAMS) and all(r["within_limit"] == "true" for r in residual)
    beta = next(
        r
        for r in read_csv(results(study) / "correction.csv")
        if r["analysis"] == "main" and r["term"] == "beta" and r["feature"] == "bench.band_power_mm2_s2"
    )
    assert beta["fitted"] == "false" and beta["estimate"] == "1" and beta["se"] == ""


def test_each_run_has_the_status_of_section_6_4(study: Path) -> None:
    # Detects a marked, rejected, or bumped run that stays valid, an invalid camera record that fails
    # its run, a fault run counted as valid, a passing rerun that is not used, a rerun used without a
    # failed earlier run, or a device record turned by 2 degrees that is not flagged (section 8.2).
    status = {r["run_id"]: r for r in read_csv(results(study) / "run_status.csv")}
    assert len(status) == len(read_csv(study / "bench" / "runs.csv"))
    for rid, defect in DEFECTS.items():
        row = status[rid]
        if defect == "invalid_camera":
            assert row["valid"] == "true" and row["camera"] == "invalid"
            assert row["camera_rules"] == "marker_missing" and row["motion_failure"] == ""
            continue
        if defect == "rotated":
            assert row["valid"] == "true" and row["rotation_deviation"] == "true"
            continue
        assert row["failed"] == "true" and row["valid"] == "false", rid
        if defect in ("disturbance", "deviceProblem"):
            assert row["marks"] == defect and row["accepted"] == "true"
        elif defect == "backgrounded":
            assert row["rejection"] == "incomplete_trial" and row["accepted"] == "false"
        else:
            assert row["motion_failure"] == "true" and row["reason"] == "motion_failure"
    assert status["P-M1-loops-l-v40-r6"]["valid"] == "true"
    assert status["W-M2-spiral-s-v40-r7"]["valid"] == "true"
    assert status["W-M2-spiral-s-v40-r6"]["valid"] == "false"
    for rid in UNUSED_RERUNS:
        unused = status[rid]
        assert unused["valid"] == "false" and unused["failed"] == "false"
        assert unused["reason"] == "unused_rerun"
    faults = [r for r in status.values() if r["kind"] == "fault"]
    assert len(faults) == len(BLOCKS)
    assert all(r["valid"] == "false" and r["failed"] == "false" and r["camera"] == "absent" for r in faults)
    clean = [
        r
        for r in status.values()
        if r["kind"] == "main" and r["run_id"] not in DEFECTS and r["run_id"] not in UNUSED_RERUNS
    ]
    assert all(r["valid"] == "true" and r["camera"] == "valid" for r in clean)
    assert all(float(r["correlation"]) > 0.99 and r["lag_s"] != "" for r in clean)
    assert all(r["rotation_deviation"] == "false" for r in clean)
    gate = read_csv(results(study) / "gate.csv")
    detected = {(r["device"], r["program"]): r for r in gate if r["kind"] == "detected"}
    for fault in bench.FAULTS:
        for device in ("P", "W"):
            row = detected[(device, fault.id)]
            assert (row["successes"], row["total"]) == ("2", "2"), (device, fault.id, row)
            assert float(row["lower"]) == pytest.approx(0.1581, abs=0.001)
    accepted = {r["device"]: r for r in gate if r["kind"] == "accepted"}
    assert accepted["W"]["successes"] == str(int(accepted["W"]["total"]) - 1)
    assert accepted["P"]["successes"] == accepted["P"]["total"]


def test_the_sampling_and_accuracy_tables_describe_the_simulated_devices(study: Path) -> None:
    # Detects a sampling summary that misses the device interval, a noise floor that is not near zero
    # on a still hold, an accuracy table whose camera fit does not recover the mounting rotation, or
    # one that does not show the 6 percent scale error of device P as a distance from the camera.
    sampling = read_csv(results(study) / "sampling.csv")
    intervals = [r for r in sampling if r["kind"] == "intervals"]
    assert len(intervals) == 2 * len(PROGRAMS)
    assert all(abs(float(r["median_s"]) - 1 / RATE_HZ) < 1e-4 for r in intervals)
    hold = next(
        r
        for r in sampling
        if r["kind"] == "noise_floor_hold" and r["mounting"] == "M1" and r["device"] == "P"
    )
    assert 0 < float(hold["band_power_mm2_s2"]) < 0.01
    missing = next(
        r
        for r in sampling
        if r["kind"] == "noise_floor_hold" and r["device"] == "W" and r["mounting"] == "M1"
    )
    assert (
        missing["band_power_mm2_s2"] == "" and missing["reason"] == "no qualification session in the registry"
    )
    spiral = [r for r in sampling if r["kind"] == "noise_floor_spiral" and r["mounting"] == "all"]
    assert len(spiral) == 2 and all(float(r["band_power_mm2_s2"]) < 0.05 for r in spiral)
    accuracy = read_csv(results(study) / "accuracy.csv")
    rms = [r for r in accuracy if r["measure"] == "camera_rms_mm"]
    assert len(rms) == 2 * len(PROGRAMS)
    assert all(float(r["median"]) < 0.05 for r in rms if r["device"] == "W")
    assert all(1.0 < float(r["median"]) < 3.0 for r in rms if r["device"] == "P")
    rotation = [r for r in accuracy if r["measure"] == "camera_rotation_deg"]
    assert all(0.05 < float(r["median"]) < 0.6 for r in rotation if r["device"] == "P")
    deviations = {(r["device"], r["program"]): r["deviations"] for r in rotation}
    assert deviations[("P", "spiral-s-v40")] == "1"
    assert all(v == "0" for k, v in deviations.items() if k != ("P", "spiral-s-v40"))
    assert all(r["deviations"] == "" for r in accuracy if r["measure"] != "camera_rotation_deg")
    duration = [r for r in accuracy if r["measure"] == "executed_duration_error"]
    assert all(abs(float(r["median"])) < 0.01 for r in duration)
    # About 75 s of CPU time on an idle machine. CPU time grows when another check loads every core,
    # because a thread then runs at half speed and still counts full time, so the limit leaves room.
    assert float((study / "analyse_seconds.txt").read_text()) < 120


def test_the_analysis_refuses_an_incomplete_study_a_changed_file_and_a_foreign_build(
    study: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects an analysis that runs on a study with a planned row or a missing block, after a locked
    # file changed, or on a journal whose build the lock does not list.
    root = tmp_path / "copy"
    shutil.copytree(study, root, ignore=shutil.ignore_patterns("analyse_seconds.txt"))
    for output in (root / "bench" / "results").iterdir():
        if output.name != "bench_precision.json":
            output.unlink()
    runs = root / "bench" / "runs.csv"
    original = runs.read_text(encoding="utf-8")
    lines = original.splitlines()
    target = next(i for i, line in enumerate(lines) if line.startswith("P-M3-loops-l-v40-r1,"))
    fields = lines[target].split(",")
    fields[7], fields[8], fields[9], fields[10] = "", "", "", "planned"
    lines[target] = ",".join(fields)
    runs.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main(["bench", "analyse", str(root)]) == 1
    assert "P-M3-loops-l-v40-r1 is planned" in capsys.readouterr().err
    kept = [line for line in original.splitlines() if line.split(",")[3] != "6"]
    runs.write_text("\n".join(kept) + "\n", encoding="utf-8")
    assert main(["bench", "analyse", str(root)]) == 1
    assert "Block 6 has no row" in capsys.readouterr().err
    runs.write_text(original, encoding="utf-8")
    protocol = root / "docs" / "BENCH_PROTOCOL.md"
    protocol.write_text(protocol.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    assert main(["bench", "analyse", str(root)]) == 2
    assert "docs/BENCH_PROTOCOL.md" in capsys.readouterr().err
    protocol.write_text("content of docs/BENCH_PROTOCOL.md\n", encoding="utf-8")
    foreign = lock_args(root)
    foreign[foreign.index(BUILD.source_revision)] = "other"
    assert main(foreign) == 0
    assert main(["bench", "analyse", str(root)]) == 2
    assert "which the lock does not list" in capsys.readouterr().err
    assert not (root / "bench" / "results" / "agreement.csv").exists()


def test_the_qualification_session_and_the_track_calibration_are_checked(
    study: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a hold noise floor read from a qualification session whose build the lock does not list
    # (sections 2.4 and 8.1), and a track made with the calibration of another mounting (section 7.2)
    # that the analysis accepts.
    from bench_study import QUALIFICATION_SESSION

    from scriptkin.bench.inputs import Build, InputError
    from scriptkin.bench.study import hold_noise, read_study

    locked = read_study(study)
    assert locked.lock is not None
    assert set(hold_noise(locked)) == {("P", "M1")}
    builds = {**locked.lock.builds, "P": Build("2", "other")}
    foreign = replace(locked, lock=replace(locked.lock, builds=builds))
    with pytest.raises(InputError, match=f"Session {QUALIFICATION_SESSION}.*which the lock does not list"):
        hold_noise(foreign)
    root = tmp_path / "copy"
    shutil.copytree(study, root, ignore=shutil.ignore_patterns("analyse_seconds.txt"))
    facts = root / "store" / "camera" / "W-M1" / "W-M1-loops-l-v20-r1.track.json"
    facts.write_text(facts.read_text(encoding="utf-8").replace("c" * 64, "d" * 64), encoding="utf-8")
    assert main(["bench", "analyse", str(root)]) == 2
    err = capsys.readouterr().err
    assert "W-M1-loops-l-v20-r1" in err and "calibration" in err and "mounting M1 of device W" in err


@pytest.mark.slow
def test_the_analysis_refuses_an_unlisted_file_and_runs_changed_code_post_hoc_only(
    study: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a lock check that passes a move list file added after the lock, a lock file that lost an
    # entry, or analysis code that changed after the lock (section 14), and a post hoc analysis whose
    # outputs do not say so or that does not name the changed files (section 13).
    root = tmp_path / "copy"
    shutil.copytree(study, root, ignore=shutil.ignore_patterns("analyse_seconds.txt"))
    out_dir = root / "bench" / "results"
    for output in out_dir.iterdir():
        if output.name != "bench_precision.json":
            output.unlink()
    programs = root / "bench" / "programs"
    added = programs / "loops-l-v20~edited.json"
    added.write_bytes((programs / "loops-l-v20.json").read_bytes())
    assert main(["bench", "analyse", str(root)]) == 2
    assert "does not hold these files: bench/programs/loops-l-v20~edited.json" in capsys.readouterr().err
    added.unlink()
    lock_path = root / "bench" / "BENCH_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    del lock["files"]["bench/registry.json"]
    lock_path.write_text(json.dumps(lock, indent=1) + "\n", encoding="utf-8")
    assert main(["bench", "analyse", str(root)]) == 2
    assert "does not hold these files: bench/registry.json" in capsys.readouterr().err
    assert main(lock_args(root)) == 0
    code = root / CODE_FILE
    code.write_text(code.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    assert main(["bench", "analyse", str(root)]) == 2
    assert CODE_FILE in capsys.readouterr().err
    assert not (out_dir / "agreement.csv").exists()
    assert main(["bench", "analyse", str(root), "--post-hoc"]) == 0
    out = capsys.readouterr().out
    assert "post hoc" in out and CODE_FILE in out
    for name in (
        "run_status.csv",
        "bench_features.csv",
        "agreement.csv",
        "bland_altman.csv",
        "correction.csv",
        "sampling.csv",
        "accuracy.csv",
        "gate.csv",
    ):
        rows = read_csv(out_dir / name)
        assert rows and all(r["post_hoc"] == "true" for r in rows), name
    deliverables = json.loads((out_dir / "deliverables.json").read_text(encoding="utf-8"))
    assert deliverables["postHoc"] == {"changedFiles": [CODE_FILE]}
    table = read_feature_table(out_dir / "features.csv")
    assert table.rows and all(r.site == "bench-post-hoc" for r in table.rows)


@pytest.fixture(scope="module")
def interim_study(
    tmp_path_factory: pytest.TempPathFactory, programs_folder: Path, move_lists: dict[str, MoveList]
) -> Path:
    """A locked study after block 6 (section 6.6): 3 mountings per device with 2 runs per program, and
    the main runs of blocks 7 to 12 planned in `bench/runs.csv`."""
    root = tmp_path_factory.mktemp("interim")
    write_study(root, programs_folder, move_lists, mountings=3, runs_per_program=2, planned_after=True)
    assert main(lock_args(root)) == 0
    return root


def test_the_interim_holds_no_device_difference(
    interim_study: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects an interim output that names both devices in one row, prints a device mean or a bias,
    # or flags the mounting variance of a study without a mounting effect, and an interim that refuses
    # the planned rows of the later blocks.
    study = interim_study
    rows = read_csv(study / "bench" / "runs.csv")
    assert {r["status"] for r in rows if int(r["block"]) > 6} == {"planned"}
    assert main(["bench", "interim", str(study)]) == 0
    out = capsys.readouterr().out
    assert "bias" not in out and "difference" not in out
    assert "Add this row to bench/DEVIATIONS.md" in out and "scriptkin bench interim" in out
    interim = read_csv(results(study) / "interim.csv")
    assert tuple(interim[0]) == INTERIM_COLUMNS
    assert {r["device"] for r in interim} == {"P", "W"}
    assert len(interim) == 2 * (len(FEATURE_SET) + 4)
    assert all(r["above_quarter_limit"] == "false" for r in interim), [
        r for r in interim if r["above_quarter_limit"] != "false"
    ]
    assert all(float(r["sigma_mount_over_limit"]) < 0.25 for r in interim)
    assert {r["values"] for r in interim} == {"raw", "camera_referenced"}
    lines = [line for line in out.splitlines() if line.startswith(("P\t", "W\t"))]
    assert len(lines) == len(interim)
    for line in lines:
        cells = line.split("\t")
        assert not any(c in ("P", "W") for c in cells[1:])


def test_the_analysis_refuses_a_registry_without_the_complete_design(
    interim_study: Path, pilot_study: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects an analysis that compares the devices before the last block (section 13): after block 6
    # with the later rows planned, and on a locked study of 4 recorded blocks without a planned row.
    assert main(["bench", "analyse", str(interim_study)]) == 1
    err = capsys.readouterr().err
    assert "design of section 6.3" in err and "blocks [1, 2, 3, 4, 5, 6]" in err
    assert not (results(interim_study) / "agreement.csv").exists()
    root = tmp_path / "four_blocks"
    shutil.copytree(pilot_study, root)
    assert main(lock_args(root)) == 0
    assert main(["bench", "analyse", str(root)]) == 1
    assert "design of section 6.3" in capsys.readouterr().err
    assert not (results(root) / "agreement.csv").exists()


def test_the_design_check_knows_the_block_order_of_section_6_3(tmp_path: Path) -> None:
    # Detects a design check that passes 12 blocks in another device order, a mounting code out of its
    # block order, or 16 blocks whose last four are not P W W P, or that fails the two complete designs.
    import test_bench_inputs as inputs_tests

    from scriptkin.bench.study import BLOCK_ORDER, EXTRA_BLOCK_ORDER, design_problems

    def registry(order: str, swap: tuple[int, int] | None = None) -> Registry:
        mountings: list[dict[str, object]] = []
        counts = {"P": 0, "W": 0}
        for block, device in enumerate(order, start=1):
            counts[device] += 1
            mountings.append(
                {
                    "device": device,
                    "mounting": f"M{counts[device]}",
                    "block": block,
                    "originMM": {"x": 0, "y": 0},
                    "calibrationSHA256": "c" * 64,
                }
            )
        if swap is not None:
            a, b = swap
            mountings[a]["mounting"], mountings[b]["mounting"] = (
                mountings[b]["mounting"],
                mountings[a]["mounting"],
            )
        return read_registry(inputs_tests.registry_json(tmp_path / "r.json", mountings=mountings), tmp_path)

    assert design_problems(registry(BLOCK_ORDER)) == []
    assert design_problems(registry(BLOCK_ORDER + EXTRA_BLOCK_ORDER)) == []
    assert design_problems(registry(BLOCK_ORDER[:6])) == [
        "The registry lists blocks [1, 2, 3, 4, 5, 6]; the design of section 6.3 needs blocks 1 to 12,"
        " or 1 to 16 after the interim check added mountings."
    ]
    wrong_device = design_problems(registry("PWPWPWWPWPPW"))
    assert wrong_device[:2] == [
        "Block 1 is device P; section 6.3 puts device W there.",
        "Block 2 is device W; section 6.3 puts device P there.",
    ]
    assert (
        design_problems(registry(BLOCK_ORDER + "WPPW"))[0]
        == "Block 13 is device W; section 6.3 puts device P there."
    )
    swapped = design_problems(registry(BLOCK_ORDER, swap=(1, 2)))
    assert swapped == [
        "Block 2 is mounting M2 of device P; its block order makes it M1.",
        "Block 3 is mounting M1 of device P; its block order makes it M2.",
    ]


@pytest.fixture(scope="module")
def pilot_study(
    tmp_path_factory: pytest.TempPathFactory, programs_folder: Path, move_lists: dict[str, MoveList]
) -> Path:
    """A pilot study of section 6.5: 2 mountings per device, 2 runs per main program, no lock, and the
    constants of the registry not yet decided: no band-power program and the dwell expectation unset."""
    root = tmp_path_factory.mktemp("pilot")
    write_study(root, programs_folder, move_lists, mountings=2, runs_per_program=2)
    registry = root / "bench" / "registry.json"
    document = json.loads(registry.read_text(encoding="utf-8"))
    document["constants"]["bandPowerPrograms"] = []
    document["constants"]["faultDwellGapsOnP"] = True
    registry.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return root


def test_the_pilot_compares_each_device_with_its_own_command_only(
    pilot_study: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a pilot that needs a lock file, names both devices in one row, prints a bias, fails a
    # clean run against its command, misses the band power, the noise floor, or the 10-times rule, or
    # reads the constants that it must decide: the band-power programs and the dwell expectation.
    study = pilot_study
    assert not (study / "bench" / "BENCH_LOCK.json").exists()
    assert main(["bench", "pilot", str(study)]) == 0
    out = capsys.readouterr().out
    assert "bias" not in out and "difference" not in out
    pilot = read_csv(results(study) / "pilot.csv")
    assert tuple(pilot[0]) == PILOT_COLUMNS
    for row in pilot:
        assert row["device"] in ("P", "W", ""), row
        assert not (row["device"] == "" and row["kind"] != "ten_times"), row
    durations = [r for r in pilot if r["kind"] == "duration" and r["run_id"] not in DEFECTS]
    assert durations and all(r["pass"] == "true" for r in durations)
    assert {r["source"] for r in durations} == {"device", "camera"}
    band = [r for r in pilot if r["kind"] == "band_power"]
    assert len(band) == 2 * 2 * 2 * len(LINE_PROGRAMS) + 1
    assert all(r["pass"] == "true" for r in band if r["run_id"] not in DEFECTS)
    ten = [r for r in pilot if r["kind"] == "ten_times"]
    assert [r["program"] for r in ten] == list(LINE_PROGRAMS) and all(r["pass"] == "true" for r in ten)
    faults = [r for r in pilot if r["kind"] == "fault"]
    assert {r["program"] for r in faults} == {"fault-lift"} and all(r["pass"] == "true" for r in faults)
    dwell = {(r["run_id"], r["kind"]): r["pass"] for r in pilot if r["program"] == "fault-dwell"}
    assert {k[1] for k in dwell} == {"fault_gaps", "fault_no_gaps"}
    assert all(v == "true" for k, v in dwell.items() if k[1] == "fault_no_gaps")
    assert all(
        v == ("false" if k[0].startswith("P") else "true") for k, v in dwell.items() if k[1] == "fault_gaps"
    )
    floors = [r for r in pilot if r["kind"].startswith("noise_floor")]
    assert {(r["device"], r["kind"]) for r in floors if r["value"]} == {
        ("P", "noise_floor_spiral"),
        ("W", "noise_floor_spiral"),
        ("P", "noise_floor_hold"),
    }


def test_the_start_point_and_the_lock_commands_read_the_files(
    study: Path, move_lists: dict[str, MoveList], capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a start point that ignores the mounting origin, keeps y down, or names a run outside the
    # registry, and a lock file that misses a program file.
    assert main(["bench", "start-point", str(study), "W-M2-loops-l-v40-r1"]) == 0
    x, y = (float(v) for v in capsys.readouterr().out.split())
    sx, sy = move_lists["loops-l-v40"].start_mm
    ox, oy = ORIGINS_MM[("W", "M2")]
    assert (x, y) == pytest.approx((ox + sx, oy - sy), abs=1e-4)
    assert main(["bench", "start-point", str(study), "W-M7-loops-l-v40-r1"]) == 2
    assert "no mounting M7" in capsys.readouterr().err
    assert main(["bench", "start-point", str(study), "nonsense"]) == 2
    lock = json.loads((study / "bench" / "BENCH_LOCK.json").read_text(encoding="utf-8"))
    assert "bench/programs/fault-dwell.json" in lock["files"] and lock["builds"]["W"]["build"] == BUILD.build
    assert math.isfinite(float(LIMITS["bench.motion_s"]))


def two_mounting_registry(tmp_path: Path) -> Registry:
    import test_bench_inputs as inputs_tests

    blocks = (("W", "M1"), ("P", "M1"), ("P", "M2"), ("W", "M2"))
    return read_registry(
        inputs_tests.registry_json(
            tmp_path / "registry.json",
            mountings=[
                {
                    "device": device,
                    "mounting": mounting,
                    "block": block,
                    "originMM": {"x": 0, "y": 0},
                    "calibrationSHA256": "c" * 64,
                }
                for block, (device, mounting) in enumerate(blocks, start=1)
            ],
        ),
        tmp_path,
    )


def evaluated_run(
    program: str, device: str, mounting: str, index: int, trial: Trial, move_lists: dict[str, MoveList]
) -> Evaluated:
    from test_bench_runs import row

    rid = f"{device}-{mounting}-{program}-r{index}"
    return evaluate(
        row(program, index, 0, run_id=rid, clip=rid, device=device, mounting=mounting),
        trial,
        move_lists[program],
        None,
    )


def test_run_arrays_place_each_run_and_the_median_analysis_shifts_the_means(
    move_lists: dict[str, MoveList], tmp_path: Path
) -> None:
    # Detects a run in the wrong mounting row or run slot, a failed run or a fault run that keeps a
    # value, a camera-referenced value without a camera record, or a median analysis that changes the
    # counts or the run spread.
    import test_bench_features as features_tests
    from test_bench_runs import scaled

    from scriptkin.bench.analysis import ANALYSES, MAIN_ANALYSIS, Group, run_arrays

    registry = two_mounting_registry(tmp_path)
    ml = move_lists["loops-s-v40"]
    base = features_tests.device_run(ml, noise_mm=0.002, seed=9)
    factors = {1: 1.0, 2: 1.1, 3: 1.3, 4: 1.0}
    evaluated: dict[str, Evaluated] = {}
    for device, mounting in (("P", "M1"), ("P", "M2"), ("W", "M1"), ("W", "M2")):
        for index, factor in factors.items():
            marks = ("deviceProblem",) if (device, mounting, index) == ("P", "M2", 4) else ()
            trial = replace(scaled(base, factor), marks=marks)
            run = evaluated_run("loops-s-v40", device, mounting, index, trial, move_lists)
            evaluated[run.row.run_id] = run
    fault = evaluated_run("fault-lift", "P", "M1", 1, base, move_lists)
    evaluated[fault.row.run_id] = fault
    group = (Group("loops-s-v40", (bench.program("loops-s-v40"),)),)
    raw = next(a for a in ANALYSES if a.name == "raw_device")
    arrays = run_arrays(evaluated, registry, "bench.path_length_mm", group, raw)
    assert arrays.p.shape == (1, 2, 7) and arrays.w.shape == (1, 2, 7)
    reference = arrays.w[0, 0, 0]
    for k, factor in factors.items():
        assert arrays.w[0, 0, k - 1] == pytest.approx(reference + math.log(factor), abs=1e-3)
    assert math.isnan(arrays.p[0, 1, 3]) and not math.isnan(arrays.p[0, 0, 3])
    assert all(math.isnan(v) for v in arrays.p[0, :, 4:].ravel())
    camera = run_arrays(evaluated, registry, "bench.path_length_mm", group, MAIN_ANALYSIS)
    assert all(math.isnan(v) for v in camera.p.ravel()) and all(math.isnan(v) for v in camera.w.ravel())
    median = replace(next(a for a in ANALYSES if a.name == "median"), camera_referenced=False)
    shifted = run_arrays(evaluated, registry, "bench.path_length_mm", group, median)
    for a, b in ((shifted.w[0, 0], arrays.w[0, 0]), (shifted.p[0, 1], arrays.p[0, 1])):
        valid = np.isfinite(b)
        assert np.array_equal(np.isfinite(a), valid)
        assert np.mean(a[valid]) == pytest.approx(np.median(b[valid]))
        assert np.std(a[valid]) == pytest.approx(np.std(b[valid]))


def test_a_pooled_group_averages_the_per_program_mounting_means(
    move_lists: dict[str, MoveList], tmp_path: Path
) -> None:
    # Detects a group mean of the by_scale analysis that is the mean of all runs of its programs, so
    # that a program with more valid runs in a mounting weighs more, and a group that keeps a mounting
    # in which one of its programs is not estimable.
    import test_bench_features as features_tests
    from test_bench_runs import scaled

    from scriptkin.bench import stats
    from scriptkin.bench.analysis import ANALYSES, Group, run_arrays

    registry = two_mounting_registry(tmp_path)
    counts = {
        ("M1", "loops-s-v40"): 4,
        ("M1", "spiral-s-v40"): 5,
        ("M2", "loops-s-v40"): 3,
        ("M2", "spiral-s-v40"): 5,
    }
    evaluated: dict[str, Evaluated] = {}
    for (mounting, pid), count in counts.items():
        factor = 1.5 if pid.startswith("spiral") else 1.0
        for index in range(1, count + 1):
            trial = scaled(features_tests.device_run(move_lists[pid], noise_mm=0.002, seed=index), factor)
            run = evaluated_run(pid, "W", mounting, index, trial, move_lists)
            evaluated[run.row.run_id] = run
    group = (Group("scale 0.7", (bench.program("loops-s-v40"), bench.program("spiral-s-v40"))),)
    raw = next(a for a in ANALYSES if a.name == "raw_device")
    arrays = run_arrays(evaluated, registry, "bench.path_length_mm", group, raw)
    assert arrays.w.shape == (1, 2, 14)
    assert stats.valid_counts(arrays.w[0]).tolist() == [9, 0]
    m1 = arrays.w[0, 0]
    loops_runs = np.log(_lengths(evaluated, "M1", "loops-s-v40"))
    spiral_runs = np.log(_lengths(evaluated, "M1", "spiral-s-v40"))
    assert np.nanmean(m1[:7]) == pytest.approx(np.nanmean(m1[7:]), abs=1e-9)
    unweighted = (loops_runs.mean() + spiral_runs.mean()) / 2
    assert float(stats.mounting_means(arrays.w[0])[0]) == pytest.approx(unweighted, abs=1e-9)
    assert np.std(m1[:4]) == pytest.approx(np.std(loops_runs), abs=1e-9)
    assert not bool(stats.estimable(arrays.w[0]))


def _lengths(evaluated: dict[str, Evaluated], mounting: str, program: str) -> list[float]:
    values = [
        e.device_value("bench.path_length_mm")
        for e in evaluated.values()
        if e.row.mounting == mounting and e.row.program == program
    ]
    return [float(v) for v in values if not isinstance(v, Refusal)]


def test_a_rerun_is_used_only_after_a_failed_earlier_run(
    move_lists: dict[str, MoveList], tmp_path: Path
) -> None:
    # Detects an analysis that uses a rerun recorded after five runs that it finds valid, or a second
    # rerun after a first rerun that passed, and a status table that reports such a rerun as valid.
    import test_bench_features as features_tests

    from scriptkin.bench.analysis import ANALYSES, Group, run_arrays
    from scriptkin.bench.study import mark_unused_reruns

    registry = two_mounting_registry(tmp_path)
    ml = move_lists["loops-s-v40"]
    base = features_tests.device_run(ml, noise_mm=0.002, seed=9)
    failed = replace(base, marks=("deviceProblem",))
    plan = {
        "M1": {1: base, 2: base, 3: base, 4: base, 5: base, 6: base},
        "M2": {1: base, 2: failed, 3: base, 4: base, 5: base, 6: base, 7: base},
    }
    evaluated: dict[str, Evaluated] = {}
    for mounting, runs in plan.items():
        for index, trial in runs.items():
            run = evaluated_run("loops-s-v40", "W", mounting, index, trial, move_lists)
            evaluated[run.row.run_id] = run
    marked = mark_unused_reruns(evaluated, ())
    unused = {rid for rid, run in marked.items() if run.outcome.unused_rerun}
    assert unused == {"W-M1-loops-s-v40-r6", "W-M2-loops-s-v40-r7"}
    assert not marked["W-M2-loops-s-v40-r6"].outcome.failed and marked["W-M2-loops-s-v40-r6"].valid
    assert marked["W-M1-loops-s-v40-r6"].outcome.reason == "unused_rerun"
    assert not marked["W-M1-loops-s-v40-r6"].valid
    group = (Group("loops-s-v40", (bench.program("loops-s-v40"),)),)
    raw = next(a for a in ANALYSES if a.name == "raw_device")
    arrays = run_arrays(marked, registry, "bench.path_length_mm", group, raw)
    assert np.isfinite(arrays.w[0, 0]).tolist() == [True] * 5 + [False, False]
    assert np.isfinite(arrays.w[0, 1]).tolist() == [True, False, True, True, True, True, False]
    void = replace(evaluated["W-M1-loops-s-v40-r1"].row, status=RunStatus.VOID, deviation_row="3")
    after_void = mark_unused_reruns({k: v for k, v in evaluated.items() if k != void.run_id}, (void,))
    assert not after_void["W-M1-loops-s-v40-r6"].outcome.unused_rerun
