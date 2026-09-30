BACKUP_MODE=""
BACKUP_METHOD=""
BACKUP_REF="none"
BACKUP_SHA=""
BACKUP_CREATED=false
BACKUP_TMP=""
BACKUP_UNTRACKED=()

selected_changes_exist() {
  case "$BACKUP_MODE" in
    none) return 1 ;;
    tracked) has_tracked_changes ;;
    untracked) has_untracked_changes ;;
    all) has_local_changes ;;
    *) fail "unsupported git.backup.mode: $BACKUP_MODE" ;;
  esac
}

require_deleted_paths_absent() {
  local path
  while IFS= read -r -d '' path; do
    [[ ! -e "$path" && ! -L "$path" ]] ||
      fail "untracked content replaces a staged deletion: $path; local changes retained"
  done < "$BACKUP_TMP/deleted"
}

build_backup_trees() {
  local path
  BACKUP_HEAD="$(git rev-parse --verify HEAD^{commit})" ||
    fail "backup requires an initial commit; local changes retained"
  BACKUP_BASE_TREE="$(git rev-parse "$BACKUP_HEAD^{tree}")"
  BACKUP_INDEX_TREE="$(git write-tree)" || fail "cannot snapshot index; local changes retained"
  BACKUP_TRACKED_TREE="$BACKUP_BASE_TREE"
  BACKUP_STASH_INDEX="$BACKUP_BASE_TREE"
  BACKUP_UNTRACKED=()
  : > "$BACKUP_TMP/untracked"

  if [[ "$BACKUP_MODE" == tracked || "$BACKUP_MODE" == all ]]; then
    git diff --cached --name-only --diff-filter=D -z > "$BACKUP_TMP/deleted"
    require_deleted_paths_absent
    GIT_INDEX_FILE="$BACKUP_TMP/index" git read-tree "$BACKUP_INDEX_TREE"
    GIT_INDEX_FILE="$BACKUP_TMP/index" git add -u -- .
    BACKUP_TRACKED_TREE="$(GIT_INDEX_FILE="$BACKUP_TMP/index" git write-tree)"
    BACKUP_STASH_INDEX="$BACKUP_INDEX_TREE"
  fi

  if [[ "$BACKUP_MODE" == untracked || "$BACKUP_MODE" == all ]]; then
    git ls-files --others --exclude-standard -z > "$BACKUP_TMP/untracked"
    while IFS= read -r -d '' path; do
      [[ ! -d "$path" || -L "$path" ]] ||
        fail "cannot back up untracked directory: $path; local changes retained"
      BACKUP_UNTRACKED+=("$path")
    done < "$BACKUP_TMP/untracked"
  fi

  GIT_INDEX_FILE="$BACKUP_TMP/untracked-index" git read-tree --empty
  if [[ -s "$BACKUP_TMP/untracked" ]]; then
    GIT_LITERAL_PATHSPECS=1 GIT_INDEX_FILE="$BACKUP_TMP/untracked-index" \
      git add --pathspec-from-file="$BACKUP_TMP/untracked" --pathspec-file-nul
  fi
  BACKUP_UNTRACKED_TREE="$(GIT_INDEX_FILE="$BACKUP_TMP/untracked-index" git write-tree)"

  GIT_INDEX_FILE="$BACKUP_TMP/index" git read-tree "$BACKUP_TRACKED_TREE"
  if [[ -s "$BACKUP_TMP/untracked" ]]; then
    GIT_LITERAL_PATHSPECS=1 GIT_INDEX_FILE="$BACKUP_TMP/index" \
      git add --pathspec-from-file="$BACKUP_TMP/untracked" --pathspec-file-nul
  fi
  BACKUP_TREE="$(GIT_INDEX_FILE="$BACKUP_TMP/index" git write-tree)"
}

create_stash_backup() {
  local index_commit untracked_commit
  local parents=()
  # Store a standard stash without clearing files before verification.
  index_commit="$(git commit-tree "$BACKUP_STASH_INDEX" -p "$BACKUP_HEAD" \
    -m "index for $BACKUP_MESSAGE")"
  parents=(-p "$BACKUP_HEAD" -p "$index_commit")
  if [[ -s "$BACKUP_TMP/untracked" ]]; then
    untracked_commit="$(git commit-tree "$BACKUP_UNTRACKED_TREE" -m "untracked for $BACKUP_MESSAGE")"
    parents+=(-p "$untracked_commit")
  fi
  BACKUP_SHA="$(git commit-tree "$BACKUP_TRACKED_TREE" "${parents[@]}" -m "On $(current_branch): $BACKUP_MESSAGE")"
  git stash store -m "On $(current_branch): $BACKUP_MESSAGE" "$BACKUP_SHA" ||
    fail "failed to store stash backup; local changes retained"
  BACKUP_REF="$BACKUP_SHA"
}

create_commit_backup() {
  BACKUP_SHA="$(git commit-tree "$BACKUP_TREE" -p "$BACKUP_HEAD" -m "$BACKUP_MESSAGE")"
  BACKUP_REF="refs/agent-workflow/backups/$BACKUP_TOKEN"
  git update-ref "$BACKUP_REF" "$BACKUP_SHA" "" ||
    fail "failed to store commit backup; local changes retained"
}

