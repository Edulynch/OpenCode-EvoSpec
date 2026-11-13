from __future__ import annotations

import json
import unittest
from pathlib import Path

from evospec.domain import (
    AssessmentConfidence,
    Decision,
    DecisionId,
    ChangeId,
    ExpectedRevision,
    FeatureId,
    LockAssessment,
    LockAuthorization,
    LockId,
    OperationalState,
    ReviewResult,
    SemanticLock,
    SemanticLockRevision,
    VerificationEvidence,
    WorkRelation,
    WorkRelationKind,
)
from evospec.lifecycle import ReviewVerdict
from evospec.persistence import RecoveryError
from evospec.services import CoreService, ServiceError
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_persistence_atomic import FailingReplace
from tests.test_persistence import _legacy_before_e1
from tests.test_services_state import convergence_assessment
from tests.test_workflow_assurance import established_change_assessment


class WorkQueueTests(unittest.TestCase):
    def _ready_locked_work(self, root: Path, case: str):
        workflow = WorkflowService.open(root)
        workflow.init()
        baseline = workflow.new("Lock-protected capability")
        workflow.record_progress("implement", "lock", (), "work", (), "review")
        workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
        workflow.record_verification(VerificationEvidence(("tests",), True))
        workflow.converge(convergence_assessment(workflow.status().change_id, True))
        if baseline.feature_id is None:
            self.fail("expected accepted Feature identity")
        publication = CoreService.open(root).recover().publications[0]
        lock = SemanticLock(LockId("lock-queue"), FeatureId(baseline.feature_id), 1)
        workflow.create_lock(
            lock,
            SemanticLockRevision(lock.id, 1, publication, ("delivery",), "preserve delivery"),
        )
        changed = workflow.change(
            "Change lock-protected capability",
            established_change_assessment(baseline.feature_id, "Lock-protected capability"),
        )
        if changed.change_id is None:
            self.fail("expected active Change identity")
        change_id = ChangeId(changed.change_id)
        workflow.record_progress("implement", "lock", (), "work", (), "review")
        assessment = LockAssessment(
            lock.id,
            1,
            ("delivery",),
            ("delivery",) if case in ("conflict", "unauthorized") else (),
            "preserve delivery",
            case in ("conflict", "unauthorized"),
            case == "evasion",
            AssessmentConfidence.HIGH,
            ("tests",),
            case,
        )
        workflow.assess_locks((assessment,))
        if case == "stale":
            decision = Decision(
                DecisionId("decision-revise"), lock.feature_id, "constraint", "revise lock", change_id,
            )
            workflow.record_lock_decision(decision)
            workflow.revise_lock(
                SemanticLockRevision(lock.id, 2, publication, ("delivery",), "refresh lock", decision.id)
            )
        if case == "unauthorized":
            decision = Decision(
                DecisionId("decision-authorize"), lock.feature_id, "constraint", "authorize conflict", change_id,
            )
            workflow.record_lock_decision(decision)
            workflow.authorize_lock(
                LockAuthorization(lock.id, 1, lock.feature_id, change_id, decision.id, ("delivery",))
            )
            workflow.assess_locks((assessment,))
        workflow.park("deferred")
        item = next(
            item for item in CoreService.open(root).recover().work_items
            if item.context is not None and item.context.change_id == change_id
        )
        CoreService.open(root).set_work_state(
            CoreService.open(root).recover(), item.id, OperationalState.READY,
        )
        return workflow, item.id, root

    def _assert_non_actionable_activation(self, root: Path, workflow: WorkflowService, work_id: str) -> None:
        before = CoreService.open(root).recover()
        expected = ExpectedRevision(before.revision)
        self.assertEqual(workflow.next_work(expected_revision=expected).code, "NONE")
        with self.assertRaises(WorkflowError):
            workflow.activate_work(work_id, expected_revision=expected)
        self.assertEqual(CoreService.open(root).recover(), before)

    def test_schema_eight_parked_snapshot_migrates_to_blocked_without_loss(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Queued capability")
            workflow.record_progress("implement", "queue", (), "work", (), "resume")
            workflow.park("external verification service is unavailable")
            state_path = root / ".evospec" / "state.json"
            schema_nine = _legacy_before_e1(json.loads(state_path.read_text(encoding="utf-8")))
            item = schema_nine["work_items"][0]
            schema_eight = dict(schema_nine)
            schema_eight["schema"] = 8
            schema_eight.pop("irreversible_authorizations", None)
            schema_eight.pop("pending_autonomy_expansion", None)
            schema_eight.pop("autonomy_expansion_history", None)
            for field in ("active_work_id", "preserved_primary_work_id", "work_items", "work_relations"):
                schema_eight.pop(field)
            schema_eight["parked_work"] = {
                "context": item["context"],
                "preserved_primary_context": item["preserved_primary_context"],
                "deferred_side_request": item["deferred_side_request"],
                "pending_intake": item["pending_intake"],
                "reason": item["reason"],
                "parked_at": item["transitioned_at"],
            }
            state_path.write_text(json.dumps(schema_eight), encoding="utf-8")

            recovered = CoreService.open(root).recover()

            self.assertEqual(len(recovered.work_items), 1)
            migrated = recovered.work_items[0]
            if migrated.context is None:
                self.fail("migration must retain parked lifecycle context")
            self.assertEqual(migrated.operational_state, OperationalState.BLOCKED)
            self.assertEqual(migrated.context.feature_id, item["context"]["feature_id"])
            self.assertEqual(migrated.context.review, None)
            self.assertEqual(migrated.reason, item["reason"])
            self.assertEqual(migrated.transitioned_at, item["transitioned_at"])

    def test_next_actionable_never_prioritizes_equal_ready_items(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("First capability")
            workflow.park("deferred")
            state = CoreService.open(root).recover()
            first = state.work_items[0]
            state = CoreService.open(root).set_work_state(state, first.id, OperationalState.READY)
            workflow.new("Second capability")
            workflow.park("deferred")
            state = CoreService.open(root).recover()
            second = next(item for item in state.work_items if item.id != first.id)
            state = CoreService.open(root).set_work_state(state, second.id, OperationalState.READY)

            projection = CoreService.open(root).next_actionable(state)

            self.assertEqual(projection.code, "AMBIGUOUS")
            self.assertEqual(set(projection.candidates), {first.id, second.id})

    def test_hard_relation_blocks_ready_activation_but_soft_does_not(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Predecessor capability")
            workflow.park("deferred")
            first = CoreService.open(root).recover().work_items[0]
            state = CoreService.open(root).set_work_state(CoreService.open(root).recover(), first.id, OperationalState.READY)
            workflow.new("Successor capability")
            workflow.park("deferred")
            state = CoreService.open(root).recover()
            second = next(item for item in state.work_items if item.id != first.id)
            state = CoreService.open(root).set_work_state(state, second.id, OperationalState.READY)
            state = CoreService.open(root).add_work_relation(state, WorkRelation(first.id, second.id, WorkRelationKind.HARD))

            projection = CoreService.open(root).next_actionable(state)

            self.assertEqual(projection.code, "SELECTED")
            self.assertEqual(projection.work_id, first.id)

    def test_strict_activation_rejects_each_non_ready_operational_state_without_authority_change(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Deferred capability")
            workflow.park("deferred")
            item = CoreService.open(root).recover().work_items[0]
            for operational_state in (
                OperationalState.BLOCKED,
                OperationalState.FROZEN,
                OperationalState.WAITING,
            ):
                with self.subTest(operational_state=operational_state):
                    state = CoreService.open(root).set_work_state(
                        CoreService.open(root).recover(), item.id, operational_state,
                    )
                    expected = ExpectedRevision(state.revision)
                    self.assertEqual(workflow.next_work(expected_revision=expected).code, "NONE")
                    with self.assertRaises(WorkflowError):
                        workflow.activate_work(str(item.id), expected_revision=expected)
                    self.assertEqual(CoreService.open(root).recover(), state)

            CoreService.open(root).set_work_state(
                CoreService.open(root).recover(), item.id, OperationalState.FROZEN,
            )
            workflow.resume(str(item.id))
            workflow.record_progress("implement", "deferred", (), "work", (), "review")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, False))
            done = CoreService.open(root).recover().work_items[0]
            expected = ExpectedRevision(CoreService.open(root).recover().revision)
            self.assertEqual(done.operational_state, OperationalState.DONE)
            self.assertEqual(workflow.next_work(expected_revision=expected).code, "NONE")
            with self.assertRaises(WorkflowError):
                workflow.activate_work(str(done.id), expected_revision=expected)

    def test_locked_work_revalidation_keeps_next_and_activation_aligned(self) -> None:
        for case in ("stale", "evasion", "conflict", "unauthorized"):
            with self.subTest(case=case), TemporaryDirectory() as temporary:
                root = Path(temporary)
                workflow, work_id, root = self._ready_locked_work(root, case)
                self._assert_non_actionable_activation(root, workflow, str(work_id))

    def test_rejecting_active_work_detaches_its_relations_and_recovers(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Deferred predecessor")
            workflow.park("deferred")
            predecessor = CoreService.open(root).recover().work_items[0]
            workflow.new("Rejected successor")
            state = CoreService.open(root).recover()
            active_work_id = state.active_work_id
            if active_work_id is None:
                self.fail("expected active successor work identity")
            CoreService.open(root).add_work_relation(
                state,
                WorkRelation(predecessor.id, active_work_id, WorkRelationKind.HARD),
            )

            rejected = workflow.reject()
            recovered = CoreService.open(root).recover()

            self.assertEqual(rejected.code, "REJECTED")
            self.assertIsNone(recovered.active_context)
            self.assertEqual(recovered.work_relations, ())
            self.assertEqual(recovered.work_items, (predecessor,))

    def test_schema_nine_rejects_noncanonical_active_and_done_work_identities(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Canonical work")
            state_path = root / ".evospec" / "state.json"
            active = json.loads(state_path.read_text(encoding="utf-8"))
            active["active_work_id"] = "work-arbitrary"
            state_path.write_text(json.dumps(active), encoding="utf-8")

            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()

            active["active_work_id"] = f"work-{active['active_context']['change_id']}"
            state_path.write_text(json.dumps(active), encoding="utf-8")
            workflow.record_progress("implement", "canonical", (), "work", (), "review")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, False))
            accepted = json.loads(state_path.read_text(encoding="utf-8"))

            for duplicate in (False, True):
                with self.subTest(duplicate=duplicate):
                    payload = json.loads(json.dumps(accepted))
                    altered = dict(payload["work_items"][0])
                    altered["id"] = "work-arbitrary"
                    if duplicate:
                        payload["work_items"].append(altered)
                    else:
                        payload["work_items"][0] = altered
                    state_path.write_text(json.dumps(payload), encoding="utf-8")

                    with self.assertRaises(RecoveryError):
                        CoreService.open(root).recover()

    def test_queue_publication_atomic_failures_become_workflow_errors_without_authority_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("First queued capability")
            workflow.park("deferred")
            state = CoreService.open(root).recover()
            first = state.work_items[0]
            failing = CoreService.open(root, FailingReplace())

            with self.assertRaises(WorkflowError) as state_failure:
                WorkflowService(root, failing).set_work_state(
                    str(first.id), OperationalState.READY, None,
                    expected_revision=ExpectedRevision(state.revision),
                )
            self.assertEqual(state_failure.exception.__cause__.__class__.__name__, "ServiceError")
            self.assertEqual(CoreService.open(root).recover(), state)

            state = CoreService.open(root).set_work_state(state, first.id, OperationalState.READY)
            with self.assertRaises(WorkflowError) as activation_failure:
                WorkflowService(root, failing).activate_work(
                    str(first.id), expected_revision=ExpectedRevision(state.revision)
                )
            self.assertIsInstance(activation_failure.exception.__cause__, ServiceError)
            self.assertEqual(CoreService.open(root).recover(), state)

            workflow.new("Second queued capability")
            workflow.park("deferred")
            state = CoreService.open(root).recover()
            second = next(item for item in state.work_items if item.id != first.id)
            relation = WorkRelation(first.id, second.id, WorkRelationKind.HARD)
            with self.assertRaises(WorkflowError) as addition_failure:
                WorkflowService(root, failing).add_work_relation(
                    str(first.id), str(second.id), str(relation.kind),
                    expected_revision=ExpectedRevision(state.revision),
                )
            self.assertIsInstance(addition_failure.exception.__cause__, ServiceError)
            self.assertEqual(CoreService.open(root).recover(), state)

            state = CoreService.open(root).add_work_relation(state, relation)
            with self.assertRaises(WorkflowError) as removal_failure:
                WorkflowService(root, failing).remove_work_relation(
                    str(first.id), str(second.id), str(relation.kind),
                    expected_revision=ExpectedRevision(state.revision),
                )
            self.assertIsInstance(removal_failure.exception.__cause__, ServiceError)
            self.assertEqual(CoreService.open(root).recover(), state)
