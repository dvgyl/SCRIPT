"""Turn the bench analysis outputs into the result tables, figures, and numbers of the paper.

The script reads the result folder of the bench analysis and, when they exist, the two result folders of
the consequence experiment. It writes LaTeX files into an output folder. The paper loads each file by
name and shows a visible note for a file that does not exist.

    python3 paper/tools/make_results.py --results bench/results --out <folder>

`--consequence` and `--grid` name the consequence folders. Their defaults are `consequence` and
`consequence_grid` inside the result folder.

A result folder that holds the file `DRY_RUN_SYNTHETIC`, or whose lock names the test fixture, is a dry
run. Every file written from it carries a visible dry-run mark, the script refuses to write it inside
the repository, and the submission build refuses it.

The script uses the Python standard library only. It never fills a missing value: a missing value
prints as "n/a", and a missing input file gives no output file.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

Rows = list[dict[str, str]]

MARKER = "DRY_RUN_SYNTHETIC"
FIXTURE_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
REPOSITORY = Path(__file__).resolve().parents[2]

FEATURES = (
    "bench.motion_s",
    "bench.path_length_mm",
    "bench.speed_mean_mm_s",
    "bench.loop_height_mm",
    "bench.speed_cv",
    "bench.band_power_mm2_s2",
)
FEATURE_LABEL = {
    "bench.motion_s": "Motion duration",
    "bench.path_length_mm": "Path length",
    "bench.speed_mean_mm_s": "Mean speed",
    "bench.loop_height_mm": "Loop height",
    "bench.speed_cv": "Speed CV",
    "bench.band_power_mm2_s2": "Band power",
}
# The program codes of the program table in the paper.
PROGRAM_CODE = {
    "loops-l-v20": "LL20",
    "loops-l-v40": "LL40",
    "loops-l-v80": "LL80",
    "loops-s-v20": "LS20",
    "loops-s-v40": "LS40",
    "spiral-l-v20": "SL20",
    "spiral-l-v40": "SL40",
    "spiral-s-v20": "SS20",
    "spiral-s-v40": "SS40",
    "line-osc5": "O5",
    "line-osc8": "O8",
    "line-osc11": "O11",
}
PROGRAM_ORDER = tuple(PROGRAM_CODE)
FAULT_LABEL = {
    "fault-dwell": "Dwell fault",
    "fault-lift": "Lift fault",
    "fault-truncated": "Truncation fault",
}
MODEL_LABEL = {"covariates": "Covariates", "slope": "Slope", "shift": "Shift"}
SCENARIO_LABEL = {
    "all_w": "One device",
    "transfer": "Transfer",
    "confounded": "Confounding",
}
SUPPORT_LABEL = {
    "bench_supported": "supported",
    "failed_lack_of_fit": "failed check",
    "unavailable": "unavailable",
}
NA = "n/a"
DRY_MARK = r"\dryrunmark{}"


# ----------------------------------------------------------------------------------------------------
# Reading and formatting


def read_csv(path: Path) -> Rows:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def value(text: str | None) -> float | None:
    if text is None or text == "":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def fixed(number: float | None, digits: int, *, sign: bool = False) -> str:
    if number is None:
        return NA
    text = f"{number:+.{digits}f}" if sign else f"{number:.{digits}f}"
    if float(text) == 0:
        text = f"{0:.{digits}f}"
    return text.replace("-", "$-$").replace("+", "$+$") if sign else text.replace("-", "$-$")


def significant(number: float | None, digits: int = 2) -> str:
    """A number with the given count of significant digits, without an exponent where practical."""
    if number is None:
        return NA
    if number == 0:
        return "0"
    exponent = math.floor(math.log10(abs(number)))
    if -4 <= exponent < 5:
        decimals = max(0, digits - 1 - exponent)
        return f"{number:.{decimals}f}".replace("-", "$-$")
    mantissa = number / 10**exponent
    return f"${mantissa:.{digits - 1}f}\\times10^{{{exponent}}}$"


def p_value(number: float | None) -> str:
    if number is None:
        return NA
    return "$<$0.001" if number < 0.001 else f"{number:.3f}"


def interval(low: float | None, high: float | None, digits: int, *, sign: bool = False) -> str:
    if low is None or high is None:
        return NA
    return f"[{fixed(low, digits, sign=sign)}, {fixed(high, digits, sign=sign)}]"


def percent_ratio(log_difference: float | None) -> float | None:
    """A difference on the natural-logarithm scale as a percent difference of the ratio."""
    return None if log_difference is None else 100.0 * math.expm1(log_difference)


def code(program: str) -> str:
    return PROGRAM_CODE.get(program, program.replace("_", r"\_"))


def program_key(program: str) -> int:
    return PROGRAM_ORDER.index(program) if program in PROGRAM_ORDER else len(PROGRAM_ORDER)


def yes_no(text: str) -> str:
    return {"true": "yes", "false": "no"}.get(text, NA)


def main_rows(rows: Rows) -> Rows:
    return [r for r in rows if r.get("analysis", "main") == "main"]


class Output:
    """The output folder. Every file starts with a comment that names its source."""

    def __init__(self, folder: Path, source: str, dry_run: bool) -> None:
        self.folder = folder
        self.source = source
        self.dry_run = dry_run
        self.written: list[str] = []
        folder.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, body: str) -> None:
        kind = "DRY RUN from synthetic test input. NOT A RESULT." if self.dry_run else "Measured results."
        head = f"% Generated by the results script. Do not edit. {kind}\n% Source: {self.source}\n"
        (self.folder / f"{name}.tex").write_text(head + body.rstrip() + "\n", encoding="utf-8")
        self.written.append(name)

    def caption(self, text: str) -> str:
        return rf"\caption{{{DRY_MARK + ' ' if self.dry_run else ''}{text}}}"


# ----------------------------------------------------------------------------------------------------
# Completeness


def completeness(results: Path, out: Output, numbers: dict[str, str]) -> None:
    status = read_csv(results / "run_status.csv")
    agreement = main_rows(read_csv(results / "agreement.csv"))
    lines = [
        r"\begin{tabular}{@{}lrr@{}}",
        r"\toprule",
        r"Count & Device P & Device W \\",
        r"\midrule",
    ]
    table: dict[str, dict[str, int]] = defaultdict(dict)
    for device in ("P", "W"):
        rows = [r for r in status if r["device"] == device]
        main = [r for r in rows if r["kind"] == "main"]
        planned = [r for r in main if int(r["run_index"] or 0) <= 5]
        reruns = [r for r in main if int(r["run_index"] or 0) >= 6]
        table["Planned main runs"][device] = len(planned)
        table["Failed planned main runs"][device] = sum(r["failed"] == "true" for r in planned)
        table["Reruns recorded"][device] = len(reruns)
        table["Reruns not used"][device] = sum(r["reason"] == "unused_rerun" for r in reruns)
        table["Valid main runs in the analysis"][device] = sum(r["valid"] == "true" for r in main)
        table["Valid main runs with a valid camera record"][device] = sum(
            r["valid"] == "true" and r["camera"] == "valid" for r in main
        )
        table["Motion failures"][device] = sum(r["motion_failure"] == "true" for r in main)
        table["Fault runs"][device] = sum(r["kind"] == "fault" for r in rows)
    for label, counts in table.items():
        lines.append(f"{label} & {counts['P']} & {counts['W']} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    not_estimable = sorted(
        {(r["feature"], r["program"]) for r in agreement if r["level"] == "program" and r["estimable"] == "false"}
    )
    note = (
        "Every program is estimable for every feature."
        if not not_estimable
        else "Not estimable: "
        + "; ".join(f"{FEATURE_LABEL.get(f, f)} on {code(p)}" for f, p in not_estimable)
        + "."
    )
    body = "\n".join(
        [
            r"\begin{table}[t]",
            r"\centering",
            out.caption(
                "Run counts. A valid run passed the quality checks and the executed-movement check. " + note
            ),
            r"\label{tab:completeness}",
            *lines,
            r"\end{table}",
        ]
    )
    out.write("tab_completeness", body)
    numbers["MainValidP"] = str(table["Valid main runs in the analysis"]["P"])
    numbers["MainValidW"] = str(table["Valid main runs in the analysis"]["W"])
    numbers["MainPlanned"] = str(table["Planned main runs"]["P"] + table["Planned main runs"]["W"])
    numbers["NotEstimable"] = str(len(not_estimable))
    reasons = Counter(r["reason"] for r in status if r["kind"] == "main" and r["failed"] == "true")
    numbers["FailureReasons"] = (
        "; ".join(f"{k.replace('_', ' ') or 'no reason given'}: {n}" for k, n in sorted(reasons.items()))
        or "none"
    )


# ----------------------------------------------------------------------------------------------------
# Sampling and accuracy


def _range(values: Sequence[float | None]) -> tuple[float | None, float | None, float | None]:
    found = sorted(v for v in values if v is not None)
    if not found:
        return None, None, None
    middle = found[len(found) // 2] if len(found) % 2 else 0.5 * (found[len(found) // 2 - 1] + found[len(found) // 2])
    return middle, found[0], found[-1]


def _summary(values: Sequence[float | None], digits: int, factor: float = 1.0) -> str:
    middle, low, high = _range([None if v is None else v * factor for v in values])
    if middle is None:
        return NA
    return f"{fixed(middle, digits)} ({fixed(low, digits)} to {fixed(high, digits)})"


def sampling(results: Path, out: Output, numbers: dict[str, str]) -> None:
    sampling_rows = read_csv(results / "sampling.csv")
    accuracy = read_csv(results / "accuracy.csv")
    cells: dict[str, dict[str, str]] = defaultdict(dict)
    for device in ("P", "W"):
        intervals = [r for r in sampling_rows if r["device"] == device and r["kind"] == "intervals"]
        medians = [value(r["median_s"]) for r in intervals]
        cells["Sample interval, ms"][device] = _summary(medians, 2, 1000)
        middle = _range(medians)[0]
        cells["Sampling rate, Hz"][device] = fixed(None if not middle else 1 / middle, 1)
        numbers[f"Rate{device}"] = fixed(None if not middle else 1 / middle, 0)
        low = _range([value(r["p5_low_s"]) for r in intervals])[1]
        high = _range([value(r["p95_high_s"]) for r in intervals])[2]
        cells["5th to 95th percentile interval, ms"][device] = (
            NA if low is None or high is None else f"{fixed(low * 1000, 2)} to {fixed(high * 1000, 2)}"
        )
        cells["Intervals above 30 ms, \\%"][device] = fixed(
            _range([value(r["share_above_30ms"]) for r in intervals])[2], 2
        ) if intervals else NA
        gap = _range([value(r["share_above_max_gap"]) for r in intervals])[2]
        cells["Intervals above 50 ms, \\%"][device] = fixed(None if gap is None else 100 * gap, 2)
        share30 = _range([value(r["share_above_30ms"]) for r in intervals])[2]
        cells["Intervals above 30 ms, \\%"][device] = fixed(None if share30 is None else 100 * share30, 2)
        for kind, label in (
            ("noise_floor_hold", "Noise floor at rest, (mm/s)$^2$"),
            ("noise_floor_spiral", "Noise floor in spiral runs, (mm/s)$^2$"),
        ):
            row = next(
                (
                    r
                    for r in sampling_rows
                    if r["device"] == device and r["kind"] == kind and r["mounting"] == "all"
                ),
                None,
            )
            cells[label][device] = significant(value(row["band_power_mm2_s2"]) if row else None)
        for measure, label, digits, factor in (
            ("camera_rms_mm", "Distance to camera track, RMS, mm", 3, 1.0),
            ("camera_p95_mm", "Distance to camera track, 95th percentile, mm", 3, 1.0),
            ("camera_rotation_deg", "Fitted rotation to camera track, degrees", 2, 1.0),
            ("trace_rms_mm", "Distance to commanded path, RMS, mm", 3, 1.0),
            ("executed_max_mm", "Executed movement: largest distance to command, mm", 3, 1.0),
            ("executed_duration_error", "Executed movement: duration error, \\%", 2, 100.0),
        ):
            rows = [r for r in accuracy if r["device"] == device and r["measure"] == measure]
            middle = _range([value(r["median"]) for r in rows])[0]
            low = _range([value(r["low"]) for r in rows])[1]
            high = _range([value(r["high"]) for r in rows])[2]
            cells[label][device] = (
                NA
                if middle is None
                else f"{fixed(middle * factor, digits)} ({fixed(low * factor, digits)} to "
                f"{fixed(high * factor, digits)})"
            )
        rotation = [r for r in accuracy if r["device"] == device and r["measure"] == "camera_rotation_deg"]
        cells["Runs with a fitted rotation above 1 degree"][device] = str(
            sum(int(r["deviations"] or 0) for r in rotation)
        )
    lines = [
        r"\begin{tabular}{@{}lll@{}}",
        r"\toprule",
        r"Measure & Device P & Device W \\",
        r"\midrule",
    ]
    lines += [f"{label} & {both.get('P', NA)} & {both.get('W', NA)} \\\\" for label, both in cells.items()]
    lines += [r"\bottomrule", r"\end{tabular}"]
    body = "\n".join(
        [
            r"\begin{table}[t]",
            r"\centering",
            out.caption(
                "Sampling and spatial accuracy of each device over the valid main runs. "
                "An entry with a range is the median of the program medians, with the smallest and the "
                "largest run value. The interval rows give the median of the program medians and the "
                "widest percentile range. These measures have no acceptance limit."
            ),
            r"\label{tab:sampling}",
            *lines,
            r"\end{table}",
        ]
    )
    out.write("tab_sampling", body)


# ----------------------------------------------------------------------------------------------------
# Agreement


def _natural(feature_scale: str, number: float | None) -> str:
    """A bias in natural terms: a percent difference for a logarithmic feature, else the difference."""
    if number is None:
        return NA
    if feature_scale == "ln":
        return fixed(percent_ratio(number), 2, sign=True) + r"\,\%"
    return fixed(number, 4, sign=True)


def agreement(results: Path, out: Output, numbers: dict[str, str]) -> None:
    rows = main_rows(read_csv(results / "agreement.csv"))
    lines = [
        r"\begin{tabular}{@{}llrrllrll@{}}",
        r"\toprule",
        r"Feature & Scale & $L$ & Programs & Deciding & Bias in units of $L$ & Bias & $p$ & Equivalent \\",
        r" & & & & program & (90\,\% CI) & & & (Holm) \\",
        r"\midrule",
    ]
    equivalent: list[str] = []
    not_shown: list[str] = []
    for feature in FEATURES:
        programs = [r for r in rows if r["feature"] == feature and r["level"] == "program"]
        decision = next((r for r in rows if r["feature"] == feature and r["level"] == "feature"), None)
        if decision is None:
            continue
        limit = value(decision["limit"]) or float("nan")
        estimable = [r for r in programs if r["estimable"] == "true"]
        deciding = max(
            estimable,
            key=lambda r: (value(r["p"]) or 0.0, abs(value(r["bias"]) or 0.0)),
            default=None,
        )
        if deciding is None:
            middle = [NA, NA, NA]
        else:
            bias, low, high = value(deciding["bias"]), value(deciding["lower"]), value(deciding["upper"])
            middle = [
                code(deciding["program"]),
                (
                    NA
                    if bias is None
                    else f"{fixed(bias / limit, 2, sign=True)} "
                    + interval(
                        None if low is None else low / limit, None if high is None else high / limit, 2, sign=True
                    )
                ),
                _natural(decision["scale"], bias),
            ]
        adjusted = decision["adjusted"] == "true"
        (equivalent if adjusted else not_shown).append(FEATURE_LABEL[feature])
        mark = "yes" if adjusted else "not shown"
        if decision["adjusted"] != decision["unadjusted"]:
            mark += r"\textsuperscript{a}"
        lines.append(
            " & ".join(
                [
                    FEATURE_LABEL[feature],
                    "ln" if decision["scale"] == "ln" else "abs.",
                    fixed(limit, 3),
                    f"{len(estimable)}/{len(programs)}",
                    *middle,
                    p_value(value(decision["feature_p"])),
                    mark,
                ]
            )
            + r" \\"
        )
    lines += [r"\bottomrule", r"\end{tabular}"]
    body = "\n".join(
        [
            r"\begin{table*}[t]",
            r"\centering",
            out.caption(
                "Feature agreement between device P and device W. A feature is equivalent when the "
                "90\\,\\% interval of the bias (P minus W) lies inside $\\pm L$ on every program of its "
                "set, after the Holm adjustment across the six features. `Programs' gives the estimable "
                "programs and the size of the program set. The deciding program has the largest "
                "equivalence $p$-value; the table gives its bias. For a logarithmic feature, the bias "
                "is also given as a percent difference. \\textsuperscript{a}The unadjusted decision "
                "differs from the adjusted decision."
            ),
            r"\label{tab:agreement}",
            *lines,
            r"\end{table*}",
        ]
    )
    out.write("tab_agreement", body)
    numbers["EquivalentCount"] = str(len(equivalent))
    numbers["EquivalentList"] = ", ".join(equivalent).lower() or "none"
    numbers["NotShownList"] = ", ".join(not_shown).lower() or "none"

    # The figure: one panel per feature, the bias of each program in units of the limit.
    panels: list[str] = []
    for feature in FEATURES:
        programs = sorted(
            (r for r in rows if r["feature"] == feature and r["level"] == "program"),
            key=lambda r: program_key(r["program"]),
        )
        if not programs:
            continue
        limit = value(programs[0]["limit"]) or float("nan")
        coordinates = []
        for index, r in enumerate(programs, start=1):
            bias, low, high = value(r["bias"]), value(r["lower"]), value(r["upper"])
            if bias is None or low is None or high is None:
                continue
            coordinates.append(
                f"({index},{bias / limit:.5g}) += (0,{(high - bias) / limit:.5g}) "
                f"-= (0,{(bias - low) / limit:.5g})"
            )
        ticks = ",".join(str(i) for i in range(1, len(programs) + 1))
        labels = ",".join(code(r["program"]) for r in programs)
        panels.append(
            "\n".join(
                [
                    rf"\nextgroupplot[title={{{FEATURE_LABEL[feature]}}}, xtick={{{ticks}}}, "
                    rf"xticklabels={{{labels}}}, xmin=0.4, xmax={len(programs) + 0.6}]",
                    r"\draw[densely dashed] (axis cs:0.4,1) -- (axis cs:" + f"{len(programs) + 0.6}" + ",1);",
                    r"\draw[densely dashed] (axis cs:0.4,-1) -- (axis cs:" + f"{len(programs) + 0.6}" + ",-1);",
                    r"\draw[gray] (axis cs:0.4,0) -- (axis cs:" + f"{len(programs) + 0.6}" + ",0);",
                    r"\addplot+[only marks, mark=*, mark size=1.2pt, black, "
                    r"error bars/.cd, y dir=both, y explicit] coordinates {",
                    *coordinates,
                    "};",
                ]
            )
        )
    figure = "\n".join(
        [
            r"\begin{figure*}[t]",
            r"\centering",
            r"\begin{tikzpicture}",
            r"\begin{groupplot}[group style={group size=3 by 2, horizontal sep=1.3cm, vertical sep=1.6cm},",
            r"  width=0.36\textwidth, height=4.2cm, ylabel={Bias / $L$}, enlarge y limits=0.15,",
            r"  x tick label style={rotate=60, anchor=east, font=\scriptsize}, title style={font=\small},",
            r"  ymin=-1.2, ymax=1.2, clip=false, y tick label style={font=\scriptsize}, ylabel style={font=\small}]",
            *panels,
            r"\end{groupplot}",
            r"\end{tikzpicture}",
            out.caption(
                "Bias of each program (P minus W) in units of the equivalence limit $L$, with its "
                "90\\,\\% interval. The dashed lines are the limits. A feature is equivalent only when "
                "every interval of its panel lies between the dashed lines. Table~\\ref{tab:programs} "
                "defines the program codes."
            ),
            r"\label{fig:agreement}",
            r"\end{figure*}",
        ]
    )
    out.write("fig_agreement", figure)


def bland_altman(results: Path, out: Output) -> None:
    rows = main_rows(read_csv(results / "bland_altman.csv"))
    panels: list[str] = []
    table = [
        r"\begin{tabular}{@{}lrrrrr@{}}",
        r"\toprule",
        r"Feature & Mean difference & \multicolumn{2}{c}{Mounting means} & \multicolumn{2}{c}{Single runs} \\",
        r" & & Lower & Upper & Lower & Upper \\",
        r"\midrule",
    ]
    for feature in FEATURES:
        points = [r for r in rows if r["feature"] == feature and r["kind"] == "point"]
        pooled = next((r for r in rows if r["feature"] == feature and r["kind"] == "pooled"), None)
        single = next((r for r in rows if r["feature"] == feature and r["kind"] == "single_run"), None)
        table.append(
            " & ".join(
                [
                    FEATURE_LABEL[feature],
                    significant(value(pooled["mean"]) if pooled else None, 3),
                    significant(value(pooled["lower"]) if pooled else None, 3),
                    significant(value(pooled["upper"]) if pooled else None, 3),
                    significant(value(single["lower"]) if single else None, 3),
                    significant(value(single["upper"]) if single else None, 3),
                ]
            )
            + r" \\"
        )
        coordinates = [
            f"({value(r['average']):.6g},{value(r['difference']):.6g})"
            for r in points
            if value(r["average"]) is not None and value(r["difference"]) is not None
        ]
        if not coordinates:
            continue
        extra = ""
        if pooled and value(pooled["lower"]) is not None and value(pooled["upper"]) is not None:
            extra = (
                rf"extra y ticks={{{value(pooled['lower']):.6g},{value(pooled['mean']):.6g},"
                rf"{value(pooled['upper']):.6g}}}, extra y tick labels={{}}, "
                r"extra y tick style={grid=major, grid style={densely dashed, black}}, "
            )
        panels.append(
            "\n".join(
                [
                    rf"\nextgroupplot[title={{{FEATURE_LABEL[feature]}}}, {extra}]",
                    r"\addplot+[only marks, mark=o, mark size=1pt, black] coordinates {",
                    " ".join(coordinates),
                    "};",
                ]
            )
        )
    table += [r"\bottomrule", r"\end{tabular}"]
    out.write(
        "tab_bland_altman",
        "\n".join(
            [
                r"\begin{table}[t]",
                r"\centering",
                out.caption(
                    "Limits of agreement between the devices (P minus W) on the comparison scale, "
                    "pooled over the program set of each feature. The limits for mounting means use "
                    "the paired mountings. The limits for single runs add the run variances."
                ),
                r"\label{tab:blandaltman}",
                *table,
                r"\end{table}",
            ]
        ),
    )
    if panels:
        out.write(
            "fig_bland_altman",
            "\n".join(
                [
                    r"\begin{figure}[t]",
                    r"\centering",
                    r"\begin{tikzpicture}",
                    r"\begin{groupplot}[group style={group size=2 by 3, horizontal sep=1.6cm, "
                    r"vertical sep=1.5cm}, width=0.5\linewidth, height=4cm, "
                    r"tick label style={font=\scriptsize}, title style={font=\small}, "
                    r"scaled ticks=false, tick label style={/pgf/number format/fixed, "
                    r"/pgf/number format/precision=4, font=\scriptsize}]",
                    *panels,
                    r"\end{groupplot}",
                    r"\end{tikzpicture}",
                    out.caption(
                        "Bland--Altman plots on the comparison scale. Each point is one program and one "
                        "pair of mountings: the difference of the mounting means (P minus W) against "
                        "their average. The dashed lines are the mean difference and the pooled limits "
                        "of agreement for mounting means."
                    ),
                    r"\label{fig:blandaltman}",
                    r"\end{figure}",
                ]
            ),
        )


# ----------------------------------------------------------------------------------------------------
# Correction and quality gate


def correction(results: Path, out: Output, numbers: dict[str, str]) -> None:
    rows = main_rows(read_csv(results / "correction.csv"))
    limits = {
        r["feature"]: value(r["limit"])
        for r in main_rows(read_csv(results / "agreement.csv"))
        if r["level"] == "feature"
    }
    lines = [
        r"\begin{tabular}{@{}llllllll@{}}",
        r"\toprule",
        r"Feature & Model & $\alpha$ & $\beta$ & $\gamma_u$ & $\gamma_v$ & Largest residual / $L$ & Check \\",
        r"\midrule",
    ]
    passed = 0
    for feature in FEATURES:
        mine = [r for r in rows if r["feature"] == feature]
        if not mine:
            continue
        coefficients = {r["term"]: r for r in mine if r["kind"] == "coefficient" and r["term"]}
        residuals = [r for r in mine if r["kind"] == "residual"]
        limit = limits.get(feature) or float("nan")
        terms = []
        for term in ("alpha", "beta", "gamma_u", "gamma_v"):
            r = coefficients.get(term)
            if r is None or value(r["estimate"]) is None:
                terms.append(NA)
            elif r["fitted"] == "true":
                terms.append(f"{significant(value(r['estimate']), 4)} ({significant(value(r['se']), 2)})")
            else:
                terms.append(f"{significant(value(r['estimate']), 3)} (fixed)")
        worst = max(
            (r for r in residuals if value(r["lower"]) is not None and value(r["upper"]) is not None),
            key=lambda r: max(abs(value(r["lower"]) or 0.0), abs(value(r["upper"]) or 0.0)),
            default=None,
        )
        if worst is None:
            residual, check = NA, "unavailable"
        else:
            residual = (
                f"{fixed((value(worst['estimate']) or 0.0) / limit, 2, sign=True)} "
                + interval(
                    (value(worst["lower"]) or 0.0) / limit, (value(worst["upper"]) or 0.0) / limit, 2, sign=True
                )
                + f" ({code(worst['program'])})"
            )
            ok = all(r["within_limit"] == "true" for r in residuals)
            passed += ok
            check = "passed" if ok else "failed"
        model = MODEL_LABEL.get(mine[0]["model"], NA)
        lines.append(" & ".join([FEATURE_LABEL[feature], model, *terms, residual, check]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    body = "\n".join(
        [
            r"\begin{table*}[t]",
            r"\centering",
            out.caption(
                "Cross-device correction from device W to device P on the comparison scale, with "
                "jackknife standard errors in parentheses. A fixed term is not fitted in its model. "
                "The residual is the mean bias that remains on held-out mountings, with its 90\\,\\% "
                "interval, for the program with the widest interval. The check passes when the "
                "interval of every program lies inside $\\pm L$. It is a lack-of-fit check, not an "
                "independent validation."
            ),
            r"\label{tab:correction}",
            *lines,
            r"\end{table*}",
        ]
    )
    out.write("tab_correction", body)
    numbers["CorrectionPassed"] = str(passed)


def gate(results: Path, out: Output) -> None:
    rows = read_csv(results / "gate.csv")

    def cell(device: str, program: str, kind: str) -> str:
        r = next(
            (x for x in rows if x["device"] == device and x["program"] == program and x["kind"] == kind), None
        )
        if r is None or not r["total"] or int(r["total"]) == 0:
            return NA
        return (
            f"{r['successes']}/{r['total']} "
            f"[{fixed(value(r['lower']), 2)}, {fixed(value(r['upper']), 2)}]"
        )

    lines = [
        r"\begin{tabular}{@{}lll@{}}",
        r"\toprule",
        r"Check & Device P & Device W \\",
        r"\midrule",
        f"Main runs accepted & {cell('P', '', 'accepted')} & {cell('W', '', 'accepted')} \\\\",
    ]
    for fault, label in FAULT_LABEL.items():
        lines.append(f"{label} detected & {cell('P', fault, 'detected')} & {cell('W', fault, 'detected')} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    body = "\n".join(
        [
            r"\begin{table}[t]",
            r"\centering",
            out.caption(
                "Quality checks. Each entry gives the count, the total, and the exact 95\\,\\% "
                "Clopper--Pearson interval of the share."
            ),
            r"\label{tab:gate}",
            *lines,
            r"\end{table}",
        ]
    )
    out.write("tab_gate", body)


# ----------------------------------------------------------------------------------------------------
# Consequence experiment


def _is_primary(row: dict[str, str]) -> bool:
    """The primary summaries of the consequence protocol, as its primary table selects them."""
    scenario, validation, metric = row["scenario"], row["validation"], row["metric"]
    if row.get("sensitivity", "primary") != "primary":
        return False
    if scenario == "all_w":
        return validation == "participant" and metric == "auc"
    if scenario == "transfer":
        return validation == "device" and row["direction"] == "W-to-P" and metric == "intercept"
    if scenario == "confounded":
        return validation == "participant" and metric == "auc" and float(row["effect"]) == 0.0
    return False


def _summary_rows(folder: Path) -> Rows:
    path = folder / "consequence_summary.csv"
    return read_csv(path) if path.is_file() else []


def consequence_table(folder: Path, out: Output, name: str, label: str, numbers: dict[str, str]) -> None:
    primary_path = folder / "table_primary.csv"
    rows = read_csv(primary_path) if primary_path.is_file() else [r for r in _summary_rows(folder) if _is_primary(r)]
    if not rows:
        return
    incomplete = 0
    lines = [
        r"\begin{tabular}{@{}llrlllrl@{}}",
        r"\toprule",
        r"Scenario and metric & Feature & Effect & Raw change & Corrected change & Absolute reduction "
        r"& Pairs & Correction \\",
        r"\midrule",
    ]
    for scenario in ("all_w", "transfer", "confounded"):
        metric = "calibration intercept" if scenario == "transfer" else "AUC"
        first = True
        for feature in FEATURES:
            effects = sorted({float(r["effect"]) for r in rows if r["scenario"] == scenario and r["feature"] == feature})
            for effect in effects:
                cell = {
                    r["contrast"]: r
                    for r in rows
                    if r["scenario"] == scenario and r["feature"] == feature and float(r["effect"]) == effect
                }
                raw = cell.get("D_raw")
                if raw is None:
                    continue

                def show(r: dict[str, str] | None) -> str:
                    if r is None or value(r["mean"]) is None:
                        return NA
                    return f"{fixed(value(r['mean']), 3, sign=True)} " + interval(
                        value(r["mean_lower"]), value(r["mean_upper"]), 3, sign=True
                    )

                usable = raw["usable_R"] or "0"
                status = "" if raw["status"] == "ok" else r"\textsuperscript{b}"
                incomplete += raw["status"] != "ok"
                support = raw.get("correction_support", "")
                lines.append(
                    " & ".join(
                        [
                            f"{SCENARIO_LABEL[scenario]}, {metric}" if first else "",
                            FEATURE_LABEL.get(feature, feature),
                            fixed(effect, 1),
                            show(raw),
                            show(cell.get("D_corrected")),
                            show(cell.get("absolute_reduction")),
                            f"{usable}/{raw['planned_R']}{status}",
                            SUPPORT_LABEL.get(support, support.replace("_", " ") or NA),
                        ]
                    )
                    + r" \\"
                )
                first = False
        if scenario != "confounded":
            lines.append(r"\addlinespace")
    lines += [r"\bottomrule", r"\end{tabular}"]
    source = "the measured bench bias and noise" if label == "measured" else "the assumed grid of bias and noise"
    body = "\n".join(
        [
            r"\begin{table*}[t]",
            r"\centering",
            out.caption(
                f"Primary summaries of the consequence experiment with {source}. Each change is a "
                "paired difference from the reference cohort, in which every record uses device P: the "
                "mean over the generated cohorts with the 95\\,\\% interval of the mean. A negative "
                "absolute reduction means that the correction moved the metric away from the "
                "reference. `Effect' is the standardised case-control difference. `Pairs' gives the "
                "usable and the planned cohort pairs. \\textsuperscript{b}Fewer than 95\\,\\% of the "
                "planned pairs are usable."
            ),
            rf"\label{{tab:{name}}}",
            *lines,
            r"\end{table*}",
        ]
    )
    out.write(f"tab_{name}", body)
    numbers[f"Consequence{label.capitalize()}Incomplete"] = str(incomplete)


def _mean_series(rows: Rows, key: str) -> list[tuple[float, float, float, float]]:
    series = []
    for r in rows:
        x, mean = value(r[key]), value(r["mean"])
        low, high = value(r["mean_lower"]), value(r["mean_upper"])
        if x is None or mean is None:
            continue
        series.append((x, mean, mean if low is None else low, mean if high is None else high))
    return sorted(series)


def _plot(series: list[tuple[float, float, float, float]], style: str) -> str:
    coordinates = " ".join(f"({x:.5g},{y:.5g}) += (0,{hi - y:.5g}) -= (0,{y - lo:.5g})" for x, y, lo, hi in series)
    return rf"\addplot+[{style}, error bars/.cd, y dir=both, y explicit] coordinates {{{coordinates}}};"


def consequence_figure(grid: Path, measured: Path | None, out: Output) -> None:
    response_path = grid / "figure_bias_response.csv"
    curves_path = grid / "figure_decision_curves.csv"
    cells_path = grid / "consequence_cells.csv"
    if not (response_path.is_file() and curves_path.is_file() and cells_path.is_file()):
        return
    response = [r for r in read_csv(response_path) if r.get("sensitivity", "primary") == "primary"]
    feature = FEATURES[0]
    styles = {
        "zero": "black, mark=o, mark size=1.2pt",
        "nominal": "black, mark=*, mark size=1.2pt",
        "stress": "black, mark=square*, mark size=1.2pt",
    }

    def panel(scenario: str, metric: str, validation: str, direction: str, effect: float, title: str, ylabel: str) -> str:
        lines = [rf"\nextgroupplot[title={{{title}}}, xlabel={{Bias / $L$}}, ylabel={{{ylabel}}}]"]
        for noise, style in styles.items():
            for contrast, dash in (("D_raw", "solid"), ("D_corrected", "densely dashed")):
                rows = [
                    r
                    for r in response
                    if r["feature"] == feature
                    and r["scenario"] == scenario
                    and r["metric"] == metric
                    and r["validation"] == validation
                    and r["direction"] == direction
                    and float(r["effect"]) == effect
                    and r["noise_id"] == noise
                    and r["contrast"] == contrast
                ]
                series = _mean_series(rows, "x_bias_over_L")
                if series:
                    lines.append(_plot(series, f"{style}, {dash}"))
        if measured is not None and (measured / "consequence_inputs.csv").is_file():
            inputs = {r["feature"]: r for r in read_csv(measured / "consequence_inputs.csv")}
            marks = []
            for r in _summary_rows(measured):
                if (
                    r.get("sensitivity", "primary") == "primary"
                    and r["scenario"] == scenario
                    and r["metric"] == metric
                    and r["validation"] == validation
                    and r["direction"] == direction
                    and float(r["effect"]) == effect
                    and r["contrast"] == "D_raw"
                    and r["feature"] in inputs
                ):
                    bias, limit = value(inputs[r["feature"]]["bias"]), value(inputs[r["feature"]]["limit"])
                    mean = value(r["mean"])
                    if bias is not None and limit and mean is not None and abs(bias / limit) <= 2.2:
                        marks.append(f"({bias / limit:.5g},{mean:.5g})")
            if marks:
                lines.append(
                    r"\addplot+[only marks, mark=triangle*, mark size=2pt, red] coordinates {"
                    + " ".join(marks)
                    + "};"
                )
        return "\n".join(lines)

    cells = {r["cell_id"]: r for r in read_csv(cells_path)}
    target = next(
        (
            cid
            for cid, c in cells.items()
            if c["feature"] == feature
            and c["scenario"] == "confounded"
            and float(c["effect"]) == 0.8
            and value(c["bias_multiple"]) == 1.0
            and c["noise_id"] == "nominal"
            and c.get("sensitivity", "primary") == "primary"
        ),
        None,
    )
    curve_lines = [r"\nextgroupplot[title={(c) Decision curves}, xlabel={Threshold}, ylabel={Net benefit}]"]
    if target is not None:
        curves = [
            r
            for r in read_csv(curves_path)
            if r["cell_id"] == target and r["validation"] == "participant" and r["metric"] in ("model_nb", "all_nb")
        ]
        for version, metric, style in (
            ("reference", "model_nb", "black, solid, no marks"),
            ("uncorrected", "model_nb", "black, densely dashed, no marks"),
            ("corrected", "model_nb", "black, dotted, thick, no marks"),
            ("reference", "all_nb", "gray, solid, no marks"),
        ):
            series = _mean_series([r for r in curves if r["version"] == version and r["metric"] == metric], "threshold")
            if series:
                coordinates = " ".join(f"({x:.4g},{y:.5g})" for x, y, _, _ in series)
                curve_lines.append(rf"\addplot+[{style}] coordinates {{{coordinates}}};")
    figure = "\n".join(
        [
            r"\begin{figure*}[t]",
            r"\centering",
            r"\begin{tikzpicture}",
            r"\begin{groupplot}[group style={group size=3 by 1, horizontal sep=1.7cm}, "
            r"width=0.34\textwidth, height=5cm, tick label style={font=\scriptsize}, "
            r"label style={font=\small}, title style={font=\small}, "
            r"y tick label style={/pgf/number format/fixed, /pgf/number format/precision=3}]",
            panel("confounded", "auc", "participant", "internal", 0.0, "(a) Confounding, no true effect", "Change in AUC"),
            panel("transfer", "intercept", "device", "W-to-P", 0.8, "(b) Transfer from W to P", "Change in intercept"),
            *curve_lines,
            r"\end{groupplot}",
            r"\end{tikzpicture}",
            out.caption(
                "Consequence of a device bias for a screening model in generated cohorts. (a) Change "
                "in participant-wise AUC from the reference when the device is associated with case "
                "status and the feature has no true effect. (b) Change in the calibration intercept "
                "when a model trained on device W is tested on device P. The horizontal axis is the "
                "assumed bias in units of the equivalence limit $L$. Circles, dots, and squares are "
                "the zero, nominal, and stress noise settings. Solid lines are uncorrected and dashed "
                "lines are corrected with the known shift. Bars are 95\\,\\% intervals of the mean. "
                "Red triangles are the results with the measured bench inputs, one per feature. "
                "(c) Mean net benefit for the confounded cohort with an effect of 0.8 and a bias of "
                "$L$ at nominal noise: reference (solid), uncorrected (dashed), corrected (dotted), "
                "and the classify-all strategy (grey). Panels (a) and (b) use the assumed grid for "
                "motion duration."
            ),
            r"\label{fig:consequence}",
            r"\end{figure*}",
        ]
    )
    out.write("fig_consequence", figure)


def program_selection(folder: Path, out: Output) -> None:
    path = folder / "consequence_inputs.csv"
    if not path.is_file():
        return
    seen: dict[str, dict[str, str]] = {}
    for r in read_csv(path):
        seen.setdefault(r["feature"], r)
    lines = [
        r"\begin{tabular}{@{}llrrrl@{}}",
        r"\toprule",
        r"Feature & Program & Distance & Frequency & Bias / $L$ & In range \\",
        r" & & & difference, Hz & & \\",
        r"\midrule",
    ]
    for feature in FEATURES:
        r = seen.get(feature)
        if r is None:
            continue
        bias, limit = value(r["bias"]), value(r["limit"])
        lines.append(
            " & ".join(
                [
                    FEATURE_LABEL[feature],
                    code(r["program"]),
                    fixed(value(r["distance"]), 2),
                    fixed(value(r["frequency_distance_hz"]), 0),
                    fixed(None if bias is None or not limit else bias / limit, 2, sign=True),
                    {"True": "yes", "False": "no"}.get(r["in_range"], NA),
                ]
            )
            + r" \\"
        )
    lines += [r"\bottomrule", r"\end{tabular}"]
    out.write(
        "tab_selection",
        "\n".join(
            [
                r"\begin{table}[t]",
                r"\centering",
                out.caption(
                    "Bench inputs of the consequence experiment. For each feature, the table gives the "
                    "selected program, its distance from the target in scale and speed, the absolute "
                    "difference of the oscillation frequency, and the measured bias of that program."
                ),
                r"\label{tab:selection}",
                *lines,
                r"\end{table}",
            ]
        ),
    )


# ----------------------------------------------------------------------------------------------------
# Entry point


def is_dry_run(results: Path) -> bool:
    if (results / MARKER).is_file():
        return True
    path = results / "deliverables.json"
    if path.is_file():
        lock = json.loads(path.read_text(encoding="utf-8")).get("lock") or {}
        return lock.get("commit") in (FIXTURE_COMMIT, None)
    return False


def post_hoc(results: Path) -> bool:
    path = results / "deliverables.json"
    return bool(path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("postHoc"))


def write_numbers(out: Output, numbers: dict[str, str], *, is_post_hoc: bool) -> None:
    lines = [
        r"\providecommand{\dryrunmark}{}",
        r"\resultsloadedtrue",
        r"\dryruntrue" if out.dry_run else r"\dryrunfalse",
        r"\posthoctrue" if is_post_hoc else r"\posthocfalse",
    ]
    for name, text in sorted(numbers.items()):
        lines.append(rf"\newcommand{{\Res{name}}}{{{text}}}")
    out.write("numbers", "\n".join(lines))


def run(arguments: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--results", type=Path, required=True, help="the result folder of the bench analysis")
    parser.add_argument("--consequence", type=Path, help="the measured consequence folder")
    parser.add_argument("--grid", type=Path, help="the grid consequence folder")
    parser.add_argument("--out", type=Path, required=True, help="the folder for the LaTeX files")
    args = parser.parse_args(arguments)
    results: Path = args.results.resolve()
    if not (results / "agreement.csv").is_file():
        print(f"make_results: {results} holds no bench analysis output.", file=sys.stderr)
        return 1
    measured = (args.consequence or results / "consequence").resolve()
    grid = (args.grid or results / "consequence_grid").resolve()
    dry = is_dry_run(results) or any((f / MARKER).is_file() for f in (measured, grid))
    target: Path = args.out.resolve()
    if dry and (target == REPOSITORY or REPOSITORY in target.parents):
        print("make_results: a dry run must not be written inside the repository.", file=sys.stderr)
        return 1
    out = Output(target, source=str(results), dry_run=dry)
    numbers: dict[str, str] = {}
    completeness(results, out, numbers)
    sampling(results, out, numbers)
    agreement(results, out, numbers)
    bland_altman(results, out)
    correction(results, out, numbers)
    gate(results, out)
    if measured.is_dir():
        consequence_table(measured, out, "consequence", "measured", numbers)
        program_selection(measured, out)
    if grid.is_dir():
        consequence_table(grid, out, "consequence_grid", "grid", numbers)
        consequence_figure(grid, measured if measured.is_dir() else None, out)
    write_numbers(out, numbers, is_post_hoc=post_hoc(results))
    kind = "DRY RUN (synthetic test input)" if dry else "measured results"
    print(f"make_results: wrote {len(out.written)} files to {target} from {kind}: {', '.join(out.written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
