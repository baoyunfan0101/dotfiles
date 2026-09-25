#!/usr/bin/env bash
set -euo pipefail

repository_url="${DOTFILES_REPOSITORY_URL:-https://github.com/baoyunfan0101/dotfiles.git}"
checkout="${XDG_DATA_HOME:-$HOME/.local/share}/dotfiles"

fail() {
  printf 'dotfiles install: %s\n' "$1" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || fail "git is required; install git and retry"
command -v bash >/dev/null 2>&1 || fail "bash is required; install bash and retry"

if [[ ! -e "$checkout" ]]; then
  mkdir -p "$(dirname "$checkout")" || fail "cannot create checkout parent directory"
  git clone --quiet --branch main --single-branch "$repository_url" "$checkout" || \
    fail "cannot clone $repository_url; check network access and repository permissions"
else
  [[ -d "$checkout/.git" ]] || fail "$checkout is not a Git checkout; move it and retry"
  [[ "$(git -C "$checkout" remote get-url origin 2>/dev/null)" == "$repository_url" ]] || \
    fail "$checkout has a different origin; inspect it before retrying"
  [[ "$(git -C "$checkout" branch --show-current)" == main ]] || \
    fail "$checkout is not on main; switch it to main and retry"
  [[ -z "$(git -C "$checkout" status --porcelain)" ]] || \
    fail "$checkout has local changes; save them before retrying"
  git -C "$checkout" fetch --quiet origin main || \
    fail "cannot fetch main; check network access and repository permissions"
  git -C "$checkout" merge --quiet --ff-only origin/main || \
    fail "cannot fast-forward main; inspect local commits in $checkout"
fi

bash "$checkout/ai/install.sh" --agents codex --symlink --force --clean || \
  fail "internal installer failed; inspect the error above and retry"

cat <<'EOF'
dotfiles installed.

In the Git repository you want to configure:

  cd /path/to/your/repository
  agent-project set workflow.enabled true

Inspect project settings:
  agent-project effective

Help:
  agent-project --help
  git-workflow --help
EOF
