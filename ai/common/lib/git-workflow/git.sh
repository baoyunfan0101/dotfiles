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
