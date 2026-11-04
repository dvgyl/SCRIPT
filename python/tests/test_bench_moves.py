from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from scriptkin.bench import PROGRAMS, program
from scriptkin.bench.moves import MoveList, MoveListError, commanded_trial, read_move_list, read_move_lists

STUDY = Path(__file__).resolve().parents[1] / "studies" / "bench_programs.py"
MOVES_OF_TABLE_5_1 = {
    "loops-l-v20": 1904,
    "loops-l-v40": 962,
    "loops-l-v80": 491,
    "loops-s-v20": 1339,
    "loops-s-v40": 679,
    "spiral-l-v20": 1858,
    "spiral-l-v40": 939,
    "spiral-s-v20": 1307,
    "spiral-s-v40": 664,
    "line-osc5": 640,
    "line-osc8": 640,
    "line-osc11": 640,
    "fault-dwell": 1906,
    "fault-lift": 1906,
    "fault-truncated": 952,
}


@pytest.fixture(scope="session")
def programs_folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The move lists that the study script writes, as the lock registers them."""
    folder = tmp_path_factory.mktemp("programs")
    subprocess.run(
        [sys.executable, "-I", str(STUDY), "--write", str(folder)], check=True, capture_output=True
    )
    return folder


@pytest.fixture(scope="session")
def move_lists(programs_folder: Path) -> dict[str, MoveList]:
    return read_move_lists(programs_folder)


def test_every_written_file_reads_back_as_its_registered_program(move_lists: dict[str, MoveList]) -> None:
    # Detects a reader that rebuilds the wrong design from a main or a fault file, or drops moves.
    assert set(move_lists) == {p.id for p in PROGRAMS}
    for pid, ml in move_lists.items():
        assert ml.program == program(pid)
        assert len(ml.moves) == MOVES_OF_TABLE_5_1[pid]
        assert ml.move_s == 0.01
        assert ml.ramp_s == 0.2


@pytest.mark.parametrize(
    ("pid", "start"),
    [
        ("loops-l-v20", (8.0, 24.0)),
        ("loops-s-v40", (5.6, 16.8)),
        ("spiral-l-v20", (60.0, 52.0)),
        ("spiral-s-v20", (42.0, 36.4)),
        ("line-osc8", (4.0, 8.0)),
        ("fault-lift", (8.0, 24.0)),
    ],
)
def test_the_start_point_is_the_first_point_of_the_scaled_stimulus(
    move_lists: dict[str, MoveList], pid: str, start: tuple[float, float]
) -> None:
    # Detects a start field that is absent, unscaled, or in another frame than the moves.
    assert move_lists[pid].start_mm == pytest.approx(start, abs=1e-9)
    first = move_lists[pid].moves[0]
    assert np.hypot(first.x_mm - start[0], first.y_mm - start[1]) < 0.02


def test_the_commanded_path_places_every_move_end_at_its_time(move_lists: dict[str, MoveList]) -> None:
    # Detects a path that drops the start point, misplaces a move end in time, or bends a move.
    ml = move_lists["spiral-l-v40"]
    path = ml.path()
    assert len(path.t) == len(ml.moves) + 1
    assert path.t[0] == 0.0
    assert path.duration_s == pytest.approx(ml.duration_s)
    assert path.xy[0].tolist() == list(ml.start_mm)
    ends = np.array([[m.x_mm, m.y_mm] for m in ml.moves])
    np.testing.assert_allclose(path.position(path.t[1:]), ends)
    midpoints = (path.t[:-1] + path.t[1:]) / 2
    np.testing.assert_allclose(path.position(midpoints), (path.xy[:-1] + path.xy[1:]) / 2)
    assert path.lifts == ()


def test_a_lift_is_in_the_air_between_its_ends_only(move_lists: dict[str, MoveList]) -> None:
    # Detects a lift that splits nothing, or one that cuts the samples at its ends.
    path = move_lists["fault-lift"].path()
    assert len(path.lifts) == 1
    start, end = path.lifts[0]
    assert end - start == pytest.approx(0.7)
    assert path.in_contact(np.array([start - 1e-6, start, (start + end) / 2, end, end + 1e-6])).tolist() == [
        True,
        True,
        False,
        True,
        True,
    ]
    assert np.array_equal(path.position(np.array([start])), path.position(np.array([end])))
    trial = commanded_trial(move_lists["fault-lift"])
    assert len(trial.strokes) == 2
    assert trial.strokes[0].t[-1] == pytest.approx(start)
    assert trial.strokes[1].t[0] == pytest.approx(end)
    dwell = commanded_trial(move_lists["fault-dwell"])
    assert len(dwell.strokes) == 1
    assert np.diff(dwell.strokes[0].t).max() == pytest.approx(0.01)


def test_the_commanded_trial_mirrors_y_into_the_reader_frame(move_lists: dict[str, MoveList]) -> None:
    # Detects a commanded trial in the stimulus frame, where y goes down, against reader output with y up.
    ml = move_lists["loops-l-v20"]
    trial = commanded_trial(ml)
    stroke = trial.strokes[0]
    assert trial.device.spatial_unit == "mm"
    assert stroke.xy[0].tolist() == pytest.approx([8.0, -24.0])
    assert stroke.xy[:, 1].max() == pytest.approx(-4.0, abs=1e-6)
    assert stroke.xy[:, 1].min() == pytest.approx(-24.0, abs=1e-6)
    np.testing.assert_allclose(stroke.xy, ml.path().xy * [1, -1])
    np.testing.assert_array_equal(stroke.t, ml.path().t)


def test_a_file_whose_program_differs_from_the_registry_is_refused(
    programs_folder: Path, tmp_path: Path
) -> None:
    # Detects a reader that trusts the program fields of a file over the registered program.
    raw = json.loads((programs_folder / "loops-l-v20.json").read_text(encoding="utf-8"))
    raw["program"]["design"]["speed_mm_s"] = 21.0
    changed = tmp_path / "loops-l-v20.json"
    changed.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MoveListError, match="registered"):
        read_move_list(changed)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"start": {"x_mm": 8.0}}, "y_mm"),
        ({"moves": [{"x_mm": 1.0, "y_mm": 1.0, "duration_s": -0.01, "lift_mm": 0.0}]}, "positive"),
        ({"battery": {"id": "other", "version": "0"}}, "battery"),
    ],
)
def test_a_damaged_file_is_refused(
    programs_folder: Path, tmp_path: Path, change: dict[str, object], message: str
) -> None:
    # Detects a reader that accepts a missing start coordinate, a negative duration, or another battery.
    raw = json.loads((programs_folder / "line-osc5.json").read_text(encoding="utf-8"))
    raw.update(change)
    damaged = tmp_path / "line-osc5.json"
    damaged.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MoveListError, match=message):
        read_move_list(damaged)


def test_two_files_with_one_program_id_are_refused(programs_folder: Path, tmp_path: Path) -> None:
    # Detects a reader that lets a later file with the id of a locked program replace the locked move
    # list without an error.
    for name in ("loops-s-v40.json", "spiral-s-v40.json"):
        (tmp_path / name).write_bytes((programs_folder / name).read_bytes())
    assert set(read_move_lists(tmp_path)) == {"loops-s-v40", "spiral-s-v40"}
    (tmp_path / "loops-s-v40~edited.json").write_bytes((programs_folder / "loops-s-v40.json").read_bytes())
    with pytest.raises(MoveListError, match=r"loops-s-v40\.json and loops-s-v40~edited\.json both hold"):
        read_move_lists(tmp_path)
