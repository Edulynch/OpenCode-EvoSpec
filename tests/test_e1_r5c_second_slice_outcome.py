"""R5-C option A: separate Slice authority and outcomes under one Feature."""

from __future__ import annotations

import hashlib
import io
import json
import platform
import shutil
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from evospec.agent_protocol import execute
from evospec.cli import main as cli_main
from evospec.domain import (
    CriterionCoverageDeclaration,
    CoverageInput,
    ExpectedRevision,
    SliceCriterion,
)
from evospec.input_fingerprint import (
    TEST_ENVIRONMENT_PROFILE_ID,
    sanitized_test_environment,
    test_environment_profile_digest,
)
from evospec.persistence import AtomicFailure, FileOperations
from evospec.services import (
    CoreService,
    DurabilityUncertainError,
    ServiceError,
    SliceCriteriaSpec,
    UserAcceptance,
    next_slice_acceptance_statement,
    slice_acceptance_statement,
)
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory


FEATURE = "Personal planner"
SLICE_A = "Local task list"
TASK_A = "Render saved tasks"
CHECKPOINT_A = "Task list survives restart"
SLICE_B = "Create a task"
TASK_B = "Implement local task creation"
CHECKPOINT_B = "Created task is visible after restart"
TARGET = "tests.test_r5c_fixture"
PYTHON_TEST_RUNNER = (
    "import sys,unittest; root,module=sys.argv[1:3]; "
    "sys.path.insert(0,root); unittest.main(module=module,argv=[sys.argv[0]])"
)


def _consent(statement: str) -> UserAcceptance:
    return UserAcceptance(
        statement,
        f"tty:{hashlib.sha256(statement.encode('utf-8')).hexdigest()[:12]}",
    )


def _spec(label: str) -> SliceCriteriaSpec:
    return SliceCriteriaSpec(
        f"{label} works after restart",
        f"A {label.lower()} remains visible after reopening",
        (f"Keep {label.lower()} local",),
        ("No account or synchronization",),
        ("Use local persistence",),
        (
            SliceCriterion("AC-1", f"{label}: saved item is visible", f"python-unittest:{TARGET}"),
            SliceCriterion("AC-2", f"{label}: empty state is deterministic", f"python-unittest:{TARGET}"),
        ),
    )


