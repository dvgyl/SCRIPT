from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import CONTRACT, FIXTURES, write_encrypted_export, write_export
from conftest import fixture_lines as lines
from jsonschema import Draft202012Validator

from scriptkin import Channel, PenState, bench
from scriptkin.contract import FORCE, JournalBuilder
from scriptkin.formats.export import load_export, read_journal
from scriptkin.formats.journal import JournalError, fold, summarize
from scriptkin.recording import (
    BenchContext,
    BenchRunContext,
    BlockOutcome,
    DocumentRef,
    EndReason,
    LocalizedRef,
    ModelTableScale,
    ParticipantContext,
    ReferenceScale,
    TabletContext,
    TabletScale,
    TrialContext,
    TrialOutcome,
)

SCHEMA = Draft202012Validator(json.loads((CONTRACT / "journal.schema.json").read_bytes()))

FOLD_CASES = sorted(
    p for p in (FIXTURES / "journal").glob("*.jsonl") if not p.name.startswith("._")
) + sorted(p for p in (FIXTURES / "export").glob("*/journal.jsonl") if not p.parent.name.startswith("._"))


def oracle(path: Path) -> Path:
    """The .fold.json beside a journal fixture, or beside the folder of an export fixture."""
    return (
        path.with_suffix(".fold.json")
        if path.parent.name == "journal"
        else path.parent.with_suffix(".fold.json")
    )


