"""The bench programs: the registered motions of the bench protocol, written to contract/bench_programs.json.

A task is a geometry in `battery.json`. A program is a motion of that geometry at one speed, with or
without a fault. Both captures bundle the programs file. The decider checks each run against it.
"""

from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = "1.0"
# Provisional. The protocol is a draft. At its lock, this becomes the SHA-256 of docs/BENCH_PROTOCOL.md.
PROTOCOL_SHA256 = "0" * 64


@dataclass(frozen=True)
class Program:
    id: str
    task: str
    speed_mm_per_s: float
    fault: str | None = None


BENCH_PROGRAMS = (
    Program("loops-s1-v20", "bench-loops-l", 20.0),
    Program("loops-s1-v40", "bench-loops-l", 40.0),
    Program("loops-s05-v20", "bench-loops-s", 20.0),
    Program("loops-s05-v40", "bench-loops-s", 40.0),
    Program("spiral-s1-v20", "bench-spiral-l", 20.0),
    Program("spiral-s1-v40", "bench-spiral-l", 40.0),
    Program("spiral-s05-v20", "bench-spiral-s", 20.0),
    Program("spiral-s05-v40", "bench-spiral-s", 40.0),
    Program("fault-dwell", "bench-loops-l", 20.0, "dwell"),
    Program("fault-lift", "bench-loops-l", 20.0, "lift"),
    Program("fault-truncated", "bench-loops-l", 20.0, "truncated"),
)


def program(program_id: str) -> Program:
    for p in BENCH_PROGRAMS:
        if p.id == program_id:
            return p
    raise KeyError(program_id)


def trajectory_sha256(program_id: str) -> str:
    """The digest of the registered move list of one program."""
    # Provisional. Until the move lists exist, the digest is of a placeholder text that names the program.
    placeholder = f"SCRIPT bench placeholder trajectory {program(program_id).id}"
    return hashlib.sha256(placeholder.encode()).hexdigest()


def programs() -> dict[str, Any]:
    return {
        "protocol": {"version": PROTOCOL_VERSION, "sha256": PROTOCOL_SHA256},
        "programs": [
            {
                "id": p.id,
                "task": p.task,
                "speedMMPerS": p.speed_mm_per_s,
                "fault": p.fault,
                "trajectorySHA256": trajectory_sha256(p.id),
            }
            for p in BENCH_PROGRAMS
        ],
    }


@functools.cache
def programs_json() -> bytes:
    """The canonical bytes of contract/bench_programs.json."""
    return (json.dumps(programs(), indent=1) + "\n").encode()


@functools.cache
def programs_sha256() -> str:
    return hashlib.sha256(programs_json()).hexdigest()
