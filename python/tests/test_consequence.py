from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import test_bench_moves as moves_tests
from scipy.special import expit, logit
from scipy.stats import norm

from scriptkin import bench
from scriptkin.bench.analysis import correction_model
from scriptkin.bench.moves import MoveList
from scriptkin.consequence.generate import correct_w, generate_cohort, observe
from scriptkin.consequence.inputs import (
    Centers,
    Coefficients,
    centers_for,
    grid_inputs,
    read_deliverables,
    select_program,
)
from scriptkin.consequence.metrics import (
    MetricSet,
    measure,
    paired_contrasts,
    summarize,
    weighted_auc,
    weighted_calibration,
)
from scriptkin.consequence.plan import (
    FEATURES,
    LIMITS,
    CellSpec,
    ConsequencePlan,
    ProgramTarget,
    SeedKey,
    cells,
    planned_replicates,
    seed_for,
)
from scriptkin.consequence.report import run_unit, write_csv, write_json, write_outputs
from scriptkin.consequence.validation import (
    Failure,
    PredictionSet,
    audit_fits,
    evaluate_matrix,
    make_splits,
    matrix,
    participant_weights,
    permute_participants,
)
from scriptkin.evaluate.screening import CrossFit, FitRecord, FoldFit

programs_folder = moves_tests.programs_folder
move_lists = moves_tests.move_lists


@pytest.fixture(scope="session")
def centers(move_lists: dict[str, MoveList]) -> Centers:
    return centers_for(move_lists)


PLAN = ConsequencePlan()
CELL = CellSpec(n=120, effect=0.8)


def fixture_document(centers: Centers) -> dict[str, Any]:
    features: dict[str, Any] = {}
    for i, name in enumerate(FEATURES):
        f = grid_inputs(PLAN, CellSpec(features=(name,)), centers=centers).features[0]
        model = correction_model(name)
        program = {
            "bias": {"estimate": 0.0, "lower": -0.001, "upper": 0.001},
            "se": 0.001,
            "factor": {"estimate": 1.0, "lower": float(np.exp(-0.001)), "upper": float(np.exp(0.001))},
            "components": {d: {"run": 0.0001, "mount": 0.0001} for d in ("P", "W")},
        }
        features[name] = {
            "limit": LIMITS[name],
            "scale": "absolute" if i == 4 else "ln",
            "values": {"bias": f.bias_basis, "correction": "raw_device"},
            "featureP": 1.0,
            "equivalent": {"adjusted": False, "unadjusted": False},
            "range": {},
            "programs": {f.selection.program.id: program},
            "correction": {
                "model": model.value,
                "coefficients": {
                    term: {"estimate": value, "se": 0.001, "fitted": fitted}
                    for term, value, fitted in (
                        ("alpha", 0.0, True),
                        ("beta", 1.0, model.fits_slope),
                        ("gamma_u", 0.0, model.fits_covariates),
                        ("gamma_v", 0.0, model.fits_covariates),
                    )
                },
                "residual": {
                    f.selection.program.id: {
                        "estimate": 0.0,
                        "lower": -0.001,
                        "upper": 0.001,
                        "withinLimit": True,
                    }
                },
            },
        }
    return {
        "protocol": {"version": bench.PROTOCOL_VERSION},
        "analysis": "main",
        "lock": {"commit": "a" * 40},
        "library": "scriptkin",
        "postHoc": None,
        "features": features,
    }


def read_fixture(tmp_path: Path, document: dict[str, Any], centers: Centers) -> Any:
    path = tmp_path / "deliverables.json"
    path.write_text(json.dumps(document))
    return read_deliverables(path, centers, expected_protocol=bench.PROTOCOL_VERSION)


def prediction(scheme: str = "participant") -> PredictionSet:
    cohort = generate_cohort(CELL, seed=5)
    data = matrix(cohort, cohort.x, scheme)
    splits = make_splits(data, scheme, seed=7)
    result = evaluate_matrix(data, PLAN, splits, seed=9)
    assert isinstance(result, PredictionSet)
    return result


def test_input_shape(tmp_path: Path, centers: Centers) -> None:
    # Defect: The reader accepts a missing required field.
    raw = fixture_document(centers)
    del raw["analysis"]
    with pytest.raises(ValueError):
        read_fixture(tmp_path, raw, centers)


