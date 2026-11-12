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
from tests.test_services_state import (
    advance_context,
    convergence_assessment,
    implementation_context,
    proposed_context,
)


def bootstrap(
    service: CoreService, living_spec: LivingSpecId | None = None
) -> tuple[State, Feature, Change]:
    feature = Feature(FeatureId("feature-cart"), "Cart", living_spec)
    change = Change(ChangeId("change-cart"), feature.id, "Cart change")
    state = service.record_feature(service.recover(), feature)
    state = service.record_change(state, change)
    return state, feature, change


class TruthBoundaryTests(unittest.TestCase):
    def test_material_history_is_linked_without_changing_product_truth(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            state, feature, change = bootstrap(service)
            decision = Decision(
                DecisionId("decision-cart"),
                feature.id,
                "constraint",
                "Keep tax",
                change.id,
            )
            state = service.record_decision(state, decision)
            observation = Observation(
                ObservationId("experiment-cart"),
                change.id,
                "experiment",
                "Rejected trial",
                outcome=ExperimentOutcome.REJECTED,
            )
            state = service.record_observation(state, observation)

            recovered = CoreService.open(Path(temporary)).recover()
            self.assertEqual(recovered, state)
            self.assertEqual(recovered.publications, ())
            self.assertEqual(recovered.decisions, (decision,))
            self.assertEqual(recovered.observations, (observation,))

    def test_supersession_preserves_original_and_selects_one_successor(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            state, feature, change = bootstrap(service)
            original = Decision(
                DecisionId("decision-old"), feature.id, "clarification", "Old", change.id
            )
            state = service.record_decision(state, original)
            replacement = Decision(
                DecisionId("decision-new"),
                feature.id,
                "clarification",
                "New",
                change.id,
                original.id,
            )
            state = service.supersede_decision(state, replacement)

            self.assertEqual(state.decisions, (original, replacement))
            with self.assertRaises(ServiceError):
                service.supersede_decision(
                    state,
                    replace(replacement, id=DecisionId("decision-other")),
                )
            with self.assertRaises(ServiceError):
                service.record_decision(
                    state,
                    replace(replacement, id=DecisionId("decision-direct")),
                )

    def test_supersession_updates_active_reference_without_rewriting_history(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            state, feature, change = bootstrap(service)
            original = Decision(
                DecisionId("decision-old"), feature.id, "clarification", "Old", change.id
            )
            state = service.record_decision(state, original)
            active = replace(
                implementation_context(feature.id, change.id),
                decision_ids=(original.id,),
            )
            state = advance_context(service, state, active)
            replacement = Decision(
                DecisionId("decision-new"),
                feature.id,
                "clarification",
                "New",
                change.id,
                original.id,
            )

            state = service.supersede_decision(state, replacement)

            self.assertEqual(state.active_context.decision_ids if state.active_context else (), (replacement.id,))
            self.assertEqual(state.decisions, (original, replacement))
            self.assertEqual(service.inspect_review().current_decisions, (replacement,))

    def test_only_accepted_archive_can_select_living_spec_truth(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            state, feature, change = bootstrap(service, LivingSpecId("spec-cart"))
            context = implementation_context(feature.id, change.id)
            converged = replace(
                context,
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, converged)
            state = service.publish_accepted_convergence(
                state,
                feature.id,
                change.id,
                LivingSpecId("spec-cart"),
                "1",
                "accepted truth\n",
                convergence_assessment(change.id, True),
            )
            drift = Drift(
                ObservationId("drift-cart"),
                LivingSpecId("spec-cart"),
                "runtime differs",
                change.id,
            )
            state = service.record_drift(state, drift)
            orphan = root / ".evospec" / "living-specs" / "spec-cart" / "99.md"
            orphan.write_text("orphan\n", encoding="utf-8")

            recovered = CoreService.open(root).recover()
            self.assertEqual(recovered, state)
            self.assertEqual(recovered.publications[0].revision, "1")
            accepted = next(
                item
                for item in recovered.terminal_archives
                if item.context.change_id == change.id
            )
            self.assertEqual(accepted.context.publication, recovered.publications[0])
            self.assertEqual(recovered.drifts, (drift,))

    def test_publication_evolution_links_predecessor_and_new_accepted_change(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            state, feature, first_change = bootstrap(service, LivingSpecId("spec-cart"))
            first = replace(
                implementation_context(feature.id, first_change.id),
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, first)
            state = service.publish_accepted_convergence(
                state, feature.id, first_change.id, LivingSpecId("spec-cart"), "1", "one\n", convergence_assessment(first_change.id, True)
            )
            first_archive = state.terminal_archives[0]
            second_change = Change(ChangeId("change-cart-2"), feature.id, "Evolve cart")
            state = service.record_change(state, second_change)
            second = replace(
                implementation_context(feature.id, second_change.id),
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, second)

            state = service.publish_accepted_convergence(
                state, feature.id, second_change.id, LivingSpecId("spec-cart"), "2", "two\n", convergence_assessment(second_change.id, True)
            )

            self.assertEqual(state.publications[0].previous_revision, "1")
            self.assertEqual(tuple(item.revision for item in state.publication_history), ("1", "2"))
            self.assertEqual(state.terminal_archives[0], first_archive)
            self.assertEqual(first_archive.context.publication.revision if first_archive.context.publication else None, "1")
            self.assertEqual(CoreService.open(root).recover(), state)

    def test_experiment_requires_explicit_outcome(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            state, _feature, change = bootstrap(service)

            with self.assertRaises(ServiceError):
                service.record_observation(
                    state,
                    Observation(
                        ObservationId("experiment-implicit"),
                        change.id,
                        "experiment",
                        "No outcome",
                    ),
                )
