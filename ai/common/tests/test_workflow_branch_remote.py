import shutil
import subprocess
import unittest

from workflow_helpers import WorkflowTestCase, CLI


class WorkflowBranchRemoteTests(WorkflowTestCase):
    def test_remote_help_and_invalid_actions(self):
        help_text = self.run_cli("--help").stdout
        for action in ("connect", "reconnect", "disconnect"):
            self.assertIn(f"git-workflow remote {action}", help_text)
            for args in (("remote", "--help"), ("remote", action, "--help")):
                output = self.run_cli(*args).stdout
                self.assertIn(f"git-workflow remote {action}", output)
                self.assertNotIn("origin", output)
        for args in (("remote",), ("remote", "unknown")):
            result = self.run_cli(*args, ok=False)
            self.assertIn('[git] remote error reason="', result.stderr)

    def test_remote_lifecycle_preserves_unrelated_remotes(self):
        self.git("remote", "add", "other", "../other.git")
        self.git("config", "remote.other.pushurl", "../other-push.git")
        other = self.git("config", "--get-regexp", r"^remote\.other\.")
        self.run_cli("remote", "disconnect")
        for action, url in (("connect", str(self.remote)),
                            ("reconnect", str(self.root / "new remote.git"))):
            result = self.run_cli("remote", action, url)
            self.assertEqual(result.stdout, f"[git] remote {action} ok url={url}\n")
            self.assertEqual(self.git("remote", "get-url", "origin"), url)
            self.assertEqual(self.git("config", "--get-regexp", r"^remote\.other\."), other)
        self.assertEqual(self.run_cli("remote", "disconnect").stdout,
                         "[git] remote disconnect ok\n")
        self.assertEqual(self.git("remote"), "other")
        self.assertEqual(self.git("config", "--get-regexp", r"^remote\.other\."), other)

    def test_remote_invalid_transitions_do_not_mutate(self):
        for connected in (True, False):
            if not connected:
                self.git("remote", "remove", "origin")
            before = (self.repo / ".git/config").read_bytes()
            cases = (("connect", "new.git"),) if connected else (
                ("reconnect", "new.git"), ("disconnect",))
            for args in cases:
                result = self.run_cli("remote", *args, ok=False)
                self.assertIn(f'[git] remote {args[0]} error reason="', result.stderr)
                self.assertEqual((self.repo / ".git/config").read_bytes(), before)

    def test_remote_argument_validation_does_not_mutate(self):
        for connected in (True, False):
            if not connected:
                self.git("remote", "remove", "origin")
            before = (self.repo / ".git/config").read_bytes()
            for action in ("connect", "reconnect"):
                for args in ((), ("one", "two"), ("",), ("   ",), ("--bad",),
                             ("-x",), ("url\nother",), ("url\tother",),
                             ("--help", "extra")):
                    with self.subTest(connected=connected, action=action, args=args):
                        result = self.run_cli("remote", action, *args, ok=False)
                        self.assertIn(f'[git] remote {action} error reason="', result.stderr)
                        self.assertEqual((self.repo / ".git/config").read_bytes(), before)
            self.run_cli("remote", "disconnect", "extra", ok=False)
            self.assertEqual((self.repo / ".git/config").read_bytes(), before)

    def test_remote_disabled_and_repository_requirement(self):
        (self.repo / ".ai/project.json").unlink()
        before = (self.repo / ".git/config").read_bytes()
        for action, args in (("connect", ("new.git",)), ("reconnect", ("new.git",)),
                             ("disconnect", ())):
            result = self.run_cli("remote", action, *args)
            self.assertEqual(result.stdout,
                             f"[git] remote {action} skip reason=workflow-disabled\n")
            outside = subprocess.run([str(CLI), "remote", action, *args],
                                     cwd=self.root, env=self.env, capture_output=True, text=True)
            self.assertNotEqual(outside.returncode, 0)
            self.assertIn("not inside a Git worktree", outside.stderr)
        self.assertEqual((self.repo / ".git/config").read_bytes(), before)

    def test_remote_connect_first_push(self):
        self.git("remote", "remove", "origin")
        target = self.root / "publish.git"
        self.git("init", "--bare", str(target))
        self.run_cli("remote", "connect", str(target))
        self.run_cli("push")
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("--git-dir", str(target), "rev-parse", "refs/heads/main"))
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "@{upstream}"), "origin/main")

    def test_remote_stored_url_verification_and_multiple_urls(self):
        self.git("config", "url.file:///rewritten/.insteadOf", "alias:")
        self.git("config", "--add", "remote.origin.url", "extra.git")
        self.run_cli("remote", "reconnect", "alias:repo.git")
        self.assertEqual(self.git("config", "--get-all", "remote.origin.url"), "alias:repo.git")
        self.run_cli("remote", "disconnect")
        self.run_cli("remote", "connect", "alias:repo.git")
        self.assertEqual(self.git("config", "--get-all", "remote.origin.url"), "alias:repo.git")

    def test_branch_create_switch_and_local_delete(self):
        head = self.git("rev-parse", "HEAD")
        result = self.run_cli("branch", "create", "feat/branch-domain")
        self.assertIn("branch create ok name=feat/branch-domain", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "feat/branch-domain")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/branch-domain"), "")
        result = self.run_cli("branch", "switch", "main")
        self.assertIn("branch switch ok name=main", result.stdout)
        result = self.run_cli("branch", "delete", "feat/branch-domain")
        self.assertIn("remote=false", result.stdout)
        self.assertEqual(self.git("branch", "--list", "feat/branch-domain"), "")

    def test_branch_create_rejects_invalid_and_duplicate_without_mutation(self):
        self.git("branch", "feat/existing")
        for name, message in (
            ("codex/agent", "invalid branch type: codex"),
            ("plain", "branch name must use"),
            ("feat/foo..bar", "invalid branch name"),
            ("feat/existing", "branch already exists"),
        ):
            with self.subTest(name=name):
                before = self.git_snapshot()
                self.assertIn(message, self.run_cli("branch", "create", name, ok=False).stderr)
                self.assertEqual(self.git_snapshot(), before)

    def test_branch_switch_rejects_dirty_missing_and_detached(self):
        self.git("branch", "work-in-progress")
        self.change()
        before = self.git_snapshot()
        self.assertIn("working tree has local changes",
                      self.run_cli("branch", "switch", "work-in-progress", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)
        self.git("restore", "tracked")
        before = self.git_snapshot()
        self.assertIn("local branch not found",
                      self.run_cli("branch", "switch", "missing", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)
        self.git("checkout", "--detach", "-q", "HEAD")
        before = self.git_snapshot()
        self.assertIn("detached HEAD", self.run_cli("branch", "switch", "main", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_branch_rename_local_and_invalid_targets(self):
        self.git("switch", "-c", "work-in-progress")
        before = self.git_snapshot()
        self.assertIn("invalid branch type",
                      self.run_cli("branch", "rename", "codex/renamed", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)
        self.git("branch", "feat/existing")
        before = self.git_snapshot()
        self.assertIn("branch already exists",
                      self.run_cli("branch", "rename", "feat/existing", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)
        result = self.run_cli("branch", "rename", "fix/renamed")
        self.assertIn("old=work-in-progress new=fix/renamed remote=false", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "fix/renamed")
        self.assertEqual(self.git("branch", "--list", "work-in-progress"), "")

    def test_branch_rename_rejects_base_and_detached_head(self):
        before = self.git_snapshot()
        self.assertIn("configured base branch",
                      self.run_cli("branch", "rename", "fix/main", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)
        self.git("checkout", "--detach", "-q", "HEAD")
        before = self.git_snapshot()
        self.assertIn("detached HEAD",
                      self.run_cli("branch", "rename", "fix/detached", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_branch_rename_remote_and_open_pr_guard(self):
        self.stub_gh()
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        before = self.git_snapshot()
        self.env["GH_EXTRA_PRS"] = '[{"url":"https://example.invalid/pr/2"}]'
        result = self.run_cli("branch", "rename", "fix/new-name", ok=False)
        self.assertIn("current branch has an open pull request", result.stderr)
        self.assertEqual(self.git_snapshot(), before)
        self.env.pop("GH_EXTRA_PRS")
        result = self.run_cli("branch", "rename", "fix/new-name")
        self.assertIn("old=feat/old-name new=fix/new-name remote=true", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "fix/new-name")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
                         "origin/fix/new-name")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/old-name"), "")
        self.assertTrue(self.git("ls-remote", "--heads", "origin", "fix/new-name"))

    def test_branch_rename_requires_pr_verification(self):
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        before = self.git_snapshot()
        self.assertIn("cannot verify open pull requests",
                      self.run_cli("branch", "rename", "fix/new-name", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_branch_rename_remote_push_failure_restores_local_name(self):
        self.stub_gh()
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push && "$2" == --quiet && "$3" == --set-upstream ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        before = self.git_snapshot()
        result = self.run_cli("branch", "rename", "fix/new-name", ok=False)
        self.assertIn("original branch restored", result.stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_branch_rename_rejects_remote_ahead(self):
        self.stub_gh()
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        self.advance_remote_branch("feat/old-name")
        before = self.git_snapshot()
        self.assertIn("remote branch has commits absent from local history",
                      self.run_cli("branch", "rename", "fix/new-name", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_branch_rename_rejects_remote_copy_without_upstream(self):
        self.run_cli("branch", "create", "feat/old-name")
        self.git("push", "origin", "feat/old-name")
        before = self.git_snapshot()
        self.assertIn("remote branch exists without an upstream",
                      self.run_cli("branch", "rename", "fix/new-name", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_branch_rename_old_remote_deletion_failure_is_partial(self):
        self.stub_gh()
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push && "$3" == --force-with-lease=* ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        result = self.run_cli("branch", "rename", "fix/new-name", ok=False)
        self.assertIn("remote rename partial", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "fix/new-name")
        self.assertTrue(self.git("ls-remote", "--heads", "origin", "feat/old-name"))
        self.assertTrue(self.git("ls-remote", "--heads", "origin", "fix/new-name"))

    def test_branch_delete_guards_and_remote_behavior(self):
        self.stub_gh()
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        for name, message in (("main", "configured base branch"),
                              ("feat/old-name", "current branch"),
                              ("missing", "local branch not found")):
            before = self.git_snapshot()
            self.assertIn(message, self.run_cli("branch", "delete", name, "--remote", ok=False).stderr)
            self.assertEqual(self.git_snapshot(), before)
        self.run_cli("branch", "switch", "main")
        self.env["GH_EXTRA_PRS"] = '[{"url":"https://example.invalid/pr/2"}]'
        result = self.run_cli("branch", "delete", "feat/old-name", "--remote")
        self.assertIn("remote=deleted", result.stdout)
        self.assertEqual(self.git("branch", "--list", "feat/old-name"), "")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/old-name"), "")
        self.assertEqual(self.gh_calls(), [])

    def test_branch_delete_remote_without_usable_gh(self):
        self.run_cli("branch", "create", "feat/provider-independent")
        self.run_cli("push")
        self.run_cli("branch", "switch", "main")
        result = self.run_cli("branch", "delete", "feat/provider-independent", "--remote")
        self.assertIn("remote=deleted", result.stdout)
        self.assertEqual(self.git("branch", "--list", "feat/provider-independent"), "")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/provider-independent"), "")

    def test_branch_delete_remote_never_invokes_failing_gh(self):
        gh_stub = self.bin / "gh"
        gh_marker = self.root / "gh-called"
        gh_stub.write_text(f'#!/usr/bin/env bash\nprintf called > "{gh_marker}"\nexit 1\n')
        gh_stub.chmod(0o755)
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        self.run_cli("branch", "switch", "main")
        self.assertIn("remote=deleted",
                      self.run_cli("branch", "delete", "feat/old-name", "--remote").stdout)
        self.assertFalse(gh_marker.exists())

    def test_branch_delete_local_only_preserves_remote(self):
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        self.run_cli("branch", "switch", "main")
        self.assertIn("remote=false",
                      self.run_cli("branch", "delete", "feat/old-name").stdout)
        self.assertTrue(self.git("ls-remote", "--heads", "origin", "feat/old-name"))

    def test_branch_delete_missing_remote_and_partial_failure(self):
        self.stub_gh()
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("branch", "switch", "main")
        self.assertIn("remote=already-missing",
                      self.run_cli("branch", "delete", "feat/old-name", "--remote").stdout)
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        self.run_cli("branch", "switch", "main")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == push && "$3" == --force-with-lease=* ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)
        result = self.run_cli("branch", "delete", "feat/old-name", "--remote", ok=False)
        self.assertIn("local branch deleted but remote deletion failed", result.stderr)
        self.assertEqual(self.git("branch", "--list", "feat/old-name"), "")
        self.assertTrue(self.git("ls-remote", "--heads", "origin", "feat/old-name"))

    def test_branch_delete_rejects_ambiguous_remote(self):
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("branch", "switch", "main")
        self.git("remote", "add", "mirror", str(self.remote))
        before = self.git_snapshot()
        self.assertIn("ambiguous remote",
                      self.run_cli("branch", "delete", "feat/old-name", "--remote", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_branch_delete_remote_rejects_remote_ahead(self):
        self.run_cli("branch", "create", "feat/old-name")
        self.run_cli("push")
        self.run_cli("branch", "switch", "main")
        self.advance_remote_branch("feat/old-name")
        before = self.git_snapshot()
        self.assertIn("remote branch has commits absent from local history",
                      self.run_cli("branch", "delete", "feat/old-name", "--remote", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_branch_delete_remote_rejects_unmerged_local_branch(self):
        self.run_cli("branch", "create", "feat/unmerged")
        self.run_cli("push")
        self.change()
        self.git("add", "tracked")
        self.git("commit", "-qm", "Unmerged")
        self.run_cli("branch", "switch", "main")
        before = self.git_snapshot()
        self.assertIn("not fully merged",
                      self.run_cli("branch", "delete", "feat/unmerged", "--remote", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)

    def test_branch_delete_rejects_unmerged_branch(self):
        self.run_cli("branch", "create", "feat/unmerged")
        self.change()
        self.git("add", "tracked")
        self.git("commit", "-qm", "Unmerged")
        self.run_cli("branch", "switch", "main")
        before = self.git_snapshot()
        self.assertIn("not fully merged",
                      self.run_cli("branch", "delete", "feat/unmerged", ok=False).stderr)
        self.assertEqual(self.git_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
