"""First E1 Slice: explicit acceptance, single Core commit, and read-only recovery."""

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
from unittest.mock import patch

from evospec.agent_protocol import execute
from evospec.cli import main as cli_main
from evospec.domain import ExpectedRevision
from evospec.persistence import FileOperations, RecoveryError
from evospec.services import CoreService, ServiceError, StaleRevisionError, UserAcceptance
from evospec.workflow import WorkflowError, WorkflowService


FEATURE = "Aplicación móvil para tareas"
SLICE = "Primera pantalla de tareas"
TASK = "Implementar la lista de tareas local"
CHECKPOINT = "Listado local comprobado"
STATEMENT = (
    f"Acepto Feature: {FEATURE}; Slice: {SLICE}; "
    f"Task: {TASK}; checkpoint: {CHECKPOINT}"
)


class FailStateReplace(FileOperations):
    """Leave previously flushed immutable records but no published new State."""

    def replace(self, source: Path, target: Path) -> None:
        if target.name == "state.json":
            raise OSError("injected state publication failure")
        super().replace(source, target)


class FailDirectorySync(FileOperations):
    """Exercise a failure on either side of the authoritative State boundary."""

    def __init__(self, directory_name: str) -> None:
        self.directory_name = directory_name

    def fsync_directory(self, directory: Path) -> None:
        if directory.name == self.directory_name:
            raise OSError("injected durability uncertainty")
        super().fsync_directory(directory)


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class FirstSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="evospec-e1-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workflow = WorkflowService.open(self.root)
        self.initial = self.workflow.init()
        self.acceptance = UserAcceptance(STATEMENT, f"tty:{sha256(STATEMENT.encode()).hexdigest()[:12]}")

    def _accept(self, workflow: WorkflowService | None = None):
        facade = self.workflow if workflow is None else workflow
        return facade.accept_first_slice(
            FEATURE, SLICE, TASK, CHECKPOINT, self.acceptance,
            expected_revision=ExpectedRevision(self.initial.revision),
        )

    def _context(self) -> dict:
        response = json.loads(execute(self.root, json.dumps({"action": "context"})))
        self.assertNotIn("error", response)
        return response["context"]

    def test_b01_accepts_two_immutable_records_one_revision_and_stable_task(self) -> None:
        accepted = self._accept()
        self.assertEqual(accepted.code, "SLICE_ACCEPTED")
        self.assertEqual(accepted.revision, self.initial.revision + 1)
        recovered = CoreService.open(self.root).recover()
        self.assertEqual(len(recovered.agreements), 2)
        self.assertEqual([item.kind for item in recovered.agreements], ["FEATURE", "SLICE"])
        self.assertEqual(recovered.active_context.current_work, TASK)
        self.assertEqual(recovered.active_context.checkpoints[0].name, CHECKPOINT)
        self.assertFalse(recovered.active_context.checkpoints[0].valid)
        self.assertEqual(recovered.active_context.feature_agreement_ref, recovered.agreements[0].id)
        self.assertEqual(recovered.active_context.slice_agreement_ref, recovered.agreements[1].id)
        self.assertEqual(len(list((self.root / ".evospec/records/agreements").glob("*.json"))), 2)
        self.assertEqual(len(list((self.root / ".evospec/records/changes").glob("*.json"))), 1)

    def test_b02_new_protocol_session_recovers_exact_same_scope_and_task(self) -> None:
        self._accept()
        first = self._context()
        # A new Python process models the child protocol process after host restart.
        separate_process = subprocess.run(
            [sys.executable, "-m", "evospec.agent_protocol", "--root", str(self.root)],
            input=b'{"action":"context"}', capture_output=True, check=True,
        )
        second = json.loads(separate_process.stdout)["context"]
        self.assertEqual(first["accepted_slice"], second["accepted_slice"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(first["accepted_slice"]["initial_task"], TASK)
        self.assertEqual(first["progress"]["current_work"], TASK)
        self.assertEqual(first["repository_id"], self.initialized_root_id())

    def initialized_root_id(self) -> str:
        return sha256(str(self.root.resolve()).encode()).hexdigest()[:16]

    def test_b03_status_reads_without_rewriting_any_authority(self) -> None:
        self._accept()
        before = {
            path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (self.root / ".evospec").rglob("*") if path.is_file()
        }
        self._context()
        output = io.StringIO()
        self.assertEqual(cli_main(["--project", str(self.root), "status"], output), 0)
        self.assertIn(f"Current: {TASK}", output.getvalue())
        after = {
            path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (self.root / ".evospec").rglob("*") if path.is_file()
        }
        self.assertEqual(before, after)

    def test_b04_copied_repository_root_is_not_trusted(self) -> None:
        self._accept()
        with tempfile.TemporaryDirectory(prefix="evospec-copied-") as other:
            copied = Path(other)
            shutil.copytree(self.root / ".evospec", copied / ".evospec")
            with self.assertRaises(RecoveryError):
                WorkflowService.open(copied).status()

    def test_b05_failed_state_publish_leaves_inert_records_and_safe_retry(self) -> None:
        core = CoreService.open(self.root, FailStateReplace())
        with self.assertRaises(ServiceError):
            core.accept_first_slice(
                core.recover(), FEATURE, SLICE, TASK, CHECKPOINT, self.acceptance,
            )
        original = CoreService.open(self.root).recover()
        self.assertEqual(original.revision, self.initial.revision)
        self.assertEqual(original.agreements, ())
        self.assertEqual(len(list((self.root / ".evospec/records/agreements").glob("*.json"))), 2)
        recovered = self._accept()
        self.assertEqual(recovered.code, "SLICE_ACCEPTED")
        self.assertEqual(len(CoreService.open(self.root).recover().agreements), 2)

    def test_b06_concurrent_cas_rejects_duplicate_publisher(self) -> None:
        def publish() -> str:
            try:
                self._accept(WorkflowService.open(self.root))
                return "SUCCESS"
            except (StaleRevisionError, WorkflowError):
                return "REJECTED"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: publish(), range(2)))
        self.assertEqual(sorted(results), ["REJECTED", "SUCCESS"])
        self.assertEqual(CoreService.open(self.root).recover().revision, self.initial.revision + 1)

    def test_b07_tampering_agreement_reference_fails_closed(self) -> None:
        self._accept()
        path = self.root / ".evospec/state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["agreement_refs"][0]["path"] = "records/agreements/not-authoritative.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            CoreService.open(self.root).recover()

    def test_b08_unconfirmed_and_headless_cli_cannot_mutate(self) -> None:
        args = ["--project", str(self.root), "accept-slice", "--feature", FEATURE,
                "--slice", SLICE, "--task", TASK, "--checkpoint", CHECKPOINT,
                "--expected-revision", str(self.initial.revision)]
        sink = io.StringIO()
        self.assertEqual(cli_main(args, sink), 2)
        self.assertIn("interactive user terminal", sink.getvalue())
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch("builtins.input", return_value="NO"):
            self.assertEqual(cli_main(args, io.StringIO()), 2)
        self.assertEqual(CoreService.open(self.root).recover().revision, self.initial.revision)

    def test_b09_interactive_cli_requires_exact_consent_phrase(self) -> None:
        args = ["--project", str(self.root), "accept-slice", "--feature", FEATURE,
                "--slice", SLICE, "--task", TASK, "--checkpoint", CHECKPOINT,
                "--expected-revision", str(self.initial.revision)]
        token = sha256(STATEMENT.encode("utf-8")).hexdigest()[:12]
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch("builtins.input", return_value=f"ACEPTO {token}"):
            self.assertEqual(cli_main(args, io.StringIO()), 0)
        self.assertEqual(self._context()["accepted_slice"]["source"], "INTERACTIVE_TERMINAL")

    def test_b10_second_scope_and_stale_revision_are_blocked(self) -> None:
        self._accept()
        with self.assertRaises(StaleRevisionError):
            self._accept()
        with self.assertRaises(WorkflowError):
            WorkflowService.open(self.root).accept_first_slice(
                "Other feature", "Other slice", TASK, CHECKPOINT, self.acceptance,
                expected_revision=ExpectedRevision(self.initial.revision + 1),
            )

    def test_b11_inferred_or_mismatched_agreement_is_rejected(self) -> None:
        with self.assertRaises(WorkflowError):
            self.workflow.accept_first_slice(
                FEATURE, SLICE, TASK, CHECKPOINT,
                UserAcceptance("agent inferred yes", "tty:fake"),
                expected_revision=ExpectedRevision(self.initial.revision),
            )
        self.assertEqual(CoreService.open(self.root).recover().agreements, ())

    def test_b12_legacy_readability_does_not_authorize_new_e1_writes(self) -> None:
        state_path = self.root / ".evospec/state.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy["schema"] = 12
        legacy.pop("agreement_refs")
        state_path.write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(CoreService.open(self.root).recover().revision, self.initial.revision)
        with self.assertRaises(WorkflowError):
            self._accept()
        self.assertEqual(CoreService.open(self.root).recover().revision, self.initial.revision)

    def test_b13_precommit_directory_sync_failure_remains_retryable(self) -> None:
        core = CoreService.open(self.root, FailDirectorySync("agreements"))
        with self.assertRaises(ServiceError):
            core.accept_first_slice(core.recover(), FEATURE, SLICE, TASK, CHECKPOINT, self.acceptance)
        self.assertEqual(CoreService.open(self.root).recover().revision, self.initial.revision)
        self._accept()
        self.assertEqual(CoreService.open(self.root).recover().revision, self.initial.revision + 1)

    def test_b14_postcommit_directory_sync_failure_requires_recovery(self) -> None:
        core = CoreService.open(self.root, FailDirectorySync(".evospec"))
        with self.assertRaises(ServiceError):
            core.accept_first_slice(core.recover(), FEATURE, SLICE, TASK, CHECKPOINT, self.acceptance)
        recovered = CoreService.open(self.root).recover()
        self.assertEqual(recovered.revision, self.initial.revision + 1)
        self.assertEqual(len(recovered.agreements), 2)
        with self.assertRaises(StaleRevisionError):
            self._accept()


if __name__ == "__main__":
    unittest.main()
