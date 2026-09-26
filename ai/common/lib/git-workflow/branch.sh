BRANCH_NAME=""
BRANCH_MODE=""
BASE_BRANCHES_JSON="[]"
ORIGINAL_BRANCH=""
WORKING_BRANCH=""
BRANCH_CREATED=false

is_base_branch() {
  local branch="$1"

  require_command python3

  python3 - "$branch" "$BASE_BRANCHES_JSON" <<'PY'
import json
import sys

branch = sys.argv[1]
base_branches = json.loads(sys.argv[2])
raise SystemExit(0 if branch in base_branches else 1)
PY
}

create_working_branch() {
  if [[ -z "$BRANCH_NAME" ]]; then
    fail "--branch-name is required by the configured branch mode"
  fi

  if ! git check-ref-format --branch "$BRANCH_NAME" >/dev/null 2>&1; then
    fail "invalid branch name: $BRANCH_NAME"
  fi

  if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
    fail "branch already exists: $BRANCH_NAME"
  fi

  git checkout -q -b "$BRANCH_NAME"
  BRANCH_CREATED=true
  mark_workflow_branch "$BRANCH_NAME" "$ORIGINAL_BRANCH"
}

select_working_branch() {
  case "$BRANCH_MODE" in
    current)
      ;;
    alwaysCreate)
      create_working_branch
      ;;
    fromBase)
      if is_base_branch "$ORIGINAL_BRANCH"; then
        create_working_branch
      fi
      ;;
    *)
      fail "unsupported git.branch.mode: $BRANCH_MODE"
      ;;
  esac
}
