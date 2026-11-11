from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstructionTests(unittest.TestCase):
    def test_adapters_reference_the_shared_instruction_paths(self):
        shared = (ROOT / "instructions.md").read_text()
        for path in (Path("adapters", "codex", "AGENTS.md"),
                     Path("adapters", "claude", "CLAUDE.md")):
            adapter = (ROOT / path).read_text()
            with self.subTest(adapter=path):
                self.assertIn("${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/instructions.md", adapter)
        self.assertIn("`${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/rules/`", shared)
        self.assertIn("`git-workflow.md`", shared)
        self.assertTrue((ROOT / "rules" / "git-workflow.md").is_file())

    def test_readme_links_to_workflow_quick_start(self):
        readme = (ROOT / "README.md").read_text()
        guide = Path("tools", "README.md")
        self.assertIn(f"({guide.as_posix()}#quick-start)", readme)
        self.assertIn("## Quick Start", (ROOT / guide).read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
