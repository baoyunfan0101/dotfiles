# Project Settings

This reference describes development (`prepare`, `commit`, `push`) and explicitly authorized delivery (`merge`, `pr submit`, `pr merge`). Project settings are stored in:

```text
<repository>/.ai/project.json
```

The file is optional. Without it, the current built-in defaults apply and the workflow is disabled. Once created, the file contains every schema 1 setting. An incomplete file is invalid.

## Manage project settings

`agent-project` resolves `.ai/project.json` from the current Git repository root and fails outside a Git worktree.

`agent-project schema` exposes user-configurable settings. `schemaVersion` is managed internally as project-file metadata and is not part of the configurable setting namespace.

When a user asks to configure project settings, an agent can discover available values with `agent-project schema` or inspect one setting with `agent-project schema <path>`. Schema output describes supported settings and defaults without reading `.ai/project.json`; it also works when that file is invalid. Agents should use `get` or `effective` only when the current value is needed, and should not read or edit the JSON file directly during normal configuration changes.

Show the complete effective configuration or one value:

```bash
agent-project effective
agent-project get workflow.enabled
agent-project get git.branch.mode
```

Set a value:

```bash
agent-project set workflow.enabled true
agent-project set git.integration.mode pullRequest
```

`set` parses the schema type, validates the complete configuration, and creates `.ai/project.json` from the current defaults when needed. Booleans use `true` or `false`; lists use JSON, for example:

```bash
agent-project set git.branch.baseBranches '["main","develop"]'
```

Writes use a temporary file in `.ai/` followed by atomic replacement. Invalid changes leave the existing configuration untouched.

Reset a value to the current built-in default:

```bash
agent-project unset git.integration.mode
```

`unset` writes the default value explicitly. It never removes a required field, and `schemaVersion` is managed internally.

Related settings can be changed in one operation. Each path and value is parsed in order, then the complete configuration is validated and written atomically once. Any invalid path, value, or resulting configuration leaves the file unchanged:

```bash
agent-project set \
  workflow.enabled true \
  git.integration.mode pullRequest \
  git.commit.mode manual

agent-project unset git.branch.mode git.integration.mergeMethod
```

Single-setting `set` and `unset` remain supported. An agent can translate a natural-language request into these commands; the CLI does not parse natural language.

`project.json` stores the complete configuration, including `schemaVersion`. `effective` returns that file when it exists or the current built-in defaults when it does not. Existing project behavior does not change when built-in defaults change.

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
| `git.sync` | Repository synchronization during `prepare` and local integration. |
| `git.backup` | Protect local changes during `prepare`. |
| `git.commit` | Commit and push policy. |
| `git.branch` | Working-branch policy. |
| `git.integration` | Local integration or pull-request delivery, and merge method. |

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

The field is required in every existing project file; the file's presence alone does not enable the workflow.

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
| `true` | Repository-changing work uses `prepare -> edit -> commit*`; delivery requires explicit authorization. |

Enable the workflow for the current repository:

```bash
agent-project set workflow.enabled true
```

Disable it explicitly:

```bash
agent-project set workflow.enabled false
```

Write the current default `false` explicitly:

```bash
agent-project unset workflow.enabled
```

When disabled, development and delivery commands return `skip reason=workflow-disabled`. Delivery-specific checks such as GitHub CLI authentication are not performed.

## Git synchronization

### `git.sync.mode`

Controls synchronization of the current branch during `prepare` and the base branch during local `merge`. After a successful PR merge, the base is always fetched and fast-forwarded to the remote result.

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
| `"commit"` | Store the backup as a commit referenced under `refs/agent-workflow/backups/`. A temporary stash transports working-tree changes during `git-workflow prepare`. |

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

Controls whether `git-workflow prepare` creates a working branch.

Default:

```json
"fromBase"
```

Supported values:

