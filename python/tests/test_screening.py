from __future__ import annotations

import importlib
import math
from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from conftest import FEATURES, Analysed, quick_plan
from scipy.special import expit, logit
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold

from scriptkin.evaluate.metrics import fold_auc
from scriptkin.evaluate.participants import medians
from scriptkin.evaluate.plan import (
    KNN,
    GateCriteria,
    Holdout,
    ModelSpec,
    NormSpec,
    Target,
    column,
)
from scriptkin.evaluate.reliability import reliability
from scriptkin.evaluate.report import NORMS_STATEMENT, TRIPOD_AI, Refused, RefusedCode, Screening
from scriptkin.evaluate.screening import Design, Fold, crossfit, screening, witness_refusal
from scriptkin.features import FeatureRow, FeatureTable, Provenance, catalog_digest
from scriptkin.labels import Label
from scriptkin.quality import DEFAULT_QC
from scriptkin.recording import BlockOutcome
from scriptkin.signal import DEFAULT_PREPROCESSING, Estimate

# The package exports the `screening` function under the module's own name, so import the module itself.
screening_module = importlib.import_module("scriptkin.evaluate.screening")


def run(analysed: Analysed, **changes: Any) -> Screening:
    result = screening(analysed.table, analysed.cohort.labels, plan=quick_plan(**changes), seed=3)
    assert isinstance(result, Screening), result
    return result


@pytest.fixture(scope="module")
def null_report(null_cohort: Analysed) -> Screening:
    return run(null_cohort, permutations=20, repeats=2)


@pytest.fixture(scope="module")
def signal_report(signal_cohort: Analysed) -> Screening:
    return run(signal_cohort, repeats=2, bootstrap=500)


def test_null_cohort_interval_covers_chance_and_passes_the_permutation_check(null_report: Screening) -> None:
    # Detects a harness that finds signal where none exists, or whose permutation null is off centre.
    assert null_report.auc.low <= 0.5 <= null_report.auc.high
    assert null_report.null.low <= null_report.null.mean <= null_report.null.high
    assert null_report.null.high - null_report.null.low < 0.12
    assert null_report.null.p_value > 0.05


def test_fold_auc_compares_pairs_inside_one_test_fold() -> None:
    # Detects an AUC that pools pairs across folds, which carries each fold's training prevalence.
    folds = np.array([[0, 0, 0, 0, 1, 1, 1, 1]])
    y = np.array([1, 1, 1, 0, 1, 0, 0, 0])
    constant = np.array([[0.3, 0.3, 0.3, 0.3, 0.7, 0.7, 0.7, 0.7]])
    rows = np.arange(8)
    assert fold_auc(constant, folds, y, rows) == 0.5
    separating = np.array([[0.9, 0.8, 0.7, 0.1, 0.6, 0.5, 0.4, 0.2]])
    assert fold_auc(separating, folds, y, rows) == 1.0
    assert fold_auc(np.vstack((constant, separating)), np.vstack((folds, folds)), y, rows) == 0.75


def test_a_baseline_without_information_scores_exactly_chance(null_cohort: Analysed) -> None:
    # Detects pooled AUC bias: with uneven folds, a constant predictor ranks folds by training prevalence.
    report = run(null_cohort, folds=7, permutations=10, bootstrap=50)
    assert report.cases % 7 and (report.n - report.cases) % 7
    assert report.baselines["device"] == Estimate(0.5, 0.5, 0.5)


def test_signal_cohort_interval_covers_the_oracle_auc(
    signal_cohort: Analysed, signal_report: Screening
) -> None:
    # Detects a harness that loses or inflates true signal relative to the Bayes risk of the planted traits.
    oracle = signal_cohort.cohort.oracle_auc
    assert oracle > 0.7
    assert signal_report.auc.low <= oracle <= signal_report.auc.high
    assert signal_report.null.p_value <= 1 / 11


def session_design(analysed: Analysed, ids_of: str) -> Design:
    """One row per session with the participant label; `ids_of` names what the analysis treats as a unit."""
    keys, x = medians(analysed.table, FEATURES[:1] + FEATURES[3:], lambda r: (r.participant, r.session))
    case = {p.participant: p.case for p in analysed.cohort.truth.participants}
    y = np.array([int(bool(case[pid])) for pid, _ in keys], dtype=np.int64)
    ids = tuple(pid if ids_of == "participant" else f"{pid}/{session}" for pid, session in keys)
    return Design(x, y, ids, FEATURES[:1] + FEATURES[3:], tuple({} for _ in keys))


