from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES, fixture_lines, write_encrypted_export, write_export

from scriptkin.contract import encode_line, manifest_bytes
from scriptkin.formats.export import read_journal, verify_export
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


def damaged_journal() -> tuple[bytes, int]:
    """A self-administered journal with an unreached-item flag and, after it, a line that does not decode."""
    lines = (FIXTURES / "journal" / "declined_unreached_safety.jsonl").read_bytes().splitlines(keepends=True)
    damaged_line = len(lines) - 3
    return b"".join([*lines[:damaged_line], b"{not json\n", *lines[damaged_line:]]), damaged_line


def write_damaged_package(folder: Path, data: bytes, line: int, private_key: bytes | None) -> None:
    """Write a package as the app's Export damaged journal does: the bytes as found and a `damaged` member."""
    session = json.loads(data.splitlines()[0])["sessionID"]
    if private_key is None:
        folder.mkdir(parents=True)
        (folder / "journal.jsonl").write_bytes(data)
        (folder / "manifest.json").write_bytes(manifest_bytes(session, data))
    else:
        write_encrypted_export(folder, data, session, private_key)
    manifest = json.loads((folder / "manifest.json").read_bytes())
    manifest = {
        **{k: manifest[k] for k in ("format", "version", "sessionID")},
        "damaged": {"line": line},
        **manifest,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")


@pytest.mark.parametrize("encrypted", [False, True])
def test_a_damaged_package_is_filed_for_review_and_never_as_a_session(
    tmp_path: Path, encrypted: bool
) -> None:
    # Detects a damaged journal that intake quarantines, refuses, or stores as a session, or whose flags a
    # reviewer cannot read from the store.
    data, line = damaged_journal()
    session = json.loads(data.splitlines()[0])["sessionID"]
    write_damaged_package(tmp_path / "in" / "a", data, line, KEY if encrypted else None)
    report = intake([tmp_path / "in"], tmp_path / "store", private_key=KEY)
    [receipt] = report.receipts
    assert (receipt.result, receipt.session_id, receipt.encrypted) == ("damaged", session, encrypted)
    assert receipt.reason == f"Damage at line {line}."
    assert receipt.journal_sha256 == hashlib.sha256(data).hexdigest()
    assert names(tmp_path / "store") == ["damaged", "intake.jsonl", "receipts"]
    [filed] = [tmp_path / "store" / "damaged" / name for name in names(tmp_path / "store" / "damaged")]
    assert filed.name == receipt.export_sha256[:16]
    assert (filed / "journal.jsonl").read_bytes() == data
    assert names(filed) == sorted(
        ["manifest.json", "journal.jsonl", *(["journal.jsonl.hpke"] if encrypted else [])]
    )
    readable = []
    for raw in (filed / "journal.jsonl").read_bytes().splitlines():
        try:
            readable.append(json.loads(raw))
        except ValueError:
            continue
    assert [(r["item"], r["skipped"]) for r in readable if r["kind"] == "safetyFlagged"] == [("phq9-9", True)]
    assert [
        r["result"] for r in json.loads((tmp_path / "store" / "receipts" / f"{session}.json").read_text())
    ] == ["damaged"]
    before = snapshot(tmp_path / "store")
    assert intake([tmp_path / "in"], tmp_path / "store", private_key=KEY).receipts == ()
    assert snapshot(tmp_path / "store") == before


def test_the_app_damaged_package_is_filed_for_review(tmp_path: Path) -> None:
    # Detects a Swift package of a damaged journal that intake cannot decrypt, match, or file for review.
    configured = os.environ.get("SCRIPT_CONTRACT_OUT")
    if configured is None:
        pytest.skip("SCRIPT_CONTRACT_OUT is not set.")
    folder = Path(configured) / "damaged"
    assert folder.is_dir(), f"{folder} is absent: the ScriptCore tests did not write the damaged package."
    key = base64.b64decode((folder / "private_key.txt").read_text())
    store = tmp_path / "store"
    [receipt] = intake([folder], store, private_key=key).receipts
    expected = (FIXTURES / "recovery" / "damaged_interior.jsonl").read_bytes()
    assert (receipt.result, receipt.encrypted, receipt.reason) == ("damaged", True, "Damage at line 3.")
    assert (store / "damaged" / receipt.export_sha256[:16] / "journal.jsonl").read_bytes() == expected


@pytest.mark.parametrize("encryption", ["x", {}])
def test_malformed_encryption_is_quarantined(tmp_path: Path, encryption: Any) -> None:
    # Detects malformed encryption metadata that stops intake before the next export.
    folder = tmp_path / "in" / "a"
    write_export(folder, fixture_lines())
    path = folder / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["encryption"] = encryption
    path.write_text(json.dumps(manifest))
    write_export(tmp_path / "in" / "b", fixture_lines())
    report = intake([tmp_path / "in"], tmp_path / "store")
    assert [r.result for r in report.receipts] == ["quarantined", "accepted"]
    assert report.receipts[0].reason is not None
    assert report.receipts[0].reason.startswith("I5")


def test_unexpected_verification_error_is_contained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Detects an unexpected verification exception that stops the next export or exposes its message.
    import scriptkin.intake as module

    for name in ("a", "b"):
        write_export(tmp_path / "in" / name, fixture_lines())
    calls = 0

    def verify(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("PRIVATE-CELL")
        return verify_export(*args, **kwargs)

    monkeypatch.setattr(module, "verify_export", verify)
    report = intake([tmp_path / "in"], tmp_path / "store")
    assert [r.result for r in report.receipts] == ["quarantined", "accepted"]
    assert report.receipts[0].reason == "RuntimeError"


def test_session_case_conflict_keeps_both_copies(tmp_path: Path) -> None:
    # Detects session identifiers with different letter case that replace an accepted journal.
    rows = fixture_lines()
    write_export(tmp_path / "in" / "a", rows)
    rows[0]["sessionID"] = rows[0]["sessionID"].lower()
    rows[0]["participant"] = "P9999"
    write_export(tmp_path / "in" / "b", rows)
    report = intake([tmp_path / "in"], tmp_path / "store")
    assert [r.result for r in report.receipts] == ["accepted", "conflict"]
    assert len(list((tmp_path / "store" / "quarantine").glob("*/journal.jsonl"))) == 2


def test_unlogged_session_folder_is_preserved(tmp_path: Path) -> None:
    # Detects replacement of a session folder that has no intake log entry.
    rows = fixture_lines()
    store = tmp_path / "store"
    write_export(store / "sessions" / rows[0]["sessionID"], rows)
    rows[0]["participant"] = "P9999"
    write_export(tmp_path / "in", rows)
    assert intake([tmp_path / "in"], store).receipts[0].result == "conflict"
    assert len(list((store / "quarantine").glob("*/journal.jsonl"))) == 2


@pytest.mark.parametrize("manifest", [b"{", b'{"sessionID":"FFFFFFFF-0000-4000-8000-000000000000"}'])
def test_store_copies_finds_journal_identifier(tmp_path: Path, manifest: bytes) -> None:
    # Detects a quarantine copy that withdrawal cannot find by its journal identifier.
    from scriptkin.intake import store_copies

    rows = fixture_lines()
    folder = tmp_path / "in"
    write_export(folder, rows)
    (folder / "manifest.json").write_bytes(manifest)
    store = tmp_path / "store"
    intake([folder], store)
    copies = store_copies(store, [rows[0]["sessionID"].lower(), "FFFFFFFF-0000-4000-8000-000000000000"])
    assert len(copies[rows[0]["sessionID"]]) == 1
    if manifest != b"{":
        assert copies[rows[0]["sessionID"]] == copies["FFFFFFFF-0000-4000-8000-000000000000"]
    assert copies["unidentified"] == []


def test_store_copies_lists_unidentified_copy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Detects an unreadable quarantine copy omitted from the withdrawal copy list.
    from scriptkin.cli import main
    from scriptkin.intake import store_copies

    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "manifest.json").write_bytes(b"{")
    (folder / "journal.jsonl.hpke").write_bytes(b"unreadable")
    store = tmp_path / "store"
    intake([folder], store)
    [copy] = store_copies(store, [fixture_lines()[0]["sessionID"]])["unidentified"]
    assert json.loads((copy / "reason.json").read_bytes())["sessionID"] is None
    assert main(["store-copies", str(store), fixture_lines()[0]["sessionID"]]) == 0
    assert str(copy) in capsys.readouterr().out


def test_store_copies_includes_accepted_and_damaged_folders(tmp_path: Path) -> None:
    # Detects stored accepted or damaged copies omitted from the withdrawal copy list.
    from scriptkin.intake import store_copies

    rows = fixture_lines()
    session = rows[0]["sessionID"]
    for name in ("a", "b"):
        folder = tmp_path / "in" / name
        write_export(folder, rows)
        if name == "b":
            manifest = json.loads((folder / "manifest.json").read_bytes())
            manifest["damaged"] = {"line": 2}
            (folder / "manifest.json").write_text(json.dumps(manifest))
    store = tmp_path / "store"
    report = intake([tmp_path / "in"], store)
    assert [r.result for r in report.receipts] == ["accepted", "damaged"]
    copies = store_copies(store, [session])
    assert set(copies[session]) == {
        store / "sessions" / session,
        store / "damaged" / report.receipts[1].export_sha256[:16],
    }
    assert copies["unidentified"] == []


def test_encrypted_mismatch_has_receipts_for_both_identifiers(tmp_path: Path) -> None:
    # Detects a decrypted journal copy omitted from withdrawal under its own session identifier.
    from conftest import write_encrypted_export

    from scriptkin.intake import store_copies

    rows = fixture_lines()
    session = rows[0]["sessionID"]
    claimed = "FFFFFFFF-0000-4000-8000-000000000000"
    key = bytes(range(32))
    source, store = tmp_path / "in", tmp_path / "store"
    write_encrypted_export(source, b"".join(map(encode_line, rows)), claimed, key)
    assert intake([source], store, private_key=key).receipts[0].result == "quarantined"
    copies = store_copies(store, [session, claimed])
    assert len(copies[session]) == 1
    assert copies[session] == copies[claimed]
