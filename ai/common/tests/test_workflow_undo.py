import shutil
import unittest

from workflow_helpers import WorkflowTestCase


class WorkflowUndoTests(WorkflowTestCase):
    def test_undo_unpublished_commit_preserves_all_file_changes(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        (self.repo / "removed").write_text("old\n")
        self.run_cli("commit", "--message", "Before", "--", "removed")
        parent = self.git("rev-parse", "HEAD")
        (self.repo / "tracked").write_text("modified\n")
        (self.repo / "added").write_text("new\n")
        (self.repo / "removed").unlink()
        self.run_cli("commit", "--message", "Undo me", "--all")
        removed = self.git("rev-parse", "HEAD")

        result = self.run_cli("undo")

        self.assertIn(f"removed={removed} head={parent} remote=unchanged", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), parent)
        self.assertEqual(self.git("cat-file", "-t", removed), "commit")
        self.assertIn(removed, self.git("reflog", "--format=%H", "-n", "3"))
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertEqual({line.strip() for line in self.git("status", "--porcelain").splitlines()},
                         {"M tracked", "D removed", "?? added"})
        self.assertEqual((self.repo / "tracked").read_text(), "modified\n")
        self.assertEqual((self.repo / "added").read_text(), "new\n")
        self.assertFalse((self.repo / "removed").exists())

    def test_undo_unpublished_commit_does_not_create_remote_branch(self):
        self.prepare()
        self.change()
        self.git("add", "tracked")
        self.git("commit", "-qm", "Local only")
        parent = self.git("rev-parse", "HEAD^")

        result = self.run_cli("undo")

        self.assertIn("remote=unchanged", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), parent)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")
        self.assertEqual(self.git("status", "--porcelain"), "M tracked")

    def test_undo_rejects_head_shared_with_base_before_mutation(self):
        (self.repo / "base-note").write_text("base\n")
        self.git("add", "base-note")
        self.git("commit", "-qm", "Base history")
        self.git("push", "-q", "origin", "main")
        self.prepare()
        before = self.git_snapshot()

        result = self.run_cli("undo", ok=False)

        self.assertIn("commit belongs to configured base branch: main", result.stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_undo_checks_all_configured_base_branches(self):
        self.save_settings(branch={"baseBranches": ["main", "develop"]})
        self.git("push", "-q", "origin", "main")
        self.git("switch", "-c", "develop")
        (self.repo / "develop-file").write_text("base\n")
        self.git("add", "develop-file")
        self.git("commit", "-qm", "Develop base")
        self.git("push", "-qu", "origin", "develop")
        self.git("switch", "-c", "feat/test")
        shared = self.git("rev-parse", "HEAD")
        self.git("switch", "develop")
        (self.repo / "develop-next").write_text("next\n")
        self.git("add", "develop-next")
        self.git("commit", "-qm", "Advance develop")
        self.git("push", "-q", "origin", "develop")
        self.git("switch", "feat/test")
        before = self.git_snapshot()

        self.assertIn("commit belongs to configured base branch: develop",
                      self.run_cli("undo", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)
        (self.repo / "feature-only").write_text("feature\n")
        self.git("add", "feature-only")
        self.git("commit", "-qm", "Feature only")
        self.assertIn("remote=unchanged", self.run_cli("undo").stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), shared)
        self.assertEqual(self.git("status", "--porcelain"), "?? feature-only")

    def test_undo_verifies_configured_base_that_only_exists_remotely(self):
        self.save_settings(branch={"baseBranches": ["main", "develop"]})
        self.git("push", "-q", "origin", "main")
        self.git("push", "-q", "origin", "main:develop")
        self.assertEqual(self.git("branch", "--list", "develop"), "")
        self.run_cli("prepare", "--base", "main", "--branch-name", "feat/test")
        self.change()
        self.commit()

        result = self.run_cli("undo")

        self.assertIn("remote=updated", result.stdout)
        self.assertEqual(self.git("status", "--porcelain"), "M tracked")

    def test_undo_checks_remote_base_when_local_base_is_stale(self):
        self.prepare()
        self.change()
        self.commit()
        shared = self.git("rev-parse", "HEAD")
        self.git("push", "-q", "origin", "HEAD:main")
        before = self.git_snapshot()

        result = self.run_cli("undo", ok=False)

        self.assertIn("commit belongs to configured base branch: main", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), shared)
        self.assertEqual(self.git_snapshot(), before)

    def test_undo_without_remote_preserves_modified_added_and_deleted_files(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        (self.repo / "removed").write_text("old\n")
        self.run_cli("commit", "--message", "Before", "--", "removed")
        parent = self.git("rev-parse", "HEAD")
        self.git("remote", "remove", "origin")
        (self.repo / "tracked").write_text("modified\n")
        (self.repo / "added").write_text("new\n")
        (self.repo / "removed").unlink()
        self.run_cli("commit", "--message", "Local only", "--all")

        result = self.run_cli("undo")

        self.assertIn("remote=unchanged", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), parent)
        self.assertEqual(self.git("remote"), "")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertEqual({line.strip() for line in self.git("status", "--porcelain").splitlines()},
                         {"M tracked", "D removed", "?? added"})
        self.assertEqual((self.repo / "tracked").read_text(), "modified\n")
        self.assertEqual((self.repo / "added").read_text(), "new\n")
        self.assertFalse((self.repo / "removed").exists())

    def test_undo_rejects_unreachable_configured_remote_before_mutation(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        original_head = self.git("rev-parse", "HEAD")
        self.git("remote", "set-url", "origin", str(self.root / "missing.git"))
        original_index = self.git("ls-files", "--stage")

        result = self.run_cli("undo", ok=False)

        self.assertIn("cannot inspect configured remote", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("ls-files", "--stage"), original_index)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_undo_rejects_unverifiable_remote_base_before_mutation(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        original_head = self.git("rev-parse", "HEAD")
        self.git("--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/main")
        original_index = self.git("ls-files", "--stage")

        result = self.run_cli("undo", ok=False)

        self.assertIn("cannot verify remote base history: main", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("ls-files", "--stage"), original_index)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_undo_published_commit_auto_publishes_with_lease(self):
        self.prepare()
        self.change()
        self.commit()
        removed = self.git("rev-parse", "HEAD")
        parent = self.git("rev-parse", "HEAD^")
        log = self.root / "push.log"
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            f'if [[ "$1" == push ]]; then printf "%s\\n" "$*" >> "{log}"; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("undo")

        self.assertIn(f"removed={removed} head={parent} remote=updated", result.stdout)
        self.assertIn(f"--force-with-lease=refs/heads/feat/test:{removed}", log.read_text())
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], parent)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertEqual(self.git("status", "--porcelain"), "M tracked")
        self.assertIn("no recorded local rewrite", self.run_cli("push", "--rewrite", ok=False).stderr)

    def test_undo_published_commit_auto_commit_retries_explicitly(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("push")
        removed = self.git("rev-parse", "HEAD")
        parent = self.git("rev-parse", "HEAD^")

        result = self.run_cli("undo")

        self.assertIn("remote=pending", result.stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], removed)
        self.assertEqual(self.git("status", "--porcelain"), "M tracked")
        self.assertIn("non-fast-forward", self.run_cli("push", ok=False).stderr)
        self.assertIn("pr=none", self.run_cli("push", "--rewrite").stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], parent)
        self.assertEqual(self.git("status", "--porcelain"), "M tracked")

    def test_undo_manual_never_publishes_without_override(self):
        self.save_settings(commit={"mode": "manual"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.run_cli("commit", "--override-manual", "--message", "Manual", "--all")
        self.run_cli("push", "--override-manual")
        removed = self.git("rev-parse", "HEAD")
        parent = self.git("rev-parse", "HEAD^")

        self.assertIn("remote=pending", self.run_cli("undo").stdout)
        self.assertIn("skip reason=manual", self.run_cli("push", "--rewrite").stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], removed)
        self.run_cli("push", "--rewrite", "--override-manual")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], parent)
        self.assertEqual(self.git("status", "--porcelain"), "M tracked")

    def test_undo_rejects_dirty_worktree_and_in_progress_operation(self):
        self.prepare()
        self.change()
        self.commit()
        original = self.git("rev-parse", "HEAD")
        (self.repo / "tracked").write_text("dirty\n")
        self.assertIn("working tree must be clean", self.run_cli("undo", ok=False).stderr)
        self.git("reset", "--hard", "HEAD")
        (self.repo / "untracked").write_text("keep\n")
        self.assertIn("working tree must be clean", self.run_cli("undo", ok=False).stderr)
        (self.repo / "untracked").unlink()
        (self.repo / "tracked").write_text("staged\n")
        self.git("add", "tracked")
        self.assertIn("working tree must be clean", self.run_cli("undo", ok=False).stderr)
        self.git("reset", "--hard", "HEAD")
        (self.repo / ".git/MERGE_HEAD").write_text(original + "\n")
        self.assertIn("finish or abort", self.run_cli("undo", ok=False).stderr)
        (self.repo / ".git/MERGE_HEAD").unlink()
        self.assertEqual(self.git("rev-parse", "HEAD"), original)

    def test_undo_rejects_base_detached_root_and_merge_commits(self):
        self.assertIn("configured base branch", self.run_cli("undo", ok=False).stderr)
        self.git("switch", "-c", "feat/root")
        self.assertIn("root commit", self.run_cli("undo", ok=False).stderr)
        self.git("switch", "--detach")
        self.assertIn("detached HEAD", self.run_cli("undo", ok=False).stderr)
        self.git("switch", "feat/root")
        self.git("switch", "-c", "feat/side")
        (self.repo / "side").write_text("side\n")
        self.git("add", "side")
        self.git("commit", "-qm", "Side")
        self.git("switch", "feat/root")
        self.git("merge", "--no-ff", "-m", "Merge side", "feat/side")
        merged = self.git("rev-parse", "HEAD")
        self.assertIn("merge commit", self.run_cli("undo", ok=False).stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), merged)

    def test_undo_rejects_remote_advance_before_changing_local_history(self):
        self.prepare()
        self.change()
        self.commit()
        local_head = self.git("rev-parse", "HEAD")
        self.advance_remote_branch("feat/test")
        remote_head = self.git("ls-remote", "--heads", "origin", "feat/test").split()[0]

        result = self.run_cli("undo", ok=False)

        self.assertIn("remote branch has commits absent", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), local_head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], remote_head)

    def test_undo_failed_publication_keeps_changes_and_rewrite_record(self):
        self.prepare()
        self.change()
        self.commit()
        removed = self.git("rev-parse", "HEAD")
        parent = self.git("rev-parse", "HEAD^")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push && "$*" == *--force-with-lease* ]]; then\n'
                            '  exit 1\n'
                            'fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("undo", ok=False)

        self.assertIn("retry with push --rewrite", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), parent)
        self.assertEqual(self.git("status", "--porcelain"), "M tracked")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], removed)
        git_stub.unlink()
        self.assertIn("push ok", self.run_cli("push", "--rewrite").stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], parent)

    def test_undo_rewrite_rejects_remote_race_and_preserves_local_work(self):
        self.prepare()
        self.change()
        self.commit()
        removed = self.git("rev-parse", "HEAD")
        parent = self.git("rev-parse", "HEAD^")
        remote_advance = self.git("--git-dir", str(self.remote), "commit-tree",
                                  f"{removed}^{{tree}}", "-p", removed,
                                  "-m", "Concurrent remote update")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push && "$*" == *--force-with-lease* ]]; then\n'
                            f'  "{git_executable}" --git-dir "{self.remote}" update-ref refs/heads/feat/test {remote_advance}\n'
                            'fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        self.assertIn("lease-safe push failed", self.run_cli("undo", ok=False).stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), parent)
        self.assertEqual(self.git("status", "--porcelain"), "M tracked")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0],
                         remote_advance)

    def test_undo_interoperates_with_pending_amend_and_rebase(self):
        self.save_settings(commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("push")
        published = self.git("rev-parse", "HEAD")
        (self.repo / "tracked").write_text("amended\n")
        self.run_cli("commit", "--amend", "--all")

        self.assertIn("remote=pending", self.run_cli("undo").stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0],
                         published)
        self.run_cli("push", "--rewrite")
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), self.git("rev-parse", "HEAD"))
        self.run_cli("commit", "--message", "Recommit", "--", "tracked")
        self.run_cli("push")
        self.advance_remote_branch("main")
        self.assertIn("pushed=false", self.run_cli("rebase").stdout)
        self.run_cli("push", "--rewrite")
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), self.git("rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
