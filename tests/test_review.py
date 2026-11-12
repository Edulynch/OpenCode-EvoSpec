from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from evospec.domain import Change, ChangeId, Feature, ReviewResult, VerificationEvidence
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.services import CoreService, ServiceError
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import (
    advance_context,
    implementation_context,
    proposed_context,
)


class ReviewTests(unittest.TestCase):
    def test_inspection_is_read_only_and_convergence_requires_guards(self) -> None:
        # Given: persisted review and verification facts
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            context = implementation_context("feature-main", "change-main")
            state = service.record_feature(service.recover(), Feature(context.feature_id, "Main", None))
            change = Change(ChangeId("change-main"), context.feature_id, "Main change")
            state = service.record_change(state, change)
            state = advance_context(service, state, context)
            state = service.advance_lifecycle(
                state, LifecycleState.IN_PROGRESS, Phase.REVIEW
            )

            # When/Then: insufficient guards cannot be converged automatically
            state = service.record_review(
                state, ReviewResult(ReviewVerdict.BLOCKED, (), False)
            )
            with self.assertRaises(ServiceError):
                service.advance_lifecycle(
                    state, LifecycleState.VERIFYING, Phase.CONVERGE
                )

    def test_state_linked_convergence_requires_verify_then_sufficient_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            context = implementation_context("feature-main", "change-main")
            state = service.record_feature(service.recover(), Feature(context.feature_id, "Main", None))
            change = Change(ChangeId("change-main"), context.feature_id, "Main change")
            state = service.record_change(state, change)
            review = ReviewResult(ReviewVerdict.PASS, (), True)
            review_context = replace(context, phase=Phase.REVIEW, review=review)
            state = advance_context(service, state, review_context)
            evidence = VerificationEvidence(("tests",), True)
            verifying = replace(
                review_context,
                status=LifecycleState.VERIFYING,
                phase=Phase.VERIFY,
                verification=evidence,
            )
            state = service.advance_lifecycle(
                state, LifecycleState.VERIFYING, Phase.VERIFY
            )
            state = service.record_verification(state, evidence)
            state = service.advance_lifecycle(
                state, LifecycleState.VERIFYING, Phase.CONVERGE
            )
            converging = service.confirm_convergence(state)

            self.assertEqual(converging.active_context.phase if converging.active_context else None, Phase.CONVERGE)

    def test_implementation_alteration_invalidates_prior_checkpoints(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            change = Change(
                ChangeId("change-main"),
                implementation_context("feature-main", "change-main").feature_id,
                "Main",
            )
            state = service.record_feature(
                service.recover(), Feature(change.feature_id, "Main", None)
            )
            state = service.record_change(state, change)
            state = advance_context(
                service,
                state,
                replace(
                    implementation_context("feature-main", "change-main"),
                    phase=Phase.VERIFY,
                    review=ReviewResult(ReviewVerdict.PASS, (), True),
                    verification=VerificationEvidence(("tests",), True),
                ),
            )

            altered = service.record_implementation_alteration(state)

            self.assertIsNone(altered.active_context.review if altered.active_context else None)
            self.assertFalse(
                altered.active_context.verification.sufficient
                if altered.active_context
                else True
            )
            self.assertFalse(
                altered.active_context.convergence_complete
                if altered.active_context
                else True
            )

    def test_recorded_implementation_alteration_preserves_lifecycle_pair(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            change = Change(ChangeId("change-main"), implementation_context("feature-main", "change-main").feature_id, "Main")
            state = service.record_feature(
                service.recover(), Feature(change.feature_id, "Main", None)
            )
            state = service.record_change(state, change)
            state = advance_context(
                service,
                state,
                implementation_context("feature-main", "change-main"),
            )

            altered = service.record_implementation_alteration(state)

            self.assertEqual(
                (altered.active_context.status, altered.active_context.phase)
                if altered.active_context is not None
                else None,
                (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT),
            )

    def test_review_and_verification_require_their_matching_phases(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            change = Change(
                ChangeId("change-main"),
                implementation_context("feature-main", "change-main").feature_id,
                "Main",
            )
            state = service.record_feature(
                service.recover(), Feature(change.feature_id, "Main", None)
            )
            state = service.record_change(state, change)

            with self.assertRaises(ServiceError):
                service.record_review(state, ReviewResult(ReviewVerdict.PASS, (), True))
            self.assertEqual(service.recover(), state)

    def test_record_review_rejects_non_string_findings_atomically(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            context = implementation_context("feature-main", "change-main")
            state = service.record_feature(
                service.recover(), Feature(context.feature_id, "Main", None)
            )
            state = service.record_change(
                state, Change(ChangeId("change-main"), context.feature_id, "Main change")
            )
            state = service.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
            state = service.advance_lifecycle(
                state, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT
            )
            state = service.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.REVIEW)
            review = ReviewResult(ReviewVerdict.PASS, (), True)
            object.__setattr__(review, "findings", (123,))

            with self.assertRaises(ServiceError):
                service.record_review(state, review)

            self.assertEqual(service.recover(), state)
            with self.assertRaises(ServiceError):
                service.record_verification(
                    state, VerificationEvidence(("tests",), True)
                )
            self.assertEqual(service.recover(), state)

    def test_record_review_rejects_malformed_verdict_atomically(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            context = implementation_context("feature-main", "change-main")
            state = service.record_feature(
                service.recover(), Feature(context.feature_id, "Main", None)
            )
            state = service.record_change(
                state, Change(ChangeId("change-main"), context.feature_id, "Main change")
            )
            state = service.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
            state = service.advance_lifecycle(
                state, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT
            )
            state = service.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.REVIEW)
            review = ReviewResult(ReviewVerdict.PASS, (), True)
            object.__setattr__(review, "verdict", "PASS")

            with self.assertRaises(ServiceError):
                service.record_review(state, review)

            self.assertEqual(service.recover(), state)

    def test_alteration_returns_review_or_verification_work_to_implementation(self) -> None:
        for phase in (Phase.REVIEW, Phase.VERIFY, Phase.CONVERGE):
            with self.subTest(phase=phase), TemporaryDirectory() as temporary:
                service = CoreService.open(Path(temporary))
                change = Change(
                    ChangeId("change-main"),
                    implementation_context("feature-main", "change-main").feature_id,
                    "Main",
                )
                state = service.record_feature(
                    service.recover(), Feature(change.feature_id, "Main", None)
                )
                state = service.record_change(state, change)
                target = replace(
                    implementation_context("feature-main", "change-main"),
                    status=(
                        LifecycleState.IN_PROGRESS
                        if phase == Phase.REVIEW
                        else LifecycleState.VERIFYING
                    ),
                    phase=phase,
                    review=ReviewResult(ReviewVerdict.PASS, (), True),
                    verification=VerificationEvidence(("tests",), True),
                )
                state = advance_context(service, state, target)

                altered = service.record_implementation_alteration(state)

                self.assertEqual(
                    (altered.active_context.status, altered.active_context.phase)
                    if altered.active_context is not None
                    else None,
                    (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT),
                )
                self.assertEqual(service.recover(), altered)

    def test_inspection_returns_holistic_state_without_mutation(self) -> None:
        # Given: a context with durable review, verification, and work facts
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            context = implementation_context("feature-main", "change-main")
            state = service.record_feature(service.recover(), Feature(context.feature_id, "Main", None))
            change = Change(ChangeId("change-main"), context.feature_id, "Main change")
            state = service.record_change(state, change)
            state = advance_context(service, state, context)

            # When: a caller requests inspection
            inspection = service.inspect_review()

            # Then: inspection is read-only and includes recovery-relevant facts
            self.assertEqual(inspection.context, context)
            self.assertEqual(service.recover(), state)

    def test_repair_count_advances_once_and_cannot_reset(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            change = Change(ChangeId("change-main"), implementation_context("feature-main", "change-main").feature_id, "Main")
            state = service.record_feature(service.recover(), Feature(change.feature_id, "Main", None))
            state = service.record_change(state, change)

            once = service.record_repair(state, 1, None, None)
            twice = service.record_repair(once, 2, "blocked", "ask owner")

            self.assertEqual(twice.active_context.repair_count if twice.active_context else None, 2)
            with self.assertRaises(ServiceError):
                service.record_repair(twice, 1, None, None)
            self.assertEqual(CoreService.open(Path(temporary)).recover(), twice)
