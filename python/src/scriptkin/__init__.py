"""scriptkin: read, check, and measure drawing kinematics. See docs/DESIGN.md."""

__version__ = "0.1.0"

from scriptkin.features import CATALOG, FeatureTable, extract, feature_table
from scriptkin.formats import FormatError
from scriptkin.formats.export import pen_only, read_journal
from scriptkin.formats.journal import JournalError, fold, summarize
from scriptkin.formats.parkinsonhw import load_parkinsonhw, read_parkinsonhw
from scriptkin.formats.svc import SvcReader, Tablet
from scriptkin.quality import Accepted, QcPolicy, QcReport, Refusal, RefusalCode, Rejected, Segment, assess
from scriptkin.recording import Channel, Device, PenState, Recording, Stroke, TaskRef, Trial, TrialOutcome
from scriptkin.signal import Preprocessing

__all__ = [
    "CATALOG",
    "Accepted",
    "Channel",
    "Device",
    "FeatureTable",
    "FormatError",
    "JournalError",
    "PenState",
    "Preprocessing",
    "QcPolicy",
    "QcReport",
    "Recording",
    "Refusal",
    "RefusalCode",
    "Rejected",
    "Segment",
    "Stroke",
    "SvcReader",
    "Tablet",
    "TaskRef",
    "Trial",
    "TrialOutcome",
    "assess",
    "extract",
    "feature_table",
    "fold",
    "load_parkinsonhw",
    "pen_only",
    "read_journal",
    "read_parkinsonhw",
    "summarize",
]
