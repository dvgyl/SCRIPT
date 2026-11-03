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
    """Every journal fixture and the export fixture: 15 sessions, one duplicate, and one open session."""
    root = tmp_path_factory.mktemp("qc")
    for name in FIXTURE_NAMES:
        write_export(root / "in" / name, fixture_lines(name))
    report = intake([root / "in", FIXTURES / "export"], root / "store")
    assert sorted(r.result for r in report.receipts).count("accepted") == 15
    return root / "store"


def test_qc_report_counts_match_a_hand_count_of_the_contract_fixtures(fixture_store: Path) -> None:
    # Detects a QC report that counts a session, trial, sample, correction, or safety event wrongly.
    # The expected values come from the .fold.json oracles and the header lines of the fixtures.
    report = qc_report(fixture_store)
    assert report["sessions"] == 15
    assert report["sessionsBy"] == {
        "site": {"DEMO": 15},
        "deviceAsset": {"IPAD-01": 15},
        "configSHA256": {"bf2d2222fac8c0704708bd1666838a02fcf938276df52b148e7e8fc74c18c04f": 15},
        "appVersion": {"1.0.0": 15},
        "administration": {"self": 2, "supervised": 13},
        "purpose": {"qualification": 1, "study": 14},
    }
    assert report["trials"] == {
        "accepted": 0,
        "refused": 19,
        "refusalCodes": {
            "duplicate_times": 1,
            "incomplete_trial": 4,
            "too_few_values": 19,
            "unresolved_estimates": 3,
        },
    }
    assert report["estimates"] == {
        "samples": 143,
        "unresolved": 3,
        "unresolvedRate": 3 / 143,
        "corrections": 7,
        "unmatched": 3,
        "unmatchedRate": 3 / 7,
    }
    assert report["interrupted"] == {"trials": 1, "blocks": 1}
    assert report["recovered"] == {"sessions": 2, "truncatedBytes": 37}
    assert report["safetyAudit"] == {"actions": 1, "crisisListShown": 5, "flags": 2, "operatorAlerts": 1}
    planned = {"force": 13, "altitude": 14, "azimuth": 14, "roll": 12, "hover": 13}
    observed = {**planned, "hover": 2}
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
    ]
    latest = devices["IPAD-01", "pro"]["latestQualification"]
    assert latest["sessionID"] == "3F2A9C1E-0000-4000-8000-00000000000D"
    assert [d["latestQualification"] for k, d in devices.items() if k[1] != "pro"] == [None, None, None]
    assert devices["IPAD-01", "pro"]["sampleRateHz"]["median"] == pytest.approx(240.0)


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
