COMMIT_MODE=""
INTEGRATION_MODE=""

setting() {
  agent-project get "$1"
}

load_start_settings() {
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

load_finish_settings() {
  SYNC_MODE="$(setting git.sync.mode)"
  SYNC_UPDATE_METHOD="$(setting git.sync.updateMethod)"
  INTEGRATION_MODE="$(setting git.integration.mode)"
  INTEGRATION_MERGE_METHOD="$(setting git.integration.mergeMethod)"
  DELETE_AFTER_INTEGRATION="$(setting git.branch.deleteAfterIntegration)"
}
