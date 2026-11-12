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
    Decision,
    DecisionId,
    Drift,
    ExpectedRevision,
    ExperimentOutcome,
    Feature,
    FeatureId,
    IntentAssessment,
    PendingDiscoveryRecovery,
    LivingSpecId,
    ObservationId,
    RepositoryPath,
    ReviewResult,
    VerificationEvidence,
)
from evospec.lifecycle import Classification, ReviewVerdict
from evospec.services import CoreService
from evospec.workflow import ClarificationAnswer, WorkflowError, WorkflowService
from evospec.domain import MaterialDecision
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import convergence_assessment


def material_new_assessment(capability: str, ambiguity: str) -> IntentAssessment:
    return IntentAssessment(
        AssessmentOperation.NEW,
        Classification.FEATURE,
        AssessmentConfidence.HIGH,
        CapabilityState.PLANNED,
        capability,
        (capability,),
        None,
        (AssessmentEvidence("caller", RepositoryPath("tests/test_workflow_dogfood.py")),),
        (MaterialDecision(ambiguity, ("email", "Add export retry delay")),),
        AssessmentDepth(
            AssessmentDepthValue.NORMAL,
            AssessmentDepthValue.NORMAL,
            AssessmentDepthValue.NORMAL,
        ),
        "caller identified a material decision",
    )


def established_change_assessment(feature_id: str, capability: str) -> IntentAssessment:
    return IntentAssessment(
        AssessmentOperation.CHANGE,
        Classification.PATCH,
        AssessmentConfidence.HIGH,
        CapabilityState.ESTABLISHED,
        capability,
        (capability,),
        FeatureId(feature_id),
        (AssessmentEvidence("caller", RepositoryPath("tests/test_workflow_dogfood.py")),),
        (),
        AssessmentDepth(
            AssessmentDepthValue.NORMAL,
            AssessmentDepthValue.NORMAL,
            AssessmentDepthValue.NORMAL,
        ),
        "caller selected an established Core capability",
    )


