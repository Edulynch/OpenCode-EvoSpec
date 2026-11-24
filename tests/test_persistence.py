from __future__ import annotations

import json
import multiprocessing
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, cast
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
    CoverageInput,
    CriterionCoverageDeclaration,
    Drift,
    ExpectedRevision,
    ExperimentOutcome,
    Feature,
    FeatureId,
    IntentAssessment,
    LivingSpecId,
    Observation,
    ObservationId,
    PendingDiscoveryRecovery,
    ProvisionalSliceCriterion,
    ProvisionalSliceHead,
    ProvisionalSliceProvenance,
    ProvisionalSliceRevision,
    ProvisionalSliceSourceReference,
    RepositoryPath,
    ReviewResult,
    State,
    StateValidationError,
    DomainError,
    VerificationEvidence,
    WorkRelation,
    WorkRelationKind,
    validate_state,
)
from evospec.lifecycle import (
    Classification,
    LifecycleState,
    Phase,
    ReviewVerdict,
    is_valid_pair,
)
from evospec.guidance import allowed_actions
from evospec.persistence import (
    FEATURE_COMPLETION_SCHEMA,
    FEATURE_CYCLE_SCHEMA,
    PROVISIONAL_SLICE_SCHEMA,
    REQUEST_OBJECTIVE_SCHEMA,
    R3B_SCHEMA,
    R3_SCHEMA,
    SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
    LEGACY_WRITE_SCHEMA,
    SCHEMA,
    AtomicFailure,
    DecodeError,
    FileOperations,
    JsonValue,
    RecoveryError,
    StateStore,
    _StateStore,
    _atomic_replace,
    _artifact_path,
    _state_bytes,
    _state_publish_lock,
    _lock_release_from_json,
    _agreement_to_json,
    decode_json,
    encode_json,
    load_integration_providers,
    _provisional_slice_from_json,
    _provisional_slice_to_json,
    _recover_state_with_schema,
    _resolved_root,
    _validate_provisional_slice_authority,
    _validate_provisional_slice_record,
    save_integration_providers,
)
from evospec.services import (
    CoreService,
    SessionRequestEvidence,
    ServiceError,
    SliceCriteriaSpec,
    UserAcceptance,
    next_slice_acceptance_statement,
    slice_acceptance_statement,
)
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import (
    advance_context,
    convergence_assessment,
    implementation_context,
    proposed_context,
)


def _legacy_before_e1(value: dict[str, Any]) -> dict[str, Any]:
    """Remove schema-14 and first-Slice fields for authentic legacy fixtures."""
    value.pop("agreement_refs", None)

    def remove_context_fields(item: object) -> None:
        if isinstance(item, dict):
            item.pop("e1_schema_upgrade", None)
            item.pop("task_progress", None)
            item.pop("feature_agreement_ref", None)
            item.pop("slice_agreement_ref", None)
            for nested in item.values():
                remove_context_fields(nested)
        elif isinstance(item, list):
            for nested in item:
                remove_context_fields(nested)

    remove_context_fields(value)
    return value


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _provisional_provenance_json(provenance: ProvisionalSliceProvenance) -> dict[str, Any]:
    if provenance.kind == "AGENT_SUGGESTION":
        return {"kind": "AGENT_SUGGESTION"}
    assert provenance.source_reference is not None
    return {
        "kind": "USER_EXPRESSED",
        "source_reference": {
            "session_id": provenance.source_reference.session_id,
            "message_id": provenance.source_reference.message_id,
        },
        "expression": provenance.expression,
        "expression_sha256": provenance.expression_sha256,
    }


def _make_provisional_revision(
    state: State,
    *,
    previous: ProvisionalSliceRevision | None = None,
    repository_id: str | None = None,
    feature_id: str | None = None,
    title: str = "Provisional future Slice",
    outcome: str = "The future Slice is clearly described",
    example: str | None = None,
    inclusions: tuple[str, ...] = ("Describe bounded future work",),
    exclusions: tuple[str, ...] = (),
    considerations: tuple[str, ...] = (),
    open_questions: tuple[str, ...] = (),
    criteria: tuple[ProvisionalSliceCriterion, ...] | None = None,
    provenance: ProvisionalSliceProvenance | None = None,
) -> ProvisionalSliceRevision:
    if state.initialization is None:
        raise AssertionError("fixture state must be initialized")
    number = 1 if previous is None else previous.revision + 1
    repository = state.initialization.repository_id if repository_id is None else repository_id
    feature = str(state.features[0].id) if feature_id is None else feature_id
    selected_criteria = (
        (ProvisionalSliceCriterion("c-1", "It is bounded", "An observable result"),)
        if criteria is None else criteria
    )
    selected_provenance = (
        ProvisionalSliceProvenance("AGENT_SUGGESTION")
        if provenance is None else provenance
    )
    provenance_json = _provisional_provenance_json(selected_provenance)
    genesis = {
        "repository_id": repository,
        "feature_id": feature,
        "title": title,
        "outcome": outcome,
        "example": example,
        "inclusions": list(inclusions),
        "exclusions": list(exclusions),
        "considerations": list(considerations),
        "open_questions": list(open_questions),
        "criteria": [
            {
                "id": item.id,
                "expected_result": item.expected_result,
                "expected_evidence": item.expected_evidence,
            }
            for item in selected_criteria
        ],
        "provenance": provenance_json,
    }
    proposal_id = (
        previous.proposal_id
        if previous is not None
        else "ps-" + sha256(_canonical_json(genesis)).hexdigest()
    )
    revision_id = f"{proposal_id}-r{number}"
    value = {
        "proposal_id": proposal_id,
        "revision_id": revision_id,
        "revision": number,
        **genesis,
        "previous_revision_id": None if previous is None else previous.revision_id,
    }
    digest = sha256(_canonical_json(value)).hexdigest()
    return ProvisionalSliceRevision(
        proposal_id,
        revision_id,
        number,
        repository,
        FeatureId(feature),
        title,
        outcome,
        example,
        inclusions,
        exclusions,
        considerations,
        open_questions,
        selected_criteria,
        selected_provenance,
        None if previous is None else previous.revision_id,
        digest,
    )


