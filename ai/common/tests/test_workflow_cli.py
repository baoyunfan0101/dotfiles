import os
from pathlib import Path
import re
import subprocess
import unittest
from sandbox import isolated_environment

from workflow_helpers import WorkflowTestCase, COMMON


class WorkflowCliTests(WorkflowTestCase):
    def test_command_help_and_dispatch(self):
        help_text = self.run_cli("--help").stdout
        self.assertEqual(set(re.findall(r"^  ([a-z][a-z-]*)$", help_text, re.MULTILINE)),
                         {"prepare", "bootstrap", "sync", "backup", "commit", "undo", "restore", "revert", "cherry-pick",
                          "rebase", "merge", "push", "branch", "remote", "pr"})
        for meaning in ("before editing", "after editing", "--message MESSAGE",
                        "-- PATH...", "outside commit", "current branch",
                        "local", "pull request", "--branch-name NAME",
                        "manual skips", "autoCommit stays local", "autoCommitAndPush publishes"):
            self.assertIn(meaning, help_text)
        for action in ("prepare", "bootstrap", "sync", "commit", "undo", "restore", "revert", "cherry-pick",
                       "rebase", "merge", "push"):
            self.assertIn("Usage:", self.run_cli(action, "--help").stdout)
            self.assertIn(f"[git] {action} error", self.run_cli(action, "--invalid", ok=False).stderr)
        self.assertIn("configured base branch", self.run_cli("merge", ok=False).stderr)
        pr_help = self.run_cli("pr", "--help").stdout
        self.assertEqual(set(re.findall(r"^  ([a-z]+)$", pr_help, re.MULTILINE)), {"submit", "merge"})
        branch_help = self.run_cli("branch", "--help").stdout
        self.assertEqual(set(re.findall(r"^  ([a-z]+)$", branch_help, re.MULTILINE)),
                         {"create", "switch", "rename", "delete"})
        for action in ("create", "switch", "rename", "delete"):
            self.assertIn("Usage:", self.run_cli("branch", action, "--help").stdout)
        for action in ("create", "list", "apply", "delete"):
            self.assertIn("Usage:", self.run_cli("backup", action, "--help").stdout)
        for command in ("start", "finish", "add", "stash", "pull", "checkout",
                        "switch", "reset"):
            self.assertIn("unknown command", self.run_cli(command, ok=False).stderr)
            self.assertNotIn(f"  git-workflow {command}\n", help_text)
        self.run_cli("pr", "unknown", ok=False)
        self.run_cli("branch", "unknown", ok=False)
        self.run_cli("pr", "submit", "--title", "No override", ok=False)

    def test_disabled_or_unconfigured_project_skips_workflow(self):
        path = self.repo / ".ai/project.json"
        path.unlink()

        for action, arguments in (
            ("prepare", ("--base", "main", "--branch-name", "feat/test")),
            ("bootstrap", ("--message", "Initial", "--all")),
            ("sync", ()),
            ("backup", ("create",)),
            ("backup", ("delete", "b-" + "0" * 40)),
            ("commit", ("--message", "Change", "--all")),
            ("undo", ()),
            ("push", ()),
            ("branch", ("create", "feat/disabled")),
            ("merge", ()),
            ("pr", ("submit",)),
            ("pr", ("merge",)),
        ):
            result = self.run_cli(action, *arguments)
            self.assertEqual(
                result.stdout,
                f"[git] {action + ' ' + arguments[0] if action in ('pr', 'branch', 'backup') else action} skip reason=workflow-disabled\n",
            )
        self.assertIn("backup not found", self.run_cli("backup", "apply",
                                                       "b-" + "0" * 40, ok=False).stderr)

    def test_command_executables(self):
        for action in ("prepare", "bootstrap", "sync", "backup", "commit", "undo", "restore", "revert", "cherry-pick",
                       "rebase", "merge", "push", "branch", "remote", "pr"):
            path = COMMON / "libexec/git-workflow" / action
            self.assertTrue(os.access(path, os.X_OK))
            self.assertEqual(path.read_text().splitlines()[0], "#!/usr/bin/env bash")
            self.assertIn("Usage:", self.run_cli("--help", executable=path).stdout)

    def check_install(self, mode):
        install_env = isolated_environment(self.root / "installed environment")
        install_env["PATH"] = self.env["PATH"]
        destination = Path(install_env["HOME"])
        self.assertIn(self.root, destination.parents)
        result = subprocess.run([str(COMMON.parent / "install.sh"), "--agents", "codex", mode],
                                env=install_env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifests = list(Path(install_env["XDG_STATE_HOME"]).glob("dotfiles/ai/*.manifest"))
        self.assertEqual({path.name for path in manifests}, {"common.manifest", "codex.manifest"})
        installed_agents = destination / ".codex/AGENTS.md"
        self.assertTrue(installed_agents.is_file())
        self.assertFalse(installed_agents.is_symlink())
        self.assertIn("<!-- BEGIN baoyunfan0101/dotfiles managed block -->",
                      installed_agents.read_text())
        for manifest in manifests:
            for target in manifest.read_text().splitlines():
                self.assertIn(destination, Path(target).parents)
                self.assertTrue(Path(target).exists())
        self.env = {**install_env, "PATH": f"{destination / '.local/bin'}:{install_env['PATH']}"}
        installed_project = destination / ".local/bin/agent-project"
        self.assertTrue(installed_project.exists())
        installed_probe = destination / ".local/libexec/agent-workflow-opt-in"
        self.assertTrue(os.access(installed_probe, os.X_OK))
        probe = subprocess.run([str(installed_probe)], cwd=self.repo, env=self.env,
                               capture_output=True, text=True)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertFalse((destination / ".local/bin/agent-project-settings").exists())
        self.assertIn(
            "agent-project set",
            self.run_cli("--help", executable=installed_project).stdout,
        )

        installed = destination / ".local/bin/git-workflow"
        for command in ("prepare", "bootstrap", "sync", "backup", "commit", "undo", "restore", "revert", "cherry-pick",
                        "rebase", "push", "branch", "remote", "merge", "pr"):
            self.assertTrue(os.access(destination / ".local/libexec/git-workflow" / command, os.X_OK))
        for command in ("start", "finish"):
            self.assertFalse((destination / ".local/libexec/git-workflow" / command).exists())
        for action in ("submit", "merge"):
            self.assertIn("Usage:", self.run_cli("pr", action, "--help", executable=installed).stdout)
        for action in ("create", "list", "apply", "delete"):
            self.assertIn("Usage:", self.run_cli("backup", action, "--help", executable=installed).stdout)
        self.assertTrue((destination / ".local/lib/git-workflow/pr-summary.py").is_file())
        self.assertTrue((destination / ".local/lib/git-workflow/rewrite.sh").is_file())
        for name in ("__init__.py", "schema.py", "storage.py", "exclude.py"):
            self.assertTrue((destination / ".local/lib/agent_project" / name).is_file())
        project_library = destination / ".local/lib/agent_project"
        help_env = {key: value for key, value in self.env.items()
                    if key != "PYTHONDONTWRITEBYTECODE"}
        result = subprocess.run([str(installed_project), "--help"], cwd=self.repo,
                                env=help_env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((project_library / "__pycache__").exists())
        self.assertIn("[git] prepare ok", self.run_cli("prepare", "--base", "main", "--branch-name", "feat/test", executable=installed).stdout)
        for action in ("connect", "reconnect", "disconnect"):
            self.assertIn("Usage:", self.run_cli("remote", action, "--help", executable=installed).stdout)
        self.run_cli("remote", "disconnect", executable=installed)
        self.run_cli("remote", "connect", str(self.remote), executable=installed)
        self.run_cli("remote", "reconnect", str(self.remote), executable=installed)
        self.change()
        self.run_cli("commit", "--message", "Installed", "--all", executable=installed)
        self.run_cli("push", executable=installed)
        self.run_cli("merge", executable=installed)
        for path in (destination / ".local/lib/git-workflow").glob("*.sh"):
            self.assertFalse(path.stat().st_mode & 0o111)

    def test_symlink_install(self):
        self.check_install("--symlink")

    def test_copy_install(self):
        self.check_install("--copy")


if __name__ == "__main__":
    unittest.main()
