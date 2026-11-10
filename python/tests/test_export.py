from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from conftest import fixture_lines, write_encrypted_export, write_export

from scriptkin import hpke
from scriptkin.contract import KINDS
from scriptkin.formats.export import pen_only, read_journal, verify_export
from scriptkin.formats.journal import QUESTIONNAIRE_KINDS, JournalError, summarize

# RFC 9180 Appendix A.2.1: DHKEM(X25519, HKDF-SHA256), HKDF-SHA256, ChaCha20Poly1305, base mode.
VECTOR = {
    "info": "4f6465206f6e2061204772656369616e2055726e",
    "skEm": "f4ec9b33b792c372c1d2c2063507b684ef925b8c75a42dbcbf57d63ccd381600",
    "pkRm": "4310ee97d88cc1f088a5576c77ab0cf5c3ac797f3d95139c6c84b5429c59662a",
    "skRm": "8057991eef8f1f1af18f4a9491d16a1ce333f695d4db8e38da75975c4478e0fb",
    "enc": "1afa08d3dec047a643885163f1180476fa7ddb54c6a8029ea33f95796bf2ac4a",
    "pt": "4265617574792069732074727574682c20747275746820626561757479",
    "aad": "436f756e742d30",
    "ct": "1c5250d8034ec2b784ba2cfd69dbdb8af406cfe3ff938e131f0def8c8b60b4db21993c62ce81883d2dd1b51a28",
}
KEY = bytes(range(1, 33))


def vector(name: str) -> bytes:
    return bytes.fromhex(VECTOR[name])


def test_rfc9180_vector_seals_and_opens() -> None:
    # Detects an HPKE KEM, key schedule, nonce, or AEAD step that differs from RFC 9180 A.2.1.
    assert hpke.public_key(vector("skRm")) == vector("pkRm")
    enc, ct = hpke.seal(vector("pkRm"), vector("info"), vector("aad"), vector("pt"), ephemeral=vector("skEm"))
    assert (enc, ct) == (vector("enc"), vector("ct"))
    assert hpke.open_sealed(
        vector("skRm"), vector("enc"), vector("info"), vector("aad"), vector("ct")
    ) == vector("pt")


def encrypted_basic(tmp_path: Path) -> tuple[Path, Path]:
    rows = fixture_lines()
    data = write_export(tmp_path / "plain", rows)
    write_encrypted_export(tmp_path / "sealed", data, rows[0]["sessionID"], KEY)
    return tmp_path / "plain", tmp_path / "sealed"


def test_encrypted_export_reads_like_its_plaintext(tmp_path: Path) -> None:
    # Detects an encrypted export reader that loses, alters, or skips checking the decrypted journal.
    plain, sealed = encrypted_basic(tmp_path)
    expected = read_journal(plain)
    actual = read_journal(sealed, private_key=KEY)
    assert actual.source_hashes == expected.source_hashes
    assert [len(s.t) for t in actual.trials for s in t.strokes] == [
        len(s.t) for t in expected.trials for s in t.strokes
    ]


def reseal_manifest(folder: Path) -> None:
    """Make the manifest agree with a changed ciphertext, so that only the AEAD tag can refuse it."""
    ciphertext = (folder / "journal.jsonl.hpke").read_bytes()
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["encryption"]["ciphertext"] = {
        "bytes": len(ciphertext),
        "sha256": hashlib.sha256(ciphertext).hexdigest(),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))


@pytest.mark.parametrize("change", ["ciphertext", "key", "session", "manifest", "absent key"])
def test_tampered_or_misaddressed_ciphertext_is_refused(tmp_path: Path, change: str) -> None:
    # Detects a reader that accepts changed ciphertext, wrong manifest facts, a wrong key, or no key.
    _, sealed = encrypted_basic(tmp_path)
    key: bytes | None = KEY
    path = sealed / "journal.jsonl.hpke"
    if change == "ciphertext":
        data = bytearray(path.read_bytes())
        data[100] ^= 1
        path.write_bytes(bytes(data))
        reseal_manifest(sealed)
    elif change == "key":
        key = bytes(32)
    elif change == "session":
        session = fixture_lines()[0]["sessionID"]
        data = (tmp_path / "plain" / "journal.jsonl").read_bytes()
        write_encrypted_export(sealed, data, "3F2A9C1E-0000-4000-8000-000000000099", KEY)
        manifest = json.loads((sealed / "manifest.json").read_text())
        manifest["sessionID"] = session
        (sealed / "manifest.json").write_text(json.dumps(manifest))
    elif change == "manifest":
        manifest = json.loads((sealed / "manifest.json").read_text())
        manifest["encryption"]["ciphertext"]["bytes"] += 1
        (sealed / "manifest.json").write_text(json.dumps(manifest))
    else:
        key = None
    with pytest.raises(JournalError, match="I5, line 0"):
        read_journal(sealed, private_key=key)


def test_wrong_private_key_is_refused_by_key_id_before_decryption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a reader that tries to decrypt with a private key whose public key does not give the keyID.
    _, sealed = encrypted_basic(tmp_path)
    manifest = json.loads((sealed / "manifest.json").read_text())
    assert manifest["encryption"]["keyID"] == hashlib.sha256(hpke.public_key(KEY)).hexdigest()

    def opened(*_: object) -> bytes:
        raise AssertionError("The reader tried to decrypt with the wrong key.")

    monkeypatch.setattr(hpke, "open_sealed", opened)
    with pytest.raises(JournalError, match=r"I5, line 0: .*keyID"):
        read_journal(sealed, private_key=bytes(range(2, 34)))


