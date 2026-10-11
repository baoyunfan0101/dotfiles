import json
from pathlib import Path
import shutil
import subprocess
import unittest

from workflow_helpers import WorkflowTestCase, COMMON


class WorkflowDeliveryTests(WorkflowTestCase):
    def open_test_pr(self, commit_mode="autoCommitAndPush"):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"},
                           commit={"mode": commit_mode})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--stay")
        return Path(self.env["GH_STATE"])

    def existing_branch(self, mode="pullRequest", bases=("main",)):
        self.stub_gh()
        self.save_settings(integration={"mode": mode}, branch={"baseBranches": list(bases)})
        self.git("push", "-q", "origin", "main")
        for base in bases:
            if base != "main":
                self.git("branch", base)
                self.git("push", "-q", "origin", base)
        self.git("switch", "-c", "fix/manual")
        result = self.run_cli("prepare", "--continue")
        self.assertIn("created=false", result.stdout)
        self.assertNotIn("base=", result.stdout)
        self.change()
        self.commit()
        self.run_cli("push")

    def test_existing_current_branch_pr_delivery(self):
        self.existing_branch()
        self.run_cli("pr", "submit", "--stay")
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["base"], "main")
        self.run_cli("pr", "merge")
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_pr_submit_returns_to_actual_base_and_stay_retains_branch(self):
        self.existing_branch(bases=("main", "develop"))
        base_head = self.git("rev-parse", "develop")
        working_head = self.git("rev-parse", "HEAD")

        result = self.run_cli("pr", "submit", "--base", "develop")

        self.assertIn("current=develop", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "develop")
        self.assertEqual(self.git("rev-parse", "HEAD"), base_head)
        self.assertEqual(self.git("rev-parse", "fix/manual"), working_head)
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["state"], "OPEN")
        self.git("switch", "fix/manual")
        result = self.run_cli("pr", "submit", "--stay")
        self.assertIn("current=fix/manual", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "fix/manual")
        self.run_cli("pr", "submit")
        self.assertEqual(self.git("branch", "--show-current"), "develop")
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in self.gh_calls()), 1)

    def test_pr_submit_publishes_independently_of_manual_commit_mode(self):
        self.stub_gh()
        self.save_settings(commit={"mode": "manual"}, integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.git("switch", "-c", "feat/manual-submit")
        self.change()
        self.run_cli("commit", "--override-manual", "--message", "Manual change", "--", "tracked")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/manual-submit"), "")

        result = self.run_cli("pr", "submit")

        self.assertIn("pr submit ok", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertNotEqual(self.git("ls-remote", "--heads", "origin", "feat/manual-submit"), "")

    def test_pr_submit_failure_and_switch_failure_preserve_working_branch(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.env["GH_FAIL"] = "pr create"
        self.run_cli("pr", "submit", ok=False)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.env.pop("GH_FAIL")
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == checkout && "$2" == -q && "$3" == main ]]; then exit 1; fi\n'
                            f'exec "{git_executable}" "$@"\n')
        git_stub.chmod(0o755)

        result = self.run_cli("pr", "submit", ok=False)

        self.assertIn("PR submitted", result.stderr)
        self.assertIn("could not switch to base main", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["state"], "OPEN")

    def test_current_branch_local_delivery(self):
        self.existing_branch(mode="localMerge")
        self.run_cli("merge")
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_multiple_local_bases_require_explicit_base(self):
        self.existing_branch(mode="localMerge", bases=("main", "develop"))
        before = self.git_snapshot()
        self.assertIn("ambiguous", self.run_cli("merge", ok=False).stderr)
        self.assertEqual(before, self.git_snapshot())
        self.run_cli("merge", "--base", "develop")
        self.assertEqual(self.git("branch", "--show-current"), "develop")

    def test_local_base_sync_failure_keeps_current_branch(self):
        self.existing_branch(mode="localMerge")
        self.save_settings(sync={"mode": "update", "updateMethod": "ffOnly"})
        original = self.git("rev-parse", "HEAD")
        local_base = self.git("commit-tree", "main^{tree}", "-p", "main", "-m", "Local base advance")
        self.git("update-ref", "refs/heads/main", local_base)
        remote_base = self.git("--git-dir", str(self.remote), "commit-tree", "main^{tree}",
                               "-p", "main", "-m", "Remote base advance")
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/main", remote_base)
        self.assertIn("base sync failed", self.run_cli("merge", ok=False).stderr)
        self.assertEqual(self.git("branch", "--show-current"), "fix/manual")
        self.assertEqual(self.git("rev-parse", "HEAD"), original)
        self.assertEqual(self.git("rev-parse", "main"), local_base)

    def test_local_base_conflicting_sync_restores_working_branch(self):
        for method in ("rebase", "merge"):
            with self.subTest(method=method):
                self.save_settings(sync={"mode": "update", "updateMethod": method})
                self.git("push", "-q", "origin", "main")
                self.git("switch", "-c", f"fix/sync-{method}")
                (self.repo / "feature").write_text("working change\n")
                self.git("add", "feature")
                self.git("commit", "-qm", "Working change")
                working_head = self.git("rev-parse", "HEAD")
                self.git("switch", "main")
                base_head, remote_base = self.diverge_main_with_conflict()
                self.git("switch", f"fix/sync-{method}")

                result = self.run_cli("merge", ok=False)
                self.assertIn("base sync failed", result.stderr)
                self.assertEqual(self.git("branch", "--show-current"), f"fix/sync-{method}")
                self.assertEqual(self.git("rev-parse", "HEAD"), working_head)
                self.assertEqual(self.git("rev-parse", "main"), base_head)
                self.assertEqual(self.git("ls-remote", "--heads", "origin", "main"), remote_base)
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assert_no_in_progress_git_operation()
                self.assertTrue(self.git("branch", "--list", f"fix/sync-{method}"))
                self.git("switch", "main")
                self.git("reset", "--hard", "-q", "origin/main")

    def test_pr_base_resolution_and_disambiguation(self):
        self.existing_branch(bases=("main", "develop"))
        before = self.git_snapshot()
        self.assertIn("ambiguous", self.run_cli("pr", "submit", ok=False).stderr)
        self.assertEqual(before, self.git_snapshot())
        self.run_cli("pr", "submit", "--base", "develop", "--stay")
        self.run_cli("pr", "submit", "--stay")
        state_path = Path(self.env["GH_STATE"])
        self.assertEqual(json.loads(state_path.read_text())["base"], "develop")
        self.env["GH_EXTRA_PRS"] = json.dumps([{"url": "https://example.invalid/pr/2", "baseRefName": "main"}])
        for action in ("submit", "merge"):
            before = self.git_snapshot()
            self.assertIn("ambiguous", self.run_cli("pr", action, ok=False).stderr)
            self.assertEqual(before, self.git_snapshot())
        self.run_cli("pr", "submit", "--base=develop", "--stay")
        self.run_cli("pr", "merge", "--base", "develop")
        self.assertEqual(self.git("branch", "--show-current"), "develop")
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in self.gh_calls()), 1)

    def test_existing_pr_base_overrides_multiple_configured_defaults(self):
        self.existing_branch(bases=("main", "develop"))
        self.run_cli("pr", "submit", "--base", "develop", "--stay")
        self.run_cli("pr", "merge")
        self.assertEqual(self.git("branch", "--show-current"), "develop")

    def test_unconfigured_pr_base_fails_without_mutation(self):
        self.existing_branch()
        self.env["GH_EXTRA_PRS"] = json.dumps([{"url": "https://example.invalid/pr/2", "baseRefName": "outside"}])
        for action in ("submit", "merge"):
            before = self.git_snapshot()
            self.assertIn("PR base is not configured", self.run_cli("pr", action, ok=False).stderr)
            self.assertEqual(before, self.git_snapshot())
        self.run_cli("pr", "submit", "--base", "main")
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["base"], "main")

    def test_invalid_explicit_bases_fail_before_mutation(self):
        self.existing_branch()
        for action in ("submit", "merge"):
            before = self.git_snapshot()
            self.assertIn("base is not configured", self.run_cli("pr", action, "--base", "outside", ok=False).stderr)
            self.assertEqual(before, self.git_snapshot())
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "edit"])
                             for call in self.gh_calls()))
        self.save_settings(integration={"mode": "localMerge"})
        before = self.git_snapshot()
        self.assertIn("base is not configured", self.run_cli("merge", "--base", "outside", ok=False).stderr)
        self.assertEqual(before, self.git_snapshot())

    def test_all_configured_base_branches_reject_delivery(self):
        for mode, actions in (("localMerge", [("merge",)]),
                              ("pullRequest", [("pr", "submit"), ("pr", "merge")])):
            self.save_settings(integration={"mode": mode}, branch={"baseBranches": ["main", "develop"]})
            for action in actions:
                before = self.git_snapshot()
                self.assertIn("configured base branch", self.run_cli(*action, "--base", "develop", ok=False).stderr)
                self.assertEqual(before, self.git_snapshot())

    def merge_local(self, method, cleanup):
        self.save_settings(integration={"mergeMethod": method}, branch={"deleteAfterIntegration": cleanup})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        result = self.run_cli("merge", "--message", "Deliver")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Deliver")
        self.assertIn(f"method={method}", result.stdout)
        self.assertIn(f"deleted={'local-and-remote' if cleanup else 'false'}", result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/main"))
        self.assertEqual((self.repo / "tracked").read_text(), "changed\n")
        self.assertEqual(len(self.git("rev-list", "--parents", "-n", "1", "HEAD").split()),
                         3 if method == "mergeCommit" else 2)
        self.assertEqual(bool(self.git("ls-remote", "--heads", "origin", "feat/test")), not cleanup)
        self.assertEqual(bool(self.git("branch", "--list", "feat/test")), not cleanup)

    def test_merge_merge_commit(self):
        self.merge_local("mergeCommit", False)

    def test_merge_merge_cleanup(self):
        self.merge_local("mergeCommit", True)

    def test_merge_squash(self):
        self.merge_local("squash", False)

    def test_merge_squash_cleanup(self):
        self.merge_local("squash", True)

    def test_local_integration_conflicts_restore_both_branches(self):
        for method in ("mergeCommit", "squash"):
            with self.subTest(method=method):
                self.save_settings(integration={"mergeMethod": method})
                self.git("push", "-q", "origin", "main")
                branch_name = f"feat/test-{method}"
                self.run_cli("prepare", "--base", "main", "--branch-name", branch_name)
                self.change()
                self.commit()
                working_head = self.git("rev-parse", "HEAD")
                self.git("switch", "main")
                (self.repo / "tracked").write_text("base change\n")
                self.git("add", "tracked")
                self.git("commit", "-qm", "Conflicting base change")
                base_head = self.git("rev-parse", "HEAD")
                remote_base = self.git("ls-remote", "--heads", "origin", "main")
                self.git("switch", branch_name)

                result = self.run_cli("merge", ok=False)
                self.assertIn("local integration failed", result.stderr)
                self.assertEqual(self.git("branch", "--show-current"), branch_name)
                self.assertEqual(self.git("rev-parse", "HEAD"), working_head)
                self.assertEqual(self.git("rev-parse", "main"), base_head)
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assertEqual(self.git("ls-files", "--unmerged"), "")
                self.assertEqual(self.git("ls-remote", "--heads", "origin", "main"), remote_base)
                self.assertEqual(self.git("branch", "--list", branch_name), f"* {branch_name}")
                self.git("switch", "main")
                self.git("reset", "--hard", "-q", "origin/main")
                self.git("branch", "-D", branch_name)

    def test_merge_invalid_state(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.assertIn("working tree must be clean", self.run_cli("merge", "--message", "Deliver", ok=False).stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_pull_request_create_and_reuse(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        for subject in ("Commit A", "Commit B", "Commit C"):
            self.run_cli("prepare", "--continue")
            (self.repo / "tracked").write_text(subject)
            self.run_cli("commit", "--message", subject, "--", "tracked")
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "edit"])
                             for call in self.gh_calls()))
        self.assertIn("url=https://github.com/owner/dotfiles/pull/1",
                      self.run_cli("pr", "submit", "--stay").stdout)
        state_path = Path(self.env["GH_STATE"])
        state = json.loads(state_path.read_text())
        self.assertEqual(state["title"], "feat/test")
        self.assertEqual(state["body"], "<!-- git-workflow:summary:start -->\n"
                         "## Summary\n\n- Commit A\n- Commit B\n- Commit C\n"
                         "<!-- git-workflow:summary:end -->")
        self.run_cli("pr", "submit", "--stay")
        self.change("other")
        self.run_cli("commit", "--message", "Commit D", "--", "other")
        state = json.loads(state_path.read_text())
        self.assertIn("- Commit D", state["body"])
        self.run_cli("pr", "submit", "--stay")
        state = json.loads(state_path.read_text())
        self.assertEqual(state["body"], "<!-- git-workflow:summary:start -->\n"
                         "## Summary\n\n- Commit A\n- Commit B\n- Commit C\n- Commit D\n"
                         "<!-- git-workflow:summary:end -->")
        calls = self.gh_calls()
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in calls), 1)
        self.assertEqual(sum(call[:2] == ["pr", "edit"] for call in calls), 1)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in calls))
        for call in calls:
            if call[:2] == ["pr", "list"]:
                self.assertEqual(call[2:6], ["--state", "open", "--head", "feat/test"])
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_auto_push_updates_complete_summary_and_preserves_manual_content(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["title"] = "Human title"
        state["body"] += "\n\n## Notes\n\nKeep this note.\n"
        state_path.write_text(json.dumps(state))
        for subject in ("Second", "Third"):
            (self.repo / subject).write_text(subject + "\n")
            result = self.run_cli("commit", "--message", subject, "--", subject)
            self.assertIn("pushed=true pr=updated", result.stdout)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["title"], "Human title")
        self.assertIn("- Change\n- Second\n- Third", state["body"])
        self.assertTrue(state["body"].endswith("\n\n## Notes\n\nKeep this note.\n"))
        self.assertEqual(state["body"].count("git-workflow:summary:start"), 1)
        edit_count = sum(call[:2] == ["pr", "edit"] for call in self.gh_calls())

        result = self.run_cli("push")

        self.assertIn("pr=unchanged", result.stdout)
        self.assertEqual(sum(call[:2] == ["pr", "edit"] for call in self.gh_calls()), edit_count)

    def test_explicit_push_updates_summary_after_local_commit(self):
        state_path = self.open_test_pr("autoCommit")
        (self.repo / "second").write_text("second\n")
        result = self.run_cli("commit", "--message", "Second", "--", "second")
        self.assertIn("pushed=false", result.stdout)
        self.assertNotIn("- Second", json.loads(state_path.read_text())["body"])

        pushed = self.run_cli("push")

        self.assertIn("pr=updated", pushed.stdout)
        self.assertIn("- Change\n- Second", json.loads(state_path.read_text())["body"])

    def test_explicit_rewrite_push_updates_summary_after_local_amend(self):
        state_path = self.open_test_pr("autoCommit")
        state = json.loads(state_path.read_text())
        state["body"] += "\n\n## Notes\n\nKeep this note.\n"
        state_path.write_text(json.dumps(state))
        old_remote = self.git("rev-parse", "origin/feat/test")
        (self.repo / "tracked").write_text("amended\n")

        amended = self.run_cli("commit", "--amend", "--message", "Revised", "--all")
        self.assertIn("pushed=false", amended.stdout)
        self.assertEqual(self.git("rev-parse", "origin/feat/test"), old_remote)
        self.assertIn("- Change", json.loads(state_path.read_text())["body"])

        pushed = self.run_cli("push", "--rewrite")
        body = json.loads(state_path.read_text())["body"]
        self.assertIn("pr=updated", pushed.stdout)
        self.assertIn("- Revised", body)
        self.assertNotIn("- Change", body)
        self.assertTrue(body.endswith("\n\n## Notes\n\nKeep this note.\n"))
        self.assertEqual(self.git("rev-parse", "origin/feat/test"),
                         self.git("rev-parse", "HEAD"))

    def test_undo_published_commit_updates_pr_summary_and_preserves_manual_content(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["title"] = "Human title"
        state["body"] += "\n\n## Notes\n\nKeep this note.\n"
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")
        self.run_cli("commit", "--message", "Second", "--", "second")
        self.assertIn("- Second", json.loads(state_path.read_text())["body"])
        previous_calls = len(self.gh_calls())

        result = self.run_cli("undo")

        state = json.loads(state_path.read_text())
        self.assertIn("remote=updated pr=updated", result.stdout)
        self.assertEqual(state["title"], "Human title")
        self.assertIn("- Change", state["body"])
        self.assertNotIn("- Second", state["body"])
        self.assertTrue(state["body"].endswith("\n\n## Notes\n\nKeep this note.\n"))
        self.assertEqual(self.git("status", "--porcelain"), "?? second")
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "close"], ["pr", "merge"])
                             for call in self.gh_calls()[previous_calls:]))

    def test_undo_auto_commit_rewrite_push_updates_pr_summary(self):
        state_path = self.open_test_pr("autoCommit")
        (self.repo / "second").write_text("second\n")
        self.run_cli("commit", "--message", "Second", "--", "second")
        self.run_cli("push")
        self.assertIn("- Second", json.loads(state_path.read_text())["body"])

        self.assertIn("remote=pending", self.run_cli("undo").stdout)
        result = self.run_cli("push", "--rewrite")

        self.assertIn("pr=updated", result.stdout)
        body = json.loads(state_path.read_text())["body"]
        self.assertIn("- Change", body)
        self.assertNotIn("- Second", body)

    def test_undo_only_pr_commit_leaves_open_pr_with_empty_summary(self):
        state_path = self.open_test_pr()
        previous_calls = len(self.gh_calls())

        result = self.run_cli("undo")

        state = json.loads(state_path.read_text())
        self.assertIn("remote=updated pr=updated", result.stdout)
        self.assertIn("## Summary\n\nNo commits relative to base.", state["body"])
        self.assertNotIn("- Change", state["body"])
        self.assertEqual(state["state"], "OPEN")
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "close"], ["pr", "merge"])
                             for call in self.gh_calls()[previous_calls:]))

    def test_undo_pr_metadata_failure_preserves_successful_publication(self):
        state_path = self.open_test_pr()
        original_body = json.loads(state_path.read_text())["body"]
        self.env["GH_FAIL"] = "pr edit"
        removed = self.git("rev-parse", "HEAD")
        parent = self.git("rev-parse", "HEAD^")

        result = self.run_cli("undo")

        self.assertIn("remote=updated pr=failed", result.stdout)
        self.assertIn("pr sync warning", result.stderr)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "feat/test").split()[0], parent)
        self.assertEqual(json.loads(state_path.read_text())["body"], original_body)
        self.assertNotEqual(removed, parent)

    def test_undo_in_local_merge_mode_never_calls_gh(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "localMerge"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()

        result = self.run_cli("undo")

        self.assertIn("remote=updated pr=none", result.stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_local_merge_explicit_push_works_without_gh(self):
        self.save_settings(integration={"mode": "localMerge"},
                           commit={"mode": "autoCommit"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        gh_log = self.root / "unexpected-gh.log"
        gh_stub = self.bin / "gh"
        gh_stub.write_text('#!/usr/bin/env bash\n'
                           'printf "%s\\n" "$*" >> "$GH_UNEXPECTED_LOG"\n'
                           'exit 1\n')
        gh_stub.chmod(0o755)
        self.env["GH_UNEXPECTED_LOG"] = str(gh_log)
        self.change()

        self.assertIn("pushed=false", self.commit().stdout)
        result = self.run_cli("push")

        self.assertIn("push ok branch=feat/test pr=none", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])
        self.assertFalse(gh_log.exists())

    def test_local_merge_auto_push_and_rewrites_never_call_gh(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "localMerge"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.assertIn("pushed=true pr=none", self.commit().stdout)
        (self.repo / "tracked").write_text("amended\n")
        self.assertIn("pushed=true pr=none",
                      self.run_cli("commit", "--amend", "--all").stdout)
        self.advance_remote_branch("main")
        self.assertIn("pushed=true pr=none", self.run_cli("rebase").stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_metadata_identity_failure_preserves_pushed_commit(self):
        state_path = self.open_test_pr()
        original_body = json.loads(state_path.read_text())["body"]
        git_executable = shutil.which("git")
        git_stub = self.bin / "git"
        git_stub.write_text('#!/usr/bin/env bash\n'
                            'if [[ "$1" == remote && "$2" == get-url && "$3" == origin ]]; then\n'
                            '  printf "%s\\n" "file:///unverifiable/repo.git"\n'
                            '  exit 0\n'
                            'fi\n'
                            f'exec "{git_executable}" "$@"\n')
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertIn("cannot verify GitHub remote repository", result.stderr)
        self.assertEqual(json.loads(state_path.read_text())["body"], original_body)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])
        self.assertFalse(any(call[:2] == ["pr", "edit"] for call in self.gh_calls()))

    def test_missing_pr_identity_preserves_pushed_commit(self):
        state_path = self.open_test_pr()
        original_body = json.loads(state_path.read_text())["body"]
        self.env["GH_REPO_ID"] = ""
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertIn("repository identity is unavailable", result.stderr)
        self.assertEqual(json.loads(state_path.read_text())["body"], original_body)
        self.assertFalse(any(call[:2] == ["pr", "edit"] for call in self.gh_calls()))
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])

    def test_mismatched_pr_identity_does_not_edit(self):
        state_path = self.open_test_pr()
        original_body = json.loads(state_path.read_text())["body"]
        self.env["GH_REPO_ID"] = "other/dotfiles"
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=none", result.stdout)
        self.assertEqual(json.loads(state_path.read_text())["body"], original_body)
        self.assertFalse(any(call[:2] == ["pr", "edit"] for call in self.gh_calls()))
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])

    def test_summary_uses_open_pr_actual_base(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"},
                           branch={"baseBranches": ["main", "develop"]})
        self.git("push", "-q", "origin", "main")
        self.git("switch", "-c", "develop")
        (self.repo / "develop-only").write_text("base content\n")
        self.git("add", "develop-only")
        self.git("commit", "-qm", "Develop base")
        self.git("push", "-qu", "origin", "develop")
        self.run_cli("prepare", "--base", "develop", "--branch-name", "feat/from-develop")
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--base", "develop", "--stay")
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pr=updated", result.stdout)
        body = json.loads(Path(self.env["GH_STATE"]).read_text())["body"]
        self.assertIn("- Change\n- Second", body)
        self.assertNotIn("Develop base", body)

    def test_legacy_summary_migrates_without_losing_other_sections(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["title"] = "Human title"
        state["body"] = "## Summary\n\n- Change\n\n## Notes\n\nKeep this note."
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pr=updated", result.stdout)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["title"], "Human title")
        self.assertEqual(state["body"].count("## Summary"), 1)
        self.assertIn("- Change\n- Second", state["body"])
        self.assertTrue(state["body"].endswith("\n\n## Notes\n\nKeep this note."))

    def test_handwritten_description_without_summary_is_preserved(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["body"] = "Human context.\n\n## Checks\n\nKeep this section."
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pr=updated", result.stdout)
        body = json.loads(state_path.read_text())["body"]
        self.assertIn("- Change\n- Second", body)
        self.assertTrue(body.endswith("Human context.\n\n## Checks\n\nKeep this section."))

    def test_pr_selection_ignores_same_named_fork_branch(self):
        prs_path = self.root / "prs.json"
        body_path = self.root / "body.txt"
        helper = COMMON / "lib/git-workflow/pr-summary.py"
        fork = {"url": "https://github.com/owner/dotfiles/pull/2", "baseRefName": "main",
                "body": "fork", "headRepository": {"nameWithOwner": "other/dotfiles"}}
        own = {"url": "https://github.com/owner/dotfiles/pull/1", "baseRefName": "develop",
               "body": "own", "headRepository": {"nameWithOwner": "owner/dotfiles"}}
        prs_path.write_text(json.dumps([fork, own]))

        selected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                   str(body_path), "git@github.com:owner/dotfiles.git"],
                                  capture_output=True, text=True)

        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(selected.stdout.strip(),
                         "https://github.com/owner/dotfiles/pull/1\tdevelop")
        self.assertEqual(body_path.read_text(), "own")
        prs_path.write_text(json.dumps([fork]))
        no_match = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                   str(body_path), "git@github.com:owner/dotfiles.git"],
                                  capture_output=True, text=True)
        self.assertEqual(no_match.returncode, 3)

    def test_pr_selection_requires_verifiable_repository_identity(self):
        prs_path = self.root / "prs.json"
        body_path = self.root / "body.txt"
        helper = COMMON / "lib/git-workflow/pr-summary.py"
        own = {"url": "https://github.com/owner/dotfiles/pull/1", "baseRefName": "main",
               "body": "own", "headRepository": {"nameWithOwner": "owner/dotfiles"}}
        prs_path.write_text(json.dumps([own]))

        for remote_url in ("https://github.com/owner/dotfiles.git",
                           "git@github.com:owner/dotfiles.git",
                           "ssh://git@github.com/owner/dotfiles.git"):
            with self.subTest(remote_url=remote_url):
                selected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                           str(body_path), remote_url],
                                          capture_output=True, text=True)
                self.assertEqual(selected.returncode, 0, selected.stderr)
                self.assertEqual(body_path.read_text(), "own")

        body_path.write_text("unchanged")
        for remote_url in (str(self.remote), "https://github.com/owner/dotfiles/extra",
                           "https://github.com/owner/dotfiles.git?ref=main"):
            with self.subTest(remote_url=remote_url):
                rejected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                           str(body_path), remote_url],
                                          capture_output=True, text=True)
                self.assertEqual(rejected.returncode, 1)
                self.assertIn("cannot verify GitHub remote repository", rejected.stderr)
                self.assertEqual(body_path.read_text(), "unchanged")

        for repository in (None, {}, {"nameWithOwner": "other/dotfiles"}):
            with self.subTest(repository=repository):
                prs_path.write_text(json.dumps([{**own, "headRepository": repository}]))
                rejected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                           str(body_path), "git@github.com:owner/dotfiles.git"],
                                          capture_output=True, text=True)
                expected_code = (3 if isinstance(repository, dict) and
                                 repository.get("nameWithOwner") == "other/dotfiles" else 1)
                self.assertEqual(rejected.returncode, expected_code)
                self.assertEqual(body_path.read_text(), "unchanged")
        prs_path.write_text(json.dumps([{**own, "url": "https://other.example/pull/1"}]))
        rejected = subprocess.run(["python3", str(helper), "select", str(prs_path),
                                   str(body_path), "git@github.com:owner/dotfiles.git"],
                                  capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("host does not match", rejected.stderr)

    def test_unmanaged_summary_does_not_block_push(self):
        state_path = self.open_test_pr()
        state = json.loads(state_path.read_text())
        state["body"] = "## Summary\n\nHuman summary."
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertEqual(json.loads(state_path.read_text())["body"], state["body"])
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])

    def test_ambiguous_pr_association_does_not_block_push(self):
        state_path = self.open_test_pr()
        original = json.loads(state_path.read_text())["body"]
        self.env["GH_EXTRA_PRS"] = json.dumps([{
            "url": "https://github.com/owner/dotfiles/pull/2", "baseRefName": "main",
            "body": "## Summary\n\n- Other",
            "headRepository": {"nameWithOwner": "owner/dotfiles"}}])
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertIn("ambiguous open PRs", result.stderr)
        self.assertEqual(json.loads(state_path.read_text())["body"], original)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])

    def test_pr_edit_failure_keeps_successful_push_and_can_retry(self):
        state_path = self.open_test_pr()
        old_body = json.loads(state_path.read_text())["body"]
        self.env["GH_FAIL"] = "pr edit"
        (self.repo / "second").write_text("second\n")

        result = self.run_cli("commit", "--message", "Second", "--", "second")

        self.assertIn("pushed=true pr=failed", result.stdout)
        self.assertIn("pr sync warning", result.stderr)
        self.assertEqual(json.loads(state_path.read_text())["body"], old_body)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])
        self.env.pop("GH_FAIL")
        self.assertIn("pr=updated", self.run_cli("push").stdout)
        self.assertIn("- Second", json.loads(state_path.read_text())["body"])

    def test_push_without_open_pr_does_not_create_one(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.assertIn("pr=none", self.commit().stdout)
        self.assertEqual(self.gh_calls(), [])
        (self.repo / "second").write_text("second\n")
        result = self.run_cli("commit", "--message", "Second", "--", "second")
        self.assertIn("pr=none", result.stdout)
        self.assertNotIn("error status", result.stderr)
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "edit"])
                             for call in self.gh_calls()))
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text()), {})

    def test_explicit_base_branch_push_skips_pr_lookup(self):
        self.stub_gh()
        self.git("push", "-q", "origin", "main")

        result = self.run_cli("push")

        self.assertIn("branch=main pr=none", result.stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_push_does_not_edit_closed_or_merged_pr(self):
        for pr_state in ("CLOSED", "MERGED"):
            with self.subTest(pr_state=pr_state):
                state_path = self.open_test_pr()
                state = json.loads(state_path.read_text())
                state["state"] = pr_state
                state_path.write_text(json.dumps(state))
                (self.repo / "second").write_text("second\n")

                result = self.run_cli("commit", "--message", "Second", "--", "second")

                self.assertIn("pushed=true pr=none", result.stdout)
                self.assertEqual(json.loads(state_path.read_text())["body"], state["body"])
                self.git("switch", "main")
                self.git("branch", "-D", "feat/test")
                self.git("push", "-q", "origin", "--delete", "feat/test")
                state_path.unlink()

    def test_resubmit_preserves_manual_title_and_other_sections(self):
        state_path = self.open_test_pr("autoCommit")
        state = json.loads(state_path.read_text())
        state["title"] = "Human title"
        state["body"] += "\n\n## Notes\n\nKeep."
        state_path.write_text(json.dumps(state))
        (self.repo / "second").write_text("second\n")
        self.run_cli("commit", "--message", "Second", "--", "second")

        result = self.run_cli("pr", "submit", "--stay")

        self.assertIn("pr submit ok", result.stdout)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["title"], "Human title")
        self.assertIn("- Change\n- Second", state["body"])
        self.assertTrue(state["body"].endswith("\n\n## Notes\n\nKeep."))

    def test_pr_submit_without_new_commits_does_not_push(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.git("switch", "-c", "fix/no-new-commits")
        remote_before = self.git("ls-remote", "--heads", "origin")

        result = self.run_cli("pr", "submit", ok=False)
        self.assertIn("no commits to submit", result.stderr)
        self.assertEqual(self.git("ls-remote", "--heads", "origin"), remote_before)
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "edit"])
                             for call in self.gh_calls()))

    def test_squash_default_single_commit_subject(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Change")

    def test_squash_default_multiple_commit_summary(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.git("push", "-q", "origin", "main")
        self.run_cli("prepare", "--base", "main", "--branch-name", "feat/ai-harness-preflight")
        self.change()
        self.commit()
        self.change("other")
        self.commit("other")
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "feat(ai): harness preflight")

    def test_squash_default_unconventional_branch(self):
        self.save_settings(integration={"mergeMethod": "squash"})
        self.git("switch", "-c", "work-in-progress")
        self.assertIn("created=false", self.run_cli("prepare", "--continue").stdout)
        self.change()
        self.commit()
        self.change("other")
        self.commit("other")
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "work-in-progress")

    def test_merge_commit_keeps_git_default_message(self):
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("merge")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Merge branch 'feat/test'")

    def test_pull_request_default_title(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.run_cli("prepare", "--base", "main", "--branch-name", "feat/ai-harness-preflight")
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--stay")
        state_path = Path(self.env["GH_STATE"])
        self.assertEqual(json.loads(state_path.read_text())["title"], "Change")
        self.change("other")
        self.commit("other")
        self.run_cli("pr", "submit", "--stay")
        self.assertEqual(json.loads(state_path.read_text())["title"], "Change")

    def test_pr_rechecks_authentication(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        (self.bin / "gh").write_text("#!/bin/sh\nexit 1\n")
        self.change()
        self.commit()
        self.run_cli("push")
        for action in ("submit", "merge"):
            self.assertIn("check=gh-auth", self.run_cli("pr", action, ok=False).stderr)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_delivery_mode_isolation(self):
        self.prepare()
        for action in ("submit", "merge"):
            self.assertIn("localMerge mode", self.run_cli("pr", action, ok=False).stderr)
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.assertIn("git-workflow pr merge", self.run_cli("merge", ok=False).stderr)

    def test_pr_merge_missing_or_wrong_base(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.assertIn("no open PR", self.run_cli("pr", "merge", ok=False).stderr)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in self.gh_calls()))
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--stay")
        state_path = Path(self.env["GH_STATE"])
        state = json.loads(state_path.read_text())
        state["base"] = "other"
        state_path.write_text(json.dumps(state))
        self.run_cli("pr", "merge", ok=False)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.gh_calls()))

    def check_pr_merge(self, method, cleanup):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest", "mergeMethod": method},
                           branch={"deleteAfterIntegration": cleanup})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--stay")
        result = self.run_cli("pr", "merge")
        call = next(call for call in self.gh_calls() if call[:2] == ["pr", "merge"])
        self.assertIn("--merge" if method == "mergeCommit" else "--squash", call)
        self.assertNotIn("--auto", call)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/main"))
        self.assertEqual((self.repo / "tracked").read_text(), "changed\n")
        self.assertIn(f"deleted={'local-and-remote' if cleanup else 'false'}", result.stdout)
        self.assertEqual(bool(self.git("branch", "--list", "feat/test")), not cleanup)
        self.assertEqual(bool(self.git("ls-remote", "--heads", "origin", "feat/test")), not cleanup)
        self.assertEqual(self.git("config", "--get-regexp", "^branch.main.remote$"), "branch.main.remote origin")

    def test_pr_merge_commit(self):
        self.check_pr_merge("mergeCommit", False)

    def test_pr_merge_commit_cleanup(self):
        self.check_pr_merge("mergeCommit", True)

    def test_pr_squash(self):
        self.check_pr_merge("squash", False)

    def test_pr_squash_cleanup(self):
        self.check_pr_merge("squash", True)

    def test_pr_failures_preserve_git_state(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.run_cli("pr", "submit", ok=False)
        self.assertEqual(self.gh_calls(), [])
        self.commit()
        self.env["GH_FAIL"] = "pr list"
        self.run_cli("pr", "submit", ok=False)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in self.gh_calls()))
        del self.env["GH_FAIL"]
        self.run_cli("pr", "submit", "--stay")
        self.env["GH_FAIL"] = "pr merge"
        self.run_cli("pr", "merge", ok=False)
        del self.env["GH_FAIL"]
        self.env["GH_PENDING"] = "1"
        self.run_cli("pr", "merge", ok=False)
        self.assertEqual(self.git("branch", "--show-current"), "feat/test")

    def test_pr_sync_failure_preserves_state_and_branches(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"},
                           branch={"deleteAfterIntegration": True})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--stay")
        local_base = self.git("commit-tree", "main^{tree}", "-p", "main", "-m", "Local base advance")
        self.git("update-ref", "refs/heads/main", local_base)
        self.run_cli("pr", "merge", ok=False)
        self.assertEqual(json.loads(Path(self.env["GH_STATE"]).read_text())["state"], "MERGED")
        self.assertEqual(self.git("rev-parse", "main"), local_base)
        self.assertTrue(self.git("branch", "--list", "feat/test"))
        self.assertTrue(self.git("ls-remote", "--heads", "origin", "feat/test"))

    def test_pr_merge_rejects_merge_queue_before_mutation(self):
        self.stub_gh()
        self.save_settings(integration={"mode": "pullRequest"})
        self.git("push", "-q", "origin", "main")
        self.prepare()
        self.change()
        self.commit()
        self.run_cli("pr", "submit", "--stay")
        for queue_value in ("true", "null"):
            self.env["GH_MERGE_QUEUE"] = queue_value
            self.run_cli("pr", "merge", ok=False)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.gh_calls()))

    def test_amend_replaces_old_subject_in_pr_summary(self):
        state_path = self.open_test_pr()
        (self.repo / "tracked").write_text("amended\n")

        result = self.run_cli("commit", "--amend", "--message", "Amended", "--all")

        self.assertIn("pushed=true pr=updated", result.stdout)
        body = json.loads(state_path.read_text())["body"]
        self.assertIn("- Amended", body)
        self.assertNotIn("- Change", body)
        self.assertEqual(self.git("rev-parse", "HEAD"),
                         self.git("ls-remote", "--heads", "origin", "feat/test").split()[0])


if __name__ == "__main__":
    unittest.main()
