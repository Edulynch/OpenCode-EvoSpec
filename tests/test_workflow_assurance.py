from __future__ import annotations

import unittest
from pathlib import Path

from evospec.domain import (
    AssessmentConfidence,
    AssessmentDepth,
    AssessmentDepthValue,
    AssessmentEvidence,
    AssessmentOperation,
    CapabilityState,
    ChangeId,
    ConvergenceAssessment,
    Decision,
    DecisionId,
    Drift,
    ExpectedRevision,
    FeatureId,
    IntentAssessment,
    LockAssessment,
    LockAuthorization,
    LockId,
    LockRelease,
    ObservationId,
    ProductTruthContent,
    RepositoryPath,
    ReviewResult,
    SemanticLock,
    SemanticLockRevision,
    VerificationEvidence,
)
from evospec.lifecycle import Classification, ReviewVerdict
from evospec.services import CoreService, StaleRevisionError
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import convergence_assessment


def established_change_assessment(feature_id: str, capability: str) -> IntentAssessment:
    return IntentAssessment(
        AssessmentOperation.CHANGE,
        Classification.PATCH,
        AssessmentConfidence.HIGH,
        CapabilityState.ESTABLISHED,
        capability,
        (capability,),
        FeatureId(feature_id),
        (AssessmentEvidence("caller", RepositoryPath("tests/test_workflow_assurance.py")),),
        (),
        AssessmentDepth(
            AssessmentDepthValue.NORMAL,
            AssessmentDepthValue.NORMAL,
            AssessmentDepthValue.NORMAL,
        ),
        "caller selected an established Core capability",
    )


