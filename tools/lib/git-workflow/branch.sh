BRANCH_NAME=""
ORIGINAL_BRANCH=""
WORKING_BRANCH=""
BRANCH_CREATED=false

validate_branch_name() {
  local branch_name="$1"
  local branch_type

  if [[ -z "$branch_name" ]]; then
    fail "--branch-name is required"
  fi

  if ! git check-ref-format --branch "$branch_name" >/dev/null 2>&1; then
    fail "invalid branch name: $branch_name"
  fi

  if [[ "$branch_name" != */* ]]; then
    fail "branch name must use <type>/<description>: $branch_name"
  fi

  branch_type="${branch_name%%/*}"
  case "$branch_type" in
    feat|fix|chore|docs|refactor|test|ci|build|perf|style|revert|hotfix)
      ;;
    *)
      fail "invalid branch type: $branch_type"
      ;;
  esac
}

validate_new_branch() {
  local branch_name="$1"

  validate_branch_name "$branch_name"
  if branch_exists_local "$branch_name"; then
    fail "branch already exists: $branch_name"
  fi
}

branch_exists_local() {
  git show-ref --verify --quiet "refs/heads/$1"
}

branch_remote_sha() {
  local remote="$1"
  local branch_name="$2"
  local result

  result="$(git ls-remote --heads "$remote" "refs/heads/$branch_name")" ||
    fail "cannot inspect remote branch: $remote/$branch_name"
  printf '%s\n' "${result%%$'\t'*}"
}

branch_checkout_new() {
  git checkout -q -b "$1"
}

branch_create() {
  local branch_name="$1"

  validate_new_branch "$branch_name"
  branch_checkout_new "$branch_name" || fail "branch creation failed: $branch_name"
  [[ "$(current_branch)" == "$branch_name" ]] || fail "created branch verification failed: $branch_name"
}

branch_switch() {
  local branch_name="$1"

  branch_exists_local "$branch_name" || fail "local branch not found: $branch_name"
  git checkout -q "$branch_name" || fail "branch switch failed: $branch_name"
  [[ "$(current_branch)" == "$branch_name" ]] || fail "branch switch verification failed: $branch_name"
}

branch_delete_local() {
  local branch_name="$1"
  local force="${2:-false}"

  if [[ "$force" == true ]]; then
    git branch -D -- "$branch_name" >/dev/null || fail "local branch deletion failed: $branch_name"
  else
    git branch -d -- "$branch_name" >/dev/null || fail "branch is not fully merged or local deletion failed: $branch_name"
  fi
}

branch_delete_remote() {
  local remote="$1"
  local branch_name="$2"
  local expected_sha="$3"

  git push --quiet "--force-with-lease=refs/heads/$branch_name:$expected_sha" \
    "$remote" ":refs/heads/$branch_name"
}

branch_has_open_pr() {
  local branch_name="$1"
  local prs
  local count

  command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 ||
    fail "cannot verify open pull requests for remote branch"
  prs="$(gh pr list --state open --head "$branch_name" --limit 1000 --json url)" ||
    fail "cannot verify open pull requests for remote branch"
  count="$(python3 - "$prs" <<'PY'
import json
import sys
prs = json.loads(sys.argv[1])
if not isinstance(prs, list):
    raise SystemExit(1)
print(len(prs))
PY
)" || fail "cannot verify open pull requests for remote branch"
  [[ "$count" == 0 ]] || fail "current branch has an open pull request"
}

branch_remote_for_delete() {
  local branch_name="$1"
  local remote
  local candidate
  local remotes

  remote="$(git config --get "branch.$branch_name.remote" || true)"
  [[ "$remote" != "." ]] || fail "branch tracks a local branch instead of a remote"
  if [[ -z "$remote" ]]; then
    remotes="$(git remote)" || fail "cannot inspect configured remotes"
    while IFS= read -r candidate; do
      [[ -z "$candidate" ]] && continue
      [[ -z "$remote" ]] || fail "ambiguous remote for branch: $branch_name"
      remote="$candidate"
    done <<< "$remotes"
  fi
  if [[ -n "$remote" ]]; then
    git remote get-url "$remote" >/dev/null 2>&1 || fail "remote not found: $remote"
  fi
  printf '%s\n' "$remote"
}

branch_rename() {
  local old_name="$1"
  local new_name="$2"
  local remote
  local merge_ref
  local old_sha
  local new_sha
  local tracking_ref
  local tracking_refs

  validate_new_branch "$new_name"
  remote="$(git config --get "branch.$old_name.remote" || true)"
  merge_ref="$(git config --get "branch.$old_name.merge" || true)"
  BRANCH_RENAME_REMOTE=false

  if [[ -n "$remote" || -n "$merge_ref" ]]; then
    [[ -n "$remote" && "$remote" != "." && "$merge_ref" == "refs/heads/$old_name" ]] ||
      fail "unsupported upstream for branch rename: $old_name"
    git remote get-url "$remote" >/dev/null 2>&1 || fail "remote not found: $remote"
    old_sha="$(branch_remote_sha "$remote" "$old_name")"
    [[ -n "$old_sha" ]] || fail "upstream remote branch not found: $remote/$old_name"
    git merge-base --is-ancestor "$old_sha" HEAD ||
      fail "remote branch has commits absent from local history: $remote/$old_name"
    new_sha="$(branch_remote_sha "$remote" "$new_name")"
    [[ -z "$new_sha" ]] ||
      fail "remote branch already exists: $remote/$new_name"
    branch_has_open_pr "$old_name"
  else
    tracking_refs="$(git for-each-ref --format='%(refname)' refs/remotes)" ||
      fail "cannot inspect remote tracking branches"
    while IFS= read -r tracking_ref; do
      if [[ "$tracking_ref" == refs/remotes/*/"$old_name" ]]; then
        fail "remote branch exists without an upstream: $tracking_ref"
      fi
    done <<< "$tracking_refs"
    remote=""
  fi

  git branch -m "$new_name" || fail "local branch rename failed: $old_name"
  if [[ -n "$remote" ]]; then
    if ! git push --quiet --set-upstream "$remote" "refs/heads/$new_name:refs/heads/$new_name"; then
      new_sha="$(branch_remote_sha "$remote" "$new_name")" ||
        fail "remote push failed; new remote branch state could not be verified"
      if [[ -z "$new_sha" ]]; then
        git branch -m "$old_name" || fail "remote push failed and local rollback failed"
        git config "branch.$old_name.remote" "$remote"
        git config "branch.$old_name.merge" "$merge_ref"
        fail "remote push failed; original branch restored"
      fi
      fail "remote push failed after new branch appeared; old remote branch was preserved"
    fi
    [[ "$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)" == "$remote/$new_name" ]] ||
      fail "remote rename partial: new upstream could not be verified; old remote branch was preserved"
    branch_delete_remote "$remote" "$old_name" "$old_sha" ||
      fail "remote rename partial: old remote branch deletion failed"
    old_sha="$(branch_remote_sha "$remote" "$old_name")" ||
      fail "remote rename partial: old remote branch state could not be verified"
    [[ -z "$old_sha" ]] ||
      fail "remote rename partial: old remote branch remains"
    new_sha="$(branch_remote_sha "$remote" "$new_name")" ||
      fail "remote rename partial: new remote branch state could not be verified"
    [[ "$new_sha" == "$(git rev-parse HEAD)" ]] ||
      fail "remote rename partial: new remote branch verification failed"
    BRANCH_RENAME_REMOTE=true
  fi
  [[ "$(current_branch)" == "$new_name" ]] || fail "branch rename verification failed: $new_name"
}
