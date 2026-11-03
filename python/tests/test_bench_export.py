"""The exports that the ScriptBench Mac tests leave in `$SCRIPT_CONTRACT_OUT/bench/`.

`./check bench` runs the Mac tests. They drive the bench store with tablet events and publish each session
into this folder, with the Swift fold summary beside it. These tests read each export as an analyst does.
They skip when the folder is absent, unless `SCRIPT_REQUIRE_BENCH_EXPORTS=1`: `./check` sets it when the
`bench` section ran in the same invocation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import CONTRACT, FIXTURES, write_export
from conftest import fixture_lines as lines
from jsonschema import Draft202012Validator

from scriptkin import Channel, PenState, Recording, Refusal, bench
from scriptkin.formats.export import load_export, read_journal
from scriptkin.formats.journal import summarize
from scriptkin.qualification import Intervals, qualify
from scriptkin.recording import BenchContext, BenchRunContext, TabletScale

SCHEMA = Draft202012Validator(json.loads((CONTRACT / "journal.schema.json").read_bytes()))
TABLET_SCALE = TabletScale(200.0, 0.005)


def bench_exports() -> list[Path]:
    """The export folders in `$SCRIPT_CONTRACT_OUT/bench/`."""
    missing = pytest.fail if os.environ.get("SCRIPT_REQUIRE_BENCH_EXPORTS") == "1" else pytest.skip
    configured = os.environ.get("SCRIPT_CONTRACT_OUT")
    if configured is None:
        missing("SCRIPT_CONTRACT_OUT is not set.")
    folder = Path(configured) / "bench"
    if not folder.is_dir():
        missing(f"No bench exports in {folder}: `./check bench` has not run.")
    exports = sorted(p.parent for p in folder.glob("*/manifest.json") if not p.parent.name.startswith("._"))
    assert exports, f"{folder} exists but has no exports."
    return exports


def recordings(purpose: str) -> list[Recording]:
    found = [r for r in map(read_journal, bench_exports()) if r.context and r.context.purpose == purpose]
    assert found, f"No {purpose} session in the bench exports."
    return found


def test_bench_exports_match_the_schema_and_the_swift_summary() -> None:
    # Detects a Mac export that the reader refuses, a line outside the schema, an encrypted or unmarked
    # export, or a Python fold that differs from the Swift fold.
    for folder in bench_exports():
        manifest, data = load_export(folder)
        assert manifest["demo"] is True, folder.name
        raw = data.splitlines(keepends=True)
        for line in raw:
            SCHEMA.validate(json.loads(line))
        read_journal(folder)
        swift = json.loads((folder.parent / f"{folder.name}.summary.json").read_bytes())
        assert summarize(raw) == swift, folder.name


def test_bench_facts_reach_the_recording() -> None:
    # Detects a Mac bench export whose bench header, tablet, run facts, or native tablet channels do not
    # reach the Recording.
    for rec in recordings("bench"):
        context = rec.context
        assert context is not None and rec.participant is None
        subject = context.subject
        assert isinstance(subject, BenchContext) and subject.role == "W"
        assert (subject.protocol_version, subject.protocol_sha256) == (
            bench.PROTOCOL_VERSION,
            bench.PROTOCOL_SHA256,
        )
        assert context.tablet is not None and context.scale == TABLET_SCALE
        assert (rec.device.model, rec.device.input) == (context.tablet.model, "tablet")
        assert rec.device.planned_channels == {Channel.FORCE, Channel.TILT_X, Channel.TILT_Y, Channel.HOVER}
        assert rec.trials
        for trial in rec.trials:
            run = trial.context
            assert isinstance(run, BenchRunContext)
            assert run.trajectory_sha256 == bench.trajectory_sha256(run.program)
            assert run.clip == f"W-{subject.mounting}-{run.program}-r{run.run}"
            assert trial.task.id == bench.program(run.program).task
            contact = [s for s in trial.strokes if s.state == PenState.CONTACT]
            air = [s for s in trial.strokes if s.state == PenState.IN_AIR]
            assert contact and air
            for stroke in contact:
                assert set(stroke.channels) == {Channel.FORCE, Channel.TILT_X, Channel.TILT_Y}
                assert stroke.max_force == 1 and not stroke.unresolved.any()
            assert all(Channel.HOVER not in s.channels for s in air)


def test_tablet_qualification_export_qualifies() -> None:
    # Detects a Mac qualification export that the qualification report cannot use: no sample intervals, no
    # hold or line result, a scale other than the tablet resolution, or a lost planned channel.
    for rec in recordings("qualification"):
        assert rec.participant == "QUAL"
        report = qualify(rec)
        assert isinstance(report.intervals, Intervals) and report.intervals.count > 0
        assert not isinstance(report.hold_jitter_mm, Refusal)
        assert not isinstance(report.line_deviation_mm, Refusal)
        assert report.scale == TABLET_SCALE
        assert report.planned_channels == {Channel.FORCE, Channel.TILT_X, Channel.TILT_Y, Channel.HOVER}


@pytest.mark.parametrize("contract_out", [False, True])
def test_bench_export_checks_fail_without_exports_after_check_ran_the_bench(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contract_out: bool
) -> None:
    # Detects a bench export check that skips, and so passes, after `./check` ran the bench section.
    def outcome() -> type[BaseException] | None:
        try:
            test_bench_exports_match_the_schema_and_the_swift_summary()
        except (pytest.fail.Exception, pytest.skip.Exception) as e:
            return type(e)
        return None

    if contract_out:
        monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    else:
        monkeypatch.delenv("SCRIPT_CONTRACT_OUT", raising=False)
    monkeypatch.setenv("SCRIPT_REQUIRE_BENCH_EXPORTS", "1")
    assert outcome() is pytest.fail.Exception
    monkeypatch.delenv("SCRIPT_REQUIRE_BENCH_EXPORTS")
    assert outcome() is pytest.skip.Exception


def test_bench_export_checks_fail_for_an_empty_bench_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a bench export check that passes silently when the Mac tests published nothing.
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    (tmp_path / "bench").mkdir()
    with pytest.raises(AssertionError, match="has no exports"):
        test_bench_exports_match_the_schema_and_the_swift_summary()


def test_bench_export_check_compares_the_swift_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detects a configured bench export check that does not compare the Python fold with the Swift summary.
    name = "SCRIPT_BENCH_WM3_3f2a9c1e"
    write_export(tmp_path / "bench" / name, lines("bench_wacom"))
    summary = json.loads((FIXTURES / "journal" / "bench_wacom.fold.json").read_bytes())
    (tmp_path / "bench" / f"{name}.summary.json").write_text(json.dumps(summary))
    monkeypatch.setenv("SCRIPT_CONTRACT_OUT", str(tmp_path))
    test_bench_exports_match_the_schema_and_the_swift_summary()
    test_bench_facts_reach_the_recording()
    summary["trials"][0]["samples"] += 1
    (tmp_path / "bench" / f"{name}.summary.json").write_text(json.dumps(summary))
    with pytest.raises(AssertionError, match=name):
        test_bench_exports_match_the_schema_and_the_swift_summary()
