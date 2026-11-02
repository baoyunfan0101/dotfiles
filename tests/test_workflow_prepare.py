import json
from pathlib import Path
import shutil
import unittest

from workflow_helpers import WorkflowTestCase


class WorkflowPrepareTests(WorkflowTestCase):
    def test_prepare_backup_modes_and_methods(self):
        for method in ("stash", "commit"):
            for mode in ("none", "tracked", "untracked", "all"):
                with self.subTest(method=method, mode=mode):
                    self.save_settings(backup={"mode": mode, "method": method})
                    self.git("push", "-q", "origin", "main")
                    self.change()
                    self.git("add", "tracked")
                    self.change("other")
                    branch = f"feat/backup-{mode}-{method}"
                    result = self.run_cli("prepare", "--base", "main", "--branch-name", branch)
                    self.assertIn(f"backup={method if mode != 'none' else 'none'}", result.stdout)
                    tracked_selected = mode in ("tracked", "all")
                    untracked_selected = mode in ("untracked", "all")
                    self.assertEqual((self.repo / "tracked").read_text(),
                                     "original\n" if tracked_selected else "changed\n")
                    self.assertEqual(bool(self.git("diff", "--cached", "--name-only")),
                                     not tracked_selected)
                    self.assertEqual((self.repo / "other").exists(), not untracked_selected)
                    if not untracked_selected:
                        self.assertEqual((self.repo / "other").read_text(), "changed\n")
                    if mode != "none":
                        refs = "refs/stash" if method == "stash" else "refs/agent-workflow/backups/"
                        self.assertTrue(self.git("for-each-ref", "--format=%(refname)", refs))
                    self.git("restore", "--staged", "--worktree", "tracked")
                    (self.repo / "other").unlink(missing_ok=True)
                    self.git("switch", "main")

    def test_prepare_accepts_allowed_branch_types(self):
        for branch_type in ("feat", "fix", "chore", "docs", "refactor", "test",
                            "ci", "build", "perf", "style", "revert", "hotfix"):
            with self.subTest(branch_type=branch_type):
                branch = f"{branch_type}/branch-validation"
                result = self.run_cli("prepare", "--base", "main", "--branch-name", branch)
                self.assertIn(f"branch={branch} created=true", result.stdout)
                self.git("switch", "main")

    def test_prepare_uses_sole_configured_base_and_rejects_ambiguity(self):
        result = self.run_cli("prepare", "--branch-name", "feat/implicit-base")
        self.assertIn("branch=feat/implicit-base created=true", result.stdout)
        self.git("switch", "main")
        self.settings(branch={"baseBranches": ["main", "develop"]})
        before = self.git_snapshot()

        result = self.run_cli("prepare", "--branch-name", "feat/ambiguous-base", ok=False)

        self.assertIn("ambiguous base", result.stderr)
        self.assertEqual(before, self.git_snapshot())

    def test_prepare_rejects_invalid_branch_names_without_mutation(self):
        self.git("branch", "feat/existing")
        for branch, message in (
            ("", "--branch-name is required"),
            ("codex/delete-session", "invalid branch type: codex"),
            ("claude/delete-session", "invalid branch type: claude"),
            ("agent/delete-session", "invalid branch type: agent"),
            ("feature/delete-session", "invalid branch type: feature"),
            ("delete-session", "branch name must use <type>/<description>: delete-session"),
            ("feat/foo..bar", "invalid branch name: feat/foo..bar"),
            ("feat/existing", "branch already exists: feat/existing"),
        ):
            with self.subTest(branch=branch):
                before = self.git_snapshot()
                result = self.run_cli("prepare", "--base", "main", "--branch-name", branch, ok=False)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.git_snapshot(), before)

    def test_prepare_rejects_invalid_branch_before_backup_or_sync(self):
        self.save_settings(backup={"mode": "tracked", "method": "commit"},
                           sync={"mode": "fetch"})
        self.change()
        before = self.git_snapshot()
        result = self.run_cli("prepare", "--base", "main", "--branch-name", "codex/invalid", ok=False)
        self.assertIn("invalid branch type: codex", result.stderr)
        self.assertEqual(self.git_snapshot(), before)
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)",
                                  "refs/agent-workflow/backups/"), "")

    def test_prepare_explicit_base_rejects_invalid_branch_without_mutation(self):
        before = self.git_snapshot()
        result = self.run_cli("prepare", "--base", "main", "--branch-name",
                              "codex/invalid", ok=False)
        self.assertIn("invalid branch type: codex", result.stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_prepare_explicitly_continues_existing_branch(self):
        self.git("switch", "-c", "codex/legacy")
        before = self.git_snapshot()
        result = self.run_cli("prepare", "--continue")
        self.assertIn("branch=codex/legacy created=false", result.stdout)
        self.assertEqual(self.git_snapshot(), before)

    def test_prepare_continue_skips_update_without_upstream(self):
        self.save_settings(sync={"mode": "update"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        before = self.git_snapshot()
        tracking = self.git("for-each-ref", "--format=%(upstream)",
                            "refs/heads/feat/test")
        self.assertEqual(tracking, "")

        operations = self.root / "git-operations"
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'case "$1" in fetch|pull|merge|rebase|push) '
                            'printf "%s\\n" "$1" >> "$GIT_OPERATION_LOG";; esac\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        self.env["GIT_OPERATION_LOG"] = str(operations)

        result = self.run_cli("prepare", "--continue")

        self.assertEqual(result.stdout,
                         "[git] prepare ok backup=none sync=skipped "
                         "branch=feat/test created=false\n")
        self.assertEqual(self.git_snapshot(), before)
        self.assertEqual(self.git("for-each-ref", "--format=%(upstream)",
                                  "refs/heads/feat/test"), tracking)
        self.assertFalse(operations.exists())

    def test_prepare_continue_skips_update_after_local_commit(self):
        self.save_settings(sync={"mode": "update"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.git("add", "tracked")
        self.git("commit", "-qm", "Local unpublished change")
        before = self.git_snapshot()

        result = self.run_cli("prepare", "--continue")

        self.assertIn("sync=skipped branch=feat/test created=false", result.stdout)
        self.assertEqual(self.git_snapshot(), before)
        self.assertEqual(self.git("for-each-ref", "--format=%(upstream)",
                                  "refs/heads/feat/test"), "")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")

    def test_prepare_continue_updates_configured_upstream_by_method(self):
        for method in ("ffOnly", "rebase", "merge"):
            with self.subTest(method=method):
                self.save_settings(sync={"mode": "update", "updateMethod": method})
                self.git("push", "-q", "origin", "main")
                branch = f"feat/update-{method.lower()}"
                self.run_cli("prepare", "--branch-name", branch)
                self.git("push", "-qu", "origin", branch)
                self.advance_remote_branch(branch)
                remote_head = self.git("ls-remote", "--heads", "origin", branch).split()[0]
                if method != "ffOnly":
                    (self.repo / "local-note").write_text(f"local {method}\n")
                    self.git("add", "local-note")
                    self.git("commit", "-qm", "Local change")

                result = self.run_cli("prepare", "--continue")

                self.assertIn(f"sync=updated branch={branch} created=false", result.stdout)
                self.assertEqual(self.git("branch", "--show-current"), branch)
                self.assertEqual(self.git("merge-base", "--is-ancestor",
                                          remote_head, "HEAD"), "")
                self.assertEqual(self.git("for-each-ref", "--format=%(upstream:short)",
                                          f"refs/heads/{branch}"), f"origin/{branch}")
                self.git("switch", "main")

    def test_prepare_continue_rejects_partial_upstream(self):
        self.save_settings(sync={"mode": "update"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        for key, value in (("remote", "origin"), ("merge", "refs/heads/feat/test")):
            with self.subTest(key=key):
                self.git("config", f"branch.feat/test.{key}", value)
                before = self.git_snapshot()
                result = self.run_cli("prepare", "--continue", ok=False)
                self.assertIn("invalid upstream configuration", result.stderr)
                self.assertEqual(self.git_snapshot(), before)
                self.git("config", "--unset", f"branch.feat/test.{key}")

    def test_prepare_continue_rejects_invalid_upstream(self):
        self.save_settings(sync={"mode": "update"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        for remote, merge, message in (
            (".", "refs/heads/feat/test", "invalid upstream configuration"),
            ("origin", "not-a-head", "invalid upstream configuration"),
            ("missing", "refs/heads/feat/test", "upstream remote is unavailable"),
            ("origin", "refs/heads/missing", "upstream branch is unavailable"),
        ):
            with self.subTest(remote=remote, merge=merge):
                self.git("config", "branch.feat/test.remote", remote)
                self.git("config", "branch.feat/test.merge", merge)
                before = self.git_snapshot()
                result = self.run_cli("prepare", "--continue", ok=False)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.git_snapshot(), before)

    def test_prepare_continue_without_upstream_keeps_backup_behavior(self):
        self.save_settings(sync={"mode": "update"},
                           backup={"mode": "tracked", "method": "stash"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        original_head = self.git("rev-parse", "HEAD")
        self.change()
        self.git("add", "tracked")

        result = self.run_cli("prepare", "--continue")

        self.assertIn("backup=stash sync=skipped branch=feat/test created=false",
                      result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual((self.repo / "tracked").read_text(), "original\n")
        self.assertTrue(self.git("stash", "list"))

    def test_prepare_does_not_implicitly_reuse_working_branch(self):
        self.prepare()
        self.change()
        self.commit()
        old_head = self.git("rev-parse", "HEAD")
        for args in ((), ("--base", "main"),
                     ("--continue", "--branch-name", "feat/next"),
                     ("--continue", "--base", "main")):
            with self.subTest(args=args):
                before = self.git_snapshot()
                result = self.run_cli("prepare", *args, ok=False)
                self.assertIn("[git] prepare error", result.stderr)
                self.assertEqual(self.git_snapshot(), before)
        self.run_cli("prepare", "--branch-name", "feat/next")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "main"))
        self.assertEqual(self.git("rev-parse", "feat/test"), old_head)

    def test_prepare_requires_one_complete_operation_before_mutation(self):
        for arguments in ((), ("--base", "main"),
                          ("--continue", "--branch-name", "feat/next"),
                          ("--continue", "--base", "main"),
                          ("--continue", "--branch-name", "")):
            with self.subTest(arguments=arguments):
                before = self.git_snapshot()
                self.assertIn("[git] prepare error",
                              self.run_cli("prepare", *arguments, ok=False).stderr)
                self.assertEqual(self.git_snapshot(), before)
        self.assertIn("--continue requires a working branch",
                      self.run_cli("prepare", "--continue", ok=False).stderr)

    def test_prepare_new_task_ignores_pr_state(self):
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        for pr_state in ("none", "open", "merged"):
            with self.subTest(pr_state=pr_state):
                self.git("switch", "main")
                self.stub_gh()
                state_path = Path(self.env["GH_STATE"])
                state_path.unlink(missing_ok=True)
                branch = f"feat/previous-{pr_state}"
                self.run_cli("prepare", "--base", "main", "--branch-name", branch)
                self.change()
                self.commit()
                if pr_state != "none":
                    self.run_cli("pr", "submit", "--stay")
                    if pr_state == "merged":
                        state = json.loads(state_path.read_text())
                        state["state"] = "MERGED"
                        state_path.write_text(json.dumps(state))
                old_head = self.git("rev-parse", "HEAD")
                next_branch = f"feat/next-{pr_state}"
                self.run_cli("prepare", "--base", "main", "--branch-name", next_branch)
                self.assertEqual(self.git("branch", "--show-current"), next_branch)
                self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "main"))
                self.assertEqual(self.git("rev-parse", branch), old_head)

    def test_prepare_explicitly_continues_open_pr(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--stay")
        before = self.git("rev-parse", "HEAD")
        self.assertIn("created=false", self.run_cli("prepare", "--continue").stdout)
        (self.repo / "review-fix").write_text("feedback\n")
        self.commit("review-fix")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), before)
        self.assertIn(self.git("rev-parse", "HEAD"),
                      self.git("ls-remote", "--heads", "origin", "feat/test"))
        self.run_cli("pr", "submit", "--stay")
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in self.gh_calls()), 1)

    def test_prepare_continuation_does_not_inspect_pr_state(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        for state in ("none", "OPEN", "CLOSED", "MERGED"):
            with self.subTest(state=state):
                self.git("switch", "main")
                branch = f"feat/continue-{state.lower()}"
                self.run_cli("prepare", "--base", "main", "--branch-name", branch)
                self.change()
                self.commit()
                state_path = Path(self.env["GH_STATE"])
                state_path.write_text(json.dumps({"state": state, "head": branch,
                                                  "base": "main", "url": "https://example.invalid/pr/1"}))
                gh_before = self.gh_calls()
                result = self.run_cli("prepare", "--continue")
                self.assertIn(f"branch={branch} created=false", result.stdout)
                self.assertEqual(self.gh_calls(), gh_before)

    def assert_prepare_explicit_base_uses_sync_mode(self, sync_mode):
        self.save_settings(sync={"mode": sync_mode, "updateMethod": "ffOnly"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        old_branch_head = self.git("rev-parse", "HEAD")
        old_main = self.git("rev-parse", "main")
        remote_tip = self.git("--git-dir", str(self.remote), "commit-tree", "main^{tree}",
                              "-p", old_main, "-m", "Remote base advance")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main", remote_tip)
        (self.repo / "user-note").write_text("protected\n")

        result = self.run_cli("prepare", "--base", "main", "--branch-name", "feat/next")
        self.assertIn("branch=feat/next created=true", result.stdout)
        self.assertIn(f"sync={dict(none='skipped', fetch='fetched', update='updated')[sync_mode]}",
                      result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "feat/next")
        expected_head = remote_tip if sync_mode == "update" else old_main
        self.assertEqual(self.git("rev-parse", "HEAD"), expected_head)
        self.assertEqual(self.git("rev-parse", "main"), expected_head)
        self.assertEqual(self.git("rev-parse", "origin/main"),
                         old_main if sync_mode == "none" else remote_tip)
        self.assertEqual(self.git("rev-parse", "feat/test"), old_branch_head)
        self.assertFalse((self.repo / "user-note").exists())
        self.assertTrue(self.git("stash", "list"))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_prepare_explicit_base_uses_local_base_when_sync_none(self):
        self.assert_prepare_explicit_base_uses_sync_mode("none")

    def test_prepare_explicit_base_fetches_without_advancing_base(self):
        self.assert_prepare_explicit_base_uses_sync_mode("fetch")

    def test_prepare_explicit_base_starts_from_latest_base_when_sync_update(self):
        self.assert_prepare_explicit_base_uses_sync_mode("update")

    def test_prepare_explicit_base_none_works_offline_without_fetch(self):
        self.save_settings(sync={"mode": "none"})
        base_head = self.git("rev-parse", "main")
        self.git("remote", "remove", "origin")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == fetch || "$1" == ls-remote ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        result = self.run_cli("prepare", "--branch-name", "feat/offline")
        self.assertIn("sync=skipped", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), base_head)

    def test_prepare_explicit_base_fetch_allows_divergence(self):
        self.save_settings(sync={"mode": "fetch"})
        self.git("push", "-q", "origin", "main")
        base_head, remote_head = self.diverge_main_with_conflict()
        result = self.run_cli("prepare", "--branch-name", "feat/diverged")
        self.assertIn("sync=fetched", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), base_head)
        self.assertEqual(self.git("rev-parse", "main"), base_head)
        self.assertEqual(self.git("rev-parse", "origin/main"), remote_head.split()[0])

    def test_prepare_fetch_refreshes_rewritten_remote_base_without_moving_local_base(self):
        self.save_settings(sync={"mode": "fetch"})
        self.git("push", "-q", "origin", "main")
        local_base = self.git("rev-parse", "main")
        rewritten = self.git("--git-dir", str(self.remote), "commit-tree",
                             "main^{tree}", "-m", "Rewritten remote base")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main",
                 rewritten)

        result = self.run_cli("prepare", "--branch-name", "feat/rewritten-fetch")

        self.assertIn("sync=fetched", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "feat/rewritten-fetch")
        self.assertEqual(self.git("rev-parse", "HEAD"), local_base)
        self.assertEqual(self.git("rev-parse", "main"), local_base)
        self.assertEqual(self.git("rev-parse", "origin/main"), rewritten)

    def test_prepare_update_rejects_rewritten_remote_base_and_retains_backup(self):
        self.save_settings(sync={"mode": "update"})
        self.git("push", "-q", "origin", "main")
        local_base = self.git("rev-parse", "main")
        rewritten = self.git("--git-dir", str(self.remote), "commit-tree",
                             "main^{tree}", "-m", "Rewritten remote base")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main",
                 rewritten)
        (self.repo / "user-note").write_text("protected\n")

        result = self.run_cli("prepare", "--branch-name", "feat/rewritten-update",
                              ok=False)

        self.assertIn("local base diverged", result.stderr)
        self.assertIn("backup retained", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD"), local_base)
        self.assertEqual(self.git("rev-parse", "main"), local_base)
        self.assertEqual(self.git("rev-parse", "origin/main"), rewritten)
        self.assertEqual(self.git("branch", "--list", "feat/rewritten-update"), "")
        self.assertFalse((self.repo / "user-note").exists())
        self.assertTrue(self.git("stash", "list"))

    def test_prepare_none_ignores_rewritten_remote_base(self):
        self.save_settings(sync={"mode": "none"})
        self.git("push", "-q", "origin", "main")
        local_base = self.git("rev-parse", "main")
        rewritten = self.git("--git-dir", str(self.remote), "commit-tree",
                             "main^{tree}", "-m", "Rewritten remote base")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main",
                 rewritten)
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == fetch || "$1" == ls-remote ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("prepare", "--branch-name", "feat/rewritten-offline")

        self.assertIn("sync=skipped", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), local_base)
        self.assertEqual(self.git("rev-parse", "main"), local_base)
        self.assertEqual(self.git("rev-parse", "origin/main"), local_base)
        self.assertEqual(self.git("--git-dir", str(self.remote), "rev-parse", "main"),
                         rewritten)

    def test_prepare_explicit_base_validates_before_mutation(self):
        self.prepare()
        for arguments, message in (
            (("--base", "outside", "--branch-name", "feat/next"), "base is not configured"),
            (("--base", "main"), "new work requires --branch-name"),
            (("--base", "main", "--branch-name", "feat/test"), "branch already exists"),
        ):
            with self.subTest(arguments=arguments):
                before = self.git_snapshot()
                self.assertIn(message, self.run_cli("prepare", *arguments, ok=False).stderr)
                self.assertEqual(self.git_snapshot(), before)

    def test_prepare_explicit_base_divergence_preserves_local_history(self):
        self.save_settings(sync={"mode": "update"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        working_head = self.git("rev-parse", "HEAD")
        self.git("switch", "main")
        base_head, remote_head = self.diverge_main_with_conflict()
        self.git("switch", "feat/test")
        (self.repo / "user-note").write_text("protected\n")

        result = self.run_cli("prepare", "--base", "main", "--branch-name", "feat/next", ok=False)
        self.assertIn("local base diverged", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("rev-parse", "HEAD"), working_head)
        self.assertEqual(self.git("rev-parse", "main"), base_head)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main"), remote_head)
        self.assertFalse((self.repo / "user-note").exists())
        self.assertTrue(self.git("stash", "list"))
        self.assertEqual(self.git("branch", "--list", "feat/next"), "")
        self.assert_no_in_progress_git_operation()

    def test_prepare_explicit_base_fetch_and_update_failures_retain_backup(self):
        self.save_settings(sync={"mode": "update"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        original_head = self.git("rev-parse", "HEAD")
        base_head = self.git("rev-parse", "main")
        remote_tip = self.git("--git-dir", str(self.remote), "commit-tree",
                              "main^{tree}", "-p", base_head, "-m", "Remote base advance")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main", remote_tip)
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"

        for operation, condition in (
            ("fetch", '"$1" == fetch'),
            ("update", '"$1" == merge && "$2" == --quiet && "$3" == --ff-only'),
        ):
            with self.subTest(operation=operation):
                self.change()
                self.git("add", "tracked")
                (self.repo / "user-note").write_text("protected\n")
                git_stub.write_text('#!/usr/bin/env bash\n'
                                    f'if [[ {condition} ]]; then exit 1; fi\n'
                                    f'exec "{git_executable}" "$@"\n')
                git_stub.chmod(0o755)
                result = self.run_cli("prepare", "--base", "main", "--branch-name", "feat/next", ok=False)
                self.assertIn(f"base {operation} failed", result.stderr)
                self.assertEqual(self.git("branch", "--show-current"), "feat/test")
                self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
                self.assertEqual(self.git("rev-parse", "main"), base_head)
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assertIn("backup retained", result.stderr)
                self.assertEqual((self.repo / "tracked").read_text(), "original\n")
                self.assertFalse((self.repo / "user-note").exists())
                self.assertTrue(self.git("stash", "list"))
                self.assertEqual(self.git("branch", "--list", "feat/next"), "")
                self.assert_no_in_progress_git_operation()

    def test_prepare_explicit_base_never_applies_backup(self):
        self.save_settings(sync={"mode": "none"})
        self.prepare()
        (self.repo / "user-note").write_text("protected\n")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == stash && "$2" == apply ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        original_head = self.git("rev-parse", "HEAD")

        result = self.run_cli("prepare", "--base", "main", "--branch-name", "feat/next")
        self.assertIn("branch=feat/next created=true", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "feat/next")
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse((self.repo / "user-note").exists())
        self.assertTrue(self.git("stash", "list"))

    def test_prepare_explicit_base_branch_creation_failure_retains_backup(self):
        self.save_settings(sync={"mode": "none"})
        self.prepare()
        original_head = self.git("rev-parse", "HEAD")
        (self.repo / "user-note").write_text("protected\n")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == checkout && "$2" == -q && "$3" == -b ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("prepare", "--base", "main", "--branch-name", "feat/next", ok=False)
        self.assertIn("branch creation failed", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertFalse((self.repo / "user-note").exists())
        self.assertTrue(self.git("stash", "list"))
        self.assertEqual(self.git("branch", "--list", "feat/next"), "")
        self.assert_no_in_progress_git_operation()


if __name__ == "__main__":
    unittest.main()
