"""Auditable study units and deterministic tables. Units support exact restarts."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import multiprocessing
import platform
import subprocess
import time
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from itertools import groupby, islice
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import scipy
import sklearn
from scipy.stats import t

from scriptkin.bench.analysis import correction_model
from scriptkin.consequence.generate import generate_cohort, observe
from scriptkin.consequence.inputs import BenchInputs, Centers, for_cell, grid_inputs
from scriptkin.consequence.metrics import measure, paired_contrasts, summarize, weighted_auc
from scriptkin.consequence.plan import (
    LIMITS,
    CellSpec,
    ConsequencePlan,
    SeedKey,
    cells,
    plan_digest,
    planned_replicates,
    seed_for,
)
from scriptkin.consequence.storage import (
    archive_cell,
    clean,
    compressed_text,
    expand_unit,
    pack_unit,
    read_restart_unit,
    read_unit,
    stored_units,
    unit_name,
    write_compressed_json,
)
from scriptkin.consequence.validation import (
    AnalysisMatrix,
    Failure,
    PredictionSet,
    SplitPlan,
    evaluate_matrix,
    make_splits,
    matrix,
    permute_participants,
)
from scriptkin.evaluate.metrics import fold_auc_null_sd

Row = dict[str, Any]
TABLES = (
    "inputs",
    "corrections",
    "replicates",
    "contrasts",
    "decision_curves",
    "calibration",
    "splits",
    "failures",
)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(clean(value), sort_keys=True, indent=1, allow_nan=False) + "\n", encoding="utf-8"
    )


def write_csv(path: Path, rows: list[Row]) -> None:
    columns = sorted({k for r in rows for k in r}) or ["status", "reason"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(clean(rows))


def membership_hash(ids: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(ids), separators=(",", ":")).encode()).hexdigest()


def compact_membership(train: list[str], test: list[str], *, overlap: int) -> Row:
    return {
        "training_sha256": membership_hash(train),
        "test_sha256": membership_hash(test),
        "training_count": len(train),
        "test_count": len(test),
        "test_overlap": overlap,
    }


def fit_rows(prediction: PredictionSet, data: AnalysisMatrix, splits: SplitPlan, *, full: bool) -> list[Row]:
    if full:
        return list(prediction.audit.records)
    grouped: dict[tuple[int, str], list[Row]] = defaultdict(list)
    for raw_record in prediction.audit.records:
        record: Row = dict(raw_record)
        grouped[(int(record["fold"]), str(record["stage"]))].append(record)
    result = []
    for (fold, stage), records in sorted(grouped.items()):
        train = sorted({row for record in records for row in record["training_rows"]})
        test_indices = splits.folds[fold].test
        test = [data.row_ids[i] for i in test_indices]
        result.append(
            {
                "fold": fold,
                "stage": stage,
                "detail": "compact",
                **compact_membership(train, test, overlap=prediction.audit.overlaps[fold]),
                "fit_count": len(records),
                "fit_training_sha256": [membership_hash(r["training_rows"]) for r in records],
            }
        )
    return result


def cell_metadata(plan: ConsequencePlan, cell: CellSpec, inputs: BenchInputs) -> Row:
    common: Row = {
        "source": inputs.source,
        "cell_id": cell.id,
        "feature": "joint" if len(cell.features) > 1 else next(iter(cell.features)),
        **asdict(cell),
        "planned_R": planned_replicates(plan, cell),
        "post_hoc_bench": inputs.post_hoc,
        "correction_support": ";".join(f.support for f in inputs.features),
        "reliability": "reliability_not_established",
    }
    common["feature"] = "joint" if len(cell.features) > 1 else next(iter(cell.features))
    if inputs.source == "measured":
        common["bias_multiple"] = None
    return common


def input_tables(common: Row, inputs: BenchInputs) -> dict[str, list[Row]]:
    tables: dict[str, list[Row]] = {"inputs": [], "corrections": []}
    for f in inputs.features:
        program = f.selection.program
        tables["inputs"].append(
            {
                **common,
                "feature": f.feature,
                "program": program.id,
                "geometry": program.geometry,
                "scale": program.scale,
                "speed_mm_s": program.speed_mm_s,
                "oscillation_hz": program.oscillation_hz,
                "target_scale": 1.0,
                "target_speed_mm_s": 20.0,
                "target_geometry": "line" if f.feature == "bench.band_power_mm2_s2" else "loops",
                "distance": f.selection.distance,
                "in_range": f.selection.in_range,
                "limit": LIMITS[f.feature],
                "comparison_scale": "absolute" if f.feature == "bench.speed_cv" else "ln",
                "bias": f.bias,
                "lower": f.lower,
                "upper": f.upper,
                "center": f.center,
                "factor": math.exp(f.bias) if f.feature != "bench.speed_cv" else None,
                "run_p": f.run_p,
                "mount_p": f.mount_p,
                "run_w": f.run_w,
                "mount_w": f.mount_w,
                "bias_basis": f.bias_basis,
                "reason": f.reason,
            }
        )
        correction_base = {
            **common,
            "feature": f.feature,
            "program": f.selection.program.id,
            "support": f.support,
            "model": correction_model(f.feature).value,
        }
        if inputs.raw:
            delivered = json.loads(inputs.raw)["features"][f.feature]["correction"]
            if delivered:
                for term, values in delivered["coefficients"].items():
                    tables["corrections"].append({**correction_base, "term": term, **values})
                for program_id, residual in delivered["residual"].items():
                    tables["corrections"].append(
                        {**correction_base, "program": program_id, "term": "residual", **residual}
                    )
            else:
                tables["corrections"].append({**correction_base, "status": "unavailable"})
        else:
            for term, value in asdict(f.correction).items() if f.correction else ():
                tables["corrections"].append(
                    {**correction_base, "term": term, "estimate": value, "se": None, "fitted": False}
                )
    return tables


def runs_null(cell: CellSpec, replicate: int, scheme: str, version: str, *, smoke: bool) -> bool:
    return (
        not smoke
        and cell.sensitivity == "primary"
        and cell.bias_multiple == 0
        and cell.noise_id == "nominal"
        and 0 <= replicate < 20
        and version == "uncorrected"
        and scheme in ("participant", "device")
    )


def run_unit(
    plan: ConsequencePlan, cell: CellSpec, replicate: int, inputs: BenchInputs, *, smoke: bool
) -> Row:
    full_audit = smoke or replicate == 0
    tables: dict[str, list[Row]] = {name: [] for name in TABLES}
    audit: list[Row] = []
    full_log: list[Row] = []
    split_audit: list[Row] = []
    seeds: dict[int, Row] = {}

    def seed(stream: int) -> int:
        key = SeedKey(cell.effect, cell.n, cell.prevalence, replicate, stream)
        value = seed_for(plan.seed, key)
        seeds[stream] = {"key": asdict(key), "seed": value}
        return value

    def record_fit(prediction: PredictionSet, data: AnalysisMatrix, splits: SplitPlan, base: Row) -> None:
        audit.extend({**base, **row} for row in fit_rows(prediction, data, splits, full=False))
        if full_audit:
            full_log.extend({**base, **row} for row in fit_rows(prediction, data, splits, full=True))

    cohort = generate_cohort(cell, seed=seed(1))
    versions = observe(cohort, inputs, seed=seed(2), floor_cv=cell.sensitivity == "floor_cv")
    common = {
        **cell_metadata(plan, cell, inputs),
        "replicate": replicate,
        "negative_cv": versions.negative_cv,
    }
    tables.update(input_tables(common, inputs))
    for scheme, direction in (
        ("participant", "internal"),
        ("record", "internal"),
        ("device", "W-to-P"),
        ("device", "P-to-W"),
    ):
        metrics = {}
        baseline_rows: list[Row] = []
        split_seed, fit_seed = seed(3), seed(4)
        data = matrix(cohort, versions.reference, scheme)
        splits = make_splits(data, scheme, seed=split_seed, folds=plan.folds, direction=direction)
        base = {**common, "validation": scheme, "direction": direction}
        audit_base = {k: base[k] for k in ("cell_id", "replicate", "validation", "direction")}
        if full_audit:
            for number, fold in enumerate(splits.folds):
                for role, indices in (("train", fold.train), ("test", fold.test)):
                    for i in indices:
                        person = int(i) // 2 if scheme == "record" else int(i)
                        tables["splits"].append(
                            {
                                **base,
                                "fold": number,
                                "row_id": data.row_ids[i],
                                "participant_id": data.design.ids[i],
                                "device": int(data.device[i]),
                                "site": int(data.site[i]),
                                "mounting_block": int(cohort.block[person]),
                                "role": role,
                            }
                        )
        for number, fold in enumerate(splits.folds):
            train = [data.row_ids[i] for i in fold.train]
            test = [data.row_ids[i] for i in fold.test]
            overlap = len({data.design.ids[i] for i in fold.train} & {data.design.ids[i] for i in fold.test})
            split_audit.append(
                {
                    **audit_base,
                    "fold": number,
                    "stage": "outer",
                    "detail": "compact",
                    **compact_membership(train, test, overlap=overlap),
                }
            )
        for baseline_name, context in (("device_only", data.device), ("site_only", data.site)):
            predictors = np.column_stack([context == level for level in np.unique(context)]).astype(float)
            baseline_data = replace(
                data,
                design=replace(
                    data.design,
                    x=predictors,
                    columns=tuple(f"{baseline_name}_{i}" for i in range(predictors.shape[1])),
                ),
            )
            baseline = evaluate_matrix(baseline_data, plan, splits, seed=fit_seed, model=cell.model)
            if isinstance(baseline, PredictionSet):
                baseline_metric = measure(
                    baseline, prevalence=cell.prevalence, thresholds=np.array(plan.thresholds)
                )
                baseline_rows.extend(
                    {
                        **base,
                        "version": baseline_name,
                        "metric": metric,
                        "estimate": value,
                        "status": "ok" if math.isfinite(value) else "unavailable",
                    }
                    for metric, value in baseline_metric.values.items()
                )
                record_fit(baseline, baseline_data, splits, {**audit_base, "version": baseline_name})
        tables["replicates"].extend(baseline_rows)
        for version in ("reference", "uncorrected", "corrected"):
            x = getattr(versions, version)
            prediction = (
                Failure("missing_selected_program_value")
                if any(f.reason for f in inputs.features)
                else Failure("correction_unavailable")
                if x is None
                else evaluate_matrix(matrix(cohort, x, scheme), plan, splits, seed=fit_seed, model=cell.model)
            )
            if isinstance(prediction, Failure):
                tables["failures"].append(
                    {
                        **base,
                        "version": version,
                        "stage": "evaluation",
                        "reason": prediction.reason,
                        "planned": prediction.reason
                        in ("one_class_holdout", "not_applicable_one_device", "correction_unavailable"),
                    }
                )
                for metric in ("auc", "intercept", "slope", "brier", "mean_nb"):
                    tables["replicates"].append(
                        {
                            **base,
                            "version": version,
                            "metric": metric,
                            "estimate": None,
                            "status": "unavailable",
                            "reason": prediction.reason,
                        }
                    )
                tables["decision_curves"].extend(
                    {
                        **base,
                        "version": version,
                        "threshold": threshold,
                        "model_nb": None,
                        "all_nb": None,
                        "none_nb": None,
                        "status": "unavailable",
                        "reason": prediction.reason,
                    }
                    for threshold in plan.thresholds
                )
                continue
            result = measure(prediction, prevalence=cell.prevalence, thresholds=np.array(plan.thresholds))
            metrics[version] = result
            for metric, value in result.values.items():
                tables["replicates"].append(
                    {
                        **base,
                        "version": version,
                        "metric": metric,
                        "estimate": value,
                        "tested_participants": len(
                            set(data.design.ids[i] for i in np.flatnonzero(prediction.weights))
                        ),
                        "cases": int(np.sum(prediction.y * prediction.weights)),
                        "status": "ok" if math.isfinite(value) else "unavailable",
                        "reason": "" if math.isfinite(value) else "calibration_not_estimable",
                        "overlap_share": sum(prediction.audit.overlaps) / sum(prediction.audit.tested),
                    }
                )
            tables["decision_curves"].extend({**base, "version": version, **r} for r in result.curves)
            tables["calibration"].extend({**base, "version": version, **r} for r in result.calibration)
            record_fit(prediction, data, splits, {**audit_base, "version": version})
            assert x is not None
            # The null is a diagnostic. It never selects a result or changes a seed.
            if runs_null(cell, replicate, scheme, version, smoke=smoke):
                null_aucs = []
                for permutation in range(50):
                    null_seed = seed(1000 + permutation)
                    null_data = permute_participants(
                        matrix(cohort, x, scheme), seed=null_seed, within_device=scheme == "device"
                    )
                    null_splits = make_splits(
                        null_data, scheme, seed=null_seed, folds=plan.folds, direction=direction
                    )
                    null = evaluate_matrix(null_data, plan, null_splits, seed=null_seed, model=cell.model)
                    if isinstance(null, PredictionSet):
                        null_aucs.append(
                            weighted_auc(
                                null.cross.probabilities[0], null.y, null.cross.folds[0], null.weights
                            )
                        )
                        record_fit(
                            null,
                            null_data,
                            null_splits,
                            {**audit_base, "version": version, "permutation": permutation},
                        )
                    else:
                        tables["failures"].append(
                            {
                                **base,
                                "version": version,
                                "stage": "permutation",
                                "reason": null.reason,
                                "permutation": permutation,
                            }
                        )
                spread = float(np.std(null_aucs, ddof=1)) if len(null_aucs) > 1 else float("nan")
                spread = max(spread, fold_auc_null_sd(prediction.cross.folds[0], prediction.y))
                half = (
                    float(t.ppf(0.995, len(null_aucs) - 1)) * spread / np.sqrt(len(null_aucs))
                    if null_aucs
                    else float("nan")
                )
                audit.append(
                    {
                        **audit_base,
                        "version": version,
                        "stage": "null_check",
                        "aucs": null_aucs,
                        "mean": float(np.mean(null_aucs)) if null_aucs else None,
                        "lower": 0.5 - half,
                        "upper": 0.5 + half,
                        "passed": bool(null_aucs and abs(float(np.mean(null_aucs)) - 0.5) <= half),
                    }
                )
        if "reference" not in metrics or "uncorrected" not in metrics:
            for metric in ("auc", "intercept", "slope", "brier", "mean_nb"):
                tables["contrasts"].append(
                    {
                        **base,
                        "metric": metric,
                        "reference": None,
                        "uncorrected": None,
                        "corrected": None,
                        "D_raw": None,
                        "D_corrected": None,
                        "removed": None,
                        "absolute_reduction": None,
                        "status": "unavailable",
                    }
                )
        if "reference" in metrics and "uncorrected" in metrics:
            tables["contrasts"].extend({**base, **r} for r in paired_contrasts(metrics))
            ref = metrics["reference"].curves
            raw = metrics["uncorrected"].curves
            cor = metrics["corrected"].curves if "corrected" in metrics else None
            tables["decision_curves"].extend(
                {
                    **base,
                    "version": "paired",
                    "threshold": r["threshold"],
                    "D_raw": raw[i]["model_nb"] - r["model_nb"],
                    "D_corrected": cor[i]["model_nb"] - r["model_nb"] if cor else None,
                }
                for i, r in enumerate(ref)
            )
    return clean(
        {
            "common": common,
            "tables": tables,
            "audit": audit,
            "full_audit": full_log,
            "split_audit": split_audit,
            "seeds": list(seeds.values()),
            "audit_detail": "full" if full_audit else "compact",
        }
    )


@dataclass(frozen=True)
class UnitTask:
    plan: ConsequencePlan
    cell: CellSpec
    replicate: int
    inputs: BenchInputs
    smoke: bool
    path: Path
    detail_dir: Path


def write_unit(task: UnitTask) -> tuple[str, int, float]:
    start = time.perf_counter()
    result = run_unit(task.plan, task.cell, task.replicate, task.inputs, smoke=task.smoke)
    if task.smoke or task.replicate == 0:
        context = set(result["common"])
        detail = {
            "cell_id": task.cell.id,
            "replicate": task.replicate,
            "splits": [
                {k: v for k, v in row.items() if k not in context} for row in result["tables"]["splits"]
            ],
            "audit": [
                {k: v for k, v in row.items() if k not in ("cell_id", "replicate")}
                for row in result["full_audit"]
            ],
        }
        detail_path = task.detail_dir / task.path.name
        temporary = detail_path.with_suffix(".pending")
        write_compressed_json(temporary, detail)
        temporary.replace(detail_path)
    packed = pack_unit(result)
    packed["inputs_sha256"] = task.inputs.digest
    temporary = task.path.with_suffix(".pending")
    write_compressed_json(temporary, packed)
    temporary.replace(task.path)
    return task.cell.id, task.replicate, time.perf_counter() - start


def summary_rows(tables: dict[str, list[Row]]) -> dict[str, list[Row]]:
    groups: dict[tuple[str, str, str, str, str], list[Row]] = defaultdict(list)
    for row in tables["contrasts"]:
        for contrast in ("D_raw", "D_corrected", "removed", "absolute_reduction"):
            groups[(row["cell_id"], row["validation"], row["direction"], row["metric"], contrast)].append(
                {**row, "estimate": row[contrast]}
            )
    for row in tables["replicates"]:
        groups[(row["cell_id"], row["validation"], row["direction"], row["metric"], row["version"])].append(
            row
        )
    summary = []
    for key, rows in sorted(groups.items()):
        base = {
            k: v
            for k, v in rows[0].items()
            if k
            not in (
                "replicate",
                "estimate",
                "reference",
                "uncorrected",
                "corrected",
                "D_raw",
                "D_corrected",
                "removed",
                "absolute_reduction",
                "material_raw",
            )
        }
        summary.append({**base, "contrast": key[-1], **summarize(rows, planned=int(rows[0]["planned_R"]))})
    curves: dict[tuple[str, str, str, str, float], list[Row]] = defaultdict(list)
    for row in tables["decision_curves"]:
        curves[
            (row["cell_id"], row["validation"], row["direction"], row["version"], row["threshold"])
        ].append(row)
    curve_summary = []
    for key, rows in sorted(curves.items()):
        for metric in ("D_raw", "D_corrected") if key[3] == "paired" else ("model_nb", "all_nb", "none_nb"):
            estimates = [{**r, "estimate": r.get(metric), "metric": "mean_nb"} for r in rows]
            curve_summary.append(
                {
                    "cell_id": key[0],
                    "validation": key[1],
                    "direction": key[2],
                    "version": key[3],
                    "threshold": key[4],
                    "metric": metric,
                    **summarize(estimates, planned=int(rows[0]["planned_R"])),
                }
            )
    primary = []
    for row in summary:
        scenario, validation, metric = row["scenario"], row["validation"], row["metric"]
        selected = (
            (scenario == "all_w" and validation == "participant" and metric == "auc")
            or (
                scenario == "transfer"
                and validation == "device"
                and row["direction"] == "W-to-P"
                and metric == "intercept"
            )
            or (
                scenario == "confounded"
                and row["effect"] == 0
                and validation == "participant"
                and metric == "auc"
            )
        )
        if (
            selected
            and row["sensitivity"] == "primary"
            and row["contrast"] in ("D_raw", "D_corrected", "absolute_reduction")
        ):
            primary.append(row)
    validation_rows: dict[tuple[str, str, str, str], Row] = {}
    for row in summary:
        if row["contrast"] not in ("reference", "uncorrected", "corrected"):
            continue
        key = (row["cell_id"], row["metric"], row["contrast"], row["direction"])
        target = validation_rows.setdefault(
            key, {"cell_id": key[0], "metric": key[1], "version": key[2], "direction": key[3]}
        )
        target[f"{row['validation']}_estimate"] = row["mean"]
        target[f"{row['validation']}_usable_R"] = row["usable_R"]
        if row["validation"] == "record":
            target["overlap_share"] = row.get("overlap_share")
    return {
        "consequence_summary": summary,
        "figure_bias_response": [{**r, "x_bias_over_L": r["bias_multiple"]} for r in summary],
        "figure_transfer_calibration": [r for r in summary if r["scenario"] == "transfer"],
        "figure_decision_curves": curve_summary,
        "table_primary": primary,
        "table_validation": list(validation_rows.values()),
    }


def merge_outputs(
    out: Path,
    unit_keys: list[tuple[str, int]],
    catalog: dict[str, tuple[Row, BenchInputs]],
    thresholds: tuple[float, ...],
    *,
    smoke: bool,
) -> list[Path]:
    common_keys = set(next(iter(catalog.values()))[0]) if catalog else set()
    per_replicate = common_keys | {"replicate", "negative_cv"}
    columns = {
        "consequence_cells": common_keys | {"inputs_sha256"},
        "consequence_inputs": set(),
        "consequence_corrections": set(),
        "consequence_replicates": per_replicate
        | {
            "validation",
            "direction",
            "version",
            "metric",
            "estimate",
            "tested_participants",
            "cases",
            "overlap_share",
            "status",
            "reason",
        },
        "consequence_contrasts": per_replicate
        | {
            "validation",
            "direction",
            "metric",
            "reference",
            "uncorrected",
            "corrected",
            "D_raw",
            "D_corrected",
            "removed",
            "absolute_reduction",
            "material_raw",
            "status",
        },
        "consequence_decision_curves": per_replicate
        | {
            "validation",
            "direction",
            "version",
            "threshold",
            "model_nb",
            "all_nb",
            "none_nb",
            "D_raw",
            "D_corrected",
            "status",
            "reason",
        },
        "consequence_calibration": per_replicate
        | {
            "validation",
            "direction",
            "version",
            "bin",
            "mean_probability",
            "observed_share",
            "participant_weight",
            "intercept",
            "slope",
            "brier",
        },
        "consequence_splits": {
            "cell_id",
            "replicate",
            "validation",
            "direction",
            "fold",
            "row_id",
            "participant_id",
            "device",
            "site",
            "mounting_block",
            "role",
        },
        "consequence_failures": per_replicate
        | {"validation", "direction", "version", "stage", "reason", "planned", "permutation"},
    }
    input_rows: dict[str, list[Row]] = {"inputs": [], "corrections": []}
    completed_cells = sorted({cell_id for cell_id, _ in unit_keys})
    for cell_id in completed_cells:
        common, inputs = catalog[cell_id]
        for name, rows in input_tables(common, inputs).items():
            input_rows[name].extend(rows)
            columns[f"consequence_{name}"].update(key for row in rows for key in row)
    summary_keys = set(summarize([], planned=1))
    dropped = {
        "replicate",
        "estimate",
        "reference",
        "uncorrected",
        "corrected",
        "D_raw",
        "D_corrected",
        "removed",
        "absolute_reduction",
        "material_raw",
    }
    summary_columns = (
        (columns["consequence_replicates"] | columns["consequence_contrasts"]) - dropped
        | summary_keys
        | {"contrast"}
    )
    columns["consequence_summary"] = summary_columns
    if not smoke:
        columns.update(
            {
                "figure_bias_response": summary_columns | {"x_bias_over_L"},
                "figure_transfer_calibration": summary_columns,
                "table_primary": summary_columns,
                "figure_decision_curves": summary_keys
                | {"cell_id", "validation", "direction", "version", "threshold", "metric"},
                "table_validation": {
                    "cell_id",
                    "metric",
                    "version",
                    "direction",
                    "overlap_share",
                    "participant_estimate",
                    "participant_usable_R",
                    "record_estimate",
                    "record_usable_R",
                    "device_estimate",
                    "device_usable_R",
                },
            }
        )
    zipped = {"consequence_replicates", "consequence_contrasts", "consequence_splits"}
    paths = [out / (name + ".csv" + (".gz" if name in zipped else "")) for name in columns]
    audit_path = out / "consequence_fit_audit.jsonl.gz"
    with ExitStack() as stack:
        writers = {}
        for name, path in zip(columns, paths, strict=True):
            stream = stack.enter_context(
                compressed_text(path) if name in zipped else path.open("w", encoding="utf-8", newline="")
            )
            writer = csv.DictWriter(
                stream, sorted(columns[name]) or ["status", "reason"], lineterminator="\n"
            )
            writer.writeheader()
            writers[name] = writer
        audit_stream = stack.enter_context(compressed_text(audit_path))
        writers["consequence_cells"].writerows(
            clean([{**common, "inputs_sha256": inputs.digest} for common, inputs in catalog.values()])
        )
        for name, rows in input_rows.items():
            writers[f"consequence_{name}"].writerows(clean(rows))
        for cell_id, members in groupby(unit_keys, key=lambda key: key[0]):
            common, inputs = catalog[cell_id]
            collected: dict[str, list[Row]] = {
                name: [] for name in ("replicates", "contrasts", "decision_curves")
            }
            for _, replicate in members:
                unit = read_restart_unit(out / "units", cell_id, replicate)
                assert unit is not None
                if (
                    unit["format"] != 2
                    or unit["cell_id"] != cell_id
                    or unit["inputs_sha256"] != inputs.digest
                    or unit["replicate"] != replicate
                ):
                    raise ValueError("A completed unit has different input or cell hashes.")
                expanded = expand_unit(unit, common, thresholds)
                detail = smoke or unit["replicate"] == 0
                for name, rows in expanded.items():
                    if name not in ("decision_curves", "calibration") or detail:
                        writers[f"consequence_{name}"].writerows(rows)
                    if name in collected:
                        collected[name].extend(rows)
                if detail:
                    full = read_unit(out / "details" / unit_name(cell_id, replicate))
                    context = {"cell_id": cell_id, "replicate": unit["replicate"]}
                    writers["consequence_splits"].writerows({**context, **row} for row in full["splits"])
                    for row in full["audit"]:
                        audit_stream.write(
                            json.dumps({**context, **row}, sort_keys=True, separators=(",", ":")) + "\n"
                        )
                    for group in unit["groups"]:
                        for version, records in group["audit"].items():
                            for row in records:
                                if row["stage"] == "null_check":
                                    audit_stream.write(
                                        json.dumps(
                                            {
                                                **context,
                                                "validation": group["validation"],
                                                "direction": group["direction"],
                                                "version": version,
                                                **row,
                                            },
                                            sort_keys=True,
                                        )
                                        + "\n"
                                    )
            for name, rows in summary_rows(collected).items():
                if name in writers:
                    writers[name].writerows(clean(rows))
    return [*paths, audit_path]


def write_outputs(
    plan: ConsequencePlan,
    out: Path,
    *,
    centers: Centers,
    measured: BenchInputs | None = None,
    cell_id: str | None = None,
    replicate: int | None = None,
    smoke: bool = False,
    workers: int = 1,
) -> tuple[Path, ...]:
    if any(p.startswith("._") for p in out.parts):
        raise ValueError("Metadata paths are excluded.")
    selected_cells = cells(plan)
    if cell_id:
        selected_cells = tuple(c for c in selected_cells if c.id == cell_id)
        if not selected_cells:
            raise ValueError("Unknown cell identifier.")
    elif smoke:
        selected_cells = tuple(
            c
            for c in selected_cells
            if c.sensitivity == "primary"
            and c.effect == 0.8
            and c.bias_multiple == (1 if plan.source == "grid" else 0)
            and c.noise_id in ("nominal", "measured")
        )
    if workers < 1:
        raise ValueError("The worker count must be positive.")
    if replicate is not None and any(
        not 0 <= replicate < (2 if smoke else planned_replicates(plan, c)) for c in selected_cells
    ):
        raise ValueError("The replicate is outside the planned range.")
    package = Path(__file__).resolve().parents[1]
    files = sorted(p for p in package.rglob("*.py") if not any(part.startswith("._") for part in p.parts))
    code_hash = hashlib.sha256(
        b"".join(p.relative_to(package).as_posix().encode() + p.read_bytes() for p in files)
    ).hexdigest()
    protocol_path = package.parents[2] / "docs/CONSEQUENCE_PROTOCOL.md"
    identity = {
        "code_sha256": code_hash,
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "plan_sha256": plan_digest(plan),
        "input_sha256": measured.digest if measured else "grid",
        "smoke": smoke,
        "unit_format": 2,
    }
    out.mkdir(parents=True, exist_ok=True)
    identity_path = out / "identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("The output folder has a different plan or input hash.")
    write_json(identity_path, identity)
    units = out / "units"
    units.mkdir(exist_ok=True)
    (out / "details").mkdir(exist_ok=True)

    def pending_tasks(cell: CellSpec, inputs: BenchInputs, stage: Path) -> Iterator[UnitTask]:
        count = 2 if smoke else planned_replicates(plan, cell)
        for r in (replicate,) if replicate is not None else range(count):
            name = unit_name(cell.id, r)
            completed = read_restart_unit(units, cell.id, r)
            if completed is not None and (
                completed["cell_id"] != cell.id
                or completed["replicate"] != r
                or completed["format"] != 2
                or completed["inputs_sha256"] != inputs.digest
            ):
                raise ValueError("A completed unit has different input or cell hashes.")
            if completed is None or ((smoke or r == 0) and not (out / "details" / name).exists()):
                yield UnitTask(plan, cell, r, inputs, smoke, stage / name, out / "details")

    with (
        TemporaryDirectory(prefix=".consequence-stage-", dir=out if smoke else None) as temporary,
        ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool,
    ):
        stage = Path(temporary)
        for cell in selected_cells:
            inputs = for_cell(measured, cell) if measured else grid_inputs(plan, cell, centers)
            tasks = pending_tasks(cell, inputs, stage)
            while batch := tuple(islice(tasks, 2 * workers)):
                timings = list(pool.map(write_unit, batch, chunksize=1))
                archive_cell(stage, units, cell.id)
                for cell_id, r, seconds in timings:
                    print(f"{cell_id} replicate {r}: {seconds:.6f} s", flush=True)
    unit_keys = stored_units(units)
    catalog = {}
    for cell in cells(plan):
        inputs = for_cell(measured, cell) if measured else grid_inputs(plan, cell, centers)
        catalog[cell.id] = (cell_metadata(plan, cell, inputs), inputs)
    paths = merge_outputs(out, unit_keys, catalog, plan.thresholds, smoke=smoke)
    protocol = Path(__file__).resolve().parents[4] / "docs/CONSEQUENCE_PROTOCOL.md"
    registry = protocol.parent.parent / "contract/bench_programs.json"
    manifest = {
        **identity,
        "protocol_sha256": hashlib.sha256(protocol.read_bytes()).hexdigest(),
        "registry_sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
        "plan": asdict(plan),
        "cell_table": "consequence_cells.csv",
        "planned_cells": len(catalog),
        "completed_units": len(unit_keys),
        "timing": "Per-unit elapsed seconds are printed to stdout, outside the deterministic outputs.",
        "seed_keys": {
            "master": plan.seed,
            "fields": list(SeedKey.__dataclass_fields__),
            "values": "Unit seeds are indexed by stream. Other key fields come from the cell and replicate.",
        },
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "sklearn": sklearn.__version__,
        },
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=protocol.parent, text=True
        ).strip(),
        "deviations": [],
        "measured_input": measured.raw if measured else None,
        "output_sha256": {p.name: file_digest(p) for p in paths},
    }
    path = out / "consequence_manifest.json"
    write_json(path, manifest)
    return (*paths, path)
