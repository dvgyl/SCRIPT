"""Export folders: the manifest (rule I5), encrypted journals, and the pen-only copy.

docs/DESIGN.md sections 3.1 and 17 define the folder. A plaintext export holds `journal.jsonl`. An
encrypted export holds `journal.jsonl.hpke`, sealed to the study's public key with HPKE base mode.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from cryptography.exceptions import InvalidTag

from scriptkin import __version__, hpke
from scriptkin.contract import KINDS, READABLE_MAJORS, encode_line, manifest_bytes
from scriptkin.formats.checks import boolean, integer, one_of, record, text
from scriptkin.formats.journal import QUESTIONNAIRE_KINDS, FoldState, JournalError, Line, recording, replay
from scriptkin.recording import Recording

HPKE_INFO = b"SCRIPT export 1.0"
HPKE_SUITE = "X25519-HKDF-SHA256-ChaCha20Poly1305"


class StudyKeyError(JournalError):
    """The study private key is absent, or its public key does not give the manifest keyID."""


def _facts(data: bytes) -> dict[str, Any]:
    return {"bytes": len(data), "lines": data.count(b"\n"), "sha256": hashlib.sha256(data).hexdigest()}


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as e:
        raise JournalError("I1", 0, f"Cannot read {path.name}.") from e


def _decrypt(folder: Path, manifest: dict[str, Any], private_key: bytes | None) -> bytes:
    encryption = manifest["encryption"]
    if encryption["scheme"] != "hpke-base" or encryption["suite"] != HPKE_SUITE:
        raise JournalError("I5", 0, "Unknown encryption scheme or suite.")
    if private_key is None:
        raise StudyKeyError("I5", 0, "This export is encrypted. Give the study's private key.")
    if len(private_key) != 32:
        raise ValueError("The private key must be 32 raw X25519 bytes.")
    if hpke.key_id(hpke.public_key(private_key)) != encryption["keyID"]:
        raise StudyKeyError("I5", 0, "The public key of this private key does not give the manifest keyID.")
    ciphertext = _read(folder / "journal.jsonl.hpke")
    sealed = {"bytes": len(ciphertext), "sha256": hashlib.sha256(ciphertext).hexdigest()}
    if encryption["ciphertext"] != sealed:
        raise JournalError("I5", 0, "The manifest does not match journal.jsonl.hpke.")
    try:
        enc = base64.b64decode(encryption["encapsulatedKey"], validate=True)
        data = hpke.open_sealed(private_key, enc, HPKE_INFO, manifest["sessionID"].encode(), ciphertext)
    except (InvalidTag, ValueError, binascii.Error) as e:
        raise JournalError("I5", 0, "The ciphertext does not open with this key.") from e
    if encryption["plaintext"] != _facts(data):
        raise JournalError("I5", 0, "The decrypted journal does not match the manifest.")
    return data


_FACTS = record({"bytes": integer(0), "lines": integer(0), "sha256": text})
_HEAD = {"format": one_of("script.journal"), "version": text, "sessionID": text}
_PLAIN_MANIFEST = record({**_HEAD, "journal": _FACTS, "demo": lambda v: boolean(v) and v})
_ENCRYPTED_MANIFEST = record(
    {
        **_HEAD,
        "encryption": record(
            {
                "scheme": text,
                "suite": text,
                "keyID": text,
                "encapsulatedKey": text,
                "plaintext": _FACTS,
                "ciphertext": record({"bytes": integer(0), "sha256": text}),
            }
        ),
    }
)


def load_export(folder: Path, private_key: bytes | None = None) -> tuple[dict[str, Any], bytes]:
    """Return the manifest and the plaintext journal bytes after the manifest checks of rule I5."""
    try:
        manifest: Any = json.loads((folder / "manifest.json").read_bytes())
    except (OSError, ValueError) as e:
        raise JournalError("I5", 0, "Invalid or absent manifest.") from e
    if not isinstance(manifest, dict):
        raise JournalError("I5", 0, "Invalid manifest.")
    check = _ENCRYPTED_MANIFEST if "encryption" in manifest else _PLAIN_MANIFEST
    if (
        ("journal" in manifest and "encryption" in manifest)
        or not check(manifest)
        or str(manifest["version"]).split(".")[0] not in READABLE_MAJORS
    ):
        raise JournalError("I5", 0, "Invalid manifest or unknown major version.")
    manifest = cast(dict[str, Any], manifest)
    if "encryption" in manifest:
        return manifest, _decrypt(folder, manifest, private_key)
    data = _read(folder / "journal.jsonl")
    if manifest["journal"] != _facts(data):
        raise JournalError("I5", 0, "The manifest does not match journal.jsonl.")
    return manifest, data


@dataclass(frozen=True)
class VerifiedExport:
    """A closed export that passed the manifest checks and rules I1 to I7, with its plaintext journal."""

    manifest: dict[str, Any]
    journal: bytes
    rows: list[Line]
    state: FoldState

    def to_recording(self) -> Recording:
        return recording(self.state, hashlib.sha256(self.journal).hexdigest())


def verify_export(path: str | Path, *, private_key: bytes | None = None) -> VerifiedExport:
    """Check an export folder and return what it holds.

    An encrypted export needs `private_key`. A missing or wrong key raises `StudyKeyError`. Every other
    failed check raises `JournalError`.
    """
    manifest, data = load_export(Path(path), private_key)
    rows, state = replay(data.splitlines(keepends=True), closed=True)
    if manifest["sessionID"] != rows[0]["sessionID"]:
        raise JournalError("I5", 0, "The manifest names another session.")
    return VerifiedExport(manifest, data, rows, state)


def read_journal(path: str | Path, *, private_key: bytes | None = None) -> Recording:
    """Read a closed export folder and check rules I1 to I7. An encrypted export needs `private_key`."""
    return verify_export(path, private_key=private_key).to_recording()


def pen_only(export: str | Path, target: str | Path, *, private_key: bytes | None = None) -> Path:
    """Write a copy of an export without questionnaire, response, or safety lines, for label-blind analysis.

    The copy ends at the last `trialEnded`. It drops every later line except `sessionClosed`, whose `t`
    becomes the time of that `trialEnded`, because the time after the last task shows whether an operator
    handled a safety flag. A session with no `trialEnded` keeps all its non-questionnaire lines and its
    `sessionClosed.t`.

    The copy refuses a session with a questionnaire or safety line before the last `trialEnded`, for example
    a `beforeTasks` block. The copy would keep the times on both sides of the dropped lines, and the gap
    would show how long the block took. A session with no `trialEnded` must have no such line at all.

    The copy has new `seq` values, recounted closing counts, its own manifest, and `derivation.json`
    with the source session and the SHA-256 of the source journal. It is checked like any export.
    """
    source, out = Path(export), Path(target)
    if out.resolve() == source.resolve():
        raise ValueError("The pen-only copy needs its own folder.")
    verified = verify_export(source, private_key=private_key)
    data, rows = verified.journal, verified.rows
    closing, body = dict(rows[-1]), rows[:-1]
    ended = [i for i, r in enumerate(body) if r["kind"] == "trialEnded"]
    if ended:
        body = body[: ended[-1] + 1]
        closing["t"] = body[-1]["t"]
    dropped = next((r for r in body if r["kind"] in QUESTIONNAIRE_KINDS), None)
    if dropped is not None:
        raise ValueError(
            "A pen-only copy needs every questionnaire and safety line after the last trialEnded."
            f" Line {dropped['seq']} ({dropped['kind']}) is not, and the time gap around it would show how"
            " long the block took."
        )
    kept: list[Line] = [dict(r) for r in body if r["kind"] not in QUESTIONNAIRE_KINDS] + [closing]
    for seq, row in enumerate(kept):
        row["seq"] = seq
        row.pop("interruptedBlock", None)
    counts = dict.fromkeys(KINDS[:-1], 0)
    for row in kept[:-1]:
        counts[row["kind"]] += 1
    kept[-1]["counts"] = counts
    journal = b"".join(map(encode_line, kept))
    session_id = rows[0]["sessionID"]
    derivation = {
        "derivation": "penOnly",
        "droppedKinds": list(QUESTIONNAIRE_KINDS),
        "library": f"scriptkin {__version__}",
        "sourceJournalSHA256": hashlib.sha256(data).hexdigest(),
        "sourceSessionID": session_id,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "journal.jsonl").write_bytes(journal)
    (out / "manifest.json").write_bytes(manifest_bytes(session_id, journal))
    (out / "derivation.json").write_text(json.dumps(derivation, indent=1) + "\n", encoding="utf-8")
    read_journal(out)
    return out