def test_variance_units(centers: Centers) -> None:
    # Defect: Noise uses a variance as a standard deviation.
    c = generate_cohort(replace(CELL, n=50000), seed=4)
    inputs = grid_inputs(PLAN, CELL, centers=centers)
    f = replace(inputs.features[0], run_p=0.04, mount_p=0)
    observed = observe(c, replace(inputs, features=(f,)), seed=8)
    residual = observed.reference[:, :, 0] - c.x[:, :, 0] - f.center
    assert abs(float(residual.var()) - 0.04) < 5 * 0.04 * np.sqrt(2 / 100000)


def test_bias_direction(centers: Centers) -> None:
    # Defect: W receives the P/W factor in the wrong direction.
    cell = replace(CELL, bias_multiple=1.0, noise_id="zero")
    c = generate_cohort(cell, seed=5)
    o = observe(c, grid_inputs(PLAN, cell, centers=centers), seed=6)
    np.testing.assert_allclose(o.reference - o.uncorrected, LIMITS[FEATURES[0]])


def test_absolute_scale(centers: Centers) -> None:
    # Defect: Speed CV receives a logarithmic transform.
    cell = replace(CELL, features=(FEATURES[4],), bias_multiple=1.0, noise_id="zero")
    c = generate_cohort(cell, seed=5)
    o = observe(c, grid_inputs(PLAN, cell, centers=centers), seed=6)
    np.testing.assert_allclose(o.reference - o.uncorrected, 0.030)


def test_missing_program() -> None:
    # Defect: An absent geometry is silently replaced.
    with pytest.raises(ValueError):
        select_program(ProgramTarget(), [bench.MAIN[-1]])


def test_program_tie() -> None:
    # Defect: Program ties depend on input order.
    p = bench.MAIN[0]
    a, b = replace(p, id="a"), replace(p, id="b")
    assert select_program(ProgramTarget(), [b, a]).program == a
    assert select_program(ProgramTarget(), [a, b]).program == a


def test_program_outside_range() -> None:
    # Defect: An extrapolated target is marked within range.
    assert not select_program(ProgramTarget(speed_mm_s=100.0), bench.MAIN).in_range


def test_raw_correction_basis(tmp_path: Path, centers: Centers) -> None:
    # Defect: The reader accepts a camera difference as a raw correction input.
    raw = fixture_document(centers)
    raw["features"][FEATURES[0]]["values"]["correction"] = "camera_referenced"
    with pytest.raises(ValueError):
        read_fixture(tmp_path, raw, centers)


def test_fixed_terms(tmp_path: Path, centers: Centers) -> None:
    # Defect: A shift model changes beta.
    raw = fixture_document(centers)
    raw["features"][FEATURES[5]]["correction"]["coefficients"]["beta"]["estimate"] = 1.1
    with pytest.raises(ValueError):
        read_fixture(tmp_path, raw, centers)


def test_correction_formula() -> None:
    # Defect: Correction omits gamma covariates or changes P values.
    x = np.ones((2, 2))
    np.testing.assert_allclose(
        correct_w(x, np.array([0, 1]), Coefficients(2, 3, 4, 5), 2, 3), [[1, 1], [28, 28]]
    )


def test_failed_check_retained(tmp_path: Path, centers: Centers) -> None:
    # Defect: A failed correction disappears from diagnostic inputs.
    raw = fixture_document(centers)
    residual = raw["features"][FEATURES[0]]["correction"]["residual"]
    next(iter(residual.values()))["withinLimit"] = False
    f = read_fixture(tmp_path, raw, centers).features[0]
    assert f.correction is not None and f.support == "failed_lack_of_fit"


def test_mounting_dependence(centers: Centers) -> None:
    # Defect: Mounting effects are redrawn per session.
    c = generate_cohort(CELL, seed=4)
    inputs = grid_inputs(PLAN, CELL, centers=centers)
    f = replace(inputs.features[0], run_w=0.0, mount_w=0.1)
    o = observe(c, replace(inputs, features=(f,)), seed=9)
    error = o.uncorrected[:, :, 0] - c.x[:, :, 0] - f.center
    np.testing.assert_allclose(error[:, 0], error[:, 1], atol=1e-14)
    for block in range(6):
        assert np.ptp(error[c.block == block]) < 1e-14


