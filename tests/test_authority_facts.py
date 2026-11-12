from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from evospec.domain import (
    Change,
    ChangeId,
    Decision,
    DecisionId,
    Drift,
    ExperimentOutcome,
    Feature,
    FeatureId,
    LivingSpecId,
    Observation,
    ObservationId,
    ReviewResult,
    State,
    VerificationEvidence,
)
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.services import CoreService, ServiceError
from tests._tempdir import TemporaryDirectory
from tests.test_authority_redesign import context
from tests.test_services_state import (
    advance_context,
    convergence_assessment,
    proposed_context,
)


def bootstrap(root: Path, living_spec_id: LivingSpecId | None = None) -> tuple[CoreService, State]:
    service = CoreService.open(root)
    feature_id = FeatureId("feature-main")
    change_id = ChangeId("change-main")
    state = service.record_feature(service.recover(), Feature(feature_id, "Core", living_spec_id))
    change = Change(change_id, feature_id, "Implement authority")
    state = service.record_change(state, change)
    return service, state


class AuthorityFactTests(unittest.TestCase):
    def test_records_and_holistic_inspection_are_state_linked_and_read_only(self) -> None:
        with TemporaryDirectory() as temporary:
            service, state = bootstrap(Path(temporary), LivingSpecId("spec-main"))
            decision = Decision(DecisionId("decision-main"), FeatureId("feature-main"), "constraint", "Keep state authoritative", ChangeId("change-main"))
            state = service.record_decision(state, decision)
            experiment = Observation(ObservationId("experiment-main"), ChangeId("change-main"), "experiment", "Trial failed", outcome=ExperimentOutcome.REJECTED)
            state = service.record_observation(state, experiment)

            with self.assertRaises(ServiceError):
                service.record_observation(state, Observation(ObservationId("experiment-implicit"), ChangeId("change-main"), "experiment", "No outcome"))

            inspection = service.inspect_review()
            self.assertEqual(inspection.decisions, (decision,))
            self.assertEqual(inspection.observations, (experiment,))
            self.assertEqual(service.recover(), state)

    def test_publication_requires_authoritative_accepted_archive_identity(self) -> None:
        with TemporaryDirectory() as temporary:
            service, state = bootstrap(Path(temporary), LivingSpecId("spec-main"))
            active = replace(
                context(FeatureId("feature-main"), ChangeId("change-main")),
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, active)

            archived = service.accept_convergence_without_publication(
                state, convergence_assessment(active.change_id, False)
            )
            self.assertEqual(archived.publications, ())
            self.assertFalse((Path(temporary) / ".evospec" / "living-specs").exists())

    def test_accepted_side_can_restore_primary_without_publication(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service, state = bootstrap(root)
            primary = context(FeatureId("feature-main"), ChangeId("change-main"))
            state = advance_context(service, state, primary)
            side_feature = Feature(
                FeatureId("feature-side"),
                "Side",
                LivingSpecId("spec-side"),
                primary.feature_id,
            )
            side_change = Change(ChangeId("change-side"), side_feature.id, "Side")
            state = service.record_feature(state, side_feature)
            state = service.activate_side(state, side_change)
            converged = replace(
                context(side_feature.id, side_change.id, side_of=primary.feature_id),
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, converged)

            restored = service.accept_convergence_without_publication(
                state, convergence_assessment(converged.change_id, False)
            )

            self.assertEqual(restored.active_context, primary)
            self.assertIsNone(restored.completed_side_contexts[-1].context.publication)
            self.assertFalse((root / ".evospec" / "living-specs").exists())


    def test_drift_references_authoritative_publication(self) -> None:
        with TemporaryDirectory() as temporary:
            service, state = bootstrap(Path(temporary))
            with self.assertRaises(ServiceError):
                service.record_drift(state, Drift(ObservationId("drift-main"), LivingSpecId("missing-spec"), "runtime differs"))


if __name__ == "__main__":
    unittest.main()
