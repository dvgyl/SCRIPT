from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import test_bench_camera as camera_tests
import test_bench_features as features_tests
import test_bench_moves as moves_tests
from conftest import fixture_lines, write_export

from scriptkin.bench.camera import CameraRule, ExecutedMovement, PathFit, SpatialAccuracy, TimeAlignment
from scriptkin.bench.inputs import InputError, RunRow, RunStatus, read_bench_journal
from scriptkin.bench.moves import MoveList
from scriptkin.bench.runs import (
    CameraState,
    CleanMedians,
    Evaluated,
    comparison_value,
    evaluate,
    fault_detected,
    trial_of,
)
from scriptkin.quality import Refusal, RefusalCode, Rejected
from scriptkin.recording import FloatArray, PenState, Stroke, Trial, TrialOutcome

programs_folder = moves_tests.programs_folder
move_lists = moves_tests.move_lists
device_run = features_tests.device_run
camera_track = camera_tests.camera_track

SESSION = "3F2A9C1E-0000-4000-8000-000000000013"


def row(program: str = "loops-l-v20", run_index: int = 1, trial_index: int = 0, **changes: object) -> RunRow:
    fields: dict[str, object] = {
        "run_id": f"P-M1-{program}-r{run_index}",
        "device": "P",
        "mounting": "M1",
        "block": 2,
        "position": 1,
        "program": program,
        "run_index": run_index,
        "session_id": SESSION,
        "trial_index": trial_index,
        "clip": f"P-M1-{program}-r{run_index}",
        "status": RunStatus.RECORDED,
        "deviation_row": "",
    }
    fields.update(changes)
    return RunRow(**fields)  # type: ignore[arg-type]


def scaled(trial: Trial, factor: float) -> Trial:
    return replace(trial, strokes=tuple(Stroke(s.t, s.xy * factor) for s in trial.strokes))


def hover(t0: float, xy: FloatArray, seconds: float, *, gap: bool = False) -> Stroke:
    """An in-air stroke still at `xy` at 240 Hz from `t0`, with a 0.125 s gap inside it when `gap`."""
    t = t0 + np.arange(int(seconds * 240)) / 240
    if gap:
        t = t[(t < t0 + 0.2) | (t >= t0 + 0.325)]
    return Stroke(t, np.repeat(xy[None, :], len(t), axis=0), PenState.IN_AIR)


def dwell_gap_s(dwell: MoveList) -> float:
    """The time of the dwell move of `fault-dwell`."""
    first_long = next(i for i, m in enumerate(dwell.moves) if m.duration_s > 0.4)
    return sum(m.duration_s for m in dwell.moves[:first_long])


def test_the_trial_of_a_row_is_found_and_every_disagreement_is_an_error(tmp_path: Path) -> None:
    # Detects a lookup that returns a trial of another program, run, or session, or that passes a
    # row whose clip, run index, or program differs from the trial.
    write_export(tmp_path / "ipad", fixture_lines("bench_ipad"))
    journal = read_bench_journal(tmp_path / "ipad")
    trial = trial_of(journal, row())
    assert trial.index == 0 and trial.task.id == "bench-loops-l"
    assert trial_of(journal, row("fault-lift", 1, 2)).index == 2
    cases: list[tuple[str, dict[str, Any]]] = [
        ("records", {"trial_index": 1}),
        ("no trial 9", {"trial_index": 9}),
        ("records", {"run_id": "P-M1-loops-l-v20-r2", "clip": "P-M1-loops-l-v20-r2", "run_index": 2}),
        ("mounting M1", {"mounting": "M2"}),
        ("is device P", {"device": "W"}),
    ]
    for message, changes in cases:
        with pytest.raises(InputError, match=message):
            trial_of(journal, row(**changes))


def test_comparison_values_follow_the_scale_of_each_feature() -> None:
    # Detects a logarithm on speed_cv, a raw value on a logarithmic feature, or a refusal or a
    # non-positive value that does not become NaN.
    assert comparison_value("bench.path_length_mm", 2.0) == pytest.approx(math.log(2.0))
    assert comparison_value("bench.speed_cv", 0.3) == 0.3
    assert math.isnan(comparison_value("bench.motion_s", Refusal(RefusalCode.UNDEFINED, "x")))
    assert math.isnan(comparison_value("bench.band_power_mm2_s2", 0.0))


@pytest.fixture(scope="module")
def clean(move_lists: dict[str, MoveList]) -> Evaluated:
    ml = move_lists["loops-s-v40"]
    return evaluate(
        row("loops-s-v40"), device_run(ml, noise_mm=0.005, seed=2), ml, camera_track(ml, noise_mm=0.005)
    )