| Value | Meaning |
|---|---|
| `"current"` | Continue working on the current branch. Never create a working branch automatically. |
| `"alwaysCreate"` | Always create a new working branch during `git-workflow prepare`. |
| `"fromBase"` | Create a new working branch only when the current branch is listed in `git.branch.baseBranches`. Otherwise continue on the current branch. |

When the selected mode requires a new branch, `git-workflow prepare` must receive a candidate branch name:

```bash
git-workflow prepare --branch-name <candidate-branch>
```

A working branch is an ordinary Git branch and may contain multiple Task Specs and atomic commits. Existing branches work with `current` and `fromBase`; a new Task Spec does not by itself select a new branch. Delivery resolves its base when requested.

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

This list controls `fromBase` branch selection and the allowed/default delivery bases. Delivery uses explicit `--base`, then an existing PR's base for PR operations, then the sole configured base. Multiple possibilities require `--base`; bases outside this list are rejected. A configured base branch cannot itself be delivered.

### `git.branch.deleteAfterIntegration`

Controls whether the working branch is deleted after successful local integration or PR merge.

Default:

```json
false
```

Supported values:

| Value | Meaning |
|---|---|
| `false` | Keep the working branch after integration. |
| `true` | Delete the integrated working branch locally and delete its remote branch when the remote branch exists. |

This setting applies to both integration modes. PR submission keeps the branch.

When squash integration is used, the local working branch is force-deleted because its commits are not direct ancestors of the resulting squash commit.

## Delivery

Delivery requires explicit user authorization:

```bash
git-workflow pr submit [--base <branch>]
git-workflow pr merge [--base <branch>]
git-workflow merge [--base <branch>]
```

Use `pr submit` only when the user requests PR submission or an update, `pr merge` only when they approve merging the PR, and `merge` only when they authorize local integration. Completing a Task Spec or commit does not authorize delivery. PR submission and CI success do not authorize merging.

Delivery requires a non-base current branch, a resolvable configured base, and a clean working tree. Successful integration syncs the base and applies branch cleanup.

### `git.integration.mode`

Selects local integration or pull-request delivery.

Default:

```json
"localMerge"
```

Supported values:

| Value | Meaning |
|---|---|
| `"localMerge"` | Check out the original base branch, synchronize it, integrate the working branch locally, and push the resulting base branch. |
| `"pullRequest"` | `pr submit` creates or updates a PR; separately authorized `pr merge` merges it. |

PR commands require `gh` and authentication. Development commands do not. `merge` rejects pullRequest mode; PR commands reject localMerge mode.

`pr submit` finds open PRs for the current head and resolves the base before pushing. Without `--base`, a single existing PR supplies its base after validation; multiple PRs require explicit selection. It generates the title from the sole commit subject or, for multiple commits, from the working branch name. The body lists all `base..working-branch` subjects oldest first under `## Summary`. Repeated submission updates the same PR using the complete history.

`pr merge` requires an existing open PR and never creates one or enables auto-merge. It rejects merge queues, checks that the PR head matches the current commit, and confirms the PR has merged before syncing the base and cleaning up.

### `git.integration.mergeMethod`

Controls how local integration and explicitly authorized PR merges integrate the working branch.

Default:

```json
"mergeCommit"
```

Supported values:

| Value | Meaning |
|---|---|
| `"mergeCommit"` | Merge the working branch using a merge commit. Fast-forward-only integration is not used. |
| `"squash"` | Squash all changes from the working branch into a single commit on the base branch. |

For `"mergeCommit"`, Git generates the normal merge commit message unless `--message` overrides it.

For `"squash"`, the message defaults to the sole branch commit's subject, or to a summary derived from the working branch name. `--message` overrides it. For example, `feat/ai-harness-preflight` becomes `feat(ai): harness preflight`; names outside that convention are kept unchanged.

Optional message override for either local merge method:

```bash
git-workflow merge \
  --message "docs(ai): document project settings"
```

For PR merging, `mergeCommit` maps to `gh pr merge --merge` and `squash` maps to `gh pr merge --squash`.
