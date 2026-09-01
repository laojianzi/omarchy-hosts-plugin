#!/usr/bin/env bash
set -euo pipefail

CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
PLUGINS_DIR="$CONFIG_HOME/omarchy/plugins"
TARGET="$PLUGINS_DIR/io.omarchy.hosts"
STATE="$CONFIG_HOME/omarchy/hosts"
PURGE_STATE=0

case "${1:-}" in
  "") ;;
  --purge-state) PURGE_STATE=1 ;;
  -h|--help)
    echo "Usage: scripts/uninstall-local.sh [--purge-state]"
    exit 0
    ;;
  *)
    echo "uninstall-local: unknown option: $1" >&2
    exit 2
    ;;
esac

[[ $(basename -- "$TARGET") == io.omarchy.hosts ]] || {
  echo "uninstall-local: unsafe target: $TARGET" >&2
  exit 1
}
[[ $(dirname -- "$TARGET") == "$PLUGINS_DIR" ]] || {
  echo "uninstall-local: unsafe plugin directory" >&2
  exit 1
}

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin disable io.omarchy.hosts || true
fi

if [[ -L $TARGET ]]; then
  unlink -- "$TARGET"
elif [[ -d $TARGET ]]; then
  rm -rf --one-file-system -- "$TARGET"
elif [[ -e $TARGET ]]; then
  echo "uninstall-local: target is not a directory or symlink: $TARGET" >&2
  exit 1
fi

echo "Removed user plugin: $TARGET"

if (( PURGE_STATE )); then
  [[ $(basename -- "$STATE") == hosts ]] || exit 1
  rm -rf --one-file-system -- "$STATE"
  echo "Removed user state: $STATE"
else
  echo "Preserved user state: $STATE"
fi

echo "Root helper and backups were not removed. Use pacman and explicit administrator review for those files."
