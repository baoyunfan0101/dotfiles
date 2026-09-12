# Shared agent instructions

## Authoring

- Match the user's language unless requested otherwise.
- Preserve existing comments unless explicitly requested otherwise.
- Use ASCII unless explicitly requested otherwise.
- When showing Markdown containing fenced code blocks, use `~~~md` as the outer fence.

## Workflow

Run `agent-doctor` before starting work. If it fails, surface its output and stop the automatic workflow.

Use `git-workflow` for Git operations; do not reproduce its behavior manually.

Prepare with:

```bash
git-workflow prepare --branch-name <candidate-branch>
```

Commit each atomic change by path with:

```bash
git-workflow commit --message "<message>" -- <paths>...
```

Finish a workflow-created branch with:

```bash
git-workflow integrate \
  --message "<message>" \
  --title "<title>" \
  --body "<body>"
```

Use `--all` only for one atomic change. Use `--override-manual` only for an explicit user request blocked by manual mode.

Follow repository naming conventions, or default to:

```text
branch: <type>/<area>-<description>
commit: <type>(<area>): <summary>
```

Surface each `[doctor]` and `[git]` result verbatim exactly once.