class WorkflowDogfoodTests(unittest.TestCase):
    def _bounded_recovery(self, root: Path) -> tuple[WorkflowService, PendingDiscoveryRecovery, int]:
        (root / "evidence-one.md").write_text("External Blocker Reconciliation", encoding="utf-8")
        (root / "evidence-two.md").write_text("External Blocker Reconciliation", encoding="utf-8")
        overflow = root / "overflow"
        overflow.mkdir()
        for index in range(129):
            (overflow / f"z-{index:03}.txt").write_text("noise", encoding="utf-8")
        workflow = WorkflowService.open(root)
        workflow.init()
        limited = workflow.new("External Blocker Reconciliation")
        recovery = CoreService.open(root).recover().pending_discovery_recovery
        if recovery is None:
            self.fail("expected persisted discovery recovery")
        self.assertEqual(limited.code, "DISCOVERY_RECOVERY_REQUIRED")
        return workflow, recovery, limited.revision

    def test_bounded_new_recovery_blocks_intake_and_requires_exact_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow, recovery, revision = self._bounded_recovery(root)
            before = CoreService.open(root).recover()

            blocked = workflow.new("Another capability")
            forged = workflow.recover_discovery(
                str(recovery.id), recovery.candidates[0], ("evidence-forged",),
                expected_revision=ExpectedRevision(revision),
            )

            self.assertEqual(blocked.code, "PENDING_DISCOVERY_RECOVERY")
            self.assertEqual(forged.code, "INVALID_DISCOVERY_RECOVERY")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_bounded_new_recovery_revalidates_fingerprints_and_is_revision_bound(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow, recovery, revision = self._bounded_recovery(root)
            evidence_ids = tuple(item.id for item in recovery.evidence if item.candidate == recovery.candidates[0])
            (root / "evidence-one.md").write_text("tampered", encoding="utf-8")

            tampered = workflow.recover_discovery(
                str(recovery.id), recovery.candidates[0], evidence_ids,
                expected_revision=ExpectedRevision(revision),
            )

            self.assertEqual(tampered.code, "INVALID_DISCOVERY_RECOVERY")
            self.assertIsNotNone(CoreService.open(root).recover().pending_discovery_recovery)

    def test_pre_evidence_recovery_remains_pending_until_evidence_is_collected(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            recovery = PendingDiscoveryRecovery(
                "discovery-pre-evidence",
                AssessmentOperation.NEW,
                "External Blocker Reconciliation",
                ("external blocker reconciliation",),
                (),
            )
            core = CoreService.open(root)
            before = core.record_discovery_recovery(core.recover(), recovery)

            result = workflow.recover_discovery(
                str(recovery.id), recovery.candidates[0], (), expected_revision=ExpectedRevision(before.revision)
            )

            self.assertEqual(result.code, "INVALID_DISCOVERY_RECOVERY")
            self.assertEqual(core.recover(), before)

    def test_bounded_new_recovery_transitions_to_existing_selection_without_changing_selection_rules(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow, recovery, revision = self._bounded_recovery(root)
            core = CoreService.open(root)
            state = core.record_feature(core.recover(), Feature(FeatureId("feature-one"), "External Blocker Reconciliation", None))
            state = core.record_feature(state, Feature(FeatureId("feature-two"), "External Blocker Reconciliation", None))
            evidence_ids = tuple(item.id for item in recovery.evidence if item.candidate == recovery.candidates[0])

            ambiguous = workflow.recover_discovery(
                str(recovery.id), recovery.candidates[0], evidence_ids,
                expected_revision=ExpectedRevision(state.revision),
            )
            pending = CoreService.open(root).recover().pending_capability_selection
            if pending is None:
                self.fail("expected Core-issued capability selection")
            replay = workflow.recover_discovery(
                str(recovery.id), recovery.candidates[0], evidence_ids,
                expected_revision=ExpectedRevision(ambiguous.revision),
            )

            self.assertEqual(ambiguous.code, "AMBIGUOUS_CAPABILITY")
            self.assertIsNone(CoreService.open(root).recover().pending_discovery_recovery)
            self.assertEqual(replay.code, "NO_PENDING_DISCOVERY_RECOVERY")
            self.assertEqual(
                workflow.select_capability(
                    str(pending.id), "feature-forged", expected_revision=ExpectedRevision(ambiguous.revision)
                ).code,
                "INVALID_CAPABILITY_SELECTION",
            )

    def test_assurance_invalidation_on_side_work_preserves_its_primary(self) -> None:
        # Given: a verified side change holding an implementation primary context
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            primary = CoreService.open(root).recover().active_context
            workflow.change("unrelated: documentation")
            workflow.record_progress("implement", "side", (), "write", (), "review")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))

            # When: side-work assurance is invalidated then the side work ends
            invalidated = workflow.invalidate_assurance(
                expected_revision=ExpectedRevision(workflow.status().revision)
            )
            state = CoreService.open(root).recover()
            restored = workflow.reject()

            # Then: invalidation resets only the side assurance and primary restoration remains exact
            self.assertEqual(invalidated.code, "ASSURANCE_INVALIDATED")
            self.assertEqual(state.preserved_primary_context, primary)
            if state.active_context is None:
                self.fail("expected active side work")
            self.assertEqual((state.active_context.status, state.active_context.phase), ("IN_PROGRESS", "IMPLEMENT"))
            self.assertIsNone(state.active_context.review)
            self.assertFalse(state.active_context.verification.sufficient)
            self.assertEqual(restored.code, "PRIMARY_RESTORED")
            self.assertEqual(CoreService.open(root).recover().active_context, primary)

    def test_project_established_methodology_converges_only_after_normal_gates(self) -> None:
        # Given: demonstrated EvoSpec behavior supplied as structured project evidence
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            supplied = IntentAssessment(
                AssessmentOperation.CHANGE,
                Classification.PATCH,
                AssessmentConfidence.HIGH,
                CapabilityState.PROJECT_ESTABLISHED,
                "EvoSpec methodology",
                ("EvoSpec methodology",),
                None,
                (AssessmentEvidence("caller", RepositoryPath("tests/test_workflow_dogfood.py")),),
                (),
                AssessmentDepth(
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                ),
                "demonstrated workflow behavior",
            )

            # When: CHANGE adopts it and passes the normal lifecycle safeguards
            intake = workflow.change("Repair methodology assessment", assessment=supplied)
            before_convergence = CoreService.open(root).recover()
            workflow.record_progress("implement", "methodology", (), "work", (), "review")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            done = workflow.converge(convergence_assessment(workflow.status().change_id, True))

            # Then: intake does not publish truth, while guarded convergence does
            self.assertEqual(intake.code, "PROPOSED")
            self.assertEqual(before_convergence.publications, ())
            self.assertEqual(done.code, "DONE")
            self.assertEqual(len(CoreService.open(root).recover().publications), 1)

    def test_decision_text_remains_research_evidence_not_capability_authority(self) -> None:
        # Given: a resolved decision attached to an unrelated terminal capability
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            intake = workflow.new(
                "Add billing retry delay",
                assessment=material_new_assessment(
                    "Add billing retry delay", "billing retry delivery"
                ),
            )
            workflow.clarify((ClarificationAnswer(intake.questions[0].id, "Add export retry delay"),))
            workflow.reject()
            before = workflow.status().revision

            # When: CHANGE repeats the decision text as a different capability
            result = WorkflowService.open(root).change("Add export retry delay")

            # Then: decision wording cannot independently authorize a Core change
            self.assertEqual(result.code, "NO_ESTABLISHED_CAPABILITY")
            self.assertEqual(WorkflowService.open(root).status().revision, before)

    def test_linked_native_target_is_rejected_before_shared_state_in_either_order(self) -> None:
        # Given: native export evidence and a distinct unestablished billing route
        for intent in (
            "Add billing retries | Add export delivery",
            "Add export delivery | Add billing retries",
        ):
            with self.subTest(intent=intent), TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "src").mkdir()
                (root / "docs").mkdir()
                (root / "src" / "export.py").write_text("export delivery", encoding="utf-8")
                (root / "docs" / "export.md").write_text("export delivery", encoding="utf-8")
                workflow = WorkflowService.open(root)
                workflow.init()
                before = workflow.status().revision

                # When: linked intake includes the native capability
                result = workflow.new(intent)

                # Then: no shared or deferred route persists before rejection
                state = CoreService.open(root).recover()
                self.assertEqual(result.code, "LINKED_TARGET_UNSAFE")
                self.assertEqual(state.revision, before)
                self.assertEqual(state.shared_intents, ())
                self.assertEqual(state.deferred_linked_intents, ())

    def test_deferred_linked_release_rejects_an_operation_invalid_route_without_mutation(self) -> None:
        # Given: a terminal predecessor has released an unestablished linked target
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add billing retries | Add export delivery")
            workflow.reject()
            before = workflow.status().revision

            # When: CHANGE attempts to materialize the unestablished deferred target
            rejected = WorkflowService.open(root).change("Add export delivery")

            # Then: the deferred route remains available for its valid NEW release
            state = CoreService.open(root).recover()
            self.assertEqual(rejected.code, "NO_ESTABLISHED_CAPABILITY")
            self.assertEqual(state.revision, before)
            self.assertEqual(len(state.deferred_linked_intents), 1)
            self.assertEqual(WorkflowService.open(root).new("Add export delivery").code, "PROPOSED")

    def test_linked_existing_units_do_not_cross_bind_features(self) -> None:
        # Given: two terminal Core Features with distinct multi-term capabilities
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add export encryption")
            workflow.reject()
            workflow.new("Add billing retries")
            workflow.reject()
            before = workflow.status().revision

            # When: a linked request names both existing units in reverse order
            result = WorkflowService.open(root).new("Add billing retries | Add export encryption")

            # Then: Core cannot bind either linked unit to the other Feature
            self.assertEqual(result.code, "LINKED_TARGET_UNSAFE")
            self.assertEqual(WorkflowService.open(root).status().revision, before)

    def test_discovery_limit_recovers_only_with_two_attributable_native_references(self) -> None:
        # Given: enough eligible project files to exceed the global discovery budget
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(129):
                (root / f"evidence-{index:03}.py").write_text(
                    "export delivery" if index < 2 else "unrelated", encoding="utf-8"
                )
            workflow = WorkflowService.open(root)
            workflow.init()
            initialization = CoreService.open(root).recover().initialization

            # When: CHANGE receives exactly one complete recovery candidate
            result = workflow.change("Change export delivery")
            state = CoreService.open(root).recover()

            # Then: the existing typed assessment path records the attributed project capability
            self.assertEqual(result.code, "PROPOSED")
            self.assertEqual(state.initialization, initialization)
            self.assertEqual(state.features[-1].title, "export delivery")
            assessment = state.changes[-1].assessment
            if assessment is None:
                self.fail("expected bounded recovery assessment")
            self.assertEqual(tuple(str(item.reference) for item in assessment.evidence), ("evidence-000.py", "evidence-001.py"))

    def test_discovery_limit_with_insufficient_recovery_evidence_is_non_mutating(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(129):
                (root / f"evidence-{index:03}.py").write_text(
                    "export delivery" if index == 0 else "unrelated", encoding="utf-8"
                )
            workflow = WorkflowService.open(root)
            workflow.init()
            before = CoreService.open(root).recover()

            result = workflow.change("Change export delivery")

            self.assertEqual(result.code, "DISCOVERY_LIMIT")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_discovery_limit_can_continue_through_honest_typed_repository_assessment(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(129):
                (root / f"evidence-{index:03}.py").write_text(
                    "export delivery" if index == 0 else "unrelated", encoding="utf-8"
                )
            workflow = WorkflowService.open(root)
            workflow.init()
            before = CoreService.open(root).recover()

            limited = workflow.change("Change export delivery")
            unchanged = CoreService.open(root).recover()
            assessed = IntentAssessment(
                AssessmentOperation.CHANGE,
                Classification.PATCH,
                AssessmentConfidence.HIGH,
                CapabilityState.PROJECT_ESTABLISHED,
                "export delivery",
                ("export delivery",),
                None,
                (AssessmentEvidence("repository", RepositoryPath("evidence-000.py")),),
                (),
                AssessmentDepth(
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                ),
                "the cited repository file demonstrates the project capability; no Core feature identity is claimed",
            )
            recovered = workflow.change("Change export delivery", assessment=assessed)

            self.assertEqual(limited.code, "DISCOVERY_LIMIT")
            self.assertEqual(unchanged, before)
            self.assertEqual(recovered.code, "PROPOSED")
            state = CoreService.open(root).recover()
            self.assertEqual(state.features[-1].title, "export delivery")
            self.assertEqual(state.changes[-1].assessment, assessed)

    def test_core_capability_precedes_native_discovery_limit(self) -> None:
        # Given: an established Core capability and an over-limit native repository
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            established = workflow.new("Add export delivery")
            workflow.reject()
            for index in range(129):
                (root / f"evidence-{index:03}.py").write_text("unrelated native evidence", encoding="utf-8")

            # When: CHANGE names the established Core capability
            if established.feature_id is None:
                self.fail("expected established Core Feature identity")
            result = WorkflowService.open(root).change(
                "Change export delivery behavior",
                assessment=established_change_assessment(
                    established.feature_id, "Add export delivery"
                ),
            )

            # Then: Core authority is selected without scanning native files
            self.assertEqual(result.code, "PROPOSED")
            self.assertEqual(result.feature_id, established.feature_id)

    def test_project_native_candidate_adoption_retains_provenance_without_truth(self) -> None:
        # Given: repository-native export evidence and no Core Feature history
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "export.py").write_text("export delivery", encoding="utf-8")
            (root / "docs" / "export.md").write_text("export delivery", encoding="utf-8")
            (root / "tests" / "test_export.py").write_text("export delivery", encoding="utf-8")
            workflow = WorkflowService.open(root)
            workflow.init()

            # When: CHANGE adopts exactly one strong project-native capability
            intake = workflow.change("Change export delivery behavior")
            state = CoreService.open(root).recover()
            context = state.active_context
            if context is None:
                self.fail("expected active project-native change")
            workflow.record_progress("implement", "export", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            reviewed = CoreService.open(root).recover().active_context
            if reviewed is None:
                self.fail("expected reviewed project-native change")

            # Then: provenance is durable research evidence, not premature Product Truth
            self.assertEqual(intake.code, "PROPOSED")
            self.assertEqual(len(state.features), 1)
            self.assertEqual(len(state.changes), 1)
            self.assertEqual(state.publications, ())
            self.assertTrue(any(item.startswith("project-native:") for item in context.safety_facts.scope))
            self.assertTrue(any(item.source == "project-native" for item in reviewed.review_evidence))

    def test_multiple_project_native_candidates_are_ambiguous_without_mutation(self) -> None:
        # Given: two equally strong native capability candidates
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src" / "export.py").write_text("export delivery", encoding="utf-8")
            (root / "docs" / "export.md").write_text("export delivery", encoding="utf-8")
            (root / "src" / "retry.py").write_text("billing retry", encoding="utf-8")
            (root / "docs" / "retry.md").write_text("billing retry", encoding="utf-8")
            workflow = WorkflowService.open(root)
            workflow.init()
            before = workflow.status().revision

            # When: both strong candidates match CHANGE
            result = workflow.change("Change export delivery billing retry behavior")

            # Then: no candidate is adopted by accident
            self.assertEqual(result.code, "AMBIGUOUS_CAPABILITY")
            self.assertEqual(WorkflowService.open(root).status().revision, before)

    def test_ambiguous_core_intake_persists_selection_and_materializes_only_offered_standalone_choice(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            core = CoreService.open(root)
            state = core.record_feature(core.recover(), Feature(FeatureId("feature-export-a"), "Export delivery", LivingSpecId("spec-export-a")))
            core.record_feature(state, Feature(FeatureId("feature-export-b"), "Export delivery", LivingSpecId("spec-export-b")))

            pending = workflow.change("Change export delivery behavior")
            state = CoreService.open(root).recover()
            selection = state.pending_capability_selection

            self.assertEqual(pending.code, "AMBIGUOUS_CAPABILITY")
            self.assertIsNotNone(selection)
            if selection is None:
                self.fail("expected Core-owned selection")
            self.assertEqual(selection.provenance, ("feature:feature-export-a", "feature:feature-export-b"))
            rejected = workflow.select_capability(str(selection.id), "feature-forged", expected_revision=ExpectedRevision(pending.revision))
            self.assertEqual(rejected.code, "INVALID_CAPABILITY_SELECTION")
            self.assertEqual(CoreService.open(root).recover().revision, pending.revision)

            proposed = workflow.select_capability(
                str(selection.id), "CREATE_STANDALONE_CAPABILITY", expected_revision=ExpectedRevision(pending.revision)
            )
            final = CoreService.open(root).recover()

            self.assertEqual(proposed.code, "PROPOSED")
            self.assertIsNone(final.pending_capability_selection)
            self.assertEqual(len(final.capability_selection_history), 1)
            self.assertIsNotNone(proposed.change_id)
            self.assertEqual(final.changes[-1].id, proposed.change_id)

    def test_ambiguous_core_selection_rejects_replay_and_allows_only_offered_feature(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            core = CoreService.open(root)
            state = core.record_feature(core.recover(), Feature(FeatureId("feature-export-a"), "Export delivery", LivingSpecId("spec-export-a")))
            core.record_feature(state, Feature(FeatureId("feature-export-b"), "Export delivery", LivingSpecId("spec-export-b")))
            pending = workflow.change("Change export delivery behavior")
            selection = CoreService.open(root).recover().pending_capability_selection
            if selection is None:
                self.fail("expected Core-owned selection")

            selected = workflow.select_capability(str(selection.id), "feature-export-a", expected_revision=ExpectedRevision(pending.revision))
            replay = workflow.select_capability(str(selection.id), "feature-export-a", expected_revision=ExpectedRevision(selected.revision))

            self.assertEqual(selected.code, "ESTABLISHED_CAPABILITY")
            self.assertEqual(replay.code, "NO_PENDING_CAPABILITY_SELECTION")

    def test_native_candidate_does_not_bind_an_incidental_subphrase(self) -> None:
        # Given: repeated native evidence for a different multi-term capability
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src" / "billing.py").write_text("billing retry delay", encoding="utf-8")
            (root / "docs" / "billing.md").write_text("billing retry delay", encoding="utf-8")
            workflow = WorkflowService.open(root)
            workflow.init()
            before = workflow.status().revision

            # When: CHANGE shares only the retry-delay subphrase
            result = workflow.change("Add export retry delay")

            # Then: native evidence cannot authorize the unrelated capability
            self.assertEqual(result.code, "NO_ESTABLISHED_CAPABILITY")
            self.assertEqual(WorkflowService.open(root).status().revision, before)

    def test_persisted_new_then_change_uses_fresh_disk_reloads(self) -> None:
        # Given: an initialized repository with no conversational memory
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            new_workflow = WorkflowService.open(root)
            new_workflow.init()
            new = new_workflow.new("Initial export delivery")
            new_workflow.record_progress("implement", "export", (), "work", (), "resume")
            new_workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            new_workflow.record_verification(VerificationEvidence(("initial tests",), True))
            new_workflow.converge(convergence_assessment(new_workflow.status().change_id, True))

            # When: a fresh facade reloads disk and opens a real later change
            if new.feature_id is None:
                self.fail("expected established Core Feature identity")
            changed = WorkflowService.open(root).change(
                "Change export delivery behavior",
                assessment=established_change_assessment(
                    new.feature_id, "Initial export delivery"
                ),
            )
            reloaded = WorkflowService.open(root)

            # Then: CHANGE has distinct persisted authority and starts its own research
            self.assertNotEqual(changed.change_id, new.change_id)
            self.assertEqual(reloaded.status().change_id, changed.change_id)
            self.assertEqual(reloaded.status().phase, "RESEARCH")

    def test_persisted_direct_facade_vertical_scenario(self) -> None:
        # Given: a fresh repository and no conversational state
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)

            # When: primary work is clarified, interrupted by side work, and reloaded
            initialized = workflow.init()
            self.assertIsNone(initialized.feature_id)
            intake = workflow.new(
                "Change established export delivery",
                assessment=material_new_assessment(
                    "Change established export delivery", "export delivery behavior"
                ),
            )
            self.assertEqual(workflow.clarify().code, "NEEDS_CLARIFICATION")
            ready = workflow.clarify((ClarificationAnswer(intake.questions[0].id, "email"),))
            workflow.record_progress("implement", "export", ("research",), "write code", ("review",), "resume export")
            primary = WorkflowService.open(root).status()
            side = workflow.change("unrelated: documentation")
            deferred = workflow.change("unrelated: later request")
            reloaded = WorkflowService.open(root)
            side_revision = reloaded.status().revision
            self.assertEqual(reloaded.continue_work().revision, side_revision)
            self.assertEqual(reloaded.status().revision, side_revision)
            restored = reloaded.reject()
            pending = WorkflowService.open(root)

            # Then: terminal side completion restores primary and only reports pending intake
            self.assertEqual(ready.code, "READY")
            self.assertEqual(side.code, "SIDE_ACTIVE")
            self.assertEqual(deferred.code, "DEFERRED")
            self.assertEqual(restored.code, "PRIMARY_RESTORED")
            self.assertEqual(pending.status().feature_id, primary.feature_id)
            self.assertEqual(pending.status().focus, primary.focus)
            self.assertEqual(pending.status().pending_intake_id, deferred.pending_intake_id)
            pending_revision = pending.status().revision
            self.assertEqual(pending.continue_work().revision, pending_revision)

            # When: an explicit call materializes pending work, then primary completes
            materialized = pending.change("explicit materialization")
            self.assertEqual(materialized.code, "SIDE_ACTIVE")
            self.assertIsNone(materialized.pending_intake_id)
            self.assertEqual(WorkflowService.open(root).reject().code, "PRIMARY_RESTORED")
            reloaded = WorkflowService.open(root)
            reloaded.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            unavailable = reloaded.record_verification(VerificationEvidence(("tool unavailable",), False))
            paused = reloaded.pause()
            resumed = WorkflowService.open(root).continue_work()
            resumed_workflow = WorkflowService.open(root)
            resumed_workflow.record_verification(VerificationEvidence(("unit tests",), True))
            done = resumed_workflow.converge(convergence_assessment(resumed_workflow.status().change_id, True))

            # Then: the verification gate and terminal publication survive reload
            self.assertEqual(unavailable.code, "INSUFFICIENT")
            self.assertEqual(paused.code, "PAUSED")
            self.assertEqual(resumed.phase, "VERIFY")
            self.assertEqual(done.code, "DONE")
            self.assertIsNone(WorkflowService.open(root).status().change_id)

            # When: an accepted experiment is explicitly recorded and converged
            accepted = WorkflowService.open(root)
            accepted.new("Experiment delivery behavior")
            accepted.record_progress("experiment", "delivery", (), "trial", (), "resume trial")
            recorded = accepted.record_experiment("delivery trial passed", ExperimentOutcome.ACCEPTED)
            accepted_intake = accepted.status()
            accepted_change_id = accepted_intake.change_id
            accepted_feature_id = accepted_intake.feature_id
            if accepted_change_id is None or accepted_feature_id is None:
                self.fail("expected accepted experiment identifiers")
            prior_truth = CoreService.open(root).recover().publications[-1]
            accepted_reconciliation = accepted.reconcile_drift(
                Drift(
                    ObservationId("drift-accepted-delivery"),
                    prior_truth.living_spec_id,
                    "accepted delivery behavior",
                    ChangeId(accepted_change_id),
                ),
                Decision(
                    DecisionId("decision-accepted-delivery"),
                    FeatureId(accepted_feature_id),
                    "constraint",
                    "adopt accepted delivery behavior",
                    ChangeId(accepted_change_id),
                ),
            )
            accepted.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            accepted.record_verification(VerificationEvidence(("experiment tests",), True))
            accepted_done = accepted.converge(convergence_assessment(accepted.status().change_id, True))
            accepted_truth = CoreService.open(root).recover().publications

            # Then: acceptance remains evidence until Core publishes immutable truth
            self.assertEqual(recorded.code, "EXPERIMENT_RECORDED")
            self.assertEqual(accepted_reconciliation.code, "RECONCILED")
            self.assertEqual(accepted_done.code, "DONE")
            self.assertEqual(len(accepted_truth), 2)
            self.assertEqual(accepted_truth[-1].revision, str(accepted_done.revision))

            # When: a rejected experiment records explicit drift reconciliation
            rejected = WorkflowService.open(root)
            rejected_intake = rejected.new("Experiment rejected notification behavior")
            rejected_change_id = rejected_intake.change_id
            rejected_feature_id = rejected_intake.feature_id
            if rejected_change_id is None or rejected_feature_id is None:
                self.fail("expected rejected experiment identifiers")
            rejected.record_progress("experiment", "delivery", (), "trial", (), "reject trial")
            rejected.record_experiment("delivery trial regressed", ExperimentOutcome.REJECTED)
            reconciliation = rejected.reconcile_drift(
                Drift(
                    ObservationId("drift-rejected-delivery"),
                    accepted_truth[-1].living_spec_id,
                    "rejected delivery behavior",
                    ChangeId(rejected_change_id),
                ),
                Decision(
                    DecisionId("decision-rejected-delivery"),
                    FeatureId(rejected_feature_id),
                    "constraint",
                    "keep accepted delivery truth",
                    ChangeId(rejected_change_id),
                ),
            )
            rejected.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            rejected.record_verification(VerificationEvidence(("rejected experiment tests",), True))
            before_rejected_convergence = CoreService.open(root).recover()
            with self.assertRaises(WorkflowError):
                rejected.converge(convergence_assessment(rejected.status().change_id, True))
            rejected_done = rejected.reject()
            rejected_state = CoreService.open(root).recover()

            # Then: reconciliation and rejection persist without publishing rejected behavior
            self.assertEqual(reconciliation.code, "RECONCILED")
            self.assertEqual(rejected_done.code, "REJECTED")
            self.assertEqual(rejected_state.publications, accepted_truth)
            self.assertEqual(rejected_state.revision, before_rejected_convergence.revision + 1)
            self.assertEqual(rejected_state.drifts[-1].id, ObservationId("drift-rejected-delivery"))
            self.assertEqual(rejected_state.decisions[-1].id, DecisionId("decision-rejected-delivery"))
            self.assertEqual(rejected_state.observations[-1].outcome, ExperimentOutcome.REJECTED)

            # When: a linked request reaches a terminal predecessor
            linked = WorkflowService.open(root)
            linked.new("First capability | Second capability")
            blocked = linked.new("Second capability")
            linked.reject()
            second = WorkflowService.open(root).new("Second capability")

            # Then: canonical guidance blocks the duplicate active intake until terminal release
            self.assertEqual(blocked.code, "ACTIVE_WORK")
            self.assertEqual(second.code, "PROPOSED")
            self.assertEqual(WorkflowService.open(root).status().change_id, second.change_id)