def test_rule_b_control() -> None:
    # Defect: The L-sized shift gives the wrong theoretical scalar AUC.
    c = generate_cohort(replace(CELL, n=50000, effect=0), seed=1)
    scale = LIMITS[FEATURES[0]] / (np.sqrt(2) * norm.ppf(0.55))
    assert norm.cdf(LIMITS[FEATURES[0]] / (np.sqrt(2) * scale)) == pytest.approx(0.55)
    assert abs(float(c.x[:, 0, 0].var()) - scale**2) < 5 * scale**2 * np.sqrt(2 / 50000)


def test_zero_difference_pair(centers: Centers) -> None:
    # Defect: Equal device inputs give different paired observations.
    c = generate_cohort(CELL, seed=2)
    inputs = grid_inputs(PLAN, CELL, centers=centers)
    f = replace(inputs.features[0], mount_p=0.0, mount_w=0.0)
    o = observe(c, replace(inputs, features=(f,)), seed=3)
    np.testing.assert_array_equal(o.reference, o.uncorrected)


def test_oracle_shift_noise(centers: Centers) -> None:
    # Defect: Oracle correction removes random W noise.
    c = generate_cohort(CELL, seed=2)
    inputs = grid_inputs(PLAN, replace(CELL, bias_multiple=1.0), centers=centers)
    o = observe(c, inputs, seed=3)
    assert o.corrected is not None
    np.testing.assert_allclose(o.corrected - o.uncorrected, LIMITS[FEATURES[0]])
    assert np.std(o.corrected[:, :, 0] - c.x[:, :, 0]) > 0


def test_participant_label() -> None:
    # Defect: Labels change between sessions.
    c = generate_cohort(CELL, seed=2)
    d = matrix(c, c.x, "record").design
    np.testing.assert_array_equal(d.y[::2], d.y[1::2])


def test_seed_stability() -> None:
    # Defect: Adding a cell changes an existing seed.
    key = SeedKey(0.8, 600, 0.3, 4, 1)
    value = seed_for(PLAN.seed, key)
    seed_for(PLAN.seed, replace(key, stream=99))
    assert seed_for(PLAN.seed, key) == value
    a, b = generate_cohort(CELL, seed=value), generate_cohort(replace(CELL, bias_multiple=2.0), seed=value)
    np.testing.assert_array_equal(a.x, b.x)


def test_participant_split() -> None:
    # Defect: A participant crosses a protected outer split.
    c = generate_cohort(CELL, seed=2)
    d = matrix(c, c.x, "participant")
    for fold in make_splits(d, "participant", seed=3).folds:
        assert not {d.design.ids[i] for i in fold.train} & {d.design.ids[i] for i in fold.test}


def test_device_split() -> None:
    # Defect: A held-out device appears in training.
    c = generate_cohort(replace(CELL, scenario="transfer"), seed=2)
    d = matrix(c, c.x, "device")
    for fold in make_splits(d, "device", seed=3).folds:
        assert not set(d.device[fold.train]) & set(d.device[fold.test])


def test_record_overlap_reported() -> None:
    # Defect: Record validation hides participant overlap.
    assert sum(prediction("record").audit.overlaps) > 0


def test_inner_witness() -> None:
    # Defect: A calibration fit accesses an outer test row.
    c = generate_cohort(CELL, seed=5)
    d = matrix(c, c.x, "record")
    splits = make_splits(d, "record", seed=7)
    p = prediction("record")
    wrong = FitRecord(
        0,
        "calibration",
        frozenset(d.design.ids[i] for i in splits.folds[0].train),
        frozenset([int(splits.folds[0].test[0])]),
    )
    with pytest.raises(ValueError, match="test row"):
        audit_fits(replace(p.cross, log=(*p.cross.log, wrong)), d, splits)


def test_fold_auc() -> None:
    # Defect: AUC compares scores from separate outer models.
    assert (
        weighted_auc(
            np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 1, 0, 1]), np.array([0, 0, 1, 1]), np.ones(4)
        )
        == 1
    )


def test_record_weights() -> None:
    # Defect: More records give a participant more metric weight.
    w = participant_weights(("a", "a", "a", "b"), np.arange(4))
    assert w[:3].sum() == w[3] == 1


