from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from scriptkin.bench.analysis import LIMITS
from scriptkin.bench.runs import FEATURE_SET

# The protocol table of section 10 prints three decimals, and two for the band power.
DECIMALS = {"bench.band_power_mm2_s2": 2}


def _bench_limits_script(monkeypatch: pytest.MonkeyPatch) -> Any:
    path = Path(__file__).resolve().parents[1] / "studies" / "bench_limits.py"
    spec = importlib.util.spec_from_file_location("bench_limits_study", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    # The script's dataclass resolves its string annotations through sys.modules.
    monkeypatch.setitem(sys.modules, spec.name, script)
    spec.loader.exec_module(script)
    return script


def test_the_registered_limits_equal_the_limits_that_the_study_script_computes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Detects a value of analysis.LIMITS that differs from the limit of bench_limits.py at the
    # precision of the protocol table, a feature of the set without a limit, or a limit of a feature
    # outside the set.
    table = {
        feature: (scale, limit) for feature, scale, limit, _ in _bench_limits_script(monkeypatch).limits()
    }
    assert set(table) == set(FEATURE_SET) == set(LIMITS)
    for feature, (scale, limit) in table.items():
        decimals = DECIMALS.get(feature, 3)
        assert round(limit, decimals) == LIMITS[feature], (feature, limit, LIMITS[feature])
        assert (scale == "absolute") == (feature == "bench.speed_cv")
