BACKUP_MODE=""
BACKUP_METHOD=""
BACKUP_REF="none"
BACKUP_SHA=""
BACKUP_CREATED=false
BACKUP_TRANSPORT_SHA=""
BACKUP_TRANSPORT_KEEP=false
BACKUP_OWNER=prepare

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

      git stash push -m "$message" >/dev/null || return 1
      ;;
    untracked)
      while IFS= read -r -d '' path; do
        untracked_paths+=("$path")
        ((untracked_count += 1))
      done < <(git ls-files --others --exclude-standard -z)

      if ((untracked_count == 0)); then
        return 0
      fi

      git stash push -u -m "$message" -- "${untracked_paths[@]}" >/dev/null || return 1
      ;;
    all)
      if ! has_local_changes; then
        return 0
      fi

      git stash push -u -m "$message" >/dev/null || return 1
      ;;
    *)
      fail "unsupported git.backup.mode: $BACKUP_MODE"
      ;;
  esac

  BACKUP_TRANSPORT_SHA="$(git rev-parse --verify refs/stash)"
}

create_stash_backup() {
  local message

  message="agent-workflow backup $BACKUP_OWNER $(date -u +%Y-%m-%dT%H:%M:%SZ)-$$"
  stash_selected_changes "$message" || fail "failed to create stash backup"

  if [[ -z "$BACKUP_TRANSPORT_SHA" ]]; then
    return 0
  fi

  BACKUP_REF="$BACKUP_TRANSPORT_SHA"
  BACKUP_SHA="$BACKUP_TRANSPORT_SHA"
  BACKUP_CREATED=true
  BACKUP_TRANSPORT_KEEP=true
}

create_commit_backup() {
  local timestamp
  local backup_ref
  local message
  local tmp_index
  local tree
  local snapshot_sha
  local untracked_paths=()
  local untracked_count=0
  local path

  if ! selected_changes_exist; then
    return 0
  fi

  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_ref="refs/agent-workflow/backups/${timestamp}-$$"
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
  snapshot_sha="$(printf 'agent-workflow backup %s %s-%s\n' "$BACKUP_OWNER" "$timestamp" "$$" |
    git commit-tree "$tree" -p HEAD)"
  git update-ref "$backup_ref" "$snapshot_sha"
  rm -f "$tmp_index"
  BACKUP_REF="$backup_ref"
  BACKUP_SHA="$snapshot_sha"
  BACKUP_CREATED=true

  message="agent-workflow transport $BACKUP_OWNER $(date -u +%Y-%m-%dT%H:%M:%SZ)-$$"
  stash_selected_changes "$message" || fail "failed to create transport stash; backup retained"

  if [[ -z "$BACKUP_TRANSPORT_SHA" ]]; then
    fail "failed to create temporary transport stash for commit backup"
  fi

  git update-ref "$backup_ref" "$BACKUP_TRANSPORT_SHA" "$snapshot_sha" ||
    fail "failed to retain commit backup; transport stash retained"
  BACKUP_SHA="$BACKUP_TRANSPORT_SHA"
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

  git stash apply --index "$BACKUP_TRANSPORT_SHA" >/dev/null || return $?

  if [[ "$BACKUP_TRANSPORT_KEEP" == false ]]; then
    stash_name="$(stash_name_for_sha "$BACKUP_TRANSPORT_SHA" || true)"

    [[ -n "$stash_name" ]] || return 1
    git stash drop "$stash_name" >/dev/null || return $?
  fi
}

backup_id() {
  printf 'b-%s\n' "$1"
}

