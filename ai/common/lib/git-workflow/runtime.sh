BRANCH_NAME=""

BACKUP_MODE=""
BACKUP_METHOD=""
BACKUP_REF="none"
BACKUP_CREATED=false
BACKUP_TRANSPORT_SHA=""
BACKUP_TRANSPORT_KEEP=false

SYNC_MODE=""
SYNC_UPDATE_METHOD=""
SYNC_RESULT="skipped"

BRANCH_MODE=""
BASE_BRANCHES_JSON="[]"

COMMIT_MODE=""
COMMIT_MESSAGE=""
COMMIT_ALL=false
COMMIT_OVERRIDE_MANUAL=false
COMMIT_PATHS=()
COMMIT_PATH_COUNT=0

PUSH_OVERRIDE_MANUAL=false

INTEGRATION_MODE=""
INTEGRATION_MERGE_METHOD=""
DELETE_AFTER_INTEGRATION=false
INTEGRATION_MESSAGE=""
PR_TITLE=""
PR_BODY=""
PR_BODY_FILE=""

ORIGINAL_BRANCH=""
WORKING_BRANCH=""
BRANCH_CREATED=false
CURRENT_ACTION="workflow"
INTEGRATION_BRANCH_DELETED=false

usage() {
  cat <<EOF2
Usage:
  git-workflow start [--branch-name NAME]
  git-workflow commit [--override-manual] --message MESSAGE (--all | -- PATH...)
  git-workflow push [--override-manual]
  git-workflow finish [options]

Commands:
  start
      Back up selected local changes, synchronize the current branch,
      select or create the working branch, and reapply the backup.

  commit
      Commit one atomic change. Automatic mode also pushes it.

  push
      Push the current branch according to project settings.

  finish
      Finish a workflow-created task according to project settings.

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
      Squash commit message, or optional merge-commit message.

  --title TITLE
      Pull request title.

  --body BODY
      Pull request body.

  --body-file FILE
      Read the pull request body from FILE.

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

trap 'handle_error "$?"' ERR

require_command() {
  local command_name="$1"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    fail "required command not found: $command_name"
  fi
}

setting() {
  agent-project-settings get "$1"
}

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

preflight() {
  local integration_mode

  preflight_command git core "install git and ensure it is on PATH"
  preflight_command python3 core "install python3 and ensure it is on PATH"
  preflight_command agent-project-settings core "re-run the dotfiles AI installer"

  if ! integration_mode="$(setting git.integration.mode 2>/dev/null)"; then
    preflight_error project-settings "configuration invalid" core \
      "fix .ai/project.json and run agent-project-settings effective"
  fi

  if [[ "$integration_mode" == pullRequest ]]; then
    preflight_command gh git.integration.mode:pullRequest \
      "install gh and ensure it is on PATH"

    if ! gh auth status >/dev/null 2>&1; then
      preflight_error gh-auth "authentication failed" git.integration.mode:pullRequest \
        "run gh auth login"
    fi
  fi
}

require_repository() {
  require_command git
  require_command agent-project-settings

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    fail "current directory is not inside a Git worktree"
  fi
}

current_branch() {
  local branch

  branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"

  if [[ -z "$branch" ]]; then
    fail "detached HEAD is not supported"
  fi

  printf '%s\n' "$branch"
}

workflow_base_branch() {
  git config --get "branch.$1.agentWorkflowBase" || true
}

workflow_branch_created() {
  git config --bool --get "branch.$1.agentWorkflowCreated" || printf 'false\n'
}

mark_workflow_branch() {
  local branch="$1"
  local base_branch="$2"

  git config "branch.$branch.agentWorkflowBase" "$base_branch"
  git config "branch.$branch.agentWorkflowCreated" true
}

clear_workflow_branch() {
  local branch="$1"

  git config --unset-all "branch.$branch.agentWorkflowBase" >/dev/null 2>&1 || true
  git config --unset-all "branch.$branch.agentWorkflowCreated" >/dev/null 2>&1 || true
}

has_tracked_changes() {
  if ! git diff --quiet -- || ! git diff --cached --quiet --; then
    return 0
  fi

  return 1
}

has_untracked_changes() {
  local untracked

  untracked="$(git ls-files --others --exclude-standard)"
  [[ -n "$untracked" ]]
}

has_local_changes() {
  if has_tracked_changes || has_untracked_changes; then
    return 0
  fi

  return 1
}

selected_changes_exist() {
  case "$BACKUP_MODE" in
    none)
      return 1
      ;;
    tracked)
      has_tracked_changes
      ;;
    untracked)
      has_untracked_changes
      ;;
    all)
      has_local_changes
      ;;
    *)
      fail "unsupported git.backup.mode: $BACKUP_MODE"
      ;;
  esac
}

