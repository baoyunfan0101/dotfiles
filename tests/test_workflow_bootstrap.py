import shutil
import subprocess
import unittest

from workflow_helpers import WorkflowTestCase


class WorkflowBootstrapTests(WorkflowTestCase):
    def unborn_repo(self, head="main"):
        self.repo = self.root / "empty-repo"
        self.remote = self.root / "empty-remote.git"
        self.repo.mkdir()
        self.git("init", "-q", "-b", head)
        self.git("init", "-q", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.settings(sync={"mode": "update"})

    def bootstrapped_root(self):
        self.unborn_repo()
        self.settings(commit={"mode": "manual"})
        (self.repo / "README.md").write_text("initial\n")
        self.run_cli("bootstrap", "--message", "Initial", "--all")

    def bootstrap_rewrite_refs(self):
        return self.git("for-each-ref", "--format=%(refname) %(objectname)",
                        "refs/git-workflow/rewrite/")

    def assert_unborn(self, branch):
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), branch)
        self.assertNotEqual(subprocess.run(["git", "rev-parse", "--verify", "HEAD"],
                                           cwd=self.repo, env=self.env, capture_output=True).returncode, 0)
        self.assertEqual(self.git("rev-list", "--all", "--count"), "0")

    def test_bootstrap_commits_and_publishes_base_before_development(self):
        self.unborn_repo()
        (self.repo / ".git/info/exclude").write_text("/.ai/project.json\n")
        self.settings(commit={"mode": "autoCommitAndPush"})
        (self.repo / "README.md").write_bytes(b"initial project\n")
        (self.repo / "staged.txt").write_bytes(b"staged\n")
        (self.repo / "unselected.txt").write_bytes(b"keep\n")
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        (self.repo / "ignored.txt").write_text("ignored\n")
        self.git("add", "staged.txt")
        result = self.run_cli("bootstrap", "--base", "main", "--message", "Initial",
                              "--", "README.md", "staged.txt")
        self.assertIn("base=main remote=origin/main", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "HEAD"), "README.md\nstaged.txt")
        self.assertTrue((self.repo / "unselected.txt").exists())
        self.assertTrue((self.repo / "ignored.txt").exists())
        self.assertEqual(self.git("rev-list", "--parents", "-n", "1", "HEAD").split(),
                         [self.git("rev-parse", "HEAD")])
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/init"), "")
        (self.repo / "README.md").write_text("must remain uncommitted\n")
        self.assertIn("configured base branch",
                      self.run_cli("commit", "--message", "Forbidden", "--", "README.md", ok=False).stderr)
        self.assertEqual(self.git("show", "HEAD:README.md"), "initial project")
        self.assertEqual((self.repo / "README.md").read_text(), "must remain uncommitted\n")
        (self.repo / "README.md").write_text("initial project\n")
        self.assertIn("branch=feat/test created=true", self.prepare().stdout)
        self.change()
        commit_result = self.commit()
        self.assertIn("pushed=true", commit_result.stdout)
        self.assertNotEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")

    def test_prepare_rejects_unborn_repository_without_mutation(self):
        self.unborn_repo()
        (self.repo / "keep").write_text("untouched\n")
        before = self.git_snapshot()
        result = self.run_cli("prepare", "--base", "main", "--branch-name", "feat/init", ok=False)
        self.assertIn("run git-workflow bootstrap", result.stderr)
        self.assertEqual(before, self.git_snapshot())

    def test_bootstrap_all_includes_staged_configuration(self):
        self.unborn_repo()
        self.settings(commit={"mode": "manual"})
        (self.repo / "README.md").write_text("initial\n")
        self.git("add", ".ai/project.json")
        result = self.run_cli("bootstrap", "--message", "Initial", "--all")
        self.assertIn("bootstrap ok", result.stdout)
        self.assertTrue((self.repo / ".ai/project.json").exists())
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "HEAD"),
                         ".ai/project.json\nREADME.md")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertNotEqual(self.git("ls-remote", "--heads", "origin", "main"), "")
        self.assertIn("already contains commits", self.run_cli("bootstrap", "--message", "Again", "--all", ok=False).stderr)

    def test_bootstrap_all_follows_local_exclude_and_includes_other_ai_files(self):
        self.unborn_repo()
        (self.repo / ".git/info/exclude").write_text("/.ai/project.json\n")
        (self.repo / ".ai/notes.txt").write_text("shareable\n")
        (self.repo / "README.md").write_text("initial\n")
        self.run_cli("bootstrap", "--message", "Initial", "--all")
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "HEAD"),
                         ".ai/notes.txt\nREADME.md")
        self.assertTrue((self.repo / ".ai/project.json").exists())
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         self.git("rev-parse", "HEAD"))

    def test_bootstrap_all_respects_explicit_gitignore_override(self):
        self.unborn_repo()
        (self.repo / ".git/info/exclude").write_text("/.ai/project.json\n")
        (self.repo / ".gitignore").write_text("!/.ai/project.json\n")
        (self.repo / "README.md").write_text("initial\n")
        self.run_cli("bootstrap", "--message", "Initial", "--all")
        self.assertIn(".ai/project.json",
                      self.git("ls-tree", "-r", "--name-only", "HEAD").splitlines())

    def test_bootstrap_validates_empty_selection_base_and_remote(self):
        self.unborn_repo(head="develop")
        (self.repo / ".git/info/exclude").write_text("/.ai/project.json\n")
        (self.repo / "README.md").write_text("initial\n")
        self.settings(branch={"baseBranches": ["main", "develop"]})
        before = self.git_snapshot()
        result = self.run_cli("bootstrap", "--message", "Initial", "--all", ok=False)
        self.assertIn("ambiguous base", result.stderr)
        self.assertEqual(before, self.git_snapshot())
        self.settings(branch={"baseBranches": []})
        before = self.git_snapshot()
        result = self.run_cli("prepare", "--branch-name", "feat/no-base", ok=False)
        self.assertIn("no configured base branches", result.stderr)
        self.assertEqual(before, self.git_snapshot())
        self.settings(branch={"baseBranches": ["main", "develop"]})
        before = self.git_snapshot()
        result = self.run_cli("bootstrap", "--base", "main", "--message", "Initial", "--all", ok=False)
        self.assertIn("unborn branch is develop", result.stderr)
        self.assertEqual(before, self.git_snapshot())
        (self.repo / "README.md").unlink()
        result = self.run_cli("bootstrap", "--base", "develop", "--message", "Initial", "--all", ok=False)
        self.assertIn("initial commit contains no selected changes", result.stderr)
        self.assert_unborn("develop")
        (self.repo / "README.md").write_text("initial\n")
        result = self.run_cli("bootstrap", "--base", "develop", "--message", "Initial", "--", "missing", ok=False)
        self.assertIn("could not stage selected initial files", result.stderr)
        self.assert_unborn("develop")
        self.git("remote", "remove", "origin")
        result = self.run_cli("bootstrap", "--base", "develop", "--message", "Initial", "--all", ok=False)
        self.assertIn("no configured remote", result.stderr)
        self.assert_unborn("develop")
        self.git("remote", "add", "origin", str(self.root / "missing-remote.git"))
        result = self.run_cli("bootstrap", "--base", "develop", "--message", "Initial", "--all", ok=False)
        self.assertIn("configured remote is inaccessible", result.stderr)
        self.assert_unborn("develop")

    def test_bootstrap_rejects_remote_conflict_and_failed_push_preserves_commit(self):
        self.unborn_repo()
        (self.repo / "README.md").write_text("initial\n")
        seed = self.root / "seed"
        seed.mkdir()
        subprocess.check_call(["git", "clone", "-q", str(self.remote), str(seed)], env=self.env)
        (seed / "remote").write_text("occupied\n")
        subprocess.check_call(["git", "-C", str(seed), "add", "remote"], env=self.env)
        subprocess.check_call(["git", "-C", str(seed), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "Remote"], env=self.env)
        subprocess.check_call(["git", "-C", str(seed), "push", "-q", "origin", "HEAD:main"], env=self.env)
        result = self.run_cli("bootstrap", "--message", "Initial", "--all", ok=False)
        self.assertIn("remote repository is not empty", result.stderr)
        self.assertFalse((self.repo / ".git/refs/heads/main").exists())

        self.git("--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/main")
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        result = self.run_cli("bootstrap", "--message", "Initial", "--all", ok=False)
        self.assertIn("exists locally on main but push failed", result.stderr)
        self.assertTrue(self.git("rev-parse", "--verify", "main").strip())
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_bootstrap_amend_rewrites_only_the_published_root(self):
        self.bootstrapped_root()
        self.git("--git-dir", str(self.remote), "symbolic-ref", "HEAD",
                 "refs/heads/main")
        original = self.git("rev-parse", "HEAD")
        (self.repo / "README.md").write_text("corrected\n")

        result = self.run_cli("bootstrap", "--amend", "--message", "Corrected",
                              "--all")

        amended = self.git("rev-parse", "HEAD")
        self.assertNotEqual(amended, original)
        self.assertIn("bootstrap amend ok", result.stdout)
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "1")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Corrected")
        self.assertEqual(self.git("show", "HEAD:README.md"), "corrected")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         amended)
        self.assertEqual(self.bootstrap_rewrite_refs(), "")

    def test_bootstrap_amend_message_only_and_rejects_noop(self):
        self.bootstrapped_root()
        original_tree = self.git("rev-parse", "HEAD^{tree}")
        self.run_cli("bootstrap", "--amend", "--message", "Better initial message",
                     "--", "README.md")
        amended = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), original_tree)
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Better initial message")

        result = self.run_cli("bootstrap", "--amend", "--message",
                              "Better initial message", "--", "README.md", ok=False)
        self.assertIn("no commit changes", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), amended)
        self.assertEqual(self.bootstrap_rewrite_refs(), "")

    def test_bootstrap_amend_selected_paths_preserve_other_work(self):
        self.unborn_repo()
        self.settings(commit={"mode": "manual"})
        (self.repo / "README.md").write_text("initial\n")
        (self.repo / "other.txt").write_text("original other\n")
        self.run_cli("bootstrap", "--message", "Initial", "--all")
        (self.repo / "README.md").write_text("selected\n")
        (self.repo / "other.txt").write_text("unselected change\n")
        self.git("add", "other.txt")
        self.run_cli("bootstrap", "--amend", "--message", "Initial", "--",
                     "README.md")
        self.assertEqual(self.git("show", "HEAD:README.md"), "selected")
        self.assertEqual(self.git("show", "HEAD:other.txt"), "original other")
        self.assertEqual((self.repo / "other.txt").read_text(), "unselected change\n")
        self.assertIn("M\tother.txt", self.git("diff", "--cached", "--name-status"))

    def test_bootstrap_amend_all_includes_staged_configuration(self):
        self.bootstrapped_root()
        self.git("add", ".ai/project.json")
        (self.repo / "README.md").write_text("corrected\n")
        self.run_cli("bootstrap", "--amend", "--message", "Initial", "--all")
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "HEAD"),
                         ".ai/project.json\nREADME.md")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_bootstrap_amend_rejects_non_base_and_advanced_base(self):
        self.bootstrapped_root()
        self.git("switch", "-qc", "feat/next")
        before = self.git("rev-parse", "HEAD")
        result = self.run_cli("bootstrap", "--amend", "--message", "Change",
                              "--all", ok=False)
        self.assertIn("configured base branch", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

        self.git("switch", "main")
        (self.repo / "next").write_text("next\n")
        self.git("add", "next")
        self.git("commit", "-qm", "Second")
        before = self.git("rev-parse", "HEAD")
        result = self.run_cli("bootstrap", "--amend", "--message", "Change",
                              "--all", ok=False)
        self.assertIn("single root commit", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_bootstrap_amend_rejects_extra_local_refs(self):
        self.bootstrapped_root()
        for kind in ("branch", "tag"):
            with self.subTest(kind=kind):
                self.git(kind, "extra")
                original = self.git("rev-parse", "HEAD")
                result = self.run_cli("bootstrap", "--amend", "--message", "Change",
                                      "--all", ok=False)
                self.assertIn("no other local branches or tags", result.stderr)
                self.assertEqual(self.git("rev-parse", "HEAD"), original)
                self.git(kind, "-d", "extra")

    def test_bootstrap_amend_rejects_extra_remote_refs(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        for kind, ref in (("branch", "refs/heads/extra"),
                          ("tag", "refs/tags/extra")):
            with self.subTest(kind=kind):
                self.git("--git-dir", str(self.remote), "update-ref", ref, original)
                result = self.run_cli("bootstrap", "--amend", "--message", "Change",
                                      "--all", ok=False)
                self.assertIn("no other branches or tags", result.stderr)
                self.assertEqual(self.git("rev-parse", "HEAD"), original)
                self.git("--git-dir", str(self.remote), "update-ref", "-d", ref)

    def test_bootstrap_amend_rejects_changed_or_unavailable_remote(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        advanced = self.git("--git-dir", str(self.remote), "commit-tree",
                            f"{original}^{{tree}}", "-p", original, "-m", "Remote advance")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main", advanced)
        result = self.run_cli("bootstrap", "--amend", "--message", "Change",
                              "--all", ok=False)
        self.assertIn("original root commit", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), original)

        self.git("remote", "set-url", "origin", str(self.root / "missing.git"))
        result = self.run_cli("bootstrap", "--amend", "--message", "Change",
                              "--all", ok=False)
        self.assertIn("configured remote is inaccessible", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), original)

    def test_bootstrap_amend_rejects_unfinished_operation(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        (self.repo / ".git/MERGE_HEAD").write_text(original + "\n")
        result = self.run_cli("bootstrap", "--amend", "--message", "Change",
                              "--all", ok=False)
        self.assertIn("finish or abort", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), original)

    def test_bootstrap_amend_uses_exact_lease(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        log = self.root / "git-push.log"
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push ]]; then '
                            'printf "%s\\n" "$*" >> "$GIT_PUSH_LOG"; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        self.env["GIT_PUSH_LOG"] = str(log)

        self.run_cli("bootstrap", "--amend", "--message", "Changed", "--all")

        self.assertIn(f"--force-with-lease=refs/heads/main:{original}", log.read_text())
        self.assertNotIn("--force ", log.read_text())

    def test_bootstrap_amend_rejects_remote_race_during_push(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        advanced = self.git("--git-dir", str(self.remote), "commit-tree",
                            f"{original}^{{tree}}", "-p", original, "-m", "Remote advance")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push && "$*" == *--force-with-lease* ]]; then\n'
                            f'  "{git_executable}" --git-dir "{self.remote}" '
                            f'update-ref refs/heads/main "{advanced}"\n'
                            'fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("bootstrap", "--amend", "--message", "Changed",
                              "--all", ok=False)

        self.assertIn("retry with git-workflow bootstrap --retry-amend", result.stderr)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), original)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         advanced)
        self.assertIn(original, self.bootstrap_rewrite_refs())

    def test_bootstrap_amend_failed_push_can_retry(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        (self.repo / "README.md").write_text("corrected\n")

        result = self.run_cli("bootstrap", "--amend", "--message", "Corrected",
                              "--all", ok=False)
        amended = self.git("rev-parse", "HEAD")
        self.assertNotEqual(amended, original)
        self.assertIn("retry with git-workflow bootstrap --retry-amend", result.stderr)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         original)
        self.assertIn(original, self.bootstrap_rewrite_refs())
        self.assertIn(amended, self.bootstrap_rewrite_refs())
        hook.unlink()

        result = self.run_cli("bootstrap", "--retry-amend")
        self.assertIn("bootstrap amend ok", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), amended)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         amended)
        self.assertEqual(self.bootstrap_rewrite_refs(), "")

    def test_bootstrap_amend_retry_reconciles_verified_publication_without_push(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        (self.repo / "README.md").write_text("corrected\n")
        push_log = self.root / "bootstrap-push.log"
        fail_verification = self.root / "fail-verification"
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push && "$*" == *--force-with-lease* ]]; then\n'
                            '  printf "%s\\n" "$*" >> "$BOOTSTRAP_PUSH_LOG"\n'
                            f'  "{git_executable}" "$@" || exit $?\n'
                            '  touch "$BOOTSTRAP_FAIL_VERIFICATION"\n'
                            '  exit 0\n'
                            'fi\n'
                            'if [[ "$1" == ls-remote && -e "$BOOTSTRAP_FAIL_VERIFICATION" ]]; then\n'
                            '  rm "$BOOTSTRAP_FAIL_VERIFICATION"\n'
                            '  exit 1\n'
                            'fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        self.env["BOOTSTRAP_PUSH_LOG"] = str(push_log)
        self.env["BOOTSTRAP_FAIL_VERIFICATION"] = str(fail_verification)

        result = self.run_cli("bootstrap", "--amend", "--message", "Corrected",
                              "--all", ok=False)
        amended = self.git("rev-parse", "HEAD")
        self.assertIn("cannot verify amended root commit", result.stderr)
        self.assertNotEqual(amended, original)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         amended)
        self.assertIn(original, self.bootstrap_rewrite_refs())
        self.assertIn(amended, self.bootstrap_rewrite_refs())
        pushes = push_log.read_text().splitlines()
        self.assertEqual(len(pushes), 1)
        self.assertIn(f"--force-with-lease=refs/heads/main:{original}", pushes[0])

        unrelated = self.git("--git-dir", str(self.remote), "commit-tree",
                             f"{amended}^{{tree}}", "-m", "Unrelated root")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main",
                 unrelated)
        rejected = self.run_cli("bootstrap", "--retry-amend", ok=False)
        self.assertIn("original root commit", rejected.stderr)
        self.assertEqual(self.bootstrap_rewrite_refs(),
                         f"refs/git-workflow/rewrite/expected/origin/main {original}\n"
                         f"refs/git-workflow/rewrite/head/origin/main {amended}")
        self.assertEqual(push_log.read_text().splitlines(), pushes)
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main",
                 amended)

        retry = self.run_cli("bootstrap", "--retry-amend")
        self.assertIn("bootstrap amend ok", retry.stdout)
        self.assertEqual(push_log.read_text().splitlines(), pushes)
        self.assertEqual(self.git("rev-parse", "HEAD"), amended)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         amended)
        self.assertEqual(self.bootstrap_rewrite_refs(), "")

    def test_bootstrap_amend_retry_rejects_incomplete_or_invalid_record(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.run_cli("bootstrap", "--amend", "--message", "Changed", "--all",
                     ok=False)
        amended = self.git("rev-parse", "HEAD")
        hook.unlink()
        expected_ref = "refs/git-workflow/rewrite/expected/origin/main"
        head_ref = "refs/git-workflow/rewrite/head/origin/main"

        self.git("update-ref", "-d", head_ref)
        result = self.run_cli("bootstrap", "--retry-amend", ok=False)
        self.assertIn("incomplete rewrite record", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), amended)
        self.git("update-ref", head_ref, amended)

        self.git("update-ref", expected_ref, amended)
        result = self.run_cli("bootstrap", "--retry-amend", ok=False)
        self.assertIn("invalid bootstrap amend rewrite record", result.stderr)
        self.git("update-ref", expected_ref, original)

        non_root = self.git("commit-tree", f"{amended}^{{tree}}", "-p", amended,
                            "-m", "Not a root")
        self.git("update-ref", expected_ref, non_root)
        result = self.run_cli("bootstrap", "--retry-amend", ok=False)
        self.assertIn("single root commit", result.stderr)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         original)
        self.assertIn(non_root, self.bootstrap_rewrite_refs())

    def test_bootstrap_amend_retry_rejects_extra_local_and_remote_refs(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.run_cli("bootstrap", "--amend", "--message", "Changed", "--all",
                     ok=False)
        amended = self.git("rev-parse", "HEAD")
        hook.unlink()
        records = self.bootstrap_rewrite_refs()

        for kind in ("branch", "tag"):
            with self.subTest(location="local", kind=kind):
                self.git(kind, "extra")
                result = self.run_cli("bootstrap", "--retry-amend", ok=False)
                self.assertIn("no other local branches or tags", result.stderr)
                self.git(kind, "-d", "extra")
            with self.subTest(location="remote", kind=kind):
                ref = f"refs/{'heads' if kind == 'branch' else 'tags'}/extra"
                self.git("--git-dir", str(self.remote), "update-ref", ref, original)
                result = self.run_cli("bootstrap", "--retry-amend", ok=False)
                self.assertIn("no other branches or tags", result.stderr)
                self.git("--git-dir", str(self.remote), "update-ref", "-d", ref)
        self.assertEqual(self.git("rev-parse", "HEAD"), amended)
        self.assertEqual(self.bootstrap_rewrite_refs(), records)

    def test_bootstrap_amend_retry_rejects_remote_race(self):
        self.bootstrapped_root()
        original = self.git("rev-parse", "HEAD")
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.run_cli("bootstrap", "--amend", "--message", "Changed", "--all",
                     ok=False)
        amended = self.git("rev-parse", "HEAD")
        hook.unlink()
        advanced = self.git("--git-dir", str(self.remote), "commit-tree",
                            f"{original}^{{tree}}", "-p", original, "-m", "Remote advance")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main", advanced)

        result = self.run_cli("bootstrap", "--retry-amend", ok=False)

        self.assertIn("original root commit", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), amended)
        self.assertIn(original, self.bootstrap_rewrite_refs())
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main").split()[0],
                         advanced)

    def test_bootstrap_amend_retry_rejects_stale_local_record(self):
        self.bootstrapped_root()
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.run_cli("bootstrap", "--amend", "--message", "Changed", "--all",
                     ok=False)
        recorded = self.git("rev-parse", "HEAD")
        self.git("commit", "--amend", "-qm", "Unrecorded local rewrite")
        current = self.git("rev-parse", "HEAD")
        hook.unlink()

        result = self.run_cli("bootstrap", "--retry-amend", ok=False)

        self.assertIn("local HEAD differs from recorded", result.stderr)
        self.assertNotEqual(current, recorded)
        self.assertEqual(self.git("rev-parse", "HEAD"), current)
        self.assertIn(recorded, self.bootstrap_rewrite_refs())

    def test_bootstrap_amend_keeps_normal_base_protections(self):
        self.bootstrapped_root()
        for arguments in (("commit", "--amend", "--override-manual", "--all"),
                          ("undo",), ("push", "--rewrite", "--override-manual")):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments, ok=False)
                self.assertIn("configured base branch", result.stderr)

    def test_unborn_prepare_rejects_invalid_states(self):
        self.unborn_repo(head="develop")
        self.settings(branch={"baseBranches": ["main", "develop"]})
        for arguments, message in (
            (("--base", "main", "--branch-name", "feat/init"), "run git-workflow bootstrap"),
            (("--base", "outside", "--branch-name", "feat/init"), "base is not configured"),
            (("--base", "develop", "--branch-name", "invalid"), "branch name must use"),
            (("--continue",), "--continue requires a working branch"),
        ):
            with self.subTest(arguments=arguments):
                self.assertIn(message, self.run_cli("prepare", *arguments, ok=False).stderr)
                self.assert_unborn("develop")
        self.settings(branch={"baseBranches": ["main", "develop", "feat/base"]})
        self.assertIn("configured base branch", self.run_cli("prepare", "--base", "develop",
                      "--branch-name", "feat/base", ok=False).stderr)
        self.assert_unborn("develop")

    def test_missing_base_with_history_does_not_bootstrap(self):
        self.git("switch", "-c", "feat/current")
        self.git("branch", "-D", "main")
        result = self.run_cli("prepare", "--base", "main", "--branch-name", "feat/next", ok=False)
        self.assertIn("local base branch not found", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/current")


if __name__ == "__main__":
    unittest.main()
