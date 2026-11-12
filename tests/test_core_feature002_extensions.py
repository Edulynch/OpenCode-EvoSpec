from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from evospec.domain import (
    Change,
    ChangeId,
    DeferredLinkedIntent,
    DeferredSideRequest,
    Feature,
    FeatureId,
    IntakeId,
    LivingSpecId,
    PendingIntake,
    RepositoryInitialization,
    ReviewEvidence,
    ReviewEvidenceCategory,
    ReviewEvidenceKind,
    ReviewFinding,
    ReviewResult,
    SharedIntent,
    SharedIntentId,
    State,
    StateValidationError,
    VerificationEvidence,
    validate_state,
)
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.services import CoreService, ServiceError
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import (
    advance_context,
    convergence_assessment,
    implementation_context,
)


class Feature002CoreTests(unittest.TestCase):
    def test_initialization_is_idempotent_and_persisted(self) -> None:
        # Given: an empty repository
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            initialization = RepositoryInitialization("repo", ())

            # When: initialization is requested twice
            first = service.initialize(service.recover(), initialization)
            second = service.initialize(first, initialization)

            # Then: valid empty authority is retained without duplicate work
            self.assertEqual(second.initialization, initialization)
            self.assertIsNone(second.active_context)
            self.assertEqual(CoreService.open(Path(temporary)).recover(), second)

    def test_terminal_side_completion_creates_pending_intake_atomically(self) -> None:
        # Given: primary work, active side work, and one deferred side request
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            primary = implementation_context("primary", "primary-change")
            state = service.record_feature(service.recover(), Feature(primary.feature_id, "Primary", None))
            state = service.record_change(state, Change(ChangeId("primary-change"), primary.feature_id, "Primary"))
            state = advance_context(service, state, primary)
            side_feature = Feature(FeatureId("side"), "Side", None, primary.feature_id)
            state = service.record_feature(state, side_feature)
            state = service.activate_side(state, Change(ChangeId("side-change"), side_feature.id, "Side"))
            state = service.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
            state = service.defer_side_request(
                state,
                DeferredSideRequest(IntakeId("later"), "Later", primary.feature_id, "side is active"),
            )
            if state.active_context is None:
                self.fail("expected active context")
            terminal = replace(state.active_context, status=LifecycleState.ABANDONED)

            # When: Core completes the terminal side in one mutation
            restored = service.complete_side(state, terminal)

            # Then: primary is exact and the request is a non-lifecycle pending intake
            self.assertEqual(restored.active_context, primary)
            self.assertIsNone(restored.preserved_primary_context)
            self.assertIsNone(restored.deferred_side_request)
            if restored.pending_intake is None:
                self.fail("expected pending intake")
            self.assertEqual(restored.pending_intake.intent, "Later")
            self.assertEqual(CoreService.open(root).recover(), restored)

    def test_second_deferred_side_request_is_rejected_without_mutation(self) -> None:
        # Given: active side work with one deferred request
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            primary = implementation_context("primary", "primary-change")
            state = service.record_feature(service.recover(), Feature(primary.feature_id, "Primary", None))
            state = service.record_change(state, Change(ChangeId("primary-change"), primary.feature_id, "Primary"))
            state = advance_context(service, state, primary)
            side = Feature(FeatureId("side"), "Side", LivingSpecId("spec-side"), primary.feature_id)
            state = service.record_feature(state, side)
            state = service.activate_side(state, Change(ChangeId("side-change"), side.id, "Side"))
            state = service.defer_side_request(state, DeferredSideRequest(IntakeId("one"), "One", primary.feature_id, "active"))

            # When/Then: another request cannot replace authoritative deferral
            with self.assertRaises(ServiceError):
                service.defer_side_request(state, DeferredSideRequest(IntakeId("two"), "Two", primary.feature_id, "active"))
            self.assertEqual(service.recover(), state)

    def test_accepted_side_publication_transforms_deferred_request_to_pending_intake(self) -> None:
        # Given: converged side work with a deferred request and preserved primary
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            primary = implementation_context("primary", "primary-change")
            state = service.record_feature(service.recover(), Feature(primary.feature_id, "Primary", None))
            state = service.record_change(state, Change(ChangeId("primary-change"), primary.feature_id, "Primary"))
            state = advance_context(service, state, primary)
            side = Feature(FeatureId("side"), "Side", LivingSpecId("spec-side"), primary.feature_id)
            state = service.record_feature(state, side)
            state = service.activate_side(state, Change(ChangeId("side-change"), side.id, "Side"))
            state = service.defer_side_request(state, DeferredSideRequest(IntakeId("later"), "Later", primary.feature_id, "side is active"))
            if state.active_context is None:
                self.fail("expected active context")
            converged = replace(
                state.active_context,
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, converged)

            # When: accepted side convergence is published
            restored = service.publish_accepted_side_convergence(state, "1", "side truth\n", convergence_assessment(ChangeId("side-change"), True))

            # Then: the same authoritative commit restores primary and retains intake
            self.assertEqual(restored.active_context, primary)
            self.assertIsNone(restored.deferred_side_request)
            self.assertEqual(restored.pending_intake, PendingIntake(IntakeId("later"), "Later", primary.feature_id, ChangeId("side-change")))

    def test_pending_intake_materializes_as_side_work_over_restored_primary(self) -> None:
        # Given: restored primary work and its deferred non-lifecycle intake
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            primary = implementation_context("primary", "primary-change")
            state = service.record_feature(service.recover(), Feature(primary.feature_id, "Primary", None))
            state = service.record_change(state, Change(ChangeId("primary-change"), primary.feature_id, "Primary"))
            state = advance_context(service, state, primary)
            pending = PendingIntake(IntakeId("later"), "Later", primary.feature_id, ChangeId("side-change"))
            state = service.defer_side_request(
                service.activate_side(
                    service.record_feature(state, Feature(FeatureId("side"), "Side", None, primary.feature_id)),
                    Change(ChangeId("side-change"), FeatureId("side"), "Side"),
                ),
                DeferredSideRequest(pending.id, pending.intent, primary.feature_id, "side is active"),
            )
            state = service.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
            if state.active_context is None:
                self.fail("expected active context")
            state = service.complete_side(state, replace(state.active_context, status=LifecycleState.ABANDONED))

            # When: the caller explicitly materializes it after primary restoration
            materialized = service.materialize_pending(
                state,
                Feature(FeatureId("later"), "Later", None, primary.feature_id),
                Change(ChangeId("later-change"), FeatureId("later"), "Later"),
            )

            # Then: it becomes the only active side lifecycle and is consumed atomically
            if materialized.active_context is None:
                self.fail("expected active context")
            self.assertEqual(materialized.active_context.change_id, ChangeId("later-change"))
            self.assertEqual(materialized.preserved_primary_context, primary)
            self.assertIsNone(materialized.pending_intake)

    def test_pending_intake_rejects_malformed_provenance(self) -> None:
        # Given: an otherwise valid state
        state = State(0, None)

        # When/Then: every pending-intake relationship is validated before persistence
        malformed = PendingIntake(IntakeId("bad intake"), "", FeatureId("missing"), ChangeId("missing-change"))
        with self.assertRaises(StateValidationError):
            validate_state(replace(state, pending_intake=malformed))

    def test_core_persists_typed_review_records_with_the_review_result(self) -> None:
        # Given: work in review with one structured required evidence item and finding
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            context = implementation_context("review", "review-change")
            state = service.record_feature(service.recover(), Feature(context.feature_id, "Review", None))
            state = service.record_change(state, Change(ChangeId("review-change"), context.feature_id, "Review"))
            state = service.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
            state = service.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT)
            state = service.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.REVIEW)
            evidence = ReviewEvidence(ReviewEvidenceCategory.WORKFLOW, ReviewEvidenceKind.FACT, "Core", True, "review context")
            finding = ReviewFinding(ReviewVerdict.WARN, ReviewEvidenceCategory.WORKFLOW, "coverage warning", "evidence is incomplete")

            # When: Core persists the typed review facts and derived result
            saved = service.record_review(
                state,
                ReviewResult(ReviewVerdict.WARN, ("coverage warning",), False),
                evidence=(evidence,),
                findings=(finding,),
            )

            # Then: exact typed facts survive reload with the authoritative review
            self.assertEqual(CoreService.open(Path(temporary)).recover(), saved)
            if saved.active_context is None:
                self.fail("expected active context")
            self.assertEqual(saved.active_context.review_evidence, (evidence,))
            self.assertEqual(saved.active_context.review_findings, (finding,))

    def test_linked_intent_materializes_only_after_terminal_predecessor(self) -> None:
        # Given: a shared intent with a deferred independent capability
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            shared = SharedIntent(SharedIntentId("shared"), "Two related capabilities")
            deferred = DeferredLinkedIntent(IntakeId("linked"), shared.id, "Second", "Second capability")
            state = service.record_shared_intent(service.recover(), shared, deferred)
            first_feature = Feature(FeatureId("first"), "First", None)
            first_change = Change(ChangeId("first-change"), first_feature.id, "First capability", shared_intent_id=shared.id)
            state = service.record_feature(state, first_feature)
            state = service.record_change(state, first_change)

            # When/Then: nonterminal work retains the deferred non-lifecycle intent
            with self.assertRaises(ServiceError):
                service.materialize_linked(state, "linked", Feature(FeatureId("second"), "Second", None), Change(ChangeId("second-change"), FeatureId("second"), "Second capability", shared_intent_id=shared.id))
            state = service.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
            if state.active_context is None:
                self.fail("expected active context")
            state = service.archive_terminal(state, replace(state.active_context, status=LifecycleState.ABANDONED))

            # When: the terminal predecessor permits explicit Core materialization
            materialized = service.materialize_linked(state, "linked", Feature(FeatureId("second"), "Second", None), Change(ChangeId("second-change"), FeatureId("second"), "Second capability", shared_intent_id=shared.id))

            # Then: exactly one new lifecycle owner exists and the deferred intent is consumed
            if materialized.active_context is None:
                self.fail("expected active context")
            self.assertEqual(materialized.active_context.change_id, ChangeId("second-change"))
            self.assertEqual(materialized.deferred_linked_intents, ())
