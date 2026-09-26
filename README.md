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
Development              -> prepare -> edit -> commit*
Delivery                 -> explicit user authorization
```

| Command | Meaning |
|---|---|
| `git-workflow prepare --branch-name <name>` | Protect changes, sync, and select a working branch. |
| `git-workflow commit --message "<message>" -- <paths>...` | Record one atomic change. |
| `git-workflow pr submit` | Create or update a PR when explicitly requested. |
| `git-workflow pr merge` | Merge an existing PR when explicitly authorized. |
| `git-workflow merge` | Integrate locally when explicitly authorized. |
| `git-workflow push` | Auxiliary explicit push. |

A working branch may contain multiple Task Specs and atomic commits. Task completion, PR submission, and CI success do not authorize merging. PR metadata covers all commits from base to working branch. If `prepare` reports `workflow-disabled`, stop using workflow commands for that work. See `git-workflow --help` and `git-workflow pr --help`.

Existing Git branches need no registration. Delivery chooses `--base`, an existing PR's base, or the sole configured `git.branch.baseBranches` entry. Ambiguous bases require `--base`; configured base branches cannot be delivered.

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

Inspect or change project settings with:

```bash
agent-project effective
agent-project get git.branch.mode
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
