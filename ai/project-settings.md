# Project Settings

This reference describes repository workflow operations, including development, synchronization, and explicitly authorized delivery. Project settings are stored in:

```text
<repository>/.ai/project.json
```

The file is optional. Without it, the current built-in defaults apply and the workflow is disabled. Once created, the file contains every schema 1 setting. An incomplete file is invalid.

`agent-project set` and `unset` add a root-level `/.ai/project.json` rule to Git's local `info/exclude` file before writing settings. They preserve other exclude rules and never edit or create `.gitignore`. New, untracked configuration stays local and is absent from commits, clones, and forks. If `.ai/project.json` is already tracked, the command warns: the exclude rule cannot remove existing tracking, which requires separate explicit cleanup.

## Manage project settings

`agent-project` resolves `.ai/project.json` from the current Git repository root and fails outside a Git worktree.

`agent-project schema` exposes user-configurable settings. `schemaVersion` is managed internally as project-file metadata and is not part of the configurable setting namespace.

When a user asks to configure project settings, an agent can discover available values with `agent-project schema` or inspect one setting with `agent-project schema <path>`. Schema output describes supported settings and defaults without reading `.ai/project.json`; it also works when that file is invalid. Agents should use `get` or `effective` only when the current value is needed, and should not read or edit the JSON file directly during normal configuration changes.

Show the complete effective configuration or one value:

