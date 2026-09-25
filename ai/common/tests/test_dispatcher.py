from copy import deepcopy
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

from sandbox import isolated_environment


ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / "ai/common/libexec/agent-workflow-opt-in"
DEFAULT_SETTINGS = runpy.run_path(str(ROOT / "ai/common/bin/agent-project"))["DEFAULT_SETTINGS"]


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = isolated_environment(self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)],
                       env=self.env, check=True)
        self.config = self.repo / ".ai/project.json"

    def probe(self, cwd=None):
        return subprocess.run([str(PROBE)], cwd=cwd or self.repo,
                              env=self.env, capture_output=True, text=True)

    def test_missing_and_disabled_projects_do_not_load_workflow(self):
        result = self.probe()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertFalse(self.config.parent.exists())

        self.config.parent.mkdir()
        self.config.write_text(json.dumps(DEFAULT_SETTINGS))
        before = self.config.read_bytes()
        result = self.probe()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertEqual(self.config.read_bytes(), before)

    def test_enabled_project_is_detected_from_nested_directory(self):
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["workflow"]["enabled"] = True
        self.config.parent.mkdir()
        self.config.write_text(json.dumps(settings))
        nested = self.repo / "src" / "nested"
        nested.mkdir(parents=True)
        result = self.probe(cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_invalid_marker_and_non_repository(self):
        self.config.parent.mkdir()
        self.config.write_text("{")
        result = self.probe()
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read", result.stderr)

        self.config.write_text('{"workflow":{"enabled":1}}')
        result = self.probe()
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid workflow.enabled", result.stderr)

        outside = self.root / "outside"
        outside.mkdir()
        result = self.probe(cwd=outside)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertEqual(list(outside.iterdir()), [])

    def test_codex_adapter_defers_shared_instructions(self):
        adapter = (ROOT / "ai/codex/AGENTS.md").read_text()
        self.assertLess(len(adapter), 800)
        self.assertIn("agent-workflow-opt-in", adapter)
        self.assertIn("Exit 0: read `~/.config/agent-workflow/instructions.md`", adapter)
        self.assertIn("Exit 1: stop loading dotfiles workflow guidance", adapter)
        probe_source = PROBE.read_text()
        self.assertNotIn("instructions.md", probe_source)
        self.assertNotIn("agent-project", probe_source)
        self.assertNotIn("git-workflow", probe_source)
