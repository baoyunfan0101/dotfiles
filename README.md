# dotfiles

A personal development environment and lightweight harness for AI coding agents. Scripts handle deterministic workflow operations so agents can focus on the task.

## Structure

```text
dotfiles/
  install.sh                    One-command install and update
  .ai/
    project.json                Complete workflow settings for this repository
  README.md                     Project entry point
  test.sh                       Isolated repository tests
  ai/
    install.sh                  Installer entry point
    install/                    Installer helpers
    project-settings.md         Configuration reference
    common/                     Shared agent workflow and runtime
      module.sh                 Managed file declarations
      instructions.md           Minimal agent contract
      bin/                      Public command entry points
      libexec/git-workflow/      Command implementations
      lib/git-workflow/          Shared workflow libraries
      lib/agent_project/         Configuration schema, persistence, and exclusion
      tests/                    Regression tests
    codex/                      Codex configuration and skills
```

## Quick Start

Install, update, or repair:

```bash
curl -fsSL https://raw.githubusercontent.com/baoyunfan0101/dotfiles/main/install.sh | bash
```

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

Use CLI help for command details and [Project Settings](ai/project-settings.md) for the full configuration reference.

## AI Workflow

Installing dotfiles makes the workflow available globally, but each repository decides whether to use it. The built-in default is `workflow.enabled = false`.

The Codex global adapter checks the project's opt-in flag before loading shared workflow instructions. Projects without an enabled workflow do not load that guidance or run workflow commands for activation discovery.

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

Bootstrap creates and publishes the first commit in an empty repository. `--all` respects Git's effective ignore rules. Before development advances, `bootstrap --amend` can correct the published root commit using an exact remote lease; `bootstrap --retry-amend` recovers a failed publication. See [Project Settings](ai/project-settings.md) for the restrictions and recovery behavior.

A working branch may contain multiple Task Specs and logical commits when continuation is explicitly requested. PR submission, merging, and local integration each require explicit authorization; task completion and CI success do not authorize merging.

In pullRequest mode, successful working-branch pushes refresh an existing open PR's managed Summary while preserving its title and other sections. Metadata failures leave the successful push intact. PR creation requires `pr submit`.

After a PR is merged remotely, use `git-workflow branch switch main` followed by `git-workflow sync` to update the local base. Sync requires a clean working tree and configured remote tracking. It succeeds when already current and rejects local history that cannot be fast-forwarded. `prepare` remains for starting or continuing development work.

## Project Configuration

Project settings are stored in:

```text
<repository>/.ai/project.json
```

A repository without this file uses the built-in defaults, including `workflow.enabled = false`. Enable the workflow explicitly with:

```bash
agent-project set workflow.enabled true
```

Project settings can be managed through `agent-project`; agents discover settings on demand instead of editing `.ai/project.json` directly.

`set` creates a complete `.ai/project.json` when needed. Existing project files keep their explicit settings even if built-in defaults change later. `unset` writes the current default value for a selected setting.

`agent-project` keeps new `.ai/project.json` files local by adding `/.ai/project.json` to Git's repository-local `info/exclude` file. It never changes the project's `.gitignore`. Untracked local settings do not appear in published commits, clones, or forks. If the configuration is already tracked, Git continues tracking it; `agent-project` warns so its removal from the index can be handled explicitly.

Inspect or change project settings with:

```bash
agent-project effective
agent-project get git.branch.baseBranches
agent-project set git.integration.mode pullRequest
agent-project unset git.integration.mode
```

Project-specific Agent rules belong in the repository-root `AGENTS.md`. They are independent of `.ai/project.json`: a project can have Agent rules without using this Git workflow, or use the workflow without additional Agent rules.

See [Project Settings](ai/project-settings.md) for the complete configuration reference.

## Testing

```bash
./test.sh
```

GitHub Actions runs the same test command for pull requests targeting `main`.

Workflow tests are grouped by responsibility in `ai/common/tests/test_workflow_*.py` and share isolated fixtures in `workflow_helpers.py`. Run the group with `./test.sh workflow`, or a focused module such as `./test.sh workflow_prepare`.

Tests use temporary homes, repositories, local bare remotes, isolated configuration directories, and a stubbed `gh`. Both installer modes run inside the sandbox, which is removed on exit. Tests do not install into the real user environment or contact GitHub.

## Installation

Use the Quick Start command for normal installation and updates. It keeps a checkout at `~/.local/share/dotfiles` and runs the installer from there.

Dotfiles does not own the entire `~/.codex/AGENTS.md` file. It manages only the content inside its marked block; personal rules outside the block are preserved:

```md
# Personal rules

<!-- BEGIN baoyunfan0101/dotfiles managed block -->

...dotfiles instructions...

<!-- END baoyunfan0101/dotfiles managed block -->
```

For development from an existing checkout, install using symlinks:

```bash
./ai/install.sh --agents codex
```

Install by copying files:

```bash
./ai/install.sh --agents codex --copy
```

Select multiple agents with a comma-separated list:

```bash
./ai/install.sh --agents codex,claude
```

The shared `common` module is installed with the selected agents. For all installer options, run:

```bash
./ai/install.sh --help
```

## Uninstall

```bash
./ai/install.sh --agents codex --uninstall
```

The shared `common` module is removed automatically when no installed agent modules remain.
