from __future__ import annotations

import json
import unittest
from pathlib import Path

from evospec.agent_protocol import execute
from evospec.domain import (
    AutonomyAuthorization,
    AutonomyEnvelope,
    AutonomyEnvelopeDelta,
    AutonomyExpansionDecision,
    ExpectedRevision,
    RepositoryPath,
)
from evospec.services import CoreService, ServiceError
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory


def active_work(root: Path) -> tuple[WorkflowService, CoreService]:
    workflow = WorkflowService.open(root)
    workflow.init()
    workflow.new("Implement explicit autonomy grants")
    return workflow, CoreService.open(root)


def delta() -> AutonomyEnvelopeDelta:
    return AutonomyEnvelopeDelta(
        allowed_paths=(RepositoryPath("evospec"), RepositoryPath("tests")),
        operation_budget=20,
        reversible_actions=("progress", "commit", "push", "ci"),
        external_wait_allowed=True,
        retry_budget=3,
        irreversible_permissions=("push",),
        stop_conditions=(
            "material decision required",
            "scope expansion proposed",
            "irreversible operation proposed",
        ),
    )


def protocol_delta() -> dict[str, object]:
    return {
        "allowed_paths": ["evospec", "tests"],
        "operation_budget": 20,
        "reversible_actions": ["progress", "commit", "push", "ci"],
        "external_wait_allowed": True,
        "retry_budget": 3,
        "irreversible_permissions": ["push"],
        "stop_conditions": [
            "material decision required",
            "scope expansion proposed",
            "irreversible operation proposed",
        ],
    }


def request(action: str, **values: object) -> str:
    return json.dumps({"action": action, **values})


