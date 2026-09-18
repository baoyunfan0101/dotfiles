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

    def run_project(self, *arguments):
        return subprocess.run(
            [sys.executable, str(PROJECT), *arguments],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def write_config(self, config):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps(config))

    def test_defaults_and_partial_overrides(self):
        result = self.run_project("effective")
        self.assertEqual(result.returncode, 0, result.stderr)
        defaults = json.loads(result.stdout)
        self.assertEqual(defaults["git"]["integration"]["mode"], "localMerge")

        self.write_config({"schemaVersion": 1, "git": {"commit": {"mode": "manual"}}})
        result = self.run_project("effective")
        self.assertEqual(result.returncode, 0, result.stderr)
        defaults["git"]["commit"]["mode"] = "manual"
        self.assertEqual(json.loads(result.stdout), defaults)
        self.assertEqual(self.run_project("get", "git.commit.mode").stdout, "manual\n")

    def test_set_creates_minimal_override_and_unset_restores_default(self):
        result = self.run_project("set", "git.commit.mode", "manual")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(self.config.read_text()),
            {"schemaVersion": 1, "git": {"commit": {"mode": "manual"}}},
        )
        self.assertEqual(self.run_project("get", "git.commit.mode").stdout, "manual\n")

        result = self.run_project("unset", "git.commit.mode")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text()), {"schemaVersion": 1})
        self.assertEqual(self.run_project("get", "git.commit.mode").stdout, "automatic\n")

        result = self.run_project("unset", "git.commit.mode")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skip", result.stdout)

    def test_set_parses_schema_types(self):
        self.assertEqual(
            self.run_project("set", "git.branch.deleteAfterIntegration", "true").returncode,
            0,
        )
        self.assertEqual(
            self.run_project("set", "git.branch.baseBranches", '["main","develop"]').returncode,
            0,
        )
        config = json.loads(self.config.read_text())
        self.assertIs(config["git"]["branch"]["deleteAfterIntegration"], True)
        self.assertEqual(config["git"]["branch"]["baseBranches"], ["main", "develop"])

    def test_invalid_set_does_not_write_invalid_configuration(self):
        result = self.run_project("set", "git.integration.mode", "invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be one of", result.stderr)
        self.assertFalse(self.config.exists())

        result = self.run_project("set", "git.branch.deleteAfterIntegration", "yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("true or false", result.stderr)
        self.assertFalse(self.config.exists())

        result = self.run_project("set", "git.branch.baseBranches", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("valid JSON", result.stderr)
        self.assertFalse(self.config.exists())

    def test_unset_schema_version_is_rejected(self):
        self.write_config({"schemaVersion": 1})
        result = self.run_project("unset", "schemaVersion")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be unset", result.stderr)
        self.assertEqual(json.loads(self.config.read_text()), {"schemaVersion": 1})

    def test_invalid_settings(self):
        for config, error in (
            ({}, "schemaVersion is required"),
            ({"schemaVersion": 2}, "unsupported schemaVersion"),
            ({"schemaVersion": 1, "unknown": True}, "unknown setting"),
            ({"schemaVersion": 1, "git": {"commit": {"mode": "bad"}}}, "must be one of"),
        ):
            with self.subTest(config=config):
                self.write_config(config)
                result = self.run_project("effective")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)


if __name__ == "__main__":
    unittest.main()
