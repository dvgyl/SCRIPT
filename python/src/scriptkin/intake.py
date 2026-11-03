"""Study-store intake: verify each export, keep one copy of each session, and record a receipt.

docs/DATA_GOVERNANCE.md, stage 4. The store holds:

    sessions/<sessionID>/       a verified export as received: manifest.json and journal.jsonl, or
                                manifest.json and journal.jsonl.hpke (and journal.jsonl only with
                                keep_plaintext)
    quarantine/<digest>/        an export that failed a check or conflicts with another, and reason.json
    receipts/<sessionID>.json   every intake of that session
    intake.jsonl                every intake, in order

A session identifier with one journal is in `sessions/`. A session identifier with two or more
different journals is a conflict: every version is in `quarantine/`. Intake is idempotent: an export
that the log already records from the same folder with the same bytes is not processed again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from scriptkin.formats import FormatError
from scriptkin.formats.export import StudyKeyError, verify_export

Result = Literal["accepted", "duplicate", "conflict", "quarantined"]

EXPORT_FILES = ("manifest.json", "journal.jsonl", "journal.jsonl.hpke")
_SESSION_ID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
_LOG = "intake.jsonl"
_STAGING = ".partial"


@dataclass(frozen=True)
class Receipt:
    """One intake of one export folder. `export_sha256` covers the files as received."""

    source: str
    export_sha256: str
    session_id: str | None
    journal_sha256: str | None
    encrypted: bool
    key_id: str | None
    result: Result
    reason: str | None
    at: str

    def to_json(self) -> dict[str, Any]:
        return {key: getattr(self, name) for name, key in _RECEIPT_KEYS.items()}

    @staticmethod
    def from_json(d: dict[str, Any]) -> Receipt:
        return Receipt(**{name: d[key] for name, key in _RECEIPT_KEYS.items()})


_RECEIPT_KEYS = {
    "source": "source",
    "export_sha256": "exportSHA256",
    "session_id": "sessionID",
    "journal_sha256": "journalSHA256",
    "encrypted": "encrypted",
    "key_id": "keyID",
    "result": "result",
    "reason": "reason",
    "at": "at",
}


@dataclass(frozen=True)
class Refused:
    """An encrypted export that the given key cannot open. Intake writes nothing for it."""

    source: str
    reason: str


@dataclass(frozen=True)
class IntakeReport:
    receipts: tuple[Receipt, ...]
    known: tuple[str, ...]
    refused: tuple[Refused, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "intake",
            "receipts": [r.to_json() for r in self.receipts],
            "known": list(self.known),
            "refused": [{"source": r.source, "reason": r.reason} for r in self.refused],
        }


def session_folders(store: str | Path) -> list[Path]:
    """The accepted sessions of a store, in session identifier order."""
    folder = Path(store) / "sessions"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_dir() and _SESSION_ID.match(p.name))


def _exports(sources: Iterable[str | Path], store: Path) -> list[Path]:
    """Every folder with a manifest under the sources, outside the store, in path order."""
    found: set[Path] = set()
    for source in map(Path, sources):
        if not source.is_dir():
            raise ValueError(f"{source} is not a folder.")
        for manifest in source.rglob("manifest.json"):
            folder = manifest.parent.resolve()
            hidden = any(part.startswith("._") for part in folder.parts)
            if manifest.is_file() and not hidden and store not in (folder, *folder.parents):
                found.add(folder)
    return sorted(found)


def _digest(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(f"{name}\0{len(files[name])}\0".encode())
        h.update(files[name])
    return h.hexdigest()


def _claimed_session(files: dict[str, bytes]) -> str | None:
    """The session identifier that an unverified manifest claims, when it has the UUID form."""
    try:
        claimed: Any = json.loads(files["manifest.json"])["sessionID"]
    except (KeyError, TypeError, ValueError):
        return None
    return claimed if isinstance(claimed, str) and _SESSION_ID.match(claimed) else None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Store:
    """The store folders and the intake log.

    A folder or a receipt file is complete before it gets its final name. The log is append-only. A
    crash leaves at most a torn last log line, which the next run removes.
    """

    def __init__(self, root: Path):
        self.root = root
        self.staging = root / _STAGING
        shutil.rmtree(self.staging, ignore_errors=True)
        data = (root / _LOG).read_bytes() if (root / _LOG).is_file() else b""
        complete = data[: data.rfind(b"\n") + 1]
        if complete != data:
            self.write(root / _LOG, complete)
        self.log = [Receipt.from_json(json.loads(line)) for line in complete.splitlines()]

    def stage(self, name: str, files: dict[str, bytes]) -> Path:
        folder = self.staging / name
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        for file, data in files.items():
            (folder / file).write_bytes(data)
        return folder

    def place(self, folder: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        os.replace(folder, target)

    def write(self, target: Path, data: bytes) -> None:
        self.staging.mkdir(parents=True, exist_ok=True)
        partial = self.staging / target.name
        partial.write_bytes(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, target)

    def quarantine(self, folder: Path, digest: str, reason: str, session_id: str | None) -> None:
        target = self.root / "quarantine" / digest[:16]
        self.place(folder, target)
        note = {"reason": reason, "sessionID": session_id}
        self.write(target / "reason.json", (json.dumps(note, indent=1) + "\n").encode())

    def record(self, receipt: Receipt) -> None:
        self.log.append(receipt)
        if receipt.session_id is not None:
            entries = [r.to_json() for r in self.log if r.session_id == receipt.session_id]
            text = json.dumps(entries, indent=1) + "\n"
            self.write(self.root / "receipts" / f"{receipt.session_id}.json", text.encode())
        with (self.root / _LOG).open("ab") as log:
            log.write((json.dumps(receipt.to_json(), separators=(",", ":")) + "\n").encode())
            log.flush()
            os.fsync(log.fileno())

    def versions(self, session_id: str) -> dict[str, Receipt]:
        """The receipt that first brought each distinct journal of a session into the store."""
        found: dict[str, Receipt] = {}
        for r in self.log:
            if r.session_id == session_id and r.journal_sha256 and r.result in ("accepted", "conflict"):
                found.setdefault(r.journal_sha256, r)
        return found

    def close(self) -> None:
        shutil.rmtree(self.staging, ignore_errors=True)


def _admit(
    store: _Store, source: str, files: dict[str, bytes], private_key: bytes | None, keep_plaintext: bool
) -> Receipt | Refused:
    """Verify one export from a staged copy, then move the copy to sessions or quarantine, or drop it."""
    digest = _digest(files)
    staged = store.stage(digest, files)
    try:
        export = verify_export(staged, private_key=private_key)
        session_id: str = export.manifest["sessionID"]
        if not _SESSION_ID.match(session_id):
            raise FormatError("The session identifier is not a UUID.")
    except StudyKeyError as e:
        shutil.rmtree(staged)
        return Refused(source, str(e))
    except FormatError as e:
        claimed = _claimed_session(files)
        store.quarantine(staged, digest, str(e), claimed)
        encrypted = "journal.jsonl.hpke" in files
        return Receipt(source, digest, claimed, None, encrypted, None, "quarantined", str(e), _now())
    encryption: dict[str, Any] | None = export.manifest.get("encryption")
    journal_sha256 = hashlib.sha256(export.journal).hexdigest()
    versions = store.versions(session_id)
    result: Result = "duplicate" if journal_sha256 in versions else "conflict" if versions else "accepted"
    reason = None
    if result == "conflict":
        reason = f"Session {session_id} has another journal. Every version is in quarantine."
    key_id = encryption["keyID"] if encryption else None
    receipt = Receipt(
        source, digest, session_id, journal_sha256, encryption is not None, key_id, result, reason, _now()
    )
    for extra in set(files) - {"manifest.json", "journal.jsonl.hpke" if encryption else "journal.jsonl"}:
        (staged / extra).unlink()
    accepted = store.root / "sessions" / session_id
    if result == "accepted":
        if keep_plaintext and encryption:
            (staged / "journal.jsonl").write_bytes(export.journal)
        store.place(staged, accepted)
    elif result == "duplicate":
        shutil.rmtree(staged)
    else:
        if accepted.exists():
            first = next(iter(versions.values()))
            store.quarantine(accepted, first.export_sha256, str(reason), session_id)
        store.quarantine(staged, digest, str(reason), session_id)
    return receipt


def intake(
    sources: Iterable[str | Path],
    store: str | Path,
    *,
    private_key: bytes | None = None,
    keep_plaintext: bool = False,
) -> IntakeReport:
    """Verify every export folder under `sources` and bring it into `store` once.

    An export that fails a check goes to quarantine with the reason. An encrypted export that the
    key cannot open is refused, and nothing is written for it. The store keeps an encrypted export as
    received; it also keeps the decrypted journal only when `keep_plaintext` is true.
    """
    if private_key is not None and len(private_key) != 32:
        raise ValueError("The private key must be 32 raw X25519 bytes.")
    root = Path(store).resolve()
    exports = _exports(sources, root)
    state = _Store(root)
    seen = {(r.source, r.export_sha256) for r in state.log}
    receipts: list[Receipt] = []
    known: list[str] = []
    refused: list[Refused] = []
    try:
        for folder in exports:
            files = {n: (folder / n).read_bytes() for n in EXPORT_FILES if (folder / n).is_file()}
            if (str(folder), _digest(files)) in seen:
                known.append(str(folder))
                continue
            outcome = _admit(state, str(folder), files, private_key, keep_plaintext)
            if isinstance(outcome, Refused):
                refused.append(outcome)
                continue
            state.record(outcome)
            seen.add((outcome.source, outcome.export_sha256))
            receipts.append(outcome)
    finally:
        state.close()
    return IntakeReport(tuple(receipts), tuple(known), tuple(refused))
