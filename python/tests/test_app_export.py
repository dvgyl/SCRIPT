"""The exports that the SCRIPT Capture UI tests leave in `$SCRIPT_CONTRACT_OUT/app/`.

`./check app` runs the UI tests on the simulator and copies every export out of the app container. A UI test
run of the app also writes the Swift fold summary of each export beside it, as `<export name>.summary.json`.
These tests read each export as an analyst does. They skip when no app exports exist, unless
`SCRIPT_REQUIRE_APP_EXPORTS=1`: `./check` sets it when the `app` section ran in the same invocation.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from conftest import CONTRACT, write_export
from conftest import fixture_lines as lines
from jsonschema import Draft202012Validator

from scriptkin import bench
from scriptkin.formats.export import load_export, read_journal
from scriptkin.formats.journal import summarize
from scriptkin.labels import labels
from scriptkin.quality import Refusal
from scriptkin.recording import BenchContext, BenchRunContext, DocumentRef, EndReason


def app_exports() -> list[Path]:
    """The export folders in `$SCRIPT_CONTRACT_OUT/app/`."""
    missing = pytest.fail if os.environ.get("SCRIPT_REQUIRE_APP_EXPORTS") == "1" else pytest.skip
    configured = os.environ.get("SCRIPT_CONTRACT_OUT")
    if configured is None:
        missing("SCRIPT_CONTRACT_OUT is not set.")
    folder = Path(configured) / "app"
    if not folder.is_dir():
        missing(f"No app exports in {folder}: `./check app` has not run.")
    exports = sorted(p.parent for p in folder.glob("*/manifest.json") if not p.parent.name.startswith("._"))
    assert exports, f"{folder} exists but has no exports."
    return exports


def journal_rows(folder: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (folder / "journal.jsonl").read_bytes().splitlines()]


def kinds(rows: list[dict[str, Any]]) -> Counter[str]:
    shown = Counter(f"safetyShown.{r['what']}" for r in rows if r["kind"] == "safetyShown")
    return Counter(r["kind"] for r in rows) + shown


def study_exports(administration: str) -> list[tuple[Path, list[dict[str, Any]]]]:
    found = []
    for folder in app_exports():
        rows = journal_rows(folder)
        if rows[0]["purpose"] == "study" and rows[0]["administration"] == administration:
            found.append((folder, rows))
    assert found, f"No {administration} study export in the app exports."
    return found


def bench_exports() -> list[tuple[Path, list[dict[str, Any]]]]:
    exports = [(folder, journal_rows(folder)) for folder in app_exports()]
    found = [(folder, rows) for folder, rows in exports if rows[0]["purpose"] == "bench"]
    assert found, "No bench export in the app exports."
    return found


def swift_summary(folder: Path) -> Any:
    """The app's Swift fold summary of an export folder."""
    return json.loads((folder.parent / f"{folder.name}.summary.json").read_bytes())


def test_app_exports_read_and_match_the_schema() -> None:
    # Detects an app export that the reader refuses, a line outside the schema, a real-Pencil claim from the
    # simulator, or a participant code in an export name.
    schema = Draft202012Validator(json.loads((CONTRACT / "journal.schema.json").read_bytes()))
    for folder in app_exports():
        read_journal(folder)
        rows = journal_rows(folder)
        for row in rows:
            schema.validate(row)
        assert rows[0]["input"] == "directTouchAsPen", folder.name
        if rows[0]["purpose"] == "bench":
            assert "participant" not in rows[0], folder.name
        else:
            assert rows[0]["participant"] not in folder.name, folder.name


def test_app_exports_fold_to_the_swift_summary() -> None:
    # Detects an app export whose Python fold differs from the Swift fold of the app that wrote it.
    for folder in app_exports():
        _, data = load_export(folder)
        assert summarize(data.splitlines(keepends=True)) == swift_summary(folder), folder.name


def test_bench_export_is_plaintext_and_keeps_every_bench_fact() -> None:
    # Detects a bench export that is encrypted or names a person, or that loses a header fact, a run fact, a
    # clip, a mark, or the operatorDone end of a run.
    for folder, _ in bench_exports():
        manifest, _ = load_export(folder)
        assert manifest["demo"] is True and "encryption" not in manifest, folder.name
        assert folder.name.startswith("SCRIPT_BENCH_PM2_"), folder.name
        recording = read_journal(folder)
        assert recording.participant is None and recording.questionnaires == ()
        context = recording.context
        assert context is not None and context.purpose == "bench" and context.tablet is None
        assert context.subject == BenchContext(
            "P", "M2", DocumentRef("nextdraw-8511", "2.4"), bench.PROTOCOL_VERSION, bench.protocol_sha256()
        )
        assert context.device_asset == "IPAD-01"
        assert [t.context for t in recording.trials] == [
            BenchRunContext(
                False, 1, "loops-l-v20", 1, bench.trajectory_sha256("loops-l-v20"), "P-M2-loops-l-v20-r1"
            ),
            BenchRunContext(
                False, 2, "fault-lift", 1, bench.trajectory_sha256("fault-lift"), "P-M2-fault-lift-r1"
            ),
        ]
        assert [t.marks for t in recording.trials] == [("disturbance",), ()]
        assert {t.end_reason for t in recording.trials} == {EndReason.OPERATOR_DONE}
        assert all(t.strokes for t in recording.trials)


