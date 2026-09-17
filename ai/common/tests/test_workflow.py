import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from sandbox import isolated_environment


COMMON = Path(__file__).resolve().parents[1]
CLI = COMMON / "bin/git-workflow"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.remote = self.root / "remote.git"
        self.repo.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "agent-project-settings").symlink_to(COMMON / "bin/project-settings")
        self.env = isolated_environment(self.root)
        self.env["PATH"] = f"{self.bin}:{self.env['PATH']}"
        shutil.copyfile(COMMON / "tests/stubs/gh", self.bin / "gh")
        (self.bin / "gh").chmod(0o755)
        self.git("init", "-q", "-b", "main")
        self.git("init", "-q", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        (self.repo / "tracked").write_text("original\n")
        self.settings(sync={"mode": "none"})
        self.git("add", ".")
        self.git("commit", "-qm", "Initial")
        self.git("push", "-qu", "origin", "main")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env,
                                       text=True, stderr=subprocess.PIPE).strip()

    def settings(self, **values):
        path = self.repo / ".ai/project.json"
        path.parent.mkdir(exist_ok=True)
        config = json.loads(path.read_text()) if path.exists() else {"schemaVersion": 1, "git": {}}
        for key, value in values.items():
            config["git"].setdefault(key, {}).update(value)
        path.write_text(json.dumps(config))

    def save_settings(self, **values):
        self.settings(**values)
        self.git("add", ".ai/project.json")
        self.git("commit", "-qm", "Configure")

    def run_cli(self, *args, ok=True, executable=CLI):
        result = subprocess.run([str(executable), *args], cwd=self.repo,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def start(self):
        return self.run_cli("start", "--branch-name", "feat/test")

    def change(self, name="tracked"):
        (self.repo / name).write_text("changed\n")

    def commit(self, *paths):
        return self.run_cli("commit", "--message", "Change", "--", *(paths or ("tracked",)))

    def stub_gh(self, existing=False):
        path = self.bin / "gh"
        path.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$GH_LOG"\n'
                        'case "$*" in\n"auth status") exit 0;;\n'
                        f'"pr view "*) {"echo https://example.invalid/pr/1" if existing else "exit 1"};;\n'
                        '"pr create "*) echo https://example.invalid/pr/1;;\n*) exit 2;;\nesac\n')
        path.chmod(0o755)
        self.env["GH_LOG"] = str(self.root / "gh.log")

    def test_command_help_and_dispatch(self):
        self.assertIn("git-workflow start", self.run_cli("--help").stdout)
        for action in ("start", "commit", "finish", "push"):
            self.assertIn("Usage:", self.run_cli(action, "--help").stdout)
            self.assertIn(f"[git] {action} error", self.run_cli(action, "--invalid", ok=False).stderr)
        self.assertIn("no-workflow-created-branch", self.run_cli("finish").stdout)

    def test_command_executables(self):
        for action in ("start", "commit", "finish", "push"):
            path = COMMON / "libexec/git-workflow" / action
            self.assertTrue(os.access(path, os.X_OK))
            self.assertEqual(path.read_text().splitlines()[0], "#!/usr/bin/env bash")
            self.assertIn("Usage:", self.run_cli("--help", executable=path).stdout)

    def check_install(self, mode):
        install_env = isolated_environment(self.root / "installed environment")
        install_env["PATH"] = self.env["PATH"]
        destination = Path(install_env["HOME"])
        self.assertIn(self.root, destination.parents)
        result = subprocess.run([str(COMMON.parent / "install.sh"), "--agents", "codex", mode],
                                env=install_env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifests = list(Path(install_env["XDG_STATE_HOME"]).glob("dotfiles/ai/*.manifest"))
        self.assertEqual({path.name for path in manifests}, {"common.manifest", "codex.manifest"})
        for manifest in manifests:
            for target in manifest.read_text().splitlines():
                self.assertIn(destination, Path(target).parents)
                self.assertTrue(Path(target).exists())
        self.env = {**install_env, "PATH": f"{destination / '.local/bin'}:{install_env['PATH']}"}
        installed = destination / ".local/bin/git-workflow"
        self.assertIn("[git] start ok", self.run_cli("start", "--branch-name", "feat/test", executable=installed).stdout)
        self.change()
        self.run_cli("commit", "--message", "Installed", "--all", executable=installed)
        self.run_cli("push", executable=installed)
        self.run_cli("finish", executable=installed)
        for path in (destination / ".local/lib/git-workflow").glob("*.sh"):
            self.assertFalse(path.stat().st_mode & 0o111)

    def test_symlink_install(self):
        self.check_install("--symlink")

    def test_copy_install(self):
        self.check_install("--copy")

    def test_atomic_paths_and_automatic_push(self):
        self.start()
        self.change()
        self.change("other")
        self.git("add", "other")
        result = self.commit()
        self.assertIn("pushed=true", result.stdout)
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"), "tracked")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "other")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

    def test_all_and_invalid_commit(self):
        self.start()
        self.change()
        self.change("other")
        self.run_cli("commit", "--message", "All", "--all")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertIn("no changes", self.run_cli("commit", "--message", "Empty", "--all", ok=False).stderr)
        self.run_cli("commit", "--message", "Invalid", "--all", "--", "tracked", ok=False)
        self.run_cli("commit", "--all", ok=False)

    def test_manual_commit_and_push(self):
        self.save_settings(commit={"mode": "manual"})
        self.start()
        self.change()
        before = self.git("rev-parse", "HEAD")
        self.assertIn("skip reason=manual", self.commit().stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertIn("pushed=false", self.run_cli("commit", "--override-manual",
                      "--message", "Manual", "--", "tracked").stdout)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test"), "")
        self.assertIn("skip reason=manual", self.run_cli("push").stdout)
        self.run_cli("push", "--override-manual")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/feat/test"))

    def test_start_backup_modes_and_methods(self):
        for method in ("stash", "commit"):
            for mode in ("none", "tracked", "untracked", "all"):
                with self.subTest(method=method, mode=mode):
                    self.save_settings(backup={"mode": mode, "method": method}, branch={"mode": "current"})
                    self.change()
                    self.git("add", "tracked")
                    self.change("other")
                    status = self.git("status", "--porcelain")
                    result = self.start()
                    self.assertIn(f"backup={method if mode != 'none' else 'none'}", result.stdout)
                    self.assertEqual(self.git("status", "--porcelain"), status)
                    self.assertEqual((self.repo / "tracked").read_text(), "changed\n")
                    self.assertEqual((self.repo / "other").read_text(), "changed\n")
                    if mode != "none":
                        refs = "refs/stash" if method == "stash" else "refs/agent-workflow/backups/"
                        self.assertTrue(self.git("for-each-ref", "--format=%(refname)", refs))
                    self.git("restore", "--staged", "--worktree", "tracked")
                    (self.repo / "other").unlink()

    def test_start_branch_modes(self):
        self.save_settings(branch={"mode": "current"})
        self.assertIn("created=false", self.start().stdout)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.save_settings(branch={"mode": "alwaysCreate"})
        self.assertIn("created=true", self.start().stdout)
        self.assertEqual(self.git("config", "branch.feat/test.agentWorkflowBase"), "main")
        self.save_settings(branch={"mode": "fromBase"})
        self.assertIn("created=false", self.start().stdout)

    def test_sync_modes_and_update_methods(self):
        for mode, method in (("none", "ffOnly"), ("fetch", "ffOnly"),
                             ("update", "ffOnly"), ("update", "rebase"), ("update", "merge")):
            with self.subTest(mode=mode, method=method):
                self.save_settings(sync={"mode": mode, "updateMethod": method}, branch={"mode": "current"})
                self.git("push", "-q", "origin", "main")
                old = self.git("rev-parse", "HEAD")
                tree = self.git("rev-parse", "HEAD^{tree}")
                remote_tip = self.git("--git-dir", str(self.remote), "commit-tree", tree, "-p", old, "-m", "Remote change")
                self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main", remote_tip)
                result = self.start()
                self.assertIn(f"sync={dict(none='skipped', fetch='fetched', update='updated')[mode]}", result.stdout)
                self.assertEqual(self.git("rev-parse", "HEAD"), remote_tip if mode == "update" else old)
                if mode != "none":
                    self.assertEqual(self.git("rev-parse", "origin/main"), remote_tip)
                self.git("fetch", "-q", "origin")
                self.git("merge", "--ff-only", remote_tip)

    def finish_local(self, method, cleanup):
        self.save_settings(integration={"mergeMethod": method}, branch={"deleteAfterIntegration": cleanup})
        self.start()
        self.change()
        self.commit()
        result = self.run_cli("finish", "--message", "Deliver")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Deliver")
        self.assertIn(f"method={method}", result.stdout)
        self.assertIn(f"deleted={'local-and-remote' if cleanup else 'false'}", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/main"))
        self.assertEqual((self.repo / "tracked").read_text(), "changed\n")
        self.assertEqual(len(self.git("rev-list", "--parents", "-n", "1", "HEAD").split()),
                         3 if method == "mergeCommit" else 2)
        self.assertEqual(bool(self.git("ls-remote", "--heads", "origin", "feat/test")), not cleanup)
        self.assertEqual(bool(self.git("branch", "--list", "feat/test")), not cleanup)

    def test_finish_merge_commit(self):
        self.finish_local("mergeCommit", False)

    def test_finish_merge_cleanup(self):
        self.finish_local("mergeCommit", True)

    def test_finish_squash(self):
        self.finish_local("squash", False)

    def test_finish_squash_cleanup(self):
        self.finish_local("squash", True)

    def test_finish_invalid_state(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.start()
        self.change()
        self.assertIn("working tree must be clean", self.run_cli("finish", "--message", "Deliver", ok=False).stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_pull_request_create_and_reuse(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.start()
        self.change()
        self.commit()
        self.assertIn("url=https://example.invalid/pr/1", self.run_cli("finish", "--title", "Task", "--body", "Details").stdout)
        log = Path(self.env["GH_LOG"])
        self.assertIn("pr create --base main --head feat/test --title Task --body Details", log.read_text())
        self.stub_gh(existing=True)
        log.write_text("")
        self.run_cli("finish", "--title", "Task")
        self.assertNotIn("pr create", log.read_text())
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("config", "branch.feat/test.agentWorkflowCreated"), "true")

    def test_squash_default_single_commit_subject(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.start()
        self.change()
        self.commit()
        self.run_cli("finish")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Change")

    def test_squash_default_multiple_commit_summary(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.run_cli("start", "--branch-name", "feat/ai-harness-preflight")
        self.change()
        self.commit()
        self.change("other")
        self.commit("other")
        self.run_cli("finish")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "feat(ai): harness preflight")

    def test_squash_default_unconventional_branch(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.run_cli("start", "--branch-name", "work-in-progress")
        self.change()
        self.commit()
        self.change("other")
        self.commit("other")
        self.run_cli("finish")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "work-in-progress")

    def test_merge_commit_keeps_git_default_message(self):
        self.start()
        self.change()
        self.commit()
        self.run_cli("finish")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Merge branch 'feat/test'")

    def test_pull_request_default_title(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.run_cli("start", "--branch-name", "feat/ai-harness-preflight")
        self.change()
        self.commit()
        self.run_cli("finish")
        log = Path(self.env["GH_LOG"])
        self.assertIn("--title Change --body", log.read_text())
        self.change("other")
        self.commit("other")
        log.write_text("")
        self.run_cli("finish")
        self.assertIn("--title feat(ai): harness preflight --body", log.read_text())

    def test_finish_rechecks_authentication(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.start()
        (self.bin / "gh").write_text("#!/bin/sh\nexit 1\n")
        self.change()
        self.commit()
        self.run_cli("push")
        self.assertIn("check=gh-auth", self.run_cli("finish", "--title", "Task", ok=False).stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("config", "branch.feat/test.agentWorkflowCreated"), "true")


if __name__ == "__main__":
    unittest.main()
