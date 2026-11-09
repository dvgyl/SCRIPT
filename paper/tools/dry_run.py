"""Write a synthetic bench study and analyse it, to test the paper results pipeline only.

The output is NOT data. The script uses the synthetic study of the analysis tests: simulated devices
with a known scale error on four programs. It writes the marker file `DRY_RUN_SYNTHETIC` into the
results folder. `make_results.py` then marks every table and figure, and the submission build refuses
the folder.

Run from the repository root in the `./check` environment:

    UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \
        python paper/tools/dry_run.py --out <folder outside the repository>

With `--consequence`, the script also runs the consequence experiment in smoke mode on the synthetic
deliverables (two replicates of the signal cells).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKER = "DRY_RUN_SYNTHETIC"
MARKER_TEXT = (
    "Synthetic test input for the paper results pipeline. Not a measurement. "
    "Never use these files in a manuscript.\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--consequence", action="store_true")
    args = parser.parse_args()
    out: Path = args.out.resolve()
    if ROOT in out.parents or out == ROOT:
        parser.error("The dry-run folder must be outside the repository.")
    if out.exists():
        shutil.rmtree(out)
    study = out / "study"
    programs = out / "programs"
    study.mkdir(parents=True)
    programs.mkdir()

    sys.path.insert(0, str(ROOT / "python" / "tests"))
    from bench_study import BUILD, COMMIT, write_study  # type: ignore[import-not-found]

    from scriptkin.bench.moves import read_move_lists
    from scriptkin.cli import main as scriptkin

    subprocess.run(
        [sys.executable, "-I", str(ROOT / "python/studies/bench_programs.py"), "--write", str(programs)],
        check=True,
        capture_output=True,
    )
    write_study(study, programs, read_move_lists(programs))
    lock = [
        "bench", "lock", str(study), "--commit", COMMIT,
        "--build", "P", BUILD.build, BUILD.source_revision,
        "--build", "W", BUILD.build, BUILD.source_revision,
        "--out", str(study / "bench" / "BENCH_LOCK.json"),
    ]  # fmt: skip
    if scriptkin(lock) != 0 or scriptkin(["bench", "analyse", str(study)]) != 0:
        return 1
    results = study / "bench" / "results"
    (results / MARKER).write_text(MARKER_TEXT, encoding="utf-8")

    if args.consequence:
        script = ROOT / "python/studies/consequence.py"
        from scriptkin.consequence.plan import ConsequencePlan, plan_digest

        digest = plan_digest(ConsequencePlan(source="measured"))
        target = results / "consequence"
        subprocess.run(
            [
                sys.executable, "-I", str(script), "--mode", "measured", "--smoke",
                "--deliverables", str(results / "deliverables.json"),
                "--registry", str(study / "bench" / "registry.json"),
                "--expect-plan-sha256", digest, "--out", str(target),
            ],  # fmt: skip
            check=True,
        )
        (target / MARKER).write_text(MARKER_TEXT, encoding="utf-8")
    print(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
