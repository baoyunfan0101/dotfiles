# dotfiles

Personal development environment configuration.

## Structure

```text
dotfiles/
  .gitignore
  README.md
  ai/
    install.sh
    codex/
      AGENTS.md
      install.sh
      skills/
```

## Install

Installs config by creating symlinks by default:

```bash
./ai/install.sh
```

Install by copying files/directories instead:

```bash
./ai/install.sh --copy
```

Default:
- Install using symlinks and replace empty files.
- Keep real files/directories untouched.
- Set `DOTFILES_INSTALL_MODE=copy` to change the default mode.

| Option | Description |
|---|---|
| `--mode symlink\|copy`, `-m symlink\|copy` | Choose the install mode. |
| `--symlink` | Install using symlinks. |
| `--copy` | Install by copying files/directories. |
| `--force`, `-f` | Replace conflicting real files/directories. |
| `--clean`, `-c` | Remove stale skill symlinks. |
| `--help`, `-h` | Show help. |
