# dotfiles

A personal development environment and lightweight harness for AI coding agents. Scripts handle deterministic workflow operations so agents can focus on the task.

## Structure

```text
dotfiles/
  .ai/
    project.json                Workflow overrides for this repository
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

Install the Codex configuration:

```bash
./ai/install.sh --agents codex
```

Test the current checkout in isolation:

```bash
./test.sh
```

## AI Workflow

Installing dotfiles makes the workflow available globally, but each repository decides whether to use it. The built-in default is `workflow.enabled = false`.

For an enabled repository:

```text
Read-only task           -> no workflow command
Repository-changing task -> start -> edit -> commit* -> finish
```

| Command | Meaning |
|---|---|
| `git-workflow start --branch-name <name>` | Begin a repository-changing task. |
| `git-workflow commit --message "<message>" -- <paths>...` | Record one atomic change. |
| `git-workflow finish` | Finish and deliver the task according to configuration and user authorization. |
| `git-workflow push` | Auxiliary explicit push. |

Run `start` once before editing, `commit` zero or more times, and `finish` at most once when complete. `push` is not a required lifecycle step. If `start` reports `workflow-disabled`, no further workflow commands are used for that task. See `git-workflow --help` for command options.

## Project Configuration

Project overrides are stored in:

```text
<repository>/.ai/project.json
```

A repository with no override uses the built-in defaults, including `workflow.enabled = false`. Enable the workflow explicitly with:

```bash
agent-project set workflow.enabled true
```

`set` creates `.ai/project.json` when needed. The file stores only project overrides plus `schemaVersion`; omitted values continue to inherit built-in defaults.

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

Tests use temporary homes, repositories, local bare remotes, isolated configuration directories, and a stubbed `gh`. Both installer modes run inside the sandbox, which is removed on exit. Tests do not install into the real user environment or contact GitHub.

## Installation

Install using symlinks (the default):

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
