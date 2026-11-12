from __future__ import annotations

import unittest
from pathlib import Path

from evospec.agent import project_context
from evospec.domain import (
    AssessmentConfidence,
    AssessmentDepth,
    AssessmentDepthValue,
    AssessmentEvidence,
    AssessmentOperation,
    CapabilityState,
    IntentAssessment,
    MaterialDecision,
    OperationalState,
    RepositoryPath,
    ReviewResult,
    VerificationEvidence,
)
from evospec.guidance import Intervention, MaterialDecisionEvidence, Outcome, OutcomeKind
from evospec.lifecycle import Classification, ReviewVerdict
from evospec.services import CoreService
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory


class GuidancePreflightTests(unittest.TestCase):
    def test_outcome_kind_is_exact_and_enforces_evidence_and_intervention_invariants(self) -> None:
        self.assertEqual(
            [kind.value for kind in OutcomeKind],
            [
                "HUMAN_MATERIAL_DECISION",
                "SAFE_AUTOMATIC_OPERATION",
                "BOUNDED_DELEGATION",
                "EXTERNAL_WAIT",
                "STRUCTURAL_DEFECT",
            ],
        )
        evidence = MaterialDecisionEvidence("select a capability", ("existing", "new"))
        outcomes = (
            (OutcomeKind.HUMAN_MATERIAL_DECISION, evidence, Intervention.USER),
            (OutcomeKind.SAFE_AUTOMATIC_OPERATION, None, Intervention.AUTOMATIC),
            (OutcomeKind.BOUNDED_DELEGATION, None, Intervention.AUTOMATIC),
            (OutcomeKind.EXTERNAL_WAIT, None, Intervention.AUTOMATIC),
            (OutcomeKind.STRUCTURAL_DEFECT, None, Intervention.NONE),
        )
        for kind, material_decision, intervention in outcomes:
            self.assertEqual(Outcome(kind, "fact-derived", material_decision).intervention, intervention)
        with self.assertRaises(ValueError):
            Outcome(OutcomeKind.HUMAN_MATERIAL_DECISION, "missing evidence")
        with self.assertRaises(ValueError):
            Outcome(OutcomeKind.BOUNDED_DELEGATION, "delegation", evidence)

    def test_outcome_taxonomy_is_fact_derived_and_human_requires_material_choices(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)

            uninitialized = project_context(workflow).outcome
            self.assertIsNotNone(uninitialized)
            assert uninitialized is not None
            self.assertEqual(uninitialized.kind, OutcomeKind.STRUCTURAL_DEFECT)
            self.assertEqual(uninitialized.intervention.value, "NONE")

            workflow.init()
            initialized = project_context(workflow).outcome
            self.assertIsNotNone(initialized)
            assert initialized is not None
            self.assertEqual(initialized.kind, OutcomeKind.SAFE_AUTOMATIC_OPERATION)
            workflow.new("Export capability", IntentAssessment(
                AssessmentOperation.NEW,
                Classification.FEATURE,
                AssessmentConfidence.HIGH,
                CapabilityState.PLANNED,
                "Export capability",
                (),
                None,
                (AssessmentEvidence("test", RepositoryPath("tests/test_guidance.py")),),
                (MaterialDecision("delivery channel", ("email", "download")),),
                AssessmentDepth(
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                ),
                "material clarification is required",
            ))
            clarification = project_context(workflow).outcome
            self.assertIsNotNone(clarification)
            assert clarification is not None
            self.assertEqual(clarification.kind, OutcomeKind.HUMAN_MATERIAL_DECISION)
            self.assertEqual(clarification.material_decision, MaterialDecisionEvidence("delivery channel", ("email", "download")))

            workflow.park("external verification")
            parked = CoreService.open(root).recover().work_items[0]
            CoreService.open(root).set_work_state(
                CoreService.open(root).recover(), parked.id, OperationalState.WAITING,
            )
            waiting = project_context(WorkflowService.open(root)).outcome
            self.assertIsNotNone(waiting)
            assert waiting is not None
            self.assertEqual(waiting.kind, OutcomeKind.EXTERNAL_WAIT)
            self.assertEqual(waiting.intervention.value, "AUTOMATIC")

    def test_uninitialized_and_idle_clarification_are_non_mutating_guidance_blocks(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            before = workflow.inspect_locks().state
            blocked = workflow.clarify()
            self.assertEqual(blocked.code, "INIT_REQUIRED")
            self.assertEqual(workflow.inspect_locks().state, before)
            initialization = next(item for item in project_context(workflow).guidance if item.action.value == "init")
            self.assertEqual(initialization.intervention.value, "USER")
            workflow.init()
            idle = workflow.clarify()
            self.assertEqual(idle.code, "NO_ACTIVE_WORK")
            context = project_context(workflow)
            clarify = next(item for item in context.guidance if item.action.value == "clarify")
            self.assertEqual(clarify.required_actions[0].value, "new")

    def test_clarification_not_required_and_premature_assurance_blocks_preserve_revision(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Export capability")
            clarification = workflow.clarify()
            self.assertEqual(clarification.code, "CLARIFICATION_NOT_REQUIRED")
            before = workflow.inspect_locks().state
            self.assertEqual(workflow.record_verification(VerificationEvidence(("tests",), True)).code, "REVIEW_REQUIRED")
            convergence = next(item for item in project_context(workflow).guidance if item.action.value == "converge")
            self.assertEqual(convergence.reason_code, "REVIEW_REQUIRED")
            self.assertEqual(workflow.inspect_locks().state, before)

    def test_paused_and_invalidated_assurance_are_preflighted_without_partial_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.pause()
            paused = workflow.inspect_locks().state
            continuation = next(item for item in project_context(workflow).guidance if item.action.value == "continue")
            self.assertTrue(continuation.allowed)
            self.assertNotEqual(continuation.next_action, continuation.action)
            self.assertEqual(workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True)).code, "NOTHING_TO_RESUME")
            self.assertEqual(workflow.inspect_locks().state, paused)
            workflow.continue_work()
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            convergence = next(item for item in project_context(workflow).guidance if item.action.value == "converge")
            self.assertTrue(convergence.allowed)
            self.assertEqual(convergence.intervention.value, "USER")
            workflow.invalidate_assurance()
            invalidated = workflow.inspect_locks().state
            convergence = next(item for item in project_context(workflow).guidance if item.action.value == "converge")
            self.assertEqual(convergence.reason_code, "REVIEW_REQUIRED")
            self.assertEqual(workflow.record_verification(VerificationEvidence(("tests",), True)).code, "REVIEW_REQUIRED")
            self.assertEqual(workflow.inspect_locks().state, invalidated)
