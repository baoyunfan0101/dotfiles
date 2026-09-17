# dotfiles

Personal development environment configuration.

## Structure

```text
ai/
  install.sh                 Installer entry point
  install/                   Installer helpers
  project-settings.md        Configuration reference
  common/
    module.sh                Managed files
    instructions.md          Shared Agent contract
    bin/                     Public command entry points
    libexec/git-workflow/     start, commit, finish, push executables
    lib/git-workflow/         Shared workflow libraries
    tests/                   Workflow regression tests
  codex/                     Agent configuration and skills
```

`ai/install.sh` is the public installer.

Each module declares the files it manages through its own `module.sh`. The `common` module is installed automatically together with the selected agent modules.

## Install

Install Codex configuration using symlinks:

```bash
./ai/install.sh --agents codex
```

Install by copying files instead:

```bash
./ai/install.sh --agents codex --copy
```

Multiple agents can be selected with a comma-separated list:

```bash
./ai/install.sh --agents codex,claude
```

Available options:

| Option | Description |
|---|---|
| `-a, --agents AGENT,...` | Agents to install or uninstall. |
| `-m, --mode symlink\|copy` | Installation mode. |
| `--symlink` | Install using symbolic links. |
| `--copy` | Install by copying files and directories. |
| `-f, --force` | Replace conflicting unmanaged files or directories. |
| `-c, --clean` | Remove previously managed targets that are no longer declared. |
| `--uninstall` | Remove targets managed by the selected modules. |
| `-h, --help` | Show help. |

The default install mode is `symlink`. It can also be set with:

```bash
export DOTFILES_INSTALL_MODE=copy
```

## Uninstall

Uninstall an agent module with:

```bash
./ai/install.sh --agents codex --uninstall
```

The shared `common` module is removed automatically when no installed agent modules remain.

## Shared AI workflow

The `common` module installs:

```text
~/.local/bin/agent-project-settings
~/.local/bin/git-workflow
~/.local/libexec/git-workflow/
~/.local/lib/git-workflow/
~/.config/agent-workflow/instructions.md
```

Read-only tasks do not run workflow commands. Repository-changing tasks use:

| Command | When to use |
|---|---|
| `git-workflow start --branch-name <name>` | Once before the first edit; begins the task. |
| `git-workflow commit --message "<message>" -- <paths>...` | For each atomic change; automatic mode also pushes. |
| `git-workflow finish` | Once when complete; finishes and delivers the task according to configuration and user authorization. |
| `git-workflow push` | Auxiliary explicit push, such as manual mode or recovery. |

The normal lifecycle is `start -> commit* -> finish`, with zero or more commits and at most one finish. Run `git-workflow --help` for options.

Required external dependencies are validated internally. The installer only manages this repository's files.

Project-level workflow behavior can be configured in:

```text
<repository>/.ai/project.json
```

`agent-project-settings` reads this configuration and merges it with the built-in defaults.

Show the effective project settings with:

```bash
agent-project-settings effective
```

See [Project Settings](ai/project-settings.md) for all supported settings, values, defaults, and behavior.

Run the regression tests with:

```bash
./test.sh
```

Tests use temporary repositories, local remotes, homes, and configuration directories. Both installer modes run inside the sandbox; they do not install into your real environment or contact GitHub. The sandbox is removed on exit.
