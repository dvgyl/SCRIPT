from __future__ import annotations

import copy
import json
from typing import Any

from conftest import CONTRACT, FIXTURES
from jsonschema import Draft202012Validator

from scriptkin.formats.journal import JournalError, replay


def test_reader_applies_schema_constraints() -> None:
    # Detects schema string and numeric constraints absent from the Python line reader.
    schema = json.loads((CONTRACT / "journal.schema.json").read_bytes())
    validator = Draft202012Validator(schema)
    expected: set[str] = set()
    checked: set[str] = set()
    failures: set[str] = set()

    def constraints(node: dict[str, Any]) -> dict[str, Any]:
        return {
            key: node[key]
            for key in ("pattern", "enum", "minLength", "minimum", "exclusiveMinimum", "format")
            if key in node and (key != "format" or node[key] == "uuid")
        }

    def inventory(node: Any, path: str) -> None:
        if isinstance(node, dict):
            expected.update(path + "/" + key for key in constraints(node))
            for key, child in node.items():
                inventory(child, path + "/" + key)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                inventory(child, path + "/" + str(index))

    inventory(schema, "#")

    def walk(
        node: dict[str, Any], location: str, value: Any, path: list[Any], row: dict[str, Any], purpose: str
    ) -> None:
        if "$ref" in node:
            location = node["$ref"]
            node = schema["$defs"][location.rsplit("/", 1)[1]]
        for key, limit in constraints(node).items():
            name = location + "/" + key
            bad = (
                limit - 1
                if key == "minimum"
                else limit
                if key == "exclusiveMinimum"
                else ""
                if key == "minLength"
                else "INVALID PRIVATE TEXT!"
            )
            assert not Draft202012Validator(
                {key: limit}, format_checker=Draft202012Validator.FORMAT_CHECKER
            ).is_valid(bad), name
            changed = copy.deepcopy(row)
            target = changed
            for step in path[:-1]:
                target = target[step]
            target[path[-1]] = bad
            if row["kind"] == "trialStarted" and path in (["trial", "task"], ["spec", "id"]):
                changed["trial"]["task"] = changed["spec"]["id"] = bad
            try:
                replay([*rows[: row["seq"]], changed], closed=False)
            except JournalError as error:
                if error.rule != "I1":
                    failures.add(name)
            else:
                failures.add(name)
            checked.add(name)
        for key, child in node.get("properties", {}).items():
            if isinstance(value, dict) and key in value:
                walk(child, location + "/properties/" + key, value[key], [*path, key], row, purpose)
        if "items" in node and isinstance(value, list):
            for index, child in enumerate(value):
                walk(node["items"], location + "/items", child, [*path, index], row, purpose)
        for keyword in ("allOf", "oneOf", "anyOf"):
            for index, child in enumerate(node.get(keyword, [])):
                if keyword == "allOf" or validator.evolve(schema=child).is_valid(value):
                    walk(child, location + "/" + keyword + "/" + str(index), value, path, row, purpose)

    for fixture in sorted((FIXTURES / "journal").glob("*.jsonl")):
        if fixture.name.startswith("._"):
            continue
        rows = [json.loads(raw) for raw in fixture.read_bytes().splitlines()]
        for row in rows:
            walk(schema["$defs"][row["kind"]], "#/$defs/" + row["kind"], row, [], row, rows[0]["purpose"])
    rows = [json.loads(raw) for raw in (FIXTURES / "journal" / "basic.jsonl").read_bytes().splitlines()]
    row = next(row for row in rows if row["kind"] == "trialStarted")
    row["spec"]["completion"] = {"kind": "timed", "seconds": 1}
    walk(schema["$defs"]["trialStarted"], "#/$defs/trialStarted", row, [], row, rows[0]["purpose"])
    missing = expected - checked
    assert not missing and not failures, (
        "Missing carriers: " + str(sorted(missing)) + "; unapplied constraints: " + str(sorted(failures))
    )
