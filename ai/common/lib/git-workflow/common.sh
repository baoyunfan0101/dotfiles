CURRENT_ACTION="workflow"

usage() {
  cat <<EOF2
Usage:
  git-workflow start [--branch-name NAME]
  git-workflow commit [--override-manual] --message MESSAGE (--all | -- PATH...)
  git-workflow push [--override-manual]
  git-workflow finish [options]

Commands:
  start
      Start a repository-changing task once before editing: protect changes,
      synchronize, select the working branch, and restore protected changes.

  commit
      Commit one atomic change. Automatic mode also pushes it.

  push
      Auxiliary operation: push the current branch according to project settings.

  finish
      Finish and deliver a completed workflow-created task according to project settings.

Read-only tasks do not use workflow commands.

Start options:
  --branch-name NAME
      Candidate task branch name.

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

Finish options:
  --message MESSAGE
      Required for squash local merge; optional for merge-commit delivery.

  --title TITLE
      Required for pull request delivery.

  --body BODY
      Optional pull request body; mutually exclusive with --body-file.

  --body-file FILE
      Read an optional pull request body from FILE; mutually exclusive with --body.

  -h, --help
      Show this help.
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
