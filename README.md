# dotfiles

A personal development environment and lightweight harness for AI coding agents. Scripts handle deterministic workflow operations so agents can focus on the task.

## Structure

```text
dotfiles/
  AGENTS.md                     Instructions for developing this repository
  instructions.md               Shared entry point installed for daily assistant use
  install.sh                    One-command install and update
  install/                      Installer entry point, helpers, and shared file declarations
  rules/                        Guidance grouped by deliverable
  workflow/                     Opt-in Git workflow and project configuration
    README.md                   Setup and command guide
    instructions.md             Instructions for assistants in enabled repositories
    project-settings.md         Configuration reference
  adapters/                     Tool-specific entry points and install declarations
    codex/
    claude/
  tests/                        Isolated regression tests and fixtures
  test.sh                       Test runner
```

## Quick Start

Install, update, or repair:

```bash
curl -fsSL https://raw.githubusercontent.com/baoyunfan0101/dotfiles/main/install.sh | bash
```

To enable the optional Git workflow for a repository, follow the
[Git workflow quick start](workflow/README.md#quick-start).

The shared entry point is installed at `${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/instructions.md`.
It is independent of workflow opt-in and also applies outside Git repositories.
The repository-root `AGENTS.md` governs development here; it is not the installed shared entry point.

## Rules

Shared rules apply independently of Git workflow opt-in. The shared entry point
selects a directory by deliverable. Its index lists when to read each rule.

Rules are installed at `${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/rules` in both
symlink and copy modes, and removed with the last installed adapter.

## Testing

```bash
./test.sh
```

GitHub Actions runs the same test command for pull requests targeting `main`.

Workflow tests are grouped by responsibility in `tests/test_workflow_*.py` and share isolated fixtures in `workflow_helpers.py`. Run the group with `./test.sh workflow`, or a focused module such as `./test.sh workflow_prepare`.

Tests use temporary homes, repositories, local bare remotes, isolated configuration directories, and a stubbed `gh`. Both installer modes run inside the sandbox, which is removed on exit. Tests do not install into the real user environment or contact GitHub.

## Installation

Use the Quick Start command for normal installation and updates. It keeps a checkout at `~/.local/share/dotfiles` and runs the installer from there.

Dotfiles does not own the entire `~/.codex/AGENTS.md` or `~/.claude/CLAUDE.md` file. It manages only the content inside its marked block; personal rules outside the block are preserved:

```md
# Personal rules

<!-- BEGIN baoyunfan0101/dotfiles managed block -->

...dotfiles instructions...

<!-- END baoyunfan0101/dotfiles managed block -->
```

For development from an existing checkout, install using symlinks:

```bash
./install/install.sh --agents codex
```

Install by copying files:

```bash
./install/install.sh --agents codex --copy
```

Select multiple agents with a comma-separated list:

```bash
./install/install.sh --agents codex,claude
```

Shared content is installed with the selected adapters. The existing `common` installation manifest and state directory are retained for updates from older layouts. For all installer options, run:

```bash
./install/install.sh --help
```

## Uninstall

```bash
./install/install.sh --agents codex --uninstall
```

Shared content is removed automatically when no installed adapters remain.
