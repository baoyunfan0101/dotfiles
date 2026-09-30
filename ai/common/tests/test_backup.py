import json
from copy import deepcopy
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest

from sandbox import isolated_environment


COMMON = Path(__file__).resolve().parents[1]
CLI = COMMON / "bin/git-workflow"
DEFAULT_SETTINGS = runpy.run_path(str(COMMON / "bin/agent-project"))["DEFAULT_SETTINGS"]


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.remote = self.root / "remote.git"
        self.env = isolated_environment(self.root)
        self.git("init", "-q", "-b", "main")
        self.git("init", "-q", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        (self.repo / "tracked").write_text("original\n")
        self.config = deepcopy(DEFAULT_SETTINGS)
        self.config["workflow"]["enabled"] = True
        self.config["git"]["sync"]["mode"] = "none"
        self.save_config()
        self.git("add", ".")
        self.git("commit", "-qm", "Initial")
        self.git("push", "-qu", "origin", "main")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stderr=subprocess.PIPE,
        ).strip()

    def run_cli(self, *args, ok=True):
        result = subprocess.run([str(CLI), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def save_config(self, commit=False):
        path = self.repo / ".ai/project.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(self.config))
        if commit:
            self.git("add", ".ai/project.json")
            if subprocess.run(["git", "diff", "--cached", "--quiet"],
                              cwd=self.repo, env=self.env).returncode != 0:
                self.git("commit", "-qm", "Configure")
                self.git("push", "-q", "origin", "main")

    def set_backup(self, method, delete_after_restore=False):
        self.config["git"]["backup"].update(
            method=method, deleteAfterRestore=delete_after_restore)
        self.save_config(commit=True)

    def selected_changes(self):
        (self.repo / "tracked").write_text("staged\n")
        self.git("add", "tracked")
        (self.repo / "tracked").write_text("unstaged\n")
        (self.repo / "new-file").write_text("untracked\n")

    def clean_changes(self):
        self.git("restore", "--staged", "--worktree", "tracked")
        (self.repo / "new-file").unlink(missing_ok=True)

    def create(self):
        result = self.run_cli("backup", "create")
        match = re.search(r"id=(b-[0-9a-f]{40,64})", result.stdout)
        self.assertIsNotNone(match, result.stdout)
        return match.group(1)

    def listed_ids(self):
        output = self.run_cli("backup", "list").stdout
        return re.findall(r"id=(b-[0-9a-f]{40,64})", output)

    def test_manual_lifecycle_preserves_index_and_untracked_files(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                self.selected_changes()
                original_status = self.git("status", "--porcelain")
                backup_id = self.create()
                self.assertIn(backup_id, self.listed_ids())
                self.assertEqual(self.git("status", "--porcelain"), original_status)
                self.clean_changes()
                self.run_cli("backup", "restore", backup_id)
                self.assertEqual(self.git("status", "--porcelain"), original_status)
                self.assertEqual((self.repo / "tracked").read_text(), "unstaged\n")
                self.assertEqual((self.repo / "new-file").read_text(), "untracked\n")
                self.assertIn(backup_id, self.listed_ids())
                self.run_cli("backup", "delete", backup_id)
                self.assertNotIn(backup_id, self.listed_ids())
                self.assertEqual(self.git("status", "--porcelain"), original_status)
                self.clean_changes()

    def test_stable_identity_and_unrelated_stash_isolation(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                (self.repo / "tracked").write_text("backup A\n")
                backup_a = self.create()
                self.git("restore", "--worktree", "tracked")
                (self.repo / "tracked").write_text("unrelated\n")
                self.git("stash", "push", "-m", "user stash")
                unrelated = self.git("rev-parse", "refs/stash")
                (self.repo / "tracked").write_text("backup B\n")
                backup_b = self.create()
                self.git("update-ref", "refs/agent-workflow/backups/unrelated", "HEAD")
                self.git("restore", "--worktree", "tracked")
                self.assertEqual(set(self.listed_ids()), {backup_a, backup_b})
                self.run_cli("backup", "restore", backup_a)
                self.assertEqual((self.repo / "tracked").read_text(), "backup A\n")
                self.assertNotEqual((self.repo / "tracked").read_text(), "backup B\n")
                self.run_cli("backup", "delete", backup_a)
                self.assertEqual(self.listed_ids(), [backup_b])
                self.assertIn(unrelated, self.git("stash", "list", "--format=%H"))
                self.run_cli("backup", "delete", backup_b)
                self.assertEqual(self.git("stash", "list", "--format=%H"), unrelated)
                self.git("restore", "--worktree", "tracked")
                self.git("stash", "drop", "stash@{0}")

    def test_identical_manual_backups_have_distinct_ids(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                (self.repo / "tracked").write_text("same change\n")
                first = self.create()
                second = self.create()
                self.assertNotEqual(first, second)
                self.assertEqual(set(self.listed_ids()), {first, second})
                self.run_cli("backup", "delete", first)
                self.run_cli("backup", "delete", second)
                self.git("restore", "--worktree", "tracked")

    def test_prepare_cleanup_and_manual_isolation(self):
        for method in ("stash", "commit"):
            for cleanup in (False, True):
                with self.subTest(method=method, cleanup=cleanup):
                    self.set_backup(method, cleanup)
                    (self.repo / "tracked").write_text("manual\n")
                    manual_id = self.create()
                    self.git("restore", "--worktree", "tracked")
                    (self.repo / "tracked").write_text("prepare\n")
                    self.git("add", "tracked")
                    (self.repo / "new-file").write_text("new\n")
                    original_status = self.git("status", "--porcelain")
                    branch = f"feat/{method}-{int(cleanup)}"
                    self.run_cli("prepare", "--base", "main", "--branch-name", branch)
                    self.assertEqual(self.git("status", "--porcelain"), original_status)
                    ids = self.listed_ids()
                    self.assertIn(manual_id, ids)
                    self.assertEqual(len(ids), 1 if cleanup else 2)
                    if method == "commit":
                        self.assertEqual(self.git("stash", "list"), "")
                    self.clean_changes()
                    self.git("switch", "main")
                    self.run_cli("backup", "delete", manual_id)
                    for backup_id in self.listed_ids():
                        self.run_cli("backup", "delete", backup_id)

    def test_prepare_failure_retains_backup(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method, True)
                (self.repo / "tracked").write_text("protected\n")
                self.git("remote", "remove", "origin")
                result = self.run_cli("prepare", "--base", "main",
                                      "--branch-name", f"feat/fail-{method}", ok=False)
                self.assertIn("base remote resolution failed", result.stderr)
                self.assertEqual((self.repo / "tracked").read_text(), "protected\n")
                self.assertEqual(len(self.listed_ids()), 1)
                self.git("restore", "--worktree", "tracked")
                self.run_cli("backup", "delete", self.listed_ids()[0])
                self.git("remote", "add", "origin", str(self.remote))

    def test_invalid_ids_and_conflicting_restore_keep_backup(self):
        self.selected_changes()
        backup_id = self.create()
        self.clean_changes()
        for invalid in ("stash@{0}", "b-nope", "b-" + "0" * 40):
            self.run_cli("backup", "delete", invalid, ok=False)
            self.run_cli("backup", "restore", invalid, ok=False)
        (self.repo / "tracked").write_text("conflict\n")
        result = self.run_cli("backup", "restore", backup_id, ok=False)
        self.assertIn("backup retained", result.stderr)
        self.assertIn(backup_id, self.listed_ids())

    def test_delete_does_not_change_other_repository_state(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                (self.repo / "tracked").write_text("A\n")
                backup_a = self.create()
                self.git("restore", "--worktree", "tracked")
                (self.repo / "tracked").write_text("B\n")
                backup_b = self.create()
                self.git("update-ref", "refs/notes/unrelated", "HEAD")
                self.selected_changes()
                before = (self.git("status", "--porcelain"),
                          self.git("ls-files", "--stage"),
                          self.git("rev-parse", "HEAD"),
                          self.git("for-each-ref", "--format=%(refname) %(objectname)",
                                   "refs/heads", "refs/notes"),
                          (self.repo / "tracked").read_bytes(),
                          (self.repo / "new-file").read_bytes())
                self.run_cli("backup", "delete", backup_a)
                after = (self.git("status", "--porcelain"),
                         self.git("ls-files", "--stage"),
                         self.git("rev-parse", "HEAD"),
                         self.git("for-each-ref", "--format=%(refname) %(objectname)",
                                  "refs/heads", "refs/notes"),
                         (self.repo / "tracked").read_bytes(),
                         (self.repo / "new-file").read_bytes())
                self.assertEqual(before, after)
                self.assertEqual(self.listed_ids(), [backup_b])
                self.run_cli("backup", "delete", backup_b)
                self.clean_changes()

    def test_manual_selection_modes(self):
        for mode in ("none", "tracked", "untracked"):
            with self.subTest(mode=mode):
                self.config["git"]["backup"]["mode"] = mode
                self.save_config(commit=True)
                (self.repo / "tracked").write_text("tracked change\n")
                (self.repo / "new-file").write_text("untracked change\n")
                result = self.run_cli("backup", "create")
                if mode == "none":
                    self.assertIn("skip reason=no-selected-changes", result.stdout)
                    self.assertEqual(self.listed_ids(), [])
                else:
                    backup_id = re.search(r"id=(b-[0-9a-f]{40,64})", result.stdout).group(1)
                    self.clean_changes()
                    self.run_cli("backup", "restore", backup_id)
                    self.assertEqual((self.repo / "tracked").read_text(),
                                     "tracked change\n" if mode == "tracked" else "original\n")
                    self.assertEqual((self.repo / "new-file").exists(), mode == "untracked")
                    self.run_cli("backup", "delete", backup_id)
                self.clean_changes()

    def test_legacy_backups_are_managed(self):
        (self.repo / "tracked").write_text("legacy commit\n")
        self.git("add", "tracked")
        tree = self.git("write-tree")
        head = self.git("rev-parse", "HEAD")
        legacy_commit = self.git("commit-tree", tree, "-p", head,
                                 "-m", "agent-workflow backup legacy")
        self.git("update-ref", "refs/agent-workflow/backups/legacy", legacy_commit)
        self.git("restore", "--staged", "--worktree", "tracked")
        commit_id = "b-" + legacy_commit
        self.assertIn(commit_id, self.listed_ids())
        self.run_cli("backup", "restore", commit_id)
        self.assertIn("M  tracked", self.git("status", "--porcelain"))
        self.git("restore", "--staged", "--worktree", "tracked")
        self.run_cli("backup", "delete", commit_id)

        (self.repo / "tracked").write_text("legacy stash\n")
        self.git("stash", "push", "-m", "agent-workflow backup legacy")
        stash_id = "b-" + self.git("rev-parse", "refs/stash")
        self.assertIn(stash_id, self.listed_ids())
        self.run_cli("backup", "restore", stash_id)
        self.assertEqual((self.repo / "tracked").read_text(), "legacy stash\n")
        self.run_cli("backup", "delete", stash_id)

    def test_prepare_restore_failure_retains_backup_with_cleanup_enabled(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method, True)
                (self.repo / "tracked").write_text("protected\n")
                stub_dir = self.root / "stub-bin"
                stub_dir.mkdir(exist_ok=True)
                real_git = shutil.which("git")
                stub = stub_dir / "git"
                stub.write_text('#!/usr/bin/env bash\n'
                                'if [[ "$1" == stash && "$2" == apply ]]; then exit 1; fi\n'
                                f'exec "{real_git}" "$@"\n')
                stub.chmod(0o755)
                self.env["PATH"] = f"{stub_dir}:{self.env['PATH']}"
                result = self.run_cli("prepare", "--base", "main",
                                      "--branch-name", f"feat/fail-restore-{method}", ok=False)
                self.assertIn("backup retained", result.stderr)
                self.assertEqual(len(self.listed_ids()), 1)
                self.env["PATH"] = self.env["PATH"].split(":", 1)[1]
                self.run_cli("backup", "restore", self.listed_ids()[0])
                self.assertEqual((self.repo / "tracked").read_text(), "protected\n")
                self.git("restore", "--worktree", "tracked")
                self.run_cli("backup", "delete", self.listed_ids()[0])

    def test_cleanup_failure_reports_error_and_retains_backup(self):
        self.set_backup("stash", True)
        (self.repo / "tracked").write_text("protected\n")
        stub_dir = self.root / "stub-bin"
        stub_dir.mkdir()
        real_git = shutil.which("git")
        stub = stub_dir / "git"
        stub.write_text('#!/usr/bin/env bash\n'
                        'if [[ "$1" == stash && "$2" == drop ]]; then exit 1; fi\n'
                        f'exec "{real_git}" "$@"\n')
        stub.chmod(0o755)
        self.env["PATH"] = f"{stub_dir}:{self.env['PATH']}"
        result = self.run_cli("prepare", "--base", "main",
                              "--branch-name", "feat/cleanup-fails", ok=False)
        self.assertIn("backup deletion failed", result.stderr)
        self.assertEqual((self.repo / "tracked").read_text(), "protected\n")
        self.assertEqual(len(self.listed_ids()), 1)

    def test_commit_transport_creation_failure_retains_snapshot(self):
        self.set_backup("commit")
        (self.repo / "tracked").write_text("protected\n")
        stub_dir = self.root / "stub-bin"
        stub_dir.mkdir()
        real_git = shutil.which("git")
        stub = stub_dir / "git"
        stub.write_text('#!/usr/bin/env bash\n'
                        'if [[ "$1" == stash && "$2" == push ]]; then exit 1; fi\n'
                        f'exec "{real_git}" "$@"\n')
        stub.chmod(0o755)
        self.env["PATH"] = f"{stub_dir}:{self.env['PATH']}"
        self.run_cli("backup", "create", ok=False)
        self.assertEqual((self.repo / "tracked").read_text(), "protected\n")
        self.assertEqual(len(self.listed_ids()), 1)
        self.env["PATH"] = self.env["PATH"].split(":", 1)[1]
        self.git("restore", "--worktree", "tracked")
        self.run_cli("backup", "restore", self.listed_ids()[0])
        self.assertEqual((self.repo / "tracked").read_text(), "protected\n")

    def test_disabled_workflow_skips_mutations(self):
        self.config["workflow"]["enabled"] = False
        self.save_config()
        (self.repo / "tracked").write_text("changed\n")
        before = self.git("status", "--porcelain")
        for action, args in (("create", ()), ("restore", ("b-" + "0" * 40,)),
                             ("delete", ("b-" + "0" * 40,))):
            result = self.run_cli("backup", action, *args)
            self.assertIn("skip reason=workflow-disabled", result.stdout)
            self.assertEqual(self.git("status", "--porcelain"), before)
        self.assertEqual(self.git("stash", "list"), "")

    def test_help_and_no_selected_changes(self):
        for args in (("backup", "--help"), ("backup", "create", "--help"),
                     ("backup", "list", "--help"), ("backup", "restore", "--help"),
                     ("backup", "delete", "--help")):
            self.assertIn("Usage:", self.run_cli(*args).stdout)
        self.assertIn("skip reason=no-selected-changes",
                      self.run_cli("backup", "create").stdout)