backup_sha_from_id() {
  local id="$1"
  if [[ "$id" =~ ^b-([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  else
    fail "invalid backup id: $id"
  fi
}

workflow_stash_subject() {
  [[ "$1" == *": agent-workflow backup "* ]]
}

workflow_ref_subject() {
  [[ "$1" == agent-workflow\ backup\ * ||
     "$1" == *": agent-workflow transport "* ]]
}

find_backup() {
  local target_sha="$1"
  local ref sha created subject

  FOUND_BACKUP_METHOD=""
  FOUND_BACKUP_REF=""
  FOUND_BACKUP_STASH=""
  while IFS=$'\t' read -r ref sha created; do
    [[ "$sha" == "$target_sha" ]] || continue
    subject="$(git show -s --format=%s "$sha")"
    workflow_ref_subject "$subject" || continue
    [[ -z "$FOUND_BACKUP_METHOD" ]] || fail "ambiguous backup id: $(backup_id "$target_sha")"
    FOUND_BACKUP_METHOD=commit
    FOUND_BACKUP_REF="$ref"
  done < <(git for-each-ref --format='%(refname)%09%(objectname)%09%(creatordate:iso-strict)' \
    refs/agent-workflow/backups/)

  while IFS=$'\t' read -r ref sha subject created; do
    [[ "$sha" == "$target_sha" ]] || continue
    workflow_stash_subject "$subject" || continue
    [[ -z "$FOUND_BACKUP_METHOD" ]] || fail "ambiguous backup id: $(backup_id "$target_sha")"
    FOUND_BACKUP_METHOD=stash
    FOUND_BACKUP_STASH="$ref"
  done < <(git stash list --format='%gd%x09%H%x09%gs%x09%cI')

  [[ -n "$FOUND_BACKUP_METHOD" ]]
}

list_backups() {
  local ref sha created subject
  local count=0

  while IFS=$'\t' read -r ref sha created; do
    subject="$(git show -s --format=%s "$sha")"
    workflow_ref_subject "$subject" || continue
    printf '[git] backup list item id=%s method=commit created=%s\n' \
      "$(backup_id "$sha")" "$created"
    ((count += 1))
  done < <(git for-each-ref --format='%(refname)%09%(objectname)%09%(creatordate:iso-strict)' \
    refs/agent-workflow/backups/)

  while IFS=$'\t' read -r ref sha subject created; do
    workflow_stash_subject "$subject" || continue
    printf '[git] backup list item id=%s method=stash created=%s\n' \
      "$(backup_id "$sha")" "$created"
    ((count += 1))
  done < <(git stash list --format='%gd%x09%H%x09%gs%x09%cI')

  printf '[git] backup list ok count=%s\n' "$count"
}

restore_backup_id() {
  local id="$1"
  local sha subject
  sha="$(backup_sha_from_id "$id")"
  find_backup "$sha" || fail "backup not found: $id"

  if [[ "$FOUND_BACKUP_METHOD" == stash ]]; then
    git stash apply --index "$sha" || fail "backup restore failed; backup retained: $id"
    return 0
  fi

  subject="$(git show -s --format=%s "$sha")"
  if [[ "$subject" == *": agent-workflow transport "* ]]; then
    git stash apply --index "$sha" || fail "backup restore failed; backup retained: $id"
  else
    git diff --binary "$sha^" "$sha" | git apply --3way --index ||
      fail "backup restore failed; backup retained: $id"
  fi
}

delete_backup_id() {
  local id="$1"
  local sha current_name
  sha="$(backup_sha_from_id "$id")"
  find_backup "$sha" || fail "backup not found: $id"

  if [[ "$FOUND_BACKUP_METHOD" == stash ]]; then
    current_name="$(stash_name_for_sha "$sha" || true)"
    [[ "$current_name" == "$FOUND_BACKUP_STASH" ]] || fail "backup position changed: $id"
    git stash drop "$current_name" >/dev/null || fail "backup deletion failed: $id"
  else
    git update-ref -d "$FOUND_BACKUP_REF" "$sha" || fail "backup deletion failed: $id"
  fi

  if find_backup "$sha"; then
    fail "backup deletion could not be verified: $id"
  fi
}

cleanup_prepare_backup() {
  if [[ "$BACKUP_CREATED" == true && "$BACKUP_DELETE_AFTER_RESTORE" == true ]]; then
    delete_backup_id "$(backup_id "$BACKUP_SHA")" ||
      fail "restoration succeeded but backup cleanup failed; backup retained"
  fi
}
