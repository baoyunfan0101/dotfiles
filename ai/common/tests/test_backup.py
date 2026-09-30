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

    def set_backup(self, method, mode="all"):
        self.config["git"]["backup"].update(
            method=method, mode=mode)
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

    def stub_git(self, body):
        stub_dir = self.root / "stub-bin"
        stub_dir.mkdir(exist_ok=True)
        real_git = shutil.which("git")
        stub = stub_dir / "git"
        stub.write_text('#!/usr/bin/env bash\n' + body + '\n'
                        f'exec "{real_git}" "$@"\n')
        stub.chmod(0o755)
        self.env["PATH"] = f"{stub_dir}:{self.env['PATH']}"
        return stub

    def local_snapshot(self):
        return (self.git("rev-parse", "HEAD"), self.git("ls-files", "--stage"),
                self.git("status", "--porcelain"),
                {str(p.relative_to(self.repo)): p.read_bytes()
                 for p in self.repo.rglob("*") if p.is_file() and
                 ".git" not in p.relative_to(self.repo).parts})

    def clean_all_changes(self):
        self.git("restore", "--staged", "--worktree", ".")
        self.git("clean", "-fd")

    def test_manual_lifecycle_discards_then_applies(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                self.selected_changes()
                original_index = self.git("show", ":tracked")
                head = self.git("rev-parse", "HEAD")
                backup_id = self.create()
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assertEqual(self.listed_ids(), [backup_id])
                sha = backup_id[2:]
                if method == "commit":
                    self.assertEqual(self.git("show", "-s", "--format=%P", sha), head)
                    self.assertEqual(self.git("stash", "list"), "")
                    refs = self.git("for-each-ref", "--format=%(objectname)",
                                    "refs/agent-workflow/backups/")
                    self.assertEqual(refs, sha)
                else:
                    self.assertEqual(self.git("stash", "list", "--format=%H"), sha)
                    self.assertGreaterEqual(len(self.git("show", "-s", "--format=%P", sha).split()), 2)
                self.run_cli("backup", "apply", backup_id)
                self.assertEqual((self.repo / "tracked").read_text(), "unstaged\n")
                self.assertEqual((self.repo / "new-file").read_text(), "untracked\n")
                self.assertEqual(self.git("show", ":tracked"),
                                 original_index if method == "stash" else "unstaged")
                self.assertEqual(self.git("rev-parse", "HEAD"), head)
                self.assertEqual(self.listed_ids(), [backup_id])
                before = self.local_snapshot()
                self.run_cli("backup", "delete", backup_id)
                self.assertEqual(self.local_snapshot(), before)
                self.assertEqual(self.listed_ids(), [])
                self.clean_all_changes()

    def test_selection_modes_preserve_unselected_changes(self):
        for method in ("stash", "commit"):
            for mode in ("none", "tracked", "untracked", "all"):
                with self.subTest(method=method, mode=mode):
                    self.set_backup(method, mode)
                    self.selected_changes()
                    (self.repo / "added").write_text("new staged file\n")
                    self.git("add", "added")
                    (self.repo / "space [1]").write_text("literal path\n")
                    before_index = self.git("ls-files", "--stage")
                    result = self.run_cli("backup", "create")
                    if mode == "none":
                        self.assertIn("skip reason=no-selected-changes", result.stdout)
                        self.assertEqual(self.listed_ids(), [])
                    else:
                        backup_id = re.search(r"id=(b-[0-9a-f]{40,64})", result.stdout).group(1)
                        if mode in ("tracked", "all"):
                            self.assertEqual((self.repo / "tracked").read_text(), "original\n")
                            self.assertFalse((self.repo / "added").exists())
                        else:
                            self.assertEqual((self.repo / "tracked").read_text(), "unstaged\n")
                            self.assertEqual(self.git("ls-files", "--stage"), before_index)
                        self.assertEqual((self.repo / "new-file").exists(), mode == "tracked")
                        self.assertEqual((self.repo / "space [1]").exists(), mode == "tracked")
                        self.clean_all_changes()
                        self.run_cli("backup", "apply", backup_id)
                        self.assertEqual((self.repo / "tracked").read_text(),
                                         "original\n" if mode == "untracked" else "unstaged\n")
                        self.assertEqual((self.repo / "added").exists(), mode != "untracked")
                        self.assertEqual((self.repo / "new-file").exists(), mode != "tracked")
                        self.run_cli("backup", "delete", backup_id)
                    self.clean_all_changes()

    def test_prepare_discards_and_retains_all_backups(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                (self.repo / "tracked").write_text("manual\n")
                manual = self.create()
                self.selected_changes()
                branch = f"feat/{method}"
                self.run_cli("prepare", "--base", "main", "--branch-name", branch)
                self.assertEqual(self.git("branch", "--show-current"), branch)
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assertEqual((self.repo / "tracked").read_text(), "original\n")
                ids = self.listed_ids()
                self.assertEqual(len(ids), 2)
                self.assertIn(manual, ids)
                self.assertEqual(bool(self.git("stash", "list")), method == "stash")
                self.assertEqual(self.git("ls-remote", "origin", "refs/agent-workflow/backups/*"), "")
                for backup_id in ids:
                    self.run_cli("backup", "delete", backup_id)
                self.git("switch", "main")

    def test_prepare_failure_retains_backup_without_applying(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                self.selected_changes()
                self.git("remote", "remove", "origin")
                result = self.run_cli("prepare", "--base", "main",
                                      "--branch-name", f"feat/fail-{method}", ok=False)
                self.assertIn("backup retained", result.stderr)
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assertEqual(self.git("branch", "--show-current"), "main")
                backup_id = self.listed_ids()[0]
                self.run_cli("backup", "apply", backup_id)
                self.assertEqual((self.repo / "tracked").read_text(), "unstaged\n")
                self.run_cli("backup", "delete", backup_id)
                self.clean_all_changes()
                self.git("remote", "add", "origin", str(self.remote))

    def test_stable_identity_and_delete_isolation(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                (self.repo / "tracked").write_text("A\n")
                first = self.create()
                (self.repo / "tracked").write_text("unrelated\n")
                self.git("stash", "push", "-m", "user stash")
                unrelated = self.git("rev-parse", "refs/stash")
                (self.repo / "tracked").write_text("B\n")
                second = self.create()
                self.git("update-ref", "refs/agent-workflow/backups/unrelated", "HEAD")
                self.assertEqual(set(self.listed_ids()), {first, second})
                self.run_cli("backup", "apply", first)
                self.assertEqual((self.repo / "tracked").read_text(), "A\n")
                before = self.local_snapshot()
                refs = self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")
                self.run_cli("backup", "delete", first)
                self.assertEqual(self.local_snapshot(), before)
                self.assertEqual(self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"), refs)
                self.assertEqual(self.git("rev-parse", "refs/agent-workflow/backups/unrelated"), self.git("rev-parse", "HEAD"))
                self.assertEqual(self.listed_ids(), [second])
                self.assertIn(unrelated, self.git("stash", "list", "--format=%H"))
                self.run_cli("backup", "delete", second)
                self.assertEqual(self.git("stash", "list", "--format=%H"), unrelated)
                self.clean_all_changes()
                self.git("stash", "drop", "stash@{0}")

    def test_apply_old_backup_keeps_new_head_and_unrelated_work(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                (self.repo / "tracked").write_text("old useful change\n")
                backup_id = self.create()
                (self.repo / "later").write_text("later commit\n")
                self.git("add", "later")
                self.git("commit", "-qm", "Later", "--allow-empty")
                head = self.git("rev-parse", "HEAD")
                (self.repo / "later").write_text("current staged\n")
                self.git("add", "later")
                (self.repo / "later").write_text("current unstaged\n")
                self.run_cli("backup", "apply", backup_id)
                self.assertEqual(self.git("rev-parse", "HEAD"), head)
                self.assertEqual((self.repo / "later").read_text(), "current unstaged\n")
                self.assertEqual(self.git("show", ":later"), "current staged")
                self.assertEqual((self.repo / "tracked").read_text(), "old useful change\n")
                self.assertIn(backup_id, self.listed_ids())
                self.run_cli("backup", "delete", backup_id)
                self.clean_all_changes()

    def test_invalid_ids_and_conflicting_apply_retain_backup(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                self.selected_changes()
                backup_id = self.create()
                for invalid in ("stash@{0}", "b-nope", "b-" + "0" * 40):
                    before = self.local_snapshot()
                    self.run_cli("backup", "delete", invalid, ok=False)
                    self.run_cli("backup", "apply", invalid, ok=False)
                    self.assertEqual(self.local_snapshot(), before)
                (self.repo / "tracked").write_text("conflict\n")
                result = self.run_cli("backup", "apply", backup_id, ok=False)
                self.assertIn("backup retained", result.stderr)
                self.assertIn(backup_id, self.listed_ids())
                self.run_cli("backup", "delete", backup_id)
                self.clean_all_changes()

    def test_creation_and_verification_failure_never_discard(self):
        for method in ("stash", "commit"):
            for failure in ("construct", "store", "verify", "missing"):
                with self.subTest(method=method, failure=failure):
                    self.set_backup(method)
                    self.selected_changes()
                    before = self.local_snapshot()
                    marker = self.root / "stored"
                    marker.unlink(missing_ok=True)
                    store_condition = ('"$1" == stash && "$2" == store' if method == "stash"
                                       else '"$1" == update-ref && "$2" == refs/agent-workflow/backups/*')
                    real_git = shutil.which("git")
                    if failure == "construct":
                        body = 'if [[ "$1" == commit-tree ]]; then exit 1; fi'
                    elif failure in ("store", "missing"):
                        body = f'if [[ {store_condition} ]]; then exit {1 if failure == "store" else 0}; fi'
                    else:
                        body = (f'if [[ {store_condition} ]]; then\n'
                                f'  "{real_git}" "$@" || exit $?\n'
                                f'  touch "{marker}"; exit 0\nfi\n'
                                f'if [[ -e "{marker}" && "$1" == rev-parse && "$2" == *^* ]]; then exit 1; fi')
                    stub = self.stub_git(body)
                    self.run_cli("backup", "create", ok=False)
                    self.assertEqual(self.local_snapshot(), before)
                    stub.unlink()
                    for backup_id in self.listed_ids():
                        self.run_cli("backup", "delete", backup_id)
                    self.clean_all_changes()

    def test_untracked_discard_is_exact_and_preserves_ignored_files(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method, "untracked")
                (self.repo / ".git/info/exclude").write_text("*.ignored\n")
                (self.repo / "folder").mkdir(exist_ok=True)
                (self.repo / "folder/keep.ignored").write_text("ignored\n")
                (self.repo / "folder/saved").write_text("selected\n")
                real_git = shutil.which("git")
                condition = ('"$1" == stash && "$2" == store' if method == "stash"
                             else '"$1" == update-ref && "$2" == refs/agent-workflow/backups/*')
                stub = self.stub_git(f'if [[ {condition} ]]; then\n'
                                     f'  "{real_git}" "$@" || exit $?\n'
                                     '  echo new > folder/new-file; exit 0\nfi')
                backup_id = self.create()
                self.assertFalse((self.repo / "folder/saved").exists())
                self.assertEqual((self.repo / "folder/keep.ignored").read_text(), "ignored\n")
                self.assertTrue((self.repo / "folder/new-file").exists())
                stub.unlink()
                self.run_cli("backup", "apply", backup_id)
                self.assertEqual((self.repo / "folder/saved").read_text(), "selected\n")
                self.run_cli("backup", "delete", backup_id)
                self.clean_all_changes()

    def test_deleted_binary_and_symlink_changes_round_trip(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.set_backup(method)
                (self.repo / "tracked").unlink()
                (self.repo / "binary").write_bytes(b"\x00\xffsaved\x00")
                self.git("add", "binary")
                (self.repo / "link").symlink_to("binary")
                backup_id = self.create()
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.run_cli("backup", "apply", backup_id)
                self.assertFalse((self.repo / "tracked").exists())
                self.assertEqual((self.repo / "binary").read_bytes(), b"\x00\xffsaved\x00")
                self.assertEqual((self.repo / "link").readlink(), Path("binary"))
                self.run_cli("backup", "delete", backup_id)
                self.clean_all_changes()

    def test_recreated_staged_deletion_is_not_overwritten(self):
        for method in ("stash", "commit"):
            for mode in ("tracked", "all"):
                with self.subTest(method=method, mode=mode):
                    self.set_backup(method, mode)
                    self.git("rm", "tracked")
                    (self.repo / "tracked").write_text("untracked replacement\n")
                    before = self.local_snapshot()
                    self.run_cli("backup", "create", ok=False)
                    self.assertEqual(self.local_snapshot(), before)
                    self.assertEqual(self.listed_ids(), [])
                    self.clean_all_changes()

    def test_files_changed_after_snapshot_are_not_discarded(self):
        for method in ("stash", "commit"):
            for mode in ("tracked", "untracked"):
                with self.subTest(method=method, mode=mode):
                    self.set_backup(method, mode)
                    path = "tracked" if mode == "tracked" else "new-file"
                    if mode == "tracked":
                        (self.repo / path).unlink()
                    else:
                        (self.repo / path).write_text("saved\n")
                    index = self.git("ls-files", "--stage")
                    real_git = shutil.which("git")
                    condition = ('"$1" == stash && "$2" == store' if method == "stash"
                                 else '"$1" == update-ref && "$2" == refs/agent-workflow/backups/*')
                    stub = self.stub_git(f'if [[ {condition} ]]; then\n'
                                         f'  "{real_git}" "$@" || exit $?\n'
                                         f'  echo later > {path}; exit 0\nfi')
                    result = self.run_cli("backup", "create", ok=False)
                    self.assertIn("changed during backup", result.stderr)
                    self.assertEqual((self.repo / path).read_text(), "later\n")
                    self.assertEqual(self.git("ls-files", "--stage"), index)
                    stub.unlink()
                    ids = self.listed_ids()
                    self.assertEqual(len(ids), 1)
                    self.run_cli("backup", "delete", ids[0])
                    self.clean_all_changes()

    def test_continue_sync_failure_retains_backup_without_applying(self):
        for method in ("stash", "commit"):
            with self.subTest(method=method):
                self.config["git"]["sync"]["mode"] = "fetch"
                self.set_backup(method)
                branch = f"feat/continue-{method}"
                self.run_cli("prepare", "--base", "main", "--branch-name", branch)
                self.selected_changes()
                head = self.git("rev-parse", "HEAD")
                stub = self.stub_git('if [[ "$1" == fetch ]]; then exit 1; fi')
                result = self.run_cli("prepare", "--continue", ok=False)
                self.assertIn("backup retained", result.stderr)
                self.assertEqual(self.git("branch", "--show-current"), branch)
                self.assertEqual(self.git("rev-parse", "HEAD"), head)
                self.assertEqual(self.git("status", "--porcelain"), "")
                stub.unlink()
                ids = self.listed_ids()
                self.assertEqual(len(ids), 1)
                self.run_cli("backup", "apply", ids[0])
                self.assertEqual((self.repo / "tracked").read_text(), "unstaged\n")
                self.run_cli("backup", "delete", ids[0])
                self.clean_all_changes()
                self.git("switch", "main")

    def test_legacy_commit_and_stash_are_discoverable(self):
        (self.repo / "tracked").write_text("legacy commit\n")
        self.git("add", "tracked")
        sha = self.git("commit-tree", self.git("write-tree"), "-p", "HEAD",
                       "-m", "agent-workflow backup legacy")
        self.git("update-ref", "refs/agent-workflow/backups/legacy", sha)
        self.clean_all_changes()
        self.run_cli("backup", "apply", "b-" + sha)
        self.assertEqual((self.repo / "tracked").read_text(), "legacy commit\n")
        self.run_cli("backup", "delete", "b-" + sha)
        self.clean_all_changes()
        (self.repo / "tracked").write_text("legacy stash\n")
        self.git("stash", "push", "-m", "agent-workflow backup legacy")
        sha = self.git("rev-parse", "refs/stash")
        self.run_cli("backup", "apply", "b-" + sha)
        self.assertEqual((self.repo / "tracked").read_text(), "legacy stash\n")
        self.run_cli("backup", "delete", "b-" + sha)

    def test_disabled_and_help_do_not_mutate(self):
        self.config["workflow"]["enabled"] = False
        self.save_config()
        self.selected_changes()
        before = self.local_snapshot()
        for action, args in (("create", ()), ("apply", ("b-" + "0" * 40,)),
                             ("delete", ("b-" + "0" * 40,))):
            self.assertIn("skip reason=workflow-disabled", self.run_cli("backup", action, *args).stdout)
            self.assertEqual(self.local_snapshot(), before)
        for args in (("backup", "--help"), ("backup", "create", "--help"),
                     ("backup", "list", "--help"), ("backup", "apply", "--help"),
                     ("backup", "delete", "--help")):
            self.assertIn("Usage:", self.run_cli(*args).stdout)
        self.assertEqual(self.local_snapshot(), before)

    def test_empty_selection_skips_and_identical_backups_have_unique_ids(self):
        self.assertIn("skip reason=no-selected-changes", self.run_cli("backup", "create").stdout)
        for method in ("stash", "commit"):
            self.set_backup(method)
            ids = []
            for _ in range(2):
                (self.repo / "tracked").write_text("same\n")
                ids.append(self.create())
            self.assertNotEqual(*ids)
            for backup_id in ids:
                self.run_cli("backup", "delete", backup_id)
