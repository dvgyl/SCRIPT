"""Reader output in millimetres, seconds, and radians, with y up."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]

Purpose = Literal["study", "qualification"]
Administration = Literal["supervised", "self"]
AgeBand = Literal["13-17", "18-24"]
Hand = Literal["left", "right", "unknown"]
Accommodation = Literal["readAloud", "largerText", "extraTime", "positionAssist", "other"]


class Channel(StrEnum):
    FORCE = "force"
    ALTITUDE = "altitude"
    AZIMUTH = "azimuth"
    ROLL = "roll"
    HOVER = "hover"


class PenState(StrEnum):
    CONTACT = "contact"
    IN_AIR = "in_air"


class TrialOutcome(StrEnum):
    COMPLETED = "completed"
    TIME_LIMIT = "timeLimit"
    DECLINED = "declined"
    UNABLE = "unable"
    ABORTED = "aborted"
    INTERRUPTED = "interrupted"
    TECHNICAL_FAILURE = "technicalFailure"
    OPEN = "open"


class EndReason(StrEnum):
    PARTICIPANT_DONE = "participantDone"
    TIME_LIMIT = "timeLimit"
    DECLINED = "declined"
    UNABLE = "unable"
    OPERATOR_ABORT = "operatorAbort"
    BACKGROUNDED = "backgrounded"
    TECHNICAL_FAILURE = "technicalFailure"


class BlockOutcome(StrEnum):
    COMPLETED = "completed"
    DECLINED = "declined"
    INTERRUPTED = "interrupted"
    OPEN = "open"


@dataclass(frozen=True)
class Device:
    model: str
    input: Literal["pencil", "directTouchAsPen", "tablet"]
    planned_channels: frozenset[Channel] = frozenset()
    observed_channels: frozenset[Channel] = frozenset()
    spatial_unit: Literal["mm", "source"] = "mm"


@dataclass(frozen=True)
class TaskRef:
    id: str
    mode: Literal["trace", "copy", "free"] = "free"
    paths: tuple[FloatArray, ...] = ()


@dataclass(frozen=True)
class Stroke:
    t: FloatArray
    xy: FloatArray
    state: PenState = PenState.CONTACT
    channels: dict[Channel, FloatArray] = field(default_factory=dict)
    unresolved: IntArray = field(default_factory=lambda: np.array([], dtype=np.int64))
    boot: str = "source"
    contact: int | None = None
    max_force: float | None = None

    def __post_init__(self) -> None:
        n = len(self.t)
        if self.t.shape != (n,) or self.xy.shape != (n, 2):
            raise ValueError("Stroke arrays must have shapes (n,) and (n, 2).")
        if len(self.unresolved) not in (0, n) or any(v.shape != (n,) for v in self.channels.values()):
            raise ValueError("Channel and estimate arrays must match the sample count.")
        for a in (self.t, self.xy, self.unresolved, *self.channels.values()):
            a.setflags(write=False)


@dataclass(frozen=True)
class LocalizedRef:
    """A versioned text in one locale: a task instruction or a questionnaire instrument."""

    id: str
    version: str
    locale: str


@dataclass(frozen=True)
class TrialContext:
    practice: bool
    position: int
    hand: Hand
    instruction: LocalizedRef
    accommodations: tuple[Accommodation, ...] = ()


@dataclass(frozen=True)
class Trial:
    index: int
    task: TaskRef
    strokes: tuple[Stroke, ...]
    device: Device
    attempt: int = 1
    outcome: TrialOutcome = TrialOutcome.COMPLETED
    started: float | None = None
    ended: float | None = None
    boot: str = "source"
    finger_contacts: int = 0
    unmatched_corrections: int = 0
    marks: tuple[str, ...] = ()
    context: TrialContext | None = None
    began: float | None = None
    end_reason: EndReason | None = None


@dataclass(frozen=True)
class Response:
    """One answered item, or a skipped item when `value` is None."""

    item: str
    value: int | None
    t: float


@dataclass(frozen=True)
class QuestionnaireBlock:
    block: int
    instrument: LocalizedRef
    started: float
    ended: float | None
    outcome: BlockOutcome
    boot: str
    responses: tuple[Response, ...]
    flagged_items: tuple[str, ...]


@dataclass(frozen=True)
class ModelTableScale:
    mm_per_point: float
    ppi: float
    native_scale: float


@dataclass(frozen=True)
class ReferenceScale:
    object: Literal["steelRule", "idCard"]
    mm_per_point: float
    length_mm: float
    drawn_points: float


@dataclass(frozen=True)
class UnknownScale:
    pass


ScaleSource = ModelTableScale | ReferenceScale | UnknownScale


@dataclass(frozen=True)
class Study:
    id: str
    site: str
    config_id: str
    config_version: str
    config_sha256: str
    protocol_version: str


@dataclass(frozen=True)
class Visit:
    id: str
    number: int


@dataclass(frozen=True)
class DocumentRef:
    id: str
    version: str


@dataclass(frozen=True)
class Consent:
    model: Literal["adult", "guardianAndAssent", "waiver"]
    attested_by: str
    attested_role: Literal["operator", "enrolment"]
    attested_at: str
    documents: tuple[DocumentRef, ...]
    waiver_reference: str | None = None


@dataclass(frozen=True)
class Ethics:
    approval_id: str
    protocol_version: str
    safety_protocol_version: str
    monitoring_disclosure_version: str | None = None


@dataclass(frozen=True)
class Surface:
    protector: Literal["none", "glossy", "matte", "paperlike", "unknown"]
    nib: Literal["new", "used", "unknown"]


@dataclass(frozen=True)
class QualificationRef:
    session_id: str
    at: str


@dataclass(frozen=True)
class SessionContext:
    """What a SCRIPT journal header records about the session. Public tablet formats have none."""

    purpose: Purpose
    administration: Administration
    study: Study
    visit: Visit
    age_band: AgeBand
    jurisdiction: str
    locale: str
    consent: Consent
    ethics: Ethics
    device_asset: str
    surface: Surface
    scale: ScaleSource
    opened_at: str
    qualification: QualificationRef | None = None


def reader_profile(reader: str, **conversions: Any) -> str:
    """Describe a reader and its unit conversions as canonical JSON for provenance."""
    return json.dumps({"reader": reader, **conversions}, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Recording:
    session_id: str
    participant: str
    device: Device
    trials: tuple[Trial, ...]
    reader_profile: str
    source_hashes: tuple[str, ...] = ()
    unmatched_corrections: int = 0
    context: SessionContext | None = None
    questionnaires: tuple[QuestionnaireBlock, ...] = ()
