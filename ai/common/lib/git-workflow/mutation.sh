source "$WORKFLOW_ROOT/lib/git-workflow/pr-metadata.sh"

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

remote_branch_sha() {
  local remote="$1"
  local branch="$2"

  git ls-remote --heads "$remote" "refs/heads/$branch" | cut -f1
}

verify_remote_ancestor() {
  local remote_sha="$1"
  local original_head="$2"

  if [[ -n "$remote_sha" ]] && ! git merge-base --is-ancestor "$remote_sha" "$original_head"; then
    fail "remote branch has commits absent from local history"
  fi
}

rewrite_ref() {
  printf 'refs/git-workflow/rewrite/%s/%s/%s\n' "$1" "$2" "$3"
}

pending_rewrite() {
  local branch="$1"
  local remote="$2"
  local expected_ref head_ref

  expected_ref="$(rewrite_ref expected "$remote" "$branch")"
  head_ref="$(rewrite_ref head "$remote" "$branch")"
  REWRITE_EXPECTED="$(git rev-parse --verify "$expected_ref" 2>/dev/null || true)"
  REWRITE_HEAD="$(git rev-parse --verify "$head_ref" 2>/dev/null || true)"
  [[ -z "$REWRITE_EXPECTED" && -z "$REWRITE_HEAD" ]] && return 1
  [[ -n "$REWRITE_EXPECTED" && -n "$REWRITE_HEAD" ]] || fail "incomplete rewrite record for $branch"
}

verify_rewrite_source() {
  local branch="$1"
  local remote="$2"
  local remote_sha="$3"
  local original_head="$4"

  if pending_rewrite "$branch" "$remote"; then
    [[ "$REWRITE_EXPECTED" == "$remote_sha" ]] || fail "remote branch changed since local rewrite"
    git merge-base --is-ancestor "$REWRITE_HEAD" "$original_head" ||
      fail "local branch diverged from recorded rewrite"
  else
    verify_remote_ancestor "$remote_sha" "$original_head"
  fi
}

record_rewrite() {
  local branch="$1"
  local remote="$2"
  local remote_sha="$3"
  local new_head="$4"
  local expected_ref head_ref expected

  [[ -n "$remote_sha" ]] || return 0
  if git merge-base --is-ancestor "$remote_sha" "$new_head"; then
    clear_rewrite "$branch" "$remote"
    return 0
  fi
  expected_ref="$(rewrite_ref expected "$remote" "$branch")"
  head_ref="$(rewrite_ref head "$remote" "$branch")"
  expected="$remote_sha"
  if pending_rewrite "$branch" "$remote"; then
    expected="$REWRITE_EXPECTED"
  fi
  printf 'update %s %s\nupdate %s %s\n' \
    "$expected_ref" "$expected" "$head_ref" "$new_head" |
    git update-ref --stdin || fail "rewritten commit created but rewrite record failed"
}

clear_rewrite() {
  local branch="$1"
  local remote="$2"
  local expected_ref head_ref

  expected_ref="$(rewrite_ref expected "$remote" "$branch")"
  head_ref="$(rewrite_ref head "$remote" "$branch")"
  printf 'delete %s\ndelete %s\n' "$expected_ref" "$head_ref" |
    git update-ref --stdin 2>/dev/null || true
}

publish_rewrite() {
  local branch="$1"
  local remote="$2"
  local previous_remote_sha="$3"

  if [[ -n "$previous_remote_sha" ]]; then
    [[ "$(remote_branch_sha "$remote" "$branch")" == "$previous_remote_sha" ]] ||
      fail "remote branch changed since local rewrite"
  fi
  if [[ -n "$previous_remote_sha" ]] &&
      ! git merge-base --is-ancestor "$previous_remote_sha" HEAD; then
    git push --quiet "--force-with-lease=refs/heads/$branch:$previous_remote_sha" \
      "$remote" "$branch:refs/heads/$branch" || return 1
  else
    push_branch "$branch" || return 1
  fi
  clear_rewrite "$branch" "$remote"
  sync_pr_after_push "$branch" "$remote" "$previous_remote_sha"
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
