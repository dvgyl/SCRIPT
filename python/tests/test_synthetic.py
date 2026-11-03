from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import SPIRAL, Analysed
from jsonschema import Draft202012Validator
from scipy.special import expit

from scriptkin import feature_table, fold, read_journal
from scriptkin import synthetic as syn
from scriptkin.evaluate.participants import medians
from scriptkin.evaluate.plan import column
from scriptkin.formats.journal import summarize

CONTRACT = Path(__file__).resolve().parents[2] / "contract"
SCHEMA = json.loads((CONTRACT / "journal.schema.json").read_text())
FULL = Draft202012Validator(SCHEMA)
# Each branch of the top-level oneOf fixes `kind` with a const, so a line matches the whole schema exactly
# when it matches the definition of its own kind. Checking that definition alone is much faster.
BY_KIND = {
    kind: Draft202012Validator({"$ref": f"#/$defs/{kind}", "$defs": SCHEMA["$defs"]})
    for kind in SCHEMA["$defs"]
}


def check_exports(folders: list[Path], journals: tuple[syn.Journal, ...]) -> set[str]:
    kinds: set[str] = set()
    for folder, journal in zip(folders, journals, strict=True):
        raw = (folder / "journal.jsonl").read_bytes().splitlines(keepends=True)
        for number, line in enumerate(raw):
            row = json.loads(line)
            validator = FULL if row["kind"] not in kinds else BY_KIND[row["kind"]]
            kinds.add(row["kind"])
            errors = sorted(validator.iter_errors(row), key=str)
            assert not errors, f"{folder.name}:{number}: {errors[0].message}"
        assert read_journal(folder).session_id == journal.session_id
    return kinds


def test_every_synthetic_journal_passes_read_journal_and_the_schema(tmp_path: Path) -> None:
    # Detects a generated line that the schema, the integrity rules I1 to I7, or QC would reject.
    devices = (syn.IPAD_PRO_PENCIL_PRO, syn.IPAD_PRO_PENCIL_2, syn.IPAD_AIR_PENCIL_USBC)
    cohort = syn.cohort(2, seed=11, devices=devices, crossed=True, tasks=("practice", *syn.MEASURED_TASKS))
    kinds = check_exports(cohort.write(tmp_path), cohort.journals)
    assert {"hover", "correction", "response", "safetyShown", "sessionClosed"} <= kinds
    table = feature_table(cohort.recordings)
    assert len(table.rows) == 2 * len(devices) * len(syn.MEASURED_TASKS)
    assert all(not row.qc for row in table.rows)


def test_declined_trials_and_skipped_items_pass_read_journal(
    missing_cohort: Analysed, tmp_path: Path
) -> None:
    # Detects a declined trial or a skipped item written in a form that the schema or the reader rejects.
    truth = {s.session_id: s for p in missing_cohort.cohort.truth.participants for s in p.sessions}
    chosen = tuple(
        j
        for j in missing_cohort.cohort.journals
        if truth[j.session_id].declined or None in truth[j.session_id].phq9
    )
    assert any(truth[j.session_id].declined for j in chosen)
    assert any(None in truth[j.session_id].phq9 for j in chosen)
    check_exports(replace(missing_cohort.cohort, journals=chosen).write(tmp_path), chosen)


def test_labels_come_from_the_response_lines(signal_cohort: Analysed) -> None:
    # Detects labels copied from the truth instead of scored from the journal response lines.
    truth = {s.session_id: s for p in signal_cohort.cohort.truth.participants for s in p.sessions}
    scored = {(lab.session, lab.instrument): lab.score for lab in signal_cohort.cohort.labels}
    for session_id, session in truth.items():
        assert scored[(session_id, "phq9")] == sum(v or 0 for v in session.phq9)
    journal = signal_cohort.cohort.journals[0]
    rows: list[dict[str, Any]] = [json.loads(line) for line in journal.data.splitlines()]
    first = next(r for r in rows if r["kind"] == "response" and r["item"] == "phq9-1")
    first["value"] = 3 - first["value"]
    edited = replace(signal_cohort.cohort, recordings=(fold(rows), *signal_cohort.cohort.recordings[1:]))
    answers = truth[journal.session_id].phq9
    expected = sum(v or 0 for v in answers) + 3 - 2 * (answers[0] or 0)
    phq9 = (
        lab.score for lab in edited.labels if (lab.session, lab.instrument) == (journal.session_id, "phq9")
    )
    assert next(phq9) == expected


