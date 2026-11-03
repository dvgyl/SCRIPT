"""Command line: `scriptkin contract`, `evaluate`, `plan-digest`, `plan-size`, `intake`, `qc-report`,
and `config`."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scriptkin import contract

if TYPE_CHECKING:
    from scriptkin.evaluate.plan import AnalysisPlan


def _contract_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "contract"


def _write(report: Mapping[str, Any], out: Path | None) -> None:
    text = json.dumps(report, indent=1, sort_keys=True, allow_nan=False) + "\n"
    if out is None:
        sys.stdout.write(text)
    else:
        out.write_text(text, encoding="utf-8")


def _sha256_hex(text: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", text):
        raise argparse.ArgumentTypeError("a SHA-256 digest is 64 hexadecimal digits")
    return text.lower()


def _read_plan(path: Path) -> AnalysisPlan:
    from scriptkin.evaluate.plan import AnalysisPlan

    return AnalysisPlan.from_json(json.loads(path.read_text(encoding="utf-8")))


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
    from scriptkin.evaluate.plan import plan_digest
    from scriptkin.evaluate.reliability import reliability
    from scriptkin.evaluate.report import Refused, refused_json
    from scriptkin.evaluate.screening import screening
    from scriptkin.features import read_feature_table
    from scriptkin.labels import read_labels_csv

    try:
        plan = _read_plan(args.plan)
        expected: str | None = args.expect_plan_digest
        if expected is not None and plan_digest(plan) != expected:
            raise ValueError(f"The plan digest is {plan_digest(plan)}, not the expected {expected}.")
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


def _plan_digest(args: argparse.Namespace) -> int:
    from scriptkin.evaluate.plan import plan_digest

    try:
        plan = _read_plan(args.plan)
    except (OSError, ValueError) as e:
        print(f"plan-digest: {e}", file=sys.stderr)
        return 2
    print(plan_digest(plan))
    return 0


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


def _config_new(args: argparse.Namespace) -> int:
    from scriptkin import studyconfig, studytools

    config = studytools.skeleton()
    text = studyconfig.config_bytes(config)
    if args.out is None:
        sys.stdout.write(text.decode())
    else:
        try:
            with args.out.open("xb") as file:
                file.write(text)
        except FileExistsError:
            print(f"config new: {args.out} exists. Give a new file.", file=sys.stderr)
            return 2
        except OSError as e:
            print(f"config new: {e}", file=sys.stderr)
            return 2
    print("Complete these items, then run `scriptkin config check`:", file=sys.stderr)
    for problem in studyconfig.validate(config):
        print(f"- {problem}", file=sys.stderr)
    return 0


def _config_keygen(args: argparse.Namespace) -> int:
    from scriptkin import studytools

    try:
        block = studytools.export_key_pair(args.out)
    except OSError as e:
        print(f"config keygen: {e}", file=sys.stderr)
        return 2
    _write(block, None)
    return 0


def _config_pin(args: argparse.Namespace) -> int:
    from scriptkin import studytools

    pin = getpass.getpass("New operator PIN: ")
    if pin != getpass.getpass("Repeat the PIN: "):
        print("config pin: the two PINs differ.", file=sys.stderr)
        return 2
    try:
        record = studytools.pin_record(pin)
    except ValueError as e:
        print(f"config pin: {e}", file=sys.stderr)
        return 2
    _write(record, args.out)
    return 0


def _config_check(args: argparse.Namespace) -> int:
    from scriptkin import studytools

    try:
        problems = studytools.check(args.file, _contract_dir() / "config.schema.json")
        digest = hashlib.sha256(args.file.read_bytes()).hexdigest()
    except OSError as e:
        print(f"config check: {e}", file=sys.stderr)
        return 2
    if problems:
        print(f"{args.file} has {len(problems)} problem(s):")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"{args.file} passes every check.\nSHA-256: {digest}")
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
    e.add_argument(
        "--expect-plan-digest",
        type=_sha256_hex,
        metavar="HEX",
        help="the registered plan digest; a plan with another digest is refused before the labels are read",
    )
    e.set_defaults(run=_evaluate)

    d = sub.add_parser("plan-digest", help="print the digest of an analysis plan, to register it")
    d.add_argument("plan", type=Path)
    d.set_defaults(run=_plan_digest)

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

    g = sub.add_parser("config", help="make and check a study configuration")
    actions = g.add_subparsers(dest="action", required=True)
    n = actions.add_parser("new", help="write a study configuration to complete")
    n.add_argument("--from", dest="source", choices=["demo"], required=True)
    n.add_argument("--out", type=Path, help="the new file (default: standard output)")
    n.set_defaults(run=_config_new)
    k = actions.add_parser("keygen", help="write the study export key pair and print the export member")
    k.add_argument(
        "--out",
        type=Path,
        required=True,
        help="the folder for study-export-key.bin (mode 600) and study-export-key.public.json",
    )
    k.set_defaults(run=_config_keygen)
    p = actions.add_parser("pin", help="ask for the operator PIN two times and print the operatorPin member")
    p.add_argument("--out", type=Path, help="the operatorPin member (default: standard output)")
    p.set_defaults(run=_config_pin)
    v = actions.add_parser("check", help="check a configuration against the schema and the rules")
    v.add_argument("file", type=Path)
    v.set_defaults(run=_config_check)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