def _create_provisional_fixture(
    root: Path, criteria: SliceCriteriaSpec | None = None,
) -> State:
    """Use the project’s ordinary accepted Feature fixture; D1 only reads it."""
    workflow = WorkflowService.open(root)
    initial = workflow.init()
    feature = "D1 fixture Feature"
    slice_title = "Existing fixture Slice"
    task = "Existing fixture Task"
    checkpoint = "Existing fixture checkpoint"
    statement = slice_acceptance_statement(feature, slice_title, task, checkpoint, criteria)
    acceptance = UserAcceptance(
        statement,
        f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}",
    )
    workflow.accept_first_slice(
        feature,
        slice_title,
        task,
        checkpoint,
        acceptance,
        expected_revision=ExpectedRevision(initial.revision),
        criteria=criteria,
    )
    return CoreService.open(root).recover()


def _create_two_slice_fixture(root: Path, base: State) -> State:
    """Use Core's accepted-pair route for synthetic multi-Slice authority."""
    workflow = WorkflowService.open(root)
    current = CoreService.open(root).recover()
    r3 = workflow.r3_upgrade_agreement(expected_revision=ExpectedRevision(current.revision))
    upgraded = workflow.upgrade_r3_schema(
        UserAcceptance(r3, f"tty:{sha256(r3.encode('utf-8')).hexdigest()[:12]}"),
        expected_revision=ExpectedRevision(current.revision),
    )
    known_input = root / "tests" / "test_r5a_fixture.py"
    known_input.parent.mkdir()
    known_input.write_text("# synthetic R3-B coverage input\n", encoding="utf-8")
    declarations = (
        CriterionCoverageDeclaration(
            "AC-1", "python-unittest", "tests.test_r5a_fixture",
            (CoverageInput("root-file", "tests/test_r5a_fixture.py"),),
        ),
    )
    r3b = workflow.r3b_upgrade_agreement(
        declarations, expected_revision=ExpectedRevision(upgraded.revision),
    )
    workflow.upgrade_r3b_schema(
        UserAcceptance(r3b, f"tty:{sha256(r3b.encode('utf-8')).hexdigest()[:12]}"),
        declarations, expected_revision=ExpectedRevision(upgraded.revision),
    )
    workflow.park("Fixture has another accepted Slice")
    current = CoreService.open(root).recover()
    from tests.test_e1_r5a_next_slice import spec

    criteria = spec("The second accepted Slice is independently bounded")
    feature = base.features[0]
    feature_id = str(feature.id)
    slice_title = "Second fixture Slice"
    initial_task = "Second fixture Task"
    checkpoint = "Second fixture checkpoint"
    statement = next_slice_acceptance_statement(
        feature_id, feature.title, slice_title, initial_task, checkpoint, criteria,
    )
    workflow.accept_next_slice(
        feature_id, slice_title, initial_task, checkpoint,
        UserAcceptance(statement, f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}"),
        expected_revision=ExpectedRevision(current.revision),
        criteria=criteria,
    )
    return CoreService.open(root).recover()


class _ProvisionalSliceTestHarness:
    """Test-only D1 publisher using the production persistence primitives."""

    def __init__(self, root: Path, operations: FileOperations | None = None) -> None:
        self.root = _resolved_root(root)
        self.operations = FileOperations() if operations is None else operations
        self.immutable_writer = _StateStore(self.root, self.operations)

    def publish(
        self,
        expected_state: State,
        revision: ProvisionalSliceRevision,
        expected_head: ProvisionalSliceHead | None,
    ) -> State:
        with _state_publish_lock(self.root):
            current, schema = _recover_state_with_schema(self.root)
            if expected_state != current:
                raise AtomicFailure("provisional Slice State revision is stale")
            if schema not in (
                SCHEMA, R3_SCHEMA, R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA,
                FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA,
                SEMANTIC_REQUEST_OBJECTIVE_SCHEMA, PROVISIONAL_SLICE_SCHEMA,
            ):
                raise AtomicFailure("provisional Slice requires schema 15–22 authority")
            try:
                _validate_provisional_slice_record(revision)
            except (DecodeError, DomainError) as error:
                raise AtomicFailure("provisional Slice candidate is invalid") from error
            heads = {item.proposal_id: item for item in current.provisional_slice_heads}
            current_head = heads.get(revision.proposal_id)
            if current_head != expected_head:
                raise AtomicFailure("provisional Slice head revision or digest is stale")
            current_record = next(
                (
                    item for item in current.provisional_slice_revisions
                    if item.revision_id == revision.revision_id
                ),
                None,
            )
            if current_head is not None and (
                current_head.revision_id == revision.revision_id
                and current_head.digest == revision.digest
                and current_record == revision
            ):
                return current
            if current_record is not None:
                raise AtomicFailure("immutable provisional Slice revision conflicts")
            expected_revision = 1 if current_head is None else current_head.revision + 1
            expected_previous = None if current_head is None else current_head.revision_id
            if (
                revision.revision != expected_revision
                or revision.previous_revision_id != expected_previous
            ):
                raise AtomicFailure("provisional Slice append does not match its current head")
            candidate_head = ProvisionalSliceHead(
                revision.proposal_id, revision.revision_id, revision.revision, revision.digest,
            )
            candidate = replace(
                current,
                revision=current.revision + 1,
                provisional_slice_revisions=tuple(sorted(
                    current.provisional_slice_revisions + (revision,),
                    key=lambda item: item.revision_id,
                )),
                provisional_slice_heads=tuple(sorted(
                    (
                        item for item in current.provisional_slice_heads
                        if item.proposal_id != revision.proposal_id
                    ),
                    key=lambda item: item.proposal_id,
                )) + (candidate_head,),
            )
            candidate = replace(
                candidate,
                provisional_slice_heads=tuple(sorted(
                    candidate.provisional_slice_heads,
                    key=lambda item: item.proposal_id,
                )),
            )
            try:
                validate_state(candidate)
                _validate_provisional_slice_authority(self.root, candidate)
            except (DecodeError, DomainError, StateValidationError) as error:
                raise AtomicFailure("provisional Slice candidate is invalid") from error
            record_bytes = encode_json(_provisional_slice_to_json(revision))
            publish_immutable = cast(
                Callable[..., None],
                getattr(self.immutable_writer, "_StateStore__publish_immutable"),
            )
            publish_immutable(
                f"records/provisional-slices/{revision.revision_id}.json",
                record_bytes,
                strict_directory=True,
            )
            _atomic_replace(
                _artifact_path(self.root, "state.json"),
                _state_bytes(candidate, PROVISIONAL_SLICE_SCHEMA),
                self.operations,
                strict_directory=True,
            )
            return candidate


