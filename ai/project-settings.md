# Project Settings

Project-level agent workflow behavior is configured through:

```text
<repository>/.ai/project.json
```

Settings in this file override the built-in defaults. Any omitted settings inherit their default values.

`schemaVersion` is required whenever `.ai/project.json` exists.

## Default configuration

```json
{
  "schemaVersion": 1,
  "git": {
    "sync": {
      "mode": "update",
      "updateMethod": "ffOnly"
    },
    "backup": {
      "mode": "all",
      "method": "stash"
    },
    "commit": {
      "mode": "automatic"
    },
    "branch": {
      "mode": "fromBase",
      "baseBranches": ["main"],
      "deleteAfterIntegration": false
    },
    "integration": {
      "mode": "localMerge",
      "mergeMethod": "mergeCommit",
      "pullRequest": {
        "autoMerge": false,
        "mergeMethod": "squash"
      }
    }
  }
}
```

## Settings reference

### `schemaVersion`

Configuration schema version. The only supported value is currently `1`.

## Git synchronization

### `git.sync.mode`

Controls synchronization during workflow preparation.

| Value | Meaning |
|---|---|
| `"none"` | Do not contact the remote repository. |
| `"fetch"` | Fetch without modifying the current branch. |
| `"update"` | Fetch and update the current branch from its upstream. |

Default: `"update"`.

When `"update"` is used, the current branch must have an upstream branch.

### `git.sync.updateMethod`

Controls how `git.sync.mode = "update"` updates the current branch.

| Value | Meaning |
|---|---|
| `"ffOnly"` | Require a fast-forward update. |
| `"rebase"` | Rebase local commits onto the upstream branch. |
| `"merge"` | Merge the upstream branch into the current branch. |

Default: `"ffOnly"`.

## Git backup

Before synchronization or branch selection, `git-workflow prepare` can preserve local changes and restore them afterward.

### `git.backup.mode`

| Value | Meaning |
|---|---|
| `"none"` | Do not create a backup. |
| `"tracked"` | Back up modifications to tracked files. |
| `"untracked"` | Back up untracked files. |
| `"all"` | Back up tracked and untracked changes. |

Default: `"all"`.

Ignored files are not included as untracked files.

### `git.backup.method`

| Value | Meaning |
|---|---|
| `"stash"` | Store the backup as a Git stash. |
| `"commit"` | Store the backup under `refs/agent-workflow/backups/`; use a temporary stash only for transport. |

Default: `"stash"`.

This setting has no effect when `git.backup.mode = "none"`.

## Git commits

### `git.commit.mode`

| Value | Meaning |
|---|---|
| `"manual"` | Normal workflow-driven commit and push operations are skipped. Explicit requests may use `--override-manual`. |
| `"automatic"` | Workflow commits are created normally and automatically pushed. |

Default: `"automatic"`.

A manually overridden commit is not automatically pushed.

## Git branches

### `git.branch.mode`

| Value | Meaning |
|---|---|
| `"current"` | Continue on the current branch. |
| `"alwaysCreate"` | Always create a new task branch during preparation. |
| `"fromBase"` | Create a task branch only when the current branch is listed in `git.branch.baseBranches`. |

Default: `"fromBase"`.

When the selected mode requires a branch, `git-workflow prepare` must receive `--branch-name <candidate-branch>`.

### `git.branch.baseBranches`

Defines branches treated as base branches by `"fromBase"` mode.

Default:

```json
["main"]
```

Entries must be unique, non-empty strings. The list cannot be empty when `git.branch.mode = "fromBase"`.

### `git.branch.deleteAfterIntegration`

Controls whether a workflow-created task branch is deleted after successful local integration.

Default: `false`.

This setting currently applies only to `git.integration.mode = "localMerge"`. Pull-request integration may complete asynchronously, so branch cleanup must not be treated as complete when auto-merge is merely enabled.

## Git integration

Only branches created and marked by the workflow are automatically integrated. The working tree must be clean before integration.

### `git.integration.mode`

| Value | Meaning |
|---|---|
| `"localMerge"` | Integrate locally into the original base branch and push it. |
| `"pullRequest"` | Push the task branch and create or reuse a GitHub pull request. |

Default: `"localMerge"`.

`"pullRequest"` requires GitHub CLI (`gh`) and authenticated GitHub access. `agent-doctor` checks these requirements before work begins.

### `git.integration.mergeMethod`

Controls local integration only.

| Value | Meaning |
|---|---|
| `"mergeCommit"` | Merge using a merge commit. |
| `"squash"` | Squash the task branch into one commit. |

Default: `"mergeCommit"`.

This setting does not control GitHub pull-request merge strategy.

### `git.integration.pullRequest.autoMerge`

Controls whether the workflow requests GitHub auto-merge after a pull request has been created or reused.

Default: `false`.

When set to `true`, the repository must have GitHub auto-merge enabled. The workflow does not decide that a pull request is safe to merge; GitHub branch protection and required checks remain the quality gate.

The shared workflow runs:

```bash
github-pr auto-merge
```

after `git-workflow integrate`. When this setting is `false`, the helper returns a stable skip result without modifying the pull request.

### `git.integration.pullRequest.mergeMethod`

Controls the merge method requested from GitHub when pull-request auto-merge is enabled.

| Value | Meaning |
|---|---|
| `"merge"` | Merge commit. |
| `"squash"` | Squash merge. |
| `"rebase"` | Rebase merge. |

Default: `"squash"`.

This setting is intentionally separate from `git.integration.mergeMethod` because local integration and GitHub pull-request integration are different mechanisms.

## Preflight and PR status

Run the workflow preflight before starting work:

```bash
agent-doctor
```

A successful default run emits only:

```text
[doctor] ready
```

Use `agent-doctor --verbose` to see successful individual checks. Failures include a stable check name, the setting that requires it, and a suggested fix.

For compact pull-request state, use:

```bash
github-pr status
```

instead of manually querying and interpreting multiple GitHub CLI commands.

## Inspecting effective settings

Show the complete configuration after project overrides have been merged with defaults:

```bash
agent-project-settings effective
```

Show one setting:

```bash
agent-project-settings get git.branch.mode
```
