import json
import shutil
import unittest

from workflow_helpers import WorkflowTestCase, COMMON


class WorkflowCommitTests(WorkflowTestCase):
    def test_atomic_paths_and_auto_commit_and_push(self):
        self.prepare()
        self.change()
        self.change("other")
        self.git("add", "other")
        result = self.commit()
        self.assertIn("pushed=true", result.stdout)
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"), "tracked")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "other")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

    def test_successive_logical_subtasks_create_separate_commits(self):
        self.prepare()
        self.change()
        self.commit("tracked")
        (self.repo / "other").write_text("second subtask\n")

        second = self.run_cli("commit", "--message", "Second subtask", "--", "other")

        self.assertIn("pushed=true", second.stdout)
        self.assertEqual(self.git("rev-list", "--count", "main..HEAD"), "2")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

    def test_amend_selected_paths_and_message(self):
        self.prepare()
        (self.repo / "other").write_text("original other\n")
        self.run_cli("commit", "--message", "Original", "--", "other")
        old_head = self.git("rev-parse", "HEAD")
        (self.repo / "tracked").write_text("amended tracked\n")
        (self.repo / "other").write_text("unselected other\n")

        result = self.run_cli("commit", "--amend", "--", "tracked")
        self.assertIn("pushed=true", result.stdout)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), old_head)
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Original")
        self.assertEqual(self.git("show", "HEAD:tracked"), "amended tracked")
        self.assertEqual(self.git("show", "HEAD:other"), "original other")
        self.assertEqual((self.repo / "other").read_text(), "unselected other\n")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

    def test_amend_all_replaces_message_and_blocks_base(self):
        before = self.git_snapshot()
        self.assertIn("configured base branch",
                      self.run_cli("commit", "--amend", "--all", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)
        self.prepare()
        self.change()
        (self.repo / "other").write_text("new other\n")
        self.commit()
        old_head = self.git("rev-parse", "HEAD")
        self.change("other")
        self.run_cli("commit", "--amend", "--message", "Revised", "--all")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), old_head)
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Revised")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_unpublished_amend_uses_normal_push(self):
        self.prepare()
        self.change()
        self.git("add", "tracked")
        self.git("commit", "-qm", "Unpublished")
        (self.repo / "tracked").write_text("amended\n")
        log = self.root / "push.log"
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            f'if [[ "$1" == push ]]; then printf "%s\\n" "$*" >> "{log}"; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        self.run_cli("commit", "--amend", "--all")
        self.assertNotIn("--force", log.read_text())
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

    def test_published_amend_uses_lease_and_rejects_race(self):
        self.prepare()
        self.change()
        self.commit()
        old_head = self.git("rev-parse", "HEAD")
        self.change("other")
        log = self.root / "push.log"
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            f'if [[ "$1" == push ]]; then printf "%s\\n" "$*" >> "{log}"; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        self.run_cli("commit", "--amend", "--all")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), old_head)
        self.assertIn("--force-with-lease=", log.read_text())
        self.assertNotIn(" --force ", f" {log.read_text()} ")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

        old_remote = self.git("rev-parse", "HEAD")
        remote_advance = self.git("--git-dir", str(self.remote), "commit-tree",
                                  f"{old_remote}^{{tree}}", "-p", old_remote,
                                  "-m", "Concurrent remote update")
        (self.repo / "tracked").write_text("second amendment\n")
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push && "$*" == *--force-with-lease* ]]; then\n'
                            f'  "{git_executable}" --git-dir "{self.remote}" update-ref refs/heads/feat/test {remote_advance}\n'
                            'fi\n'
                            f'exec "{git_executable}" "$@"\n')
        result = self.run_cli("commit", "--amend", "--all", ok=False)
        self.assertIn("lease-safe push failed", result.stderr)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0],
                         remote_advance)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_failed_amend_restores_original_index(self):
        self.prepare()
        self.change()
        self.commit()
        original_head = self.git("rev-parse", "HEAD")
        (self.repo / "staged").write_text("preexisting staged\n")
        self.git("add", "staged")
        (self.repo / "tracked").write_text("candidate amendment\n")
        original_status = self.git("status", "--porcelain")
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

        result = self.run_cli("commit", "--amend", "--all", ok=False)
        self.assertIn("original index was restored", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("status", "--porcelain"), original_status)
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), original_head)

    def test_all_and_invalid_commit(self):
        self.prepare()
        self.change()
        self.change("other")
        self.run_cli("commit", "--message", "All", "--all")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertIn("no changes", self.run_cli("commit", "--message", "Empty", "--all", ok=False).stderr)
        self.run_cli("commit", "--message", "Invalid", "--all", "--", "tracked", ok=False)
        self.run_cli("commit", "--all", ok=False)

    def test_manual_commit_and_push(self):
        self.save_settings(commit={"mode": "manual"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        before = self.git_snapshot()
        self.assertIn("skip reason=manual", self.commit().stdout)
        self.assertEqual(self.git_snapshot(), before)
        self.assertIn("pushed=false", self.run_cli("commit", "--override-manual",
                      "--message", "Manual", "--", "tracked").stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")
        (self.repo / "tracked").write_text("manual amended\n")
        amended = self.run_cli("commit", "--amend", "--override-manual", "--all")
        self.assertIn("pushed=false", amended.stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")
        self.assertIn("skip reason=manual", self.run_cli("push").stdout)
        self.run_cli("push", "--override-manual")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

    def test_auto_commit_mode_commits_locally_and_keeps_explicit_push(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        before = self.git("rev-parse", "HEAD")

        result = self.commit()

        self.assertIn("pushed=false", result.stdout)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")
        pushed = self.run_cli("push")
        self.assertIn("push ok branch=feat/test", pushed.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

    def test_auto_commit_mode_amend_stays_local(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("push")
        remote_head = self.git("rev-parse", "origin/feat/test")
        (self.repo / "tracked").write_text("amended locally\n")

        result = self.run_cli("commit", "--amend", "--all")

        self.assertIn("pushed=false", result.stdout)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), remote_head)
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), remote_head)

        self.assertIn("non-fast-forward", self.run_cli("push", ok=False).stderr)
        rewritten = self.git("rev-parse", "HEAD")
        self.run_cli("push", "--rewrite")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0],
                         rewritten)
        self.assertIn("no recorded local rewrite", self.run_cli("push", "--rewrite", ok=False).stderr)

    def test_manual_published_amend_requires_both_overrides(self):
        self.save_settings(commit={"mode": "manual"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.run_cli("commit", "--override-manual", "--message", "Manual", "--all")
        self.run_cli("push", "--override-manual")
        old_remote = self.git("rev-parse", "origin/feat/test")
        (self.repo / "tracked").write_text("manual rewrite\n")

        self.run_cli("commit", "--amend", "--override-manual", "--all")
        rewritten = self.git("rev-parse", "HEAD")
        self.assertIn("skip reason=manual", self.run_cli("push", "--rewrite").stdout)
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), old_remote)
        self.run_cli("push", "--rewrite", "--override-manual")
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), rewritten)

    def test_rewrite_push_rejects_remote_change_and_preserves_local_work(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("push")
        (self.repo / "tracked").write_text("rewritten\n")
        self.run_cli("commit", "--amend", "--all")
        rewritten = self.git("rev-parse", "HEAD")
        (self.repo / "untracked").write_text("keep\n")
        self.advance_remote_branch("feat/test")
        remote_head = self.git("ls-remote", "--heads", "origin", "feat/test").split()[0]

        result = self.run_cli("push", "--rewrite", ok=False)
        self.assertIn("remote branch changed", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), rewritten)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0],
                         remote_head)
        self.assertEqual((self.repo / "untracked").read_text(), "keep\n")

    def test_rewrite_push_rejects_unrelated_local_history_and_base(self):
        self.assertIn("configured base branch",
                      self.run_cli("push", "--rewrite", ok=False).stderr)
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("push")
        (self.repo / "tracked").write_text("rewritten\n")
        self.run_cli("commit", "--amend", "--all")
        remote_head = self.git("rev-parse", "origin/feat/test")
        self.git("reset", "--hard", "main")
        (self.repo / "unrelated").write_text("different history\n")
        self.git("add", "unrelated")
        self.git("commit", "-qm", "Unrelated")
        unrelated = self.git("rev-parse", "HEAD")

        result = self.run_cli("push", "--rewrite", ok=False)
        self.assertIn("local branch diverged", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), unrelated)
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), remote_head)

    def test_rewrite_push_uses_lease_against_race(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("push")
        old_remote = self.git("rev-parse", "origin/feat/test")
        (self.repo / "tracked").write_text("rewritten\n")
        self.run_cli("commit", "--amend", "--all")
        rewritten = self.git("rev-parse", "HEAD")
        remote_advance = self.git("--git-dir", str(self.remote), "commit-tree",
                                  f"{old_remote}^{{tree}}", "-p", old_remote,
                                  "-m", "Concurrent remote update")
        log = self.root / "push.log"
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push && "$*" == *--force-with-lease* ]]; then\n'
                            f'  printf "%s\\n" "$*" >> "{log}"\n'
                            f'  "{git_executable}" --git-dir "{self.remote}" update-ref refs/heads/feat/test {remote_advance}\n'
                            'fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("push", "--rewrite", ok=False)
        self.assertIn("lease-safe rewrite push failed", result.stderr)
        self.assertIn(f"--force-with-lease=refs/heads/feat/test:{old_remote}", log.read_text())
        self.assertEqual(self.git("rev-parse", "HEAD"), rewritten)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0],
                         remote_advance)

    def test_rewrite_push_rejects_unrelated_remote_replacement(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("push")
        (self.repo / "tracked").write_text("rewritten\n")
        self.run_cli("commit", "--amend", "--all")
        rewritten = self.git("rev-parse", "HEAD")
        replacement = self.git("rev-parse", "main")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/feat/test", replacement)

        result = self.run_cli("push", "--rewrite", ok=False)
        self.assertIn("remote branch changed", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), rewritten)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0],
                         replacement)


    def test_commit_and_push_do_not_load_prepare_modules(self):
        runtime = (COMMON / "lib/git-workflow/runtime.sh").read_text()
        for module in ("backup.sh", "sync.sh", "branch.sh"):
            self.assertNotIn(f'source "$WORKFLOW_ROOT/lib/git-workflow/{module}"', runtime)
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("push")


if __name__ == "__main__":
    unittest.main()
