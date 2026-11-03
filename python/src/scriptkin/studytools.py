"""Study configuration tools behind `scriptkin config` (docs/DESIGN.md section 14).

`skeleton` gives a configuration to complete, `export_key_pair` makes the study key pair, `pin_record`
gives the `operatorPin` member, and `check` applies the schema and `studyconfig.validate`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any, TypedDict, cast

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from scriptkin import hpke, studyconfig

PIN_ITERATIONS = 600_000
PIN_SALT_BYTES = 16
PIN_MIN_DIGITS = 4
PRIVATE_KEY_FILE = "study-export-key.bin"
PUBLIC_KEY_FILE = "study-export-key.public.json"


class ExportBlock(TypedDict):
    """The `export` member of a configuration."""

    recipientPublicKey: str
    keyID: str


class PinRecord(TypedDict):
    """The `operatorPin` member of a configuration."""

    salt: str
    iterations: int
    pbkdf2Sha256: str


def skeleton() -> dict[str, Any]:
    """The demo configuration as a study configuration to complete.

    `demo` is false and the crisis lists are empty, so `check` lists what the study team must still give:
    the export key, the operator PIN, and a crisis list from official pages for each jurisdiction.
    """
    config = studyconfig.demo_config()
    config["demo"] = False
    config["safety"]["crisisLists"] = []
    return config


def _create(path: Path, data: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as file:
        os.fchmod(file.fileno(), mode)
        file.write(data)


def export_key_pair(folder: Path) -> ExportBlock:
    """Write a new X25519 key pair into `folder` and return the `export` member of its public key.

    The private key file holds the 32 raw bytes that `scriptkin intake --key` reads. Only its owner can
    read it. The public key file holds the `export` member. Neither file may exist already.
    """
    paths = (folder / PRIVATE_KEY_FILE, folder / PUBLIC_KEY_FILE)
    for path in paths:
        if path.exists():
            raise FileExistsError(f"{path} exists. A new key pair needs its own folder.")
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    block = ExportBlock(recipientPublicKey=base64.b64encode(public).decode(), keyID=hpke.key_id(public))
    folder.mkdir(parents=True, exist_ok=True)
    _create(paths[0], private.private_bytes_raw(), 0o600)
    _create(paths[1], (json.dumps(block, indent=1) + "\n").encode(), 0o644)
    return block


def pin_record(pin: str, *, salt: bytes | None = None) -> PinRecord:
    """The `operatorPin` member of a PIN: PBKDF2-HMAC-SHA256 with 600,000 iterations and a 32-byte key.

    The salt is 16 random bytes unless `salt` gives it. The app asks for the PIN on a number pad, so a PIN
    has digits only.
    """
    if not (pin.isascii() and pin.isdigit() and len(pin) >= PIN_MIN_DIGITS):
        raise ValueError(f"A PIN has {PIN_MIN_DIGITS} or more digits and no other characters.")
    salt = secrets.token_bytes(PIN_SALT_BYTES) if salt is None else salt
    key = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PIN_ITERATIONS, dklen=32)
    return PinRecord(salt=base64.b64encode(salt).decode(), iterations=PIN_ITERATIONS, pbkdf2Sha256=key.hex())


def check(config_path: Path, schema_path: Path) -> list[str]:
    """Every problem of a configuration file. An empty list means that it passes.

    The schema problems come first, each with its location. `studyconfig.validate` runs only on a
    configuration that the schema accepts, because its rules read the members that the schema requires.
    """
    try:
        config: Any = json.loads(config_path.read_bytes())
    except ValueError as e:
        return [f"{config_path.name} is not valid JSON: {e}"]
    schema = json.loads(schema_path.read_bytes())
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    problems: list[str] = []
    for error in sorted(validator.iter_errors(config), key=lambda e: e.json_path):
        detail = best_match([error])
        where = "top level" if detail.json_path == "$" else detail.json_path.removeprefix("$.")
        problems.append(f"{where}: {detail.message}")
    return problems or studyconfig.validate(cast(dict[str, Any], config))
