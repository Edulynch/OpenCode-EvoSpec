"""R3-A contract: explicit schema 16, immutable host-test receipts, exact criterion heads."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

from evospec.agent_protocol import execute
from evospec.cli import main as cli_main
from evospec.domain import ExpectedRevision, SliceCriterion
from evospec.persistence import RecoveryError
from evospec.services import CoreService, SliceCriteriaSpec, UserAcceptance, slice_acceptance_statement, slice_criteria_decision_statement
from evospec.workflow import WorkflowError, WorkflowService
from tests.test_e1_r1d1_slice_criteria import FEATURE, SLICE, TASK, CHECKPOINT, _TTY


def accepted_spec(target: str = "tests.test_e1_r3a_criterion_verification") -> SliceCriteriaSpec:
    return SliceCriteriaSpec(
        "Los resultados se demuestran con una prueba local", "El módulo de pruebas termina sin error",
        ("Evidencia objetiva por criterio",), ("No se aprueba el Slice automáticamente",),
        ("Prueba local permitida por el host",),
        (SliceCriterion("AC-1", "La prueba termina con código 0", f"python-unittest:{target}"),),
    )


def consent(statement: str) -> UserAcceptance:
    return UserAcceptance(statement, f"tty:{sha256(statement.encode()).hexdigest()[:12]}")


class CriterionVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="evospec-r3a-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)  # pyright: ignore[reportUninitializedInstanceVariable]
        self.workflow = WorkflowService.open(self.root)  # pyright: ignore[reportUninitializedInstanceVariable]
        self.workflow.init()
        spec = accepted_spec()
        statement = slice_acceptance_statement(FEATURE, SLICE, TASK, CHECKPOINT, spec)
        self.workflow.accept_first_slice(FEATURE, SLICE, TASK, CHECKPOINT, consent(statement),
                                         expected_revision=ExpectedRevision(1), criteria=spec)

    def context(self) -> dict[str, Any]:
        payload = json.loads(execute(self.root, '{"action":"context"}'))
        self.assertNotIn("error", payload)
        return payload["context"]

    def snapshot(self) -> dict[str, bytes]:
        return {str(item.relative_to(self.root)): item.read_bytes()
                for item in (self.root / ".evospec").rglob("*") if item.is_file()}

    def result_request(self, **overrides: object) -> dict[str, Any]:
        context = self.context()
        accepted = context["accepted_slice"]
        request = {
            "action": "criterion_test_result", "expected_revision": context["revision"],
            "repository_id": context["repository_id"],
            "slice_agreement_ref": accepted["slice_agreement_ref"],
            "criteria_revision_id": accepted["criteria_revision"]["id"],
            "criterion_id": "AC-1", "test_kind": "python-unittest",
            "test_target": "tests.test_e1_r3a_criterion_verification",
            "exit_code": 0, "output_sha256": sha256(b"test output").hexdigest(),
        }
        request.update(overrides)
        return request

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        return json.loads(execute(self.root, json.dumps(request)))

    def upgrade(self) -> None:
        statement = self.workflow.r3_upgrade_agreement(expected_revision=ExpectedRevision(self.context()["revision"]))
        result = self.workflow.upgrade_r3_schema(consent(statement), expected_revision=ExpectedRevision(2))
        self.assertEqual(result.code, "R3_SCHEMA_UPGRADED")

    def test_r3_01_schema15_stays_readable_and_cannot_record_without_approval(self) -> None:
        self.assertEqual(json.loads((self.root / ".evospec/state.json").read_text())["schema"], 15)
        before = self.snapshot()
        request = self.result_request()
        self.assertEqual(self.send(request)["error"]["code"], "WORKFLOW_ERROR")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.context()["r3_schema_ready"])
        self.assertEqual(self.context()["criterion_verification"][0]["status"], "UNVERIFIED")

    def test_r3_02_approved_upgrade_preserves_history_and_is_read_only_on_retry(self) -> None:
        before = CoreService.open(self.root).recover()
        self.upgrade()
        after = CoreService.open(self.root).recover()
        self.assertEqual(after.revision, before.revision + 1)
        self.assertEqual(after.agreements, before.agreements)
        self.assertEqual(after.slice_criteria_revisions, before.slice_criteria_revisions)
        self.assertEqual(json.loads((self.root / ".evospec/state.json").read_text())["schema"], 16)
        self.assertTrue(self.context()["r3_schema_ready"])
        original = self.snapshot()
        retry = self.workflow.confirmed_r3_schema_upgrade(expected_revision=ExpectedRevision(2))
        self.assertIsNotNone(retry)
        self.assertEqual(self.snapshot(), original)

    def test_r3_03_cli_requires_terminal_and_exact_approval(self) -> None:
        before = self.snapshot()
        error = io.StringIO()
        with patch("sys.stdin", io.StringIO()), patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli_main(["--project", str(self.root), "upgrade-r3-schema",
                                       "--expected-revision", "2"], error), 2)
        self.assertIn("interactive", error.getvalue())
        self.assertEqual(self.snapshot(), before)
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch("builtins.input", return_value="ACEPTO wrong"):
            self.assertEqual(cli_main(["--project", str(self.root), "upgrade-r3-schema",
                                       "--expected-revision", "2"], io.StringIO()), 2)
        self.assertEqual(self.snapshot(), before)

    def test_r3_04_actual_result_is_per_criterion_not_global_slice_approval(self) -> None:
        self.upgrade()
        response = self.send(self.result_request())
        self.assertEqual(response["result"]["code"], "CRITERION_TEST_RECORDED")
        status = self.context()["criterion_verification"][0]
        self.assertEqual(status["status"], "VERIFIED")
        self.assertEqual(status["evidence_source"], "HOST_REPORTED_TEST")
        self.assertEqual(status["last_test"]["exit_code"], 0)
        self.assertFalse(self.context()["decision_state"]["verification_sufficient"])
        self.assertEqual(len(CoreService.open(self.root).recover().criterion_test_receipts), 1)

    def test_r3_05_latest_failed_test_replaces_status_but_preserves_receipt(self) -> None:
        self.upgrade()
        self.send(self.result_request())
        failed = self.send(self.result_request(exit_code=1, output_sha256=sha256(b"failed").hexdigest()))
        self.assertEqual(failed["result"]["code"], "CRITERION_TEST_RECORDED")
        self.assertEqual(self.context()["criterion_verification"][0]["status"], "FAILED")
        self.assertEqual(len(CoreService.open(self.root).recover().criterion_test_receipts), 2)

    def test_r3_06_stale_head_and_changed_descriptor_fail_without_writes(self) -> None:
        self.upgrade()
        before = self.snapshot()
        for request in (
            self.result_request(expected_revision=2),
            self.result_request(test_target="tests.test_other"),
            self.result_request(repository_id="wrong-repository"),
            self.result_request(criterion_id="AC-99"),
            self.result_request(output_sha256="not-sha"),
            self.result_request(exit_code=True),
        ):
            self.assertIn("error", self.send(request))
            self.assertEqual(self.snapshot(), before)

    def test_r3_07_amendment_invalidates_current_head_evidence_without_rewriting_history(self) -> None:
        self.upgrade()
        self.send(self.result_request())
        old = CoreService.open(self.root).recover()
        previous = old.slice_criteria_heads[0].revision_id
        spec = accepted_spec("tests.test_e1_guarded_task_progress")
        statement = slice_criteria_decision_statement(previous, "Revisión explícita", spec)
        self.workflow.revise_slice_criteria(spec, previous, "Revisión explícita", consent(statement),
                                            expected_revision=ExpectedRevision(old.revision))
        self.assertEqual(self.context()["criterion_verification"][0]["status"], "UNVERIFIED")
        self.assertEqual(CoreService.open(self.root).recover().criterion_test_receipts, old.criterion_test_receipts)
        self.assertIn("error", self.send(self.result_request(criteria_revision_id=previous)))

    def test_r3_08_corrupted_persisted_receipt_fails_closed(self) -> None:
        self.upgrade()
        self.send(self.result_request())
        path = self.root / ".evospec/state.json"
        value = json.loads(path.read_text())
        value["criterion_test_receipts"][0]["exit_code"] = 2
        path.write_text(json.dumps(value))
        with self.assertRaises(RecoveryError):
            CoreService.open(self.root).recover()

    def test_r3_09_no_implicit_r3_upgrade_from_legacy_e1_schema(self) -> None:
        path = self.root / ".evospec/state.json"
        value = json.loads(path.read_text())
        value["schema"] = 14
        value.pop("slice_criteria_refs")
        value.pop("slice_criteria_heads")
        # A schema-14 snapshot cannot retain current criteria authority.
        path.write_text(json.dumps(value))
        # The old layout is readable, but no R3 approval statement may be offered.
        self.assertEqual(json.loads((self.root / ".evospec/state.json").read_text())["schema"], 14)
        with self.assertRaises(WorkflowError):
            self.workflow.r3_upgrade_agreement(expected_revision=ExpectedRevision(2))


if __name__ == "__main__":
    unittest.main()
