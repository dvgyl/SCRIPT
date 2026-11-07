"""Run the fixed consequence grid or the measured experiment from the repository root."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

from scriptkin import bench
from scriptkin.bench.inputs import read_registry
from scriptkin.bench.moves import MoveList, read_move_list
from scriptkin.consequence.inputs import centers_for, read_deliverables
from scriptkin.consequence.plan import ConsequencePlan, plan_digest
from scriptkin.consequence.report import file_digest, write_outputs


def load_move_lists(folder: Path = bench.PROGRAM_FOLDER) -> dict[str, MoveList]:
    generator: ModuleType | None = None
    result: dict[str, MoveList] = {}
    if any(p.startswith("._") for p in folder.parts):
        raise ValueError("Metadata paths are excluded.")
    for design in bench.MAIN:
        path = folder / f"{design.id}.json"
        if path.is_file():
            result[design.id] = read_move_list(path)
            continue
        if generator is None:
            source = Path(__file__).with_name("bench_programs.py")
            spec = importlib.util.spec_from_file_location("consequence_bench_programs", source)
            assert spec is not None and spec.loader is not None
            generator = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = generator
            spec.loader.exec_module(generator)
        _, steps = generator.program(design)
        result[design.id] = MoveList(
            design, generator.start_point(design), tuple(steps), generator.MOVE_S, generator.RAMP_S
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("grid", "measured"), required=True)
    parser.add_argument("--deliverables", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--expect-plan-sha256")
    parser.add_argument("--seed", type=int, default=20261107)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cell-id")
    parser.add_argument("--replicate", type=int)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.registry and args.mode != "measured":
        parser.error("--registry requires --mode measured.")
    plan = ConsequencePlan(seed=args.seed, source=args.mode)
    if args.workers < 1:
        parser.error("--workers must be positive.")
    centers = centers_for(load_move_lists())
    inputs = None
    if args.mode == "measured":
        if not args.deliverables or args.expect_plan_sha256 != plan_digest(plan):
            parser.error("Measured mode requires deliverables and the matching canonical plan SHA-256.")
        registry = read_registry(args.registry, args.registry.parent.parent) if args.registry else None
        inputs = read_deliverables(
            args.deliverables, centers, expected_protocol=bench.PROTOCOL_VERSION, registry=registry
        )
        if args.registry:
            inputs = replace(inputs, registry_sha256=file_digest(args.registry))
    print(f"plan_sha256: {plan_digest(plan)}", flush=True)
    write_outputs(
        plan,
        args.out,
        centers=centers,
        measured=inputs,
        cell_id=args.cell_id,
        replicate=args.replicate,
        smoke=args.smoke,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
