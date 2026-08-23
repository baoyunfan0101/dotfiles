# Shared agent instructions

## Authoring

- Match the user's language unless requested otherwise.
- Preserve existing comments unless explicitly requested otherwise.
- Use ASCII unless explicitly requested otherwise.
- When showing Markdown containing fenced code blocks, use `~~~md` as the outer fence.

## Git

Use `git-workflow` for all Git workflow operations; do not reproduce its Git operations manually.

Start each task with:

```bash
git-workflow prepare --branch-name <candidate-branch>
```

Commit each atomic change with:

```bash
git-workflow commit --message "<message>" -- <paths>...
```

Use `--all` only when all current changes belong to the same atomic commit.

When the user explicitly requests a commit or push disabled by manual mode, use `--override-manual`.

Finish a workflow-created task branch with:

```bash
git-workflow integrate \
  --message "<message>" \
  --title "<title>" \
  --body "<body>"
```

Follow repository naming conventions when available; otherwise use:

```text
branch: <type>/<area>-<description>
commit: <type>(<area>): <summary>
```

Surface every `[git]` result from `git-workflow` verbatim exactly once. Do not summarize or repeat it.
