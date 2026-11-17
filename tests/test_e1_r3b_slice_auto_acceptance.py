"""R3-B: fresh bounded evidence and separately authorized Slice outcome receipts."""

from __future__ import annotations

import io
import json
import platform
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

from evospec.agent_protocol import execute
from evospec.cli import _load_coverage_declarations, main as cli_main
from evospec.domain import (
    CriterionCoverageDeclaration,
    CoverageInput,
    Drift,
    ExpectedRevision,
    LivingSpecId,
    ObservationId,
    SliceCriterion,
)
from evospec.input_fingerprint import (
    COVERAGE_CONTRACT_VERSION,
    INPUT_FINGERPRINT_VERSION,
    INPUT_POLICY_DIGEST,
    INPUT_POLICY_ID,
    R3B_COVERAGE_CONTRACT,
    TEST_ADMISSION_POLICY_DIGEST,
    TEST_ADMISSION_POLICY_ID,
    TEST_ENVIRONMENT_PROFILE_DIGEST,
    TEST_ENVIRONMENT_PROFILE_ID,
    sanitized_test_environment,
    test_environment_profile_digest,
)
from evospec.persistence import FileOperations
from evospec.services import (
    CoreService,
    DurabilityUncertainError,
    SliceCriteriaSpec,
    UserAcceptance,
    slice_acceptance_statement,
    slice_criteria_decision_statement,
)
from evospec.workflow import WorkflowError, WorkflowResult, WorkflowService
from tests.test_e1_r1d1_slice_criteria import FEATURE, SLICE, TASK, CHECKPOINT, _TTY


TARGET = "tests.test_e1_r3b_slice_auto_acceptance"
PYTHON_TEST_RUNNER = (
    "import sys,unittest; root,module=sys.argv[1:3]; "
    "sys.path.insert(0,root); unittest.main(module=module,argv=[sys.argv[0]])"
)


def _consent(statement: str) -> UserAcceptance:
    return UserAcceptance(statement, f"tty:{sha256(statement.encode()).hexdigest()[:12]}")


def _spec(target: str = TARGET) -> SliceCriteriaSpec:
    return SliceCriteriaSpec(
        "Every accepted criterion has a fresh passing deterministic test",
        "The exact human-accepted test descriptor exits successfully",
        ("Fresh evidence per criterion",),
        ("No subjective or unsupported evidence",),
        ("Tests execute with the fixed sanitized local profile",),
        (
            SliceCriterion("AC-1", "The first supported test passes", f"python-unittest:{target}"),
            SliceCriterion("AC-2", "The second supported test passes", f"python-unittest:{target}"),
        ),
    )


