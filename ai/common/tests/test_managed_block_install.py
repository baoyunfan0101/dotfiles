from pathlib import Path
import subprocess
import tempfile
import unittest

from sandbox import isolated_environment


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "ai/codex/AGENTS.md"
INSTALLER = ROOT / "ai/install.sh"
BEGIN = "<!-- BEGIN baoyunfan0101/dotfiles managed block -->"
END = "<!-- END baoyunfan0101/dotfiles managed block -->"


class ManagedBlockInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = isolated_environment(self.root)
        self.target = Path(self.env["CODEX_HOME"]) / "AGENTS.md"

    def install(self, *options):
        return subprocess.run([str(INSTALLER), "--agents", "codex", *options],
                              env=self.env, capture_output=True, text=True)

    def block(self):
        return f"{BEGIN}\n\n{SOURCE.read_text()}\n{END}\n"

    def assert_one_block(self, content):
        self.assertEqual(content.count(BEGIN), 1)
        self.assertEqual(content.count(END), 1)
        self.assertIn(self.block(), content)

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
                    self.assertIn("AGENTS.md contains malformed dotfiles managed block markers",
                                  result.stderr)
                    self.assertEqual(self.target.read_bytes(), before)

    def test_legacy_manifest_does_not_clean_personal_rules(self):
        personal = "legacy content stays\n"
        self.target.write_text(personal)
        manifest = Path(self.env["XDG_STATE_HOME"]) / "dotfiles/ai/codex.manifest"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(f"{self.target}\n")

        result = self.install("--force", "--clean")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.target.read_text().startswith(personal))
        self.assert_one_block(self.target.read_text())
        self.assertNotIn(str(self.target), manifest.read_text())

    def test_uninstall_removes_only_managed_block(self):
        self.target.write_text("personal rules\n")
        self.assertEqual(self.install().returncode, 0)

        result = self.install("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.target.exists())
        self.assertTrue(self.target.read_text().startswith("personal rules\n"))
        self.assertNotIn(BEGIN, self.target.read_text())
