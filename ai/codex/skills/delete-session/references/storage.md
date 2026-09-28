# Storage compatibility and failure semantics

The CLI uses only Python's standard library. `--codex-home PATH` (after the
subcommand) overrides CODEX_HOME, otherwise ~/.codex is used. Tests always supply
an isolated temporary home. No command deletes a working directory.

Supported adapters live in delete_sessions.py:

- sqlite/codex-dev.db: local catalog, scan entries, timeline, revision, sync state
  and scan checkpoints. Host filtering preserves remote and ChatGPT catalog rows.
- state_5.sqlite: app-server threads, dynamic tools, attachments, spawn edges,
  projects, project_roots and project_idempotency_keys.
- thread_history_1.sqlite: turns, items, realtime items and projection state.
- sessions and archived_sessions: exact rollout IDs and session_meta payloads.
- .codex-global-state.json: local-projects and its known project display and
  migration mappings; known thread read/membership/order maps.

Project resolution prefers the app-server registry, with mapped legacy IDs from
local-projects. Empty projects are resolvable. If neither registry exists,
exact cwd/name resolution uses known thread working directories; project deletion
is unsupported. Explicit project assignments take precedence over cwd fallback.
Unknown versions, incompatible known tables, or unknown thread-ID tables abort
before mutation. Unrelated databases and global-state keys remain untouched.

Dry runs use read-only SQLite connections and never create directories or files.
Execution preflights resolution, schema, rollout containment and JSON shapes.
Database transactions commit individually, then exact rollout files are removed
and absence is verified. Project cleanup is separate and occurs only after thread
verification. Global state uses a compare-before-replace check and atomic file
replacement. These stores do not support one cross-store transaction: any error
after a committed mutation returns partial_failure. Close Codex before large
cleanups to avoid concurrent writers; the script cannot prevent a running app
from recreating state later. Retry the same target after resolving the reported
failure; a missing single thread returns not_found safely.

The script deletes attachment metadata, not attached worktrees or other files.
It does not delete auth, configuration, skills, plugins, memories or project
working directories. Global sync reset affects local hosts only. Catalog revision
increments once per desktop transaction with changed rows.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Deleted or dry run |
| 1 | Not found (including already deleted single thread) |
| 2 | Ambiguous target; no mutation |
| 3 | Invalid CLI input |
| 4 | Database error before a committed mutation |
| 5 | Filesystem error before a committed mutation |
| 6 | Unsupported schema; no mutation |
| 7 | Unsupported project deletion; no mutation |
| 8 | Partial failure after mutation or failed verification |
