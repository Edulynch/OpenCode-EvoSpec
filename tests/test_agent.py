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
    Change,
    ChangeId,
    Decision,
    DecisionId,
    FeatureId,
    Feature,
    IntentAssessment,
    MaterialDecision,
    LockAssessment,
    LockId,
    LockRelease,
    LivingSpecId,
    RepositoryPath,
    ReviewResult,
    SemanticLock,
    SemanticLockRevision,
    VerificationEvidence,
    OperationalState,
)
from evospec.lifecycle import Classification, LifecycleState, Phase, ReviewVerdict
from evospec.services import CoreService
from evospec.workflow import ClarificationAnswer, WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import convergence_assessment
from tests.test_workflow_assurance import established_change_assessment


class AgentContextTests(unittest.TestCase):
    def test_context_projects_active_lifecycle_decisions_progress_and_legal_actions(self) -> None:
        # Given: initialized work with durable implementation progress
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Add export capability")
            workflow.record_progress(
                "implement export", "delivery", ("research",), "write adapter", ("test adapter",), "run tests"
            )

            # When: an agent asks for its provider-neutral context
            context = project_context(workflow)

            # Then: it receives deterministic active work and legal next actions
            self.assertEqual(context.lifecycle, ("IN_PROGRESS", "IMPLEMENT"))
            self.assertEqual(context.progress.current_work, "write adapter")
            self.assertEqual(context.progress.pending_work, ("test adapter",))
            self.assertEqual(context.decision_state.unresolved_material_decision, False)
            self.assertEqual(context.side_work, "PRIMARY")
            self.assertEqual(
                context.legal_actions,
                ("context", "list_work", "next_work", "park", "progress", "assess_locks", "review", "reject", "authorize_irreversible", "propose_autonomy_expansion"),
            )
            self.assertFalse(context.stale)

    def test_recovered_context_projects_persisted_material_question_for_clarification(self) -> None:
        # Given: active work with one persisted material ambiguity
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            intake = workflow.new(
                "Add export delivery",
                assessment=IntentAssessment(
                    AssessmentOperation.NEW,
                    Classification.FEATURE,
                    AssessmentConfidence.HIGH,
                    CapabilityState.PLANNED,
                    "Export delivery",
                    ("Export delivery",),
                    None,
                    (AssessmentEvidence("test", RepositoryPath("tests/test_agent.py")),),
                    (MaterialDecision("delivery channel", ("email", "download")),),
                    AssessmentDepth(
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                    ),
                    "delivery channel requires a product decision",
                ),
            )

            # When: a fresh workflow instance projects authoritative context
            recovered = WorkflowService.open(root)
            context = project_context(recovered)
            ready = recovered.clarify(
                (ClarificationAnswer(context.questions[0].id, "email"),)
            )

            # Then: the exact persisted question remains actionable and resolves normally
            self.assertEqual(context.questions, intake.questions)
            self.assertEqual(ready.code, "READY")

    def test_uninitialized_context_only_advertises_initialization(self) -> None:
        with TemporaryDirectory() as temporary:
            context = project_context(WorkflowService.open(Path(temporary)))

            self.assertEqual(context.initialization_state, "UNINITIALIZED")
            self.assertEqual(context.legal_actions, ("init", "context"))
            self.assertEqual(context.guidance[0].action.value, "context")

    def test_initialized_inactive_context_is_distinct_from_uninitialized(self) -> None:
        # Given: initialized authority with no active lifecycle work
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()

            # When: context is projected
            context = project_context(workflow)

            # Then: initialization remains explicit despite a missing lifecycle pair
            self.assertIsNone(context.lifecycle)
            self.assertEqual(context.initialization_state, "INITIALIZED")
            self.assertEqual(
                context.legal_actions,
                ("context", "new", "change", "list_work", "next_work"),
            )

    def test_context_projects_bounded_work_queue_summary(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            started = workflow.new("Add export capability")
            workflow.record_progress("implement", "delivery", (), "write adapter", (), "run tests")
            workflow.park("waiting for the integration environment")

            context = project_context(workflow)

            if started.change_id is None:
                self.fail("expected parked change identity")
            self.assertEqual(context.work_queue.total, 1)
            self.assertTrue(context.work_queue.complete)
            self.assertEqual(context.work_queue.pending[0].change_id, started.change_id)
            self.assertEqual(context.work_queue.pending[0].lifecycle, ("IN_PROGRESS", "IMPLEMENT"))
            self.assertEqual(context.work_queue.frozen[0].reason, "waiting for the integration environment")
            self.assertEqual(context.work_queue.next_code, "NONE")
            self.assertIn("resume", context.legal_actions)
            self.assertNotIn("park", context.legal_actions)

    def test_queue_guidance_and_bounded_ambiguous_candidates_follow_core_eligibility(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            core = CoreService.open(root)
            for index in range(33):
                workflow.new("x" * (index + 1))
                workflow.park("deferred")
            state = core.recover()
            for item in state.work_items:
                state = core.set_work_state(state, item.id, OperationalState.READY)

            context = project_context(workflow)

            self.assertIn("list_work", context.legal_actions)
            self.assertIn("next_work", context.legal_actions)
            self.assertIn("activate_work", context.legal_actions)
            self.assertIn("set_work_state", context.legal_actions)
            self.assertNotIn("remove_work_relation", context.legal_actions)
            self.assertEqual(context.work_queue.next_code, "AMBIGUOUS")
            self.assertEqual(len(context.work_queue.next_candidates), 32)
            self.assertEqual(context.work_queue.next_candidates_total, 33)
            self.assertFalse(context.work_queue.next_candidates_complete)

    def test_verification_advertises_convergence_only_when_sufficient(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Add export capability")
            workflow.record_progress("implement", "delivery", (), "write adapter", (), "run tests")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            self.assertIn("verify", project_context(workflow).legal_actions)
            workflow.record_verification(VerificationEvidence(("tests",), False))

            insufficient = project_context(workflow)
            self.assertIn("verify", insufficient.legal_actions)
            self.assertNotIn("converge", insufficient.legal_actions)

            workflow.record_verification(VerificationEvidence(("tests",), True))
            self.assertIn("converge", project_context(workflow).legal_actions)

    def test_review_projection_uses_core_acceptability_predicate(self) -> None:
        review_cases = (
            (ReviewVerdict.BLOCKED, False, False),
            (ReviewVerdict.WARN, False, False),
            (ReviewVerdict.PASS, True, True),
        )
        for verdict, safe_to_proceed, expects_verification in review_cases:
            with self.subTest(verdict=verdict, safe_to_proceed=safe_to_proceed), TemporaryDirectory() as temporary:
                # Given: implementation-complete work with the supplied review outcome
                workflow = WorkflowService.open(Path(temporary))
                workflow.init()
                workflow.new("Add export capability")
                workflow.record_progress("implement", "delivery", (), "write adapter", (), "run tests")
                workflow.record_review(ReviewResult(verdict, (), safe_to_proceed))

                # When: the protocol context is projected after review
                legal_actions = project_context(workflow).legal_actions

                # Then: verification is advertised only for Core-acceptable reviews
                self.assertEqual("verify" in legal_actions, expects_verification)

    def test_convergence_projection_uses_core_convergence_predicate(self) -> None:
        # Given: a safe review and sufficient verification
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Add export capability")
            workflow.record_progress("implement", "delivery", (), "write adapter", (), "run tests")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))

            # When: verification-phase context is projected
            legal_actions = project_context(workflow).legal_actions

            # Then: convergence is legal under the same Core predicate
            self.assertIn("converge", legal_actions)

    def test_retired_historical_assessment_does_not_stale_remaining_active_lock(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            baseline = workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            if baseline.feature_id is None:
                self.fail("expected accepted feature")
            publication = CoreService.open(root).recover().publications[0]
            active = SemanticLock(LockId("lock-delivery"), FeatureId(baseline.feature_id), 1)
            retired = SemanticLock(LockId("lock-retention"), FeatureId(baseline.feature_id), 1)
            for lock, aspect in ((active, "delivery"), (retired, "retention")):
                workflow.create_lock(lock, SemanticLockRevision(lock.id, 1, publication, (aspect,), f"preserve {aspect}"))
            changed = workflow.change(
                "Change export capability",
                established_change_assessment(baseline.feature_id, "Export capability"),
            )
            if changed.change_id is None:
                self.fail("expected active change")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.assess_locks(tuple(
                LockAssessment(lock.id, 1, (aspect,), (), f"preserve {aspect}", False, False, AssessmentConfidence.HIGH, ("tests",), "unchanged")
                for lock, aspect in ((active, "delivery"), (retired, "retention"))
            ))
            workflow.record_lock_decision(Decision(DecisionId("retire-retention"), active.feature_id, "constraint", "retire retention", ChangeId(changed.change_id)))
            workflow.retire_lock(LockRelease(retired.id, 1, DecisionId("retire-retention")))

            self.assertFalse(project_context(workflow).stale)

    def test_context_marks_side_work_and_pending_intake(self) -> None:
        # Given: primary work with an active side request and deferred intake
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            core = CoreService.open(Path(temporary))
            primary = core.recover().active_context
            if primary is None:
                self.fail("expected active primary")
            side_feature = Feature(FeatureId("feature-side"), "side", LivingSpecId("spec-feature-side"), primary.feature_id)
            state = core.record_feature(core.recover(), side_feature)
            core.activate_side(state, Change(ChangeId("change-side"), side_feature.id, "unrelated: documentation", Classification.PATCH))

            # When: the side work is projected
            context = project_context(workflow)

            # Then: the agent receives explicit side-work and deferral signals
            self.assertEqual(context.side_work, "SIDE")
            self.assertTrue(context.side_work_active)
            self.assertFalse(context.pending_intake)
            guidance = next(item for item in context.guidance if item.action.value == "change")
            self.assertFalse(guidance.allowed)
            self.assertEqual(guidance.reason_code, "ACTIVE_WORK")

    def test_inactive_context_projects_only_accepted_capabilities_and_active_lock_metadata(self) -> None:
        # Given: accepted Product Truth, an active Feature-owned lock, and an unpublished Feature
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            accepted = workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            if accepted.feature_id is None:
                self.fail("expected accepted feature")
            publication = CoreService.open(root).recover().publications[0]
            workflow.create_lock(
                SemanticLock(LockId("lock-export"), FeatureId(accepted.feature_id), 1),
                SemanticLockRevision(
                    LockId("lock-export"), 1, publication, ("delivery",), "preserve delivery"
                ),
            )
            workflow.change(
                "Project-only capability",
                assessment=IntentAssessment(
                    AssessmentOperation.CHANGE,
                    Classification.PATCH,
                    AssessmentConfidence.HIGH,
                    CapabilityState.PROJECT_ESTABLISHED,
                    "Project-only capability",
                    ("Project-only capability",),
                    None,
                    (AssessmentEvidence("test", RepositoryPath("tests/test_agent.py")),),
                    (),
                    AssessmentDepth(
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                    ),
                    "project evidence is not accepted Product Truth",
                ),
            )
            workflow.reject()

            # When: a new agent recovers inactive workflow context
            context = project_context(WorkflowService.open(root))

            # Then: only accepted authority and lock metadata are available for an ESTABLISHED change
            self.assertIsNone(context.lifecycle)
            self.assertTrue(context.accepted_capabilities_complete)
            self.assertEqual(len(context.accepted_capabilities), 1)
            capability = context.accepted_capabilities[0]
            self.assertEqual(capability.feature_id, accepted.feature_id)
            self.assertEqual(capability.title, "Export capability")
            self.assertEqual(capability.capability, "Export capability")
            self.assertEqual(capability.living_spec_id, publication.living_spec_id)
            self.assertEqual(capability.publication_revision, publication.revision)
            self.assertEqual(capability.publication_path, str(publication.path))
            self.assertTrue(context.active_locks_complete)
            self.assertEqual(len(context.active_locks), 1)
            lock = context.active_locks[0]
            self.assertEqual(lock.owner_feature_id, accepted.feature_id)
            self.assertEqual(lock.lock_id, "lock-export")
            self.assertEqual(lock.lock_revision, 1)
            self.assertEqual(lock.baseline_living_spec_id, publication.living_spec_id)
            self.assertEqual(lock.baseline_revision, publication.revision)
            self.assertEqual(lock.baseline_path, str(publication.path))
            self.assertEqual(lock.protected_aspects, ("delivery",))

    def test_context_projects_external_sdd_structure_without_mutating_recovery(self) -> None:
        # Given: a fresh repository with supported foreign SDD structure
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".specify").mkdir()
            (root / "openspec" / "changes").mkdir(parents=True)
            WorkflowService.open(root).init()
            before = CoreService.open(root).recover()

            # When: a fresh agent projects context
            context = project_context(WorkflowService.open(root))

            # Then: bounded structural facts are visible without changing Core state
            self.assertEqual(
                tuple((item.framework, item.paths) for item in context.external_sdd_evidence),
                (("OPENSPEC", ("openspec/changes/",)), ("SPEC_KIT", (".specify/",))),
            )
            self.assertEqual(context.external_sdd_evidence_total, 2)
            self.assertTrue(context.external_sdd_evidence_complete)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_context_projects_core_owned_autonomy_policy_metrics_and_evaluation(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "delivery", (), "write", (), "test")

            context = project_context(workflow)

            self.assertIsNotNone(context.autonomy_envelope)
            if context.autonomy_envelope is None or context.autonomy_evaluation is None:
                self.fail("expected projected autonomy facts")
            self.assertEqual(context.autonomy_envelope.goal, "Export capability")
            self.assertEqual(context.autonomy_envelope.allowed_paths, ())
            self.assertEqual(context.autonomy_metrics.automatic_operations, 1)
            self.assertEqual(context.autonomy_evaluation.authorization, "ALLOWED")
