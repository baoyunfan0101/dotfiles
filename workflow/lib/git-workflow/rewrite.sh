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