def renumber(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for i, row in enumerate(rows):
        row["seq"] = i
    return rows


def move(rows: list[dict[str, Any]], kind: str, after: str) -> list[dict[str, Any]]:
    """Move the first line of `kind` to just after the first line of kind `after`."""
    row = rows.pop(next(i for i, r in enumerate(rows) if r["kind"] == kind))
    rows.insert(next(i for i, r in enumerate(rows) if r["kind"] == after) + 1, row)
    return renumber(rows)


@pytest.mark.parametrize("path", FOLD_CASES, ids=lambda p: p.stem if p.parent.name == "journal" else "export")
def test_fold_matches_independent_builder(path: Path) -> None:
    # Detects a Python summary or Recording that differs from the builder's .fold.json summary.
    raw = path.read_bytes().splitlines(keepends=True)
    expected = json.loads(oracle(path).read_bytes())
    assert summarize(raw) == expected
    folded = []
    for trial in fold(raw).trials:
        contacts = [s for s in trial.strokes if s.state == PenState.CONTACT]
        folded.append(
            {
                "outcome": trial.outcome.value,
                "contacts": len(contacts),
                "samples": sum(len(s.t) for s in contacts),
                "unresolved": sum(int(np.count_nonzero(s.unresolved)) for s in contacts),
            }
        )
    assert folded == [
        {k: t[k] for k in ("outcome", "contacts", "samples", "unresolved")} for t in expected["trials"]
    ]


def test_export_reader_checks_and_converts() -> None:
    # Detects a reader that omits origin translation, y inversion, path scale, or late force correction.
    recording = read_journal(FIXTURES / "export" / "SCRIPT_DEMO_DEMO_3f2a9c1e")
    trial = recording.trials[0]
    first = trial.strokes[0]
    scale = lines()[0]["scale"]["mmPerPoint"]
    np.testing.assert_allclose(first.xy[0], [(300 - 147.5) * scale, -(400 - 200) * scale])
    assert first.channels[Channel.FORCE][0] == 0.42
    assert not first.unresolved.any()
    # The spiral starts 8 mm right of the centre of its 104 mm frame. The frame origin is its top-left.
    assert trial.task.paths[0][0].tolist() == [60.0, -52.0]
    assert len(recording.source_hashes[0]) == 64


def test_reader_keeps_session_context() -> None:
    # Detects a reader that drops or mixes up the session context in the journal header.
    from scriptkin.contract import demo_config_sha256

    context = fold(lines("supervised_minor_safety")).context
    assert context is not None
    person = context.subject
    assert isinstance(person, ParticipantContext)
    assert (context.purpose, person.administration, person.age_band) == ("study", "supervised", "13-17")
    assert (person.jurisdiction, person.locale, context.device_asset) == ("GB", "en", "IPAD-01")
    assert (person.visit.id, person.visit.number) == ("V1", 1)
    assert person.study.config_sha256 == demo_config_sha256()
    assert (person.consent.model, person.consent.attested_role) == ("guardianAndAssent", "operator")
    assert [d.id for d in person.consent.documents] == ["demo-guardian-permission", "demo-assent"]
    assert person.ethics.approval_id == "DEMO-NOT-APPROVED"
    assert (context.surface.protector, context.surface.nib) == ("none", "new")
    assert isinstance(context.scale, ModelTableScale) and context.scale.ppi == 264.0
    assert context.qualification is None and context.tablet is None
    self_context = fold(lines("self_administered")).context
    assert self_context is not None and isinstance(self_context.subject, ParticipantContext)
    assert self_context.subject.administration == "self"
    assert self_context.subject.consent.attested_role == "enrolment"
    assert self_context.scale == ReferenceScale("idCard", 0.1925, 85.6, 444.67532467532465)


def test_reader_keeps_trial_context_begin_time_and_end_reason() -> None:
    # Detects a trial that loses its practice flag, position, hand, accommodations, Begin time, or end reason.
    trials = fold(lines("multi_trial")).trials
    contexts = [t.context for t in trials if isinstance(t.context, TrialContext)]
    assert len(contexts) == len(trials)
    assert [c.practice for c in contexts] == [True, False, False, False, False, False]
    assert [c.position for c in contexts] == [1, 2, 3, 4, 5, 6]
    assert contexts[5].accommodations == ("readAloud",)
    assert contexts[1].instruction == LocalizedRef("spiral-trace", "1.0", "en")
    assert [t.began for t in trials] == [1005.5, 1010.4, 1013.1, 1016.2, None, 1147.0]
    assert [t.end_reason for t in trials] == [
        EndReason.PARTICIPANT_DONE,
        EndReason.PARTICIPANT_DONE,
        EndReason.PARTICIPANT_DONE,
        EndReason.TIME_LIMIT,
        EndReason.DECLINED,
        EndReason.UNABLE,
    ]
    assert trials[4].outcome == TrialOutcome.DECLINED
    left = fold(lines("usbc_no_force")).trials[0].context
    assert isinstance(left, TrialContext) and left.hand == "left"


def test_bench_header_and_run_facts_reach_the_recording() -> None:
    # Detects a reader that loses the bench header, a run fact, the clip, a mark, or the end reason.
    rec = fold(lines("bench_ipad"))
    assert rec.participant is None and rec.questionnaires == ()
    context = rec.context
    assert context is not None and context.purpose == "bench" and context.tablet is None
    assert context.subject == BenchContext(
        "P", "M1", DocumentRef("nextdraw-8511", "unknown"), bench.PROTOCOL_VERSION, bench.PROTOCOL_SHA256
    )
    assert context.device_asset == "IPAD-02"
    assert [t.context for t in rec.trials] == [
        BenchRunContext(
            False, 1, "loops-l-v20", 1, bench.trajectory_sha256("loops-l-v20"), "P-M1-loops-l-v20-r1"
        ),
        BenchRunContext(
            False, 2, "spiral-s-v40", 1, bench.trajectory_sha256("spiral-s-v40"), "P-M1-spiral-s-v40-r1"
        ),
        BenchRunContext(
            False, 3, "fault-lift", 1, bench.trajectory_sha256("fault-lift"), "P-M1-fault-lift-r1"
        ),
    ]
    assert [t.task.id for t in rec.trials] == ["bench-loops-l", "bench-spiral-s", "bench-loops-l"]
    assert [t.attempt for t in rec.trials] == [1, 1, 2]
    assert {t.end_reason for t in rec.trials} == {EndReason.OPERATOR_DONE}
    assert {t.outcome for t in rec.trials} == {TrialOutcome.COMPLETED}
    assert [t.marks for t in rec.trials] == [(), ("disturbance",), ()]
    rerun = fold(lines("bench_wacom")).trials[0]
    assert rerun.task.id == "bench-line" and rerun.context == BenchRunContext(
        False, 1, "line-osc8", 2, bench.trajectory_sha256("line-osc8"), "W-M3-line-osc8-r2"
    )


def test_tablet_samples_keep_native_tilt_pressure_and_lines() -> None:
    # Detects a tablet sample converted to Pencil angles, scaled by the wrong factor, or named after the Mac.
    rec = fold(lines("bench_wacom"))
    assert (rec.device.model, rec.device.input) == ("Wacom Intuos Pro Medium (2025)", "tablet")
    assert rec.device.planned_channels == {Channel.FORCE, Channel.TILT_X, Channel.TILT_Y, Channel.HOVER}
    context = rec.context
    assert context is not None and context.device_asset == "MAC-01"
    assert context.tablet == TabletContext(
        "Wacom Intuos Pro Medium (2025)", "WACOM-01", "6.4.10-3", "proPen3"
    )
    assert context.scale == TabletScale(200.0, 0.005)
    first = next(s for s in rec.trials[0].strokes if s.state == PenState.CONTACT)
    np.testing.assert_allclose(first.xy[1], [10016 * 0.005, -8000.5 * 0.005])
    assert set(first.channels) == {Channel.FORCE, Channel.TILT_X, Channel.TILT_Y}
    assert first.channels[Channel.FORCE].tolist() == pytest.approx([0.3 + 0.02 * i for i in range(5)])
    assert first.channels[Channel.TILT_X].tolist() == pytest.approx([0.1 + 0.01 * i for i in range(5)])
    assert first.channels[Channel.TILT_Y].tolist() == [-0.2] * 5
    assert first.max_force == 1 and not first.unresolved.any()


def test_hover_without_height_builds_an_in_air_stroke() -> None:
    # Detects a tablet hover line dropped for its absent height, or a hover channel invented for it.
    trial = fold(lines("bench_wacom")).trials[0]
    air = [s for s in trial.strokes if s.state == PenState.IN_AIR]
    assert [len(s.t) for s in air] == [3, 2]
    assert all(Channel.HOVER not in s.channels for s in air)
    assert air[0].t[0] == 1010.8 and air[0].xy[0].tolist() == [9800 * 0.005, -8000 * 0.005]
    ipad = [s for s in fold(lines("multi_trial")).trials[1].strokes if s.state == PenState.IN_AIR]
    assert ipad[0].channels[Channel.HOVER].tolist() == [0.4]


@pytest.mark.parametrize(
    ("fixture", "kind", "key", "donor"),
    [
        ("bench_ipad", "sessionOpened", "participant", "basic"),
        ("bench_ipad", "sessionOpened", "administration", "basic"),
        ("basic", "sessionOpened", "bench", "bench_ipad"),
        ("bench_ipad", "trialStarted", "hand", "basic"),
        ("basic", "trialStarted", "bench", "bench_ipad"),
    ],
)
def test_a_participant_key_on_a_bench_session_is_refused(
    fixture: str, kind: str, key: str, donor: str
) -> None:
    # Detects a bench journal with a participant fact, or a participant journal with a run fact.
    rows = lines(fixture)
    row = next(r for r in rows if r["kind"] == kind)
    row[key] = next(r for r in lines(donor) if r["kind"] == kind)[key]
    with pytest.raises(JournalError, match=f"I1, line {row['seq']}"):
        summarize(rows)
    assert list(SCHEMA.iter_errors(row))


def _set(rows: list[dict[str, Any]], kind: str, keys: tuple[str, ...], value: Any) -> dict[str, Any]:
    """Set the value under `keys` on the first line of `kind`, and return that line."""
    row = next(r for r in rows if r["kind"] == kind)
    target = row
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    return row


@pytest.mark.parametrize(
    ("kind", "keys", "value"),
    [
        ("trialStarted", ("bench", "run"), 0),
        ("trialStarted", ("bench", "run"), 8),
        ("sessionOpened", ("bench", "mounting"), "M9"),
        ("mark", ("code",), "cameraSync"),
    ],
)
def test_a_run_index_mounting_or_mark_outside_the_protocol_is_refused(
    kind: str, keys: tuple[str, ...], value: Any
) -> None:
    # Detects a reader or schema that accepts a run index outside 1 to 7, a mounting outside M1 to M8, or
    # a synchronisation mark, which the protocol does not define.
    rows = lines("bench_ipad")
    row = _set(rows, kind, keys, value)
    with pytest.raises(JournalError, match=f"I1, line {row['seq']}"):
        summarize(rows)
    assert list(SCHEMA.iter_errors(row))


@pytest.mark.parametrize(
    ("kind", "keys", "value"),
    [
        ("sample", ("tiltX",), 1.5),
        ("hover", ("tiltY",), -2.0),
        ("sessionOpened", ("bench", "protocol", "sha256"), "abc"),
        ("sessionOpened", ("bench", "protocol", "version"), ""),
        ("sessionOpened", ("bench", "motion", "id"), ""),
        ("sessionOpened", ("bench", "motion", "version"), ""),
        ("sessionOpened", ("tablet", "model"), ""),
        ("sessionOpened", ("tablet", "driver"), ""),
        ("sessionOpened", ("tablet", "asset"), "WACOM 01"),
        ("trialStarted", ("bench", "program"), "Line-osc8"),
        ("trialStarted", ("bench", "trajectorySHA256"), "xyz"),
        ("trialStarted", ("bench", "clip"), ""),
        ("trialStarted", ("bench", "clip"), "x" * 65),
    ],
)
def test_a_bench_or_tablet_value_that_the_schema_refuses_is_refused(
    kind: str, keys: tuple[str, ...], value: Any
) -> None:
    # Detects a Python reader that accepts a tilt outside -1 to 1, a digest that is not SHA-256 hex, an
    # empty bench or tablet name, an asset that is not a code, a program that is not a program id, or a
    # clip outside 1 to 64 characters, which the schema and the Swift reader refuse.
    rows = lines("bench_wacom")
    row = _set(rows, kind, keys, value)
    with pytest.raises(JournalError, match=f"I1, line {row['seq']}"):
        summarize(rows)
    assert list(SCHEMA.iter_errors(row))


def test_a_tablet_channel_on_a_correction_line_is_not_read() -> None:
    # Detects a reader that refuses a correction line for a tablet key, which the Swift reader ignores, or
    # that copies the key into the corrected samples.
    rows = lines("bench_ipad")
    next(r for r in rows if r["kind"] == "correction")["tiltX"] = "a"
    assert summarize(rows) == summarize(lines("bench_ipad"))
    assert all(Channel.TILT_X not in s.channels for s in fold(rows).trials[0].strokes)


def test_the_last_rerun_and_the_eighth_mounting_are_accepted() -> None:
    # Detects a reader or schema whose run or mounting range stops short of the protocol's last values.
    rows = lines("bench_ipad")
    head = _set(rows, "sessionOpened", ("bench", "mounting"), "M8")
    run = _set(rows, "trialStarted", ("bench", "run"), 7)
    assert summarize(rows)["state"] == "closed"
    assert not list(SCHEMA.iter_errors(head)) and not list(SCHEMA.iter_errors(run))


@pytest.mark.parametrize("kind", ["handoff", "questionnaireStarted", "safetyShown", "safetyAction"])
def test_a_questionnaire_line_in_a_bench_session_is_refused(kind: str) -> None:
    # Detects a bench journal that can hold a handoff, a questionnaire, or a safety line.
    rows = lines("bench_ipad")
    donor = next(r for r in lines("supervised_minor_safety") if r["kind"] == kind)
    index = next(i for i, r in enumerate(rows) if r["kind"] == "trialEnded") + 1
    rows.insert(index, {**donor, "t": rows[index - 1]["t"]})
    renumber(rows)
    rows[-1]["counts"][kind] += 1
    with pytest.raises(JournalError, match=f"I6, line {index}"):
        summarize(rows)


def test_format_1_headers_read_and_format_3_is_refused(tmp_path: Path) -> None:
    # Detects a 2.0 reader that refuses a 1.0 journal or manifest, or accepts a major it does not know.
    rows = lines()
    rows[0]["format"] = "1.0"
    for key in ("tiltX", "tiltY", "rotation"):
        del rows[0]["channels"][key]
    assert fold(rows).device.planned_channels == {
        Channel.FORCE,
        Channel.ALTITUDE,
        Channel.AZIMUTH,
        Channel.ROLL,
        Channel.HOVER,
    }
    write_export(tmp_path, rows)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest_path.write_text(json.dumps({**manifest, "version": "1.0"}))
    assert read_journal(tmp_path).session_id == rows[0]["sessionID"]
    manifest_path.write_text(json.dumps({**manifest, "version": "3.0"}))
    with pytest.raises(JournalError, match="I5, line 0"):
        read_journal(tmp_path)
    rows[0]["format"] = "3.0"
    with pytest.raises(JournalError, match="I3, line 0"):
        summarize(rows)


def test_bench_summary_has_no_administration() -> None:
    # Detects a bench summary with an administration key, null or not, or a participant summary without one.
    for name in ("bench_ipad", "bench_wacom"):
        summary = summarize(lines(name))
        assert "administration" not in summary
        assert summary == json.loads((FIXTURES / "journal" / f"{name}.fold.json").read_bytes())
    assert summarize(lines())["administration"] == "supervised"


def test_questionnaire_blocks_stay_apart_from_strokes() -> None:
    # Detects responses lost, mixed into pen strokes, or attached to the wrong block.
    rec = fold(lines("supervised_minor_safety"))
    phq9, gad7 = rec.questionnaires
    assert (phq9.instrument.id, gad7.instrument.id) == ("phq9", "gad7")
    assert len(phq9.responses) == 10 and phq9.flagged_items == ("phq9-9",)
    assert next(r for r in gad7.responses if r.item == "gad7-4").value is None
    assert (phq9.outcome, gad7.outcome) == (BlockOutcome.COMPLETED, BlockOutcome.COMPLETED)
    assert phq9.started == 1020.0 and phq9.ended == 1031.0
    assert sum(len(s.t) for t in rec.trials for s in t.strokes) == 6
    interrupted = fold(lines("recovered_in_questionnaire")).questionnaires
    assert [b.outcome for b in interrupted] == [BlockOutcome.INTERRUPTED, BlockOutcome.COMPLETED]
    assert interrupted[0].boot != interrupted[1].boot


@pytest.mark.parametrize("corrected", [True, False])
def test_correction_resolves_every_sample_with_its_pending_key(corrected: bool) -> None:
    # Detects a correction that resolves only the last sample with a repeated pending key.
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-0000000000AA", "P0002").opened()
    b.trial("spiral-trace", 1, 1010.0)
    c = b.contact(1011.0)
    for i in range(2):
        b.sample(c, 1011.0 + i / 240, 300.0 + i, 400.0, est=FORCE, pending=(FORCE, 5))
    b.sample(c, 1011.0 + 2 / 240, 302.0, 400.0)
    if corrected:
        b.correction(c, 5, 1011.05, 0, force=0.5)
    b.lift(1011.1, c)
    b.end(1012.0, "participantDone")
    b.close(1013.0)
    assert summarize(b.lines) == b.summary()
    stroke = fold(b.lines).trials[0].strokes[0]
    assert stroke.unresolved.tolist() == ([0, 0, 0] if corrected else [FORCE, FORCE, 0])
    assert stroke.channels[Channel.FORCE].tolist() == ([0.5, 0.5, 0.31] if corrected else [0.31] * 3)


@pytest.mark.parametrize("payload", [b'{"kind":', b"[]\n", b'{"seq":0,"kind":"alien"}\n', b"{}", b"{}\r\n"])
def test_i1_rejects_invalid_lines(payload: bytes) -> None:
    # Detects acceptance of invalid JSON, objects, kinds, or LF endings.
    with pytest.raises(JournalError, match="I1, line 0"):
        summarize([payload])


def test_i2_rejects_sequence_error() -> None:
    # Detects a journal with a missing or reordered sequence number.
    rows = lines()
    rows[3]["seq"] = 4
    with pytest.raises(JournalError, match="I2, line 3"):
        summarize(rows)


@pytest.mark.parametrize("change", ["first", "version", "last"])
def test_i3_rejects_invalid_session_bounds(tmp_path: Path, change: str) -> None:
    # Detects an export without supported session boundaries.
    rows = lines()
    if change == "first":
        rows[0] = {"kind": "hover", "seq": 0, "t": 0, "clock": "callback", "x": 0, "y": 0, "z": 0}
    elif change == "version":
        rows[0]["format"] = "3.0"
    else:
        rows.pop()
    write_export(tmp_path, rows)
    with pytest.raises(JournalError, match="I3, line"):
        read_journal(tmp_path)


def test_i4_rejects_wrong_recount() -> None:
    # Detects a closing count that omits a sample.
    rows = lines()
    rows[-1]["counts"]["sample"] -= 1
    with pytest.raises(JournalError, match=f"I4, line {len(rows) - 1}"):
        summarize(rows)


@pytest.mark.parametrize("field", ["bytes", "lines", "sha256", "sessionID", "absent"])
def test_i5_rejects_manifest_mismatch(tmp_path: Path, field: str) -> None:
    # Detects a manifest whose identity, bytes, lines, or digest do not match the journal.
    write_export(tmp_path, lines())
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if field == "absent":
        path.unlink()
    else:
        if field == "sessionID":
            manifest[field] = "wrong"
        else:
            manifest["journal"][field] = "wrong"
        path.write_text(json.dumps(manifest))
    with pytest.raises(JournalError, match="I5, line 0"):
        read_journal(tmp_path)


@pytest.mark.parametrize(
    ("kind", "after"),
    [
        ("response", "trialEnded"),
        ("response", "questionnaireEnded"),
        ("trialBegan", "trialEnded"),
        ("safetyFlagged", "questionnaireEnded"),
        ("trialEnded", "questionnaireStarted"),
    ],
)
def test_i6_rejects_lines_outside_their_trial_or_block(kind: str, after: str) -> None:
    # Detects a response, Begin tap, or safety flag outside its trial or block, or a block inside a trial.
    rows = move(lines("supervised_minor_safety"), kind, after)
    with pytest.raises(JournalError, match="I6, line"):
        summarize(rows)


def test_i6_rejects_a_second_begin_and_a_begin_for_another_trial() -> None:
    # Detects a trialBegan that repeats or names a trial that is not the open one.
    for change in ("repeat", "other"):
        rows = lines()
        index = next(i for i, r in enumerate(rows) if r["kind"] == "trialBegan")
        if change == "repeat":
            rows.insert(index + 1, copy.deepcopy(rows[index]))
            rows[-1]["counts"]["trialBegan"] += 1
        else:
            rows[index]["trial"] = 7
        with pytest.raises(JournalError, match="I6, line"):
            summarize(renumber(rows))


def test_i6_rejects_recovery_that_names_the_wrong_block() -> None:
    # Detects a recovered line whose interrupted block is not the block that was open.
    rows = lines("recovered_in_questionnaire")
    next(r for r in rows if r["kind"] == "recovered")["interruptedBlock"] = 3
    with pytest.raises(JournalError, match="I6, line"):
        summarize(rows)


@pytest.mark.parametrize("change", ["sample", "end", "overlap", "contact", "close"])
def test_i6_rejects_invalid_nesting(change: str) -> None:
    # Detects samples, contacts, or trial boundaries outside their parent lifecycle.
    rows = lines()
    if change in ("sample", "end"):
        row = next(r for r in rows if r["kind"] == ("sample" if change == "sample" else "contactEnded"))
        row["contact"] = 999
    elif change == "overlap":
        rows[3] = {**copy.deepcopy(rows[1]), "seq": 3, "t": rows[3]["t"]}
    elif change == "contact":
        rows[1] = {**copy.deepcopy(rows[2]), "seq": 1, "t": rows[1]["t"]}
    else:
        rows.pop(-2)
        rows[-1]["seq"] -= 1
    with pytest.raises(JournalError, match="I6, line"):
        summarize(rows)


def test_i7_rejects_clock_reset_without_new_boot() -> None:
    # Detects a recovered uptime reset incorrectly assigned to the previous boot.
    rows = lines("recovered_interrupted")
    next(r for r in rows if r["kind"] == "recovered")["boot"] = rows[0]["boot"]
    with pytest.raises(JournalError, match="I7, line"):
        summarize(rows)


def test_recovery_never_compares_two_boot_clocks() -> None:
    # Detects comparison of a post-reboot uptime with an earlier boot uptime.
    rec = fold(lines("recovered_interrupted"))
    assert rec.trials[0].boot != rec.trials[1].boot
    assert rec.trials[0].ended is None
    assert rec.trials[1].ended == 17


def test_corrections_join_original_contact_and_keep_last_values() -> None:
    # Detects a correction applied to the current trial instead of its source contact.
    rows = lines("multi_trial")
    recording = fold(rows)
    spiral, loops = recording.trials[1:3]
    s = spiral.strokes[0]
    assert s.channels[Channel.FORCE][0] == 0.37
    assert s.channels[Channel.ALTITUDE][0] == 1.1
    assert s.unresolved.tolist() == [0, 1, 0]
    assert spiral.finger_contacts == 0
    assert loops.finger_contacts == 1
    assert spiral.strokes[1].state == "in_air"


def test_location_correction_uses_origin_and_scale() -> None:
    # Detects a location correction left in capture coordinates.
    rows = lines()
    correction = next(r for r in rows if r["kind"] == "correction")
    correction.update(x=147.5, y=200)
    np.testing.assert_array_equal(fold(rows).trials[0].strokes[0].xy[0], [0, 0])


def test_unknown_scale_keeps_relative_source_coordinates() -> None:
    # Detects invented millimetres when the scale is unknown.
    rows = lines()
    rows[0]["scale"] = {"kind": "unknown"}
    rec = fold(rows)
    assert rec.device.spatial_unit == "source"
    np.testing.assert_array_equal(rec.trials[0].strokes[0].xy[0], [152.5, -200])


def contract_out() -> Path:
    configured = os.environ.get("SCRIPT_CONTRACT_OUT")
    if configured is None:
        pytest.skip("SCRIPT_CONTRACT_OUT is not set.")
    return Path(configured)


def swift_summary(folder: Path) -> Any:
    """The Swift summary of an export folder, written beside it as `<export name>.summary.json`."""
    return json.loads((contract_out() / f"{folder.name}.summary.json").read_bytes())


def test_swift_exports_match_schema_and_summary() -> None:
    # Detects empty Swift output or disagreement with a Swift-produced summary.
    root = contract_out()
    paths = sorted(p for p in root.glob("*/journal.jsonl") if not p.parent.name.startswith("._"))
    assert paths, "SCRIPT_CONTRACT_OUT is set but has no exports."
    schema = Draft202012Validator(json.loads((CONTRACT / "journal.schema.json").read_bytes()))
    for path in paths:
        raw = path.read_bytes().splitlines(keepends=True)
        for line in raw:
            schema.validate(json.loads(line))
        read_journal(path.parent)
        assert summarize(raw) == swift_summary(path.parent)


def test_swift_encrypted_exports_open_to_the_plaintext_export() -> None:
    # Detects a Swift encrypted export that does not open with the study key or differs from its plaintext.
    root = contract_out() / "encrypted"
    manifests = sorted(p for p in root.glob("*/manifest.json") if not p.parent.name.startswith("._"))
    assert manifests, "SCRIPT_CONTRACT_OUT is set but has no encrypted exports."
    key = base64.b64decode((root / "private_key.txt").read_text().strip(), validate=True)
    for manifest in manifests:
        folder = manifest.parent
        read_journal(folder, private_key=key)
        _, data = load_export(folder, key)
        assert data == (contract_out() / folder.name / "journal.jsonl").read_bytes()
        assert summarize(data.splitlines(keepends=True)) == swift_summary(folder)


def test_delayed_sample_is_not_compared_with_callback_clock() -> None:
    # Detects a coalesced sample rejected because an earlier callback arrived later than its timestamp.
    rows = lines()
    index = next(i for i, row in enumerate(rows) if row["kind"] == "sample") + 1
    rows.insert(index, {"kind": "correction", "contact": 999, "update": 5, "arrived": 1011.02, "expect": 0})
    for i, row in enumerate(rows):
        row["seq"] = i
    rows[-1]["counts"]["correction"] += 1
    assert summarize(rows)["unmatchedCorrections"] == 1


@pytest.mark.parametrize(
    "check",
    [test_swift_exports_match_schema_and_summary, test_swift_encrypted_exports_open_to_the_plaintext_export],
)
def test_swift_export_checks_fail_for_empty_configured_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, check: Any
) -> None:
    # Detects a Swift export check that silently skips an empty configured folder.
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    (tmp_path / "encrypted").mkdir()
    with pytest.raises(AssertionError, match="has no"):
        check()


