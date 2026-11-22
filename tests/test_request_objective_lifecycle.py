"""Core lifecycle for the exact, bounded SESSION_REQUEST heading objective."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import unittest

from evospec.domain import ExpectedRevision, OperationalState, validate_state
from evospec.persistence import REQUEST_OBJECTIVE_SCHEMA, SCHEMA
from evospec.request_scoped_objective import REQUEST_SCOPED_OBJECTIVE
from evospec.services import (
    CoreService,
    ServiceError,
    SessionRequestEvidence,
    StaleRevisionError,
)
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory


REQUEST = 'Create a static HTML page with the heading "Welcome".'
PAGE = b"<!doctype html><html><head></head><body><h1>Welcome</h1></body></html>\n"
SAME_OBJECTIVE_PAGE = (
    b"<!doctype html><html><head><title>Updated</title></head>"
    b"<body><h1>Welcome</h1></body></html>\n"
)


class RequestObjectiveLifecycleTests(unittest.TestCase):
    root: Path = Path()
    workflow: WorkflowService = WorkflowService.open(Path.cwd())
    evidence: SessionRequestEvidence = SessionRequestEvidence("", "", "", "")

    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="evospec-request-objective-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workflow = WorkflowService.open(self.root)
        self.workflow.init()
        repository_id = sha256(str(self.root).encode("utf-8")).hexdigest()[:16]
        self.evidence = SessionRequestEvidence(
            "ses_test1234", "msg_test1234", REQUEST, repository_id,
        )
        self.workflow.accept_first_slice_session(
            self.evidence,
            expected_revision=ExpectedRevision(CoreService.open(self.root).recover().revision),
        )

    def _state_snapshot(self):
        core = CoreService.open(self.root)
        return core.input_snapshot(core.recover().revision)

    def _write_page(self, content: bytes = PAGE) -> None:
        (self.root / "index.html").write_bytes(content)

    def _schema(self) -> int:
        return json.loads((self.root / ".evospec" / "state.json").read_text())["schema"]

    def _request(self, evidence: SessionRequestEvidence | None = None):
        workflow = WorkflowService.open(self.root)
        state, snapshot = self._state_snapshot()
        return workflow.request_scoped_objective(
            self.evidence if evidence is None else evidence,
            snapshot.fingerprint,
            expected_revision=ExpectedRevision(state.revision),
        )

    def test_staged_verification_completion_recovery_and_replay(self) -> None:
        self._write_page()
        workflow = WorkflowService.open(self.root)
        before, snapshot = self._state_snapshot()
        status = workflow.request_scoped_objective_status(before, snapshot)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.status, "PENDING")
        self.assertEqual(self._schema(), SCHEMA, "read-only status must not select schema 20")

        first = workflow.request_scoped_objective(
            self.evidence,
            snapshot.fingerprint,
            expected_revision=ExpectedRevision(before.revision),
        )
        self.assertEqual(first.code, "REQUEST_OBJECTIVE_VERIFIED")
        self.assertEqual(first.revision, before.revision + 1)
        verified = CoreService.open(self.root).recover()
        validate_state(verified)
        self.assertEqual(self._schema(), REQUEST_OBJECTIVE_SCHEMA)
        self.assertIsNone(verified.r3_schema_upgrade)
        self.assertIsNone(verified.r3b_schema_upgrade)
        self.assertEqual(len(verified.slice_criteria_revisions), 1)
        self.assertEqual(verified.slice_criteria_revisions[0].source, REQUEST_SCOPED_OBJECTIVE)
        self.assertEqual(len(verified.criterion_test_receipts), 1)
        first_proof = verified.criterion_test_receipts[0]
        self.assertEqual(first_proof.output_sha256, sha256(PAGE).hexdigest())
        self.assertEqual(first_proof.source_revision, before.revision)
        self.assertEqual(first_proof.recorded_revision, verified.revision)
        self.assertEqual(first_proof.input_fingerprint_before, snapshot.fingerprint)
        self.assertEqual(first_proof.input_fingerprint_after, snapshot.fingerprint)
        self.assertEqual(len(verified.slice_outcome_receipts), 1)
        first_outcome = verified.slice_outcome_receipts[0]
        self.assertEqual(first_outcome.source, REQUEST_SCOPED_OBJECTIVE)
        self.assertEqual(first_outcome.evidence_receipt_ids, (first_proof.id,))
        self.assertEqual(first_outcome.input_fingerprint, snapshot.fingerprint)
        self.assertFalse(verified.feature_completion_receipts)
        self.assertFalse(verified.terminal_archives)
        self.assertFalse(verified.publications)
        self.assertIsNotNone(verified.active_context)
        assert verified.active_context is not None
        self.assertIsNone(verified.active_context.review)

        status = WorkflowService.open(self.root).request_scoped_objective_status(
            verified, self._state_snapshot()[1],
        )
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.status, "VERIFIED")

        # A changed file with the same visible heading invalidates the old proof.
        self._write_page(SAME_OBJECTIVE_PAGE)
        stale_status = WorkflowService.open(self.root).request_scoped_objective_status(
            verified, self._state_snapshot()[1],
        )
        self.assertIsNotNone(stale_status)
        assert stale_status is not None
        self.assertEqual(stale_status.status, "PENDING")
        self.assertIn("REQUEST_OBJECTIVE_PROOF_STALE", stale_status.blockers)
        refreshed = self._request()
        self.assertEqual(refreshed.code, "REQUEST_OBJECTIVE_VERIFIED")
        self.assertEqual(refreshed.revision, verified.revision + 1)
        verified_again = CoreService.open(self.root).recover()
        self.assertEqual(len(verified_again.criterion_test_receipts), 2)
        self.assertEqual(len(verified_again.slice_outcome_receipts), 2)
        self.assertEqual(verified_again.criterion_test_receipts[0], first_proof)
        latest_proof = verified_again.criterion_test_receipts[-1]
        latest_outcome = verified_again.slice_outcome_receipts[-1]
        self.assertEqual(latest_proof.output_sha256, sha256(SAME_OBJECTIVE_PAGE).hexdigest())
        self.assertNotEqual(latest_proof.input_fingerprint_before, first_proof.input_fingerprint_before)
        self.assertEqual(latest_proof.source_revision, verified.revision)
        self.assertEqual(latest_proof.recorded_revision, verified_again.revision)
        self.assertEqual(latest_outcome.evidence_receipt_ids, (latest_proof.id,))
        self.assertEqual(latest_outcome.input_fingerprint, latest_proof.input_fingerprint_after)

        # Re-open Core between stages to prove the recovered proof is the basis.
        completion_workflow = WorkflowService.open(self.root)
        completion_state, completion_snapshot = self._state_snapshot()
        complete = completion_workflow.request_scoped_objective(
            self.evidence,
            completion_snapshot.fingerprint,
            expected_revision=ExpectedRevision(completion_state.revision),
        )
        self.assertEqual(complete.code, "REQUEST_OBJECTIVE_COMPLETE")
        self.assertEqual(complete.revision, completion_state.revision + 1)
        closed = CoreService.open(self.root).recover()
        validate_state(closed)
        self.assertEqual(len(closed.feature_completion_receipts), 1)
        closure = closed.feature_completion_receipts[0]
        self.assertEqual(closure.source, REQUEST_SCOPED_OBJECTIVE)
        self.assertEqual(closure.source_reference, self.evidence.source_reference)
        self.assertEqual(closure.feature_id, closed.agreements[0].feature_id)
        self.assertEqual(closure.source_revision, completion_state.revision)
        self.assertEqual(closure.recorded_revision, closed.revision)
        self.assertEqual(closure.slice_outcome_receipt_ids, (latest_outcome.id,))
        self.assertEqual(closure.input_fingerprint, completion_snapshot.fingerprint)
        self.assertEqual(len(closed.terminal_archives), 1)
        archived = closed.terminal_archives[0].context
        self.assertEqual(archived.phase.value, "DONE")
        self.assertEqual(archived.status.value, "ACCEPTED")
        self.assertIsNotNone(archived.review)
        assert archived.review is not None
        self.assertEqual(archived.review.verdict.value, "PASS")
        self.assertTrue(archived.verification.sufficient)
        self.assertTrue(archived.convergence_complete)
        self.assertEqual(len(closed.work_items), 1)
        self.assertIs(closed.work_items[0].operational_state, OperationalState.DONE)
        self.assertFalse(closed.publications)
        self.assertFalse(closed.publication_history)

        status = WorkflowService.open(self.root).request_scoped_objective_status(
            closed, self._state_snapshot()[1],
        )
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.status, "COMPLETE")
        reread, reread_snapshot = self._state_snapshot()
        replay = WorkflowService.open(self.root).request_scoped_objective(
            self.evidence,
            reread_snapshot.fingerprint,
            expected_revision=ExpectedRevision(reread.revision),
        )
        self.assertEqual(replay.code, "REQUEST_OBJECTIVE_ALREADY_COMPLETE")
        self.assertEqual(replay.revision, closed.revision)
        self.assertEqual(CoreService.open(self.root).recover(), closed)

    def test_missing_false_or_fabricated_fingerprint_never_writes(self) -> None:
        state, snapshot = self._state_snapshot()
        with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_ARTIFACT_MISSING"):
            WorkflowService.open(self.root).request_scoped_objective(
                self.evidence, snapshot.fingerprint,
                expected_revision=ExpectedRevision(state.revision),
            )
        self.assertEqual(CoreService.open(self.root).recover(), state)
        self.assertEqual(self._schema(), SCHEMA)

        self._write_page(PAGE.replace(b"Welcome", b"Not Welcome"))
        state, snapshot = self._state_snapshot()
        with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED"):
            WorkflowService.open(self.root).request_scoped_objective(
                self.evidence, snapshot.fingerprint,
                expected_revision=ExpectedRevision(state.revision),
            )
        self.assertEqual(CoreService.open(self.root).recover(), state)
        self.assertEqual(self._schema(), SCHEMA)

        self._write_page()
        state, snapshot = self._state_snapshot()
        with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_FINGERPRINT_STALE"):
            WorkflowService.open(self.root).request_scoped_objective(
                self.evidence, "0" * 64,
                expected_revision=ExpectedRevision(state.revision),
            )
        self.assertEqual(CoreService.open(self.root).recover(), state)
        self.assertEqual(self._schema(), SCHEMA)

    def test_wrong_request_root_stale_revision_and_ambiguous_state_fail_closed(self) -> None:
        self._write_page()
        state, snapshot = self._state_snapshot()
        workflow = WorkflowService.open(self.root)
        forged_snapshot = replace(
            snapshot,
            file_sha256=(("index.html", "0" * 64),),
        )
        projected = workflow.request_scoped_objective_status(state, forged_snapshot)
        self.assertIsNotNone(projected)
        assert projected is not None
        self.assertEqual(projected.status, "BLOCKED")
        self.assertIn("REQUEST_OBJECTIVE_INPUT_SNAPSHOT_INVALID", projected.blockers)
        self.assertEqual(CoreService.open(self.root).recover(), state)

        invalid_evidence = (
            replace(self.evidence, session_id="ses_other1234"),
            replace(self.evidence, message_id="msg_other1234"),
            replace(self.evidence, statement=REQUEST + " "),
            replace(self.evidence, repository_id="wrong-root"),
        )
        for evidence in invalid_evidence:
            with self.subTest(evidence=evidence):
                with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_SESSION_MISMATCH"):
                    workflow.request_scoped_objective(
                        evidence, snapshot.fingerprint,
                        expected_revision=ExpectedRevision(state.revision),
                    )
                self.assertEqual(CoreService.open(self.root).recover(), state)

        with self.assertRaises(StaleRevisionError):
            workflow.request_scoped_objective(
                self.evidence, snapshot.fingerprint,
                expected_revision=ExpectedRevision(state.revision - 1),
            )
        self.assertEqual(CoreService.open(self.root).recover(), state)

        core = CoreService.open(self.root)
        feature, accepted = state.agreements
        ambiguous_slice = replace(accepted, id=accepted.id + "-ambiguous")
        ambiguous = replace(state, agreements=(feature, accepted, ambiguous_slice))
        self.assertEqual(
            core._request_objective_pair(ambiguous)[2],
            ("REQUEST_OBJECTIVE_AMBIGUOUS_SESSION_REQUESTS",),
        )
        with self.assertRaises(ServiceError):
            core.request_scoped_objective(ambiguous, self.evidence, snapshot.fingerprint)
        self.assertEqual(CoreService.open(self.root).recover(), state)
        self.assertEqual(self._schema(), SCHEMA)

    def test_closed_receipt_is_not_current_after_file_change(self) -> None:
        self._write_page()
        self._request()
        self._request()
        closed = CoreService.open(self.root).recover()
        self.assertEqual(len(closed.feature_completion_receipts), 1)

        self._write_page(SAME_OBJECTIVE_PAGE)
        changed_state, changed_snapshot = self._state_snapshot()
        with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_PROOF_STALE"):
            WorkflowService.open(self.root).request_scoped_objective(
                self.evidence,
                changed_snapshot.fingerprint,
                expected_revision=ExpectedRevision(changed_state.revision),
            )
        self.assertEqual(CoreService.open(self.root).recover(), closed)

        stale_projection = WorkflowService.open(self.root).request_scoped_objective_status(
            changed_state, changed_snapshot,
        )
        self.assertIsNotNone(stale_projection)
        assert stale_projection is not None
        self.assertEqual(stale_projection.status, "BLOCKED")
        self.assertIn("REQUEST_OBJECTIVE_PROOF_STALE", stale_projection.blockers)


if __name__ == "__main__":
    unittest.main()
