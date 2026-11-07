"""Compact restart units and explicit adapters to summary rows."""

from __future__ import annotations

import gzip
import io
import json
import math
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scriptkin.consequence.metrics import MetricSet, paired_contrasts

Row = dict[str, Any]
METRICS = ("auc", "intercept", "slope", "brier", "mean_nb")
CURVES = ("model_nb", "all_nb", "none_nb")
BINS = ("mean_probability", "observed_share", "participant_weight")


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@contextmanager
def compressed_text(path: Path) -> Generator[io.TextIOWrapper]:
    # Gzip must not record a timestamp or an output-folder-dependent filename.
    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as zipped,
        io.TextIOWrapper(zipped, encoding="utf-8", newline="") as stream,
    ):
        yield stream


def write_compressed_json(path: Path, value: Row) -> None:
    with compressed_text(path) as stream:
        json.dump(clean(value), stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
        stream.write("\n")


def read_unit(path: Path) -> Row:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def pack_unit(raw: Row) -> Row:
    common = raw["common"]
    context = set(common) | {"validation", "direction", "version"}
    groups: dict[tuple[str, str], Row] = {}

    def group(row: Row) -> Row:
        key = (row["validation"], row["direction"])
        return groups.setdefault(
            key,
            {
                "validation": key[0],
                "direction": key[1],
                "versions": {},
                "audit": {},
                "splits": [],
                "failures": [],
            },
        )

    for row in raw["tables"]["replicates"]:
        packet = group(row)["versions"].setdefault(row["version"], {"metrics": {}, "counts": {}})
        packet["metrics"][row["metric"]] = row["estimate"]
        for key in ("tested_participants", "cases", "overlap_share"):
            if key in row:
                packet["counts"][key] = row[key]
        if row.get("reason") and row["reason"] != "calibration_not_estimable":
            packet["reason"] = row["reason"]
    for table, columns in (("decision_curves", CURVES), ("calibration", BINS)):
        for row in raw["tables"][table]:
            if row["version"] == "paired":
                continue
            packet = group(row)["versions"][row["version"]]
            arrays = packet.setdefault(table, {key: [] for key in columns})
            for key in columns:
                arrays[key].append(row[key])
    for row in raw["audit"]:
        group(row)["audit"].setdefault(row["version"], []).append(
            {key: value for key, value in row.items() if key not in context}
        )
    for row in raw["split_audit"]:
        group(row)["splits"].append({key: value for key, value in row.items() if key not in context})
    for row in raw["tables"]["failures"]:
        group(row)["failures"].append(
            {key: value for key, value in row.items() if key not in context or key == "version"}
        )
    return {
        "format": 2,
        "cell_id": common["cell_id"],
        "replicate": common["replicate"],
        "negative_cv": common["negative_cv"],
        "groups": list(groups.values()),
        "seeds": {str(item["key"]["stream"]): item["seed"] for item in raw["seeds"]},
    }


def expand_unit(unit: Row, cell: Row, thresholds: tuple[float, ...]) -> dict[str, list[Row]]:
    tables: dict[str, list[Row]] = {
        name: [] for name in ("replicates", "contrasts", "decision_curves", "calibration", "failures")
    }
    common = {**cell, "replicate": unit["replicate"], "negative_cv": unit["negative_cv"]}
    for group in unit["groups"]:
        base = {**common, "validation": group["validation"], "direction": group["direction"]}
        available: dict[str, MetricSet] = {}
        packets = group["versions"]
        for version, packet in packets.items():
            values = packet["metrics"]
            for metric in METRICS:
                value = values[metric]
                row = {
                    **base,
                    "version": version,
                    **packet["counts"],
                    "metric": metric,
                    "estimate": value,
                    "status": "ok" if value is not None else "unavailable",
                }
                if version in ("reference", "uncorrected", "corrected"):
                    row["reason"] = packet.get("reason", "calibration_not_estimable" if value is None else "")
                tables["replicates"].append(row)
            arrays = packet.get("decision_curves")
            if arrays is not None:
                for i, threshold in enumerate(thresholds):
                    row = {
                        **base,
                        "version": version,
                        "threshold": threshold,
                        **{key: arrays[key][i] for key in CURVES},
                    }
                    if "reason" in packet:
                        row.update(status="unavailable", reason=packet["reason"])
                    tables["decision_curves"].append(row)
            bins = packet.get("calibration")
            if bins is not None:
                for i in range(len(bins[BINS[0]])):
                    tables["calibration"].append(
                        {
                            **base,
                            "version": version,
                            "bin": float(i),
                            **{key: bins[key][i] for key in BINS},
                            **{key: values[key] for key in ("intercept", "slope", "brier")},
                        }
                    )
            if version in ("reference", "uncorrected", "corrected") and "reason" not in packet:
                available[version] = MetricSet(
                    {key: float(value) if value is not None else math.nan for key, value in values.items()},
                    (),
                    (),
                )
        if "reference" in available and "uncorrected" in available:
            tables["contrasts"].extend({**base, **row} for row in paired_contrasts(available))
            ref = packets["reference"]["decision_curves"]["model_nb"]
            raw = packets["uncorrected"]["decision_curves"]["model_nb"]
            cor = packets["corrected"]["decision_curves"]["model_nb"] if "corrected" in available else None
            for i, threshold in enumerate(thresholds):
                tables["decision_curves"].append(
                    {
                        **base,
                        "version": "paired",
                        "threshold": threshold,
                        "D_raw": raw[i] - ref[i],
                        "D_corrected": cor[i] - ref[i] if cor is not None else None,
                    }
                )
        else:
            for metric in METRICS:
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
        tables["failures"].extend({**base, **row} for row in group["failures"])
    return clean(tables)


def unit_name(cell_id: str, replicate: int) -> str:
    return f"{cell_id}-{replicate:03d}.json.gz"


def read_restart_unit(folder: Path, cell_id: str, replicate: int) -> Row | None:
    from zipfile import ZipFile

    path = folder / f"{cell_id}.zip"
    if not path.exists():
        return None
    with ZipFile(path) as archive:
        try:
            payload = archive.read(unit_name(cell_id, replicate))
        except KeyError:
            return None
    return json.loads(gzip.decompress(payload))


def stored_units(folder: Path) -> list[tuple[str, int]]:
    from zipfile import ZipFile

    result = []
    for path in sorted(folder.glob("*.zip")):
        if path.name.startswith("._"):
            continue
        with ZipFile(path) as archive:
            for name in archive.namelist():
                if name.startswith("._"):
                    continue
                cell, replicate = name.removesuffix(".json.gz").rsplit("-", 1)
                if cell != path.stem or name != unit_name(cell, int(replicate)):
                    raise ValueError("The unit archive has an invalid member name.")
                result.append((cell, int(replicate)))
    if len(set(result)) != len(result):
        raise ValueError("The unit archive repeats a completed unit.")
    return sorted(result)


def archive_cell(stage: Path, folder: Path, cell_id: str) -> None:
    from zipfile import ZIP_STORED, ZipFile, ZipInfo

    paths = [p for p in sorted(stage.glob(f"{cell_id}-*.json.gz")) if not p.name.startswith("._")]
    if not paths:
        return
    target = folder / f"{cell_id}.zip"
    members: dict[str, bytes] = {}
    if target.exists():
        with ZipFile(target) as archive:
            members = {name: archive.read(name) for name in archive.namelist() if not name.startswith("._")}
    for path in paths:
        payload = path.read_bytes()
        if path.name in members and payload != members[path.name]:
            raise ValueError("The completed unit differs from its archived copy.")
        members[path.name] = payload
    temporary = target.with_suffix(".pending")
    with ZipFile(temporary, "w", compression=ZIP_STORED) as archive:
        for name, payload in sorted(members.items()):
            member = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = 0o600 << 16
            archive.writestr(member, payload)
    temporary.replace(target)
    for path in paths:
        path.unlink()
