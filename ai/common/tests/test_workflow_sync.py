import unittest

from workflow_helpers import WorkflowTestCase


class WorkflowSyncTests(WorkflowTestCase):
    def test_sync_fast_forwards_checked_out_base_after_remote_merge(self):
        self.prepare()
        self.advance_remote_branch("main")
        remote_head = self.git("ls-remote", "--heads", "origin", "main").split()[0]
        self.run_cli("branch", "switch", "main")
        old_head = self.git("rev-parse", "HEAD")

        result = self.run_cli("sync")

        self.assertIn("[git] sync ok branch=main result=updated", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD"), remote_head)
        self.assertEqual(self.git("rev-parse", "HEAD^"), old_head)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_sync_already_current_preserves_history(self):
        before = self.git_snapshot()

        result = self.run_cli("sync")

        self.assertIn("[git] sync ok branch=main result=up-to-date", result.stdout)
        self.assertEqual(before, self.git_snapshot())

    def test_sync_uses_configured_upstream_branch_name(self):
        self.git("push", "-q", "origin", "main:tracked-base")
        self.git("branch", "--set-upstream-to=origin/tracked-base", "main")
        self.advance_remote_branch("tracked-base")
        remote_head = self.git("ls-remote", "--heads", "origin", "tracked-base").split()[0]

        result = self.run_cli("sync")

        self.assertIn("[git] sync ok branch=main result=updated", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), remote_head)
        self.assertNotEqual(self.git("rev-parse", "origin/main"), remote_head)

    def test_sync_rejects_dirty_worktree_without_fetch(self):
        self.advance_remote_branch("main")
        self.change()
        before = self.git_snapshot()
        tracking_head = self.git("rev-parse", "origin/main")

        result = self.run_cli("sync", ok=False)

        self.assertIn("working tree has local changes", result.stderr)
        self.assertEqual(before, self.git_snapshot())
        self.assertEqual(self.git("rev-parse", "origin/main"), tracking_head)

    def test_sync_rejects_non_base_branch(self):
        self.prepare()
        before = self.git_snapshot()

        result = self.run_cli("sync", ok=False)

        self.assertIn("not a configured base branch", result.stderr)
        self.assertEqual(before, self.git_snapshot())

    def test_sync_rejects_diverged_history_without_reconciliation(self):
        local_head, _ = self.diverge_main_with_conflict()
        before = self.git_snapshot()

        result = self.run_cli("sync", ok=False)

        self.assertIn("cannot fast-forward base branch", result.stderr)
        self.assertEqual(before, self.git_snapshot())
        self.assertEqual(self.git("rev-parse", "HEAD"), local_head)
        self.assert_no_in_progress_git_operation()

    def test_sync_rejects_local_history_ahead_of_remote(self):
        self.change()
        self.git("add", "tracked")
        self.git("commit", "-qm", "Local base advance")
        before = self.git_snapshot()

        result = self.run_cli("sync", ok=False)

        self.assertIn("cannot fast-forward base branch", result.stderr)
        self.assertEqual(before, self.git_snapshot())

    def test_sync_requires_usable_remote_tracking(self):
        self.git("branch", "--unset-upstream")
        before = self.git_snapshot()
        result = self.run_cli("sync", ok=False)
        self.assertIn("no usable remote tracking configuration", result.stderr)
        self.assertEqual(before, self.git_snapshot())

        self.git("branch", "--set-upstream-to=origin/main")
        self.git("config", "branch.main.remote", "missing")
        before = self.git_snapshot()
        result = self.run_cli("sync", ok=False)
        self.assertIn("tracking remote is unavailable", result.stderr)
        self.assertEqual(before, self.git_snapshot())

        self.git("config", "branch.main.remote", "origin")
        self.git("config", "branch.main.merge", "refs/heads/missing")
        before = self.git_snapshot()
        result = self.run_cli("sync", ok=False)
        self.assertIn("cannot fetch tracked remote branch", result.stderr)
        self.assertEqual(before, self.git_snapshot())


if __name__ == "__main__":
    unittest.main()
