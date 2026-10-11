source "$WORKFLOW_ROOT/lib/git-workflow/pr-metadata.sh"
source "$WORKFLOW_ROOT/lib/git-workflow/rewrite.sh"

require_clean_worktree() {
  if has_local_changes; then
    fail "working tree must be clean before $CURRENT_ACTION"
  fi
}

restore_failed_amend_index() {
  local original_index_tree="$1"

  git read-tree "$original_index_tree" || fail "amend failed and index recovery failed"
  fail "amend failed; original index was restored"
}

require_working_branch() {
  local branch="$1"

  BASE_BRANCHES_JSON="$(setting git.branch.baseBranches)"
  if is_base_branch "$branch"; then
    fail "cannot $CURRENT_ACTION on a configured base branch: $branch"
  fi
}

resolve_configured_base() {
  local requested="$1"
  local resolved

  if ! resolved="$(python3 - "$BASE_BRANCHES_JSON" "$requested" <<'PY'
import json
import sys

bases = json.loads(sys.argv[1])
requested = sys.argv[2]
if not bases:
    print("no configured base branches in git.branch.baseBranches")
    sys.exit(1)
if requested and requested not in bases:
    print("base is not configured in git.branch.baseBranches: " + requested)
    sys.exit(1)
if not requested and len(bases) != 1:
    print("ambiguous base; specify --base BRANCH")
    sys.exit(1)
print(requested or bases[0])
PY
)"; then
    fail "$resolved"
  fi
  printf '%s\n' "$resolved"
}

push_working_branch() {
  local branch="$1"
  local remote
  local previous_remote_sha

  BASE_BRANCHES_JSON="$(setting git.branch.baseBranches)" || return 1
  if is_base_branch "$branch"; then
    PR_SYNC_STATUS=none
    push_branch "$branch"
    return
  fi
  remote="$(remote_for_branch "$branch")" || return 1
  previous_remote_sha="$(remote_branch_sha "$remote" "$branch")" || return 1
  push_branch "$branch" || return 1
  sync_pr_after_push "$branch" "$remote" "$previous_remote_sha"
}

restore_failed_sequence() {
  local operation="$1"
  local branch="$2"
  local original_head="$3"

  git "$operation" --abort || fail "$operation failed and abort failed"
  [[ "$(current_branch)" == "$branch" ]] || fail "$operation recovery failed: branch changed"
  if [[ "$(git rev-parse HEAD)" != "$original_head" ]]; then
    git reset --hard --quiet "$original_head" || fail "$operation recovery failed: HEAD changed"
  fi
  if [[ -n "$(git ls-files --unmerged)" ]] ||
      git rev-parse --quiet --verify REVERT_HEAD >/dev/null 2>&1 ||
      git rev-parse --quiet --verify CHERRY_PICK_HEAD >/dev/null 2>&1 ||
      git rev-parse --quiet --verify MERGE_HEAD >/dev/null 2>&1 ||
      [[ -d "$(git rev-parse --git-path rebase-merge)" ||
         -d "$(git rev-parse --git-path rebase-apply)" ]]; then
    fail "$operation recovery failed: Git operation state remains"
  fi
  fail "$operation failed; original branch and HEAD were restored"
}
