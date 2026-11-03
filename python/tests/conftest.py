from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from scriptkin import Device, Stroke, TaskRef, Trial, hpke
from scriptkin.contract import encode_line, manifest_bytes
from scriptkin.formats.export import HPKE_INFO, HPKE_SUITE
from scriptkin.recording import FloatArray

CONTRACT = Path(__file__).resolve().parents[2] / "contract"
FIXTURES = CONTRACT / "fixtures"


def make_trial(
    t: FloatArray,
    xy: FloatArray | None = None,
    *,
    strokes: tuple[Stroke, ...] | None = None,
    device: Device | None = None,
    task: TaskRef | None = None,
) -> Trial:
    positions = np.column_stack((t * 10, np.zeros(len(t)))) if xy is None else xy
    return Trial(
        0,
        task or TaskRef("clock"),
        strokes or (Stroke(t, positions),),
        device or Device("synthetic", "pencil"),
        started=float(t[0]),
        ended=float(t[-1]),
    )


def fixture_lines(name: str = "basic") -> list[dict[str, Any]]:
    return [json.loads(s) for s in (FIXTURES / "journal" / f"{name}.jsonl").read_bytes().splitlines()]


def write_export(folder: Path, rows: list[dict[str, Any]]) -> bytes:
    """Write a plaintext export and return its journal bytes."""
    folder.mkdir(parents=True, exist_ok=True)
    data = b"".join(map(encode_line, rows))
    (folder / "journal.jsonl").write_bytes(data)
    (folder / "manifest.json").write_bytes(manifest_bytes(rows[0].get("sessionID", "invalid"), data))
    return data


def write_encrypted_export(folder: Path, data: bytes, session_id: str, private_key: bytes) -> None:
    """Write an export as DESIGN section 17 specifies, sealed to the public key of `private_key`."""
    folder.mkdir(parents=True, exist_ok=True)
    enc, ciphertext = hpke.seal(hpke.public_key(private_key), HPKE_INFO, session_id.encode(), data)
    (folder / "journal.jsonl.hpke").write_bytes(ciphertext)
    encryption = {
        "scheme": "hpke-base",
        "suite": HPKE_SUITE,
        "keyID": "test",
        "encapsulatedKey": base64.b64encode(enc).decode(),
        "plaintext": {
            "bytes": len(data),
            "lines": data.count(b"\n"),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "ciphertext": {"bytes": len(ciphertext), "sha256": hashlib.sha256(ciphertext).hexdigest()},
    }
    manifest = {
        "format": "script.journal",
        "version": "1.0",
        "sessionID": session_id,
        "encryption": encryption,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
