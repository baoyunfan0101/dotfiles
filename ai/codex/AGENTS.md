# Global instructions

## 1. Character set

Use ASCII characters only in all project-related content unless the user explicitly requests otherwise.

## 2. Git safety

Before modifying project files, check whether the current directory is inside a Git repository.

If no Git repository is detected, warn the user and recommend creating one before proceeding. This warning is mandatory regardless of permissions or approval settings.

Without Git history, do not perform rollback or any attempt to recover a previous file state based on model inference.

## 3. Comments

Do not modify existing comments as long as the commented code or commented section remains present unless the user explicitly requests it.