def test_export_check_runs_schema_reader_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects a configured export check that does not compare the independent summary.
    write_export(tmp_path / "SCRIPT_DEMO_DEMO_3f2a9c1e", lines())
    oracle = json.loads((FIXTURES / "journal" / "basic.fold.json").read_bytes())
    summary = tmp_path / "SCRIPT_DEMO_DEMO_3f2a9c1e.summary.json"
    summary.write_text(json.dumps(oracle))
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    test_swift_exports_match_schema_and_summary()
    oracle["unmatchedCorrections"] = 99
    summary.write_text(json.dumps(oracle))
    with pytest.raises(AssertionError):
        test_swift_exports_match_schema_and_summary()


def test_encrypted_export_check_decrypts_and_compares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a configured encrypted-export check that does not compare the plaintext with its export.
    rows = lines()
    data = write_export(tmp_path / "SCRIPT_DEMO_DEMO_3f2a9c1e", rows)
    oracle = json.loads((FIXTURES / "journal" / "basic.fold.json").read_bytes())
    (tmp_path / "SCRIPT_DEMO_DEMO_3f2a9c1e.summary.json").write_text(json.dumps(oracle))
    key = bytes(range(32))
    write_encrypted_export(
        tmp_path / "encrypted" / "SCRIPT_DEMO_DEMO_3f2a9c1e", data, rows[0]["sessionID"], key
    )
    (tmp_path / "encrypted" / "private_key.txt").write_text(base64.b64encode(key).decode() + "\n")
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    test_swift_encrypted_exports_open_to_the_plaintext_export()
    rows[3]["t"] += 0.001
    write_export(tmp_path / "SCRIPT_DEMO_DEMO_3f2a9c1e", rows)
    with pytest.raises(AssertionError):
        test_swift_encrypted_exports_open_to_the_plaintext_export()


def test_fold_records_source_hash() -> None:
    # Detects a fold that loses the digest needed for feature provenance.
    import hashlib

    data = (FIXTURES / "journal" / "multi_trial.jsonl").read_bytes()
    assert fold(data.splitlines(keepends=True)).source_hashes == (hashlib.sha256(data).hexdigest(),)


def test_hover_stroke_survives_interleaved_correction() -> None:
    # Detects a hover stroke split only because a correction arrived between hover samples.
    rows = lines("multi_trial")
    index = next(i for i, r in enumerate(rows) if r["kind"] == "hover")
    hover = copy.deepcopy(rows[index])
    hover["t"] += 0.01
    rows[index + 1 : index + 1] = [
        {"kind": "correction", "update": 123, "arrived": 1011.055, "expect": 0},
        hover,
    ]
    for i, row in enumerate(rows):
        row["seq"] = i
    rows[-1]["counts"]["correction"] += 1
    rows[-1]["counts"]["hover"] += 1
    air = [s for s in fold(rows).trials[1].strokes if s.state == "in_air"]
    assert len(air) == 1 and len(air[0].t) == 2
