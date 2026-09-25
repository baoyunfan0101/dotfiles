# Global instructions

At the start of a task in a Git repository, run `~/.local/libexec/agent-workflow-opt-in` once.

- Exit 0: read `~/.config/agent-workflow/instructions.md` and follow it.
- Exit 1: stop loading dotfiles workflow guidance; do not call `agent-project` or `git-workflow` merely to check activation.
- Any other exit: report the error and do not perform automatic Git workflow operations.

For an explicit request to inspect or change dotfiles project settings, use `agent-project` even when the Git workflow is disabled.
