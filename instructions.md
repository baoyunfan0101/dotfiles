# Shared agent instructions

## Authoring

- Match the user's latest language in responses unless requested otherwise.
- Use ASCII in project files unless requested otherwise.
- Preserve existing comments while their code or section remains unless requested otherwise.
- Do not create backups directly; use configured workflows.
- Wrap Markdown containing fenced code blocks in an outer `~~~md` fence.

## Rules

When explaining something the user is unfamiliar with, read and follow
`${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/rules/explaining-unfamiliar-topics.md`.
This applies whether or not the Git workflow is enabled, including outside Git repositories.

## Workflow Loading

At the start of a task in a Git repository, run `~/.local/libexec/agent-workflow-opt-in` once.

- Exit 0: read `${XDG_CONFIG_HOME:-$HOME/.config}/agent-workflow/instructions.md` and follow it.
- Exit 1: stop loading dotfiles workflow guidance; do not call `agent-project` or `git-workflow` merely to check activation.
- Any other exit: report the error and do not perform automatic Git workflow operations.

For an explicit request to inspect or change dotfiles project settings, use `agent-project` even when the Git workflow is disabled.
