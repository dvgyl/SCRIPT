from __future__ import annotations

import numpy as np
import pytest
from conftest import SPIRAL, Analysed, analysed

from scriptkin import synthetic as syn
from scriptkin.evaluate.plan import GateCriteria, column
from scriptkin.evaluate.reliability import GateStatus, icc_a1, reliability, reliability_gate
from scriptkin.features import FeatureRow, FeatureTable, Provenance, catalog_digest
from scriptkin.quality import DEFAULT_QC
from scriptkin.signal import DEFAULT_PREPROCESSING

# Shrout and Fleiss (1979), Table 2: six targets rated by four judges.
SHROUT_FLEISS = np.array(
    [[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8], [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]], dtype=float
)
SPEED = column("spiral-trace", "kinematic.speed_mean")
FORCE = column("spiral-trace", "pressure.force_mean")


def test_icc_a1_matches_shrout_and_fleiss_case_2() -> None:
    # Detects a wrong ICC(A,1), for example the consistency form ICC(C,1) = 0.71 or the one-way form 0.17.
    icc, sem, between_sd = icc_a1(SHROUT_FLEISS)
    assert icc.point == pytest.approx(0.29, abs=0.005)
    # From the published mean squares: judges 32.49, residual 1.02, targets 11.24, with n = 6 and k = 4.
    assert sem == pytest.approx(np.sqrt((32.486 - 1.0194) / 6 + 1.0194), abs=1e-3)
    assert between_sd == pytest.approx(np.sqrt((11.2417 - 1.0194) / 4), abs=1e-3)
    assert icc.low < icc.point < icc.high


def test_icc_a1_interval_matches_the_published_wine_example() -> None:
    # Detects a wrong ICC(A,1) interval, for example swapped F degrees of freedom (lower bound 0.29).
    # The `icc` dataset and the result ICC(A,1) 0.728, 95% CI [0.43, 0.93] are from the pingouin
    # documentation of `intraclass_corr`: eight wines scored by four judges.
    wines = np.array(
        [
            [1, 2, 0, 1],
            [1, 3, 3, 2],
            [3, 8, 1, 4],
            [6, 4, 3, 3],
            [6, 5, 5, 6],
            [7, 5, 6, 2],
            [8, 7, 7, 9],
            [9, 9, 9, 8],
        ],
        dtype=float,
    )
    icc = icc_a1(wines)[0]
    assert (round(icc.point, 3), round(icc.low, 2), round(icc.high, 2)) == (0.728, 0.43, 0.93)


def test_icc_interval_covers_the_true_value_at_the_nominal_rate() -> None:
    # Detects a wrong ICC(A,1) interval, for example swapped F degrees of freedom or a wrong Satterthwaite df.
    # The McGraw and Wong interval is approximate. With two raters it undercovers when the rater variance is
    # large (about 0.81 at rater SD 0.8 and error SD 0.5), so this test uses a small rater variance.
    rng = np.random.default_rng(3)
    n, k, rater_sd, error_sd = 30, 2, 0.2, 0.5
    true = 1 / (1 + rater_sd**2 + error_sd**2)
    hits = 0
    for _ in range(1000):
        y = (
            rng.standard_normal((n, 1))
            + rater_sd * rng.standard_normal((1, k))
            + error_sd * rng.standard_normal((n, k))
        )
        icc = icc_a1(y)[0]
        hits += icc.low <= true <= icc.high
    assert 0.93 <= hits / 1000 <= 0.97


def test_gate_fails_a_feature_with_injected_session_noise(retest_cohort: Analysed) -> None:
    # Detects a gate that passes a feature whose session-to-session noise makes it unreliable.
    agreement = reliability(retest_cohort.table, "session")
    criteria = GateCriteria(between="session", min_participants=20)
    assert agreement[FORCE].occasions == ("visit 1", "visit 2")
    assert agreement[FORCE].n == agreement[SPEED].n == 40
    assert reliability_gate(agreement[FORCE], criteria).status == GateStatus.FAIL
    assert reliability_gate(agreement[SPEED], criteria).status == GateStatus.PASS
    assert agreement[FORCE].icc.point < 0.75 < agreement[SPEED].icc.low


def test_gate_is_not_established_without_the_evidence_it_needs(retest_cohort: Analysed) -> None:
    # Detects a gate that passes or fails a feature without enough participants or the right occasions.
    speed = reliability(retest_cohort.table, "session")[SPEED]
    for agreement, criteria in (
        (None, GateCriteria(between="session")),
        (speed, GateCriteria(between="session", min_participants=100)),
        (speed, GateCriteria(between="device", min_participants=20)),
    ):
        result = reliability_gate(agreement, criteria)
        assert result.status == GateStatus.NOT_ESTABLISHED and result.reason


def test_device_agreement_pairs_each_participant_across_devices() -> None:
    # Detects agreement from unpaired rows, or a gate that misses a device bias in absolute agreement.
    devices = (syn.IPAD_PRO_PENCIL_PRO, syn.IPAD_PRO_PENCIL_2)
    crossed = analysed(n=24, seed=33, scenario="null", devices=devices, crossed=True, tasks=SPIRAL)
    agreement = reliability(crossed.table, "device")
    assert agreement[SPEED].occasions == tuple(sorted(d.model for d in devices)) and agreement[SPEED].n == 24
    assert agreement[SPEED].icc.point > 0.9 and agreement[SPEED].icc.low > 0.75
    force = agreement[FORCE]
    pair = force.bland_altman[0]
    assert (pair.first, pair.second) == (syn.IPAD_PRO_PENCIL_2.model, syn.IPAD_PRO_PENCIL_PRO.model)
    assert pair.bias > 0 and pair.low < pair.bias < pair.high
    result = reliability_gate(force, GateCriteria(between="device", min_participants=20, min_icc_low=-1.0))
    assert result.status == GateStatus.FAIL and "Bias" in result.reason


def test_session_occasions_follow_the_visit_number() -> None:
    # Detects visits sorted as text, which puts "visit 10" before "visit 2" and reverses Bland-Altman pairs.
    rng = np.random.default_rng(5)
    speed = rng.normal(100.0, 10.0, 12)
    rows = tuple(
        FeatureRow(
            f"P{i}",
            f"P{i}-{visit}",
            "S1",
            "supervised",
            "18-24",
            visit,
            None,
            "iPad16,3",
            "spiral-trace",
            0,
            1,
            "right",
            (),
            (),
            {"kinematic.speed_mean": float(speed[i] + visit + rng.normal(0.0, 1.0))},
        )
        for i in range(12)
        for visit in (10, 2, 1)
    )
    provenance = Provenance("test", DEFAULT_QC, DEFAULT_PREPROCESSING, catalog_digest(), (), ())
    agreement = reliability(FeatureTable(rows, provenance), "session")[SPEED]
    assert agreement.occasions == ("visit 1", "visit 2", "visit 10")
    pairs = {(pair.first, pair.second): pair.bias for pair in agreement.bland_altman}
    assert list(pairs) == [("visit 1", "visit 2"), ("visit 1", "visit 10"), ("visit 2", "visit 10")]
    assert pairs["visit 2", "visit 10"] == pytest.approx(8.0, abs=1.5)
