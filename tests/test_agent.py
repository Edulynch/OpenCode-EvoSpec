from __future__ import annotations

import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from evospec.agent import _slice_acceptance_inventory, project_context
from evospec.agent_protocol import context_json
from evospec.domain import (
    AgreementRecord,
    AssessmentConfidence,
    AssessmentDepth,
    AssessmentDepthValue,
    AssessmentEvidence,
    AssessmentOperation,
    CapabilityState,
    Change,
    ChangeId,
    CriterionTestReceipt,
    Decision,
    DecisionId,
    ExpectedRevision,
    FeatureId,
    Feature,
    IntentAssessment,
    MaterialDecision,
    LockAssessment,
    LockId,
    LockRelease,
    LivingSpecId,
    RepositoryPath,
    ReviewResult,
    SemanticLock,
    SemanticLockRevision,
    VerificationEvidence,
    OperationalState,
    ParkedWork,
    ProvisionalSliceCriterion,
    ProvisionalSliceProvenance,
    ProvisionalSliceSourceReference,
    SliceCriteriaHead,
    SliceCriteriaRevision,
    SliceCriterion,
    SliceOutcomeReceipt,
    WorkId,
    WorkItem,
)
from evospec.lifecycle import Classification, LifecycleState, Phase, ReviewVerdict
from evospec.persistence import JsonValue
from evospec.services import CoreService, SliceCriteriaSpec, slice_acceptance_statement
from evospec.workflow import ClarificationAnswer, WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_e1_r1d1_slice_criteria import CHECKPOINT, FEATURE, SLICE, TASK, _provenance, _spec
from tests.test_services_state import convergence_assessment
from tests.test_workflow_assurance import established_change_assessment


