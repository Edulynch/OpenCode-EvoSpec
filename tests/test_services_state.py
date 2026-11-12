from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast

from evospec.domain import (
    AssessmentConfidence,
    AssessmentEvidence,
    Change,
    ChangeId,
    Checkpoint,
    ConvergenceAssessment,
    ExperimentOutcome,
    Feature,
    FeatureId,
    Observation,
    ObservationId,
    ProductTruthContent,
    RepositoryPath,
    ReviewResult,
    SafetyFacts,
    State,
    VerificationEvidence,
    WorkingContext,
)
from evospec.lifecycle import Classification, LifecycleState, Phase, ReviewVerdict
from evospec.services import CoreService, ServiceError
from tests._tempdir import TemporaryDirectory


def implementation_context(feature: str | FeatureId, change: str | ChangeId) -> WorkingContext:
    feature_id = FeatureId(feature)
    change_id = ChangeId(change)
    return WorkingContext(
        feature_id, change_id, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT,
        "write", "focus", ("completed",), "current", ("pending",), (), (),
        None, (), VerificationEvidence((), False), False, None, 0, "resume",
        (change_id,), (),
    )


def proposed_context(change: Change, side_of: FeatureId | None = None) -> WorkingContext:
    return WorkingContext(
        change.feature_id, change.id, LifecycleState.PROPOSED, Phase.RESEARCH,
        None, None, (), None, (), (), (), None, (), VerificationEvidence((), False),
        False, change.classification, 0, None, (change.id,), (), side_of=side_of,
    )


def convergence_assessment(
    change_id: ChangeId | str | None, product_truth_changed: bool
) -> ConvergenceAssessment:
    if change_id is None:
        raise AssertionError("convergence assessment requires an active Change")
    return ConvergenceAssessment(
        ChangeId(change_id),
        product_truth_changed,
        AssessmentConfidence.HIGH,
        (AssessmentEvidence("test", RepositoryPath("tests/test_services_state.py")),),
        "explicit test convergence decision",
        ProductTruthContent("# Accepted test capability\n\nExplicit test Product Truth.\n")
        if product_truth_changed
        else None,
    )


def advance_context(service: CoreService, state: State, target: WorkingContext) -> State:
    active = state.active_context
    if active is None:
        raise AssertionError("explicit transition helper requires active context")
    if target == active:
        return state
    state = service.record_progress(
        state,
        target.current_action,
        target.focus,
        target.completed_work,
        target.current_work,
        target.pending_work,
        target.resume_point,
    )
    if target.status in (LifecycleState.CLARIFYING, LifecycleState.READY):
        return service.advance_lifecycle(state, target.status, target.phase)
    state = service.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
    state = service.advance_lifecycle(
        state, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT
    )
    if target.phase == Phase.IMPLEMENT:
        return state
    state = service.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.REVIEW)
    if target.review is not None:
        state = service.record_review(state, target.review, target.checkpoints)
    if target.phase == Phase.REVIEW:
        return state
    state = service.advance_lifecycle(state, LifecycleState.VERIFYING, Phase.VERIFY)
    if target.verification != VerificationEvidence((), False):
        state = service.record_verification(state, target.verification)
    if target.phase == Phase.VERIFY:
        return state
    state = service.advance_lifecycle(state, LifecycleState.VERIFYING, Phase.CONVERGE)
    return service.confirm_convergence(state) if target.convergence_complete else state


