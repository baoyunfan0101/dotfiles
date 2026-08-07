#!/usr/bin/env bash
set -euo pipefail

FORCE=false
CLEAN=false
INSTALL_MODE="${DOTFILES_INSTALL_MODE:-symlink}"

usage() {
  cat >&2 <<EOF
Usage: $0 [--mode symlink|copy] [--force] [--clean]

Default:
  Install using symlinks and replace empty files.
  Keep real files/directories untouched.
  Set DOTFILES_INSTALL_MODE=copy to change the default mode.

Options:
  -m, --mode MODE
      Install mode: symlink or copy.

  --symlink
      Install using symlinks.

  --copy
      Install by copying files/directories.

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
    -m|--mode)
      if [ $# -lt 2 ]; then
        echo "Missing value for $1" >&2
        usage
        exit 1
      fi

      INSTALL_MODE="$2"
      shift 2
      ;;
    --mode=*)
      INSTALL_MODE="${1#*=}"
      shift
      ;;
    --symlink)
      INSTALL_MODE="symlink"
      shift
      ;;
    --copy)
      INSTALL_MODE="copy"
      shift
      ;;
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

case "$INSTALL_MODE" in
  symlink|copy)
    ;;
  *)
    echo "Invalid install mode: $INSTALL_MODE" >&2
    usage
    exit 1
    ;;
esac

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

install_path() {
  local source_path="$1"
  local target_path="$2"

  ensure_exists "$source_path"
  mkdir -p "$(dirname "$target_path")"

  remove_target_if_allowed "$target_path"

  case "$INSTALL_MODE" in
    symlink)
      ln -s "$source_path" "$target_path"
      echo "Linked: $target_path -> $source_path"
      ;;
    copy)
      cp -R "$source_path" "$target_path"
      echo "Copied: $source_path -> $target_path"
      ;;
  esac
}

install_dir_contents() {
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

    install_path "$source_path" "$target_path"
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

install_path "$AGENTS_SOURCE" "$AGENTS_TARGET"
install_dir_contents "$SKILLS_SOURCE" "$SKILLS_TARGET"
install_dir_contents "$BIN_SOURCE" "$BIN_TARGET"
clean_stale_dir_links "$SKILLS_SOURCE" "$SKILLS_TARGET"
clean_stale_dir_links "$BIN_SOURCE" "$BIN_TARGET"