class AutonomyExpansionTests(unittest.TestCase):
    def test_proposal_requires_explicit_authorization_and_persists_the_audit(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow, core = active_work(root)
            before = core.recover()
            before_context = before.active_context
            if before_context is None:
                self.fail("expected active work before the proposal")
            # A zero legacy operation budget means no cap; the grant below
            # replaces that implicit setting with a finite explicit budget.
            self.assertEqual(
                core.evaluate_autonomy(before, "progress").authorization,
                AutonomyAuthorization.ALLOWED,
            )

            proposed = core.propose_autonomy_expansion(before, delta())
            pending = proposed.pending_autonomy_expansion
            active_after_proposal = proposed.active_context
            if pending is None or active_after_proposal is None:
                self.fail("expected pending proposal and active work")
            self.assertEqual(active_after_proposal.autonomy_envelope, before_context.autonomy_envelope)
            self.assertEqual(active_after_proposal.autonomy_metrics.operational_handoffs, 1)
            self.assertEqual(CoreService.open(root).recover(), proposed)

            projected = json.loads(execute(root, request("context")))["context"]
            self.assertEqual(projected["outcome"]["kind"], "HUMAN_MATERIAL_DECISION")
            self.assertEqual(
                projected["outcome"]["material_decision"]["options"],
                ["AUTHORIZE", "REJECT", "NARROW"],
            )
            self.assertEqual(
                projected["pending_autonomy_expansion"]["proposal_id"],
                pending.proposal_id,
            )

            workflow.resolve_autonomy_expansion(
                str(pending.proposal_id),
                AutonomyExpansionDecision.AUTHORIZE,
                expected_revision=ExpectedRevision(proposed.revision),
            )
            resolved = CoreService.open(root).recover()
            active = resolved.active_context
            if active is None:
                self.fail("expected active work after authorization")
            self.assertEqual(active.autonomy_envelope, pending.proposed_envelope)
            self.assertEqual(active.autonomy_metrics.human_material_decisions, 1)
            self.assertEqual(active.autonomy_metrics.operational_handoffs, 1)
            self.assertIsNone(resolved.pending_autonomy_expansion)
            record = resolved.autonomy_expansion_history[-1]
            self.assertEqual(record.decision, AutonomyExpansionDecision.AUTHORIZE)
            self.assertEqual(record.resolved_envelope, pending.proposed_envelope)
            self.assertEqual(
                core.evaluate_autonomy(resolved, "commit", (RepositoryPath("evospec/domain.py"),)).authorization,
                AutonomyAuthorization.ALLOWED,
            )
            self.assertEqual(
                core.evaluate_autonomy(resolved, "ci").authorization,
                AutonomyAuthorization.ALLOWED,
            )
            self.assertEqual(
                core.evaluate_autonomy(resolved, "commit", (RepositoryPath("evospec-extra/file.py"),)).authorization,
                AutonomyAuthorization.HUMAN_MATERIAL_DECISION,
            )
            self.assertEqual(
                core.evaluate_autonomy(
                    resolved,
                    "push",
                    (RepositoryPath("tests/test_autonomy_expansion.py"),),
                    irreversible=True,
                ).authorization,
                AutonomyAuthorization.ALLOWED,
            )
            for protected in (
                "push --force-with-lease",
                "reset --hard",
                "tag v1",
                "release",
            ):
                self.assertEqual(
                    core.evaluate_autonomy(resolved, protected).authorization,
                    AutonomyAuthorization.HUMAN_MATERIAL_DECISION,
                )
            self.assertEqual(CoreService.open(root).recover(), resolved)

    def test_rejection_preserves_the_envelope_and_records_the_choice(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow, core = active_work(root)
            initial = core.recover()
            if initial.active_context is None:
                self.fail("expected active work before proposal")
            original = initial.active_context.autonomy_envelope
            proposed = core.propose_autonomy_expansion(initial, delta())
            pending = proposed.pending_autonomy_expansion
            if pending is None:
                self.fail("expected pending proposal")
            workflow.resolve_autonomy_expansion(
                str(pending.proposal_id),
                AutonomyExpansionDecision.REJECT,
                expected_revision=ExpectedRevision(proposed.revision),
            )
            resolved = CoreService.open(root).recover()
            if resolved.active_context is None:
                self.fail("expected active work after rejection")
            self.assertEqual(resolved.active_context.autonomy_envelope, original)
            record = resolved.autonomy_expansion_history[-1]
            self.assertEqual(record.decision, AutonomyExpansionDecision.REJECT)
            self.assertEqual(record.resolved_envelope, original)

    def test_narrowing_accepts_only_a_strict_subset_of_the_pending_proposal(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow, core = active_work(root)
            proposed = core.propose_autonomy_expansion(core.recover(), delta())
            pending = proposed.pending_autonomy_expansion
            if pending is None:
                self.fail("expected pending proposal")
            narrower = AutonomyEnvelope(
                pending.proposed_envelope.goal,
                (RepositoryPath("evospec"),),
                5,
                ("progress", "commit", "ci"),
                True,
                1,
                (),
                pending.proposed_envelope.stop_conditions,
            )
            workflow.resolve_autonomy_expansion(
                str(pending.proposal_id),
                AutonomyExpansionDecision.NARROW,
                narrower,
                expected_revision=ExpectedRevision(proposed.revision),
            )
            resolved = CoreService.open(root).recover()
            if resolved.active_context is None:
                self.fail("expected active work after narrowing")
            self.assertEqual(resolved.active_context.autonomy_envelope, narrower)
            self.assertEqual(
                resolved.autonomy_expansion_history[-1].decision,
                AutonomyExpansionDecision.NARROW,
            )

    def test_protocol_exposes_exact_proposal_and_rejects_stale_resolution(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, core = active_work(root)
            revision = core.recover().revision
            proposed = json.loads(
                execute(
                    root,
                    request(
                        "propose_autonomy_expansion",
                        expected_revision=revision,
                        delta=protocol_delta(),
                    ),
                )
            )
            proposal = proposed["context"]["pending_autonomy_expansion"]
            self.assertEqual(proposed["result"]["code"], "AUTONOMY_EXPANSION_PROPOSED")
            self.assertIn("resolve_autonomy_expansion", proposed["context"]["legal_actions"])
            self.assertNotIn("progress", proposed["context"]["legal_actions"])
            self.assertNotIn("propose_autonomy_expansion", proposed["context"]["legal_actions"])
            self.assertNotIn("authorize_irreversible", proposed["context"]["legal_actions"])
            resolution_guidance = next(
                item
                for item in proposed["context"]["guidance"]
                if item["action"] == "resolve_autonomy_expansion"
            )
            self.assertEqual(resolution_guidance["intervention"], "USER")
            stale = json.loads(
                execute(
                    root,
                    request(
                        "resolve_autonomy_expansion",
                        expected_revision=revision,
                        proposal_id=proposal["proposal_id"],
                        decision="AUTHORIZE",
                        narrowed_envelope=None,
                    ),
                )
            )
            self.assertEqual(stale["error"]["code"], "STALE_REVISION")
            resolved = json.loads(
                execute(
                    root,
                    request(
                        "resolve_autonomy_expansion",
                        expected_revision=proposed["context"]["revision"],
                        proposal_id=proposal["proposal_id"],
                        decision="AUTHORIZE",
                        narrowed_envelope=None,
                    ),
                )
            )
            self.assertEqual(resolved["result"]["code"], "AUTONOMY_EXPANSION_AUTHORIZED")
            self.assertIsNone(resolved["context"]["pending_autonomy_expansion"])

    def test_protocol_narrowing_uses_the_full_typed_envelope_shape(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, core = active_work(root)
            proposed = json.loads(
                execute(
                    root,
                    request(
                        "propose_autonomy_expansion",
                        expected_revision=core.recover().revision,
                        delta=protocol_delta(),
                    ),
                )
            )
            pending = proposed["context"]["pending_autonomy_expansion"]
            narrowed = dict(pending["proposed_envelope"])
            narrowed.update(
                {
                    "allowed_paths": ["evospec"],
                    "operation_budget": 5,
                    "reversible_actions": ["progress", "commit", "ci"],
                    "retry_budget": 1,
                    "irreversible_permissions": [],
                }
            )
            resolved = json.loads(
                execute(
                    root,
                    request(
                        "resolve_autonomy_expansion",
                        expected_revision=proposed["context"]["revision"],
                        proposal_id=pending["proposal_id"],
                        decision="NARROW",
                        narrowed_envelope=narrowed,
                    ),
                )
            )
            self.assertEqual(resolved["result"]["code"], "AUTONOMY_EXPANSION_NARROWED")
            self.assertEqual(resolved["context"]["autonomy_envelope"]["allowed_paths"], ["evospec"])

    def test_protected_grants_and_broader_narrowing_fail_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            _, core = active_work(Path(temporary))
            invalid = AutonomyEnvelopeDelta(
                operation_budget=10,
                reversible_actions=("progress", "git push --force-with-lease"),
            )
            with self.assertRaises(ServiceError):
                core.propose_autonomy_expansion(core.recover(), invalid)
            weakened_stops = AutonomyEnvelopeDelta(
                operation_budget=10,
                stop_conditions=(),
            )
            with self.assertRaises(ServiceError):
                core.propose_autonomy_expansion(core.recover(), weakened_stops)
            with self.assertRaises(ServiceError):
                core.propose_autonomy_expansion(
                    core.recover(), AutonomyEnvelopeDelta(operation_budget=0)
                )

        with TemporaryDirectory() as temporary:
            workflow, core = active_work(Path(temporary))
            proposed = core.propose_autonomy_expansion(core.recover(), delta())
            pending = proposed.pending_autonomy_expansion
            if pending is None:
                self.fail("expected pending proposal")
            broader = AutonomyEnvelope(
                pending.proposed_envelope.goal,
                (RepositoryPath("."),),
                pending.proposed_envelope.operation_budget + 1,
                pending.proposed_envelope.reversible_actions,
                True,
                pending.proposed_envelope.retry_budget + 1,
                pending.proposed_envelope.irreversible_permissions,
                pending.proposed_envelope.stop_conditions,
            )
            with self.assertRaises(WorkflowError):
                workflow.resolve_autonomy_expansion(
                    str(pending.proposal_id),
                    AutonomyExpansionDecision.NARROW,
                    broader,
                    expected_revision=ExpectedRevision(proposed.revision),
                )
