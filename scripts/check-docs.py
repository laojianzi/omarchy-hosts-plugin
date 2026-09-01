#!/usr/bin/env python3
"""Validate the canonical English / Simplified Chinese documentation set."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

PAIRS = (
    (Path("README.md"), Path("README.zh-CN.md")),
    (Path("CHANGELOG.md"), Path("CHANGELOG.zh-CN.md")),
    (Path("CONTRIBUTING.md"), Path("CONTRIBUTING.zh-CN.md")),
    (Path("SECURITY.md"), Path("SECURITY.zh-CN.md")),
    (Path("docs/ARCHITECTURE.md"), Path("docs/ARCHITECTURE.zh-CN.md")),
    (Path("docs/THREAT-MODEL.md"), Path("docs/THREAT-MODEL.zh-CN.md")),
)

LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")


def fail(message: str) -> None:
    raise ValueError(message)


def read(path: Path) -> str:
    full = ROOT / path
    if not full.is_file():
        fail(f"missing documentation file: {path}")
    try:
        return full.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        fail(f"documentation is not UTF-8: {path}: {exc}")


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def english_switch(english: Path, chinese: Path) -> str:
    return f"**English** | [简体中文]({chinese.name})"


def chinese_switch(english: Path) -> str:
    return f"[English]({english.name}) | **简体中文**"


def validate_pair(english: Path, chinese: Path) -> None:
    english_text = read(english)
    chinese_text = read(chinese)

    actual_english_switch = first_nonempty_line(english_text)
    actual_chinese_switch = first_nonempty_line(chinese_text)

    expected_english_switch = english_switch(english, chinese)
    expected_chinese_switch = chinese_switch(english)

    if actual_english_switch != expected_english_switch:
        fail(
            f"{english}: first non-empty line must be "
            f"{expected_english_switch!r}, got {actual_english_switch!r}"
        )
    if actual_chinese_switch != expected_chinese_switch:
        fail(
            f"{chinese}: first non-empty line must be "
            f"{expected_chinese_switch!r}, got {actual_chinese_switch!r}"
        )

    english_levels = HEADING_RE.findall(english_text)
    chinese_levels = HEADING_RE.findall(chinese_text)
    if english_levels != chinese_levels:
        fail(
            f"heading structure differs between {english} and {chinese}: "
            f"{english_levels!r} != {chinese_levels!r}"
        )


def mask_fenced_code_blocks(text: str) -> str:
    """Replace fenced-code lines with blanks before scanning Markdown links.

    Link-shaped examples inside fenced code are literal documentation, not
    navigable Markdown links. Keeping a blank line for each masked line makes
    future diagnostics stable without requiring a third-party Markdown parser.
    """

    output: list[str] = []
    fence_character = ""
    minimum_fence_length = 0

    for line in text.splitlines():
        if not fence_character:
            match = FENCE_OPEN_RE.match(line)
            if match:
                marker = match.group("fence")
                fence_character = marker[0]
                minimum_fence_length = len(marker)
                output.append("")
                continue
            output.append(line)
            continue

        closing_re = re.compile(
            rf"^[ \t]{{0,3}}{re.escape(fence_character)}"
            rf"{{{minimum_fence_length},}}[ \t]*$"
        )
        if closing_re.match(line):
            fence_character = ""
            minimum_fence_length = 0
        output.append("")

    return "\n".join(output)


def normalize_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    # A quoted Markdown link title follows whitespace. Repository paths here do
    # not contain spaces, so keeping the first token is deterministic.
    target = target.split()[0] if target else ""
    target = target.split("#", 1)[0].split("?", 1)[0]
    return unquote(target)


def validate_links(path: Path, text: str) -> None:
    scan_text = mask_fenced_code_blocks(text)
    for raw_target in LINK_RE.findall(scan_text):
        target = normalize_link_target(raw_target)
        if not target:
            continue
        lowered = target.lower()
        if lowered.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
            continue
        if target.startswith("/"):
            fail(f"absolute repository link is not allowed in {path}: {raw_target}")

        resolved = (ROOT / path.parent / target).resolve()
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            fail(f"link escapes the repository in {path}: {raw_target}")
        if not resolved.exists():
            fail(f"broken local link in {path}: {raw_target}")


def validate_document_inventory() -> None:
    expected = {path for pair in PAIRS for path in pair}
    actual = {
        path.relative_to(ROOT)
        for path in ROOT.rglob("*.md")
        if ".git" not in path.parts
    }
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        fail("missing expected documentation: " + ", ".join(map(str, missing)))
    if unexpected:
        fail(
            "new Markdown documentation must be added as an English/zh-CN pair "
            "and registered in scripts/check-docs.py: "
            + ", ".join(map(str, unexpected))
        )


def main() -> int:
    try:
        validate_document_inventory()
        for english, chinese in PAIRS:
            validate_pair(english, chinese)
            validate_links(english, read(english))
            validate_links(chinese, read(chinese))
    except ValueError as exc:
        print(f"documentation check failed: {exc}", file=sys.stderr)
        return 1

    print(f"Bilingual documentation checks passed for {len(PAIRS)} document pairs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