stash_selected_changes() {
  local message="$1"
  local untracked_paths=()
  local untracked_count=0
  local path

  BACKUP_TRANSPORT_SHA=""

  case "$BACKUP_MODE" in
    none)
      return 0
      ;;
    tracked)
      if ! has_tracked_changes; then
        return 0
      fi

      git stash push -m "$message" >/dev/null
      ;;
    untracked)
      while IFS= read -r -d '' path; do
        untracked_paths+=("$path")
        ((untracked_count += 1))
      done < <(git ls-files --others --exclude-standard -z)

      if ((untracked_count == 0)); then
        return 0
      fi

      git stash push -u -m "$message" -- "${untracked_paths[@]}" >/dev/null
      ;;
    all)
      if ! has_local_changes; then
        return 0
      fi

      git stash push -u -m "$message" >/dev/null
      ;;
    *)
      fail "unsupported git.backup.mode: $BACKUP_MODE"
      ;;
  esac

  BACKUP_TRANSPORT_SHA="$(git rev-parse --verify refs/stash)"
}

create_stash_backup() {
  local message

  message="agent-workflow backup $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  stash_selected_changes "$message"

  if [[ -z "$BACKUP_TRANSPORT_SHA" ]]; then
    return 0
  fi

  BACKUP_REF="$BACKUP_TRANSPORT_SHA"
  BACKUP_CREATED=true
  BACKUP_TRANSPORT_KEEP=true
}

create_commit_backup_ref() {
  local timestamp
  local backup_ref
  local tmp_index
  local tree
  local commit_sha
  local message
  local untracked_paths=()
  local untracked_count=0
  local path

  if ! selected_changes_exist; then
    return 0
  fi

  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_ref="refs/agent-workflow/backups/${timestamp}-$$"
  message="agent-workflow backup $timestamp"

  tmp_index="$(mktemp "${TMPDIR:-/tmp}/agent-workflow-index.XXXXXX")"
  rm -f "$tmp_index"


  GIT_INDEX_FILE="$tmp_index" git read-tree HEAD

  case "$BACKUP_MODE" in
    tracked)
      GIT_INDEX_FILE="$tmp_index" git add -u --
      ;;
    untracked)
      while IFS= read -r -d '' path; do
        untracked_paths+=("$path")
        ((untracked_count += 1))
      done < <(git ls-files --others --exclude-standard -z)

      if ((untracked_count > 0)); then
        GIT_INDEX_FILE="$tmp_index" git add -- "${untracked_paths[@]}"
      fi
      ;;
    all)
      GIT_INDEX_FILE="$tmp_index" git add -A --
      ;;
    *)
      fail "unsupported git.backup.mode: $BACKUP_MODE"
      ;;
  esac

  tree="$(GIT_INDEX_FILE="$tmp_index" git write-tree)"
  commit_sha="$(printf '%s\n' "$message" | git commit-tree "$tree" -p HEAD)"
  git update-ref "$backup_ref" "$commit_sha"

  BACKUP_REF="$backup_ref"
  BACKUP_CREATED=true

  rm -f "$tmp_index"
}

create_commit_backup() {
  local message

  create_commit_backup_ref

  if [[ "$BACKUP_CREATED" != true ]]; then
    return 0
  fi

  message="agent-workflow transport $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  stash_selected_changes "$message"

  if [[ -z "$BACKUP_TRANSPORT_SHA" ]]; then
    fail "failed to create temporary transport stash for commit backup"
  fi

  BACKUP_TRANSPORT_KEEP=false
}

create_backup() {
  if [[ "$BACKUP_MODE" == "none" ]]; then
    return 0
  fi

  case "$BACKUP_METHOD" in
    stash)
      create_stash_backup
      ;;
    commit)
      create_commit_backup
      ;;
    *)
      fail "unsupported git.backup.method: $BACKUP_METHOD"
      ;;
  esac
}

stash_name_for_sha() {
  local target_sha="$1"
  local stash_name
  local stash_sha

  while read -r stash_name stash_sha; do
    if [[ "$stash_sha" == "$target_sha" ]]; then
      printf '%s\n' "$stash_name"
      return 0
    fi
  done < <(git stash list --format='%gd %H')

  return 1
}

