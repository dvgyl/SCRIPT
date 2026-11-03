from __future__ import annotations

import itertools
import json
import math
import struct
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scriptkin import battery, contract

CONTRACT = Path(__file__).resolve().parents[2] / "contract"
SCHEMA = Draft202012Validator(
    json.loads((CONTRACT / "journal.schema.json").read_text()),
    format_checker=Draft202012Validator.FORMAT_CHECKER,
)


def test_checked_in_contract_matches_the_generator() -> None:
    # Detects a hand-edited or stale fixture, battery, or doubles file.
    assert contract.verify(CONTRACT) == []


@pytest.mark.parametrize(
    "path",
    sorted(p for p in (CONTRACT / "fixtures").rglob("*.jsonl") if not p.name.startswith("._")),
    ids=lambda p: p.stem,
)
def test_every_complete_fixture_line_matches_the_schema(path: Path) -> None:
    # Detects a fixture line kind or field that the schema does not allow.
    data = path.read_bytes()
    complete = data[: data.rfind(b"\n") + 1]
    for number, raw in enumerate(complete.splitlines()):
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            assert path.stem == "damaged_interior", f"{path.name}:{number} is not JSON"
            continue
        errors = sorted(SCHEMA.iter_errors(line), key=str)
        assert not errors, f"{path.name}:{number}: {errors[0].message}"


def test_doubles_round_trip_to_the_recorded_bits() -> None:
    # Detects a float text that does not parse back to the same binary64 value.
    rows = json.loads((CONTRACT / "fixtures" / "doubles.json").read_text())["doubles"]
    for row in rows:
        bits = struct.unpack("<Q", struct.pack("<d", float(row["text"])))[0]
        assert f"{bits:016x}" == row["bits"], row


def test_session_closed_counts_equal_a_recount() -> None:
    # Detects a builder that writes counts which integrity rule I4 would reject.
    for name, build in contract.JOURNAL_FIXTURES.items():
        lines = build().lines
        if lines[-1]["kind"] != "sessionClosed":
            continue
        recount = {k: 0 for k in contract.KINDS if k != "sessionClosed"}
        for line in lines[:-1]:
            recount[line["kind"]] += 1
        assert lines[-1]["counts"] == recount, name


def test_spiral_has_two_turns_and_a_50_mm_outer_radius() -> None:
    # Detects a change to the stimulus geometry inherited from SCRIPT v5.
    spec = battery.task_spec("spiral-trace")["stimulus"]
    side = spec["frameMM"][0]
    centre = (side / 2, side / 2)
    path = spec["paths"][0]
    assert math.dist(path[0], centre) == pytest.approx(battery.SPIRAL_INNER_RADIUS_MM, abs=1e-3)
    assert math.dist(path[-1], centre) == pytest.approx(50.0, abs=1e-3)
    assert battery.spiral_radius_mm(2 * math.pi) - battery.spiral_radius_mm(0) == pytest.approx(21.0)


def test_every_stimulus_fits_its_frame_and_has_short_chords() -> None:
    # Detects geometry outside the frame, or a polyline too coarse to draw as a smooth curve.
    for task in battery.battery()["tasks"]:
        stimulus = task["stimulus"]
        if stimulus is None or stimulus["kind"] != "paths":
            continue
        width, height = stimulus["frameMM"]
        for path in stimulus["paths"]:
            for x, y in path:
                assert 0 <= x <= width and 0 <= y <= height, task["id"]
            for a, b in itertools.pairwise(path):
                assert math.dist(a, b) <= 0.5 + 1e-3, task["id"]


def test_every_stimulus_fits_the_smallest_supported_ipad() -> None:
    # Detects a frame that would not fit an iPad mini (A17 Pro) screen, about 115 mm wide.
    for task in battery.battery()["tasks"]:
        if task["stimulus"] is not None:
            assert task["stimulus"]["frameMM"][0] <= 112.0, task["id"]


def test_task_ids_are_unique() -> None:
    ids = [task["id"] for task in battery.battery()["tasks"]]
    assert len(ids) == len(set(ids))
