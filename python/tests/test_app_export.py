"""The exports that the SCRIPT Capture UI tests leave in `$SCRIPT_CONTRACT_OUT/app/`.

`./check app` runs the UI tests on the simulator and copies every export out of the app container. These
tests read each export as an analyst does. They skip when no app exports exist, unless
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

from scriptkin.formats.export import read_journal
from scriptkin.labels import labels
from scriptkin.quality import Refusal


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
        assert rows[0]["participant"] not in folder.name, folder.name


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


def test_app_export_checks_need_both_administrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects a configured app export check that passes when the supervised UI test left no export.
    rows = lines("self_administered")
    write_export(tmp_path / "app" / "SCRIPT_DEMO_DEMO_3f2a9c1e", rows)
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    with pytest.raises(AssertionError, match="No supervised study export"):
        test_supervised_export_has_one_flag_alert_action_and_crisis_list()
