import json
from copy import deepcopy
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from sandbox import isolated_environment


COMMON = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = runpy.run_path(str(COMMON / "bin/agent-project"))["DEFAULT_SETTINGS"]
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
        self.env = isolated_environment(self.root)
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
        (self.bin / "agent-project").symlink_to(COMMON / "bin/agent-project")

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
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["workflow"]["enabled"] = True
        settings["git"]["sync"]["mode"] = sync
        settings["git"]["integration"]["mode"] = mode
        (directory / "project.json").write_text(json.dumps(settings))

    def prepare(self):
        return self.run_action("prepare")

    def run_action(self, action, cwd=None):
        arguments = {
            "prepare": ["--branch-name", "feat/test"],
            "commit": ["--message", "Change", "--all"],
            "merge": [],
            "push": [],
            "pr submit": [],
            "pr merge": [],
        }
        return subprocess.run(
            [BASH, str(COMMON / "bin/git-workflow"), *action.split(), *arguments[action]],
            cwd=self.repo if cwd is None else cwd,
            env={**self.env, "PATH": str(self.bin)},
            capture_output=True, text=True,
        )

    def assert_ready(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[git] prepare ok backup=none sync=skipped "
                         "branch=feat/test base=main created=true\n")

    def assert_blocked(self, check, reason, required_by, fix, action="prepare"):
        if not action.startswith("pr "):
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
        allowed = {"rev-parse --show-toplevel", "rev-parse --is-inside-work-tree"}
        if action.startswith("pr "):
            allowed.update({"symbolic-ref --quiet --short HEAD",
                            "config --get branch.feat/test.agentWorkflowBase",
                            "config --bool --get branch.feat/test.agentWorkflowCreated",
                            "diff --quiet --", "diff --cached --quiet --",
                            "ls-files --others --exclude-standard"})
        self.assertTrue(all(call in allowed for call in calls), calls)

    def test_local_merge_without_gh(self):
        self.assert_ready()

    def test_removed_commands_are_rejected(self):
        for command in ("start", "finish", "integrate"):
            result = subprocess.run(
                [BASH, str(COMMON / "bin/git-workflow"), command],
                cwd=self.repo, env=self.env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(f'unknown command: {command}', result.stderr)

    def test_disabled_workflow_skips_before_delivery_preflight(self):
        directory = self.repo / ".ai"
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["git"]["integration"]["mode"] = "pullRequest"
        (directory / "project.json").write_text(json.dumps(settings))

        for action in ("prepare", "commit", "merge", "push", "pr submit", "pr merge"):
            result = self.run_action(action)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                f"[git] {action} skip reason=workflow-disabled\n",
            )

    def test_pull_request_without_gh(self):
        self.configure("pullRequest")
        self.git("add", ".")
        self.git("commit", "-qm", "Configure")
        self.assert_ready()

    def test_disabled_workflow_preserves_repository_and_never_calls_gh(self):
        marker = self.root / "gh-called"
        self.stub("gh", f'printf called > {shlex.quote(str(marker))}; exit 1')
        (self.repo / "tracked").write_text("changed\n")
        (self.repo / "untracked").write_text("untracked\n")
        config = self.repo / ".ai/project.json"

        for enabled in (None, False):
            if enabled is None:
                config.unlink(missing_ok=True)
            else:
                settings = deepcopy(DEFAULT_SETTINGS)
                settings["workflow"]["enabled"] = enabled
                settings["git"]["integration"]["mode"] = "pullRequest"
                config.write_text(json.dumps(settings))
            before = {str(p.relative_to(self.repo)): p.read_bytes()
                      for p in self.repo.rglob("*") if p.is_file()}

            for action in ("prepare", "commit", "merge", "push", "pr submit", "pr merge"):
                with self.subTest(enabled=enabled, action=action):
                    result = self.run_action(action)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout,
                                     f"[git] {action} skip reason=workflow-disabled\n")
                    self.assertEqual(result.stderr, "")
                    self.assertFalse(marker.exists())
                    after = {str(p.relative_to(self.repo)): p.read_bytes()
                             for p in self.repo.rglob("*") if p.is_file()}
                    self.assertEqual(before, after)

        allowed = {"rev-parse --is-inside-work-tree", "rev-parse --show-toplevel"}
        calls = self.log.read_text().splitlines()
        self.assertTrue(all(call in allowed for call in calls), calls)

    def test_pull_request_without_authentication(self):
        self.configure("pullRequest")
        self.git("add", ".")
        self.git("commit", "-qm", "Configure")
        self.stub("gh", 'echo "auth noise" >&2; exit 1')
        self.assert_ready()

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
                for action in ("prepare", "commit", "merge", "push", "pr submit", "pr merge"):
                    self.assert_blocked("project-settings", "configuration invalid", "core",
                                        "fix .ai/project.json and run agent-project effective", action)

    def test_missing_core_commands(self):
        for command in ("git", "python3", "agent-project"):
            with self.subTest(command=command):
                path = self.bin / command
                hidden = self.root / command
                path.rename(hidden)
                try:
                    fix = ("re-run the dotfiles AI installer" if command == "agent-project"
                           else f"install {command} and ensure it is on PATH")
                    for action in ("prepare", "commit", "merge", "push", "pr submit", "pr merge"):
                        self.assert_blocked(command, "command not found", "core", fix, action)
                finally:
                    hidden.rename(path)

    def test_repository_validation_does_not_repeat_dependency_lookup(self):
        lookups = self.root / "lookups.log"
        startup = self.root / "trace-lookups.sh"
        startup.write_text(
            'command() {\n'
            '  if [[ "${1:-}" == -v ]]; then\n'
            f'    printf "%s\\n" "$2" >> {shlex.quote(str(lookups))}\n'
            '  fi\n'
            '  builtin command "$@"\n'
            '}\n'
        )
        self.env["BASH_ENV"] = str(startup)
        (self.repo / ".ai/project.json").unlink()
        outside = self.root / "outside"
        outside.mkdir()

        for directory in (self.repo, outside):
            for action in ("prepare", "commit", "merge", "push", "pr submit", "pr merge"):
                with self.subTest(directory=directory.name, action=action):
                    lookups.write_text("")
                    result = self.run_action(action, cwd=directory)
                    self.assertEqual(lookups.read_text().splitlines(),
                                     ["git", "python3", "agent-project"])
                    if directory == self.repo:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout,
                                         f"[git] {action} skip reason=workflow-disabled\n")
                        self.assertEqual(result.stderr, "")
                    else:
                        self.assertEqual(result.returncode, 1)
                        self.assertEqual(result.stdout, "")
                        self.assertEqual(result.stderr,
                                         f'[git] {action} error reason="current directory is not inside a Git worktree"\n')
                        self.assertEqual(list(outside.iterdir()), [])

    def test_pr_requires_gh_and_authentication(self):
        self.configure("pullRequest")
        self.git("add", ".")
        self.git("commit", "-qm", "Configure")
        self.git("checkout", "-qb", "feat/test")
        self.git("config", "branch.feat/test.agentWorkflowBase", "main")
        self.git("config", "branch.feat/test.agentWorkflowCreated", "true")
        for action in ("pr submit", "pr merge"):
            self.assert_blocked("gh", "command not found", "git.integration.mode:pullRequest",
                                "install gh and ensure it is on PATH", action)
        self.stub("gh", "exit 1")
        for action in ("pr submit", "pr merge"):
            self.assert_blocked("gh-auth", "authentication failed", "git.integration.mode:pullRequest",
                                "run gh auth login", action)

    def test_non_workflow_delivery_fails_without_gh(self):
        self.configure("pullRequest")
        before = {str(p.relative_to(self.repo)): p.read_bytes()
                  for p in self.repo.rglob("*") if p.is_file()}
        result = self.run_action("pr submit")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("workflow working branch", result.stderr)
        self.assertEqual(result.stdout, "")
        after = {str(p.relative_to(self.repo)): p.read_bytes()
                 for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_non_workflow_delivery_does_not_check_authentication(self):
        self.configure("pullRequest")
        self.stub("gh", f'printf called > {shlex.quote(str(self.root / "gh-called"))}; exit 1')
        result = self.run_action("pr merge")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("workflow working branch", result.stderr)
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
