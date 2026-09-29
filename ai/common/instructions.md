# Shared agent instructions

## Authoring

- User-facing responses: match the language of the user's latest message unless requested otherwise.
- Project files: use ASCII only unless requested otherwise.
- Existing comments: preserve them while their code or section remains unless requested otherwise.
- Do not create backups directly; backups may only be created by configured workflows.
- When showing Markdown containing fenced code blocks, use `~~~md` as the outer fence.

## Project Configuration

- Use `agent-project` only when the user explicitly asks to inspect or change dotfiles project configuration.
- Use `agent-project --help` for project configuration command syntax.
- Discover available settings on demand with `agent-project schema [path]`.
- Use `get` for one current value, `effective` for the full configuration, and `set` or `unset` to change settings. `unset` writes the current default value. Combine related changes in one command.
- Do not read or edit `.ai/project.json` directly, except when the user explicitly asks to inspect the raw file or when debugging `agent-project` itself.

## Workflow

Read-only Git commands may run directly. Do not directly run Git commands that modify the working tree, index, history, branches, or remote state. Use `git-workflow` for supported mutations; if an operation is unsupported, stop and report the workflow capability gap instead of bypassing it with raw Git.

Development: prepare -> edit -> commit*. Delivery requires explicit user authorization. Use `git-workflow --help` for syntax and options. Supported mutations: `prepare`, `commit`, `commit --amend`, `restore`, `revert`, `cherry-pick`, `rebase`, `push`, `branch create/switch/rename/delete`, `remote connect/reconnect/disconnect`, `merge`, `pr submit`, and `pr merge`.

For each new independent task, start a new branch from the configured base before editing:

```bash
git-workflow prepare --base <base> --branch-name <name>
```

To explicitly continue work already assigned to the checked-out working branch, run `git-workflow prepare --continue`. The checked-out branch alone does not establish continuation intent. This also applies when it has no PR or its PR is open, closed, or merged. An open PR can be updated with further commits and pushes after explicit continuation.

If `prepare` reports `workflow-disabled`, do not use further workflow commands for that work.

A working branch may contain multiple Task Specs and atomic commits when continuation is explicitly requested. A Task Spec does not authorize PR submission, merge, or integration.

Delivery uses `--base`, an existing PR's base, or the sole configured base. Supply `--base` when ambiguous; it must be configured.

Commit each atomic change by path, as often as needed:

```bash
git-workflow commit --message "<message>" -- <paths>...
```

- Run `git-workflow pr submit` only when the user explicitly asks to submit, create, or update a PR.
- Run `git-workflow pr merge` only when the user explicitly authorizes merging the PR (including "approve").
- Run `git-workflow merge` only when the user explicitly authorizes local integration.
- Task Spec or commit completion does not authorize PR submission or integration. Never infer merge authorization from PR submission, CI success, or task completion.

Use `--all` only for one atomic change. Use `--override-manual` only for an explicit user request blocked by manual mode.

Follow repository naming conventions, or default to:

```text
branch: <type>/<area>-<description>
commit: <type>(<area>): <summary>
```

Surface each `[git]` result verbatim exactly once.