class ServiceStateTests(unittest.TestCase):
    def test_record_feature_rejects_non_string_title_without_authority(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            prior = service.recover()

            with self.assertRaises(ServiceError):
                malformed_title = cast(str, cast(object, 1))
                service.record_feature(prior, Feature(FeatureId("feature-main"), malformed_title, None))

            self.assertEqual(service.recover(), prior)
            self.assertFalse((root / ".evospec" / "state.json").exists())

    def test_record_progress_rejects_non_string_current_action_without_authority(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            change = Change(ChangeId("change-main"), FeatureId("feature-main"), "Main")
            state = service.record_feature(
                service.recover(), Feature(change.feature_id, "Main", None)
            )
            state = service.record_change(state, change)
            state_path = root / ".evospec" / "state.json"
            before = state_path.read_bytes()

            with self.assertRaises(ServiceError):
                malformed_action = cast(str | None, cast(object, 1))
                service.record_progress(state, malformed_action, None, (), None, (), None)

            self.assertEqual(service.recover(), state)
            self.assertEqual(state_path.read_bytes(), before)

    def test_public_routes_reject_decoder_parity_gaps_without_authority(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            feature = Feature(FeatureId("feature-main"), "Main", None)
            state = service.record_feature(service.recover(), feature)
            state_path = root / ".evospec" / "state.json"

            before = state_path.read_bytes()
            for classification in (1, "PATCH"):
                with self.subTest(classification=classification):
                    with self.assertRaises(ServiceError):
                        service.record_change(
                            state,
                            Change(
                                ChangeId("change-main"),
                                feature.id,
                                "Main",
                                cast(Classification | None, cast(object, classification)),
                            ),
                        )
                    self.assertEqual(service.recover(), state)
                    self.assertEqual(state_path.read_bytes(), before)

            state = service.record_change(
                state, Change(ChangeId("change-main"), feature.id, "Main")
            )
            before = state_path.read_bytes()
            for outcome in (1, "REJECTED"):
                with self.subTest(outcome=outcome):
                    with self.assertRaises(ServiceError):
                        service.record_observation(
                            state,
                            Observation(
                                ObservationId("experiment-main"),
                                ChangeId("change-main"),
                                "experiment",
                                "Trial",
                                outcome=cast(ExperimentOutcome | None, cast(object, outcome)),
                            ),
                        )
                    self.assertEqual(service.recover(), state)
                    self.assertEqual(state_path.read_bytes(), before)

            with self.assertRaises(ServiceError):
                malformed_status = cast(LifecycleState, cast(object, "READY"))
                service.advance_lifecycle(state, malformed_status, Phase.READY)
            self.assertEqual(service.recover(), state)
            self.assertEqual(state_path.read_bytes(), before)

            for progress in (
                (None, 1, (), None, (), None),
                (None, None, (1,), None, (), None),
                (None, None, (), 1, (), None),
                (None, None, (), None, (1,), None),
                (None, None, (), None, (), 1),
            ):
                with self.subTest(progress=progress):
                    with self.assertRaises(ServiceError):
                        service.record_progress(
                            state,
                            cast(str | None, cast(object, progress[0])),
                            cast(str | None, cast(object, progress[1])),
                            cast(tuple[str, ...], cast(object, progress[2])),
                            cast(str | None, cast(object, progress[3])),
                            cast(tuple[str, ...], cast(object, progress[4])),
                            cast(str | None, cast(object, progress[5])),
                        )
                    self.assertEqual(service.recover(), state)
                    self.assertEqual(state_path.read_bytes(), before)

            review_state = service.advance_lifecycle(
                service.advance_lifecycle(state, LifecycleState.READY, Phase.READY),
                LifecycleState.IN_PROGRESS,
                Phase.IMPLEMENT,
            )
            review_state = service.advance_lifecycle(
                review_state,
                LifecycleState.IN_PROGRESS,
                Phase.REVIEW,
            )
            before = state_path.read_bytes()
            with self.assertRaises(ServiceError):
                service.record_review(
                    review_state,
                    ReviewResult(ReviewVerdict.PASS, (cast(str, cast(object, 1)),), True),
                    (Checkpoint(cast(str, cast(object, 1)), True),),
                )
            self.assertEqual(service.recover(), review_state)
            self.assertEqual(state_path.read_bytes(), before)

            review_state = service.record_review(
                review_state, ReviewResult(ReviewVerdict.PASS, (), True)
            )
            verification_state = service.advance_lifecycle(
                review_state, LifecycleState.VERIFYING, Phase.VERIFY
            )
            before = state_path.read_bytes()
            with self.assertRaises(ServiceError):
                service.record_verification(
                    verification_state,
                    VerificationEvidence((cast(str, cast(object, 1)),), True),
                )
            self.assertEqual(service.recover(), verification_state)
            self.assertEqual(state_path.read_bytes(), before)

            for repair in ((1, None), (None, 1)):
                with self.subTest(repair=repair):
                    with self.assertRaises(ServiceError):
                        service.record_repair(
                            verification_state,
                            1,
                            cast(str | None, cast(object, repair[0])),
                            cast(str | None, cast(object, repair[1])),
                        )
                    self.assertEqual(service.recover(), verification_state)
                    self.assertEqual(state_path.read_bytes(), before)

            active = verification_state.active_context
            if active is None:
                self.fail("verification route must retain active context")
            before = state_path.read_bytes()
            with self.assertRaises(ServiceError):
                service.archive_terminal(
                    verification_state,
                    replace(active, status="REJECTED", phase=Phase.VERIFY),
                )
            self.assertEqual(service.recover(), verification_state)
            self.assertEqual(state_path.read_bytes(), before)

    def test_record_change_requires_exact_initial_context_atomically(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            change = Change(ChangeId("change-main"), FeatureId("feature-main"), "Main")
            state = service.record_feature(
                service.recover(), Feature(change.feature_id, "Main", None)
            )
            from inspect import signature

            self.assertEqual(
                tuple(signature(service.record_change).parameters),
                ("state", "change"),
            )

            self.assertEqual(service.recover(), state)
            self.assertFalse(
                (root / ".evospec" / "records" / "changes" / "change-main.json").exists()
            )

    def test_each_lifecycle_transition_is_caller_recorded_once(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            change = Change(ChangeId("change-main"), FeatureId("feature-main"), "Main")
            state = service.record_feature(
                service.recover(), Feature(change.feature_id, "Main", None)
            )
            initial = proposed_context(change)

            established = service.record_change(state, change)

            self.assertEqual(established.active_context, initial)
            self.assertEqual(established.revision, state.revision + 1)
            forged = replace(
                initial,
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            with self.assertRaises(ServiceError):
                service.advance_lifecycle(established, forged.status, forged.phase)
            self.assertEqual(service.recover(), established)

    def test_caller_persists_and_recovers_full_canonical_context(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            context = implementation_context("feature-main", "change-main")
            change_id = ChangeId("change-main")
            change = Change(change_id, context.feature_id, "Main change")
            state = service.record_feature(
                service.recover(), Feature(context.feature_id, "Main", None)
            )
            state = service.record_change(state, change)

            saved = advance_context(service, state, context)

            self.assertEqual(service.recover(), saved)
            self.assertEqual(saved.active_context, context)

    def test_empty_clarifications_remain_valid(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            context = implementation_context("feature-main", "change-main")
            change = Change(ChangeId("change-main"), context.feature_id, "Main change")
            state = service.record_feature(
                service.recover(), Feature(context.feature_id, "Main", None)
            )
            state = service.record_change(state, change)

            saved = advance_context(service, state, context)

            self.assertEqual(saved.active_context.decision_ids if saved.active_context else (), ())

    def test_caller_safety_facts_round_trip_without_advancing_work(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            change = Change(ChangeId("change-main"), FeatureId("feature-main"), "Main")
            state = service.record_feature(service.recover(), Feature(change.feature_id, "Main", None))
            state = service.record_change(state, change)
            facts = SafetyFacts(("Choose migration",), ("API",), ("data loss",), "deep", "full", "full")

            saved = service.record_safety_facts(state, True, facts)

            self.assertEqual(CoreService.open(Path(temporary)).recover(), saved)
            self.assertEqual(saved.active_context.safety_facts if saved.active_context else None, facts)
            with self.assertRaises(ServiceError):
                service.advance_lifecycle(saved, LifecycleState.READY, Phase.READY)

    def test_record_safety_facts_rejects_non_string_tuple_entries_atomically(self) -> None:
        for field in (
            "unresolved_decision_details",
            "scope",
            "risks",
        ):
            with self.subTest(field=field), TemporaryDirectory() as temporary:
                service = CoreService.open(Path(temporary))
                change = Change(ChangeId("change-main"), FeatureId("feature-main"), "Main")
                state = service.record_feature(
                    service.recover(), Feature(change.feature_id, "Main", None)
                )
                state = service.record_change(state, change)
                facts = SafetyFacts()
                object.__setattr__(facts, field, (123,))

                with self.assertRaises(ServiceError):
                    service.record_safety_facts(state, True, facts)

                self.assertEqual(service.recover(), state)

    def test_record_safety_facts_rejects_non_string_depths_atomically(self) -> None:
        for field in ("review_depth", "verification_depth", "convergence_depth"):
            with self.subTest(field=field), TemporaryDirectory() as temporary:
                service = CoreService.open(Path(temporary))
                change = Change(ChangeId("change-main"), FeatureId("feature-main"), "Main")
                state = service.record_feature(
                    service.recover(), Feature(change.feature_id, "Main", None)
                )
                state = service.record_change(state, change)
                facts = SafetyFacts()
                object.__setattr__(facts, field, 123)

                with self.assertRaises(ServiceError):
                    service.record_safety_facts(state, True, facts)

                self.assertEqual(service.recover(), state)


if __name__ == "__main__":
    unittest.main()
