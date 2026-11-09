"""Write the data files of the design figure from the move lists of the movement programs.

The files show the commanded movement only. They hold no measurement.

    python3 paper/tools/make_design.py --programs <folder with the move lists> --out paper/figures

The study script of the bench protocol writes the move lists.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

PATHS = {"loops-l-v20": "path_loops", "spiral-l-v20": "path_spiral"}
SPEEDS = {"loops-l-v20": "speed_loops", "spiral-l-v20": "speed_spiral", "line-osc8": "speed_line"}
SPEED_WINDOW_S = 4.0
HEAD = "# Commanded movement of one program, from its move list. Design data, not a measurement.\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--programs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for program in SPEEDS:
        raw = json.loads((args.programs / f"{program}.json").read_text(encoding="utf-8"))
        points = [(raw["start"]["x_mm"], raw["start"]["y_mm"])]
        times = [0.0]
        for move in raw["moves"]:
            points.append((move["x_mm"], move["y_mm"]))
            times.append(times[-1] + move["duration_s"])
        step = max(1, len(points) // 450)
        kept = points[::step] + ([points[-1]] if (len(points) - 1) % step else [])
        lines = [HEAD, "x y\n"] + [f"{x:.3f} {y:.3f}\n" for x, y in kept]
        if program in PATHS:
            (args.out / f"{PATHS[program]}.dat").write_text("".join(lines), encoding="utf-8")
        speed = [HEAD, "t v\n"]
        for k in range(1, len(points)):
            duration = times[k] - times[k - 1]
            middle = 0.5 * (times[k] + times[k - 1])
            if duration <= 0 or middle > SPEED_WINDOW_S:
                continue
            distance = math.dist(points[k], points[k - 1])
            speed.append(f"{middle:.4f} {distance / duration:.3f}\n")
        (args.out / f"{SPEEDS[program]}.dat").write_text("".join(speed), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
