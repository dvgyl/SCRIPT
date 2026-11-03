"""The shared contract: battery, instruments, demo configuration, journal fixtures, and doubles.

`regenerate` writes every generated file under contract/. `verify` regenerates into memory and
reports each file that differs from the checked-in bytes. Expected fold summaries come from the
builder's own record of what it wrote, never from a reader, so they are an independent oracle.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scriptkin import battery, instruments, studyconfig

FORMAT_VERSION = "1.0"
KINDS = (
    "sessionOpened",
    "handoff",
    "trialStarted",
    "trialBegan",
    "contactBegan",
    "sample",
    "correction",
    "contactEnded",
    "hover",
    "finger",
    "trialEnded",
    "questionnaireStarted",
    "response",
    "questionnaireEnded",
    "safetyFlagged",
    "safetyShown",
    "safetyAction",
    "mark",
    "recovered",
    "sessionClosed",
)
OUTCOME_OF_REASON = {
    "participantDone": "completed",
    "timeLimit": "timeLimit",
    "declined": "declined",
    "unable": "unable",
    "operatorAbort": "aborted",
    "backgrounded": "interrupted",
    "technicalFailure": "technicalFailure",
}
FORCE, AZIMUTH, ALTITUDE, LOCATION, ROLL = 1, 2, 4, 8, 16

ALL_CHANNELS = {"force": True, "altitude": True, "azimuth": True, "roll": True, "hover": True}
MODEL_TABLE_SCALE = {"kind": "modelTable", "mmPerPoint": 25.4 * 2 / 264, "ppi": 264.0, "nativeScale": 2.0}


def encode_line(line: dict[str, Any]) -> bytes:
    return (json.dumps(line, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()


def demo_config_sha256() -> str:
    return hashlib.sha256(studyconfig.config_bytes(studyconfig.demo_config())).hexdigest()


@dataclass
class _Trial:
    index: int
    task: str
    attempt: int
    practice: bool
    began: bool = False
    outcome: str = "open"
    contacts: int = 0
    samples: int = 0
    corrections: int = 0


@dataclass
class _Block:
    block: int
    instrument: str
    answered: int = 0
    skipped: int = 0
    outcome: str = "open"


@dataclass
class JournalBuilder:
    """Writes journal lines and records, by construction, the fold summary they must produce."""

    session_id: str
    participant: str
    channels: dict[str, bool] = field(default_factory=lambda: dict(ALL_CHANNELS))
    pencil: str = "pro"
    input: str = "pencil"
    scale: dict[str, Any] = field(default_factory=lambda: dict(MODEL_TABLE_SCALE))
    boot: str = "0B0B0B0B-0000-4000-8000-000000000001"
    opened_uptime: float = 1000.0
    purpose: str = "study"
    administration: str = "supervised"
    age_band: str = "18-24"
    jurisdiction: str = "US"
    visit: int = 1
    qualification: dict[str, str] | None = None

    lines: list[dict[str, Any]] = field(default_factory=list)
    _trials: list[_Trial] = field(default_factory=list)
    _blocks: list[_Block] = field(default_factory=list)
    _open_trial: int | None = None
    _open_block: int | None = None
    _open_contacts: set[int] = field(default_factory=set)
    _next_contact: int = 1
    _contact_trial: dict[int, int] = field(default_factory=dict)
    _pending: dict[tuple[int, int], tuple[int, list[int]]] = field(default_factory=dict)
    _unmatched: int = 0
    _truncated: int = 0
    _flags: int = 0
    _crisis_shown: int = 0
    _alerts: int = 0
    _actions: int = 0
    _state: str = "none"

    def _emit(self, kind: str, **payload: Any) -> dict[str, Any]:
        line: dict[str, Any] = {"seq": len(self.lines), "kind": kind}
        line.update({k: v for k, v in payload.items() if v is not None})
        self.lines.append(line)
        return line

    def _consent(self) -> dict[str, Any]:
        model = studyconfig.demo_config()["consentModel"][self.age_band]
        role = "operator" if self.administration == "supervised" else "enrolment"
        return {
            "model": model["model"],
            "attestedBy": {"code": "OP1", "role": role},
            "attestedAt": "2026-11-03T08:55:00Z",
            "documents": model["documents"],
        }

    def opened(self) -> JournalBuilder:
        self._emit(
            "sessionOpened",
            format=FORMAT_VERSION,
            purpose=self.purpose,
            sessionID=self.session_id,
            participant=self.participant,
            openedAt="2026-11-03T09:00:00Z",
            openedUptime=self.opened_uptime,
            boot=self.boot,
            app={"version": "1.0.0", "build": "1", "sourceRevision": "fixture"},
            device={"model": "iPad16,3", "os": "iPadOS 27.0", "nativeScale": 2.0, "asset": "IPAD-01"},
            surface={"protector": "none", "nib": "new"},
            qualification=self.qualification,
            scale=self.scale,
            pencil={"declared": self.pencil},
            channels=self.channels,
            input=self.input,
            administration=self.administration,
            study={
                "id": "DEMO",
                "site": "DEMO",
                "configID": "DEMO",
                "configVersion": "1.0",
                "configSHA256": demo_config_sha256(),
                "protocolVersion": "demo",
            },
            visit={"id": f"V{self.visit}", "number": self.visit},
            ageBand=self.age_band,
            jurisdiction=self.jurisdiction,
            locale="en",
            consent=self._consent(),
            ethics={
                "approvalID": "DEMO-NOT-APPROVED",
                "protocolVersion": "demo",
                "safetyProtocolVersion": "demo",
            },
            battery={
                "id": battery.BATTERY_ID,
                "version": battery.BATTERY_VERSION,
                "sha256": battery.battery_sha256(),
            },
        )
        self._state = "ready"
        return self

    def handoff(self, t: float, to: str) -> None:
        self._emit("handoff", t=t, to=to)

    def trial(
        self,
        task: str,
        attempt: int,
        t: float,
        *,
        practice: bool = False,
        position: int = 1,
        hand: str = "right",
        accommodations: list[str] | None = None,
    ) -> int:
        assert self._state in ("ready", "suspended") and self._open_trial is None
        index = len(self._trials)
        self._trials.append(_Trial(index, task, attempt, practice))
        self._emit(
            "trialStarted",
            t=t,
            trial={"index": index, "task": task, "attempt": attempt},
            spec=battery.task_spec(task),
            practice=practice,
            position=position,
            hand=hand,
            instruction={"id": task, "version": "1.0", "locale": "en"},
            accommodations=accommodations or [],
            view={"width": 834.0, "height": 1210.0},
            stimulusOrigin={"x": 147.5, "y": 200.0},
        )
        self._open_trial = index
        self._state = "drawing"
        return index

    def began(self, t: float) -> None:
        assert self._open_trial is not None
        self._emit("trialBegan", t=t, trial=self._open_trial)
        self._trials[self._open_trial].began = True

    def contact(self, t: float, max_force: float | None = 4.166666666666667) -> int:
        assert self._open_trial is not None
        contact = self._next_contact
        self._next_contact += 1
        self._open_contacts.add(contact)
        self._contact_trial[contact] = self._open_trial
        self._trials[self._open_trial].contacts += 1
        self._emit(
            "contactBegan", t=t, contact=contact, maxForce=max_force if self.channels["force"] else None
        )
        return contact

    def sample(
        self,
        contact: int,
        t: float,
        x: float,
        y: float,
        phase: str = "moved",
        *,
        force: float = 0.31,
        altitude: float = 1.05,
        azimuth: float = 2.2,
        roll: float = 0.4,
        est: int = 0,
        pending: tuple[int, int] | None = None,
    ) -> None:
        assert contact in self._open_contacts
        trial = self._contact_trial[contact]
        c = self.channels
        line = self._emit(
            "sample",
            contact=contact,
            t=t,
            x=x,
            y=y,
            force=force if c["force"] else None,
            altitude=altitude if c["altitude"] else None,
            azimuth=azimuth if c["azimuth"] else None,
            roll=roll if c["roll"] else None,
            phase=phase,
            est=est,
        )
        if pending is not None:
            expect, update = pending
            line["pending"] = {"expect": expect, "update": update}
            self._pending.setdefault((contact, update), (trial, []))[1].append(expect)
        self._trials[trial].samples += 1

    def correction(
        self, contact: int | None, update: int, arrived: float, expect: int, **values: float
    ) -> None:
        self._emit("correction", contact=contact, update=update, arrived=arrived, **values, expect=expect)
        key = (contact, update) if contact is not None else None
        if key is None or key not in self._pending:
            self._unmatched += 1
            return
        trial, remaining = self._pending[key]
        remaining[:] = [expect] * len(remaining)
        self._trials[trial].corrections += 1

    def lift(self, t: float, contact: int, cancelled: bool = False) -> None:
        self._open_contacts.remove(contact)
        self._emit("contactEnded", t=t, contact=contact, cancelled=cancelled)

    def hover(self, t: float, x: float, y: float, z: float) -> None:
        c = self.channels
        self._emit(
            "hover",
            t=t,
            clock="callback",
            x=x,
            y=y,
            z=z,
            altitude=0.9 if c["altitude"] else None,
            azimuth=2.0 if c["azimuth"] else None,
            roll=0.3 if c["roll"] else None,
        )

    def finger(self, t: float, phase: str, x: float, y: float) -> None:
        self._emit("finger", t=t, phase=phase, x=x, y=y)

    def end(self, t: float, reason: str) -> None:
        assert self._open_trial is not None and not self._open_contacts
        self._emit("trialEnded", t=t, trial=self._open_trial, reason=reason)
        self._trials[self._open_trial].outcome = OUTCOME_OF_REASON[reason]
        self._open_trial = None
        self._state = "ready"

    def questionnaire(self, instrument_id: str, t: float) -> int:
        assert self._state in ("ready", "suspended") and self._open_block is None and self._open_trial is None
        spec = instruments.instrument(instrument_id)
        block = len(self._blocks)
        self._blocks.append(_Block(block, instrument_id))
        self._emit(
            "questionnaireStarted",
            t=t,
            block=block,
            instrument={"id": instrument_id, "version": spec["version"], "locale": spec["locale"]},
        )
        self._open_block = block
        self._state = "answering"
        return block

    def respond(self, item: str, value: int | None, t: float) -> None:
        """Answer (or skip, with value None) one item. A safety item flags at value >= 1 or when skipped."""
        assert self._open_block is not None
        block = self._blocks[self._open_block]
        spec = instruments.instrument(block.instrument)
        item_spec = next(i for i in spec["items"] if i["id"] == item)
        skipped = value is None
        self._emit("response", t=t, block=block.block, item=item, value=value, skipped=skipped)
        if skipped:
            block.skipped += 1
        else:
            block.answered += 1
        if item_spec["safety"] and (value is None or value >= 1):
            self._emit("safetyFlagged", t=t, block=block.block, item=item, value=value, skipped=skipped)
            self._flags += 1

    def questionnaire_end(self, t: float, reason: str = "completed") -> None:
        assert self._open_block is not None
        self._emit("questionnaireEnded", t=t, block=self._open_block, reason=reason)
        self._blocks[self._open_block].outcome = reason
        self._open_block = None
        self._state = "ready"

    def answer_all(self, instrument_id: str, t: float, values: dict[str, int | None] | None = None) -> None:
        """Run one complete block. Items not in `values` get the value 0."""
        spec = instruments.instrument(instrument_id)
        self.questionnaire(instrument_id, t)
        for i, item in enumerate(spec["items"], start=1):
            self.respond(item["id"], (values or {}).get(item["id"], 0), t + i)
        self.questionnaire_end(t + len(spec["items"]) + 1)

    def shown(self, t: float, what: str) -> None:
        list_version = (
            f"{self.jurisdiction}-{studyconfig.CRISIS_LISTS_VERIFIED_ON}" if what == "crisisList" else None
        )
        self._emit("safetyShown", t=t, what=what, listVersion=list_version)
        if what == "crisisList":
            self._crisis_shown += 1
        else:
            self._alerts += 1

    def action(self, t: float, code: str) -> None:
        self._emit("safetyAction", t=t, code=code, role="operator")
        self._actions += 1

    def mark(self, t: float, trial: int, code: str) -> None:
        self._emit("mark", t=t, trial=trial, code=code)

    def recovered(self, t: float, boot: str, truncated_bytes: int) -> None:
        interrupted, block = self._open_trial, self._open_block
        self._emit(
            "recovered",
            t=t,
            at="2026-11-03T09:30:00Z",
            boot=boot,
            truncatedBytes=truncated_bytes,
            interrupted=interrupted,
            interruptedBlock=block,
        )
        if interrupted is not None:
            self._trials[interrupted].outcome = "interrupted"
        if block is not None:
            self._blocks[block].outcome = "interrupted"
        self._open_trial = None
        self._open_block = None
        self._open_contacts.clear()
        self._truncated += truncated_bytes
        self._state = "suspended"

    def close(self, t: float) -> None:
        assert self._open_trial is None and self._open_block is None
        counts = {k: 0 for k in KINDS if k != "sessionClosed"}
        for line in self.lines:
            counts[line["kind"]] += 1
        self._emit("sessionClosed", t=t, counts=counts)
        self._state = "closed"

    def journal_bytes(self) -> bytes:
        return b"".join(encode_line(line) for line in self.lines)

    def summary(self) -> dict[str, Any]:
        counts = {k: 0 for k in KINDS}
        for line in self.lines:
            counts[line["kind"]] += 1
        unresolved = [0] * len(self._trials)
        for trial, remaining in self._pending.values():
            unresolved[trial] += sum(1 for bits in remaining if bits)
        return {
            "sessionID": self.session_id,
            "purpose": self.purpose,
            "administration": self.administration,
            "state": self._state,
            "counts": counts,
            "trials": [
                {
                    "index": t.index,
                    "task": t.task,
                    "attempt": t.attempt,
                    "practice": t.practice,
                    "began": t.began,
                    "outcome": t.outcome,
                    "contacts": t.contacts,
                    "samples": t.samples,
                    "corrections": t.corrections,
                    "unresolved": unresolved[t.index],
                }
                for t in self._trials
            ],
            "questionnaires": [
                {
                    "block": b.block,
                    "instrument": b.instrument,
                    "answered": b.answered,
                    "skipped": b.skipped,
                    "outcome": b.outcome,
                }
                for b in self._blocks
            ],
            "safety": {
                "flags": self._flags,
                "crisisListShown": self._crisis_shown,
                "operatorAlerts": self._alerts,
                "actions": self._actions,
            },
            "unmatchedCorrections": self._unmatched,
            "truncatedBytes": self._truncated,
        }


def _stroke(
    b: JournalBuilder,
    t0: float,
    n: int,
    x0: float,
    y0: float,
    *,
    dt: float = 1 / 240,
    first_pending: tuple[int, int] | None = None,
    est_first: int = 0,
) -> tuple[int, float]:
    """One Pencil contact of n samples along a gentle curve. Returns the contact and the lift time."""
    contact = b.contact(t0)
    for i in range(n):
        phase = "began" if i == 0 else ("ended" if i == n - 1 else "moved")
        b.sample(
            contact,
            t0 + i * dt,
            x0 + 0.75 * i,
            y0 + 0.02 * i * i,
            phase,
            force=0.2 + 0.01 * i,
            est=est_first if i == 0 else 0,
            pending=first_pending if i == 0 else None,
        )
    lift = t0 + n * dt
    b.lift(lift, contact)
    return contact, lift


def _basic() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-000000000001", "P0001").opened()
    b.trial("spiral-trace", 1, 1010.0)
    b.began(1010.5)
    c1, lift = _stroke(b, 1011.0, 6, 300.0, 400.0, est_first=FORCE, first_pending=(FORCE, 101))
    b.correction(c1, 101, lift + 0.004, 0, force=0.42)
    _stroke(b, 1012.0, 4, 320.0, 410.0)
    b.end(1013.0, "participantDone")
    b.close(1014.0)
    return b


def _multi_trial() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-000000000002", "P0002").opened()
    b.trial("practice", 1, 1005.0, practice=True, position=1)
    b.began(1005.5)
    _stroke(b, 1006.0, 4, 100.0, 200.0)
    b.end(1007.0, "participantDone")
    b.trial("spiral-trace", 1, 1010.0, position=2)
    b.began(1010.4)
    c1 = b.contact(1011.0)
    b.sample(c1, 1011.0, 300.0, 400.0, "began", est=FORCE | ALTITUDE, pending=(FORCE | ALTITUDE, 7))
    b.sample(c1, 1011.004, 301.0, 400.5, "moved", est=FORCE, pending=(FORCE, 8))
    b.sample(c1, 1011.008, 302.0, 401.0, "ended")
    b.lift(1011.012, c1)
    b.hover(1011.05, 305.0, 402.0, 0.4)
    b.end(1012.0, "participantDone")
    b.mark(1012.5, 1, "repeatRequested")
    b.trial("loops", 1, 1013.0, position=3)
    b.began(1013.1)
    b.correction(c1, 7, 1013.01, ALTITUDE, force=0.37)
    b.finger(1013.2, "began", 50.0, 60.0)
    b.finger(1013.3, "ended", 50.0, 61.0)
    _stroke(b, 1014.0, 5, 200.0, 300.0)
    b.correction(c1, 7, 1014.5, 0, altitude=1.1)
    b.end(1015.0, "participantDone")
    b.trial("spiral-trace", 2, 1016.0, position=4)
    b.began(1016.2)
    _stroke(b, 1017.0, 8, 300.0, 400.0)
    b.end(1137.0, "timeLimit")
    b.trial("pentagons", 1, 1140.0, position=5)
    b.end(1145.0, "declined")
    b.trial("house", 1, 1146.0, position=6, accommodations=["readAloud"])
    b.began(1147.0)
    b.end(1150.0, "unable")
    b.close(1151.0)
    return b


def _pencil2_no_roll() -> JournalBuilder:
    channels = dict(ALL_CHANNELS, roll=False)
    b = JournalBuilder(
        "3F2A9C1E-0000-4000-8000-000000000003", "P0003", channels=channels, pencil="second"
    ).opened()
    b.trial("loops", 1, 1010.0)
    b.began(1010.3)
    _stroke(b, 1011.0, 5, 200.0, 300.0)
    b.hover(1011.2, 210.0, 300.0, 0.8)
    b.end(1012.0, "operatorAbort")
    b.close(1013.0)
    return b


def _usbc_no_force() -> JournalBuilder:
    channels = dict(ALL_CHANNELS, force=False, roll=False, hover=False)
    b = JournalBuilder(
        "3F2A9C1E-0000-4000-8000-000000000004", "P0004", channels=channels, pencil="usbC"
    ).opened()
    b.trial("pentagons", 1, 1010.0, hand="left")
    b.began(1010.3)
    _stroke(b, 1011.0, 5, 200.0, 300.0)
    b.end(1012.0, "participantDone")
    b.close(1013.0)
    return b


def _recovered_interrupted() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-000000000005", "P0005").opened()
    b.trial("house", 1, 1010.0)
    b.began(1010.2)
    _stroke(b, 1011.0, 5, 200.0, 300.0)
    crashed = b.contact(1012.0)
    for i in range(3):
        b.sample(crashed, 1012.0 + i / 240, 250.0 + i, 300.0, "began" if i == 0 else "moved")
    b.recovered(12.0, "0B0B0B0B-0000-4000-8000-000000000002", 37)
    b.trial("house", 2, 15.0)
    b.began(15.4)
    _stroke(b, 16.0, 5, 200.0, 300.0)
    b.end(17.0, "participantDone")
    b.close(18.0)
    return b


def _unmatched_correction() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-000000000006", "P0006").opened()
    b.trial("clock", 1, 1010.0)
    b.began(1010.1)
    c1, lift = _stroke(b, 1011.0, 4, 200.0, 300.0, est_first=FORCE, first_pending=(FORCE, 3))
    b.correction(None, 99, lift + 0.01, 0, force=0.5)
    b.correction(c1, 4, lift + 0.02, 0, force=0.5)
    b.end(1012.0, "participantDone")
    b.close(1013.0)
    return b


def _duplicate_timestamps() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-000000000007", "P0007").opened()
    b.trial("sentence", 1, 1010.0)
    b.began(1010.2)
    c1 = b.contact(1011.0)
    for i, t in enumerate((1011.0, 1011.004, 1011.004, 1011.008, 1011.008, 1011.012)):
        b.sample(c1, t, 100.0 + i, 200.0, "began" if i == 0 else ("ended" if i == 5 else "moved"))
    b.lift(1011.016, c1)
    b.end(1012.0, "participantDone")
    b.close(1013.0)
    return b


def _open_session() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-000000000008", "P0008").opened()
    b.trial("spiral-trace", 1, 1010.0)
    b.began(1010.5)
    c1 = b.contact(1011.0)
    for i in range(4):
        b.sample(c1, 1011.0 + i / 240, 300.0 + i, 400.0, "began" if i == 0 else "moved")
    return b


def _simulated_input() -> JournalBuilder:
    channels = {"force": False, "altitude": False, "azimuth": False, "roll": False, "hover": False}
    b = JournalBuilder(
        "3F2A9C1E-0000-4000-8000-000000000009",
        "SIM-0001",
        channels=channels,
        pencil="none",
        input="directTouchAsPen",
    ).opened()
    b.trial("spiral-trace", 1, 1010.0)
    b.began(1010.5)
    _stroke(b, 1011.0, 6, 300.0, 400.0, dt=1 / 60)
    b.end(1012.0, "participantDone")
    b.close(1013.0)
    return b


def _supervised_minor_safety() -> JournalBuilder:
    b = JournalBuilder(
        "3F2A9C1E-0000-4000-8000-00000000000A", "P0010", age_band="13-17", jurisdiction="GB"
    ).opened()
    b.handoff(1004.0, "participant")
    b.trial("spiral-trace", 1, 1010.0)
    b.began(1010.5)
    _stroke(b, 1011.0, 6, 300.0, 400.0)
    b.end(1013.0, "participantDone")
    b.answer_all("phq9", 1020.0, {"phq9-2": 2, "phq9-9": 1})
    b.answer_all("gad7", 1040.0, {"gad7-4": None})
    b.handoff(1060.0, "operator")
    b.shown(1061.0, "operatorAlert")
    b.action(1090.0, "clinicianAssessment")
    b.shown(1091.0, "crisisList")
    b.close(1095.0)
    return b


def _self_administered() -> JournalBuilder:
    scale = {
        "kind": "reference",
        "object": "idCard",
        "mmPerPoint": 0.1925,
        "lengthMM": 85.6,
        "drawnPoints": 444.67532467532465,
    }
    b = JournalBuilder(
        "3F2A9C1E-0000-4000-8000-00000000000B", "P0011", administration="self", scale=scale, jurisdiction="AU"
    ).opened()
    b.trial("practice", 1, 1005.0, practice=True)
    b.began(1005.5)
    _stroke(b, 1006.0, 4, 100.0, 200.0)
    b.end(1007.0, "participantDone")
    b.trial("loops", 1, 1010.0, position=2)
    b.began(1010.5)
    _stroke(b, 1011.0, 6, 300.0, 400.0)
    b.end(1013.0, "participantDone")
    b.answer_all("phq8", 1020.0, {"phq8-1": 1})
    b.answer_all("gad7", 1040.0)
    b.shown(1060.0, "crisisList")
    b.close(1061.0)
    return b


def _self_with_remote_review() -> JournalBuilder:
    b = JournalBuilder(
        "3F2A9C1E-0000-4000-8000-00000000000C",
        "P0012",
        administration="self",
        age_band="13-17",
        jurisdiction="IN",
    ).opened()
    b.trial("spiral-trace", 1, 1010.0)
    b.began(1010.5)
    _stroke(b, 1011.0, 6, 300.0, 400.0)
    b.end(1013.0, "participantDone")
    b.questionnaire("phq9", 1020.0)
    for i in range(1, 9):
        b.respond(f"phq9-{i}", 1, 1020.0 + i)
    b.respond("phq9-9", None, 1029.0)
    b.shown(1029.5, "crisisList")
    b.respond("difficulty", 1, 1030.0)
    b.questionnaire_end(1031.0)
    b.shown(1035.0, "crisisList")
    b.close(1036.0)
    return b


def _qualification() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-00000000000D", "QUAL-01", purpose="qualification").opened()
    for position, task in enumerate(battery.QUALIFICATION_TASKS, start=1):
        b.trial(task, 1, 1000.0 + 20 * position, position=position)
        b.began(1001.0 + 20 * position)
        _stroke(b, 1002.0 + 20 * position, 12, 120.0, 300.0)
        b.end(1010.0 + 20 * position, "participantDone")
    b.close(1100.0)
    return b


def _recovered_in_questionnaire() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-00000000000E", "P0014").opened()
    b.trial("clock", 1, 1010.0)
    b.began(1010.5)
    _stroke(b, 1011.0, 5, 200.0, 300.0)
    b.end(1012.0, "participantDone")
    b.questionnaire("gad7", 1020.0)
    b.respond("gad7-1", 2, 1021.0)
    b.recovered(5.0, "0B0B0B0B-0000-4000-8000-000000000003", 0)
    b.answer_all("gad7", 8.0)
    b.shown(30.0, "crisisList")
    b.close(31.0)
    return b


def _repeated_update_key() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-00000000000F", "P0015").opened()
    b.trial("loops", 1, 1010.0)
    b.began(1010.1)
    c1 = b.contact(1011.0)
    b.sample(c1, 1011.0, 300.0, 400.0, "began", est=FORCE, pending=(FORCE, 5))
    b.sample(c1, 1011.004, 301.0, 400.0, "moved", est=FORCE, pending=(FORCE, 5))
    b.sample(c1, 1011.008, 302.0, 400.0, "ended")
    b.lift(1011.012, c1)
    b.correction(c1, 5, 1011.02, 0, force=0.33)
    b.end(1012.0, "participantDone")
    b.close(1013.0)
    return b


def _cross_contact_update() -> JournalBuilder:
    b = JournalBuilder("3F2A9C1E-0000-4000-8000-000000000010", "P0016").opened()
    b.trial("loops", 1, 1010.0)
    b.began(1010.1)
    _stroke(b, 1011.0, 4, 300.0, 400.0, est_first=FORCE, first_pending=(FORCE, 7))
    c2, lift = _stroke(b, 1012.0, 4, 320.0, 400.0)
    b.correction(c2, 7, lift + 0.01, 0, force=0.4)
    b.end(1013.0, "participantDone")
    b.close(1014.0)
    return b


JOURNAL_FIXTURES = {
    "basic": _basic,
    "multi_trial": _multi_trial,
    "pencil2_no_roll": _pencil2_no_roll,
    "usbc_no_force": _usbc_no_force,
    "recovered_interrupted": _recovered_interrupted,
    "unmatched_correction": _unmatched_correction,
    "duplicate_timestamps": _duplicate_timestamps,
    "open_session": _open_session,
    "simulated_input": _simulated_input,
    "supervised_minor_safety": _supervised_minor_safety,
    "self_administered": _self_administered,
    "self_with_remote_review": _self_with_remote_review,
    "qualification": _qualification,
    "recovered_in_questionnaire": _recovered_in_questionnaire,
    "repeated_update_key": _repeated_update_key,
    "cross_contact_update": _cross_contact_update,
}


def _recovery_fixtures() -> dict[str, tuple[bytes, dict[str, Any]]]:
    good = _open_session().journal_bytes()
    lines = good.count(b"\n")
    torn = b'{"seq":6,"kind":"sample","contact":1,"t":1011.0'
    complete_unterminated = encode_line(
        {"seq": lines, "kind": "finger", "t": 1011.5, "phase": "began", "x": 1.0, "y": 2.0}
    ).rstrip(b"\n")
    parts = good.split(b"\n")
    damaged = b"\n".join([*parts[:3], b"{not json", *parts[3:]])
    return {
        "clean": (good, {"outcome": "clean", "lines": lines, "truncatedBytes": 0}),
        "torn_tail": (good + torn, {"outcome": "truncated", "lines": lines, "truncatedBytes": len(torn)}),
        "unterminated_valid": (
            good + complete_unterminated,
            {"outcome": "truncated", "lines": lines, "truncatedBytes": len(complete_unterminated)},
        ),
        "damaged_interior": (damaged, {"outcome": "damaged", "line": 3}),
    }


def manifest_bytes(session_id: str, journal: bytes) -> bytes:
    manifest = {
        "format": "script.journal",
        "version": FORMAT_VERSION,
        "sessionID": session_id,
        "journal": {
            "bytes": len(journal),
            "lines": journal.count(b"\n"),
            "sha256": hashlib.sha256(journal).hexdigest(),
        },
    }
    return (json.dumps(manifest, indent=1) + "\n").encode()


DOUBLES = (
    0.1,
    1.0,
    1e-05,
    1e-07,
    5e-324,
    2.220446049250313e-16,
    0.30000000000000004,
    3.141592653589793,
    123456.789,
    86400.123456789,
    1e16,
    1234567890123456.0,
    1e21,
    1.7976931348623157e308,
    0.007444444444444444,
    1011.0041666666667,
)


def _doubles_bytes() -> bytes:
    rows = [
        {"text": repr(v), "bits": f"{struct.unpack('<Q', struct.pack('<d', v))[0]:016x}"} for v in DOUBLES
    ]
    return (json.dumps({"doubles": rows}, indent=1) + "\n").encode()


def _json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=1) + "\n").encode()


def generated_files() -> dict[str, bytes]:
    """Every generated file, keyed by its path relative to contract/."""
    files: dict[str, bytes] = {
        "battery.json": battery.battery_bytes(),
        "instruments.json": instruments.instruments_bytes(),
        "configs/demo.json": studyconfig.config_bytes(studyconfig.demo_config()),
        "fixtures/doubles.json": _doubles_bytes(),
    }
    files["fixtures/svc/synthetic.svc"] = (
        b"8\n"
        b"100 200 1000 1 900 450 512\n"
        b"200 300 1010 1 900 450 1024\n"
        b"300 400 1020 1 900 450 256\n"
        b"400 500 1030 1 900 450 512\n"
        b"500 600 1040 0 1800 900 0\n"
        b"600 700 1050 0 1800 900 0\n"
        b"700 800 1060 0 1800 900 0\n"
        b"800 900 1070 0 1800 900 0\n"
    )
    files["fixtures/parkinsonhw/synthetic.txt"] = (
        b"X;Y;Z;Pressure;GripAngle;Timestamp;Test ID\n"
        b"100;200;0;512;450;1000;0\n"
        b"200;300;0;1023;450;1010;0\n"
        b"300;400;1;0;450;1020;0\n"
        b"400;500;2;0;450;1030;0\n"
        b"500;600;0;256;450;0;1\n"
        b"600;700;0;512;450;10;1\n"
    )
    for name, build in JOURNAL_FIXTURES.items():
        b = build()
        files[f"fixtures/journal/{name}.jsonl"] = b.journal_bytes()
        files[f"fixtures/journal/{name}.fold.json"] = _json_bytes(b.summary())
    for name, (data, expected) in _recovery_fixtures().items():
        files[f"fixtures/recovery/{name}.jsonl"] = data
        files[f"fixtures/recovery/{name}.expected.json"] = _json_bytes(expected)
    basic = _basic()
    folder = f"fixtures/export/{basic.participant}_{basic.session_id[:8].lower()}"
    journal = basic.journal_bytes()
    files[f"{folder}/journal.jsonl"] = journal
    files[f"{folder}/manifest.json"] = manifest_bytes(basic.session_id, journal)
    files[f"{folder}.fold.json"] = _json_bytes(basic.summary())
    return files


def regenerate(contract_dir: Path) -> list[str]:
    written: list[str] = []
    for rel, data in generated_files().items():
        path = contract_dir / rel
        if path.exists() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(rel)
    return written


def verify(contract_dir: Path) -> list[str]:
    """Paths whose checked-in bytes differ from the generator, or that the generator no longer makes."""
    expected = generated_files()
    stale = [
        rel
        for rel, data in expected.items()
        if not (contract_dir / rel).exists() or (contract_dir / rel).read_bytes() != data
    ]
    fixtures = contract_dir / "fixtures"
    for path in sorted(fixtures.rglob("*")) if fixtures.exists() else []:
        rel = path.relative_to(contract_dir).as_posix()
        if path.is_file() and not path.name.startswith("._") and rel not in expected:
            stale.append(rel)
    return stale
