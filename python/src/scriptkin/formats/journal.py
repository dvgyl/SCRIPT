"""Validate and fold the append-only SCRIPT journal (docs/DESIGN.md section 3)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, cast

import numpy as np

from scriptkin import bench
from scriptkin.contract import KINDS, OUTCOME_OF_REASON, READABLE_MAJORS, encode_line
from scriptkin.formats import FormatError
from scriptkin.formats.checks import (
    Check,
    any_of,
    array,
    between,
    boolean,
    integer,
    matches,
    nonempty,
    number,
    one_of,
    point,
    positive,
    record,
    sized,
    text,
    uptime,
)
from scriptkin.recording import (
    BenchContext,
    BenchRunContext,
    BlockOutcome,
    Channel,
    Consent,
    Device,
    DocumentRef,
    EndReason,
    Ethics,
    LocalizedRef,
    ModelTableScale,
    ParticipantContext,
    PenState,
    QualificationRef,
    QuestionnaireBlock,
    Recording,
    ReferenceScale,
    Response,
    ScaleSource,
    SessionContext,
    Stroke,
    Study,
    Surface,
    TabletContext,
    TabletScale,
    TaskRef,
    Trial,
    TrialContext,
    TrialOutcome,
    UnknownScale,
    Visit,
    reader_profile,
)


class JournalError(FormatError):
    def __init__(self, rule: str, line: int, detail: str):
        self.rule, self.line = rule, line
        super().__init__(f"{rule}, line {line}: {detail}")


Line = dict[str, Any]
Lines = Iterable[Line | str | bytes]
_PENCIL_CHANNELS = ("force", "altitude", "azimuth", "roll")
_TABLET_CHANNELS = ("tiltX", "tiltY", "rotation")
_CHANNELS = (*_PENCIL_CHANNELS, *_TABLET_CHANNELS)
QUESTIONNAIRE_KINDS = (
    "questionnaireStarted",
    "response",
    "questionnaireEnded",
    "safetyFlagged",
    "safetyShown",
    "safetyAction",
)
# The kinds that need a person. A bench session has none of them.
_PARTICIPANT_KINDS = ("handoff", *QUESTIONNAIRE_KINDS)
# App events use one uptime clock in one boot, so they must not go back in time. Touch times can.
_APP_CLOCK_KINDS = (
    "trialStarted",
    "trialBegan",
    "trialEnded",
    "questionnaireStarted",
    "questionnaireEnded",
    "recovered",
    "sessionClosed",
)


_CONTACT = integer(1)
_BITS = integer(0, 31)
_INDEX = integer(0)
_UUID = matches("[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
_CODE = matches("[A-Za-z0-9-]{1,32}")
_ID = matches("[a-z0-9-]+")
_LOCALE = matches("[a-z]{2,3}(-[A-Za-z0-9]{2,8})*")
_LOCALIZED = record({"id": text, "version": text, "locale": _LOCALE})
_INSTRUMENT = record({"id": _ID, "version": text, "locale": _LOCALE})
_SHA256 = matches("[0-9a-f]{64}")
_PENCIL_VALUES = {k: uptime if k == "force" else number for k in _PENCIL_CHANNELS}
# A tablet tilt component is the normalised tilt of AppKit, -1 to 1.
_TABLET_VALUES = {"tiltX": between(-1, 1), "tiltY": between(-1, 1), "rotation": number}
_SCALE = any_of(
    record({"kind": one_of("modelTable"), "mmPerPoint": positive, "ppi": positive, "nativeScale": positive}),
    record(
        {
            "kind": one_of("reference"),
            "object": one_of("steelRule", "idCard"),
            "mmPerPoint": positive,
            "lengthMM": positive,
            "drawnPoints": positive,
        }
    ),
    record({"kind": one_of("tablet"), "linesPerMM": positive, "mmPerPoint": positive}),
    record({"kind": one_of("unknown")}),
)
_TABLET = record(
    {
        "model": nonempty,
        "asset": matches("[A-Za-z0-9-]{1,32}"),
        "driver": nonempty,
        "pen": record({"declared": one_of("proPen3", "unknown")}),
    }
)
_STIMULUS = any_of(
    lambda v: v is None,
    record(
        {
            "kind": one_of("paths"),
            "frameMM": point,
            "strokeMM": positive,
            "paths": array(array(point, minimum=2), minimum=1),
        },
        {"start": point},
    ),
    record({"kind": one_of("text"), "frameMM": point, "text": nonempty, "fontSizeMM": positive}),
)
_SPEC = record(
    {
        "id": _ID,
        "mode": one_of("trace", "copy", "free"),
        "instruction": nonempty,
        "stimulus": _STIMULUS,
        "completion": any_of(
            record({"kind": one_of("participantDone"), "maxSeconds": positive}),
            record({"kind": one_of("timed"), "seconds": positive}),
        ),
    }
)
_ANSWER = {"t": uptime, "block": _INDEX, "item": _ID, "skipped": boolean}

# Required and optional payload fields of each line kind. Unknown keys pass: a minor version adds keys.
# `sessionOpened` and `trialStarted` also have the keys of their subject shape, in `_SUBJECT_LINES`.
_LINES: dict[str, tuple[dict[str, Check], dict[str, Check]]] = {
    "sessionOpened": (
        {
            "format": text,
            "purpose": one_of("study", "qualification", "bench"),
            "sessionID": _UUID,
            "openedAt": text,
            "openedUptime": uptime,
            "boot": _UUID,
            "app": record({"version": text, "build": text, "sourceRevision": nonempty}),
            "device": record({"model": text, "os": text, "nativeScale": positive, "asset": _CODE}),
            "surface": record(
                {
                    "protector": one_of("none", "glossy", "matte", "paperlike", "unknown"),
                    "nib": one_of("new", "used", "unknown"),
                }
            ),
            "scale": _SCALE,
            "pencil": record({"declared": one_of("pro", "second", "usbC", "none")}),
            "channels": record(
                {k: boolean for k in (*_PENCIL_CHANNELS, "hover")}, {k: boolean for k in _TABLET_CHANNELS}
            ),
            "input": one_of("pencil", "directTouchAsPen", "tablet"),
            "battery": record({"id": text, "version": text, "sha256": _SHA256}),
        },
        {"qualification": record({"sessionID": _UUID, "at": text}), "tablet": _TABLET},
    ),
    "handoff": ({"t": uptime, "to": one_of("participant", "operator")}, {}),
    "trialStarted": (
        {
            "t": uptime,
            "trial": record({"index": _INDEX, "task": _ID, "attempt": integer(1)}),
            "spec": _SPEC,
            "practice": boolean,
            "position": integer(1),
            "view": record({"width": positive, "height": positive}),
            "stimulusOrigin": record({"x": number, "y": number}),
        },
        {},
    ),
    "trialBegan": ({"t": uptime, "trial": _INDEX}, {}),
    "contactBegan": ({"t": uptime, "contact": _CONTACT}, {"maxForce": positive}),
    "sample": (
        {
            "contact": _CONTACT,
            "t": uptime,
            "x": number,
            "y": number,
            "phase": one_of("began", "moved", "stationary", "ended", "cancelled"),
            "est": _BITS,
        },
        {
            **_PENCIL_VALUES,
            **_TABLET_VALUES,
            "pending": record({"expect": integer(1, 31), "update": integer()}),
        },
    ),
    # A correction updates the estimated Pencil values only. A tablet sample has no estimate.
    "correction": (
        {"update": integer(), "arrived": uptime, "expect": _BITS},
        {"contact": _CONTACT, "x": number, "y": number, **_PENCIL_VALUES},
    ),
    "contactEnded": ({"t": uptime, "contact": _CONTACT, "cancelled": boolean}, {}),
    "hover": (
        {"t": uptime, "clock": one_of("callback", "event"), "x": number, "y": number},
        {"z": uptime, **{k: number for k in ("altitude", "azimuth", "roll")}, **_TABLET_VALUES},
    ),
    "finger": ({"t": uptime, "phase": one_of("began", "ended", "cancelled"), "x": number, "y": number}, {}),
    "trialEnded": ({"t": uptime, "trial": _INDEX, "reason": one_of(*OUTCOME_OF_REASON)}, {}),
    "questionnaireStarted": ({"t": uptime, "block": _INDEX, "instrument": _INSTRUMENT}, {}),
    "response": (_ANSWER, {"value": integer()}),
    "questionnaireEnded": (
        {"t": uptime, "block": _INDEX, "reason": one_of("completed", "declined", "interrupted")},
        {},
    ),
    "safetyFlagged": (_ANSWER, {"value": integer()}),
    "safetyShown": ({"t": uptime, "what": one_of("crisisList", "operatorAlert")}, {"listVersion": text}),
    "safetyAction": ({"t": uptime, "code": _CODE, "role": one_of("operator")}, {}),
    "mark": (
        {
            "t": uptime,
            "trial": _INDEX,
            "code": one_of("repeatRequested", "disturbance", "deviceProblem", "exclude"),
        },
        {},
    ),
    "recovered": (
        {"t": uptime, "at": text, "boot": _UUID, "truncatedBytes": integer(0)},
        {"interrupted": _INDEX, "interruptedBlock": _INDEX},
    ),
    "sessionClosed": ({"t": uptime, "counts": record({k: integer(0) for k in KINDS[:-1]})}, {}),
}

# The required keys of each subject shape. The purpose selects one shape, and the keys of the other
# shape are refused. A study or qualification session has a participant, and a bench session has none.
_PARTICIPANT_SESSION: dict[str, Check] = {
    "participant": matches("[A-Z0-9-]{3,16}"),
    "administration": one_of("supervised", "self"),
    "study": record(
        {
            "id": _CODE,
            "site": _CODE,
            "configID": _CODE,
            "configVersion": text,
            "configSHA256": _SHA256,
            "protocolVersion": text,
        }
    ),
    "visit": record({"id": _CODE, "number": integer(1)}),
    "ageBand": one_of("13-17", "18-24"),
    "jurisdiction": matches("[A-Z]{2}"),
    "locale": _LOCALE,
    "consent": record(
        {
            "model": one_of("adult", "guardianAndAssent", "waiver"),
            "attestedBy": record({"code": _CODE, "role": one_of("operator", "enrolment")}),
            "attestedAt": text,
            "documents": array(record({"id": nonempty, "version": nonempty})),
        },
        {"waiverReference": text},
    ),
    "ethics": record(
        {"approvalID": text, "protocolVersion": text, "safetyProtocolVersion": text},
        {"monitoringDisclosureVersion": text},
    ),
}
_BENCH_SESSION: dict[str, Check] = {
    "bench": record(
        {
            "role": one_of("P", "W"),
            "mounting": one_of(*bench.MOUNTINGS),
            "motion": record({"id": nonempty, "version": nonempty}),
            "protocol": record({"version": nonempty, "sha256": _SHA256}),
        }
    ),
}
_PARTICIPANT_TRIAL: dict[str, Check] = {
    "hand": one_of("left", "right", "unknown"),
    "instruction": _LOCALIZED,
    "accommodations": array(
        one_of("readAloud", "largerText", "extraTime", "positionAssist", "other"), unique=True
    ),
}
_BENCH_TRIAL: dict[str, Check] = {
    "bench": record(
        {
            "program": matches("[a-z0-9-]{1,32}"),
            "run": integer(min(bench.RUNS), max(bench.RUNS)),
            "trajectorySHA256": _SHA256,
        },
        {"clip": sized(1, 64)},
    ),
}
_SUBJECT_LINES: dict[str, dict[str, dict[str, Check]]] = {
    "sessionOpened": {"participant": _PARTICIPANT_SESSION, "bench": _BENCH_SESSION},
    "trialStarted": {"participant": _PARTICIPANT_TRIAL, "bench": _BENCH_TRIAL},
}


def _shape(line: Line, i: int, purpose: str) -> None:
    """Check the payload of one line. `purpose` is the session purpose, which selects the subject shape."""
    kind = line["kind"]
    required, optional = _LINES[kind]
    subject = "bench" if purpose == "bench" else "participant"
    shapes = _SUBJECT_LINES.get(kind, {})
    required = {**required, **shapes.get(subject, {})}
    for k, check in required.items():
        if k not in line:
            raise JournalError("I1", i, f"{k} is absent.")
        if not check(line[k]):
            raise JournalError("I1", i, f"{k} is invalid.")
    for k, check in optional.items():
        if k in line and not check(line[k]):
            raise JournalError("I1", i, f"{k} is invalid.")
    for other, keys in shapes.items():
        if other == subject:
            continue
        for k in keys:
            if k in line:
                raise JournalError("I1", i, f"{k} is not a key of a {subject} {kind}.")
    if kind == "sessionOpened" and (line["input"] == "tablet") != ("tablet" in line):
        raise JournalError("I1", i, "tablet is present exactly when the input is a tablet.")
    if kind == "trialStarted" and line["trial"]["task"] != line["spec"]["id"]:
        raise JournalError("I1", i, "The task reference must match the task specification.")
    if kind in ("response", "safetyFlagged") and line["skipped"] == ("value" in line):
        raise JournalError("I1", i, "A skipped item has no value, and an answered item has one.")


_LONG_DIGITS = re.compile(rb"[0-9]{19}")


def _invalid_constant(value: str) -> Any:
    raise ValueError("Invalid JSON constant.")


def _valid_integers(value: Any) -> bool:
    if type(value) is int:
        return -(2**63) <= value < 2**63
    if isinstance(value, dict):
        return all(_valid_integers(v) for v in value.values())
    if isinstance(value, list):
        return all(_valid_integers(v) for v in value)
    return True


def _decode(lines: Lines) -> list[Line]:
    decoded: list[Line] = []
    purpose = ""
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
                if isinstance(raw, bytes) and raw.startswith(b"\xef\xbb\xbf"):
                    raise ValueError("BOM")
                line = json.loads(raw, parse_constant=_invalid_constant)
                # Only a `\u` escape can give a lone surrogate, which does not encode to UTF-8.
                escaped = b"\\u" in raw if isinstance(raw, bytes) else "\\u" in raw
                if escaped:
                    json.dumps(line, ensure_ascii=False).encode("utf-8")
            except (ValueError, UnicodeError) as e:
                raise JournalError("I1", i, "Invalid UTF-8 JSON.") from e
        if not isinstance(line, dict) or line.get("kind") not in KINDS:
            raise JournalError("I1", i, "Expected an object with a known kind.")
        # Only a number of 19 digits or more can leave the signed 64-bit range, so a line of bytes without one
        # skips the walk.
        walk = not isinstance(raw, bytes) or _LONG_DIGITS.search(raw) is not None
        if walk and not _valid_integers(line):
            raise JournalError("I1", i, "Integer outside the signed 64-bit range.")
        row = cast(Line, line)
        if type(row.get("seq")) is int and not integer(0)(row["seq"]):
            raise JournalError("I1", i, "seq is invalid.")
        if type(row.get("seq")) is not int or row["seq"] != i:
            raise JournalError("I2", i, "seq must equal the zero-based line index.")
        if i == 0:
            if row["kind"] != "sessionOpened":
                raise JournalError("I3", 0, "Line 0 must open the session.")
            purpose = row.get("purpose", "")
        _shape(row, i, purpose)
        decoded.append(row)
    return decoded


@dataclass
class _Trial:
    start: Line
    boot: str
    began: float | None = None
    samples: dict[int, list[Line]] = field(default_factory=dict)
    hover: list[list[Line]] = field(default_factory=list)
    forces: dict[int, float] = field(default_factory=dict)
    outcome: str = "open"
    reason: str | None = None
    end: float | None = None
    corrections: int = 0
    fingers: int = 0
    unmatched: int = 0
    marks: list[str] = field(default_factory=list)
    hover_open: bool = False

    @property
    def index(self) -> int:
        return self.start["trial"]["index"]


@dataclass
class _Block:
    start: Line
    boot: str
    responses: list[Line] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    answered: int = 0
    skipped: int = 0
    outcome: str = "open"
    end: float | None = None

    @property
    def number(self) -> int:
        return self.start["block"]


class FoldState:
    """The session state machine of docs/DESIGN.md section 5, applied one line at a time.

    States: ready, drawing (open trial), answering (open block), suspended (after recovery), closed.
    """

    def __init__(self, head: Line):
        self.head = head
        self.bench = head["purpose"] == "bench"
        self.state = "ready"
        self.counts: dict[str, int] = dict.fromkeys(KINDS, 0)
        self.counts["sessionOpened"] = 1
        self.trials: list[_Trial] = []
        self.blocks: list[_Block] = []
        self.trial: _Trial | None = None
        self.block: _Block | None = None
        self.contacts: dict[int, _Trial] = {}
        self.open_contacts: set[int] = set()
        # Samples can share a pending key. A correction with that key resolves all of them.
        self.pending: dict[tuple[int, int], tuple[_Trial, list[Line]]] = {}
        self.boot: str = head["boot"]
        self.clocks: dict[str, float] = {self.boot: float(head["openedUptime"])}
        self.unmatched = 0
        self.truncated = 0
        self.safety = {"flags": 0, "crisisListShown": 0, "operatorAlerts": 0, "actions": 0}
        self.handlers: dict[str, Callable[[int, Line], None]] = {
            "sessionOpened": self._reopened,
            "handoff": self._ignore,
            "trialStarted": self._trial_started,
            "trialBegan": self._trial_began,
            "contactBegan": self._contact_began,
            "sample": self._sample,
            "correction": self._correction,
            "contactEnded": self._contact_ended,
            "hover": self._hover,
            "finger": self._finger,
            "trialEnded": self._trial_ended,
            "questionnaireStarted": self._questionnaire_started,
            "response": self._response,
            "questionnaireEnded": self._questionnaire_ended,
            "safetyFlagged": self._safety_flagged,
            "safetyShown": self._safety_shown,
            "safetyAction": self._safety_action,
            "mark": self._mark,
            "recovered": self._recovered,
            "sessionClosed": self._session_closed,
        }

    def apply(self, i: int, row: Line) -> None:
        """Check nesting (I6) before clocks (I7), so a misplaced line reports its nesting error."""
        kind = row["kind"]
        if self.state == "closed":
            raise JournalError("I6", i, "The session is closed.")
        if self.bench and kind in _PARTICIPANT_KINDS:
            raise JournalError("I6", i, "A bench session has no questionnaire, safety, or handoff line.")
        self.handlers[kind](i, row)
        if kind in _APP_CLOCK_KINDS:
            now = float(row["t"])
            if now < self.clocks.get(self.boot, 0):
                raise JournalError("I7", i, "App uptime goes back in time inside one boot.")
            self.clocks[self.boot] = now
        self.counts[kind] += 1

    def _reopened(self, i: int, row: Line) -> None:
        raise JournalError("I6", i, "Only line 0 opens the session.")

    def _ignore(self, i: int, row: Line) -> None:
        pass

    def _open_trial(self, i: int, index: int, what: str) -> _Trial:
        if self.trial is None or self.trial.index != index:
            raise JournalError("I6", i, f"{what} needs its trial to be open.")
        return self.trial

    def _open_block(self, i: int, number: int, what: str) -> _Block:
        if self.block is None or self.block.number != number:
            raise JournalError("I6", i, f"{what} needs its questionnaire block to be open.")
        return self.block

    def _trial_started(self, i: int, row: Line) -> None:
        index = row["trial"]["index"]
        if self.state not in ("ready", "suspended") or index != len(self.trials):
            raise JournalError("I6", i, "Trials and blocks overlap, or the trial index is not next.")
        self.trial = _Trial(row, self.boot)
        self.trials.append(self.trial)
        self.state = "drawing"

    def _trial_began(self, i: int, row: Line) -> None:
        trial = self._open_trial(i, row["trial"], "trialBegan")
        if trial.began is not None:
            raise JournalError("I6", i, "The trial already began.")
        trial.began = float(row["t"])

    def _contact_began(self, i: int, row: Line) -> None:
        c = row["contact"]
        if self.trial is None or c in self.contacts:
            raise JournalError("I6", i, "A contact needs an open trial and a unique ID.")
        self.trial.hover_open = False
        self.contacts[c] = self.trial
        self.open_contacts.add(c)
        self.trial.samples[c] = []
        if "maxForce" in row:
            self.trial.forces[c] = row["maxForce"]

    def _sample(self, i: int, row: Line) -> None:
        c = row["contact"]
        if c not in self.open_contacts:
            raise JournalError("I6", i, "The contact is not open.")
        s = dict(row)
        s["remaining"] = row.get("pending", {}).get("expect", 0)
        self.contacts[c].samples[c].append(s)
        if "pending" in row:
            self.pending.setdefault((c, row["pending"]["update"]), (self.contacts[c], []))[1].append(s)

    def _contact_ended(self, i: int, row: Line) -> None:
        if row["contact"] not in self.open_contacts:
            raise JournalError("I6", i, "The contact is not open.")
        self.open_contacts.remove(row["contact"])

    def _correction(self, i: int, row: Line) -> None:
        key = (row.get("contact", -1), row["update"])
        if key not in self.pending:
            self.unmatched += 1
            target = self.contacts.get(row.get("contact", -1), self.trial)
            if target is not None:
                target.unmatched += 1
            return
        target, samples = self.pending[key]
        target.corrections += 1
        for s in samples:
            s.update({name: row[name] for name in ("x", "y", *_PENCIL_CHANNELS) if name in row})
            s["remaining"] = row["expect"]

    def _hover(self, i: int, row: Line) -> None:
        if self.trial is None:
            return
        if not self.trial.hover_open:
            self.trial.hover.append([])
            self.trial.hover_open = True
        self.trial.hover[-1].append(row)

    def _finger(self, i: int, row: Line) -> None:
        if self.trial is not None:
            self.trial.fingers += row["phase"] == "began"

    def _trial_ended(self, i: int, row: Line) -> None:
        trial = self._open_trial(i, row["trial"], "trialEnded")
        if self.open_contacts:
            raise JournalError("I6", i, "A trial cannot end with an open contact.")
        if self.bench and row["reason"] == "timeLimit":
            raise JournalError("I6", i, "A bench run has no time limit, so it cannot end with timeLimit.")
        trial.outcome = OUTCOME_OF_REASON[row["reason"]]
        trial.reason = row["reason"]
        trial.end = row["t"]
        self.trial, self.state = None, "ready"

    def _questionnaire_started(self, i: int, row: Line) -> None:
        number = row["block"]
        if self.state not in ("ready", "suspended") or number != len(self.blocks):
            raise JournalError("I6", i, "Trials and blocks overlap, or the block index is not next.")
        self.block = _Block(row, self.boot)
        self.blocks.append(self.block)
        self.state = "answering"

    def _response(self, i: int, row: Line) -> None:
        block = self._open_block(i, row["block"], "A response")
        block.responses.append(row)
        if row["skipped"]:
            block.skipped += 1
        else:
            block.answered += 1

    def _questionnaire_ended(self, i: int, row: Line) -> None:
        block = self._open_block(i, row["block"], "questionnaireEnded")
        block.outcome = row["reason"]
        block.end = row["t"]
        self.block, self.state = None, "ready"

    def _safety_flagged(self, i: int, row: Line) -> None:
        self._open_block(i, row["block"], "A safety flag").flags.append(row["item"])
        self.safety["flags"] += 1

    def _safety_shown(self, i: int, row: Line) -> None:
        self.safety["crisisListShown" if row["what"] == "crisisList" else "operatorAlerts"] += 1

    def _safety_action(self, i: int, row: Line) -> None:
        self.safety["actions"] += 1

    def _mark(self, i: int, row: Line) -> None:
        target = next((t for t in self.trials if t.index == row["trial"]), None)
        if target is None:
            raise JournalError("I6", i, "The mark refers to an unknown trial.")
        target.marks.append(row["code"])

    def _recovered(self, i: int, row: Line) -> None:
        trial = self.trial.index if self.trial else None
        block = self.block.number if self.block else None
        if row.get("interrupted") != trial or row.get("interruptedBlock") != block:
            raise JournalError("I6", i, "The interrupted trial or block does not match the open one.")
        if self.trial:
            self.trial.outcome = "interrupted"
        if self.block:
            self.block.outcome = "interrupted"
        self.trial, self.block, self.state = None, None, "suspended"
        self.open_contacts.clear()
        self.truncated += row["truncatedBytes"]
        self.boot = row["boot"]

    def _session_closed(self, i: int, row: Line) -> None:
        if self.state not in ("ready", "suspended") or self.open_contacts:
            raise JournalError("I6", i, "An open trial or block prevents session closure.")
        if row["counts"] != {kind: n for kind, n in self.counts.items() if kind != "sessionClosed"}:
            raise JournalError("I4", i, "The closing counts differ from the recount.")
        self.state = "closed"

    def summary(self) -> Line:
        summary: Line = {"sessionID": self.head["sessionID"], "purpose": self.head["purpose"]}
        if "administration" in self.head:
            summary["administration"] = self.head["administration"]
        summary.update(
            state=self.state,
            counts=dict(self.counts),
            trials=[
                {
                    "index": t.index,
                    "task": t.start["trial"]["task"],
                    "attempt": t.start["trial"]["attempt"],
                    "practice": t.start["practice"],
                    "began": t.began is not None,
                    "outcome": t.outcome,
                    "contacts": len(t.samples),
                    "samples": sum(map(len, t.samples.values())),
                    "corrections": t.corrections,
                    "unresolved": sum(bool(s["remaining"]) for ss in t.samples.values() for s in ss),
                }
                for t in self.trials
            ],
            questionnaires=[
                {
                    "block": b.number,
                    "instrument": b.start["instrument"]["id"],
                    "answered": b.answered,
                    "skipped": b.skipped,
                    "outcome": b.outcome,
                }
                for b in self.blocks
            ],
            safety=dict(self.safety),
            unmatchedCorrections=self.unmatched,
            truncatedBytes=self.truncated,
        )
        return summary


def replay(lines: Lines, *, closed: bool = False) -> tuple[list[Line], FoldState]:
    """Check rules I1 to I4, I6, and I7, and fold every line. `closed` requires a closed session."""
    rows = _decode(lines)
    if not rows:
        raise JournalError("I3", 0, "Line 0 must open the session.")
    if str(rows[0]["format"]).split(".")[0] not in READABLE_MAJORS:
        raise JournalError("I3", 0, "Unsupported major version.")
    if closed and rows[-1]["kind"] != "sessionClosed":
        raise JournalError("I3", len(rows) - 1, "An export must end with sessionClosed.")
    state = FoldState(rows[0])
    for i, row in enumerate(rows[1:], start=1):
        state.apply(i, row)
    return rows, state


def summarize(lines: Lines) -> Line:
    """Return the shared fold summary. An open session is allowed."""
    return replay(lines)[1].summary()


def _scale(scale: Line) -> ScaleSource:
    if scale["kind"] == "modelTable":
        return ModelTableScale(scale["mmPerPoint"], scale["ppi"], scale["nativeScale"])
    if scale["kind"] == "reference":
        return ReferenceScale(scale["object"], scale["mmPerPoint"], scale["lengthMM"], scale["drawnPoints"])
    if scale["kind"] == "tablet":
        return TabletScale(scale["linesPerMM"], scale["mmPerPoint"])
    return UnknownScale()


def _subject(head: Line) -> ParticipantContext | BenchContext:
    if head["purpose"] == "bench":
        bench = head["bench"]
        return BenchContext(
            bench["role"],
            bench["mounting"],
            DocumentRef(bench["motion"]["id"], bench["motion"]["version"]),
            bench["protocol"]["version"],
            bench["protocol"]["sha256"],
        )
    study, consent, ethics = head["study"], head["consent"], head["ethics"]
    return ParticipantContext(
        administration=head["administration"],
        study=Study(
            study["id"],
            study["site"],
            study["configID"],
            study["configVersion"],
            study["configSHA256"],
            study["protocolVersion"],
        ),
        visit=Visit(head["visit"]["id"], head["visit"]["number"]),
        age_band=head["ageBand"],
        jurisdiction=head["jurisdiction"],
        locale=head["locale"],
        consent=Consent(
            consent["model"],
            consent["attestedBy"]["code"],
            consent["attestedBy"]["role"],
            consent["attestedAt"],
            tuple(DocumentRef(d["id"], d["version"]) for d in consent["documents"]),
            consent.get("waiverReference"),
        ),
        ethics=Ethics(
            ethics["approvalID"],
            ethics["protocolVersion"],
            ethics["safetyProtocolVersion"],
            ethics.get("monitoringDisclosureVersion"),
        ),
    )


def _context(head: Line) -> SessionContext:
    qualification = head.get("qualification")
    tablet = head.get("tablet")
    return SessionContext(
        purpose=head["purpose"],
        subject=_subject(head),
        device_asset=head["device"]["asset"],
        surface=Surface(head["surface"]["protector"], head["surface"]["nib"]),
        scale=_scale(head["scale"]),
        opened_at=head["openedAt"],
        qualification=QualificationRef(qualification["sessionID"], qualification["at"])
        if qualification
        else None,
        tablet=TabletContext(tablet["model"], tablet["asset"], tablet["driver"], tablet["pen"]["declared"])
        if tablet
        else None,
    )


def _localized(ref: Line) -> LocalizedRef:
    return LocalizedRef(ref["id"], ref["version"], ref["locale"])


def _trial(t: _Trial, device: Device, scale: float) -> Trial:
    origin = t.start["stimulusOrigin"]
    strokes: list[Stroke] = []
    groups = [(c, ss, PenState.CONTACT) for c, ss in t.samples.items()]
    groups += [(None, ss, PenState.IN_AIR) for ss in t.hover]
    groups.sort(key=lambda group: group[1][0]["seq"] if group[1] else t.start["seq"])
    for contact, ss, state in groups:
        xy = np.array([[(s["x"] - origin["x"]) * scale, -(s["y"] - origin["y"]) * scale] for s in ss])
        keys = {k: Channel(k) for k in _CHANNELS}
        if state == PenState.IN_AIR:
            keys["z"] = Channel.HOVER
        channels = {
            channel: np.array([s.get(k, np.nan) for s in ss], dtype=float)
            for k, channel in keys.items()
            if any(k in s for s in ss)
        }
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
    # A planned hover channel is observed when the pen moved in the air, with or without a height.
    if any(s.state == PenState.IN_AIR for s in strokes):
        observed |= {Channel.HOVER}
    start = t.start
    spec = start["spec"]
    stimulus = spec["stimulus"]
    paths = (
        tuple(np.asarray(p, dtype=float) * [1, -1] for p in stimulus["paths"])
        if (stimulus and stimulus["kind"] == "paths")
        else ()
    )
    context: TrialContext | BenchRunContext
    if "bench" in start:
        run = start["bench"]
        context = BenchRunContext(
            start["practice"],
            start["position"],
            run["program"],
            run["run"],
            run["trajectorySHA256"],
            run.get("clip"),
        )
    else:
        context = TrialContext(
            start["practice"],
            start["position"],
            start["hand"],
            _localized(start["instruction"]),
            tuple(start["accommodations"]),
        )
    return Trial(
        t.index,
        TaskRef(spec["id"], spec["mode"], paths),
        tuple(strokes),
        replace(device, observed_channels=observed),
        start["trial"]["attempt"],
        TrialOutcome(t.outcome),
        start["t"],
        t.end,
        t.boot,
        t.fingers,
        t.unmatched,
        tuple(t.marks),
        context,
        t.began,
        EndReason(t.reason) if t.reason else None,
    )


def _block(b: _Block) -> QuestionnaireBlock:
    return QuestionnaireBlock(
        b.number,
        _localized(b.start["instrument"]),
        b.start["t"],
        b.end,
        BlockOutcome(b.outcome),
        b.boot,
        tuple(Response(r["item"], r.get("value"), r["t"]) for r in b.responses),
        tuple(b.flags),
    )


def recording(state: FoldState, source_hash: str) -> Recording:
    head = state.head
    known = head["scale"]["kind"] != "unknown"
    # The device that samples the pen: the tablet when the input is a tablet, else the host computer.
    sampler = head["tablet"] if head["input"] == "tablet" else head["device"]
    device = Device(
        sampler["model"],
        head["input"],
        frozenset(c for c in Channel if head["channels"].get(c.value, False)),
        spatial_unit="mm" if known else "source",
    )
    trials = tuple(_trial(t, device, head["scale"]["mmPerPoint"] if known else 1.0) for t in state.trials)
    return Recording(
        head["sessionID"],
        head.get("participant"),
        replace(device, observed_channels=frozenset(k for t in trials for k in t.device.observed_channels)),
        trials,
        reader_profile("script.journal"),
        (source_hash,),
        state.unmatched,
        _context(head),
        tuple(_block(b) for b in state.blocks),
    )


def fold(lines: Lines) -> Recording:
    """Apply corrections and convert all positions to the library frame. An open session is allowed."""
    raw = list(lines)
    data = b"".join(
        encode_line(row) if isinstance(row, dict) else (row.encode("utf-8") if isinstance(row, str) else row)
        for row in raw
    )
    return recording(replay(raw)[1], hashlib.sha256(data).hexdigest())
