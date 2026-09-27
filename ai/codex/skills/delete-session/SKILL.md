---
name: delete-session
description: Deterministically delete local Codex conversations by thread, project, or all conversations. Does not delete server-side ChatGPT conversations.
---

# Delete Session

Route the user's request to one command, then invoke the script once:

- One conversation: `python3 <skill-dir>/scripts/delete_sessions.py thread '<target>'`
- A project's conversations: `python3 <skill-dir>/scripts/delete_sessions.py project '<name-or-path>'`
- All local conversations: `python3 <skill-dir>/scripts/delete_sessions.py all`

Thread targets accept Codex thread links, UUIDs, rollout paths, or exact titles.
Projects are preserved by default. Pass `--delete-projects` only when the user
explicitly requests removing project metadata too; never infer it from vague
wording. Pass `--dry-run` for a requested preview. Quote targets as shell data.

Surface the JSON result concisely. On ambiguity, ask the user to select from the
returned candidates; never guess or fuzzy-match. On partial failure or unsupported
storage, report the limitation. Do not manually resolve targets, inspect schemas,
or mutate storage as a substitute for the script. See
[storage and exit codes](references/storage.md) when diagnosing script failures.
