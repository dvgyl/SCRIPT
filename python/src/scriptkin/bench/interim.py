"""The interim check of protocol section 6.6 and the pilot checks of section 6.5.

Neither function compares the two devices. The interim reports variance components per device. The
pilot compares each device with its own command. The commands of `scriptkin.cli` print these rows and
nothing else, so no device mean and no device difference reaches a person before the last block.

Interim. The check reads the runs of blocks 1 to 6 and estimates, per device and feature, the pooled
variance components of section 11.2 over the program set of the feature. It reports sigma_mount in
units of the limit L and whether it is above 0.25 L. For the four camera features it reports the
components of the raw device values and of the camera-referenced differences, because the main analysis
compares the differences and the design assumption of section 11.8 is about the analysed values. A
camera-referenced difference involves one device only.

Pilot. For every recorded run the pilot reports the motion duration against the commanded motion
interval on the device and on the camera (within 1 percent), the band power of a line program against
the band power of its move list (0.5 to 2 times), and the detection of a fault run. Per device it
reports the two noise floors of section 8.1. Per line program it reports the 10-times rule: the
commanded band power against 10 times the larger noise floor of the two devices. The pilot decides the
constants of section 6.5, so it does not read them: it checks every line program of the main set, and
it reports a `fault-dwell` run under both expectations of section 5.3, as `fault_gaps` (a `gaps`
finding with two contact segments) and as `fault_no_gaps` (no `gaps` finding).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from scriptkin import bench
from scriptkin.bench import stats
from scriptkin.bench.analysis import (
    ANALYSES,
    DEVICES,
    LIMITS,
    MAIN_ANALYSIS,
    Analysis,
    groups,
    program_set,
    run_arrays,
)
from scriptkin.bench.camera import ExecutedMovement, MotionCheck
from scriptkin.bench.features import Motion, commanded_motion, feature_cells
from scriptkin.bench.outputs import HOLD_NOISE_FLOOR, SPIRAL_NOISE_FLOOR, Row, sampling_rows
from scriptkin.bench.runs import CAMERA_FEATURES, FEATURE_SET, Evaluated, fault_detected
from scriptkin.bench.study import EvaluatedStudy, clean_medians
from scriptkin.quality import Refusal
from scriptkin.recording import BenchRole

INTERIM_BLOCKS = frozenset(range(1, 7))
SIGMA_MOUNT_SHARE = 0.25
DURATION_TOLERANCE = 0.01
BAND_POWER_RANGE = (0.5, 2.0)
NOISE_FLOOR_FACTOR = 10.0
RAW_DEVICE = next(a for a in ANALYSES if a.name == "raw_device")
FAULT_GAPS = "fault_gaps"
FAULT_NO_GAPS = "fault_no_gaps"

INTERIM_COLUMNS = (
    "device",
    "feature",
    "values",
    "programs",
    "runs",
    "sigma2_run",
    "sigma2_mount",
    "mean_count",
    "sigma_mount",
    "sigma_mount_over_limit",
    "above_quarter_limit",
    "reason",
)


@dataclass(frozen=True)
class InterimRow:
    device: BenchRole
    feature: str
    values: str
    """`raw` for the device values, `camera_referenced` for the differences of section 9.2."""
    programs: int
    runs: int
    components: stats.VarianceComponents

    @property
    def sigma_mount(self) -> float:
        return float(np.sqrt(self.components.mount))

    @property
    def over_limit(self) -> float:
        return self.sigma_mount / LIMITS[self.feature]

    @property
    def above(self) -> bool | None:
        return None if math.isnan(self.over_limit) else self.over_limit > SIGMA_MOUNT_SHARE

    def row(self) -> Row:
        c = self.components
        return {
            "device": self.device,
            "feature": self.feature,
            "values": self.values,
            "programs": self.programs,
            "runs": self.runs,
            "sigma2_run": float(c.run),
            "sigma2_mount": float(c.mount),
            "mean_count": float(c.mean_count),
            "sigma_mount": self.sigma_mount,
            "sigma_mount_over_limit": self.over_limit,
            "above_quarter_limit": self.above,
            "reason": "" if self.above is not None else "fewer than 2 mountings with a valid run",
        }


def _interim_rows(study: EvaluatedStudy, feature: str, values: str, analysis: Analysis) -> list[InterimRow]:
    programs = program_set(feature, study.study)
    arrays = run_arrays(study.runs, study.study.registry, feature, groups(programs, "program"), analysis)
    rows: list[InterimRow] = []
    for device in DEVICES:
        a = arrays.of(device)
        rows.append(
            InterimRow(
                device,
                feature,
                values,
                len(programs),
                int(np.isfinite(a).sum()),
                stats.pooled_variance_components(a),
            )
        )
    return rows


def interim(study: EvaluatedStudy) -> tuple[InterimRow, ...]:
    """Section 6.6 on the evaluated runs, which the caller limits to blocks 1 to 6."""
    rows: list[InterimRow] = []
    for feature in FEATURE_SET:
        rows.extend(_interim_rows(study, feature, "raw", RAW_DEVICE))
        if feature in CAMERA_FEATURES:
            rows.extend(_interim_rows(study, feature, "camera_referenced", MAIN_ANALYSIS))
    return tuple(rows)


def interim_text(rows: Sequence[InterimRow]) -> str:
    """The lines that the interim command prints: variance components only."""
    lines = [
        "device\tfeature\tvalues\tprograms\truns\tsigma2_run\tsigma2_mount\tsigma_mount / L\tabove 0.25 L"
    ]
    for r in rows:
        above = "" if r.above is None else ("yes" if r.above else "no")
        lines.append(
            f"{r.device}\t{r.feature}\t{r.values}\t{r.programs}\t{r.runs}\t{float(r.components.run):.4g}"
            f"\t{float(r.components.mount):.4g}\t{r.over_limit:.3f}\t{above}"
        )
    return "\n".join(lines)


def deviation_row(person: str, when: str) -> str:
    """Section 13: the data access row of `bench/DEVIATIONS.md` for one interim run."""
    return (
        f"| {when} | blocks 1 to 6 | data access: scriptkin bench interim | section 6.6 interim check |"
        f" printed the variance components only; wrote bench/results/interim.csv | {person} |"
    )


PILOT_COLUMNS = (
    "device",
    "program",
    "run_id",
    "kind",
    "source",
    "value",
    "command",
    "ratio",
    "pass",
    "reason",
)


def _duration_rows(run: Evaluated, commanded: Motion | Refusal) -> list[Row]:
    head: Row = {
        "device": run.row.device,
        "program": run.row.program,
        "run_id": run.row.run_id,
        "kind": "duration",
    }
    if isinstance(commanded, Refusal):
        return [{**head, "reason": commanded.detail}]
    command = commanded.interval.duration_s
    rows: list[Row] = []
    device = run.device_value("bench.motion_s")
    rows.append(_ratio_row(head, "device", device, command, lambda r: abs(r - 1) <= DURATION_TOLERANCE))
    if isinstance(run.executed, ExecutedMovement):
        # A camera interval that touches its window edge holds only a part of the movement.
        edge = run.executed.window_edge
        rows.append(
            _ratio_row(
                head,
                "camera",
                run.executed.motion_s,
                command,
                lambda r: abs(r - 1) <= DURATION_TOLERANCE and not edge,
            )
        )
    else:
        reason = run.executed.detail if isinstance(run.executed, Refusal) else "no valid camera record"
        row: Row = {**head, "source": "camera", "reason": reason}
        if run.outcome.motion_check is MotionCheck.ALIGNMENT:
            # The clip has no camera cause and shows a movement that does not match the command.
            row = {**row, "command": command, "pass": False}
        rows.append(row)
    return rows


def _ratio_row(
    head: Row, source: str, value: float | Refusal, command: float, ok: Callable[[float], bool]
) -> Row:
    if isinstance(value, Refusal):
        return {**head, "source": source, "command": command, "reason": value.detail}
    ratio = value / command if command else math.nan
    passed = bool(ok(ratio)) if math.isfinite(ratio) else None
    return {**head, "source": source, "value": value, "command": command, "ratio": ratio, "pass": passed}


def pilot(study: EvaluatedStudy) -> list[Row]:
    """Section 6.5: every check of each device against its own command, as rows of `pilot.csv`."""
    rows: list[Row] = []
    commanded = {pid: commanded_motion(ml) for pid, ml in study.study.move_lists.items()}
    command_power = {
        d.id: feature_cells(commanded[d.id])["bench.band_power_mm2_s2"]
        for d in study.study.main_programs
        if d.geometry == "line"
    }
    for run in sorted(study.runs.values(), key=lambda r: r.row.run_id):
        pid = run.row.program
        if run.row.fault:
            clean = clean_medians(study.runs, run.row.block)
            expectations = (
                ((FAULT_GAPS, True), (FAULT_NO_GAPS, False))
                if bench.program(pid).fault == "dwell"
                else (("fault", False),)
            )
            for kind, gaps_on_p in expectations:
                found, reason = (
                    (False, "no valid loops-l-v20 run in the block")
                    if clean is None
                    else fault_detected(run, clean, gaps_on_p=gaps_on_p)
                )
                rows.append(
                    {
                        "device": run.row.device,
                        "program": pid,
                        "run_id": run.row.run_id,
                        "kind": kind,
                        "source": "device",
                        "pass": found,
                        "reason": reason,
                    }
                )
            continue
        rows.extend(_duration_rows(run, commanded[pid]))
        if pid in command_power:
            head: Row = {
                "device": run.row.device,
                "program": pid,
                "run_id": run.row.run_id,
                "kind": "band_power",
            }
            command = command_power[pid]
            if isinstance(command, Refusal):
                rows.append({**head, "source": "device", "reason": command.detail})
            else:
                rows.append(
                    _ratio_row(
                        head,
                        "device",
                        run.device_value("bench.band_power_mm2_s2"),
                        command,
                        lambda r: BAND_POWER_RANGE[0] <= r <= BAND_POWER_RANGE[1],
                    )
                )
    floors: list[float] = []
    for row in sampling_rows(study.study, study.runs, study.hold):
        if row["kind"] in (SPIRAL_NOISE_FLOOR, HOLD_NOISE_FLOOR) and row["mounting"] == "all":
            value = row["band_power_mm2_s2"]
            if value is not None:
                floors.append(float(value))
            rows.append(
                {
                    "device": row["device"],
                    "kind": row["kind"],
                    "source": "device",
                    "value": value,
                    "reason": row["reason"],
                }
            )
    largest = max(floors) if floors else None
    for pid, command in command_power.items():
        head = {"program": pid, "kind": "ten_times", "source": "command"}
        if isinstance(command, Refusal):
            rows.append({**head, "reason": command.detail})
        elif largest is None:
            rows.append({**head, "value": command, "reason": "no noise floor"})
        else:
            ratio = command / largest if largest > 0 else math.inf
            rows.append(
                {
                    **head,
                    "value": command,
                    "command": NOISE_FLOOR_FACTOR * largest,
                    "ratio": ratio,
                    "pass": ratio >= NOISE_FLOOR_FACTOR,
                }
            )
    return rows


def pilot_text(rows: Sequence[Row]) -> str:
    """A summary of the pilot per device and check: the counts of passed checks."""
    counts: dict[tuple[str, str, str], list[int]] = {}
    for r in rows:
        key = (str(r.get("device", "")), str(r["kind"]), str(r.get("source", "")))
        passed = r.get("pass")
        counts.setdefault(key, [0, 0])
        if passed is not None:
            counts[key][0] += int(bool(passed))
            counts[key][1] += 1
    lines = ["device\tcheck\tsource\tpassed\tchecked"]
    for (device, kind, source), (passed, total) in sorted(counts.items()):
        lines.append(f"{device}\t{kind}\t{source}\t{passed}\t{total}")
    return "\n".join(lines)
