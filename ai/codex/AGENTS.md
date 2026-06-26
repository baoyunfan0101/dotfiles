# Global instructions

## 1. Character set

Use ASCII characters only in all project-related content unless the user explicitly requests otherwise.

## 2. Git safety

### 2.1. Preserve before modifying

Before modifying project files, check whether existing changes have been saved in a commit, stash, or patch.

If not, warn the user and offer to create a Git stash before proceeding.

This warning is mandatory regardless of permissions or approval settings.

### 2.2. Protect the working branch

Use a temporary branch, stash, or patch by default.

Do not create commits on the user's working branch unless explicitly requested.

Ask for a commit message before committing to the user's working branch if none is provided.

### 2.3. Roll back only from Git

Without Git history, do not perform rollback or attempt to recover a previous file state based on model inference.

## 3. Comments

Do not modify existing comments as long as the commented code or commented section remains present unless the user explicitly requests it.
