from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from workflow_helpers import WorkflowTestCase


class WorkflowOperationTests(WorkflowTestCase):
    def test_rebase_published_history_then_explicit_rewrite_push(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        (self.repo / "feature").write_text("feature\n")
        self.run_cli("commit", "--message", "Feature", "--", "feature")
        self.run_cli("push")
        old_remote = self.git("rev-parse", "origin/feat/test")
        self.advance_remote_branch("main")

        result = self.run_cli("rebase")
        rewritten = self.git("rev-parse", "HEAD")
        self.assertIn("pushed=false", result.stdout)
        self.assertNotEqual(rewritten, old_remote)
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), old_remote)
        self.run_cli("push", "--rewrite")
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), rewritten)

    def test_restore_only_selected_tracked_worktree_paths(self):
        self.prepare()
        (self.repo / "other").write_text("other original\n")
        self.run_cli("commit", "--message", "Add other", "--", "other")
        original_head = self.git("rev-parse", "HEAD")
        remote_head = self.git("ls-remote", "--heads", "origin", "feat/test")
        self.change()
        (self.repo / "other").write_text("other modified\n")
        (self.repo / "untracked").write_text("keep me\n")
        self.run_cli("restore", "--", "tracked")
        self.assertEqual((self.repo / "tracked").read_text(), "original\n")
        self.assertEqual((self.repo / "other").read_text(), "other modified\n")
        self.assertEqual((self.repo / "untracked").read_text(), "keep me\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), remote_head)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertIn("unknown restore option", self.run_cli("restore", "--staged", "--", "tracked", ok=False).stderr)
        self.assertIn("path is not tracked", self.run_cli("restore", "--", "untracked", ok=False).stderr)

    def test_restore_explicit_source(self):
        self.prepare()
        self.change()
        self.commit()
        original_head = self.git("rev-parse", "HEAD")
        self.run_cli("restore", "--source", "HEAD~1", "--", "tracked")
        self.assertEqual((self.repo / "tracked").read_text(), "original\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)

    def test_restore_rejects_missing_source_path_before_changes(self):
        self.prepare()
        (self.repo / "later").write_text("later content\n")
        self.run_cli("commit", "--message", "Add later", "--", "later")
        self.change()
        before = self.git_snapshot()
        result = self.run_cli("restore", "--source", "HEAD~1", "--", "later", "tracked", ok=False)
        self.assertIn("path is absent from source", result.stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_revert_creates_inverse_commit_and_rejects_merge_commit(self):
        self.prepare()
        self.change()
        self.commit()
        target = self.git("rev-parse", "HEAD")
        result = self.run_cli("revert", target)
        self.assertIn("pushed=true", result.stdout)
        self.assertEqual((self.repo / "tracked").read_text(), "original\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))
        self.assertTrue(self.git("merge-base", "--is-ancestor", target, "HEAD") == "")
        merge_sha = self.git("commit-tree", "HEAD^{tree}", "-p", "HEAD", "-p", "main",
                             "-m", "Synthetic merge")
        self.git("update-ref", "refs/heads/feat/test", merge_sha)
        self.assertIn("merge commits is not supported",
                      self.run_cli("revert", merge_sha, ok=False).stderr)

    def test_auto_commit_mode_revert_and_cherry_pick_stay_local(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        target = self.git("rev-parse", "HEAD")

        reverted = self.run_cli("revert", target)

        self.assertIn("pushed=false", reverted.stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")
        self.git("branch", "source", "main")
        self.git("switch", "source")
        (self.repo / "picked").write_text("picked\n")
        self.git("add", "picked")
        self.git("commit", "-qm", "Source")
        source = self.git("rev-parse", "HEAD")
        self.git("switch", "feat/test")

        picked = self.run_cli("cherry-pick", source)

        self.assertIn("pushed=false", picked.stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")

    def test_revert_conflict_aborts_without_changing_history(self):
        self.prepare()
        (self.repo / "tracked").write_text("first\n")
        self.commit()
        first = self.git("rev-parse", "HEAD")
        (self.repo / "tracked").write_text("second\n")
        self.commit()
        original_head = self.git("rev-parse", "HEAD")
        remote_head = self.git("ls-remote", "--heads", "origin", "feat/test")
        result = self.run_cli("revert", first, ok=False)
        self.assertIn("original branch and HEAD were restored", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), remote_head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assert_no_in_progress_git_operation()

    def test_cherry_pick_applies_one_commit_and_aborts_conflict(self):
        self.prepare()
        (self.repo / "feature").write_text("feature content\n")
        self.run_cli("commit", "--message", "Feature", "--", "feature")
        self.git("branch", "source", "main")
        self.git("switch", "source")
        (self.repo / "picked").write_text("picked content\n")
        self.git("add", "picked")
        self.git("commit", "-qm", "Source change")
        source_sha = self.git("rev-parse", "HEAD")
        self.git("switch", "feat/test")
        result = self.run_cli("cherry-pick", source_sha)
        self.assertIn("pushed=true", result.stdout)
        self.assertEqual((self.repo / "picked").read_text(), "picked content\n")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), source_sha)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

        self.git("switch", "source")
        (self.repo / "tracked").write_text("source conflict\n")
        self.git("add", "tracked")
        self.git("commit", "-qm", "Conflicting source")
        conflicting_sha = self.git("rev-parse", "HEAD")
        self.git("switch", "feat/test")
        (self.repo / "tracked").write_text("working conflict\n")
        self.commit()
        original_head = self.git("rev-parse", "HEAD")
        remote_head = self.git("ls-remote", "--heads", "origin", "feat/test")
        result = self.run_cli("cherry-pick", conflicting_sha, ok=False)
        self.assertIn("original branch and HEAD were restored", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), remote_head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assert_no_in_progress_git_operation()

    def test_rebase_uses_latest_remote_base_and_lease(self):
        self.git("push", "-q", "origin", "main")
        self.prepare()
        (self.repo / "feature").write_text("feature content\n")
        self.run_cli("commit", "--message", "Feature", "--", "feature")
        original_head = self.git("rev-parse", "HEAD")
        upstream = Path(tempfile.mkdtemp(dir=self.root))
        subprocess.check_call(["git", "clone", "-q", "-b", "main", str(self.remote), str(upstream)],
                              env=self.env)
        (upstream / "base-note").write_text("new base\n")
        for arguments in (("add", "base-note"), ("commit", "-qm", "Advance base"),
                          ("push", "-q", "origin", "main")):
            subprocess.check_call(["git", "-C", str(upstream), *arguments], env=self.env)
        remote_base = self.git("ls-remote", "--heads", "origin", "main").split()[0]
        log = self.root / "push.log"
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            f'if [[ "$1" == push ]]; then printf "%s\\n" "$*" >> "{log}"; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("rebase")
        self.assertIn("base=main", result.stdout)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("merge-base", "HEAD", remote_base), remote_base)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))
        self.assertIn("--force-with-lease=", log.read_text())
        self.assertNotIn(" --force ", f" {log.read_text()} ")
        self.assertEqual(self.git("rev-parse", "main"), self.git("merge-base", "main", remote_base))
        self.assert_no_in_progress_git_operation()

    def test_rebase_in_auto_commit_mode_does_not_publish(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        (self.repo / "feature").write_text("feature content\n")
        self.run_cli("commit", "--message", "Feature", "--", "feature")
        self.advance_remote_branch("main")

        result = self.run_cli("rebase")

        self.assertIn("pushed=false", result.stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")
        self.assertTrue(self.git("merge-base", "HEAD", "origin/main"))

    def test_rebase_base_validation_and_ambiguity(self):
        self.save_settings(branch={"baseBranches": ["main", "develop"]})
        self.git("branch", "develop")
        self.git("push", "-q", "origin", "main", "develop")
        self.prepare()
        before = self.git_snapshot()
        self.assertIn("ambiguous base", self.run_cli("rebase", ok=False).stderr)
        self.assertIn("base is not configured",
                      self.run_cli("rebase", "--base", "outside", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)
        self.assertIn("base=develop", self.run_cli("rebase", "--base", "develop").stdout)

    def test_rebase_conflict_restores_original_state(self):
        self.git("push", "-q", "origin", "main")
        self.prepare()
        (self.repo / "tracked").write_text("feature conflict\n")
        self.commit()
        original_head = self.git("rev-parse", "HEAD")
        remote_feature = self.git("ls-remote", "--heads", "origin", "feat/test")
        upstream = Path(tempfile.mkdtemp(dir=self.root))
        subprocess.check_call(["git", "clone", "-q", "-b", "main", str(self.remote), str(upstream)],
                              env=self.env)
        (upstream / "tracked").write_text("base conflict\n")
        for arguments in (("add", "tracked"), ("commit", "-qm", "Conflicting base"),
                          ("push", "-q", "origin", "main")):
            subprocess.check_call(["git", "-C", str(upstream), *arguments], env=self.env)

        result = self.run_cli("rebase", ok=False)
        self.assertIn("original branch and HEAD were restored", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), remote_feature)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assert_no_in_progress_git_operation()


if __name__ == "__main__":
    unittest.main()
