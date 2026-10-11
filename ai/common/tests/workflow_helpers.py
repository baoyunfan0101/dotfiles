import json
from copy import deepcopy
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from sandbox import isolated_environment


COMMON = Path(__file__).resolve().parents[1]

DEFAULT_SETTINGS = runpy.run_path(str(COMMON / "bin/agent-project"))["DEFAULT_SETTINGS"]

CLI = COMMON / "bin/git-workflow"


class WorkflowTestCase(unittest.TestCase):
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

