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
from bench_study import BUILD, COMMIT, DEFECTS, LINE_PROGRAMS, PROGRAMS, RATE_HZ, Truth, write_study

from scriptkin import bench
from scriptkin.bench.analysis import LIMITS
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
from scriptkin.bench.runs import FEATURE_SET
from scriptkin.cli import main
from scriptkin.features import read_feature_table

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
    """The synthetic study, locked, and analysed once. The tests read its outputs."""
    root = tmp_path_factory.mktemp("study")
    write_study(root, programs_folder, move_lists)
    assert main(lock_args(root)) == 0
    started = time.perf_counter()
    assert main(["bench", "analyse", str(root)]) == 0
    (root / "analyse_seconds.txt").write_text(f"{time.perf_counter() - started:.1f}")
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
    path_length = deliverables["features"]["bench.path_length_mm"]
    assert set(path_length["programs"]) == set(PROGRAMS)
    assert path_length["correction"]["model"] == "covariates"
    assert path_length["range"]["speedsMMPerS"] == [20.0, 40.0]


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
    # its run, a fault run counted as valid, or a passing rerun that is not used.
    status = {r["run_id"]: r for r in read_csv(results(study) / "run_status.csv")}
    assert len(status) == len(read_csv(study / "bench" / "runs.csv"))
    for rid, defect in DEFECTS.items():
        row = status[rid]
        if defect == "invalid_camera":
            assert row["valid"] == "true" and row["camera"] == "invalid"
            assert row["camera_rules"] == "marker_missing" and row["motion_failure"] == ""
            continue
        assert row["failed"] == "true" and row["valid"] == "false", rid
        if defect in ("disturbance", "deviceProblem"):
            assert row["marks"] == defect and row["accepted"] == "true"
        elif defect == "backgrounded":
            assert row["rejection"] == "incomplete_trial" and row["accepted"] == "false"
        else:
            assert row["motion_failure"] == "true" and row["reason"] == "motion_failure"
    assert status["P-M1-loops-s-v40-r6"]["valid"] == "true"
    assert status["W-M2-spiral-s-v40-r7"]["valid"] == "true"
    assert status["W-M2-spiral-s-v40-r6"]["valid"] == "false"
    faults = [r for r in status.values() if r["kind"] == "fault"]
    assert len(faults) == 6 * len(bench.FAULTS)
    assert all(r["valid"] == "false" and r["failed"] == "false" and r["camera"] == "absent" for r in faults)
    clean = [r for r in status.values() if r["kind"] == "main" and r["run_id"] not in DEFECTS]
    assert all(r["valid"] == "true" and r["camera"] == "valid" for r in clean)
    assert all(float(r["correlation"]) > 0.99 and r["lag_s"] != "" for r in clean)
    gate = read_csv(results(study) / "gate.csv")
    detected = {(r["device"], r["program"]): r for r in gate if r["kind"] == "detected"}
    for fault in bench.FAULTS:
        for device in ("P", "W"):
            row = detected[(device, fault.id)]
            assert (row["successes"], row["total"]) == ("3", "3"), (device, fault.id, row)
            assert float(row["lower"]) == pytest.approx(0.2924, abs=0.001)
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
    rotation = [r for r in accuracy if r["measure"] == "camera_rotation_deg" and r["device"] == "P"]
    assert all(0.1 < float(r["median"]) < 0.6 for r in rotation)
    duration = [r for r in accuracy if r["measure"] == "executed_duration_error"]
    assert all(abs(float(r["median"])) < 0.01 for r in duration)
    assert float((study / "analyse_seconds.txt").read_text()) < 90


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


@pytest.fixture(scope="module")
def interim_study(
    tmp_path_factory: pytest.TempPathFactory, programs_folder: Path, move_lists: dict[str, MoveList]
) -> Path:
    """A locked study with the six blocks of section 6.6 and 2 runs per program, for the interim check."""
    root = tmp_path_factory.mktemp("interim")
    write_study(root, programs_folder, move_lists, runs_per_program=2)
    assert main(lock_args(root)) == 0
    return root


