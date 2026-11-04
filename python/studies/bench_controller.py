"""Write the NextDraw 8511 controller file of each bench move list (protocol section 4.1).

Run it from the repository root after `bench_programs.py --write bench/programs`:

    UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \\
        python -I python/studies/bench_controller.py bench/programs

For each move list `<program>.json` in the folder, it writes the controller file `<program>.ebb` and the
record `<program>.ebb.csv` with `scriptkin.bench.controller`, which defines the translation and the
machine settings. The SHA-256 of the controller file is the trajectory digest of the program (section
2.4). `scriptkin contract regenerate` then writes it into `contract/bench_programs.json`. The script
writes nothing when it refuses a program, and it exits with 1.

The record holds one row per point: the start point, then the end point of each move. A row gives the
end time and the position of the move list, the end time and the motor positions of the controller file,
the quantised position in millimetres, and its distance from the move list. The script prints the
largest distance and the largest end-time difference of each program, their bounds, and the settings.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path

from scriptkin.bench.controller import (
    END_TIME_BOUND_MS,
    NEXTDRAW_8511,
    ControllerError,
    position_mm,
    translate,
    write_translation,
)
from scriptkin.bench.moves import MoveListError, read_move_lists


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the NextDraw 8511 controller file of each move list.")
    parser.add_argument("folder", type=Path, help="the folder of the move lists, bench/programs at the lock")
    args = parser.parse_args()
    machine = NEXTDRAW_8511
    try:
        move_lists = read_move_lists(args.folder)
        if not move_lists:
            raise ControllerError(f"{args.folder} holds no move list.")
        translations = {pid: translate(ml, machine) for pid, ml in move_lists.items()}
    except (MoveListError, ControllerError) as e:
        sys.exit(f"bench_controller: {e}")
    header = ("program", "moves", "commands", "duration ms", "end-point error mm", "end-time error ms")
    print("\t".join((*header, "peak steps/s", "SHA-256")))
    low, high = [math.inf, math.inf], [-math.inf, -math.inf]
    for pid, translation in translations.items():
        move_list = move_lists[pid]
        controller = write_translation(args.folder, move_list, translation, machine)
        path = move_list.path()
        quantised = [position_mm(p.motor, machine) for p in translation.points]
        errors = [math.dist(q, (float(x), float(y))) for q, (x, y) in zip(quantised, path.xy, strict=True)]
        lags = [abs(p.end_ms - 1000 * float(t)) for p, t in zip(translation.points, path.t, strict=True)]
        rates = [
            max(abs(b.motor[k] - a.motor[k]) for k in range(2)) * 1000 / (b.end_ms - a.end_ms)
            for a, b in zip(translation.points, translation.points[1:], strict=False)
            if b.end_ms > a.end_ms
        ]
        for k in range(2):
            low[k] = min(low[k], *(q[k] for q in quantised))
            high[k] = max(high[k], *(q[k] for q in quantised))
        digest = hashlib.sha256(controller.read_bytes()).hexdigest()
        print(
            f"{pid}\t{len(move_list.moves)}\t{len(translation.commands)}\t{translation.points[-1].end_ms}"
            f"\t{max(errors):.5f}\t{max(lags):.3f}\t{max(rates):.0f}\t{digest}"
        )
    print(
        f"\nbounds: end point {machine.end_point_bound_mm:g} mm, end time {END_TIME_BOUND_MS:g} ms,"
        f" step rate {machine.max_step_rate} steps/s per motor"
    )
    (travel_x, travel_y), (origin_x, origin_y) = machine.travel_mm, machine.origin_mm
    print(
        f"origin ({origin_x:g}, {origin_y:g}) mm in the travel of {travel_x:g} x {travel_y:g} mm."
        f" Every program fits for an origin from ({-low[0]:.2f}, {-low[1]:.2f})"
        f" to ({travel_x - high[0]:.2f}, {travel_y - high[1]:.2f}) mm."
    )
    speed = machine.servo_rate / abs(machine.servo_per_mm) / machine.servo_cycle_ms * 1000
    print(
        f"servo: pin RP{machine.servo_pin}, cycle {machine.servo_cycle_ms} ms, rate {machine.servo_rate}"
        f" ({speed:.2f} mm/s), surface {machine.servo_surface}, lift {machine.lift_mm:g} mm at"
        f" {machine.pulse(machine.lift_mm)} in {machine.slew_ms(machine.lift_mm)} ms"
    )
    # A motor turns 200 full steps for 40 mm of belt, so the microsteps are the steps per mm times 40 / 200.
    print(
        f"EBB state for every run: {machine.steps_per_mm * 40 // 200} microsteps, set before homing,"
        f" SC,8,{machine.servo_channels}, and servo power for the whole run (SR)"
    )
    print(f"wrote {len(translations)} controller files to {args.folder}")


if __name__ == "__main__":
    main()
