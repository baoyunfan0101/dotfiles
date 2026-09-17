# Shared agent instructions

## Authoring

- Match the user's language unless requested otherwise.
- Preserve existing comments unless explicitly requested otherwise.
- Use ASCII unless explicitly requested otherwise.
- When showing Markdown containing fenced code blocks, use `~~~md` as the outer fence.

## Workflow

Use `git-workflow` for repository-changing tasks. Do not run workflow commands for read-only tasks or reproduce their Git operations manually.

Start once before the task's first edit:

```bash
git-workflow start --branch-name <candidate-branch>
```

Commit each atomic change by path, as often as needed:

```bash
git-workflow commit --message "<message>" -- <paths>...
```

Finish the workflow-created task once when complete, within the user's authorized delivery scope:

```bash
git-workflow finish
```

Use `--all` only for one atomic change. Use `--override-manual` only for an explicit user request blocked by manual mode.

Follow repository naming conventions, or default to:

```text
branch: <type>/<area>-<description>
commit: <type>(<area>): <summary>
```

Surface each `[git]` result verbatim exactly once.