def folds(design: Design, splitter: StratifiedKFold | StratifiedGroupKFold) -> list[list[Fold]]:
    if isinstance(splitter, StratifiedGroupKFold):
        split = splitter.split(design.x, design.y, design.ids)
    else:
        split = splitter.split(design.x, design.y)
    return [[Fold(np.asarray(train), np.asarray(test)) for train, test in split]]


def test_witness_refuses_an_ungrouped_splitter(fingerprint_cohort: Analysed) -> None:
    # Detects a witness that misses a participant whose other session is in a fit of its test fold.
    design = session_design(fingerprint_cohort, "participant")
    ungrouped = folds(design, StratifiedKFold(5, shuffle=True, random_state=1))
    leak = witness_refusal(crossfit(design, KNN, ungrouped, inner_folds=3, seed=1), ungrouped, design.ids)
    assert isinstance(leak, Refused) and leak.code == RefusedCode.LEAKAGE
    grouped = folds(design, StratifiedGroupKFold(5, shuffle=True, random_state=1))
    assert witness_refusal(crossfit(design, KNN, grouped, inner_folds=3, seed=1), grouped, design.ids) is None


def test_screening_refuses_when_a_fit_sees_test_participants(
    null_cohort: Analysed, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a screening run that reports metrics although a fit used participants of its test fold.
    original = screening_module.outer_splits

    def leaky(*args: Any) -> Any:
        splits = original(*args)
        assert not isinstance(splits, Refused)
        return [[Fold(np.sort(np.concatenate((f.train, f.test[:1]))), f.test) for f in fs] for fs in splits]

    monkeypatch.setattr(screening_module, "outer_splits", leaky)
    result = screening(null_cohort.table, null_cohort.cohort.labels, plan=quick_plan(), seed=3)
    assert isinstance(result, Refused) and result.code == RefusedCode.LEAKAGE


def test_fingerprint_inflates_an_ungrouped_auc_and_grouped_auc_stays_at_chance(
    fingerprint_cohort: Analysed,
) -> None:
    # Detects a fingerprint scenario too weak to show leakage, or a grouped analysis that still leaks.
    naive = session_design(fingerprint_cohort, "session")
    naive_cross = crossfit(
        naive, KNN, folds(naive, StratifiedKFold(5, shuffle=True, random_state=2)), inner_folds=3, seed=2
    )
    grouped = session_design(fingerprint_cohort, "participant")
    grouped_folds = folds(grouped, StratifiedGroupKFold(5, shuffle=True, random_state=2))
    grouped_cross = crossfit(grouped, KNN, grouped_folds, inner_folds=3, seed=2)
    rows = np.arange(len(grouped.y))
    inflated = naive_cross.auc(naive.y, rows)
    honest = grouped_cross.auc(grouped.y, rows)
    assert inflated > 0.75
    assert abs(honest - 0.5) < 0.1
    report = run(fingerprint_cohort, model=KNN, features=FEATURES[:1] + FEATURES[3:])
    assert report.auc.low <= 0.5 <= report.auc.high


def test_device_baseline_explains_a_device_confounded_result(device_cohort: Analysed) -> None:
    # Detects a report that presents a device difference as a screening result.
    report = run(device_cohort)
    assert device_cohort.cohort.oracle_auc == 0.5
    assert report.auc.low > 0.6
    assert report.baselines["device"].point >= report.auc.low
    caution = report.caution("device_baseline")
    assert caution is not None and "device can explain the result" in caution.detail


def test_single_device_cohort_has_no_device_caution(signal_report: Screening) -> None:
    # Detects a device caution raised when the device cannot predict the label.
    assert signal_report.baselines["device"].point == 0.5
    assert signal_report.caution("device_baseline") is None


def test_site_holdout_keeps_whole_sites_out_of_every_fit(signal_cohort: Analysed) -> None:
    # Detects a site holdout that lets participants of the test site into a fit of that fold.
    report = run(signal_cohort, holdout=Holdout.SITE)
    assert [f.test_sites for f in report.folds] == [("S1",), ("S2",), ("S3",)]
    for fold in report.folds:
        assert set(fold.fit_sites) == {"S1", "S2", "S3"} - set(fold.test_sites)


def test_device_holdout_keeps_whole_devices_out_of_every_fit(device_cohort: Analysed) -> None:
    # Detects a device holdout that lets the test device into a fit of that fold.
    report = run(device_cohort, holdout=Holdout.DEVICE, permutations=10)
    assert len(report.folds) == 2
    for fold in report.folds:
        assert len(fold.test_devices) == 1 and fold.test_devices[0] not in fold.fit_devices


def test_temporal_holdout_fits_only_earlier_participants(signal_cohort: Analysed) -> None:
    # Detects a temporal holdout that trains on participants who joined after the test participants.
    report = run(signal_cohort, holdout=Holdout.TEMPORAL, folds=4, permutations=10)
    assert len(report.folds) == 3
    assert all(f.fit_latest < f.test_earliest for f in report.folds)
    assert report.n < len(signal_cohort.cohort.truth.participants)


def ppv(se: float, sp: float, prevalence: float) -> float:
    return se * prevalence / (se * prevalence + (1 - sp) * (1 - prevalence))


def npv(se: float, sp: float, prevalence: float) -> float:
    return sp * (1 - prevalence) / (sp * (1 - prevalence) + (1 - se) * prevalence)


def test_ppv_and_npv_at_the_stated_prevalence_equal_the_closed_form(signal_cohort: Analysed) -> None:
    # Detects PPV or NPV taken from the sample prevalence or computed with a wrong formula.
    report = run(signal_cohort, prevalence=0.08, permutations=10, bootstrap=50)
    assert abs(report.sample_prevalence - 0.08) > 0.1
    for row in report.thresholds:
        se, sp = row.sensitivity, row.specificity
        assert row.ppv.point == pytest.approx(ppv(se.point, sp.point, 0.08), rel=1e-12)
        assert row.npv.point == pytest.approx(npv(se.point, sp.point, 0.08), rel=1e-12)
        assert row.ppv.low == pytest.approx(ppv(se.low, sp.low, 0.08), rel=1e-12)
        assert row.npv.high == pytest.approx(npv(se.high, sp.high, 0.08), rel=1e-12)
    assert any(
        r.ppv.point != pytest.approx(ppv(r.sensitivity.point, r.specificity.point, report.sample_prevalence))
        for r in report.thresholds
    )


def test_prior_shift_is_applied_when_the_sample_prevalence_differs(signal_cohort: Analysed) -> None:
    # Detects thresholds applied to sample-prevalence risks when the stated prevalence differs.
    thresholds = (0.05, 0.1, 0.2)
    shifted = run(signal_cohort, prevalence=0.08, thresholds=thresholds, permutations=10, bootstrap=50)
    sample = shifted.sample_prevalence
    moved = tuple(float(expit(logit(t) - logit(0.08) + logit(sample))) for t in thresholds)
    same = run(signal_cohort, prevalence=sample, thresholds=moved, permutations=10, bootstrap=50)
    plain = run(signal_cohort, prevalence=sample, thresholds=thresholds, permutations=10, bootstrap=50)
    for a, b in zip(shifted.thresholds, same.thresholds, strict=True):
        assert (a.sensitivity, a.specificity) == (b.sensitivity, b.specificity)
    assert [(r.sensitivity, r.specificity) for r in shifted.thresholds] != [
        (r.sensitivity, r.specificity) for r in plain.thresholds
    ]
    assert shifted.caution("prevalence_shift") is not None
    assert plain.caution("prevalence_shift") is None


MATRIX_FEATURES = ("kinematic.speed_mean", "kinematic.jerk_mean", "spatial.trace_rmse")
MATRIX_COLUMNS = tuple(column("spiral-trace", f) for f in MATRIX_FEATURES)


def matrix_cohort(x: np.ndarray, y: np.ndarray, sites: list[str]) -> tuple[FeatureTable, list[Label]]:
    """A feature table with one spiral trial per participant and the three columns of `x`."""
    rows = tuple(
        FeatureRow(
            f"P{i:05d}",
            f"S{i:05d}",
            sites[i],
            "supervised",
            "18-24",
            1,
            "2026-01-05T09:00:00Z",
            "iPad16,3",
            "spiral-trace",
            0,
            1,
            "right",
            (),
            (),
            dict(zip(MATRIX_FEATURES, map(float, x[i]), strict=True)),
        )
        for i in range(len(y))
    )
    provenance = Provenance("test", DEFAULT_QC, DEFAULT_PREPROCESSING, catalog_digest(), (), ())
    labels = [
        Label(
            f"P{i:05d}",
            f"S{i:05d}",
            "phq9",
            "1.0",
            "en",
            "supervised",
            0,
            0.0,
            "B",
            BlockOutcome.COMPLETED,
            "total",
            15.0 if y[i] else 3.0,
        )
        for i in range(len(y))
    ]
    return FeatureTable(rows, provenance), labels


def logistic_cohort(n: int, seed: int) -> tuple[FeatureTable, list[Label]]:
    """A cohort that a logistic model specifies exactly: three features and a logistic outcome."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, 3))
    y = rng.random(n) < expit(-1.0 + x @ np.array([1.0, -0.8, 0.5]))
    return matrix_cohort(x, y, ["S1"] * n)


def test_site_confounding_is_a_caution_and_not_a_harness_failure() -> None:
    # Detects a null check that permutes inside sites with mixed-site folds, which refuses a sound pipeline
    # whenever the features can tell the sites apart and the sites differ in prevalence.
    rng = np.random.default_rng(4)
    sites = ["A" if i % 2 else "B" for i in range(300)]
    site_a = np.array([s == "A" for s in sites])
    y = rng.random(300) < np.where(site_a, 0.43, 0.18)
    x = np.column_stack(
        (np.where(site_a, 60.0, 50.0) + rng.standard_normal(300), rng.standard_normal((300, 2)))
    )
    table, labels = matrix_cohort(x, y, sites)
    result = screening(table, labels, plan=quick_plan(features=MATRIX_COLUMNS, permutations=20), seed=2)
    assert isinstance(result, Screening), result
    assert result.null.low <= result.null.mean <= result.null.high
    assert result.auc.low > 0.5
    assert result.caution("site_baseline") is not None
    assert result.null.p_value > 0.05
    assert np.mean(result.null.site_aucs) > result.null.high


@pytest.mark.slow
def test_calibration_slope_is_near_one_for_a_well_specified_model() -> None:
    # Detects a missing or mis-scaled recalibration, or a wrong slope estimate. The strong L2 penalty shrinks
    # the raw scores, so only a working cross-fitted calibrator brings the slope to 1.
    table, labels = logistic_cohort(3000, 1)
    plan = quick_plan(
        features=MATRIX_COLUMNS,
        model=ModelSpec(grid=(1.0,)),
        permutations=10,
        bootstrap=100,
    )
    result = screening(table, labels, plan=plan, seed=5)
    assert isinstance(result, Screening), result
    assert result.calibration.slope == pytest.approx(1.0, abs=0.12)
    assert abs(result.calibration.intercept) < 0.12
    for predicted, observed, count in result.calibration.curve:
        assert abs(predicted - observed) < 3.5 * math.sqrt(predicted * (1 - predicted) / count) + 0.01


def test_attrition_reports_refused_trials_and_participants_with_codes(missing_cohort: Analysed) -> None:
    # Detects attrition counts that miss refused labels or rejected trials, or lose their codes.
    report = run(missing_cohort, permutations=10)
    people = missing_cohort.cohort.truth.participants
    declined = sum(len(s.declined) for p in people for s in p.sessions)
    refused_labels = sum(p.case is None for p in people)
    declined_with_label = sum(bool(p.sessions[0].declined) and p.case is not None for p in people)
    assert declined and refused_labels
    assert report.attrition.trials_refused == declined
    assert report.attrition.trial_codes["incomplete_trial"] == declined
    assert report.attrition.excluded == {
        "label_refused:too_few_values": refused_labels,
        "no_accepted_trial": declined_with_label,
    }
    assert report.attrition.analysed == len(people) - refused_labels - declined_with_label
    assert report.n == report.attrition.analysed


def test_report_lists_tripod_items_and_marks_the_rest_author_supplied(null_report: Screening) -> None:
    # Detects a TRIPOD+AI list that drops an item or claims an item that only the authors can supply.
    assert [t.item for t in null_report.tripod] == [item for item, _ in TRIPOD_AI]
    known = {t.item for t in null_report.tripod if not t.author_supplied}
    assert {"7", "8a", "9a", "11", "12c", "12e", "20a", "23a"} <= known
    assert not known & {"1", "2", "3a", "5a", "6b", "17", "18a", "19", "25", "26", "27a"}
    assert null_report.plan_digest in next(t.content or "" for t in null_report.tripod if t.item == "18c")


def test_without_reliability_data_the_report_says_reliability_is_not_established(
    null_report: Screening,
) -> None:
    # Detects a report that implies reliable features when no reliability evidence was given.
    caution = null_report.caution("reliability_not_established")
    assert caution is not None and all(c in caution.detail for c in FEATURES)
    assert {g.status for g in null_report.reliability} == {"not_established"}


def test_reliability_gate_removes_a_feature_that_fails(retest_cohort: Analysed) -> None:
    # Detects a screening run that uses a feature which failed the reliability gate.
    agreement = reliability(retest_cohort.table, "session")
    gate = GateCriteria(between="session", min_participants=20)
    report = run(retest_cohort, gate=gate, permutations=10)
    assert report.caution("reliability_not_established") is not None
    gated = screening(
        retest_cohort.table,
        retest_cohort.cohort.labels,
        plan=quick_plan(gate=gate, permutations=10),
        seed=3,
        reliability=agreement,
    )
    assert isinstance(gated, Screening), gated
    force = column("spiral-trace", "pressure.force_mean")
    assert force not in gated.columns
    assert column("spiral-trace", "kinematic.speed_mean") in gated.columns
    assert next(g.status for g in gated.reliability if g.column == force) == "fail"
    assert gated.caution("reliability_not_established") is None


def test_null_check_refuses_a_harness_that_leaks_labels(
    null_cohort: Analysed, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a null check that passes a pipeline whose features carry the labels.
    original = screening_module.crossfit

    def leaky(design: Design, *args: Any, **kwargs: Any) -> Any:
        x = np.column_stack((design.x, design.y.astype(float)))
        return original(replace(design, x=x, columns=(*design.columns, "label")), *args, **kwargs)

    monkeypatch.setattr(screening_module, "crossfit", leaky)
    result = screening(null_cohort.table, null_cohort.cohort.labels, plan=quick_plan(), seed=3)
    assert isinstance(result, Refused) and result.code == RefusedCode.NULL_CHECK


def test_norms_inside_folds_state_that_a_deviation_is_not_a_diagnosis(null_cohort: Analysed) -> None:
    # Detects a normative analysis whose report omits that a deviation is not a diagnosis.
    report = run(null_cohort, norms=NormSpec(("age_band",)), permutations=10)
    assert NORMS_STATEMENT in report.statements
    assert NORMS_STATEMENT not in run(null_cohort, permutations=10).statements


def test_unknown_feature_is_refused(null_cohort: Analysed) -> None:
    # Detects a plan column that silently drops out because the table has no such task.
    plan = quick_plan(features=(column("house", "kinematic.speed_mean"),))
    result = screening(null_cohort.table, null_cohort.cohort.labels, plan=plan, seed=3)
    assert isinstance(result, Refused) and result.code == RefusedCode.UNKNOWN_FEATURE


def test_target_requires_the_planned_instrument_and_cut(
    signal_cohort: Analysed, signal_report: Screening
) -> None:
    # Detects a label join that ignores the planned instrument or threshold.
    cases = sum(bool(p.case) for p in signal_cohort.cohort.truth.participants)
    assert signal_report.cases == cases
    higher = run(signal_cohort, target=Target(cut=15.0), permutations=10, bootstrap=50)
    assert higher.cases < cases
    gad = run(signal_cohort, target=Target(instrument="gad7"), permutations=10, bootstrap=50)
    assert gad.cases != cases
