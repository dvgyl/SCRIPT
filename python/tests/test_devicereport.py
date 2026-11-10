from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import fixture_lines, write_export

from scriptkin.cli import main
from scriptkin.contract import TABLET, TABLET_SCALE, JournalBuilder
from scriptkin.devicereport import Check, DeviceReport, device_report, json_form, markdown


def check(report: DeviceReport, ident: str) -> list[Check]:
    return [c for c in report.checks if c.id == ident]


def pencil(tmp_path: Path, *, missing_roll: bool = False) -> list[dict[str, Any]]:
    b = JournalBuilder("12345678-0000-4000-8000-000000000001", "PRIVATE-TEST").opened()
    b.trial("qual-line", 1, 1001)
    b.began(1002)
    c = b.contact(1003)
    for i in range(100):
        b.sample(c, 1003 + i / 240, 200 + i, 300)
    b.lift(1004, c)
    b.end(1005, "participantDone")
    b.close(1006)
    b.lines[0]["app"]["sourceRevision"] = "abc123"
    if missing_roll:
        del next(r for r in b.lines if r["kind"] == "sample")["roll"]
    write_export(tmp_path / "export", b.lines)
    return b.lines


def test_pencil_channels_and_private_fields(tmp_path: Path) -> None:
    # Detects private identifiers in the aggregate report.
    rows = pencil(tmp_path)
    report = device_report([tmp_path])
    assert check(report, "P2")[0].result == "PASS"
    text = markdown(report) + json.dumps(json_form(report))
    assert rows[0]["participant"] not in text
    assert rows[0]["sessionID"] not in text


def test_missing_roll_counts(tmp_path: Path) -> None:
    # Detects incomplete roll coverage reported as complete.
    pencil(tmp_path, missing_roll=True)
    row = check(device_report([tmp_path]), "P2")[0]
    assert row.result == "FAIL"
    assert "roll: 99 of 100 samples" in row.value


def test_simulator_cannot_pass_device_checks(tmp_path: Path) -> None:
    # Detects simulator input used as evidence for a device check.
    rows = fixture_lines("simulated_input")
    rows[0]["input"] = "directTouchAsPen"
    write_export(tmp_path / "export", rows)
    report = device_report([tmp_path])
    assert all(c.result == "NOT RUN" for c in report.checks if c.id.startswith("P"))
    assert "simulator" in markdown(report)


def test_hover_height_outside_range(tmp_path: Path) -> None:
    # Detects an out-of-range hover height that does not fail the report.
    rows = fixture_lines("multi_trial")
    next(r for r in rows if r["kind"] == "hover")["z"] = 1.5
    write_export(tmp_path / "export", rows)
    report = device_report([tmp_path])
    assert check(report, "P3")[0].result == "FAIL"


def tablet(tmp_path: Path, dy: int = 1, length: int = 100) -> DeviceReport:
    b = JournalBuilder(
        "12345678-0000-4000-8000-000000000002",
        "QUAL",
        purpose="qualification",
        input="tablet",
        tablet=dict(TABLET),
        scale=dict(TABLET_SCALE),
        view=(52600, 29600),
        channels={
            "force": True,
            "altitude": False,
            "azimuth": False,
            "roll": False,
            "hover": True,
            "tiltX": True,
            "tiltY": True,
            "rotation": False,
        },
        max_force=1,
    ).opened()
    b.trial("qual-reference", 1, 1001)
    b.began(1002)
    for j in range(2):
        c = b.contact(1003 + j)
        for i in range(20):
            distance = length * 200 * i / 19
            b.sample(
                c, 1003 + j + i / 240, 1000 + (distance if j == 0 else 0), 25000 + (dy * distance if j else 0)
            )
        b.lift(1003.2 + j, c)
    b.end(1006, "participantDone")
    b.close(1007)
    write_export(tmp_path / "export", b.lines)
    return device_report([tmp_path])


def test_tablet_reference_strokes_pass(tmp_path: Path) -> None:
    # Detects correct tablet reference strokes reported as a failure.
    assert check(tablet(tmp_path), "W3")[0].result == "PASS"


def test_tablet_reversed_y_hint(tmp_path: Path) -> None:
    # Detects reversed tablet y without the correct table-setting instruction.
    row = check(tablet(tmp_path, dy=-1), "W3")[0]
    assert row.result == "FAIL"
    assert "absoluteY grows downwards; set yDown" in row.value


def test_tablet_wrong_scale_fails(tmp_path: Path) -> None:
    # Detects a 90 mm reference stroke accepted as 100 mm.
    assert check(tablet(tmp_path, length=90), "W3")[0].result == "FAIL"


