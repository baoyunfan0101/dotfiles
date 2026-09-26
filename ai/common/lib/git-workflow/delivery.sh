INTEGRATION_MERGE_METHOD=""
DELETE_AFTER_INTEGRATION=false
INTEGRATION_MESSAGE=""
PR_TITLE=""
PR_BODY=""
INTEGRATION_BRANCH_DELETED=false

parse_merge_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --message)
        [[ $# -ge 2 ]] || fail "missing value for --message"
        INTEGRATION_MESSAGE="$2"
        shift 2
        ;;
      --message=*)
        INTEGRATION_MESSAGE="${1#*=}"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "unknown merge option: $1"
        ;;
    esac
  done
}

delete_delivered_branch() {
  local working_branch="$1"
  local base_branch="$2"
  local remote
  local remote_deleted=false

  remote="$(remote_for_branch "$working_branch" "$base_branch")"

  if git ls-remote --exit-code --heads "$remote" "$working_branch" >/dev/null 2>&1; then
    git push --quiet "$remote" --delete "$working_branch"
    remote_deleted=true
  fi

  if [[ "$INTEGRATION_MERGE_METHOD" == "squash" ]]; then
    git branch -D "$working_branch" >/dev/null
  else
    git branch -d "$working_branch" >/dev/null
  fi

  if [[ "$remote_deleted" == true ]]; then
    INTEGRATION_BRANCH_DELETED="local-and-remote"
  else
    INTEGRATION_BRANCH_DELETED="local"
  fi
}

branch_summary() {
  local working_branch="$1"
  local base_branch="$2"
  local subject
  local description

  if [[ "$(git rev-list --count "$base_branch..$working_branch")" == 1 ]]; then
    subject="$(git log -1 --format=%s "$base_branch..$working_branch")"
    if [[ -n "$subject" ]]; then
      printf '%s\n' "$subject"
      return 0
    fi
  fi

  if [[ "$working_branch" =~ ^([[:alnum:]_]+)/([[:alnum:]_]+)-(.+)$ ]]; then
    description="${BASH_REMATCH[3]//-/ }"
    printf '%s(%s): %s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "$description"
  else
    printf '%s\n' "$working_branch"
  fi
}

integrate_local_branch() {
  local working_branch="$1"
  local base_branch="$2"
  local delivery_sha
  local merge_needed=true

  if [[ "$INTEGRATION_MERGE_METHOD" == "squash" && -z "$INTEGRATION_MESSAGE" ]]; then
    INTEGRATION_MESSAGE="$(branch_summary "$working_branch" "$base_branch")"
  fi

  git checkout -q "$base_branch"
  sync_current_branch "$base_branch"

  case "$INTEGRATION_MERGE_METHOD" in
    mergeCommit)
      if git merge-base --is-ancestor "$working_branch" HEAD; then
        merge_needed=false
      fi

      if [[ "$merge_needed" == true ]]; then
        if [[ -n "$INTEGRATION_MESSAGE" ]]; then
          git merge --quiet --no-ff -m "$INTEGRATION_MESSAGE" "$working_branch"
        else
          git merge --quiet --no-ff --no-edit "$working_branch"
        fi
      fi
      ;;
    squash)
      if git diff --quiet HEAD "$working_branch"; then
        merge_needed=false
      fi

      if [[ "$merge_needed" == true ]]; then
        git merge --quiet --squash "$working_branch"
        git commit --quiet -m "$INTEGRATION_MESSAGE"
      fi
      ;;
    *)
      fail "unsupported git.integration.mergeMethod: $INTEGRATION_MERGE_METHOD"
      ;;
  esac

  delivery_sha="$(git rev-parse HEAD)"

  if ! push_branch "$base_branch"; then
    git checkout "$working_branch" >/dev/null 2>&1 || true
    fail "integration completed locally but push failed; workflow state was preserved"
  fi

  if [[ "$DELETE_AFTER_INTEGRATION" == true ]]; then
    delete_delivered_branch "$working_branch" "$base_branch"
    clear_workflow_branch "$working_branch"
  else
    clear_workflow_branch "$working_branch"
    INTEGRATION_BRANCH_DELETED=false
  fi

  printf '[git] merge ok mode=localMerge target=%s method=%s sha=%s deleted=%s\n' \
    "$base_branch" \
    "$INTEGRATION_MERGE_METHOD" \
    "${delivery_sha:0:7}" \
    "$INTEGRATION_BRANCH_DELETED"
}

initialize_delivery() {
  local expected_mode="$1"
  preflight_core
  require_repository

  if ! project_workflow_enabled; then
    printf '[git] %s skip reason=workflow-disabled\n' "$CURRENT_ACTION"
    exit 0
  fi

  load_delivery_settings

  if [[ "$INTEGRATION_MODE" != "$expected_mode" ]]; then
    if [[ "$INTEGRATION_MODE" == pullRequest ]]; then
      fail "pullRequest mode requires git-workflow pr merge"
    fi
    fail "localMerge mode requires git-workflow merge"
  fi

  working_branch="$(current_branch)"
  base_branch="$(workflow_base_branch "$working_branch")"
  if [[ "$(workflow_branch_created "$working_branch")" != true || -z "$base_branch" || "$working_branch" == "$base_branch" ]]; then
    fail "current branch must be a workflow working branch with a known base"
  fi

  if has_local_changes; then
    fail "working tree must be clean before $CURRENT_ACTION"
  fi

  preflight_delivery
}
