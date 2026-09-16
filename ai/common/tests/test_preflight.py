import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


COMMON = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
BASH = shutil.which("bash")


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "git.log"
        self.env = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        self.git("init", "-q", "-b", "main")
        (self.repo / "tracked").write_text("original\n")
        self.configure("localMerge")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial")
        self.stub("git", f'printf "%s\\n" "$*" >> {shlex.quote(str(self.log))}\n'
                  f'exec {shlex.quote(GIT)} "$@"')
        (self.bin / "python3").symlink_to(sys.executable)
        (self.bin / "date").symlink_to(shutil.which("date"))
        for command in ("bash", "dirname", "readlink", "cat"):
            (self.bin / command).symlink_to(shutil.which(command))
        (self.bin / "agent-project-settings").symlink_to(COMMON / "bin/project-settings")

    def git(self, *args):
        return subprocess.check_output(
            [GIT, *args], cwd=self.repo, env=self.env, text=True
        )

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def configure(self, mode, sync="none"):
        directory = self.repo / ".ai"
        directory.mkdir(exist_ok=True)
        (directory / "project.json").write_text(json.dumps({
            "schemaVersion": 1,
            "git": {"sync": {"mode": sync}, "integration": {"mode": mode}},
        }))

    def start(self):
        return self.run_action("start")

    def run_action(self, action):
        arguments = {
            "start": ["--branch-name", "feat/test"],
            "commit": ["--message", "Change", "--all"],
            "finish": [],
            "push": [],
        }
        return subprocess.run(
            [BASH, str(COMMON / "bin/git-workflow"), action, *arguments[action]],
            cwd=self.repo, env={**self.env, "PATH": str(self.bin)},
            capture_output=True, text=True,
        )

    def assert_ready(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[git] start ok backup=none sync=skipped "
                         "branch=feat/test base=main created=true\n")

    def assert_blocked(self, check, reason, required_by, fix, action="start"):
        (self.repo / "tracked").write_text("changed\n")
        (self.repo / "untracked").write_text("untracked\n")
        before = {str(p.relative_to(self.repo)): p.read_bytes()
                  for p in self.repo.rglob("*") if p.is_file()}
        result = self.run_action(action)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr,
                         f'[git] {action} error check={check} reason="{reason}" '
                         f'required-by={required_by} fix="{fix}"\n')
        after = {str(p.relative_to(self.repo)): p.read_bytes()
                 for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        allowed = {"rev-parse --show-toplevel"}
        if action == "finish":
            allowed.update({"rev-parse --is-inside-work-tree", "symbolic-ref --quiet --short HEAD",
                            "config --get branch.feat/test.agentWorkflowBase",
                            "config --bool --get branch.feat/test.agentWorkflowCreated"})
        self.assertTrue(all(call in allowed for call in calls), calls)

    def test_local_merge_without_gh(self):
        self.assert_ready()

    def test_removed_commands_are_rejected(self):
        for command in ("prepare", "integrate"):
            result = subprocess.run(
                [BASH, str(COMMON / "bin/git-workflow"), command],
                cwd=self.repo, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(f'unknown command: {command}', result.stderr)

    def test_pull_request_without_gh(self):
        self.configure("pullRequest", sync="fetch")
        self.assert_blocked("gh", "command not found", "git.integration.mode:pullRequest",
                            "install gh and ensure it is on PATH")

    def test_pull_request_without_authentication(self):
        self.configure("pullRequest", sync="fetch")
        self.stub("gh", 'echo "auth noise" >&2; exit 1')
        self.assert_blocked("gh-auth", "authentication failed", "git.integration.mode:pullRequest",
                            "run gh auth login")

    def test_authenticated_pull_request(self):
        self.configure("pullRequest")
        self.git("add", ".ai/project.json")
        self.git("commit", "-qm", "Configure PR")
        self.stub("gh", '[ "$*" = "auth status" ] || exit 2; echo "auth noise"')
        self.assert_ready()

    def test_invalid_settings(self):
        for content in ('{', '{"schemaVersion":1,"unknown":true}',
                        '{"schemaVersion":1,"git":{"integration":{"mode":"invalid"}}}'):
            with self.subTest(content=content):
                (self.repo / ".ai/project.json").write_text(content)
                for action in ("start", "commit", "finish", "push"):
                    self.assert_blocked("project-settings", "configuration invalid", "core",
                                        "fix .ai/project.json and run agent-project-settings effective", action)

    def test_missing_core_commands(self):
        for command in ("git", "python3", "agent-project-settings"):
            with self.subTest(command=command):
                path = self.bin / command
                hidden = self.root / command
                path.rename(hidden)
                try:
                    fix = ("re-run the dotfiles AI installer" if command == "agent-project-settings"
                           else f"install {command} and ensure it is on PATH")
                    for action in ("start", "commit", "finish", "push"):
                        self.assert_blocked(command, "command not found", "core", fix, action)
                finally:
                    hidden.rename(path)

    def test_finish_requires_gh_and_authentication(self):
        self.configure("pullRequest")
        self.git("checkout", "-qb", "feat/test")
        self.git("config", "branch.feat/test.agentWorkflowBase", "main")
        self.git("config", "branch.feat/test.agentWorkflowCreated", "true")
        self.assert_blocked("gh", "command not found", "git.integration.mode:pullRequest",
                            "install gh and ensure it is on PATH", "finish")
        self.stub("gh", "exit 1")
        self.assert_blocked("gh-auth", "authentication failed", "git.integration.mode:pullRequest",
                            "run gh auth login", "finish")

    def test_non_workflow_finish_skips_without_gh(self):
        self.configure("pullRequest")
        before = {str(p.relative_to(self.repo)): p.read_bytes()
                  for p in self.repo.rglob("*") if p.is_file()}
        result = self.run_action("finish")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[git] finish skip reason=no-workflow-created-branch\n")
        self.assertEqual(result.stderr, "")
        after = {str(p.relative_to(self.repo)): p.read_bytes()
                 for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_non_workflow_finish_does_not_check_authentication(self):
        self.configure("pullRequest")
        self.stub("gh", f'printf called > {shlex.quote(str(self.root / "gh-called"))}; exit 1')
        result = self.run_action("finish")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[git] finish skip reason=no-workflow-created-branch\n")
        self.assertFalse((self.root / "gh-called").exists())

    def test_commit_and_push_do_not_require_gh(self):
        self.configure("pullRequest")
        path = self.repo / ".ai/project.json"
        config = json.loads(path.read_text())
        config["git"]["commit"] = {"mode": "manual"}
        path.write_text(json.dumps(config))
        for action in ("commit", "push"):
            result = self.run_action(action)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"[git] {action} skip reason=manual\n")
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
