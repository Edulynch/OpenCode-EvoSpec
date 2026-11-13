from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from evospec.domain import (
    AssessmentConfidence,
    AssessmentDepth,
    AssessmentDepthValue,
    AssessmentEvidence,
    AssessmentOperation,
    CapabilityState,
    Change,
    ChangeId,
    Drift,
    ExperimentOutcome,
    Feature,
    FeatureId,
    IntentAssessment,
    LivingSpecId,
    Observation,
    ObservationId,
    PendingDiscoveryRecovery,
    RepositoryPath,
    ReviewResult,
    State,
    VerificationEvidence,
    WorkRelation,
    WorkRelationKind,
)
from evospec.lifecycle import (
    Classification,
    LifecycleState,
    Phase,
    ReviewVerdict,
    is_valid_pair,
)
from evospec.persistence import (
    SCHEMA,
    DecodeError,
    RecoveryError,
    StateStore,
    _lock_release_from_json,
    decode_json,
    load_integration_providers,
    save_integration_providers,
)
from evospec.services import CoreService, ServiceError
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import (
    advance_context,
    convergence_assessment,
    implementation_context,
    proposed_context,
)


def _legacy_before_e1(value: dict[str, Any]) -> dict[str, Any]:
    """Remove only schema-13 fields from a fixture representing older authority."""
    value.pop("agreement_refs", None)

    def remove_context_fields(item: object) -> None:
        if isinstance(item, dict):
            item.pop("feature_agreement_ref", None)
            item.pop("slice_agreement_ref", None)
            for nested in item.values():
                remove_context_fields(nested)
        elif isinstance(item, list):
            for nested in item:
                remove_context_fields(nested)

    remove_context_fields(value)
    return value