def test_supervised_export_has_one_flag_alert_action_and_crisis_list() -> None:
    # Detects a supervised session that loses the safety flag, skips the operator alert or the action code, or
    # ends without the crisis list.
    for folder, rows in study_exports("supervised"):
        counts = kinds(rows)
        assert counts["safetyFlagged"] == 1, folder.name
        assert counts["safetyShown.operatorAlert"] == 1, folder.name
        assert counts["safetyAction"] == 1, folder.name
        assert counts["safetyShown.crisisList"] == 1, folder.name
        assert rows[-1]["kind"] == "sessionClosed"


def test_self_administered_export_has_no_operator_alert_and_ends_with_the_crisis_list() -> None:
    # Detects an operator alert in a session without an operator, or a self-administered session without the
    # closing crisis list.
    for folder, rows in study_exports("self"):
        counts = kinds(rows)
        assert counts["safetyShown.operatorAlert"] == 0, folder.name
        assert counts["safetyAction"] == 0, folder.name
        last_block_end = max(i for i, r in enumerate(rows) if r["kind"] == "questionnaireEnded")
        assert any(r["kind"] == "safetyShown" and r["what"] == "crisisList" for r in rows[last_block_end:])


def test_labels_score_every_block_of_every_app_export() -> None:
    # Detects an app export whose responses cannot be scored: a missing item, a value outside the options,
    # or a block that labels skips.
    for folder in app_exports():
        recording = read_journal(folder)
        scored = labels(recording)
        assert {label.block for label in scored} == {block.block for block in recording.questionnaires}
        for label in scored:
            assert not isinstance(label.score, Refusal), (folder.name, label.instrument, label.score)


def test_app_export_checks_fail_for_an_empty_app_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects an app export check that passes silently when the UI tests copied nothing.
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    (tmp_path / "app").mkdir()
    with pytest.raises(AssertionError, match="has no exports"):
        test_app_exports_read_and_match_the_schema()


@pytest.mark.parametrize("contract_out", [False, True])
def test_app_export_checks_fail_without_exports_after_check_ran_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contract_out: bool
) -> None:
    # Detects an app export check that skips, and so passes, after `./check` ran the app section.
    def outcome() -> type[BaseException] | None:
        try:
            test_app_exports_read_and_match_the_schema()
        except (pytest.fail.Exception, pytest.skip.Exception) as e:
            return type(e)
        return None

    if contract_out:
        monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    else:
        monkeypatch.delenv("SCRIPT_CONTRACT_OUT", raising=False)
    monkeypatch.setenv("SCRIPT_REQUIRE_APP_EXPORTS", "1")
    assert outcome() is pytest.fail.Exception
    monkeypatch.delenv("SCRIPT_REQUIRE_APP_EXPORTS")
    assert outcome() is pytest.skip.Exception


def test_app_export_checks_need_a_bench_export_and_its_swift_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a configured app export check that passes when the bench UI test left no export, or that does
    # not compare the Python fold with the Swift summary.
    folder = tmp_path / "app" / "SCRIPT_DEMO_DEMO_3f2a9c1e"
    write_export(folder, lines("self_administered"))
    oracle = json.loads((CONTRACT / "fixtures" / "journal" / "self_administered.fold.json").read_bytes())
    summary = folder.parent / f"{folder.name}.summary.json"
    summary.write_text(json.dumps(oracle))
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    with pytest.raises(AssertionError, match="No bench export"):
        test_bench_export_is_plaintext_and_keeps_every_bench_fact()
    test_app_exports_fold_to_the_swift_summary()
    oracle["unmatchedCorrections"] = 99
    summary.write_text(json.dumps(oracle))
    with pytest.raises(AssertionError, match=folder.name):
        test_app_exports_fold_to_the_swift_summary()


def test_app_export_checks_need_both_administrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects a configured app export check that passes when the supervised UI test left no export.
    rows = lines("self_administered")
    write_export(tmp_path / "app" / "SCRIPT_DEMO_DEMO_3f2a9c1e", rows)
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    with pytest.raises(AssertionError, match="No supervised study export"):
        test_supervised_export_has_one_flag_alert_action_and_crisis_list()
