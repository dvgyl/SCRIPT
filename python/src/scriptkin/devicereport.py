"""Aggregate checks for device-test exports. No questionnaire data enter the report."""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any, Literal

from scriptkin import bench
from scriptkin.formats.export import VerifiedExport, verify_export
from scriptkin.formats.journal import JournalError
from scriptkin.qualification import Intervals, intervals, qualify
from scriptkin.quality import Refusal, Rejected, assess
from scriptkin.recording import PenState

Result = Literal["PASS", "FAIL", "RECORD", "NOT RUN"]


@dataclass(frozen=True)
class Check:
    id: str
    name: str
    result: Result
    value: str
    sessions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Session:
    purpose: str
    input: str
    short_id: str
    revision: str


@dataclass(frozen=True)
class DeviceReport:
    date: str
    checks: tuple[Check, ...]
    sessions: tuple[Session, ...]


def json_form(report: DeviceReport) -> dict[str, Any]:
    """Return only the aggregate fields of the report."""
    return asdict(report)


def _folders(sources: Iterable[str | Path]) -> list[Path]:
    found: set[Path] = set()
    for source in sources:
        root = Path(source)
        if root.name.startswith("._"):
            continue
        if not root.is_dir():
            raise ValueError("Each source must be a folder.")
        for directory, names, files in os.walk(root):
            names[:] = sorted(n for n in names if not n.startswith("._"))
            if "manifest.json" in files:
                found.add(Path(directory).resolve())
                names[:] = []
    if not found:
        raise ValueError("No export found.")
    return sorted(found)


def _measure(value: float | Refusal) -> str:
    return value.code.value if isinstance(value, Refusal) else f"{value:.3f}"


def _timing(value: Intervals | Refusal) -> str:
    if isinstance(value, Refusal):
        return value.code.value
    return (
        f"median {value.median_s * 1000:.3f} ms; p5 {value.p5_s * 1000:.3f} ms; "
        f"p95 {value.p95_s * 1000:.3f} ms; above 30 ms {value.gap_share:.3%}"
    )


def _bench(export: VerifiedExport, role: str) -> tuple[Result, str]:
    h = export.rows[0]
    valid = (
        export.manifest.get("demo") is True and "encryption" not in export.manifest and "participant" not in h
    )
    runs: dict[int, str] = {}
    ids = []
    for row in export.rows:
        if row["kind"] == "trialStarted":
            run = row["bench"]
            expected = f"{role}-{h['bench']['mounting']}-{run['program']}-r{run['run']}"
            valid &= run.get("clip") == expected
            runs[row["trial"]["index"]] = run["program"]
            ids.append(expected)
        if row["kind"] == "mark" and row["code"] == "exclude":
            program = next((p for p in bench.PROGRAMS if p.id == runs[row["trial"]]), None)
            valid &= program is not None and program.fault is None
    return ("PASS" if valid and ids else "FAIL", ", ".join(ids) or "No runs.")


