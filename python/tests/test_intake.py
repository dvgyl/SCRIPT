from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import fixture_lines, write_encrypted_export, write_export

from scriptkin.contract import encode_line, manifest_bytes
from scriptkin.formats.export import read_journal
from scriptkin.intake import intake

KEY = bytes(range(1, 33))
SESSION = fixture_lines()[0]["sessionID"]


def snapshot(root: Path) -> dict[str, str]:
    """The SHA-256 of every file under `root`, by relative path. Folders map to an empty string."""
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""
        for p in sorted(root.rglob("*"))
        if not p.name.startswith("._")
    }


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if not p.name.startswith("._"))


def changed_sample(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same session with one sample moved, so the journal stays valid and differs."""
    rows = [dict(r) for r in rows]
    sample = next(r for r in rows if r["kind"] == "sample")
    sample["x"] += 1.0
    return rows


def receipts(store: Path) -> list[dict[str, Any]]:
    return json.loads((store / "receipts" / f"{SESSION}.json").read_text())


def test_intake_copies_a_verified_export_with_its_manifest_and_receipt(tmp_path: Path) -> None:
    # Detects an intake that changes the exported bytes or records a wrong receipt for a good export.
    data = write_export(tmp_path / "in" / "a", fixture_lines())
    report = intake([tmp_path / "in"], tmp_path / "store")
    stored = tmp_path / "store" / "sessions" / SESSION
    assert names(stored) == ["journal.jsonl", "manifest.json"]
    assert (stored / "journal.jsonl").read_bytes() == data
    assert (stored / "manifest.json").read_bytes() == (tmp_path / "in" / "a" / "manifest.json").read_bytes()
    assert read_journal(stored).session_id == SESSION
    [receipt] = receipts(tmp_path / "store")
    assert receipt["source"] == str((tmp_path / "in" / "a").resolve())
    assert receipt["journalSHA256"] == hashlib.sha256(data).hexdigest()
    assert (receipt["encrypted"], receipt["keyID"], receipt["result"]) == (False, None, "accepted")
    assert receipt["at"].endswith("Z")
    log = (tmp_path / "store" / "intake.jsonl").read_bytes().splitlines()
    assert [json.loads(line) for line in log] == [receipt] == [r.to_json() for r in report.receipts]


@pytest.mark.parametrize("change", ["journal byte", "closing counts"])
def test_tampered_export_goes_to_quarantine_with_its_reason(tmp_path: Path, change: str) -> None:
    # Detects an intake that accepts an export that fails the manifest check (I5) or the recount (I4).
    folder = tmp_path / "in" / "a"
    if change == "journal byte":
        data = bytearray(write_export(folder, fixture_lines()))
        data[200] ^= 1
        (folder / "journal.jsonl").write_bytes(bytes(data))
        rule = "I5"
    else:
        rows = fixture_lines()
        rows[-1]["counts"]["sample"] += 1
        write_export(folder, rows)
        rule = "I4"
    report = intake([tmp_path / "in"], tmp_path / "store")
    assert [r.result for r in report.receipts] == ["quarantined"]
    assert not (tmp_path / "store" / "sessions").exists()
    [quarantined] = [
        tmp_path / "store" / "quarantine" / name for name in names(tmp_path / "store" / "quarantine")
    ]
    reason = json.loads((quarantined / "reason.json").read_text())
    assert reason["reason"].startswith(f"{rule}, line")
    assert reason["sessionID"] == SESSION
    assert (quarantined / "journal.jsonl").read_bytes() == (folder / "journal.jsonl").read_bytes()
    assert receipts(tmp_path / "store")[0]["reason"] == reason["reason"]


@pytest.mark.parametrize("copy", ["same files", "sealed again"])
def test_a_duplicate_session_copies_nothing(tmp_path: Path, copy: str) -> None:
    # Detects an intake that copies or replaces a session it holds, or calls a re-encrypted copy a conflict.
    data = write_export(tmp_path / "plain", fixture_lines())
    for name in ("a", "b"):
        if copy == "same files":
            write_export(tmp_path / "in" / name, fixture_lines())
        else:
            write_encrypted_export(tmp_path / "in" / name, data, SESSION, KEY)
    first = snapshot(tmp_path / "in" / "a")
    report = intake([tmp_path / "in"], tmp_path / "store", private_key=KEY)
    assert [r.result for r in report.receipts] == ["accepted", "duplicate"]
    stored = tmp_path / "store" / "sessions" / SESSION
    assert snapshot(stored) == first
    assert not (tmp_path / "store" / "quarantine").exists()
    assert [r["result"] for r in receipts(tmp_path / "store")] == ["accepted", "duplicate"]


def test_a_conflict_puts_both_journals_in_quarantine(tmp_path: Path) -> None:
    # Detects an intake that keeps either of two different journals under one session identifier.
    journals = {
        write_export(tmp_path / "in" / "a", fixture_lines()),
        write_export(tmp_path / "in" / "b", changed_sample(fixture_lines())),
    }
    report = intake([tmp_path / "in"], tmp_path / "store")
    assert [r.result for r in report.receipts] == ["accepted", "conflict"]
    assert not (tmp_path / "store" / "sessions" / SESSION).exists()
    quarantined = [
        tmp_path / "store" / "quarantine" / name for name in names(tmp_path / "store" / "quarantine")
    ]
    assert {(q / "journal.jsonl").read_bytes() for q in quarantined} == journals
    for q in quarantined:
        reason = json.loads((q / "reason.json").read_text())
        assert reason["sessionID"] == SESSION and "another journal" in reason["reason"]


def every_case(folder: Path) -> None:
    """A good, a tampered, a duplicate, a conflicting, and an encrypted export."""
    write_export(folder / "good", fixture_lines())
    write_export(folder / "duplicate", fixture_lines())
    write_export(folder / "conflict", changed_sample(fixture_lines()))
    tampered = fixture_lines("multi_trial")
    tampered[-1]["counts"]["sample"] += 1
    write_export(folder / "tampered", tampered)
    rows = fixture_lines("supervised_minor_safety")
    write_encrypted_export(folder / "sealed", b"".join(map(encode_line, rows)), rows[0]["sessionID"], KEY)


def test_a_second_intake_changes_no_byte_in_the_store(tmp_path: Path) -> None:
    # Detects an intake that rewrites a copy, a receipt, a reason, or the log when it runs again.
    every_case(tmp_path / "in")
    first = intake([tmp_path / "in"], tmp_path / "store", private_key=KEY)
    assert sorted(r.result for r in first.receipts) == [
        "accepted",
        "accepted",
        "conflict",
        "duplicate",
        "quarantined",
    ]
    before = snapshot(tmp_path / "store")
    again = intake([tmp_path / "in"], tmp_path / "store", private_key=KEY)
    assert snapshot(tmp_path / "store") == before
    assert again.receipts == ()
    assert len(again.known) == 5


def test_a_torn_log_line_is_dropped_and_its_export_is_taken_again(tmp_path: Path) -> None:
    # Detects an intake that fails on, or keeps, a last log line that a crash left without its end.
    write_export(tmp_path / "in" / "a", fixture_lines())
    intake([tmp_path / "in"], tmp_path / "store")
    log = tmp_path / "store" / "intake.jsonl"
    complete = log.read_bytes()
    write_export(tmp_path / "in" / "b", fixture_lines())
    log.write_bytes(complete + complete[:40])
    report = intake([tmp_path / "in"], tmp_path / "store")
    assert [r.result for r in report.receipts] == ["duplicate"]
    assert log.read_bytes().startswith(complete)
    assert [json.loads(line)["result"] for line in log.read_bytes().splitlines()] == ["accepted", "duplicate"]


@pytest.mark.parametrize("keep_plaintext", [False, True])
def test_an_encrypted_export_stays_encrypted_unless_the_plaintext_is_kept(
    tmp_path: Path, keep_plaintext: bool
) -> None:
    # Detects a decrypted journal in the store without --keep-plaintext, or any new file outside the store.
    rows = fixture_lines("supervised_minor_safety")
    data = b"".join(map(encode_line, rows))
    session = rows[0]["sessionID"]
    write_encrypted_export(tmp_path / "in" / "a", data, session, KEY)
    outside = snapshot(tmp_path)
    report = intake([tmp_path / "in"], tmp_path / "store", private_key=KEY, keep_plaintext=keep_plaintext)
    assert {k: v for k, v in snapshot(tmp_path).items() if not k.startswith("store")} == outside
    [receipt] = report.receipts
    manifest = json.loads((tmp_path / "in" / "a" / "manifest.json").read_text())
    assert (receipt.result, receipt.encrypted) == ("accepted", True)
    assert receipt.key_id == manifest["encryption"]["keyID"]
    assert receipt.journal_sha256 == hashlib.sha256(data).hexdigest()
    stored = tmp_path / "store" / "sessions" / session
    kept = ["journal.jsonl"] if keep_plaintext else []
    assert names(stored) == sorted(["manifest.json", "journal.jsonl.hpke", *kept])
    assert (stored / "journal.jsonl.hpke").read_bytes() == (
        tmp_path / "in" / "a" / "journal.jsonl.hpke"
    ).read_bytes()
    store_files = [p.read_bytes() for p in (tmp_path / "store").rglob("*") if p.is_file()]
    assert sum(content == data for content in store_files) == len(kept)
    assert not any(KEY in content for content in store_files)
    if keep_plaintext:
        assert (stored / "journal.jsonl").read_bytes() == data


@pytest.mark.parametrize("key", [bytes(range(2, 34)), None])
def test_a_key_that_does_not_open_the_export_is_refused(tmp_path: Path, key: bytes | None) -> None:
    # Detects an intake that stores or quarantines an encrypted export because the operator gave a wrong key.
    rows = fixture_lines()
    write_encrypted_export(tmp_path / "in" / "a", b"".join(map(encode_line, rows)), SESSION, KEY)
    report = intake([tmp_path / "in"], tmp_path / "store", private_key=key)
    assert report.receipts == ()
    assert [r.reason.split(":")[0] for r in report.refused] == ["I5, line 0"]
    assert not [k for k, v in snapshot(tmp_path / "store").items() if v]
    retry = intake([tmp_path / "in"], tmp_path / "store", private_key=KEY)
    assert [r.result for r in retry.receipts] == ["accepted"]


def test_intake_does_not_take_its_own_store_as_exports(tmp_path: Path) -> None:
    # Detects an intake that reads the session and quarantine folders of a store inside a source as exports.
    write_export(tmp_path / "in" / "a", fixture_lines())
    (tmp_path / "in" / "b").mkdir()
    (tmp_path / "in" / "b" / "manifest.json").write_bytes(manifest_bytes(SESSION, b""))
    first = intake([tmp_path / "in"], tmp_path / "in" / "store")
    assert [r.result for r in first.receipts] == ["accepted", "quarantined"]
    again = intake([tmp_path / "in"], tmp_path / "in" / "store")
    assert again.receipts == ()
    assert len(again.known) == 2
