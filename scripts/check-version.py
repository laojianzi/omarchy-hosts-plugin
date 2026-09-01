#!/usr/bin/env python3
"""Check that source, documentation, and an optional Git tag share one version."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def fail(message: str) -> None:
    raise ValueError(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def manifest_version() -> str:
    payload = json.loads(read("manifest.json"))
    value = str(payload.get("version", "")).strip()
    if not SEMVER_RE.fullmatch(value):
        fail(f"manifest.json contains an invalid semantic version: {value!r}")
    return value


def python_version() -> str:
    match = re.search(
        r"^__version__\s*=\s*['\"]([^'\"]+)['\"]\s*$",
        read("src/omarchy_hosts/__init__.py"),
        flags=re.MULTILINE,
    )
    if not match:
        fail("src/omarchy_hosts/__init__.py does not define __version__")
    return match.group(1)


def newest_changelog_version(path: str) -> str:
    match = re.search(r"^## \[([^\]]+)\]", read(path), flags=re.MULTILINE)
    if not match:
        fail(f"{path} does not contain a release heading like '## [1.0.0]'")
    return match.group(1)


def documented_readme_version(path: str) -> str:
    match = re.search(r"`(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)`", read(path))
    if not match:
        fail(f"{path} does not contain a displayed version")
    return match.group(1)


def normalize_tag(tag: str) -> str:
    value = tag.strip()
    if value.startswith("refs/tags/"):
        value = value[len("refs/tags/") :]
    if not value.startswith("v"):
        fail(f"release tag must use the vMAJOR.MINOR.PATCH form: {tag!r}")
    version = value[1:]
    if not SEMVER_RE.fullmatch(version):
        fail(f"release tag contains an invalid semantic version: {tag!r}")
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tag",
        help="Optional Git tag to compare, for example v1.0.0 or refs/tags/v1.0.0",
    )
    args = parser.parse_args()

    try:
        expected = manifest_version()
        versions = {
            "src/omarchy_hosts/__init__.py": python_version(),
            "CHANGELOG.md": newest_changelog_version("CHANGELOG.md"),
            "CHANGELOG.zh-CN.md": newest_changelog_version("CHANGELOG.zh-CN.md"),
            "README.md": documented_readme_version("README.md"),
            "README.zh-CN.md": documented_readme_version("README.zh-CN.md"),
        }
        if args.tag:
            versions[f"tag {args.tag}"] = normalize_tag(args.tag)

        mismatches = {name: value for name, value in versions.items() if value != expected}
        if mismatches:
            details = ", ".join(f"{name}={value!r}" for name, value in mismatches.items())
            fail(f"version mismatch; manifest.json={expected!r}; {details}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"version check failed: {exc}", file=sys.stderr)
        return 1

    print(f"Version consistency check passed: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
