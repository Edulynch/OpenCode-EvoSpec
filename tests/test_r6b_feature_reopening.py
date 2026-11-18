"""R6-B: reopening preserves an earlier Feature decision and creates a new cycle."""

from __future__ import annotations

import io
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from evospec.cli import main as cli_main
from evospec.domain import (
    ExpectedRevision, StateValidationError, feature_reopen_semantic_id, validate_state,
)
from evospec.input_fingerprint import (
    TEST_ENVIRONMENT_PROFILE_ID, sanitized_test_environment,
    test_environment_profile_digest,
)
from evospec.services import CoreService, DurabilityUncertainError, ServiceError, StaleRevisionError, UserAcceptance
from evospec.persistence import RecoveryError
from evospec.workflow import WorkflowError, WorkflowService
from tests import test_r6a_feature_completion as r6a
from tests.test_e1_r5c_second_slice_outcome import _consent
from tests import test_e1_r5c_second_slice_outcome as r5c


class R6BFeatureReopeningTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        # These placeholders are replaced by fresh isolated authority in setUp.
        self.historical = r6a.R6AFeatureCompletionTests()
        self.root = Path.cwd()
        self.core = CoreService.open(self.root)
        self.workflow = WorkflowService.open(self.root)
        self.feature_id = ""

    def setUp(self) -> None:
        # R6-A's real schema-18 two-Slice fixture is our historical authority.
        self.historical = r6a.R6AFeatureCompletionTests()
        self.historical.setUp()
        self.addCleanup(self.historical.doCleanups)
        self.historical._ready_two_slices()
        self.historical._close()
        self.core = self.historical.core
        self.workflow = self.historical.workflow
        self.feature_id = self.historical.fixture.feature_id
        self.root = self.historical.root

    def _reopen(self, reason: str = "Continued requirements", purpose: str = "Third auditable Slice"):
        current = self.core.recover()
        previous = current.feature_completion_receipts[-1]
        statement = self.workflow.feature_reopening_agreement(
            self.feature_id, previous.id, reason, purpose,
            expected_revision=ExpectedRevision(current.revision),
        )
        return self.workflow.reopen_feature(
            self.feature_id, previous.id, reason, purpose, _consent(statement),
            expected_revision=ExpectedRevision(current.revision),
        )

    def test_reopening_is_atomic_and_does_not_activate_work(self) -> None:
        before = self.core.recover()
        result = self._reopen()
        after = self.core.recover()
        self.assertEqual(result.code, "FEATURE_REOPENED")
        self.assertEqual(after.revision, before.revision + 1)
        self.assertEqual(after.feature_completion_receipts, before.feature_completion_receipts)
        self.assertEqual(after.agreements, before.agreements)
        self.assertEqual(after.slice_outcome_receipts, before.slice_outcome_receipts)
        self.assertIsNone(after.active_context)
        self.assertEqual(len(after.feature_reopen_receipts), 1)
        self.assertEqual(after.feature_reopen_receipts[0].cycle_id, 1)
        self.assertEqual(after.feature_reopen_receipts[0].previous_completion_receipt_id,
                         before.feature_completion_receipts[0].id)
        with (self.root / ".evospec" / "state.json").open(encoding="utf-8") as handle:
            state_data = json.load(handle)
        self.assertEqual(state_data["schema"], 19)
        validate_state(after)

    def test_retry_recovers_exact_reopening_without_new_revision(self) -> None:
        before = self.core.recover()
        previous = before.feature_completion_receipts[-1]
        self._reopen()
        saved = self.core.recover()
        retry = self.workflow.confirmed_feature_reopening(
            self.feature_id, previous.id, "Continued requirements", "Third auditable Slice",
            expected_revision=ExpectedRevision(before.revision),
        )
        self.assertIsNotNone(retry)
        assert retry is not None
        self.assertEqual(retry.code, "ALREADY_REOPENED")
        self.assertEqual(self.core.recover(), saved)

    def test_multiple_cycles_and_read_only_projections(self) -> None:
        first = self._reopen()
        state = self.core.recover()
        before_bytes = (self.root / ".evospec/state.json").read_bytes()
        context = self.historical.fixture.context()
        status = io.StringIO()
        self.assertEqual(cli_main(("--project", str(self.root), "status"), status), 0)
        self.assertEqual(context["feature_completion"]["cycle_id"], 1)
        self.assertEqual(context["feature_completion"]["cycle_state"], "OPEN")
        self.assertIn("Feature cycle: 1 (OPEN)", status.getvalue())
        self.assertEqual(len(context["feature_completion"]["history"]), 2)
        self.assertEqual(self.core.recover(), state)
        self.assertEqual((self.root / ".evospec/state.json").read_bytes(), before_bytes)

        statement = self.workflow.feature_completion_agreement(
            self.feature_id, "Cycle one verified", expected_revision=ExpectedRevision(state.revision),
        )
        self.workflow.complete_feature(
            self.feature_id, "Cycle one verified", _consent(statement),
            expected_revision=ExpectedRevision(state.revision),
        )
        second = self._reopen("New direction", "Second follow-up cycle")
        after = self.core.recover()
        assert first.feature_reopen_receipt is not None
        self.assertEqual(second.code, "FEATURE_REOPENED")
        self.assertEqual([item.cycle_id for item in after.feature_reopen_receipts], [1, 2])
        self.assertEqual([item.cycle_id for item in after.feature_completion_receipts], [0, 1])
        self.assertEqual(after.feature_reopen_receipts[0].id, first.feature_reopen_receipt.id)
        self.assertEqual(self.historical.fixture.context()["feature_completion"]["cycle_id"], 2)
        validate_state(after)

    def test_reopen_failure_before_replace_and_uncertain_fsync_are_recoverable(self) -> None:
        before = self.core.recover()
        previous = before.feature_completion_receipts[-1]
        statement = self.workflow.feature_reopening_agreement(
            self.feature_id, previous.id, "New requirements", "New Slice",
            expected_revision=ExpectedRevision(before.revision),
        )
        _, snapshot = self.core.input_snapshot()
        failing = CoreService.open(self.root, operations=r6a.FailCompletionReplace())
        with self.assertRaises(ServiceError):
            failing.reopen_feature(
                before, self.feature_id, previous.id, "New requirements", "New Slice",
                _consent(statement), snapshot,
            )
        self.assertEqual(self.core.recover(), before)
        self.assertEqual(json.loads((self.root / ".evospec/state.json").read_text())["schema"], 18)
        uncertain = CoreService.open(self.root, operations=r6a.FailCompletionDirectorySync())
        with self.assertRaises(DurabilityUncertainError):
            uncertain.reopen_feature(
                before, self.feature_id, previous.id, "New requirements", "New Slice",
                _consent(statement), snapshot,
            )
        after = self.core.recover()
        self.assertEqual(after.revision, before.revision + 1)
        self.assertEqual(len(after.feature_reopen_receipts), 1)
        confirmed = self.workflow.confirmed_feature_reopening(
            self.feature_id, previous.id, "New requirements", "New Slice",
            expected_revision=ExpectedRevision(before.revision),
        )
        self.assertIsNotNone(confirmed)
        self.assertEqual(self.core.recover(), after)

    def test_wrong_root_revision_and_forged_history_fail_closed(self) -> None:
        before = self.core.recover()
        previous = before.feature_completion_receipts[-1]
        with self.assertRaises((WorkflowError, StaleRevisionError)):
            self.workflow.feature_reopening_agreement(
                self.feature_id, previous.id, "Reason", "Purpose",
                expected_revision=ExpectedRevision(before.revision - 1),
            )
        from tests._tempdir import TemporaryDirectory
        with TemporaryDirectory(prefix="evospec-r6b-foreign-") as temporary:
            foreign = Path(temporary)
            shutil.copytree(self.root / ".evospec", foreign / ".evospec")
            with self.assertRaises(RecoveryError):
                CoreService.open(foreign).recover()
        self._reopen()
        state = self.core.recover()
        receipt = state.feature_reopen_receipts[0]
        forged = replace(receipt, reason="An unapproved reason")
        forged = replace(forged, id=feature_reopen_semantic_id(forged))
        with self.assertRaises(StateValidationError):
            validate_state(replace(state, feature_reopen_receipts=(forged,)))
        self.assertEqual(self.core.recover(), state)

    def test_cycle_completion_preserves_old_schema18_receipt(self) -> None:
        previous = self.core.recover().feature_completion_receipts[0]
        self._reopen()
        # Reclosure still checks actual fresh outcomes and a new TTY statement.
        current = self.core.recover()
        statement = self.workflow.feature_completion_agreement(
            self.feature_id, "Current result is verified in cycle one",
            expected_revision=ExpectedRevision(current.revision),
        )
        self.assertIn("Cycle: 1", statement)
        result = self.workflow.complete_feature(
            self.feature_id, "Current result is verified in cycle one", _consent(statement),
            expected_revision=ExpectedRevision(current.revision),
        )
        self.assertEqual(result.code, "FEATURE_COMPLETED")
        after = self.core.recover()
        self.assertEqual(len(after.feature_completion_receipts), 2)
        self.assertEqual(after.feature_completion_receipts[0], previous)
        self.assertEqual(after.feature_completion_receipts[1].cycle_id, 1)
        self.assertNotEqual(after.feature_completion_receipts[1].id, previous.id)
        validate_state(after)

    def test_new_slice_after_reopening_does_not_invalidate_first_closure(self) -> None:
        before = self.core.recover()
        old_receipt = before.feature_completion_receipts[0]
        self._reopen()
        spec = r5c._spec("Export tasks")
        state = self.core.recover()
        statement = r5c.next_slice_acceptance_statement(
            self.feature_id, r5c.FEATURE, "Export tasks", "Render export",
            "Export survives restart", spec,
        )
        accepted = self.workflow.accept_next_slice(
            self.feature_id, "Export tasks", "Render export",
            "Export survives restart", _consent(statement),
            expected_revision=ExpectedRevision(state.revision), criteria=spec,
        )
        self.assertEqual(accepted.code, "NEXT_SLICE_ACCEPTED")
        self.assertEqual(self.core.recover().feature_completion_receipts, (old_receipt,))
        self.assertEqual(len(self.core.recover().agreements), 6)
        validate_state(self.core.recover())

        # The independently accepted Slice must still acquire its own evidence.
        state = self.core.recover()
        declarations = r5c._coverage(spec)
        coverage_statement = self.workflow.r3b_coverage_renewal_agreement(
            declarations, expected_revision=ExpectedRevision(state.revision),
        )
        self.workflow.renew_r3b_coverage(
            _consent(coverage_statement), declarations,
            expected_revision=ExpectedRevision(state.revision),
        )
        for criterion_id in ("AC-1", "AC-2"):
            result = self.historical.fixture.record_r3b_result(criterion_id)
            self.assertEqual(result["result"]["code"], "CRITERION_TEST_RECORDED")
        context = self.historical.fixture.context()
        outcome = self.historical.fixture.send({
            "action": "accept_slice_outcome",
            "expected_revision": context["revision"],
            "input_snapshot": context["input_snapshot"],
        })
        self.assertEqual(outcome["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        self.historical._archive_current_slice()
        state = self.core.recover()
        closure_statement = self.workflow.feature_completion_agreement(
            self.feature_id, "Three Slices independently verified",
            expected_revision=ExpectedRevision(state.revision),
        )
        self.workflow.complete_feature(
            self.feature_id, "Three Slices independently verified", _consent(closure_statement),
            expected_revision=ExpectedRevision(state.revision),
        )
        after = self.core.recover()
        self.assertEqual(after.feature_completion_receipts[0], old_receipt)
        self.assertEqual(len(after.feature_completion_receipts[1].slice_agreement_refs), 3)
        validate_state(after)

    def test_wrong_prior_receipt_or_noninteractive_cli_does_not_reopen(self) -> None:
        before = self.core.recover()
        with self.assertRaises((WorkflowError, ServiceError)):
            self.workflow.feature_reopening_agreement(
                self.feature_id, "feature-completion-invalid", "Reason", "Purpose",
                expected_revision=ExpectedRevision(before.revision),
            )
        output = r6a.TerminalBuffer()
        with patch("sys.stdin", io.StringIO("ACEPTO whatever\n")), patch("sys.stdout", output):
            code = cli_main((
                "--project", str(self.root), "reopen-feature",
                "--feature-id", self.feature_id,
                "--previous-receipt-id", before.feature_completion_receipts[0].id,
                "--reason", "Reason", "--purpose", "Purpose",
                "--expected-revision", str(before.revision),
            ), output)
        self.assertEqual(code, 2)
        self.assertIn("interactive", output.getvalue())
        self.assertEqual(self.core.recover(), before)

    def _record_historical_test(self, slice_ref: str, criterion_id: str) -> None:
        """Use a real deterministic host process, not a synthetic PASS assertion."""
        context = self.historical.fixture.context()
        history = next(item for item in context["slice_acceptance_inventory"]["items"]
                       if item["slice_agreement_ref"] == slice_ref)
        revision = history["criteria_revision"]
        approval = next(item for item in context["r3b_coverage_approvals"]
                        if item["slice_agreement_ref"] == slice_ref
                        and item["criterion_id"] == criterion_id)
        runtime = str(Path(sys.executable).resolve())
        environment = sanitized_test_environment(self.root, runtime)
        argv = ["-I", "-B", "-c", r5c.PYTHON_TEST_RUNNER, str(self.root), r5c.TARGET]
        completed = subprocess.run(
            [runtime, *argv], cwd=self.root, env=environment,
            capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0)
        policy = context["r3b_policy"]
        request = {
            "action": "criterion_test_result_r3b",
            "expected_revision": context["revision"],
            "repository_id": context["repository_id"],
            "slice_agreement_ref": slice_ref,
            "criteria_revision_id": revision["id"],
            "criterion_id": criterion_id,
            "test_kind": "python-unittest", "test_target": r5c.TARGET,
            "exit_code": completed.returncode,
            "output_sha256": hashlib.sha256(completed.stdout + completed.stderr).hexdigest(),
            "input_fingerprint_before": context["input_snapshot"]["fingerprint"],
            "input_fingerprint_after": context["input_snapshot"]["fingerprint"],
            "input_policy_id": policy["input_policy_id"],
            "input_policy_digest": policy["input_policy_digest"],
            "admission_policy_id": policy["admission_policy_id"],
            "admission_policy_digest": policy["admission_policy_digest"],
            "runtime_executable": runtime,
            "runtime_executable_sha256": hashlib.sha256(Path(runtime).read_bytes()).hexdigest(),
            "runtime_version": f"Python {platform.python_version()}",
            "runtime_argv": argv,
            "environment_profile_id": TEST_ENVIRONMENT_PROFILE_ID,
            "environment_profile_digest": test_environment_profile_digest(environment),
            "coverage_approval_id": approval["id"],
            "coverage_contract_digest": approval["contract_digest"],
        }
        result = self.historical.fixture.send(request)
        self.assertNotIn("error", result)
        self.assertEqual(result["result"]["code"], "CRITERION_TEST_RECORDED")

    def test_historical_slices_can_receive_fresh_evidence_without_activation(self) -> None:
        before = self.core.recover()
        slice_refs = tuple(item.id for item in before.agreements if item.kind == "SLICE")
        old_receipts = before.feature_completion_receipts, before.slice_outcome_receipts
        self._reopen()
        # A root change makes BOTH formerly passing Slices stale at once.
        (self.root / "new-cycle-input.txt").write_text("new cycle", encoding="utf-8")
        _, snapshot = self.core.input_snapshot()
        readiness = self.workflow.feature_completion_eligibility(
            self.core.recover(), self.feature_id, snapshot,
        )
        self.assertEqual(readiness.status, "BLOCKED")
        for slice_ref, spec in zip(slice_refs, (self.historical.fixture.spec_a,
                                                self.historical.fixture.spec_b)):
            state = self.core.recover()
            declarations = r5c._coverage(spec)
            statement = self.workflow.r3b_coverage_renewal_agreement(
                declarations, expected_revision=ExpectedRevision(state.revision),
                slice_agreement_ref=slice_ref,
            )
            approved = self.workflow.renew_r3b_coverage(
                _consent(statement), declarations,
                expected_revision=ExpectedRevision(state.revision),
                slice_agreement_ref=slice_ref,
            )
            self.assertEqual(approved.code, "R3B_COVERAGE_RENEWED")
            for criterion_id in ("AC-1", "AC-2"):
                self._record_historical_test(slice_ref, criterion_id)
            context = self.historical.fixture.context()
            accepted = self.historical.fixture.send({
                "action": "accept_historical_slice_outcome",
                "expected_revision": context["revision"],
                "input_snapshot": context["input_snapshot"],
                "slice_agreement_ref": slice_ref,
            })
            self.assertNotIn("error", accepted)
            self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
            self.assertIsNone(self.core.recover().active_context)
        after = self.core.recover()
        self.assertEqual(after.feature_completion_receipts, old_receipts[0])
        self.assertEqual(after.slice_outcome_receipts[:2], old_receipts[1])
        readiness = self.workflow.feature_completion_eligibility(
            after, self.feature_id, self.core.input_snapshot()[1],
        )
        self.assertEqual(readiness.status, "READY_FOR_HUMAN_DECISION")


if __name__ == "__main__":
    unittest.main()
