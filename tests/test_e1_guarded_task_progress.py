"""E1 Slice C: one guarded, unverified Task report per authoritative revision."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

from evospec.agent import project_context
from evospec.agent_protocol import execute
from evospec.cli import main as cli_main
from evospec.domain import ExpectedRevision, Observation, ObservationId
from evospec.persistence import FileOperations, RecoveryError, SCHEMA
from evospec.services import (
    CoreService, DurabilityUncertainError, ServiceError, StaleRevisionError, UserAcceptance,
)
from evospec.workflow import WorkflowError, WorkflowService
from tests.test_e1_first_slice import CHECKPOINT, FEATURE, SLICE, STATEMENT, TASK, _TTY


def _request(action: str, **fields: Any) -> dict[str, Any]:
    """Use the public protocol, as an independent OpenCode adapter would."""
    return {"action": action, **fields}


def _record_with_core(core: CoreService, request: dict[str, Any]) -> None:
    """Drive the guarded Core API with the exact typed request fields in failure tests."""
    _ = core.record_accepted_task_progress(
        core.recover(),
        repository_id=request["repository_id"],
        task_id=request["task_id"],
        feature_agreement_ref=request["feature_agreement_ref"],
        slice_agreement_ref=request["slice_agreement_ref"],
        checkpoint=request["checkpoint"],
        statement=request["statement"],
        evidence_refs=tuple(request["evidence_refs"]),
    )


class FailStateReplace(FileOperations):
    def replace(self, source: Path, target: Path) -> None:
        if target.name == "state.json":
            raise OSError("injected precommit state replace error")
        super().replace(source, target)


class FailStateDirectorySync(FileOperations):
    def fsync_directory(self, directory: Path) -> None:
        if directory.name == ".evospec":
            raise OSError("injected post-replace directory sync error")
        super().fsync_directory(directory)


class GuardedTaskProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="evospec-e1c-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)  # pyright: ignore[reportUninitializedInstanceVariable]
        self.workflow = WorkflowService.open(self.root)  # pyright: ignore[reportUninitializedInstanceVariable]
        self.workflow.init()
        acceptance = UserAcceptance(STATEMENT, f"tty:{sha256(STATEMENT.encode()).hexdigest()[:12]}")
        self.workflow.accept_first_slice(
            FEATURE, SLICE, TASK, CHECKPOINT, acceptance, expected_revision=ExpectedRevision(1),
        )

    def _context(self) -> dict[str, Any]:
        response = json.loads(execute(self.root, json.dumps(_request("context"))))
        self.assertNotIn("error", response)
        return response["context"]

    def _progress_request(self, **changes: Any) -> dict[str, Any]:
        context = self._context()
        accepted = context["accepted_slice"]
        assert isinstance(accepted, dict)
        request = _request(
            "accepted_task_progress", expected_revision=context["revision"],
            repository_id=context["repository_id"], task_id=accepted["task_id"],
            feature_agreement_ref=accepted["feature_agreement_ref"],
            slice_agreement_ref=accepted["slice_agreement_ref"],
            checkpoint="Lista visible con tres tareas", statement="Implementé persistencia local en memoria",
            evidence_refs=["tests/task-list.test.ts", "src/tasks.ts"],
        )
        request.update(changes)
        return request

    def _send(self, request: dict[str, Any]) -> dict[str, Any]:
        return json.loads(execute(self.root, json.dumps(request, ensure_ascii=False)))

    def _authority_snapshot(self) -> dict[str, tuple[bytes, int]]:
        return {
            str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (self.root / ".evospec").rglob("*") if path.is_file()
        }

    def _schema13(self) -> None:
        """Model the exact valid E1 schema-13 state produced by the earlier release."""
        state_path = self.root / ".evospec/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["schema"], 14)
        state["schema"] = 13
        state.pop("e1_schema_upgrade")

        def strip_report(value: object) -> None:
            if isinstance(value, dict):
                value.pop("task_progress", None)
                for child in value.values():
                    strip_report(child)
            elif isinstance(value, list):
                for child in value:
                    strip_report(child)

        strip_report(state)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(CoreService.open(self.root).recover().revision, 2)

    def test_c01_report_one_revision_and_immutable_agreements(self) -> None:
        before = self._authority_snapshot()
        guidance = self._context()
        self.assertTrue(guidance["e1_schema_ready"])
        self.assertIn("accepted_task_progress", guidance["legal_actions"])
        self.assertNotIn("progress", guidance["legal_actions"])
        response = self._send(self._progress_request())
        self.assertEqual(response["result"]["code"], "TASK_PROGRESS_RECORDED")
        self.assertEqual(response["result"]["revision"], 3)
        projected = response["context"]
        self.assertEqual(projected["lifecycle"], ["IN_PROGRESS", "IMPLEMENT"])
        self.assertEqual(projected["accepted_slice"]["checkpoint"], CHECKPOINT)
        self.assertEqual(projected["task_progress"]["report_kind"], "REPORTED_UNVERIFIED")
        self.assertEqual(projected["task_progress"]["source_kind"], "UNKNOWN")
        self.assertIsNone(projected["task_progress"]["source_reference"])
        self.assertEqual(projected["progress"]["resume_point"], "Lista visible con tres tareas")
        state = json.loads((self.root / ".evospec/state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["schema"], SCHEMA)
        self.assertIsNone(state["active_context"]["resume_point"])
        self.assertEqual(state["active_context"]["task_progress"]["recorded_revision"], 3)
        self.assertEqual(state["active_context"]["autonomy_metrics"]["automatic_operations"], 1)
        for path, identity in before.items():
            if Path(path).name != "state.json":
                self.assertEqual(self._authority_snapshot()[path], identity)

    def test_c02_fresh_process_and_status_are_read_only(self) -> None:
        self._send(self._progress_request())
        before = self._authority_snapshot()
        child = subprocess.run(
            [sys.executable, "-m", "evospec.agent_protocol", "--root", str(self.root)],
            input=b'{"action":"context"}', capture_output=True, check=True,
        )
        first = self._context()
        second = json.loads(child.stdout)["context"]
        self.assertEqual(first, second)
        output = io.StringIO()
        self.assertEqual(cli_main(["--project", str(self.root), "status"], output), 0)
        self.assertIn("REPORTED_UNVERIFIED", output.getvalue())
        self.assertIn(CHECKPOINT, output.getvalue())
        self.assertEqual(before, self._authority_snapshot())

    def test_c03_wrong_authority_or_replayed_revision_never_mutates(self) -> None:
        baseline = self._authority_snapshot()
        base = self._progress_request()
        for field, value in (
            ("repository_id", "other-root"), ("task_id", "task-other"),
            ("feature_agreement_ref", "agreement-feature-other"),
            ("slice_agreement_ref", "agreement-slice-other"),
        ):
            with self.subTest(field=field):
                response = self._send({**base, field: value})
                self.assertIn("error", response)
                self.assertEqual(baseline, self._authority_snapshot())
        accepted = self._send(base)
        self.assertEqual(accepted["result"]["revision"], 3)
        committed = self._authority_snapshot()
        replay = self._send(base)
        self.assertEqual(replay["error"]["code"], "STALE_REVISION")
        self.assertEqual(committed, self._authority_snapshot())

    def test_c04_generic_progress_is_blocked_before_any_lifecycle_publication(self) -> None:
        before = self._authority_snapshot()
        response = self._send(_request(
            "progress", expected_revision=2, current_action="implement", focus="replacement",
            completed_work=[], current_work="changed Task", pending_work=[], resume_point="false",
        ))
        self.assertIn("error", response)
        self.assertEqual(before, self._authority_snapshot())
        self.assertEqual(self._context()["lifecycle"], ["PROPOSED", "RESEARCH"])

    def test_c05_concurrent_same_revision_yields_exactly_one_report(self) -> None:
        request = self._progress_request()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self._send(request), range(2)))
        self.assertEqual(sum("result" in item for item in responses), 1)
        self.assertEqual(sum("error" in item for item in responses), 1)
        self.assertEqual(CoreService.open(self.root).recover().revision, 3)

    def test_c06_precommit_failure_and_postreplace_uncertainty(self) -> None:
        request = self._progress_request()
        core = CoreService.open(self.root, FailStateReplace())
        before = self._authority_snapshot()
        with self.assertRaises(ServiceError):
            _record_with_core(core, request)
        self.assertEqual(before, self._authority_snapshot())
        uncertain = CoreService.open(self.root, FailStateDirectorySync())
        with self.assertRaises(DurabilityUncertainError):
            _record_with_core(uncertain, request)
        self.assertEqual(CoreService.open(self.root).recover().revision, 3)
        self.assertEqual(self._send(request)["error"]["code"], "STALE_REVISION")

    def test_c07_schema13_requires_interactive_revision_bound_optin(self) -> None:
        self._schema13()
        request = self._progress_request()
        before = self._authority_snapshot()
        blocked = self._send(request)
        self.assertIn("E1_SCHEMA_UPGRADE_REQUIRED", blocked["error"]["detail"])
        read_only = self._context()
        self.assertFalse(read_only["e1_schema_ready"])
        self.assertEqual(read_only["legal_actions"], ["context"])
        self.assertEqual(before, self._authority_snapshot())
        args = ["--project", str(self.root), "upgrade-e1-schema", "--expected-revision", "2"]
        output = io.StringIO()
        self.assertEqual(cli_main(args, output), 2)
        self.assertIn("interactive user terminal", output.getvalue())
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch("builtins.input", return_value="NO"):
            self.assertEqual(cli_main(args, io.StringIO()), 2)
        self.assertEqual(before, self._authority_snapshot())
        statement = self.workflow.e1_upgrade_agreement(expected_revision=ExpectedRevision(2))
        token = sha256(statement.encode("utf-8")).hexdigest()[:12]
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch("builtins.input", return_value=f"ACEPTO {token}"):
            self.assertEqual(cli_main(args, io.StringIO()), 0)
        self.assertEqual(CoreService.open(self.root).recover().revision, 3)
        state_path = self.root / ".evospec/state.json"
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], 14)
        receipt = json.loads(state_path.read_text(encoding="utf-8"))["e1_schema_upgrade"]
        self.assertEqual(receipt["source_revision"], 2)
        self.assertEqual(receipt["converted_revision"], 3)
        self.assertEqual(receipt["source"], "INTERACTIVE_TERMINAL")
        committed = self._authority_snapshot()
        # The same command acknowledges the recovered conversion without prompting.
        self.assertEqual(cli_main(args, io.StringIO()), 0)
        self.assertEqual(committed, self._authority_snapshot())
        self.assertIsNone(self._context()["task_progress"])
        after = self._authority_snapshot()
        for path, identity in before.items():
            if Path(path).name != "state.json":
                self.assertEqual(after[path], identity)
        self.assertEqual(self._send({**request, "expected_revision": 3})["result"]["revision"], 4)

    def test_c08_schema13_ordinary_auxiliary_writer_fails_without_orphans(self) -> None:
        self._schema13()
        core = CoreService.open(self.root)
        current = core.recover()
        assert current.active_context is not None and current.active_context.change_id is not None
        before = self._authority_snapshot()
        with self.assertRaises(ServiceError):
            core.record_observation(
                current, Observation(ObservationId("observation-scope-guard"),
                                     current.active_context.change_id, "RESULT", "unverified observation"),
            )
        self.assertEqual(before, self._authority_snapshot())

    def test_c09_tampered_typed_record_and_copied_root_are_rejected(self) -> None:
        self._send(self._progress_request())
        with tempfile.TemporaryDirectory(prefix="evospec-e1c-copy-") as other:
            copied = Path(other)
            shutil.copytree(self.root / ".evospec", copied / ".evospec")
            with self.assertRaises(RecoveryError):
                CoreService.open(copied).recover()
        path = self.root / ".evospec/state.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["active_context"]["task_progress"]["report_kind"] = "VERIFIED"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            CoreService.open(self.root).recover()

    def test_c10_protocol_rejects_extra_or_missing_report_fields(self) -> None:
        original = self._progress_request()
        before = self._authority_snapshot()
        for bad in ({**original, "unapproved_scope": True},
                    {key: value for key, value in original.items() if key != "task_id"},
                    {**original, "evidence_refs": [""]},
                    {**original, "statement": " "}):
            response = self._send(bad)
            self.assertIn("error", response)
            self.assertEqual(before, self._authority_snapshot())

    def test_c11_upgrade_binds_exact_source_bytes_even_at_same_revision(self) -> None:
        self._schema13()
        statement = self.workflow.e1_upgrade_agreement(expected_revision=ExpectedRevision(2))
        token = sha256(statement.encode("utf-8")).hexdigest()[:12]
        path = self.root / ".evospec/state.json"
        # Reformat identical decoded authority while preserving its revision.
        # The original confirmation must not silently cover different source bytes.
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=2), encoding="utf-8")
        with self.assertRaises(WorkflowError):
            self.workflow.upgrade_e1_schema(
                UserAcceptance(statement, f"tty:{token}"), expected_revision=ExpectedRevision(2),
            )
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 13)

    def test_c12_competing_schema_upgrades_commit_once(self) -> None:
        self._schema13()
        statement = self.workflow.e1_upgrade_agreement(expected_revision=ExpectedRevision(2))
        token = sha256(statement.encode("utf-8")).hexdigest()[:12]
        acceptance = UserAcceptance(statement, f"tty:{token}")

        def upgrade() -> str:
            try:
                result = WorkflowService.open(self.root).upgrade_e1_schema(
                    acceptance, expected_revision=ExpectedRevision(2),
                )
                return result.code
            except (WorkflowError, StaleRevisionError):
                return "REJECTED"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: upgrade(), range(2)))
        self.assertIn("E1_SCHEMA_UPGRADED", results)
        self.assertTrue(set(results) <= {"E1_SCHEMA_UPGRADED", "E1_SCHEMA_ALREADY_UPGRADED", "REJECTED"})
        recovered = CoreService.open(self.root).recover()
        self.assertEqual(recovered.revision, 3)
        assert recovered.active_context is not None
        self.assertIsNone(recovered.active_context.task_progress)
        self.assertTrue(WorkflowService.open(self.root).e1_schema_ready())

    def test_c13_no_e1_conversion_for_non_e1_or_old_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="evospec-non-e1-") as other:
            separate = WorkflowService.open(Path(other))
            separate.init()
            with self.assertRaises(WorkflowError):
                separate.e1_upgrade_agreement(expected_revision=ExpectedRevision(1))
        self._schema13()
        path = self.root / ".evospec/state.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema"] = 12
        payload.pop("agreement_refs")
        payload["active_context"].pop("feature_agreement_ref")
        payload["active_context"].pop("slice_agreement_ref")
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(WorkflowError):
            self.workflow.e1_upgrade_agreement(expected_revision=ExpectedRevision(2))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 12)

    def test_c14_exhausted_autonomy_budget_never_writes_another_report(self) -> None:
        path = self.root / ".evospec/state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        # Set a restrictive, valid envelope to prove Core honors its budget.
        state["active_context"]["autonomy_envelope"]["operation_budget"] = 1
        path.write_text(json.dumps(state), encoding="utf-8")
        first = self._send(self._progress_request())
        self.assertEqual(first["result"]["revision"], 3)
        before = self._authority_snapshot()
        self.assertNotIn("accepted_task_progress", self._context()["legal_actions"])
        refused = self._send(self._progress_request())
        self.assertIn("error", refused)
        self.assertEqual(before, self._authority_snapshot())

    def test_c15_changed_focus_is_not_advertised_or_accepted(self) -> None:
        path = self.root / ".evospec/state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        # A persisted focus outside the human agreement must fail closed.
        state["active_context"]["focus"] = "Unapproved replacement Slice"
        path.write_text(json.dumps(state), encoding="utf-8")
        before = self._authority_snapshot()
        self.assertNotIn("accepted_task_progress", self._context()["legal_actions"])
        refused = self._send(self._progress_request())
        self.assertIn("error", refused)
        self.assertEqual(before, self._authority_snapshot())

    def test_c16_new_schema14_without_e1_keeps_legacy_working(self) -> None:
        with tempfile.TemporaryDirectory(prefix="evospec-non-e1-v14-") as other:
            root = Path(other)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Standard non-E1 local feature")
            workflow.record_progress("implement", "standard feature", (), "current task", (), "resume")
            state_path = root / ".evospec/state.json"
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], 14)
            recovered = project_context(WorkflowService.open(root))
            self.assertIsNone(recovered.accepted_slice)
            self.assertIsNone(recovered.task_progress)
            self.assertEqual(recovered.progress.resume_point, "resume")


if __name__ == "__main__":
    unittest.main()