def _pencil(export: VerifiedExport) -> list[tuple[str, str, Result, str]]:
    h, rows = export.rows[0], export.rows
    summary, rec = export.state.summary(), export.to_recording()
    samples = [r for r in rows if r["kind"] == "sample"]
    hover = [r for r in rows if r["kind"] == "hover"]
    channels = ("force", "altitude", "azimuth", "roll")
    counts = {c: sum(c in s for s in samples) for c in channels}
    good = bool(samples) and all(h["channels"].get(c) and counts[c] == len(samples) for c in channels)
    result: list[tuple[str, str, Result, str]] = [
        (
            "P2",
            "Pencil channels",
            "PASS" if good else "FAIL",
            "; ".join(f"{c}: {n} of {len(samples)} samples" for c, n in counts.items()),
        )
    ]
    if h["purpose"] == "study":
        z = [r["z"] for r in hover if "z" in r]
        good = bool(hover) and all(0 <= v <= 1 for v in z)
        status: Result = "PASS" if good else ("FAIL" if hover or h["channels"].get("hover") else "RECORD")
        result.append(
            (
                "P3",
                "Hover",
                status,
                f"{len(hover)} lines;"
                f" z {min(z):.3f} to {max(z):.3f};"
                f" clocks {', '.join(sorted({r['clock'] for r in hover}))}"
                if z
                else f"{len(hover)} lines; no z range.",
            )
        )
        fingers = sum(r["kind"] == "finger" for r in rows)
        result.append(("P7", "Finger and palm", "PASS" if fingers else "FAIL", f"{fingers} finger lines."))
        s = summary["safety"]
        good = (
            s["flags"] >= 1
            and s["flags"] == s["actions"]
            and s["operatorAlerts"] >= 1
            and s["crisisListShown"] >= 1
            and summary["state"] == "closed"
        )
        result.append(
            (
                "P11",
                "Safety flow on the device",
                "PASS" if good else "FAIL",
                f"flags {s['flags']};"
                f" actions {s['actions']};"
                f" operator alerts {s['operatorAlerts']};"
                f" crisis lists {s['crisisListShown']}.",
            )
        )
    timed = [r for r in rows if r["kind"] in ("sample", "hover")]
    earlier = sum(b["kind"] == "hover" and b["t"] < a["t"] for a, b in pairwise(timed))
    dt = [b["t"] - a["t"] for a, b in pairwise(hover)]
    result.append(
        (
            "P4",
            "Hover timing",
            "RECORD",
            f"median {1000 * median(dt):.3f} ms; earlier lines {earlier}."
            if dt
            else f"No hover interval; earlier lines {earlier}.",
        )
    )
    unresolved = sum(t["unresolved"] for t in summary["trials"])
    share = f"{unresolved / len(samples):.3%}" if samples else "no samples"
    result.append(
        (
            "P5",
            "Estimated properties",
            "RECORD",
            f"estimated samples {sum(s['est'] != 0 for s in samples)};"
            f" corrections {summary['counts']['correction']};"
            f" unmatched {summary['unmatchedCorrections']};"
            f" unresolved {share}.",
        )
    )
    contact = [s for t in rec.trials for s in t.strokes if s.state == PenState.CONTACT]
    timing = qualify(rec).intervals if h["purpose"] == "qualification" else intervals(contact)
    codes: set[str] = set()
    skipped = 0
    if h["purpose"] == "study":
        for t in rec.trials:
            if t.context and t.context.practice:
                continue
            if not any(s.state == PenState.CONTACT for s in t.strokes):
                skipped += 1
                continue
            qc = assess(t)
            if isinstance(qc, Rejected):
                codes.update(r.code.value for r in qc.refusals)
    result.append(
        (
            "P6",
            "Sample rate",
            "FAIL" if codes else "RECORD",
            f"{_timing(timing)};"
            f" skipped empty trials {skipped};"
            f" refusals {', '.join(sorted(codes)) or 'none'}.",
        )
    )
    scale = h["scale"]
    result.append(
        (
            "P8",
            "Scale",
            "PASS"
            if scale["kind"] == "modelTable"
            else ("RECORD" if scale["kind"] == "reference" else "FAIL"),
            f"{scale['kind']};"
            f" mmPerPoint {scale.get('mmPerPoint', 'absent')};"
            f" ppi {scale.get('ppi', 'absent')}.",
        )
    )
    if h["purpose"] == "qualification":
        q = qualify(rec)
        result.append(
            (
                "P9",
                "Qualification",
                "RECORD",
                f"hold jitter {_measure(q.hold_jitter_mm)} mm;"
                f" line deviation {_measure(q.line_deviation_mm)} mm;"
                f" channels {', '.join(sorted(c.value for c in q.observed_channels))}.",
            )
        )
    if h["purpose"] == "bench" and h["bench"]["role"] == "P":
        status, value = _bench(export, "P")
        result.append(("P10", "Bench mode", status, value))
    return result