class R3BSliceOutcomeTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.root = Path()
        self.workflow = WorkflowService.open(Path.cwd())
        self.initial = WorkflowResult("UNINITIALIZED", 0, None, None, None, "")
        self.spec = _spec()
        self.schema16_revision = 0

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="evospec-r3b-outcome-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        test_root = self.root / "tests"
        test_root.mkdir()
        (test_root / "__init__.py").write_text("", encoding="utf-8")
        (test_root / "test_e1_r3b_slice_auto_acceptance.py").write_text(
            "import unittest\n"
            "class CoveredFixture(unittest.TestCase):\n"
            "    def test_fixture(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (test_root / "test_agent.py").write_text(
            "import unittest\n"
            "class CoveredFixture(unittest.TestCase):\n"
            "    def test_fixture(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.workflow = WorkflowService.open(self.root)
        self.initial = self.workflow.init()
        self.spec = _spec()
        agreement = slice_acceptance_statement(FEATURE, SLICE, TASK, CHECKPOINT, self.spec)
        accepted = self.workflow.accept_first_slice(
            FEATURE,
            SLICE,
            TASK,
            CHECKPOINT,
            _consent(agreement),
            expected_revision=ExpectedRevision(self.initial.revision),
            criteria=self.spec,
        )
        self.schema16_revision = accepted.revision
        statement = self.workflow.r3_upgrade_agreement(
            expected_revision=ExpectedRevision(accepted.revision),
        )
        self.schema16_revision = self.workflow.upgrade_r3_schema(
            _consent(statement),
            expected_revision=ExpectedRevision(accepted.revision),
        ).revision

    def context(self) -> dict[str, Any]:
        payload = json.loads(execute(self.root, '{"action":"context"}'))
        self.assertNotIn("error", payload)
        return payload["context"]

    def upgrade_r3b(self) -> int:
        declarations = self.coverage_declarations()
        statement = self.workflow.r3b_upgrade_agreement(
            declarations,
            expected_revision=ExpectedRevision(self.schema16_revision),
        )
        result = self.workflow.upgrade_r3b_schema(
            _consent(statement),
            declarations,
            expected_revision=ExpectedRevision(self.schema16_revision),
        )
        self.assertEqual(result.code, "R3B_SCHEMA_UPGRADED")
        return result.revision

    def coverage_declarations(
        self,
        spec: SliceCriteriaSpec | None = None,
        known_path: str = "tests/test_e1_r3b_slice_auto_acceptance.py",
    ) -> tuple[CriterionCoverageDeclaration, ...]:
        selected = self.spec if spec is None else spec
        declarations: list[CriterionCoverageDeclaration] = []
        for criterion in selected.criteria:
            test_kind, _, test_target = criterion.expected_evidence.partition(":")
            declarations.append(CriterionCoverageDeclaration(
                criterion.id,
                test_kind,
                test_target,
                (CoverageInput("root-file", known_path),),
            ))
        return tuple(declarations)

    def write_coverage_file(self) -> Path:
        declarations = self.coverage_declarations()
        path = self.root / "coverage-contract.json"
        path.write_text(json.dumps({
            "version": COVERAGE_CONTRACT_VERSION,
            "criteria": [
                {
                    "criterion_id": item.criterion_id,
                    "test_kind": item.test_kind,
                    "test_target": item.test_target,
                    "known_inputs": [
                        {"kind": known.kind, "path": known.path}
                        for known in item.known_inputs
                    ],
                }
                for item in declarations
            ],
        }), encoding="utf-8")
        return Path("coverage-contract.json")

    def renew_coverage(
        self,
        declarations: tuple[CriterionCoverageDeclaration, ...] | None = None,
    ) -> WorkflowResult:
        selected = self.coverage_declarations() if declarations is None else declarations
        state = CoreService.open(self.root).recover()
        statement = self.workflow.r3b_coverage_renewal_agreement(
            selected,
            expected_revision=ExpectedRevision(state.revision),
        )
        return self.workflow.renew_r3b_coverage(
            _consent(statement),
            selected,
            expected_revision=ExpectedRevision(state.revision),
        )

    def result_request(self, criterion_id: str, **overrides: object) -> dict[str, Any]:
        context = self.context()
        accepted = context["accepted_slice"]
        policy = context["r3b_policy"]
        snapshot = context["input_snapshot"]
        criterion = next(
            (
                item for item in accepted["criteria_revision"]["criteria"]
                if item["id"] == criterion_id
            ),
            accepted["criteria_revision"]["criteria"][0],
        )
        test_kind, test_target = criterion["expected_evidence"].split(":", 1)
        runtime = str(Path(sys.executable).resolve())
        environment = sanitized_test_environment(self.root, runtime)
        request: dict[str, Any] = {
            "action": "criterion_test_result_r3b",
            "expected_revision": context["revision"],
            "repository_id": context["repository_id"],
            "slice_agreement_ref": accepted["slice_agreement_ref"],
            "criteria_revision_id": accepted["criteria_revision"]["id"],
            "criterion_id": criterion_id,
            "test_kind": test_kind,
            "test_target": test_target,
            "exit_code": 0,
            "output_sha256": sha256(b"R3-B bounded test output").hexdigest(),
            "input_fingerprint_before": snapshot["fingerprint"],
            "input_fingerprint_after": snapshot["fingerprint"],
            "input_policy_id": policy["input_policy_id"],
            "input_policy_digest": policy["input_policy_digest"],
            "admission_policy_id": policy["admission_policy_id"],
            "admission_policy_digest": policy["admission_policy_digest"],
            "runtime_executable": runtime,
            "runtime_executable_sha256": sha256(Path(runtime).read_bytes()).hexdigest(),
            "runtime_version": f"Python {platform.python_version()}",
            "runtime_argv": (
                ["-I", "-B", "-c", PYTHON_TEST_RUNNER, str(self.root), test_target]
                if test_kind == "python-unittest"
                else ["--test", test_target]
            ),
            "environment_profile_id": policy["environment_profile_id"],
            "environment_profile_digest": test_environment_profile_digest(environment),
        }
        approval = next(
            (
                item for item in context["r3b_coverage_approvals"]
                if item["criterion_id"] == criterion_id
            ),
            context["r3b_coverage_approvals"][0],
        )
        request["coverage_approval_id"] = approval["id"]
        request["coverage_contract_digest"] = approval["contract_digest"]
        request.update(overrides)
        return request

    def test_coverage_file_is_project_relative_and_explicit(self) -> None:
        path = self.write_coverage_file()
        declarations = _load_coverage_declarations(str(path), self.root)
        self.assertEqual(declarations, self.coverage_declarations())
        with self.assertRaises(ValueError):
            _load_coverage_declarations(str((self.root / path).resolve()), self.root)
        for unsafe in ("../coverage.json", ".evospec/coverage.json", "tests\\coverage.json"):
            with self.subTest(path=unsafe), self.assertRaises(ValueError):
                _load_coverage_declarations(unsafe, self.root)

    def test_coverage_requires_explicit_complete_supported_current_declarations(self) -> None:
        with self.assertRaises(WorkflowError):
            self.workflow.r3b_upgrade_agreement(
                (),
                expected_revision=ExpectedRevision(self.schema16_revision),
            )
        unsupported = self.coverage_declarations()
        unsupported = (
            replace(
                unsupported[0],
                known_inputs=(CoverageInput("external-file", "C:/credential.txt"),),
            ),
            *unsupported[1:],
        )
        with self.assertRaises(WorkflowError):
            self.workflow.r3b_upgrade_agreement(
                unsupported,
                expected_revision=ExpectedRevision(self.schema16_revision),
            )
        absent = self.coverage_declarations(known_path="tests/missing.py")
        with self.assertRaises(WorkflowError):
            self.workflow.r3b_upgrade_agreement(
                absent,
                expected_revision=ExpectedRevision(self.schema16_revision),
            )

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        return json.loads(execute(self.root, json.dumps(request)))

    def record_both(self) -> None:
        for criterion_id in ("AC-1", "AC-2"):
            result = self.send(self.result_request(criterion_id))
            self.assertEqual(result["result"]["code"], "CRITERION_TEST_RECORDED")

    def accept_request(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.context() if context is None else context
        return {
            "action": "accept_slice_outcome",
            "expected_revision": current["revision"],
            "input_snapshot": current["input_snapshot"],
        }

    def test_r3b_01_schema17_requires_separate_tty_decision_bound_to_policy_and_scope(self) -> None:
        state_path = self.root / ".evospec" / "state.json"
        before = state_path.read_bytes()
        state16 = CoreService.open(self.root).recover()
        self.assertEqual(json.loads(before)["schema"], 16)
        self.assertIsNone(state16.r3b_schema_upgrade)
        _, snapshot16 = self.workflow.input_snapshot(
            expected_revision=ExpectedRevision(state16.revision),
        )
        with self.assertRaises(WorkflowError):
            self.workflow.accept_slice_outcome(
                snapshot16, expected_revision=ExpectedRevision(state16.revision),
            )
        self.assertEqual(state_path.read_bytes(), before)

        output = _TTY()
        coverage_file = self.write_coverage_file()
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch(
            "builtins.input", return_value="ACEPTO incorrecto",
        ):
            self.assertEqual(cli_main([
                "--project", str(self.root), "upgrade-r3-outcome-schema",
                "--expected-revision", str(self.schema16_revision),
                "--coverage-file", str(coverage_file),
            ], output), 2)
        self.assertEqual(state_path.read_bytes(), before)

        declarations = self.coverage_declarations()
        statement = self.workflow.r3b_upgrade_agreement(
            declarations,
            expected_revision=ExpectedRevision(self.schema16_revision),
        )
        for part in (
            INPUT_FINGERPRINT_VERSION, INPUT_POLICY_DIGEST,
            TEST_ADMISSION_POLICY_ID, TEST_ADMISSION_POLICY_DIGEST,
            TEST_ENVIRONMENT_PROFILE_ID, TEST_ENVIRONMENT_PROFILE_DIGEST,
            R3B_COVERAGE_CONTRACT, "per-criterion TTY-approved root-input declarations",
            "not an OS sandbox",
        ):
            self.assertIn(part, statement)

        converted = self.workflow.upgrade_r3b_schema(
            _consent(statement),
            declarations,
            expected_revision=ExpectedRevision(self.schema16_revision),
        )
        self.assertEqual(converted.revision, self.schema16_revision + 1)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], 17)
        recovered = CoreService.open(self.root).recover()
        migration = recovered.r3b_schema_upgrade
        self.assertIsNotNone(migration)
        assert migration is not None
        self.assertEqual(migration.statement, statement)
        before_retry = state_path.read_bytes()
        retry = self.workflow.confirmed_r3b_schema_upgrade(
            expected_revision=ExpectedRevision(self.schema16_revision),
        )
        self.assertIsNotNone(retry)
        assert retry is not None
        self.assertEqual(retry.revision, converted.revision)
        self.assertEqual(state_path.read_bytes(), before_retry)
        with (
            patch("sys.stdin", io.StringIO()),
            patch("sys.stdout", io.StringIO()),
            patch("builtins.input", side_effect=AssertionError("committed migration must not prompt")),
        ):
            self.assertEqual(cli_main([
                "--project", str(self.root), "upgrade-r3-outcome-schema",
                "--expected-revision", str(self.schema16_revision),
                "--coverage-file", str(coverage_file),
            ], io.StringIO()), 0)
        self.assertEqual(state_path.read_bytes(), before_retry)

    def test_r3b_01c_non_tty_approval_sink_refuses_migration_and_coverage_renewal(self) -> None:
        state_path = self.root / ".evospec" / "state.json"
        coverage_file = self.write_coverage_file()
        migration_before = state_path.read_bytes()
        migration_state_before = CoreService.open(self.root).recover()
        migration_output = io.StringIO()
        with (
            patch("sys.stdin", _TTY()),
            patch("sys.stdout", _TTY()),
            patch("builtins.input", return_value="NO") as migration_prompt,
        ):
            migration_result = cli_main([
                "--project", str(self.root), "upgrade-r3-outcome-schema",
                "--expected-revision", str(self.schema16_revision),
                "--coverage-file", str(coverage_file),
            ], migration_output)
        self.assertEqual(migration_result, 2)
        self.assertIn("interactive user terminal", migration_output.getvalue())
        migration_prompt.assert_not_called()
        self.assertEqual(state_path.read_bytes(), migration_before)
        migration_state = CoreService.open(self.root).recover()
        self.assertEqual(migration_state.revision, self.schema16_revision)
        self.assertIsNone(migration_state.r3b_schema_upgrade)
        self.assertEqual(migration_state.criterion_coverage_approvals, ())
        self.assertEqual(migration_state.agreements, migration_state_before.agreements)
        self.assertEqual(migration_state.slice_outcome_receipts, ())

        self.upgrade_r3b()
        (self.root / "new-root-input").write_text("changed", encoding="utf-8")
        renewal_before = state_path.read_bytes()
        renewal_state_before = CoreService.open(self.root).recover()
        renewal_output = io.StringIO()
        with (
            patch("sys.stdin", _TTY()),
            patch("sys.stdout", _TTY()),
            patch("builtins.input", return_value="NO") as renewal_prompt,
        ):
            renewal_result = cli_main([
                "--project", str(self.root), "renew-r3b-coverage",
                "--expected-revision", str(renewal_state_before.revision),
                "--coverage-file", str(coverage_file),
            ], renewal_output)
        self.assertEqual(renewal_result, 2)
        self.assertIn("interactive user terminal", renewal_output.getvalue())
        renewal_prompt.assert_not_called()
        self.assertEqual(state_path.read_bytes(), renewal_before)
        renewal_state_after = CoreService.open(self.root).recover()
        self.assertEqual(renewal_state_after.revision, renewal_state_before.revision)
        self.assertEqual(
            renewal_state_after.criterion_coverage_approvals,
            renewal_state_before.criterion_coverage_approvals,
        )
        self.assertEqual(renewal_state_after.agreements, renewal_state_before.agreements)
        self.assertEqual(
            renewal_state_after.slice_outcome_receipts,
            renewal_state_before.slice_outcome_receipts,
        )

    def test_r3b_01b_interrupted_postreplace_conversion_is_recovered_without_replay(self) -> None:
        class FailDirectorySync(FileOperations):
            def fsync_directory(self, directory: Path) -> None:
                raise OSError("injected post-replace sync uncertainty")

        declarations = self.coverage_declarations()
        statement = self.workflow.r3b_upgrade_agreement(
            declarations,
            expected_revision=ExpectedRevision(self.schema16_revision),
        )
        interrupted = CoreService.open(self.root, FailDirectorySync())
        with self.assertRaises(DurabilityUncertainError):
            interrupted.upgrade_r3b_schema(interrupted.recover(), _consent(statement), declarations)

        state_path = self.root / ".evospec" / "state.json"
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], 17)
        recovered = CoreService.open(self.root).recover()
        self.assertEqual(recovered.revision, self.schema16_revision + 1)
        self.assertIsNotNone(recovered.r3b_schema_upgrade)
        before_retry = state_path.read_bytes()
        retry = self.workflow.confirmed_r3b_schema_upgrade(
            expected_revision=ExpectedRevision(self.schema16_revision),
        )
        self.assertIsNotNone(retry)
        assert retry is not None
        self.assertEqual(retry.revision, recovered.revision)
        self.assertEqual(state_path.read_bytes(), before_retry)

    def test_r3b_02_each_current_criterion_needs_fresh_success_and_acceptance_is_separate(self) -> None:
        converted_revision = self.upgrade_r3b()
        before = CoreService.open(self.root).recover()
        blocked = self.send(self.accept_request())
        self.assertEqual(blocked["error"]["code"], "WORKFLOW_ERROR")
        self.send(self.result_request("AC-1"))
        self.assertEqual(self.context()["slice_outcome"]["status"], "BLOCKED")
        self.send(self.result_request("AC-2"))

        ready = self.context()
        self.assertEqual(ready["slice_outcome"]["status"], "READY")
        self.assertEqual(
            {item["status"] for item in ready["criterion_verification"]},
            {"VERIFIED"},
        )
        accepted = self.send(self.accept_request(ready))
        self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        receipt = accepted["result"]["slice_outcome_receipt"]
        self.assertEqual(receipt["basis"], "ALL_CRITERIA_FRESH_SUPPORTED_TESTS")
        self.assertEqual(receipt["source"], "CORE_OBJECTIVE_ACCEPTANCE")
        self.assertEqual(receipt["input_policy_id"], INPUT_POLICY_ID)
        self.assertEqual(receipt["input_policy_digest"], INPUT_POLICY_DIGEST)
        self.assertEqual(receipt["admission_policy_digest"], TEST_ADMISSION_POLICY_DIGEST)
        self.assertEqual(set(receipt["evidence_receipt_ids"]), {
            item["last_test"]["receipt_id"] for item in ready["criterion_verification"]
        })
        after = CoreService.open(self.root).recover()
        self.assertEqual(after.revision, ready["revision"] + 1)
        self.assertEqual(after.r3b_schema_upgrade, before.r3b_schema_upgrade)
        self.assertEqual(after.agreements, before.agreements)
        self.assertEqual(after.slice_criteria_heads, before.slice_criteria_heads)
        after_active = after.active_context
        before_active = before.active_context
        self.assertIsNotNone(after_active)
        self.assertIsNotNone(before_active)
        assert after_active is not None and before_active is not None
        self.assertEqual(after_active.verification, before_active.verification)
        self.assertEqual(after.publications, before.publications)
        self.assertEqual(len(after.slice_outcome_receipts), 1)
        self.assertEqual(self.context()["slice_outcome"]["status"], "CURRENT")

        state_path = self.root / ".evospec" / "state.json"
        persisted = state_path.read_bytes()
        already = self.send(self.accept_request())
        self.assertEqual(already["result"]["code"], "ALREADY_ACCEPTED")
        self.assertEqual(already["result"]["slice_outcome_receipt"], receipt)
        self.assertEqual(state_path.read_bytes(), persisted)
        self.assertEqual(already["result"]["revision"], converted_revision + 3)

    def test_r3b_03_workspace_change_stales_history_and_stops_reacceptance(self) -> None:
        self.upgrade_r3b()
        self.record_both()
        accepted = self.send(self.accept_request())
        self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        prior = CoreService.open(self.root).recover().slice_outcome_receipts
        stale_test = self.result_request("AC-1")
        (self.root / "ignored-or-untracked.test-input").write_text("new input")
        before = (self.root / ".evospec" / "state.json").read_bytes()
        rejected_test = self.send(stale_test)
        self.assertEqual(rejected_test["error"]["code"], "WORKFLOW_ERROR")
        self.assertIn("fingerprint changed", rejected_test["error"]["detail"])
        self.assertEqual((self.root / ".evospec" / "state.json").read_bytes(), before)
        changed = self.context()
        self.assertEqual(changed["slice_outcome"]["status"], "STALE")
        self.assertEqual(changed["slice_outcome"]["latest_receipt_id"], prior[-1].id)
        rejected = self.send(self.accept_request(changed))
        self.assertEqual(rejected["error"]["code"], "WORKFLOW_ERROR")
        self.assertEqual((self.root / ".evospec" / "state.json").read_bytes(), before)
        self.assertEqual(CoreService.open(self.root).recover().slice_outcome_receipts, prior)

    def test_r3b_04_core_lock_rescans_and_rejects_prepermission_snapshot_drift(self) -> None:
        self.upgrade_r3b()
        context = self.context()
        request = self.result_request("AC-1")
        request["input_fingerprint_before"] = context["input_snapshot"]["fingerprint"]
        request["input_fingerprint_after"] = context["input_snapshot"]["fingerprint"]
        (self.root / "changed-after-snapshot").write_text("changed")
        before = (self.root / ".evospec" / "state.json").read_bytes()
        response = self.send(request)
        self.assertEqual(response["error"]["code"], "WORKFLOW_ERROR")
        self.assertIn("fingerprint changed", response["error"]["detail"])
        self.assertEqual((self.root / ".evospec" / "state.json").read_bytes(), before)

    def test_r3b_05_latest_failure_unknown_external_or_bad_profile_cannot_enable(self) -> None:
        self.upgrade_r3b()
        self.assertEqual(
            self.send(self.result_request("AC-1"))["result"]["code"],
            "CRITERION_TEST_RECORDED",
        )
        self.assertEqual(
            self.send(self.result_request("AC-1", exit_code=1))["result"]["code"],
            "CRITERION_TEST_RECORDED",
        )
        self.assertEqual(
            self.send(self.result_request("AC-2"))["result"]["code"],
            "CRITERION_TEST_RECORDED",
        )
        self.assertEqual(self.context()["slice_outcome"]["status"], "BLOCKED")

        bad_external = self.result_request("AC-1", external_inputs=["HOME"])
        before = (self.root / ".evospec" / "state.json").read_bytes()
        self.assertEqual(self.send(bad_external)["error"]["code"], "INVALID_REQUEST")
        bad_profile = self.result_request(
            "AC-1", environment_profile_digest="0" * 64,
        )
        self.assertIn("error", self.send(bad_profile))
        self.assertEqual((self.root / ".evospec" / "state.json").read_bytes(), before)

    def test_r3b_05b_wrong_scope_head_root_and_revision_cannot_record_evidence(self) -> None:
        self.upgrade_r3b()
        unapproved = self.result_request(
            "AC-1",
            coverage_approval_id="coverage-" + "0" * 20,
            coverage_contract_digest="0" * 64,
        )
        requests = (
            self.result_request("AC-1", repository_id="another-repository"),
            self.result_request("AC-1", slice_agreement_ref="another-slice"),
            self.result_request("AC-1", criteria_revision_id="another-head"),
            self.result_request("unknown-criterion"),
            self.result_request("AC-1", test_target="tests.test_agent"),
            unapproved,
            self.result_request("AC-1", expected_revision=self.context()["revision"] - 1),
        )
        before = (self.root / ".evospec" / "state.json").read_bytes()
        results = tuple(self.send(request) for request in requests)
        self.assertTrue(all("error" in result for result in results))
        self.assertEqual(results[-1]["error"]["code"], "STALE_REVISION")
        self.assertEqual((self.root / ".evospec" / "state.json").read_bytes(), before)

    def test_r3b_06_r3a_evidence_stays_reportable_but_never_counts_as_fresh_r3b(self) -> None:
        state16 = CoreService.open(self.root).recover()
        initialization = state16.initialization
        self.assertIsNotNone(initialization)
        assert initialization is not None
        accepted = self.context()["accepted_slice"]
        old_result = json.loads(execute(self.root, json.dumps({
            "action": "criterion_test_result",
            "expected_revision": state16.revision,
            "repository_id": initialization.repository_id,
            "slice_agreement_ref": accepted["slice_agreement_ref"],
            "criteria_revision_id": accepted["criteria_revision"]["id"],
            "criterion_id": "AC-1",
            "test_kind": "python-unittest",
            "test_target": TARGET,
            "exit_code": 0,
            "output_sha256": sha256(b"R3-A only").hexdigest(),
        })))
        self.assertEqual(old_result["result"]["code"], "CRITERION_TEST_RECORDED")
        self.schema16_revision = old_result["result"]["revision"]
        self.upgrade_r3b()
        context = self.context()
        self.assertTrue(context["r3_schema_ready"])
        self.assertTrue(context["r3b_schema_ready"])
        self.assertEqual(context["criterion_verification"][0]["evidence_source"], "HOST_REPORTED_TEST")
        self.assertEqual(context["slice_outcome"]["status"], "BLOCKED")

        self.send(self.result_request("AC-1"))
        legacy_after_schema17 = json.loads(execute(self.root, json.dumps({
            "action": "criterion_test_result",
            "expected_revision": self.context()["revision"],
            "repository_id": self.context()["repository_id"],
            "slice_agreement_ref": accepted["slice_agreement_ref"],
            "criteria_revision_id": accepted["criteria_revision"]["id"],
            "criterion_id": "AC-1",
            "test_kind": "python-unittest",
            "test_target": TARGET,
            "exit_code": 0,
            "output_sha256": sha256(b"R3-A still works").hexdigest(),
        })))
        self.assertEqual(legacy_after_schema17["result"]["code"], "CRITERION_TEST_RECORDED")
        self.assertEqual(self.context()["slice_outcome"]["status"], "BLOCKED")

    def test_r3b_07_pending_material_decision_and_relevant_drift_block(self) -> None:
        self.upgrade_r3b()
        self.record_both()
        core = CoreService.open(self.root)
        current = core.recover()
        active = current.active_context
        self.assertIsNotNone(active)
        assert active is not None
        pending = core.record_safety_facts(
            current,
            True,
            replace(active.safety_facts, unresolved_decision_details=("unresolved",)),
        )
        self.assertEqual(pending.revision, current.revision + 1)
        self.assertIn("MATERIAL_DECISION_PENDING", self.context()["slice_outcome"]["blockers"])
        blocked = self.send(self.accept_request())
        self.assertEqual(blocked["error"]["code"], "WORKFLOW_ERROR")

        current = core.recover()
        active = current.active_context
        assert active is not None
        resolved = core.record_safety_facts(
            current,
            False,
            replace(active.safety_facts, unresolved_decision_details=()),
        )
        change_id = resolved.active_context.change_id if resolved.active_context else None
        self.assertIsNotNone(change_id)
        assert change_id is not None
        # A persisted drift needs an existing product-truth publication; here
        # directly probe Core's same candidate evaluator with that drift fact.
        drifted = replace(resolved, drifts=(Drift(
            ObservationId("drift-r3b-relevant"), LivingSpecId("spec-r3b"),
            "changed publication", change_id, True,
        ),))
        _, snapshot = self.workflow.input_snapshot(
            expected_revision=ExpectedRevision(resolved.revision),
        )
        candidate, blockers = core._slice_outcome_candidate(drifted, snapshot)
        self.assertIsNone(candidate)
        self.assertIn("RELEVANT_DRIFT_UNRESOLVED", blockers)

    def test_r3b_08_amended_head_invalidates_evidence_without_rewriting_receipts(self) -> None:
        self.upgrade_r3b()
        self.record_both()
        old = CoreService.open(self.root).recover()
        amendment = _spec("tests.test_agent")
        prior = old.slice_criteria_heads[0].revision_id
        reason = "Human-approved evidence target change"
        statement = slice_criteria_decision_statement(prior, reason, amendment)
        self.workflow.revise_slice_criteria(
            amendment,
            prior,
            reason,
            _consent(statement),
            expected_revision=ExpectedRevision(old.revision),
        )
        current = CoreService.open(self.root).recover()
        self.assertEqual(current.criterion_test_receipts, old.criterion_test_receipts)
        self.assertNotEqual(current.slice_criteria_heads[0].revision_id, prior)
        self.assertEqual(self.context()["slice_outcome"]["status"], "BLOCKED")
        self.assertTrue(all(item["status"] == "UNVERIFIED" for item in self.context()["criterion_verification"]))
        self.renew_coverage(
            self.coverage_declarations(amendment, known_path="tests/test_agent.py"),
        )
        self.record_both()
        self.assertEqual(self.context()["slice_outcome"]["status"], "READY")

    def test_r3b_09_strict_protocol_rejects_partial_or_extra_fields(self) -> None:
        self.upgrade_r3b()
        context = self.context()
        before = (self.root / ".evospec" / "state.json").read_bytes()
        malformed = self.result_request("AC-1")
        malformed["surprise"] = True
        response = self.send(malformed)
        self.assertEqual(response["error"]["code"], "INVALID_REQUEST")
        malformed = self.result_request("AC-1")
        del malformed["coverage_approval_id"]
        response = self.send(malformed)
        self.assertEqual(response["error"]["code"], "INVALID_REQUEST")
        malformed = self.accept_request(context)
        malformed["repository_id"] = context["repository_id"]
        response = self.send(malformed)
        self.assertEqual(response["error"]["code"], "INVALID_REQUEST")
        self.assertEqual((self.root / ".evospec" / "state.json").read_bytes(), before)

    def test_r3b_10_stale_expected_revision_rejects_even_an_already_accepted_receipt(self) -> None:
        self.upgrade_r3b()
        self.record_both()
        context = self.context()
        accepted = self.send(self.accept_request(context))
        self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        stale = self.accept_request(context)
        response = self.send(stale)
        self.assertEqual(response["error"]["code"], "STALE_REVISION")
        self.assertEqual(len(CoreService.open(self.root).recover().slice_outcome_receipts), 1)

    def test_r3b_11_concurrent_acceptance_has_one_revision_and_one_receipt(self) -> None:
        self.upgrade_r3b()
        self.record_both()
        current, snapshot = self.workflow.input_snapshot()
        expected_revision = ExpectedRevision(current.revision)

        def accept():
            try:
                return self.workflow.accept_slice_outcome(
                    snapshot, expected_revision=expected_revision,
                )
            except WorkflowError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(accept)
            second = executor.submit(accept)
            outcomes = (first.result(), second.result())
        self.assertEqual(sum(not isinstance(item, WorkflowError) for item in outcomes), 1)
        self.assertEqual(len(CoreService.open(self.root).recover().slice_outcome_receipts), 1)

    def test_r3b_12_rerunning_equivalent_successful_criteria_reuses_canonical_outcome(self) -> None:
        self.upgrade_r3b()
        self.record_both()
        first = self.send(self.accept_request())
        self.assertEqual(first["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        receipt = first["result"]["slice_outcome_receipt"]
        state_path = self.root / ".evospec" / "state.json"
        before_rerun = state_path.read_bytes()

        self.record_both()
        before_accept = CoreService.open(self.root).recover()
        accepted_again = self.send(self.accept_request())

        self.assertEqual(accepted_again["result"]["code"], "ALREADY_ACCEPTED")
        self.assertEqual(accepted_again["result"]["slice_outcome_receipt"], receipt)
        self.assertEqual(len(CoreService.open(self.root).recover().slice_outcome_receipts), 1)
        self.assertEqual(
            CoreService.open(self.root).recover().revision,
            before_accept.revision,
        )
        self.assertNotEqual(state_path.read_bytes(), before_rerun)

    def test_r3b_13_root_change_requires_explicit_tty_coverage_renewal(self) -> None:
        self.upgrade_r3b()
        (self.root / "new-root-input").write_text("changed")
        coverage_file = self.write_coverage_file()
        state = CoreService.open(self.root).recover()
        declarations = self.coverage_declarations()
        statement = self.workflow.r3b_coverage_renewal_agreement(
            declarations,
            expected_revision=ExpectedRevision(state.revision),
        )
        token = sha256(statement.encode("utf-8")).hexdigest()[:12]
        output = _TTY()
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch(
            "builtins.input", return_value=f"ACEPTO {token}",
        ):
            result = cli_main([
                "--project", str(self.root), "renew-r3b-coverage",
                "--expected-revision", str(state.revision),
                "--coverage-file", str(coverage_file),
            ], output)
        self.assertEqual(result, 0)
        renewed = CoreService.open(self.root).recover()
        self.assertEqual(renewed.revision, state.revision + 1)
        self.assertEqual(len(renewed.criterion_coverage_approvals), 4)
        self.record_both()
        self.assertEqual(self.context()["slice_outcome"]["status"], "READY")
        accepted = self.send(self.accept_request())
        self.assertEqual(accepted["result"]["code"], "SLICE_OUTCOME_ACCEPTED")

    def test_r3b_14_changed_contract_invalidates_old_tests_without_root_drift(self) -> None:
        self.upgrade_r3b()
        self.record_both()
        before = self.context()
        original_fingerprint = before["input_snapshot"]["fingerprint"]
        self.send(self.accept_request(before))

        declarations = self.coverage_declarations(known_path="tests/test_agent.py")
        self.renew_coverage(declarations)
        changed = self.context()
        self.assertEqual(changed["input_snapshot"]["fingerprint"], original_fingerprint)
        self.assertEqual(changed["slice_outcome"]["status"], "STALE")
        self.assertTrue(any(
            blocker.startswith("CRITERION_PROVENANCE_STALE:")
            for blocker in changed["slice_outcome"]["blockers"]
        ))
        self.record_both()
        replacement = self.send(self.accept_request())
        self.assertEqual(replacement["result"]["code"], "SLICE_OUTCOME_ACCEPTED")
        self.assertNotEqual(
            replacement["result"]["slice_outcome_receipt"]["id"],
            CoreService.open(self.root).recover().slice_outcome_receipts[0].id,
        )

    def test_r3b_15_a_to_b_to_a_returns_original_canonical_receipt(self) -> None:
        self.upgrade_r3b()
        self.record_both()
        first = self.send(self.accept_request())
        receipt_a = first["result"]["slice_outcome_receipt"]
        covered_file = self.root / "tests" / "test_e1_r3b_slice_auto_acceptance.py"
        original = covered_file.read_bytes()

        covered_file.write_bytes(original + b"\n# root B\n")
        self.renew_coverage()
        self.record_both()
        receipt_b = self.send(self.accept_request())["result"]["slice_outcome_receipt"]
        self.assertNotEqual(receipt_a["id"], receipt_b["id"])

        covered_file.write_bytes(original)
        self.record_both()
        before_accept = CoreService.open(self.root).recover()
        returned = self.send(self.accept_request())
        self.assertEqual(returned["result"]["code"], "ALREADY_ACCEPTED")
        self.assertEqual(returned["result"]["slice_outcome_receipt"], receipt_a)
        after = CoreService.open(self.root).recover()
        self.assertEqual(after.revision, before_accept.revision)
        projected = self.context()["slice_outcome"]
        self.assertEqual(projected["status"], "CURRENT")
        self.assertEqual(projected["latest_receipt_id"], receipt_a["id"])
        self.assertEqual(projected["receipt"], receipt_a)


if __name__ == "__main__":
    unittest.main()
