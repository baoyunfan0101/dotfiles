#!/usr/bin/env bash
set -Eeuo pipefail

test_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
test_sandbox="$(mktemp -d /tmp/dotfiles-test.XXXXXX)"
trap 'rm -rf -- "$test_sandbox"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$test_sandbox/home" "$test_sandbox/tmp" "$test_sandbox/bin"
ln -s "$test_root/ai/common/tests/stubs/gh" "$test_sandbox/bin/gh"
cd "$test_sandbox"

env -i \
  PATH="$test_sandbox/bin:$PATH" \
  HOME="$test_sandbox/home" \
  CODEX_HOME="$test_sandbox/home/.codex" \
  XDG_CONFIG_HOME="$test_sandbox/home/.config" \
  XDG_STATE_HOME="$test_sandbox/home/.local/state" \
  XDG_DATA_HOME="$test_sandbox/home/.local/share" \
  XDG_CACHE_HOME="$test_sandbox/home/.cache" \
  GH_CONFIG_DIR="$test_sandbox/home/.config/gh" \
  TMPDIR="$test_sandbox/tmp" \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
  GIT_ALLOW_PROTOCOL=file \
  LC_ALL=C PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
  DOTFILES_TEST_SANDBOX="$test_sandbox" \
  python3 -B -m unittest discover -s "$test_root/ai/common/tests"