class _FailSecondProvisionalReplace(FileOperations):
    """Leave an immutable proposal orphan when its State pointer replace fails."""

    def __init__(self) -> None:
        self.replacements = 0

    def replace(self, source: Path, target: Path) -> None:
        self.replacements += 1
        if self.replacements == 2:
            raise OSError("injected provisional State replacement failure")
        super().replace(source, target)


def _create_d4_session_request_fixture(root: Path) -> tuple[WorkflowService, State]:
    workflow = WorkflowService.open(root)
    initialized = workflow.init()
    repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    workflow.accept_first_slice_session(
        SessionRequestEvidence(
            "ses_d4fixture123",
            "msg_d4fixture123",
            "Implement only the bounded request.",
            repository_id,
        ),
        expected_revision=ExpectedRevision(initialized.revision),
    )
    return workflow, CoreService.open(root).recover()


def _d4_competing_proposal_worker(
    root: str,
    repository_id: str,
    feature_id: str,
    expected_revision: int,
    title: str,
    barrier: Any,
    outcomes: Any,
) -> None:
    path = Path(root)
    state = CoreService.open(path).recover()
    barrier.wait(timeout=20)
    if state.revision != expected_revision:
        outcomes.put(("unexpected_revision", state.revision))
        return
    try:
        saved = _StateStore(path).publish_d4_provisional_slice(
            state,
            repository_id,
            feature_id,
            None,
            title,
            f"Bounded outcome for {title}",
            None,
            ("A bounded inclusion",),
            (),
            (),
            (),
            (ProvisionalSliceCriterion("c-1", "A visible result", "A test"),),
        )
    except AtomicFailure as error:
        outcome = "stale" if "stale" in str(error).lower() else "rejected"
        outcomes.put((outcome, str(error)))
    else:
        outcomes.put(("committed", saved.revision))


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
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], LEGACY_WRITE_SCHEMA)
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
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], LEGACY_WRITE_SCHEMA)

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
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], LEGACY_WRITE_SCHEMA)
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

                self.assertEqual(forward["schema"], LEGACY_WRITE_SCHEMA)
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
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], LEGACY_WRITE_SCHEMA)

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

    def test_schema_22_is_provisional_only_and_keeps_default_at_15(self) -> None:
        empty = State(0, None)

        self.assertEqual(SCHEMA, 15)
        self.assertEqual(json.loads(_state_bytes(empty))["schema"], 15)
        provisional = json.loads(_state_bytes(empty, 22))
        self.assertEqual(provisional["schema"], 22)
        self.assertEqual(provisional["provisional_slice_refs"], [])
        self.assertEqual(provisional["provisional_slice_heads"], [])

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


class ProvisionalSlicePersistenceTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temporary: TemporaryDirectory[str] = TemporaryDirectory(prefix="evospec-d1-")
        self.addCleanup(self.temporary.cleanup)
        self.root: Path = Path(self.temporary.name)
        self.base: State = _create_provisional_fixture(self.root)
        self.store = _ProvisionalSliceTestHarness(self.root)

    def _publish_first(
        self, provenance: ProvisionalSliceProvenance | None = None,
    ) -> tuple[ProvisionalSliceRevision, State]:
        revision = _make_provisional_revision(self.base, provenance=provenance)
        return revision, self.store.publish(self.base, revision, None)

    def _snapshot(self) -> dict[Path, bytes]:
        directory = self.root / ".evospec"
        return {
            path.relative_to(directory): path.read_bytes()
            for path in directory.rglob("*")
            if path.is_file()
        }

    def _create_two_slice_state(self, prefix: str) -> tuple[Path, State]:
        from tests.test_e1_r5a_next_slice import spec

        temporary = TemporaryDirectory(prefix=prefix)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        base = _create_provisional_fixture(
            root, criteria=spec("The first accepted Slice has human-approved criteria"),
        )
        return root, _create_two_slice_fixture(root, base)

    def test_schema22_roundtrips_canonical_records_without_changing_the_default(self) -> None:
        state_path = self.root / ".evospec" / "state.json"
        before_read = state_path.read_bytes()
        self.assertEqual(SCHEMA, 15)
        self.assertEqual(json.loads(_state_bytes(self.base))["schema"], 15)
        self.assertEqual(StateStore(self.root).load_state(), self.base)
        self.assertEqual(state_path.read_bytes(), before_read)

        revision = _make_provisional_revision(self.base)
        saved = self.store.publish(self.base, revision, None)
        encoded = json.loads(state_path.read_bytes())
        self.assertEqual(encoded["schema"], PROVISIONAL_SLICE_SCHEMA)
        self.assertEqual(
            encoded["provisional_slice_refs"],
            [{
                "id": revision.revision_id,
                "path": f"records/provisional-slices/{revision.revision_id}.json",
            }],
        )
        self.assertEqual(
            encoded["provisional_slice_heads"],
            [{
                "proposal_id": revision.proposal_id,
                "revision_id": revision.revision_id,
                "revision": 1,
                "digest": revision.digest,
            }],
        )
        record_path = (
            self.root / ".evospec" / "records" / "provisional-slices"
            / f"{revision.revision_id}.json"
        )
        record_bytes = record_path.read_bytes()
        self.assertEqual(encode_json(json.loads(record_bytes)), record_bytes)
        self.assertEqual(
            set(json.loads(record_bytes)),
            {
                "proposal_id", "revision_id", "revision", "repository_id",
                "feature_id", "title", "outcome", "example", "inclusions",
                "exclusions", "considerations", "open_questions", "criteria",
                "provenance", "previous_revision_id", "digest",
            },
        )
        self.assertEqual(saved, StateStore(self.root).load_state())
        self.assertEqual(saved.agreements, self.base.agreements)
        self.assertEqual(saved.active_context, self.base.active_context)
        self.assertFalse(hasattr(_StateStore(self.root), "publish_provisional_slice"))
        self.assertFalse(hasattr(StateStore(self.root), "publish_provisional_slice"))
        self.assertEqual(json.loads(record_bytes)["provenance"], {"kind": "AGENT_SUGGESTION"})

    def test_schema22_guidance_keeps_reads_and_blocks_writes(self) -> None:
        _, saved = self._publish_first()
        inspection = CoreService.open(self.root).inspect_review()
        self.assertEqual(inspection.persisted_schema, 22)
        self.assertEqual(
            set(allowed_actions(inspection)),
            {"context", "list_work", "next_work"},
        )
        before = self._snapshot()
        workflow = WorkflowService.open(self.root)
        self.assertEqual(
            workflow.list_work("all", expected_revision=ExpectedRevision(saved.revision)).revision,
            saved.revision,
        )
        self.assertEqual(
            workflow.next_work(expected_revision=ExpectedRevision(saved.revision)).revision,
            saved.revision,
        )
        self.assertEqual(
            workflow.reject(expected_revision=ExpectedRevision(saved.revision)).code,
            "D1_PROVISIONAL_SCHEMA_READ_ONLY",
        )
        self.assertEqual(self._snapshot(), before)

    def test_d1_publisher_is_absent_from_runtime_api_namespaces(self) -> None:
        import evospec
        import evospec.agent_protocol as protocol
        import evospec.cli as cli
        import evospec.persistence as persistence
        import evospec.services as services
        import evospec.workflow as workflow

        surfaces = (
            evospec,
            persistence,
            persistence.StateStore,
            persistence._StateStore,
            services.CoreService,
            workflow.WorkflowService,
            protocol,
            cli,
        )
        for surface in surfaces:
            with self.subTest(surface=surface):
                self.assertFalse(hasattr(surface, "publish_provisional_slice"))

    def test_user_expression_is_exact_attributable_data_not_acceptance(self) -> None:
        expression = "  I would like this future Slice considered.  "
        provenance = ProvisionalSliceProvenance(
            "USER_EXPRESSED",
            ProvisionalSliceSourceReference("ses_test_123", "msg_test_123"),
            expression,
            sha256(expression.encode("utf-8")).hexdigest(),
        )
        revision, _ = self._publish_first(provenance)
        raw_value = _provisional_slice_to_json(revision)
        assert isinstance(raw_value, dict)
        raw: dict[str, JsonValue] = raw_value
        provenance_value = raw["provenance"]
        assert isinstance(provenance_value, dict)
        self.assertEqual(raw["provenance"], _provisional_provenance_json(provenance))
        self.assertEqual(_provisional_slice_from_json(raw), revision)

        bad_expression = dict(raw)
        bad_expression_provenance = dict(provenance_value)
        expression_value = bad_expression_provenance["expression"]
        assert isinstance(expression_value, str)
        bad_expression_provenance["expression"] = expression_value + " "
        bad_expression["provenance"] = bad_expression_provenance
        with self.assertRaises(DecodeError):
            _provisional_slice_from_json(bad_expression)

        extra_provenance = dict(raw)
        extra_provenance["provenance"] = {**provenance_value, "accepted": True}
        with self.assertRaises(DecodeError):
            _provisional_slice_from_json(extra_provenance)

        bad_source = dict(raw)
        bad_source["provenance"] = {
            **provenance_value,
            "source_reference": {"session_id": "not-a-session", "message_id": "msg_test_123"},
        }
        with self.assertRaises(DecodeError):
            _provisional_slice_from_json(bad_source)

        extra_record_field: dict[str, JsonValue] = {**raw, "status": "ACCEPTED"}
        with self.assertRaises(DecodeError):
            _provisional_slice_from_json(extra_record_field)

    def test_malformed_revision_shapes_and_boolean_revision_fail_closed(self) -> None:
        raw_value = _provisional_slice_to_json(_make_provisional_revision(self.base))
        assert isinstance(raw_value, dict)
        raw: dict[str, JsonValue] = raw_value
        malformed: tuple[dict[str, JsonValue], ...] = (
            {**raw, "revision": True},
            {**raw, "title": "   "},
            {**raw, "criteria": [{"id": "c-01", "expected_result": "x", "expected_evidence": "y"}]},
            {**raw, "criteria": [{"id": "c-1", "expected_result": "", "expected_evidence": "y"}]},
            {**raw, "digest": "0" * 64},
            {**raw, "previous_revision_id": "wrong"},
        )
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(DecodeError):
                _provisional_slice_from_json(value)

    def test_wrong_root_feature_and_missing_feature_agreement_write_nothing(self) -> None:
        before = self._snapshot()
        for revision in (
            _make_provisional_revision(self.base, repository_id="0000000000000000"),
            _make_provisional_revision(self.base, feature_id="missing-feature"),
        ):
            with self.subTest(revision=revision.feature_id), self.assertRaises(AtomicFailure):
                self.store.publish(self.base, revision, None)
            self.assertEqual(self._snapshot(), before)

        with TemporaryDirectory(prefix="evospec-d1-unagreed-") as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            state = CoreService.open(root).recover()
            core = CoreService.open(root)
            feature = Feature(FeatureId("unagreed-feature"), "Unagreed Feature", None)
            state = core.record_feature(state, feature)
            state = core.record_change(
                state, Change(ChangeId("unagreed-change"), feature.id, "Fixture change"),
            )
            before_unagreed = {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*") if path.is_file()
            }
            with self.assertRaises(AtomicFailure):
                _ProvisionalSliceTestHarness(root).publish(
                    state, _make_provisional_revision(state), None,
                )
            self.assertEqual(
                before_unagreed,
                {
                    path.relative_to(root / ".evospec"): path.read_bytes()
                    for path in (root / ".evospec").rglob("*") if path.is_file()
                },
            )

    def test_multiple_accepted_slices_for_one_feature_publish_and_recover(self) -> None:
        root, state = self._create_two_slice_state("evospec-d1-multi-")
        store = _ProvisionalSliceTestHarness(root)
        recovery = StateStore(root)
        feature_agreements = tuple(
            item for item in state.agreements
            if item.kind == "FEATURE" and item.feature_id == state.features[0].id
        )
        slice_agreements = tuple(
            item for item in state.agreements
            if item.kind == "SLICE" and item.feature_id == state.features[0].id
        )
        self.assertEqual(len(feature_agreements), 2)
        self.assertEqual(len(slice_agreements), 2)
        self.assertEqual(len({item.change_id for item in feature_agreements}), 2)

        contexts = tuple(
            context for context in (state.active_context, state.preserved_primary_context)
            if context is not None
        ) + tuple(
            context for item in state.work_items
            for context in (item.context, item.preserved_primary_context)
            if context is not None
        ) + tuple(archive.context for archive in state.terminal_archives)
        for feature_agreement in feature_agreements:
            matching_slices = tuple(
                item for item in slice_agreements
                if item.change_id == feature_agreement.change_id
            )
            self.assertEqual(len(matching_slices), 1)
            slice_agreement = matching_slices[0]
            self.assertEqual(
                (feature_agreement.source, feature_agreement.statement, feature_agreement.source_reference),
                (slice_agreement.source, slice_agreement.statement, slice_agreement.source_reference),
            )
            self.assertTrue(any(
                context.feature_id == feature_agreement.feature_id
                and context.change_id == feature_agreement.change_id
                and context.feature_agreement_ref == feature_agreement.id
                and context.slice_agreement_ref == slice_agreement.id
                for context in contexts
            ))

        revision = _make_provisional_revision(state)
        saved = store.publish(state, revision, None)
        self.assertEqual(saved, recovery.load_state())
        self.assertEqual(saved.agreements, state.agreements)

    def test_feature_association_rejects_mismatched_provenance_without_writing(self) -> None:
        for mismatch in ("statement", "source"):
            with self.subTest(mismatch=mismatch):
                root, state = self._create_two_slice_state(f"evospec-d1-provenance-{mismatch}-")
                feature_agreement = tuple(
                    item for item in state.agreements if item.kind == "FEATURE"
                )[-1]
                slice_agreement = next(
                    item for item in state.agreements
                    if item.kind == "SLICE" and item.change_id == feature_agreement.change_id
                )
                if mismatch == "statement":
                    statement = f"{slice_agreement.statement} (different terminal statement)"
                    mismatched = replace(
                        slice_agreement,
                        statement=statement,
                        source_reference=f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}",
                    )
                else:
                    reference = (
                        f"session:ses_test_123:msg_test_123:"
                        f"{sha256(feature_agreement.statement.encode('utf-8')).hexdigest()[:16]}"
                    )
                    mismatched = replace(
                        feature_agreement, source="SESSION_REQUEST", source_reference=reference,
                    )
                candidate = replace(
                    state,
                    agreements=tuple(
                        mismatched if item.id == mismatched.id else item
                        for item in state.agreements
                    ),
                    slice_criteria_revisions=tuple(
                        item for item in state.slice_criteria_revisions
                        if item.slice_agreement_ref != slice_agreement.id
                    ),
                    slice_criteria_heads=tuple(
                        item for item in state.slice_criteria_heads
                        if item.slice_agreement_ref != slice_agreement.id
                    ),
                )
                validate_state(candidate)
                paired_feature = next(
                    item for item in candidate.agreements
                    if item.kind == "FEATURE" and item.change_id == feature_agreement.change_id
                )
                paired_slice = next(
                    item for item in candidate.agreements
                    if item.kind == "SLICE" and item.change_id == feature_agreement.change_id
                )
                self.assertNotEqual(
                    (paired_feature.source, paired_feature.statement, paired_feature.source_reference),
                    (paired_slice.source, paired_slice.statement, paired_slice.source_reference),
                )
                for agreement in (paired_feature, paired_slice):
                    if agreement.source == "INTERACTIVE_TERMINAL":
                        self.assertEqual(
                            agreement.source_reference,
                            f"tty:{sha256(agreement.statement.encode('utf-8')).hexdigest()[:12]}",
                        )
                    else:
                        self.assertEqual(
                            agreement.source_reference,
                            f"session:ses_test_123:msg_test_123:"
                            f"{sha256(agreement.statement.encode('utf-8')).hexdigest()[:16]}",
                        )

                state_path = root / ".evospec" / "state.json"
                agreement_path = (
                    root / ".evospec" / "records" / "agreements" / f"{mismatched.id}.json"
                )
                agreement_path.write_bytes(encode_json(_agreement_to_json(mismatched)))
                state_path.write_bytes(_state_bytes(candidate, PROVISIONAL_SLICE_SCHEMA))
                persisted = StateStore(root).load_state()
                self.assertEqual(persisted, candidate)
                authority_root = root / ".evospec"
                before = {
                    path.relative_to(authority_root): path.read_bytes()
                    for path in authority_root.rglob("*") if path.is_file()
                }
                revision = _make_provisional_revision(persisted)
                with self.assertRaises(AtomicFailure):
                    _ProvisionalSliceTestHarness(root).publish(persisted, revision, None)
                self.assertEqual(before, {
                    path.relative_to(authority_root): path.read_bytes()
                    for path in authority_root.rglob("*") if path.is_file()
                })

    def test_feature_association_rejects_duplicate_logical_pairs_and_crossed_refs(self) -> None:
        root, state = self._create_two_slice_state("evospec-d1-invalid-association-")
        revision = _make_provisional_revision(state)
        head = ProvisionalSliceHead(
            revision.proposal_id, revision.revision_id, revision.revision, revision.digest,
        )
        proposal_state = replace(
            state, provisional_slice_revisions=(revision,), provisional_slice_heads=(head,),
        )
        before = {
            path.relative_to(root / ".evospec"): path.read_bytes()
            for path in (root / ".evospec").rglob("*") if path.is_file()
        }

        accepted_feature = next(item for item in state.agreements if item.kind == "FEATURE")
        duplicate_feature = replace(accepted_feature, id="agreement-feature-duplicate")
        duplicate = replace(
            proposal_state, agreements=proposal_state.agreements + (duplicate_feature,),
        )
        with self.assertRaises(DecodeError):
            _validate_provisional_slice_authority(root, duplicate)
        self.assertEqual(before, {
            path.relative_to(root / ".evospec"): path.read_bytes()
            for path in (root / ".evospec").rglob("*") if path.is_file()
        })

        invalid_provenance = replace(
            state,
            agreements=tuple(
                replace(item, source_reference="tty:invalid")
                if item.id == accepted_feature.id else item
                for item in state.agreements
            ),
        )
        with self.assertRaises(StateValidationError):
            validate_state(invalid_provenance)
        self.assertEqual(before, {
            path.relative_to(root / ".evospec"): path.read_bytes()
            for path in (root / ".evospec").rglob("*") if path.is_file()
        })

        active = state.active_context
        assert active is not None
        other_feature_ref = next(
            item.id for item in state.agreements
            if item.kind == "FEATURE" and item.id != active.feature_agreement_ref
        )
        crossed = replace(
            proposal_state,
            active_context=replace(active, feature_agreement_ref=other_feature_ref),
        )
        with self.assertRaises(DecodeError):
            _validate_provisional_slice_authority(root, crossed)
        self.assertEqual(before, {
            path.relative_to(root / ".evospec"): path.read_bytes()
            for path in (root / ".evospec").rglob("*") if path.is_file()
        })

    def test_recovery_rejects_bad_refs_digest_head_and_wrong_root(self) -> None:
        revision, saved = self._publish_first()
        state_path = self.root / ".evospec" / "state.json"
        original_state = state_path.read_bytes()
        original_record = _provisional_slice_to_json(revision)

        corruptions = (
            lambda value: value["provisional_slice_refs"][0].update(path="records/other.json"),
            lambda value: value["provisional_slice_refs"].append(
                dict(value["provisional_slice_refs"][0])
            ),
            lambda value: value["provisional_slice_heads"].append(
                dict(value["provisional_slice_heads"][0])
            ),
            lambda value: value["provisional_slice_heads"][0].update(digest="0" * 64),
            lambda value: value.update(unexpected=True),
        )
        for corrupt in corruptions:
            with self.subTest(corrupt=corrupt):
                state_value = json.loads(original_state)
                corrupt(state_value)
                state_path.write_text(json.dumps(state_value), encoding="utf-8")
                with self.assertRaises(RecoveryError):
                    StateStore(self.root).load_state()
                state_path.write_bytes(original_state)

        record_path = (
            self.root / ".evospec" / "records" / "provisional-slices"
            / f"{revision.revision_id}.json"
        )
        assert isinstance(original_record, dict)
        bad_record: dict[str, JsonValue] = dict(original_record)
        bad_record["outcome"] = "tampered without a matching digest"
        record_path.write_bytes(encode_json(bad_record))
        with self.assertRaises(RecoveryError):
            StateStore(self.root).load_state()
        record_path.write_bytes(encode_json(original_record))
        self.assertEqual(StateStore(self.root).load_state(), saved)

        with TemporaryDirectory(prefix="evospec-d1-wrong-root-") as temporary:
            wrong_root = Path(temporary)
            import shutil

            shutil.copytree(self.root / ".evospec", wrong_root / ".evospec")
            with self.assertRaises(RecoveryError):
                StateStore(wrong_root).load_state()

    def test_recovery_rejects_chain_gaps_nonfinal_heads_and_unreachable_refs(self) -> None:
        first = _make_provisional_revision(self.base)
        state1 = self.store.publish(self.base, first, None)
        second = _make_provisional_revision(state1, previous=first, outcome="Revision two")
        state2 = self.store.publish(
            state1, second, state1.provisional_slice_heads[0],
        )
        state_path = self.root / ".evospec" / "state.json"
        original_state = state_path.read_bytes()
        second_proposal = _make_provisional_revision(self.base, title="Separate proposal")
        second_record = (
            self.root / ".evospec" / "records" / "provisional-slices"
            / f"{second_proposal.revision_id}.json"
        )
        second_record.write_bytes(encode_json(_provisional_slice_to_json(second_proposal)))

        corruptions = (
            lambda value: value["provisional_slice_refs"].pop(0),
            lambda value: value["provisional_slice_heads"][0].update(
                revision_id=first.revision_id,
                revision=first.revision,
                digest=first.digest,
            ),
            lambda value: value["provisional_slice_refs"].append({
                "id": second_proposal.revision_id,
                "path": f"records/provisional-slices/{second_proposal.revision_id}.json",
            }),
        )
        for corrupt in corruptions:
            with self.subTest(corrupt=corrupt):
                state_value = json.loads(original_state)
                corrupt(state_value)
                state_value["provisional_slice_refs"].sort(key=lambda item: item["id"])
                state_path.write_text(json.dumps(state_value), encoding="utf-8")
                with self.assertRaises(RecoveryError):
                    StateStore(self.root).load_state()
                state_path.write_bytes(original_state)
        self.assertEqual(StateStore(self.root).load_state(), state2)

    def test_replay_is_noop_and_stale_state_or_head_cannot_publish(self) -> None:
        first = _make_provisional_revision(self.base)
        state1 = self.store.publish(self.base, first, None)
        snapshot = self._snapshot()
        head1 = state1.provisional_slice_heads[0]
        replay = self.store.publish(state1, first, head1)
        self.assertEqual(replay, state1)
        self.assertEqual(self._snapshot(), snapshot)

        second = _make_provisional_revision(
            state1,
            previous=first,
            title="Revised provisional future Slice",
            criteria=(
                ProvisionalSliceCriterion("c-1", "Still bounded", "Observable result"),
                ProvisionalSliceCriterion("c-2", "New limit stated", "Limit is present"),
            ),
        )
        with self.assertRaises(AtomicFailure):
            self.store.publish(state1, second, None)
        self.assertEqual(self._snapshot(), snapshot)
        state2 = self.store.publish(state1, second, head1)
        snapshot2 = self._snapshot()
        with self.assertRaises(AtomicFailure):
            self.store.publish(state1, first, head1)
        self.assertEqual(self._snapshot(), snapshot2)

        head2 = state2.provisional_slice_heads[0]
        third = _make_provisional_revision(state2, previous=second, outcome="Another outcome")
        with self.assertRaises(AtomicFailure):
            self.store.publish(
                state2, third, replace(head2, digest="0" * 64),
            )
        self.assertEqual(self._snapshot(), snapshot2)

        removed = _make_provisional_revision(
            state2,
            previous=second,
            criteria=(ProvisionalSliceCriterion("c-2", "Keep c-2", "Evidence"),),
        )
        state3 = self.store.publish(state2, removed, head2)
        resurrected = _make_provisional_revision(
            state3,
            previous=removed,
            criteria=(
                ProvisionalSliceCriterion("c-1", "Resurrected", "Evidence"),
                ProvisionalSliceCriterion("c-2", "Keep c-2", "Evidence"),
            ),
        )
        snapshot3 = self._snapshot()
        with self.assertRaises(AtomicFailure):
            self.store.publish(
                state3, resurrected, state3.provisional_slice_heads[0],
            )
        self.assertEqual(self._snapshot(), snapshot3)

    def test_ordinary_core_writer_rejects_schema22_before_any_effect(self) -> None:
        revision = _make_provisional_revision(self.base)
        saved = self.store.publish(self.base, revision, None)
        if saved.active_context is None:
            self.fail("fixture must have an active Core context")
        before = self._snapshot()
        core = CoreService.open(self.root)
        with self.assertRaises(ServiceError) as raised:
            core.record_safety_facts(
                saved,
                True,
                replace(
                    saved.active_context.safety_facts,
                    unresolved_decision_details=("ordinary writer probe",),
                ),
            )
        self.assertIsInstance(raised.exception.__cause__, AtomicFailure)
        self.assertIn("D1_PROVISIONAL_SCHEMA_READ_ONLY", str(raised.exception.__cause__))
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(StateStore(self.root).load_state(), saved)
        with self.assertRaises(ServiceError):
            CoreService.open(self.root).activate_provisional_slice_writes(
                saved,
                saved.initialization.repository_id if saved.initialization else "",
                str(saved.features[0].id),
            )
        self.assertEqual(self._snapshot(), before)

    def test_d4_schema23_activation_is_bound_to_the_exact_initial_session_request(self) -> None:
        with TemporaryDirectory(prefix="evospec-d4-activation-") as temporary:
            root = Path(temporary)
            workflow, source = _create_d4_session_request_fixture(root)
            self.assertEqual(json.loads((root / ".evospec" / "state.json").read_bytes())["schema"], 15)
            before = {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*") if path.is_file()
            }
            assert source.initialization is not None
            feature_id = str(source.features[0].id)

            with self.assertRaises(WorkflowError):
                workflow.activate_provisional_slice_writes(
                    source.initialization.repository_id,
                    "feature-not-the-initial-request",
                    expected_revision=ExpectedRevision(source.revision),
                )
            self.assertEqual(before, {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*") if path.is_file()
            })

            source_bytes = (root / ".evospec" / "state.json").read_bytes()
            enabled = workflow.activate_provisional_slice_writes(
                source.initialization.repository_id,
                feature_id,
                expected_revision=ExpectedRevision(source.revision),
            )
            state = CoreService.open(root).recover()
            self.assertEqual(enabled.code, "PROVISIONAL_SLICE_WRITES_ACTIVATED")
            self.assertEqual(state.revision, source.revision + 1)
            self.assertEqual(state.provisional_slice_revisions, ())
            self.assertEqual(state.provisional_slice_heads, ())
            self.assertEqual(
                json.loads((root / ".evospec" / "state.json").read_bytes())["schema"],
                23,
            )
            marker = state.provisional_slice_activation
            self.assertIsNotNone(marker)
            assert marker is not None
            self.assertEqual(marker.marker, "D4_SCHEMA15_AUTO_FIRST")
            self.assertEqual(marker.source_schema, 15)
            self.assertEqual(marker.source_revision, source.revision)
            self.assertEqual(marker.source_sha256, sha256(source_bytes).hexdigest())
            self.assertEqual(marker.repository_id, source.initialization.repository_id)
            self.assertEqual(marker.feature_id, source.features[0].id)
            self.assertEqual(marker.recorded_revision, state.revision)
            self.assertEqual(StateStore(root).load_state(), state)

    def test_d4_core_proposals_create_supersede_replay_and_preserve_immutable_history(self) -> None:
        with TemporaryDirectory(prefix="evospec-d4-proposals-") as temporary:
            root = Path(temporary)
            workflow, source = _create_d4_session_request_fixture(root)
            assert source.initialization is not None
            repository_id = source.initialization.repository_id
            feature_id = str(source.features[0].id)
            enabled = workflow.activate_provisional_slice_writes(
                repository_id, feature_id,
                expected_revision=ExpectedRevision(source.revision),
            )
            current = CoreService.open(root).recover()

            def propose(
                state: State,
                expected_head: ProvisionalSliceHead | None,
                *,
                outcome: str = "Bounded proposed outcome",
            ):
                return workflow.propose_provisional_slice(
                    repository_id,
                    feature_id,
                    expected_head,
                    "Suggested next Slice",
                    outcome,
                    None,
                    ("Keep scope bounded",),
                    ("No acceptance is implied",),
                    (),
                    ("Confirm the final UI choice",),
                    (ProvisionalSliceCriterion("c-1", "A bounded result", "A visible check"),),
                    expected_revision=ExpectedRevision(state.revision),
                )

            first_result = propose(current, None)
            first = CoreService.open(root).recover()
            head1 = first.provisional_slice_heads[0]
            revision1 = next(item for item in first.provisional_slice_revisions if item.revision_id == head1.revision_id)
            record_path = root / ".evospec" / "records" / "provisional-slices" / f"{head1.revision_id}.json"
            original_record = record_path.read_bytes()
            self.assertEqual(first_result.code, "PROVISIONAL_SLICE_PROPOSED")
            self.assertEqual(first.revision, enabled.revision + 1)
            self.assertEqual(revision1.provenance, ProvisionalSliceProvenance("AGENT_SUGGESTION"))

            replay_result = propose(first, None)
            replay = CoreService.open(root).recover()
            self.assertEqual(replay_result.revision, first.revision)
            self.assertEqual(replay, first)

            revised_result = propose(first, head1, outcome="Clarified bounded outcome")
            revised = CoreService.open(root).recover()
            head2 = revised.provisional_slice_heads[0]
            self.assertEqual(revised_result.revision, first.revision + 1)
            self.assertEqual(head2.proposal_id, head1.proposal_id)
            self.assertEqual(head2.revision, 2)
            self.assertEqual(head2.revision_id, f"{head1.proposal_id}-r2")
            self.assertEqual(record_path.read_bytes(), original_record)
            self.assertEqual(
                {item.revision_id for item in revised.provisional_slice_revisions},
                {head1.revision_id, head2.revision_id},
            )
            self.assertEqual(StateStore(root).load_state(), revised)

    def test_d4_provisional_slice_publication_cas_is_process_safe(self) -> None:
        with TemporaryDirectory(prefix="evospec-d4-process-cas-") as temporary:
            root = Path(temporary)
            workflow, source = _create_d4_session_request_fixture(root)
            assert source.initialization is not None
            repository_id = source.initialization.repository_id
            feature_id = str(source.features[0].id)
            workflow.activate_provisional_slice_writes(
                repository_id,
                feature_id,
                expected_revision=ExpectedRevision(source.revision),
            )
            baseline = CoreService.open(root).recover()
            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            outcomes = context.Queue()
            processes = [
                context.Process(
                    target=_d4_competing_proposal_worker,
                    args=(
                        str(root),
                        repository_id,
                        feature_id,
                        baseline.revision,
                        title,
                        barrier,
                        outcomes,
                    ),
                )
                for title in ("Concurrent proposal A", "Concurrent proposal B")
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(25)
            lingering = [process for process in processes if process.is_alive()]
            for process in lingering:
                process.terminate()
                process.join()
            self.assertFalse(lingering, "D4 CAS workers did not finish")
            self.assertEqual([process.exitcode for process in processes], [0, 0])
            results = [outcomes.get(timeout=5) for _ in processes]
            outcomes.close()
            outcomes.join_thread()

            self.assertEqual(
                sorted(result[0] for result in results),
                ["committed", "stale"],
            )
            saved = CoreService.open(root).recover()
            self.assertEqual(saved.revision, baseline.revision + 1)
            self.assertEqual(len(saved.provisional_slice_heads), 1)
            self.assertEqual(len(saved.provisional_slice_revisions), 1)
            record_paths = tuple(
                (root / ".evospec" / "records" / "provisional-slices").glob("*.json")
            )
            self.assertEqual(len(record_paths), 1)

    def test_d4_proposal_validation_is_bounded_and_failed_state_commit_recovers_safely(self) -> None:
        with TemporaryDirectory(prefix="evospec-d4-failure-") as temporary:
            root = Path(temporary)
            workflow, source = _create_d4_session_request_fixture(root)
            assert source.initialization is not None
            repository_id = source.initialization.repository_id
            feature_id = str(source.features[0].id)
            workflow.activate_provisional_slice_writes(
                repository_id, feature_id,
                expected_revision=ExpectedRevision(source.revision),
            )
            enabled = CoreService.open(root).recover()

            with self.assertRaises(WorkflowError):
                workflow.propose_provisional_slice(
                    repository_id, feature_id, None, "é" * 1025,
                    "Outcome", None, (), (), (), (), (),
                    expected_revision=ExpectedRevision(enabled.revision),
                )
            self.assertEqual(CoreService.open(root).recover(), enabled)

            failing_core = CoreService.open(root, _FailSecondProvisionalReplace())
            with self.assertRaises(ServiceError):
                failing_core.propose_provisional_slice(
                    enabled,
                    repository_id,
                    feature_id,
                    None,
                    "Orphaned suggestion",
                    "Bounded outcome",
                    None,
                    ("A bounded scope",),
                    (),
                    (),
                    (),
                    (ProvisionalSliceCriterion("c-1", "Result", "Evidence"),),
                )
            after_failure = CoreService.open(root).recover()
            self.assertEqual(after_failure, enabled)
            state_path = root / ".evospec" / "state.json"
            self.assertEqual(json.loads(state_path.read_bytes())["provisional_slice_refs"], [])
            orphan_paths = tuple((root / ".evospec" / "records" / "provisional-slices").glob("*.json"))
            self.assertEqual(len(orphan_paths), 1)

            recovered = CoreService.open(root).propose_provisional_slice(
                after_failure,
                repository_id,
                feature_id,
                None,
                "Orphaned suggestion",
                "Bounded outcome",
                None,
                ("A bounded scope",),
                (),
                (),
                (),
                (ProvisionalSliceCriterion("c-1", "Result", "Evidence"),),
            )
            self.assertEqual(recovered.revision, enabled.revision + 1)
            self.assertEqual(len(recovered.provisional_slice_revisions), 1)
            self.assertEqual(StateStore(root).load_state(), recovered)
