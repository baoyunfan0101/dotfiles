import json
from copy import deepcopy
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest

from sandbox import isolated_environment


COMMON = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = runpy.run_path(str(COMMON / "bin/agent-project"))["DEFAULT_SETTINGS"]
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
        (self.bin / "agent-project").symlink_to(COMMON / "bin/agent-project")
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
        config = json.loads(path.read_text()) if path.exists() else deepcopy(DEFAULT_SETTINGS)
        config["workflow"]["enabled"] = True
        for key, value in values.items():
            config["git"][key].update(value)
        path.write_text(json.dumps(config))

    def save_settings(self, **values):
        self.settings(**values)
        self.git("add", ".ai/project.json")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.repo,
                          env=self.env).returncode != 0:
            self.git("commit", "-qm", "Configure")

    def run_cli(self, *args, ok=True, executable=CLI):
        result = subprocess.run([str(executable), *args], cwd=self.repo,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def prepare(self):
        return self.run_cli("prepare", "--branch-name", "feat/test")

    def change(self, name="tracked"):
        (self.repo / name).write_text("changed\n")

    def commit(self, *paths):
        return self.run_cli("commit", "--message", "Change", "--", *(paths or ("tracked",)))

    def stub_gh(self):
        path = self.bin / "gh"
        shutil.copyfile(COMMON / "tests/stubs/gh-pr", path)
        path.chmod(0o755)
        self.env["GH_LOG"] = str(self.root / "gh.log")
        self.env["GH_STATE"] = str(self.root / "pr.json")

    def gh_calls(self):
        log = Path(self.env["GH_LOG"])
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def git_snapshot(self):
        return (self.git("branch", "--show-current"), self.git("rev-parse", "HEAD"),
                self.git("status", "--porcelain"),
                self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"),
                self.git("ls-remote", "--heads", "origin"),
                {str(path.relative_to(self.repo)): path.read_bytes()
                 for path in self.repo.rglob("*") if path.is_file() and ".git" not in path.relative_to(self.repo).parts})

    def assert_no_in_progress_git_operation(self):
        for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply"):
            self.assertFalse((self.repo / ".git" / name).exists(), name)
        self.assertEqual(self.git("ls-files", "--unmerged"), "")

    def diverge_main_with_conflict(self):
        sequence = getattr(self, "conflict_sequence", 0) + 1
        self.conflict_sequence = sequence
        upstream = Path(tempfile.mkdtemp(dir=self.root))
        subprocess.check_call(["git", "clone", "-q", "-b", "main", str(self.remote), str(upstream)],
                              env=self.env)
        (self.repo / "tracked").write_text(f"local conflict {sequence}\n")
        self.git("add", "tracked")
        self.git("commit", "-qm", "Local conflicting change")
        local_head = self.git("rev-parse", "HEAD")
        (upstream / "tracked").write_text(f"remote conflict {sequence}\n")
        subprocess.check_call(["git", "-C", str(upstream), "add", "tracked"], env=self.env)
        subprocess.check_call(["git", "-C", str(upstream), "commit", "-qm", "Remote conflicting change"],
                              env=self.env)
        subprocess.check_call(["git", "-C", str(upstream), "push", "-q", "origin", "main"],
                              env=self.env)
        return local_head, self.git("ls-remote", "--heads", "origin", "main")

    def existing_branch(self, mode="pullRequest", bases=("main",), branch_mode="current"):
        self.stub_gh()
        self.save_settings(integration={"mode": mode},
                           branch={"mode": branch_mode, "baseBranches": list(bases)})
        self.git("push", "-q", "origin", "main")
        for base in bases:
            if base != "main":
                self.git("branch", base)
                self.git("push", "-q", "origin", base)
        self.git("switch", "-c", "fix/manual")
        result = self.run_cli("prepare")
        self.assertIn("created=false", result.stdout)
        self.assertNotIn("base=", result.stdout)
        self.change()
        self.commit()
        self.run_cli("push")

    def test_existing_current_branch_pr_delivery(self):
        self.existing_branch()
        self.run_cli("pr", "submit")
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["base"], "main")
        self.run_cli("pr", "merge")
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_existing_from_base_branch_is_reused(self):
        self.existing_branch(branch_mode="fromBase")
        self.run_cli("pr", "submit")
        self.assertEqual(self.git("branch", "--show-current"), "fix/manual")

    def test_current_branch_local_delivery(self):
        self.existing_branch(mode="localMerge")
        self.run_cli("merge")
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_multiple_local_bases_require_explicit_base(self):
        self.existing_branch(mode="localMerge", bases=("main", "develop"))
        before = self.git_snapshot()
        self.assertIn("ambiguous", self.run_cli("merge", ok=False).stderr)
        self.assertEqual(before, self.git_snapshot())
        self.run_cli("merge", "--base", "develop")
        self.assertEqual(self.git("branch", "--show-current"), "develop")

    def test_local_base_sync_failure_keeps_current_branch(self):
        self.existing_branch(mode="localMerge")
        self.save_settings(sync={"mode": "update", "updateMethod": "ffOnly"})
        original = self.git("rev-parse", "HEAD")
        local_base = self.git("commit-tree", "main^{tree}", "-p", "main", "-m", "Local base advance")
        self.git("update-ref", "refs/heads/main", local_base)
        remote_base = self.git("--git-dir", str(self.remote), "commit-tree", "main^{tree}",
                               "-p", "main", "-m", "Remote base advance")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main", remote_base)
        self.assertIn("base sync failed", self.run_cli("merge", ok=False).stderr)
        self.assertEqual(self.git("branch", "--show-current"), "fix/manual")
        self.assertEqual(self.git("rev-parse", "HEAD"), original)
        self.assertEqual(self.git("rev-parse", "main"), local_base)

    def test_local_base_conflicting_sync_restores_working_branch(self):
        for method in ("rebase", "merge"):
            with self.subTest(method=method):
                self.save_settings(sync={"mode": "update", "updateMethod": method})
                self.git("push", "-q", "origin", "main")
                self.git("switch", "-c", f"fix/sync-{method}")
                (self.repo / "feature").write_text("working change\n")
                self.git("add", "feature")
                self.git("commit", "-qm", "Working change")
                working_head = self.git("rev-parse", "HEAD")
                self.git("switch", "main")
                base_head, remote_base = self.diverge_main_with_conflict()
                self.git("switch", f"fix/sync-{method}")

                result = self.run_cli("merge", ok=False)
                self.assertIn("base sync failed", result.stderr)
                self.assertEqual(self.git("branch", "--show-current"), f"fix/sync-{method}")
                self.assertEqual(self.git("rev-parse", "HEAD"), working_head)
                self.assertEqual(self.git("rev-parse", "main"), base_head)
                self.assertEqual(self.git("ls-remote", "--heads", "origin", "main"), remote_base)
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assert_no_in_progress_git_operation()
                self.assertTrue(self.git("branch", "--list", f"fix/sync-{method}"))
                self.git("switch", "main")
                self.git("reset", "--hard", "-q", "origin/main")

    def test_pr_base_resolution_and_disambiguation(self):
        self.existing_branch(bases=("main", "develop"))
        before = self.git_snapshot()
        self.assertIn("ambiguous", self.run_cli("pr", "submit", ok=False).stderr)
        self.assertEqual(before, self.git_snapshot())
        self.run_cli("pr", "submit", "--base", "develop")
        self.run_cli("pr", "submit")
        state_path = Path(self.env["GH_STATE"])
        self.assertEqual(json.loads(state_path.read_text())["base"], "develop")
        self.env["GH_EXTRA_PRS"] = json.dumps([{"url": "https://example.invalid/pr/2", "baseRefName": "main"}])
        for action in ("submit", "merge"):
            before = self.git_snapshot()
            self.assertIn("ambiguous", self.run_cli("pr", action, ok=False).stderr)
            self.assertEqual(before, self.git_snapshot())
        self.run_cli("pr", "submit", "--base=develop")
        self.run_cli("pr", "merge", "--base", "develop")
        self.assertEqual(self.git("branch", "--show-current"), "develop")
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in self.gh_calls()), 1)

    def test_existing_pr_base_overrides_multiple_configured_defaults(self):
        self.existing_branch(bases=("main", "develop"))
        self.run_cli("pr", "submit", "--base", "develop")
        self.run_cli("pr", "merge")
        self.assertEqual(self.git("branch", "--show-current"), "develop")

    def test_unconfigured_pr_base_fails_without_mutation(self):
        self.existing_branch()
        self.env["GH_EXTRA_PRS"] = json.dumps([{"url": "https://example.invalid/pr/2", "baseRefName": "outside"}])
        for action in ("submit", "merge"):
            before = self.git_snapshot()
            self.assertIn("PR base is not configured", self.run_cli("pr", action, ok=False).stderr)
            self.assertEqual(before, self.git_snapshot())
        self.run_cli("pr", "submit", "--base", "main")
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["base"], "main")

    def test_invalid_explicit_bases_fail_before_mutation(self):
        self.existing_branch()
        for action in ("submit", "merge"):
            before = self.git_snapshot()
            self.assertIn("base is not configured", self.run_cli("pr", action, "--base", "outside", ok=False).stderr)
            self.assertEqual(before, self.git_snapshot())
        self.assertEqual(self.gh_calls(), [])
        self.save_settings(integration={"mode": "localMerge"})
        before = self.git_snapshot()
        self.assertIn("base is not configured", self.run_cli("merge", "--base", "outside", ok=False).stderr)
        self.assertEqual(before, self.git_snapshot())

    def test_all_configured_base_branches_reject_delivery(self):
        for mode, actions in (("localMerge", [("merge",)]),
                              ("pullRequest", [("pr", "submit"), ("pr", "merge")])):
            self.save_settings(integration={"mode": mode}, branch={"baseBranches": ["main", "develop"]})
            for action in actions:
                before = self.git_snapshot()
                self.assertIn("configured base branch", self.run_cli(*action, "--base", "develop", ok=False).stderr)
                self.assertEqual(before, self.git_snapshot())

    def test_command_help_and_dispatch(self):
        help_text = self.run_cli("--help").stdout
        self.assertEqual(set(re.findall(r"^  ([a-z][a-z-]*)$", help_text, re.MULTILINE)),
                         {"prepare", "commit", "restore", "revert", "cherry-pick",
                          "rebase", "merge", "push", "pr"})
        for meaning in ("before editing", "after editing", "--message MESSAGE",
                        "-- PATH...", "outside commit", "current branch",
                        "local", "pull request", "--branch-name NAME"):
            self.assertIn(meaning, help_text)
        for action in ("prepare", "commit", "restore", "revert", "cherry-pick",
                       "rebase", "merge", "push"):
            self.assertIn("Usage:", self.run_cli(action, "--help").stdout)
            self.assertIn(f"[git] {action} error", self.run_cli(action, "--invalid", ok=False).stderr)
        self.assertIn("configured base branch", self.run_cli("merge", ok=False).stderr)
        pr_help = self.run_cli("pr", "--help").stdout
        self.assertEqual(set(re.findall(r"^  ([a-z]+)$", pr_help, re.MULTILINE)), {"submit", "merge"})
        for command in ("start", "finish", "add", "stash", "pull", "checkout",
                        "switch", "branch", "reset"):
            self.assertIn("unknown command", self.run_cli(command, ok=False).stderr)
            self.assertNotIn(f"  git-workflow {command}\n", help_text)
        self.run_cli("pr", "unknown", ok=False)
        self.run_cli("pr", "submit", "--title", "No override", ok=False)

    def test_disabled_or_unconfigured_project_skips_workflow(self):
        path = self.repo / ".ai/project.json"
        path.unlink()

        for action, arguments in (
            ("prepare", ("--branch-name", "feat/test")),
            ("commit", ("--message", "Change", "--all")),
            ("push", ()),
            ("merge", ()),
            ("pr", ("submit",)),
            ("pr", ("merge",)),
        ):
            result = self.run_cli(action, *arguments)
            self.assertEqual(
                result.stdout,
                f"[git] {action + ' ' + arguments[0] if action == 'pr' else action} skip reason=workflow-disabled\n",
            )

    def test_command_executables(self):
        for action in ("prepare", "commit", "restore", "revert", "cherry-pick",
                       "rebase", "merge", "push", "pr"):
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
        installed_agents = destination / ".codex/AGENTS.md"
        self.assertTrue(installed_agents.is_file())
        self.assertFalse(installed_agents.is_symlink())
        self.assertIn("<!-- BEGIN baoyunfan0101/dotfiles managed block -->",
                      installed_agents.read_text())
        for manifest in manifests:
            for target in manifest.read_text().splitlines():
                self.assertIn(destination, Path(target).parents)
                self.assertTrue(Path(target).exists())
        self.env = {**install_env, "PATH": f"{destination / '.local/bin'}:{install_env['PATH']}"}
        installed_project = destination / ".local/bin/agent-project"
        self.assertTrue(installed_project.exists())
        installed_probe = destination / ".local/libexec/agent-workflow-opt-in"
        self.assertTrue(os.access(installed_probe, os.X_OK))
        probe = subprocess.run([str(installed_probe)], cwd=self.repo, env=self.env,
                               capture_output=True, text=True)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertFalse((destination / ".local/bin/agent-project-settings").exists())
        self.assertIn(
            "agent-project set",
            self.run_cli("--help", executable=installed_project).stdout,
        )

        installed = destination / ".local/bin/git-workflow"
        for command in ("prepare", "commit", "restore", "revert", "cherry-pick",
                        "rebase", "push", "merge", "pr"):
            self.assertTrue(os.access(destination / ".local/libexec/git-workflow" / command, os.X_OK))
        for command in ("start", "finish"):
            self.assertFalse((destination / ".local/libexec/git-workflow" / command).exists())
        for action in ("submit", "merge"):
            self.assertIn("Usage:", self.run_cli("pr", action, "--help", executable=installed).stdout)
        self.assertIn("[git] prepare ok", self.run_cli("prepare", "--branch-name", "feat/test", executable=installed).stdout)
        self.change()
        self.run_cli("commit", "--message", "Installed", "--all", executable=installed)
        self.run_cli("push", executable=installed)
        self.run_cli("merge", executable=installed)
        for path in (destination / ".local/lib/git-workflow").glob("*.sh"):
            self.assertFalse(path.stat().st_mode & 0o111)

    def test_symlink_install(self):
        self.check_install("--symlink")

    def test_copy_install(self):
        self.check_install("--copy")

    def test_atomic_paths_and_automatic_push(self):
        self.prepare()
        self.change()
        self.change("other")
        self.git("add", "other")
        result = self.commit()
        self.assertIn("pushed=true", result.stdout)
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"), "tracked")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "other")
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
        self.prepare()
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
        result = self.run_cli("cherry-pick", conflicting_sha, ok=False)
        self.assertIn("original branch and HEAD were restored", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
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

    def test_prepare_backup_modes_and_methods(self):
        for method in ("stash", "commit"):
            for mode in ("none", "tracked", "untracked", "all"):
                with self.subTest(method=method, mode=mode):
                    self.save_settings(backup={"mode": mode, "method": method}, branch={"mode": "current"})
                    self.change()
                    self.git("add", "tracked")
                    self.change("other")
                    status = self.git("status", "--porcelain")
                    result = self.prepare()
                    self.assertIn(f"backup={method if mode != 'none' else 'none'}", result.stdout)
                    self.assertEqual(self.git("status", "--porcelain"), status)
                    self.assertEqual((self.repo / "tracked").read_text(), "changed\n")
                    self.assertEqual((self.repo / "other").read_text(), "changed\n")
                    if mode != "none":
                        refs = "refs/stash" if method == "stash" else "refs/agent-workflow/backups/"
                        self.assertTrue(self.git("for-each-ref", "--format=%(refname)", refs))
                    self.git("restore", "--staged", "--worktree", "tracked")
                    (self.repo / "other").unlink()

    def test_prepare_branch_modes(self):
        self.save_settings(branch={"mode": "current"})
        self.assertIn("created=false", self.prepare().stdout)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.save_settings(branch={"mode": "alwaysCreate"})
        self.assertIn("created=true", self.prepare().stdout)
        self.save_settings(branch={"mode": "fromBase"})
        self.assertIn("created=false", self.prepare().stdout)

    def test_prepare_explicit_base_starts_from_latest_base(self):
        self.save_settings(sync={"mode": "update", "updateMethod": "ffOnly"})
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
        self.assertEqual(self.git("branch", "--show-current"), "feat/next")
        self.assertEqual(self.git("rev-parse", "HEAD"), remote_tip)
        self.assertEqual(self.git("rev-parse", "main"), remote_tip)
        self.assertEqual(self.git("rev-parse", "feat/test"), old_branch_head)
        self.assertEqual((self.repo / "user-note").read_text(), "protected\n")
        self.assertIn("user-note", self.git("status", "--porcelain"))

    def test_prepare_explicit_base_validates_before_mutation(self):
        self.prepare()
        for arguments, message in (
            (("--base", "outside", "--branch-name", "feat/next"), "base is not configured"),
            (("--base", "main"), "--base requires --branch-name"),
            (("--base", "main", "--branch-name", "feat/test"), "branch already exists"),
        ):
            with self.subTest(arguments=arguments):
                before = self.git_snapshot()
                self.assertIn(message, self.run_cli("prepare", *arguments, ok=False).stderr)
                self.assertEqual(self.git_snapshot(), before)

    def test_prepare_explicit_base_sync_conflict_restores_original_branch(self):
        self.save_settings(sync={"mode": "update", "updateMethod": "merge"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        working_head = self.git("rev-parse", "HEAD")
        self.git("switch", "main")
        base_head, remote_head = self.diverge_main_with_conflict()
        self.git("switch", "feat/test")
        (self.repo / "user-note").write_text("protected\n")

        result = self.run_cli("prepare", "--base", "main", "--branch-name", "feat/next", ok=False)
        self.assertIn("base sync failed", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("rev-parse", "HEAD"), working_head)
        self.assertEqual(self.git("rev-parse", "main"), base_head)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "main"), remote_head)
        self.assertEqual((self.repo / "user-note").read_text(), "protected\n")
        self.assertEqual(self.git("branch", "--list", "feat/next"), "")
        self.assert_no_in_progress_git_operation()

    def test_prepare_explicit_base_retains_backup_on_restore_failure(self):
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

        result = self.run_cli("prepare", "--base", "main", "--branch-name", "feat/next", ok=False)
        self.assertIn("backup retained", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git("branch", "--list", "feat/next"), "")
        self.assertTrue(self.git("stash", "list"))

    def test_prepare_explicit_base_branch_creation_failure_restores_changes(self):
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
        self.assertEqual((self.repo / "user-note").read_text(), "protected\n")
        self.assertEqual(self.git("branch", "--list", "feat/next"), "")
        self.assert_no_in_progress_git_operation()

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
                result = self.prepare()
                self.assertIn(f"sync={dict(none='skipped', fetch='fetched', update='updated')[mode]}", result.stdout)
                self.assertEqual(self.git("rev-parse", "HEAD"), remote_tip if mode == "update" else old)
                if mode != "none":
                    self.assertEqual(self.git("rev-parse", "origin/main"), remote_tip)
                self.git("fetch", "-q", "origin")
                self.git("merge", "--ff-only", remote_tip)

    def test_conflicting_sync_restores_original_head(self):
        for method in ("rebase", "merge"):
            with self.subTest(method=method):
                self.save_settings(sync={"mode": "update", "updateMethod": method},
                                   backup={"mode": "none"}, branch={"mode": "current"})
                self.git("push", "-q", "origin", "main")
                local_head, remote_head = self.diverge_main_with_conflict()

                result = self.run_cli("prepare", ok=False)
                self.assertIn("sync failed", result.stderr)
                self.assertEqual(self.git("branch", "--show-current"), "main")
                self.assertEqual(self.git("rev-parse", "HEAD"), local_head)
                self.assertEqual(self.git("ls-remote", "--heads", "origin", "main"), remote_head)
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assert_no_in_progress_git_operation()
                self.git("reset", "--hard", "-q", "origin/main")

    def test_prepare_sync_conflict_reapplies_protected_changes(self):
        for update_method in ("ffOnly", "rebase", "merge"):
            for backup_method in ("stash", "commit"):
                with self.subTest(update_method=update_method, backup_method=backup_method):
                    self.save_settings(sync={"mode": "update", "updateMethod": update_method},
                                       backup={"mode": "all", "method": backup_method},
                                       branch={"mode": "current"})
                    if not (self.repo / "staged").exists():
                        (self.repo / "staged").write_text("original staged\n")
                        (self.repo / "unstaged").write_text("original unstaged\n")
                        self.git("add", "staged", "unstaged")
                        self.git("commit", "-qm", "Add change targets")
                    self.git("push", "-q", "origin", "main")
                    local_head, remote_head = self.diverge_main_with_conflict()
                    (self.repo / "staged").write_text("user staged\n")
                    self.git("add", "staged")
                    (self.repo / "unstaged").write_text("user unstaged\n")
                    (self.repo / "untracked").write_text("user untracked\n")
                    original_status = self.git("status", "--porcelain")

                    result = self.run_cli("prepare", ok=False)
                    self.assertIn("sync failed", result.stderr)
                    self.assertEqual(self.git("branch", "--show-current"), "main")
                    self.assertEqual(self.git("rev-parse", "HEAD"), local_head)
                    self.assertEqual(self.git("ls-remote", "--heads", "origin", "main"), remote_head)
                    self.assertEqual(self.git("status", "--porcelain"), original_status)
                    self.assertEqual((self.repo / "staged").read_text(), "user staged\n")
                    self.assertEqual((self.repo / "unstaged").read_text(), "user unstaged\n")
                    self.assertEqual((self.repo / "untracked").read_text(), "user untracked\n")
                    self.assert_no_in_progress_git_operation()
                    self.git("reset", "--hard", "-q", "origin/main")
                    (self.repo / "untracked").unlink()

    def test_prepare_keeps_transport_backup_when_reapply_fails(self):
        self.save_settings(sync={"mode": "update", "updateMethod": "merge"},
                           backup={"mode": "all", "method": "commit"},
                           branch={"mode": "current"})
        self.git("push", "-q", "origin", "main")
        local_head, _ = self.diverge_main_with_conflict()
        (self.repo / "user-change").write_text("protected\n")
        self.git("add", "user-change")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text(f'#!/usr/bin/env bash\n'
                            f'if [[ "$1" == stash && "$2" == apply ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("prepare", ok=False)
        self.assertIn("backup was retained", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), local_head)
        self.assert_no_in_progress_git_operation()
        self.assertTrue(self.git("stash", "list"))
        self.assertTrue(self.git("for-each-ref", "--format=%(refname)",
                                 "refs/agent-workflow/backups/"))

    def merge_local(self, method, cleanup):
        self.save_settings(integration={"mergeMethod": method}, branch={"deleteAfterIntegration": cleanup})
        self.prepare()
        self.change()
        self.commit()
        result = self.run_cli("merge", "--message", "Deliver")
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

    def test_merge_merge_commit(self):
        self.merge_local("mergeCommit", False)

    def test_merge_merge_cleanup(self):
        self.merge_local("mergeCommit", True)

    def test_merge_squash(self):
        self.merge_local("squash", False)

    def test_merge_squash_cleanup(self):
        self.merge_local("squash", True)

    def test_local_integration_conflicts_restore_both_branches(self):
        for method in ("mergeCommit", "squash"):
            with self.subTest(method=method):
                self.save_settings(integration={"mergeMethod": method})
                self.git("push", "-q", "origin", "main")
                branch_name = f"feat/test-{method}"
                self.run_cli("prepare", "--branch-name", branch_name)
                self.change()
                self.commit()
                working_head = self.git("rev-parse", "HEAD")
                self.git("switch", "main")
                (self.repo / "tracked").write_text("base change\n")
                self.git("add", "tracked")
                self.git("commit", "-qm", "Conflicting base change")
                base_head = self.git("rev-parse", "HEAD")
                remote_base = self.git("ls-remote", "--heads", "origin", "main")
                self.git("switch", branch_name)

                result = self.run_cli("merge", ok=False)
                self.assertIn("local integration failed", result.stderr)
                self.assertEqual(self.git("branch", "--show-current"), branch_name)
                self.assertEqual(self.git("rev-parse", "HEAD"), working_head)
                self.assertEqual(self.git("rev-parse", "main"), base_head)
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assertEqual(self.git("ls-files", "--unmerged"), "")
                self.assertEqual(self.git("ls-remote", "--heads", "origin", "main"), remote_base)
                self.assertEqual(self.git("branch", "--list", branch_name), f"* {branch_name}")
                self.git("switch", "main")
                self.git("reset", "--hard", "-q", "origin/main")
                self.git("branch", "-D", branch_name)

    def test_merge_invalid_state(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.prepare()
        self.change()
        self.assertIn("working tree must be clean", self.run_cli("merge", "--message", "Deliver", ok=False).stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_pull_request_create_and_reuse(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        for subject in ("Commit A", "Commit B", "Commit C"):
            (self.repo / "tracked").write_text(subject)
            self.run_cli("prepare")
            self.run_cli("commit", "--message", subject, "--", "tracked")
        self.assertEqual(self.gh_calls(), [])
        self.assertIn("url=https://example.invalid/pr/1", self.run_cli("pr", "submit").stdout)
        state_path = Path(self.env["GH_STATE"])
        state = json.loads(state_path.read_text())
        self.assertEqual(state["title"], "feat/test")
        self.assertEqual(state["body"], "## Summary\n\n- Commit A\n- Commit B\n- Commit C")
        self.run_cli("pr", "submit")
        self.change("other")
        self.run_cli("commit", "--message", "Commit D", "--", "other")
        self.run_cli("pr", "submit")
        state = json.loads(state_path.read_text())
        self.assertEqual(state["body"], "## Summary\n\n- Commit A\n- Commit B\n- Commit C\n- Commit D")
        calls = self.gh_calls()
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in calls), 1)
        self.assertEqual(sum(call[:2] == ["pr", "edit"] for call in calls), 2)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in calls))
        for call in calls:
            if call[:2] == ["pr", "list"]:
                self.assertEqual(call[2:6], ["--state", "open", "--head", "feat/test"])
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_pr_submit_without_new_commits_does_not_push(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.git("switch", "-c", "fix/no-new-commits")
        remote_before = self.git("ls-remote", "--heads", "origin")

        result = self.run_cli("pr", "submit", ok=False)
        self.assertIn("no commits to submit", result.stderr)
        self.assertEqual(self.git("ls-remote", "--heads", "origin"), remote_before)
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "edit"])
                             for call in self.gh_calls()))

    def test_commit_and_push_do_not_load_prepare_modules(self):
        runtime = (COMMON / "lib/git-workflow/runtime.sh").read_text()
        for module in ("backup.sh", "sync.sh", "branch.sh"):
            self.assertNotIn(f'source "$WORKFLOW_ROOT/lib/git-workflow/{module}"', runtime)
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("push")

    def test_squash_default_single_commit_subject(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Change")

    def test_squash_default_multiple_commit_summary(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.run_cli("prepare", "--branch-name", "feat/ai-harness-preflight")
        self.change()
        self.commit()
        self.change("other")
        self.commit("other")
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "feat(ai): harness preflight")

    def test_squash_default_unconventional_branch(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.run_cli("prepare", "--branch-name", "work-in-progress")
        self.change()
        self.commit()
        self.change("other")
        self.commit("other")
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "work-in-progress")

    def test_merge_commit_keeps_git_default_message(self):
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Merge branch 'feat/test'")

    def test_pull_request_default_title(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.run_cli("prepare", "--branch-name", "feat/ai-harness-preflight")
        self.change()
        self.commit()
        self.run_cli("pr", "submit")
        state_path = Path(self.env["GH_STATE"])
        self.assertEqual(json.loads(state_path.read_text())["title"], "Change")
        self.change("other")
        self.commit("other")
        self.run_cli("pr", "submit")
        self.assertEqual(json.loads(state_path.read_text())["title"], "feat(ai): harness preflight")

    def test_pr_rechecks_authentication(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        (self.bin / "gh").write_text("#!/bin/sh\nexit 1\n")
        self.change()
        self.commit()
        self.run_cli("push")
        for action in ("submit", "merge"):
            self.assertIn("check=gh-auth", self.run_cli("pr", action, ok=False).stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_delivery_mode_isolation(self):
        self.prepare()
        for action in ("submit", "merge"):
            self.assertIn("localMerge mode", self.run_cli("pr", action, ok=False).stderr)
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.assertIn("git-workflow pr merge", self.run_cli("merge", ok=False).stderr)

    def test_pr_merge_missing_or_wrong_base(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.assertIn("no open PR", self.run_cli("pr", "merge", ok=False).stderr)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in self.gh_calls()))
        self.change()
        self.commit()
        self.run_cli("pr", "submit")
        state_path = Path(self.env["GH_STATE"])
        state = json.loads(state_path.read_text())
        state["base"] = "other"
        state_path.write_text(json.dumps(state))
        self.run_cli("pr", "merge", ok=False)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.gh_calls()))

    def check_pr_merge(self, method, cleanup):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest", "mergeMethod": method},
                           branch={"deleteAfterIntegration": cleanup})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("pr", "submit")
        result = self.run_cli("pr", "merge")
        call = next(call for call in self.gh_calls() if call[:2] == ["pr", "merge"])
        self.assertIn("--merge" if method == "mergeCommit" else "--squash", call)
        self.assertNotIn("--auto", call)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/main"))
        self.assertEqual((self.repo / "tracked").read_text(), "changed\n")
        self.assertIn(f"deleted={'local-and-remote' if cleanup else 'false'}", result.stdout)
        self.assertEqual(bool(self.git("branch", "--list", "feat/test")), not cleanup)
        self.assertEqual(bool(self.git("ls-remote", "--heads", "origin", "feat/test")), not cleanup)
        self.assertEqual(self.git("config", "--get-regexp", "^branch.main.remote$"), "branch.main.remote origin")

    def test_pr_merge_commit(self):
        self.check_pr_merge("mergeCommit", False)

    def test_pr_merge_commit_cleanup(self):
        self.check_pr_merge("mergeCommit", True)

    def test_pr_squash(self):
        self.check_pr_merge("squash", False)

    def test_pr_squash_cleanup(self):
        self.check_pr_merge("squash", True)

    def test_pr_failures_preserve_git_state(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.run_cli("pr", "submit", ok=False)
        self.assertEqual(self.gh_calls(), [])
        self.commit()
        self.env["GH_FAIL"] = "pr list"
        self.run_cli("pr", "submit", ok=False)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in self.gh_calls()))
        del self.env["GH_FAIL"]
        self.run_cli("pr", "submit")
        self.env["GH_FAIL"] = "pr merge"
        self.run_cli("pr", "merge", ok=False)
        del self.env["GH_FAIL"]
        self.env["GH_PENDING"] = "1"
        self.run_cli("pr", "merge", ok=False)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_pr_sync_failure_preserves_state_and_branches(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"},
                           branch={"deleteAfterIntegration": True})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("pr", "submit")
        local_base = self.git("commit-tree", "main^{tree}", "-p", "main", "-m", "Local base advance")
        self.git("update-ref", "refs/heads/main", local_base)
        self.run_cli("pr", "merge", ok=False)
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["state"], "MERGED")
        self.assertEqual(self.git("rev-parse", "main"), local_base)
        self.assertTrue(self.git("branch", "--list", "feat/test"))
        self.assertTrue(self.git("ls-remote", "--heads", "origin", "feat/test"))

    def test_pr_merge_rejects_merge_queue_before_mutation(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("pr", "submit")
        for queue_value in ("true", "null"):
            self.env["GH_MERGE_QUEUE"] = queue_value
            self.run_cli("pr", "merge", ok=False)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.gh_calls()))


if __name__ == "__main__":
    unittest.main()
