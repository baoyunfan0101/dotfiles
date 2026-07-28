#!/usr/bin/env bash
set -euo pipefail

FORCE=false
CLEAN=false

usage() {
  cat >&2 <<EOF
Usage: $0 [--force] [--clean]

Default:
  Recreate symlinks and replace empty files.
  Keep real files/directories untouched.

Options:
  -f, --force
      Replace conflicting real files/directories.

  -c, --clean
      Remove stale managed symlinks.

  -h, --help
      Show help.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -f|--force)
      FORCE=true
      shift
      ;;
    -c|--clean)
      CLEAN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_SOURCE_DIR="${CODEX_SOURCE_DIR:-$SCRIPT_DIR}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

AGENTS_SOURCE="$CODEX_SOURCE_DIR/AGENTS.md"
BIN_SOURCE="$CODEX_SOURCE_DIR/bin"
SKILLS_SOURCE="$CODEX_SOURCE_DIR/skills"

AGENTS_TARGET="$CODEX_HOME/AGENTS.md"
BIN_TARGET="$CODEX_HOME/bin"
SKILLS_TARGET="$CODEX_HOME/skills"

ensure_exists() {
  local source_path="$1"

  if [ ! -e "$source_path" ]; then
    echo "Missing source: $source_path" >&2
    exit 1
  fi
}

remove_target_if_allowed() {
  local target_path="$1"

  if [ ! -e "$target_path" ] && [ ! -L "$target_path" ]; then
    return
  fi

  if [ -L "$target_path" ]; then
    rm "$target_path"
    return
  fi

  if [ -f "$target_path" ] && [ ! -s "$target_path" ]; then
    rm "$target_path"
    return
  fi

  if [ "$FORCE" = true ]; then
    rm -rf "$target_path"
    return
  fi

  echo "Target already exists and is not a symlink or empty file: $target_path" >&2
  echo "Use -f/--force to overwrite it." >&2
  exit 1
}

link_path() {
  local source_path="$1"
  local target_path="$2"

  ensure_exists "$source_path"
  mkdir -p "$(dirname "$target_path")"

  remove_target_if_allowed "$target_path"

  ln -s "$source_path" "$target_path"
  echo "Linked: $target_path -> $source_path"
}

link_dir_contents() {
  local source_dir="$1"
  local target_dir="$2"

  if [ ! -d "$source_dir" ]; then
    echo "Missing source directory: $source_dir" >&2
    exit 1
  fi

  mkdir -p "$target_dir"

  shopt -s nullglob dotglob

  local source_path
  for source_path in "$source_dir"/*; do
    local name
    local target_path

    name="$(basename "$source_path")"
    target_path="$target_dir/$name"

    remove_target_if_allowed "$target_path"

    ln -s "$source_path" "$target_path"
    echo "Linked: $target_path -> $source_path"
  done
}

clean_stale_dir_links() {
  local source_dir="$1"
  local target_dir="$2"

  if [ "$CLEAN" != true ]; then
    return
  fi

  if [ ! -d "$target_dir" ]; then
    return
  fi

  shopt -s nullglob dotglob

  local target_path
  for target_path in "$target_dir"/*; do
    if [ ! -L "$target_path" ]; then
      continue
    fi

    local link_source

    link_source="$(readlink "$target_path")"

    case "$link_source" in
      "$source_dir"/*)
        if [ ! -e "$link_source" ]; then
          rm "$target_path"
          echo "Removed stale link: $target_path"
        fi
        ;;
    esac
  done
}

mkdir -p "$CODEX_HOME"

link_path "$AGENTS_SOURCE" "$AGENTS_TARGET"
link_dir_contents "$SKILLS_SOURCE" "$SKILLS_TARGET"
link_dir_contents "$BIN_SOURCE" "$BIN_TARGET"
clean_stale_dir_links "$SKILLS_SOURCE" "$SKILLS_TARGET"
clean_stale_dir_links "$BIN_SOURCE" "$BIN_TARGET"
