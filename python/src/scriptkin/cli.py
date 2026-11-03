"""Command line: `scriptkin contract`, `evaluate`, `plan-size`, `intake`, and `qc-report`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scriptkin import contract


def _contract_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "contract"


def _write(report: dict[str, Any], out: Path | None) -> None:
    text = json.dumps(report, indent=1, sort_keys=True, allow_nan=False) + "\n"
    if out is None:
        sys.stdout.write(text)
    else:
        out.write_text(text, encoding="utf-8")


def _contract(args: argparse.Namespace) -> int:
    directory: Path = args.dir or _contract_dir()
    if args.action == "regenerate":
        written = contract.regenerate(directory)
        print(f"contract: {len(written)} file(s) written")
        return 0
    stale = contract.verify(directory)
    if stale:
        print(
            "contract: generated files differ from the generator. Run `scriptkin contract regenerate`:",
            file=sys.stderr,
        )
        for rel in stale:
            print(f"  {rel}", file=sys.stderr)
        return 1
    print("contract: up to date")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    from scriptkin.evaluate.plan import AnalysisPlan
    from scriptkin.evaluate.reliability import reliability
    from scriptkin.evaluate.report import Refused, refused_json
    from scriptkin.evaluate.screening import screening
    from scriptkin.features import read_feature_table
    from scriptkin.labels import read_labels_csv

    try:
        plan = AnalysisPlan.from_json(json.loads(Path(args.plan).read_text(encoding="utf-8")))
        table = read_feature_table(args.features)
        labels = read_labels_csv(args.labels)
        agreement = None
        if args.reliability is not None:
            if plan.gate is None:
                raise ValueError("--reliability needs a plan with a gate.")
            agreement = reliability(read_feature_table(args.reliability), plan.gate.between)
    except (OSError, ValueError) as e:
        print(f"evaluate: {e}", file=sys.stderr)
        return 2
    result = screening(table, labels, plan=plan, seed=args.seed, reliability=agreement)
    _write(refused_json(result) if isinstance(result, Refused) else result.to_json(), args.out)
    return 1 if isinstance(result, Refused) else 0


def _plan_size(args: argparse.Namespace) -> int:
    from scriptkin.evaluate.planning import Assumptions, precision, smallest_enrolment

    try:
        assumptions = Assumptions(
            args.prevalence, args.sensitivity, args.specificity, args.attrition, args.confidence
        )
        report: dict[str, Any] = {"kind": "planSize", "assumptions": asdict(assumptions)}
        if args.enrolled is not None:
            report["precision"] = asdict(precision(assumptions, args.enrolled))
        if args.half_width is not None:
            report["smallestEnrolment"] = smallest_enrolment(assumptions, args.half_width)
    except ValueError as e:
        print(f"plan-size: {e}", file=sys.stderr)
        return 2
    _write(report, args.out)
    return 0


def _key(path: Path | None) -> bytes | None:
    return None if path is None else path.read_bytes()


def _intake(args: argparse.Namespace) -> int:
    from scriptkin.intake import intake

    try:
        report = intake(
            args.sources, args.store, private_key=_key(args.key), keep_plaintext=args.keep_plaintext
        )
    except (OSError, ValueError) as e:
        print(f"intake: {e}", file=sys.stderr)
        return 2
    _write(report.to_json(), args.out)
    settled = all(r.result in ("accepted", "duplicate") for r in report.receipts)
    return 0 if settled and not report.refused else 1


def _qc_report(args: argparse.Namespace) -> int:
    from scriptkin.qcreport import qc_markdown, qc_report

    try:
        report = qc_report(args.store, private_key=_key(args.key))
    except (OSError, ValueError) as e:
        print(f"qc-report: {e}", file=sys.stderr)
        return 2
    _write(report, args.out)
    if args.markdown is not None:
        args.markdown.write_text(qc_markdown(report), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scriptkin")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("contract", help="regenerate or verify the generated contract files")
    c.add_argument("action", choices=["regenerate", "verify"])
    c.add_argument("--dir", type=Path, default=None, help="contract directory (default: the repo contract/)")
    c.set_defaults(run=_contract)

    e = sub.add_parser("evaluate", help="run a screening analysis plan and write a JSON report")
    e.add_argument("--plan", type=Path, required=True)
    e.add_argument("--features", type=Path, required=True, help="a feature table CSV with its provenance")
    e.add_argument("--labels", type=Path, required=True, help="a label CSV")
    e.add_argument("--reliability", type=Path, help="a feature table CSV of repeated devices or sessions")
    e.add_argument("--seed", type=int, default=0)
    e.add_argument("--out", type=Path)
    e.set_defaults(run=_evaluate)

    s = sub.add_parser("plan-size", help="expected precision or smallest enrolment for a screening study")
    s.add_argument("--prevalence", type=float, required=True)
    s.add_argument("--sensitivity", type=float, required=True)
    s.add_argument("--specificity", type=float, required=True)
    s.add_argument("--attrition", type=float, default=0.0, help="share of enrolled participants lost to QC")
    s.add_argument("--confidence", type=float, default=0.95)
    s.add_argument("--enrolled", type=int, help="report precision for this enrolment")
    s.add_argument("--half-width", type=float, help="report the smallest enrolment for this half-width")
    s.add_argument("--out", type=Path)
    s.set_defaults(run=_plan_size)

    i = sub.add_parser("intake", help="verify exports and bring each session into a study store once")
    i.add_argument("sources", type=Path, nargs="+", help="folders that hold export folders")
    i.add_argument("--store", type=Path, required=True)
    i.add_argument("--key", type=Path, help="the study private key file: 32 raw X25519 bytes")
    i.add_argument(
        "--keep-plaintext",
        action="store_true",
        help="also keep the decrypted journal of each encrypted export",
    )
    i.add_argument("--out", type=Path, help="the intake report (default: standard output)")
    i.set_defaults(run=_intake)

    q = sub.add_parser("qc-report", help="count sessions, trials, rates, and channels of a study store")
    q.add_argument("store", type=Path)
    q.add_argument("--key", type=Path, help="the study private key file, for encrypted sessions")
    q.add_argument("--out", type=Path, help="the JSON report (default: standard output)")
    q.add_argument("--markdown", type=Path, help="also write the short Markdown table here")
    q.set_defaults(run=_qc_report)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