def test_prior_shift() -> None:
    # Defect: Risk adjustment uses pooled training prevalence.
    c = CrossFit(np.array([[0.2, 0.7]]), np.array([[0, 1]]), (FoldFit(10, 0.2, 0), FoldFit(10, 0.7, 0)), ())
    np.testing.assert_allclose(c.shifted(0.3), 0.3)


def test_calibration_offset() -> None:
    # Defect: Calibration intercept uses a free slope.
    p = np.tile(np.array([0.2, 0.8]), 100)
    y = np.tile(np.array([0, 1]), 100)
    a, _ = weighted_calibration(p, y, np.ones(len(y)))
    assert abs(float(np.mean(y - expit(logit(p) + a)))) < 1e-7


def test_constant_slope() -> None:
    # Defect: Constant predictions get a finite calibration slope.
    _, slope = weighted_calibration(np.full(10, 0.3), np.array([0] * 7 + [1] * 3), np.ones(10))
    assert np.isnan(slope)


def test_net_benefit() -> None:
    # Defect: False positives use the wrong threshold odds penalty.
    c = CrossFit(
        np.array([[0.9, 0.9, 0.1, 0.1]]), np.zeros((1, 4), dtype=np.int64), (FoldFit(10, 0.5, 0),), ()
    )
    from scriptkin.consequence.validation import FitAudit

    p = PredictionSet(c, np.array([1, 0, 1, 0]), np.ones(4), FitAudit((), (), ()))
    m = measure(p, prevalence=0.5, thresholds=np.array([0.2]))
    assert m.values["mean_nb"] == pytest.approx(0.25 - 0.25 * 0.2 / 0.8)


def test_cluster_permutation() -> None:
    # Defect: Permutation gives two sessions of one participant different labels.
    c = generate_cohort(CELL, seed=5)
    d = permute_participants(matrix(c, c.x, "record"), seed=4)
    np.testing.assert_array_equal(d.design.y[::2], d.design.y[1::2])


def test_one_class_holdout() -> None:
    # Defect: Full confounding gives a fabricated device metric.
    c = generate_cohort(replace(CELL, scenario="confounded", association=1.0), seed=5)
    d = matrix(c, c.x, "device")
    result = evaluate_matrix(d, PLAN, make_splits(d, "device", seed=3), seed=4)
    assert isinstance(result, Failure) and result.reason == "one_class_holdout"


def test_paired_summary() -> None:
    # Defect: A summary pairs different replicate identifiers.
    with pytest.raises(ValueError):
        summarize([{"replicate": 1, "estimate": 0.1}, {"replicate": 1, "estimate": 0.2}])
    m = {
        v: MetricSet({"auc": x}, (), ())
        for v, x in (("reference", 0.5), ("uncorrected", 0.7), ("corrected", 0.6))
    }
    assert paired_contrasts(m)[0]["absolute_reduction"] == pytest.approx(0.1)


def test_failure_denominator() -> None:
    # Defect: Summary hides failed pairs.
    s = summarize([{"replicate": 0, "estimate": 0.1}, {"replicate": 1, "estimate": None}], planned=2)
    assert s["planned_R"] == 2 and s["usable_R"] == 1 and s["status"] == "incomplete"


def test_output_roundtrip(tmp_path: Path) -> None:
    # Defect: Export changes an estimate or unavailable status.
    rows = [{"estimate": 0.1234567891234567, "status": "ok"}, {"estimate": None, "status": "unavailable"}]
    path = tmp_path / "test.csv"
    write_csv(path, rows)
    with path.open() as stream:
        result = list(csv.DictReader(stream))
    assert float(result[0]["estimate"]) == rows[0]["estimate"]
    assert result[1] == {"estimate": "", "status": "unavailable"}
    write_json(tmp_path / "test.json", {"estimate": float("nan")})
    assert json.loads((tmp_path / "test.json").read_text()) == {"estimate": None}


def test_smoke_tables(tmp_path: Path, centers: Centers) -> None:
    # Defect: A smoke run creates a primary table.
    cell = next(c for c in cells(PLAN) if c.noise_id == "zero")
    write_outputs(PLAN, tmp_path, cell_id=cell.id, replicate=0, smoke=True, centers=centers)
    assert not (tmp_path / "table_primary.csv").exists()
    with pytest.raises(ValueError, match="different plan"):
        write_outputs(replace(PLAN, seed=1), tmp_path, cell_id=cell.id, smoke=True, centers=centers)


