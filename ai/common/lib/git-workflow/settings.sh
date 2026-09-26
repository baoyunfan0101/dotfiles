WORKFLOW_ENABLED=""
COMMIT_MODE=""
INTEGRATION_MODE=""
BASE_BRANCHES_JSON="[]"

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

setting() {
  agent-project get "$1"
}

load_prepare_settings() {
  BACKUP_MODE="$(setting git.backup.mode)"
  BACKUP_METHOD="$(setting git.backup.method)"
  SYNC_MODE="$(setting git.sync.mode)"
  SYNC_UPDATE_METHOD="$(setting git.sync.updateMethod)"
  BRANCH_MODE="$(setting git.branch.mode)"
  BASE_BRANCHES_JSON="$(setting git.branch.baseBranches)"
}

load_commit_settings() {
  COMMIT_MODE="$(setting git.commit.mode)"
}

load_delivery_settings() {
  BASE_BRANCHES_JSON="$(setting git.branch.baseBranches)"
  SYNC_MODE="$(setting git.sync.mode)"
  SYNC_UPDATE_METHOD="$(setting git.sync.updateMethod)"
  INTEGRATION_MODE="$(setting git.integration.mode)"
  INTEGRATION_MERGE_METHOD="$(setting git.integration.mergeMethod)"
  DELETE_AFTER_INTEGRATION="$(setting git.branch.deleteAfterIntegration)"
}