```bash
agent-project effective
agent-project get workflow.enabled
agent-project get git.branch.baseBranches
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

agent-project unset git.branch.baseBranches git.integration.mergeMethod
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
      "mode": "autoCommitAndPush"
    },
    "branch": {
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
| `git.backup` | Save selected local changes before discarding them. |
| `git.commit` | Commit and push policy. |
| `git.branch` | Allowed bases and branch cleanup after integration. |
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

Controls automatic synchronization during new branch preparation, explicit branch continuation, and local integration. After a successful PR merge, the base is always fetched and fast-forwarded to the remote result.

This setting does not control an explicit `git-workflow sync`; that command always fetches and permits only a fast-forward of the checked-out base.

Default:

```json
"update"
```

Supported values:

| Value | Meaning |
|---|---|
| `"none"` | Do not contact or synchronize with the remote repository; a new branch starts from the local base. |
| `"fetch"` | Fetch remote information without advancing the local branch; a new branch starts from the local base. |
| `"update"` | Fetch and update the local branch; new branch preparation fast-forwards the base, while continuation uses `git.sync.updateMethod`. |

With `"update"`, continuation skips synchronization when the working branch has no upstream. A configured upstream must be valid.

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

Before synchronization or branch creation, `git-workflow prepare` saves and verifies a persistent local backup, then discards the selected changes. It does not automatically apply backups, even if preparation later fails. Bootstrap is the explicit initialization operation for an unborn repository and preserves all unselected files.

### `git.backup.mode`

Controls which local changes are backed up and discarded by `prepare` or `git-workflow backup create`.

Default:

```json
"all"
```

Supported values:

| Value | Meaning |
|---|---|
| `"none"` | Do not create a backup or discard changes. |
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
| `"stash"` | Store a persistent Git stash, including selected staging state and untracked files. |
| `"commit"` | Store the selected working-file snapshot as a commit with the current HEAD as its sole parent, under `refs/agent-workflow/backups/`. Applying its diff stages the saved changes. |

`git-workflow backup create` saves and verifies a backup before discarding only the selected changes. Unselected and ignored files remain untouched. `backup list` returns stable IDs for both methods. `backup apply <id>` explicitly applies old changes onto the current branch and HEAD, retaining the backup even if the backed-up project configuration is temporarily absent. It retains the backup even on conflicts. `backup delete <id>` deletes only the selected backup. Backups remain local and are not pushed or synchronized.

Both manual creation and `prepare` use the same backup mechanism. Backups remain until explicitly deleted. Creation or verification failure leaves selected changes in place. For commit backups, staged-only intermediate versions are not separate saved states; the snapshot contains the selected working-file contents. Git stash backups preserve the selected index state as well.

This setting has no effect when:

```json
"git.backup.mode": "none"
```

## Git commits

### `git.commit.mode`

Controls how Shell handles ordinary workflow commits, including amendments, and whether it pushes them.

Default:

```json
"autoCommitAndPush"
```

Supported values:

| Value | Meaning |
|---|---|
| `"manual"` | Ordinary `commit` commands skip without changing the index or working tree. `push` also skips unless manually overridden. |
| `"autoCommit"` | `commit` creates a local commit without pushing. Explicit `push` remains available. |
| `"autoCommitAndPush"` | `commit` creates a local commit and pushes it after success. |

In `"manual"` mode, an explicitly requested commit can be performed locally with:

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

A manually overridden commit is not automatically pushed. `bootstrap`, local integration, and PR submission keep their own publication behavior.

## Git branches

Start an independent task with `git-workflow prepare --branch-name <name>`. The command uses the sole configured base, or accepts `--base <base>` when several are configured, applies `git.sync.mode`, and creates the new branch from the local base HEAD. Use `git-workflow prepare --continue` only when intentionally continuing the checked-out working branch. The checked-out branch alone never establishes continuation intent. PR state does not control branch reuse. A working branch may contain multiple logical-subtask commits when continuation is explicitly requested. A Task Spec does not authorize delivery. Delivery resolves its base when requested.

Use `git-workflow sync` to update an existing, checked-out base branch from its configured remote tracking branch, such as after a PR has been merged remotely. The working tree must be clean. Sync fetches the remote state, fast-forwards the local base when possible, and succeeds without changing history when already current. It rejects ahead or diverged local history; it never merges or rebases. For a post-merge update, run `git-workflow branch switch main` and then `git-workflow sync`.

For a genuinely empty repository, run `git-workflow bootstrap [--base <base>] --message "<initial message>" -- <paths>...` (or use `--all`) first. It commits selected files on the configured base and pushes that branch before any working branch is created. `--all` follows Git's effective ignore rules, including the repository-local exclusion for `.ai/project.json`. If several bases are configured, specify `--base`; the command rejects ambiguous configuration. This explicit operation runs in manual commit mode too. Then use the normal `prepare -> edit -> commit*` development flow. Direct ordinary commits to configured bases remain prohibited, and delivery still requires separate authorization.

Before development advances, `git-workflow bootstrap --amend --message "Initial commit" --all` can correct the single published root commit. The command requires the base to be the only local and remote branch, with no tags, and uses an exact remote lease. Branch protection may reject the rewritten commit. A failed publication leaves the amended commit local and can be retried with `git-workflow bootstrap --retry-amend`; ordinary base-branch amendments remain prohibited.

### `git.branch.baseBranches`

Defines the bases allowed for new work and delivery.

Default:

```json
["main"]
```

Example:

```json
["main", "develop"]
```

Each entry must be a unique, non-empty string.

New work requires an explicit `--base` from this list. Delivery uses explicit `--base`, then an existing PR's base for PR operations, then the sole configured base. Multiple possibilities require `--base`; bases outside this list are rejected. A configured base branch cannot itself be delivered.

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
git-workflow pr submit [--base <branch>] [--stay]
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

`pr submit` finds open PRs for the current head and resolves the base before pushing. Without `--base`, a single existing PR supplies its base after validation; multiple PRs require explicit selection. It generates the title from the sole commit subject or, for multiple commits, from the working branch name. The body lists all `base..working-branch` subjects oldest first under a managed `## Summary`. Repeated submission updates the same PR using the complete history while preserving its existing title and content outside the Summary. After success it checks out the PR's base without advancing that branch; `--stay` keeps the working branch checked out for this invocation. A failed submission leaves the working branch checked out. If submission succeeds but checkout fails, the PR remains open and the command reports partial success.

Successful pushes to a working branch with an existing open PR refresh its managed Summary from the full commit history against that PR's actual base. This applies to automatic commit pushes, explicit `push`, amendments, and other working-branch pushes. The workflow does not create a PR during a push or change its title. It recognizes descriptions generated by earlier workflow versions and keeps separate manual sections. An ambiguous PR association or an unmanaged Summary is left untouched. The push remains successful if metadata synchronization fails; the result reports `pr=failed` with an actionable warning. A branch without an open PR reports `pr=none`.

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
