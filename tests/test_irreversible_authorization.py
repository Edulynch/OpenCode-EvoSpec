from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from evospec.agent_protocol import execute
from evospec.domain import (
    AutonomyAuthorization,
    GitCommitRewrite,
    GitHistoryRewritePolicy,
    IrreversibleAuthorizationStatus,
    IrreversibleOperationKind,
    ReviewResult,
    VerificationEvidence,
)
from evospec.lifecycle import LifecycleState, ReviewVerdict
from evospec.services import CoreService, ServiceError, _evaluate_autonomy
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory


OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
TARGET_REF = "refs/heads/master"
PURPOSE = "normalize the explicitly authorized master history"
IDENTITY_NAME = "Synthetic Contributor"
IDENTITY_EMAIL = "security-researcher@example.invalid"
REWRITE_POLICY = GitHistoryRewritePolicy.NORMALIZE_MISMATCHED_IDENTITIES


def active_change(root: Path):
    workflow = WorkflowService.open(root)
    workflow.init()
    proposed = workflow.new("Explicit irreversible authorization")
    if proposed.change_id is None:
        raise AssertionError("expected active Core Change")
    workflow.record_progress(
        "implement authorization",
        "explicit irreversible authorization",
        (),
        "implement Core authorization",
        (),
        "continue authorization work",
    )
    return workflow, CoreService.open(root), CoreService.open(root).recover()


def git(root: Path, *arguments: str, input_bytes: bytes | None = None, env: dict[str, str] | None = None) -> bytes:
    environment = os.environ.copy()
    if env is not None:
        environment.update(env)
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        input=input_bytes,
        capture_output=True,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode("utf-8", errors="replace"))
    return completed.stdout.strip()


def git_raw(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(("git", *arguments), cwd=root, capture_output=True, check=False)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode("utf-8", errors="replace"))
    return completed.stdout


