import json
from copy import deepcopy
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
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
        return self.run_cli("prepare", "--base", "main", "--branch-name", "feat/test")

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
        self.env["GH_REPO_ID"] = "owner/dotfiles"
        self.env["GH_REMOTE_PATH"] = str(self.remote)
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == remote && "$2" == get-url && "$3" == origin ]]; then\n'
                            '  printf "%s\\n" "git@github.com:owner/dotfiles.git"\n'
                            '  exit 0\n'
                            'fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

    def gh_calls(self):
        log = Path(self.env["GH_LOG"])
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def open_test_pr(self, commit_mode="autoCommitAndPush"):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"},
                           commit={"mode": commit_mode})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--stay")
        return Path(self.env["GH_STATE"])

    def git_snapshot(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, env=self.env,
                              capture_output=True, text=True)
        return (self.git("branch", "--show-current"), head.stdout.strip() if head.returncode == 0 else "unborn",
                self.git("status", "--porcelain"),
                self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"),
                self.git("ls-remote", "--heads", "origin"),
                {str(path.relative_to(self.repo)): path.read_bytes()
                 for path in self.repo.rglob("*") if path.is_file() and ".git" not in path.relative_to(self.repo).parts})

    def assert_no_in_progress_git_operation(self):
        for name in ("MERGE_HEAD", "REVERT_HEAD", "CHERRY_PICK_HEAD", "REBASE_HEAD",
                     "rebase-merge", "rebase-apply", "sequencer"):
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

    def advance_remote_branch(self, branch):
        upstream = Path(tempfile.mkdtemp(dir=self.root))
        subprocess.check_call(["git", "clone", "-q", "-b", branch,
                               str(self.remote), str(upstream)], env=self.env)
        (upstream / "remote-note").write_text("remote advance\n")
        for arguments in (("add", "remote-note"), ("commit", "-qm", "Remote advance"),
                          ("push", "-q", "origin", branch)):
            subprocess.check_call(["git", "-C", str(upstream), *arguments], env=self.env)

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

    def existing_branch(self, mode="pullRequest", bases=("main",)):
        self.stub_gh()
        self.save_settings(integration={"mode": mode}, branch={"baseBranches": list(bases)})
        self.git("push", "-q", "origin", "main")
        for base in bases:
            if base != "main":
                self.git("branch", base)
                self.git("push", "-q", "origin", base)
        self.git("switch", "-c", "fix/manual")
        result = self.run_cli("prepare", "--continue")
        self.assertIn("created=false", result.stdout)
        self.assertNotIn("base=", result.stdout)
        self.change()
        self.commit()
        self.run_cli("push")

    def test_existing_current_branch_pr_delivery(self):
        self.existing_branch()
        self.run_cli("pr", "submit", "--stay")
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["base"], "main")
        self.run_cli("pr", "merge")
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_pr_submit_returns_to_actual_base_and_stay_retains_branch(self):
        self.existing_branch(bases=("main", "develop"))
        base_head = self.git("rev-parse", "develop")
        working_head = self.git("rev-parse", "HEAD")

        result = self.run_cli("pr", "submit", "--base", "develop")

        self.assertIn("current=develop", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "develop")
        self.assertEqual(self.git("rev-parse", "HEAD"), base_head)
        self.assertEqual(self.git("rev-parse", "fix/manual"), working_head)
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["state"], "OPEN")
        self.git("switch", "fix/manual")
        result = self.run_cli("pr", "submit", "--stay")
        self.assertIn("current=fix/manual", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "fix/manual")
        self.run_cli("pr", "submit")
        self.assertEqual(self.git("branch", "--show-current"), "develop")
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in self.gh_calls()), 1)

    def test_pr_submit_publishes_independently_of_manual_commit_mode(self):
        self.stub_gh()
        self.save_settings(commit={"mode": "manual"}, integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.git("switch", "-c", "feat/manual-submit")
        self.change()
        self.run_cli("commit", "--override-manual", "--message", "Manual change", "--", "tracked")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/manual-submit"), "")

        result = self.run_cli("pr", "submit")

        self.assertIn("pr submit ok", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertNotEqual(self.git("ls-remote", "--heads", "origin", "feat/manual-submit"), "")

    def test_pr_submit_failure_and_switch_failure_preserve_working_branch(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.env["GH_FAIL"] = "pr create"
        self.run_cli("pr", "submit", ok=False)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.env.pop("GH_FAIL")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == checkout && "$2" == -q && "$3" == main ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("pr", "submit", ok=False)

        self.assertIn("PR submitted", result.stderr)
        self.assertIn("could not switch to base main", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["state"], "OPEN")

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
        self.run_cli("pr", "submit", "--base", "develop", "--stay")
        self.run_cli("pr", "submit", "--stay")
        state_path = Path(self.env["GH_STATE"])
        self.assertEqual(json.loads(state_path.read_text())["base"], "develop")
        self.env["GH_EXTRA_PRS"] = json.dumps([{"url": "https://example.invalid/pr/2", "baseRefName": "main"}])
        for action in ("submit", "merge"):
            before = self.git_snapshot()
            self.assertIn("ambiguous", self.run_cli("pr", action, ok=False).stderr)
            self.assertEqual(before, self.git_snapshot())
        self.run_cli("pr", "submit", "--base=develop", "--stay")
        self.run_cli("pr", "merge", "--base", "develop")
        self.assertEqual(self.git("branch", "--show-current"), "develop")
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in self.gh_calls()), 1)

    def test_existing_pr_base_overrides_multiple_configured_defaults(self):
        self.existing_branch(bases=("main", "develop"))
        self.run_cli("pr", "submit", "--base", "develop", "--stay")
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
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "edit"])
                             for call in self.gh_calls()))
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
                         {"prepare", "bootstrap", "sync", "backup", "commit", "undo", "restore", "revert", "cherry-pick",
                          "rebase", "merge", "push", "branch", "remote", "pr"})
        for meaning in ("before editing", "after editing", "--message MESSAGE",
                        "-- PATH...", "outside commit", "current branch",
                        "local", "pull request", "--branch-name NAME",
                        "manual skips", "autoCommit stays local", "autoCommitAndPush publishes"):
            self.assertIn(meaning, help_text)
        for action in ("prepare", "bootstrap", "sync", "commit", "undo", "restore", "revert", "cherry-pick",
                       "rebase", "merge", "push"):
            self.assertIn("Usage:", self.run_cli(action, "--help").stdout)
            self.assertIn(f"[git] {action} error", self.run_cli(action, "--invalid", ok=False).stderr)
        self.assertIn("configured base branch", self.run_cli("merge", ok=False).stderr)
        pr_help = self.run_cli("pr", "--help").stdout
        self.assertEqual(set(re.findall(r"^  ([a-z]+)$", pr_help, re.MULTILINE)), {"submit", "merge"})
        branch_help = self.run_cli("branch", "--help").stdout
        self.assertEqual(set(re.findall(r"^  ([a-z]+)$", branch_help, re.MULTILINE)),
                         {"create", "switch", "rename", "delete"})
        for action in ("create", "switch", "rename", "delete"):
            self.assertIn("Usage:", self.run_cli("branch", action, "--help").stdout)
        for action in ("create", "list", "apply", "delete"):
            self.assertIn("Usage:", self.run_cli("backup", action, "--help").stdout)
        for command in ("start", "finish", "add", "stash", "pull", "checkout",
                        "switch", "reset"):
            self.assertIn("unknown command", self.run_cli(command, ok=False).stderr)
            self.assertNotIn(f"  git-workflow {command}\n", help_text)
        self.run_cli("pr", "unknown", ok=False)
        self.run_cli("branch", "unknown", ok=False)
        self.run_cli("pr", "submit", "--title", "No override", ok=False)

    def test_disabled_or_unconfigured_project_skips_workflow(self):
        path = self.repo / ".ai/project.json"
        path.unlink()

        for action, arguments in (
            ("prepare", ("--base", "main", "--branch-name", "feat/test")),
            ("bootstrap", ("--message", "Initial", "--all")),
            ("sync", ()),
            ("backup", ("create",)),
            ("backup", ("apply", "b-" + "0" * 40)),
            ("backup", ("delete", "b-" + "0" * 40)),
            ("commit", ("--message", "Change", "--all")),
            ("undo", ()),
            ("push", ()),
            ("branch", ("create", "feat/disabled")),
            ("merge", ()),
            ("pr", ("submit",)),
            ("pr", ("merge",)),
        ):
            result = self.run_cli(action, *arguments)
            self.assertEqual(
                result.stdout,
                f"[git] {action + ' ' + arguments[0] if action in ('pr', 'branch', 'backup') else action} skip reason=workflow-disabled\n",
            )

    def test_command_executables(self):
        for action in ("prepare", "bootstrap", "sync", "backup", "commit", "undo", "restore", "revert", "cherry-pick",
                       "rebase", "merge", "push", "branch", "remote", "pr"):
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
        for command in ("prepare", "bootstrap", "sync", "backup", "commit", "undo", "restore", "revert", "cherry-pick",
                        "rebase", "push", "branch", "remote", "merge", "pr"):
            self.assertTrue(os.access(destination / ".local/libexec/git-workflow" / command, os.X_OK))
        for command in ("start", "finish"):
            self.assertFalse((destination / ".local/libexec/git-workflow" / command).exists())
        for action in ("submit", "merge"):
            self.assertIn("Usage:", self.run_cli("pr", action, "--help", executable=installed).stdout)
        for action in ("create", "list", "apply", "delete"):
            self.assertIn("Usage:", self.run_cli("backup", action, "--help", executable=installed).stdout)
        self.assertTrue((destination / ".local/lib/git-workflow/pr-summary.py").is_file())
        self.assertIn("[git] prepare ok", self.run_cli("prepare", "--base", "main", "--branch-name", "feat/test", executable=installed).stdout)
        for action in ("connect", "reconnect", "disconnect"):
            self.assertIn("Usage:", self.run_cli("remote", action, "--help", executable=installed).stdout)
        self.run_cli("remote", "disconnect", executable=installed)
        self.run_cli("remote", "connect", str(self.remote), executable=installed)
        self.run_cli("remote", "reconnect", str(self.remote), executable=installed)
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

    def test_bootstrap_commits_and_publishes_base_before_development(self):
        self.unborn_repo()
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

    def test_bootstrap_manual_mode_and_repeated_attempt(self):
        self.unborn_repo()
        self.settings(commit={"mode": "manual"})
        (self.repo / "README.md").write_text("initial\n")
        self.git("add", ".ai/project.json")
        result = self.run_cli("bootstrap", "--message", "Initial", "--all")
        self.assertIn("bootstrap ok", result.stdout)
        self.assertTrue((self.repo / ".ai/project.json").exists())
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "HEAD"), "README.md")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), ".ai/project.json")
        self.assertNotEqual(self.git("ls-remote", "--heads", "origin", "main"), "")
        self.assertIn("already contains commits", self.run_cli("bootstrap", "--message", "Again", "--all", ok=False).stderr)

    def test_bootstrap_validates_empty_selection_base_and_remote(self):
        self.unborn_repo(head="develop")
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

    def test_bootstrap_amend_all_excludes_local_configuration(self):
        self.bootstrapped_root()
        self.git("add", ".ai/project.json")
        (self.repo / "README.md").write_text("corrected\n")
        self.run_cli("bootstrap", "--amend", "--message", "Initial", "--all")
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "HEAD"), "README.md")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), ".ai/project.json")

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

    def assert_prepare_explicit_base_starts_from_latest_base(self, sync_mode):
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
        self.assertEqual(self.git("branch", "--show-current"), "feat/next")
        self.assertEqual(self.git("rev-parse", "HEAD"), remote_tip)
        self.assertEqual(self.git("rev-parse", "main"), remote_tip)
        self.assertEqual(self.git("rev-parse", "feat/test"), old_branch_head)
        self.assertFalse((self.repo / "user-note").exists())
        self.assertTrue(self.git("stash", "list"))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_prepare_explicit_base_starts_from_latest_base_when_sync_none(self):
        self.assert_prepare_explicit_base_starts_from_latest_base("none")

    def test_prepare_explicit_base_starts_from_latest_base_when_sync_fetch(self):
        self.assert_prepare_explicit_base_starts_from_latest_base("fetch")

    def test_prepare_explicit_base_starts_from_latest_base_when_sync_update(self):
        self.assert_prepare_explicit_base_starts_from_latest_base("update")

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
        self.save_settings(sync={"mode": "none"})
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
        self.save_settings(sync={"mode": "none"})
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

    def merge_local(self, method, cleanup):
        self.save_settings(integration={"mergeMethod": method}, branch={"deleteAfterIntegration": cleanup})
        self.git("push", "-q", "origin", "main")
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
                self.run_cli("prepare", "--base", "main", "--branch-name", branch_name)
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
        self.git("push", "-q", "origin", "main")
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
            self.run_cli("prepare", "--continue")
            (self.repo / "tracked").write_text(subject)
            self.run_cli("commit", "--message", subject, "--", "tracked")
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "edit"])
                             for call in self.gh_calls()))
        self.assertIn("url=https://github.com/owner/dotfiles/pull/1",
                      self.run_cli("pr", "submit", "--stay").stdout)
        state_path = Path(self.env["GH_STATE"])
        state = json.loads(state_path.read_text())
        self.assertEqual(state["title"], "feat/test")
        self.assertEqual(state["body"], "<!-- git-workflow:summary:start -->\n"
                         "## Summary\n\n- Commit A\n- Commit B\n- Commit C\n"
                         "<!-- git-workflow:summary:end -->")
        self.run_cli("pr", "submit", "--stay")
        self.change("other")
        self.run_cli("commit", "--message", "Commit D", "--", "other")
        state = json.loads(state_path.read_text())
        self.assertIn("- Commit D", state["body"])
        self.run_cli("pr", "submit", "--stay")
        state = json.loads(state_path.read_text())
        self.assertEqual(state["body"], "<!-- git-workflow:summary:start -->\n"
                         "## Summary\n\n- Commit A\n- Commit B\n- Commit C\n- Commit D\n"
                         "<!-- git-workflow:summary:end -->")
        calls = self.gh_calls()
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in calls), 1)
        self.assertEqual(sum(call[:2] == ["pr", "edit"] for call in calls), 1)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in calls))
        for call in calls:
            if call[:2] == ["pr", "list"]:
                self.assertEqual(call[2:6], ["--state", "open", "--head", "feat/test"])
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_auto_push_updates_complete_summary_and_preserves_manual_content(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["title"] = "Human title"
        state["body"] += "\n\n## Notes\n\nKeep this note.\n"
        state_path.write_text(json.dumps(state))
        for subject in ("Second", "Third"):
            (self.repo / subject).write_text(subject + "\n")
            result = self.run_cli("commit", "--message", subject, "--", subject)
            self.assertIn("pushed=true pr=updated", result.stdout)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["title"], "Human title")
        self.assertIn("- Change\n- Second\n- Third", state["body"])
        self.assertTrue(state["body"].endswith("\n\n## Notes\n\nKeep this note.\n"))
        self.assertEqual(state["body"].count("git-workflow:summary:start"), 1)
        edit_count = sum(call[:2] == ["pr", "edit"] for call in self.gh_calls())

        result = self.run_cli("push")

        self.assertIn("pr=unchanged", result.stdout)
        self.assertEqual(sum(call[:2] == ["pr", "edit"] for call in self.gh_calls()), edit_count)

    def test_explicit_push_updates_summary_after_local_commit(self):
        state_path = self.open_test_pr("autoCommit")
        (self.repo / "second").write_text("second\n")
        result = self.run_cli("commit", "--message", "Second", "--", "second")
        self.assertIn("pushed=false", result.stdout)
        self.assertNotIn("- Second", json.loads(state_path.read_text())["body"])

        pushed = self.run_cli("push")

        self.assertIn("pr=updated", pushed.stdout)
        self.assertIn("- Change\n- Second", json.loads(state_path.read_text())["body"])

    def test_explicit_rewrite_push_updates_summary_after_local_amend(self):
        state_path = self.open_test_pr("autoCommit")
        state = json.loads(state_path.read_text())
        state["body"] += "\n\n## Notes\n\nKeep this note.\n"
        state_path.write_text(json.dumps(state))
        old_remote = self.git("rev-parse", "origin/feat/test")
        (self.repo / "tracked").write_text("amended\n")

        amended = self.run_cli("commit", "--amend", "--message", "Revised", "--all")
        self.assertIn("pushed=false", amended.stdout)
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), old_remote)
        self.assertIn("- Change", json.loads(state_path.read_text())["body"])

        pushed = self.run_cli("push", "--rewrite")
        body = json.loads(state_path.read_text())["body"]
        self.assertIn("pr=updated", pushed.stdout)
        self.assertIn("- Revised", body)
        self.assertNotIn("- Change", body)
        self.assertTrue(body.endswith("\n\n## Notes\n\nKeep this note.\n"))
        self.assertEqual(self.git("rev-parse", "origin/feat/test"),
                         self.git("rev-parse", "HEAD"))

    def test_undo_published_commit_updates_pr_summary_and_preserves_manual_content(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["title"] = "Human title"
        state["body"] += "\n\n## Notes\n\nKeep this note.\n"
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")
        self.run_cli("commit", "--message", "Second", "--", "second")
        self.assertIn("- Second", json.loads(state_path.read_text())["body"])
        previous_calls = len(self.gh_calls())

        result = self.run_cli("undo")

        state = json.loads(state_path.read_text())
        self.assertIn("remote=updated pr=updated", result.stdout)
        self.assertEqual(state["title"], "Human title")
        self.assertIn("- Change", state["body"])
        self.assertNotIn("- Second", state["body"])
        self.assertTrue(state["body"].endswith("\n\n## Notes\n\nKeep this note.\n"))
        self.assertEqual(self.git("status", "--porcelain"), "?? second")
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "close"], ["pr", "merge"])
                             for call in self.gh_calls()[previous_calls:]))

    def test_undo_auto_commit_rewrite_push_updates_pr_summary(self):
        state_path = self.open_test_pr("autoCommit")
        (self.repo / "second").write_text("second\n")
        self.run_cli("commit", "--message", "Second", "--", "second")
        self.run_cli("push")
        self.assertIn("- Second", json.loads(state_path.read_text())["body"])

        self.assertIn("remote=pending", self.run_cli("undo").stdout)
        result = self.run_cli("push", "--rewrite")

        self.assertIn("pr=updated", result.stdout)
        body = json.loads(state_path.read_text())["body"]
        self.assertIn("- Change", body)
        self.assertNotIn("- Second", body)

    def test_undo_only_pr_commit_leaves_open_pr_with_empty_summary(self):
        state_path = self.open_test_pr()
        previous_calls = len(self.gh_calls())

        result = self.run_cli("undo")

        state = json.loads(state_path.read_text())
        self.assertIn("remote=updated pr=updated", result.stdout)
        self.assertIn("## Summary\n\nNo commits relative to base.", state["body"])
        self.assertNotIn("- Change", state["body"])
        self.assertEqual(state["state"], "OPEN")
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "close"], ["pr", "merge"])
                             for call in self.gh_calls()[previous_calls:]))

    def test_undo_pr_metadata_failure_preserves_successful_publication(self):
        state_path = self.open_test_pr()
        original_body = json.loads(state_path.read_text())["body"]
        self.env["GH_FAIL"] = "pr edit"
        removed = self.git("rev-parse", "HEAD")
        parent = self.git("rev-parse", "HEAD^")

        result = self.run_cli("undo")

        self.assertIn("remote=updated pr=failed", result.stdout)
        self.assertIn("pr sync warning", result.stderr)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], parent)
        self.assertEqual(json.loads(state_path.read_text())["body"], original_body)
        self.assertNotEqual(removed, parent)

    def test_undo_in_local_merge_mode_never_calls_gh(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "localMerge"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()

        result = self.run_cli("undo")

        self.assertIn("remote=updated pr=none", result.stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_local_merge_explicit_push_works_without_gh(self):
        self.save_settings(integration={"mode": "localMerge"},
                           commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        gh_log = self.root / "unexpected-gh.log"
        gh_stub = self.bin / "gh"
        gh_stub.write_text('#!/usr/bin/env bash\n'
                           'printf "%s\\n" "$*" >> "$GH_UNEXPECTED_LOG"\n'
                           'exit 1\n')
        gh_stub.chmod(0o755)
        self.env["GH_UNEXPECTED_LOG"] = str(gh_log)
        self.change()

        self.assertIn("pushed=false", self.commit().stdout)
        result = self.run_cli("push")

        self.assertIn("push ok branch=feat/test pr=none", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])
        self.assertFalse(gh_log.exists())

    def test_local_merge_auto_push_and_rewrites_never_call_gh(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "localMerge"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.assertIn("pushed=true pr=none", self.commit().stdout)
        (self.repo / "tracked").write_text("amended\n")
        self.assertIn("pushed=true pr=none",
                      self.run_cli("commit", "--amend", "--all").stdout)
        self.advance_remote_branch("main")
        self.assertIn("pushed=true pr=none", self.run_cli("rebase").stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_metadata_identity_failure_preserves_pushed_commit(self):
        state_path = self.open_test_pr()
        original_body = json.loads(state_path.read_text())["body"]
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == remote && "$2" == get-url && "$3" == origin ]]; then\n'
                            '  printf "%s\\n" "file:///unverifiable/repo.git"\n'
                            '  exit 0\n'
                            'fi\n'
                            f'exec "{git_executable}" "$@"\n')
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertIn("cannot verify GitHub remote repository", result.stderr)
        self.assertEqual(json.loads(state_path.read_text())["body"], original_body)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])
        self.assertFalse(any(call[:2] == ["pr", "edit"] for call in self.gh_calls()))

    def test_missing_pr_identity_preserves_pushed_commit(self):
        state_path = self.open_test_pr()
        original_body = json.loads(state_path.read_text())["body"]
        self.env["GH_REPO_ID"] = ""
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertIn("repository identity is unavailable", result.stderr)
        self.assertEqual(json.loads(state_path.read_text())["body"], original_body)
        self.assertFalse(any(call[:2] == ["pr", "edit"] for call in self.gh_calls()))
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])

    def test_mismatched_pr_identity_does_not_edit(self):
        state_path = self.open_test_pr()
        original_body = json.loads(state_path.read_text())["body"]
        self.env["GH_REPO_ID"] = "other/dotfiles"
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=none", result.stdout)
        self.assertEqual(json.loads(state_path.read_text())["body"], original_body)
        self.assertFalse(any(call[:2] == ["pr", "edit"] for call in self.gh_calls()))
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])

    def test_summary_uses_open_pr_actual_base(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"},
                           branch={"baseBranches": ["main", "develop"]})
        self.git("push", "-q", "origin", "main")
        self.git("switch", "-c", "develop")
        (self.repo / "develop-only").write_text("base content\n")
        self.git("add", "develop-only")
        self.git("commit", "-qm", "Develop base")
        self.git("push", "-qu", "origin", "develop")
        self.run_cli("prepare", "--base", "develop", "--branch-name", "feat/from-develop")
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--base", "develop", "--stay")
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pr=updated", result.stdout)
        body = json.loads(Path(self.env["GH_STATE"]).read_text())["body"]
        self.assertIn("- Change\n- Second", body)
        self.assertNotIn("Develop base", body)

    def test_amend_replaces_old_subject_in_pr_summary(self):
        state_path = self.open_test_pr()
        (self.repo / "tracked").write_text("amended\n")

        result = self.run_cli("commit", "--amend", "--message", "Amended", "--all")

        self.assertIn("pushed=true pr=updated", result.stdout)
        body = json.loads(state_path.read_text())["body"]
        self.assertIn("- Amended", body)
        self.assertNotIn("- Change", body)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])

    def test_legacy_summary_migrates_without_losing_other_sections(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["title"] = "Human title"
        state["body"] = "## Summary\n\n- Change\n\n## Notes\n\nKeep this note."
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pr=updated", result.stdout)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["title"], "Human title")
        self.assertEqual(state["body"].count("## Summary"), 1)
        self.assertIn("- Change\n- Second", state["body"])
        self.assertTrue(state["body"].endswith("\n\n## Notes\n\nKeep this note."))

    def test_handwritten_description_without_summary_is_preserved(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["body"] = "Human context.\n\n## Checks\n\nKeep this section."
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pr=updated", result.stdout)
        body = json.loads(state_path.read_text())["body"]
        self.assertIn("- Change\n- Second", body)
        self.assertTrue(body.endswith("Human context.\n\n## Checks\n\nKeep this section."))

    def test_pr_selection_ignores_same_named_fork_branch(self):
        prs_path = self.root / "prs.json"
        body_path = self.root / "body.txt"
        helper = COMMON / "lib/git-workflow/pr-summary.py"
        fork = {"url": "https://github.com/owner/dotfiles/pull/2", "baseRefName": "main",
                "body": "fork", "headRepository": {"nameWithOwner": "other/dotfiles"}}
        own = {"url": "https://github.com/owner/dotfiles/pull/1", "baseRefName": "develop",
               "body": "own", "headRepository": {"nameWithOwner": "owner/dotfiles"}}
        prs_path.write_text(json.dumps([fork, own]))

        selected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                   str(body_path), "git@github.com:owner/dotfiles.git"],
                                  capture_output=True, text=True)

        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(selected.stdout.strip(),
                         "https://github.com/owner/dotfiles/pull/1\tdevelop")
        self.assertEqual(body_path.read_text(), "own")
        prs_path.write_text(json.dumps([fork]))
        no_match = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                   str(body_path), "git@github.com:owner/dotfiles.git"],
                                  capture_output=True, text=True)
        self.assertEqual(no_match.returncode, 3)

    def test_pr_selection_requires_verifiable_repository_identity(self):
        prs_path = self.root / "prs.json"
        body_path = self.root / "body.txt"
        helper = COMMON / "lib/git-workflow/pr-summary.py"
        own = {"url": "https://github.com/owner/dotfiles/pull/1", "baseRefName": "main",
               "body": "own", "headRepository": {"nameWithOwner": "owner/dotfiles"}}
        prs_path.write_text(json.dumps([own]))

        for remote_url in ("https://github.com/owner/dotfiles.git",
                           "git@github.com:owner/dotfiles.git",
                           "ssh://git@github.com/owner/dotfiles.git"):
            with self.subTest(remote_url=remote_url):
                selected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                           str(body_path), remote_url],
                                          capture_output=True, text=True)
                self.assertEqual(selected.returncode, 0, selected.stderr)
                self.assertEqual(body_path.read_text(), "own")

        body_path.write_text("unchanged")
        for remote_url in (str(self.remote), "https://github.com/owner/dotfiles/extra",
                           "https://github.com/owner/dotfiles.git?ref=main"):
            with self.subTest(remote_url=remote_url):
                rejected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                           str(body_path), remote_url],
                                          capture_output=True, text=True)
                self.assertEqual(rejected.returncode, 1)
                self.assertIn("cannot verify GitHub remote repository", rejected.stderr)
                self.assertEqual(body_path.read_text(), "unchanged")

        for repository in (None, {}, {"nameWithOwner": "other/dotfiles"}):
            with self.subTest(repository=repository):
                prs_path.write_text(json.dumps([{**own, "headRepository": repository}]))
                rejected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                           str(body_path), "git@github.com:owner/dotfiles.git"],
                                          capture_output=True, text=True)
                expected_code = (3 if isinstance(repository, dict) and
                                 repository.get("nameWithOwner") == "other/dotfiles" else 1)
                self.assertEqual(rejected.returncode, expected_code)
                self.assertEqual(body_path.read_text(), "unchanged")
        prs_path.write_text(json.dumps([{**own, "url": "https://other.example/pull/1"}]))
        rejected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                   str(body_path), "git@github.com:owner/dotfiles.git"],
                                  capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("host does not match", rejected.stderr)

    def test_unmanaged_summary_does_not_block_push(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["body"] = "## Summary\n\nHuman summary."
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertEqual(json.loads(state_path.read_text())["body"], state["body"])
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])

    def test_ambiguous_pr_association_does_not_block_push(self):
        state_path = self.open_test_pr()
        original = json.loads(state_path.read_text())["body"]
        self.env["GH_EXTRA_PRS"] = json.dumps([{
            "url": "https://github.com/owner/dotfiles/pull/2", "baseRefName": "main",
            "body": "## Summary\n\n- Other",
            "headRepository": {"nameWithOwner": "owner/dotfiles"}}])
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertIn("ambiguous open PRs", result.stderr)
        self.assertEqual(json.loads(state_path.read_text())["body"], original)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])

    def test_pr_edit_failure_keeps_successful_push_and_can_retry(self):
        state_path = self.open_test_pr()
        old_body = json.loads(state_path.read_text())["body"]
        self.env["GH_FAIL"] = "pr edit"
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertIn("pr sync warning", result.stderr)
        self.assertEqual(json.loads(state_path.read_text())["body"], old_body)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])
        self.env.pop("GH_FAIL")
        self.assertIn("pr=updated", self.run_cli("push").stdout)
        self.assertIn("- Second", json.loads(state_path.read_text())["body"])

    def test_push_without_open_pr_does_not_create_one(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.assertIn("pr=none", self.commit().stdout)
        self.assertEqual(self.gh_calls(), [])
        (self.repo / "second").write_text("second\n")
        result = self.run_cli("commit", "--message", "Second", "--", "second")
        self.assertIn("pr=none", result.stdout)
        self.assertNotIn("error status", result.stderr)
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "edit"])
                             for call in self.gh_calls()))
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text()), {})

    def test_explicit_base_branch_push_skips_pr_lookup(self):
        self.stub_gh()
        self.git("push", "-q", "origin", "main")

        result = self.run_cli("push")

        self.assertIn("branch=main pr=none", result.stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_push_does_not_edit_closed_or_merged_pr(self):
        for pr_state in ("CLOSED", "MERGED"):
            with self.subTest(pr_state=pr_state):
                state_path = self.open_test_pr()
                state = json.loads(state_path.read_text())
                state["state"] = pr_state
                state_path.write_text(json.dumps(state))
                (self.repo / "second").write_text("second\n")

                result = self.run_cli("commit", "--message", "Second", "--", "second")

                self.assertIn("pushed=true pr=none", result.stdout)
                self.assertEqual(json.loads(state_path.read_text())["body"], state["body"])
                self.git("switch", "main")
                self.git("branch", "-D", "feat/test")
                self.git("push", "-q", "origin", "--delete", "feat/test")
                state_path.unlink()

    def test_resubmit_preserves_manual_title_and_other_sections(self):
        state_path = self.open_test_pr("autoCommit")
        state = json.loads(state_path.read_text())
        state["title"] = "Human title"
        state["body"] += "\n\n## Notes\n\nKeep."
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")
        self.run_cli("commit", "--message", "Second", "--", "second")

        result = self.run_cli("pr", "submit", "--stay")

        self.assertIn("pr submit ok", result.stdout)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["title"], "Human title")
        self.assertIn("- Change\n- Second", state["body"])
        self.assertTrue(state["body"].endswith("\n\n## Notes\n\nKeep."))

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
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Change")

    def test_squash_default_multiple_commit_summary(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.git("push", "-q", "origin", "main")
        self.run_cli("prepare", "--base", "main", "--branch-name", "feat/ai-harness-preflight")
        self.change()
        self.commit()
        self.change("other")
        self.commit("other")
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "feat(ai): harness preflight")

    def test_squash_default_unconventional_branch(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.git("switch", "-c", "work-in-progress")
        self.assertIn("created=false", self.run_cli("prepare", "--continue").stdout)
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
        self.run_cli("prepare", "--base", "main", "--branch-name", "feat/ai-harness-preflight")
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--stay")
        state_path = Path(self.env["GH_STATE"])
        self.assertEqual(json.loads(state_path.read_text())["title"], "Change")
        self.change("other")
        self.commit("other")
        self.run_cli("pr", "submit", "--stay")
        self.assertEqual(json.loads(state_path.read_text())["title"], "Change")

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
        self.run_cli("pr", "submit", "--stay")
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
        self.run_cli("pr", "submit", "--stay")
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
        self.run_cli("pr", "submit", "--stay")
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
        self.run_cli("pr", "submit", "--stay")
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
        self.run_cli("pr", "submit", "--stay")
        for queue_value in ("true", "null"):
            self.env["GH_MERGE_QUEUE"] = queue_value
            self.run_cli("pr", "merge", ok=False)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.gh_calls()))


if __name__ == "__main__":
    unittest.main()
