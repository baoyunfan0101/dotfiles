BACKUP_MODE=""
BACKUP_METHOD=""
BACKUP_REF="none"
BACKUP_CREATED=false
BACKUP_TRANSPORT_SHA=""
BACKUP_TRANSPORT_KEEP=false

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
