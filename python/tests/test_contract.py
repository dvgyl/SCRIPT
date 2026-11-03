from __future__ import annotations

import copy
import itertools
import json
import math
import struct
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from scriptkin import battery, contract, instruments, studyconfig

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


CONFIG_SCHEMA = Draft202012Validator(
    json.loads((CONTRACT / "config.schema.json").read_text()),
    format_checker=Draft202012Validator.FORMAT_CHECKER,
)


def _demo() -> dict[str, Any]:
    return copy.deepcopy(studyconfig.demo_config())


def test_demo_config_matches_the_schema_and_every_rule() -> None:
    # Detects a shipped demo configuration that the app would refuse to import.
    config = json.loads((CONTRACT / "configs" / "demo.json").read_text())
    assert not list(CONFIG_SCHEMA.iter_errors(config))
    assert studyconfig.validate(config) == []


def test_self_administered_safety_item_needs_remote_review() -> None:
    # Detects a configuration that puts PHQ-9 item 9 in an unmonitored home session.
    config = _demo()
    config["questionnaires"].append(
        {"administration": "self", "ageBand": "18-24", "position": "afterTasks", "instruments": ["phq9"]}
    )
    assert any("remoteReview" in p for p in studyconfig.validate(config))
    config["safety"]["remoteReview"] = {
        "reviewerRole": "study clinician",
        "imminentReviewWithinHours": 24,
        "routineReviewWithinWeekdays": 1,
        "exportEveryHours": 12,
        "monitoringDisclosureVersion": "1",
    }
    assert studyconfig.validate(config) == []
    config["safety"]["remoteReview"]["exportEveryHours"] = 48
    assert any("exportEveryHours" in p for p in studyconfig.validate(config))


def test_unverified_or_out_of_range_instruments_are_refused() -> None:
    # Detects a configuration that administers unverified text or an instrument outside its validated ages.
    config = _demo()
    config["questionnaires"].append(
        {
            "administration": "supervised",
            "ageBand": "13-17",
            "position": "afterTasks",
            "instruments": ["dass21"],
        }
    )
    problems = studyconfig.validate(config)
    assert any("not verified" in p for p in problems)
    assert any("not validated for age band 13-17" in p for p in problems)


def test_study_config_without_key_or_pin_is_refused() -> None:
    # Detects a real study configuration that would write plaintext exports or open operator screens freely.
    config = _demo()
    config["demo"] = False
    problems = studyconfig.validate(config)
    assert any("recipientPublicKey" in p for p in problems)
    assert any("operatorPin" in p for p in problems)


def test_qualification_tasks_cannot_enter_a_study_session() -> None:
    # Detects a study configuration that mixes device-qualification tasks into participant sessions.
    config = _demo()
    config["tasks"].append({"task": "qual-hold", "practice": False, "attempts": 1, "hands": "dominant"})
    assert any("qualification" in p for p in studyconfig.validate(config))


def test_phq9_item_9_is_the_only_safety_item_in_the_default_instruments() -> None:
    # Detects a lost or misplaced safety flag on an instrument item.
    flagged = {item["id"] for spec in instruments.instruments() for item in spec["items"] if item["safety"]}
    assert flagged == {"phq9-9"}


def test_a_skipped_or_endorsed_safety_item_is_flagged_in_the_fixtures() -> None:
    # Detects a builder that misses the rule that a skipped safety item counts as a flag.
    minor = json.loads((CONTRACT / "fixtures" / "journal" / "supervised_minor_safety.fold.json").read_text())
    remote = json.loads((CONTRACT / "fixtures" / "journal" / "self_with_remote_review.fold.json").read_text())
    assert minor["safety"] == {"flags": 1, "crisisListShown": 1, "operatorAlerts": 1, "actions": 1}
    assert remote["safety"]["flags"] == 1 and remote["safety"]["crisisListShown"] == 2


def test_one_correction_resolves_every_sample_that_shares_its_key() -> None:
    # Detects the summary rule for repeated (contact, update) keys drifting from DESIGN section 3.3.
    fold = json.loads((CONTRACT / "fixtures" / "journal" / "repeated_update_key.fold.json").read_text())
    assert fold["trials"][0]["unresolved"] == 0
    cross = json.loads((CONTRACT / "fixtures" / "journal" / "cross_contact_update.fold.json").read_text())
    assert cross["unmatchedCorrections"] == 1 and cross["trials"][0]["unresolved"] == 1
