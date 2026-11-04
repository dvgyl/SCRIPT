from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import bench_camera_render as render
import numpy as np
import pytest

from scriptkin.bench.calibration import (
    calibrate_lens,
    calibrate_plane,
    to_plane,
    undistort,
)
from scriptkin.bench.camera import (
    Calibration,
    CameraFileError,
    Clock,
    Lens,
    Plane,
    Readout,
    Region,
    read_camera_file,
    sha256_file,
    write_camera_file,
)
from scriptkin.bench.track import FrameTimes
from scriptkin.bench.tracker import measure_marker_offset
from scriptkin.quality import Refusal, RefusalCode
from scriptkin.recording import FloatArray

pytestmark = pytest.mark.slow

PATTERN = (9, 6)


def _device_pixels(camera: render.Camera) -> tuple[FloatArray, FloatArray]:
    """Raw pixels on a 20-pixel grid whose plane points lie in the area that the tests track, with the plane
    point that each one sees."""
    xy = camera.plane_xy[::20, ::20]
    v, u = np.mgrid[0 : camera.size[1] : 20, 0 : camera.size[0] : 20]
    keep = (xy[..., 0] > -25) & (xy[..., 0] < 100) & (xy[..., 1] > -17) & (xy[..., 1] < 98)
    return np.column_stack((u[keep], v[keep])).astype(np.float64), xy[keep]


def test_lens_calibration_recovers_the_known_matrix_and_distortion() -> None:
    # Detects a lens calibration that misses the known focal length, centre, or distortion of the camera.
    camera = render.main_camera()
    lens = render.estimated_lens()
    assert lens.image_size == (1920, 1080)
    assert lens.views == 24
    assert lens.reprojection_rms_px < 0.1
    np.testing.assert_allclose(np.diag(lens.matrix)[:2], [1250.0, 1250.0], rtol=1e-3)
    np.testing.assert_allclose(lens.matrix[:2, 2], [959.5, 539.5], atol=1.0)
    raw, _ = _device_pixels(camera)
    ideal = undistort(raw, camera.lens())
    assert np.abs(undistort(raw, lens) - ideal).max() < 0.25
    without = Lens(lens.image_size, lens.matrix, np.zeros(5), 0.0, lens.views)
    assert np.abs(undistort(raw, without) - ideal).max() > 10


def test_plane_calibration_recovers_the_known_homography() -> None:
    # Detects a plane map that differs from the known camera pose in the tracked area.
    camera = render.main_camera()
    plane = render.estimated_plane()
    assert 0 < plane.residual_rms_mm <= 0.03
    raw, xy = _device_pixels(camera)
    assert np.abs(to_plane(raw, plane) - xy).max() < 0.02


def test_plane_calibration_refuses_a_warped_board() -> None:
    # Detects a plane calibration that accepts a residual above 0.03 mm RMS.
    camera = render.main_camera()
    plane = calibrate_plane(render.board(camera, warp_mm=0.15), camera.lens(), PATTERN, 10.0)
    assert isinstance(plane, Refusal)
    assert plane.code is RefusalCode.OUT_OF_RANGE
    assert "0.03 mm" in plane.detail


def test_plane_frame_follows_the_named_x_direction() -> None:
    # Detects a plane frame that takes its origin or axes from the detector order, not from device +x.
    turned = render.main_camera(turned=True)
    image = render.board(turned)
    raw, xy = _device_pixels(turned)
    left = calibrate_plane(image, turned.lens(), PATTERN, 10.0, "left")
    assert not isinstance(left, Refusal)
    assert np.abs(to_plane(raw, left) - xy).max() < 0.03
    right = calibrate_plane(image, turned.lens(), PATTERN, 10.0, "right")
    assert not isinstance(right, Refusal)
    np.testing.assert_allclose(to_plane(raw, right), (80.0, 50.0) - xy, atol=0.03)
    up = calibrate_plane(image, turned.lens(), PATTERN, 10.0, "up")
    assert isinstance(up, Refusal) and "45 degrees" in up.detail


def test_lens_calibration_refuses_a_view_without_the_board_and_too_few_views() -> None:
    # Detects a lens calibration that skips a failed view silently or runs on fewer than 20 views.
    views = render.lens_views(render.main_camera())[:20]
    blank = np.full_like(views[0], 128)
    refused = calibrate_lens([*views[:19], blank], PATTERN, 10.0)
    assert refused == Refusal(RefusalCode.UNDEFINED, "The board corners were not found in view 19.")
    too_few = calibrate_lens(views[:19], PATTERN, 10.0)
    assert isinstance(too_few, Refusal) and too_few.code is RefusalCode.TOO_FEW_VALUES


@pytest.mark.parametrize("background", ["ipad", "wacom"])
def test_marker_offset_is_the_marker_minus_the_tip(background: render.Background) -> None:
    # Detects a marker offset that is not the marker position relative to the tip on the plane origin.
    calibration = render.estimated_calibration(background)
    np.testing.assert_allclose(calibration.marker_offset_mm, render.MARKER_OFFSET_MM, atol=0.01)
    assert calibration.led == render.LED


def test_marker_offset_refuses_a_still_without_the_marker() -> None:
    # Detects a marker offset taken from a bright region that is not the marker.
    scene = render.Scene(render.main_camera(), "ipad")
    still = next(scene.frames(render.still((0.0, 0.0)), 1, missing=frozenset({0})))
    refused = measure_marker_offset(still, render.estimated_plane(), (-8.0, 0.0), render.LED)
    assert isinstance(refused, Refusal) and refused.code is RefusalCode.UNDEFINED


def _files() -> list[Lens | Plane | Calibration | Clock | Readout]:
    plane = render.main_camera().plane()
    return [
        plane.lens,
        plane,
        Calibration(plane, (-8.25, 0.5), Region(10, 20, 30, 40)),
        Clock(239.987, FrameTimes.PLAYBACK, 0.125, 59, 1.0, 240.0),
        Readout(3.5e-6, 1 / (3.5e-6 * 1000.0), 1000.0, 12),
    ]


def test_camera_files_round_trip_with_their_sources(tmp_path: Path) -> None:
    # Detects a camera file that loses a value, or that does not name its inputs by SHA-256.
    source = tmp_path / "input.png"
    source.write_bytes(b"a view")
    for value in _files():
        path = write_camera_file(value, tmp_path / f"{value.FORMAT}.json", [source])
        back = read_camera_file(type(value), path)
        assert json.dumps(back.to_json()) == json.dumps(value.to_json())
        raw = json.loads(path.read_text())
        assert (raw["format"], raw["version"]) == (value.FORMAT, 1)
        assert raw["sources"] == [{"name": "input.png", "sha256": sha256_file(source)}]


def test_camera_files_refuse_another_format_and_an_invalid_plane(tmp_path: Path) -> None:
    # Detects a reader that accepts a file of another step, or a plane above the residual limit.
    plane = render.main_camera().plane()
    path = write_camera_file(plane, tmp_path / "plane.json")
    with pytest.raises(CameraFileError, match="not a script-bench-lens file"):
        read_camera_file(Lens, path)
    raw = json.loads(path.read_text())
    raw["residual_rms_mm"] = 0.031
    path.write_text(json.dumps(raw))
    with pytest.raises(CameraFileError, match=r"0\.03 mm or less"):
        read_camera_file(Plane, path)
    with pytest.raises(CameraFileError, match="capture times exactly"):
        replace(Clock(239.987, FrameTimes.PLAYBACK, 0.125, 59, 1.0, 240.0), frame_times=FrameTimes.CAPTURE)
