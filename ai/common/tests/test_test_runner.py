import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
TEST_SCRIPT = ROOT / "test.sh"


class TestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "python3"
        wrapper.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-B" ] && [ "$2" = "-m" ] && [ "$3" = "unittest" ]; then\n'
            '  printf "UNITTEST_ARGS=%s\\n" "$*"\n'
            '  printf "SANDBOX=%s\\nHOME=%s\\n" "$DOTFILES_TEST_SANDBOX" "$HOME"\n'
            "  exit 0\n"
            "fi\n"
            f"exec {shlex.quote(sys.executable)} \"$@\"\n"
        )
        wrapper.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = f"{bin_dir}:{self.env['PATH']}"

    def run_tests(self, *arguments):
        return subprocess.run([str(TEST_SCRIPT), *arguments], cwd=self.root,
                              env=self.env, capture_output=True, text=True)

    def test_no_argument_keeps_full_discovery_in_isolated_environment(self):
        result = self.run_tests()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"unittest discover -s {ROOT}/ai/common/tests\n", result.stdout)
        self.assertNotIn(" -p ", result.stdout)
        self.assertIn("SANDBOX=/tmp/dotfiles-test.", result.stdout)
        self.assertIn("HOME=/tmp/dotfiles-test.", result.stdout)

    def test_existing_modules_can_be_selected_by_name(self):
        for name in ("workflow", "settings", "preflight", "bootstrap"):
            with self.subTest(name=name):
                result = self.run_tests(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f" -p test_{name}.py\n", result.stdout)
                self.assertIn("SANDBOX=/tmp/dotfiles-test.", result.stdout)

    def test_invalid_or_unknown_targets_fail_explicitly(self):
        for arguments, message in (
            (("missing",), "Unknown test target: missing"),
            (("../workflow",), "Invalid test target: ../workflow"),
            (("workflow.py",), "Invalid test target: workflow.py"),
            (("workflow", "settings"), "Usage: ./test.sh [test name]"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_tests(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertNotIn("UNITTEST_ARGS=", result.stdout)