class AgentContextTests(unittest.TestCase):
    def test_provisional_slice_inventory_projects_current_heads_and_provenance(self) -> None:
        from tests.test_persistence import (
            _ProvisionalSliceTestHarness,
            _create_provisional_fixture,
            _create_two_slice_fixture,
            _make_provisional_revision,
        )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = _create_provisional_fixture(root, criteria=SliceCriteriaSpec(
                "Base accepted outcome",
                "Base accepted example",
                ("Base inclusion",),
                ("Base exclusion",),
                ("Base decision",),
                (SliceCriterion(
                    "AC-1", "Base result", "python-unittest:tests.test_r5a_fixture",
                ),),
            ))
            accepted = _create_two_slice_fixture(root, base)
            publisher = _ProvisionalSliceTestHarness(root)
            legacy_projection = project_context(WorkflowService.open(root)).provisional_slice_inventory
            self.assertEqual(legacy_projection.availability, "AVAILABLE")
            self.assertEqual(legacy_projection.total, 0)
            self.assertTrue(legacy_projection.complete)

            suggested = _make_provisional_revision(
                accepted,
                title="Agent suggestion",
                criteria=(
                    ProvisionalSliceCriterion("c-1", "First result", "First evidence"),
                    ProvisionalSliceCriterion("c-2", "Second result", "Second evidence"),
                ),
            )
            state = publisher.publish(accepted, suggested, None)
            expression = "Consider this later Slice."
            expressed = _make_provisional_revision(
                state,
                title="User-expressed proposal",
                provenance=ProvisionalSliceProvenance(
                    "USER_EXPRESSED",
                    ProvisionalSliceSourceReference("ses_d2_test123", "msg_d2_test123"),
                    expression,
                    sha256(expression.encode("utf-8")).hexdigest(),
                ),
            )
            state = publisher.publish(state, expressed, None)
            suggested_revision = _make_provisional_revision(
                state,
                previous=suggested,
                title="Agent suggestion revised",
                criteria=(
                    ProvisionalSliceCriterion("c-1", "Updated first result", "Updated evidence"),
                    ProvisionalSliceCriterion("c-2", "Second result", "Second evidence"),
                ),
            )
            suggested_head = next(
                item for item in state.provisional_slice_heads
                if item.proposal_id == suggested.proposal_id
            )
            state = publisher.publish(state, suggested_revision, suggested_head)
            before = CoreService.open(root).recover()
            authority_root = root / ".evospec"

            def authority_snapshot() -> dict[str, tuple[bytes, int]]:
                return {
                    path.relative_to(authority_root).as_posix(): (
                        path.read_bytes(), path.stat().st_mtime_ns,
                    )
                    for path in authority_root.rglob("*")
                    if path.is_file()
                }

            files_before = authority_snapshot()
            workflow = WorkflowService.open(root)
            context = project_context(workflow)
            repeated = project_context(workflow)

            inventory = context.provisional_slice_inventory
            self.assertEqual(inventory.availability, "AVAILABLE")
            self.assertEqual(inventory.total, 2)
            self.assertTrue(inventory.complete)
            self.assertFalse(inventory.truncated)
            self.assertEqual(inventory, repeated.provisional_slice_inventory)
            self.assertEqual(len(inventory.items), len({
                item.proposal_id for item in inventory.items
            }))
            self.assertEqual(
                [item.proposal_id for item in inventory.items],
                sorted(item.proposal_id for item in inventory.items),
            )
            items = {item.proposal_id: item for item in inventory.items}
            self.assertEqual(set(items), {suggested.proposal_id, expressed.proposal_id})
            suggested_item = items[suggested.proposal_id]
            self.assertEqual(suggested_item.head_revision_id, suggested_revision.revision_id)
            self.assertEqual(suggested_item.head_revision, suggested_revision.revision)
            self.assertEqual(suggested_item.head_digest, suggested_revision.digest)
            self.assertEqual(suggested_item.title, "Agent suggestion revised")
            self.assertEqual(suggested_item.status, "PROVISIONAL")
            self.assertEqual(suggested_item.provenance, "AGENT_SUGGESTION")
            self.assertEqual(suggested_item.criteria, suggested_revision.criteria)
            self.assertEqual(len(suggested_item.criteria), 2)
            self.assertEqual(
                {item.head_revision_id for item in inventory.items},
                {item.revision_id for item in state.provisional_slice_heads},
            )
            self.assertNotIn(suggested.revision_id, {
                item.head_revision_id for item in inventory.items
            })
            assert state.initialization is not None, "the fixture state must be initialized"
            self.assertEqual(
                (suggested_item.repository_id, suggested_item.feature_id),
                (state.initialization.repository_id, str(suggested_revision.feature_id)),
            )
            self.assertEqual(items[expressed.proposal_id].provenance, "USER_EXPRESSED")
            expressed_item = items[expressed.proposal_id]
            self.assertEqual(expressed_item.status, "PROVISIONAL")
            accepted_slices = tuple(
                agreement for agreement in state.agreements
                if agreement.kind == "SLICE"
            )
            self.assertEqual(len(accepted_slices), 2)
            self.assertEqual(
                len({item.change_id for item in accepted_slices}), 2,
            )
            self.assertEqual(
                {item.slice_agreement_ref for item in context.slice_acceptance_inventory.items},
                {item.id for item in accepted_slices},
            )
            self.assertEqual(context.slice_acceptance_inventory.total, 2)

            serialized = context_json(context)["provisional_slice_inventory"]
            self.assertIsInstance(serialized, dict)
            if not isinstance(serialized, dict):
                self.fail("provisional Slice inventory must serialize as an object")
            self.assertEqual(serialized["availability"], "AVAILABLE")
            serialized_items = serialized["items"]
            self.assertIsInstance(serialized_items, list)
            if not isinstance(serialized_items, list):
                self.fail("provisional Slice items must serialize as an array")
            serialized_suggested = next(
                item for item in serialized_items
                if isinstance(item, dict) and item.get("proposal_id") == suggested.proposal_id
            )
            self.assertEqual(set(serialized_suggested), {
                "proposal_id", "head", "repository_id", "feature_id", "title",
                "status", "provenance", "criteria",
            })
            self.assertEqual(serialized_suggested["status"], "PROVISIONAL")
            self.assertEqual(serialized_suggested["provenance"], "AGENT_SUGGESTION")
            self.assertEqual(serialized_suggested["head"], {
                "revision_id": suggested_revision.revision_id,
                "revision": suggested_revision.revision,
                "digest": suggested_revision.digest,
            })
            self.assertEqual(
                serialized_suggested["criteria"],
                [
                    {"id": item.id, "expected_result": item.expected_result,
                     "expected_evidence": item.expected_evidence}
                    for item in suggested_revision.criteria
                ],
            )
            self.assertEqual(CoreService.open(root).recover(), before)
            self.assertEqual(authority_snapshot(), files_before)

    def test_provisional_slice_inventory_is_bounded_with_stable_order(self) -> None:
        from tests.test_persistence import (
            _ProvisionalSliceTestHarness,
            _create_provisional_fixture,
            _make_provisional_revision,
        )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = _create_provisional_fixture(root)
            publisher = _ProvisionalSliceTestHarness(root)
            for index in range(33):
                proposal = _make_provisional_revision(
                    state, title=f"Bounded proposal {index:02d}",
                )
                state = publisher.publish(state, proposal, None)

            inventory = project_context(WorkflowService.open(root)).provisional_slice_inventory

            ordered = sorted(
                state.provisional_slice_heads,
                key=lambda head: (
                    str(next(
                        item.feature_id for item in state.provisional_slice_revisions
                        if item.revision_id == head.revision_id
                    )),
                    head.proposal_id,
                ),
            )
            self.assertEqual(inventory.total, 33)
            self.assertEqual(len(inventory.items), 32)
            self.assertFalse(inventory.complete)
            self.assertTrue(inventory.truncated)
            self.assertEqual(
                [item.proposal_id for item in inventory.items],
                [item.proposal_id for item in ordered[:32]],
            )

    def test_ordinary_r3b_historical_acceptance_accepts_earlier_canonical_proofs(self) -> None:
        from tests.test_e1_r3b_slice_auto_acceptance import R3BSliceOutcomeTests

        fixture = R3BSliceOutcomeTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.upgrade_r3b()
        fixture.record_both()

        accepted_response = fixture.send(fixture.accept_request())
        self.assertEqual(accepted_response["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        accepted_state = CoreService.open(fixture.root).recover()
        receipt = accepted_state.slice_outcome_receipts[-1]
        proofs = tuple(
            next(item for item in accepted_state.criterion_test_receipts if item.id == proof_id)
            for proof_id in receipt.evidence_receipt_ids
        )
        self.assertEqual(receipt.source, "CORE_OBJECTIVE_ACCEPTANCE")
        self.assertTrue(any(
            proof.source_revision != receipt.source_revision
            or proof.recorded_revision != receipt.recorded_revision
            for proof in proofs
        ))
        self.assertTrue(all(proof.recorded_revision <= receipt.source_revision for proof in proofs))
        self.assertTrue(all(proof.recorded_revision < receipt.recorded_revision for proof in proofs))
        fixture.workflow.park("deferred for later")

        parked_state = CoreService.open(fixture.root).recover()
        item = _slice_acceptance_inventory(parked_state).items[0]
        self.assertEqual(item.scope_status, "PARKED")
        self.assertEqual(item.historical_acceptance_status, "ACCEPTED_PREVIOUSLY")
        # Confirm the historical details exist before inspecting their receipts.
        history = item.historical_acceptance
        assert history is not None, "accepted history must include its supporting records"
        self.assertEqual(history.receipt, receipt)
        self.assertEqual(
            tuple(proof.id for proof in history.criterion_evidence),
            receipt.evidence_receipt_ids,
        )

    def test_historical_slice_acceptance_requires_exact_receipt_and_criterion_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            initial = workflow.init()
            criteria = _spec()
            statement = slice_acceptance_statement(FEATURE, SLICE, TASK, CHECKPOINT, criteria)
            workflow.accept_first_slice(
                FEATURE, SLICE, TASK, CHECKPOINT, _provenance(statement),
                expected_revision=ExpectedRevision(initial.revision), criteria=criteria,
            )
            state = CoreService.open(root).recover()
            _, accepted = state.agreements
            prior_revision = state.slice_criteria_revisions[0]
            current_revision = replace(
                prior_revision,
                id="criteria-amended",
                outcome="A revised outcome",
                previous_revision_id=prior_revision.id,
            )
            proofs = tuple(
                CriterionTestReceipt(
                    f"proof-{criterion.id}", accepted.id, prior_revision.id, criterion.id,
                    "python-unittest", f"tests/{criterion.id}.py", 0, "a" * 64, 2, 3,
                    "b" * 64, "b" * 64, "input-policy", "c" * 64,
                    "admission-policy", "d" * 64,
                )
                for criterion in prior_revision.criteria
            )
            receipt = SliceOutcomeReceipt(
                "receipt-prior", accepted.repository_id, accepted.id, prior_revision.id,
                tuple(proof.id for proof in proofs), "b" * 64, "input-policy", "c" * 64,
                "admission-policy", "d" * 64, "e" * 64,
                "ALL_CRITERIA_FRESH_SUPPORTED_TESTS", "CORE_OBJECTIVE_ACCEPTANCE", 3, 4,
            )

            def inventory(
                outcome: SliceOutcomeReceipt | None = receipt,
                evidence: tuple[CriterionTestReceipt, ...] = proofs,
                revisions: tuple[SliceCriteriaRevision, ...] = (prior_revision, current_revision),
            ):
                fixture = replace(
                    state,
                    revision=max(state.revision, 4),
                    slice_criteria_revisions=revisions,
                    slice_criteria_heads=(SliceCriteriaHead(accepted.id, current_revision.id),),
                    slice_outcome_receipts=() if outcome is None else (outcome,),
                    criterion_test_receipts=evidence,
                )
                return _slice_acceptance_inventory(fixture).items[0]

            item = inventory()
            self.assertEqual(item.scope_status, "ACTIVE")
            self.assertEqual(item.criteria_revision, current_revision)
            self.assertEqual(item.historical_acceptance_status, "ACCEPTED_PREVIOUSLY")
            # Confirm the historical details exist before inspecting their receipts.
            history = item.historical_acceptance
            assert history is not None, "accepted history must include its supporting records"
            self.assertEqual(history.receipt.id, receipt.id)
            self.assertEqual(
                history.criteria_revision.id, prior_revision.id,
            )
            self.assertEqual(
                tuple(proof.criterion_id for proof in history.criterion_evidence),
                tuple(criterion.id for criterion in prior_revision.criteria),
            )
            self.assertEqual(
                tuple(proof.output_sha256 for proof in history.criterion_evidence),
                ("a" * 64,) * len(prior_revision.criteria),
            )

            self.assertEqual(
                inventory(replace(receipt, repository_id="another-repository"))
                .historical_acceptance_status,
                "UNAVAILABLE",
            )
            self.assertEqual(
                inventory(replace(receipt, criteria_revision_id="missing-revision"))
                .historical_acceptance_status,
                "UNAVAILABLE",
            )
            self.assertEqual(
                inventory(replace(receipt, slice_agreement_ref="another-slice"))
                .historical_acceptance_status,
                "NOT_RECORDED",
            )
            self.assertEqual(
                inventory(evidence=proofs[:-1]).historical_acceptance_status,
                "PARTIAL",
            )
            self.assertEqual(
                inventory(evidence=(
                    replace(proofs[0], criteria_revision_id="another-revision"),
                    *proofs[1:],
                )).historical_acceptance_status,
                "UNAVAILABLE",
            )
            self.assertEqual(
                inventory(None).historical_acceptance_status,
                "NOT_RECORDED",
            )

    def test_slice_acceptance_inventory_uses_exact_agreements_heads_and_provenance(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            initial = workflow.init()
            criteria = _spec()
            statement = slice_acceptance_statement(FEATURE, SLICE, TASK, CHECKPOINT, criteria)
            _ = workflow.accept_first_slice(
                FEATURE, SLICE, TASK, CHECKPOINT, _provenance(statement),
                expected_revision=ExpectedRevision(initial.revision), criteria=criteria,
            )
            state = CoreService.open(root).recover()
            feature, active_slice = state.agreements
            first_revision = state.slice_criteria_revisions[0]
            self.assertIsNotNone(state.active_context)
            active_context = state.active_context
            if active_context is None:
                self.fail("accepted Slice fixture must have active context")

            parked_slice = AgreementRecord(
                "agreement-slice-parked", "SLICE", active_slice.feature_id, active_slice.change_id,
                "Parked Slice", "accepted parked scope", "INTERACTIVE_TERMINAL", "tty:parked",
                active_slice.repository_id, "task-parked", "Parked Task", "Parked checkpoint",
            )
            queued_feature_id = FeatureId("feature-r4b-other")
            queued_change_id = ChangeId("change-r4b-other")
            queued_feature = AgreementRecord(
                "agreement-feature-r4b-other", "FEATURE", queued_feature_id, queued_change_id,
                "Other Feature", "accepted other feature", "INTERACTIVE_TERMINAL", "tty:feature",
                active_slice.repository_id,
            )
            queued_slice = AgreementRecord(
                "agreement-slice-r4b-other", "SLICE", queued_feature_id, queued_change_id,
                "Other Slice", "accepted other scope", "INTERACTIVE_TERMINAL", "tty:other",
                active_slice.repository_id, "task-other", "Other Task", "Other checkpoint",
            )
            orphan_slice = AgreementRecord(
                "agreement-slice-orphan", "SLICE", FeatureId("feature-orphan"), ChangeId("change-orphan"),
                "Orphan Slice", "unassociated scope", "INTERACTIVE_TERMINAL", "tty:orphan",
                active_slice.repository_id, "task-orphan", "Orphan Task", "Orphan checkpoint",
            )

            def criteria_for(reference: str, revision_id: str, result: str) -> SliceCriteriaRevision:
                return replace(
                    first_revision,
                    id=revision_id,
                    slice_agreement_ref=reference,
                    outcome=result,
                    criteria=(SliceCriterion(f"AC-{revision_id}", result, "fixture evidence"),),
                )

            parked_context = replace(
                active_context, focus="Parked Slice", feature_agreement_ref=feature.id,
                slice_agreement_ref=parked_slice.id, task_progress=None,
            )
            queued_context = replace(
                active_context, feature_id=queued_feature_id, change_id=queued_change_id,
                change_ids=(queued_change_id,), focus="Other Slice",
                feature_agreement_ref=queued_feature.id, slice_agreement_ref=queued_slice.id,
                task_progress=None,
            )
            fixture = replace(
                state,
                agreements=(
                    feature, active_slice, parked_slice, parked_slice,
                    queued_feature, queued_slice, orphan_slice,
                ),
                slice_criteria_revisions=(
                    first_revision,
                    criteria_for(parked_slice.id, "criteria-parked", "parked-specific criterion"),
                    criteria_for(queued_slice.id, "criteria-other", "other-feature-specific criterion"),
                ),
                slice_criteria_heads=(
                    state.slice_criteria_heads[0],
                    SliceCriteriaHead(parked_slice.id, "criteria-parked"),
                    SliceCriteriaHead(queued_slice.id, "criteria-other"),
                ),
                slice_outcome_receipts=(
                    SliceOutcomeReceipt(
                        "receipt-parked", active_slice.repository_id, parked_slice.id,
                        "criteria-parked", (), "fingerprint", "input-policy", "input-digest",
                        "admission-policy", "admission-digest", "drift-digest", "basis",
                        "CORE", 2, 3,
                    ),
                    SliceOutcomeReceipt(
                        "receipt-wrong-criteria", active_slice.repository_id, queued_slice.id,
                        "criteria-parked", (), "fingerprint", "input-policy", "input-digest",
                        "admission-policy", "admission-digest", "drift-digest", "basis",
                        "CORE", 2, 3,
                    ),
                ),
                parked_work=ParkedWork(
                    parked_context, None, None, None, "waiting", "fixture",
                ),
                work_items=(WorkItem(
                    WorkId(f"work-{queued_change_id}"), OperationalState.READY, queued_context,
                    None, None, None, "waiting", "fixture",
                ),),
            )

            inventory = _slice_acceptance_inventory(fixture)
            self.assertEqual(inventory.source, "CORE_AGREEMENTS")
            self.assertEqual(inventory.history_scope, "AGREEMENT_RECORDS_ONLY_NONEXHAUSTIVE")
            self.assertEqual(inventory.total, 4)
            self.assertFalse(inventory.complete)
            self.assertFalse(inventory.truncated)
            items = {item.slice_agreement_ref: item for item in inventory.items}
            self.assertEqual(len(items), 4, "duplicate exact Slice refs are deduplicated")
            self.assertEqual(items[active_slice.id].scope_status, "ACTIVE")
            self.assertEqual(items[active_slice.id].criteria_revision, first_revision)
            self.assertEqual(items[parked_slice.id].scope_status, "PARKED")
            self.assertEqual(items[parked_slice.id].provenance, "PARKED_CONTEXT")
            parked_revision = items[parked_slice.id].criteria_revision
            if parked_revision is None:
                self.fail("parked Slice must retain its canonical criteria revision")
            self.assertEqual(parked_revision.criteria[0].expected_result, "parked-specific criterion")
            self.assertTrue(items[parked_slice.id].historical_receipt)
            self.assertEqual(items[queued_slice.id].scope_status, "QUEUED")
            self.assertEqual(items[queued_slice.id].feature_id, str(queued_feature_id))
            self.assertEqual(items[queued_slice.id].operational_state, str(OperationalState.READY))
            queued_revision = items[queued_slice.id].criteria_revision
            if queued_revision is None:
                self.fail("queued Slice must retain its canonical criteria revision")
            self.assertEqual(queued_revision.criteria[0].expected_result, "other-feature-specific criterion")
            self.assertFalse(items[queued_slice.id].historical_receipt)
            self.assertEqual(items[orphan_slice.id].authority_status, "UNAVAILABLE")
            self.assertEqual(items[orphan_slice.id].scope_status, "UNAVAILABLE")
            self.assertEqual(items[orphan_slice.id].provenance, "ASSOCIATION_UNAVAILABLE")
            serialized = context_json(replace(
                project_context(workflow), slice_acceptance_inventory=inventory,
            ))["slice_acceptance_inventory"]
            if not isinstance(serialized, dict):
                self.fail("serialized inventory must be a JSON object")
            self.assertEqual(serialized.get("total"), 4)
            serialized_items_value = serialized.get("items")
            if not isinstance(serialized_items_value, list):
                self.fail("serialized inventory items must be a JSON array")
            serialized_items: list[dict[str, JsonValue]] = []
            for item in serialized_items_value:
                if not isinstance(item, dict):
                    self.fail("serialized inventory item must be a JSON object")
                serialized_items.append(item)
            parked_item = next(
                (item for item in serialized_items if item.get("slice_agreement_ref") == parked_slice.id),
                None,
            )
            if parked_item is None:
                self.fail("serialized inventory must include the parked Slice")
            parked_revision_value = parked_item.get("criteria_revision")
            if not isinstance(parked_revision_value, dict):
                self.fail("serialized parked Slice must include its criteria revision")
            parked_criteria_value = parked_revision_value.get("criteria")
            if not isinstance(parked_criteria_value, list) or not parked_criteria_value:
                self.fail("serialized parked Slice must include its criteria")
            parked_criterion_value = parked_criteria_value[0]
            if not isinstance(parked_criterion_value, dict):
                self.fail("serialized parked Slice criterion must be a JSON object")
            self.assertEqual(
                parked_criterion_value.get("expected_result"),
                "parked-specific criterion",
            )
            self.assertEqual(parked_item.get("scope_status"), "PARKED")

            extra_slices = tuple(
                AgreementRecord(
                    f"000-extra-{index:02}", "SLICE", active_slice.feature_id, active_slice.change_id,
                    f"Extra {index}", "accepted scope", "INTERACTIVE_TERMINAL", "tty:extra",
                    active_slice.repository_id, f"task-{index}", "Task", "Checkpoint",
                )
                for index in range(33)
            )
            capped = _slice_acceptance_inventory(replace(
                state, agreements=(feature, active_slice, *extra_slices),
            ))
            self.assertEqual(capped.total, 34)
            self.assertEqual(len(capped.items), 32)
            self.assertTrue(capped.truncated)
            self.assertFalse(capped.complete)
            self.assertIn(active_slice.id, {item.slice_agreement_ref for item in capped.items})

            invalid_head = _slice_acceptance_inventory(replace(
                state,
                slice_criteria_heads=state.slice_criteria_heads + (
                    SliceCriteriaHead(active_slice.id, "missing-current-head"),
                ),
            ))
            active_item = next(item for item in invalid_head.items if item.slice_agreement_ref == active_slice.id)
            self.assertEqual(active_item.criteria_status, "UNAVAILABLE")
            self.assertFalse(invalid_head.complete)

    def test_slice_acceptance_inventory_identifies_normal_parked_queue_work(self) -> None:
        for reason, expected_state in (
            ("deferred for later", OperationalState.FROZEN),
            ("external integration service is unavailable", OperationalState.BLOCKED),
        ):
            with self.subTest(reason=reason), TemporaryDirectory() as temporary:
                root = Path(temporary)
                workflow = WorkflowService.open(root)
                initial = workflow.init()
                criteria = _spec()
                statement = slice_acceptance_statement(FEATURE, SLICE, TASK, CHECKPOINT, criteria)
                _ = workflow.accept_first_slice(
                    FEATURE, SLICE, TASK, CHECKPOINT, _provenance(statement),
                    expected_revision=ExpectedRevision(initial.revision), criteria=criteria,
                )
                workflow.park(reason)

                item = _slice_acceptance_inventory(CoreService.open(root).recover()).items[0]

                self.assertEqual(item.scope_status, "PARKED")
                self.assertEqual(item.provenance, "PARKED_WORK_ITEM")
                self.assertEqual(item.operational_state, str(expected_state))

    def test_context_projects_active_lifecycle_decisions_progress_and_legal_actions(self) -> None:
        # Given: initialized work with durable implementation progress
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Add export capability")
            workflow.record_progress(
                "implement export", "delivery", ("research",), "write adapter", ("test adapter",), "run tests"
            )

            # When: an agent asks for its provider-neutral context
            context = project_context(workflow)

            # Then: it receives deterministic active work and legal next actions
            self.assertEqual(context.lifecycle, ("IN_PROGRESS", "IMPLEMENT"))
            self.assertEqual(context.progress.current_work, "write adapter")
            self.assertEqual(context.progress.pending_work, ("test adapter",))
            self.assertEqual(context.decision_state.unresolved_material_decision, False)
            self.assertEqual(context.side_work, "PRIMARY")
            self.assertEqual(
                context.legal_actions,
                ("context", "list_work", "next_work", "park", "progress", "assess_locks", "review", "reject", "authorize_irreversible", "propose_autonomy_expansion"),
            )
            self.assertFalse(context.stale)

    def test_recovered_context_projects_persisted_material_question_for_clarification(self) -> None:
        # Given: active work with one persisted material ambiguity
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            intake = workflow.new(
                "Add export delivery",
                assessment=IntentAssessment(
                    AssessmentOperation.NEW,
                    Classification.FEATURE,
                    AssessmentConfidence.HIGH,
                    CapabilityState.PLANNED,
                    "Export delivery",
                    ("Export delivery",),
                    None,
                    (AssessmentEvidence("test", RepositoryPath("tests/test_agent.py")),),
                    (MaterialDecision("delivery channel", ("email", "download")),),
                    AssessmentDepth(
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                    ),
                    "delivery channel requires a product decision",
                ),
            )

            # When: a fresh workflow instance projects authoritative context
            recovered = WorkflowService.open(root)
            context = project_context(recovered)
            ready = recovered.clarify(
                (ClarificationAnswer(context.questions[0].id, "email"),)
            )

            # Then: the exact persisted question remains actionable and resolves normally
            self.assertEqual(context.questions, intake.questions)
            self.assertEqual(ready.code, "READY")

    def test_uninitialized_context_only_advertises_initialization(self) -> None:
        with TemporaryDirectory() as temporary:
            context = project_context(WorkflowService.open(Path(temporary)))

            self.assertEqual(context.initialization_state, "UNINITIALIZED")
            self.assertEqual(context.legal_actions, ("init", "context"))
            self.assertEqual(context.guidance[0].action.value, "context")

    def test_initialized_inactive_context_is_distinct_from_uninitialized(self) -> None:
        # Given: initialized authority with no active lifecycle work
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()

            # When: context is projected
            context = project_context(workflow)

            # Then: initialization remains explicit despite a missing lifecycle pair
            self.assertIsNone(context.lifecycle)
            self.assertEqual(context.initialization_state, "INITIALIZED")
            self.assertEqual(
                context.legal_actions,
                ("context", "new", "change", "list_work", "next_work"),
            )

    def test_context_projects_bounded_work_queue_summary(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            started = workflow.new("Add export capability")
            workflow.record_progress("implement", "delivery", (), "write adapter", (), "run tests")
            workflow.park("waiting for the integration environment")

            context = project_context(workflow)

            if started.change_id is None:
                self.fail("expected parked change identity")
            self.assertEqual(context.work_queue.total, 1)
            self.assertTrue(context.work_queue.complete)
            self.assertEqual(context.work_queue.pending[0].change_id, started.change_id)
            self.assertEqual(context.work_queue.pending[0].lifecycle, ("IN_PROGRESS", "IMPLEMENT"))
            self.assertEqual(context.work_queue.frozen[0].reason, "waiting for the integration environment")
            self.assertEqual(context.work_queue.next_code, "NONE")
            self.assertIn("resume", context.legal_actions)
            self.assertNotIn("park", context.legal_actions)

    def test_queue_guidance_and_bounded_ambiguous_candidates_follow_core_eligibility(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            core = CoreService.open(root)
            for index in range(33):
                workflow.new("x" * (index + 1))
                workflow.park("deferred")
            state = core.recover()
            for item in state.work_items:
                state = core.set_work_state(state, item.id, OperationalState.READY)

            context = project_context(workflow)

            self.assertIn("list_work", context.legal_actions)
            self.assertIn("next_work", context.legal_actions)
            self.assertIn("activate_work", context.legal_actions)
            self.assertIn("set_work_state", context.legal_actions)
            self.assertNotIn("remove_work_relation", context.legal_actions)
            self.assertEqual(context.work_queue.next_code, "AMBIGUOUS")
            self.assertEqual(len(context.work_queue.next_candidates), 32)
            self.assertEqual(context.work_queue.next_candidates_total, 33)
            self.assertFalse(context.work_queue.next_candidates_complete)

    def test_verification_advertises_convergence_only_when_sufficient(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Add export capability")
            workflow.record_progress("implement", "delivery", (), "write adapter", (), "run tests")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            self.assertIn("verify", project_context(workflow).legal_actions)
            workflow.record_verification(VerificationEvidence(("tests",), False))

            insufficient = project_context(workflow)
            self.assertIn("verify", insufficient.legal_actions)
            self.assertNotIn("converge", insufficient.legal_actions)

            workflow.record_verification(VerificationEvidence(("tests",), True))
            self.assertIn("converge", project_context(workflow).legal_actions)

    def test_review_projection_uses_core_acceptability_predicate(self) -> None:
        review_cases = (
            (ReviewVerdict.BLOCKED, False, False),
            (ReviewVerdict.WARN, False, False),
            (ReviewVerdict.PASS, True, True),
        )
        for verdict, safe_to_proceed, expects_verification in review_cases:
            with self.subTest(verdict=verdict, safe_to_proceed=safe_to_proceed), TemporaryDirectory() as temporary:
                # Given: implementation-complete work with the supplied review outcome
                workflow = WorkflowService.open(Path(temporary))
                workflow.init()
                workflow.new("Add export capability")
                workflow.record_progress("implement", "delivery", (), "write adapter", (), "run tests")
                workflow.record_review(ReviewResult(verdict, (), safe_to_proceed))

                # When: the protocol context is projected after review
                legal_actions = project_context(workflow).legal_actions

                # Then: verification is advertised only for Core-acceptable reviews
                self.assertEqual("verify" in legal_actions, expects_verification)

    def test_convergence_projection_uses_core_convergence_predicate(self) -> None:
        # Given: a safe review and sufficient verification
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Add export capability")
            workflow.record_progress("implement", "delivery", (), "write adapter", (), "run tests")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))

            # When: verification-phase context is projected
            legal_actions = project_context(workflow).legal_actions

            # Then: convergence is legal under the same Core predicate
            self.assertIn("converge", legal_actions)

    def test_retired_historical_assessment_does_not_stale_remaining_active_lock(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            baseline = workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            if baseline.feature_id is None:
                self.fail("expected accepted feature")
            publication = CoreService.open(root).recover().publications[0]
            active = SemanticLock(LockId("lock-delivery"), FeatureId(baseline.feature_id), 1)
            retired = SemanticLock(LockId("lock-retention"), FeatureId(baseline.feature_id), 1)
            for lock, aspect in ((active, "delivery"), (retired, "retention")):
                workflow.create_lock(lock, SemanticLockRevision(lock.id, 1, publication, (aspect,), f"preserve {aspect}"))
            changed = workflow.change(
                "Change export capability",
                established_change_assessment(baseline.feature_id, "Export capability"),
            )
            if changed.change_id is None:
                self.fail("expected active change")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.assess_locks(tuple(
                LockAssessment(lock.id, 1, (aspect,), (), f"preserve {aspect}", False, False, AssessmentConfidence.HIGH, ("tests",), "unchanged")
                for lock, aspect in ((active, "delivery"), (retired, "retention"))
            ))
            workflow.record_lock_decision(Decision(DecisionId("retire-retention"), active.feature_id, "constraint", "retire retention", ChangeId(changed.change_id)))
            workflow.retire_lock(LockRelease(retired.id, 1, DecisionId("retire-retention")))

            self.assertFalse(project_context(workflow).stale)

    def test_context_marks_side_work_and_pending_intake(self) -> None:
        # Given: primary work with an active side request and deferred intake
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            core = CoreService.open(Path(temporary))
            primary = core.recover().active_context
            if primary is None:
                self.fail("expected active primary")
            side_feature = Feature(FeatureId("feature-side"), "side", LivingSpecId("spec-feature-side"), primary.feature_id)
            state = core.record_feature(core.recover(), side_feature)
            core.activate_side(state, Change(ChangeId("change-side"), side_feature.id, "unrelated: documentation", Classification.PATCH))

            # When: the side work is projected
            context = project_context(workflow)

            # Then: the agent receives explicit side-work and deferral signals
            self.assertEqual(context.side_work, "SIDE")
            self.assertTrue(context.side_work_active)
            self.assertFalse(context.pending_intake)
            guidance = next(item for item in context.guidance if item.action.value == "change")
            self.assertFalse(guidance.allowed)
            self.assertEqual(guidance.reason_code, "ACTIVE_WORK")

    def test_inactive_context_projects_only_accepted_capabilities_and_active_lock_metadata(self) -> None:
        # Given: accepted Product Truth, an active Feature-owned lock, and an unpublished Feature
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            accepted = workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            if accepted.feature_id is None:
                self.fail("expected accepted feature")
            publication = CoreService.open(root).recover().publications[0]
            workflow.create_lock(
                SemanticLock(LockId("lock-export"), FeatureId(accepted.feature_id), 1),
                SemanticLockRevision(
                    LockId("lock-export"), 1, publication, ("delivery",), "preserve delivery"
                ),
            )
            workflow.change(
                "Project-only capability",
                assessment=IntentAssessment(
                    AssessmentOperation.CHANGE,
                    Classification.PATCH,
                    AssessmentConfidence.HIGH,
                    CapabilityState.PROJECT_ESTABLISHED,
                    "Project-only capability",
                    ("Project-only capability",),
                    None,
                    (AssessmentEvidence("test", RepositoryPath("tests/test_agent.py")),),
                    (),
                    AssessmentDepth(
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                        AssessmentDepthValue.NORMAL,
                    ),
                    "project evidence is not accepted Product Truth",
                ),
            )
            workflow.reject()

            # When: a new agent recovers inactive workflow context
            context = project_context(WorkflowService.open(root))

            # Then: only accepted authority and lock metadata are available for an ESTABLISHED change
            self.assertIsNone(context.lifecycle)
            self.assertTrue(context.accepted_capabilities_complete)
            self.assertEqual(len(context.accepted_capabilities), 1)
            capability = context.accepted_capabilities[0]
            self.assertEqual(capability.feature_id, accepted.feature_id)
            self.assertEqual(capability.title, "Export capability")
            self.assertEqual(capability.capability, "Export capability")
            self.assertEqual(capability.living_spec_id, publication.living_spec_id)
            self.assertEqual(capability.publication_revision, publication.revision)
            self.assertEqual(capability.publication_path, str(publication.path))
            self.assertTrue(context.active_locks_complete)
            self.assertEqual(len(context.active_locks), 1)
            lock = context.active_locks[0]
            self.assertEqual(lock.owner_feature_id, accepted.feature_id)
            self.assertEqual(lock.lock_id, "lock-export")
            self.assertEqual(lock.lock_revision, 1)
            self.assertEqual(lock.baseline_living_spec_id, publication.living_spec_id)
            self.assertEqual(lock.baseline_revision, publication.revision)
            self.assertEqual(lock.baseline_path, str(publication.path))
            self.assertEqual(lock.protected_aspects, ("delivery",))

    def test_context_projects_external_sdd_structure_without_mutating_recovery(self) -> None:
        # Given: a fresh repository with supported foreign SDD structure
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".specify").mkdir()
            (root / "openspec" / "changes").mkdir(parents=True)
            WorkflowService.open(root).init()
            before = CoreService.open(root).recover()

            # When: a fresh agent projects context
            context = project_context(WorkflowService.open(root))

            # Then: bounded structural facts are visible without changing Core state
            self.assertEqual(
                tuple((item.framework, item.paths) for item in context.external_sdd_evidence),
                (("OPENSPEC", ("openspec/changes/",)), ("SPEC_KIT", (".specify/",))),
            )
            self.assertEqual(context.external_sdd_evidence_total, 2)
            self.assertTrue(context.external_sdd_evidence_complete)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_context_projects_core_owned_autonomy_policy_metrics_and_evaluation(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "delivery", (), "write", (), "test")

            context = project_context(workflow)

            self.assertIsNotNone(context.autonomy_envelope)
            if context.autonomy_envelope is None or context.autonomy_evaluation is None:
                self.fail("expected projected autonomy facts")
            self.assertEqual(context.autonomy_envelope.goal, "Export capability")
            self.assertEqual(context.autonomy_envelope.allowed_paths, ())
            self.assertEqual(context.autonomy_metrics.automatic_operations, 1)
            self.assertEqual(context.autonomy_evaluation.authorization, "ALLOWED")
