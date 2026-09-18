import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sandbox import isolated_environment


PROJECT = Path(__file__).resolve().parents[1] / "bin/agent-project"


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = isolated_environment(self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], env=self.env, check=True)
        self.config = self.repo / ".ai/project.json"
        self.config.parent.mkdir()

    def run_settings(self, *arguments):
        return subprocess.run([sys.executable, str(PROJECT), *arguments],
                              cwd=self.repo, env=self.env, capture_output=True, text=True)

    def test_defaults_and_partial_overrides(self):
        result = self.run_settings("effective")
        self.assertEqual(result.returncode, 0, result.stderr)
        defaults = json.loads(result.stdout)
        self.assertEqual(defaults["git"]["integration"]["mode"], "localMerge")
        self.config.write_text(json.dumps({"schemaVersion": 1, "git": {"commit": {"mode": "manual"}}}))
        result = self.run_settings("effective")
        self.assertEqual(result.returncode, 0, result.stderr)
        defaults["git"]["commit"]["mode"] = "manual"
        self.assertEqual(json.loads(result.stdout), defaults)
        self.assertEqual(self.run_settings("get", "git.commit.mode").stdout, "manual\n")

    def test_invalid_settings(self):
        for config, error in (({}, "schemaVersion is required"),
                              ({"schemaVersion": 2}, "unsupported schemaVersion"),
                              ({"schemaVersion": 1, "unknown": True}, "unknown setting"),
                              ({"schemaVersion": 1, "git": {"commit": {"mode": "bad"}}}, "must be one of")):
            with self.subTest(config=config):
                self.config.write_text(json.dumps(config))
                result = self.run_settings("effective")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
