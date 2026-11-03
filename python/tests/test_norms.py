from __future__ import annotations

import numpy as np
import pytest

from scriptkin.evaluate.norms import NormRow, centiles, fit_norms
from scriptkin.quality import Refusal, RefusalCode


def rows(n: int, seed: int) -> list[NormRow]:
    """Norms whose location and scale both change with age. Hand shifts the location."""
    rng = np.random.default_rng(seed)
    age = rng.uniform(13, 25, n)
    left = rng.random(n) < 0.2
    location = 20 + 3 * np.log(age) + 0.8 * left
    scale = 0.5 + 0.08 * (age - 13)
    value = location + scale * rng.standard_normal(n)
    return [
        NormRow({"age": float(a), "hand": "left" if h else "right"}, {"speed": float(v)})
        for a, h, v in zip(age, left, value, strict=True)
    ]


def test_centiles_cover_the_5th_and_95th_percentiles_at_the_nominal_rate() -> None:
    # Detects a normative model with a wrong location, or a constant scale where the scale grows with age.
    norms = fit_norms(rows(4000, 1), ("age", "hand"))
    low, high = norms.support.age or (0.0, 0.0)
    test = [r for r in rows(4000, 2) if low <= float(r.covariates["age"]) <= high]
    assert len(test) > 3990
    values = np.array([cells["speed"] for cells in centiles(norms, test)], dtype=float)
    age = np.array([float(r.covariates["age"]) for r in test])
    for part in (age < 17, age > 21, np.ones(len(age), dtype=bool)):
        assert 0.032 <= np.mean(values[part] < 5) <= 0.068
        assert 0.032 <= np.mean(values[part] > 95) <= 0.068


def test_a_row_outside_the_covariate_range_is_refused() -> None:
    # Detects a centile extrapolated beyond the ages or the levels of the normative sample.
    train = rows(500, 3)
    norms = fit_norms(train, ("age", "hand"))
    ages = [float(r.covariates["age"]) for r in train]
    assert norms.support.age == (min(ages), max(ages))
    probe = [
        NormRow({"age": 30.0, "hand": "right"}, {"speed": 30.0}),
        NormRow({"age": min(ages) - 0.1, "hand": "right"}, {"speed": 30.0}),
        NormRow({"age": 18.0, "hand": "unknown"}, {"speed": 30.0}),
        NormRow({"hand": "right"}, {"speed": 30.0}),
        NormRow({"age": 18.0, "hand": "right"}, {"speed": 29.0}),
    ]
    cells = [c["speed"] for c in centiles(norms, probe)]
    codes = [c.code if isinstance(c, Refusal) else None for c in cells]
    assert codes == [RefusalCode.OUT_OF_RANGE] * 3 + [RefusalCode.UNDEFINED, None]
    assert cells[4] == pytest.approx(50.0, abs=40.0)
