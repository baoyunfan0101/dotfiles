# Global instructions

## Pre-write gate

Before the first project write in each Codex turn, run:

```bash
CODEX_TURN_ID="<x-codex-turn-metadata.turn_id>" ~/.codex/bin/pre-write-gate
```

Run it only once per turn.

If it returns `state=dirty`, report `snapshot_dir` before writing.

If it returns `state=already-snapshotted`, continue.

If it fails, do not write project files.

## Character set

Use ASCII characters only in all project-related content unless the user explicitly requests otherwise.

## Git safety

### Protect the working branch

If a temporary branch is available, commit any changes to the temporary branch; otherwise, use stash or patch to save progress.

Do not create commits on the USER-OWNED working branch unless explicitly requested.

Ask for a commit message before committing to the USER-OWNED working branch if none is provided.

### Roll back only from Git

Without Git history, do not perform rollback or attempt to recover a previous file state based on model inference.

## Comments

Do not modify existing comments as long as the commented code or commented section remains present unless the user explicitly requests it.