verify_backup() {
  local expected_tree="$BACKUP_TREE"
  if [[ "$BACKUP_METHOD" == stash ]]; then
    [[ "$(stash_name_for_sha "$BACKUP_SHA")" != "" ]] || return 1
    expected_tree="$BACKUP_TRACKED_TREE"
    [[ "$(git rev-parse "$BACKUP_SHA^2^{tree}")" == "$BACKUP_STASH_INDEX" ]] || return 1
    if [[ -s "$BACKUP_TMP/untracked" ]]; then
      [[ "$(git rev-parse "$BACKUP_SHA^3^{tree}")" == "$BACKUP_UNTRACKED_TREE" ]] || return 1
    fi
  else
    [[ "$(git rev-parse --verify "$BACKUP_REF")" == "$BACKUP_SHA" ]] || return 1
    [[ "$(git show -s --format=%P "$BACKUP_SHA")" == "$BACKUP_HEAD" ]] || return 1
  fi
  [[ "$(git rev-parse "$BACKUP_SHA^1")" == "$BACKUP_HEAD" ]] || return 1
  [[ "$(git rev-parse "$BACKUP_SHA^{tree}")" == "$expected_tree" ]] || return 1
}

discard_selected_changes() {
  local path
  [[ "$(git rev-parse HEAD)" == "$BACKUP_HEAD" &&
     "$(git write-tree)" == "$BACKUP_INDEX_TREE" ]] ||
    fail "HEAD or index changed during backup; local changes retained"

  if [[ "$BACKUP_MODE" == tracked || "$BACKUP_MODE" == all ]]; then
    require_deleted_paths_absent
    GIT_INDEX_FILE="$BACKUP_TMP/check-index" git read-tree "$BACKUP_INDEX_TREE"
    GIT_INDEX_FILE="$BACKUP_TMP/check-index" git add -u -- .
    [[ "$(GIT_INDEX_FILE="$BACKUP_TMP/check-index" git write-tree)" == "$BACKUP_TRACKED_TREE" ]] ||
      fail "tracked files changed during backup; local changes retained"
    GIT_INDEX_FILE="$BACKUP_TMP/check-index" git diff-files --quiet -- ||
      fail "tracked files changed during backup; local changes retained"
  fi
  if [[ -s "$BACKUP_TMP/untracked" ]]; then
    GIT_INDEX_FILE="$BACKUP_TMP/check-index" git read-tree "$BACKUP_UNTRACKED_TREE"
    GIT_INDEX_FILE="$BACKUP_TMP/check-index" git update-index --refresh >/dev/null ||
      fail "untracked files changed during backup; local changes retained"
    GIT_INDEX_FILE="$BACKUP_TMP/check-index" git diff-files --quiet -- ||
      fail "untracked files changed during backup; local changes retained"
  fi

  if [[ "$BACKUP_MODE" == tracked || "$BACKUP_MODE" == all ]]; then
    git restore --source="$BACKUP_HEAD" --staged --worktree -- . ||
      fail "could not discard tracked changes; backup retained"
  fi
  if [[ -s "$BACKUP_TMP/untracked" ]]; then
    for path in "${BACKUP_UNTRACKED[@]}"; do
      rm -f -- "$path" || fail "could not discard selected file: $path; backup retained"
    done
  fi
}

create_backup() {
  cd "$(git rev-parse --show-toplevel)"
  selected_changes_exist || return 0
  BACKUP_TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-workflow-backup.XXXXXX")"
  trap 'rm -rf -- "$BACKUP_TMP"' EXIT
  BACKUP_TOKEN="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  BACKUP_MESSAGE="agent-workflow backup $BACKUP_TOKEN"
  build_backup_trees
  case "$BACKUP_METHOD" in
    stash) create_stash_backup ;;
    commit) create_commit_backup ;;
    *) fail "unsupported git.backup.method: $BACKUP_METHOD" ;;
  esac
  verify_backup || fail "backup verification failed; local changes retained"
  BACKUP_CREATED=true
  discard_selected_changes
  rm -rf -- "$BACKUP_TMP"
  trap - EXIT
}

stash_name_for_sha() {
  local target_sha="$1"
  local stash_name stash_sha
  while read -r stash_name stash_sha; do
    if [[ "$stash_sha" == "$target_sha" ]]; then
      printf '%s\n' "$stash_name"
      return 0
    fi
  done < <(git stash list --format='%gd %H')
  return 1
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
  [[ "$1" == agent-workflow\ backup\ * ]]
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

apply_backup_id() {
  local id="$1"
  local sha
  sha="$(backup_sha_from_id "$id")"
  find_backup "$sha" || fail "backup not found: $id"
  cd "$(git rev-parse --show-toplevel)"

  if [[ "$FOUND_BACKUP_METHOD" == stash ]]; then
    git stash apply --index "$sha" || fail "backup apply failed; backup retained: $id"
    return 0
  fi

  git diff --binary "$sha^" "$sha" | git apply --3way --index ||
    fail "backup apply failed; backup retained: $id"
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
