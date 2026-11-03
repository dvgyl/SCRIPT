"""`./check` with stand-ins for uv, swift, xcrun, and xcodebuild that log what they see."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAND_INS = {
    "uv": """
case " $* " in
    *" python - "*) echo SIMULATOR ;;
    *" pytest "*) echo "pytest SCRIPT_REQUIRE_APP_EXPORTS=${SCRIPT_REQUIRE_APP_EXPORTS:-}" >>"$LOG" ;;
esac
""",
    "swift": """
if [ -e "$SCRIPT_CONTRACT_OUT/old" ]; then echo "swift tests see an old export" >>"$LOG"; fi
mkdir -p "$SCRIPT_CONTRACT_OUT"
""",
    "xcodebuild": """
if [ -e "$SCRIPT_BUILD/contract-out/app/old" ]; then echo "app tests see an old export" >>"$LOG"; fi
""",
    "xcrun": """
if [ "$1 $2" = "simctl get_app_container" ]; then echo "$CONTAINER"; fi
""",
}


def run_check(tmp_path: Path, *sections: str) -> list[str]:
    """Run `./check` on a build folder that holds an old Swift export and an old app export."""
    stand_ins = tmp_path / "bin"
    stand_ins.mkdir(exist_ok=True)
    for name, body in STAND_INS.items():
        (stand_ins / name).write_text(f"#!/bin/bash\n{body}exit 0\n")
        (stand_ins / name).chmod(0o755)
    build, container, log = tmp_path / "build", tmp_path / "container", tmp_path / "log"
    for old in (build / "contract-out" / "old", build / "contract-out" / "app" / "old"):
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("")
    (container / "Documents" / "Exports" / "new").mkdir(parents=True, exist_ok=True)
    log.write_text("")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ("SCRIPT_CONTRACT_OUT", "SCRIPT_REQUIRE_APP_EXPORTS")
    }
    environment |= {
        "PATH": f"{stand_ins}:{os.environ['PATH']}",
        "SCRIPT_BUILD": str(build),
        "LOG": str(log),
        "CONTAINER": str(container),
    }
    subprocess.run([ROOT / "check", *sections], env=environment, check=True, capture_output=True)
    return log.read_text().splitlines()


def test_check_core_starts_the_swift_tests_without_old_exports(tmp_path: Path) -> None:
    # Detects a contract output that keeps an export from an earlier run, which the Python tests then read.
    assert run_check(tmp_path, "core") == []
    assert not (tmp_path / "build" / "contract-out" / "old").exists()


def test_check_app_starts_the_ui_tests_without_old_exports(tmp_path: Path) -> None:
    # Detects app exports from an earlier run that remain when the UI tests fail to write new ones.
    assert run_check(tmp_path, "app") == []
    assert sorted(p.name for p in (tmp_path / "build" / "contract-out" / "app").iterdir()) == ["new"]


def test_check_python_needs_the_app_exports_only_after_the_app_section(tmp_path: Path) -> None:
    # Detects a Python step that lets the app export tests skip after `./check` ran the app section.
    assert run_check(tmp_path, "app", "python") == ["pytest SCRIPT_REQUIRE_APP_EXPORTS=1"]
    (tmp_path / "log").unlink()
    assert run_check(tmp_path, "python") == ["pytest SCRIPT_REQUIRE_APP_EXPORTS="]
