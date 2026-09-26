CURRENT_ACTION="workflow"

usage() {
  cat <<EOF2
Usage:
  git-workflow prepare [--branch-name NAME]
  git-workflow commit [--override-manual] --message MESSAGE (--all | -- PATH...)
  git-workflow push [--override-manual]
  git-workflow merge [--message MESSAGE]
  git-workflow pr submit
  git-workflow pr merge

Commands:
  prepare
      Prepare for repository-changing work before editing: protect changes,
      synchronize, select the working branch, and restore protected changes.

  commit
      Commit one atomic change after editing. Automatic mode also pushes it.

  push
      Auxiliary operation for an explicitly needed push outside commit: push
      the current branch according to project settings.

  merge
      Integrate a workflow-created branch locally only with explicit user
      authorization. Requires localMerge mode.

  pr
      Submit or merge a pull request only with explicit user authorization.
      Requires pullRequest mode. See git-workflow pr --help.

Read-only tasks do not use workflow commands.

Prepare options:
  --branch-name NAME
      Candidate working branch name.

Commit options:
  --message MESSAGE
      Commit message.

  --all
      Commit all current changes.

  --override-manual
      Allow an explicitly requested commit when git.commit.mode is manual.
      The commit is created but is not pushed automatically.

  -- PATH...
      Commit only the selected paths.

Push options:
  --override-manual
      Allow an explicitly requested push when git.commit.mode is manual.

Merge options:
  --message MESSAGE
      Override the derived squash message or Git's default merge-commit message.

  -h, --help
      Show this help.
EOF2
}

pr_usage() {
  cat <<EOF2
Usage:
  git-workflow pr submit
  git-workflow pr merge

Commands:
  submit
      Create or update the open PR for the working branch and base, using
      complete branch history. Requires an explicit user request. Stops at URL.

  merge
      Merge an existing open PR only with explicit user authorization, using
      git.integration.mergeMethod. Sync base and apply configured cleanup.

PR submission, CI success, and task completion do not authorize merging.
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
