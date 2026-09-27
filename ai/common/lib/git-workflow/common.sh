CURRENT_ACTION="workflow"

usage() {
  cat <<EOF2
Usage:
  git-workflow prepare [--branch-name NAME]
  git-workflow prepare --base BRANCH --branch-name NAME
  git-workflow commit [--override-manual] --message MESSAGE (--all | -- PATH...)
  git-workflow commit --amend [--message MESSAGE] (--all | -- PATH...)
  git-workflow restore [--source REF] -- PATH...
  git-workflow revert COMMIT
  git-workflow cherry-pick COMMIT
  git-workflow rebase [--base BRANCH]
  git-workflow push [--override-manual]
  git-workflow merge [--base BRANCH] [--message MESSAGE]
  git-workflow pr submit [--base BRANCH]
  git-workflow pr merge [--base BRANCH]

Commands:
  prepare
      Prepare the current branch before editing. With --base and --branch-name,
      start a new branch from the updated configured base.

  commit
      Commit one atomic change after editing. Automatic mode also pushes it.
      --amend rewrites the current working-branch HEAD with lease-safe publishing.

  restore
      Restore selected tracked paths in the working tree from HEAD or a ref.

  revert
      Create one inverse commit for a non-merge commit.

  cherry-pick
      Apply one non-merge commit to the current working branch.

  rebase
      Replay the current working branch onto the latest configured base.

  push
      Auxiliary operation for an explicitly needed push outside commit: push
      the current branch according to project settings.

  merge
      Integrate the current branch locally only with explicit user
      authorization. Requires localMerge mode.

  pr
      Submit or merge a pull request only with explicit user authorization.
      Requires pullRequest mode. See git-workflow pr --help.

Read-only tasks do not use workflow commands.

Prepare options:
  --branch-name NAME
      Candidate branch name using <type>/<description> when starting from a base.

  --base BRANCH
      Start a new working branch from this configured base. Requires
      --branch-name; without --base, prepare continues the current branch.

Commit options:
  --message MESSAGE
      Commit message. Optional with --amend, which otherwise keeps the message.

  --amend
      Rewrite the current working-branch HEAD.

  --all
      Commit all current changes.

  --override-manual
      Allow an explicitly requested commit when git.commit.mode is manual.
      The commit is created but is not pushed automatically.

  -- PATH...
      Commit only the selected paths.

Restore options:
  --source REF
      Read selected tracked paths from REF instead of HEAD.

  -- PATH...
      Required tracked paths; only the working tree is changed.

Rebase options:
  --base BRANCH
      Use a configured base; required when multiple bases are configured.

Push options:
  --override-manual
      Allow an explicitly requested push when git.commit.mode is manual.

Merge options:
  --base BRANCH
      Use a configured delivery base. Required when the base is ambiguous.

  --message MESSAGE
      Override the derived squash message or Git's default merge-commit message.

  -h, --help
      Show this help.
EOF2
}

pr_usage() {
  cat <<EOF2
Usage:
  git-workflow pr submit [--base BRANCH]
  git-workflow pr merge [--base BRANCH]

Commands:
  submit
      Create or update the open PR for the working branch and base, using
      complete branch history. Requires an explicit user request. Stops at URL.

  merge
      Merge an existing open PR only with explicit user authorization, using
      git.integration.mergeMethod. Sync base and apply configured cleanup.

PR submission, CI success, and task completion do not authorize merging.
Base: --base, then an existing PR, then the sole configured base.
Ambiguous bases require --base. Configured base branches cannot be delivered.
EOF2
}

fail() {
  printf '[git] %s error reason="%s"\n' "$CURRENT_ACTION" "$*" >&2
  exit 1
}

handle_error() {
  local status="$1"

  trap - ERR
  printf '[git] %s error status=%s\n' "$CURRENT_ACTION" "$status" >&2
  exit "$status"
}

require_command() {
  local command_name="$1"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    fail "required command not found: $command_name"
  fi
}

trap 'handle_error "$?"' ERR
