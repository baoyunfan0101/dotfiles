from pathlib import Path
import subprocess
import tempfile
import unittest

from sandbox import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install" / "install.sh"
BEGIN = "<!-- BEGIN baoyunfan0101/dotfiles managed block -->"
END = "<!-- END baoyunfan0101/dotfiles managed block -->"


class ManagedBlockInstallTests(unittest.TestCase):
    AGENT = "codex"
    SOURCE = ROOT / "adapters" / "codex" / "AGENTS.md"
    HOME_VARIABLE = "CODEX_HOME"
    TARGET_NAME = "AGENTS.md"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = isolated_environment(self.root)
        self.target = Path(self.env[self.HOME_VARIABLE]) / self.TARGET_NAME

    def install(self, *options):
        return subprocess.run([str(INSTALLER), "--agents", self.AGENT, *options],
                              env=self.env, capture_output=True, text=True)

    def block(self):
        return f"{BEGIN}\n\n{self.SOURCE.read_text()}\n{END}\n"

    def assert_one_block(self, content):
        self.assertEqual(content.count(BEGIN), 1)
        self.assertEqual(content.count(END), 1)
        self.assertIn(self.block(), content)

    def test_shared_entry_survives_until_last_adapter_is_uninstalled(self):
        result = subprocess.run([str(INSTALLER), "--agents", "codex,claude"],
                                env=self.env, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        shared = Path(self.env["XDG_CONFIG_HOME"]) / "dotfiles" / "instructions.md"
        self.assertEqual(shared.read_text(), (ROOT / "instructions.md").read_text())
        for agent in ("codex", "claude"):
            result = subprocess.run([str(INSTALLER), "--agents", agent, "--uninstall"],
                                    env=self.env, cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(shared.exists(), agent != "claude")

    def test_update_repairs_old_layout_symlinks_using_existing_manifest(self):
        # A checkout update leaves old symlinks dangling until the installer repairs them.
        config = Path(self.env["XDG_CONFIG_HOME"])
        targets = (
            (Path(self.env["HOME"]) / ".local" / "bin" / "agent-project",
             ROOT / "workflow" / "bin" / "agent-project"),
            (config / "agent-workflow" / "instructions.md",
             ROOT / "workflow" / "instructions.md"),
        )
        manifest = Path(self.env["XDG_STATE_HOME"]) / "dotfiles" / "ai" / "common.manifest"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("".join(str(target) + "\n" for target, _ in targets))
        for target, _ in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(self.root / "removed-old-source")
        for mode in ("--symlink", "--copy"):
            result = self.install(mode, "--clean")
            self.assertEqual(result.returncode, 0, result.stderr)
            for target, source in targets:
                self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertEqual(target.is_symlink(), mode == "--symlink")

    def test_rules_install_outside_git_and_are_removed_with_shared_content(self):
        config = Path(self.env["XDG_CONFIG_HOME"]) / "dotfiles"
        filename = "explaining-unfamiliar-topics.md"
        for mode in ("--symlink", "--copy"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(INSTALLER), "--agents", self.AGENT, mode],
                                        env=self.env, cwd=self.root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((config / "rules" / filename).read_bytes(),
                                 (ROOT / "rules" / filename).read_bytes())
                self.assertEqual((config / "rules").is_symlink(), mode == "--symlink")
                shared = (config / "instructions.md").read_text()
                self.assertLess(shared.index("## Rules"), shared.index("## Workflow Loading"))
                self.assertNotIn(filename, (ROOT / "workflow" / "instructions.md").read_text())
                self.assertNotIn(filename, self.target.read_text())
                probe = Path(self.env["HOME"]) / ".local" / "libexec" / "agent-workflow-opt-in"
                result = subprocess.run([str(probe)], env=self.env, cwd=self.root,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.install("--uninstall").returncode, 0)
                self.assertFalse((config / "rules").exists())

    def test_new_file_contains_one_managed_block(self):
        result = self.install("--symlink")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(self.target.read_text(), self.block())

    def test_existing_personal_rules_are_preserved_even_with_force(self):
        personal = b"# Personal rules\n\nKeep this exact text."
        self.target.write_bytes(personal)

        result = self.install("--force", "--clean")
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self.target.read_bytes()
        self.assertTrue(content.startswith(personal + b"\n\n"))
        self.assert_one_block(content.decode())

    def test_update_replaces_only_block_and_is_idempotent(self):
        before = "personal-before\n\n"
        after = "\npersonal-after without final newline"
        self.target.write_text(f"{before}{BEGIN}\n\nold dotfiles content\n\n{END}\n{after}")

        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = f"{before}{self.block()}{after}"
        self.assertEqual(self.target.read_text(), expected)
        self.assert_one_block(expected)

        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_text(), expected)

    def test_malformed_markers_fail_closed_even_with_force(self):
        malformed = (
            f"personal\n{BEGIN}\nold\n",
            f"personal\n{END}\n",
            f"{BEGIN}\nold\n{END}\n{BEGIN}\nother\n{END}\n",
            f"{END}\n{BEGIN}\n",
        )
        for content in malformed:
            for options in ((), ("--force",)):
                with self.subTest(content=content, options=options):
                    self.target.write_text(content)
                    before = self.target.read_bytes()
                    result = self.install(*options)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"{self.TARGET_NAME} contains malformed dotfiles managed block markers",
                                  result.stderr)
                    self.assertEqual(self.target.read_bytes(), before)

    def test_uninstall_removes_only_managed_block(self):
        self.target.write_text("personal rules\n")
        self.assertEqual(self.install().returncode, 0)

        result = self.install("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.target.exists())
        self.assertTrue(self.target.read_text().startswith("personal rules\n"))
        self.assertNotIn(BEGIN, self.target.read_text())


class ClaudeManagedBlockInstallTests(ManagedBlockInstallTests):
    AGENT = "claude"
    SOURCE = ROOT / "adapters" / "claude" / "CLAUDE.md"
    HOME_VARIABLE = "CLAUDE_CONFIG_DIR"
    TARGET_NAME = "CLAUDE.md"
