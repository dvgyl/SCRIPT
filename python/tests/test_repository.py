from __future__ import annotations

from pathlib import Path


def test_temporary_files_stay_outside_the_repository(tmp_path: Path) -> None:
    # Detects pytest temporary files written inside the repository, which can sit on an exFAT drive.
    repository = Path(__file__).resolve().parents[2]
    assert not tmp_path.resolve().is_relative_to(repository)
