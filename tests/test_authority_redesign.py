from __future__ import annotations

import json
import unittest
from dataclasses import replace
from inspect import getsource
from pathlib import Path

import evospec.persistence as persistence
import evospec.services as services
from evospec import ports
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
    StateValidationError,
    TerminalArchive,
    VerificationEvidence,
    WorkingContext,
    validate_state,
)
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.persistence import RecoveryError, StateStore
from evospec.services import CoreService, ServiceError
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import (
    advance_context,
    convergence_assessment,
    proposed_context,
)


def context(
    feature_id: FeatureId,
    change_id: ChangeId,
    *,
    side_of: FeatureId | None = None,
) -> WorkingContext:
    return WorkingContext(
        feature_id,
        change_id,
        LifecycleState.IN_PROGRESS,
        Phase.IMPLEMENT,
        "implement",
        "core",
        (),
        "authority",
        ("recover",),
        (),
        (),
        None,
        (),
        VerificationEvidence((), False),
        False,
        None,
        0,
        "continue",
        (change_id,),
        (),
        side_of=side_of,
    )


class AuthorityRedesignTests(unittest.TestCase):
    def test_candidate_and_encoding_authority_is_not_factory_injectable(self) -> None:
        forbidden_persistence = {
            "_open_core_service",
        }

        self.assertNotIn("_BOUND_SERVICE_FACTORY", vars(services))
        self.assertTrue(forbidden_persistence.isdisjoint(vars(persistence)))
        self.assertFalse(hasattr(CoreService, "record_context"))
        self.assertFalse(
            hasattr(CoreService, "invalidate_checkpoints_after_implementation")
        )

    def test_core_service_methods_close_over_no_generic_persistence_capability(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            method_names = (
                "recover",
                "record_feature",
                "record_change",
                "record_decision",
                "supersede_decision",
                "record_observation",
                "record_drift",
                "advance_lifecycle",
                "record_progress",
                "record_safety_facts",
                "record_review",
                "record_verification",
                "confirm_convergence",
                "record_repair",
                "activate_side",
                "archive_terminal",
                "restore_primary",
                "publish_accepted_convergence",
                "publish_accepted_side_convergence",
                "inspect_review",
                "record_implementation_alteration",
                "record_irreversible_authorization",
                "execute_irreversible_authorization",
            )

            self.assertIs(type(service), CoreService)
            self.assertEqual(CoreService.__module__, "evospec.services")
            self.assertEqual(CoreService.__slots__, ("__persistence", "__repository_root"))
            self.assertIsNone(CoreService.__dict__["open"].__func__.__closure__)
            self.assertFalse(hasattr(service, "__dict__"))
            self.assertTrue(
                {
                    "_make_bound_worker",
                    "_BoundCoreService",
                    "_BOUND_WORKERS",
                    "create_bound_service",
                    "CoreService",
                }.isdisjoint(vars(persistence))
            )
            for name in method_names:
                with self.subTest(name=name):
                    implementation = CoreService.__dict__[name]
                    self.assertIs(getattr(service, name).__func__, implementation)
                    self.assertIsNone(implementation.__closure__)

    def test_open_accepts_file_operations_subclass_and_rejects_duck_writer(self) -> None:
        class SupportedOperations(persistence.FileOperations):
            pass

        class DuckOperations:
            def replace(self, source: Path, target: Path) -> None:
                source.replace(target)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root, SupportedOperations())

            self.assertIs(type(service), CoreService)
            self.assertEqual(service.recover(), State(0, None))
            with self.assertRaises(TypeError):
                getattr(CoreService, "open")(root, DuckOperations())

    def bootstrap(
        self,
        root: Path,
        feature_id: FeatureId,
        change_id: ChangeId,
        *,
        title: str = "Core",
        living_spec_id: LivingSpecId | None = None,
    ) -> tuple[CoreService, State]:
        service = CoreService.open(root)
        state = service.record_feature(service.recover(), Feature(feature_id, title, living_spec_id))
        change = Change(change_id, feature_id, "Implement authority")
        state = service.record_change(state, change)
        return service, state

    def test_state_store_exposes_no_arbitrary_state_writer(self) -> None:
        with TemporaryDirectory() as temporary:
            store = StateStore(Path(temporary))

            for name in (
                "save_state",
                "write_immutable",
                "_commit_state",
                "_write_change",
                "_write_decision",
                "_write_observation",
                "_write_drift",
                "_write_publication",
                "publish_state",
            ):
                self.assertFalse(hasattr(store, name))
                self.assertFalse(hasattr(persistence._StateStore, name))
            self.assertFalse(hasattr(ports, "_CommitPermit"))
            self.assertFalse(hasattr(ports, "_SERVICE_PERMIT"))
            self.assertFalse(hasattr(ports, "AuthorityPersistence"))

    def test_core_service_source_contains_no_artifact_io(self) -> None:
        source = getsource(CoreService)

        for forbidden in (
            "_artifact_path",
            "_atomic_replace",
            "_state_bytes",
            ".exists(",
            ".read_bytes(",
            "_file_operations",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_core_service_requires_supported_factory(self) -> None:
        with TemporaryDirectory() as temporary:
            with self.assertRaises(TypeError):
                CoreService(StateStore(Path(temporary)))

    def test_core_service_exposes_no_generic_mutation_callback(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))

            for name in (
                "_commit",
                "_store_record",
                "_store_change",
                "_store_decision",
                "_store_observation",
                "_store_drift",
                "_store_publication",
                "_operations",
            ):
                self.assertFalse(hasattr(service, name))

    def test_stale_or_forged_state_cannot_authorize_service_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            service, state = self.bootstrap(Path(temporary), FeatureId("feature-main"), ChangeId("change-main"))
            recorded = advance_context(
                service,
                state,
                context(FeatureId("feature-main"), ChangeId("change-main")),
            )
            forged = replace(recorded, active_context=None)

            with self.assertRaises(ServiceError):
                service.record_decision(
                    forged,
                    Decision(
                        DecisionId("decision-main"),
                        FeatureId("feature-main"),
                        "clarification",
                        "Keep authority singular",
                        ChangeId("change-main"),
                    ),
                )

            self.assertEqual(service.recover(), recorded)

    def test_orphan_material_record_is_ignored_and_missing_reference_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service, state = self.bootstrap(root, FeatureId("feature-main"), ChangeId("change-main"))
            orphan = root / ".evospec" / "records" / "changes" / "orphan.json"
            orphan.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "id": "orphan",
                        "feature_id": "feature-main",
                        "intent": "ignored",
                        "classification": None,
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(service.recover(), state)

            state_file = root / ".evospec" / "state.json"
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            payload["change_refs"][0]["path"] = "records/changes/missing.json"
            state_file.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RecoveryError):
                service.recover()

    def test_exact_recovery_rejects_wrong_nested_types_and_relationships(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service, state = self.bootstrap(root, FeatureId("feature-main"), ChangeId("change-main"))
            state = advance_context(
                service,
                state,
                context(FeatureId("feature-main"), ChangeId("change-main")),
            )
            state_file = root / ".evospec" / "state.json"
            original = json.loads(state_file.read_text(encoding="utf-8"))

            mutations = (
                lambda payload: payload["active_context"].update({"completed_work": [1]}),
                lambda payload: payload["active_context"].update({"unexpected": True}),
                lambda payload: payload["active_context"].update({"feature_id": "unknown-feature"}),
                lambda payload: payload.update({"revision": True}),
                lambda payload: payload["active_context"].update({"status": "ACCEPTED", "phase": "DONE"}),
            )
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    payload = json.loads(json.dumps(original))
                    mutate(payload)
                    state_file.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(RecoveryError):
                        service.recover()

    def test_primary_terminal_is_persisted_as_archive_and_not_active(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service, state = self.bootstrap(root, FeatureId("feature-main"), ChangeId("change-main"))
            active = context(FeatureId("feature-main"), ChangeId("change-main"))
            state = advance_context(service, state, active)

            archived = service.archive_terminal(state, replace(active, status=LifecycleState.ABANDONED))
            recovered = CoreService.open(root).recover()

            self.assertIsNone(archived.active_context)
            self.assertEqual(recovered, archived)
            self.assertEqual(recovered.terminal_archives[0].context.status, LifecycleState.ABANDONED)

    def test_recovery_rejects_context_publication_without_exact_history(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service, state = self.bootstrap(
                root, FeatureId("feature-main"), ChangeId("change-main")
            )
            review = ReviewResult(ReviewVerdict.PASS, (), True)
            converged = replace(
                context(FeatureId("feature-main"), ChangeId("change-main")),
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=review,
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, converged)
            state = service.accept_convergence_without_publication(
                state, convergence_assessment(converged.change_id, False)
            )
            state_file = root / ".evospec" / "state.json"
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            payload["terminal_archives"][0]["context"]["publication"] = {
                "living_spec_id": "spec-fake",
                "revision": "ghost",
                "capability": "Forged",
                "path": "living-specs/spec-fake/ghost.md",
                "previous_revision": None,
                "feature_id": "feature-other",
                "change_id": "change-other",
            }
            forged_file = root / ".evospec" / "living-specs" / "spec-fake" / "ghost.md"
            forged_file.parent.mkdir(parents=True)
            forged_file.write_text("forged\n", encoding="utf-8")
            state_file.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()

    def test_side_completion_must_equal_current_side_and_restores_exact_primary(self) -> None:
        with TemporaryDirectory() as temporary:
            service, state = self.bootstrap(Path(temporary), FeatureId("feature-primary"), ChangeId("change-primary"))
            primary = context(FeatureId("feature-primary"), ChangeId("change-primary"))
            state = advance_context(service, state, primary)
            state = service.record_feature(
                state,
                Feature(FeatureId("feature-side"), "Side", None, FeatureId("feature-primary")),
            )
            side_change = Change(
                ChangeId("change-side"), FeatureId("feature-side"), "Side change"
            )
            side = context(
                FeatureId("feature-side"),
                ChangeId("change-side"),
                side_of=FeatureId("feature-primary"),
            )
            side_state = service.activate_side(state, side_change)
            side_state = advance_context(service, side_state, side)
            forged = replace(side, focus="forged", status=LifecycleState.ABANDONED)

            with self.assertRaises(ServiceError):
                service.restore_primary(side_state, forged)

            restored = service.restore_primary(
                side_state,
                replace(side, status=LifecycleState.ABANDONED),
            )
            self.assertEqual(restored.active_context, primary)
            self.assertEqual(restored.completed_side_contexts[0].parent_feature_id, primary.feature_id)

    def test_review_to_verify_needs_review_but_not_sufficient_verification(self) -> None:
        review = ReviewResult(ReviewVerdict.PASS, (), True)
        context_at_review = replace(
            context(FeatureId("feature-main"), ChangeId("change-main")),
            phase=Phase.REVIEW,
            review=review,
        )

        from evospec.lifecycle import advance

        self.assertEqual(
            advance(
                LifecycleState.IN_PROGRESS,
                Phase.REVIEW,
                LifecycleState.VERIFYING,
                Phase.VERIFY,
                False,
                review,
                VerificationEvidence((), False),
            ),
            (LifecycleState.VERIFYING, Phase.VERIFY),
        )
        self.assertEqual(context_at_review.verification.sufficient, False)

    def test_every_recorded_change_has_exactly_one_lifecycle_owner(self) -> None:
        # Given: one recorded change and valid active/archive context shapes
        change = Change(ChangeId("change-main"), FeatureId("feature-main"), "Main")
        feature = Feature(change.feature_id, "Main", None)
        active = proposed_context(change)
        terminal = replace(active, status=LifecycleState.ABANDONED)

        # When/Then: ownerless and duplicate-owned changes are rejected
        with self.assertRaises(StateValidationError):
            validate_state(State(0, None, features=(feature,), changes=(change,)))
        with self.assertRaises(StateValidationError):
            validate_state(
                State(
                    0,
                    active,
                    features=(feature,),
                    changes=(change,),
                    terminal_archives=(TerminalArchive(terminal),),
                )
            )

    def test_repair_count_rejects_boolean_at_service_boundary(self) -> None:
        with TemporaryDirectory() as temporary:
            service, state = self.bootstrap(
                Path(temporary), FeatureId("feature-main"), ChangeId("change-main")
            )
            active = state.active_context
            if active is None:
                self.fail("recorded change must have an active context")

            with self.assertRaises(ServiceError):
                service.record_repair(state, True, None, None)

            self.assertEqual(service.recover(), state)

if __name__ == "__main__":
    unittest.main()
