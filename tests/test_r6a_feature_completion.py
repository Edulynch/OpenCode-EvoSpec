"""R6-A: a Feature closes only after its own human decision and fresh Slice evidence."""

from __future__ import annotations

import io
import json
import shutil
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from evospec.agent_protocol import execute
from evospec.cli import main as cli_main
from evospec.domain import (
    ExpectedRevision, ReviewResult, StateValidationError, VerificationEvidence,
    feature_completion_semantic_id, validate_state,
)
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.persistence import FileOperations, RecoveryError
from evospec.services import CoreService, DurabilityUncertainError, ServiceError, StaleRevisionError, UserAcceptance
from evospec.workflow import WorkflowError, WorkflowService
from tests import test_e1_r5c_second_slice_outcome as r5c
from tests.test_services_state import convergence_assessment


CONDITION = "Both saved tasks and task creation meet the accepted Feature result"


class FailCompletionReplace(FileOperations):
    def replace(self, source: Path, target: Path) -> None:
        # A failed state replacement must leave the previous authority recoverable.
        if target.name == "state.json":
            raise OSError("injected R6-A precommit failure")
        super().replace(source, target)


class FailCompletionDirectorySync(FileOperations):
    def fsync_directory(self, directory: Path) -> None:
        # A post-replacement failure is uncertain, not proof that commit rolled back.
        if directory.name == ".evospec":
            raise OSError("injected R6-A durability uncertainty")
        super().fsync_directory(directory)


class TerminalBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class R6AFeatureCompletionTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.fixture = r5c.R5CSecondSliceOutcomeTests()
        self.root = Path()
        self.workflow = WorkflowService.open(Path.cwd())
        self.core = CoreService.open(Path.cwd())

    def setUp(self) -> None:
        # Reuse R5-C's real two-Slice, schema-17, host-reported test fixture.
        self.fixture = r5c.R5CSecondSliceOutcomeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.workflow = self.fixture.workflow
        self.core = CoreService.open(self.root)

    def _accept_outcome(self) -> None:
        for criterion_id in ("AC-1", "AC-2"):
            result = self.fixture.record_r3b_result(criterion_id)
            self.assertEqual(result["result"]["code"], "CRITERION_TEST_RECORDED")
        current = self.fixture.context()
        result = self.fixture.send({
            "action": "accept_slice_outcome",
            "expected_revision": current["revision"],
            "input_snapshot": current["input_snapshot"],
        })
        self.assertEqual(result["result"]["code"], "SLICE_OUTCOME_ACCEPTED")

    def _archive_current_slice(self) -> None:
        # Slice acceptance is deliberately independent of the terminal lifecycle.
        before = self.core.recover()
        active = before.active_context
        self.assertIsNotNone(active)
        assert active is not None
        # E1 guards its Task ledger, so only the normal lifecycle actions may
        # advance a verified Slice into its independent terminal archive.
        state = self.core.advance_lifecycle(before, LifecycleState.READY, Phase.READY)
        state = self.core.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT)
        state = self.core.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.REVIEW)
        state = self.core.record_review(
            state, ReviewResult(ReviewVerdict.PASS, (), True), active.checkpoints,
        )
        state = self.core.advance_lifecycle(state, LifecycleState.VERIFYING, Phase.VERIFY)
        state = self.core.record_verification(state, VerificationEvidence(("tests",), True))
        state = self.core.advance_lifecycle(state, LifecycleState.VERIFYING, Phase.CONVERGE)
        converged = self.core.confirm_convergence(state)
        archived = self.core.accept_convergence_without_publication(
            converged, convergence_assessment(active.change_id, False),
        )
        self.assertIsNone(archived.active_context)
        self.assertEqual(archived.terminal_archives[-1].context.status, LifecycleState.ACCEPTED)

    def _ready_one_slice(self) -> None:
        self._accept_outcome()
        self._archive_current_slice()

    def _ready_two_slices(self) -> None:
        self._ready_one_slice()
        self.fixture.accept_b()
        self.fixture.renew_b_coverage()
        self._accept_outcome()
        self._archive_current_slice()

    def _statement(self, condition: str = CONDITION) -> str:
        state = self.core.recover()
        return self.workflow.feature_completion_agreement(
            self.fixture.feature_id, condition,
            expected_revision=ExpectedRevision(state.revision),
        )

    def _close(self, condition: str = CONDITION) -> None:
        state = self.core.recover()
        statement = self._statement(condition)
        result = self.workflow.complete_feature(
            self.fixture.feature_id, condition, r5c._consent(statement),
            expected_revision=ExpectedRevision(state.revision),
        )
        self.assertEqual(result.code, "FEATURE_COMPLETED")

    def test_01_multi_slice_closure_is_atomic_durable_and_read_only_after_restart(self) -> None:
        self._ready_two_slices()
        before = self.core.recover()
        agreements, outcomes = before.agreements, before.slice_outcome_receipts
        self.assertEqual(len(outcomes), 2)
        statement = self._statement()
        self.assertIn("Schema conversion: 17 to 18", statement)
        self.assertIn(outcomes[0].id, statement)
        self.assertIn(outcomes[1].id, statement)

        self._close()
        after = CoreService.open(self.root).recover()
        self.assertEqual(after.revision, before.revision + 1)
        self.assertEqual(after.agreements, agreements)
        self.assertEqual(after.slice_outcome_receipts, outcomes)
        self.assertEqual(after.publications, before.publications)
        self.assertEqual(len(after.feature_completion_receipts), 1)
        receipt = after.feature_completion_receipts[0]
        self.assertEqual(receipt.condition, CONDITION)
        self.assertEqual(set(receipt.slice_outcome_receipt_ids), {item.id for item in outcomes})
        self.assertEqual(json.loads((self.root / ".evospec/state.json").read_text())["schema"], 18)

        # Reads in a new process-facing adapter must not change authority bytes.
        state_bytes = (self.root / ".evospec/state.json").read_bytes()
        context = json.loads(execute(self.root, '{"action":"context"}'))["context"]
        output = io.StringIO()
        self.assertEqual(cli_main(["--project", str(self.root), "status"], output), 0)
        self.assertEqual(context["feature_completion"]["status"], "COMPLETE")
        self.assertEqual(context["feature_completion"]["receipt_id"], receipt.id)
        self.assertIn("Feature completion: COMPLETE", output.getvalue())
        self.assertEqual((self.root / ".evospec/state.json").read_bytes(), state_bytes)

        retry = self.workflow.confirmed_feature_completion(
            self.fixture.feature_id, CONDITION,
            expected_revision=ExpectedRevision(before.revision),
        )
        self.assertIsNotNone(retry)
        assert retry is not None
        self.assertEqual(retry.code, "ALREADY_COMPLETED")
        self.assertEqual(self.core.recover().revision, after.revision)

    def test_02_parked_or_active_work_never_autocompletes(self) -> None:
        self._accept_outcome()
        current = self.core.recover()
        with self.assertRaises(WorkflowError) as active:
            self._statement()
        self.assertIn("FEATURE_WORK_NOT_TERMINAL", str(active.exception))
        self.workflow.park("waiting for explicit resume")
        with self.assertRaises(WorkflowError) as parked:
            self._statement()
        self.assertIn("FEATURE_QUEUE_NOT_TERMINAL", str(parked.exception))
        self.assertEqual(self.core.recover().feature_completion_receipts, ())
        self.assertGreater(self.core.recover().revision, current.revision)

    def test_03_stale_evidence_and_wrong_tty_fail_without_writes(self) -> None:
        self._ready_one_slice()
        before = self.core.recover()
        statement = self._statement()
        with self.assertRaises(WorkflowError) as wrong:
            self.workflow.complete_feature(
                self.fixture.feature_id, CONDITION,
                UserAcceptance(statement, "tty:incorrect"),
                expected_revision=ExpectedRevision(before.revision),
            )
        self.assertIn("exact human terminal", str(wrong.exception))
        (self.root / "tests/new-test-file.txt").write_text("different root", encoding="utf-8")
        with self.assertRaises(WorkflowError) as stale:
            self._statement()
        self.assertIn("COVERAGE_APPROVAL_REQUIRED", str(stale.exception))
        self.assertEqual(self.core.recover().revision, before.revision)

    def test_04_failure_to_replace_state_preserves_prior_authority(self) -> None:
        self._ready_one_slice()
        before = self.core.recover()
        _, snapshot = self.workflow.input_snapshot(expected_revision=ExpectedRevision(before.revision))
        statement = self._statement()
        failing = CoreService.open(self.root, operations=FailCompletionReplace())
        with self.assertRaises(ServiceError):
            failing.complete_feature(
                before, self.fixture.feature_id, CONDITION, r5c._consent(statement), snapshot,
            )
        after = CoreService.open(self.root).recover()
        self.assertEqual(after, before)
        self.assertEqual(after.feature_completion_receipts, ())

    def test_05_noninteractive_cli_cannot_create_a_human_closure(self) -> None:
        self._ready_one_slice()
        before = self.core.recover()
        output = io.StringIO()
        args = [
            "--project", str(self.root), "complete-feature",
            "--feature-id", self.fixture.feature_id,
            "--condition", CONDITION,
            "--expected-revision", str(before.revision),
        ]
        with patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(cli_main(args, output), 2)
        self.assertIn("interactive user terminal", output.getvalue())
        self.assertEqual(self.core.recover(), before)

    def test_06_cli_human_confirmation_and_no_reopen_without_new_decision(self) -> None:
        self._ready_one_slice()
        before = self.core.recover()
        statement = self._statement()
        token = sha256(statement.encode("utf-8")).hexdigest()[:12]
        args = [
            "--project", str(self.root), "complete-feature",
            "--feature-id", self.fixture.feature_id,
            "--condition", CONDITION, "--expected-revision", str(before.revision),
        ]
        terminal = TerminalBuffer()
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("builtins.input", return_value=f"ACEPTO {token}"),
        ):
            self.assertEqual(cli_main(args, terminal), 0)
        self.assertIn("FEATURE_COMPLETED", terminal.getvalue())
        self.assertIn("schema 17→18", terminal.getvalue())
        closed = self.core.recover()

        # Recovery is an inspection: the old revision and a headless retry
        # show the same receipt without requesting another acceptance.
        retry = io.StringIO()
        self.assertEqual(cli_main(args, retry), 0)
        self.assertIn("ALREADY_COMPLETED", retry.getvalue())
        self.assertEqual(self.core.recover().revision, closed.revision)
        with self.assertRaises(WorkflowError) as reopened:
            self.workflow.next_slice_agreement(
                self.fixture.feature_id, "Another Slice", "Another Task", "Checkpoint",
                self.fixture.spec_b, expected_revision=ExpectedRevision(closed.revision),
            )
        self.assertIn("REOPEN_DECISION_REQUIRED", str(reopened.exception))

    def test_07_postcommit_uncertainty_recovers_one_receipt(self) -> None:
        self._ready_one_slice()
        before = self.core.recover()
        _, snapshot = self.workflow.input_snapshot(expected_revision=ExpectedRevision(before.revision))
        acceptance = r5c._consent(self._statement())
        failing = CoreService.open(self.root, operations=FailCompletionDirectorySync())
        with self.assertRaises(DurabilityUncertainError):
            failing.complete_feature(before, self.fixture.feature_id, CONDITION, acceptance, snapshot)
        committed = self.core.recover()
        self.assertEqual(committed.revision, before.revision + 1)
        self.assertEqual(len(committed.feature_completion_receipts), 1)
        self.assertEqual(
            self.core.complete_feature(committed, self.fixture.feature_id, CONDITION, acceptance, snapshot),
            committed,
        )
        self.assertEqual(len(self.core.recover().feature_completion_receipts), 1)

    def test_08_stale_revision_wrong_root_and_condition_conflict_fail_closed(self) -> None:
        self._ready_one_slice()
        before = self.core.recover()
        with self.assertRaises(StaleRevisionError):
            self.workflow.feature_completion_agreement(
                self.fixture.feature_id, CONDITION,
                expected_revision=ExpectedRevision(before.revision - 1),
            )

        from tests._tempdir import TemporaryDirectory
        with TemporaryDirectory(prefix="evospec-r6a-foreign-root-") as temporary:
            foreign = Path(temporary)
            shutil.copytree(self.root / ".evospec", foreign / ".evospec")
            with self.assertRaises(RecoveryError):
                CoreService.open(foreign).recover()

        self._close()
        closed = self.core.recover()
        with self.assertRaises(WorkflowError):
            self.workflow.feature_completion_agreement(
                self.fixture.feature_id, "A different condition",
                expected_revision=ExpectedRevision(closed.revision),
            )
        self.assertEqual(self.core.recover(), closed)

    def test_09_receipt_statement_must_match_the_exact_human_completion_basis(self) -> None:
        self._ready_one_slice()
        self._close()
        state = self.core.recover()
        receipt = state.feature_completion_receipts[0]

        # A matching digest proves only that text was hashed, not that the
        # human declaration actually describes the immutable Feature evidence.
        attempts = (
            ("unrelated statement", "I approved an unrelated Feature", receipt.condition),
            ("changed condition", receipt.statement, "A different completion condition"),
        )
        for label, statement, condition in attempts:
            with self.subTest(label=label):
                altered = replace(
                    receipt,
                    statement=statement,
                    condition=condition,
                    source_reference=f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}",
                )
                altered = replace(altered, id=feature_completion_semantic_id(altered))
                with self.assertRaises(StateValidationError):
                    validate_state(replace(state, feature_completion_receipts=(altered,)))
        self.assertEqual(self.core.recover(), state)

        # Recovery must fail closed on the same inconsistency when it appears
        # in the persisted schema-18 authority, even with recomputed hashes.
        unrelated = "I approved an unrelated Feature"
        altered = replace(
            receipt, statement=unrelated,
            source_reference=f"tty:{sha256(unrelated.encode('utf-8')).hexdigest()[:12]}",
        )
        altered = replace(altered, id=feature_completion_semantic_id(altered))
        path = self.root / ".evospec/state.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["feature_completion_receipts"][0].update({
            "id": altered.id,
            "statement": altered.statement,
            "source_reference": altered.source_reference,
        })
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            CoreService.open(self.root).recover()


if __name__ == "__main__":
    unittest.main()
