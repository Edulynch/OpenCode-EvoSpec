from __future__ import annotations

import json
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from evospec.domain import (
    AssessmentConfidence,
    AssessmentDepth,
    AssessmentDepthValue,
    AssessmentEvidence,
    AssessmentOperation,
    CapabilityState,
    FeatureId,
    ExpectedRevision,
    IntentAssessment,
    MaterialClarification,
    MaterialDecision,
    RepositoryPath,
    ReviewResult,
    VerificationEvidence,
)
from evospec.lifecycle import Classification, ReviewVerdict
from evospec.services import CoreService
from evospec.workflow import ClarificationAnswer, WorkflowError, WorkflowService
from evospec.agent import project_context
from evospec.services import StaleRevisionError
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import convergence_assessment


def assessment(
    operation: AssessmentOperation,
    classification: Classification,
    state: CapabilityState,
    confidence: AssessmentConfidence = AssessmentConfidence.HIGH,
    feature_id: str | None = None,
    depth: AssessmentDepth = AssessmentDepth(
        AssessmentDepthValue.NORMAL,
        AssessmentDepthValue.NORMAL,
        AssessmentDepthValue.NORMAL,
    ),
    ambiguities: tuple[MaterialDecision, ...] | tuple[str, ...] = (),
    capability: str = "Semantic Locks",
) -> IntentAssessment:
    return IntentAssessment(
        operation,
        classification,
        confidence,
        state,
        capability,
        () if feature_id is None else (capability,),
        None if feature_id is None else FeatureId(feature_id),
        tuple(AssessmentEvidence("caller", RepositoryPath("assessment.md")) for _ in (0,)),
        ambiguities,
        depth,
        "caller supplied assessment",
    )


