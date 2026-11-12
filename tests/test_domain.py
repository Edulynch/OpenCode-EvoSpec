from __future__ import annotations

import unittest

from evospec.domain import (
    Change,
    ChangeId,
    Decision,
    DomainError,
    DecisionId,
    Feature,
    FeatureId,
    LivingSpecId,
    LivingSpecPublication,
    Observation,
    ObservationId,
    RepositoryContext,
    RepositoryPath,
    WorkingContext,
    VerificationEvidence,
)
from evospec.lifecycle import LifecycleState, Phase


class DomainTests(unittest.TestCase):
    def test_records_preserve_explicit_relationships(self) -> None:
        # Given: stable, repository-relative product records
        publication = LivingSpecPublication(
            LivingSpecId("spec-auth"), "1", "Authentication", RepositoryPath("living-specs/spec-auth/1.md")
        )
        feature = Feature(FeatureId("feature-auth"), "Authentication", publication.living_spec_id)
        change = Change(ChangeId("change-1"), feature.id, "Add sign-in")
        decision = Decision(DecisionId("decision-1"), feature.id, "clarification", "Session duration")
        observation = Observation(ObservationId("observation-1"), change.id, "test", "Observed behavior")

        # When: one canonical context is constructed
        context = WorkingContext(
            feature.id, change.id, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT, "implement", "auth",
            (), "write auth", ("test auth",), (decision.id,), (), None, (), VerificationEvidence((), False),
            False, None, 0, "continue", (change.id,), (observation.id,)
        )

        # Then: relationships are explicit and recovery data is complete
        self.assertEqual(RepositoryContext(RepositoryPath(".")).root, RepositoryPath("."))
        self.assertEqual(context.feature_id, feature.id)
        self.assertEqual(context.decision_ids, (decision.id,))
        self.assertEqual(context.observation_ids, (observation.id,))

    def test_repository_path_rejects_nonportable_or_escaping_references(self) -> None:
        # Given/When/Then: persisted paths have one portable representation
        for value in ("/absolute", "../escape", "a\\b", "C:/root", "//server/share"):
            with self.assertRaises(DomainError):
                RepositoryPath(value)
