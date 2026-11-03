from __future__ import annotations

import pytest
from conftest import fixture_lines

from scriptkin import Recording, Refusal, fold, instruments
from scriptkin.contract import JournalBuilder
from scriptkin.labels import Label, labels, safety_flag_count
from scriptkin.recording import BlockOutcome


def session(
    instrument: str, values: dict[str, int | None], *, administration: str = "supervised"
) -> Recording:
    b = JournalBuilder(
        "3F2A9C1E-0000-4000-8000-0000000000BB", "P0099", administration=administration
    ).opened()
    b.answer_all(instrument, 1020.0, values)
    b.shown(1050.0, "crisisList")
    b.close(1051.0)
    return fold(b.lines)


def scores(rec: Recording) -> dict[str, float | Refusal]:
    return {label.scale: label.score for label in labels(rec)}


def refused(cell: float | Refusal) -> bool:
    return isinstance(cell, Refusal) and cell.code == "too_few_values"


def test_phq9_sums_scored_items_only() -> None:
    # Detects a PHQ-9 score that includes the unscored difficulty item or drops a scored item.
    rec = session("phq9", {"phq9-1": 3, "phq9-2": 2, "phq9-9": 1, "difficulty": 3})
    (label,) = labels(rec)
    assert label.score == 6
    boot = "0B0B0B0B-0000-4000-8000-000000000001"
    assert label == Label(
        "P0099",
        rec.session_id,
        "phq9",
        "1.0",
        "en",
        "supervised",
        0,
        1020.0,
        boot,
        BlockOutcome.COMPLETED,
        "total",
        6,
    )


@pytest.mark.parametrize(("instrument", "full"), [("phq9", 27), ("phq8", 24), ("gad7", 21)])
def test_sum_scores_refuse_any_missing_item(instrument: str, full: int) -> None:
    # Detects a sum score that counts a skipped item as zero instead of refusing the block.
    items = instruments.instrument(instrument)["scoring"]["items"]
    assert scores(session(instrument, dict.fromkeys(items, 3))) == {"total": full}
    skipped = scores(session(instrument, {**dict.fromkeys(items, 3), items[-1]: None}))
    assert refused(skipped["total"])


def test_dass21_scales_double_and_prorate_one_missing_item() -> None:
    # Detects DASS-21 scales without the factor 2, without proration, or scored with two missing items.
    rule = instruments.instrument("dass21")["scoring"]["scales"]
    values: dict[str, int | None] = dict.fromkeys(rule["s"], 2)
    values |= dict(zip(rule["d"], (3, 2, 1, 0, 1, 2, None), strict=True))
    values |= dict(zip(rule["a"], (1, 1, 1, 1, 1, None, None), strict=True))
    result = scores(session("dass21", values))
    assert result["s"] == 28
    assert result["d"] == pytest.approx(2 * 9 * 7 / 6)
    assert refused(result["a"])


def test_interrupted_block_gives_a_refusal_not_a_zero() -> None:
    # Detects an interrupted block with one answer scored as a low symptom level.
    blocks = [label.score for label in labels(fold(fixture_lines("recovered_in_questionnaire")))]
    assert refused(blocks[0]) and blocks[1] == 0


def test_unknown_instrument_version_is_refused() -> None:
    # Detects a block scored with the rule of another instrument version.
    rows = fixture_lines("supervised_minor_safety")
    for row in rows:
        if row["kind"] == "questionnaireStarted":
            row["instrument"]["version"] = "9.9"
    assert all(isinstance(label.score, Refusal) for label in labels(fold(rows)))


def test_label_records_carry_administration() -> None:
    # Detects labels that lose the administration mode needed to stratify an analysis.
    assert {label.administration for label in labels(session("gad7", {}, administration="self"))} == {"self"}


@pytest.mark.parametrize(("name", "flags"), [("supervised_minor_safety", 1), ("self_with_remote_review", 1)])
def test_safety_flag_count_counts_endorsed_and_skipped_items(name: str, flags: int) -> None:
    # Detects a safety audit count that misses an endorsed or a skipped safety item.
    rec = fold(fixture_lines(name))
    assert safety_flag_count(rec) == flags
    assert {label.scale for label in labels(rec)} == {"total"}