def test_fault_exclude_fails(tmp_path: Path) -> None:
    # Detects an excluded fault run accepted by the device report.
    rows = fixture_lines("bench_ipad")
    start = next(r for r in rows if r["kind"] == "trialStarted")
    from scriptkin import bench

    start["bench"].update(
        program="fault-lift",
        trajectorySHA256=bench.trajectory_sha256("fault-lift"),
        clip="P-M1-fault-lift-r1",
    )
    # The task specification must name the fault program's task.
    from scriptkin import battery

    task = bench.program("fault-lift").task
    start["trial"]["task"] = task
    start["spec"] = battery.task_spec(task)
    mark = next((r for r in rows if r["kind"] == "mark"), None)
    if mark is None:
        end = next(i for i, r in enumerate(rows) if r["kind"] == "trialEnded")
        rows.insert(
            end, {"kind": "mark", "t": rows[end]["t"], "trial": start["trial"]["index"], "code": "exclude"}
        )
        rows[-1]["counts"]["mark"] += 1
    else:
        mark.update(trial=start["trial"]["index"], code="exclude")
    for i, row in enumerate(rows):
        row["seq"] = i
    write_export(tmp_path / "export", rows)
    report = device_report([tmp_path])
    assert check(report, "P10")[0].result == "FAIL"


def test_diagnostics_missing_protection(tmp_path: Path) -> None:
    # Detects an existing file without a protection class reported as protected.
    pencil(tmp_path)
    d = {
        "format": "script.diagnostics",
        "version": "1.0",
        "storage": {"items": [{"name": "Sessions", "exists": True, "excludedFromBackup": True}]},
        "deviceSecret": {},
        "app": {"sourceRevision": "abc123"},
        "device": {"model": "test", "os": "test", "nativeScale": 2, "scale": {"inDeviceTable": True}},
        "display": {"widthPoints": 834, "heightPoints": 1210, "maximumFramesPerSecond": 120},
    }
    path = tmp_path / "diagnostics.json"
    path.write_text(json.dumps(d))
    report = device_report([tmp_path], [path])
    assert check(report, "D1")[0].result == "FAIL"
    assert check(report, "D2")[0].result == "PASS"
    assert check(report, "D3")[0].value == "no item yet"


def test_invalid_diagnostics_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Detects an invalid diagnostics format accepted by the command.
    pencil(tmp_path)
    path = tmp_path / "diagnostics.json"
    path.write_text('{"format": "other"}')
    assert main(["device-report", str(tmp_path), "--diagnostics", str(path)]) == 2
    assert "Invalid diagnostics file" in capsys.readouterr().err


def test_cli_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Detects a command failure when all device checks are absent.
    rows = fixture_lines("simulated_input")
    rows[0]["input"] = "directTouchAsPen"
    write_export(tmp_path / "export", rows)
    assert main(["device-report", str(tmp_path)]) == 0
    assert "# SCRIPT device report" in capsys.readouterr().out


def test_cli_failure_and_output_files(tmp_path: Path) -> None:
    # Detects a failed check that does not set exit code 1.
    pencil(tmp_path, missing_roll=True)
    out, md = tmp_path / "report.json", tmp_path / "report.md"
    assert main(["device-report", str(tmp_path), "--out", str(out), "--markdown", str(md)]) == 1
    assert json.loads(out.read_text())["checks"]
    assert "| P2 Pencil channels | FAIL |" in md.read_text()


def test_no_exports_cli(tmp_path: Path) -> None:
    # Detects an empty source accepted by the command.
    assert main(["device-report", str(tmp_path)]) == 2


def test_invalid_export_does_not_stop_next_export(tmp_path: Path) -> None:
    # Detects an invalid export that prevents checks of a valid export.
    pencil(tmp_path)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{")
    report = device_report([tmp_path])
    assert any(c.id.startswith("E") and c.result == "FAIL" for c in report.checks)
    assert check(report, "P2")[0].result == "PASS"


def test_encrypted_export_needs_demo(tmp_path: Path) -> None:
    # Detects a device report that asks for a private key.
    from conftest import write_encrypted_export

    from scriptkin.contract import encode_line

    rows = fixture_lines()
    write_encrypted_export(
        tmp_path / "export", b"".join(map(encode_line, rows)), rows[0]["sessionID"], bytes(range(32))
    )
    report = device_report([tmp_path])
    assert any(c.result == "FAIL" and "demo configuration" in c.value for c in report.checks)


def test_revision_mismatch(tmp_path: Path) -> None:
    # Detects different pencil revisions reported as one revision.
    rows = pencil(tmp_path)
    rows[0]["app"]["sourceRevision"] = "other"
    write_export(tmp_path / "second", rows)
    assert check(device_report([tmp_path]), "P1")[0].result == "FAIL"


def test_empty_trial_is_skipped(tmp_path: Path) -> None:
    # Detects quality control applied to a trial without a contact.
    b = JournalBuilder("12345678-0000-4000-8000-000000000003", "PRIVATE-TEST").opened()
    b.trial("qual-line", 1, 1001)
    b.began(1002)
    b.end(1003, "participantDone")
    b.close(1004)
    write_export(tmp_path / "export", b.lines)
    row = check(device_report([tmp_path]), "P6")[0]
    assert row.result == "RECORD"
    assert "skipped empty trials 1; refusals none" in row.value


def test_measured_trial_refusal(tmp_path: Path) -> None:
    # Detects a measured trial refusal omitted from the device report.
    write_export(tmp_path / "export", fixture_lines())
    row = check(device_report([tmp_path]), "P6")[0]
    assert row.result == "FAIL"
    assert "too_few_values" in row.value
