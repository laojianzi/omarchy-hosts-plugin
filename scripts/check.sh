#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
PYTHON_BIN=${PYTHON_BIN:-$(command -v python)}
cd -- "$ROOT"

"$PYTHON_BIN" - "$ROOT" <<'PY'
from __future__ import annotations

from pathlib import Path
import json
import os
import sys
import xml.etree.ElementTree as ET

root = Path(sys.argv[1])
manifest_path = root / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
required = {"schemaVersion", "id", "name", "version", "kinds", "entryPoints"}
missing = sorted(required - manifest.keys())
if missing:
    raise SystemExit(f"manifest missing fields: {', '.join(missing)}")
if manifest["schemaVersion"] != 1:
    raise SystemExit("manifest schemaVersion must be 1")
if manifest["id"] != "io.omarchy.hosts":
    raise SystemExit("unexpected plugin id")
if "bar-widget" not in manifest["kinds"]:
    raise SystemExit("manifest must declare bar-widget")
entry = manifest["entryPoints"].get("barWidget")
if not isinstance(entry, str) or not entry or entry.startswith("/") or ".." in Path(entry).parts:
    raise SystemExit("unsafe barWidget entry point")
if not (root / entry).is_file():
    raise SystemExit(f"missing barWidget entry point: {entry}")

for path in root.rglob("*"):
    if ".git" in path.parts:
        continue
    if path.is_symlink():
        raise SystemExit(f"symlinks are not allowed in the plugin tree: {path.relative_to(root)}")

for path in sorted(root.rglob("*.py")):
    if "__pycache__" in path.parts:
        continue
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec", dont_inherit=True)

policy = root / "system" / "io.omarchy.hosts.policy"
ET.parse(policy)
print("Python, manifest, XML, and tree safety checks passed.")
PY

for script in scripts/*.sh; do
  bash -n "$script"
done

grep -q '^import qs.Commons$' Panel.qml
grep -q '^import qs.Ui$' Panel.qml
grep -q 'Panel {' Panel.qml
grep -q 'KeyboardPanel {' Panel.qml
grep -q 'IpcHandler {' Panel.qml
grep -q 'Process {' Service.qml
if grep -Eq 'bash[[:space:]]+-c|sh[[:space:]]+-c' Service.qml; then
  echo "Service.qml must not invoke a shell command string" >&2
  exit 1
fi

echo "QML structural checks passed."

./scripts/sync-packaging.sh --check

version=$("$PYTHON_BIN" -I -B bin/omarchy-hosts --version)
[[ $version == 'omarchy-hosts 1.0.0' ]] || {
  echo "unexpected CLI version: $version" >&2
  exit 1
}

PYTHONPATH=src "$PYTHON_BIN" -B -m unittest discover -s tests -v

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin validate "$ROOT"
else
  echo "Omarchy runtime not found; skipped native manifest validation."
fi

if command -v makepkg >/dev/null 2>&1 && (( EUID != 0 )); then
  (cd packaging/arch && makepkg --verifysource)
else
  echo "makepkg source verification skipped (makepkg unavailable or running as root)."
fi

echo "All available checks passed."
