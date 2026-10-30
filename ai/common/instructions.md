# Shared agent instructions

## Authoring

- Match the user's latest language in responses unless requested otherwise.
- Use ASCII in project files unless requested otherwise.
- Preserve existing comments while their code or section remains unless requested otherwise.
- Do not create backups directly; use configured workflows.
- Wrap Markdown containing fenced code blocks in an outer `~~~md` fence.

## Project Configuration

- Use `agent-project` only when the user explicitly asks to inspect or change dotfiles project configuration, including when the workflow is disabled.
- Discover syntax with `agent-project --help` and settings with `agent-project schema [path]`. Combine related changes in one command.
- Do not read or edit `.ai/project.json` directly unless the user explicitly requests the raw file or you are debugging `agent-project` itself.

## Workflow

Read-only Git commands may run directly. Use supported `git-workflow` commands for every Git mutation to the working tree, index, history, branches, or remote state. For unsupported mutations, stop and report the workflow capability gap; never bypass it with raw Git.

Discover command syntax with `git-workflow --help` and command-specific `--help`.

### Development

- For an empty repository, run `git-workflow bootstrap` before development.
- For each independent task, run `git-workflow prepare --branch-name <name>` before editing.
- To explicitly continue work assigned to the checked-out working branch, run `git-workflow prepare --continue`. Neither the checked-out branch nor PR state establishes continuation intent. Explicit continuation may span multiple Task Specs and logical-subtask commits.
- `prepare` discards uncommitted changes after backing them up, so run it before the first edit. If uncommitted changes on the working branch belong to the current task, commit them before `prepare --continue`; if their owner is unclear, stop and ask.
- Follow `prepare -> edit -> commit*`. After each logical subtask, invoke `git-workflow commit --message "<message>" -- <paths>...` with a concise message. Use `--all` only when every change belongs to that subtask.
- Shell enforces configured Commit and Push policies. Add `--override-manual` only for an explicitly requested commit or push.
- For local base updates, use `git-workflow branch switch <base>`, then `git-workflow sync`.
- If `prepare` reports `workflow-disabled`, stop using workflow commands for that work.

Use repository naming conventions; otherwise use `<type>/<area>-<description>` for branches and `<type>(<area>): <summary>` for commits.

### Backups

Use `git-workflow backup create` to preserve selected changes before discarding them, and `backup list` to inspect backups. Use `backup apply <id>` only for explicitly requested recovery and `backup delete <id>` only for explicitly requested deletion. `prepare` backs up and discards selected changes; it never automatically applies a backup.

### Delivery

Each delivery action requires explicit user authorization:

- `git-workflow pr submit`: only when explicitly asked to create, submit, or update a PR. It returns to the base; use `--stay` when asked to remain on the working branch.
- `git-workflow pr merge`: only with separate explicit authorization to merge the PR (including "approve").
- `git-workflow merge`: only with separate explicit authorization for local integration.

Task Spec or commit completion does not authorize PR submission or integration. Never infer merge authorization from PR submission, CI success, or task completion.

Report failures and partial successes accurately. Surface each `[git]` result verbatim exactly once.
