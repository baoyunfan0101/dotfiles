---
name: delete-session
description: Delete local Codex session files identified by session ID, Codex deeplink, or relative session reference.
---

# Delete Session

Invoke with:

`/delete-session <target>`

Codex session files are stored at:

`~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDTHH-MM-SS-<session-id>.jsonl`

Resolve the target session, locate the corresponding JSONL file under the session storage path, and delete it.
