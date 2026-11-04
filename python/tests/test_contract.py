from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import itertools
import json
import math
import re
import struct
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from scriptkin import battery, bench, contract, instruments, studyconfig

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
    # Detects a frame that would not fit an iPad mini (A17 Pro) screen, about 115 mm wide. A bench task
    # runs on the iPad of the bench protocol only, so its frame is outside this rule.
    for task in battery.battery()["tasks"]:
        if task["stimulus"] is not None and task["id"] not in battery.BENCH_TASKS:
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


def test_unverified_or_out_of_range_instruments_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects a configuration that administers unverified text or an instrument outside its validated ages.
    table = instruments.instruments()
    for spec in table:
        if spec["id"] == "dass21":
            spec["textStatus"] = "unverified"
    monkeypatch.setattr(instruments, "instruments", lambda: table)
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


def test_export_key_id_must_be_the_hash_of_the_public_key() -> None:
    # Detects a configuration whose keyID names another key than export.recipientPublicKey.
    public = bytes.fromhex("4310ee97d88cc1f088a5576c77ab0cf5c3ac797f3d95139c6c84b5429c59662a")
    config = _demo()
    config["export"] = {
        "recipientPublicKey": base64.b64encode(public).decode(),
        "keyID": hashlib.sha256(public).hexdigest(),
    }
    assert studyconfig.validate(config) == []
    config["export"]["keyID"] = hashlib.sha256(bytes(32)).hexdigest()
    assert any("keyID" in p for p in studyconfig.validate(config))


def test_qualification_tasks_cannot_enter_a_study_session() -> None:
    # Detects a study configuration that mixes device-qualification tasks into participant sessions.
    config = _demo()
    config["tasks"].append({"task": "qual-hold", "practice": False, "attempts": 1, "hands": "dominant"})
    assert any("qualification" in p for p in studyconfig.validate(config))


def test_bench_tasks_cannot_enter_a_study_session() -> None:
    # Detects a study configuration that mixes bench geometry into participant sessions.
    config = _demo()
    config["tasks"].append({"task": "bench-loops-s", "practice": False, "attempts": 1, "hands": "dominant"})
    assert any("bench" in p for p in studyconfig.validate(config))


