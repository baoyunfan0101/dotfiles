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

Installs config by creating symlinks:

```bash
./ai/install.sh
```

Default:
- Recreate symlinks and replace empty files.
- Keep real files/directories untouched.

| Option | Description |
|---|---|
| `--force`, `-f` | Replace conflicting real files/directories. |
| `--clean`, `-c` | Remove stale skill symlinks. |
| `--help`, `-h` | Show help. |
