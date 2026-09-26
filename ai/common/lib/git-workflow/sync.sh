SYNC_MODE=""
SYNC_UPDATE_METHOD=""
SYNC_RESULT="skipped"

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
      git fetch --quiet "$remote" || return $?
      SYNC_RESULT="fetched"
      ;;
    update)
      remote="$(remote_for_branch "$branch")"
      upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"

      if [[ -z "$upstream" ]]; then
        fail "current branch has no upstream: $branch"
      fi

      git fetch --quiet "$remote" || return $?

      case "$SYNC_UPDATE_METHOD" in
        ffOnly)
          git merge --quiet --ff-only "$upstream" || return $?
          ;;
        rebase)
          git rebase --quiet "$upstream" || return $?
          ;;
        merge)
          git merge --quiet --no-edit "$upstream" || return $?
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