def _bench_programs_script(monkeypatch: pytest.MonkeyPatch) -> Any:
    path = Path(__file__).resolve().parents[1] / "studies" / "bench_programs.py"
    spec = importlib.util.spec_from_file_location("bench_programs_study", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    # The script's dataclasses resolve their string annotations through sys.modules.
    monkeypatch.setitem(sys.modules, spec.name, script)
    spec.loader.exec_module(script)
    return script


def test_bench_programs_script_uses_the_package_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects a study script that keeps its own copy of the program table instead of the package registry.
    script = _bench_programs_script(monkeypatch)
    assert script.MAIN is bench.MAIN and script.FAULTS is bench.FAULTS and script.Design is bench.Design


def test_bench_move_lists_keep_the_program_keys_of_protocol_1_1(monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects a Design field, such as base, that reaches the program object of a move-list file, which
    # changes the files that the protocol lock hashes.
    script = _bench_programs_script(monkeypatch)
    keys = ["id", "task", "geometry", "scale", "speed_mm_s", "oscillation_hz", "oscillation_mm", "fault"]
    summary, _ = script.program(bench.MAIN[0])
    assert list(script.registered(summary)["design"]) == keys
    assert list(script.registered(bench.FAULTS[0])) == keys


def test_bench_programs_match_the_generator_and_the_battery() -> None:
    # Detects a programs file with a wrong entry list, key order, task, scale, oscillation, base, or digest,
    # a bench fixture run that differs from the table, or a bench task whose geometry is not the scaled base
    # geometry or the line of the protocol.
    programs = json.loads((CONTRACT / "bench_programs.json").read_text())
    assert programs == json.loads(bench.programs_json())
    assert programs["protocol"] == {"version": "1.2", "sha256": bench.PROTOCOL_SHA256}
    table = {p["id"]: p for p in programs["programs"]}
    assert list(table) == [
        "loops-l-v20", "loops-l-v40", "loops-l-v80", "loops-s-v20", "loops-s-v40",
        "spiral-l-v20", "spiral-l-v40", "spiral-s-v20", "spiral-s-v40",
        "line-osc5", "line-osc8", "line-osc11",
        "fault-dwell", "fault-lift", "fault-truncated",
    ]  # fmt: skip
    keys = ["id", "task", "scale", "speedMMPerS", "oscillation", "fault", "base", "trajectorySHA256"]
    assert all(list(p) == keys for p in table.values())
    main = [p for p in table.values() if p["fault"] is None]
    faults = [p for p in table.values() if p["fault"] is not None]
    assert len(main) == 12 and all(p["base"] is None for p in main)
    assert [(p["id"], p["fault"], p["base"]) for p in faults] == [
        ("fault-dwell", "dwell", "loops-l-v20"),
        ("fault-lift", "lift", "loops-l-v20"),
        ("fault-truncated", "truncated", "loops-l-v20"),
    ]
    own = ("id", "fault", "base", "trajectorySHA256")
    for p in faults:
        base = table[p["base"]]
        assert base in main
        assert {k: v for k, v in p.items() if k not in own} == {k: v for k, v in base.items() if k not in own}
    assert (table["loops-s-v40"]["task"], table["loops-s-v40"]["scale"]) == ("bench-loops-s", 0.7)
    assert (table["spiral-l-v20"]["speedMMPerS"], table["spiral-l-v20"]["oscillation"]) == (20.0, None)
    assert (table["line-osc8"]["task"], table["line-osc8"]["scale"]) == ("bench-line", 1.0)
    assert table["line-osc8"]["oscillation"] == {"hz": 8.0, "mm": 0.25}
    assert {p["task"] for p in table.values()} == set(battery.BENCH_TASKS)
    digests = {p["trajectorySHA256"] for p in table.values()}
    assert len(digests) == 15 and all(re.fullmatch(r"[0-9a-f]{64}", d) for d in digests)
    for name in ("bench_ipad", "bench_wacom"):
        runs = [r for r in _fixture_rows(name) if r["kind"] == "trialStarted"]
        assert runs
        for run in runs:
            program = table[run["bench"]["program"]]
            assert run["trial"]["task"] == program["task"]
            assert run["bench"]["trajectorySHA256"] == program["trajectorySHA256"]
    loops, spiral = battery.task_spec("loops")["stimulus"], battery.task_spec("spiral-trace")["stimulus"]
    assert battery.task_spec("bench-loops-l")["stimulus"] == loops
    assert battery.task_spec("bench-spiral-l")["stimulus"] == spiral
    for bench_id, base in (("bench-loops-s", loops), ("bench-spiral-s", spiral)):
        small = battery.task_spec(bench_id)["stimulus"]
        assert small["frameMM"] == pytest.approx([v * 0.7 for v in base["frameMM"]], abs=5e-5)
        assert small["start"] == pytest.approx([v * 0.7 for v in base["start"]], abs=5e-5)
        assert len(small["paths"][0]) == len(base["paths"][0])
        assert small["paths"][0][7] == pytest.approx([v * 0.7 for v in base["paths"][0][7]], abs=5e-5)
    line = battery.task_spec("bench-line")["stimulus"]
    assert (line["frameMM"], line["start"], len(line["paths"])) == ([132.0, 16.0], [4.0, 8.0], 1)
    assert (line["paths"][0][0], line["paths"][0][-1]) == ([4.0, 8.0], [128.0, 8.0])
    assert {y for _, y in line["paths"][0]} == {8.0}


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


def _fixture_rows(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (CONTRACT / "fixtures" / "journal" / f"{name}.jsonl").read_bytes().splitlines()
    ]


@pytest.mark.parametrize(
    ("name", "end_kind", "crisis_list"),
    [
        ("declined_unreached_safety", "questionnaireEnded", True),
        ("interrupted_unreached_safety", "recovered", False),
    ],
)
def test_an_unreached_safety_item_is_flagged_inside_its_block(
    name: str, end_kind: str, crisis_list: bool
) -> None:
    # Detects a block that ends declined or interrupted before PHQ-9 item 9 and leaves no safety flag.
    rows = _fixture_rows(name)
    started = next(i for i, r in enumerate(rows) if r["kind"] == "questionnaireStarted")
    end = next(i for i, r in enumerate(rows) if r["kind"] == end_kind and i > started)
    block = rows[started]["block"]
    flags = [r for r in rows if r["kind"] == "safetyFlagged"]
    assert flags == [r for r in rows[started:end] if r["kind"] == "safetyFlagged"]
    assert [(f["block"], f["item"], f["skipped"], "value" in f) for f in flags] == [
        (block, "phq9-9", True, False)
    ]
    assert not any(r["kind"] == "response" and r["item"] == "phq9-9" for r in rows[started:end])
    shown_in_block = [r["what"] for r in rows[started:end] if r["kind"] == "safetyShown"]
    assert shown_in_block == (["crisisList"] if crisis_list else [])
    last = max(r["t"] for r in rows[started:end] if r["kind"] in ("questionnaireStarted", "response"))
    if end_kind == "recovered":
        assert flags[0]["t"] == last
    fold = json.loads((CONTRACT / "fixtures" / "journal" / f"{name}.fold.json").read_text())
    assert fold["safety"]["flags"] == 1


def test_an_empty_source_revision_is_outside_the_schema() -> None:
    # Detects a journal schema that accepts an empty app.sourceRevision, which names no build.
    head = _fixture_rows("basic")[0]
    assert not list(SCHEMA.iter_errors(head))
    head["app"]["sourceRevision"] = ""
    assert list(SCHEMA.iter_errors(head))


def test_the_resume_window_has_a_default_and_a_schema_shape() -> None:
    # Detects a demo configuration without the resume window, or a schema that accepts a window of zero hours.
    config = _demo()
    assert config["selfAdministered"] == {"resumeWithinHours": 24}
    config["selfAdministered"]["resumeWithinHours"] = 0
    assert list(CONFIG_SCHEMA.iter_errors(config))
    del config["selfAdministered"]
    assert not list(CONFIG_SCHEMA.iter_errors(config))


def test_a_resume_window_longer_than_the_review_interval_is_refused() -> None:
    # Detects a self-administered safety item whose flags wait in an interrupted session past the review.
    config = _demo()
    config["questionnaires"].append(
        {"administration": "self", "ageBand": "13-17", "position": "afterTasks", "instruments": ["phq9"]}
    )
    config["safety"]["remoteReview"] = {
        "reviewerRole": "study clinician",
        "imminentReviewWithinHours": 12,
        "routineReviewWithinWeekdays": 1,
        "exportEveryHours": 12,
        "monitoringDisclosureVersion": "1",
    }
    assert studyconfig.validate(config) == [
        "selfAdministered.resumeWithinHours is longer than the review interval"
    ]
    config["selfAdministered"]["resumeWithinHours"] = 12
    assert studyconfig.validate(config) == []
    del config["selfAdministered"]
    assert studyconfig.validate(config) == [
        "selfAdministered.resumeWithinHours is longer than the review interval"
    ]


def test_one_correction_resolves_every_sample_that_shares_its_key() -> None:
    # Detects the summary rule for repeated (contact, update) keys drifting from DESIGN section 3.3.
    fold = json.loads((CONTRACT / "fixtures" / "journal" / "repeated_update_key.fold.json").read_text())
    assert fold["trials"][0]["unresolved"] == 0
    cross = json.loads((CONTRACT / "fixtures" / "journal" / "cross_contact_update.fold.json").read_text())
    assert cross["unmatchedCorrections"] == 1 and cross["trials"][0]["unresolved"] == 1
