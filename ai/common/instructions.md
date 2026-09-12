# Shared agent instructions

## Authoring

- Match the user's language unless requested otherwise.
- Preserve existing comments unless explicitly requested otherwise.
- Use ASCII unless explicitly requested otherwise.
- When showing Markdown containing fenced code blocks, use `~~~md` as the outer fence.

## Git

Use the deterministic workflow tools for Git and environment operations; do not reproduce their checks or Git operations manually.

Before starting a task, run:

```bash
agent-doctor
```

Surface every `[doctor]` result verbatim exactly once. If the doctor is not ready, stop automatic Git mutations and surface its fix guidance.

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

When pull-request integration is configured, then run:

```bash
github-pr auto-merge
```

This command is a no-op when pull-request auto-merge is disabled.

Use `github-pr status` when compact pull-request status is needed instead of manually querying GitHub state.

Follow repository naming conventions when available; otherwise use:

```text
branch: <type>/<area>-<description>
commit: <type>(<area>): <summary>
```

Surface every `[git]` and `[pr]` result verbatim exactly once. Do not summarize or repeat it.
