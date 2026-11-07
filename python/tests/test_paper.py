from __future__ import annotations

import re
from pathlib import Path

PAPER = Path(__file__).resolve().parents[2] / "paper"
INPUT = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
CITE = re.compile(r"\\cite\w*\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}")
INTERNAL = re.compile(
    r"\b(?:python|docs|bench|contract)/"
    r"|\b[\w./-]+\.(?:py|json|csv|md)\b"
    r"|(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])"
)


def without_comments(source: str) -> str:
    lines: list[str] = []
    for line in source.splitlines():
        for index, char in enumerate(line):
            if char != "%":
                continue
            slashes = len(line[:index]) - len(line[:index].rstrip("\\"))
            if slashes % 2 == 0:
                line = line[:index]
                break
        lines.append(line)
    return "\n".join(lines)


def sources() -> list[tuple[Path, str]]:
    return [
        (path, without_comments(path.read_text(encoding="utf-8")))
        for path in sorted(PAPER.rglob("*.tex"))
        if not any(part.startswith("._") for part in path.relative_to(PAPER).parts)
    ]


def test_paper_inputs_exist() -> None:
    # Detects an input command whose target file is missing.
    missing: list[str] = []
    for path, source in sources():
        for name in INPUT.findall(source):
            target = PAPER / name
            if not target.suffix:
                target = target.with_suffix(".tex")
            if any(part.startswith("._") for part in target.parts) or not target.is_file():
                missing.append(f"{path.name}: {name}")
    assert not missing, missing


def test_paper_citations_exist() -> None:
    # Detects a citation key that has no bibliography entry.
    bibliography = without_comments((PAPER / "references.bib").read_text(encoding="utf-8"))
    keys = set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", bibliography))
    missing = {
        key.strip()
        for _, source in sources()
        for group in CITE.findall(source)
        for key in group.split(",")
        if key.strip() not in keys
    }
    assert not missing, sorted(missing)


def test_paper_has_no_rendered_internal_identifiers() -> None:
    # Detects an internal path, filename, or revision hash in manuscript text.
    findings: list[str] = []
    for path, source in sources():
        # These arguments select resources or anchors. They do not print text.
        visible = re.sub(r"\\(?:input|include|label|bibliography|bibliographystyle)\s*\{[^}]*\}", "", source)
        # Join line wraps and escaped underscores before checking printable content.
        visible = visible.replace("\\_", "_").replace("\n", " ")
        findings.extend(f"{path.name}: {match.group()}" for match in INTERNAL.finditer(visible))
    assert not findings, findings
