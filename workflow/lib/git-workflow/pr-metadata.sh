PR_SYNC_STATUS=none
PR_SYNC_REASON=""
PR_SUMMARY_HELPER="$WORKFLOW_ROOT/lib/git-workflow/pr-summary.py"

render_pr_body() {
  local temp="$1"
  local base_ref="$2"
  local previous_head="$3"
  local current_head="$4"
  local existing_body="$5"
  local output_body="$6"

  : > "$temp/old-summary"
  if [[ -n "$previous_head" ]]; then
    git log --reverse --format='- %s' "$base_ref..$previous_head" > "$temp/old-summary" || return 1
  fi
  git log --reverse --format='- %s' "$base_ref..$current_head" > "$temp/new-summary" || return 1
  python3 "$PR_SUMMARY_HELPER" render "$existing_body" "$temp/old-summary" \
    "$temp/new-summary" "$output_body"
}

pr_sync_failure() {
  PR_SYNC_STATUS=failed
  PR_SYNC_REASON="$1"
}

_sync_pr_after_push() {
  local temp="$1"
  local branch="$2"
  local remote="$3"
  local previous_head="$4"
  local pr_url
  local base_branch
  local base_ref
  local remote_url

  if ! command -v gh >/dev/null 2>&1; then
    pr_sync_failure "gh is unavailable; install it and run git-workflow push to retry"
    return
  fi
  if ! git remote get-url "$remote" > "$temp/remote-url" 2> "$temp/error"; then
    pr_sync_failure "cannot resolve remote $remote for PR association"
    return
  fi
  IFS= read -r remote_url < "$temp/remote-url"
  if ! gh pr list --state open --head "$branch" --limit 1000 \
      --json url,baseRefName,body,headRepository > "$temp/prs.json" 2> "$temp/error"; then
    pr_sync_failure "cannot inspect open PRs; check gh authentication and retry git-workflow push"
    return
  fi
  if python3 "$PR_SUMMARY_HELPER" select "$temp/prs.json" \
      "$temp/existing-body" "$remote_url" > "$temp/selection" 2> "$temp/error"; then
    IFS=$'\t' read -r pr_url base_branch < "$temp/selection"
  else
    if [[ $? -eq 3 ]]; then
      PR_SYNC_STATUS=none
    else
      pr_sync_failure "$(cat "$temp/error"); resolve the PR association before retrying git-workflow push"
    fi
    return
  fi
  base_ref="refs/remotes/$remote/$base_branch"
  if ! git fetch --quiet "$remote" "refs/heads/$base_branch:$base_ref" 2> "$temp/error"; then
    pr_sync_failure "cannot fetch PR base $base_branch; retry git-workflow push"
    return
  fi
  if ! render_pr_body "$temp" "$base_ref" "$previous_head" HEAD \
      "$temp/existing-body" "$temp/updated-body" 2> "$temp/error"; then
    pr_sync_failure "$(cat "$temp/error"); repair the PR description and retry git-workflow push"
    return
  fi
  if cmp -s "$temp/existing-body" "$temp/updated-body"; then
    PR_SYNC_STATUS=unchanged
    return
  fi
  if ! gh pr edit "$pr_url" --body-file "$temp/updated-body" > /dev/null 2> "$temp/error"; then
    pr_sync_failure "could not update $pr_url; check gh access and retry git-workflow push"
    return
  fi
  PR_SYNC_STATUS=updated
}

sync_pr_after_push() {
  local branch="$1"
  local remote="$2"
  local previous_head="$3"
  local temp
  local integration_mode

  PR_SYNC_STATUS=none
  PR_SYNC_REASON=""
  [[ -n "$previous_head" ]] || return 0
  if ! integration_mode="$(setting git.integration.mode 2>/dev/null)"; then
    pr_sync_failure "cannot read integration mode"
  elif [[ "$integration_mode" != pullRequest ]]; then
    return 0
  else
    temp="$(mktemp -d 2>/dev/null || true)"
    if [[ -z "$temp" ]]; then
      pr_sync_failure "cannot create temporary PR metadata files"
    else
      _sync_pr_after_push "$temp" "$branch" "$remote" "$previous_head"
      rm -rf -- "$temp" || true
    fi
  fi
  if [[ "$PR_SYNC_STATUS" == failed ]]; then
    printf '[git] pr sync warning branch=%s reason="%s"\n' "$branch" "$PR_SYNC_REASON" >&2
  fi
  return 0
}