def test_a_clean_run_with_a_clean_track_passes_with_every_measure(clean: Evaluated) -> None:
    # Detects an outcome that fails a clean run, or an evaluation that drops the camera check, the
    # alignment, the accuracy, or the trace error.
    assert not clean.outcome.failed and clean.outcome.reason == ""
    assert clean.outcome.camera is CameraState.VALID and clean.outcome.motion_failure is False
    assert isinstance(clean.executed, ExecutedMovement) and not clean.executed.failure
    assert isinstance(clean.alignment, TimeAlignment) and clean.alignment.lag_s == pytest.approx(
        0.6, abs=0.002
    )
    assert isinstance(clean.accuracy, SpatialAccuracy) and clean.accuracy.rms_mm < 0.05
    assert isinstance(clean.trace, PathFit) and clean.trace.rms_mm < 0.02
    assert not isinstance(clean.library["timing.stroke_count"], Refusal)
    for name in ("bench.motion_s", "bench.path_length_mm", "bench.speed_mean_mm_s", "bench.loop_height_mm"):
        assert abs(clean.camera_difference(name)) < 0.01, name


def test_a_scaled_device_record_gives_the_scale_as_the_camera_difference(
    move_lists: dict[str, MoveList], clean: Evaluated
) -> None:
    # Detects a camera difference with the wrong sign or scale: a device that reports 6 percent more
    # must differ from the camera by ln 1.06 on the path length.
    ml = move_lists["loops-s-v40"]
    run = evaluate(row("loops-s-v40"), scaled(clean.trial, 1.06), ml, camera_track(ml, noise_mm=0.005))
    assert run.camera_difference("bench.path_length_mm") == pytest.approx(math.log(1.06), abs=0.001)
    assert run.camera_difference("bench.motion_s") == pytest.approx(0.0, abs=0.01)


def test_each_failure_cause_gives_its_outcome(move_lists: dict[str, MoveList], clean: Evaluated) -> None:
    # Detects a mark, a rejection, or a motion failure that does not fail the run, an exclude mark that
    # fails it, a motion failure that also fails the sensitivity status, or a missing or invalid camera
    # record that fails the run or gives a camera value.
    ml = move_lists["loops-s-v40"]
    marked = evaluate(row("loops-s-v40"), replace(clean.trial, marks=("disturbance",)), ml, None)
    assert marked.outcome.failed and marked.outcome.marks == ("disturbance",)
    assert marked.outcome.camera is CameraState.ABSENT and marked.outcome.motion_failure is None
    excluded = evaluate(row("loops-s-v40"), replace(clean.trial, marks=("exclude",)), ml, None)
    assert excluded.outcome.failed and excluded.outcome.rejected == (RefusalCode.EXCLUDED,)
    assert excluded.outcome.marks == () and isinstance(excluded.qc, Rejected)
    assert isinstance(excluded.features, Refusal)
    assert all(isinstance(c, Refusal) for c in excluded.library.values())
    assert isinstance(excluded.device_value("bench.motion_s"), Refusal)
    interrupted = evaluate(
        row("loops-s-v40"), replace(clean.trial, outcome=TrialOutcome.INTERRUPTED), ml, None
    )
    assert interrupted.outcome.rejected == (RefusalCode.INCOMPLETE_TRIAL,) and interrupted.outcome.failed
    absent = evaluate(row("loops-s-v40"), clean.trial, ml, None)
    assert not absent.outcome.failed and absent.outcome.camera is CameraState.ABSENT
    assert math.isnan(absent.camera_difference("bench.path_length_mm"))
    assert isinstance(absent.trace, PathFit)
    bumped = evaluate(row("loops-s-v40"), clean.trial, ml, camera_track(ml, noise_mm=0.005, bump=(3.0, 0.3)))
    assert bumped.outcome.failed and bumped.outcome.motion_failure is True
    assert not bumped.outcome.failed_without_motion_check and bumped.outcome.reason == "motion_failure"
    invalid = evaluate(row("loops-s-v40"), clean.trial, ml, camera_track(ml, missing_share=0.03))
    assert not invalid.outcome.failed and invalid.outcome.camera is CameraState.INVALID
    assert invalid.outcome.camera_rules == (CameraRule.MARKER_MISSING,) and invalid.executed is None
    assert isinstance(invalid.camera_value("bench.path_length_mm"), Refusal)
    assert math.isnan(invalid.camera_difference("bench.path_length_mm"))