reapply_backup() {
  local stash_name

  if [[ -z "$BACKUP_TRANSPORT_SHA" ]]; then
    return 0
  fi

  git stash apply --index "$BACKUP_TRANSPORT_SHA" >/dev/null

  if [[ "$BACKUP_TRANSPORT_KEEP" == false ]]; then
    stash_name="$(stash_name_for_sha "$BACKUP_TRANSPORT_SHA" || true)"

    if [[ -n "$stash_name" ]]; then
      git stash drop "$stash_name" >/dev/null
    fi
  fi
}

remote_for_branch() {
  local branch="$1"
  local fallback_branch="${2:-}"
  local remote

  remote="$(git config --get "branch.$branch.remote" || true)"

  if [[ -n "$remote" && "$remote" != "." ]]; then
    printf '%s\n' "$remote"
    return 0
  fi

  if [[ -n "$fallback_branch" ]]; then
    remote="$(git config --get "branch.$fallback_branch.remote" || true)"

    if [[ -n "$remote" && "$remote" != "." ]]; then
      printf '%s\n' "$remote"
      return 0
    fi
  fi

  if git remote get-url origin >/dev/null 2>&1; then
    printf 'origin\n'
    return 0
  fi

  fail "cannot determine remote for branch: $branch"
}

sync_current_branch() {
  local branch="$1"
  local remote
  local upstream

  case "$SYNC_MODE" in
    none)
      SYNC_RESULT="skipped"
      ;;
    fetch)
      remote="$(remote_for_branch "$branch")"
      git fetch --quiet "$remote"
      SYNC_RESULT="fetched"
      ;;
    update)
      remote="$(remote_for_branch "$branch")"
      upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"

      if [[ -z "$upstream" ]]; then
        fail "current branch has no upstream: $branch"
      fi

      git fetch --quiet "$remote"

      case "$SYNC_UPDATE_METHOD" in
        ffOnly)
          git merge --quiet --ff-only "$upstream"
          ;;
        rebase)
          git rebase --quiet "$upstream"
          ;;
        merge)
          git merge --quiet --no-edit "$upstream"
          ;;
        *)
          fail "unsupported git.sync.updateMethod: $SYNC_UPDATE_METHOD"
          ;;
      esac

      SYNC_RESULT="updated"
      ;;
    *)
      fail "unsupported git.sync.mode: $SYNC_MODE"
      ;;
  esac
}

push_branch() {
  local branch="$1"
  local fallback_branch="${2:-}"
  local remote
  local merge_ref

  remote="$(remote_for_branch "$branch" "$fallback_branch")"
  merge_ref="$(git config --get "branch.$branch.merge" || true)"

  if [[ -n "$merge_ref" ]]; then
    git push --quiet "$remote" "$branch"
  else
    git push --quiet --set-upstream "$remote" "$branch"
  fi
}

is_base_branch() {
  local branch="$1"

  require_command python3

  python3 - "$branch" "$BASE_BRANCHES_JSON" <<'PY'
import json
import sys

branch = sys.argv[1]
base_branches = json.loads(sys.argv[2])
raise SystemExit(0 if branch in base_branches else 1)
PY
}

create_task_branch() {
  if [[ -z "$BRANCH_NAME" ]]; then
    fail "--branch-name is required by the configured branch mode"
  fi

  if ! git check-ref-format --branch "$BRANCH_NAME" >/dev/null 2>&1; then
    fail "invalid branch name: $BRANCH_NAME"
  fi

  if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
    fail "branch already exists: $BRANCH_NAME"
  fi

  git checkout -q -b "$BRANCH_NAME"
  BRANCH_CREATED=true
  mark_workflow_branch "$BRANCH_NAME" "$ORIGINAL_BRANCH"
}

select_working_branch() {
  case "$BRANCH_MODE" in
    current)
      ;;
    alwaysCreate)
      create_task_branch
      ;;
    fromBase)
      if is_base_branch "$ORIGINAL_BRANCH"; then
        create_task_branch
      fi
      ;;
    *)
      fail "unsupported git.branch.mode: $BRANCH_MODE"
      ;;
  esac
}

load_commit_settings() {
  agent-project-settings effective >/dev/null
  COMMIT_MODE="$(setting git.commit.mode)"
}

