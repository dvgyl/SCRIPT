"""Equivalence limits of the bench protocol (`docs/BENCH_PROTOCOL.md` section 10) from the opened sources.

Run it from the repository root:

    UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \\
        python -I python/studies/bench_limits.py

Rule B, the consequence rule. L = RULE_B_FACTOR times the between-subject standard deviation of the
feature on its comparison scale. RULE_B_FACTOR is sqrt(2) times the standard normal quantile of 0.55,
about 0.178. A device bias of L then gives an area under the curve of at most 0.55 when the device is
fully confounded with the case status, because the device-only AUC is Phi(bias / (sqrt(2) SD)). The
threshold 0.55 is a judgement. On the natural logarithm scale, a reported mean and SD give the SD of
ln(x) as sqrt(ln(1 + (SD / mean)^2)), the lognormal relation. Where a source gives several values for
one feature, the limit takes the smallest SD. The source values are typed here from the opened pages.

Judgement limits. `bench.path_length_mm` takes the mean-speed limit, because on the motion interval the
path length is the mean speed times the duration, and the duration is set by the motion system.
`bench.speed_cv` takes one tenth of the smallest designed speed CV of the loops programs (the designed
contrast rule of protocol 4.8), computed from `bench_programs.py`. `bench.band_power_mm2_s2` takes half
the test-retest SEM of the tablet tremor amplitude of Elble and Ellenbogen 2017, a patient group, so it
is a judgement and not a rule B limit. Their SD of the log10 test-retest differences is 0.16, the SEM
is 0.16 / sqrt(2) in log10, and the power of a speed scales with the square of the amplitude.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from scipy.stats import norm

AUC_THRESHOLD = 0.55
RULE_B_FACTOR = math.sqrt(2) * float(norm.ppf(AUC_THRESHOLD))
SIGMA_MOUNT_SHARE = 0.25


@dataclass(frozen=True)
class Candidate:
    """One mean and SD of a feature from an opened source."""

    feature: str
    source: str
    condition: str
    mean: float
    sd: float
    unit: str

    @property
    def ln_sd(self) -> float:
        return math.sqrt(math.log(1 + (self.sd / self.mean) ** 2))

    @property
    def limit(self) -> float:
        return RULE_B_FACTOR * self.ln_sd


GERTH = "Gerth 2016, 25 adults, 133 Hz"
BISIO = "Bisio 2016, 22 adults, 60 Hz"
CANDIDATES = (
    Candidate("bench.motion_s", GERTH, "loops, paper on Wacom Intuos4", 4760.90, 1273.86, "ms"),
    Candidate("bench.motion_s", GERTH, "loops, pen screen", 4549.95, 934.84, "ms"),
    Candidate("bench.motion_s", GERTH, "sentence, paper on Wacom Intuos4", 5039.54, 1414.62, "ms"),
    Candidate("bench.motion_s", GERTH, "sentence, pen screen", 5872.78, 1823.93, "ms"),
    Candidate("bench.speed_mean_mm_s", GERTH, "loops, paper on Wacom Intuos4", 104.55, 36.75, "mm/s"),
    Candidate("bench.speed_mean_mm_s", GERTH, "loops, pen screen", 125.06, 31.97, "mm/s"),
    Candidate("bench.speed_mean_mm_s", GERTH, "sentence, paper on Wacom Intuos4", 30.46, 7.90, "mm/s"),
    Candidate("bench.speed_mean_mm_s", GERTH, "sentence, pen screen", 35.88, 8.93, "mm/s"),
    Candidate("bench.loop_height_mm", BISIO, "sentence thickness, first session", 14.0, 5.0, "mm"),
    Candidate("bench.loop_height_mm", BISIO, "sentence thickness, second session", 15.0, 4.0, "mm"),
)
ELBLE_LOG10_SD_DIFFERENCES = 0.16


def _load_programs() -> ModuleType:
    path = Path(__file__).with_name("bench_programs.py")
    spec = importlib.util.spec_from_file_location("bench_programs", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_programs"] = module
    spec.loader.exec_module(module)
    return module


def rule_b_limits() -> dict[str, Candidate]:
    """The smallest-SD candidate of each rule B feature."""
    chosen: dict[str, Candidate] = {}
    for c in CANDIDATES:
        if c.feature not in chosen or c.ln_sd < chosen[c.feature].ln_sd:
            chosen[c.feature] = c
    return chosen


def limits() -> list[tuple[str, str, float, str]]:
    """The table of protocol section 10 before rounding: feature, scale, limit, and basis."""
    programs = _load_programs()
    loops_cv = min(programs.program(d)[0].speed_cv for d in programs.MAIN if d.geometry == "loops")
    chosen = rule_b_limits()
    sem_ln_amplitude = ELBLE_LOG10_SD_DIFFERENCES / math.sqrt(2) * math.log(10)
    return [
        (
            "bench.motion_s",
            "ln",
            chosen["bench.motion_s"].limit,
            "rule B, " + chosen["bench.motion_s"].condition,
        ),
        (
            "bench.path_length_mm",
            "ln",
            chosen["bench.speed_mean_mm_s"].limit,
            "judgement, equals the mean-speed limit",
        ),
        (
            "bench.speed_mean_mm_s",
            "ln",
            chosen["bench.speed_mean_mm_s"].limit,
            "rule B, " + chosen["bench.speed_mean_mm_s"].condition,
        ),
        (
            "bench.loop_height_mm",
            "ln",
            chosen["bench.loop_height_mm"].limit,
            "rule B, " + chosen["bench.loop_height_mm"].condition + " (proxy)",
        ),
        (
            "bench.speed_cv",
            "absolute",
            loops_cv / 10,
            f"judgement, one tenth of the designed CV {loops_cv:.3f}",
        ),
        (
            "bench.band_power_mm2_s2",
            "ln",
            0.5 * 2 * sem_ln_amplitude,
            f"judgement, half the SEM of a patient group ({2 * sem_ln_amplitude:.3f} ln on power)",
        ),
    ]


def main() -> None:
    print(f"rule B factor: sqrt(2) * Phi^-1({AUC_THRESHOLD}) = {RULE_B_FACTOR:.4f}")
    print("\nfeature\tsource\tcondition\tmean\tSD\tunit\tCV\tln SD\tL (rule B)")
    for c in CANDIDATES:
        print(
            f"{c.feature}\t{c.source}\t{c.condition}\t{c.mean:g}\t{c.sd:g}\t{c.unit}\t"
            f"{c.sd / c.mean:.3f}\t{c.ln_sd:.4f}\t{c.limit:.4f}"
        )
    print("\nfeature\tscale\tL\tsigma_mount bound (0.25 L)\tbasis")
    for feature, scale, limit, basis in limits():
        rounded = round(limit, 3)
        bound = SIGMA_MOUNT_SHARE * rounded
        bound_text = f"{100 * (math.exp(bound) - 1):.2f} percent" if scale == "ln" else f"{bound:.4f}"
        print(f"{feature}\t{scale}\t{rounded:.3f}\t{bound_text}\t{basis}")


if __name__ == "__main__":
    main()
