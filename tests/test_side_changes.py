from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from evospec.domain import (
    Change,
    ChangeId,
    Feature,
    FeatureId,
    LivingSpecId,
    ReviewResult,
    StateValidationError,
    VerificationEvidence,
    validate_state,
)
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.services import CoreService, ServiceError
from tests._tempdir import TemporaryDirectory
from tests.test_persistence_atomic import FailSecondReplace
from tests.test_services_state import (
    advance_context,
    convergence_assessment,
    implementation_context,
    proposed_context,
)


class SideChangeTests(unittest.TestCase):
    def test_accepted_side_publication_restores_primary_verbatim(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            primary = implementation_context("feature-primary", "change-primary")
            primary_change = Change(
                ChangeId("change-primary"), primary.feature_id, "Primary"
            )
            state = service.record_feature(
                service.recover(), Feature(primary.feature_id, "Primary", None)
            )
            state = service.record_change(state, primary_change)
            state = advance_context(service, state, primary)
            side_feature = Feature(
                FeatureId("feature-side"),
                "Side",
                LivingSpecId("spec-side"),
                primary.feature_id,
            )
            state = service.record_feature(state, side_feature)
            side_change = Change(ChangeId("change-side"), side_feature.id, "Side")
            state = service.activate_side(state, side_change)
            converged = replace(
                implementation_context(side_feature.id, side_change.id),
                side_of=primary.feature_id,
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, converged)

            published = service.publish_accepted_side_convergence(
                state, "1", "side truth\n", convergence_assessment(side_change.id, True)
            )

            self.assertEqual(published.active_context, primary)
            self.assertIsNone(published.preserved_primary_context)
            self.assertEqual(
                published.completed_side_contexts[-1].context.publication,
                published.publications[-1],
            )
            self.assertEqual(CoreService.open(root).recover(), published)
            self.assertEqual(
                (root / ".evospec" / "living-specs" / "spec-side" / "1.md").read_text(
                    encoding="utf-8"
                ),
                "side truth\n",
            )

    def test_failed_side_state_commit_leaves_publication_non_authoritative(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            primary = implementation_context("feature-primary", "change-primary")
            primary_change = Change(
                ChangeId("change-primary"), primary.feature_id, "Primary"
            )
            state = service.record_feature(
                service.recover(), Feature(primary.feature_id, "Primary", None)
            )
            state = service.record_change(state, primary_change)
            state = advance_context(service, state, primary)
            side_feature = Feature(
                FeatureId("feature-side"),
                "Side",
                LivingSpecId("spec-side"),
                primary.feature_id,
            )
            state = service.record_feature(state, side_feature)
            side_change = Change(ChangeId("change-side"), side_feature.id, "Side")
            state = service.activate_side(state, side_change)
            converged = replace(
                implementation_context(side_feature.id, side_change.id),
                side_of=primary.feature_id,
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, converged)
            failing = CoreService.open(root, FailSecondReplace())

            with self.assertRaises(ServiceError):
                failing.publish_accepted_side_convergence(state, "1", "orphan\n", convergence_assessment(side_change.id, True))

            self.assertEqual(CoreService.open(root).recover(), state)
            self.assertTrue(
                (root / ".evospec" / "living-specs" / "spec-side" / "1.md").is_file()
            )
    def test_side_activation_preserves_and_restores_context_verbatim(self) -> None:
        # Given: primary and independent side work
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            primary = implementation_context("feature-primary", "change-primary")
            side = implementation_context("feature-side", "change-side")
            side = replace(side, side_of=primary.feature_id)
            initial = service.record_feature(service.recover(), Feature(primary.feature_id, "Primary", None))
            primary_change = Change(ChangeId("change-primary"), primary.feature_id, "Primary change")
            initial = service.record_change(initial, primary_change)
            initial = advance_context(service, initial, primary)
            initial = service.record_feature(initial, Feature(side.feature_id, "Side", None, primary.feature_id))
            side_change = Change(ChangeId("change-side"), side.feature_id, "Side change")

            # When: caller activates then explicitly restores side work
            side_state = service.activate_side(initial, side_change)
            side_state = advance_context(service, side_state, side)
            completed_side = replace(side, status=LifecycleState.ABANDONED)
            restored = service.restore_primary(side_state, completed_side)

            # Then: the original serialized context is exactly active again
            self.assertEqual(side_state.preserved_primary_context, primary)
            self.assertEqual(restored.active_context, primary)
            self.assertIsNone(restored.preserved_primary_context)
            self.assertEqual(restored.completed_side_contexts[0].context, completed_side)

            with self.assertRaises(StateValidationError):
                validate_state(replace(restored, completed_side_contexts=()))

    def test_side_activation_rejects_non_initial_context_without_writes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            primary = implementation_context("feature-primary", "change-primary")
            primary_change = Change(
                ChangeId("change-primary"), primary.feature_id, "Primary"
            )
            state = service.record_feature(
                service.recover(), Feature(primary.feature_id, "Primary", None)
            )
            state = service.record_change(state, primary_change)
            state = advance_context(service, state, primary)
            side = replace(
                implementation_context("feature-side", "change-side"),
                side_of=primary.feature_id,
            )
            side_change = Change(ChangeId("change-side"), side.feature_id, "Side")
            state = service.record_feature(
                state, Feature(side.feature_id, "Side", None, primary.feature_id)
            )

            from inspect import signature

            self.assertEqual(
                tuple(signature(service.activate_side).parameters),
                ("state", "change"),
            )

            self.assertEqual(service.recover(), state)
            self.assertFalse(
                (root / ".evospec" / "records" / "changes" / "change-side.json").exists()
            )
