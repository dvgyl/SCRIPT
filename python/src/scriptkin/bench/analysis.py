"""The statistics of protocol sections 11 and 12 over the evaluated runs of a study.

Program sets. The set of a feature is the main programs of the study whose geometry the feature is
decided on (`features.CATALOG`), and for `bench.band_power_mm2_s2` the line programs of the band-power
set of the registry. The correction model of a set follows the coordinator's decision on section 11.5:
`COVARIATES` for the set of every geometry, `SLOPE` for the loops, and `SHIFT` for the line.

Run arrays. `run_arrays` fills the arrays of `scriptkin.bench.stats` from the evaluated runs: one row per
mounting of the device in mounting order, and one slot per run index 1 to 7. A failed run, an unused
slot, and a camera-referenced difference without a valid camera record are NaN. Mounting i of device P
pairs with mounting i of device W, so both devices need the same number of mountings.

Analyses. `main` is sections 11.2 to 11.5 with the camera-referenced differences of the four camera
features. The other analyses are the sensitivity analyses of section 11.9. `no_motion_exclusion` keeps
the motion failures of section 7.6. `raw_device` compares the raw device values of the camera features.
`median` replaces each mounting mean by the median of its runs: every valid run of a mounting is
shifted by the median minus the mean, which keeps the run spread and the counts of section 11.2. The
analyses `by_task`, `by_scale`, and `by_speed` pool the runs of the programs that share the factor into
one group per level, with the other factors mixed, and report sections 11.2 to 11.4 per group. They
report no correction, because the covariates of section 11.5 are per program, and no group has one
speed and one scale.

The correction of section 11.5 is fitted on the raw device values of every feature, in every analysis,
while the equivalence test and the limits of agreement of the main analysis use the camera-referenced
differences. The protocol takes the points of the correction from the pairs of section 11.4, which for
the four camera features are differences. A difference holds no spread between programs, because the
camera removes the movement, so the slope of a model on differences is not identified: in the synthetic
study of the tests its jackknife standard error is 1.1 to 5.0. The correction maps a device W value to a
device P value for recordings without a camera (section 12), so it is fitted on the values it maps.

The limits are the registered values of section 10. The functions return NaN as `stats` does, and the
output writer turns a NaN into an empty value with a reason.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from scriptkin.bench import RUNS, Design, stats
from scriptkin.bench.features import ALL, CATALOG, Geometry
from scriptkin.bench.inputs import InputError, Registry
from scriptkin.bench.runs import CAMERA_FEATURES, FEATURE_SET, Evaluated, comparison_value
from scriptkin.bench.study import Study
from scriptkin.recording import BenchRole, FloatArray

LIMITS: dict[str, float] = {
    "bench.motion_s": 0.036,
    "bench.path_length_mm": 0.044,
    "bench.speed_mean_mm_s": 0.044,
    "bench.loop_height_mm": 0.047,
    "bench.speed_cv": 0.030,
    "bench.band_power_mm2_s2": 0.26,
}
Grouping = Literal["program", "task", "scale", "speed"]
DEVICES: tuple[BenchRole, ...] = ("P", "W")
_SLOTS = len(RUNS)


@dataclass(frozen=True)
class Analysis:
    name: str
    camera_referenced: bool
    motion_exclusion: bool
    median: bool
    grouping: Grouping


ANALYSES = (
    Analysis("main", True, True, False, "program"),
    Analysis("no_motion_exclusion", True, False, False, "program"),
    Analysis("raw_device", False, True, False, "program"),
    Analysis("median", True, True, True, "program"),
    Analysis("by_task", True, True, False, "task"),
    Analysis("by_scale", True, True, False, "scale"),
    Analysis("by_speed", True, True, False, "speed"),
)
MAIN_ANALYSIS = ANALYSES[0]


@dataclass(frozen=True)
class Group:
    """The programs whose runs one cell of an analysis pools. A program is a group of one."""

    label: str
    programs: tuple[Design, ...]


def decided_on(feature: str) -> tuple[Geometry, ...]:
    return next(f.decided_on for f in CATALOG if f.name == feature)


def program_set(feature: str, study: Study) -> tuple[Design, ...]:
    """Section 9.1: the programs on which the feature is tested, in `MAIN` order."""
    programs = tuple(d for d in study.main_programs if d.geometry in decided_on(feature))
    if feature == "bench.band_power_mm2_s2":
        allowed = set(study.registry.constants.band_power_programs)
        programs = tuple(d for d in programs if d.id in allowed)
    return programs


def correction_model(feature: str) -> stats.CorrectionModel:
    geometries = decided_on(feature)
    if geometries == ALL:
        return stats.CorrectionModel.COVARIATES
    if geometries == ("loops",):
        return stats.CorrectionModel.SLOPE
    return stats.CorrectionModel.SHIFT


def groups(programs: Sequence[Design], grouping: Grouping) -> tuple[Group, ...]:
    """The groups of an analysis in program order: one per program, or one per level of the factor."""
    if grouping == "program":
        return tuple(Group(d.id, (d,)) for d in programs)
    levels: dict[str, list[Design]] = {}
    for d in programs:
        label = {"task": d.task, "scale": f"scale {d.scale:g}", "speed": f"speed {d.speed_mm_s:g}"}[grouping]
        levels.setdefault(label, []).append(d)
    return tuple(Group(label, tuple(ds)) for label, ds in levels.items())


@dataclass(frozen=True)
class Arrays:
    """The run arrays of one feature on one analysis, (G, m, r) per device."""

    groups: tuple[Group, ...]
    p: FloatArray
    w: FloatArray

    def of(self, device: BenchRole) -> FloatArray:
        return self.p if device == "P" else self.w


def run_value(run: Evaluated, feature: str, analysis: Analysis) -> float:
    """The value of a run on the comparison scale, NaN for a failed run or a missing value."""
    failed = run.outcome.failed if analysis.motion_exclusion else run.outcome.failed_without_motion_check
    if failed or run.row.fault:
        return np.nan
    if analysis.camera_referenced and feature in CAMERA_FEATURES:
        return run.camera_difference(feature)
    return comparison_value(feature, run.device_value(feature))


def _mounting_index(registry: Registry) -> dict[tuple[BenchRole, str], int]:
    index: dict[tuple[BenchRole, str], int] = {}
    counts: list[int] = []
    for device in DEVICES:
        mountings = registry.mountings_of(device)
        counts.append(len(mountings))
        for i, m in enumerate(mountings):
            index[(device, m.mounting)] = i
    if counts[0] != counts[1] or counts[0] == 0:
        raise InputError("The registry needs the same number of mountings on device P and device W.")
    return index


def run_arrays(
    evaluated: dict[str, Evaluated],
    registry: Registry,
    feature: str,
    groups: Sequence[Group],
    analysis: Analysis,
) -> Arrays:
    index = _mounting_index(registry)
    m = len(index) // 2
    slot = {(g, d.id): k * _SLOTS for g, group in enumerate(groups) for k, d in enumerate(group.programs)}
    width = max(len(g.programs) for g in groups) * _SLOTS if groups else _SLOTS
    arrays = {device: np.full((len(groups), m, width), np.nan) for device in DEVICES}
    for run in evaluated.values():
        for g, group in enumerate(groups):
            if any(d.id == run.row.program for d in group.programs):
                mounting = index[(run.row.device, run.row.mounting)]
                column = slot[(g, run.row.program)] + run.row.run_index - 1
                arrays[run.row.device][g, mounting, column] = run_value(run, feature, analysis)
    if analysis.median:
        for a in arrays.values():
            valid = np.isfinite(a)
            with np.errstate(invalid="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                means = np.where(valid, a, 0.0).sum(axis=-1, keepdims=True) / valid.sum(
                    axis=-1, keepdims=True
                )
                medians = np.nanmedian(a, axis=-1, keepdims=True)
            a += np.where(valid, medians - means, 0.0)
    return Arrays(tuple(groups), arrays["P"], arrays["W"])


@dataclass(frozen=True)
class FeatureResult:
    """Sections 11.2 to 11.5 for one feature on one analysis. Arrays have one value per group."""

    analysis: Analysis
    feature: str
    limit: float
    arrays: Arrays
    bias: stats.Bias
    p: FloatArray
    """The TOST p-value per group (section 11.3)."""
    components: dict[BenchRole, stats.VarianceComponents]
    """Per group and device (section 11.2)."""
    pooled: dict[BenchRole, stats.VarianceComponents]
    """Pooled over the groups per device."""
    bland_altman: stats.BlandAltman | None
    bland_altman_reason: str
    model: stats.CorrectionModel | None
    correction: stats.Correction | None
    correction_reason: str

    @property
    def feature_p(self) -> float:
        return float(stats.feature_p(self.p)) if len(self.p) else 1.0

    @property
    def scale(self) -> str:
        return "absolute" if self.feature == "bench.speed_cv" else "ln"


def feature_result(
    evaluated: dict[str, Evaluated], study: Study, feature: str, analysis: Analysis
) -> FeatureResult:
    programs = program_set(feature, study)
    arrays = run_arrays(evaluated, study.registry, feature, groups(programs, analysis.grouping), analysis)
    limit = LIMITS[feature]
    if not programs:
        empty = np.empty(0)
        bias = stats.Bias(empty, empty, empty)
        none = stats.VarianceComponents(empty, empty, empty)
        return FeatureResult(
            analysis,
            feature,
            limit,
            arrays,
            bias,
            empty,
            {"P": none, "W": none},
            {"P": none, "W": none},
            None,
            "The program set is empty.",
            None,
            None,
            "The program set is empty.",
        )
    bias = stats.bias(arrays.p, arrays.w)
    p = stats.tost_p(bias, limit)
    components: dict[BenchRole, stats.VarianceComponents] = {
        device: stats.variance_components(arrays.of(device)) for device in DEVICES
    }
    pooled: dict[BenchRole, stats.VarianceComponents] = {
        device: stats.pooled_variance_components(arrays.of(device)) for device in DEVICES
    }
    m = arrays.p.shape[1]
    bland_altman = None
    bland_altman_reason = ""
    if len(arrays.groups) >= 2 and m >= 2:
        bland_altman = stats.bland_altman(arrays.p, arrays.w)
    else:
        bland_altman_reason = "The limits of agreement need 2 programs and 2 mountings."
    model = correction = None
    correction_reason = ""
    if analysis.grouping != "program":
        correction_reason = "A pooled group has no covariates."
    else:
        model = correction_model(feature)
        u, v = stats.covariates(programs)
        raw = run_arrays(
            evaluated, study.registry, feature, arrays.groups, replace(analysis, camera_referenced=False)
        )
        try:
            correction = stats.correction(raw.p, raw.w, u, v, model=model)
        except ValueError as e:
            correction_reason = str(e)
    return FeatureResult(
        analysis,
        feature,
        limit,
        arrays,
        bias,
        p,
        components,
        pooled,
        bland_altman,
        bland_altman_reason,
        model,
        correction,
        correction_reason,
    )


@dataclass(frozen=True)
class AnalysisResult:
    """One analysis over the six features, with the Holm decision of section 11.3 across them."""

    analysis: Analysis
    features: tuple[FeatureResult, ...]
    decision: stats.Decision

    @property
    def feature_p(self) -> FloatArray:
        return np.array([f.feature_p for f in self.features])

    def of(self, feature: str) -> FeatureResult:
        return next(f for f in self.features if f.feature == feature)


def analyse(evaluated: dict[str, Evaluated], study: Study, analysis: Analysis) -> AnalysisResult:
    features = tuple(feature_result(evaluated, study, name, analysis) for name in FEATURE_SET)
    return AnalysisResult(analysis, features, stats.holm(np.array([f.feature_p for f in features])))


def analyse_all(evaluated: dict[str, Evaluated], study: Study) -> tuple[AnalysisResult, ...]:
    return tuple(analyse(evaluated, study, a) for a in ANALYSES)