def _tablet(export: VerifiedExport) -> list[tuple[str, str, Result, str]]:
    h, rows = export.rows[0], export.rows
    samples = [r for r in rows if r["kind"] == "sample"]
    tablet = h["tablet"]
    result: list[tuple[str, str, Result, str]] = [
        (
            "W1",
            "Source revision and driver",
            "FAIL" if h["app"]["sourceRevision"] == "unknown" else "RECORD",
            f"{h['app']['sourceRevision']};"
            f" {tablet['model']};"
            f" {tablet['pen']['declared']};"
            f" driver {tablet['driver']}.",
        )
    ]
    view = {}
    inside = True
    for r in rows:
        if r["kind"] == "trialStarted":
            view = r["view"]
        elif r["kind"] == "sample":
            inside &= 0 <= r["x"] <= view["width"] and 0 <= r["y"] <= view["height"]
    xs, ys = [s["x"] for s in samples], [s["y"] for s in samples]
    good = inside and len(set(xs)) > 1 and len(set(ys)) > 1
    result.append(
        (
            "W2",
            "Tablet position",
            "PASS" if good else "FAIL",
            f"x {min(xs):.3f} to {max(xs):.3f}; y {min(ys):.3f} to {max(ys):.3f} lines."
            if samples
            else "No samples.",
        )
    )
    if h["purpose"] == "qualification":
        contacts: dict[int, list[dict[str, Any]]] = {}
        reference = False
        for r in rows:
            if r["kind"] == "trialStarted":
                reference = r["trial"]["task"] == "qual-reference"
            elif r["kind"] == "sample" and reference:
                contacts.setdefault(r["contact"], []).append(r)
        strokes = [s for s in contacts.values() if len(s) > 10][:2]
        values = []
        good = len(strokes) == 2 and "mmPerPoint" in h["scale"]
        for i, stroke in enumerate(strokes):
            mm = h["scale"].get("mmPerPoint", 0)
            dx, dy = ((stroke[-1][c] - stroke[0][c]) * mm for c in ("x", "y"))
            length = (dx * dx + dy * dy) ** 0.5
            good &= abs(length - 100) <= 2 and (
                dx > 0 and abs(dx) > 4 * abs(dy) if i == 0 else dy > 0 and abs(dy) > 4 * abs(dx)
            )
            values.append(f"stroke {i + 1}: dx {dx:.3f}, dy {dy:.3f}, length {length:.3f} mm")
            if i == 1 and dy < 0:
                values.append("absoluteY grows downwards; set yDown of the tablet table entry to true")
        result.append(
            (
                "W3",
                "Direction and scale",
                "PASS" if good else "FAIL",
                "; ".join(values)
                if len(strokes) == 2
                else "Two reference contacts with more than 10 samples are required.",
            )
        )
    forces = [s["force"] for s in samples if "force" in s]
    maximum = max(forces) if forces else None
    result.append(
        (
            "W4",
            "Pressure maximum",
            "PASS" if maximum is not None and maximum >= 0.99 else "RECORD",
            f"maximum {maximum:.3f}." if maximum is not None else "No force samples.",
        )
    )
    rec = export.to_recording()
    result.append(
        (
            "W5",
            "Report rate",
            "RECORD",
            _timing(intervals([s for t in rec.trials for s in t.strokes if s.state == PenState.CONTACT])),
        )
    )
    result.append(
        (
            "W6",
            "Tablet-point events",
            "RECORD",
            "; ".join(f"{k}: {v}" for k, v in sorted(Counter(s["phase"] for s in samples).items()))
            or "No samples.",
        )
    )
    hover = [r for r in rows if r["kind"] == "hover"]
    good = bool(hover) and all(r["clock"] == "event" and "z" not in r for r in hover)
    result.append(
        (
            "W7",
            "Hover",
            "PASS" if good else "FAIL",
            f"{len(hover)} hover lines;"
            f" event clocks {sum(r['clock'] == 'event' for r in hover)};"
            f" with z {sum('z' in r for r in hover)}.",
        )
    )
    if h["purpose"] == "bench" and h["bench"]["role"] == "W":
        status, value = _bench(export, "W")
        result.append(("W8", "Bench mode", status, value))
    return result


NAMES = {
    "P": (
        "Source revision",
        "Pencil channels",
        "Hover",
        "Hover timing",
        "Estimated properties",
        "Sample rate",
        "Finger and palm",
        "Scale",
        "Qualification",
        "Bench mode",
        "Safety flow on the device",
    ),
    "W": (
        "Source revision and driver",
        "Tablet position",
        "Direction and scale",
        "Pressure maximum",
        "Report rate",
        "Tablet-point events",
        "Hover",
        "Bench mode",
    ),
    "D": ("File protection", "Backup exclusion", "Device secret", "Device and display"),
}


