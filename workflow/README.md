# Git workflow

Use this guide to enable the workflow and find the commands for everyday Git
operations. [Project Settings](project-settings.md) describes the configuration
and defaults. [Workflow instructions](instructions.md) define how assistants
operate in enabled repositories.

## Quick Start

Run the following commands inside the Git repository you want to configure.

```bash
cd /path/to/your/repository

agent-project set workflow.enabled true
# For a repository without an existing remote connection:
git-workflow remote connect <repository-url>
agent-project effective
agent-project --help
git-workflow --help
```

## Workflow

Installing dotfiles makes the workflow available globally, but each repository decides whether to use it. The built-in default is `workflow.enabled = false`.

The Codex and Claude Code adapters load the same shared `instructions.md` entry point, including general authoring requirements. That entry checks the project's opt-in flag before loading `workflow/instructions.md`. Projects without an enabled workflow do not load that guidance or run workflow commands for activation discovery.

For an enabled repository:

```text
Read-only task           -> no workflow command
Repository bootstrap     -> bootstrap
Development              -> prepare -> edit -> commit*
Delivery                 -> explicit user authorization
```

| Command | Meaning |
|---|---|
| `git-workflow bootstrap --message "<message>" -- <paths>...` | Create and publish the first commit on the configured base in an empty repository. |
| `git-workflow prepare --branch-name <name>` | Start an independent task on a new branch from the sole configured base. Add `--base <base>` when several bases are configured. |
| `git-workflow prepare --continue` | Explicitly continue the checked-out working branch. |
| `git-workflow sync` | Fetch and fast-forward the checked-out configured base branch from its tracked remote branch. |
| `git-workflow backup create` | Verify a persistent local backup, then discard the selected changes. |
| `git-workflow backup list` | List workflow backups by stable ID. |
| `git-workflow backup apply <id>` | Apply old changes to the current state while retaining the backup. |
| `git-workflow backup delete <id>` | Delete one workflow backup. |
| `git-workflow commit --message "<message>" -- <paths>...` | Record one logical subtask; Shell applies the configured commit and push policy. |
| `git-workflow undo` | Remove the latest working-branch commit, leaving its changes unstaged; published history is updated safely according to Commit mode. |
| `git-workflow pr submit [--stay]` | Create or update a PR when explicitly requested; return to its base unless `--stay` is set. |
| `git-workflow pr merge` | Merge an existing PR when explicitly authorized. |
| `git-workflow merge` | Integrate locally when explicitly authorized. |
| `git-workflow push` | Explicit push; use `--rewrite` after a local published amend or rebase. In pullRequest mode, a successful working-branch push refreshes an open PR's managed Summary. |
| `git-workflow remote connect <url>` | Connect the local repository to a remote repository. |
| `git-workflow remote reconnect <url>` | Change the existing remote repository connection. |
| `git-workflow remote disconnect` | Remove the existing remote repository connection. |

`prepare` verifies a persistent local backup before discarding selected changes. It never automatically applies or deletes that backup, including on failure. Use `backup apply <id>` only for explicit recovery onto the current working state.

Bootstrap creates and publishes the first commit in an empty repository. `--all` respects Git's effective ignore rules. Before development advances, `bootstrap --amend` can correct the published root commit using an exact remote lease; `bootstrap --retry-amend` recovers a failed publication. See [Project Settings](project-settings.md) for the restrictions and recovery behavior.

A working branch may contain multiple Task Specs and logical commits when continuation is explicitly requested. PR submission, merging, and local integration each require explicit authorization; task completion and CI success do not authorize merging.

In pullRequest mode, successful working-branch pushes refresh an existing open PR's managed Summary while preserving its title and other sections. Metadata failures leave the successful push intact. PR creation requires `pr submit`.

After a PR is merged remotely, use `git-workflow branch switch main` followed by `git-workflow sync` to update the local base. Sync requires a clean working tree and configured remote tracking. It succeeds when already current and rejects local history that cannot be fast-forwarded. `prepare` remains for starting or continuing development work.

## Project instructions

Put project-specific agent rules in the project's `AGENTS.md`. The Claude Code
adapter reads it when the project has no `CLAUDE.md`. Projects can use these rules
independently of workflow configuration.