def git_history_fixture(container: Path) -> tuple[Path, Path, str, str, GitCommitRewrite]:
    project = container / "project"
    remote = container / "origin.git"
    project.mkdir()
    subprocess.run(("git", "init", "--bare", str(remote)), capture_output=True, check=True)
    git(project, "init", "--initial-branch=master")
    git(project, "config", "user.name", IDENTITY_NAME)
    git(project, "config", "user.email", IDENTITY_EMAIL)
    (project / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(project, "add", "tracked.txt")
    git(
        project,
        "commit",
        "-m",
        "base commit",
        env={
            "GIT_AUTHOR_NAME": IDENTITY_NAME,
            "GIT_AUTHOR_EMAIL": IDENTITY_EMAIL,
            "GIT_COMMITTER_NAME": IDENTITY_NAME,
            "GIT_COMMITTER_EMAIL": IDENTITY_EMAIL,
            "GIT_AUTHOR_DATE": "1700000000 -0600",
            "GIT_COMMITTER_DATE": "1700000000 -0600",
        },
    )
    git(project, "tag", "v-test")
    (project / "tracked.txt").write_text("target\n", encoding="utf-8")
    git(project, "add", "tracked.txt")
    git(
        project,
        "commit",
        "-m",
        "identity normalization target",
        env={
            "GIT_AUTHOR_NAME": "Fixture Operator",
            "GIT_AUTHOR_EMAIL": "operator@example.com",
            "GIT_COMMITTER_NAME": "Fixture Operator",
            "GIT_COMMITTER_EMAIL": "operator@example.com",
            "GIT_AUTHOR_DATE": "1700000123 -0600",
            "GIT_COMMITTER_DATE": "1700000456 -0600",
        },
    )
    old_sha = git(project, "rev-parse", "refs/heads/master").decode("ascii")
    original = git_raw(project, "cat-file", "commit", old_sha)
    header, separator, message = original.partition(b"\n\n")
    if not separator:
        raise AssertionError("expected commit message separator")
    replacement = []
    identity_line = re.compile(rb"^(author|committer) .* <[^>]*> (.*)$")
    for line in header.split(b"\n"):
        match = identity_line.fullmatch(line)
        if match is not None:
            line = match.group(1) + b" " + IDENTITY_NAME.encode() + b" <" + IDENTITY_EMAIL.encode() + b"> " + match.group(2)
        replacement.append(line)
    new_raw = b"\n".join(replacement) + b"\n\n" + message
    new_sha = git(project, "hash-object", "-t", "commit", "--stdin", input_bytes=new_raw).decode("ascii")
    git(project, "remote", "add", "origin", str(remote))
    git(project, "push", "--quiet", "origin", "refs/heads/master", "refs/tags/v-test")
    mapping = GitCommitRewrite(old_sha, new_sha, True)
    return project, remote, old_sha, new_sha, mapping


def authorize(
    core: CoreService,
    state,
    *,
    old_sha: str = OLD_SHA,
    new_sha: str = NEW_SHA,
    mapping: GitCommitRewrite | None = None,
    target_ref: str = TARGET_REF,
):
    commit_map = (GitCommitRewrite(old_sha, new_sha, True),) if mapping is None else (mapping,)
    return core.record_irreversible_authorization(
        state,
        IrreversibleOperationKind.GIT_HISTORY_REWRITE,
        target_ref,
        old_sha,
        REWRITE_POLICY,
        IDENTITY_NAME,
        IDENTITY_EMAIL,
        new_sha,
        commit_map,
        PURPOSE,
        True,
    )


class IrreversibleAuthorizationTests(unittest.TestCase):
    def test_missing_authorization_fails_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            _, core, state = active_change(Path(temporary))
            context = state.active_context
            if context is None:
                self.fail("expected active context")
            evaluation = core.evaluate_autonomy(state, "GIT_HISTORY_REWRITE", irreversible=True)
            self.assertEqual(evaluation.authorization, AutonomyAuthorization.HUMAN_MATERIAL_DECISION)
            with self.assertRaises(ServiceError):
                core.execute_irreversible_authorization(state, "irreversible-missing")

    def test_generic_envelope_permissions_never_authorize_protected_git_actions(self) -> None:
        with TemporaryDirectory() as temporary:
            _, _, state = active_change(Path(temporary))
            context = state.active_context
            if context is None:
                self.fail("expected active context")
            broad = replace(
                context,
                autonomy_envelope=replace(
                    context.autonomy_envelope,
                    reversible_actions=("GIT_HISTORY_REWRITE", "GIT_FORCE_PUSH", "DESTRUCTIVE_RESET", "push", "reset", "tag", "release"),
                    irreversible_permissions=("GIT_HISTORY_REWRITE", "GIT_FORCE_PUSH", "DESTRUCTIVE_RESET", "push", "reset", "tag", "release"),
                ),
            )
            for action in (
                "GIT_HISTORY_REWRITE",
                "GIT_FORCE_PUSH",
                "git push --force-with-lease",
                "force-with-lease",
                "DESTRUCTIVE_RESET",
                "git reset --hard",
                "reset",
                "tag v1",
                "release",
            ):
                with self.subTest(action=action):
                    evaluation = _evaluate_autonomy(broad, action, (), True)
                    self.assertEqual(
                        evaluation.authorization,
                        AutonomyAuthorization.HUMAN_MATERIAL_DECISION,
                    )

    def test_exact_one_shot_authorization_executes_and_consumes_only_the_verified_push(self) -> None:
        with TemporaryDirectory() as temporary:
            project, remote, old_sha, new_sha, mapping = git_history_fixture(Path(temporary))
            _, core, state = active_change(project)
            pending = authorize(core, state, old_sha=old_sha, new_sha=new_sha, mapping=mapping)
            record = pending.irreversible_authorizations[-1]
            self.assertEqual(record.status, IrreversibleAuthorizationStatus.PENDING)
            self.assertTrue(record.one_shot)
            self.assertTrue(record.force_with_lease)
            self.assertEqual(record.target_ref, TARGET_REF)
            self.assertEqual(record.expected_remote_sha, old_sha)
            self.assertEqual(record.rewrite_policy, REWRITE_POLICY)
            self.assertEqual(record.target_identity_name, IDENTITY_NAME)
            self.assertEqual(record.target_identity_email, IDENTITY_EMAIL)
            self.assertEqual(record.expected_result_sha, new_sha)
            self.assertEqual(record.commit_rewrites, (mapping,))
            self.assertEqual(len(record.rewrite_plan_digest), 64)

            consumed, refs_unchanged = core.execute_irreversible_authorization(pending, str(record.id))
            audited = CoreService.open(project).recover().irreversible_authorizations[-1]
            self.assertEqual(audited, consumed.irreversible_authorizations[-1])
            self.assertEqual(audited.status, IrreversibleAuthorizationStatus.CONSUMED)
            self.assertEqual(audited.resulting_remote_sha, new_sha)
            self.assertTrue(refs_unchanged)
            self.assertEqual(git(remote, "rev-parse", "refs/heads/master").decode(), new_sha)
            self.assertEqual(
                git(remote, "rev-parse", "refs/tags/v-test").decode(),
                git(project, "rev-parse", "refs/tags/v-test").decode(),
            )
            self.assertEqual(git(project, "rev-parse", "refs/heads/master").decode(), old_sha)
            old_raw = git_raw(project, "cat-file", "commit", old_sha)
            new_raw = git_raw(project, "cat-file", "commit", new_sha)
            old_header, _, old_message = old_raw.partition(b"\n\n")
            new_header, _, new_message = new_raw.partition(b"\n\n")
            self.assertEqual(old_message, new_message)
            self.assertEqual(
                git(project, "show", "-s", "--format=%T", old_sha),
                git(project, "show", "-s", "--format=%T", new_sha),
            )
            old_parents = tuple(line for line in old_header.splitlines() if line.startswith(b"parent "))
            new_parents = tuple(line for line in new_header.splitlines() if line.startswith(b"parent "))
            self.assertEqual(old_parents, new_parents)
            for role in (b"author ", b"committer "):
                identity = next(line for line in new_header.splitlines() if line.startswith(role))
                self.assertIn(IDENTITY_NAME.encode(), identity)
                self.assertIn(IDENTITY_EMAIL.encode(), identity)
            with self.assertRaises(ServiceError):
                core.execute_irreversible_authorization(consumed, str(record.id))

    def test_authorization_can_be_recorded_after_technical_verification(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow, core, _ = active_change(Path(temporary))
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("full test suite passed",), True))
            state = core.recover()
            active = state.active_context
            if active is None:
                self.fail("expected active context after verification")
            self.assertEqual(active.status, LifecycleState.VERIFYING)
            pending = authorize(core, state)
            self.assertEqual(
                pending.irreversible_authorizations[-1].status,
                IrreversibleAuthorizationStatus.PENDING,
            )

    def test_unavailable_local_target_fails_closed_without_remote_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            project, remote, old_sha, new_sha, mapping = git_history_fixture(Path(temporary))
            _, core, state = active_change(project)
            pending = authorize(
                core,
                state,
                old_sha=old_sha,
                new_sha=new_sha,
                mapping=mapping,
                target_ref="refs/heads/release",
            )
            record = pending.irreversible_authorizations[-1]
            with self.assertRaises(ServiceError):
                core.execute_irreversible_authorization(pending, str(record.id))
            self.assertEqual(CoreService.open(project).recover(), pending)
            self.assertEqual(git(remote, "rev-parse", "refs/heads/master").decode(), old_sha)

    def test_stale_remote_sha_invalidates_the_authorization(self) -> None:
        with TemporaryDirectory() as temporary:
            project, remote, old_sha, new_sha, mapping = git_history_fixture(Path(temporary))
            _, core, state = active_change(project)
            pending = authorize(core, state, old_sha=old_sha, new_sha=new_sha, mapping=mapping)
            record = pending.irreversible_authorizations[-1]
            competitor = Path(temporary) / "competitor"
            subprocess.run(("git", "clone", str(remote), str(competitor)), capture_output=True, check=True)
            git(competitor, "config", "user.name", IDENTITY_NAME)
            git(competitor, "config", "user.email", IDENTITY_EMAIL)
            (competitor / "concurrent.txt").write_text("moved\n", encoding="utf-8")
            git(competitor, "add", "concurrent.txt")
            git(competitor, "commit", "-m", "concurrent remote advance")
            git(competitor, "push", "--quiet", "origin", "master")
            stale, _ = core.execute_irreversible_authorization(pending, str(record.id))
            invalidated = stale.irreversible_authorizations[-1]
            self.assertEqual(invalidated.status, IrreversibleAuthorizationStatus.INVALIDATED)
            self.assertIn("remote SHA", invalidated.invalidation_reason or "")
            with self.assertRaises(ServiceError):
                core.execute_irreversible_authorization(stale, str(record.id))
            self.assertNotEqual(git(remote, "rev-parse", "refs/heads/master").decode(), new_sha)

    def test_any_core_revision_change_invalidates_pending_authority(self) -> None:
        with TemporaryDirectory() as temporary:
            _, core, state = active_change(Path(temporary))
            pending = authorize(core, state)
            changed = core.record_progress(pending, "continue", "scope", (), None, (), "resume")
            record = changed.irreversible_authorizations[-1]
            self.assertEqual(record.status, IrreversibleAuthorizationStatus.INVALIDATED)
            self.assertEqual(record.invalidated_revision, changed.revision)
            self.assertIn("Core authority revision changed", record.invalidation_reason or "")

    def test_force_with_lease_is_mandatory_for_recording(self) -> None:
        with TemporaryDirectory() as temporary:
            _, core, state = active_change(Path(temporary))
            with self.assertRaises(ServiceError):
                core.record_irreversible_authorization(
                    state,
                    IrreversibleOperationKind.GIT_HISTORY_REWRITE,
                    TARGET_REF,
                    OLD_SHA,
                    REWRITE_POLICY,
                    IDENTITY_NAME,
                    IDENTITY_EMAIL,
                    NEW_SHA,
                    (GitCommitRewrite(OLD_SHA, NEW_SHA, True),),
                    PURPOSE,
                    False,
                )
            pending = authorize(core, state)
            self.assertEqual(CoreService.open(Path(temporary)).recover(), pending)

    def test_caller_cannot_supply_a_fake_result_or_consume_without_git_execution(self) -> None:
        with TemporaryDirectory() as temporary:
            project, remote, old_sha, new_sha, mapping = git_history_fixture(Path(temporary))
            _, core, state = active_change(project)
            pending = authorize(core, state, old_sha=old_sha, new_sha=new_sha, mapping=mapping)
            self.assertFalse(hasattr(core, "consume_irreversible_authorization"))
            with self.assertRaises(ServiceError):
                core.execute_irreversible_authorization(pending, "irreversible-not-authorized")
            self.assertEqual(git(remote, "rev-parse", "refs/heads/master").decode(), old_sha)

    def test_recomputed_commit_map_must_match_authorized_plan(self) -> None:
        with TemporaryDirectory() as temporary:
            project, remote, old_sha, _new_sha, mapping = git_history_fixture(Path(temporary))
            _, core, state = active_change(project)
            forged = GitCommitRewrite(mapping.old_sha, "f" * 40, True)
            pending = authorize(
                core,
                state,
                old_sha=old_sha,
                new_sha=forged.new_sha,
                mapping=forged,
            )
            with self.assertRaises(ServiceError):
                core.execute_irreversible_authorization(
                    pending, str(pending.irreversible_authorizations[-1].id)
                )
            self.assertEqual(
                git(remote, "rev-parse", "refs/heads/master").decode(), old_sha
            )
            self.assertEqual(
                CoreService.open(project).recover().irreversible_authorizations[-1].status,
                IrreversibleAuthorizationStatus.PENDING,
            )

    def test_unrelated_irreversible_operation_stays_blocked(self) -> None:
        with TemporaryDirectory() as temporary:
            _, core, state = active_change(Path(temporary))
            pending = authorize(core, state)
            release = core.evaluate_autonomy(pending, "release", irreversible=True)
            self.assertEqual(release.authorization, AutonomyAuthorization.HUMAN_MATERIAL_DECISION)
            with self.assertRaises(ServiceError):
                core.record_irreversible_authorization(
                    pending,
                    IrreversibleOperationKind.GIT_FORCE_PUSH,
                    TARGET_REF,
                    OLD_SHA,
                    REWRITE_POLICY,
                    IDENTITY_NAME,
                    IDENTITY_EMAIL,
                    NEW_SHA,
                    (GitCommitRewrite(OLD_SHA, NEW_SHA, True),),
                    PURPOSE,
                    True,
                )

    def test_protocol_projects_authorization_and_enforces_revision_bound_transitions(self) -> None:
        with TemporaryDirectory() as temporary:
            root, remote, old_sha, new_sha, mapping = git_history_fixture(Path(temporary))
            initialized = json.loads(execute(root, json.dumps({"action": "init", "expected_revision": 0})))
            proposed = json.loads(execute(root, json.dumps({
                "action": "new",
                "expected_revision": initialized["result"]["revision"],
                "intent": "Protocol authorization feature",
                "assessment": {
                    "operation": "NEW",
                    "classification": "FEATURE",
                    "confidence": "HIGH",
                    "capability_state": "PLANNED",
                    "selected_capability": "Protocol authorization feature",
                    "candidate_capabilities": [],
                    "selected_feature_id": None,
                    "evidence": [{
                        "source": "test",
                        "reference": "tests/test_irreversible_authorization.py",
                        "feature_id": None,
                        "living_spec_id": None,
                    }],
                    "material_ambiguities": [],
                    "recommended_depth": {
                        "review": "NORMAL",
                        "verification": "NORMAL",
                        "convergence": "NORMAL",
                    },
                    "rationale": "explicit typed test intake",
                },
            })))
            created = json.loads(execute(root, json.dumps({
                "action": "progress",
                "expected_revision": proposed["context"]["revision"],
                "current_action": "implement authorization",
                "focus": "exact Git operation",
                "completed_work": [],
                "current_work": "authorization protocol",
                "pending_work": [],
                "resume_point": "continue Core validation",
            })))
            self.assertIn("authorize_irreversible", created["context"]["legal_actions"])
            state = CoreService.open(root).recover()
            authorized = json.loads(execute(root, json.dumps({
                "action": "authorize_irreversible",
                "expected_revision": state.revision,
                "operation_kind": "GIT_HISTORY_REWRITE",
                "target_ref": TARGET_REF,
                "expected_remote_sha": old_sha,
                "rewrite_policy": REWRITE_POLICY.value,
                "target_identity_name": IDENTITY_NAME,
                "target_identity_email": IDENTITY_EMAIL,
                "expected_result_sha": new_sha,
                "commit_rewrites": [{"old_sha": mapping.old_sha, "new_sha": mapping.new_sha, "identity_normalized": True}],
                "purpose": PURPOSE,
                "force_with_lease": True,
            })))
            self.assertIn("context", authorized, str(authorized))
            record = authorized["context"]["irreversible_authorizations"][-1]
            self.assertEqual(authorized["result"]["code"], "IRREVERSIBLE_AUTHORIZED")
            self.assertEqual(record["feature_id"], created["context"]["feature_id"])
            self.assertEqual(record["change_id"], created["context"]["change_id"])
            self.assertEqual(record["status"], "PENDING")
            self.assertEqual(record["expected_result_sha"], new_sha)
            self.assertEqual(record["commit_rewrites"], [{"old_sha": mapping.old_sha, "new_sha": mapping.new_sha, "identity_normalized": True}])
            self.assertIn("execute_irreversible", authorized["context"]["legal_actions"])
            self.assertNotIn("authorize_irreversible", authorized["context"]["legal_actions"])

            stale = json.loads(execute(root, json.dumps({
                "action": "execute_irreversible",
                "expected_revision": authorized["context"]["revision"] - 1,
                "authorization_id": record["id"],
            })))
            self.assertEqual(stale["error"]["code"], "STALE_REVISION")

            consumed = json.loads(execute(root, json.dumps({
                "action": "execute_irreversible",
                "expected_revision": authorized["context"]["revision"],
                "authorization_id": record["id"],
            })))
            self.assertEqual(consumed["result"]["code"], "IRREVERSIBLE_CONSUMED")
            self.assertEqual(consumed["context"]["irreversible_authorizations"][-1]["status"], "CONSUMED")
            self.assertEqual(git(remote, "rev-parse", "refs/heads/master").decode(), new_sha)

    def test_force_with_lease_rejects_a_remote_move_after_core_precheck(self) -> None:
        with TemporaryDirectory() as temporary:
            project, remote, old_sha, new_sha, mapping = git_history_fixture(Path(temporary))
            _, core, state = active_change(project)
            pending = authorize(core, state, old_sha=old_sha, new_sha=new_sha, mapping=mapping)
            authorization_id = str(pending.irreversible_authorizations[-1].id)
            competitor = Path(temporary) / "racing-writer"
            subprocess.run(("git", "clone", str(remote), str(competitor)), capture_output=True, check=True)
            git(competitor, "config", "user.name", IDENTITY_NAME)
            git(competitor, "config", "user.email", IDENTITY_EMAIL)
            (competitor / "racer.txt").write_text("racing update\n", encoding="utf-8")
            git(competitor, "add", "racer.txt")
            git(competitor, "commit", "-m", "racing remote update")

            from evospec import services

            original_run = services._git_run
            push_arguments: list[str] = []
            moved = False

            def advance_remote_before_push(root: Path, *arguments: str, **kwargs):
                nonlocal moved
                if "push" in arguments and not moved:
                    git(competitor, "push", "--quiet", "origin", "master")
                    moved = True
                    push_arguments.extend(arguments)
                return original_run(root, *arguments, **kwargs)

            with patch("evospec.services._git_run", side_effect=advance_remote_before_push):
                result, _ = core.execute_irreversible_authorization(pending, authorization_id)

            audit = result.irreversible_authorizations[-1]
            self.assertTrue(moved)
            self.assertIn(f"--force-with-lease={TARGET_REF}:{old_sha}", push_arguments)
            self.assertIn(f"{new_sha}:{TARGET_REF}", push_arguments)
            self.assertEqual(audit.status, IrreversibleAuthorizationStatus.INVALIDATED)
            self.assertNotEqual(git(remote, "rev-parse", TARGET_REF).decode(), new_sha)

    def test_core_authority_is_reserved_during_the_remote_push(self) -> None:
        with TemporaryDirectory() as temporary:
            project, _remote, old_sha, new_sha, mapping = git_history_fixture(Path(temporary))
            _, core, state = active_change(project)
            pending = authorize(core, state, old_sha=old_sha, new_sha=new_sha, mapping=mapping)
            authorization_id = str(pending.irreversible_authorizations[-1].id)

            from evospec import services

            original_run = services._git_run
            blocked_mutations: list[bool] = []

            def attempt_core_mutation_during_push(root: Path, *arguments: str, **kwargs):
                if "push" in arguments and not blocked_mutations:
                    try:
                        core.record_progress(
                            core.recover(), "concurrent update", "scope", (), None, (), "resume"
                        )
                    except ServiceError:
                        blocked_mutations.append(True)
                return original_run(root, *arguments, **kwargs)

            with patch("evospec.services._git_run", side_effect=attempt_core_mutation_during_push):
                result, _ = core.execute_irreversible_authorization(pending, authorization_id)
            self.assertEqual(blocked_mutations, [True])
            self.assertEqual(
                result.irreversible_authorizations[-1].status,
                IrreversibleAuthorizationStatus.CONSUMED,
            )

    def test_branch_refs_and_sha_values_are_strict(self) -> None:
        with TemporaryDirectory() as temporary:
            _, core, state = active_change(Path(temporary))
            for ref, sha in (("refs/tags/v1", OLD_SHA), ("refs/heads/../master", OLD_SHA), (TARGET_REF, "abc")):
                with self.subTest(ref=ref, sha=sha), self.assertRaises(ServiceError):
                    core.record_irreversible_authorization(
                        state,
                        IrreversibleOperationKind.GIT_HISTORY_REWRITE,
                        ref,
                        sha,
                        REWRITE_POLICY,
                        IDENTITY_NAME,
                        IDENTITY_EMAIL,
                        NEW_SHA,
                        (GitCommitRewrite(OLD_SHA, NEW_SHA, True),),
                        PURPOSE,
                        True,
                    )


if __name__ == "__main__":
    unittest.main()
