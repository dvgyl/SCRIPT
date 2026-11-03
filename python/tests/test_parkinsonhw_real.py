"""The real UCI ParkinsonHW files in `$SCRIPT_PARKINSONHW`: the folder with hw_dataset/ and new_dataset/.

`./check python` sets the variable only when the unpacked dataset is present. These tests read the files
and never write beside them. The data stays outside the repository.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import pytest

from scriptkin import Accepted, PenState, assess, feature_table, load_parkinsonhw
from scriptkin.formats.parkinsonhw import TESTS
from scriptkin.recording import Recording

CLASS_WORDS = re.compile(r"control|parkinson(?!hw)|hw_dataset|new_dataset", re.IGNORECASE)
CLASS_PREFIX = re.compile(r"\b[CPH]_[A-Za-z0-9]")


@pytest.fixture(scope="module")
def root() -> Path:
    configured = os.environ.get("SCRIPT_PARKINSONHW")
    if configured is None:
        pytest.skip("SCRIPT_PARKINSONHW is not set.")
    folder = Path(configured)
    assert (folder / "hw_dataset").is_dir() and (folder / "new_dataset").is_dir(), folder
    return folder


@pytest.fixture(scope="module")
def data_files(root: Path) -> list[Path]:
    return sorted(
        p
        for part in ("hw_dataset", "new_dataset")
        for p in (root / part).rglob("*.txt")
        if not p.name.startswith("._") and p.name.lower() != "readme.txt"
    )


@pytest.fixture(scope="module")
def recordings(root: Path) -> tuple[Recording, ...]:
    return load_parkinsonhw(root)


def test_every_real_file_reads_with_every_row(
    data_files: list[Path], recordings: tuple[Recording, ...]
) -> None:
    # Detects a reader that refuses or skips a real file, or drops or adds a row.
    assert data_files
    assert len(recordings) == len(data_files)
    rows = sum(len(p.read_bytes().splitlines()) for p in data_files)
    assert sum(len(s.t) for r in recordings for t in r.trials for s in t.strokes) == rows
    assert {t.task.id for r in recordings for t in r.trials} == {
        f"parkinsonhw-{name}" for name in TESTS.values()
    }


def test_real_sample_intervals_are_milliseconds(recordings: tuple[Recording, ...]) -> None:
    # Detects a time unit other than milliseconds: the tablet samples every 7 to 9 ms.
    dt = np.concatenate([np.diff(s.t) for r in recordings for t in r.trials for s in t.strokes])
    p5, median, p95 = np.percentile(dt, [5, 50, 95])
    assert 0.006 <= p5 <= median <= p95 <= 0.010


def test_real_contact_comes_from_pressure(recordings: tuple[Recording, ...]) -> None:
    # Detects contact strokes broken into pieces by the single nonzero Z samples inside real contact.
    spirals = [t for r in recordings for t in r.trials if "spiral" in t.task.id]
    assert all(s.state == PenState.CONTACT for t in spirals for s in t.strokes)
    assert sum(len(t.strokes) for t in spirals) == len(spirals)


def test_real_features_carry_no_class_name(tmp_path: Path, recordings: tuple[Recording, ...]) -> None:
    # Detects a class folder, class prefix, or file name in a real feature row, its provenance, or a code.
    trials = [t for r in recordings for t in r.trials]
    assert any(isinstance(assess(t), Accepted) for t in trials)
    table = feature_table(recordings)
    assert len(table.rows) == len(trials)
    table.to_csv(tmp_path / "features.csv")
    for text in (
        (tmp_path / "features.csv").read_text(),
        (tmp_path / "features.csv.provenance.json").read_text(),
        " ".join(f"{r.participant} {r.session_id}" for r in recordings),
    ):
        assert not CLASS_WORDS.search(text) and not CLASS_PREFIX.search(text)