def test_failed_run_rows(centers: Centers) -> None:
    # Defect: Structural holdout failures disappear from exports.
    cell = replace(CELL, noise_id="zero")
    unit = run_unit(PLAN, cell, 0, grid_inputs(PLAN, cell, centers=centers), smoke=True)
    assert any(r["reason"] == "not_applicable_one_device" for r in unit["tables"]["failures"])


def test_value_bases(centers: Centers) -> None:
    # Defect: Band power is treated as a camera-referenced difference.
    assert (
        grid_inputs(PLAN, CellSpec(features=(FEATURES[5],)), centers=centers).features[0].bias_basis
        == "raw_device"
    )
    assert (
        grid_inputs(PLAN, CellSpec(features=(FEATURES[3],)), centers=centers).features[0].bias_basis
        == "camera_referenced"
    )


def test_completed_unit_collision(tmp_path: Path, centers: Centers) -> None:
    # Defect: A restart reuses a completed unit with a different input hash.
    cell = next(c for c in cells(PLAN) if c.noise_id == "zero")
    write_outputs(PLAN, tmp_path, cell_id=cell.id, replicate=0, smoke=True, centers=centers)
    import gzip
    from zipfile import ZipFile

    from scriptkin.consequence.storage import read_restart_unit, unit_name

    unit = read_restart_unit(tmp_path / "units", cell.id, 0)
    assert unit is not None
    unit["inputs_sha256"] = "different"
    with ZipFile(tmp_path / "units" / f"{cell.id}.zip", "w") as archive:
        archive.writestr(unit_name(cell.id, 0), gzip.compress(json.dumps(unit).encode(), mtime=0))
    with pytest.raises(ValueError, match="different input"):
        write_outputs(PLAN, tmp_path, cell_id=cell.id, replicate=0, smoke=True, centers=centers)


def test_no_all_p_cells() -> None:
    # Defect: The grid includes an all-P cell that repeats its reference.
    assert {c.scenario for c in cells(PLAN)} == {"all_w", "transfer", "confounded"}
    with pytest.raises(ValueError, match="scenario"):
        CellSpec(scenario="all_p")


def test_registered_limits(monkeypatch: pytest.MonkeyPatch, centers: Centers) -> None:
    # Defect: The consequence experiment keeps an old registered limit.
    from scriptkin.bench.analysis import LIMITS as bench_limits
    from scriptkin.bench.runs import FEATURE_SET

    assert FEATURES is FEATURE_SET and LIMITS is bench_limits
    monkeypatch.setitem(bench_limits, FEATURES[0], 0.123)
    c = replace(CELL, bias_multiple=1.0)
    assert grid_inputs(PLAN, c, centers).features[0].bias == 0.123


def test_joint_features() -> None:
    # Defect: A joint cell loses its explicit feature set.
    joint = next(c for c in cells(PLAN) if c.sensitivity == "joint")
    assert joint.features == FEATURES
    assert generate_cohort(joint, seed=9).x.shape[-1] == len(FEATURES)


def test_cell_replicates() -> None:
    # Defect: A sensitivity uses the primary replicate count.
    assert all(
        planned_replicates(PLAN, c) == (200 if c.sensitivity == "primary" else 100) for c in cells(PLAN)
    )


def test_compact_audit(centers: Centers) -> None:
    # Defect: A later replicate writes full membership instead of its hashes.
    from scriptkin.consequence.report import membership_hash

    cell = replace(CELL, n=60, scenario="transfer", bias_multiple=1.0, noise_id="zero")
    inputs = grid_inputs(PLAN, cell, centers)
    compact = run_unit(PLAN, cell, 1, inputs, smoke=False)
    full = run_unit(PLAN, cell, 1, inputs, smoke=True)
    assert compact["audit_detail"] == "compact" and full["audit_detail"] == "full"
    assert all("row_id" not in row for row in compact["tables"]["splits"])
    keys = ("validation", "direction", "version", "fold", "stage")
    for row in compact["audit"]:
        matches = [r for r in full["full_audit"] if all(r[k] == row[k] for k in keys)]
        train = sorted({i for r in matches for i in r["training_rows"]})
        test = sorted(
            r["row_id"]
            for r in full["tables"]["splits"]
            if r["role"] == "test" and all(r[k] == row[k] for k in ("validation", "direction", "fold"))
        )
        assert row["training_sha256"] == membership_hash(train)
        assert row["test_sha256"] == membership_hash(test)
        assert row["training_count"] == len(train) and row["test_count"] == len(test)
        assert row["fit_count"] == len(matches)
        assert "training_rows" not in row and "training_participants" not in row
    assert len({tuple(r[k] for k in keys) for r in compact["audit"]}) == len(compact["audit"])
    assert any(r["test_overlap"] for r in compact["audit"] if r["validation"] == "record")


