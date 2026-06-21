#!/usr/bin/env bash
set -euo pipefail

CODEX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

mkdir -p "$CODEX_HOME"

link_path() {
  local source_path="$1"
  local target_path="$2"

  if [ ! -e "$source_path" ]; then
    echo "Missing source: $source_path" >&2
    exit 1
  fi

  if [ -L "$target_path" ]; then
    rm "$target_path"
  elif [ -e "$target_path" ]; then
    echo "Target already exists and is not a symlink: $target_path" >&2
    echo "Move it manually before running this script." >&2
    exit 1
  fi

  ln -s "$source_path" "$target_path"
  echo "Linked: $target_path -> $source_path"
}

link_path "$CODEX_DIR/AGENTS.md" "$CODEX_HOME/AGENTS.md"
link_path "$CODEX_DIR/skills" "$CODEX_HOME/skills"
