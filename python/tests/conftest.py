from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from scriptkin import Device, Stroke, TaskRef, Trial, feature_table, hpke
from scriptkin import synthetic as syn
from scriptkin.contract import encode_line, manifest_bytes
from scriptkin.evaluate.plan import AnalysisPlan, ModelSpec, Target, column
from scriptkin.features import FeatureTable
from scriptkin.formats.export import HPKE_INFO, HPKE_SUITE
from scriptkin.recording import FloatArray

CONTRACT = Path(__file__).resolve().parents[2] / "contract"
FIXTURES = CONTRACT / "fixtures"


def make_trial(
    t: FloatArray,
    xy: FloatArray | None = None,
    *,
    strokes: tuple[Stroke, ...] | None = None,
    device: Device | None = None,
    task: TaskRef | None = None,
) -> Trial:
    positions = np.column_stack((t * 10, np.zeros(len(t)))) if xy is None else xy
    return Trial(
        0,
        task or TaskRef("clock"),
        strokes or (Stroke(t, positions),),
        device or Device("synthetic", "pencil"),
        started=float(t[0]),
        ended=float(t[-1]),
    )


def fixture_lines(name: str = "basic") -> list[dict[str, Any]]:
    return [json.loads(s) for s in (FIXTURES / "journal" / f"{name}.jsonl").read_bytes().splitlines()]


def write_export(folder: Path, rows: list[dict[str, Any]]) -> bytes:
    """Write a plaintext export and return its journal bytes."""
    folder.mkdir(parents=True, exist_ok=True)
    data = b"".join(map(encode_line, rows))
    (folder / "journal.jsonl").write_bytes(data)
    (folder / "manifest.json").write_bytes(manifest_bytes(rows[0].get("sessionID", "invalid"), data))
    return data


def write_encrypted_export(folder: Path, data: bytes, session_id: str, private_key: bytes) -> None:
    """Write an export as DESIGN section 17 specifies, sealed to the public key of `private_key`."""
    folder.mkdir(parents=True, exist_ok=True)
    enc, ciphertext = hpke.seal(hpke.public_key(private_key), HPKE_INFO, session_id.encode(), data)
    (folder / "journal.jsonl.hpke").write_bytes(ciphertext)
    encryption = {
        "scheme": "hpke-base",
        "suite": HPKE_SUITE,
        "keyID": hashlib.sha256(hpke.public_key(private_key)).hexdigest(),
        "encapsulatedKey": base64.b64encode(enc).decode(),
        "plaintext": {
            "bytes": len(data),
            "lines": data.count(b"\n"),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "ciphertext": {"bytes": len(ciphertext), "sha256": hashlib.sha256(ciphertext).hexdigest()},
    }
    manifest = {
        "format": "script.journal",
        "version": "1.0",
        "sessionID": session_id,
        "encryption": encryption,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")


SPIRAL = ("spiral-trace",)
SIGNAL_EFFECT = {"speed_mm_s": -0.6, "tremor_mm": 0.6}
FEATURES = tuple(
    column("spiral-trace", f)
    for f in (
        "kinematic.speed_mean",
        "kinematic.jerk_mean",
        "timing.stroke_count",
        "spatial.trace_rmse",
        "pressure.force_mean",
        "pressure.force_cv",
    )
)


@dataclass(frozen=True)
class Analysed:
    cohort: syn.Cohort
    table: FeatureTable


def analysed(**kwargs: Any) -> Analysed:
    cohort = syn.cohort(**kwargs)
    return Analysed(cohort, feature_table(cohort.recordings))


def quick_plan(**changes: Any) -> AnalysisPlan:
    """A small plan that keeps the test run short: one repeat, two grid values, few permutations."""
    plan = AnalysisPlan(
        Target(),
        FEATURES,
        prevalence=0.3,
        repeats=1,
        model=ModelSpec(grid=(10.0, 100.0)),
        permutations=10,
        bootstrap=300,
    )
    return replace(plan, **changes)


@pytest.fixture(scope="session")
def null_cohort() -> Analysed:
    return analysed(n=120, seed=21, scenario="null", tasks=SPIRAL)


@pytest.fixture(scope="session")
def signal_cohort() -> Analysed:
    sites = (syn.Site("S1"), syn.Site("S2"), syn.Site("S3"))
    return analysed(
        n=150, seed=300, scenario="signal", outcome=syn.Outcome(0.3, SIGNAL_EFFECT), sites=sites, tasks=SPIRAL
    )


@pytest.fixture(scope="session")
def device_cohort() -> Analysed:
    devices = (syn.IPAD_PRO_PENCIL_PRO, syn.IPAD_AIR_PENCIL_USBC)
    return analysed(n=150, seed=25, scenario="device_confounded", devices=devices, tasks=SPIRAL)


@pytest.fixture(scope="session")
def fingerprint_cohort() -> Analysed:
    return analysed(n=60, seed=27, scenario="fingerprint", tasks=SPIRAL)


@pytest.fixture(scope="session")
def missing_cohort() -> Analysed:
    return analysed(
        n=120, seed=29, scenario="missingness", outcome=syn.Outcome(0.3, SIGNAL_EFFECT), tasks=SPIRAL
    )


@pytest.fixture(scope="session")
def retest_cohort() -> Analysed:
    return analysed(n=40, seed=31, scenario="null", visits=2, session_noise={"pressure": 1.0}, tasks=SPIRAL)
