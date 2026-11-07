"""Compare both capture tools with the synthetic movement of protocol section 2.4."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import CONTRACT, FIXTURES, fixture_lines, write_export
from jsonschema import Draft202012Validator, ValidationError

from scriptkin import bench
from scriptkin.battery import task_spec
from scriptkin.bench.features import CATALOG as BENCH_CATALOG
from scriptkin.bench.features import run_features
from scriptkin.contract import KINDS
from scriptkin.features import CATALOG, Cell, extract, feature_table
from scriptkin.formats.export import load_export, read_journal
from scriptkin.formats.journal import summarize
from scriptkin.quality import Accepted, Refusal, assess
from scriptkin.recording import (
    BenchRunContext,
    Channel,
    Device,
    EndReason,
    PenState,
    Recording,
    Stroke,
    TaskRef,
    Trial,
)

SCHEMA = Draft202012Validator(json.loads((CONTRACT / "journal.schema.json").read_bytes()))


def parity_export(tool: str) -> Path:
    required = f"SCRIPT_REQUIRE_{'APP' if tool == 'app' else 'BENCH'}_EXPORTS"
    missing = pytest.fail if os.environ.get(required) == "1" else pytest.skip
    configured = os.environ.get("SCRIPT_CONTRACT_OUT")
    if configured is None:
        missing("SCRIPT_CONTRACT_OUT is not set.")
    root = Path(configured) / tool / "parity"
    if not root.is_dir():
        missing(f"No parity export in {root}.")
    folders = [p.parent for p in root.glob("*/manifest.json") if not p.parent.name.startswith("._")]
    assert len(folders) == 1, f"Expected one parity export in {root}."
    return folders[0]


def equivalent(fixture: dict[str, Any], tool: str) -> Recording:
    base, times = fixture["base"], fixture["trial"]
    facts = fixture["tools"][tool]
    events, origin = facts["events"], facts["origin"]
    strokes = []
    for contact in (1, 0, 2):
        rows = [row for row in events if row["contact"] == contact]
        if tool == "bench":
            xy = np.array(
                [
                    [(r["absoluteX"] - origin[0]) * 0.005, -((29600 - r["absoluteY"]) - origin[1]) * 0.005]
                    for r in rows
                ]
            )
            names = {Channel.TILT_X: "tiltX", Channel.TILT_Y: "tiltY"}
            if contact:
                names[Channel.FORCE] = "pressure"
        else:
            scale = 25.4 * 2 / 264
            xy = np.array([[(r["x"] - origin[0]) * scale, -(r["y"] - origin[1]) * scale] for r in rows])
            names = {Channel(k): k for k in ("altitude", "azimuth", "roll")}
            names[Channel.FORCE if contact else Channel.HOVER] = "force" if contact else "z"
        np.testing.assert_allclose(xy, [r["mm"] for r in rows], rtol=0, atol=1e-12)
        strokes.append(
            Stroke(
                t=np.array([base + r["offset"] for r in rows]),
                xy=xy,
                state=PenState.CONTACT if contact else PenState.IN_AIR,
                channels={channel: np.array([r[key] for r in rows]) for channel, key in names.items()},
                contact=contact or None,
                max_force=1.0 if contact else None,
            )
        )
    channels = frozenset(Channel(name) for name in facts["device"]["channels"])
    device = Device(facts["device"]["model"], "pencil" if tool == "app" else "tablet", channels, channels)
    program = fixture["program"]
    spec = task_spec(bench.program(program).task)
    paths = tuple(np.asarray(path, dtype=float) * [1, -1] for path in spec["stimulus"]["paths"])
    trial = Trial(
        index=0,
        task=TaskRef(spec["id"], spec["mode"], paths),
        strokes=tuple(strokes),
        device=device,
        started=base + times["started"],
        began=base + times["began"],
        ended=base + times["ended"],
        end_reason=EndReason.OPERATOR_DONE,
        context=BenchRunContext(
            False,
            1,
            program,
            1,
            bench.trajectory_sha256(program),
            f"{'P' if tool == 'app' else 'W'}-{fixture['mounting']}-{program}-r1",
        ),
    )
    return Recording("synthetic-parity", None, device, (trial,), "synthetic")


def same_cells(actual: dict[str, Cell], expected: dict[str, Cell]) -> None:
    assert actual.keys() == expected.keys()
    for name, value in expected.items():
        got = actual[name]
        if isinstance(value, Refusal):
            assert got == value, name
        else:
            assert not isinstance(got, Refusal), name
            assert np.isfinite(value), name
            assert got == pytest.approx(value, rel=0, abs=1e-9), name


def validate_parity_schema(folder: Path) -> None:
    _, data = load_export(folder)
    for line in data.splitlines():
        SCHEMA.validate(json.loads(line))


def compare_parity_export(folder: Path, tool: str) -> None:
    _, data = load_export(folder)
    rows = [json.loads(line) for line in data.splitlines()]
    recording = read_journal(folder)
    swift = json.loads((folder.parent / f"{folder.name}.summary.json").read_bytes())
    assert summarize(data.splitlines(keepends=True)) == swift
    fixture = json.loads((FIXTURES / "parity" / "parity.json").read_bytes())
    base = json.loads((folder.parent / f"{folder.name}.clock.json").read_bytes())["base"]
    assert base == fixture["base"]
    assert rows[0]["bench"]["mounting"] == fixture["mounting"]
    hover = [row for row in rows if row["kind"] == "hover"]
    expected_clock = "callback" if tool == "app" else "event"
    assert hover and all(row["clock"] == expected_clock for row in hover)
    expected = equivalent(fixture, tool)
    started = next(row for row in rows if row["kind"] == "trialStarted")
    origin = fixture["tools"][tool]["origin"]
    assert started["stimulusOrigin"] == dict(x=origin[0], y=origin[1])
    assert started["spec"] == task_spec(bench.program(fixture["program"]).task)
    assert len(recording.trials) == 1
    trial, reference = recording.trials[0], expected.trials[0]
    assert trial.device == reference.device
    assert trial.context == reference.context
    assert (trial.outcome, trial.end_reason) == (reference.outcome, reference.end_reason)
    for actual_time, expected_time in (
        (trial.started, reference.started),
        (trial.began, reference.began),
        (trial.ended, reference.ended),
    ):
        assert actual_time == pytest.approx(expected_time, rel=0, abs=1e-12)
    assert len(trial.strokes) == len(reference.strokes)
    for got, want in zip(trial.strokes, reference.strokes, strict=True):
        assert (got.state, got.contact, got.max_force) == (want.state, want.contact, want.max_force)
        np.testing.assert_array_equal(got.t, want.t)
        np.testing.assert_allclose(got.xy, want.xy, rtol=0, atol=1e-12)
        assert got.channels.keys() == want.channels.keys()
        for channel in want.channels:
            np.testing.assert_allclose(got.channels[channel], want.channels[channel], rtol=0, atol=1e-12)
    actual_qc, expected_qc = assess(trial), assess(reference)
    assert isinstance(actual_qc, Accepted) and isinstance(expected_qc, Accepted)
    library = extract(actual_qc)
    assert library.keys() == {f.name for f in CATALOG}
    same_cells(library, extract(expected_qc))
    if tool == "app":
        for name in (
            "pressure.force_mean",
            "pressure.force_cv",
            "tilt.altitude_mean",
            "tilt.azimuth_variance",
            "tilt.roll_variance",
            "timing.in_air_s",
        ):
            assert not isinstance(library[name], Refusal), name
    # The independent recording has no bench session context, so feature_table accepts it.
    same_cells(library, feature_table([expected]).rows[0].cells)
    actual_bench, expected_bench = run_features(actual_qc).cells, run_features(expected_qc).cells
    assert actual_bench.keys() == {f.name for f in BENCH_CATALOG}
    same_cells(actual_bench, expected_bench)
    for name in ("bench.speed_cv", "bench.band_power_mm2_s2"):
        value = expected_bench[name]
        assert not isinstance(value, Refusal)
        assert value > 0.01


@pytest.mark.parametrize("tool", ["app", "bench"])
def test_capture_preserves_the_movement_and_every_feature(tool: str) -> None:
    # Detects a capture export that changes the synthetic movement or violates the journal schema.
    folder = parity_export(tool)
    validate_parity_schema(folder)
    compare_parity_export(folder, tool)


@pytest.mark.parametrize("tool", ["app", "bench"])
@pytest.mark.parametrize(
    "defect",
    [
        "x_offset",
        "y_flip",
        "scale",
        "time_offset",
        "time_scale",
        "drop",
        "duplicate",
        "join",
        "trial_ended",
        "spec_shift",
        "channel_shift",
        "schema",
        "hover_drop",
        "hover_channel_shift",
        "hover_clock",
        "mounting",
    ],
)
def test_parity_comparison_detects_capture_corruption(
    tool: str, defect: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a parity comparison that accepts the named capture defect.
    fixture = json.loads((FIXTURES / "parity" / "parity.json").read_bytes())
    base, times = fixture["base"], fixture["trial"]
    template = fixture_lines("bench_wacom" if tool == "bench" else "bench_ipad")
    header = template[0]
    header["bench"]["mounting"] = fixture["mounting"]
    if tool == "app":
        header["input"] = "pencil"
        header["device"]["model"] = "iPad17,2"
        header["channels"]["hover"] = True
    start = next(r for r in template if r["kind"] == "trialStarted")
    start.update(
        spec=task_spec(bench.program(fixture["program"]).task),
        stimulusOrigin=dict(zip(("x", "y"), fixture["tools"][tool]["origin"], strict=True)),
        t=base + times["started"],
    )
    start["trial"]["task"] = "bench-line"
    start["bench"] = dict(
        program=fixture["program"],
        run=1,
        trajectorySHA256=bench.trajectory_sha256(fixture["program"]),
        clip=f"{'P' if tool == 'app' else 'W'}-{fixture['mounting']}-{fixture['program']}-r1",
    )
    rows: list[dict[str, Any]] = [
        header,
        start,
        {"kind": "trialBegan", "trial": 0, "t": base + times["began"]},
    ]
    for event in fixture["tools"][tool]["events"]:
        contact, t = event["contact"], base + event["offset"]
        if tool == "bench":
            phase = {"down": "began", "dragged": "moved", "up": "ended", "moved": "moved"}[event["kind"]]
            x, y = event["absoluteX"], 29600 - event["absoluteY"]
            channels = {"tiltX": event["tiltX"], "tiltY": event["tiltY"]}
            if contact:
                channels["force"] = event["pressure"]
        else:
            phase, x, y = event["phase"], event["x"], event["y"]
            channels = {k: event[k] for k in ("altitude", "azimuth", "roll")}
            channels["force" if contact else "z"] = event["force" if contact else "z"]
        if phase == "began":
            rows.append(dict(kind="contactBegan", contact=contact, t=t, maxForce=1.0))
        if contact:
            rows.append(dict(kind="sample", contact=contact, t=t, x=x, y=y, phase=phase, est=0, **channels))
        else:
            rows.append(
                dict(kind="hover", t=t, x=x, y=y, clock="callback" if tool == "app" else "event", **channels)
            )
        if phase == "ended":
            rows.append(dict(kind="contactEnded", contact=contact, t=t, cancelled=False))
    rows.append(dict(kind="trialEnded", trial=0, t=base + times["ended"], reason="operatorDone"))
    counts = Counter(r["kind"] for r in rows)
    rows.append(
        dict(
            kind="sessionClosed",
            t=base + times["closed"],
            counts={k: counts[k] for k in KINDS if k != "sessionClosed"},
        )
    )
    for seq, row in enumerate(rows):
        row["seq"] = seq
    folder = tmp_path / tool / "parity" / "synthetic"
    data = write_export(folder, rows)
    (folder.parent / "synthetic.summary.json").write_text(
        json.dumps(summarize(data.splitlines(keepends=True)))
    )
    (folder.parent / "synthetic.clock.json").write_text(json.dumps({"base": base}))
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    compare_parity_export(folder, tool)
    if defect == "schema":
        validate_parity_schema(folder)
    samples = [row for row in rows if row["kind"] == "sample"]
    if defect in ("x_offset", "y_flip", "scale", "time_offset", "time_scale"):
        for row in samples:
            if defect == "x_offset":
                row["x"] += 1
            elif defect == "y_flip":
                row["y"] = -row["y"]
            elif defect == "scale":
                row["x"] *= 1.01
            elif defect == "time_offset":
                row["t"] += 0.01
            else:
                row["t"] = base + (row["t"] - base) * 1.01
    elif defect == "hover_drop":
        rows.remove(next(row for row in rows if row["kind"] == "hover"))
    elif defect == "hover_channel_shift":
        hover = [row for row in rows if row["kind"] == "hover"]
        channel = "z" if tool == "app" else "tiltX"
        values = [row[channel] for row in hover]
        for row, value in zip(hover, values[-1:] + values[:-1], strict=True):
            row[channel] = value
    elif defect == "hover_clock":
        next(row for row in rows if row["kind"] == "hover")["clock"] = (
            "event" if tool == "app" else "callback"
        )
    elif defect == "mounting":
        header["bench"]["mounting"] = "M1" if fixture["mounting"] != "M1" else "M2"
    elif defect == "trial_ended":
        next(row for row in rows if row["kind"] == "trialEnded")["t"] = base + 0.5
    elif defect == "spec_shift":
        for path in start["spec"]["stimulus"]["paths"]:
            for point in path:
                point[0] += 5
    elif defect == "channel_shift":
        for contact in (1, 2):
            stroke = [row for row in samples if row["contact"] == contact]
            values = [row["force"] for row in stroke]
            for row, value in zip(stroke, values[-1:] + values[:-1], strict=True):
                row["force"] = value
    elif defect == "schema":
        samples[10]["est"] = -1
    elif defect == "drop":
        rows.remove(samples[10])
    elif defect == "duplicate":
        rows.insert(rows.index(samples[10]), dict(samples[10]))
    else:
        rows = [
            r
            for r in rows
            if not (r["kind"] == "contactEnded" and r["contact"] == 1)
            and not (r["kind"] == "contactBegan" and r["contact"] == 2)
        ]
        for row in rows:
            if row.get("contact") == 2:
                row["contact"] = 1
    counts = Counter(r["kind"] for r in rows[:-1])
    rows[-1]["counts"] = {k: counts[k] for k in KINDS if k != "sessionClosed"}
    for seq, row in enumerate(rows):
        row["seq"] = seq
    data = write_export(folder, rows)
    if defect != "schema":
        (folder.parent / "synthetic.summary.json").write_text(
            json.dumps(summarize(data.splitlines(keepends=True)))
        )
    if defect == "schema":
        with pytest.raises(ValidationError):
            validate_parity_schema(folder)
    else:
        with pytest.raises(AssertionError):
            compare_parity_export(folder, tool)


@pytest.mark.parametrize("tool", ["app", "bench"])
@pytest.mark.parametrize("required", [False, True])
def test_missing_parity_export_obeys_the_require_flag(
    tool: str, required: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a missing parity export that skips after the corresponding capture check ran.
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    flag = f"SCRIPT_REQUIRE_{'APP' if tool == 'app' else 'BENCH'}_EXPORTS"
    monkeypatch.setenv(flag, "1" if required else "0")
    with pytest.raises(pytest.fail.Exception if required else pytest.skip.Exception):
        parity_export(tool)
