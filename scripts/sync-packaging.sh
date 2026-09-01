#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ARCH="$ROOT/packaging/arch"
MODE=sync

case "${1:-}" in
  "") ;;
  --check) MODE=check ;;
  -h|--help)
    echo "Usage: scripts/sync-packaging.sh [--check]"
    exit 0
    ;;
  *)
    echo "sync-packaging: unknown option: $1" >&2
    exit 2
    ;;
esac

sources=(
  "system/omarchy-hosts-helper"
  "system/helper.py"
  "src/omarchy_hosts/engine.py"
  "system/io.omarchy.hosts.policy"
  "LICENSE"
)
targets=(
  "omarchy-hosts-helper"
  "helper.py"
  "engine.py"
  "io.omarchy.hosts.policy"
  "LICENSE"
)

mkdir -p -- "$ARCH"

if [[ $MODE == sync ]]; then
  for i in "${!sources[@]}"; do
    install -m644 -- "$ROOT/${sources[$i]}" "$ARCH/${targets[$i]}"
  done
  chmod 755 "$ARCH/omarchy-hosts-helper"
fi

failed=0
for i in "${!sources[@]}"; do
  src="$ROOT/${sources[$i]}"
  dst="$ARCH/${targets[$i]}"
  if [[ ! -f $dst ]] || ! cmp -s -- "$src" "$dst"; then
    echo "packaging copy differs: ${targets[$i]} <- ${sources[$i]}" >&2
    failed=1
  fi
done

if [[ $MODE == sync ]]; then
  python - "$ARCH/PKGBUILD" "${targets[@]/#/$ARCH/}" <<'PY'
from __future__ import annotations

from pathlib import Path
import hashlib
import re
import sys

pkgbuild = Path(sys.argv[1])
files = [Path(value) for value in sys.argv[2:]]
text = pkgbuild.read_text(encoding="utf-8")
hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in files]
replacement = "sha256sums=(\n" + "".join(f"  '{value}'\n" for value in hashes) + ")"
updated, count = re.subn(r"sha256sums=\(\n.*?\n\)", replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit("sync-packaging: could not locate sha256sums array")
pkgbuild.write_text(updated, encoding="utf-8")
PY
else
  mapfile -t declared < <(
    awk '
      /^sha256sums=\(/ {inside=1; next}
      inside && /^\)/ {exit}
      inside {
        line=$0
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
        gsub(/^\047|\047$/, "", line)
        if (line != "") print line
      }
    ' "$ARCH/PKGBUILD"
  )
  if (( ${#declared[@]} != ${#targets[@]} )); then
    echo "PKGBUILD checksum count differs: ${#declared[@]} != ${#targets[@]}" >&2
    failed=1
  else
    for i in "${!targets[@]}"; do
      actual=$(sha256sum -- "$ARCH/${targets[$i]}" | awk '{print $1}')
      if [[ ${declared[$i]} != "$actual" ]]; then
        echo "PKGBUILD checksum differs for ${targets[$i]}" >&2
        failed=1
      fi
    done
  fi
fi

if (( failed )); then
  exit 1
fi

echo "Arch package sources are synchronized."