class WorkflowIntakeTests(unittest.TestCase):
    def test_invalid_new_material_decisions_never_mutate_core(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            before = CoreService.open(root).recover()
            for ambiguities in (("plain prose",), (MaterialDecision("", ("A", "B")),),
                                (MaterialDecision("choose", ("A",)),),
                                (MaterialDecision("choose", ("A", "A")),),
                                (MaterialDecision("choose", ("A", "")),)):
                with self.subTest(ambiguities=ambiguities):
                    supplied = assessment(AssessmentOperation.NEW, Classification.FEATURE,
                                          CapabilityState.PLANNED, ambiguities=ambiguities)
                    if isinstance(ambiguities[0], str):
                        with self.assertRaises(WorkflowError):
                            workflow.new("Add export", assessment=supplied)
                    else:
                        self.assertEqual(workflow.new("Add export", assessment=supplied).code, "CORE_CONFLICT")
                    self.assertEqual(CoreService.open(root).recover(), before)

    def test_typed_material_decisions_are_sequential_exact_and_counted_once(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            intake = workflow.new("Add export", assessment=assessment(
                AssessmentOperation.NEW, Classification.FEATURE, CapabilityState.PLANNED,
                ambiguities=(MaterialDecision("channel", ("email", "download", "portal")),
                             MaterialDecision("retention", ("year", "month"))),
            ))
            self.assertEqual(len(intake.questions), 1)
            self.assertEqual(intake.questions[0].options, ("email", "download", "portal"))
            context = project_context(workflow)
            self.assertEqual(context.outcome.kind.value if context.outcome else None, "HUMAN_MATERIAL_DECISION")
            self.assertEqual(context.outcome.material_decision.options if context.outcome and context.outcome.material_decision else (), ("email", "download", "portal"))
            before = CoreService.open(root).recover()
            for bad in ("arbitrary", "year", "Email"):
                with self.assertRaises(WorkflowError):
                    workflow.clarify((ClarificationAnswer(intake.questions[0].id, bad),))
            with self.assertRaises(WorkflowError):
                workflow.clarify((ClarificationAnswer("wrong", "email"),))
            self.assertEqual(CoreService.open(root).recover(), before)
            partial = workflow.clarify((ClarificationAnswer(intake.questions[0].id, "email"),))
            self.assertEqual(partial.questions[0].prompt, "retention")
            active_after_first = CoreService.open(root).recover().active_context
            assert active_after_first is not None
            self.assertEqual(active_after_first.autonomy_metrics.human_material_decisions, 1)
            with self.assertRaises(WorkflowError):
                workflow.clarify((ClarificationAnswer(intake.questions[0].id, "email"),))
            completed = workflow.clarify((ClarificationAnswer(partial.questions[0].id, "year"),))
            self.assertEqual(completed.code, "READY")
            active_after_second = CoreService.open(root).recover().active_context
            assert active_after_second is not None
            self.assertEqual(active_after_second.autonomy_metrics.human_material_decisions, 2)

    def test_legacy_repair_is_exact_revision_bound_and_never_answers(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add export")
            core = CoreService.open(root)
            state = core.recover()
            active = state.active_context
            assert active is not None and active.change_id is not None and state.initialization is not None
            initialization = state.initialization
            from dataclasses import replace as update
            state = core.record_safety_facts(state, True, update(active.safety_facts, unresolved_decision_details=("Which target?",)))
            before = core.recover()
            outcome_before = project_context(workflow).outcome
            assert outcome_before is not None
            self.assertEqual(outcome_before.kind.value, "STRUCTURAL_DEFECT")
            args = (initialization.repository_id, str(active.feature_id), str(active.change_id), "Which target?", "Select target", ("A", "B"))
            for wrong in (('bad',) + args[1:], (args[0], 'bad') + args[2:], args[:2] + ('bad',) + args[3:], args[:3] + ('bad',) + args[4:]):
                with self.assertRaises(WorkflowError):
                    workflow.structure_material_clarification(*wrong, expected_revision=ExpectedRevision(state.revision))
            self.assertEqual(core.recover(), before)
            with self.assertRaises(StaleRevisionError):
                workflow.structure_material_clarification(*args, expected_revision=ExpectedRevision(state.revision - 1))
            repaired = workflow.structure_material_clarification(*args, expected_revision=ExpectedRevision(state.revision))
            self.assertEqual(repaired.questions[0].options, ("A", "B"))
            active_repaired = core.recover().active_context
            assert active_repaired is not None
            self.assertEqual(active_repaired.autonomy_metrics.human_material_decisions, 0)
            outcome_repaired = project_context(workflow).outcome
            assert outcome_repaired is not None
            self.assertEqual(outcome_repaired.kind.value, "HUMAN_MATERIAL_DECISION")
            with self.assertRaises(StaleRevisionError):
                workflow.structure_material_clarification(*args, expected_revision=ExpectedRevision(state.revision))
            with self.assertRaises(WorkflowError):
                workflow.structure_material_clarification(*args, expected_revision=ExpectedRevision(repaired.revision))

    def test_legacy_repair_preserves_order_until_all_details_are_structured(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add export")
            core = CoreService.open(root)
            state = core.recover()
            active = state.active_context
            initialization = state.initialization
            assert active is not None and active.change_id is not None and initialization is not None
            state = core.record_safety_facts(state, True, replace(active.safety_facts,
                unresolved_decision_details=("first", "second")))
            identity = (initialization.repository_id, str(active.feature_id), str(active.change_id))
            with self.assertRaises(WorkflowError):
                workflow.structure_material_clarification(*identity, "second", "Second?", ("A", "B"), expected_revision=ExpectedRevision(state.revision))
            first = workflow.structure_material_clarification(*identity, "first", "First?", ("A", "B"), expected_revision=ExpectedRevision(state.revision))
            pending_outcome = project_context(workflow).outcome
            assert pending_outcome is not None
            self.assertEqual(pending_outcome.kind.value, "HUMAN_MATERIAL_DECISION")
            second = workflow.structure_material_clarification(*identity, "second", "Second?", ("C", "D"), expected_revision=ExpectedRevision(first.revision))
            self.assertEqual(second.questions[0].prompt, "First?")
            revealed = workflow.clarify((ClarificationAnswer(second.questions[0].id, "A"),))
            self.assertEqual(revealed.questions[0].prompt, "Second?")

    def test_legacy_non_material_recovery_is_exact_auditable_and_resumes_ready(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Recover an operational legacy note")
            core = CoreService.open(root)
            state = core.recover()
            active = state.active_context
            initialization = state.initialization
            assert active is not None and active.change_id is not None and initialization is not None
            detail = "  Operational discovery note — inspect the existing route first. "
            state = core.record_safety_facts(
                state,
                True,
                replace(active.safety_facts, unresolved_decision_details=(detail,)),
            )
            before = core.recover()
            before_context = before.active_context
            assert before_context is not None
            assert before_context.autonomy_envelope is not None
            before_projection = project_context(workflow)
            before_outcome = before_projection.outcome
            assert before_outcome is not None
            self.assertEqual(before_outcome.kind.value, "STRUCTURAL_DEFECT")
            self.assertIn("recover_legacy_non_material_detail", before_projection.legal_actions)

            result = workflow.recover_legacy_non_material_detail(
                initialization.repository_id,
                str(active.feature_id),
                str(active.change_id),
                detail,
                expected_revision=ExpectedRevision(state.revision),
            )

            after = CoreService.open(root).recover()
            after_context = after.active_context
            assert after_context is not None
            self.assertEqual(result.code, "READY")
            self.assertEqual(after.revision, before.revision + 1)
            self.assertEqual(after_context.safety_facts.unresolved_decision_details, ())
            self.assertFalse(after_context.unresolved_material_decision)
            self.assertEqual((str(after_context.status), str(after_context.phase)), ("READY", "READY"))
            self.assertEqual(len(after.observations), len(before.observations) + 1)
            observation = after.observations[-1]
            self.assertEqual(observation.kind, "legacy_non_material_recovery")
            self.assertEqual(observation.statement, detail)
            self.assertFalse(observation.accepted_into_truth)
            self.assertIsNone(observation.outcome)
            self.assertIn(observation.id, after_context.observation_ids)
            self.assertEqual(after.decisions, before.decisions)
            self.assertEqual(after_context.autonomy_metrics, before_context.autonomy_metrics)
            self.assertEqual(after_context.autonomy_envelope, before_context.autonomy_envelope)
            self.assertEqual(after.publications, before.publications)
            after_projection = project_context(workflow)
            after_outcome = after_projection.outcome
            assert after_outcome is not None
            self.assertEqual(after_outcome.kind.value, "SAFE_AUTOMATIC_OPERATION")
            self.assertNotIn("recover_legacy_non_material_detail", after_projection.legal_actions)
            identity = json.dumps(
                [
                    initialization.repository_id,
                    str(active.feature_id),
                    str(active.change_id),
                    detail,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.assertEqual(
                observation.id,
                f"legacy-non-material-{sha256(identity.encode('utf-8')).hexdigest()}",
            )

    def test_legacy_non_material_recovery_checks_identity_revision_order_and_replay(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Recover ordered legacy notes")
            core = CoreService.open(root)
            state = core.recover()
            active = state.active_context
            initialization = state.initialization
            assert active is not None and active.change_id is not None and initialization is not None
            state = core.record_safety_facts(
                state,
                True,
                replace(active.safety_facts, unresolved_decision_details=("duplicate", "duplicate")),
            )
            identity = (initialization.repository_id, str(active.feature_id), str(active.change_id))
            with self.assertRaises(WorkflowError):
                workflow.recover_legacy_non_material_detail(
                    *identity, "duplicate", expected_revision=ExpectedRevision(state.revision)
                )
            state = core.recover()
            active = state.active_context
            assert active is not None
            state = core.record_safety_facts(
                state,
                True,
                replace(active.safety_facts, unresolved_decision_details=("first", "second")),
            )
            before = core.recover()
            expected = ExpectedRevision(before.revision)
            first_identity = (initialization.repository_id, str(active.feature_id), str(active.change_id))
            wrong_requests = (
                ("wrong-repository", first_identity[1], first_identity[2], "first"),
                (first_identity[0], "wrong-feature", first_identity[2], "first"),
                (first_identity[0], first_identity[1], "wrong-change", "first"),
                (*first_identity, "not-persisted"),
                (*first_identity, "second"),
                (*first_identity, ""),
            )
            for request in wrong_requests:
                with self.assertRaises(WorkflowError):
                    workflow.recover_legacy_non_material_detail(*request, expected_revision=expected)
            with self.assertRaises(StaleRevisionError):
                workflow.recover_legacy_non_material_detail(
                    *first_identity, "first", expected_revision=ExpectedRevision(before.revision - 1)
                )
            self.assertEqual(core.recover(), before)

            first = workflow.recover_legacy_non_material_detail(
                *first_identity, "first", expected_revision=expected
            )
            after_first = core.recover()
            after_first_context = after_first.active_context
            assert after_first_context is not None
            self.assertEqual(first.code, "NEEDS_CLARIFICATION")
            self.assertEqual(
                after_first_context.safety_facts.unresolved_decision_details,
                ("second",),
            )
            self.assertTrue(after_first_context.unresolved_material_decision)
            remaining_outcome = project_context(workflow).outcome
            assert remaining_outcome is not None
            self.assertEqual(remaining_outcome.kind.value, "STRUCTURAL_DEFECT")
            self.assertIn("structure_material_clarification", project_context(workflow).legal_actions)
            self.assertIn("recover_legacy_non_material_detail", project_context(workflow).legal_actions)
            with self.assertRaises(WorkflowError):
                workflow.recover_legacy_non_material_detail(
                    *first_identity, "first", expected_revision=ExpectedRevision(first.revision)
                )

            second = workflow.recover_legacy_non_material_detail(
                *first_identity, "second", expected_revision=ExpectedRevision(first.revision)
            )
            self.assertEqual(second.code, "READY")
            self.assertEqual(len(CoreService.open(root).recover().observations), 2)

    def test_legacy_recovery_preserves_remaining_typed_material_decision(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Keep typed clarification separate")
            core = CoreService.open(root)
            state = core.recover()
            active = state.active_context
            initialization = state.initialization
            assert active is not None and active.change_id is not None and initialization is not None
            details = ("confirmed non-material note",)
            typed = MaterialClarification("question-typed", "Choose a delivery mode", ("email", "download"))
            state = core.record_safety_facts(
                state,
                True,
                replace(
                    active.safety_facts,
                    unresolved_decision_details=details,
                    material_clarifications=(typed,),
                ),
            )
            identity = (initialization.repository_id, str(active.feature_id), str(active.change_id))
            result = workflow.recover_legacy_non_material_detail(
                *identity,
                details[0],
                expected_revision=ExpectedRevision(state.revision),
            )
            remaining = CoreService.open(root).recover().active_context
            assert remaining is not None
            self.assertEqual(result.code, "NEEDS_CLARIFICATION")
            self.assertTrue(remaining.unresolved_material_decision)
            self.assertEqual(remaining.safety_facts.unresolved_decision_details, ())
            self.assertEqual(remaining.safety_facts.material_clarifications, (typed,))
            typed_outcome = project_context(workflow).outcome
            assert typed_outcome is not None
            self.assertEqual(typed_outcome.kind.value, "HUMAN_MATERIAL_DECISION")
            self.assertEqual(remaining.autonomy_metrics.human_material_decisions, 0)
            self.assertEqual(remaining.autonomy_metrics.operational_handoffs, 0)

    def test_uninitialized_status_and_intake_require_initialization_without_discovery_or_mutation(self) -> None:
        # Given: a recovered Core state with no lifecycle initialization
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            before = CoreService.open(root).recover()

            # When: status and both intake routes are requested
            with patch("evospec.workflow.DiscoveryPolicy.capability_candidates") as discovery:
                status = workflow.status()
                new = workflow.new("Add export capability")
                change = workflow.change("Change export capability")

            # Then: authority is requested without discovery or durable change
            self.assertEqual((status.code, new.code, change.code), ("INIT_REQUIRED", "INIT_REQUIRED", "INIT_REQUIRED"))
            discovery.assert_not_called()
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_initialized_status_and_intake_remain_compatible(self) -> None:
        # Given: an initialized repository with no active lifecycle work
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()

            # When: status and a new intake are requested
            status = workflow.status()
            intake = workflow.new("Add export capability")

            # Then: initialized behavior remains the normal workflow path
            self.assertEqual(status.code, "STATUS")
            self.assertEqual(intake.code, "PROPOSED")

    def test_project_established_assessment_adopts_change_without_publishing_product_truth(self) -> None:
        # Given: caller-confirmed project behavior with attributable repository evidence but no Core identity
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            supplied = IntentAssessment(
                AssessmentOperation.CHANGE,
                Classification.PATCH,
                AssessmentConfidence.HIGH,
                CapabilityState.PROJECT_ESTABLISHED,
                "Export delivery",
                ("Export delivery",),
                None,
                (AssessmentEvidence("caller", RepositoryPath("src/export.py")),),
                (),
                AssessmentDepth(
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                ),
                "demonstrated project behavior",
            )

            # When: CHANGE adopts the caller-selected project behavior
            intake = workflow.change("Alter delivery scheduling", assessment=supplied)
            recovered = CoreService.open(root).recover()

            # Then: one Core Feature and Change exist, while Product Truth remains unpublished
            self.assertEqual(intake.code, "PROPOSED")
            self.assertEqual(len(recovered.features), 1)
            self.assertEqual(len(recovered.changes), 1)
            self.assertEqual(recovered.features[0].title, "Export delivery")
            self.assertEqual(recovered.changes[0].assessment, supplied)
            self.assertEqual(recovered.publications, ())
            self.assertEqual(recovered.publication_history, ())

    def test_project_established_new_routes_to_change_and_exact_core_requires_reassessment(self) -> None:
        # Given: project-established behavior without an existing Core Feature
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            supplied = IntentAssessment(
                AssessmentOperation.NEW,
                Classification.FEATURE,
                AssessmentConfidence.HIGH,
                CapabilityState.PROJECT_ESTABLISHED,
                "Export delivery",
                ("Export delivery",),
                None,
                (AssessmentEvidence("caller", RepositoryPath("src/export.py")),),
                (),
                AssessmentDepth(
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                ),
                "demonstrated project behavior",
            )

            # When: NEW receives the demonstrated capability
            reroute = workflow.new("Add delivery scheduling", assessment=supplied)
            before = CoreService.open(root).recover()
            changed = workflow.change(
                "Alter delivery scheduling",
                assessment=replace(supplied, operation=AssessmentOperation.CHANGE, classification=Classification.PATCH),
            )
            conflict = WorkflowService.open(root).change(
                "Alter delivery scheduling again",
                assessment=replace(supplied, operation=AssessmentOperation.CHANGE, classification=Classification.PATCH),
            )

            # Then: NEW never creates authority, and an exact Core title must be reassessed
            self.assertEqual(reroute.code, "ESTABLISHED_CAPABILITY")
            self.assertEqual(before.features, ())
            self.assertEqual(changed.code, "PROPOSED")
            self.assertEqual(conflict.code, "CORE_CONFLICT")

    def test_project_established_assessment_requires_identity_evidence_and_no_feature_id(self) -> None:
        # Given: malformed variants of caller-confirmed project behavior
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            base = IntentAssessment(
                AssessmentOperation.CHANGE,
                Classification.PATCH,
                AssessmentConfidence.HIGH,
                CapabilityState.PROJECT_ESTABLISHED,
                "Export delivery",
                ("Export delivery",),
                None,
                (AssessmentEvidence("caller", RepositoryPath("src/export.py")),),
                (),
                AssessmentDepth(
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                ),
                "demonstrated project behavior",
            )

            # When: required identity, safe attributable evidence, or Core identity is absent or contradictory
            missing_identity = workflow.change(
                "Alter delivery", assessment=replace(base, selected_capability=None)
            )
            missing_evidence = workflow.change(
                "Alter delivery", assessment=replace(base, evidence=())
            )
            core_identity = workflow.change(
                "Alter delivery", assessment=replace(base, selected_feature_id=FeatureId("feature-export"))
            )

            # Then: none can create a Core record
            self.assertEqual(
                (missing_identity.code, missing_evidence.code, core_identity.code),
                ("CORE_CONFLICT", "CORE_CONFLICT", "CORE_CONFLICT"),
            )
            self.assertEqual(CoreService.open(root).recover().features, ())

    def test_structured_semantic_locks_assessment_controls_new_intake_without_lexical_inference(self) -> None:
        # Given: a caller-supplied planned feature assessment whose prose contains changes
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            supplied = assessment(
                AssessmentOperation.NEW,
                Classification.FEATURE,
                CapabilityState.PLANNED,
            )

            # When: NEW accepts the assessment
            result = workflow.new("Semantic Locks changes capability behavior", assessment=supplied)
            context = WorkflowService.open(root).status()

            # Then: the caller classification and normal safety floor survive, not PATCH
            self.assertEqual(result.code, "PROPOSED")
            self.assertEqual(context.classification, "FEATURE")
            self.assertEqual(context.review_depth, "NORMAL")

    def test_absent_assessment_is_low_authority_fallback_with_operation_defaults_and_normal_floor(self) -> None:
        # Given: requests that only contain legacy leading markers or incidental change prose
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()

            # When: each request is accepted without a caller assessment
            fixed = workflow.new("Fix an export behavior")
            fixed_state = WorkflowService.open(root).status()
            workflow.reject()
            tuned = workflow.new("Tune cache delivery")
            tuned_state = WorkflowService.open(root).status()
            workflow.reject()
            experiment = workflow.new("Experiment a notification approach")
            experiment_state = WorkflowService.open(root).status()
            workflow.reject()
            ambiguous = workflow.new("Add behavior that allows customers to change preferences")

            # Then: compatibility markers remain explicit while incidental prose cannot force PATCH
            self.assertEqual(fixed.classification, "PATCH")
            self.assertEqual(tuned.classification, "TUNE")
            self.assertEqual(experiment.classification, "EXPERIMENT")
            self.assertEqual(ambiguous.classification, "FEATURE")
            self.assertEqual(
                (fixed_state.review_depth, tuned_state.review_depth, experiment_state.review_depth),
                ("NORMAL", "NORMAL", "NORMAL"),
            )

    def test_low_confidence_or_ambiguous_assessment_requires_clarification_without_mutation(self) -> None:
        # Given: an initialized repository and an assessment short of a safe commitment
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            before = workflow.status().revision
            low = assessment(
                AssessmentOperation.NEW,
                Classification.FEATURE,
                CapabilityState.UNKNOWN,
                AssessmentConfidence.LOW,
                ambiguities=(MaterialDecision("target capability", ("existing", "new")),),
            )

            # When: the low-confidence assessment is offered
            result = workflow.new("Add capability", assessment=low)
            high = assessment(
                AssessmentOperation.NEW,
                Classification.FEATURE,
                CapabilityState.PLANNED,
            )
            accepted = workflow.new("Add capability", assessment=high)

            # Then: the same intent only mutates after the caller supplies a safe assessment
            self.assertEqual(result.code, "ASSESSMENT_REQUIRED")
            self.assertEqual(accepted.code, "PROPOSED")
            self.assertEqual(accepted.revision, before + 3)

    def test_change_assessment_planned_state_does_not_fabricate_established_capability(self) -> None:
        # Given: an initialized repository with no matching Core feature
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            before = workflow.status().revision

            # When: CHANGE names only a planned capability
            result = workflow.change(
                "Change semantic locks",
                assessment=assessment(
                    AssessmentOperation.CHANGE,
                    Classification.PATCH,
                    CapabilityState.PLANNED,
                ),
            )

            # Then: it cannot create an established capability by assertion
            self.assertEqual(result.code, "NO_ESTABLISHED_CAPABILITY")
            self.assertEqual(WorkflowService.open(root).status().revision, before)

    def test_assessment_enforces_core_identity_for_duplicate_new_and_change_reuse(self) -> None:
        # Given: a rejected Core feature created from a planned assessment
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            created = workflow.new(
                "Add semantic locks",
                assessment=assessment(
                    AssessmentOperation.NEW,
                    Classification.FEATURE,
                    CapabilityState.PLANNED,
                ),
            )
            workflow.reject()
            if created.feature_id is None:
                self.fail("expected created Core feature")
            before = workflow.status().revision
            established_new = assessment(
                AssessmentOperation.NEW,
                Classification.FEATURE,
                CapabilityState.ESTABLISHED,
                feature_id=created.feature_id,
            )
            established_change = assessment(
                AssessmentOperation.CHANGE,
                Classification.PATCH,
                CapabilityState.ESTABLISHED,
                feature_id=created.feature_id,
            )

            # When: NEW and CHANGE name the same Core identity
            duplicate = WorkflowService.open(root).new("Another request", assessment=established_new)
            changed = WorkflowService.open(root).change("Alter semantic locks", assessment=established_change)

            # Then: NEW is non-mutating and CHANGE reuses the Core feature
            self.assertEqual(duplicate.code, "ESTABLISHED_CAPABILITY")
            self.assertEqual(duplicate.revision, before)
            self.assertEqual(changed.code, "PROPOSED")
            self.assertEqual(changed.feature_id, created.feature_id)

    def test_assessment_cannot_claim_planned_for_an_existing_core_feature(self) -> None:
        # Given: an existing Core feature
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            created = workflow.new("Add semantic locks")
            workflow.reject()
            if created.feature_id is None:
                self.fail("expected created Core feature")

            # When: a contradictory planned reference is supplied
            result = WorkflowService.open(root).new(
                "Add semantic locks again",
                assessment=assessment(
                    AssessmentOperation.NEW,
                    Classification.FEATURE,
                    CapabilityState.PLANNED,
                    feature_id=created.feature_id,
                ),
            )

            # Then: the Core conflict is surfaced without mutating authority
            self.assertEqual(result.code, "CORE_CONFLICT")

    def test_planned_assessment_reuses_exact_accepted_publication_without_mutation(self) -> None:
        # Given: accepted Core truth for Export and a caller-selected planned capability
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            created = workflow.new("Export")
            workflow.record_progress("implement", "export", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            before = CoreService.open(root).recover()
            planned = replace(
                assessment(AssessmentOperation.NEW, Classification.FEATURE, CapabilityState.PLANNED),
                selected_capability="Export",
            )

            # When: NEW receives the same planned capability again
            result = WorkflowService.open(root).new("unrelated wording", assessment=planned)

            # Then: accepted publication authority wins and state remains unchanged
            self.assertEqual(result.code, "ESTABLISHED_CAPABILITY")
            self.assertEqual(result.feature_id, created.feature_id)
            self.assertEqual(CoreService.open(root).recover(), before)

            # And: a distinct canonical capability remains eligible for NEW
            distinct = replace(planned, selected_capability="Export Audit")
            distinct_result = WorkflowService.open(root).new("unrelated wording", assessment=distinct)
            self.assertEqual(distinct_result.code, "PROPOSED")

    def test_planned_new_cannot_duplicate_rejected_project_established_feature(self) -> None:
        # Given: PROJECT_ESTABLISHED CHANGE adopted an unpublished Core Feature and was rejected
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            project = IntentAssessment(
                AssessmentOperation.CHANGE,
                Classification.PATCH,
                AssessmentConfidence.HIGH,
                CapabilityState.PROJECT_ESTABLISHED,
                "Export delivery",
                ("Export delivery",),
                None,
                (AssessmentEvidence("caller", RepositoryPath("src/export.py")),),
                (),
                AssessmentDepth(
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                ),
                "demonstrated project behavior",
            )
            workflow.change("Alter delivery scheduling", assessment=project)
            workflow.reject()
            before = CoreService.open(root).recover()
            planned = replace(
                assessment(AssessmentOperation.NEW, Classification.FEATURE, CapabilityState.PLANNED),
                selected_capability="Export delivery",
            )

            # When: PLANNED NEW selects that exact unpublished Core title with a distinct raw intent
            duplicate = WorkflowService.open(root).new("Create delivery dashboard", assessment=planned)
            after_duplicate = CoreService.open(root).recover()

            # Then: exact Core identity is protected without duplicate state
            self.assertEqual(duplicate.code, "CORE_CONFLICT")
            self.assertEqual(after_duplicate, before)

            # And: distinct planned work remains eligible
            distinct = WorkflowService.open(root).new(
                "Create export audit",
                assessment=replace(planned, selected_capability="Export Audit"),
            )

            # Then: only the distinct selected title creates a Feature
            self.assertEqual(distinct.code, "PROPOSED")
            self.assertEqual(CoreService.open(root).recover().features[-1].title, "Export Audit")

    def test_assessment_depth_is_provenance_but_cannot_weaken_core_floor(self) -> None:
        # Given: a foundational assessment that requests shallow work
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            supplied = assessment(
                AssessmentOperation.NEW,
                Classification.FOUNDATION,
                CapabilityState.PLANNED,
                depth=AssessmentDepth(
                    AssessmentDepthValue.TARGETED,
                    AssessmentDepthValue.TARGETED,
                    AssessmentDepthValue.TARGETED,
                ),
            )

            # When: Core accepts the assessment
            workflow.new("Build semantic locks", assessment=supplied)
            context = WorkflowService.open(root).status()

            # Then: Core's per-component foundation floor wins
            self.assertEqual(
                (context.review_depth, context.verification_depth, context.convergence_depth),
                ("DEEP", "FULL", "FULL"),
            )

    def test_malformed_assessment_rejects_before_primary_materialization(self) -> None:
        # Given: a malformed planned assessment
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            malformed = replace(
                assessment(AssessmentOperation.NEW, Classification.FEATURE, CapabilityState.PLANNED),
                evidence=(
                    AssessmentEvidence(
                        "caller", RepositoryPath("assessment.md"), FeatureId("missing-feature")
                    ),
                ),
            )
            before_primary = CoreService.open(root).recover()

            # When: primary materialization receives assessment authority Core cannot accept
            result = workflow.new("Add audit capability", assessment=malformed)

            # Then: rejection creates no Feature or authoritative revision
            self.assertEqual(result.code, "CORE_CONFLICT")
            self.assertEqual(CoreService.open(root).recover(), before_primary)

    def test_side_assessment_conflict_rejects_before_side_materialization(self) -> None:
        # Given: active primary work and an invalid established assessment for a side request
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Primary capability")
            before = CoreService.open(root).recover()

            # When: side work attempts to use an unresolved Core identity
            result = workflow.new(
                "unrelated: audit capability",
                assessment=replace(
                    assessment(
                        AssessmentOperation.NEW,
                        Classification.FEATURE,
                        CapabilityState.ESTABLISHED,
                        feature_id="missing-feature",
                    ),
                    selected_capability="Missing capability",
                ),
            )

            # Then: Core rejects before the side Feature or Change is recorded
            self.assertEqual(result.code, "CORE_CONFLICT")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_pending_assessment_preflights_before_materialization(self) -> None:
        # Given: deferred pending work below a restored primary
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            workflow.change("unrelated: pending capability")
            workflow.change("unrelated: later capability")
            workflow.reject()
            before = CoreService.open(root).recover()

            # When: CHANGE attempts to materialize planned pending work
            result = workflow.change(
                "materialize pending",
                assessment=assessment(
                    AssessmentOperation.CHANGE,
                    Classification.FOUNDATION,
                    CapabilityState.PLANNED,
                ),
            )

            # Then: it remains deferred without a revision or lifecycle mutation
            self.assertEqual(result.code, "NO_ESTABLISHED_CAPABILITY")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_pending_and_linked_materialization_persist_effective_facts(self) -> None:
        # Given: deferred pending and linked NEW work with foundational assessment requirements
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            workflow.change("unrelated: pending capability")
            workflow.change("unrelated: later capability")
            workflow.reject()

            # When: the pending and linked NEW branches materialize safe planned work
            workflow.new(
                "materialize pending",
                assessment=replace(
                    assessment(
                        AssessmentOperation.NEW,
                        Classification.FOUNDATION,
                        CapabilityState.PLANNED,
                    ),
                    selected_capability="later capability",
                ),
            )
            pending_context = CoreService.open(root).recover().active_context
            # Then: the pending branch retains every effective safety floor
            if pending_context is None:
                self.fail("expected pending context")
            self.assertEqual(
                (
                    pending_context.safety_facts.review_depth,
                    pending_context.safety_facts.verification_depth,
                    pending_context.safety_facts.convergence_depth,
                ),
                ("DEEP", "FULL", "FULL"),
            )

    def test_linked_materialization_persists_effective_facts(self) -> None:
        # Given: a released linked intent and a foundational planned assessment
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("First capability | Second capability")
            workflow.reject()
            before = CoreService.open(root).recover()

            # When: CHANGE cannot materialize planned linked work, then NEW safely releases it
            rejected = workflow.change(
                "Second capability",
                assessment=replace(
                    assessment(
                        AssessmentOperation.CHANGE,
                        Classification.FOUNDATION,
                        CapabilityState.PLANNED,
                    ),
                    selected_capability="Second capability",
                ),
            )
            after_rejected = CoreService.open(root).recover()
            workflow.new(
                "Second capability",
                assessment=replace(
                    assessment(
                        AssessmentOperation.NEW,
                        Classification.FOUNDATION,
                        CapabilityState.PLANNED,
                    ),
                    selected_capability="Second capability",
                ),
            )
            linked_context = CoreService.open(root).recover().active_context

            # Then: the linked branch retains every effective safety floor
            self.assertEqual(rejected.code, "NO_ESTABLISHED_CAPABILITY")
            self.assertEqual(after_rejected, before)
            if linked_context is None:
                self.fail("expected linked context")
            self.assertEqual(
                (
                    linked_context.safety_facts.review_depth,
                    linked_context.safety_facts.verification_depth,
                    linked_context.safety_facts.convergence_depth,
                ),
                ("DEEP", "FULL", "FULL"),
            )

    def test_selected_assessment_preserves_raw_intent_and_persists_ambiguity_for_clarification(self) -> None:
        # Given: a selected capability with materially ambiguous user intent
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            supplied = replace(
                assessment(AssessmentOperation.NEW, Classification.FEATURE, CapabilityState.PLANNED),
                selected_capability="Export Audit",
                material_ambiguities=(MaterialDecision("retention policy", ("retain reports for one year", "retain reports for two years")),),
                rationale="audit authority is planned work",
            )

            # When: Core records the selected identity and enters its existing clarification lifecycle
            intake = workflow.new("Create an annual compliance report", assessment=supplied)
            state = CoreService.open(root).recover()
            pending = workflow.clarify()
            ready = workflow.clarify(
                (ClarificationAnswer(intake.questions[0].id, "retain reports for one year"),)
            )

            # Then: identity only controls routing while raw intent and clarification facts survive
            self.assertEqual(intake.code, "NEEDS_CLARIFICATION")
            self.assertEqual(state.features[-1].title, "Export Audit")
            self.assertEqual(state.changes[-1].intent, "Create an annual compliance report")
            recovered_assessment = state.changes[-1].assessment
            if recovered_assessment is None:
                self.fail("expected recovered assessment")
            self.assertEqual(recovered_assessment.rationale, "audit authority is planned work")
            self.assertEqual(pending.questions, intake.questions)
            self.assertEqual(ready.code, "READY")
            context = CoreService.open(root).recover().active_context
            if context is None:
                self.fail("expected clarified context")
            self.assertEqual(
                context.safety_facts.unresolved_decision_details,
                (),
            )

    def test_planned_selected_capability_does_not_conflict_on_lexical_core_overlap(self) -> None:
        # Given: Core has Export while a high-confidence assessment selects distinct planned Export Audit work
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add export")
            workflow.reject()
            supplied = replace(
                assessment(AssessmentOperation.NEW, Classification.FEATURE, CapabilityState.PLANNED),
                selected_capability="Export Audit",
            )

            # When: the supplied planned identity overlaps Core only lexically
            result = workflow.new("Create export audit", assessment=supplied)
            state = CoreService.open(root).recover()

            # Then: only explicit Core identity can reject the planned capability
            self.assertEqual(result.code, "PROPOSED")
            self.assertEqual(state.features[-1].title, "Export Audit")
    def test_init_then_new_persists_one_canonical_change(self) -> None:
        # Given: a repository with no EvoSpec state
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)

            # When: the facade initializes then accepts intent
            initialized = workflow.init()
            intake = workflow.new("Add export capability")

            # Then: one Core-owned lifecycle-bearing change is recoverable
            self.assertEqual(initialized.code, "INITIALIZED")
            self.assertEqual(intake.code, "PROPOSED")
            recovered = WorkflowService.open(root).status()
            self.assertEqual(recovered.change_id, intake.change_id)
            self.assertEqual(recovered.phase, "RESEARCH")

    def test_status_is_read_only_and_continue_resumes_only_paused_work(self) -> None:
        # Given: initialized proposed work
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Add export capability")
            before = workflow.status().revision

            # When: status and ordinary continue are inspected
            status = workflow.status()
            continued = workflow.continue_work()

            # Then: neither inspection mutates authoritative revision
            self.assertEqual(status.revision, before)
            self.assertEqual(continued.revision, before)

    def test_new_established_capability_and_change_without_match_are_safe_routes(self) -> None:
        # Given: an established feature without active work
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            established = workflow.new("Add export delivery")
            workflow.reject()
            before = workflow.status().revision

            # When: NEW repeats it and CHANGE names an absent capability
            duplicate = WorkflowService.open(root).new("Add export delivery scheduling")
            no_match = WorkflowService.open(root).change("Patch billing retry delay")

            # Then: neither route manufactures authority
            self.assertEqual(duplicate.code, "ESTABLISHED_CAPABILITY")
            self.assertEqual(no_match.code, "NO_ESTABLISHED_CAPABILITY")
            self.assertEqual(WorkflowService.open(root).status().revision, before)
            self.assertIsNone(no_match.feature_id)

    def test_new_rejects_an_established_capability_without_mutation(self) -> None:
        # Given: a prior Core Feature has the same meaningful target
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add export delivery")
            workflow.reject()
            before = workflow.status().revision

            # When: NEW names the existing target again
            result = WorkflowService.open(root).new("Add export delivery scheduling")

            # Then: it does not create another capability record
            self.assertEqual(result.code, "ESTABLISHED_CAPABILITY")
            self.assertEqual(WorkflowService.open(root).status().revision, before)

    def test_one_term_capability_blocks_new_intake(self) -> None:
        # Given: a rejected one-term established Core capability
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            established = workflow.new("Add export capability")
            workflow.reject()
            before = workflow.status().revision

            # When: NEW extends the established capability name
            duplicate = WorkflowService.open(root).new("Add export delivery")

            # Then: NEW is non-mutating
            self.assertEqual(duplicate.code, "ESTABLISHED_CAPABILITY")
            self.assertEqual(duplicate.revision, before)

    def test_one_term_capability_routes_change_to_core(self) -> None:
        # Given: a rejected one-term established Core capability
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            established = workflow.new("Add export capability")
            workflow.reject()

            # When: CHANGE extends the established capability name
            if established.feature_id is None:
                self.fail("expected established Core Feature identity")
            changed = WorkflowService.open(root).change(
                "Change export delivery behavior",
                assessment=assessment(
                    AssessmentOperation.CHANGE,
                    Classification.PATCH,
                    CapabilityState.ESTABLISHED,
                    feature_id=established.feature_id,
                    capability="Add export capability",
                ),
            )

            # Then: CHANGE reuses the Core Feature
            self.assertEqual(changed.code, "PROPOSED")
            self.assertEqual(changed.feature_id, established.feature_id)

    def test_active_side_deferral_rejects_invalid_assessment_without_recording_intake(self) -> None:
        # Given: active side work with an invalid caller-selected Core identity
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            workflow.change("unrelated: active side capability")
            before = CoreService.open(root).recover()
            invalid = replace(
                assessment(
                    AssessmentOperation.CHANGE,
                    Classification.PATCH,
                    CapabilityState.ESTABLISHED,
                    feature_id="missing-feature",
                ),
                selected_capability="Missing capability",
            )

            # When: another unrelated request is deferred while the side is active
            result = workflow.change("unrelated: deferred capability", assessment=invalid)

            # Then: invalid evidence cannot create a deferred intake
            self.assertEqual(result.code, "CORE_CONFLICT")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_active_side_deferral_applies_accepted_core_precedence_before_recording_intake(self) -> None:
        # Given: accepted Core truth for Export and active side work
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Export")
            workflow.record_progress("implement", "export", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            workflow.change("unrelated: active side capability")
            before = CoreService.open(root).recover()
            planned = replace(
                assessment(AssessmentOperation.NEW, Classification.FEATURE, CapabilityState.PLANNED),
                selected_capability="Export",
            )

            # When: another unrelated request selects the accepted Core capability while the side is active
            result = workflow.new("unrelated: export request", assessment=planned)

            # Then: accepted Core precedence wins without recording a deferred intake
            self.assertEqual(result.code, "ESTABLISHED_CAPABILITY")
            self.assertEqual(CoreService.open(root).recover(), before)

            # And: a distinct planned capability still defers normally
            distinct = replace(planned, selected_capability="Export Audit")
            deferred = WorkflowService.open(root).new("unrelated: export audit request", assessment=distinct)
            self.assertEqual(deferred.code, "DEFERRED")

    def test_established_assessment_rejects_side_and_pending_routes_that_cannot_bind_core_feature(self) -> None:
        # Given: an established target plus a primary with active and pending side routes
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            established = workflow.new("Established capability")
            workflow.reject()
            if established.feature_id is None:
                self.fail("expected established Core feature")
            selected = replace(
                assessment(
                    AssessmentOperation.CHANGE,
                    Classification.PATCH,
                    CapabilityState.ESTABLISHED,
                    feature_id=established.feature_id,
                ),
                selected_capability="Established capability",
            )
            workflow.new("Primary capability")
            before_side = CoreService.open(root).recover()

            # When: side work would need a distinct Feature despite selected Core identity
            side = workflow.change("unrelated: side capability", assessment=selected)

            # Then: Core rejects before creating a duplicate side feature
            self.assertEqual(side.code, "CORE_CONFLICT")
            self.assertEqual(CoreService.open(root).recover(), before_side)
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            workflow.change("unrelated: active side capability")
            workflow.change("unrelated: deferred capability")
            workflow.reject()
            before_pending = CoreService.open(root).recover()

            # When: pending work would also need a distinct side Feature
            pending = workflow.change("deferred capability", assessment=selected)

            # Then: its authoritative state is unchanged
            self.assertEqual(pending.code, "CORE_CONFLICT")
            self.assertEqual(CoreService.open(root).recover(), before_pending)

    def test_established_assessment_reuses_selected_feature_for_linked_release(self) -> None:
        # Given: a released linked target and a separate established Core feature
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            established = workflow.new("Established capability")
            workflow.reject()
            if established.feature_id is None:
                self.fail("expected established Core feature")
            workflow.new("First capability | Second capability")
            workflow.reject()
            before = CoreService.open(root).recover()
            selected = replace(
                assessment(
                    AssessmentOperation.CHANGE,
                    Classification.PATCH,
                    CapabilityState.ESTABLISHED,
                    feature_id=established.feature_id,
                ),
                selected_capability="Established capability",
            )

            # When: the released linked route selects the established Core identity
            result = workflow.change("Second capability", assessment=selected)
            state = CoreService.open(root).recover()

            # Then: the Change binds to the canonical existing feature without duplication
            self.assertEqual(result.code, "PROPOSED")
            self.assertEqual(result.feature_id, established.feature_id)
            self.assertEqual(len(state.features), len(before.features))
            self.assertEqual(state.changes[-1].feature_id, FeatureId(established.feature_id))

    def test_duplicate_material_ambiguities_are_rejected_before_intake(self) -> None:
        # Given: a structurally duplicated material ambiguity detail
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            before = CoreService.open(root).recover()
            supplied = assessment(
                AssessmentOperation.NEW,
                Classification.FEATURE,
                CapabilityState.PLANNED,
                ambiguities=(MaterialDecision("delivery channel", ("email", "download")), MaterialDecision("delivery channel", ("email", "download"))),
            )

            # When: the assessment reaches the Core boundary
            result = workflow.new("Add export", assessment=supplied)

            # Then: no duplicate question detail can become durable authority
            self.assertEqual(result.code, "CORE_CONFLICT")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_clarification_resolves_only_matching_persisted_material_details(self) -> None:
        # Given: two independently actionable persisted ambiguity details
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            intake = workflow.new(
                "Add export",
                assessment=assessment(
                    AssessmentOperation.NEW,
                    Classification.FEATURE,
                    CapabilityState.PLANNED,
                    ambiguities=(MaterialDecision("delivery channel", ("email", "download")), MaterialDecision("retention period", ("thirty days", "one year"))),
                ),
            )
            (first,) = intake.questions

            # When: one matching and one unrelated answer arrive after a fresh reload
            partial = WorkflowService.open(root).clarify(
                (
                    ClarificationAnswer(first.id, "email"),
                )
            )
            recovered = CoreService.open(root).recover().active_context

            # Then: only the matched detail resolves and the remaining ID remains actionable
            self.assertEqual(partial.code, "NEEDS_CLARIFICATION")
            (second,) = partial.questions
            if recovered is None:
                self.fail("expected clarification context")
            self.assertEqual(tuple(item.decision for item in recovered.safety_facts.material_clarifications), ("retention period",))
            ready = WorkflowService.open(root).clarify(
                (ClarificationAnswer(second.id, "thirty days"),)
            )
            self.assertEqual(ready.code, "READY")
