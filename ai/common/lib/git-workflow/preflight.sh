preflight_error() {
  printf '[git] %s error check=%s reason="%s" required-by=%s fix="%s"\n' \
    "$CURRENT_ACTION" "$1" "$2" "$3" "$4" >&2
  exit 1
}

preflight_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    preflight_error "$1" "command not found" "$2" "$3"
  fi
}

preflight_core() {
  preflight_command git core "install git and ensure it is on PATH"
  preflight_command python3 core "install python3 and ensure it is on PATH"
  preflight_command agent-project-settings core "re-run the dotfiles AI installer"

  if ! INTEGRATION_MODE="$(setting git.integration.mode 2>/dev/null)"; then
    preflight_error project-settings "configuration invalid" core \
      "fix .ai/project.json and run agent-project-settings effective"
  fi
}

preflight_delivery() {
  if [[ "$INTEGRATION_MODE" == pullRequest ]]; then
    preflight_command gh git.integration.mode:pullRequest \
      "install gh and ensure it is on PATH"

    if ! gh auth status >/dev/null 2>&1; then
      preflight_error gh-auth "authentication failed" git.integration.mode:pullRequest \
        "run gh auth login"
    fi
  fi
}