def test_planted_effect_moves_only_the_chosen_traits(signal_cohort: Analysed, null_cohort: Analysed) -> None:
    # Detects an effect that reaches the wrong trait, misses its trait, or leaks into the null scenario.
    def correlations(analysed: Analysed) -> dict[str, float]:
        people = analysed.cohort.truth.participants
        d = np.array([p.depression for p in people])
        return {t: float(np.corrcoef(d, [p.scores[t] for p in people])[0, 1]) for t in syn.TRAITS}

    signal = correlations(signal_cohort)
    assert signal["speed_mm_s"] < -0.35 and signal["tremor_mm"] > 0.35
    assert all(abs(r) < 0.2 for t, r in signal.items() if t not in ("speed_mm_s", "tremor_mm"))
    assert all(abs(r) < 0.2 for r in correlations(null_cohort).values())
    speed = column("spiral-trace", "kinematic.speed_mean")
    keys, values = medians(signal_cohort.table, [speed], lambda r: r.participant)
    case = {p.participant: p.case for p in signal_cohort.cohort.truth.participants}
    cases = np.array([bool(case[k]) for k in keys])
    assert np.median(values[cases, 0]) < 0.85 * np.median(values[~cases, 0])


def test_latent_mean_gives_the_stated_phq9_prevalence() -> None:
    # Detects a quadrature or item convolution error that gives a cohort the wrong case prevalence.
    rng = np.random.default_rng(5)
    for prevalence in (0.08, 0.3):
        theta = syn.latent_mean(prevalence, 10) + rng.standard_normal(40000)
        totals = np.zeros(len(theta), dtype=np.int64)
        for a, b in syn.PHQ9_ITEMS:
            u = rng.random(len(theta))
            totals += sum((u < expit(a * (theta - bk))).astype(np.int64) for bk in b)
        assert np.mean(totals >= 10) == pytest.approx(prevalence, abs=0.006)


def test_the_seed_controls_every_random_stream() -> None:
    # Detects a random draw outside the seeded streams, which makes a cohort irreproducible.
    first = syn.cohort(3, seed=4, tasks=SPIRAL)
    again = syn.cohort(3, seed=4, tasks=SPIRAL)
    other = syn.cohort(3, seed=5, tasks=SPIRAL)
    assert [j.data for j in first.journals] == [j.data for j in again.journals]
    assert first.truth == again.truth
    assert [j.data for j in first.journals] != [j.data for j in other.journals]


def test_device_models_reach_the_journal(device_cohort: Analysed) -> None:
    # Detects a device model whose channels, sample rate, or late force corrections are not in the journal.
    rows: dict[str, list[dict[str, Any]]] = {}
    for journal in device_cohort.cohort.journals[:20]:
        lines = [json.loads(line) for line in journal.data.splitlines()]
        rows.setdefault(lines[0]["device"]["model"], []).extend(lines)
        summary = summarize(journal.data.splitlines(keepends=True))
        assert summary["unmatchedCorrections"] == 0
        assert all(t["unresolved"] == 0 for t in summary["trials"])
    pro, usbc = rows[syn.IPAD_PRO_PENCIL_PRO.model], rows[syn.IPAD_AIR_PENCIL_USBC.model]
    samples = [r for r in pro if r["kind"] == "sample"]
    assert all("force" in r and "roll" in r for r in samples)
    assert not any("force" in r or "roll" in r for r in usbc if r["kind"] == "sample")
    assert any(r["kind"] == "hover" for r in pro) and not any(r["kind"] == "hover" for r in usbc)
    assert any(r["kind"] == "correction" for r in pro) and not any(r["kind"] == "correction" for r in usbc)
    intervals = np.concatenate(
        [np.diff([r["t"] for r in samples if r["contact"] == c]) for c in {r["contact"] for r in samples}]
    )
    assert 1 / np.median(intervals) == pytest.approx(syn.IPAD_PRO_PENCIL_PRO.rate_hz, rel=0.01)


def test_scale_bias_scales_millimetre_features() -> None:
    # Detects a device scale bias that does not reach positions, so scale errors cannot be simulated.
    true = replace(syn.IPAD_PRO_PENCIL_PRO, position_noise_mm=0.0)
    biased = replace(true, scale_bias=1.1)
    speeds = []
    for device in (true, biased):
        table = feature_table(syn.cohort(3, seed=8, devices=(device,), tasks=SPIRAL).recordings)
        speeds.append(np.array([row.cells["kinematic.speed_mean"] for row in table.rows], dtype=float))
    np.testing.assert_allclose(speeds[1] / speeds[0], 1.1, rtol=0.01)
