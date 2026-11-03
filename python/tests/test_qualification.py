from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from conftest import fixture_lines

from scriptkin import Channel, Recording, Refusal, fold
from scriptkin.contract import MODEL_TABLE_SCALE, JournalBuilder
from scriptkin.qualification import Intervals, qualify
from scriptkin.recording import ModelTableScale

MM = float(MODEL_TABLE_SCALE["mmPerPoint"])


def builder(scale: dict[str, Any] | None = None) -> JournalBuilder:
    session = "3F2A9C1E-0000-4000-8000-0000000000CC"
    scale = scale or dict(MODEL_TABLE_SCALE)
    return JournalBuilder(session, "QUAL-02", purpose="qualification", scale=scale).opened()


def draw(b: JournalBuilder, task: str, t0: float, strokes: list[list[tuple[float, float, float]]]) -> None:
    """One trial of `task`. Each stroke is a list of (t, x, y) samples in capture points."""
    b.trial(task, 1, t0)
    b.began(t0 + 0.5)
    for samples in strokes:
        contact = b.contact(samples[0][0])
        for t, x, y in samples:
            b.sample(contact, t, x, y)
        b.lift(samples[-1][0] + 0.01, contact)
    b.end(max(s[-1][0] for s in strokes) + 1, "participantDone")


def test_sample_interval_distribution_has_exact_percentiles_and_gap_share() -> None:
    # Detects wrong percentiles, a gap share with the wrong threshold, or intervals that cross strokes.
    b = builder()
    times = 1010.0 + np.concatenate(([0.0], np.cumsum((np.arange(100) + 0.5) / 1000)))
    draw(b, "qual-reference", 1000.0, [[(float(t), 100.0, 100.0) for t in times], [(1100.0, 1.0, 1.0)]])
    b.close(1200.0)
    report = qualify(fold(b.lines))
    assert isinstance(report.intervals, Intervals)
    assert report.intervals.count == 100
    assert report.intervals.median_s == pytest.approx(0.050)
    assert report.intervals.p5_s == pytest.approx(0.00545)
    assert report.intervals.p95_s == pytest.approx(0.09455)
    assert report.intervals.gap_share == pytest.approx(0.70)


def ring(t0: float, cx: float, cy: float, radius: float) -> list[tuple[float, float, float]]:
    return [
        (t0 + i / 240, cx + radius * math.cos(i * math.pi / 2), cy + radius * math.sin(i * math.pi / 2))
        for i in range(40)
    ]


def tilted_line(t0: float, offset: float) -> list[tuple[float, float, float]]:
    """Samples along a 30 degree line, `offset` points to each side in the order -, +, +, -."""
    along, across = (
        (math.cos(math.pi / 6), math.sin(math.pi / 6)),
        (-math.sin(math.pi / 6), math.cos(math.pi / 6)),
    )
    samples: list[tuple[float, float, float]] = []
    for i in range(200):
        side = offset if i % 4 in (1, 2) else -offset
        samples.append(
            (t0 + i / 240, 200 + i * along[0] + side * across[0], 400 + i * along[1] + side * across[1])
        )
    return samples


def hold_and_line_session(scale: dict[str, Any] | None = None) -> Recording:
    b = builder(scale)
    draw(b, "qual-hold", 1000.0, [ring(1001.0, 300.0, 300.0, 2.0), ring(1003.0, 700.0, 500.0, 2.0)])
    draw(b, "qual-line", 1010.0, [tilted_line(1011.0, 1.0)])
    b.close(1100.0)
    return fold(b.lines)


def test_hold_jitter_and_line_deviation_are_in_millimetres() -> None:
    # Detects jitter that includes the offset between strokes, or line deviation measured vertically.
    report = qualify(hold_and_line_session())
    assert report.hold_jitter_mm == pytest.approx(2.0 * MM)
    assert report.line_deviation_mm == pytest.approx(1.0 * MM)


def test_unknown_scale_refuses_millimetre_measures() -> None:
    # Detects jitter reported in capture points as if they were millimetres.
    report = qualify(hold_and_line_session(scale={"kind": "unknown"}))
    for value in (report.hold_jitter_mm, report.line_deviation_mm):
        assert isinstance(value, Refusal) and value.code == "unknown_scale"


def test_report_names_scale_source_and_observed_channels() -> None:
    # Detects a report that shows planned channels as observed or loses the scale source.
    report = qualify(fold(fixture_lines("qualification")))
    assert report.scale == ModelTableScale(MM, 264.0, 2.0)
    assert Channel.HOVER in report.planned_channels
    assert report.observed_channels == {Channel.FORCE, Channel.ALTITUDE, Channel.AZIMUTH, Channel.ROLL}
    assert report.device_asset == "IPAD-01"


def test_study_session_is_not_a_qualification_session() -> None:
    # Detects a qualification report computed from a participant session.
    with pytest.raises(ValueError, match="qualification"):
        qualify(fold(fixture_lines("basic")))
