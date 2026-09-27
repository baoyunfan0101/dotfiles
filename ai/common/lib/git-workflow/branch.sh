BRANCH_NAME=""
BRANCH_MODE=""
ORIGINAL_BRANCH=""
WORKING_BRANCH=""
BRANCH_CREATED=false

validate_branch_name() {
  local branch_name="$1"
  local branch_type

  if [[ -z "$branch_name" ]]; then
    fail "--branch-name is required by the configured branch mode"
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
  validate_branch_name "$BRANCH_NAME"
  if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
    fail "branch already exists: $BRANCH_NAME"
  fi
}

create_working_branch() {
  validate_new_branch
  git checkout -q -b "$BRANCH_NAME"
  BRANCH_CREATED=true
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
