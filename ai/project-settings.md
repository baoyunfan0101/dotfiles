# Project Settings

This reference describes project-specific behavior for `start`, `commit`, and `finish`, plus the auxiliary `push` command. Project overrides are stored in:

```text
<repository>/.ai/project.json
```

The file is optional. Missing settings inherit built-in defaults, and the workflow is disabled by default. `schemaVersion` is required whenever `.ai/project.json` exists.

## Manage project settings

Show the complete effective configuration or one value:

```bash
agent-project effective
agent-project get workflow.enabled
agent-project get git.branch.mode
```

Set an override:

```bash
agent-project set workflow.enabled true
agent-project set git.integration.mode pullRequest
```

`set` validates the value against the project schema and creates `.ai/project.json` when needed.

Remove an override and return to its built-in default:

```bash
agent-project unset git.integration.mode
```

`project.json` stores only explicit overrides plus `schemaVersion`; `effective` shows the result after merging those overrides with built-in defaults.

## Default configuration

```json
{
  "schemaVersion": 1,
  "workflow": {
    "enabled": false
  },
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

| Area | Purpose |
|---|---|
| `workflow` | Whether this repository uses `git-workflow`. |
| `git.sync` | Repository synchronization during `start` and local-merge `finish`. |
| `git.backup` | Protect local changes during `start`. |
| `git.commit` | Commit and push policy. |
| `git.branch` | Task-branch policy. |
| `git.integration` | Integration strategy used by `finish`. |

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

## Workflow activation

### `workflow.enabled`

Controls whether the repository uses `git-workflow`.

Default:

```json
false
```

Supported values:

| Value | Meaning |
|---|---|
| `false` | Workflow commands skip without performing task workflow operations. |
| `true` | Repository-changing tasks use the configured `start -> commit* -> finish` lifecycle. |

Enable the workflow for the current repository:

```bash
agent-project set workflow.enabled true
```

Disable it explicitly:

```bash
agent-project set workflow.enabled false
```

Remove the project override and return to the default `false`:

```bash
agent-project unset workflow.enabled
```

When disabled, `git-workflow start`, `commit`, `push`, and `finish` return `skip reason=workflow-disabled`. Delivery-specific checks such as GitHub CLI authentication are not performed.

## Git synchronization

### `git.sync.mode`

Controls synchronization of the current branch during `start` and the base branch during a local-merge `finish`.

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

Before synchronization or branch selection, `git-workflow start` can preserve local changes and then restore them afterward.

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
| `"commit"` | Store the backup as a commit referenced under `refs/agent-workflow/backups/`. A temporary stash transports working-tree changes during `git-workflow start`. |

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

Controls whether `git-workflow start` creates a task branch.

Default:

```json
"fromBase"
```

Supported values:

| Value | Meaning |
|---|---|
| `"current"` | Continue working on the current branch. Never create a task branch automatically. |
| `"alwaysCreate"` | Always create a new task branch during `git-workflow start`. |
| `"fromBase"` | Create a new task branch only when the current branch is listed in `git.branch.baseBranches`. Otherwise continue on the current branch. |

When the selected mode requires a new branch, `git-workflow start` must receive a candidate branch name:

```bash
git-workflow start --branch-name <candidate-branch>
```

Branches created by `git-workflow start` are marked with Git configuration metadata so that `git-workflow finish` can recognize the task and its base branch.

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

## Finish integration strategy

The normal task-completion command is:

```bash
git-workflow finish
```

`finish` selects `localMerge` or `pullRequest` through `git.integration.mode`. These are strategies for the same lifecycle action.

If the current branch was not created by `git-workflow start`, `git-workflow finish` skips the task.

The working tree must be clean when `finish` has a task to deliver.

### `git.integration.mode`

Selects the integration strategy used by `git-workflow finish`.

Default:

```json
"localMerge"
```

Supported values:

| Value | Meaning |
|---|---|
| `"localMerge"` | Check out the original base branch, synchronize it, integrate the task branch locally, and push the resulting base branch. |
| `"pullRequest"` | Push the task branch and create or reuse a GitHub pull request targeting the original base branch. |

`"pullRequest"` mode requires the GitHub CLI (`gh`) and authentication at `start` and when `finish` has a workflow-created task to deliver. A skipped `finish` does not require GitHub dependencies.

`git-workflow finish` derives the title from the sole task commit's subject, or from the task branch name for other commit counts. `--title` overrides it; the body is optional.

Optional PR metadata overrides:

```bash
git-workflow finish \
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

For `"mergeCommit"`, Git generates the normal merge commit message unless `--message` overrides it.

For `"squash"`, the message defaults to the sole task commit's subject, or to a summary derived from the task branch name. `--message` overrides it. For example, `feat/ai-harness-preflight` becomes `feat(ai): harness preflight`; names outside that convention are kept unchanged.

Optional message override for either local merge method:

```bash
git-workflow finish \
  --message "docs(ai): document project settings"
```

This setting does not control how a GitHub pull request is ultimately merged when `git.integration.mode` is `"pullRequest"`.
