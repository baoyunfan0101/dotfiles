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

Do not manually reproduce backup, synchronization, or branch-selection operations handled by `git-workflow`.

If preparation fails, report the failure and do not make project changes until it is resolved.

### Branch naming

Follow the repository's existing branch naming style when available. Otherwise use:

```text
<type>/<area>-<short-kebab-description>
```

Type prefixes:

```text
feat, fix, chore, docs, refactor, test, ci, build, perf, style, revert, hotfix
```

Use a concrete touched directory or module as `area`.

The branch name passed to `prepare` is only a candidate. `git-workflow` decides whether a new branch is required.

### Commit

Treat each atomic change as a separate commit.

Follow the repository's existing commit message style when available. Otherwise use:

```text
<type>(<area>): <concise-summary>
```

For selected paths, run:

```bash
git-workflow commit \
  --message "<commit-message>" \
  -- <path>...
```

Use `--all` only when every current change intentionally belongs to the same atomic commit:

```bash
git-workflow commit \
  --message "<commit-message>" \
  --all
```

If the command reports:

```text
commit=skipped
reason=manual
```

do not commit unless the user explicitly requested it.

For an explicitly requested commit in manual mode, run:

```bash
git-workflow commit \
  --override-manual \
  --message "<commit-message>" \
  -- <path>...
```

In automatic mode, `git-workflow commit` also pushes the resulting commit.

In manual mode, `--override-manual` creates the commit but does not automatically push it.

Do not run `git commit` manually when `git-workflow commit` handles the operation.

### Push

Normally no separate push is required after an automatic commit.

For an explicit push or a retry after a failed push, run:

```bash
git-workflow push
```

If the command reports:

```text
push=skipped
reason=manual
```

do not push unless the user explicitly requested it.

For an explicitly requested push in manual mode, run:

```bash
git-workflow push --override-manual
```

Do not run `git push` manually when `git-workflow push` handles the operation.

### Integration

When finishing a task branch, run:

```bash
git-workflow integrate \
  --message "<integration-commit-message>" \
  --title "<pull-request-title>" \
  --body "<pull-request-body>"
```

`git-workflow` decides from project settings whether to perform a local merge or create a pull request.

Follow the repository's existing integration naming and pull-request style when available.

Otherwise use the commit format for `--message`:

```text
<type>(<area>): <concise-summary>
```

Use this format for `--title`:

```text
<type>: <concise-summary>
```

Use this format for `--body`:

```md
## Summary

- <summary-item>

## Verification

- <verification-item>
```

Do not manually reproduce merge, push, pull-request creation, or branch-deletion operations handled by `git-workflow`.

If integration reports:

```text
integration=skipped
reason=no-workflow-created-branch
```

continue on the current branch without automatically integrating or deleting it.
