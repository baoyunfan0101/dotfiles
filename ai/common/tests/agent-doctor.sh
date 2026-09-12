#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCTOR="$TEST_DIR/../bin/agent-doctor"
SANDBOX="$(mktemp -d)"
BIN_DIR="$SANDBOX/bin"

trap 'rm -rf "$SANDBOX"' EXIT
mkdir -p "$BIN_DIR"

write_stub() {
  local name="$1"
  local body="$2"

  printf '#!/bin/sh\n%s\n' "$body" > "$BIN_DIR/$name"
  chmod +x "$BIN_DIR/$name"
}

assert_result() {
  local name="$1"
  local expected_status="$2"
  local expected_output="$3"
  local actual_output
  local actual_status

  if actual_output="$(PATH="$BIN_DIR" /bin/bash "$DOCTOR" 2>&1)"; then
    actual_status=0
  else
    actual_status=$?
  fi

  if [[ "$actual_status" != "$expected_status" || "$actual_output" != "$expected_output" ]]; then
    printf 'FAIL: %s\nstatus: %s\noutput:\n%s\n' "$name" "$actual_status" "$actual_output" >&2
    exit 1
  fi
}

write_stub git 'exit 0'
write_stub python3 'exit 0'
write_stub git-workflow 'exit 0'
write_stub agent-project-settings '[ "${INVALID_SETTINGS:-false}" = false ] || exit 1; printf "%s\n" "${INTEGRATION_MODE:-localMerge}"'

assert_result "local merge does not require gh" 0 '[doctor] ready'

INTEGRATION_MODE=pullRequest assert_result \
  "pull request requires gh" \
  1 \
  '[doctor] check error name=gh reason="command not found"
[doctor] required-by=git.integration.mode:pullRequest
[doctor] fix="install gh and ensure it is on PATH"'

write_stub gh '[ "${GH_AUTHENTICATED:-false}" = true ]'

INTEGRATION_MODE=pullRequest assert_result \
  "pull request requires gh authentication" \
  1 \
  '[doctor] check error name=gh-auth reason="authentication failed"
[doctor] required-by=git.integration.mode:pullRequest
[doctor] fix="run gh auth login"'

INTEGRATION_MODE=pullRequest GH_AUTHENTICATED=true assert_result \
  "authenticated pull request mode is ready" \
  0 \
  '[doctor] ready'

INVALID_SETTINGS=true assert_result \
  "invalid project settings are actionable" \
  1 \
  '[doctor] check error name=project-settings reason="configuration invalid"
[doctor] required-by=core
[doctor] fix="fix .ai/project.json and run agent-project-settings effective"'

printf 'agent-doctor tests passed\n'