def test_a_camera_record_without_an_executed_movement_check_is_invalid(
    move_lists: dict[str, MoveList], clean: Evaluated
) -> None:
    # Detects a run whose camera record passes the rules of section 7.3 but has no executed movement
    # check of section 7.6, and still enters the camera-referenced comparison with its camera values.
    ml = move_lists["loops-s-v40"]
    run = evaluate(
        row("loops-s-v40"),
        clean.trial,
        ml,
        camera_track(ml, noise_mm=0.005),
        commanded=Refusal(RefusalCode.UNDEFINED, "no command"),
    )
    assert isinstance(run.executed, Refusal)
    assert run.outcome.camera is CameraState.INVALID and run.outcome.motion_failure is None
    assert not run.outcome.failed
    assert isinstance(run.camera_value("bench.path_length_mm"), Refusal)
    assert math.isnan(run.camera_difference("bench.path_length_mm"))


def test_each_fault_rule_detects_its_fault_and_rejects_a_clean_run(move_lists: dict[str, MoveList]) -> None:
    # Detects a fault rule that passes a run without the fault, fails the fault it expects, or reads
    # the device P dwell expectation the wrong way round.
    base = device_run(move_lists["loops-l-v20"])
    clean_cells = features_tests.features_of(base)
    medians = CleanMedians(clean_cells["bench.motion_s"], clean_cells["bench.path_length_mm"])
    runs = {
        pid: evaluate(row(pid), device_run(move_lists[pid]), move_lists[pid], None)
        for pid in ("fault-dwell", "fault-lift", "fault-truncated")
    }
    assert fault_detected(runs["fault-dwell"], medians, gaps_on_p=False) == (True, "")
    assert fault_detected(runs["fault-dwell"], medians, gaps_on_p=True)[1].startswith("no gaps")
    dwell = move_lists["fault-dwell"]
    at = dwell_gap_s(dwell)
    with_gap = evaluate(row("fault-dwell"), device_run(dwell, drop=(at + 0.02, at + 0.48)), dwell, None)
    assert fault_detected(with_gap, medians, gaps_on_p=True) == (True, "")
    assert fault_detected(with_gap, medians, gaps_on_p=False) == (False, "a gaps finding")
    assert fault_detected(runs["fault-lift"], medians, gaps_on_p=False) == (True, "")
    assert fault_detected(runs["fault-truncated"], medians, gaps_on_p=False) == (True, "")
    whole = evaluate(row("fault-truncated"), base, move_lists["fault-truncated"], None)
    assert fault_detected(whole, medians, gaps_on_p=False)[1].startswith("path_length_mm")
    one_stroke = evaluate(row("fault-lift"), base, move_lists["fault-lift"], None)
    assert fault_detected(one_stroke, medians, gaps_on_p=False) == (False, "stroke_count 1 is not 2")
    short = evaluate(row("fault-dwell"), base, move_lists["fault-dwell"], None)
    assert fault_detected(short, medians, gaps_on_p=False)[1].startswith("motion_s")
    with pytest.raises(ValueError, match="not a fault program"):
        fault_detected(
            evaluate(row("loops-l-v20"), base, move_lists["loops-l-v20"], None), medians, gaps_on_p=False
        )
    assert np.isfinite(clean_cells["bench.motion_s"])


def test_the_dwell_rule_counts_contact_segments_and_contact_gaps_only(
    move_lists: dict[str, MoveList],
) -> None:
    # Detects a dwell rule that counts the in-air strokes of a bench journal as segments, or an in-air
    # gap as the dwell gap. The library counts every segment and reports a gap in any stroke, so the
    # rule must look at the contact strokes only.
    base = device_run(move_lists["loops-l-v20"])
    cells = features_tests.features_of(base)
    medians = CleanMedians(cells["bench.motion_s"], cells["bench.path_length_mm"])
    dwell = move_lists["fault-dwell"]
    at = dwell_gap_s(dwell)
    with_gap = device_run(dwell, drop=(at + 0.02, at + 0.48))
    first, last = with_gap.strokes[0], with_gap.strokes[-1]
    hovered = replace(
        with_gap,
        strokes=(
            hover(first.t[0] - 0.8, first.xy[0], 0.5),
            *with_gap.strokes,
            hover(last.t[-1] + 0.4, last.xy[-1], 0.5),
        ),
    )
    run = evaluate(row("fault-dwell"), hovered, dwell, None)
    assert run.qc.report.segment_count == 4
    assert fault_detected(run, medians, gaps_on_p=True) == (True, "")
    assert fault_detected(run, medians, gaps_on_p=False) == (False, "a gaps finding")
    clean = device_run(dwell)
    start = clean.strokes[0]
    gappy = replace(clean, strokes=(hover(start.t[0] - 1.0, start.xy[0], 0.6, gap=True), *clean.strokes))
    run = evaluate(row("fault-dwell"), gappy, dwell, None)
    assert RefusalCode.GAPS in [r.code for r in run.qc.report.findings]
    assert fault_detected(run, medians, gaps_on_p=False) == (True, "")
    expected = (False, "no gaps finding with two contact segments")
    assert fault_detected(run, medians, gaps_on_p=True) == expected
