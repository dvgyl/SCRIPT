from __future__ import annotations

import json
from pathlib import Path

from conftest import Analysed, fixture_lines, quick_plan, write_encrypted_export, write_export

from scriptkin.cli import main
from scriptkin.contract import encode_line
from scriptkin.evaluate.plan import column, freeze_features, plan_digest
from scriptkin.evaluate.planning import Assumptions, smallest_enrolment
from scriptkin.labels import write_labels_csv


def write_inputs(analysed: Analysed, folder: Path, **changes: object) -> list[str]:
    plan = quick_plan(permutations=10, bootstrap=50, **changes)
    (folder / "plan.json").write_text(json.dumps(plan.to_json()))
    analysed.table.to_csv(folder / "features.csv")
    write_labels_csv(analysed.cohort.labels, folder / "labels.csv")
    return [
        "evaluate",
        "--plan",
        str(folder / "plan.json"),
        "--features",
        str(folder / "features.csv"),
        "--labels",
        str(folder / "labels.csv"),
        "--seed",
        "3",
        "--out",
        str(folder / "report.json"),
    ]


def test_evaluate_command_reports_the_digests_of_its_inputs(signal_cohort: Analysed, tmp_path: Path) -> None:
    # Detects a command that misreads the files or reports digests that differ from its inputs.
    assert main(write_inputs(signal_cohort, tmp_path)) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["kind"] == "screening"
    assert report["plan_digest"] == plan_digest(quick_plan(permutations=10, bootstrap=50))
    assert report["feature_digest"] == freeze_features(signal_cohort.table)
    assert report["cases"] == sum(bool(p.case) for p in signal_cohort.cohort.truth.participants)


def test_evaluate_command_writes_a_refusal(signal_cohort: Analysed, tmp_path: Path) -> None:
    # Detects a refusal that the command hides behind a success code or an empty file.
    args = write_inputs(signal_cohort, tmp_path, features=(column("house", "kinematic.speed_mean"),))
    assert main(args) == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert (report["kind"], report["code"]) == ("refused", "unknown_feature")


def test_plan_size_command_writes_the_smallest_enrolment(tmp_path: Path) -> None:
    # Detects a command that passes the design to the planner wrongly.
    out = tmp_path / "size.json"
    args = [
        "plan-size",
        "--prevalence",
        "0.08",
        "--sensitivity",
        "0.75",
        "--specificity",
        "0.85",
        "--attrition",
        "0.15",
        "--half-width",
        "0.1",
        "--enrolled",
        "500",
        "--out",
        str(out),
    ]
    assert main(args) == 0
    report = json.loads(out.read_text())
    assert report["smallestEnrolment"] == smallest_enrolment(Assumptions(0.08, 0.75, 0.85, 0.15), 0.1)
    assert report["precision"]["enrolled"] == 500


def test_intake_and_qc_report_commands_pass_the_key_and_flag_a_quarantine(tmp_path: Path) -> None:
    # Detects a command that drops --key or --keep-plaintext, or exits 0 after an export went to quarantine.
    rows = fixture_lines("supervised_minor_safety")
    data = b"".join(map(encode_line, rows))
    write_encrypted_export(tmp_path / "in" / "sealed", data, rows[0]["sessionID"], bytes(range(1, 33)))
    key = tmp_path / "study-export-key.bin"
    key.write_bytes(bytes(range(1, 33)))
    store = tmp_path / "store"
    intake = ["intake", str(tmp_path / "in"), "--store", str(store), "--key", str(key)]
    assert main([*intake, "--keep-plaintext", "--out", str(tmp_path / "intake.json")]) == 0
    assert (store / "sessions" / rows[0]["sessionID"] / "journal.jsonl").read_bytes() == data
    out, table = tmp_path / "qc.json", tmp_path / "qc.md"
    assert (
        main(["qc-report", str(store), "--key", str(key), "--out", str(out), "--markdown", str(table)]) == 0
    )
    assert json.loads(out.read_text())["sessions"] == 1
    assert table.read_text().startswith("| Measure | Value |")
    write_export(tmp_path / "in" / "open", fixture_lines("open_session"))
    assert main([*intake, "--out", str(tmp_path / "again.json")]) == 1
    receipts = json.loads((tmp_path / "again.json").read_text())["receipts"]
    assert [r["result"] for r in receipts] == ["quarantined"]
