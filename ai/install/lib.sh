INSTALL_MODE="${DOTFILES_INSTALL_MODE:-symlink}"
INSTALL_ACTION="install"
INSTALL_AGENTS=()
FORCE=false
CLEAN=false

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/dotfiles/ai"

MODULE_NAME=""
MODULE_DIR=""
MODULE_SOURCES=()
MODULE_TARGETS=()
MANAGED_TARGETS=()

usage() {
  cat <<EOF
Usage:
  $0 --agents AGENT,... [options]
  $0 --agents AGENT,... --uninstall

Options:
  -a, --agents AGENT,...
      Agents to install or uninstall.

  -m, --mode symlink|copy
      Installation mode.

  --symlink
      Install using symbolic links.

  --copy
      Install by copying files and directories.

  -f, --force
      Replace conflicting unmanaged files or directories.

  -c, --clean
      Remove previously managed targets that are no longer declared.

  --uninstall
      Remove all targets managed by the selected modules.

  -h, --help
      Show this help.
EOF
}

parse_install_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -a|--agents)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for $1." >&2
          return 1
        fi

        IFS=',' read -r -a INSTALL_AGENTS <<< "$2"
        shift 2
        ;;
      --agents=*)
        IFS=',' read -r -a INSTALL_AGENTS <<< "${1#*=}"
        shift
        ;;
      -m|--mode)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for $1." >&2
          return 1
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
      --uninstall)
        INSTALL_ACTION="uninstall"
        shift
        ;;
      -h|--help)
        usage
        INSTALL_ACTION="help"
        return 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        return 1
        ;;
    esac
  done

  case "$INSTALL_MODE" in
    symlink|copy)
      ;;
    *)
      echo "Invalid install mode: $INSTALL_MODE" >&2
      return 1
      ;;
  esac

  if [[ -z "${INSTALL_AGENTS[*]-}" ]]; then
    echo "No agents specified. Use --agents AGENT,... ." >&2
    return 1
  fi

  local agent

  for agent in "${INSTALL_AGENTS[@]}"; do
    if [[ -z "$agent" || ! "$agent" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
      echo "Invalid agent name: $agent" >&2
      return 1
    fi
  done

  if [[ "$INSTALL_ACTION" == "uninstall" && "$CLEAN" == true ]]; then
    echo "--clean cannot be used with --uninstall." >&2
    return 1
  fi
}

manifest_path() {
  local module="$1"

  printf '%s/%s.manifest\n' "$STATE_DIR" "$module"
}

read_manifest() {
  local module="$1"
  local manifest

  manifest="$(manifest_path "$module")"
  MANAGED_TARGETS=()

  if [[ ! -f "$manifest" ]]; then
    return 0
  fi

  while IFS= read -r target || [[ -n "$target" ]]; do
    if [[ -n "$target" ]]; then
      MANAGED_TARGETS+=("$target")
    fi
  done < "$manifest"
}

write_manifest() {
  local module="$1"
  shift

  local manifest
  local temporary_manifest
  local target

  mkdir -p "$STATE_DIR"

  manifest="$(manifest_path "$module")"
  temporary_manifest="$(mktemp "$STATE_DIR/$module.manifest.XXXXXX")"

  for target in "$@"; do
    printf '%s\n' "$target"
  done > "$temporary_manifest"

  mv "$temporary_manifest" "$manifest"
}

array_contains() {
  local expected="$1"
  shift

  local value

  for value in "$@"; do
    if [[ "$value" == "$expected" ]]; then
      return 0
    fi
  done

  return 1
}

module_reset() {
  MODULE_NAME="$1"
  MODULE_DIR="$AI_DIR/$MODULE_NAME"
  MODULE_SOURCES=()
  MODULE_TARGETS=()
}

module_path() {
  local relative_source="$1"
  local target="$2"
  local source

  source="$MODULE_DIR/$relative_source"

  if [[ ! -e "$source" && ! -L "$source" ]]; then
    echo "Missing module source: $source" >&2
    return 1
  fi

  if array_contains "$target" ${MODULE_TARGETS[@]+"${MODULE_TARGETS[@]}"}; then
    echo "Duplicate module target: $target" >&2
    return 1
  fi

  MODULE_SOURCES+=("$source")
  MODULE_TARGETS+=("$target")
}

module_dir_contents() {
  local relative_source_dir="$1"
  local target_dir="$2"
  local source_dir
  local source

  source_dir="$MODULE_DIR/$relative_source_dir"

  if [[ ! -d "$source_dir" ]]; then
    echo "Missing module source directory: $source_dir" >&2
    return 1
  fi

  while IFS= read -r -d '' source; do
    module_path \
      "$relative_source_dir/$(basename "$source")" \
      "$target_dir/$(basename "$source")"
  done < <(
    find "$source_dir" \
      -mindepth 1 \
      -maxdepth 1 \
      -print0
  )
}

load_module() {
  local module="$1"
  local module_file

  module_file="$AI_DIR/$module/module.sh"

  if [[ ! -f "$module_file" ]]; then
    echo "Unknown module: $module" >&2
    return 1
  fi

  module_reset "$module"
  source "$module_file"
}

remove_target() {
  local target="$1"

  if [[ ! -e "$target" && ! -L "$target" ]]; then
    return 0
  fi

  rm -rf "$target"
}

prepare_install_target() {
  local target="$1"

  if [[ ! -e "$target" && ! -L "$target" ]]; then
    return 0
  fi

  if array_contains "$target" ${MANAGED_TARGETS[@]+"${MANAGED_TARGETS[@]}"}; then
    remove_target "$target"
    return 0
  fi

  if [[ "$FORCE" == true ]]; then
    remove_target "$target"
    return 0
  fi

  echo "Target already exists and is not managed: $target" >&2
  echo "Use --force to replace it." >&2
  return 1
}

install_path() {
  local source="$1"
  local target="$2"

  mkdir -p "$(dirname "$target")"
  prepare_install_target "$target"

  case "$INSTALL_MODE" in
    symlink)
      ln -s "$source" "$target"
      echo "Linked: $target -> $source"
      ;;
    copy)
      cp -R "$source" "$target"
      echo "Copied: $source -> $target"
      ;;
  esac
}

clean_module() {
  local target

  if [[ "$CLEAN" != true ]]; then
    return 0
  fi

  for target in ${MANAGED_TARGETS[@]+"${MANAGED_TARGETS[@]}"}; do
    if ! array_contains "$target" ${MODULE_TARGETS[@]+"${MODULE_TARGETS[@]}"}; then
      remove_target "$target"
      echo "Removed stale target: $target"
    fi
  done
}

install_module() {
  local module="$1"
  local index

  load_module "$module"
  read_manifest "$module"

  for ((index = 0; index < ${#MODULE_TARGETS[@]}; index++)); do
    install_path \
      "${MODULE_SOURCES[$index]}" \
      "${MODULE_TARGETS[$index]}"
  done

  clean_module
  write_manifest "$module" ${MODULE_TARGETS[@]+"${MODULE_TARGETS[@]}"}
}

uninstall_module() {
  local module="$1"
  local manifest
  local target

  read_manifest "$module"

  for target in ${MANAGED_TARGETS[@]+"${MANAGED_TARGETS[@]}"}; do
    remove_target "$target"
    echo "Removed: $target"
  done

  manifest="$(manifest_path "$module")"
  rm -f "$manifest"

  echo "Uninstalled: $module"
}
