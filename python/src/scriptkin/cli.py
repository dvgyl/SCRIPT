"""Command line: `scriptkin contract regenerate|verify`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scriptkin import contract


def _contract_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "contract"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scriptkin")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("contract", help="regenerate or verify the generated contract files")
    c.add_argument("action", choices=["regenerate", "verify"])
    c.add_argument("--dir", type=Path, default=None, help="contract directory (default: the repo contract/)")
    args = parser.parse_args(argv)

    directory: Path = args.dir or _contract_dir()
    if args.action == "regenerate":
        written = contract.regenerate(directory)
        print(f"contract: {len(written)} file(s) written")
        return 0
    stale = contract.verify(directory)
    if stale:
        print(
            "contract: generated files differ from the generator. Run `scriptkin contract regenerate`:",
            file=sys.stderr,
        )
        for rel in stale:
            print(f"  {rel}", file=sys.stderr)
        return 1
    print("contract: up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
