from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
INSTRUCTIONS = ROOT / "ai/common/instructions.md"


def section(text, heading):
    return text.split(heading + "\n", 1)[1].split("\n## ", 1)[0]


class InstructionTests(unittest.TestCase):
    def setUp(self):
        self.instructions = INSTRUCTIONS.read_text()

    def test_adapter_loads_shared_instructions_only_after_opt_in(self):
        adapter = (ROOT / "ai/codex/AGENTS.md").read_text()
        self.assertLess(len(adapter), 800)
        for pattern in (
            r"At the start.*Git repository.*agent-workflow-opt-in.*once",
            r"Exit 0:.*read.*agent-workflow/instructions\.md.*follow",
            r"Exit 1:.*stop loading.*do not call.*agent-project.*git-workflow",
            r"Any other exit:.*report.*do not perform automatic Git workflow",
            r"explicit request.*project settings.*agent-project.*workflow is disabled",
        ):
            self.assertRegex(adapter, pattern)
        probe = (ROOT / "ai/common/libexec/agent-workflow-opt-in").read_text()
        for dependency in ("instructions.md", "agent-project", "git-workflow"):
            self.assertNotIn(dependency, probe)

    def test_project_configuration_requires_user_intent_and_on_demand_discovery(self):
        config = section(self.instructions, "## Project Configuration")
        for pattern in (
            r"Use `agent-project` only.*user explicitly asks.*inspect or change",
            r"agent-project --help.*agent-project schema \[path\]",
            r"Combine related changes.*one command",
            r"Do not read or edit.*\.ai/project\.json.*directly unless.*user explicitly requests the raw file or.*debugging.*agent-project",
        ):
            self.assertRegex(config, pattern)
        for reference in ("git.integration.mode", "git.commit.mode", "README"):
            self.assertNotIn(reference, self.instructions)

    def test_git_mutations_require_supported_workflow_commands(self):
        workflow = section(self.instructions, "## Workflow")
        self.assertRegex(workflow, r"Read-only Git commands may run directly")
        self.assertRegex(workflow, r"Use supported `git-workflow` commands for every Git mutation.*working tree, index, history, branches, or remote state")
        self.assertRegex(workflow, r"unsupported mutations.*stop and report.*capability gap.*never bypass.*raw Git")
        self.assertRegex(workflow, r"Discover command syntax.*git-workflow --help.*command-specific.*--help")
        self.assertRegex(workflow, r"prepare.*workflow-disabled.*stop using workflow commands")

    def test_development_requires_explicit_branch_continuation_intent(self):
        workflow = section(self.instructions, "## Workflow")
        for pattern in (
            r"empty repository.*git-workflow bootstrap.*before development",
            r"each independent task.*git-workflow prepare --branch-name <name>.*before editing",
            r"explicitly continue work assigned.*git-workflow prepare --continue",
            r"Neither the checked-out branch nor PR state establishes continuation intent",
            r"Explicit continuation may span multiple Task Specs and logical-subtask commits",
            r"prepare -> edit -> commit\*",
        ):
            self.assertRegex(workflow, pattern)

    def test_backups_require_explicit_recovery_and_deletion(self):
        backups = section(self.instructions, "### Backups").split("\n### ", 1)[0]
        for pattern in (
            r"backup create.*preserve selected changes before discarding",
            r"backup list.*inspect",
            r"backup apply <id>.*only for explicitly requested recovery",
            r"backup delete <id>.*only for explicitly requested deletion",
            r"prepare.*backs up and discards selected changes.*never automatically applies a backup",
        ):
            self.assertRegex(backups, pattern)

    def test_commit_push_and_delivery_authorizations_remain_distinct(self):
        workflow = section(self.instructions, "## Workflow")
        for pattern in (
            r"After each logical subtask.*git-workflow commit --message.*-- <paths>",
            r"--all.*only when every change belongs to that subtask",
            r"Shell enforces configured Commit and Push policies",
            r"--override-manual.*only for an explicitly requested commit or push",
            r"Each delivery action requires explicit user authorization",
            r"pr submit.*only when explicitly asked.*create, submit, or update a PR",
            r"pr merge.*only with separate explicit authorization.*merge the PR.*approve",
            r"git-workflow merge.*only with separate explicit authorization for local integration",
            r"Task Spec or commit completion does not authorize PR submission or integration",
            r"Never infer merge authorization from PR submission, CI success, or task completion",
        ):
            self.assertRegex(workflow, pattern)

    def test_authoring_scopes_and_accurate_reporting_are_preserved(self):
        authoring = section(self.instructions, "## Authoring")
        for pattern in (
            r"user's latest language.*responses unless requested otherwise",
            r"ASCII in project files unless requested otherwise",
            r"Preserve existing comments while their code or section remains unless requested otherwise",
            r"Do not create backups directly.*configured workflows",
            r"Markdown containing fenced code blocks.*outer.*~~~md",
        ):
            self.assertRegex(authoring, pattern)
        self.assertRegex(self.instructions, r"Report failures and partial successes accurately")
        self.assertRegex(self.instructions, r"Surface each.*\[git\].*result verbatim exactly once")


if __name__ == "__main__":
    unittest.main()
