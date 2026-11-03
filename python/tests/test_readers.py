from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from scriptkin import (
    Channel,
    FormatError,
    PenState,
    SvcReader,
    Tablet,
    feature_table,
    load_parkinsonhw,
    read_parkinsonhw,
)

FIXTURES = Path(__file__).resolve().parents[2] / "contract" / "fixtures"
SYNTHETIC_TABLET = Tablet(
    "synthetic", 100, "ms", "decideg", 1024, {1: PenState.CONTACT, 0: PenState.IN_AIR}, True
)


def test_svc_converts_explicit_units() -> None:
    # Detects an SVC reader that uses implicit position, time, angle, or pressure units.
    rec = SvcReader(SYNTHETIC_TABLET).read(FIXTURES / "svc" / "synthetic.svc")
    contact, air = rec.trials[0].strokes
    np.testing.assert_allclose(contact.xy[0], [1, -2])
    np.testing.assert_allclose(contact.t, [0, 0.01, 0.02, 0.03])
    np.testing.assert_allclose(contact.channels[Channel.AZIMUTH], np.pi / 2)
    np.testing.assert_allclose(contact.channels[Channel.ALTITUDE], np.pi / 4)
    assert contact.channels[Channel.FORCE][0] == 0.5
    assert contact.state == PenState.CONTACT
    assert air.state == PenState.IN_AIR
    assert rec.device.input == "tablet"
    assert rec.device.observed_channels == rec.device.planned_channels


@pytest.mark.parametrize(
    "payload", ["2\n1 2 3 1 5 6 7\n", "1\n1 2 3 1 5 6\n", "1\n1 2 3 9 5 6 7\n", "1\n1 2 3 0.5 5 6 7\n"]
)
def test_svc_rejects_invalid_layout(tmp_path: Path, payload: str) -> None:
    # Detects an SVC row count, column count, or pen status that does not match its profile.
    path = tmp_path / "bad.svc"
    path.write_text(payload)
    with pytest.raises(FormatError):
        SvcReader(SYNTHETIC_TABLET).read(path)


def test_parkinsonhw_groups_tests_and_converts_time() -> None:
    # Detects merged test IDs or a ParkinsonHW reader that treats milliseconds as seconds.
    rec = read_parkinsonhw(FIXTURES / "parkinsonhw" / "synthetic.txt")
    assert [t.index for t in rec.trials] == [0, 1]
    contact, air = rec.trials[0].strokes
    np.testing.assert_allclose(contact.t, [0, 0.01])
    np.testing.assert_allclose(contact.xy[0], [100, -200])
    assert contact.channels[Channel.FORCE][0] == 512
    assert contact.state == PenState.CONTACT
    assert air.state == PenState.IN_AIR
    assert rec.device.spatial_unit == "source"
    assert rec.trials[1].started == 0


def test_parkinsonhw_loader_does_not_return_class_labels(tmp_path: Path) -> None:
    # Detects class folders or class filename prefixes leaking into returned metadata.
    data = (FIXTURES / "parkinsonhw" / "synthetic.txt").read_bytes()
    for label in ("control", "parkinson"):
        folder = tmp_path / label
        folder.mkdir()
        (folder / "C_0001.txt").write_bytes(data)
    records = load_parkinsonhw(tmp_path)
    assert len(records) == 2
    assert records[0].participant == records[1].participant
    assert records[0].session_id == records[1].session_id
    assert records[0].source_hashes == records[1].source_hashes


def test_parkinsonhw_loader_orders_by_file_digest(tmp_path: Path) -> None:
    # Detects a recording order that follows the class folders in the file paths.
    base = (FIXTURES / "parkinsonhw" / "synthetic.txt").read_bytes()
    first, second = sorted(
        (base, base.replace(b"100;200", b"101;200")), key=lambda d: hashlib.sha256(d).digest()
    )
    for label, data in (("control", second), ("parkinson", first)):
        (tmp_path / label).mkdir()
        (tmp_path / label / "C_0001.txt").write_bytes(data)
    records = load_parkinsonhw(tmp_path)
    assert [r.source_hashes[0] for r in records] == [hashlib.sha256(d).hexdigest() for d in (first, second)]


def test_provenance_records_the_svc_reader_profile() -> None:
    # Detects provenance that stays the same when one file is read with a different unit conversion.
    path = FIXTURES / "svc" / "synthetic.svc"
    tables = [
        feature_table([SvcReader(replace(SYNTHETIC_TABLET, lines_per_mm=n)).read(path)]) for n in (100, 40)
    ]
    assert tables[0].provenance.source_hashes == tables[1].provenance.source_hashes
    assert tables[0].provenance != tables[1].provenance
    profiles = [json.loads(p) for table in tables for p in table.provenance.reader_profiles]
    assert [(p["reader"], p["lines_per_mm"]) for p in profiles] == [("svc", 100), ("svc", 40)]


def test_parkinsonhw_rejects_wrong_column_count(tmp_path: Path) -> None:
    # Detects a ParkinsonHW line with an omitted field.
    path = tmp_path / "bad.txt"
    path.write_text("1;2;3;4;5;6\n")
    with pytest.raises(FormatError):
        read_parkinsonhw(path)
