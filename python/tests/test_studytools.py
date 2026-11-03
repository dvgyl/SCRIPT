from __future__ import annotations

import base64
import hashlib
import json
import stat
from pathlib import Path

import pytest
from conftest import CONTRACT, fixture_lines, write_encrypted_export, write_export

from scriptkin import hpke, studyconfig, studytools
from scriptkin.cli import main
from scriptkin.formats.export import read_journal

SWIFT_PIN = {
    "salt": "AAECAwQFBgcICQoLDA0ODw==",
    "iterations": 600_000,
    "pbkdf2Sha256": "41e970f647a127ad1149029f290f489446ee62e4dde83ddf9e3d7e632ff56362",
}


def demo_with(tmp_path: Path, **members: object) -> Path:
    config = json.loads((CONTRACT / "configs" / "demo.json").read_bytes())
    config.update(members)
    path = tmp_path / "study.json"
    path.write_bytes(studyconfig.config_bytes(config))
    return path


def test_config_new_writes_a_skeleton_that_the_check_refuses_until_it_is_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a skeleton that passes the check while it lacks the export key, the PIN, or crisis lists that
    # the study team checked, so that a study could start with demo values.
    path = tmp_path / "study.json"
    assert main(["config", "new", "--from", "demo", "--out", str(path)]) == 0
    capsys.readouterr()
    assert main(["config", "check", str(path)]) == 1
    listed = capsys.readouterr().out.splitlines()[1:]
    assert listed == [
        "- no crisis list for jurisdiction US",
        "- no crisis list for jurisdiction GB",
        "- no crisis list for jurisdiction IN",
        "- no crisis list for jurisdiction AU",
        "- a study configuration needs export.recipientPublicKey",
        "- a study configuration needs operatorPin",
    ]


def test_config_new_never_overwrites_a_configuration(tmp_path: Path) -> None:
    # Detects a `config new` that replaces an existing study configuration with the skeleton.
    path = tmp_path / "study.json"
    path.write_text("kept")
    assert main(["config", "new", "--from", "demo", "--out", str(path)]) == 2
    assert path.read_text() == "kept"


def test_config_keygen_gives_a_key_pair_that_opens_its_exports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a printed export member that does not belong to the private key file, so that exports sealed
    # to it would not open with the study key.
    assert main(["config", "keygen", "--out", str(tmp_path / "keys")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads((tmp_path / "keys" / studytools.PUBLIC_KEY_FILE).read_bytes()) == printed
    private = (tmp_path / "keys" / studytools.PRIVATE_KEY_FILE).read_bytes()
    public = base64.b64decode(printed["recipientPublicKey"], validate=True)
    assert hpke.public_key(private) == public
    assert printed["keyID"] == hashlib.sha256(public).hexdigest()
    rows = fixture_lines()
    data = write_export(tmp_path / "plain", rows)
    write_encrypted_export(tmp_path / "sealed", data, rows[0]["sessionID"], private)
    assert read_journal(tmp_path / "sealed", private_key=private).source_hashes == (
        hashlib.sha256(data).hexdigest(),
    )
    assert studyconfig.validate({**studyconfig.demo_config(), "export": printed}) == []


def test_config_keygen_makes_a_private_key_that_only_its_owner_reads(tmp_path: Path) -> None:
    # Detects a private key file that other users can read, or a second key pair that replaces the first.
    assert main(["config", "keygen", "--out", str(tmp_path)]) == 0
    private = tmp_path / studytools.PRIVATE_KEY_FILE
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    first = private.read_bytes()
    assert len(first) == 32
    assert main(["config", "keygen", "--out", str(tmp_path)]) == 2
    assert private.read_bytes() == first


def test_pin_record_verifies_with_the_swift_test_vector() -> None:
    # Detects a PIN record that the app cannot verify: another derivation, iteration count, text encoding,
    # salt encoding, or key format than the app's test vector.
    assert studytools.pin_record("4821", salt=bytes(range(16))) == SWIFT_PIN


def test_config_pin_asks_twice_without_echo_and_salts_each_record(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a `config pin` that reads the PIN with echo, writes a record that does not verify for the PIN,
    # or uses one salt for every record.
    prompts: list[str] = []

    def no_echo(prompt: str) -> str:
        prompts.append(prompt)
        return "482193"

    monkeypatch.setattr("getpass.getpass", no_echo)
    records = []
    for _ in range(2):
        assert main(["config", "pin"]) == 0
        records.append(json.loads(capsys.readouterr().out))
    assert len(prompts) == 4
    for record in records:
        salt = base64.b64decode(record["salt"], validate=True)
        assert len(salt) == 16 and record["iterations"] == 600_000
        key = hashlib.pbkdf2_hmac("sha256", b"482193", salt, 600_000, dklen=32)
        assert record["pbkdf2Sha256"] == key.hex()
    assert records[0]["salt"] != records[1]["salt"]


def test_config_pin_refuses_two_different_entries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a `config pin` that keeps a PIN whose second entry differs: a typing error locks operators out.
    entries = iter(["4821", "4812"])

    def entered(prompt: str) -> str:
        return next(entries)

    monkeypatch.setattr("getpass.getpass", entered)
    assert main(["config", "pin"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "differ" in output.err


@pytest.mark.parametrize("pin", ["", "482", "48a1", "\uff14\uff18\uff12\uff11"])
def test_a_pin_has_four_or_more_ascii_digits(pin: str) -> None:
    # Detects a PIN record for a PIN that the app's number pad cannot enter.
    with pytest.raises(ValueError, match="digits"):
        studytools.pin_record(pin)


def test_config_check_lists_every_schema_problem_with_its_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a check that stops at the first schema problem or does not say where each problem is.
    lists = studyconfig.demo_config()["safety"]
    lists["crisisLists"][0]["verifiedOn"] = "yesterday"
    path = demo_with(
        tmp_path,
        jurisdictions=["usa"],
        operatorPin={**SWIFT_PIN, "iterations": 599_999},
        safety=lists,
    )
    assert main(["config", "check", str(path)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"{path} has 3 problem(s):",
        "- jurisdictions[0]: 'usa' does not match '^[A-Z]{2}$'",
        "- operatorPin.iterations: 599999 is less than the minimum of 600000",
        "- safety.crisisLists[0].verifiedOn: 'yesterday' is not a 'date'",
    ]


def test_config_check_applies_the_configuration_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detects a check that applies the schema only, so that a configuration which the app refuses passes.
    tasks = [
        *studyconfig.demo_config()["tasks"],
        {"task": "maze", "practice": False, "attempts": 1, "hands": "both"},
    ]
    assert main(["config", "check", str(demo_with(tmp_path, tasks=tasks))]) == 1
    assert "- task 'maze' is not in the battery" in capsys.readouterr().out.splitlines()
    path = demo_with(tmp_path)
    assert main(["config", "check", str(path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{path} passes every check.",
        f"SHA-256: {hashlib.sha256(path.read_bytes()).hexdigest()}",
    ]
