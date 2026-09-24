# Shared agent instructions

## Authoring

- Match the user's language unless requested otherwise.
- Preserve existing comments unless explicitly requested otherwise.
- Use ASCII unless explicitly requested otherwise.
- When showing Markdown containing fenced code blocks, use `~~~md` as the outer fence.

## Project Configuration

- Use `agent-project` only when the user explicitly asks to inspect or change dotfiles project configuration.
- Discover available settings on demand with `agent-project schema [path]`.
- Use `get` for one current value, `effective` for the full effective configuration, and `set` or `unset` to change overrides. Combine related changes in one command.
- Do not read or edit `.ai/project.json` directly, except when the user explicitly asks to inspect the raw file or when debugging `agent-project` itself.

## Workflow

Use `git-workflow` for repository-changing tasks. Do not run workflow commands for read-only tasks or reproduce their Git operations manually.

Start once before the task's first edit:

```bash
git-workflow start --branch-name <candidate-branch>
```

If `start` reports `[git] start skip reason=workflow-disabled`, do not use further `git-workflow` commands for that task.

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
