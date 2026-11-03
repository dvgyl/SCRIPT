from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from scriptkin import Channel
from scriptkin.contract import encode_line, manifest_bytes
from scriptkin.formats.journal import JournalError, fold, read_journal, summarize

CONTRACT = Path(__file__).resolve().parents[2] / "contract"
FIXTURES = CONTRACT / "fixtures"


def lines(name: str = "basic") -> list[dict[str, Any]]:
    return [json.loads(s) for s in (FIXTURES / "journal" / f"{name}.jsonl").read_bytes().splitlines()]


def write_export(folder: Path, rows: list[dict[str, Any]]) -> None:
    folder.mkdir(exist_ok=True)
    data = b"".join(map(encode_line, rows))
    (folder / "journal.jsonl").write_bytes(data)
    (folder / "manifest.json").write_bytes(manifest_bytes(rows[0].get("sessionID", "invalid"), data))


@pytest.mark.parametrize("path", sorted((FIXTURES / "journal").glob("*.jsonl")), ids=lambda p: p.stem)
def test_fold_matches_independent_builder(path: Path) -> None:
    # Detects a Python fold that differs from the shared builder summary.
    assert summarize(path.read_bytes().splitlines(keepends=True)) == json.loads(
        path.with_suffix(".fold.json").read_bytes()
    )
    recording = fold(path.read_bytes().splitlines(keepends=True))
    assert [t.outcome.value for t in recording.trials] == [
        t["outcome"] for t in summarize(lines(path.stem))["trials"]
    ]


def test_export_reader_checks_and_converts() -> None:
    # Detects a reader that omits origin translation, y inversion, or late force correction.
    recording = read_journal(FIXTURES / "export" / "P0001_3f2a9c1e")
    trial = recording.trials[0]
    first = trial.strokes[0]
    scale = lines()[0]["scale"]["mmPerPoint"]
    np.testing.assert_allclose(first.xy[0], [(300 - 147.5) * scale, -(400 - 200) * scale])
    assert first.channels[Channel.FORCE][0] == 0.42
    assert not first.unresolved.any()
    assert trial.task.paths[0][0, 1] < 0
    assert len(recording.source_hashes[0]) == 64


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
        rows[0]["format"] = "2.0"
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
    s = recording.trials[0].strokes[0]
    assert s.channels[Channel.FORCE][0] == 0.37
    assert s.channels[Channel.ALTITUDE][0] == 1.1
    assert s.unresolved.tolist() == [0, 1, 0]
    assert recording.trials[0].finger_contacts == 0
    assert recording.trials[1].finger_contacts == 1
    assert recording.trials[0].strokes[1].state == "in_air"


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


def test_swift_exports_match_schema_and_summary() -> None:
    # Detects empty Swift output or disagreement with a Swift-produced summary.
    configured = os.environ.get("SCRIPT_CONTRACT_OUT")
    if configured is None:
        pytest.skip("SCRIPT_CONTRACT_OUT is not set.")
    paths = sorted(Path(configured).rglob("journal.jsonl"))
    assert paths, "SCRIPT_CONTRACT_OUT is set but has no exports."
    schema = Draft202012Validator(json.loads((CONTRACT / "journal.schema.json").read_bytes()))
    for path in paths:
        raw = path.read_bytes().splitlines(keepends=True)
        for line in raw:
            schema.validate(json.loads(line))
        read_journal(path.parent)
        assert summarize(raw) == json.loads((path.parent / "summary.json").read_bytes())


def test_delayed_sample_is_not_compared_with_callback_clock() -> None:
    # Detects a coalesced sample rejected because an earlier callback arrived later than its timestamp.
    rows = lines()
    index = next(i for i, row in enumerate(rows) if row["kind"] == "sample") + 1
    rows.insert(index, {"kind": "correction", "contact": 999, "update": 5, "arrived": 1011.02, "expect": 0})
    for i, row in enumerate(rows):
        row["seq"] = i
    rows[-1]["counts"]["correction"] += 1
    assert summarize(rows)["unmatchedCorrections"] == 1


def test_swift_export_check_fails_for_empty_configured_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a Swift export check that silently skips an empty configured folder.
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    with pytest.raises(AssertionError, match="has no exports"):
        test_swift_exports_match_schema_and_summary()


def test_export_check_runs_schema_reader_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects a configured export check that does not compare the independent summary.
    write_export(tmp_path, lines())
    oracle = json.loads((FIXTURES / "journal" / "basic.fold.json").read_bytes())
    (tmp_path / "summary.json").write_text(json.dumps(oracle))
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    test_swift_exports_match_schema_and_summary()
    oracle["unmatchedCorrections"] = 99
    (tmp_path / "summary.json").write_text(json.dumps(oracle))
    with pytest.raises(AssertionError):
        test_swift_exports_match_schema_and_summary()


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
    air = [s for s in fold(rows).trials[0].strokes if s.state == "in_air"]
    assert len(air) == 1 and len(air[0].t) == 2