class WorkflowAssuranceTests(unittest.TestCase):
    def test_invalidate_assurance_resets_only_assurance_with_current_revision(self) -> None:
        # Given: published baseline truth, its active lock, and a verified established change
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
            lock = SemanticLock(LockId("lock-export"), FeatureId(baseline.feature_id), 1)
            workflow.create_lock(
                lock,
                SemanticLockRevision(lock.id, 1, publication, ("delivery",), "preserve delivery"),
            )
            workflow.change(
                "Change export capability",
                established_change_assessment(baseline.feature_id, "Export capability"),
            )
            workflow.record_progress("implement", "export", ("research",), "write", ("test",), "review")
            workflow.assess_locks(
                (
                    LockAssessment(
                        lock.id,
                        1,
                        ("delivery",),
                        (),
                        "preserve delivery",
                        False,
                        False,
                        AssessmentConfidence.HIGH,
                        ("tests",),
                        "unchanged",
                    ),
                )
            )
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            before = CoreService.open(root).recover()

            # When: the facade invalidates assurance at the authoritative revision
            result = workflow.invalidate_assurance(
                expected_revision=ExpectedRevision(before.revision)
            )
            after = CoreService.open(root).recover()
            recovered = WorkflowService.open(root).status()

            # Then: only review-derived assurance resets; Core identity and durable authority remain
            self.assertEqual(result.code, "ASSURANCE_INVALIDATED")
            self.assertEqual(after.features, before.features)
            self.assertEqual(after.changes, before.changes)
            self.assertEqual(after.publications, before.publications)
            self.assertEqual(after.publication_history, before.publication_history)
            self.assertEqual(after.locks, before.locks)
            self.assertEqual(after.lock_revisions, before.lock_revisions)
            if after.active_context is None or before.active_context is None:
                self.fail("expected active change")
            self.assertEqual(after.active_context.feature_id, before.active_context.feature_id)
            self.assertEqual(after.active_context.change_id, before.active_context.change_id)
            self.assertEqual((after.active_context.status, after.active_context.phase), ("IN_PROGRESS", "IMPLEMENT"))
            self.assertIsNone(after.active_context.review)
            self.assertFalse(after.active_context.verification.sufficient)
            self.assertFalse(after.active_context.convergence_complete)
            self.assertEqual(recovered.phase, "IMPLEMENT")
            self.assertIsNone(recovered.review_verdict)
            self.assertFalse(recovered.verification_sufficient)

            with self.assertRaises(StaleRevisionError):
                workflow.invalidate_assurance(
                    expected_revision=ExpectedRevision(before.revision)
                )
            self.assertEqual(CoreService.open(root).recover(), after)

    def test_false_assessment_archives_primary_and_side_without_publishing_truth(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Synchronize README and ROADMAP")
            workflow.record_progress("implement", "docs", (), "sync", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("docs checked",), True))

            primary_done = workflow.converge(
                convergence_assessment(workflow.status().change_id, False)
            )
            primary_state = CoreService.open(root).recover()

            self.assertEqual(primary_done.code, "DONE")
            self.assertEqual(primary_state.publications, ())
            primary_archive = primary_state.terminal_archives[-1]
            if primary_archive.convergence_assessment is None:
                self.fail("expected persisted primary convergence assessment")
            self.assertFalse(primary_archive.convergence_assessment.product_truth_changed)
            workflow = WorkflowService.open(root)
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            primary = CoreService.open(root).recover().active_context
            workflow.change("unrelated: side documentation")
            workflow.change("unrelated: later documentation")
            workflow.record_progress("implement", "side", (), "sync", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("side docs checked",), True))

            side_done = workflow.converge(
                convergence_assessment(workflow.status().change_id, False)
            )
            reloaded = CoreService.open(root).recover()

            self.assertEqual(side_done.code, "DONE")
            self.assertEqual(reloaded.active_context, primary)
            self.assertIsNone(reloaded.preserved_primary_context)
            self.assertIsNotNone(reloaded.pending_intake)
            self.assertEqual(reloaded.publications, ())
            side_archive = reloaded.completed_side_contexts[-1]
            if side_archive.convergence_assessment is None:
                self.fail("expected persisted side convergence assessment")
            self.assertFalse(side_archive.convergence_assessment.product_truth_changed)

    def test_convergence_rejects_foreign_or_malformed_assessment_without_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            before = CoreService.open(root).recover()
            active = before.active_context
            if active is None or active.change_id is None:
                self.fail("expected active Change for convergence assessment validation")

            with self.assertRaises(WorkflowError):
                workflow.converge(convergence_assessment("change-foreign", True))
            with self.assertRaises(WorkflowError):
                workflow.converge(
                    ConvergenceAssessment(
                        active.change_id,
                        False,
                        AssessmentConfidence.HIGH,
                        (),
                        "missing attributable evidence",
                    )
                )

            self.assertEqual(CoreService.open(root).recover(), before)

    def test_true_convergence_requires_explicit_product_truth_before_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            before = CoreService.open(root).recover()
            active = before.active_context
            if active is None or active.change_id is None:
                self.fail("expected verified Change")
            assessment = ConvergenceAssessment(
                active.change_id,
                True,
                AssessmentConfidence.HIGH,
                (
                    AssessmentEvidence(
                        "caller",
                        RepositoryPath("tests/test_workflow_assurance.py"),
                    ),
                ),
                "rationale is not Product Truth",
            )

            with self.assertRaises(WorkflowError):
                workflow.converge(assessment)

            self.assertEqual(CoreService.open(root).recover(), before)

    def test_true_convergence_persists_supplied_product_truth_exactly_once(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "placeholder", (), "test")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            status = workflow.status()
            if status.change_id is None:
                self.fail("expected verified Change")
            content = ProductTruthContent(
                "# Export capability\n\nCaller-authored Product Truth.\n"
            )
            assessment = ConvergenceAssessment(
                ChangeId(status.change_id),
                True,
                AssessmentConfidence.HIGH,
                (
                    AssessmentEvidence(
                        "caller",
                        RepositoryPath("tests/test_workflow_assurance.py"),
                    ),
                ),
                "accepted behavior changed",
                content,
            )

            done = workflow.converge(assessment)
            accepted = CoreService.open(root).recover()

            self.assertEqual(done.code, "DONE")
            self.assertEqual(len(accepted.publication_history), 1)
            self.assertEqual(
                (root / ".evospec" / accepted.publications[0].path.value).read_text(
                    encoding="utf-8"
                ),
                content.value,
            )

    def test_lock_weakening_requires_current_active_decision(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            baseline = workflow.new("Backend API capability")
            workflow.record_progress("implement", "backend", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            if baseline.feature_id is None:
                self.fail("expected accepted Feature identity")
            publication = CoreService.open(root).recover().publications[0]
            lock = SemanticLock(LockId("lock-contract"), FeatureId(baseline.feature_id), 1)
            workflow.create_lock(
                lock,
                SemanticLockRevision(lock.id, 1, publication, ("response-contract", "status-contract"), "preserve callers"),
            )
            changed = workflow.change(
                "Change backend behavior",
                established_change_assessment(baseline.feature_id, "Backend API capability"),
            )
            if changed.change_id is None:
                self.fail("expected current Change identity")
            workflow.record_progress("implement", "backend", (), "work", (), "resume")
            weakened = SemanticLockRevision(lock.id, 2, publication, ("response-contract",), "remove contract protection")
            with self.assertRaises(WorkflowError):
                workflow.revise_lock(weakened)
            decision = Decision(
                DecisionId("decision-weaken"),
                lock.feature_id,
                "constraint",
                "approve lock weakening",
                ChangeId(changed.change_id),
            )
            workflow.record_lock_decision(decision)
            workflow.revise_lock(
                SemanticLockRevision(
                    lock.id,
                    2,
                    publication,
                    ("response-contract",),
                    "remove contract protection",
                    decision.id,
                )
            )
            self.assertEqual(CoreService.open(root).recover().locks[0].current_revision, 2)

    def test_semantic_lock_requires_assessment_and_exact_authorization_then_rebases(self) -> None:
        # Given: accepted backend, mobile, and game Feature truth protected by generic aspects
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            baseline = workflow.new("Backend API capability")
            workflow.record_progress("implement", "backend", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            if baseline.feature_id is None:
                self.fail("expected accepted Feature identity")
            state = CoreService.open(root).recover()
            publication = state.publications[0]
            lock = SemanticLock(LockId("lock-contract"), FeatureId(baseline.feature_id), 1)
            workflow.create_lock(
                lock,
                SemanticLockRevision(lock.id, 1, publication, ("response-contract",), "preserve callers"),
            )
            retired_lock = SemanticLock(LockId("lock-status"), FeatureId(baseline.feature_id), 1)
            workflow.create_lock(
                retired_lock,
                SemanticLockRevision(retired_lock.id, 1, publication, ("status-contract",), "retire obsolete status contract"),
            )
            changed = workflow.change(
                "Change backend behavior",
                established_change_assessment(baseline.feature_id, "Backend API capability"),
            )
            if changed.change_id is None:
                self.fail("expected current Change identity")
            workflow.record_progress("implement", "backend", (), "work", (), "resume")
            workflow.record_lock_decision(
                Decision(
                    DecisionId("decision-retire"),
                    retired_lock.feature_id,
                    "constraint",
                    "retire obsolete status contract",
                    ChangeId(changed.change_id),
                )
            )
            workflow.retire_lock(
                LockRelease(retired_lock.id, 1, DecisionId("decision-retire"))
            )
            assessment = LockAssessment(
                lock.id, 1, ("response-contract",), ("response-contract",), "explicit migration", True, False,
                AssessmentConfidence.HIGH, ("design",), "callers require the new contract",
            )

            # When: the agent reports a protected conflict without authorization
            workflow.assess_locks((assessment,))
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            blocked = CoreService.open(root).recover().active_context

            # Then: review is deterministic rather than accepting the claimed PASS
            if blocked is None or blocked.review is None:
                self.fail("expected blocked review")
            self.assertEqual(blocked.review.verdict, ReviewVerdict.BLOCKED)
            workflow.record_lock_decision(
                Decision(DecisionId("decision-contract"), lock.feature_id, "constraint", "approve exact migration", ChangeId(changed.change_id))
            )
            workflow.authorize_lock(
                LockAuthorization(lock.id, 1, lock.feature_id, ChangeId(changed.change_id), DecisionId("decision-contract"), ("response-contract",))
            )
            workflow.assess_locks((assessment,))
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            reassessed = CoreService.open(root).recover().active_context
            if reassessed is None or reassessed.review is None:
                self.fail("expected reassessment review")
            self.assertEqual(reassessed.review.verdict, ReviewVerdict.BLOCKED)
            workflow.authorize_lock(
                LockAuthorization(
                    lock.id,
                    1,
                    lock.feature_id,
                    ChangeId(changed.change_id),
                    DecisionId("decision-contract"),
                    ("response-contract",),
                    2,
                )
            )
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.pause()
            with self.assertRaises(WorkflowError):
                workflow.create_lock(
                    SemanticLock(LockId("lock-paused"), lock.feature_id, 1),
                    SemanticLockRevision(
                        LockId("lock-paused"),
                        1,
                        publication,
                        ("paused-contract",),
                        "must not retain paused verification evidence",
                    ),
                )
            workflow.continue_work()
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            recovered = CoreService.open(root).recover()
            self.assertEqual(recovered.locks[0].current_revision, 2)
            self.assertFalse(recovered.locks[1].active)
            self.assertEqual(tuple(item.revision for item in recovered.lock_revisions), (1, 1, 2))
    def test_domain_neutral_direct_facade_scenarios(self) -> None:
        # Given: one direct facade over Core-only temporary persistence
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()

            # When: backend, mobile, and game requests use the same workflow contract
            results = []
            for intent in (
                "Backend API capability",
                "Mobile client capability",
                "Game interaction capability",
            ):
                results.append(workflow.new(intent))
                workflow.reject()

            # Then: each domain remains a normal persisted workflow request
            self.assertEqual(tuple(item.code for item in results), ("PROPOSED",) * 3)

    def test_intake_research_persists_discovery_scoped_depth_requirements(self) -> None:
        # Given: discovered repository authority and a high-impact request
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_text("repository instructions", encoding="utf-8")
            workflow = WorkflowService.open(root)
            workflow.init()

            # When: the workflow accepts the request through its intake facade
            workflow.new("Foundation storage migration")
            recovered = CoreService.open(root).recover().active_context

            # Then: research facts from discovery determine durable depth requirements
            if recovered is None:
                self.fail("expected active context after intake")
            self.assertEqual(recovered.safety_facts.scope, ("AGENTS.md",))
            self.assertEqual(recovered.safety_facts.review_depth, "DEEP")
            self.assertEqual(recovered.safety_facts.verification_depth, "FULL")
            self.assertEqual(recovered.safety_facts.convergence_depth, "FULL")

    def test_review_computes_blocked_when_required_progress_evidence_is_missing(self) -> None:
        # Given: implementation work with no current progress evidence
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), None, (), "resume")

            # When: a caller claims PASS without supplying findings
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            recovered = CoreService.open(root).recover().active_context

            # Then: persisted evidence gates the computed verdict instead of the claim
            if recovered is None:
                self.fail("expected active context after review")
            review = recovered.review
            if review is None:
                self.fail("expected persisted review after review")
            self.assertEqual(review.verdict, ReviewVerdict.BLOCKED)
            self.assertNotIn("TECHNICAL", tuple(item.category for item in recovered.review_evidence))

    def test_current_drift_reconciliation_survives_reload_and_requires_its_change(self) -> None:
        # Given: an accepted baseline and a later persisted change
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            baseline = workflow.new("Baseline capability")
            workflow.record_progress("implement", "baseline", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("baseline tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            change = WorkflowService.open(root)
            if baseline.feature_id is None:
                self.fail("expected established Core Feature identity")
            intake = change.change(
                "Change baseline behavior",
                assessment=established_change_assessment(
                    baseline.feature_id, "Baseline capability"
                ),
            )
            intake_change_id = intake.change_id
            intake_feature_id = intake.feature_id
            if intake_change_id is None or intake_feature_id is None:
                self.fail("expected change intake identifiers")
            change.record_progress("implement", "baseline", (), "work", (), "resume")
            prior_truth = CoreService.open(root).recover().publications[-1]

            # When: the active change records and reconciles a drift before a disk reload
            change.reconcile_drift(
                Drift(
                    ObservationId("drift-baseline"),
                    prior_truth.living_spec_id,
                    "runtime differs",
                    ChangeId(intake_change_id),
                ),
                Decision(
                    DecisionId("decision-baseline"),
                    FeatureId(intake_feature_id),
                    "constraint",
                    "restore accepted behavior",
                    ChangeId(intake_change_id),
                ),
            )
            reloaded = WorkflowService.open(root)
            recovered = CoreService.open(root).recover()

            # Then: durable reconciliation is linked to the exact active change
            self.assertEqual(reloaded.status().change_id, intake_change_id)
            self.assertEqual(recovered.drifts[-1].change_id, ChangeId(intake_change_id))
            self.assertFalse(recovered.drifts[-1].unresolved)
            self.assertEqual(recovered.decisions[-1].change_id, ChangeId(intake_change_id))

    def test_change_research_includes_established_persisted_truth(self) -> None:
        # Given: an accepted capability with an immutable Living Spec publication
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            initial = workflow.new("Initial export delivery")
            initial_change_id = initial.change_id
            initial_feature_id = initial.feature_id
            workflow.record_progress("implement", "export", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("initial tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))

            # When: a fresh facade opens a later change
            if initial_feature_id is None:
                self.fail("expected established Core Feature identity")
            changed = WorkflowService.open(root).change(
                "Change export delivery behavior",
                assessment=established_change_assessment(
                    initial_feature_id, "Initial export delivery"
                ),
            )
            changed_change_id = changed.change_id
            changed_feature_id = changed.feature_id
            if changed_change_id is None:
                self.fail("expected change intake identifier")
            context = CoreService.open(root).recover().active_context

            # Then: its durable research scope contains the established publication
            if context is None:
                self.fail("expected active context after change intake")
            self.assertEqual(context.change_id, ChangeId(changed_change_id))
            self.assertEqual(changed_feature_id, initial_feature_id)
            self.assertNotEqual(changed_change_id, initial_change_id)
            self.assertTrue(any(item.startswith("living-specs/") for item in context.safety_facts.scope))

    def test_foundation_requires_deeper_review_and_verification_than_tune(self) -> None:
        # Given: two otherwise equivalent persisted requests
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            tune = WorkflowService.open(root)
            tune.init()
            tune.new("Tune export delay")
            tune.record_progress("implement", "export", (), "work", (), "resume")
            tune.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            tune_context = CoreService.open(root).recover().active_context
            if tune_context is None:
                self.fail("expected active context after tune review")
            tune_review = tune_context.review
            if tune_review is None:
                self.fail("expected persisted tune review")
            tune.record_verification(VerificationEvidence(("targeted test",), True))
            tune.reject()
            foundation = WorkflowService.open(root)
            foundation.new("Foundation storage migration")
            foundation.record_progress("implement", "export", (), "work", (), "resume")

            # When: both classifications receive the same shallow evidence
            foundation.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            foundation_context = CoreService.open(root).recover().active_context
            if foundation_context is None:
                self.fail("expected active context after foundation review")
            foundation_review = foundation_context.review
            if foundation_review is None:
                self.fail("expected persisted foundation review")

            # Then: the deeper classification does not accept shallow review evidence
            self.assertEqual(tune_review.verdict, ReviewVerdict.PASS)
            self.assertEqual(foundation_review.verdict, ReviewVerdict.BLOCKED)

    def test_foreign_unresolved_drift_does_not_block_a_different_change(self) -> None:
        # Given: a prior publication with a drift owned by its original change
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            first = workflow.new("Baseline capability")
            first_change_id = first.change_id
            if first_change_id is None:
                self.fail("expected baseline intake identifier")
            workflow.record_progress("implement", "baseline", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("baseline tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            state = CoreService.open(root).recover()
            state = CoreService.open(root).record_drift(
                state,
                Drift(ObservationId("drift-foreign"), state.publications[-1].living_spec_id, "runtime differs", ChangeId(first_change_id)),
            )
            current = WorkflowService.open(root)
            if first.feature_id is None:
                self.fail("expected established Core Feature identity")
            intake = current.change(
                "Change baseline behavior",
                assessment=established_change_assessment(
                    first.feature_id, "Baseline capability"
                ),
            )
            intake_change_id = intake.change_id
            intake_feature_id = intake.feature_id
            if intake_change_id is None or intake_feature_id is None:
                self.fail("expected current change intake identifiers")
            current.record_progress("implement", "baseline", (), "work", (), "resume")
            state = CoreService.open(root).recover()
            state = CoreService.open(root).record_decision(
                state,
                Decision(DecisionId("decision-current"), FeatureId(intake_feature_id), "constraint", "current change only", ChangeId(intake_change_id)),
            )
            current.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            current.record_verification(VerificationEvidence(("current tests",), True))

            # When: current work attempts convergence
            done = current.converge(convergence_assessment(current.status().change_id, True))

            # Then: unrelated unresolved drift remains durable but scoped to its owner
            self.assertEqual(done.code, "DONE")
            self.assertEqual(CoreService.open(root).recover().drifts[-1].change_id, ChangeId(first_change_id))

    def test_explicit_review_is_read_only_and_convergence_reaches_done(self) -> None:
        # Given: persisted implementation work
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "work", (), "resume")
            before = workflow.status().revision

            # When: review is inspected, then sufficient evidence is supplied and converged
            inspection = workflow.review()
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("unit tests",), True))
            done = workflow.converge(convergence_assessment(workflow.status().change_id, True))

            # Then: inspection did not mutate and terminal history is persisted
            self.assertEqual(inspection.revision, before)
            self.assertEqual(done.code, "DONE")
            self.assertEqual(WorkflowService.open(Path(temporary)).status().code, "STATUS")
