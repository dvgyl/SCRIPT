from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXTURES, fixture_lines, write_export

from scriptkin.intake import intake
from scriptkin.qcreport import qc_markdown, qc_report

FIXTURE_NAMES = sorted(p.stem for p in (FIXTURES / "journal").glob("*.jsonl") if not p.name.startswith("._"))


@pytest.fixture(scope="module")
def fixture_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Every journal fixture and the export fixture: 19 sessions, one duplicate, and one open session."""
    root = tmp_path_factory.mktemp("qc")
    for name in FIXTURE_NAMES:
        write_export(root / "in" / name, fixture_lines(name))
    report = intake([root / "in", FIXTURES / "export"], root / "store")
    assert sorted(r.result for r in report.receipts).count("accepted") == 19
    return root / "store"


def test_qc_report_counts_match_a_hand_count_of_the_contract_fixtures(fixture_store: Path) -> None:
    # Detects a QC report that counts a session, trial, sample, correction, or safety event wrongly.
    # The expected values come from the .fold.json oracles and the header lines of the fixtures.
    report = qc_report(fixture_store)
    assert report["sessions"] == 19
    assert report["sessionsBy"] == {
        "site": {"DEMO": 17},
        "deviceAsset": {"IPAD-01": 17, "IPAD-02": 1, "MAC-01": 1},
        "configSHA256": {"f6873edd92a4f4d6d9f2a0f5c50c4df853e0c3af5595e7a4dc50cd7dbbe7b5c8": 17},
        "appVersion": {"1.0.0": 19},
        "administration": {"self": 3, "supervised": 14},
        "purpose": {"bench": 2, "qualification": 1, "study": 16},
    }
    assert report["trials"] == {
        "accepted": 0,
        "refused": 21,
        "refusalCodes": {
            "duplicate_times": 1,
            "incomplete_trial": 4,
            "too_few_values": 21,
            "unresolved_estimates": 3,
        },
    }
    assert report["estimates"] == {
        "samples": 155 + 24 + 13,
        "unresolved": 3,
        "unresolvedRate": 3 / 192,
        "corrections": 8,
        "unmatched": 3,
        "unmatchedRate": 3 / 8,
    }
    assert report["interrupted"] == {"trials": 1, "blocks": 2}
    assert report["recovered"] == {"sessions": 3, "truncatedBytes": 37}
    assert report["safetyAudit"] == {"actions": 2, "crisisListShown": 8, "flags": 4, "operatorAlerts": 2}
    planned = {
        "force": 17,
        "altitude": 17,
        "azimuth": 17,
        "roll": 15,
        "hover": 17,
        "tiltX": 1,
        "tiltY": 1,
        "rotation": 0,
    }
    observed = {**planned, "hover": 3}
    assert report["channels"] == {
        c: {"planned": planned[c], "observed": observed[c], "plannedNotObserved": planned[c] - observed[c]}
        for c in planned
    }
    devices = {(d["deviceAsset"], d["pencil"]): d for d in report["devices"]}
    assert sorted(devices) == [
        ("IPAD-01", "none"),
        ("IPAD-01", "pro"),
        ("IPAD-01", "second"),
        ("IPAD-01", "usbC"),
        ("IPAD-02", "pro"),
        ("WACOM-01", "proPen3"),
    ]
    latest = devices["IPAD-01", "pro"]["latestQualification"]
    assert latest["sessionID"] == "3F2A9C1E-0000-4000-8000-00000000000D"
    assert [k for k, d in devices.items() if d["latestQualification"] is not None] == [("IPAD-01", "pro")]
    assert devices["IPAD-01", "pro"]["sampleRateHz"]["median"] == pytest.approx(240.0)
    assert devices["WACOM-01", "proPen3"]["models"] == ["Wacom Intuos Pro Medium (2025)"]
    assert devices["WACOM-01", "proPen3"]["sampleRateHz"]["median"] == pytest.approx(240.0)


def test_qc_report_holds_no_label_score_or_response(fixture_store: Path) -> None:
    # Detects a QC report or table that shows a questionnaire score, a response value, or a participant.
    report = qc_report(fixture_store)
    text = json.dumps(report) + qc_markdown(report)
    for word in ("score", "label", "phq", "gad", "dass", "response", "participant", "P0001"):
        assert word not in text.lower()
    assert set(report) == {
        "kind",
        "library",
        "sessions",
        "sessionsBy",
        "trials",
        "devices",
        "channels",
        "estimates",
        "interrupted",
        "recovered",
        "safetyAudit",
    }
