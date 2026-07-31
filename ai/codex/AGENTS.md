# Global instructions

## Pre-write gate

- Before the first project write in each Codex turn, run the pre-write gate once to ensure Git, snapshot dirty worktrees, and print `repo=<repo-root>`.

  Command:

  ```bash
  CODEX_TURN_ID="<current-turn-id>" ~/.codex/bin/pre-write-gate
  ```

- Handle pre-write gate results.
  - `state=clean`: continue.
  - `state=dirty`: report `[pre-write-gate] INFO snapshot: snapshot_dir=<snapshot-dir>`, then continue.
  - `state=already-snapshotted`: continue.
  - Failure: report `[pre-write-gate] ERROR abort: output="<command-output>"`, and do not write project files.

## Authoring rules

### Logs

- Emit each workflow event log immediately when the event happens.

- Repeat all workflow event logs only at the start of the final summary as a Markdown blockquote.

### Responses

- Match the language of the user's latest message unless explicitly requested otherwise.

- When showing Markdown that contains fenced code blocks, wrap the outer block with `~~~md`.

### Project files

- Use only ASCII characters unless explicitly requested otherwise.

- Preserve existing comments for code or sections that remain present unless explicitly requested otherwise.

## Branch workflow

### Lifecycle

- Start each new task from a short-lived branch based on the latest `main`; if already on a non-`main` branch, continue on the current branch.

- Keep each branch scoped to one task, and finish it only when explicitly asked to finish the current task or start a new task.

- When finishing the current branch, merge it back into `main`.
  - Personal work: run `git switch main`, `git pull --ff-only`, and `git merge --no-ff <branch-name>`.
  - Team work: create a PR and squash merge it. Follow the repository's existing PR title and description style when available; otherwise, use the default PR format below.

    Title:

    ```text
    <type>: <concise-summary>
    ```

    Description:

    ```md
    ## Summary

    - <summary-item>

    ## Verification

    - <verification-item>
    ```

- Delete branches only when explicitly requested.

### Naming

- Follow the repository's existing branch naming style when available; otherwise, use the default branch format below.

  Format:

  ```text
  <type>/<area>-<short-kebab-description>
  ```

  Type prefixes:

  ```text
  feat, fix, chore, docs, refactor, test, ci, build, perf, style, revert, hotfix
  ```

  Area:

  Use a concrete touched directory or module.

### Reporting

- Report branch lifecycle events only when they happen, using the reporting format below.

  Format:

  ```text
  [branch] INFO create: name=<branch-name> base=<base-branch>
  [branch] INFO continue: name=<branch-name>
  [branch] INFO finish: name=<branch-name> target=<target-branch> method=<merge-method>
  [branch] INFO pr: title=<pr-title> url=<pr-url> method=squash
  [branch] INFO delete: name=<branch-name> location=<local|remote|local-and-remote>
  ```

## Commit workflow

### Lifecycle

- Before making commit or push decisions, run `~/.codex/bin/project-settings commit_mode` and handle `commit_mode`.
  - `commit_mode=manual`: do not commit or push unless explicitly requested.
  - `commit_mode=automatic`: commit each atomic change separately, then push the resulting commits.
  - Failure: report `[commit] ERROR project-settings: output="<command-output>"`, and do not commit or push unless explicitly requested.

### Naming

- Follow the repository's existing commit message style when available; otherwise, use the default commit format below.

  Format:

  ```text
  <type>(<area>): <concise-summary>
  ```

  Type prefixes:

  ```text
  feat, fix, chore, docs, refactor, test, ci, build, perf, style, revert, hotfix
  ```

  Area:

  Use a concrete touched directory or module.

### Reporting

- Report commit lifecycle events only when they happen, using the reporting format below.

  Format:

  ```text
  [commit] INFO create: sha=<commit-sha> message="<commit-message>"
  [commit] INFO push: remote=<remote-name> branch=<branch-name>
  [commit] ERROR push: output="<command-output>"
  ```
