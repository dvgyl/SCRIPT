from __future__ import annotations

import importlib
import math
import multiprocessing
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from conftest import FEATURES, Analysed, quick_plan
from scipy.special import expit, logit
from scipy.stats import t as t_distribution
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold

from scriptkin.evaluate.metrics import fold_auc, fold_auc_null_sd
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
from scriptkin.evaluate.report import (
    COUNTS_STATEMENT,
    DIGEST_STATEMENT,
    NORMS_STATEMENT,
    TRIPOD_AI,
    Refused,
    RefusedCode,
    Screening,
)
from scriptkin.evaluate.screening import Design, Fold, crossfit, null_check, screening, witness_refusal
from scriptkin.features import FeatureRow, FeatureTable, Provenance, catalog_digest
from scriptkin.labels import Label
from scriptkin.quality import DEFAULT_QC
from scriptkin.recording import BlockOutcome
from scriptkin.signal import DEFAULT_PREPROCESSING

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
    assert null_report.auc.p_value > 0.05


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
    assert report.cases % 7 and (report.tested - report.cases) % 7
    device = report.baselines["device"]
    assert (device.point, device.low, device.high, device.se_null) == (0.5, 0.5, 0.5, 0.0)


def test_signal_cohort_interval_covers_the_oracle_auc(
    signal_cohort: Analysed, signal_report: Screening
) -> None:
    # Detects a harness that loses or inflates true signal relative to the Bayes risk of the planted traits.
    # No model ranks better than the true risk, so a point above the oracle by more than noise is inflation.
    oracle = signal_cohort.cohort.oracle_auc
    assert oracle > 0.7
    assert signal_report.auc.low <= oracle <= signal_report.auc.high
    assert signal_report.auc.point <= oracle + 0.02
    assert signal_report.auc.p_value <= 1 / 11


def test_auc_intervals_reach_the_null_spread_so_an_uninformative_baseline_covers_chance(
    signal_report: Screening,
) -> None:
    # Detects an AUC interval narrower than the spread of its permutation null, which reads as a failed chance
    # test: the demographic baseline of the signal cohort carries no information but scores 0.385.
    demographic = signal_report.baselines["demographic"]
    assert demographic.low <= 0.5 <= demographic.high
    assert demographic.se_null > 0
    t975 = float(t_distribution.ppf(0.975, len(signal_report.null.aucs) - 1))
    assert signal_report.auc.se_null == pytest.approx(float(np.std(signal_report.null.aucs, ddof=1)))
    for result in (signal_report.auc, *signal_report.baselines.values()):
        assert result.low <= max(0.0, result.point - t975 * result.se_null) + 1e-12
        assert result.high >= min(1.0, result.point + t975 * result.se_null) - 1e-12
    assert any("Significance comes from the permutation p-value" in s for s in signal_report.statements)


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


