"""Command line: `scriptkin contract`, `evaluate`, `plan-digest`, `plan-size`, `intake`, `qc-report`,
`config`, and `bench`."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
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


FFPROBE_COMMAND = "ffprobe -v error -select_streams v:0 -show_frames -of json CLIP > CLIP_STEM.frames.json"
_FRAMES_HELP = f"The frames file must sit beside the clip. Make it with:\n  {FFPROBE_COMMAND}"


def _pattern(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", text)
    if not match or min(int(match[1]), int(match[2])) < 3:
        raise argparse.ArgumentTypeError("a board pattern is COLSxROWS inner corners, 3 or more each, as 9x6")
    return int(match[1]), int(match[2])


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("expected a finite number")
    return value


def _positive(text: str) -> float:
    value = _finite(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("expected a positive number")
    return value


def _bench_lens(args: argparse.Namespace) -> tuple[Any, list[Path]]:
    from scriptkin.bench.calibration import calibrate_lens, read_image

    return calibrate_lens((read_image(p) for p in args.views), args.pattern, args.pitch_mm), args.views


def _bench_plane(args: argparse.Namespace) -> tuple[Any, list[Path]]:
    from scriptkin.bench.calibration import calibrate_plane, read_image
    from scriptkin.bench.camerafiles import Lens, read_camera_file

    lens = read_camera_file(Lens, args.lens)
    plane = calibrate_plane(read_image(args.board), lens, args.pattern, args.pitch_mm, args.x_direction)
    return plane, [args.lens, args.board]


def _bench_marker_offset(args: argparse.Namespace) -> tuple[Any, list[Path]]:
    from scriptkin.bench.calibration import read_image
    from scriptkin.bench.camerafiles import Plane, Region, read_camera_file
    from scriptkin.bench.tracker import measure_marker_offset

    plane = read_camera_file(Plane, args.plane)
    calibration = measure_marker_offset(read_image(args.still), plane, tuple(args.near_mm), Region(*args.led))
    return calibration, [args.plane, args.still]


def _bench_clock(args: argparse.Namespace) -> tuple[Any, list[Path]]:
    from scriptkin.bench.camerafiles import Region, frames_path, read_frame_times
    from scriptkin.bench.clock import led_levels, measure_clock
    from scriptkin.bench.tracker import read_video

    frames = frames_path(args.clip)
    levels = led_levels(read_video(args.clip), Region(*args.led))
    clock = measure_clock(levels, read_frame_times(frames), led_hz=args.led_hz, nominal_hz=args.nominal_hz)
    return clock, [args.clip, frames]


def _bench_readout(args: argparse.Namespace) -> tuple[Any, list[Path]]:
    from scriptkin.bench.camerafiles import Region
    from scriptkin.bench.clock import measure_readout
    from scriptkin.bench.tracker import read_video

    readout = measure_readout(
        read_video(args.clip), Region(*args.region), led_hz=args.led_hz, nominal_hz=args.nominal_hz
    )
    return readout, [args.clip]


def _bench(args: argparse.Namespace) -> int:
    from scriptkin.bench.camerafiles import write_camera_file
    from scriptkin.formats import FormatError
    from scriptkin.quality import Refusal

    try:
        if args.action == "track":
            from scriptkin.bench.tracker import track_clip

            written = track_clip(
                args.clip,
                calibration=args.calibration,
                clock=args.clock,
                readout=args.readout,
                start_mm=tuple(args.start_mm),
                out_dir=args.out_dir,
            )
        else:
            value, sources = args.step(args)
            if isinstance(value, Refusal):
                print(f"bench {args.action}: refused. {value.detail}", file=sys.stderr)
                return 1
            written = (write_camera_file(value, args.out, sources),)
    except (OSError, FormatError, ValueError) as e:
        print(f"bench {args.action}: {e}", file=sys.stderr)
        return 2
    for path in written:
        print(f"bench {args.action}: wrote {path}")
    return 0


def _bench_analyse(args: argparse.Namespace) -> int:
    """`scriptkin bench analyse`: the analysis of sections 8 to 12 into bench/results/ (section 15)."""
    from scriptkin.bench.analysis import analyse_all
    from scriptkin.bench.outputs import write_results
    from scriptkin.bench.study import evaluate_study, incomplete, read_study
    from scriptkin.formats import FormatError

    try:
        study = read_study(args.root, post_hoc=args.post_hoc)
        problems = incomplete(study)
        if problems:
            print("bench analyse: refused. The study is not complete (section 13):", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        evaluated = evaluate_study(study)
        written = write_results(evaluated, analyse_all(evaluated.runs, study), args.results or study.results)
    except (OSError, FormatError, ValueError) as e:
        print(f"bench analyse: {e}", file=sys.stderr)
        return 2
    for path in written:
        print(f"bench analyse: wrote {path}")
    if study.post_hoc:
        print("bench analyse: post hoc (section 13). These locked code files changed after the lock:")
        for path in study.changed_code:
            print(f"  {path}")
    return 0


def _bench_interim(args: argparse.Namespace) -> int:
    """`scriptkin bench interim`: section 6.6 on blocks 1 to 6. Prints variance components only."""
    import datetime

    from scriptkin.bench.interim import INTERIM_BLOCKS, INTERIM_COLUMNS, deviation_row, interim, interim_text
    from scriptkin.bench.outputs import write_csv
    from scriptkin.bench.study import evaluate_study, incomplete, read_study
    from scriptkin.formats import FormatError

    try:
        study = read_study(args.root)
        problems = incomplete(study, INTERIM_BLOCKS)
        if problems:
            print("bench interim: refused. Blocks 1 to 6 are not complete:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        rows = tuple(r for r in study.rows if r.block in INTERIM_BLOCKS)
        result = interim(evaluate_study(study, rows))
        study.results.mkdir(parents=True, exist_ok=True)
        path = write_csv(study.results / "interim.csv", INTERIM_COLUMNS, (r.row() for r in result))
    except (OSError, FormatError, ValueError) as e:
        print(f"bench interim: {e}", file=sys.stderr)
        return 2
    print(interim_text(result))
    print(f"bench interim: wrote {path}")
    when = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    print("Add this row to bench/DEVIATIONS.md:")
    print(deviation_row(getpass.getuser(), when))
    return 0


def _bench_pilot(args: argparse.Namespace) -> int:
    """`scriptkin bench pilot`: section 6.5, each device against its own command, before the lock."""
    from scriptkin.bench.interim import PILOT_COLUMNS, pilot, pilot_text
    from scriptkin.bench.outputs import write_csv
    from scriptkin.bench.study import evaluate_study, read_study
    from scriptkin.formats import FormatError

    try:
        study = read_study(args.root, locked=False)
        rows = pilot(evaluate_study(study))
        study.results.mkdir(parents=True, exist_ok=True)
        path = write_csv(study.results / "pilot.csv", PILOT_COLUMNS, rows)
    except (OSError, FormatError, ValueError) as e:
        print(f"bench pilot: {e}", file=sys.stderr)
        return 2
    print(pilot_text(rows))
    print(f"bench pilot: wrote {path}")
    return 0


def _bench_lock(args: argparse.Namespace) -> int:
    """`scriptkin bench lock`: section 14 step 5, the lock file of the files and the two builds."""
    from scriptkin.bench.inputs import Build, lock_json, write_lock
    from scriptkin.formats import FormatError

    builds = {role: Build(build, revision) for role, build, revision in args.build}
    try:
        lock = write_lock(args.root, builds, args.commit, args.file)
        args.out.write_bytes(lock_json(lock))
    except (OSError, FormatError, ValueError) as e:
        print(f"bench lock: {e}", file=sys.stderr)
        return 2
    print(f"bench lock: wrote {args.out} with {len(lock.files)} file digests")
    return 0


def _bench_start_point(args: argparse.Namespace) -> int:
    """`scriptkin bench start-point`: the commanded start of a run in plane millimetres."""
    from scriptkin.bench.study import read_study, start_point_mm
    from scriptkin.formats import FormatError

    try:
        x, y = start_point_mm(read_study(args.root, locked=False), args.run_id)
    except (OSError, FormatError, ValueError) as e:
        print(f"bench start-point: {e}", file=sys.stderr)
        return 2
    print(f"{x:.4f} {y:.4f}")
    return 0


def _add_bench_analysis(steps: Any) -> None:
    root_help = "the repository root that holds bench/"
    analyse = steps.add_parser("analyse", help="run the bench analysis and write bench/results/")
    analyse.add_argument("root", type=Path, help=root_help)
    analyse.add_argument("--results", type=Path, help="the output folder (default: bench/results)")
    analyse.add_argument(
        "--post-hoc",
        action="store_true",
        help="run with analysis code that changed after the lock (section 13); every output says so",
    )
    analyse.set_defaults(run=_bench_analyse)
    interim = steps.add_parser("interim", help="print the variance components of blocks 1 to 6, section 6.6")
    interim.add_argument("root", type=Path, help=root_help)
    interim.set_defaults(run=_bench_interim)
    pilot = steps.add_parser("pilot", help="check each device against its own command, section 6.5")
    pilot.add_argument("root", type=Path, help=root_help)
    pilot.set_defaults(run=_bench_pilot)
    lock = steps.add_parser("lock", help="write the lock file of section 14 step 5")
    lock.add_argument("root", type=Path, help=root_help)
    lock.add_argument("--commit", type=str, required=True, help="the full hash of the commit to lock")
    lock.add_argument(
        "--build",
        nargs=3,
        action="append",
        required=True,
        metavar=("ROLE", "BUILD", "REVISION"),
        help="the app.build and app.sourceRevision of the capture tool of device P or W; give both",
    )
    lock.add_argument(
        "--file",
        type=Path,
        action="append",
        default=[],
        help="an extra file to lock beyond the files of section 14 step 5",
    )
    lock.add_argument("--out", type=Path, required=True, help="the lock file to write")
    lock.set_defaults(run=_bench_lock)
    start = steps.add_parser("start-point", help="print the commanded start point of a run in plane mm")
    start.add_argument("root", type=Path, help=root_help)
    start.add_argument("run_id", help="the run id, as P-M3-loops-s-v40-r2")
    start.set_defaults(run=_bench_start_point)


def _add_bench(sub: Any) -> None:
    b = sub.add_parser("bench", help="the camera steps and the analysis of the bench protocol")
    steps = b.add_subparsers(dest="action", required=True)
    _add_bench_analysis(steps)

    def step(name: str, help: str, run: Any = None, frames: bool = False) -> argparse.ArgumentParser:
        p = steps.add_parser(
            name,
            help=help,
            description=f"{help[0].upper()}{help[1:]}.",
            epilog=_FRAMES_HELP if frames else None,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        p.set_defaults(run=_bench, step=run)
        if name != "track":
            p.add_argument("--out", type=Path, required=True, help="the file to write")
        return p

    lens = step("lens", "calibrate the lens from 20 or more views of the board", _bench_lens)
    lens.add_argument("views", type=Path, nargs="+", help="still images in the video mode of the clips")
    plane = step(
        "plane", "calibrate the marker plane of one mounting from one view of the board", _bench_plane
    )
    plane.add_argument("board", type=Path, help="a still image of the board on the spacer")
    plane.add_argument("--lens", type=Path, required=True, help="the lens file")
    plane.add_argument(
        "--x-direction",
        choices=["right", "left", "up", "down"],
        default="right",
        help="the image direction of device +x, within 45 degrees (default: right)",
    )
    for p in (lens, plane):
        p.add_argument("--pattern", type=_pattern, required=True, help="inner corners along x and y, as 9x6")
        p.add_argument("--pitch-mm", type=_positive, required=True, help="the measured pitch of the board")
    offset = step(
        "marker-offset",
        "measure the marker offset of one mounting and write its calibration",
        _bench_marker_offset,
    )
    offset.add_argument("still", type=Path, help="a still image with the stylus tip on the marked grid point")
    offset.add_argument("--plane", type=Path, required=True, help="the plane file of the mounting")
    offset.add_argument(
        "--near-mm",
        nargs=2,
        type=_finite,
        required=True,
        metavar=("X", "Y"),
        help="the marker position within 3.5 mm, in plane millimetres",
    )
    offset.add_argument(
        "--led",
        nargs=4,
        type=int,
        required=True,
        metavar=("LEFT", "TOP", "WIDTH", "HEIGHT"),
        help="the pixel rectangle of the clock LED",
    )
    clock = step(
        "clock", "measure the frame clock from a 60 s clip of the 1 Hz LED", _bench_clock, frames=True
    )
    clock.add_argument("clip", type=Path)
    clock.add_argument(
        "--led",
        nargs=4,
        type=int,
        required=True,
        metavar=("LEFT", "TOP", "WIDTH", "HEIGHT"),
        help="the pixel rectangle of the clock LED",
    )
    clock.add_argument("--led-hz", type=_positive, default=1.0)
    clock.add_argument("--nominal-hz", type=_positive, default=240.0)
    readout = step("readout", "measure the row time from a clip of the 1 kHz LED", _bench_readout)
    readout.add_argument("clip", type=Path, help="a clip recorded with an exposure of 1/4000 s or shorter")
    readout.add_argument(
        "--region",
        nargs=4,
        type=int,
        required=True,
        metavar=("LEFT", "TOP", "WIDTH", "HEIGHT"),
        help="the pixel rectangle where the bands show",
    )
    readout.add_argument("--led-hz", type=_positive, default=1000.0)
    readout.add_argument("--nominal-hz", type=_positive, default=240.0)
    track = step("track", "track the marker in one clip and write its track files", frames=True)
    track.add_argument("clip", type=Path, help="the clip, named <run id> with its suffix")
    track.add_argument("--calibration", type=Path, required=True, help="the calibration file of the mounting")
    track.add_argument("--clock", type=Path, required=True, help="the clock file")
    shutter = track.add_mutually_exclusive_group(required=True)
    shutter.add_argument("--readout", type=Path, help="the readout file of a rolling shutter")
    shutter.add_argument("--global-shutter", action="store_true", help="the camera has a global shutter")
    track.add_argument(
        "--start-mm",
        nargs=2,
        type=_finite,
        required=True,
        metavar=("X", "Y"),
        help="the commanded start point of the stylus tip, in plane millimetres",
    )
    track.add_argument(
        "--out-dir", type=Path, help="the folder for the track files (default: beside the clip)"
    )


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

    _add_bench(sub)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