def _coverage(spec: SliceCriteriaSpec) -> tuple[CriterionCoverageDeclaration, ...]:
    return tuple(
        CriterionCoverageDeclaration(
            criterion.id,
            "python-unittest",
            TARGET,
            (CoverageInput("root-directory", "tests"),),
        )
        for criterion in spec.criteria
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    managed = root / ".evospec"
    return {
        path.relative_to(managed).as_posix(): path.read_bytes()
        for path in managed.rglob("*")
        if path.is_file()
    }


class FailOutcomeStateReplace(FileOperations):
    def replace(self, source: Path, target: Path) -> None:
        if target.name == "state.json":
            raise OSError("injected R5-C precommit State replacement failure")
        super().replace(source, target)


class FailOutcomeDirectorySync(FileOperations):
    def fsync_directory(self, directory: Path) -> None:
        if directory.name == ".evospec":
            raise OSError("injected R5-C postcommit directory sync failure")
        super().fsync_directory(directory)


class R5CSecondSliceOutcomeTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.root = Path()
        self.workflow = WorkflowService.open(Path.cwd())
        self.spec_a = _spec("List tasks")
        self.spec_b = _spec("Create tasks")
        self.feature_id = ""

    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="evospec-r5c-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "test_r5c_fixture.py").write_text(
            "import unittest\n"
            "class DeterministicFixture(unittest.TestCase):\n"
            "    def test_saved_item_is_visible(self):\n"
            "        self.assertEqual('saved item', 'saved item')\n"
            "    def test_empty_state_is_deterministic(self):\n"
            "        self.assertEqual([], [])\n",
            encoding="utf-8",
        )
        self.workflow = WorkflowService.open(self.root)
        self.spec_a = _spec("List tasks")
        self.spec_b = _spec("Create tasks")
        initialized = self.workflow.init()
        accepted = self.workflow.accept_first_slice(
            FEATURE,
            SLICE_A,
            TASK_A,
            CHECKPOINT_A,
            _consent(slice_acceptance_statement(
                FEATURE, SLICE_A, TASK_A, CHECKPOINT_A, self.spec_a,
            )),
            expected_revision=ExpectedRevision(initialized.revision),
            criteria=self.spec_a,
        )
        state = CoreService.open(self.root).recover()
        self.feature_id = str(state.features[0].id)
        r3 = self.workflow.r3_upgrade_agreement(
            expected_revision=ExpectedRevision(accepted.revision),
        )
        upgraded = self.workflow.upgrade_r3_schema(
            _consent(r3), expected_revision=ExpectedRevision(accepted.revision),
        )
        declarations = _coverage(self.spec_a)
        r3b = self.workflow.r3b_upgrade_agreement(
            declarations, expected_revision=ExpectedRevision(upgraded.revision),
        )
        self.workflow.upgrade_r3b_schema(
            _consent(r3b),
            declarations,
            expected_revision=ExpectedRevision(upgraded.revision),
        )

    def context(self) -> dict[str, Any]:
        response = json.loads(execute(self.root, '{"action":"context"}'))
        self.assertNotIn("error", response)
        return response["context"]

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        return json.loads(execute(self.root, json.dumps(request)))

    def r3b_request(self, criterion_id: str, **overrides: object) -> dict[str, Any]:
        context = self.context()
        accepted = context["accepted_slice"]
        policy = context["r3b_policy"]
        snapshot = context["input_snapshot"]
        criterion = next(
            item for item in accepted["criteria_revision"]["criteria"]
            if item["id"] == criterion_id
        )
        self.assertEqual(criterion["expected_evidence"], f"python-unittest:{TARGET}")
        runtime = str(Path(sys.executable).resolve())
        environment = sanitized_test_environment(self.root, runtime)
        argv = [
            "-I", "-B", "-c", PYTHON_TEST_RUNNER, str(self.root), TARGET,
        ]
        completed = subprocess.run(
            [runtime, *argv],
            cwd=self.root,
            env=environment,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode, 0,
            (completed.stdout + completed.stderr).decode("utf-8", errors="replace"),
        )
        approval = next(
            item for item in context["r3b_coverage_approvals"]
            if item["criterion_id"] == criterion_id
        )
        request: dict[str, Any] = {
            "action": "criterion_test_result_r3b",
            "expected_revision": context["revision"],
            "repository_id": context["repository_id"],
            "slice_agreement_ref": accepted["slice_agreement_ref"],
            "criteria_revision_id": accepted["criteria_revision"]["id"],
            "criterion_id": criterion_id,
            "test_kind": "python-unittest",
            "test_target": TARGET,
            "exit_code": completed.returncode,
            "output_sha256": hashlib.sha256(completed.stdout + completed.stderr).hexdigest(),
            "input_fingerprint_before": snapshot["fingerprint"],
            "input_fingerprint_after": snapshot["fingerprint"],
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
        request.update(overrides)
        return request

    def record_r3b_result(self, criterion_id: str, **overrides: object) -> dict[str, Any]:
        return self.send(self.r3b_request(criterion_id, **overrides))

    def report_task_progress(self, checkpoint: str, statement: str) -> dict[str, Any]:
        context = self.context()
        active = context["accepted_slice"]
        return self.send({
            "action": "accepted_task_progress",
            "expected_revision": context["revision"],
            "repository_id": context["repository_id"],
            "task_id": active["task_id"],
            "feature_agreement_ref": active["feature_agreement_ref"],
            "slice_agreement_ref": active["slice_agreement_ref"],
            "checkpoint": checkpoint,
            "statement": statement,
            "evidence_refs": ["tests/test_r5c_fixture.py"],
        })

    def record_a_and_park(self) -> dict[str, Any]:
        for criterion_id in ("AC-1", "AC-2"):
            result = self.record_r3b_result(criterion_id)
            self.assertEqual(result["result"]["code"], "CRITERION_TEST_RECORDED")

        ready = self.context()
        self.assertEqual(ready["slice_outcome"]["status"], "READY")
        accepted = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": ready["revision"],
            "input_snapshot": ready["input_snapshot"],
        })
        self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")

        progress = self.report_task_progress(
            CHECKPOINT_A, "The saved task list is visible after restart.",
        )
        self.assertEqual(progress["result"]["code"], "TASK_PROGRESS_RECORDED")
        before_park = CoreService.open(self.root).recover()
        active = before_park.active_context
        assert active is not None and active.slice_agreement_ref is not None
        self.assertEqual(len(before_park.slice_outcome_receipts), 1)
        self.assertEqual(before_park.slice_outcome_receipts[0].slice_agreement_ref, active.slice_agreement_ref)
        assert active.task_progress is not None
        self.assertEqual(active.task_progress.report_kind, "REPORTED_UNVERIFIED")
        agreement_ids = tuple(item.id for item in before_park.agreements)
        criteria_id = before_park.slice_criteria_heads[0].revision_id
        immutable_paths = {
            f"records/agreements/{identity}.json" for identity in agreement_ids
        } | {f"records/slice-criteria/{criteria_id}.json"}
        files = _snapshot(self.root)
        self.workflow.park("waiting for the next Slice")
        parked_state = CoreService.open(self.root).recover()
        work_item = next(
            item for item in parked_state.work_items
            if item.context is not None and item.context.slice_agreement_ref == active.slice_agreement_ref
        )
        return {
            "outcome": before_park.slice_outcome_receipts[0],
            "criteria": before_park.slice_criteria_revisions[0],
            "approvals": tuple(
                item for item in before_park.criterion_coverage_approvals
                if item.slice_agreement_ref == active.slice_agreement_ref
            ),
            "evidence": tuple(
                item for item in before_park.criterion_test_receipts
                if item.slice_agreement_ref == active.slice_agreement_ref
            ),
            "agreements": before_park.agreements[:2],
            "work_item": work_item,
            "files": {path: files[path] for path in immutable_paths},
        }

    def accept_b(self) -> dict[str, Any]:
        state = CoreService.open(self.root).recover()
        statement = next_slice_acceptance_statement(
            self.feature_id, FEATURE, SLICE_B, TASK_B, CHECKPOINT_B, self.spec_b,
        )
        result = self.workflow.accept_next_slice(
            self.feature_id,
            SLICE_B,
            TASK_B,
            CHECKPOINT_B,
            _consent(statement),
            expected_revision=ExpectedRevision(state.revision),
            criteria=self.spec_b,
        )
        self.assertEqual(result.code, "NEXT_SLICE_ACCEPTED")
        return self.context()

    def renew_b_coverage(self) -> None:
        declarations = _coverage(self.spec_b)
        state = CoreService.open(self.root).recover()
        statement = self.workflow.r3b_coverage_renewal_agreement(
            declarations, expected_revision=ExpectedRevision(state.revision),
        )
        result = self.workflow.renew_r3b_coverage(
            _consent(statement),
            declarations,
            expected_revision=ExpectedRevision(state.revision),
        )
        self.assertEqual(result.code, "R3B_COVERAGE_RENEWED")

    def prepare_ready_b(self) -> dict[str, Any]:
        original = self.record_a_and_park()
        self.accept_b()
        self.renew_b_coverage()
        for criterion_id in ("AC-1", "AC-2"):
            result = self.record_r3b_result(criterion_id)
            self.assertEqual(result["result"]["code"], "CRITERION_TEST_RECORDED")
        self.assertEqual(self.context()["slice_outcome"]["status"], "READY")
        return original

    def test_r5c_01_second_slice_has_independent_accepted_outcome_and_restart_projection(self) -> None:
        original = self.record_a_and_park()
        context = self.accept_b()
        active = context["accepted_slice"]
        active_inventory_item = next(
            item for item in context["slice_acceptance_inventory"]["items"]
            if item["slice_agreement_ref"] == active["slice_agreement_ref"]
        )
        self.assertEqual(active_inventory_item["feature_id"], self.feature_id)
        self.assertEqual(active["slice_title"], SLICE_B)
        self.assertNotEqual(active["slice_agreement_ref"], original["agreements"][1].id)
        self.assertNotEqual(active["criteria_revision"]["id"], original["criteria"].id)
        self.assertEqual(
            [item["id"] for item in active["criteria_revision"]["criteria"]],
            ["AC-1", "AC-2"],
        )
        self.assertEqual(
            [item.expected_evidence for item in original["criteria"].criteria],
            [item["expected_evidence"] for item in active["criteria_revision"]["criteria"]],
        )
        active_state = CoreService.open(self.root).recover()
        self.assertEqual(len(active_state.changes), 2)
        self.assertEqual(len(active_state.agreements), 4)
        self.assertEqual(active_state.agreements[0].kind, "FEATURE")
        self.assertEqual(active_state.agreements[1].kind, "SLICE")
        self.assertEqual(active_state.agreements[2].kind, "FEATURE")
        self.assertEqual(active_state.agreements[3].kind, "SLICE")
        self.assertEqual(active_state.agreements[0].feature_id, active_state.agreements[3].feature_id)
        self.assertNotEqual(active_state.agreements[1].change_id, active_state.agreements[3].change_id)
        self.assertNotEqual(active_state.agreements[1].task_id, active_state.agreements[3].task_id)
        self.assertEqual(len(active_state.slice_criteria_heads), 2)
        self.assertNotEqual(
            active_state.slice_criteria_heads[0].revision_id,
            active_state.slice_criteria_heads[1].revision_id,
        )
        self.assertEqual(context["slice_outcome"]["status"], "BLOCKED")
        self.assertTrue(all(
            f"COVERAGE_APPROVAL_REQUIRED:{criterion_id}" in context["slice_outcome"]["blockers"]
            for criterion_id in ("AC-1", "AC-2")
        ))

        self.renew_b_coverage()
        for criterion_id in ("AC-1", "AC-2"):
            result = self.record_r3b_result(criterion_id)
            self.assertEqual(result["result"]["code"], "CRITERION_TEST_RECORDED")
        ready = self.context()
        self.assertEqual(ready["slice_outcome"]["status"], "READY")
        self.assertTrue(all(item["fresh_for_current_workspace"] for item in ready["criterion_verification"]))
        before = CoreService.open(self.root).recover()
        accepted = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": ready["revision"],
            "input_snapshot": ready["input_snapshot"],
        })
        self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        receipt_b = accepted["result"]["slice_outcome_receipt"]
        after = CoreService.open(self.root).recover()
        self.assertEqual(after.revision, before.revision + 1)
        self.assertEqual(len(after.slice_outcome_receipts), 2)
        self.assertEqual(after.slice_outcome_receipts[0], original["outcome"])
        self.assertEqual(after.slice_outcome_receipts[1].slice_agreement_ref, active["slice_agreement_ref"])
        self.assertNotEqual(after.slice_outcome_receipts[0].id, after.slice_outcome_receipts[1].id)
        self.assertEqual(receipt_b["slice_agreement_ref"], active["slice_agreement_ref"])
        self.assertEqual(receipt_b["criteria_revision_id"], active["criteria_revision"]["id"])
        self.assertEqual(after.agreements[:2], original["agreements"])
        self.assertEqual(after.slice_criteria_revisions[0], original["criteria"])
        self.assertEqual(
            tuple(item for item in after.criterion_test_receipts
                  if item.slice_agreement_ref == original["outcome"].slice_agreement_ref),
            original["evidence"],
        )
        self.assertEqual(
            tuple(item for item in after.criterion_coverage_approvals
                  if item.slice_agreement_ref == original["outcome"].slice_agreement_ref),
            original["approvals"],
        )
        self.assertEqual(
            next(item for item in after.work_items if item.id == original["work_item"].id),
            original["work_item"],
        )
        self.assertEqual(
            {path: _snapshot(self.root)[path] for path in original["files"]},
            original["files"],
        )
        self.assertEqual(after.agreements, before.agreements)
        self.assertEqual(after.slice_criteria_heads, before.slice_criteria_heads)
        self.assertEqual(after.criterion_coverage_approvals, before.criterion_coverage_approvals)
        self.assertEqual(after.work_items, before.work_items)
        self.assertEqual(after.active_context, before.active_context)
        self.assertEqual(after.publications, before.publications)
        self.assertEqual(after.publication_history, before.publication_history)
        self.assertEqual(after.terminal_archives, before.terminal_archives)
        self.assertEqual(after.features, before.features)

        committed_bytes = _snapshot(self.root)
        retry_context = self.context()
        retry = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": retry_context["revision"],
            "input_snapshot": retry_context["input_snapshot"],
        })
        self.assertEqual(retry["result"]["code"], "ALREADY_ACCEPTED")
        self.assertEqual(retry["result"]["slice_outcome_receipt"], receipt_b)
        self.assertEqual(CoreService.open(self.root).recover().revision, after.revision)
        self.assertEqual(_snapshot(self.root), committed_bytes)

        before_restart = CoreService.open(self.root).recover()
        before_read_only_projections = _snapshot(self.root)
        protocol = self.context()
        output = io.StringIO()
        self.assertEqual(cli_main(["--project", str(self.root), "status"], output), 0)
        restarted = CoreService.open(self.root).recover()
        self.assertEqual(restarted, before_restart)
        self.assertEqual(_snapshot(self.root), before_read_only_projections)
        active_after_restart = restarted.active_context
        assert active_after_restart is not None
        self.assertEqual(active_after_restart.slice_agreement_ref, active["slice_agreement_ref"])
        parked_work = next(
            item for item in restarted.work_items
            if item.context is not None
            and item.context.slice_agreement_ref == original["outcome"].slice_agreement_ref
        )
        self.assertEqual(str(parked_work.operational_state), "FROZEN")
        self.assertEqual(protocol["continuation"]["status"], "ACTIVE_SLICE")
        self.assertEqual(protocol["continuation"]["active_slice"]["slice_title"], SLICE_B)
        self.assertEqual(protocol["continuation"]["slice_outcome_status"], "CURRENT")
        parked = next(
            item for item in protocol["slice_acceptance_inventory"]["items"]
            if item["slice_title"] == SLICE_A
        )
        self.assertEqual(parked["scope_status"], "PARKED")
        self.assertEqual(parked["criteria_status"], "RECORDED")
        self.assertTrue(parked["historical_receipt"])
        self.assertEqual(protocol["continuation"]["inventory"]["total"], 2)
        self.assertTrue(protocol["slice_acceptance_inventory"]["complete"])
        self.assertNotIn("slice_outcome_status", parked)
        self.assertIn(f"Active Slice: {SLICE_B}", output.getvalue())
        self.assertIn(SLICE_A, output.getvalue())
        self.assertIn("does not establish Feature completion", output.getvalue())
        self.assertNotIn("Feature complete", output.getvalue())

    def test_r5c_02_b_requires_its_own_coverage_and_fresh_complete_evidence(self) -> None:
        original = self.record_a_and_park()
        b_context = self.accept_b()
        b_slice_ref = b_context["accepted_slice"]["slice_agreement_ref"]
        b_criteria_id = b_context["accepted_slice"]["criteria_revision"]["id"]

        a_statement = self.workflow.r3b_coverage_renewal_agreement(
            _coverage(self.spec_b),
            expected_revision=ExpectedRevision(b_context["revision"]),
        )
        progress = self.report_task_progress(
            CHECKPOINT_B, "Task creation was reported, not independently verified.",
        )
        self.assertEqual(progress["result"]["code"], "TASK_PROGRESS_RECORDED")
        context = self.context()
        self.assertEqual(context["slice_outcome"]["status"], "BLOCKED")
        self.assertTrue(all(
            f"COVERAGE_APPROVAL_REQUIRED:{criterion_id}" in context["slice_outcome"]["blockers"]
            for criterion_id in ("AC-1", "AC-2")
        ))
        legacy = self.send({
            "action": "criterion_test_result",
            "expected_revision": context["revision"],
            "repository_id": context["repository_id"],
            "slice_agreement_ref": b_slice_ref,
            "criteria_revision_id": b_criteria_id,
            "criterion_id": "AC-1",
            "test_kind": "python-unittest",
            "test_target": TARGET,
            "exit_code": 0,
            "output_sha256": hashlib.sha256(b"legacy host-reported result").hexdigest(),
        })
        self.assertEqual(legacy["result"]["code"], "CRITERION_TEST_RECORDED")
        legacy_context = self.context()
        self.assertEqual(legacy_context["slice_outcome"]["status"], "BLOCKED")
        legacy_verification = next(
            item for item in legacy_context["criterion_verification"]
            if item["criterion_id"] == "AC-1"
        )
        self.assertFalse(legacy_verification["fresh_for_current_workspace"])
        self.assertFalse(legacy_verification["enables_slice_outcome"])
        self.assertEqual(
            legacy_verification["evidence_source"], "HOST_REPORTED_TEST",
        )

        with TemporaryDirectory(prefix="evospec-r5c-wrong-root-") as temporary:
            wrong_root = Path(temporary)
            shutil.copytree(self.root / ".evospec", wrong_root / ".evospec")
            wrong_before = _snapshot(wrong_root)
            wrong = json.loads(execute(wrong_root, '{"action":"context"}'))
            self.assertEqual(wrong["error"]["code"], "WORKFLOW_ERROR")
            self.assertIn("root", wrong["error"]["detail"].lower())
            self.assertEqual(_snapshot(wrong_root), wrong_before)

        before_rejections = _snapshot(self.root)
        blocked = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": legacy_context["revision"],
            "input_snapshot": legacy_context["input_snapshot"],
        })
        self.assertEqual(blocked["error"]["code"], "WORKFLOW_ERROR")
        self.assertIn("COVERAGE_APPROVAL_REQUIRED", blocked["error"]["detail"])
        with self.assertRaises(WorkflowError) as stale_consent:
            self.workflow.renew_r3b_coverage(
                _consent(a_statement),
                _coverage(self.spec_b),
                expected_revision=ExpectedRevision(legacy_context["revision"]),
            )
        self.assertIn("exact interactive terminal decision", str(stale_consent.exception))
        self.assertEqual(_snapshot(self.root), before_rejections)
        current_state = CoreService.open(self.root).recover()
        statement_b = self.workflow.r3b_coverage_renewal_agreement(
            _coverage(self.spec_b),
            expected_revision=ExpectedRevision(current_state.revision),
        )
        with self.assertRaises(WorkflowError) as unauthorized_consent:
            self.workflow.renew_r3b_coverage(
                UserAcceptance(statement_b, "tty:wrong"),
                _coverage(self.spec_b),
                expected_revision=ExpectedRevision(current_state.revision),
            )
        self.assertIn("exact interactive terminal decision", str(unauthorized_consent.exception))
        self.assertEqual(_snapshot(self.root), before_rejections)
        renewed = self.workflow.renew_r3b_coverage(
            _consent(statement_b),
            _coverage(self.spec_b),
            expected_revision=ExpectedRevision(current_state.revision),
        )
        self.assertEqual(renewed.code, "R3B_COVERAGE_RENEWED")
        # The current approval must be renewed against B; A's existing approvals
        # are retained but cannot authorize B's repeated criterion identities.
        after_renewal = CoreService.open(self.root).recover()
        self.assertEqual(
            tuple(item for item in after_renewal.criterion_coverage_approvals
                  if item.slice_agreement_ref == original["outcome"].slice_agreement_ref),
            original["approvals"],
        )
        approvals_b = tuple(
            item for item in after_renewal.criterion_coverage_approvals
            if item.slice_agreement_ref == b_slice_ref
        )
        self.assertEqual({item.criterion_id for item in approvals_b}, {"AC-1", "AC-2"})
        self.assertEqual({item.criteria_revision_id for item in approvals_b}, {b_criteria_id})
        self.assertTrue(set(item.id for item in approvals_b).isdisjoint(
            item.id for item in original["approvals"]
        ))
        self.assertEqual(
            {item.root_fingerprint for item in approvals_b},
            {self.context()["input_snapshot"]["fingerprint"]},
        )
        self.assertEqual(
            {item.expected_evidence for item in self.spec_a.criteria},
            {item.expected_evidence for item in self.spec_b.criteria},
        )

        context = self.context()
        self.assertEqual(context["slice_outcome"]["status"], "BLOCKED")
        self.assertIn("CRITERION_PROVENANCE_STALE:AC-1", context["slice_outcome"]["blockers"])
        self.assertIn("CRITERION_UNVERIFIED:AC-2", context["slice_outcome"]["blockers"])
        state_bytes = _snapshot(self.root)
        request = self.r3b_request("AC-1", slice_agreement_ref=original["outcome"].slice_agreement_ref)
        self.assertEqual(self.send(request)["error"]["code"], "WORKFLOW_ERROR")
        request = self.r3b_request("AC-1", criteria_revision_id=original["criteria"].id)
        self.assertEqual(self.send(request)["error"]["code"], "WORKFLOW_ERROR")
        request = self.r3b_request("AC-1", repository_id="wrong-repository")
        self.assertEqual(self.send(request)["error"]["code"], "WORKFLOW_ERROR")
        request = self.r3b_request("AC-1", expected_revision=self.context()["revision"] - 1)
        self.assertEqual(self.send(request)["error"]["code"], "STALE_REVISION")
        self.assertEqual(_snapshot(self.root), state_bytes)

        incomplete = self.record_r3b_result("AC-1")
        self.assertEqual(incomplete["result"]["code"], "CRITERION_TEST_RECORDED")
        context = self.context()
        self.assertEqual(context["slice_outcome"]["status"], "BLOCKED")
        self.assertIn("CRITERION_UNVERIFIED:AC-2", context["slice_outcome"]["blockers"])
        before_blocked_acceptance = _snapshot(self.root)
        blocked = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": context["revision"],
            "input_snapshot": context["input_snapshot"],
        })
        self.assertEqual(blocked["error"]["code"], "WORKFLOW_ERROR")
        self.assertIn("CRITERION_UNVERIFIED:AC-2", blocked["error"]["detail"])
        self.assertEqual(_snapshot(self.root), before_blocked_acceptance)
        self.record_r3b_result("AC-2")

        # Only this protocol result is synthetic: it models a host reporting a
        # failing exit for the latest invocation; passing evidence above runs
        # the real deterministic fixture using the accepted runtime contract.
        failed_request = self.r3b_request("AC-1")
        failed_request["exit_code"] = 1
        failed_request["output_sha256"] = hashlib.sha256(b"reported failure").hexdigest()
        self.assertEqual(self.send(failed_request)["result"]["code"], "CRITERION_TEST_RECORDED")
        failed = self.context()
        self.assertEqual(failed["slice_outcome"]["status"], "BLOCKED")
        self.assertIn("CRITERION_TEST_FAILED:AC-1", failed["slice_outcome"]["blockers"])
        self.record_r3b_result("AC-1")

        ready = self.context()
        self.assertEqual(ready["slice_outcome"]["status"], "READY")
        accepted = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": ready["revision"],
            "input_snapshot": ready["input_snapshot"],
        })
        self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        receipt_b = accepted["result"]["slice_outcome_receipt"]
        (self.root / "new-root-input").write_text("workspace changed after acceptance", encoding="utf-8")
        stale = self.context()
        self.assertEqual(stale["slice_outcome"]["status"], "STALE")
        self.assertEqual(stale["slice_outcome"]["latest_receipt_id"], receipt_b["id"])
        before_stale_rejection = _snapshot(self.root)
        rejected = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": stale["revision"],
            "input_snapshot": stale["input_snapshot"],
        })
        self.assertEqual(rejected["error"]["code"], "WORKFLOW_ERROR")
        self.assertEqual(_snapshot(self.root), before_stale_rejection)
        self.assertEqual(
            CoreService.open(self.root).recover().slice_outcome_receipts[0],
            original["outcome"],
        )
        self.assertEqual(
            tuple(item for item in CoreService.open(self.root).recover().slice_outcome_receipts
                  if item.slice_agreement_ref == b_slice_ref)[0].id,
            receipt_b["id"],
        )

    def test_r5c_03_unresolved_material_decision_blocks_ready_outcome(self) -> None:
        original = self.record_a_and_park()
        self.accept_b()
        self.renew_b_coverage()
        for criterion_id in ("AC-1", "AC-2"):
            self.assertEqual(
                self.record_r3b_result(criterion_id)["result"]["code"],
                "CRITERION_TEST_RECORDED",
            )
        ready = self.context()
        self.assertEqual(ready["slice_outcome"]["status"], "READY")
        state = CoreService.open(self.root).recover()
        active = state.active_context
        assert active is not None
        pending = CoreService.open(self.root).record_safety_facts(
            state,
            True,
            replace(active.safety_facts, unresolved_decision_details=("unresolved R5-C decision",)),
        )
        pending_context = self.context()
        self.assertIn("MATERIAL_DECISION_PENDING", pending_context["slice_outcome"]["blockers"])
        before_rejection = _snapshot(self.root)
        rejected = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": pending_context["revision"],
            "input_snapshot": pending_context["input_snapshot"],
        })
        self.assertEqual(rejected["error"]["code"], "WORKFLOW_ERROR")
        self.assertIn("MATERIAL_DECISION_PENDING", rejected["error"]["detail"])
        self.assertEqual(_snapshot(self.root), before_rejection)
        b_ref = pending_context["accepted_slice"]["slice_agreement_ref"]
        self.assertEqual(
            tuple(item for item in CoreService.open(self.root).recover().slice_outcome_receipts
                  if item.slice_agreement_ref == b_ref),
            (),
        )

        active = pending.active_context
        assert active is not None
        resolved = CoreService.open(self.root).record_safety_facts(
            pending,
            False,
            replace(active.safety_facts, unresolved_decision_details=()),
        )
        ready = self.context()
        self.assertEqual(ready["slice_outcome"]["status"], "READY")
        accepted = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": ready["revision"],
            "input_snapshot": ready["input_snapshot"],
        })
        self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        after = CoreService.open(self.root).recover()
        self.assertEqual(after.slice_outcome_receipts[0], original["outcome"])
        self.assertEqual(after.slice_outcome_receipts[1].slice_agreement_ref, b_ref)
        self.assertGreater(resolved.revision, pending.revision)

    def test_r5c_04_precommit_outcome_failure_does_not_publish_authority(self) -> None:
        self.prepare_ready_b()
        context = self.context()
        state, snapshot = WorkflowService.open(self.root).input_snapshot(
            expected_revision=ExpectedRevision(context["revision"]),
        )
        before = CoreService.open(self.root).recover()
        b_ref = context["accepted_slice"]["slice_agreement_ref"]
        with self.assertRaises(ServiceError) as raised:
            CoreService.open(self.root, FailOutcomeStateReplace()).accept_slice_outcome(
                state, snapshot,
            )
        self.assertIsInstance(raised.exception.__cause__, AtomicFailure)
        self.assertEqual(
            str(raised.exception.__cause__),
            "atomic publication failed before replacement",
        )

        recovered = CoreService.open(self.root).recover()
        self.assertEqual(recovered, before)
        self.assertEqual(recovered.revision, before.revision)
        self.assertEqual(
            len(recovered.slice_outcome_receipts),
            len(before.slice_outcome_receipts),
        )
        self.assertEqual(
            tuple(item for item in recovered.slice_outcome_receipts
                  if item.slice_agreement_ref == b_ref),
            (),
        )
        self.assertEqual(self.context()["slice_outcome"]["status"], "READY")
        self.assertNotEqual(self.context()["slice_outcome"]["status"], "CURRENT")

        accepted = self.send({
            "action": "accept_slice_outcome",
            "expected_revision": self.context()["revision"],
            "input_snapshot": self.context()["input_snapshot"],
        })
        self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")

    def test_r5c_05_postcommit_uncertainty_recovers_once_without_duplicate_outcome(self) -> None:
        self.prepare_ready_b()
        context = self.context()
        state, snapshot = WorkflowService.open(self.root).input_snapshot(
            expected_revision=ExpectedRevision(context["revision"]),
        )
        before = CoreService.open(self.root).recover()
        with self.assertRaises(DurabilityUncertainError) as raised:
            CoreService.open(self.root, FailOutcomeDirectorySync()).accept_slice_outcome(
                state, snapshot,
            )
        self.assertIn("E1_DURABILITY_UNKNOWN", str(raised.exception))

        recovered = CoreService.open(self.root).recover()
        self.assertEqual(recovered.revision, before.revision + 1)
        b_ref = context["accepted_slice"]["slice_agreement_ref"]
        b_receipts = tuple(
            item for item in recovered.slice_outcome_receipts
            if item.slice_agreement_ref == b_ref
        )
        self.assertEqual(len(b_receipts), 1)
        self.assertEqual(self.context()["slice_outcome"]["status"], "CURRENT")
        committed_bytes = _snapshot(self.root)
        retry = WorkflowService.open(self.root).accept_slice_outcome(
            snapshot,
            expected_revision=ExpectedRevision(recovered.revision),
        )
        self.assertEqual(retry.code, "ALREADY_ACCEPTED")
        self.assertEqual(retry.slice_outcome_receipt, b_receipts[0])
        after_retry = CoreService.open(self.root).recover()
        self.assertEqual(after_retry.revision, recovered.revision)
        self.assertEqual(after_retry.slice_outcome_receipts, recovered.slice_outcome_receipts)
        self.assertEqual(_snapshot(self.root), committed_bytes)


if __name__ == "__main__":
    unittest.main()
