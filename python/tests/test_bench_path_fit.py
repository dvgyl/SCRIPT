from __future__ import annotations

import numpy as np
import test_bench_moves as moves_tests

from scriptkin.bench.camera import nearest_on_path
from scriptkin.bench.moves import MoveList
from scriptkin.recording import FloatArray

programs_folder = moves_tests.programs_folder
move_lists = moves_tests.move_lists


def brute_force_nearest(points: FloatArray, vertices: FloatArray) -> FloatArray:
    """The nearest point on every edge, then the nearest of those, with no candidate selection."""
    a, d = vertices[:-1], np.diff(vertices, axis=0)
    lengths2 = np.where(np.sum(d**2, axis=1) > 0, np.sum(d**2, axis=1), 1.0)
    offsets = points[:, None, :] - a[None, :, :]
    fractions = np.clip(np.sum(offsets * d[None, :, :], axis=2) / lengths2[None, :], 0, 1)
    projected = a[None, :, :] + d[None, :, :] * fractions[:, :, None]
    squared = np.sum((points[:, None, :] - projected) ** 2, axis=2)
    return projected[np.arange(len(points)), np.argmin(squared, axis=1)]


def test_the_nearest_point_on_the_path_equals_the_brute_force_answer(move_lists: dict[str, MoveList]) -> None:
    # Detects a candidate edge that the ball misses, a projection outside its edge, a minimum taken
    # over the wrong point, or a point far from the path that gets no answer.
    rng = np.random.default_rng(11)
    for pid in ("loops-l-v20", "spiral-s-v40", "line-osc8", "fault-lift"):
        vertices = move_lists[pid].path().xy * [1.0, -1.0]
        on_path = move_lists[pid].path().position(rng.uniform(0, move_lists[pid].duration_s, 300)) * [
            1.0,
            -1.0,
        ]
        points = np.concatenate(
            (
                on_path + rng.normal(0, 0.3, on_path.shape),
                on_path * 1.06 + rng.normal(0, 0.01, on_path.shape),
                vertices[rng.integers(0, len(vertices), 50)],
                rng.uniform(-30, 160, (20, 2)),
            )
        )
        expected = brute_force_nearest(points, vertices)
        found = nearest_on_path(points, vertices)
        distance = np.linalg.norm(points - found, axis=1)
        assert np.allclose(np.linalg.norm(points - expected, axis=1), distance, rtol=0, atol=1e-9), pid
        assert np.allclose(found, expected, atol=1e-9), pid
