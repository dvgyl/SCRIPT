"""The output files of the bench analysis in `bench/results/` (protocol sections 8, 11, 12, and 15).

Every table is a CSV file with a header. A value that the statistics cannot give is an empty cell, and
the `reason` column of the row says why. The text `nan` never appears. Numbers are written with 12
significant digits.

`features.csv` is the `FeatureTable` of the library features of section 9.3, one row per feature cell of
every recorded run, with its provenance file beside it. The table has no participant, so its
`participant` column holds the run id, its `site` is `bench`, and its `administration`, `age_band`, and
`hand` are `unknown`. The bench cells of every run, from the device and from the camera, do not fit the
table and go into `bench_features.csv` with the run id. `run_status.csv` gives the outcome of section 6.4
of every run. The statistics go into `agreement.csv`, `bland_altman.csv`, and `correction.csv`, one row
set per analysis with the `analysis` column naming it, and `deliverables.json` holds section 12 for the
main analysis. `sampling.csv`, `accuracy.csv`, and `gate.csv` hold sections 8.1, 8.2, and 11.7.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from scriptkin import __version__, bench
from scriptkin.bench import stats
from scriptkin.bench.analysis import MAIN_ANALYSIS, AnalysisResult, FeatureResult
from scriptkin.bench.camera import ExecutedMovement, PathFit, SpatialAccuracy, TimeAlignment
from scriptkin.bench.features import CATALOG as BENCH_CATALOG
from scriptkin.bench.inputs import RunRow
from scriptkin.bench.runs import CleanMedians, Evaluated, fault_detected
from scriptkin.bench.study import EvaluatedStudy, Study, clean_medians
from scriptkin.features import FeatureRow, FeatureTable, Provenance, catalog_digest
from scriptkin.quality import DEFAULT_QC, Accepted, Refusal
from scriptkin.recording import BenchRole
from scriptkin.signal import DEFAULT_PREPROCESSING

Row = dict[str, Any]
DEVICES: tuple[BenchRole, ...] = ("P", "W")
SPIRAL_NOISE_FLOOR = "noise_floor_spiral"
HOLD_NOISE_FLOOR = "noise_floor_hold"


def number(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.12g}"


def flag(value: bool | np.bool_ | None) -> str:
    return "" if value is None else ("true" if bool(value) else "false")


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Row]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_text(row.get(c, "")) for c in columns])
    return path


def _text(value: Any) -> str:
    if isinstance(value, bool | np.bool_):
        return flag(bool(value))
    if isinstance(value, float | np.floating):
        return number(float(value))
    if value is None:
        return ""
    return str(value)


STATUS_COLUMNS = (
    "run_id",
    "device",
    "mounting",
    "block",
    "position",
    "program",
    "run_index",
    "kind",
    "status",
    "accepted",
    "rejection",
    "marks",
    "camera",
    "camera_rules",
    "motion_failure",
    "failed",
    "valid",
    "lag_s",
    "correlation",
    "reason",
)


def status_rows(study: Study, evaluated: Mapping[str, Evaluated]) -> list[Row]:
    rows: list[Row] = []
    for row in study.rows:
        base: Row = {
            "run_id": row.run_id,
            "device": row.device,
            "mounting": row.mounting,
            "block": row.block,
            "position": row.position,
            "program": row.program,
            "run_index": row.run_index,
            "kind": "fault" if row.fault else "main",
            "status": row.status,
        }
        run = evaluated.get(row.run_id)
        if run is None:
            base["reason"] = f"deviation row {row.deviation_row}" if row.deviation_row else "not recorded"
            rows.append(base)
            continue
        o = run.outcome
        alignment = run.alignment if isinstance(run.alignment, TimeAlignment) else None
        base.update(
            accepted=not o.rejected,
            rejection=";".join(o.rejected),
            marks=";".join(o.marks),
            camera=o.camera,
            camera_rules=";".join(o.camera_rules),
            motion_failure=o.motion_failure,
            failed=o.failed,
            valid=not o.failed and not row.fault,
            lag_s=alignment.lag_s if alignment else None,
            correlation=alignment.correlation if alignment else None,
            reason=o.reason,
        )
        rows.append(base)
    return rows


def features_table(study: EvaluatedStudy) -> FeatureTable:
    """Section 9.3: the library features of every recorded run in the `FeatureTable` format."""
    rows: list[FeatureRow] = []
    hashes: set[str] = set()
    profiles: set[str] = set()
    for run in study.runs.values():
        journal = study.journals[run.row.session_id]
        hashes.update(journal.recording.source_hashes)
        profiles.add(journal.recording.reader_profile)
        context = journal.recording.context
        rows.append(
            FeatureRow(
                run.row.run_id,
                run.row.session_id,
                "bench",
                "unknown",
                "unknown",
                None,
                context.opened_at if context else None,
                run.trial.device.model,
                run.trial.task.id,
                run.trial.index,
                run.trial.attempt,
                "unknown",
                (),
                run.outcome.rejected,
                dict(run.library),
            )
        )
    provenance = Provenance(
        __version__,
        DEFAULT_QC,
        DEFAULT_PREPROCESSING,
        catalog_digest(),
        tuple(sorted(hashes)),
        tuple(sorted(profiles)),
    )
    return FeatureTable(tuple(rows), provenance)


BENCH_COLUMNS = (
    "run_id",
    "device",
    "mounting",
    "block",
    "program",
    "run_index",
    "source",
    "feature",
    "value",
    "refusal",
    "detail",
)


def bench_feature_rows(evaluated: Mapping[str, Evaluated]) -> list[Row]:
    rows: list[Row] = []
    for run in evaluated.values():
        for source in ("device", "camera"):
            for feature in BENCH_CATALOG:
                cell = (
                    run.device_value(feature.name) if source == "device" else run.camera_value(feature.name)
                )
                refused = isinstance(cell, Refusal)
                rows.append(
                    {
                        "run_id": run.row.run_id,
                        "device": run.row.device,
                        "mounting": run.row.mounting,
                        "block": run.row.block,
                        "program": run.row.program,
                        "run_index": run.row.run_index,
                        "source": source,
                        "feature": feature.name,
                        "value": None if refused else cell,
                        "refusal": cell.code if refused else "",
                        "detail": cell.detail if refused else "",
                    }
                )
    return rows


AGREEMENT_COLUMNS = (
    "analysis",
    "feature",
    "level",
    "program",
    "limit",
    "scale",
    "estimable",
    "bias",
    "se",
    "df",
    "lower",
    "upper",
    "p",
    "equivalent",
    "feature_p",
    "adjusted",
    "unadjusted",
    "reason",
)


def _not_estimable(result: FeatureResult, g: int) -> str:
    counts = {d: stats.valid_counts(result.arrays.of(d)[g]).tolist() for d in DEVICES}
    short = [f"{d} {c}" for d, c in counts.items() if any(n < stats.MIN_VALID_RUNS for n in c)]
    return "fewer than 4 valid runs in a mounting: " + "; ".join(short) if short else "not estimable"


def agreement_rows(analyses: Sequence[AnalysisResult]) -> list[Row]:
    rows: list[Row] = []
    for result in analyses:
        for k, f in enumerate(result.features):
            interval = f.bias.interval
            for g, group in enumerate(f.arrays.groups):
                estimable = bool(f.bias.estimable[g])
                rows.append(
                    {
                        "analysis": result.analysis.name,
                        "feature": f.feature,
                        "level": "program",
                        "program": group.label,
                        "limit": f.limit,
                        "scale": f.scale,
                        "estimable": estimable,
                        "bias": _finite(f.bias.estimate[g]),
                        "se": _finite(f.bias.se[g]),
                        "df": _finite(f.bias.df[g]),
                        "lower": _finite(interval.lower[g]),
                        "upper": _finite(interval.upper[g]),
                        "p": float(f.p[g]),
                        "equivalent": bool(f.p[g] <= stats.ALPHA),
                        "reason": "" if estimable else _not_estimable(f, g),
                    }
                )
            rows.append(
                {
                    "analysis": result.analysis.name,
                    "feature": f.feature,
                    "level": "feature",
                    "program": "",
                    "limit": f.limit,
                    "scale": f.scale,
                    "feature_p": f.feature_p,
                    "adjusted": bool(result.decision.adjusted[k]),
                    "unadjusted": bool(result.decision.unadjusted[k]),
                    "reason": "" if f.arrays.groups else "The program set is empty.",
                }
            )
    return rows


BLAND_ALTMAN_COLUMNS = (
    "analysis",
    "feature",
    "kind",
    "program",
    "pair",
    "difference",
    "average",
    "mean",
    "sd",
    "lower",
    "upper",
    "reason",
)


def bland_altman_rows(analyses: Sequence[AnalysisResult]) -> list[Row]:
    rows: list[Row] = []
    for result in analyses:
        for f in result.features:
            head: Row = {"analysis": result.analysis.name, "feature": f.feature}
            ba = f.bland_altman
            if ba is None:
                rows.append({**head, "kind": "pooled", "reason": f.bland_altman_reason})
                continue
            for g, group in enumerate(f.arrays.groups):
                for i in range(ba.difference.shape[-1]):
                    d, a = _finite(ba.difference[g, i]), _finite(ba.average[g, i])
                    rows.append(
                        {
                            **head,
                            "kind": "point",
                            "program": group.label,
                            "pair": i + 1,
                            "difference": d,
                            "average": a,
                            "reason": "" if d is not None else "not estimable on both devices",
                        }
                    )
            for kind, limits, labels in (
                ("program", ba.program, [g.label for g in f.arrays.groups]),
                ("pooled", ba.pooled, [""]),
                ("single_run", ba.single_run, [""]),
            ):
                for g, label in enumerate(labels):
                    mean = _finite(np.ravel(limits.mean)[g])
                    rows.append(
                        {
                            **head,
                            "kind": kind,
                            "program": label,
                            "mean": mean,
                            "sd": _finite(np.ravel(limits.sd)[g]),
                            "lower": _finite(np.ravel(limits.lower)[g]),
                            "upper": _finite(np.ravel(limits.upper)[g]),
                            "reason": "" if mean is not None else "a program of the set is not estimable",
                        }
                    )
    return rows


CORRECTION_COLUMNS = (
    "analysis",
    "feature",
    "model",
    "kind",
    "term",
    "program",
    "estimate",
    "se",
    "fitted",
    "lower",
    "upper",
    "within_limit",
    "reason",
)


def correction_rows(analyses: Sequence[AnalysisResult]) -> list[Row]:
    rows: list[Row] = []
    for result in analyses:
        for f in result.features:
            head: Row = {"analysis": result.analysis.name, "feature": f.feature, "model": f.model or ""}
            c = f.correction
            if c is None:
                rows.append({**head, "kind": "coefficient", "reason": f.correction_reason})
                continue
            fitted = {
                "alpha": True,
                "beta": c.model.fits_slope,
                "gamma_u": c.model.fits_covariates,
                "gamma_v": c.model.fits_covariates,
            }
            for term, is_fitted in fitted.items():
                estimate = _finite(float(getattr(c.fit, term)))
                rows.append(
                    {
                        **head,
                        "kind": "coefficient",
                        "term": term,
                        "estimate": estimate,
                        "se": _finite(float(getattr(c.se, term))) if is_fitted else None,
                        "fitted": is_fitted,
                        "reason": ""
                        if estimate is not None
                        else "a program of the set is not estimable"
                        if not is_fitted
                        else "",
                    }
                )
            within = c.residual.within(f.limit)
            for g, group in enumerate(f.arrays.groups):
                estimate = _finite(c.residual.estimate[g])
                rows.append(
                    {
                        **head,
                        "kind": "residual",
                        "program": group.label,
                        "estimate": estimate,
                        "lower": _finite(c.residual.lower[g]),
                        "upper": _finite(c.residual.upper[g]),
                        "within_limit": bool(within[g]),
                        "reason": "" if estimate is not None else "a program of the set is not estimable",
                    }
                )
    return rows


SAMPLING_COLUMNS = (
    "device",
    "program",
    "mounting",
    "kind",
    "runs",
    "median_s",
    "p5_low_s",
    "p5_high_s",
    "p95_low_s",
    "p95_high_s",
    "share_above_30ms",
    "share_above_max_gap",
    "band_power_mm2_s2",
    "reason",
)


def _values(runs: Iterable[Evaluated], name: str) -> list[float]:
    return [v for v in (r.device_value(name) for r in runs) if not isinstance(v, Refusal)]


def _median(values: Sequence[float]) -> float | None:
    return float(np.median(values)) if values else None


def sampling_rows(
    study: Study, evaluated: Mapping[str, Evaluated], hold: Mapping[tuple[BenchRole, str], float | Refusal]
) -> list[Row]:
    """Section 8.1 per device and program over the valid main runs, and the two noise floors."""
    rows: list[Row] = []
    valid = [r for r in evaluated.values() if not r.row.fault and not r.outcome.failed]
    for device in DEVICES:
        for program in study.main_programs:
            runs = [r for r in valid if r.row.device == device and r.row.program == program.id]
            medians = _values(runs, "sampling.median_s")
            p5, p95 = _values(runs, "sampling.p5_s"), _values(runs, "sampling.p95_s")
            rows.append(
                {
                    "device": device,
                    "program": program.id,
                    "kind": "intervals",
                    "runs": len(medians),
                    "median_s": _median(medians),
                    "p5_low_s": min(p5) if p5 else None,
                    "p5_high_s": max(p5) if p5 else None,
                    "p95_low_s": min(p95) if p95 else None,
                    "p95_high_s": max(p95) if p95 else None,
                    "share_above_30ms": _median(_values(runs, "sampling.share_above_30ms")),
                    "share_above_max_gap": _median(_values(runs, "sampling.share_above_max_gap")),
                    "reason": "" if medians else "no valid run with sample intervals",
                }
            )
        mountings = study.registry.mountings_of(device)
        spiral_all: list[float] = []
        for record in (*mountings, None):
            code = record.mounting if record else "all"
            if record is not None:
                runs = [
                    r
                    for r in valid
                    if r.row.device == device
                    and r.row.mounting == code
                    and bench.program(r.row.program).geometry == "spiral"
                ]
                spiral = _values(runs, "bench.band_power_mm2_s2")
                spiral_all.extend(spiral)
            else:
                spiral = spiral_all
            rows.append(
                {
                    "device": device,
                    "mounting": code,
                    "kind": SPIRAL_NOISE_FLOOR,
                    "runs": len(spiral),
                    "band_power_mm2_s2": _median(spiral),
                    "reason": "" if spiral else "no valid spiral run with a band power",
                }
            )
        holds: list[float] = []
        for record in mountings:
            value = hold.get((device, record.mounting))
            found = value if isinstance(value, float) else None
            if found is not None:
                holds.append(found)
            reason = (
                "no qualification session in the registry"
                if value is None
                else value.detail
                if isinstance(value, Refusal)
                else ""
            )
            rows.append(
                {
                    "device": device,
                    "mounting": record.mounting,
                    "kind": HOLD_NOISE_FLOOR,
                    "runs": 1 if found is not None else 0,
                    "band_power_mm2_s2": found,
                    "reason": reason,
                }
            )
        rows.append(
            {
                "device": device,
                "mounting": "all",
                "kind": HOLD_NOISE_FLOOR,
                "runs": len(holds),
                "band_power_mm2_s2": _median(holds),
                "reason": "" if holds else "no hold record",
            }
        )
    return rows


ACCURACY_COLUMNS = ("device", "program", "measure", "runs", "median", "low", "high", "reason")
MEASURES = (
    "camera_rotation_deg",
    "camera_rms_mm",
    "camera_p95_mm",
    "lag_s",
    "trace_rms_mm",
    "trace_rotation_deg",
    "executed_max_mm",
    "executed_duration_error",
)


def _measure(run: Evaluated, measure: str) -> float | None:
    accuracy = run.accuracy if isinstance(run.accuracy, SpatialAccuracy) else None
    alignment = run.alignment if isinstance(run.alignment, TimeAlignment) else None
    trace = run.trace if isinstance(run.trace, PathFit) else None
    executed = run.executed if isinstance(run.executed, ExecutedMovement) else None
    values: dict[str, float | None] = {
        "camera_rotation_deg": accuracy.rotation_deg if accuracy else None,
        "camera_rms_mm": accuracy.rms_mm if accuracy else None,
        "camera_p95_mm": accuracy.p95_mm if accuracy else None,
        "lag_s": alignment.lag_s if alignment else None,
        "trace_rms_mm": trace.rms_mm if trace else None,
        "trace_rotation_deg": trace.rotation_deg if trace else None,
        "executed_max_mm": executed.fit.max_mm if executed else None,
        "executed_duration_error": executed.motion_s / executed.commanded_s - 1 if executed else None,
    }
    return values[measure]


def accuracy_rows(study: Study, evaluated: Mapping[str, Evaluated]) -> list[Row]:
    """Section 8.2 and 11.6: medians with ranges over the valid main runs, per device and program."""
    rows: list[Row] = []
    valid = [r for r in evaluated.values() if not r.row.fault and not r.outcome.failed]
    for device in DEVICES:
        for program in study.main_programs:
            runs = [r for r in valid if r.row.device == device and r.row.program == program.id]
            for measure in MEASURES:
                values = [v for v in (_measure(r, measure) for r in runs) if v is not None]
                rows.append(
                    {
                        "device": device,
                        "program": program.id,
                        "measure": measure,
                        "runs": len(values),
                        "median": _median(values),
                        "low": min(values) if values else None,
                        "high": max(values) if values else None,
                        "reason": "" if values else "no valid run with this measure",
                    }
                )
    return rows


GATE_COLUMNS = (
    "device",
    "program",
    "kind",
    "run_id",
    "successes",
    "total",
    "share",
    "lower",
    "upper",
    "detected",
    "reason",
)


def _share_row(device: BenchRole, program: str, kind: str, successes: int, total: int) -> Row:
    row: Row = {"device": device, "program": program, "kind": kind, "successes": successes, "total": total}
    if total:
        interval = stats.clopper_pearson(successes, total)
        row.update(share=float(interval.estimate), lower=float(interval.lower), upper=float(interval.upper))
    else:
        row["reason"] = "no run"
    return row


def gate_rows(study: Study, evaluated: Mapping[str, Evaluated]) -> list[Row]:
    """Sections 5.3 and 11.7: the accepted share per device, and the fault detection per fault and device."""
    rows: list[Row] = []
    gaps_on_p = study.registry.constants.fault_dwell_gaps_on_p
    medians: dict[int, CleanMedians | None] = {}
    for device in DEVICES:
        main = [r for r in evaluated.values() if r.row.device == device and not r.row.fault]
        accepted = sum(isinstance(r.qc, Accepted) for r in main)
        rows.append(_share_row(device, "", "accepted", accepted, len(main)))
    for fault in bench.FAULTS:
        for device in DEVICES:
            runs = sorted(
                (r for r in evaluated.values() if r.row.device == device and r.row.program == fault.id),
                key=lambda r: r.row.run_id,
            )
            detected = 0
            detail: list[Row] = []
            for run in runs:
                block = run.row.block
                if block not in medians:
                    medians[block] = clean_medians(evaluated, block)
                clean = medians[block]
                if clean is None:
                    found, reason = False, "no valid loops-l-v20 run in the block"
                else:
                    found, reason = fault_detected(run, clean, gaps_on_p=gaps_on_p)
                detected += found
                detail.append(
                    {
                        "device": device,
                        "program": fault.id,
                        "kind": "fault_run",
                        "run_id": run.row.run_id,
                        "detected": found,
                        "reason": reason,
                    }
                )
            rows.append(_share_row(device, fault.id, "detected", detected, len(runs)))
            rows.extend(detail)
    return rows


def _json_number(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _interval(interval: stats.Interval, g: int) -> dict[str, float | None]:
    return {
        "estimate": _json_number(interval.estimate[g]),
        "lower": _json_number(interval.lower[g]),
        "upper": _json_number(interval.upper[g]),
    }


def deliverables(study: Study, result: AnalysisResult) -> dict[str, Any]:
    """Section 12 for the main analysis. A value that is not estimable is null."""
    features: dict[str, Any] = {}
    for f in result.features:
        programs: dict[str, Any] = {}
        factor = stats.factor(f.bias) if f.scale == "ln" else None
        for g, group in enumerate(f.arrays.groups):
            programs[group.label] = {
                "bias": _interval(f.bias.interval, g),
                "se": _json_number(f.bias.se[g]),
                "factor": _interval(factor, g) if factor is not None else None,
                "components": {
                    d: {
                        "run": _json_number(f.components[d].run[g]),
                        "mount": _json_number(f.components[d].mount[g]),
                    }
                    for d in DEVICES
                },
            }
        correction: dict[str, Any] | None = None
        if f.correction is not None:
            c = f.correction
            correction = {
                "model": str(c.model),
                "coefficients": {
                    term: {
                        "estimate": _json_number(float(getattr(c.fit, term))),
                        "se": _json_number(float(getattr(c.se, term))),
                    }
                    for term in ("alpha", "beta", "gamma_u", "gamma_v")
                },
                "residual": {
                    group.label: {
                        **_interval(c.residual, g),
                        "withinLimit": bool(c.residual.within(f.limit)[g]),
                    }
                    for g, group in enumerate(f.arrays.groups)
                },
            }
        designs = [d for group in f.arrays.groups for d in group.programs]
        features[f.feature] = {
            "limit": f.limit,
            "scale": f.scale,
            "featureP": f.feature_p,
            "equivalent": {
                "adjusted": bool(result.decision.adjusted[result.features.index(f)]),
                "unadjusted": bool(result.decision.unadjusted[result.features.index(f)]),
            },
            "programs": programs,
            "correction": correction,
            "range": {
                "geometries": sorted({d.geometry for d in designs}),
                "scales": sorted({d.scale for d in designs}),
                "speedsMMPerS": sorted({d.speed_mm_s for d in designs}),
                "oscillationHz": sorted({d.oscillation_hz for d in designs if d.oscillation_hz}),
            },
        }
    return {
        "protocol": {"version": bench.PROTOCOL_VERSION},
        "lock": {"commit": study.lock.commit if study.lock else None},
        "library": f"scriptkin {__version__}",
        "analysis": result.analysis.name,
        "features": features,
    }


def write_results(study: EvaluatedStudy, analyses: Sequence[AnalysisResult], folder: Path) -> list[Path]:
    """Write every output of section 15 into the folder and return the paths."""
    folder.mkdir(parents=True, exist_ok=True)
    runs = study.runs
    main = next(a for a in analyses if a.analysis is MAIN_ANALYSIS)
    table = features_table(study)
    table.to_csv(folder / "features.csv")
    written = [folder / "features.csv", folder / "features.csv.provenance.json"]
    written.append(write_csv(folder / "bench_features.csv", BENCH_COLUMNS, bench_feature_rows(runs)))
    written.append(write_csv(folder / "run_status.csv", STATUS_COLUMNS, status_rows(study.study, runs)))
    written.append(write_csv(folder / "agreement.csv", AGREEMENT_COLUMNS, agreement_rows(analyses)))
    written.append(write_csv(folder / "bland_altman.csv", BLAND_ALTMAN_COLUMNS, bland_altman_rows(analyses)))
    written.append(write_csv(folder / "correction.csv", CORRECTION_COLUMNS, correction_rows(analyses)))
    written.append(
        write_csv(folder / "sampling.csv", SAMPLING_COLUMNS, sampling_rows(study.study, runs, study.hold))
    )
    written.append(write_csv(folder / "accuracy.csv", ACCURACY_COLUMNS, accuracy_rows(study.study, runs)))
    written.append(write_csv(folder / "gate.csv", GATE_COLUMNS, gate_rows(study.study, runs)))
    path = folder / "deliverables.json"
    path.write_text(
        json.dumps(deliverables(study.study, main), indent=1, allow_nan=False) + "\n", encoding="utf-8"
    )
    written.append(path)
    return written


def status_of_rows(rows: Sequence[RunRow]) -> dict[str, RunRow]:
    return {r.run_id: r for r in rows}
