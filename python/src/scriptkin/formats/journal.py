"""Validate and fold the append-only SCRIPT journal."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from scriptkin.contract import KINDS, OUTCOME_OF_REASON, encode_line
from scriptkin.formats import FormatError
from scriptkin.recording import Channel, Device, PenState, Recording, Stroke, TaskRef, Trial, TrialOutcome


class JournalError(FormatError):
    def __init__(self, rule: str, line: int, detail: str):
        self.rule, self.line = rule, line
        super().__init__(f"{rule}, line {line}: {detail}")


Line = dict[str, Any]
Lines = Iterable[Line | str | bytes]
_CHANNELS = ("force", "altitude", "azimuth", "roll")
_REQUIRED = {
    "sessionOpened": (
        "format sessionID participant openedAt openedUptime boot app device scale "
        "pencil channels input battery"
    ),
    "trialStarted": "t trial spec view stimulusOrigin",
    "contactBegan": "t contact",
    "sample": "contact t x y phase est",
    "correction": "update arrived expect",
    "contactEnded": "t contact cancelled",
    "hover": "t clock x y z",
    "finger": "t phase x y",
    "trialEnded": "t trial reason",
    "mark": "t trial code",
    "recovered": "t at boot truncatedBytes",
    "sessionClosed": "t counts",
}


def _shape(line: Line, i: int) -> None:
    def fail(detail: str) -> None:
        raise JournalError("I1", i, detail)

    def fields(value: Any, names: str) -> None:
        if not isinstance(value, dict) or any(k not in value for k in names.split()):
            fail("A required object or field is absent.")

    def finite(value: Any) -> bool:
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        if isinstance(value, list):
            return all(finite(v) for v in value)
        return value is not None and (not isinstance(value, float) or math.isfinite(value))

    fields(line, _REQUIRED[line["kind"]])
    # The task stimulus is the only nullable field in version 1.
    clean = {k: v for k, v in line.items() if k != "spec"}
    if not finite(clean):
        fail("Fields must be finite and cannot be null.")
    for k in ("t", "arrived", "openedUptime", "x", "y", "z", *_CHANNELS):
        if k in line and (type(line[k]) not in (int, float) or not math.isfinite(line[k])):
            fail(f"{k} must be a finite number.")
    for k in ("contact", "update", "est", "expect", "truncatedBytes", "interrupted"):
        if k in line and type(line[k]) is not int:
            fail(f"{k} must be an integer.")
    if "contact" in line and line["contact"] < 1:
        fail("contact must be positive.")
    if any(line[k] < 0 for k in ("t", "arrived", "openedUptime") if k in line):
        fail("Uptime must be nonnegative.")
    for k in ("est", "expect"):
        if k in line and not 0 <= line[k] <= 31:
            fail(f"{k} must be a valid estimate bit set.")
    kind = line["kind"]
    if kind == "sessionOpened":
        for key, names in {
            "app": "version build sourceRevision",
            "device": "model os nativeScale",
            "scale": "kind",
            "pencil": "declared",
            "battery": "id version sha256",
            "channels": "force altitude azimuth roll hover",
        }.items():
            fields(line[key], names)
        if any(type(line["channels"][k]) is not bool for k in (*_CHANNELS, "hover")):
            fail("Channel plans must be Boolean.")
        if not all(isinstance(line[k], str) for k in ("format", "sessionID", "participant", "boot")):
            fail("Session identifiers and version must be strings.")
        if line["input"] not in ("pencil", "directTouchAsPen"):
            fail("Unknown input kind.")
        scale = line["scale"]
        if scale["kind"] not in ("modelTable", "ruler", "unknown"):
            fail("Unknown scale kind.")
        if scale["kind"] != "unknown":
            fields(scale, "mmPerPoint")
            if type(scale["mmPerPoint"]) not in (int, float) or scale["mmPerPoint"] <= 0:
                fail("mmPerPoint must be positive.")
    elif kind == "trialStarted":
        fields(line["trial"], "index task attempt")
        fields(line["spec"], "id mode instruction stimulus completion")
        fields(line["view"], "width height")
        fields(line["stimulusOrigin"], "x y")
        trial, spec = line["trial"], line["spec"]
        if type(trial["index"]) is not int or trial["index"] < 0:
            fail("The trial index must be a nonnegative integer.")
        if type(trial["attempt"]) is not int or trial["attempt"] < 1:
            fail("The attempt must be a positive integer.")
        if trial["task"] != spec["id"] or spec["mode"] not in ("trace", "copy", "free"):
            fail("The task reference must match the task specification.")
        if not finite({k: v for k, v in spec.items() if k != "stimulus"}):
            fail("Task fields must be finite.")
        stimulus = spec["stimulus"]
        if stimulus is not None:
            fields(stimulus, "kind frameMM")
            if not finite(stimulus) or stimulus["kind"] not in ("paths", "text"):
                fail("Invalid stimulus.")
            if stimulus["kind"] == "paths":
                fields(stimulus, "paths strokeMM")
                paths = stimulus["paths"]
                if (
                    not isinstance(paths, list)
                    or not paths
                    or any(
                        not isinstance(p, list)
                        or len(p) < 2
                        or any(
                            not isinstance(xy, list)
                            or len(xy) != 2
                            or any(type(v) not in (int, float) for v in xy)
                            for xy in p
                        )
                        for p in paths
                    )
                ):
                    fail("A stimulus path needs at least two finite points.")
    elif kind == "sample":
        if line["phase"] not in ("began", "moved", "stationary", "ended", "cancelled"):
            fail("Unknown sample phase.")
        if "pending" in line:
            fields(line["pending"], "expect update")
            pending = line["pending"]
            if (
                type(pending["update"]) is not int
                or type(pending["expect"]) is not int
                or not (1 <= pending["expect"] <= 31)
            ):
                fail("Invalid pending estimate key or bits.")
    elif kind == "trialEnded":
        if type(line["trial"]) is not int or line["reason"] not in OUTCOME_OF_REASON:
            fail("Invalid trial end.")
    elif kind == "recovered" and not isinstance(line["boot"], str):
        fail("The boot identifier must be a string.")
    elif kind == "sessionClosed" and not isinstance(line["counts"], dict):
        fail("Closing counts must be an object.")


def _decode(lines: Lines) -> list[Line]:
    decoded: list[Line] = []
    for i, raw in enumerate(lines):
        if isinstance(raw, dict):
            line = dict(raw)
        else:
            valid_lf = (
                (raw.endswith(b"\n") and b"\r" not in raw)
                if isinstance(raw, bytes)
                else (raw.endswith("\n") and "\r" not in raw)
            )
            if not valid_lf:
                raise JournalError("I1", i, "Each line must end in LF.")
            try:
                line = json.loads(raw)
            except (ValueError, UnicodeError) as e:
                raise JournalError("I1", i, "Invalid UTF-8 JSON.") from e
        if not isinstance(line, dict) or line.get("kind") not in KINDS:
            raise JournalError("I1", i, "Expected an object with a known kind.")
        if type(line.get("seq")) is not int or line["seq"] != i:
            raise JournalError("I2", i, "seq must equal the zero-based line index.")
        _shape(line, i)
        decoded.append(line)
    return decoded


@dataclass
class _Trial:
    start: Line
    boot: str
    samples: dict[int, list[Line]] = field(default_factory=dict)
    hover: list[list[Line]] = field(default_factory=list)
    forces: dict[int, float] = field(default_factory=dict)
    outcome: str = "open"
    end: float | None = None
    corrections: int = 0
    fingers: int = 0
    unmatched: int = 0
    marks: list[str] = field(default_factory=list)
    hover_open: bool = False


@dataclass
class _Fold:
    lines: list[Line]
    trials: list[_Trial]
    state: str
    counts: dict[str, int]
    unmatched: int
    truncated: int

    def summary(self) -> Line:
        return {
            "sessionID": self.lines[0]["sessionID"],
            "state": self.state,
            "counts": self.counts,
            "trials": [
                {
                    "index": t.start["trial"]["index"],
                    "task": t.start["trial"]["task"],
                    "attempt": t.start["trial"]["attempt"],
                    "outcome": t.outcome,
                    "contacts": len(t.samples),
                    "samples": sum(map(len, t.samples.values())),
                    "corrections": t.corrections,
                    "unresolved": sum(bool(s.get("remaining", 0)) for ss in t.samples.values() for s in ss),
                }
                for t in self.trials
            ],
            "unmatchedCorrections": self.unmatched,
            "truncatedBytes": self.truncated,
        }


def _run(lines: Lines, *, closed: bool = False) -> _Fold:
    rows = _decode(lines)
    if not rows or rows[0]["kind"] != "sessionOpened":
        raise JournalError("I3", 0, "Line 0 must open the session.")
    if str(rows[0]["format"]).split(".")[0] != "1":
        raise JournalError("I3", 0, "Unsupported major version.")
    if closed and rows[-1]["kind"] != "sessionClosed":
        raise JournalError("I3", len(rows) - 1, "An export must end with sessionClosed.")
    counts: dict[str, int] = dict.fromkeys(KINDS, 0)
    trials: list[_Trial] = []
    active: _Trial | None = None
    contacts: dict[int, _Trial] = {}
    open_contacts: set[int] = set()
    pending: dict[tuple[int, int], tuple[_Trial, Line]] = {}
    state, unmatched, truncated = "ready", 0, 0
    boot = rows[0]["boot"]
    clocks = {boot: float(rows[0]["openedUptime"])}
    for i, row in enumerate(rows):
        k = row["kind"]
        if i == 0:
            counts[k] += 1
            continue
        if state == "closed" or k == "sessionOpened":
            raise JournalError("I6", i, "The session is not open.")
        if k == "recovered":
            boot = row["boot"]
        # Touch timestamps can precede callback times. Compare only app lifecycle clocks.
        if k in ("trialStarted", "trialEnded", "recovered", "sessionClosed"):
            now = float(row["t"])
            if now < clocks.get(boot, 0):
                raise JournalError("I7", i, "Lifecycle uptime precedes its boundary in the same boot.")
            clocks[boot] = now
        if k == "trialStarted":
            if active is not None or row["trial"]["index"] in [t.start["trial"]["index"] for t in trials]:
                raise JournalError("I6", i, "Trials overlap or reuse an index.")
            active = _Trial(row, boot)
            trials.append(active)
            state = "drawing"
        elif k == "contactBegan":
            c = row["contact"]
            if active is None or c in contacts:
                raise JournalError("I6", i, "A contact needs an open trial and a unique ID.")
            active.hover_open = False
            contacts[c] = active
            open_contacts.add(c)
            active.samples[c] = []
            if "maxForce" in row:
                active.forces[c] = row["maxForce"]
        elif k in ("sample", "contactEnded"):
            c = row["contact"]
            if c not in open_contacts:
                raise JournalError("I6", i, "The contact is not open.")
            if k == "contactEnded":
                open_contacts.remove(c)
            else:
                s = dict(row)
                s["remaining"] = row.get("pending", {}).get("expect", 0)
                contacts[c].samples[c].append(s)
                if "pending" in row:
                    pending[c, row["pending"]["update"]] = contacts[c], s
        elif k == "correction":
            key = (row.get("contact", -1), row["update"])
            if key not in pending:
                unmatched += 1
                target = contacts.get(row.get("contact", -1), active)
                if target is not None:
                    target.unmatched += 1
            else:
                target, s = pending[key]
                target.corrections += 1
                s.update({key: row[key] for key in ("x", "y", *_CHANNELS) if key in row})
                s["remaining"] = row["expect"]
        elif k == "hover" and active is not None:
            if not active.hover_open:
                active.hover.append([])
                active.hover_open = True
            active.hover[-1].append(row)
        elif k == "finger" and active is not None:
            active.fingers += row["phase"] == "began"
        elif k == "trialEnded":
            if active is None or open_contacts or row["trial"] != active.start["trial"]["index"]:
                raise JournalError("I6", i, "The trial cannot end with this nesting.")
            active.outcome = OUTCOME_OF_REASON[row["reason"]]
            active.end = row["t"]
            active, state = None, "ready"
        elif k == "mark":
            target = next((t for t in trials if t.start["trial"]["index"] == row["trial"]), None)
            if target is None:
                raise JournalError("I6", i, "The mark refers to an unknown trial.")
            target.marks.append(row["code"])
        elif k == "recovered":
            expected = active.start["trial"]["index"] if active else None
            if row.get("interrupted") != expected:
                raise JournalError("I6", i, "The interrupted trial does not match the open trial.")
            if active:
                active.outcome = "interrupted"
            active, state = None, "suspended"
            open_contacts.clear()
            truncated += row["truncatedBytes"]
        elif k == "sessionClosed":
            if active is not None or open_contacts:
                raise JournalError("I6", i, "An open trial prevents session closure.")
            if row["counts"] != {kind: n for kind, n in counts.items() if kind != "sessionClosed"}:
                raise JournalError("I4", i, "The closing counts differ from the recount.")
            state = "closed"
        counts[k] += 1
    return _Fold(rows, trials, state, counts, unmatched, truncated)


def summarize(lines: Lines) -> Line:
    """Return the shared fold summary. An open session is allowed."""
    return _run(lines).summary()


def _recording(result: _Fold) -> Recording:
    head = result.lines[0]
    known = head["scale"]["kind"] != "unknown"
    scale = head["scale"]["mmPerPoint"] if known else 1.0
    device = Device(
        head["device"]["model"],
        head["input"],
        frozenset(Channel(k) for k, v in head["channels"].items() if v),
        spatial_unit="mm" if known else "source",
    )
    trials: list[Trial] = []
    for t in result.trials:
        origin = t.start["stimulusOrigin"]
        strokes: list[Stroke] = []
        groups = [(c, ss, PenState.CONTACT) for c, ss in t.samples.items()]
        groups += [(None, ss, PenState.IN_AIR) for ss in t.hover]
        groups.sort(key=lambda group: group[1][0]["seq"] if group[1] else t.start["seq"])
        for contact, ss, state in groups:
            xy = np.array([[(s["x"] - origin["x"]) * scale, -(s["y"] - origin["y"]) * scale] for s in ss])
            channels = {
                Channel(k): np.array([s.get(k, np.nan) for s in ss], dtype=float)
                for k in _CHANNELS
                if any(k in s for s in ss)
            }
            if state == PenState.IN_AIR:
                channels[Channel.HOVER] = np.array([s["z"] for s in ss], dtype=float)
            strokes.append(
                Stroke(
                    np.array([s["t"] for s in ss], dtype=float),
                    xy.reshape(-1, 2),
                    state,
                    channels,
                    np.array([s.get("remaining", 0) for s in ss], dtype=np.int64),
                    t.boot,
                    contact,
                    t.forces.get(contact) if contact is not None else None,
                )
            )
        observed = frozenset(k for s in strokes for k, v in s.channels.items() if np.isfinite(v).any())
        spec = t.start["spec"]
        stimulus = spec["stimulus"]
        paths = (
            tuple(np.asarray(p, dtype=float) * [1, -1] for p in stimulus["paths"])
            if (stimulus and stimulus["kind"] == "paths")
            else ()
        )
        trials.append(
            Trial(
                t.start["trial"]["index"],
                TaskRef(spec["id"], spec["mode"], paths),
                tuple(strokes),
                replace(device, observed_channels=observed),
                t.start["trial"]["attempt"],
                TrialOutcome(t.outcome),
                t.start["t"],
                t.end,
                t.boot,
                t.fingers,
                t.unmatched,
                tuple(t.marks),
            )
        )
    device = replace(
        device, observed_channels=frozenset(k for t in trials for k in t.device.observed_channels)
    )
    return Recording(
        head["sessionID"], head["participant"], device, tuple(trials), unmatched_corrections=result.unmatched
    )


def fold(lines: Lines) -> Recording:
    """Apply corrections and convert all positions to the library frame."""
    raw = list(lines)
    data = b"".join(
        encode_line(row) if isinstance(row, dict) else (row.encode("utf-8") if isinstance(row, str) else row)
        for row in raw
    )
    return replace(_recording(_run(raw)), source_hashes=(hashlib.sha256(data).hexdigest(),))


def read_journal(path: str | Path) -> Recording:
    """Read a closed export folder and verify its manifest."""
    folder = Path(path)
    try:
        data = (folder / "journal.jsonl").read_bytes()
    except OSError as e:
        raise JournalError("I1", 0, "Cannot read journal.jsonl.") from e
    result = _run(data.splitlines(keepends=True), closed=True)
    try:
        manifest = json.loads((folder / "manifest.json").read_bytes())
        expected = {
            "bytes": len(data),
            "lines": data.count(b"\n"),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        valid = (
            manifest["format"] == "script.journal"
            and str(manifest["version"]).split(".")[0] == "1"
            and manifest["sessionID"] == result.lines[0]["sessionID"]
            and manifest["journal"] == expected
        )
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise JournalError("I5", 0, "Invalid or absent manifest.") from e
    if not valid:
        raise JournalError("I5", 0, "The manifest does not match journal.jsonl.")
    return replace(_recording(result), source_hashes=(expected["sha256"],))