def test_replicate_zero_audit(centers: Centers) -> None:
    # Defect: Replicate zero loses its full fit record.
    cell = replace(CELL, n=60, bias_multiple=1.0, noise_id="zero")
    result = run_unit(PLAN, cell, 0, grid_inputs(PLAN, cell, centers), smoke=False)
    assert result["audit_detail"] == "full"
    assert all("training_rows" in r for r in result["full_audit"])
    assert all("row_id" in r for r in result["tables"]["splits"])


def test_compact_leakage_check(monkeypatch: pytest.MonkeyPatch, centers: Centers) -> None:
    # Defect: Compact output bypasses the fit leakage check.
    import scriptkin.consequence.validation as validation

    def refuse(*args: object) -> None:
        raise ValueError("injected leakage")

    monkeypatch.setattr(validation, "audit_fits", refuse)
    cell = replace(CELL, bias_multiple=1.0, noise_id="zero")
    with pytest.raises(ValueError, match="injected leakage"):
        run_unit(PLAN, cell, 1, grid_inputs(PLAN, cell, centers), smoke=False)


def test_worker_count(tmp_path: Path, centers: Centers) -> None:
    # Defect: The result depends on the worker count.
    specs = (
        replace(CELL, n=60, bias_multiple=1.0, noise_id="zero"),
        replace(
            CELL, n=60, bias_multiple=1.0, noise_id="zero", sensitivity="share095", participant_share=0.95
        ),
    )
    plan = ConsequencePlan(replicates=2, sensitivity_replicates=1, folds=3, inner_folds=2, cell_specs=specs)
    first, second = tmp_path / "one", tmp_path / "two"
    write_outputs(plan, first, centers=centers, workers=1)
    write_outputs(plan, second, centers=centers, workers=2)

    def contents(folder: Path) -> dict[str, bytes]:
        return {
            p.relative_to(folder).as_posix(): p.read_bytes()
            for p in folder.rglob("*")
            if p.is_file() and not any(part.startswith("._") for part in p.parts)
        }

    assert contents(first) == contents(second)
    with (first / "consequence_summary.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        assert int(row["planned_R"]) == (2 if row["sensitivity"] == "primary" else 1)
    write_outputs(plan, first, centers=centers, workers=2)
    assert contents(first) == contents(second)


def test_sensitivity_completeness(tmp_path: Path, centers: Centers) -> None:
    # Defect: Sensitivity completeness uses the primary replicate denominator.
    spec = replace(CELL, n=60, bias_multiple=1.0, noise_id="zero", sensitivity="share095")
    plan = ConsequencePlan(replicates=200, sensitivity_replicates=2, cell_specs=(spec,))
    write_outputs(plan, tmp_path, centers=centers)
    with (tmp_path / "consequence_summary.csv").open() as stream:
        rows = [
            r for r in csv.DictReader(stream) if r["validation"] == "participant" and r["metric"] == "auc"
        ]
    assert rows and all(r["planned_R"] == "2" and r["status"] == "ok" for r in rows)


def test_study_move_lists(tmp_path: Path, move_lists: dict[str, MoveList], programs_folder: Path) -> None:
    # Defect: The study loader changes a registered move list when it builds it in memory.
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[1] / "studies/consequence.py"
    spec = importlib.util.spec_from_file_location("consequence_study_test", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = script
    spec.loader.exec_module(script)
    built = script.load_move_lists(tmp_path)
    read = script.load_move_lists(programs_folder)
    assert set(built) == set(read) == {d.id for d in bench.MAIN}
    for pid in built:
        assert built[pid] == read[pid] == move_lists[pid]


@pytest.mark.parametrize(
    ("scheme", "version", "replicate", "sensitivity", "bias", "noise", "smoke", "expected"),
    [
        ("participant", "uncorrected", 0, "primary", 0.0, "nominal", False, True),
        ("device", "uncorrected", 19, "primary", 0.0, "nominal", False, True),
        ("record", "uncorrected", 0, "primary", 0.0, "nominal", False, False),
        ("participant", "reference", 0, "primary", 0.0, "nominal", False, False),
        ("device", "corrected", 0, "primary", 0.0, "nominal", False, False),
        ("participant", "uncorrected", 20, "primary", 0.0, "nominal", False, False),
        ("participant", "uncorrected", 0, "joint", 0.0, "nominal", False, False),
        ("participant", "uncorrected", 0, "knn", 0.0, "nominal", False, False),
        ("participant", "uncorrected", 0, "primary", 1.0, "nominal", False, False),
        ("participant", "uncorrected", 0, "primary", 0.0, "stress", False, False),
        ("participant", "uncorrected", 0, "primary", 0.0, "nominal", True, False),
    ],
)
def test_null_schedule(
    scheme: str,
    version: str,
    replicate: int,
    sensitivity: str,
    bias: float,
    noise: str,
    smoke: bool,
    expected: bool,
) -> None:
    # Defect: A permutation diagnostic runs outside its fixed schedule.
    from scriptkin.consequence.report import runs_null

    cell = replace(CELL, sensitivity=sensitivity, bias_multiple=bias, noise_id=noise)
    assert runs_null(cell, replicate, scheme, version, smoke=smoke) == expected


def test_null_procedure_count(centers: Centers) -> None:
    # Defect: An eligible diagnostic omits a permutation or runs an extra validation scheme.
    cell = replace(CELL, n=60, scenario="transfer")
    plan = replace(PLAN, folds=3, inner_folds=2)
    raw = run_unit(plan, cell, 1, grid_inputs(plan, cell, centers), smoke=False)
    checks = [r for r in raw["audit"] if r["stage"] == "null_check"]
    assert {(r["validation"], r["direction"], r["version"]) for r in checks} == {
        ("participant", "internal", "uncorrected"),
        ("device", "W-to-P", "uncorrected"),
        ("device", "P-to-W", "uncorrected"),
    }
    assert all(len(r["aucs"]) == 50 for r in checks)
    fits = [r for r in raw["audit"] if "permutation" in r]
    assert {r["permutation"] for r in fits} == set(range(50))
    assert all(r["version"] == "uncorrected" and r["validation"] != "record" for r in fits)


@pytest.mark.parametrize("failed", [False, True])
def test_packed_unit_roundtrip(centers: Centers, failed: bool) -> None:
    # Defect: Compact storage changes a metric, curve, calibration bin, or unavailable value.
    from scriptkin.consequence.storage import clean, expand_unit, pack_unit

    cell = replace(CELL, n=60, scenario="transfer", bias_multiple=1.0)
    inputs = grid_inputs(PLAN, cell, centers)
    if failed:
        inputs = replace(inputs, features=tuple(replace(f, correction=None) for f in inputs.features))
    raw = run_unit(PLAN, cell, 1, inputs, smoke=False)
    packed = pack_unit(raw)
    cell_row = {k: v for k, v in raw["common"].items() if k not in ("replicate", "negative_cv")}
    restored = expand_unit(packed, cell_row, PLAN.thresholds)

    def ordered(rows: list[dict[str, Any]]) -> list[str]:
        return sorted(json.dumps(row, sort_keys=True, allow_nan=False) for row in clean(rows))

    for name in ("replicates", "contrasts", "decision_curves", "calibration", "failures"):
        assert ordered(restored[name]) == ordered(raw["tables"][name]), name
    assert "tables" not in packed and "identity" not in packed
    for group in packed["groups"]:
        assert "n" not in group and "cell_id" not in group
        for packet in group["versions"].values():
            assert all(isinstance(a, list) for a in packet.get("decision_curves", {}).values())
            assert all(isinstance(a, list) for a in packet.get("calibration", {}).values())


def test_later_curves_reach_summary(tmp_path: Path, centers: Centers) -> None:
    # Defect: Suppressing later long-form rows also removes their contribution to the summary.
    import gzip

    from scriptkin.consequence.report import cell_metadata, summary_rows
    from scriptkin.consequence.storage import expand_unit, read_restart_unit

    cell = replace(CELL, n=60, scenario="transfer", bias_multiple=1.0)
    plan = replace(PLAN, replicates=2, cell_specs=(cell,))
    write_outputs(plan, tmp_path, centers=centers)
    inputs = grid_inputs(plan, cell, centers)
    all_rows: dict[str, list[dict[str, Any]]] = {
        k: [] for k in ("replicates", "contrasts", "decision_curves")
    }
    for r in range(2):
        unit = read_restart_unit(tmp_path / "units", cell.id, r)
        assert unit is not None
        expanded = expand_unit(unit, cell_metadata(plan, cell, inputs), plan.thresholds)
        for k in all_rows:
            all_rows[k].extend(expanded[k])
    expected = summary_rows(all_rows)["figure_decision_curves"]
    with (tmp_path / "figure_decision_curves.csv").open() as stream:
        actual = list(csv.DictReader(stream))
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected, strict=True):
        assert got["usable_R"] == str(want["usable_R"])
        assert float(got["mean"]) == want["mean"]
    for name in ("consequence_decision_curves.csv", "consequence_calibration.csv"):
        with (tmp_path / name).open() as stream:
            assert {row["replicate"] for row in csv.DictReader(stream)} == {"0"}
    for name in ("consequence_replicates.csv.gz", "consequence_contrasts.csv.gz"):
        with gzip.open(tmp_path / name, "rt") as stream:
            assert {row["replicate"] for row in csv.DictReader(stream)} == {"0", "1"}
    assert not (tmp_path / "details" / f"{cell.id}-001.json.gz").exists()


def test_restart_unit_size(tmp_path: Path, centers: Centers) -> None:
    # Defect: A restart unit retains repeated cell fields or full membership and exceeds its storage budget.
    from scriptkin.consequence.storage import pack_unit, read_unit, write_compressed_json

    cell = replace(CELL, n=120, scenario="transfer", bias_multiple=1.0, noise_id="stress", features=FEATURES)
    raw = run_unit(PLAN, cell, 1, grid_inputs(PLAN, cell, centers), smoke=False)
    packet = pack_unit(raw)
    path = tmp_path / "unit.json.gz"
    write_compressed_json(path, packet)
    assert read_unit(path) == packet
    assert path.stat().st_size < 20_000
    serialized = json.dumps(packet)
    assert '"training_rows"' not in serialized and '"training_participants"' not in serialized
    assert '"participant_share"' not in serialized and '"features"' not in serialized


def test_cell_archive_allocation(tmp_path: Path, centers: Centers) -> None:
    # Defect: Separate small restart files exceed the storage target on exFAT.
    from zipfile import ZIP_STORED, ZipFile

    from scriptkin.consequence.storage import (
        archive_cell,
        pack_unit,
        read_restart_unit,
        unit_name,
        write_compressed_json,
    )

    cell = replace(CELL, n=60, bias_multiple=1.0)
    packet = pack_unit(run_unit(PLAN, cell, 1, grid_inputs(PLAN, cell, centers), smoke=False))
    stage, target = tmp_path / "stage", tmp_path / "units"
    stage.mkdir()
    target.mkdir()
    for r in range(200):
        write_compressed_json(stage / unit_name(cell.id, r), {**packet, "replicate": r})
    archive_cell(stage, target, cell.id)
    path = target / f"{cell.id}.zip"
    assert not list(stage.glob("*.json.gz"))
    assert len(list(target.glob("*"))) == 1
    with ZipFile(path) as archive:
        assert len(archive.infolist()) == 200
        assert all(member.compress_type == ZIP_STORED for member in archive.infolist())
    for r in (0, 199):
        restored = read_restart_unit(target, cell.id, r)
        assert restored == {**packet, "replicate": r}
    block = 131072
    assert ((path.stat().st_size + block - 1) // block) * block < 200 * block / 4


def test_archive_collision(tmp_path: Path) -> None:
    # Defect: A completed archive member is overwritten with different data.
    from scriptkin.consequence.storage import archive_cell, write_compressed_json

    stage, target = tmp_path / "stage", tmp_path / "units"
    stage.mkdir()
    target.mkdir()
    path = stage / "cell-000.json.gz"
    write_compressed_json(path, {"estimate": 0.1})
    archive_cell(stage, target, "cell")
    write_compressed_json(path, {"estimate": 0.2})
    with pytest.raises(ValueError, match="differs"):
        archive_cell(stage, target, "cell")