class PersistenceTests(unittest.TestCase):
    def test_schema_ten_migrates_without_irreversible_authorization_history(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Schema migration feature")
            state_path = root / ".evospec" / "state.json"
            legacy = _legacy_before_e1(json.loads(state_path.read_text(encoding="utf-8")))
            legacy["schema"] = 10
            legacy.pop("irreversible_authorizations", None)
            legacy.pop("pending_autonomy_expansion", None)
            legacy.pop("autonomy_expansion_history", None)
            state_path.write_text(json.dumps(legacy), encoding="utf-8")

            recovered = CoreService.open(root).recover()
            self.assertEqual(recovered.irreversible_authorizations, ())
            repersisted = CoreService.open(root).record_progress(
                recovered, "migration", "schema 10", (), None, (), "resume"
            )
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], SCHEMA)
            self.assertEqual(CoreService.open(root).recover(), repersisted)

    def test_schema_eleven_migrates_without_autonomy_expansion_fields(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            state = service.record_feature(
                service.recover(), Feature(FeatureId("feature-main"), "Main", None)
            )
            state_path = root / ".evospec" / "state.json"
            legacy = _legacy_before_e1(json.loads(state_path.read_text(encoding="utf-8")))
            legacy["schema"] = 11
            legacy.pop("pending_autonomy_expansion")
            legacy.pop("autonomy_expansion_history")
            state_path.write_text(json.dumps(legacy), encoding="utf-8")

            recovered = CoreService.open(root).recover()
            self.assertIsNone(recovered.pending_autonomy_expansion)
            self.assertEqual(recovered.autonomy_expansion_history, ())
            repersisted = CoreService.open(root).record_feature(
                recovered, Feature(FeatureId("feature-next"), "Next", None)
            )
            self.assertEqual(repersisted.revision, state.revision + 1)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], SCHEMA)

    def test_schema_nine_context_migrates_to_core_default_envelope_and_schema_eleven(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Legacy export")
            state_path = root / ".evospec" / "state.json"
            legacy = _legacy_before_e1(json.loads(state_path.read_text(encoding="utf-8")))
            legacy["schema"] = 9
            legacy.pop("irreversible_authorizations", None)
            legacy.pop("pending_autonomy_expansion", None)
            legacy.pop("autonomy_expansion_history", None)
            del legacy["active_context"]["autonomy_envelope"]
            del legacy["active_context"]["autonomy_metrics"]
            state_path.write_text(json.dumps(legacy), encoding="utf-8")

            recovered = CoreService.open(root).recover()
            if recovered.active_context is None:
                self.fail("expected recovered work")
            saved = CoreService.open(root).record_progress(
                recovered, None, None, (), None, (), None,
            )

            self.assertEqual(recovered.active_context.autonomy_envelope.goal, "legacy work")
            self.assertEqual(recovered.active_context.autonomy_metrics.automatic_operations, 0)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], SCHEMA)
            self.assertEqual(CoreService.open(root).recover(), saved)
    def test_integration_registry_is_separate_from_v0_2_lifecycle_state(self) -> None:
        # Given: an existing v0.2 lifecycle state
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            service.record_feature(
                service.recover(), Feature(FeatureId("feature-main"), "Main", None)
            )
            state_path = root / ".evospec" / "state.json"
            before = state_path.read_bytes()

            # When: tool-owned provider registration is saved and recovered
            save_integration_providers(root, {"opencode"})
            providers = load_integration_providers(root)

            # Then: lifecycle authority retains its existing schema and bytes
            self.assertEqual(providers, ("opencode",))
            self.assertEqual(before, state_path.read_bytes())
            self.assertEqual(json.loads(before)["schema"], SCHEMA)

    def test_json_decoder_value_and_recursion_errors_are_typed_recovery_failures(self) -> None:
        cases = (
            ("integer limit", '{"integer":' + "1" * 5_000 + "}", None),
            ("nesting limit", "{}", RecursionError("nesting limit")),
        )
        for name, value, decoder_error in cases:
            with self.subTest(name=name), TemporaryDirectory() as temporary:
                # Given: persisted authority whose JSON decoder raises a parser limit error
                root = Path(temporary)
                state_path = root / ".evospec" / "state.json"
                state_path.parent.mkdir()
                state_path.write_bytes(value.encode("utf-8"))
                before = state_path.read_bytes()

                # When: direct decoding and authoritative recovery encounter that error
                if decoder_error is None:
                    with self.assertRaises(DecodeError):
                        decode_json(value)
                    with self.assertRaises(RecoveryError):
                        StateStore(root).load_state()
                else:
                    with patch("evospec.persistence.json.loads", side_effect=decoder_error):
                        with self.assertRaises(DecodeError):
                            decode_json(value)
                        with self.assertRaises(RecoveryError):
                            StateStore(root).load_state()

                # Then: the parser error is typed and persisted authority bytes are unchanged
                self.assertEqual(state_path.read_bytes(), before)

    def test_core_refuses_unresolved_drift_at_convergence_and_terminal_boundaries(self) -> None:
        for product_truth_changed in (True, False):
            with self.subTest(product_truth_changed=product_truth_changed), TemporaryDirectory() as temporary:
                root = Path(temporary)
                service = CoreService.open(root)
                feature = Feature(FeatureId("feature-cart"), "Cart", LivingSpecId("spec-cart"))
                change = Change(ChangeId("change-cart"), feature.id, "Cart change")
                state = service.record_feature(service.recover(), feature)
                state = service.record_change(state, change)
                baseline = replace(
                    implementation_context(feature.id, change.id),
                    status=LifecycleState.VERIFYING,
                    phase=Phase.CONVERGE,
                    review=ReviewResult(ReviewVerdict.PASS, (), True),
                    verification=VerificationEvidence(("baseline tests",), True),
                    convergence_complete=True,
                )
                state = advance_context(service, state, baseline)
                state = service.publish_accepted_convergence(
                    state,
                    feature.id,
                    change.id,
                    LivingSpecId("spec-cart"),
                    "1",
                    "baseline truth\n",
                    convergence_assessment(change.id, True),
                )
                change = Change(ChangeId("change-cart-2"), feature.id, "Cart follow-up")
                state = service.record_change(state, change)
                converged = replace(
                    implementation_context(feature.id, change.id),
                    status=LifecycleState.VERIFYING,
                    phase=Phase.CONVERGE,
                    review=ReviewResult(ReviewVerdict.PASS, (), True),
                    verification=VerificationEvidence(("tests",), True),
                )
                state = advance_context(service, state, converged)
                state = service.confirm_convergence(state)
                state = service.record_drift(
                    state,
                    Drift(
                        ObservationId("drift-cart"),
                        LivingSpecId("spec-cart"),
                        "runtime differs",
                        change.id,
                    ),
                )
                before = service.recover()

                if product_truth_changed:
                    with self.assertRaises(ServiceError):
                        service.publish_accepted_convergence(
                            state,
                            feature.id,
                            change.id,
                            LivingSpecId("spec-cart"),
                            "2",
                            "accepted truth\n",
                            convergence_assessment(change.id, True),
                        )
                else:
                    with self.assertRaises(ServiceError):
                        service.accept_convergence_without_publication(
                            state, convergence_assessment(change.id, False)
                        )

                self.assertEqual(service.recover(), before)

    def test_core_refuses_missing_or_rejected_experiment_at_convergence_and_terminal_boundaries(self) -> None:
        for experiment_outcome in (None, ExperimentOutcome.REJECTED):
            for product_truth_changed in (True, False):
                with self.subTest(
                    experiment_outcome=experiment_outcome,
                    product_truth_changed=product_truth_changed,
                ), TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    service = CoreService.open(root)
                    feature = Feature(FeatureId("feature-cart"), "Cart", LivingSpecId("spec-cart"))
                    change = Change(
                        ChangeId("change-cart"),
                        feature.id,
                        "Cart experiment",
                        Classification.EXPERIMENT,
                    )
                    state = service.record_feature(service.recover(), feature)
                    state = service.record_change(state, change)
                    converged = replace(
                        implementation_context(feature.id, change.id),
                        status=LifecycleState.VERIFYING,
                        phase=Phase.CONVERGE,
                        review=ReviewResult(ReviewVerdict.PASS, (), True),
                        verification=VerificationEvidence(("tests",), True),
                    )
                    if experiment_outcome is ExperimentOutcome.REJECTED:
                        state = service.record_observation(
                            state,
                            Observation(
                                ObservationId("experiment-cart-accepted"),
                                change.id,
                                "experiment",
                                "Accepted trial",
                                outcome=ExperimentOutcome.ACCEPTED,
                            ),
                        )
                    state = advance_context(service, state, converged)
                    if experiment_outcome is None:
                        before = service.recover()
                        with self.assertRaises(ServiceError):
                            service.confirm_convergence(state)
                        self.assertEqual(service.recover(), before)
                        continue
                    state = service.confirm_convergence(state)
                    state = service.record_observation(
                        state,
                        Observation(
                            ObservationId("experiment-cart"),
                            change.id,
                            "experiment",
                            "Rejected trial",
                            outcome=experiment_outcome,
                        ),
                    )
                    before = service.recover()

                    if product_truth_changed:
                        with self.assertRaises(ServiceError):
                            service.publish_accepted_convergence(
                                state,
                                feature.id,
                                change.id,
                                LivingSpecId("spec-cart"),
                                "1",
                                "accepted truth\n",
                                convergence_assessment(change.id, True),
                            )
                    else:
                        with self.assertRaises(ServiceError):
                            service.accept_convergence_without_publication(
                                state, convergence_assessment(change.id, False)
                            )

                    self.assertEqual(service.recover(), before)

    def test_schema_five_and_six_unassessed_accepted_archives_recover_and_repersist(self) -> None:
        for schema in (5, 6):
            with self.subTest(schema=schema), TemporaryDirectory() as temporary:
                root = Path(temporary)
                workflow = WorkflowService.open(root)
                workflow.init()
                workflow.new("Synchronize documentation")
                workflow.record_progress("implement", "docs", (), "sync", (), "resume")
                workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
                workflow.record_verification(VerificationEvidence(("docs checked",), True))
                workflow.converge(convergence_assessment(workflow.status().change_id, False))
                workflow.new("Primary capability")
                workflow.record_progress("implement", "primary", (), "work", (), "resume")
                side = workflow.change("unrelated: side documentation")
                deferred = workflow.change("unrelated: later documentation")
                workflow.record_progress("implement", "side", (), "sync", (), "resume")
                workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
                workflow.record_verification(VerificationEvidence(("side docs checked",), True))
                completed_side = workflow.converge(
                    convergence_assessment(workflow.status().change_id, False)
                )
                archived = CoreService.open(root).recover()
                self.assertEqual((side.code, deferred.code, completed_side.code), ("SIDE_ACTIVE", "DEFERRED", "DONE"))
                self.assertEqual(len(archived.completed_side_contexts), 1)
                state_path = root / ".evospec" / "state.json"
                legacy = _legacy_before_e1(json.loads(state_path.read_text(encoding="utf-8")))
                legacy["schema"] = schema
                legacy.pop("irreversible_authorizations", None)
                legacy.pop("pending_autonomy_expansion", None)
                legacy.pop("autonomy_expansion_history", None)
                legacy.pop("parked_work", None)
                legacy.pop("active_work_id", None)
                legacy.pop("preserved_primary_work_id", None)
                legacy.pop("work_items", None)
                legacy.pop("work_relations", None)
                legacy.pop("pending_capability_selection", None)
                legacy.pop("capability_selection_history", None)
                for archive in legacy["terminal_archives"] + legacy["completed_side_contexts"]:
                    del archive["convergence_assessment"]
                    del archive["legacy_unassessed_acceptance"]
                if schema == 5:
                    for field in (
                        "locks",
                        "lock_revisions",
                        "lock_releases",
                        "lock_authorizations",
                        "change_lock_assessments",
                    ):
                        del legacy[field]
                state_path.write_text(json.dumps(legacy), encoding="utf-8")

                recovered = CoreService.open(root).recover()
                archives = recovered.terminal_archives

                self.assertEqual(len(archives), 2)
                self.assertIsNone(archives[0].convergence_assessment)
                self.assertIsNone(archives[1].convergence_assessment)
                self.assertEqual(recovered.completed_side_contexts[-1], archives[-1])
                active = recovered.active_context
                if active is None:
                    self.fail("expected restored primary work for forward persistence")
                repersisted = CoreService.open(root).record_progress(
                    recovered,
                    active.current_action,
                    active.focus,
                    active.completed_work,
                    active.current_work,
                    active.pending_work,
                    active.resume_point,
                )
                forward = json.loads(state_path.read_text(encoding="utf-8"))

                self.assertEqual(forward["schema"], SCHEMA)
                self.assertTrue(forward["terminal_archives"][0]["legacy_unassessed_acceptance"])
                self.assertTrue(forward["completed_side_contexts"][-1]["legacy_unassessed_acceptance"])
                self.assertEqual(CoreService.open(root).recover(), repersisted)

    def test_schema_seven_recovers_without_parked_work_and_repersistence_writes_schema_eleven(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            state = service.record_feature(
                service.recover(), Feature(FeatureId("feature-main"), "Main", None)
            )
            state_path = root / ".evospec" / "state.json"
            legacy = _legacy_before_e1(json.loads(state_path.read_text(encoding="utf-8")))
            legacy["schema"] = 7
            legacy.pop("irreversible_authorizations", None)
            legacy.pop("pending_autonomy_expansion", None)
            legacy.pop("autonomy_expansion_history", None)
            legacy.pop("active_work_id", None)
            legacy.pop("preserved_primary_work_id", None)
            legacy.pop("work_items", None)
            legacy.pop("work_relations", None)
            legacy.pop("pending_capability_selection", None)
            legacy.pop("capability_selection_history", None)
            state_path.write_text(json.dumps(legacy), encoding="utf-8")

            recovered = CoreService.open(root).recover()
            repersisted = CoreService.open(root).record_feature(
                recovered, Feature(FeatureId("feature-next"), "Next", None)
            )

            self.assertIsNone(recovered.parked_work)
            self.assertEqual(repersisted.revision, state.revision + 1)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], SCHEMA)

    def test_schema_nine_work_queue_enums_recover_as_typed_failures(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Queued predecessor")
            workflow.park("deferred")
            predecessor = CoreService.open(root).recover().work_items[0]
            workflow.new("Active successor")
            state = CoreService.open(root).recover()
            active_work_id = state.active_work_id
            if active_work_id is None:
                self.fail("expected active successor work identity")
            CoreService.open(root).add_work_relation(
                state, WorkRelation(predecessor.id, active_work_id, WorkRelationKind.HARD)
            )
            state_path = root / ".evospec" / "state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))

            for location in ("operational_state", "relation_kind"):
                with self.subTest(location=location):
                    altered = json.loads(json.dumps(payload))
                    if location == "operational_state":
                        altered["work_items"][0]["operational_state"] = "INVALID"
                    else:
                        altered["work_relations"][0]["kind"] = "INVALID"
                    state_path.write_text(json.dumps(altered), encoding="utf-8")

                    with self.assertRaises(RecoveryError):
                        CoreService.open(root).recover()

    def test_recovery_rejects_forged_pending_capability_selection(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            core = CoreService.open(root)
            state = core.record_feature(core.recover(), Feature(FeatureId("feature-export-a"), "Export delivery", LivingSpecId("spec-export-a")))
            core.record_feature(state, Feature(FeatureId("feature-export-b"), "Export delivery", LivingSpecId("spec-export-b")))
            pending = workflow.change("Change export delivery behavior")
            self.assertEqual(pending.code, "AMBIGUOUS_CAPABILITY")
            state_path = root / ".evospec" / "state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["pending_capability_selection"]["provenance"] = ["feature:forged"]
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()

    def test_recovery_rejects_malformed_pending_discovery_recovery(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "evidence-one.md").write_text("External Blocker Reconciliation", encoding="utf-8")
            (root / "evidence-two.md").write_text("External Blocker Reconciliation", encoding="utf-8")
            overflow = root / "overflow"
            overflow.mkdir()
            for index in range(129):
                (overflow / f"z-{index:03}.txt").write_text("noise", encoding="utf-8")
            workflow = WorkflowService.open(root)
            workflow.init()
            self.assertEqual(workflow.new("External Blocker Reconciliation").code, "DISCOVERY_RECOVERY_REQUIRED")
            state_path = root / ".evospec" / "state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["pending_discovery_recovery"]["evidence"][0]["fingerprint"] = ""
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()

    def test_recovery_accepts_pre_evidence_pending_discovery_recovery(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            recovery = PendingDiscoveryRecovery(
                "discovery-pre-evidence",
                AssessmentOperation.NEW,
                "External Blocker Reconciliation",
                ("external blocker reconciliation",),
                (),
            )
            saved = service.record_discovery_recovery(service.recover(), recovery)

            restored = service.recover().pending_discovery_recovery

            self.assertIsNotNone(restored)
            if restored is None:
                self.fail("expected pre-evidence discovery recovery")
            self.assertEqual(saved.pending_discovery_recovery, recovery)
            self.assertEqual(restored, recovery)

    def test_recovery_rejects_candidate_less_pending_discovery_recovery(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            service.record_discovery_recovery(
                service.recover(),
                PendingDiscoveryRecovery(
                    "discovery-pre-evidence",
                    AssessmentOperation.NEW,
                    "External Blocker Reconciliation",
                    ("external blocker reconciliation",),
                    (),
                ),
            )
            state_path = root / ".evospec" / "state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["pending_discovery_recovery"]["candidates"] = []
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()

    def test_recovery_rejects_orphan_change_lock_assessment_history(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            service.record_feature(
                service.recover(), Feature(FeatureId("feature"), "Feature", None)
            )
            state_path = root / ".evospec" / "state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["change_lock_assessments"] = [
                {"change_id": "change-ghost", "assessments": [], "history": []}
            ]
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()

    def test_lock_release_decoder_rejects_boolean_and_non_positive_revisions(self) -> None:
        for revision in (True, 0, -1):
            with self.subTest(revision=revision), self.assertRaises(DecodeError):
                _lock_release_from_json(
                    {"lock_id": "lock-contract", "revision": revision, "decision_id": "decision-contract"}
                )

    def test_project_established_assessment_reloads_without_publishing_product_truth(self) -> None:
        # Given: a persisted project-established CHANGE assessment
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

            # When: the project-established capability is adopted and recovered from disk
            intake = workflow.change("Alter delivery scheduling", assessment=supplied)
            recovered = CoreService.open(root).recover()

            # Then: its assessment and un-published intake state survive codec reload
            self.assertEqual(intake.code, "PROPOSED")
            self.assertEqual(recovered.changes[0].assessment, supplied)
            self.assertEqual(recovered.publications, ())
            self.assertEqual(recovered.publication_history, ())

    def test_change_recovery_rejects_malformed_assessment_enum_and_unsafe_evidence_reference(self) -> None:
        # Given: a persisted change with a structured assessment
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new(
                "Add export capability",
                assessment=IntentAssessment(
                    AssessmentOperation.NEW,
                    Classification.FEATURE,
                    AssessmentConfidence.HIGH,
                    CapabilityState.PLANNED,
                    "export capability",
                    (),
                    None,
                    (AssessmentEvidence("caller", RepositoryPath("assessment.md")),),
                    (),
                    AssessmentDepth(
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                    ),
                    "caller supplied assessment",
                ),
            )
            record = root / ".evospec" / "records" / "changes"
            payload = json.loads(next(record.glob("*.json")).read_text(encoding="utf-8"))
            recovered = CoreService.open(root).recover()

            # Then: accepted caller reasoning remains change-scoped provenance after reload
            self.assertIsNotNone(recovered.changes[0].assessment)
            self.assertEqual(recovered.publications, ())

            # When/Then: malformed assessment values cannot recover as authority
            for field, value in (
                ("confidence", "CERTAIN"),
                ("capability_state", "IMAGINARY"),
                ("reference", "../escape"),
            ):
                altered = json.loads(json.dumps(payload))
                if field == "reference":
                    altered["assessment"]["evidence"][0][field] = value
                else:
                    altered["assessment"][field] = value
                next(record.glob("*.json")).write_text(json.dumps(altered), encoding="utf-8")
                with self.assertRaises(RecoveryError):
                    CoreService.open(root).recover()
    def test_only_reachable_terminal_pairs_are_recoverable(self) -> None:
        valid = {
            (LifecycleState.REJECTED, Phase.CLARIFY),
            (LifecycleState.REJECTED, Phase.VERIFY),
            (LifecycleState.REJECTED, Phase.CONVERGE),
            (LifecycleState.ABANDONED, Phase.READY),
            (LifecycleState.ABANDONED, Phase.IMPLEMENT),
            (LifecycleState.ABANDONED, Phase.REVIEW),
            (LifecycleState.ACCEPTED, Phase.DONE),
        }

        for status in (
            LifecycleState.REJECTED,
            LifecycleState.ABANDONED,
            LifecycleState.ACCEPTED,
        ):
            for phase in Phase:
                with self.subTest(status=status, phase=phase):
                    self.assertEqual(is_valid_pair(status, phase), (status, phase) in valid)

    def test_json_decoder_rejects_duplicate_top_level_keys(self) -> None:
        with self.assertRaises(DecodeError):
            decode_json('{"schema":1,"schema":1}')

    def test_json_decoder_rejects_duplicate_nested_keys(self) -> None:
        with self.assertRaises(DecodeError):
            decode_json('{"context":{"phase":"research","phase":"research"}}')

    def test_state_is_lazy_deterministic_and_recoverable(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            self.assertFalse((root / ".evospec").exists())

            saved = service.record_feature(
                service.recover(), Feature(FeatureId("feature-main"), "Main", None)
            )

            state_path = root / ".evospec" / "state.json"
            payload = state_path.read_bytes()
            self.assertTrue(payload.endswith(b"\n"))
            self.assertEqual(CoreService.open(root).recover(), saved)
            decoded = json.loads(payload)
            self.assertEqual(decoded["schema"], SCHEMA)
            self.assertEqual(decoded["revision"], 1)
            self.assertEqual(decoded["features"][0]["id"], "feature-main")

    def test_invalid_or_unknown_state_is_a_typed_recovery_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            state_path = Path(temporary) / ".evospec" / "state.json"
            state_path.parent.mkdir()
            state_path.write_text('{"schema":2}\n', encoding="utf-8")

            with self.assertRaises(RecoveryError):
                StateStore(Path(temporary)).load_state()

    def test_previous_state_schema_is_rejected_without_migration(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            service.record_feature(
                service.recover(), Feature(FeatureId("feature-main"), "Main", None)
            )
            state_path = root / ".evospec" / "state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["schema"] = 1
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(RecoveryError):
                StateStore(root).load_state()

    def test_recovery_rejects_missing_and_wrong_nested_context_fields(self) -> None:
        with TemporaryDirectory() as temporary:
            state_path = Path(temporary) / ".evospec" / "state.json"
            state_path.parent.mkdir()
            state_path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "revision": True,
                        "active_context": None,
                        "preserved_primary_context": None,
                        "publications": [],
                        "completed_side_contexts": [],
                        "features": [],
                        "change_refs": [],
                        "decision_refs": [],
                        "observation_refs": [],
                        "drift_refs": [],
                        "terminal_archives": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(RecoveryError):
                StateStore(Path(temporary)).load_state()

    def test_store_has_no_public_arbitrary_publication_surface(self) -> None:
        with TemporaryDirectory() as temporary:
            store = StateStore(Path(temporary))
            self.assertFalse(hasattr(store, "save_state"))
            self.assertFalse(hasattr(store, "write_immutable"))
            self.assertFalse(hasattr(store, "publish_living_spec"))

    def test_referenced_record_requires_exact_schema(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            feature = Feature(FeatureId("feature"), "Feature", None)
            state = service.record_feature(service.recover(), feature)
            change = Change(ChangeId("change"), feature.id, "Change")
            service.record_change(state, change)
            record = root / ".evospec" / "records" / "changes" / "change.json"
            payload = json.loads(record.read_text(encoding="utf-8"))
            payload["unexpected"] = True
            record.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()

    def test_referenced_record_rejects_duplicate_keys(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            feature = Feature(FeatureId("feature"), "Feature", None)
            state = service.record_feature(service.recover(), feature)
            change = Change(ChangeId("change"), feature.id, "Change")
            service.record_change(state, change)
            record = root / ".evospec" / "records" / "changes" / "change.json"
            payload = record.read_text(encoding="utf-8")
            record.write_text(payload.replace('"id":"change"', '"id":"change","id":"change"'), encoding="utf-8")

            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()

    def test_active_and_preserved_publications_require_exact_context_identity(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            feature = Feature(FeatureId("feature-main"), "Main", LivingSpecId("spec-main"))
            first_change = Change(ChangeId("change-one"), feature.id, "First")
            state = service.record_feature(service.recover(), feature)
            state = service.record_change(state, first_change)
            converged = replace(
                implementation_context(feature.id, first_change.id),
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
                first_change.id,
                LivingSpecId("spec-main"),
                "1",
                "truth\n",
                convergence_assessment(first_change.id, True),
            )
            second_change = Change(ChangeId("change-two"), feature.id, "Second")
            state = service.record_change(state, second_change)
            side_feature = Feature(
                FeatureId("feature-side"), "Side", None, feature.id
            )
            side_change = Change(ChangeId("change-side"), side_feature.id, "Side")
            state = service.record_feature(state, side_feature)
            service.activate_side(state, side_change)
            state_path = root / ".evospec" / "state.json"
            original = json.loads(state_path.read_text(encoding="utf-8"))
            publication = original["publication_history"][0]

            for context_name in ("active_context", "preserved_primary_context"):
                with self.subTest(context=context_name):
                    payload = json.loads(json.dumps(original))
                    payload[context_name]["publication"] = publication
                    state_path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(RecoveryError):
                        CoreService.open(root).recover()

    def test_context_publication_requires_existing_immutable_file(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            feature = Feature(FeatureId("feature-main"), "Main", LivingSpecId("spec-main"))
            change = Change(ChangeId("change-main"), feature.id, "Change")
            state = service.record_feature(service.recover(), feature)
            state = service.record_change(state, change)
            converged = replace(
                implementation_context(feature.id, change.id),
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, converged)
            service.publish_accepted_convergence(
                state,
                feature.id,
                change.id,
                LivingSpecId("spec-main"),
                "1",
                "truth\n",
                convergence_assessment(change.id, True),
            )
            (root / ".evospec" / "living-specs" / "spec-main" / "1.md").unlink()

            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()

    def test_record_schema_rejects_boolean_and_float_integers(self) -> None:
        for invalid_schema in (True, 1.0):
            with self.subTest(schema=invalid_schema), TemporaryDirectory() as temporary:
                root = Path(temporary)
                service = CoreService.open(root)
                feature = Feature(FeatureId("feature"), "Feature", None)
                state = service.record_feature(service.recover(), feature)
                change = Change(ChangeId("change"), feature.id, "Change")
                service.record_change(state, change)
                record = root / ".evospec" / "records" / "changes" / "change.json"
                payload = json.loads(record.read_text(encoding="utf-8"))
                payload["schema"] = invalid_schema
                record.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaises(RecoveryError):
                    CoreService.open(root).recover()

    def test_publication_arrays_reject_null_entries(self) -> None:
        for field in ("publications", "publication_history"):
            with self.subTest(field=field), TemporaryDirectory() as temporary:
                root = Path(temporary)
                service = CoreService.open(root)
                service.record_feature(
                    service.recover(), Feature(FeatureId("feature"), "Feature", None)
                )
                state_path = root / ".evospec" / "state.json"
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                payload[field] = [None]
                state_path.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaises(RecoveryError):
                    CoreService.open(root).recover()
