#!/usr/bin/env python3
"""Generate GitHub Release notes from the canonical English changelog."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def extract_section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(?P<body>.*?)(?=^## \[|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError(f"CHANGELOG.md has no section for {version}")
    body = match.group("body").strip()
    # Drop link-reference definitions from the section body; GitHub Release
    # notes use explicit tag-pinned links appended below.
    body = re.sub(r"\n?^\[[^\]]+\]:\s+\S+\s*$", "", body, flags=re.MULTILINE).strip()
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="Release tag, for example v1.0.0")
    args = parser.parse_args()

    tag = args.tag.strip()
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", tag):
        print(f"invalid release tag: {tag!r}", file=sys.stderr)
        return 1
    version = tag[1:]

    try:
        body = extract_section(version)
    except (OSError, ValueError) as exc:
        print(f"release-note generation failed: {exc}", file=sys.stderr)
        return 1

    repository = os.environ.get("GITHUB_REPOSITORY", "laojianzi/omarchy-hosts-plugin")
    base = f"https://github.com/{repository}/blob/{tag}"

    print(f"# Omarchy Hosts {tag}")
    print()
    print("A production release of the native, keyboard-first hosts profile manager for Omarchy 4.")
    print()
    print(body)
    print()
    print("## Documentation")
    print()
    print(f"- [English README]({base}/README.md) — canonical documentation")
    print(f"- [简体中文 README]({base}/README.zh-CN.md)")
    print(f"- [English changelog]({base}/CHANGELOG.md)")
    print(f"- [简体中文更新日志]({base}/CHANGELOG.zh-CN.md)")
    print()
    print("## Install")
    print()
    print("```bash")
    print("omarchy plugin add \\")
    print(f"  https://github.com/{repository}.git \\")
    print("  --enable")
    print("```")
    print()
    print("The privileged Apply/Undo helper is intentionally installed separately from `packaging/arch/` after source review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
