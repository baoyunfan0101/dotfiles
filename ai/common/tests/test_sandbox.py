import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sandbox import isolated_environment


class SandboxTests(unittest.TestCase):
    def test_fixture_environment_discards_inherited_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"GIT_DIR": "/outside/repo", "GH_TOKEN": "canary",
                                         "HOME": "/outside/home", "CODEX_HOME": "/outside/codex",
                                         "BASH_ENV": "/outside/shell"}):
                environment = isolated_environment(root)
            for key in ("HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
                        "XDG_DATA_HOME", "XDG_CACHE_HOME", "GH_CONFIG_DIR", "TMPDIR"):
                self.assertIn(root, Path(environment[key]).parents)
            for key in ("GIT_DIR", "GH_TOKEN", "BASH_ENV"):
                self.assertNotIn(key, environment)
            self.assertEqual(environment["GIT_ALLOW_PROTOCOL"], "file")

    @unittest.skipUnless(os.environ.get("DOTFILES_TEST_SANDBOX"), "requires ./test.sh")
    def test_entrypoint_isolation(self):
        root = Path(os.environ["DOTFILES_TEST_SANDBOX"])
        self.assertEqual(Path.cwd(), root.resolve())
        for key in ("HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
                    "XDG_DATA_HOME", "XDG_CACHE_HOME", "GH_CONFIG_DIR", "TMPDIR"):
            self.assertIn(root, Path(os.environ[key]).parents)
        self.assertEqual(os.environ["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(os.environ["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(os.environ["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(os.environ["GIT_ALLOW_PROTOCOL"], "file")
        for key in ("GIT_DIR", "GH_TOKEN", "GITHUB_TOKEN", "DOTFILES_INSTALL_MODE"):
            self.assertNotIn(key, os.environ)
        result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 99)
        self.assertIn("configure a test stub", result.stderr)