def leak_one_test_participant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outer training set also hold the first test participant of its fold."""
    original = screening_module.outer_splits

    def leaky(*args: Any) -> Any:
        splits = original(*args)
        assert not isinstance(splits, Refused)
        return [[Fold(np.sort(np.concatenate((f.train, f.test[:1]))), f.test) for f in fs] for fs in splits]

    monkeypatch.setattr(screening_module, "outer_splits", leaky)


def test_screening_refuses_when_a_fit_sees_test_participants(
    null_cohort: Analysed, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a screening run that reports metrics although a fit used participants of its test fold.
    leak_one_test_participant(monkeypatch)
    result = screening(null_cohort.table, null_cohort.cohort.labels, plan=quick_plan(), seed=3)
    assert isinstance(result, Refused) and result.code == RefusedCode.LEAKAGE


def test_a_leakage_refusal_counts_the_participants_and_names_none(
    null_cohort: Analysed, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a refusal reason that names a participant code, which `scriptkin evaluate` writes to its report.
    leak_one_test_participant(monkeypatch)
    result = screening(null_cohort.table, null_cohort.cohort.labels, plan=quick_plan(), seed=3)
    assert isinstance(result, Refused) and result.code == RefusedCode.LEAKAGE
    assert re.fullmatch(
        r"Fold 0: the \w+ fit includes [1-9][0-9]* of the fold's test participants\.", result.reason
    )
    assert not [code for code in {row.participant for row in null_cohort.table.rows} if code in result.reason]


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


@pytest.fixture(scope="module")
def temporal_report(signal_cohort: Analysed) -> Screening:
    return run(signal_cohort, holdout=Holdout.TEMPORAL, folds=4, permutations=10)


def test_temporal_holdout_fits_only_earlier_participants(temporal_report: Screening) -> None:
    # Detects a temporal holdout that trains on participants who joined after the test participants.
    assert len(temporal_report.folds) == 3
    assert all(f.fit_latest < f.test_earliest for f in temporal_report.folds)


def test_the_report_tells_tested_participants_from_analysed_participants(temporal_report: Screening) -> None:
    # Detects a report count that reads as the analysed participants, although a temporal holdout never tests
    # its first block.
    assert temporal_report.tested == sum(f.test_participants for f in temporal_report.folds)
    assert temporal_report.tested < temporal_report.attrition.analysed
    report = temporal_report.to_json()
    assert "n" not in report and report["tested"] == temporal_report.tested
    assert COUNTS_STATEMENT in temporal_report.statements


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


def matrix_cohort(
    x: np.ndarray,
    y: np.ndarray,
    sites: list[str],
    *,
    age_bands: list[str] | None = None,
    dates: list[str] | None = None,
) -> tuple[FeatureTable, list[Label]]:
    """A feature table with one spiral trial per participant and the three columns of `x`."""
    rows = tuple(
        FeatureRow(
            f"P{i:05d}",
            f"S{i:05d}",
            sites[i],
            "supervised",
            age_bands[i] if age_bands else "18-24",
            1,
            dates[i] if dates else "2026-01-05T09:00:00Z",
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
    assert result.auc.point > result.null.high
    assert result.caution("site_baseline") is not None
    assert result.auc.p_value > 0.05
    assert np.mean(result.null.site_aucs) > result.null.high


def test_baseline_caution_tests_each_baseline_against_its_own_null_spread() -> None:
    # Detects a baseline caution from the bootstrap low bound. Here the age band carries no information, but
    # the bootstrap interval of its fixed predictions excludes 0.5, so that rule raises a false caution.
    rng = np.random.default_rng(121)
    x = rng.standard_normal((120, 3))
    y = rng.random(120) < 0.3
    bands = [f"band{int(b)}" for b in rng.integers(2, size=120)]
    table, labels = matrix_cohort(x, y, ["S1"] * 120, age_bands=bands)
    plan = quick_plan(features=MATRIX_COLUMNS, repeats=3, permutations=20)
    result = screening(table, labels, plan=plan, seed=121)
    assert isinstance(result, Screening), result
    assert result.caution("demographic_baseline") is None
    demographic = result.baselines["demographic"]
    assert demographic.point > 0.5 and demographic.se_null > 0
    assert demographic.point >= result.auc.low


def test_null_band_is_never_narrower_than_the_fold_size_floor() -> None:
    # Detects a band set by the null spread alone. Skewed multi-repeat null AUCs often spread little, and such
    # a band then refuses sound plans; a band that ignores a wider null spread refuses leaky plans too seldom.
    folds = np.repeat(np.arange(5), 24)
    y = np.tile((np.arange(24) < 7).astype(np.int64), 5)
    sigma0 = fold_auc_null_sd(folds, y)
    draws = np.random.default_rng(6).standard_normal(20)
    unit = (draws - draws.mean()) / draws.std(ddof=1)
    for scale, expected in ((0.5, sigma0), (2.0, 2.0 * sigma0)):
        aucs = [float(a) for a in 0.5 + scale * sigma0 * unit]
        for count in (10, 20):
            check = null_check(aucs[:count], aucs[:count], folds, y)
            assert check.sd >= sigma0
            assert check.sd >= float(np.std(aucs[:count], ddof=1))
            spread = t_distribution.ppf(0.995, count - 1) * check.sd / math.sqrt(count)
            assert check.high - 0.5 == pytest.approx(spread)
        assert null_check(aucs, aucs, folds, y).sd == pytest.approx(expected)


def test_report_gives_the_p_value_resolution_and_cautions_when_it_cannot_reach_0_05(
    null_report: Screening, signal_report: Screening
) -> None:
    # Detects a report that hides that P permutations cannot give a p-value below 1 / (P + 1).
    assert null_report.null.resolution == pytest.approx(1 / 21)
    assert null_report.caution("p_value_resolution") is None
    assert signal_report.null.resolution == pytest.approx(1 / 11)
    caution = signal_report.caution("p_value_resolution")
    assert caution is not None and "0.091" in caution.detail


def test_prior_shift_starts_from_the_training_prevalence_of_each_fold() -> None:
    # Detects a prior shift from the pooled sample prevalence. Each fold's calibrated risks carry its own
    # training prevalence, so under drifting prevalence a pooled shift gives threshold metrics spurious
    # discrimination although the features carry no information.
    rng = np.random.default_rng(1)
    y = np.concatenate([rng.permutation(np.arange(50) < cases) for cases in (5, 15, 25, 35)])
    dates = [f"2026-{1 + i // 50:02d}-{1 + i % 50 // 2:02d}T{9 + i % 2:02d}:00:00Z" for i in range(200)]
    table, labels = matrix_cohort(rng.standard_normal((200, 3)), y, ["S1"] * 200, dates=dates)
    plan = quick_plan(
        features=MATRIX_COLUMNS, holdout=Holdout.TEMPORAL, folds=4, thresholds=(0.1, 0.15), bootstrap=50
    )
    result = screening(table, labels, plan=plan, seed=1)
    assert isinstance(result, Screening), result
    assert result.sample_prevalence == 0.5
    for row in result.thresholds:
        assert abs(row.sensitivity.point + row.specificity.point - 1) < 0.1
    assert [f.train_prevalence for f in result.folds] == pytest.approx([0.1, 0.2, 0.3])


@pytest.fixture(scope="module")
def norms_report() -> Screening:
    """Norms on age band, with one participant in an age band that nobody else has."""
    rng = np.random.default_rng(8)
    table, labels = matrix_cohort(
        rng.standard_normal((120, 3)),
        rng.random(120) < 0.3,
        ["S1"] * 120,
        age_bands=["13-17"] + ["18-24"] * 119,
    )
    plan = quick_plan(features=MATRIX_COLUMNS, norms=NormSpec(("age_band",)), bootstrap=50)
    result = screening(table, labels, plan=plan, seed=4)
    assert isinstance(result, Screening), result
    return result


def test_norms_count_test_rows_outside_the_range_of_their_training_fold(norms_report: Screening) -> None:
    # Detects a test row outside the covariate range of its fold's norms that imputation hides silently.
    assert sorted(f.out_of_range for f in norms_report.folds) == [0, 0, 0, 0, 1]
    caution = norms_report.caution("norms_out_of_range")
    assert caution is not None and ": 1," in caution.detail


def test_norms_report_states_that_the_reference_includes_cases(norms_report: Screening) -> None:
    # Detects a report that hides that the norms are fitted on every training participant, cases included,
    # which pulls the norm toward the cases and shrinks their deviations.
    content = next(t.content for t in norms_report.tripod if t.item == "12b")
    assert content is not None and "without labels" in content and "cases included" in content


def null_run(seed: int) -> tuple[bool, bool] | None:
    """One null matrix cohort: whether the AUC interval covers 0.5 and whether p <= 0.05. None if refused."""
    rng = np.random.default_rng(seed)
    table, labels = matrix_cohort(rng.standard_normal((120, 3)), rng.random(120) < 0.3, ["S1"] * 120)
    plan = quick_plan(
        features=MATRIX_COLUMNS,
        folds=3,
        inner_folds=2,
        repeats=2,
        model=ModelSpec(grid=(10.0,)),
        permutations=20,
        bootstrap=50,
    )
    result = screening(table, labels, plan=plan, seed=seed)
    if isinstance(result, Refused):
        assert result.code == RefusedCode.NULL_CHECK, result
        return None
    return bool(result.auc.low <= 0.5 <= result.auc.high), result.auc.p_value <= 0.05


@pytest.mark.slow
def test_null_intervals_cover_chance_and_the_permutation_p_value_keeps_its_level() -> None:
    # Detects an AUC interval that excludes 0.5 too often on null data, as the bootstrap of fixed predictions
    # did (89% coverage in this design), or a permutation p-value that rejects at the wrong rate.
    # The runs go to worker processes; 400 runs make the binomial noise small next to both limits.
    with ProcessPoolExecutor(mp_context=multiprocessing.get_context("spawn")) as pool:
        results = [r for r in pool.map(null_run, range(400)) if r is not None]
    assert len(results) >= 380
    assert sum(covered for covered, _ in results) / len(results) >= 0.93
    assert 0.02 <= sum(rejected for _, rejected in results) / len(results) <= 0.09


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
    assert report.tested == report.attrition.analysed


def test_report_lists_tripod_items_and_marks_the_rest_author_supplied(null_report: Screening) -> None:
    # Detects a TRIPOD+AI list that drops an item or claims an item that only the authors can supply.
    assert [t.item for t in null_report.tripod] == [item for item, _ in TRIPOD_AI]
    known = {t.item for t in null_report.tripod if not t.author_supplied}
    assert {"7", "8a", "9a", "11", "12c", "12e", "20a", "23a"} <= known
    assert not known & {"1", "2", "3a", "5a", "6b", "17", "18a", "19", "25", "26", "27a"}
    assert null_report.plan_digest in next(t.content or "" for t in null_report.tripod if t.item == "18c")


def test_every_report_states_that_its_digests_do_not_date_the_plan(
    null_report: Screening, norms_report: Screening
) -> None:
    # Detects a report that lets a reader take its digests as proof that the plan was fixed before the labels.
    for report in (null_report, norms_report):
        assert DIGEST_STATEMENT in report.to_json()["statements"]


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