def device_report(sources: Iterable[str | Path], diagnostics: Iterable[str | Path] = ()) -> DeviceReport:
    """Read exports and return aggregate checks. Invalid exports do not stop the next export."""
    checks: list[Check] = []
    sessions: list[Session] = []
    revisions: set[str] = set()
    pencil: list[VerifiedExport] = []
    for number, folder in enumerate(_folders(sources), 1):
        try:
            manifest = json.loads((folder / "manifest.json").read_bytes())
            if isinstance(manifest, dict) and "encryption" in manifest:
                checks.append(
                    Check(f"E{number}", "Export", "FAIL", "I5: The device test uses the demo configuration.")
                )
                continue
            export = verify_export(folder)
        except (OSError, ValueError) as error:
            rule = error.rule if isinstance(error, JournalError) else "I5"
            checks.append(Check(f"E{number}", "Export", "FAIL", f"{rule}: Export could not be read."))
            continue
        h = export.rows[0]
        sid = h["sessionID"].split("-")[0].lower()
        sessions.append(Session(h["purpose"], h["input"], sid, h["app"]["sourceRevision"]))
        revisions.add(h["app"]["sourceRevision"])
        rows = []
        if h["input"] == "pencil":
            pencil.append(export)
            rows = _pencil(export)
        elif h["input"] == "tablet":
            rows = _tablet(export)
        checks.extend(Check(i, name, result, value, (sid,)) for i, name, result, value in rows)
    if pencil:
        bad = len({e.rows[0]["app"]["sourceRevision"] for e in pencil}) != 1 or any(
            e.rows[0]["app"]["sourceRevision"] == "unknown" for e in pencil
        )
        checks.append(
            Check(
                "P1",
                "Source revision",
                "FAIL" if bad else "RECORD",
                "; ".join(
                    f"{e.rows[0]['sessionID'][:8].lower()}: "
                    f"{e.rows[0]['app']['version']} / {e.rows[0]['app']['build']} / "
                    f"{e.rows[0]['app']['sourceRevision']}"
                    for e in pencil
                ),
                tuple(e.rows[0]["sessionID"][:8].lower() for e in pencil),
            )
        )
    for path in diagnostics:
        try:
            d = json.loads(Path(path).read_bytes())
            if (
                not isinstance(d, dict)
                or d.get("format") != "script.diagnostics"
                or d.get("version") != "1.0"
            ):
                raise ValueError
            items = [s for s in d["storage"]["items"] if s["exists"]]
            protection = all(
                s.get("protection")
                in ("NSFileProtectionCompleteUntilFirstUserAuthentication", "NSFileProtectionComplete")
                for s in items
            )
            backup = all(s.get("excludedFromBackup") is True for s in items)
            accessible = d["deviceSecret"].get("accessible")
            device, display, revision = d["device"], d["display"], d["app"]["sourceRevision"]
            values: list[tuple[Result, str]] = [
                (
                    "PASS" if protection else "FAIL",
                    "; ".join(f"{s['name']}: {s.get('protection', 'absent')}" for s in items)
                    or "No existing items.",
                ),
                (
                    "PASS" if backup else "FAIL",
                    "; ".join(f"{s['name']}: {s.get('excludedFromBackup', 'absent')}" for s in items)
                    or "No existing items.",
                ),
                ("PASS" if accessible in ("cku", "aku") else "RECORD", accessible or "no item yet"),
                (
                    "FAIL" if revisions and revisions != {revision} else "RECORD",
                    f"{device['model']};"
                    f" {device['os']};"
                    f" native scale {device['nativeScale']};"
                    f" display {display['widthPoints']} x {display['heightPoints']} points;"
                    f" maximum {display['maximumFramesPerSecond']} frames/s;"
                    f" in device table {device['scale']['inDeviceTable']};"
                    f" revision {revision}.",
                ),
            ]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError("Invalid diagnostics file.") from error
        checks.extend(
            Check(f"D{i}", NAMES["D"][i - 1], status, value) for i, (status, value) in enumerate(values, 1)
        )
    for prefix, names in NAMES.items():
        for i, name in enumerate(names, 1):
            ident = f"{prefix}{i}"
            if not any(c.id == ident for c in checks):
                device = "pencil" if prefix == "P" else "tablet"
                needed = f"{device} session"
                if prefix == "D":
                    needed = "diagnostics file"
                elif ident in ("P3", "P7", "P11"):
                    needed = "pencil study session"
                elif ident in ("P9", "W3"):
                    needed = f"{device} qualification session"
                elif ident in ("P10", "W8"):
                    needed = f"{device} bench session with role {'P' if prefix == 'P' else 'W'}"
                checks.append(Check(ident, name, "NOT RUN", f"Missing {needed}."))
    return DeviceReport(datetime.now(UTC).date().isoformat(), tuple(checks), tuple(sessions))


def markdown(report: DeviceReport) -> str:
    """Format the aggregate report as three check tables and a session list."""

    def cell(text: str) -> str:
        return text.replace("|", r"\|").replace("\n", " ").replace("\r", " ")

    lines = ["# SCRIPT device report", "", f"Date: {report.date}", ""]
    for prefix, title in (("P", "iPad"), ("W", "Tablet"), ("D", "Diagnostics")):
        lines += [f"## {title}", "", "| Check | Result | Value |", "| --- | --- | --- |"]
        selected = [
            c for c in report.checks if c.id.startswith(prefix) or (prefix == "P" and c.id.startswith("E"))
        ]
        for c in sorted(selected, key=lambda c: (c.id[0], int(c.id[1:]))):
            origin = f" ({', '.join(c.sessions)})" if c.sessions else ""
            lines.append(f"| {c.id} {c.name} | {c.result} | {cell(c.value + origin)} |")
        lines.append("")
    lines += ["Sessions read:", ""]
    lines += [
        f"- {s.purpose};"
        f" {s.input}{' (simulator)' if s.input == 'directTouchAsPen' else ''};"
        f" {s.short_id};"
        f" {cell(s.revision)}."
        for s in report.sessions
    ]
    counts = Counter(c.result for c in report.checks)
    lines += ["", "; ".join(f"{r}: {counts[r]}" for r in ("PASS", "FAIL", "RECORD", "NOT RUN")) + ".", ""]
    return "\n".join(lines)
