#!/usr/bin/env bash
set -euo pipefail

AI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$AI_DIR/install/lib.sh"

parse_install_args "$@"

if [[ "$INSTALL_ACTION" == "help" ]]; then
  exit 0
fi

case "$INSTALL_ACTION" in
  install)
    install_module common

    for agent in "${INSTALL_AGENTS[@]}"; do
      install_module "$agent"
    done
    ;;

  uninstall)
    for agent in "${INSTALL_AGENTS[@]}"; do
      uninstall_module "$agent"
    done

    has_agent=false

    for manifest in "$STATE_DIR"/*.manifest; do
      [[ -e "$manifest" ]] || continue
      [[ "$manifest" == "$(manifest_path common)" ]] && continue

      has_agent=true
      break
    done

    if [[ "$has_agent" == false ]]; then
      uninstall_module common
    fi
    ;;
esac
