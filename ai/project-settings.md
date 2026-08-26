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
      "mergeMethod": "mergeCommit"
    }
  }
}
```

## Settings reference

### `schemaVersion`

Configuration schema version.

Default:

```json
1
```

Supported values:

| Value | Meaning |
|---|---|
| `1` | Current project settings schema. |

The field is required when `.ai/project.json` exists.

## Git synchronization

### `git.sync.mode`

Controls how the current branch is synchronized during workflow preparation.

Default:

```json
"update"
```

Supported values:

| Value | Meaning |
|---|---|
| `"none"` | Do not contact or synchronize with the remote repository. |
| `"fetch"` | Fetch from the remote without modifying the current branch. |
| `"update"` | Fetch from the remote and update the current branch from its upstream according to `git.sync.updateMethod`. |

When `"update"` is used, the current branch must have an upstream branch.

### `git.sync.updateMethod`

Controls how the current branch is updated from its upstream when:

```json
"git.sync.mode": "update"
```

Default:

```json
"ffOnly"
```

Supported values:

| Value | Meaning |
|---|---|
| `"ffOnly"` | Update only when a fast-forward merge is possible. The workflow fails if the local and upstream histories have diverged. |
| `"rebase"` | Rebase local commits onto the upstream branch. |
| `"merge"` | Merge the upstream branch into the current branch. |

This setting has no effect when `git.sync.mode` is `"none"` or `"fetch"`.

## Git backup

Before synchronization or branch selection, `git-workflow prepare` can preserve local changes and then restore them afterward.

### `git.backup.mode`

Controls which local changes are included in the backup.

Default:

```json
"all"
```

Supported values:

| Value | Meaning |
|---|---|
| `"none"` | Do not create a backup. |
| `"tracked"` | Back up modifications to tracked files. |
| `"untracked"` | Back up untracked files. |
| `"all"` | Back up both tracked changes and untracked files. |

Ignored files are not included as untracked files.

### `git.backup.method`

Controls how the selected local changes are preserved.

Default:

```json
"stash"
```

Supported values:

| Value | Meaning |
|---|---|
| `"stash"` | Store the backup as a Git stash. The backup stash remains available after the changes are reapplied. |
| `"commit"` | Store the backup as a commit referenced under `refs/agent-workflow/backups/`. A temporary stash is used only to transport the working-tree changes while the workflow prepares the repository. |

This setting has no effect when:

```json
"git.backup.mode": "none"
```

## Git commits

### `git.commit.mode`

Controls whether the agent workflow automatically commits and pushes completed changes.

Default:

```json
"automatic"
```

Supported values:

| Value | Meaning |
|---|---|
| `"manual"` | Normal workflow-driven commit and push operations are skipped. Explicitly requested commits or pushes can still be performed with the workflow's manual override. |
| `"automatic"` | Workflow commits are created normally, and each successful workflow commit is automatically pushed. |

In `"manual"` mode, an explicitly requested commit can be performed with:

```bash
git-workflow commit \
  --override-manual \
  --message "<message>" \
  -- <paths>...
```

An explicitly requested push can be performed with:

```bash
git-workflow push --override-manual
```

A manually overridden commit is not automatically pushed.

## Git branches

### `git.branch.mode`

Controls whether workflow preparation creates a task branch.

Default:

```json
"fromBase"
```

Supported values:

| Value | Meaning |
|---|---|
| `"current"` | Continue working on the current branch. Never create a task branch automatically. |
| `"alwaysCreate"` | Always create a new task branch during workflow preparation. |
| `"fromBase"` | Create a new task branch only when the current branch is listed in `git.branch.baseBranches`. Otherwise continue on the current branch. |

When the selected mode requires a new branch, `git-workflow prepare` must receive a candidate branch name:

```bash
git-workflow prepare --branch-name <candidate-branch>
```

Branches created by the workflow are marked with Git configuration metadata so that they can later be recognized and integrated by `git-workflow integrate`.

### `git.branch.baseBranches`

Defines the branches treated as base branches by `"fromBase"` mode.

Default:

```json
["main"]
```

Example:

```json
["main", "develop"]
```

Each entry must be a unique, non-empty string.

When:

```json
"git.branch.mode": "fromBase"
```

the list must not be empty.

This setting primarily affects `git.branch.mode = "fromBase"`.

### `git.branch.deleteAfterIntegration`

Controls whether a workflow-created task branch is deleted after successful local integration.

Default:

```json
false
```

Supported values:

| Value | Meaning |
|---|---|
| `false` | Keep the task branch after integration. |
| `true` | Delete the integrated task branch locally and delete its remote branch when the remote branch exists. |

This setting applies to:

```json
"git.integration.mode": "localMerge"
```

When squash integration is used, the local task branch is force-deleted because its commits are not direct ancestors of the resulting squash commit.

## Git integration

Only branches created and marked by the workflow are automatically integrated.

If the current branch was not created by `git-workflow prepare`, `git-workflow integrate` skips integration.

The working tree must be clean before integration.

### `git.integration.mode`

Controls how a workflow-created task branch is integrated.

Default:

```json
"localMerge"
```

Supported values:

| Value | Meaning |
|---|---|
| `"localMerge"` | Check out the original base branch, synchronize it, integrate the task branch locally, and push the resulting base branch. |
| `"pullRequest"` | Push the task branch and create or reuse a GitHub pull request targeting the original base branch. |

`"pullRequest"` mode requires the GitHub CLI (`gh`).

A pull request title must be supplied to `git-workflow integrate`.

Example:

```bash
git-workflow integrate \
  --title "Add project settings documentation" \
  --body "Document all supported project settings."
```

### `git.integration.mergeMethod`

Controls how a task branch is integrated when:

```json
"git.integration.mode": "localMerge"
```

Default:

```json
"mergeCommit"
```

Supported values:

| Value | Meaning |
|---|---|
| `"mergeCommit"` | Merge the task branch using a merge commit. Fast-forward-only integration is not used. |
| `"squash"` | Squash all changes from the task branch into a single commit on the base branch. |

For `"mergeCommit"`, an integration message is optional. If omitted, Git generates the normal merge commit message.

Example:

```bash
git-workflow integrate \
  --message "Merge project settings documentation"
```

For `"squash"`, an integration message is required because the workflow must create the resulting squash commit.

Example:

```bash
git-workflow integrate \
  --message "docs(ai): document project settings"
```

This setting does not control how a GitHub pull request is ultimately merged when `git.integration.mode` is `"pullRequest"`.

## Inspecting effective settings

Show the complete configuration after project overrides have been merged with the defaults:

```bash
agent-project-settings effective
```

Show one setting:

```bash
agent-project-settings get git.branch.mode
```

For example:

```bash
agent-project-settings get git.backup.mode
```

prints the effective value of `git.backup.mode`.
