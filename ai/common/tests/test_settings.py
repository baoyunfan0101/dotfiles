from contextlib import redirect_stderr
from copy import deepcopy
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
        self.defaults = runpy.run_path(str(PROJECT))["DEFAULT_SETTINGS"]

    def run_project(self, *arguments, cwd=None):
        return subprocess.run(
            [sys.executable, str(PROJECT), *arguments],
            cwd=self.repo if cwd is None else cwd,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def complete(self, changes):
        result = deepcopy(self.defaults)

        def update(target, source):
            for key, value in source.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    update(target[key], value)
                else:
                    target[key] = deepcopy(value)

        update(result, changes)
        return result

    def write_config(self, config, complete=True):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps(self.complete(config) if complete else config))

    def test_shared_instructions_describe_on_demand_configuration(self):
        instructions = (PROJECT.parents[1] / "instructions.md").read_text()
        self.assertIn("only when the user explicitly asks", instructions)
        self.assertIn("agent-project schema [path]", instructions)
        self.assertIn("agent-project --help", instructions)
        self.assertIn("git-workflow --help", instructions)
        self.assertIn("start -> edit -> commit* -> finish", instructions)
        self.assertIn("Do not read or edit `.ai/project.json` directly", instructions)
        self.assertNotIn("git.integration.mode", instructions)
        self.assertNotIn("README", instructions)

    def test_help(self):
        result = self.run_project("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("schema [path]", "effective", "get <path>",
                        "set <path> <value>", "unset <path>"):
            self.assertIn(command, result.stdout)
        for meaning in ("types, defaults, allowed values, and constraints",
                        "complete configuration", "one current setting value",
                        "atomically", "complete", "Reset one or more values",
                        "remain explicitly present"):
            self.assertIn(meaning, result.stdout)

    def test_schema_discovers_supported_settings(self):
        result = self.run_project("schema")
        self.assertEqual(result.returncode, 0, result.stderr)
        schema = json.loads(result.stdout)
        model = runpy.run_path(str(PROJECT))
        paths = list(model["configurable_paths"]())
        self.assertEqual(list(schema), paths)
        self.assertIn("schemaVersion", list(model["leaf_paths"](model["SCHEMA"])))
        self.assertNotIn("schemaVersion", paths)

        type_names = model["SCHEMA_TYPE_NAMES"]
        for path in paths:
            with self.subTest(path=path):
                entry = schema[path]
                self.assertEqual(entry["default"], model["get_value"](
                    model["DEFAULT_SETTINGS"], path))
                expected = model["schema_type"](path)
                self.assertEqual(entry["type"],
                                 "enum" if path in model["ALLOWED_VALUES"]
                                 else type_names[expected])
                if path in model["ALLOWED_VALUES"]:
                    self.assertEqual(entry["values"],
                                     sorted(model["ALLOWED_VALUES"][path]))
                self.assertIn("description", entry)

    def test_schema_path_and_unknown_path(self):
        result = self.run_project("schema", "git.integration.mode")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "type": "enum",
            "default": "localMerge",
            "values": ["localMerge", "pullRequest"],
            "description": "Integration strategy used by finish.",
        })

        result = self.run_project("schema", "git.unknown")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown setting: git.unknown", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_schema_ignores_invalid_config_and_does_not_mutate(self):
        self.write_config({"invalid": True}, complete=False)
        before = self.config.read_bytes()
        result = self.run_project("schema")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workflow.enabled", json.loads(result.stdout))
        self.assertEqual(self.config.read_bytes(), before)

        result = self.run_project("schema", "workflow.enabled")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["default"], False)
        self.assertEqual(self.config.read_bytes(), before)

        self.config.unlink()
        self.config.parent.rmdir()
        result = self.run_project("schema")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.config.parent.exists())

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
                ("schema",),
                ("schema", "workflow.enabled"),
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
        self.assertEqual(json.loads(self.config.read_text()), self.complete({
            "schemaVersion": 1, "workflow": {"enabled": True},
        }))
        self.assertEqual(self.run_project("get", "workflow.enabled", cwd=nested).stdout,
                         "true\n")
        result = self.run_project("effective", cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["workflow"]["enabled"])
        result = self.run_project("unset", "workflow.enabled", cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text()), self.defaults)
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

    def test_defaults_and_complete_project_configuration(self):
        result = self.run_project("effective")
        self.assertEqual(result.returncode, 0, result.stderr)
        defaults = json.loads(result.stdout)
        self.assertIs(defaults["workflow"]["enabled"], False)
        self.assertEqual(defaults["git"]["integration"]["mode"], "localMerge")

        self.write_config({"schemaVersion": 1, "git": {"commit": {"mode": "manual"}}})
        result = self.run_project("effective")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.complete({
            "git": {"commit": {"mode": "manual"}},
        }))
        self.assertEqual(self.run_project("get", "git.commit.mode").stdout, "manual\n")

    def test_created_project_contains_every_schema_leaf(self):
        result = self.run_project("set", "workflow.enabled", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads(self.config.read_text())
        model = runpy.run_path(str(PROJECT))
        for path in model["leaf_paths"](model["SCHEMA"]):
            with self.subTest(path=path):
                model["get_value"](settings, path)
        self.assertEqual(settings["schemaVersion"], model["SCHEMA_VERSION"])

    def test_existing_project_ignores_changed_global_defaults(self):
        self.write_config({"workflow": {"enabled": True}})
        stored = json.loads(self.config.read_text())
        model = runpy.run_path(str(PROJECT))
        loader = model["load_settings"]
        changed_defaults = deepcopy(self.defaults)
        changed_defaults["git"]["integration"]["mode"] = "pullRequest"
        with mock.patch.dict(loader.__globals__, {
            "config_path": lambda: self.config,
            "DEFAULT_SETTINGS": changed_defaults,
        }):
            self.assertEqual(loader(), stored)

    def test_partial_project_is_rejected_by_reads_and_mutations(self):
        self.write_config({"schemaVersion": 1, "workflow": {"enabled": True}},
                          complete=False)
        before = self.config.read_bytes()
        for arguments in (
            ("effective",),
            ("get", "workflow.enabled"),
            ("set", "git.commit.mode", "manual"),
            ("unset", "workflow.enabled"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_project(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing setting: git", result.stderr)
                self.assertEqual(self.config.read_bytes(), before)

    def test_repository_configuration_is_complete(self):
        model = runpy.run_path(str(PROJECT))
        settings = json.loads((PROJECT.parents[3] / ".ai/project.json").read_text())
        model["validate_structure"](settings, model["SCHEMA"])
        model["validate_values"](settings)
        self.assertEqual(settings["schemaVersion"], 1)

    def test_workflow_disabled_by_default_and_can_be_enabled(self):
        self.assertEqual(self.run_project("get", "workflow.enabled").stdout, "false\n")

        result = self.run_project("set", "workflow.enabled", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_project("get", "workflow.enabled").stdout, "true\n")
        self.assertEqual(
            json.loads(self.config.read_text()),
            self.complete({"workflow": {"enabled": True}}),
        )

        result = self.run_project("unset", "workflow.enabled")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_project("get", "workflow.enabled").stdout, "false\n")
        self.assertEqual(json.loads(self.config.read_text()), self.defaults)

    def test_set_creates_complete_config_and_unset_restores_default(self):
        result = self.run_project("set", "git.commit.mode", "manual")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(self.config.read_text()),
            self.complete({"git": {"commit": {"mode": "manual"}}}),
        )
        self.assertEqual(self.run_project("get", "git.commit.mode").stdout, "manual\n")

        result = self.run_project("unset", "git.commit.mode")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text()), self.defaults)
        self.assertEqual(self.run_project("get", "git.commit.mode").stdout, "automatic\n")

        result = self.run_project("unset", "git.commit.mode")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skip", result.stdout)

    def test_batch_set_creates_one_complete_config(self):
        result = self.run_project(
            "set",
            "workflow.enabled", "true",
            "git.integration.mode", "pullRequest",
            "git.commit.mode", "manual",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[project] set ok count=3\n")
        self.assertEqual(json.loads(self.config.read_text()), self.complete({
            "schemaVersion": 1,
            "workflow": {"enabled": True},
            "git": {
                "integration": {"mode": "pullRequest"},
                "commit": {"mode": "manual"},
            },
        }))
        self.assertEqual(list(self.config.parent.iterdir()), [self.config])

    def test_batch_set_preserves_unrelated_values(self):
        self.write_config({
            "schemaVersion": 1,
            "workflow": {"enabled": False},
            "git": {"sync": {"mode": "none"}},
        })
        result = self.run_project(
            "set", "workflow.enabled", "true",
            "git.integration.mode", "pullRequest",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text()), self.complete({
            "schemaVersion": 1,
            "workflow": {"enabled": True},
            "git": {
                "sync": {"mode": "none"},
                "integration": {"mode": "pullRequest"},
            },
        }))

    def test_batch_set_invalid_first_middle_and_final_leave_file_unchanged(self):
        self.write_config({
            "schemaVersion": 1,
            "workflow": {"enabled": True},
            "git": {
                "commit": {"mode": "manual"},
                "branch": {"mode": "current", "baseBranches": []},
            },
        })
        before = self.config.read_bytes()
        cases = (
            (("unknown.setting", "x", "git.commit.mode", "automatic"),
             "unknown setting"),
            (("workflow.enabled", "false", "git.integration.mode", "invalid"),
             "must be one of"),
            (("git.branch.mode", "fromBase", "workflow.enabled", "false"),
             "must not be empty"),
        )
        for arguments, error in cases:
            with self.subTest(arguments=arguments):
                result = self.run_project("set", *arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertEqual(self.config.read_bytes(), before)
                self.assertEqual(list(self.config.parent.iterdir()), [self.config])

    def test_invalid_batch_set_does_not_create_file(self):
        result = self.run_project(
            "set", "workflow.enabled", "true",
            "git.integration.mode", "invalid",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.config.parent.exists())

    def test_batch_unset_restores_defaults_and_preserves_unrelated_values(self):
        self.write_config({
            "schemaVersion": 1,
            "workflow": {"enabled": True},
            "git": {
                "integration": {"mode": "pullRequest", "mergeMethod": "squash"},
                "commit": {"mode": "manual"},
            },
        })
        result = self.run_project(
            "unset", "git.integration.mode", "git.integration.mergeMethod",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[project] unset ok count=2\n")
        self.assertEqual(json.loads(self.config.read_text()), self.complete({
            "schemaVersion": 1,
            "workflow": {"enabled": True},
            "git": {"commit": {"mode": "manual"}},
        }))

    def test_batch_unset_unknown_path_or_invalid_result_preserves_file(self):
        self.write_config({
            "schemaVersion": 1,
            "workflow": {"enabled": True},
            "git": {"branch": {"mode": "current", "baseBranches": []}},
        })
        before = self.config.read_bytes()
        for arguments, error in (
            (("git.branch.mode", "git.unknown"), "unknown setting"),
            (("git.branch.mode", "workflow.enabled"), "must not be empty"),
            (("schemaVersion", "workflow.enabled"), "unknown setting"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_project("unset", *arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertEqual(self.config.read_bytes(), before)

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

    def test_schema_version_is_not_a_project_setting(self):
        self.write_config({"schemaVersion": 1})
        before = self.config.read_bytes()
        for arguments in (
            ("schema", "schemaVersion"),
            ("get", "schemaVersion"),
            ("set", "schemaVersion", "1"),
            ("unset", "schemaVersion"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_project(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unknown setting: schemaVersion", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.config.read_bytes(), before)

    def test_setting_mutations_manage_schema_version_internally(self):
        result = self.run_project("set", "workflow.enabled", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text()), self.complete({
            "schemaVersion": 1,
            "workflow": {"enabled": True},
        }))
        effective = json.loads(self.run_project("effective").stdout)
        self.assertEqual(effective["schemaVersion"], 1)

    def test_invalid_mutations_preserve_existing_settings(self):
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
        writer = runpy.run_path(str(PROJECT))["write_settings"]

        def reject_replace(source, destination):
            self.assertEqual(source.parent, self.config.parent)
            self.assertEqual(destination, self.config)
            self.assertEqual(json.loads(source.read_text()), self.complete({
                "schemaVersion": 1, "workflow": {"enabled": True},
            }))
            self.assertEqual(self.config.read_bytes(), before)
            raise OSError("replacement failed")

        errors = io.StringIO()
        with mock.patch.dict(writer.__globals__, {"config_path": lambda: self.config}):
            with mock.patch("os.replace", side_effect=reject_replace) as replace:
                with redirect_stderr(errors), self.assertRaises(SystemExit) as failure:
                    writer(self.complete({"workflow": {"enabled": True}}))
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
        self.assertIn("reason=already-default", result.stdout)
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
            ({"schemaVersion": 1, "workflow": {"enabled": True}}, "missing setting: git"),
        ):
            with self.subTest(config=config):
                raw = not config or "unknown" in config or (
                    "git" not in config and config.get("workflow", {}).get("enabled") is True
                )
                self.write_config(config, complete=not raw)
                result = self.run_project("effective")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)


if __name__ == "__main__":
    unittest.main()
