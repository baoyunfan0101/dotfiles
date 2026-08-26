# dotfiles

Personal development environment configuration.

## Structure

```text
dotfiles/
├── README.md
├── .gitignore
└── ai/
    ├── install.sh
    ├── install/
    │   └── lib.sh
    ├── common/
    │   ├── module.sh
    │   ├── instructions.md
    │   └── bin/
    │       ├── git-workflow
    │       └── project-settings
    └── codex/
        ├── module.sh
        ├── AGENTS.md
        └── skills/
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
~/.config/agent-workflow/instructions.md
```

`agent-project-settings` reads project-level workflow configuration from:

```text
<repository>/.ai/project.json
```

`git-workflow` provides the shared Git workflow used by supported agents.
