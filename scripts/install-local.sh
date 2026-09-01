#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
PLUGINS_DIR="$CONFIG_HOME/omarchy/plugins"
TARGET="$PLUGINS_DIR/io.omarchy.hosts"
REPLACE=0

case "${1:-}" in
  "") ;;
  --replace) REPLACE=1 ;;
  -h|--help)
    echo "Usage: scripts/install-local.sh [--replace]"
    exit 0
    ;;
  *)
    echo "install-local: unknown option: $1" >&2
    exit 2
    ;;
esac

"$ROOT/scripts/check.sh"
mkdir -p -- "$PLUGINS_DIR"
chmod 700 "$PLUGINS_DIR" 2>/dev/null || true

if [[ $(realpath -m -- "$ROOT") == $(realpath -m -- "$TARGET") ]]; then
  echo "Plugin is already installed at $TARGET"
else
  if [[ -e $TARGET || -L $TARGET ]]; then
    if (( ! REPLACE )); then
      echo "install-local: target exists: $TARGET" >&2
      echo "Use --replace to preserve it as a timestamped backup." >&2
      exit 1
    fi
  fi

  STAGING="$PLUGINS_DIR/.io.omarchy.hosts.install.$$"
  BACKUP=""
  trap 'rm -rf -- "$STAGING"' EXIT
  rm -rf -- "$STAGING"
  mkdir -m700 -- "$STAGING"

  (
    cd -- "$ROOT"
    tar \
      --exclude='./.git' \
      --exclude='./__pycache__' \
      --exclude='*/__pycache__' \
      --exclude='./.pytest_cache' \
      --exclude='./.mypy_cache' \
      --exclude='./.ruff_cache' \
      --exclude='./packaging/arch/pkg' \
      --exclude='./packaging/arch/src' \
      -cf - .
  ) | (
    cd -- "$STAGING"
    tar -xf -
  )

  find "$STAGING" -type d -exec chmod u+rwx,go-rwx {} +
  if command -v omarchy >/dev/null 2>&1; then
    omarchy plugin validate "$STAGING"
  fi

  if [[ -e $TARGET || -L $TARGET ]]; then
    BACKUP="$PLUGINS_DIR/io.omarchy.hosts.backup-$(date -u +%Y%m%dT%H%M%SZ)"
    mv -- "$TARGET" "$BACKUP"
  fi

  if ! mv -- "$STAGING" "$TARGET"; then
    if [[ -n $BACKUP && ! -e $TARGET && ! -L $TARGET ]]; then
      mv -- "$BACKUP" "$TARGET" || true
    fi
    exit 1
  fi
  trap - EXIT
  echo "Installed plugin at $TARGET"
  [[ -z $BACKUP ]] || echo "Previous installation preserved at $BACKUP"
fi

if command -v omarchy-shell >/dev/null 2>&1; then
  omarchy-shell shell rescanPlugins >/dev/null || echo "Warning: omarchy-shell is not currently reachable." >&2
fi
if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin enable io.omarchy.hosts || echo "Warning: enable the plugin manually with: omarchy plugin enable io.omarchy.hosts" >&2
fi

echo "The user plugin is installed. Install packaging/arch separately to enable Apply and Undo."
