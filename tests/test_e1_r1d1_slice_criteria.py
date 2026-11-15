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
    def setUp(self) -> None:
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

    def _context(self) -> dict:
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

    def test_r1_06_legacy_schema13_stays_not_recorded_until_explicit_amendment(self) -> None:
        first = self._accept()
        path = self.root / ".evospec/state.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        legacy["schema"] = 13
        legacy.pop("slice_criteria_refs")
        legacy.pop("slice_criteria_heads")
        path.write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(self._context()["criteria_status"], "NOT_RECORDED")
        self.assertIsNone(self._context()["criteria_revision"])
        self.assertEqual(self._revise("NOT_RECORDED", _spec(), "Criterios explicitados después del legado", first), first + 1)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 15)
        self.assertEqual(self._context()["criteria_status"], "RECORDED")

    def test_r1_07_unknown_schema14_is_never_silently_reinterpreted(self) -> None:
        path = self.root / ".evospec/state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["schema"] = 14
        path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            CoreService.open(self.root).recover()

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
            self.assertEqual(cli_main(args, io.StringIO()), 0)
        head = self._context()["criteria_revision"]["id"]
        reason = "Ajuste de prioridad"
        decision = slice_criteria_decision_statement(head, reason, spec)
        amend_args = ["--project", str(self.root), "revise-slice-criteria",
                      "--criteria-file", str(criteria_file), "--previous-criteria-revision", head,
                      "--reason", reason, "--expected-revision", str(self.initial.revision + 1)]
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch(
            "builtins.input", return_value=f"DECIDO {sha256(decision.encode()).hexdigest()[:12]}"
        ):
            self.assertEqual(cli_main(amend_args, io.StringIO()), 0)
        self.assertEqual(len(CoreService.open(self.root).recover().slice_criteria_revisions), 2)

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