@pytest.mark.parametrize(
    "name", ["supervised_minor_safety", "self_with_remote_review", "recovered_in_questionnaire"]
)
def test_pen_only_copy_passes_integrity_and_has_no_response(tmp_path: Path, name: str) -> None:
    # Detects a pen-only copy with label lines, broken I1 to I7 rules, changed pen data, or no derivation.
    rows = fixture_lines(name)
    data = write_export(tmp_path / "source", rows)
    copy = pen_only(tmp_path / "source", tmp_path / "copy")
    lines = [json.loads(s) for s in (copy / "journal.jsonl").read_bytes().splitlines()]
    assert not {r["kind"] for r in lines} & set(QUESTIONNAIRE_KINDS)
    assert all("interruptedBlock" not in r for r in lines)
    recount = dict.fromkeys(KINDS[:-1], 0)
    for r in lines[:-1]:
        recount[r["kind"]] += 1
    assert lines[-1]["counts"] == recount
    recording = read_journal(copy)
    assert recording.questionnaires == ()
    source = read_journal(tmp_path / "source")
    assert [s.t.tolist() for t in recording.trials for s in t.strokes] == [
        s.t.tolist() for t in source.trials for s in t.strokes
    ]
    assert summarize(lines)["trials"] == summarize(rows)["trials"]
    derivation = json.loads((copy / "derivation.json").read_text())
    assert derivation["sourceSessionID"] == rows[0]["sessionID"]
    assert derivation["sourceJournalSHA256"] == hashlib.sha256(data).hexdigest()


def test_pen_only_copy_ends_at_the_last_trial(tmp_path: Path) -> None:
    # Detects a pen-only copy whose lines or closing time after the last task leak a safety label.
    rows = fixture_lines("supervised_minor_safety")
    write_export(tmp_path / "source", rows)
    copy = pen_only(tmp_path / "source", tmp_path / "copy")
    lines = [json.loads(s) for s in (copy / "journal.jsonl").read_bytes().splitlines()]
    last_ended = max(i for i, r in enumerate(lines) if r["kind"] == "trialEnded")
    assert [r["kind"] for r in lines[last_ended + 1 :]] == ["sessionClosed"]
    source_ended = max(r["t"] for r in rows if r["kind"] == "trialEnded")
    assert source_ended < rows[-1]["t"]
    assert lines[-2]["t"] == lines[-1]["t"] == source_ended


def test_pen_only_copy_of_a_session_without_a_trial_keeps_its_closing_time(tmp_path: Path) -> None:
    # Detects a pen-only copy that fails or moves sessionClosed.t when the session has no trialEnded.
    opened, handoff, closed = (fixture_lines("supervised_minor_safety")[i] for i in (0, 1, -1))
    rows = [opened, handoff, closed]
    for seq, row in enumerate(rows):
        row["seq"] = seq
    closed["counts"] = {kind: sum(r["kind"] == kind for r in rows[:-1]) for kind in KINDS[:-1]}
    write_export(tmp_path / "source", rows)
    copy = pen_only(tmp_path / "source", tmp_path / "copy")
    lines = [json.loads(s) for s in (copy / "journal.jsonl").read_bytes().splitlines()]
    assert [r["kind"] for r in lines] == ["sessionOpened", "handoff", "sessionClosed"]
    assert lines[-1]["t"] == closed["t"]


def test_pen_only_reads_an_encrypted_source(tmp_path: Path) -> None:
    # Detects a pen-only copy that records the ciphertext digest instead of the source journal digest.
    rows = fixture_lines("supervised_minor_safety")
    data = write_export(tmp_path / "plain", rows)
    write_encrypted_export(tmp_path / "sealed", data, rows[0]["sessionID"], KEY)
    copy = pen_only(tmp_path / "sealed", tmp_path / "copy", private_key=KEY)
    derivation = json.loads((copy / "derivation.json").read_text())
    assert derivation["sourceJournalSHA256"] == hashlib.sha256(data).hexdigest()


def test_pen_only_refuses_to_overwrite_its_source(tmp_path: Path) -> None:
    # Detects a pen-only copy that would replace the original export.
    data = write_export(tmp_path / "source", fixture_lines("supervised_minor_safety"))
    with pytest.raises(ValueError, match="own folder"):
        pen_only(tmp_path / "source", tmp_path / "source")
    assert (tmp_path / "source" / "journal.jsonl").read_bytes() == data


def test_pen_only_refuses_a_questionnaire_block_before_the_tasks(tmp_path: Path) -> None:
    # Detects a pen-only copy of a session with a beforeTasks block: the gap before the first task would show
    # how long the block took.
    rows = fixture_lines("self_administered")
    first = [i for i, r in enumerate(rows) if r["kind"] in QUESTIONNAIRE_KINDS and r.get("block") == 0]
    block = [rows[i] for i in first]
    for k, row in enumerate(block):
        row["t"] = rows[0]["openedUptime"] + 0.1 * (k + 1)
    rows = [rows[0], *block, *(r for i, r in enumerate(rows[1:], 1) if i not in first)]
    for seq, row in enumerate(rows):
        row["seq"] = seq
    write_export(tmp_path / "source", rows)
    assert read_journal(tmp_path / "source").questionnaires
    with pytest.raises(ValueError, match=r"after the last trialEnded\. Line 1 \(questionnaireStarted\)"):
        pen_only(tmp_path / "source", tmp_path / "copy")
    assert not (tmp_path / "copy").exists()


def test_plaintext_manifest_requires_demo(tmp_path: Path) -> None:
    # Detects a plaintext export without the demo mark required by the Swift reader.
    write_export(tmp_path, fixture_lines())
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_bytes())
    del manifest["demo"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(JournalError) as error:
        verify_export(tmp_path)
    assert error.value.rule == "I5"
