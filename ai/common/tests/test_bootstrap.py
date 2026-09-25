from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from sandbox import isolated_environment


ROOT = Path(__file__).resolve().parents[3]
BASH = shutil.which("bash")


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = isolated_environment(self.root)
        self.source = self.root / "source"
        self.source.mkdir()
        shutil.copytree(ROOT / "ai", self.source / "ai")
        shutil.copy2(ROOT / "install.sh", self.source / "install.sh")
        self.git("init", "-q", "-b", "main", cwd=self.source)
        self.git("add", ".", cwd=self.source)
        self.git("commit", "-qm", "Initial", cwd=self.source)
        self.remote = self.root / "remote.git"
        self.git("clone", "-q", "--bare", str(self.source), str(self.remote))
        self.git("remote", "add", "origin", str(self.remote), cwd=self.source)
        self.git("push", "-q", "-u", "origin", "main", cwd=self.source)
        self.env["DOTFILES_REPOSITORY_URL"] = str(self.remote)
        self.checkout = Path(self.env["XDG_DATA_HOME"]) / "dotfiles"
        self.project_cli = Path(self.env["HOME"]) / ".local/bin/agent-project"

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd or self.root,
                                       env=self.env, text=True).strip()

    def bootstrap(self, piped=False):
        return subprocess.run(
            [BASH] if piped else [BASH, str(ROOT / "install.sh")],
            input=(ROOT / "install.sh").read_text() if piped else None,
            env=self.env, capture_output=True, text=True,
        )

    def test_first_install_and_repeated_run_converge(self):
        result = self.bootstrap(piped=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.project_cli.is_symlink())
        self.assertEqual(self.project_cli.resolve(),
                         (self.checkout / "ai/common/bin/agent-project").resolve())
        original_head = self.git("rev-parse", "HEAD", cwd=self.checkout)

        result = self.bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.checkout), original_head)
        self.assertEqual(self.project_cli.resolve(),
                         (self.checkout / "ai/common/bin/agent-project").resolve())

    def test_existing_checkout_updates_and_repairs_managed_target(self):
        self.assertEqual(self.bootstrap().returncode, 0)
        self.project_cli.unlink()
        self.project_cli.write_text("outdated\n")
        (self.source / "ai/common/instructions.md").write_text("updated\n")
        self.git("add", ".", cwd=self.source)
        self.git("commit", "-qm", "Update", cwd=self.source)
        self.git("push", "-q", "origin", "main", cwd=self.source)

        result = self.bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.project_cli.is_symlink())
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.checkout),
                         self.git("rev-parse", "HEAD", cwd=self.source))
        self.assertEqual((self.checkout / "ai/common/instructions.md").read_text(),
                         "updated\n")

    def test_unmanaged_declared_target_is_replaced_without_touching_other_files(self):
        self.project_cli.parent.mkdir(parents=True)
        self.project_cli.write_text("old installation\n")
        personal_file = self.project_cli.parent / "personal-tool"
        personal_file.write_text("keep me\n")

        for _ in range(2):
            result = self.bootstrap()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(self.project_cli.is_symlink())
            self.assertEqual(self.project_cli.resolve(),
                             (self.checkout / "ai/common/bin/agent-project").resolve())
            self.assertEqual(personal_file.read_text(), "keep me\n")

    def test_missing_git_and_dirty_checkout_report_errors(self):
        env = dict(self.env)
        env["PATH"] = str(self.root / "empty-bin")
        result = subprocess.run([BASH, str(ROOT / "install.sh")], env=env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git is required", result.stderr)

        self.assertEqual(self.bootstrap().returncode, 0)
        (self.checkout / "ai/common/instructions.md").write_text("local change\n")
        result = self.bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local changes", result.stderr)
        self.assertEqual((self.checkout / "ai/common/instructions.md").read_text(),
                         "local change\n")