def test_the_interim_holds_no_device_difference(
    interim_study: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects an interim output that names both devices in one row, prints a device mean or a bias,
    # or flags the mounting variance of a study without a mounting effect.
    study = interim_study
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


@pytest.fixture(scope="module")
def pilot_study(
    tmp_path_factory: pytest.TempPathFactory, programs_folder: Path, move_lists: dict[str, MoveList]
) -> Path:
    """A pilot study of section 6.5: 2 mountings per device, 2 runs per main program, and no lock."""
    root = tmp_path_factory.mktemp("pilot")
    write_study(root, programs_folder, move_lists, mountings=2, runs_per_program=2)
    return root


def test_the_pilot_compares_each_device_with_its_own_command_only(
    pilot_study: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a pilot that needs a lock file, names both devices in one row, prints a bias, fails a
    # clean run against its command, or misses the band power, the noise floor, or the 10-times rule.
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
    assert len(faults) == 4 * len(bench.FAULTS) and all(r["pass"] == "true" for r in faults)
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
    assert main(["bench", "start-point", str(study), "W-M2-loops-s-v40-r1"]) == 0
    x, y = (float(v) for v in capsys.readouterr().out.split())
    sx, sy = move_lists["loops-s-v40"].start_mm
    assert (x, y) == pytest.approx((31.0 + sx, 111.0 - sy), abs=1e-4)
    assert main(["bench", "start-point", str(study), "W-M4-loops-s-v40-r1"]) == 2
    assert "no mounting M4" in capsys.readouterr().err
    assert main(["bench", "start-point", str(study), "nonsense"]) == 2
    lock = json.loads((study / "bench" / "BENCH_LOCK.json").read_text(encoding="utf-8"))
    assert "bench/programs/fault-dwell.json" in lock["files"] and lock["builds"]["W"]["build"] == BUILD.build
    assert math.isfinite(float(LIMITS["bench.motion_s"]))


def test_run_arrays_place_each_run_and_the_median_analysis_shifts_the_means(
    move_lists: dict[str, MoveList], tmp_path: Path
) -> None:
    # Detects a run in the wrong mounting row or run slot, a failed run or a fault run that keeps a
    # value, a camera-referenced value without a camera record, or a median analysis that changes the
    # counts or the run spread.
    import test_bench_features as features_tests
    import test_bench_inputs as inputs_tests
    from test_bench_runs import row, scaled

    from scriptkin.bench.analysis import ANALYSES, MAIN_ANALYSIS, Group, run_arrays
    from scriptkin.bench.inputs import read_registry
    from scriptkin.bench.runs import Evaluated, evaluate

    registry = read_registry(
        inputs_tests.registry_json(
            tmp_path / "registry.json",
            mountings=[
                {"device": "W", "mounting": "M1", "block": 1, "originMM": {"x": 0, "y": 0}},
                {"device": "P", "mounting": "M1", "block": 2, "originMM": {"x": 0, "y": 0}},
                {"device": "P", "mounting": "M2", "block": 3, "originMM": {"x": 0, "y": 0}},
                {"device": "W", "mounting": "M2", "block": 4, "originMM": {"x": 0, "y": 0}},
            ],
        ),
        tmp_path,
    )
    ml = move_lists["loops-s-v40"]
    base = features_tests.device_run(ml, noise_mm=0.002, seed=9)
    factors = {1: 1.0, 2: 1.1, 3: 1.3, 4: 1.0}
    evaluated: dict[str, Evaluated] = {}
    for device, mounting in (("P", "M1"), ("P", "M2"), ("W", "M1"), ("W", "M2")):
        for index, factor in factors.items():
            rid = f"{device}-{mounting}-loops-s-v40-r{index}"
            marks = ("deviceProblem",) if (device, mounting, index) == ("P", "M2", 4) else ()
            trial = replace(scaled(base, factor), marks=marks)
            evaluated[rid] = evaluate(
                row("loops-s-v40", index, 0, run_id=rid, clip=rid, device=device, mounting=mounting),
                trial,
                ml,
                None,
            )
    fault_id = "P-M1-fault-lift-r1"
    evaluated[fault_id] = evaluate(
        row("fault-lift", 1, 0, run_id=fault_id, clip=fault_id), base, move_lists["fault-lift"], None
    )
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
