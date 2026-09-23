from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def run_project(self, *arguments, cwd=None):
        return subprocess.run(
            [sys.executable, str(PROJECT), *arguments],
            cwd=self.repo if cwd is None else cwd,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def write_config(self, config):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps(config))

    def test_help(self):
        result = self.run_project("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("effective", "get <path>", "set <path> <value>", "unset <path>"):
            self.assertIn(command, result.stdout)

    def test_commands_outside_worktree_fail_without_mutation(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_text("unchanged\n")
        bare = self.root / "bare.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)],
                       env=self.env, check=True)

        def snapshot(directory):
            return {str(p.relative_to(directory)): p.read_bytes() if p.is_file() else None
                    for p in directory.rglob("*")}

        for directory in (outside, bare):
            before = snapshot(directory)
            for arguments in (
                ("effective",),
                ("get", "workflow.enabled"),
                ("set", "workflow.enabled", "true"),
                ("unset", "workflow.enabled"),
            ):
                with self.subTest(directory=directory.name, arguments=arguments):
                    result = self.run_project(*arguments, cwd=directory)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr,
                                     "agent-project: current directory is not inside a Git worktree\n")
                    self.assertEqual(snapshot(directory), before)
                    self.assertFalse((directory / ".ai").exists())

    def test_nested_directory_uses_repository_root(self):
        nested = self.repo / "src" / "nested"
        nested.mkdir(parents=True)
        result = self.run_project("set", "workflow.enabled", "true", cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text()), {
            "schemaVersion": 1, "workflow": {"enabled": True},
        })
        self.assertEqual(self.run_project("get", "workflow.enabled", cwd=nested).stdout,
                         "true\n")
        result = self.run_project("effective", cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["workflow"]["enabled"])
        result = self.run_project("unset", "workflow.enabled", cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text()), {"schemaVersion": 1})
        self.assertFalse((nested / ".ai").exists())

    def test_missing_git_reports_error_without_traceback(self):
        empty_bin = self.root / "empty-bin"
        empty_bin.mkdir()
        self.env["PATH"] = str(empty_bin)
        result = self.run_project("set", "workflow.enabled", "true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("agent-project: cannot run git:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.config.parent.exists())

    def test_defaults_and_partial_overrides(self):
        result = self.run_project("effective")
        self.assertEqual(result.returncode, 0, result.stderr)
        defaults = json.loads(result.stdout)
        self.assertIs(defaults["workflow"]["enabled"], False)
        self.assertEqual(defaults["git"]["integration"]["mode"], "localMerge")

        self.write_config({"schemaVersion": 1, "git": {"commit": {"mode": "manual"}}})
        result = self.run_project("effective")
        self.assertEqual(result.returncode, 0, result.stderr)
        defaults["git"]["commit"]["mode"] = "manual"
        self.assertEqual(json.loads(result.stdout), defaults)
        self.assertEqual(self.run_project("get", "git.commit.mode").stdout, "manual\n")

    def test_workflow_disabled_by_default_and_can_be_enabled(self):
        self.assertEqual(self.run_project("get", "workflow.enabled").stdout, "false\n")

        result = self.run_project("set", "workflow.enabled", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_project("get", "workflow.enabled").stdout, "true\n")
        self.assertEqual(
            json.loads(self.config.read_text()),
            {"schemaVersion": 1, "workflow": {"enabled": True}},
        )

        result = self.run_project("unset", "workflow.enabled")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_project("get", "workflow.enabled").stdout, "false\n")
        self.assertEqual(json.loads(self.config.read_text()), {"schemaVersion": 1})

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

    def test_invalid_mutations_preserve_existing_overrides(self):
        self.write_config({
            "schemaVersion": 1,
            "workflow": {"enabled": True},
            "git": {"branch": {"mode": "current", "baseBranches": []}},
        })
        before = self.config.read_bytes()
        for arguments in (
            ("set", "workflow.enabled", "yes"),
            ("set", "git.integration.mode", "invalid"),
            ("set", "git.branch.baseBranches", '["main",1]'),
            ("set", "git.branch.baseBranches", '["main","main"]'),
            ("set", "git.branch.mode", "fromBase"),
            ("unset", "git.branch.mode"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_project(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.config.read_bytes(), before)
                self.assertEqual(list(self.config.parent.iterdir()), [self.config])

    def test_failed_atomic_replace_preserves_file_and_removes_temporary(self):
        self.write_config({"schemaVersion": 1})
        before = self.config.read_bytes()
        writer = runpy.run_path(str(PROJECT))["write_overrides"]

        def reject_replace(source, destination):
            self.assertEqual(source.parent, self.config.parent)
            self.assertEqual(destination, self.config)
            self.assertEqual(json.loads(source.read_text()), {
                "schemaVersion": 1, "workflow": {"enabled": True},
            })
            self.assertEqual(self.config.read_bytes(), before)
            raise OSError("replacement failed")

        errors = io.StringIO()
        with mock.patch.dict(writer.__globals__, {"config_path": lambda: self.config}):
            with mock.patch("os.replace", side_effect=reject_replace) as replace:
                with redirect_stderr(errors), self.assertRaises(SystemExit) as failure:
                    writer({"schemaVersion": 1, "workflow": {"enabled": True}})
        self.assertEqual(failure.exception.code, 1)
        replace.assert_called_once()
        self.assertIn("cannot write", errors.getvalue())
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(list(self.config.parent.iterdir()), [self.config])

    def test_config_directory_creation_failure_is_reported(self):
        self.config.parent.write_text("not a directory\n")
        result = self.run_project("set", "workflow.enabled", "true")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot write", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.config.parent.read_text(), "not a directory\n")

    def test_unset_without_configuration_does_not_create_file(self):
        result = self.run_project("unset", "workflow.enabled")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reason=not-overridden", result.stdout)
        self.assertFalse(self.config.parent.exists())

    def test_invalid_settings(self):
        for config, error in (
            ({}, "schemaVersion is required"),
            ({"schemaVersion": True}, "invalid type"),
            ({"schemaVersion": 1, "workflow": {"enabled": 1}}, "invalid type"),
            ({"schemaVersion": 1, "workflow": {"enabled": "true"}}, "invalid type"),
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
