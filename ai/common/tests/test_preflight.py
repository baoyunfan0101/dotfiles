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

    def prepare(self):
        return subprocess.run(
            [BASH, str(COMMON / "bin/git-workflow"), "prepare",
             "--branch-name", "feat/test"],
            cwd=self.repo, env={**self.env, "PATH": str(self.bin)},
            capture_output=True, text=True,
        )

    def assert_ready(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[git] prepare ok backup=none sync=skipped "
                         "branch=feat/test base=main created=true\n")

    def assert_blocked(self, check, reason, required_by, fix):
        (self.repo / "tracked").write_text("changed\n")
        (self.repo / "untracked").write_text("untracked\n")
        before = {str(p.relative_to(self.repo)): p.read_bytes()
                  for p in self.repo.rglob("*") if p.is_file()}
        result = self.prepare()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr,
                         f'[git] prepare error check={check} reason="{reason}" '
                         f'required-by={required_by} fix="{fix}"\n')
        after = {str(p.relative_to(self.repo)): p.read_bytes()
                 for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        self.assertTrue(all(call == "rev-parse --show-toplevel" for call in calls), calls)

    def test_local_merge_without_gh(self):
        self.assert_ready()

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
                self.assert_blocked("project-settings", "configuration invalid", "core",
                                    "fix .ai/project.json and run agent-project-settings effective")

    def test_missing_core_commands(self):
        for command in ("git", "python3", "agent-project-settings"):
            with self.subTest(command=command):
                path = self.bin / command
                hidden = self.root / command
                path.rename(hidden)
                try:
                    fix = ("re-run the dotfiles AI installer" if command == "agent-project-settings"
                           else f"install {command} and ensure it is on PATH")
                    self.assert_blocked(command, "command not found", "core", fix)
                finally:
                    hidden.rename(path)


if __name__ == "__main__":
    unittest.main()
