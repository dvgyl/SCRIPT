"""Study-store intake: verify each export, keep one copy of each session, and record a receipt.

docs/DATA_GOVERNANCE.md, stage 4. The store holds:

    sessions/<sessionID>/       a verified export as received: manifest.json and journal.jsonl, or
                                manifest.json and journal.jsonl.hpke (and journal.jsonl only with
                                keep_plaintext)
    quarantine/<digest>/        an export that failed a check or conflicts with another, and reason.json
    damaged/<digest>/           the package of a damaged journal as received, and journal.jsonl
                                decrypted, so that a reviewer can read the safety flags in it
    receipts/<sessionID>.json   every intake of that session
    intake.jsonl                every intake, in order

A session identifier with one journal is in `sessions/`. A session identifier with two or more
different journals is a conflict: every version is in `quarantine/`. A package whose manifest has a
`damaged` member never enters `sessions/` (docs/DESIGN.md section 17). Intake is idempotent: an export
that the log already records from the same folder with the same bytes is not processed again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from scriptkin.formats import FormatError
from scriptkin.formats.checks import integer, record
from scriptkin.formats.export import StudyKeyError, load_export, verify_export

Result = Literal["accepted", "duplicate", "conflict", "quarantined", "damaged"]

EXPORT_FILES = ("manifest.json", "journal.jsonl", "journal.jsonl.hpke")
_SESSION_ID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
_LOG = "intake.jsonl"
_STAGING = ".partial"
# The `damaged` member of a manifest: the first line of the journal that does not decode or fold.
_DAMAGED = record({"line": integer(0)})


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
    return sorted(p for p in folder.iterdir() if p.is_dir() and _SESSION_ID.fullmatch(p.name))


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
    return claimed.upper() if isinstance(claimed, str) and _SESSION_ID.fullmatch(claimed) else None


def _journal_session(files: dict[str, bytes]) -> str | None:
    try:
        row: Any = json.loads(files["journal.jsonl"].split(b"\n", 1)[0])
        value = row["sessionID"] if row["kind"] == "sessionOpened" else None
    except (KeyError, TypeError, ValueError):
        return None
    return value.upper() if isinstance(value, str) and _SESSION_ID.fullmatch(value) else None


def _quarantined(
    store: _Store,
    staged: Path,
    source: str,
    files: dict[str, bytes],
    digest: str,
    reason: str,
    private_key: bytes | None,
) -> Receipt:
    journal_session = _journal_session(files)
    if journal_session is None and "journal.jsonl.hpke" in files and private_key is not None:
        try:
            _, journal = load_export(staged, private_key)
            journal_session = _journal_session({"journal.jsonl": journal})
        except Exception:
            pass
    session = _claimed_session(files) or journal_session
    if journal_session is not None:
        store.identifiers[digest] = {journal_session}
    store.quarantine(staged, digest, reason, session)
    return Receipt(
        source, digest, session, None, "journal.jsonl.hpke" in files, None, "quarantined", reason, _now()
    )


def _claims_damage(files: dict[str, bytes]) -> bool:
    try:
        manifest: Any = json.loads(files["manifest.json"])
    except (KeyError, ValueError):
        return False
    return isinstance(manifest, dict) and "damaged" in manifest


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Store:
    """The store folders and the intake log.

    A folder or a receipt file is complete before it gets its final name. The log is append-only. A
    crash leaves at most a torn last log line, which the next run removes.
    """

    def __init__(self, root: Path):
        self.root = root
        self.identifiers: dict[str, set[str]] = {}
        self.staging = root / _STAGING
        shutil.rmtree(self.staging, ignore_errors=True)
        data = (root / _LOG).read_bytes() if (root / _LOG).is_file() else b""
        complete = data[: data.rfind(b"\n") + 1]
        if complete != data:
            self.write(root / _LOG, complete)
        self.log = [Receipt.from_json(json.loads(line)) for line in complete.splitlines()]
        self.log = [replace(r, session_id=r.session_id.upper()) if r.session_id else r for r in self.log]

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
        identifiers = ({receipt.session_id} - {None}) | self.identifiers.pop(receipt.export_sha256, set())
        folder = (
            self.root
            / ("damaged" if receipt.result == "damaged" else "quarantine")
            / receipt.export_sha256[:16]
        )
        if folder.is_dir():
            files = {n: (folder / n).read_bytes() for n in EXPORT_FILES if (folder / n).is_file()}
            identifiers |= {_claimed_session(files), _journal_session(files)} - {None}
        for session in identifiers:
            target = self.root / "receipts" / f"{session}.json"
            entries = json.loads(target.read_bytes()) if target.is_file() else []
            entries.append(receipt.to_json())
            self.write(target, (json.dumps(entries, indent=1) + "\n").encode())
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
    if _claims_damage(files):
        return _admit_damaged(store, source, files, digest, staged, private_key)
    try:
        export = verify_export(staged, private_key=private_key)
        session_id: str = export.manifest["sessionID"].upper()
        if not _SESSION_ID.fullmatch(session_id):
            raise FormatError("The session identifier is not a UUID.")
    except StudyKeyError as e:
        shutil.rmtree(staged)
        return Refused(source, str(e))
    except FormatError as e:
        return _quarantined(store, staged, source, files, digest, str(e), private_key)
    except Exception as e:
        return _quarantined(store, staged, source, files, digest, type(e).__name__, private_key)
    encryption: dict[str, Any] | None = export.manifest.get("encryption")
    journal_sha256 = hashlib.sha256(export.journal).hexdigest()
    versions = store.versions(session_id)
    result: Result = "duplicate" if journal_sha256 in versions else "conflict" if versions else "accepted"
    accepted = store.root / "sessions" / session_id
    if accepted.exists() and not versions:
        result = "conflict"
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
            existing = {n: (accepted / n).read_bytes() for n in EXPORT_FILES if (accepted / n).is_file()}
            old_digest = next(iter(versions.values())).export_sha256 if versions else _digest(existing)
            store.quarantine(accepted, old_digest, str(reason), session_id)
            if not versions:
                store.record(replace(receipt, export_sha256=old_digest, journal_sha256=None))
        store.quarantine(staged, digest, str(reason), session_id)
    return receipt


def _admit_damaged(
    store: _Store, source: str, files: dict[str, bytes], digest: str, staged: Path, private_key: bytes | None
) -> Receipt | Refused:
    """Check the manifest facts of a damaged journal's package, decrypt it, and file it under `damaged/`.

    The journal is not folded: its damage is known. A package that fails the facts goes to quarantine.
    """
    try:
        manifest, journal = load_export(staged, private_key)
        session_id: str = manifest["sessionID"].upper()
        if not _DAMAGED(manifest["damaged"]) or not _SESSION_ID.fullmatch(session_id):
            raise FormatError("The damaged member or the session identifier of the manifest is invalid.")
    except StudyKeyError as e:
        shutil.rmtree(staged)
        return Refused(source, str(e))
    except FormatError as e:
        return _quarantined(store, staged, source, files, digest, str(e), private_key)
    except Exception as e:
        return _quarantined(store, staged, source, files, digest, type(e).__name__, private_key)
    encryption: dict[str, Any] | None = manifest.get("encryption")
    for extra in set(files) - {"manifest.json", "journal.jsonl.hpke" if encryption else "journal.jsonl"}:
        (staged / extra).unlink()
    if encryption:
        (staged / "journal.jsonl").write_bytes(journal)
    store.place(staged, store.root / "damaged" / digest[:16])
    return Receipt(
        source,
        digest,
        session_id,
        hashlib.sha256(journal).hexdigest(),
        encryption is not None,
        encryption["keyID"] if encryption else None,
        "damaged",
        f"Damage at line {manifest['damaged']['line']}.",
        _now(),
    )


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


def store_copies(store: str | Path, session_ids: Iterable[str]) -> dict[str, list[Path]]:
    """Find stored copies by session identifier, including copies with no receipt."""
    root = Path(store)
    requested = [value.upper() for value in session_ids]
    if any(not _SESSION_ID.fullmatch(value) for value in requested):
        raise ValueError("A session identifier is not a UUID.")
    result: dict[str, list[Path]] = {value: [] for value in requested}
    named: set[Path] = set()
    for receipt in sorted((root / "receipts").glob("*.json")):
        if receipt.name.startswith("._"):
            continue
        session = receipt.stem.upper()
        copies: set[Path] = set()
        for row in json.loads(receipt.read_bytes()):
            for category in ("quarantine", "damaged"):
                folder = root / category / row["exportSHA256"][:16]
                if folder.is_dir():
                    copies.add(folder)
        named |= copies
        if session in result:
            result[session].extend(sorted(copies))
    for session in requested:
        folder = root / "sessions" / session
        if folder.is_dir():
            result[session].insert(0, folder)
    result["unidentified"] = sorted(
        folder
        for category in ("quarantine", "damaged")
        for folder in (root / category).glob("*")
        if folder.is_dir() and not folder.name.startswith("._") and folder not in named
    )
    return result
