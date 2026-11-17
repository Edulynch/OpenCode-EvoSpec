"""R1-D1: attributable Slice criteria with immutable revisions and legacy recovery."""

from __future__ import annotations

import io
import json
import subprocess
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
from evospec.cli import main as cli_main
from evospec.domain import ExpectedRevision, SliceCriterion
from evospec.persistence import FileOperations, RecoveryError
from evospec.services import (
    CoreService, ServiceError, SliceCriteriaSpec, StaleRevisionError,
    UserAcceptance, slice_acceptance_statement, slice_criteria_decision_statement,
)
from evospec.workflow import WorkflowError, WorkflowService


FEATURE = "Agenda personal"
SLICE = "Lista local de tareas"
TASK = "Mostrar lista persistida"
CHECKPOINT = "Lista renderizada después de reiniciar"


def _spec(outcome: str = "El usuario ve sus tareas después de reiniciar") -> SliceCriteriaSpec:
    return SliceCriteriaSpec(
        outcome,
        "Dada una tarea guardada, al volver a abrir la app aparece en la lista",
        ("Mostrar tareas existentes", "Conservarlas localmente"),
        ("No hay login", "No se sincronizan varios dispositivos"),
        ("Usar almacenamiento local para este Slice",),
        (SliceCriterion("AC-1", "La tarea guardada reaparece", "Prueba de reinicio local"),
         SliceCriterion("AC-2", "Lista vacía muestra un mensaje", "Captura de estado vacío")),
    )


def _provenance(statement: str) -> UserAcceptance:
    return UserAcceptance(statement, f"tty:{sha256(statement.encode()).hexdigest()[:12]}")


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class FailCriteriaSync(FileOperations):
    """Surface durability uncertainty before the Core State head can change."""

    def fsync_directory(self, directory: Path) -> None:
        if directory.name == "slice-criteria":
            raise OSError("injected criteria durability failure")
        super().fsync_directory(directory)


class SliceCriteriaTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        # Each test receives its own repository and authority lifetime.
        temporary = tempfile.TemporaryDirectory(prefix="evospec-r1d1-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workflow = WorkflowService.open(self.root)
        self.initial = self.workflow.init()

    def _accept(self, spec: SliceCriteriaSpec | None = None) -> int:
        statement = slice_acceptance_statement(FEATURE, SLICE, TASK, CHECKPOINT, spec)
        result = self.workflow.accept_first_slice(
            FEATURE, SLICE, TASK, CHECKPOINT, _provenance(statement),
            expected_revision=ExpectedRevision(self.initial.revision), criteria=spec,
        )
        return result.revision

    def _revise(self, previous: str, spec: SliceCriteriaSpec, reason: str, revision: int) -> int:
        statement = slice_criteria_decision_statement(previous, reason, spec)
        return self.workflow.revise_slice_criteria(
            spec, previous, reason, _provenance(statement), expected_revision=ExpectedRevision(revision),
        ).revision

    def _context(self) -> dict[str, Any]:
        payload = json.loads(execute(self.root, json.dumps({"action": "context"})))
        self.assertNotIn("error", payload)
        return payload["context"]["accepted_slice"]

    def test_r1_01_full_scope_single_terminal_acceptance_and_exact_recovery(self) -> None:
        revision = self._accept(_spec())
        state = CoreService.open(self.root).recover()
        self.assertEqual(revision, self.initial.revision + 1)
        self.assertEqual(len(state.slice_criteria_revisions), 1)
        self.assertEqual(len(state.slice_criteria_heads), 1)
        current = self._context()
        self.assertEqual(current["criteria_status"], "RECORDED")
        self.assertEqual(current["criteria_revision"]["criteria"][0]["id"], "AC-1")
        self.assertEqual(current["criteria_revision"]["exclusions"][0], "No hay login")
        self.assertEqual(current["criteria_revision"]["source"], "INTERACTIVE_TERMINAL")
        self.assertIsNone(current["criteria_revision"]["previous_revision_id"])
        self.assertEqual(state.agreements[0].source_reference, state.slice_criteria_revisions[0].source_reference)
        snapshot = {
            path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (self.root / ".evospec").rglob("*") if path.is_file()
        }
        separate = subprocess.run(
            [sys.executable, "-m", "evospec.agent_protocol", "--root", str(self.root)],
            input=b'{"action":"context"}', capture_output=True, check=True,
        )
        self.assertEqual(current, json.loads(separate.stdout)["context"]["accepted_slice"])
        self.assertEqual(snapshot, {
            path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (self.root / ".evospec").rglob("*") if path.is_file()
        })

    def test_r1_02_revision_preserves_predecessor_and_initial_agreement(self) -> None:
        first = self._accept(_spec())
        old = CoreService.open(self.root).recover()
        original = old.slice_criteria_revisions[0]
        record_path = self.root / ".evospec/records/slice-criteria" / f"{original.id}.json"
        original_bytes = record_path.read_bytes()
        updated = _spec("El usuario también puede filtrar la lista local")
        new_revision = self._revise(original.id, updated, "Filtro aprobado para este Slice", first)
        current = CoreService.open(self.root).recover()
        self.assertEqual(new_revision, first + 1)
        self.assertEqual(current.agreements, old.agreements)
        self.assertEqual(current.slice_criteria_revisions[0], original)
        self.assertEqual(current.slice_criteria_revisions[1].previous_revision_id, original.id)
        self.assertEqual(current.slice_criteria_revisions[1].source, "INTERACTIVE_TERMINAL_DECISION")
        self.assertEqual(current.slice_criteria_heads[0].revision_id, current.slice_criteria_revisions[1].id)
        self.assertEqual(self._context()["criteria_revision"]["outcome"], updated.outcome)
        self.assertEqual(record_path.read_bytes(), original_bytes)

    def test_r1_03_stale_cas_and_previous_head_stop_without_other_mutations(self) -> None:
        first = self._accept(_spec())
        head = self._context()["criteria_revision"]["id"]
        self._revise(head, _spec("Versión 2"), "Se ajustó el resultado esperado", first)
        with self.assertRaises(StaleRevisionError):
            self._revise(head, _spec("Versión 3"), "Vieja revisión", first)
        with self.assertRaises(WorkflowError):
            self._revise(head, _spec("Versión 3"), "Vieja cabecera", first + 1)
        self.assertEqual(len(CoreService.open(self.root).recover().slice_criteria_revisions), 2)

    def test_r1_04_unattributed_or_partial_proposals_never_publish(self) -> None:
        statement = slice_acceptance_statement(FEATURE, SLICE, TASK, CHECKPOINT, _spec())
        with self.assertRaises(WorkflowError):
            self.workflow.accept_first_slice(
                FEATURE, SLICE, TASK, CHECKPOINT, _provenance(statement.replace("AC-1", "AC-4")),
                expected_revision=ExpectedRevision(self.initial.revision), criteria=_spec(),
            )
        with self.assertRaises(WorkflowError):
            self.workflow.accept_first_slice(
                FEATURE, SLICE, TASK, CHECKPOINT, _provenance(statement),
                expected_revision=ExpectedRevision(self.initial.revision),
                criteria=replace(_spec(), criteria=()),
            )
        first = self._accept(_spec())
        head = self._context()["criteria_revision"]["id"]
        with self.assertRaises(WorkflowError):
            self.workflow.revise_slice_criteria(
                _spec("Sin prueba"), head, "Decisión inferida",
                UserAcceptance("inferred yes", "tty:fake"), expected_revision=ExpectedRevision(first),
            )
        self.assertEqual(len(CoreService.open(self.root).recover().slice_criteria_revisions), 1)

    def test_r1_05_tampered_criterion_ref_fails_closed(self) -> None:
        self._accept(_spec())
        path = self.root / ".evospec/state.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["slice_criteria_refs"][0]["path"] = "records/slice-criteria/not-authoritative.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            CoreService.open(self.root).recover()

    def test_r1_05b_tampered_record_cannot_reuse_human_authorization(self) -> None:
        self._accept(_spec())
        revision = CoreService.open(self.root).recover().slice_criteria_revisions[0]
        path = self.root / ".evospec/records/slice-criteria" / f"{revision.id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["criteria"][0]["expected_result"] = "Falso resultado aceptado"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            CoreService.open(self.root).recover()

    def test_r1_06_legacy_schema13_requires_approved_upgrade_before_amendment(self) -> None:
        first = self._accept()
        path = self.root / ".evospec/state.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        legacy["schema"] = 13
        legacy.pop("slice_criteria_refs")
        legacy.pop("slice_criteria_heads")
        legacy.pop("e1_schema_upgrade")
        # Historical context records cannot carry the newer Task-progress slot.
        for key in ("active_context", "preserved_primary_context"):
            if legacy[key] is not None:
                legacy[key].pop("task_progress", None)
        path.write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(self._context()["criteria_status"], "NOT_RECORDED")
        self.assertIsNone(self._context()["criteria_revision"])
        original_files = {
            file.relative_to(self.root): (file.read_bytes(), file.stat().st_mtime_ns)
            for file in (self.root / ".evospec").rglob("*") if file.is_file()
        }
        with self.assertRaises(WorkflowError):
            self._revise("NOT_RECORDED", _spec(), "Criterios explicitados después del legado", first)
        self.assertEqual(original_files, {
            file.relative_to(self.root): (file.read_bytes(), file.stat().st_mtime_ns)
            for file in (self.root / ".evospec").rglob("*") if file.is_file()
        })
        agreement = self.workflow.e1_upgrade_agreement(expected_revision=ExpectedRevision(first))
        upgraded = self.workflow.upgrade_e1_schema(
            _provenance(agreement), expected_revision=ExpectedRevision(first),
        )
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 14)
        receipt = CoreService.open(self.root).recover().e1_schema_upgrade
        self.assertIsNotNone(receipt)
        self.assertEqual(
            self._revise("NOT_RECORDED", _spec(), "Criterios explicitados después del legado", upgraded.revision),
            upgraded.revision + 1,
        )
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 15)
        self.assertEqual(CoreService.open(self.root).recover().e1_schema_upgrade, receipt)
        self.assertEqual(self._context()["criteria_status"], "RECORDED")

    def test_r1_07_schema14_cannot_claim_newer_criteria_authority(self) -> None:
        self._accept(_spec())
        path = self.root / ".evospec/state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["schema"] = 14
        path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            CoreService.open(self.root).recover()

    def test_r1_07a_schema14_progress_survives_explicit_criteria_revision(self) -> None:
        """A separately decided criterion revision preserves accepted Task evidence."""
        first = self._accept()
        path = self.root / ".evospec/state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(state["schema"], 15)
        state["schema"] = 14
        state.pop("slice_criteria_refs")
        state.pop("slice_criteria_heads")
        path.write_text(json.dumps(state), encoding="utf-8")

        accepted = CoreService.open(self.root).recover()
        feature, slice_record = accepted.agreements
        assert accepted.initialization is not None
        progress = self.workflow.accepted_task_progress(
            accepted.initialization.repository_id,
            str(slice_record.task_id),
            feature.id,
            slice_record.id,
            "Lista visible",
            "Primera implementación reportada sin verificar",
            ("tests/task-list.test.ts",),
            expected_revision=ExpectedRevision(first),
        )
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 14)
        original_agreements = {
            file.name: (file.read_bytes(), file.stat().st_mtime_ns)
            for file in (self.root / ".evospec/records/agreements").iterdir()
        }
        original_report = CoreService.open(self.root).recover().active_context
        assert original_report is not None
        self.assertIsNotNone(original_report.task_progress)

        revised = self._revise(
            "NOT_RECORDED", _spec(), "Se precisan criterios para el Slice",
            progress.revision,
        )
        self.assertEqual(revised, progress.revision + 1)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 15)
        current = CoreService.open(self.root).recover()
        self.assertEqual(current.agreements, accepted.agreements)
        assert current.active_context is not None
        self.assertEqual(current.active_context.task_progress, original_report.task_progress)
        self.assertEqual(self._context()["criteria_status"], "RECORDED")
        self.assertEqual(original_agreements, {
            file.name: (file.read_bytes(), file.stat().st_mtime_ns)
            for file in (self.root / ".evospec/records/agreements").iterdir()
        })

    def test_r1_07c_schema15_task_progress_preserves_criteria_history_and_head(self) -> None:
        revision = self._accept(_spec())
        before = CoreService.open(self.root).recover()
        state_path = self.root / ".evospec/state.json"
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], 15)
        self.assertEqual(len(before.slice_criteria_revisions), 1)
        self.assertEqual(len(before.slice_criteria_heads), 1)
        feature, slice_record = before.agreements
        assert before.initialization is not None
        criteria_record_files = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (self.root / ".evospec/records/slice-criteria").glob("*.json")
        }
        self.assertEqual(len(criteria_record_files), 1)

        progress = self.workflow.accepted_task_progress(
            before.initialization.repository_id,
            str(slice_record.task_id),
            feature.id,
            slice_record.id,
            "Lista visible",
            "Implementaci\u00f3n reportada sin verificar",
            ("tests/task-list.test.ts",),
            expected_revision=ExpectedRevision(revision),
        )
        self.assertEqual(progress.revision, revision + 1)
        recovered = CoreService.open(self.root).recover()
        self.assertEqual(recovered.revision, progress.revision)
        self.assertEqual(recovered.agreements, before.agreements)
        self.assertEqual(recovered.slice_criteria_revisions, before.slice_criteria_revisions)
        self.assertEqual(recovered.slice_criteria_heads, before.slice_criteria_heads)
        self.assertEqual(criteria_record_files, {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (self.root / ".evospec/records/slice-criteria").glob("*.json")
        })
        assert recovered.active_context is not None
        report = recovered.active_context.task_progress
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.report_kind, "REPORTED_UNVERIFIED")
        self.assertEqual(report.recorded_revision, progress.revision)
        self.assertEqual(report.checkpoint, "Lista visible")
        context = self._context()
        self.assertEqual(context["criteria_status"], "RECORDED")
        self.assertEqual(context["criteria_revision"]["id"], before.slice_criteria_heads[0].revision_id)

    def test_r1_07b_old_schema_cannot_discard_nonempty_criteria(self) -> None:
        self._accept(_spec())
        path = self.root / ".evospec/state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["schema"] = 13
        path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            CoreService.open(self.root).recover()

    def test_r1_08_single_prompt_cli_accept_and_separate_decision(self) -> None:
        spec = _spec()
        criteria_file = self.root / "mini-spec.json"
        criteria_file.write_text(json.dumps({
            "outcome": spec.outcome, "example": spec.example,
            "inclusions": list(spec.inclusions), "exclusions": list(spec.exclusions),
            "decisions": list(spec.decisions),
            "criteria": [{"id": item.id, "expected_result": item.expected_result,
                          "expected_evidence": item.expected_evidence} for item in spec.criteria],
        }), encoding="utf-8")
        statement = slice_acceptance_statement(FEATURE, SLICE, TASK, CHECKPOINT, spec)
        args = ["--project", str(self.root), "accept-slice", "--feature", FEATURE,
                "--slice", SLICE, "--task", TASK, "--checkpoint", CHECKPOINT,
                "--criteria-file", str(criteria_file), "--expected-revision", str(self.initial.revision)]
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch(
            "builtins.input", return_value=f"ACEPTO {sha256(statement.encode()).hexdigest()[:12]}"
        ):
            self.assertEqual(cli_main(args, _TTY()), 0)
        head = self._context()["criteria_revision"]["id"]
        reason = "Ajuste de prioridad"
        decision = slice_criteria_decision_statement(head, reason, spec)
        amend_args = ["--project", str(self.root), "revise-slice-criteria",
                      "--criteria-file", str(criteria_file), "--previous-criteria-revision", head,
                      "--reason", reason, "--expected-revision", str(self.initial.revision + 1)]
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch(
            "builtins.input", return_value=f"DECIDO {sha256(decision.encode()).hexdigest()[:12]}"
        ):
            self.assertEqual(cli_main(amend_args, _TTY()), 0)
        self.assertEqual(len(CoreService.open(self.root).recover().slice_criteria_revisions), 2)

    def test_r1_08b_malformed_criterion_id_fails_as_input_error(self) -> None:
        """Malformed external IDs must never crash the CLI or cross acceptance."""
        spec = _spec()
        payload: dict[str, Any] = {
            "outcome": spec.outcome, "example": spec.example,
            "inclusions": list(spec.inclusions), "exclusions": list(spec.exclusions),
            "decisions": list(spec.decisions),
            "criteria": [{"id": item.id, "expected_result": item.expected_result,
                          "expected_evidence": item.expected_evidence} for item in spec.criteria],
        }
        criteria_file = self.root / "invalid-criteria.json"
        args = ["--project", str(self.root), "accept-slice", "--feature", FEATURE,
                "--slice", SLICE, "--task", TASK, "--checkpoint", CHECKPOINT,
                "--criteria-file", str(criteria_file), "--expected-revision", str(self.initial.revision)]
        for invalid_id in (None, 123, [], {}):
            with self.subTest(invalid_id=invalid_id):
                payload["criteria"][0]["id"] = invalid_id
                criteria_file.write_text(json.dumps(payload), encoding="utf-8")
                output = _TTY()
                with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()):
                    self.assertEqual(cli_main(args, output), 2)
                self.assertIn("ERROR", output.getvalue())
                self.assertEqual(CoreService.open(self.root).recover().revision, self.initial.revision)

    def test_r1_09_failed_record_durability_keeps_old_head_and_allows_retry(self) -> None:
        first = self._accept(_spec())
        state = CoreService.open(self.root).recover()
        head = state.slice_criteria_heads[0].revision_id
        spec = _spec("Nueva decisión")
        reason = "El resultado cambió"
        decision = _provenance(slice_criteria_decision_statement(head, reason, spec))
        with self.assertRaises(ServiceError):
            CoreService.open(self.root, FailCriteriaSync()).revise_slice_criteria(
                state, spec, head, reason, decision,
            )
        self.assertEqual(CoreService.open(self.root).recover().revision, first)
        self.assertEqual(self._revise(head, spec, reason, first), first + 1)

    def test_r1_10_concurrent_amendment_has_exactly_one_publisher(self) -> None:
        first = self._accept(_spec())
        head = self._context()["criteria_revision"]["id"]

        def attempt() -> str:
            try:
                statement = slice_criteria_decision_statement(head, "Revisión concurrente", _spec("Nuevo"))
                WorkflowService.open(self.root).revise_slice_criteria(
                    _spec("Nuevo"), head, "Revisión concurrente", _provenance(statement),
                    expected_revision=ExpectedRevision(first),
                )
                return "SUCCESS"
            except (StaleRevisionError, WorkflowError):
                return "REJECTED"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: attempt(), range(2))), ["REJECTED", "SUCCESS"])
        self.assertEqual(len(CoreService.open(self.root).recover().slice_criteria_revisions), 2)


if __name__ == "__main__":
    unittest.main()
