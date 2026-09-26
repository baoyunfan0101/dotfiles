BRANCH_NAME=""
BRANCH_MODE=""
ORIGINAL_BRANCH=""
WORKING_BRANCH=""
BRANCH_CREATED=false

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
