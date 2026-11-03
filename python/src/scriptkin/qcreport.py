"""The quality report of a study store: counts and rates for data management, never a label or a score.

The report reads every accepted session in `sessions/` of a store that `scriptkin.intake` wrote. Trial
counts cover the measured trials of study sessions, the same trials that `feature_table` uses. Safety
counts are for audit only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from scriptkin import __version__
from scriptkin.formats.export import VerifiedExport, verify_export
from scriptkin.intake import session_folders
from scriptkin.qualification import Intervals, qualify
from scriptkin.quality import Refusal, Rejected, assess
from scriptkin.recording import Channel, PenState, Recording


@dataclass(frozen=True)
class _Session:
    head: dict[str, Any]
    summary: dict[str, Any]
    recording: Recording

    @staticmethod
    def of(export: VerifiedExport) -> _Session:
        return _Session(export.rows[0], export.state.summary(), export.to_recording())

    @property
    def device(self) -> tuple[str, str]:
        """The asset and the declared pen of the device that samples the pen: the tablet, or the iPad."""
        if self.head["input"] == "tablet":
            return self.head["tablet"]["asset"], self.head["tablet"]["pen"]["declared"]
        return self.head["device"]["asset"], self.head["pencil"]["declared"]


def _count(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _trials(sessions: list[_Session]) -> dict[str, Any]:
    outcomes = [
        assess(trial)
        for s in sessions
        if s.head["purpose"] == "study"
        for trial in s.recording.trials
        if not (trial.context and trial.context.practice)
    ]
    refused = [qc for qc in outcomes if isinstance(qc, Rejected)]
    return {
        "accepted": len(outcomes) - len(refused),
        "refused": len(refused),
        "refusalCodes": _count(r.code.value for qc in refused for r in qc.refusals),
    }


def _sample_rate(sessions: list[_Session]) -> dict[str, Any]:
    """Rates from the sample intervals inside contact strokes, pooled over the sessions."""
    steps = [
        np.diff(stroke.t)
        for s in sessions
        for trial in s.recording.trials
        for stroke in trial.strokes
        if stroke.state == PenState.CONTACT
    ]
    dt = np.concatenate([np.empty(0), *steps])
    dt = dt[np.isfinite(dt) & (dt > 0)]
    if not len(dt):
        return {"intervals": 0, "median": None, "p5": None, "p95": None}
    p5, median, p95 = np.percentile(1 / dt, [5, 50, 95]).tolist()
    return {"intervals": len(dt), "median": median, "p5": p5, "p95": p95}


def _channels(sessions: list[_Session]) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    for channel in Channel:
        planned = [channel in s.recording.device.planned_channels for s in sessions]
        observed = [channel in s.recording.device.observed_channels for s in sessions]
        report[channel.value] = {
            "planned": sum(planned),
            "observed": sum(observed),
            "plannedNotObserved": sum(p and not o for p, o in zip(planned, observed, strict=True)),
        }
    return report


def _measure(value: float | Refusal) -> float | dict[str, str]:
    return {"refusal": value.code.value} if isinstance(value, Refusal) else value


def _qualification(session: _Session) -> dict[str, Any]:
    q = qualify(session.recording)
    intervals = q.intervals
    return {
        "sessionID": session.head["sessionID"],
        "openedAt": session.head["openedAt"],
        "intervals": {
            "count": intervals.count,
            "medianS": intervals.median_s,
            "p5S": intervals.p5_s,
            "p95S": intervals.p95_s,
            "gapShare": intervals.gap_share,
        }
        if isinstance(intervals, Intervals)
        else _measure(intervals),
        "holdJitterMM": _measure(q.hold_jitter_mm),
        "lineDeviationMM": _measure(q.line_deviation_mm),
    }


def _devices(sessions: list[_Session]) -> list[dict[str, Any]]:
    """Each device asset and declared pen: its sample rates and its latest qualification session."""
    groups: dict[tuple[str, str], list[_Session]] = {}
    for s in sessions:
        groups.setdefault(s.device, []).append(s)
    report: list[dict[str, Any]] = []
    for (asset, pencil), group in sorted(groups.items()):
        qualified = sorted(
            (s for s in group if s.head["purpose"] == "qualification"), key=lambda s: s.head["openedAt"]
        )
        report.append(
            {
                "deviceAsset": asset,
                "pencil": pencil,
                "models": sorted({s.recording.device.model for s in group}),
                "sampleRateHz": _sample_rate(group),
                "latestQualification": _qualification(qualified[-1]) if qualified else None,
            }
        )
    return report


def qc_report(store: str | Path, *, private_key: bytes | None = None) -> dict[str, Any]:
    """Count the sessions, trials, sample rates, channels, estimates, interruptions, and safety events.

    An encrypted session needs `private_key`. The report holds counts, rates, and device measures only.
    """
    sessions = [_Session.of(verify_export(f, private_key=private_key)) for f in session_folders(store)]
    heads = [s.head for s in sessions]
    # A bench session has no study and no administration, so those counts cover participant sessions.
    participant_heads = [h for h in heads if h["purpose"] != "bench"]
    trials = [t for s in sessions for t in s.summary["trials"]]
    samples = sum(t["samples"] for t in trials)
    unresolved = sum(t["unresolved"] for t in trials)
    corrections = sum(s.summary["counts"]["correction"] for s in sessions)
    unmatched = sum(s.summary["unmatchedCorrections"] for s in sessions)
    safety = Counter[str]()
    for s in sessions:
        safety.update(s.summary["safety"])
    return {
        "kind": "qcReport",
        "library": f"scriptkin {__version__}",
        "sessions": len(sessions),
        "sessionsBy": {
            "site": _count(h["study"]["site"] for h in participant_heads),
            "deviceAsset": _count(h["device"]["asset"] for h in heads),
            "configSHA256": _count(h["study"]["configSHA256"] for h in participant_heads),
            "appVersion": _count(h["app"]["version"] for h in heads),
            "administration": _count(h["administration"] for h in participant_heads),
            "purpose": _count(h["purpose"] for h in heads),
        },
        "trials": _trials(sessions),
        "devices": _devices(sessions),
        "channels": _channels(sessions),
        "estimates": {
            "samples": samples,
            "unresolved": unresolved,
            "unresolvedRate": _rate(unresolved, samples),
            "corrections": corrections,
            "unmatched": unmatched,
            "unmatchedRate": _rate(unmatched, corrections),
        },
        "interrupted": {
            "trials": sum(t["outcome"] == "interrupted" for t in trials),
            "blocks": sum(
                b["outcome"] == "interrupted" for s in sessions for b in s.summary["questionnaires"]
            ),
        },
        "recovered": {
            "sessions": sum(s.summary["counts"]["recovered"] > 0 for s in sessions),
            "truncatedBytes": sum(s.summary["truncatedBytes"] for s in sessions),
        },
        "safetyAudit": dict(sorted(safety.items())),
    }


def _share(part: int, whole: int) -> str:
    return f"{part} of {whole} ({100 * part / whole:.1f}%)" if whole else f"{part} of 0"


def _hz(value: float | None) -> str:
    return "" if value is None else f"{value:.1f}"


def qc_markdown(report: dict[str, Any]) -> str:
    """Two short Markdown tables: the store totals, and the sample rates and qualification of each device."""
    trials, estimates, safety = report["trials"], report["estimates"], report["safetyAudit"]
    purposes = ", ".join(f"{k} {v}" for k, v in report["sessionsBy"]["purpose"].items())
    codes = ", ".join(f"{k} {v}" for k, v in trials["refusalCodes"].items()) or "none"
    rows = [
        ("Sessions", f"{report['sessions']} ({purposes})" if purposes else "0"),
        ("Trials accepted / refused", f"{trials['accepted']} / {trials['refused']}"),
        ("Refusal codes", codes),
        ("Unresolved estimates", _share(estimates["unresolved"], estimates["samples"])),
        ("Unmatched corrections", _share(estimates["unmatched"], estimates["corrections"])),
        (
            "Interrupted trials / blocks",
            f"{report['interrupted']['trials']} / {report['interrupted']['blocks']}",
        ),
        ("Recovered sessions", str(report["recovered"]["sessions"])),
        (
            "Safety flags / actions / crisis lists (audit only)",
            f"{safety.get('flags', 0)} / {safety.get('actions', 0)} / {safety.get('crisisListShown', 0)}",
        ),
    ]
    lines = ["| Measure | Value |", "| --- | --- |", *(f"| {k} | {v} |" for k, v in rows), ""]
    lines += [
        "| Device | Pencil | Median Hz | 5th percentile Hz | 95th percentile Hz | Latest qualification |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for d in report["devices"]:
        rate, latest = d["sampleRateHz"], d["latestQualification"]
        cells = (d["deviceAsset"], d["pencil"], *map(_hz, (rate["median"], rate["p5"], rate["p95"])))
        lines.append(f"| {' | '.join(cells)} | {latest['openedAt'] if latest else 'none'} |")
    return "\n".join(lines) + "\n"
