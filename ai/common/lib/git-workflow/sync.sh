SYNC_MODE=""
SYNC_UPDATE_METHOD=""
SYNC_RESULT="skipped"

fetch_base_for_entry() {
  local base_branch="$1"
  local remote="$2"
  local base_ref="refs/remotes/$remote/$base_branch"

  git fetch --quiet "$remote" "+refs/heads/$base_branch:$base_ref" || return $?
  git rev-parse --verify "$base_ref^{commit}" >/dev/null || return $?
  printf '%s\n' "$base_ref"
}

advance_base_for_entry() {
  local base_branch="$1"
  local base_ref="$2"

  if ! git merge-base --is-ancestor HEAD "$base_ref"; then
    printf 'local base diverged from fetched remote base: %s\n' "$base_branch" >&2
    return 1
  fi
  git merge --quiet --ff-only "$base_ref" || return $?
  [[ "$(git rev-parse HEAD)" == "$(git rev-parse "$base_ref")" ]] || return 1
  SYNC_RESULT="updated"
}

recover_failed_sync() {
  local method="$1"
  local branch="$2"
  local original_head="$3"

  case "$method" in
    rebase)
      if [[ -d "$(git rev-parse --git-path rebase-merge)" ||
            -d "$(git rev-parse --git-path rebase-apply)" ]]; then
        git rebase --abort || fail "sync recovery failed: could not abort rebase"
      fi
      ;;
    merge)
      if git rev-parse --quiet --verify MERGE_HEAD >/dev/null; then
        git merge --abort || fail "sync recovery failed: could not abort merge"
      fi
      ;;
  esac

  [[ "$(current_branch)" == "$branch" ]] || fail "sync recovery failed: original branch was not restored"
  if [[ "$(git rev-parse HEAD)" != "$original_head" ]]; then
    git reset --hard --quiet "$original_head" || fail "sync recovery failed: original HEAD was not restored"
  fi
  if [[ -d "$(git rev-parse --git-path rebase-merge)" ||
        -d "$(git rev-parse --git-path rebase-apply)" ]] ||
      git rev-parse --quiet --verify MERGE_HEAD >/dev/null ||
      [[ -n "$(git ls-files --unmerged)" ]]; then
    fail "sync recovery failed: Git operation state remains"
  fi
}

sync_current_branch() {
  local branch="$1"
  local remote
  local upstream
  local original_head
  local configured_remote=""
  local configured_merge=""
  local has_remote=false
  local has_merge=false
  local config_status

  case "$SYNC_MODE" in
    none)
      SYNC_RESULT="skipped"
      ;;
    fetch)
      remote="$(remote_for_branch "$branch")" || return $?
      git fetch --quiet "$remote" || return $?
      SYNC_RESULT="fetched"
      ;;
    update)
      if configured_remote="$(git config --get "branch.$branch.remote")"; then
        has_remote=true
      else
        config_status=$?
        [[ "$config_status" == 1 ]] || return "$config_status"
      fi
      if configured_merge="$(git config --get "branch.$branch.merge")"; then
        has_merge=true
      else
        config_status=$?
        [[ "$config_status" == 1 ]] || return "$config_status"
      fi

      if [[ "$has_remote" == false && "$has_merge" == false ]]; then
        SYNC_RESULT="skipped"
        return 0
      fi
      if [[ "$has_remote" != true || "$has_merge" != true ||
            -z "$configured_remote" || "$configured_remote" == "." ||
            "$configured_merge" != refs/heads/?* ]]; then
        printf 'invalid upstream configuration for %s; set both branch remote and merge ref\n' "$branch" >&2
        return 1
      fi
      remote="$configured_remote"
      if ! git remote get-url "$remote" >/dev/null 2>&1; then
        printf 'upstream remote is unavailable for %s: %s\n' "$branch" "$remote" >&2
        return 1
      fi
      if ! upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
        printf 'upstream branch is unavailable for %s; check branch tracking configuration\n' "$branch" >&2
        return 1
      fi

      git fetch --quiet "$remote" || return $?
      original_head="$(git rev-parse HEAD)" || return $?

      case "$SYNC_UPDATE_METHOD" in
        ffOnly)
          git merge --quiet --ff-only "$upstream" || return $?
          ;;
        rebase)
          if ! git rebase --quiet "$upstream"; then
            recover_failed_sync rebase "$branch" "$original_head"
            return 1
          fi
          ;;
        merge)
          if ! git merge --quiet --no-edit "$upstream"; then
            recover_failed_sync merge "$branch" "$original_head"
            return 1
          fi
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
