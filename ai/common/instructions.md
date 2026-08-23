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

### Startup

At the start of each task, generate a candidate task branch name and run:

```bash
git-workflow prepare --branch-name <branch-name>
```

Do not manually reproduce the backup, synchronization, or branch-selection steps handled by `git-workflow prepare`.

If preparation fails, report the failure and do not make project changes until the failure is resolved.

### Branch naming

The branch name passed to `git-workflow prepare` is only a candidate. The workflow decides from project settings whether a new branch is actually required.

Follow the repository's existing branch naming style when available. Otherwise use:

```text
<type>/<area>-<short-kebab-description>
```

Type prefixes:

```text
feat, fix, chore, docs, refactor, test, ci, build, perf, style, revert, hotfix
```

Use a concrete touched directory or module as `area`.

Report branch events returned by the workflow when they happen:

```text
[branch] INFO create: name=<branch-name> base=<base-branch>
[branch] INFO continue: name=<branch-name>
[branch] INFO delete: name=<branch-name> location=<local|remote|local-and-remote>
```

### Commit

Before making automatic commit or push decisions, read:

```bash
agent-project-settings get git.commit.mode
```

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

When finishing a task branch, read:

```bash
agent-project-settings get git.integration.mode
agent-project-settings get git.integration.mergeMethod
agent-project-settings get git.branch.deleteAfterIntegration
```

`git.integration.mode` controls integration:

- `localMerge`: integrate locally into the branch from which the task branch was created.
- `pullRequest`: create a pull request targeting that branch.

For `localMerge`, `git.integration.mergeMethod` controls the merge:

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
