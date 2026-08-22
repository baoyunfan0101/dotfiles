# Shared agent instructions

## Authoring rules

### Logs

- Emit each workflow event log immediately when the event happens.
- Repeat all workflow event logs only at the start of the final summary as a Markdown blockquote.

### Responses

- Match the language of the user's latest message unless explicitly requested otherwise.
- When showing Markdown that contains fenced code blocks, wrap the outer block with `~~~md`.

### Project files

- Use only ASCII characters unless explicitly requested otherwise.
- Preserve existing comments for code or sections that remain present unless explicitly requested otherwise.

## Git workflow

### Project settings

- At the start of each task, run:

  ```bash
  agent-project-settings effective
  ```

- Use the returned settings for all Git workflow decisions.
- If project settings cannot be read or validated, report the failure and do not perform automatic Git mutations.

### Startup

Run the startup workflow in this order:

1. Back up existing local changes according to `git.backup`.
2. Synchronize with the remote according to `git.sync`.
3. Select or create the working branch according to `git.branch`.
4. Reapply any stash created during backup.

### Backup

`git.backup.mode` controls which pre-existing local changes are backed up:

- `none`: do not create a backup.
- `tracked`: back up staged and unstaged tracked changes.
- `untracked`: back up untracked files only.
- `all`: back up tracked changes and untracked files.

`git.backup.method` controls how the backup is created:

- `stash`: create a Git stash containing only the changes selected by `git.backup.mode`. Keep the stash after reapplying it so it remains a backup.
- `commit`: create a local backup commit containing only the changes selected by `git.backup.mode`.

Do not include ignored files unless explicitly requested.

Report backup events when they happen:

```text
[backup] INFO create: mode=<backup-mode> method=<backup-method> ref=<backup-ref>
[backup] INFO reapply: ref=<backup-ref>
[backup] ERROR create: output="<command-output>"
[backup] ERROR reapply: output="<command-output>"
```

### Sync

`git.sync.mode` controls remote synchronization:

- `none`: do not contact the remote.
- `fetch`: fetch remote updates without changing the current branch.
- `update`: fetch remote updates and update the current branch using `git.sync.updateMethod`.

`git.sync.updateMethod` controls how `update` is performed:

- `ffOnly`: allow only a fast-forward update.
- `rebase`: rebase local commits onto the remote branch.
- `merge`: merge the remote branch into the current branch.

Do not automatically resolve synchronization conflicts.

Report sync events when they happen:

```text
[sync] INFO fetch: remote=<remote-name>
[sync] INFO update: branch=<branch-name> method=<update-method>
[sync] ERROR update: output="<command-output>"
```

### Branch

`git.branch.mode` controls branch selection:

- `current`: always continue on the current branch.
- `alwaysCreate`: create a new task branch.
- `fromBase`: create a new task branch only when the current branch is listed in `git.branch.baseBranches`; otherwise continue on the current branch.

Follow the repository's existing branch naming style when available. Otherwise use:

```text
<type>/<area>-<short-kebab-description>
```

Type prefixes:

```text
feat, fix, chore, docs, refactor, test, ci, build, perf, style, revert, hotfix
```

Use a concrete touched directory or module as `area`.

Report branch events when they happen:

```text
[branch] INFO create: name=<branch-name> base=<base-branch>
[branch] INFO continue: name=<branch-name>
[branch] INFO delete: name=<branch-name> location=<local|remote|local-and-remote>
```

### Commit

`git.commit.mode` controls automatic commits:

- `manual`: do not commit or push unless explicitly requested.
- `automatic`: commit each atomic change separately and push the resulting commits.

Follow the repository's existing commit message style when available. Otherwise use:

```text
<type>(<area>): <concise-summary>
```

Use the same type prefixes and area rules as branch naming.

Report commit events when they happen:

```text
[commit] INFO create: sha=<commit-sha> message="<commit-message>"
[commit] INFO push: remote=<remote-name> branch=<branch-name>
[commit] ERROR push: output="<command-output>"
```

### Integration

When finishing a task branch, use `git.integration.mode`.

- `localMerge`: integrate locally into the branch from which the task branch was created.
- `pullRequest`: create a pull request targeting that branch.

For `localMerge`, use `git.integration.mergeMethod`:

- `mergeCommit`: create a merge commit.
- `squash`: squash the task branch into one commit.

For `pullRequest`, follow the repository's existing PR style when available. Otherwise use:

```text
<type>: <concise-summary>
```

with:

```md
## Summary

- <summary-item>

## Verification

- <verification-item>
```

After successful integration, delete the task branch only when `git.branch.deleteAfterIntegration` is `true`.

Report integration events when they happen:

```text
[integration] INFO merge: branch=<branch-name> target=<target-branch> method=<merge-method>
[integration] INFO pr: title=<pr-title> url=<pr-url>
```
