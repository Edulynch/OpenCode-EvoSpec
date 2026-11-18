"""R5-A: one explicitly accepted next Slice with immutable Core provenance."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from evospec.agent_protocol import execute
from evospec.cli import main as cli_main
from evospec.domain import (
    CriterionCoverageDeclaration, CoverageInput, E1SchemaUpgradeReceipt,
    ExpectedRevision, SliceCriterion, StateValidationError, validate_state,
)
from evospec.persistence import FileOperations
from evospec.services import (
    CoreService, DurabilityUncertainError, ServiceError, SliceCriteriaSpec,
    StaleRevisionError,
    UserAcceptance, next_slice_acceptance_statement, slice_acceptance_statement,
)
from evospec.workflow import WorkflowError, WorkflowService
from tests.test_e1_first_slice import _TTY


FEATURE = "Agenda personal"
FIRST = "Listado local"
SECOND = "Crear una tarea"
TASK = "Implementar creación local"
CHECKPOINT = "Nueva tarea visible después de reiniciar"


def consent(statement: str) -> UserAcceptance:
    """Bind test consent to the same statement/token contract as the terminal."""
    return UserAcceptance(statement, f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}")


def spec(outcome: str) -> SliceCriteriaSpec:
    return SliceCriteriaSpec(
        outcome,
        "Una tarea creada permanece visible después de reiniciar",
        ("Creación y lectura local",),
        ("Sin cuentas ni sincronización",),
        ("Persistencia local",),
        (SliceCriterion("AC-1", "La tarea reaparece", "python-unittest:tests.test_r5a_fixture"),),
    )


class FailNextStateReplace(FileOperations):
    """Prove that flushed immutable records alone never authorize a new Slice."""

    def replace(self, source: Path, target: Path) -> None:
        if target.name == "state.json":
            raise OSError("injected R5 State boundary failure")
        super().replace(source, target)


class FailNextStateDirectory(FileOperations):
    """Model uncertainty when directory sync fails after authoritative replace."""

    def fsync_directory(self, directory: Path) -> None:
        if directory.name == ".evospec":
            raise OSError("injected postcommit sync failure")
        super().fsync_directory(directory)


class NextSliceAcceptanceTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        # Each test owns a distinct fixture from construction through cleanup.
        # This also makes the fixture attributes explicit to static analysis.
        temporary = tempfile.TemporaryDirectory(prefix="evospec-r5a-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "test_r5a_fixture.py").write_text(
            "import unittest\n"
            "class AcceptedCriterion(unittest.TestCase):\n"
            "    def test_criterion(self):\n"
            "        self.assertTrue(True)\n", encoding="utf-8",
        )
        self.workflow = WorkflowService.open(self.root)
        initialized = self.workflow.init()
        first_spec = spec("Puedo ver mis tareas locales")
        first_statement = slice_acceptance_statement(
            FEATURE, FIRST, "Mostrar tareas", "Lista visible", first_spec,
        )
        accepted = self.workflow.accept_first_slice(
            FEATURE, FIRST, "Mostrar tareas", "Lista visible",
            consent(first_statement), expected_revision=ExpectedRevision(initialized.revision),
            criteria=first_spec,
        )
        self.first_authority = CoreService.open(self.root).recover()
        self.feature_id = str(self.first_authority.features[0].id)
        r3 = self.workflow.r3_upgrade_agreement(expected_revision=ExpectedRevision(accepted.revision))
        upgraded = self.workflow.upgrade_r3_schema(consent(r3), expected_revision=ExpectedRevision(accepted.revision))
        declarations = (
            CriterionCoverageDeclaration(
                "AC-1", "python-unittest", "tests.test_r5a_fixture",
                (CoverageInput("root-file", "tests/test_r5a_fixture.py"),),
            ),
        )
        r3b = self.workflow.r3b_upgrade_agreement(
            declarations, expected_revision=ExpectedRevision(upgraded.revision),
        )
        self.workflow.upgrade_r3b_schema(
            consent(r3b), declarations, expected_revision=ExpectedRevision(upgraded.revision),
        )
        self.next_spec = spec("Puedo crear una tarea local")

    def state(self):
        return CoreService.open(self.root).recover()

    def snapshot(self):
        """Compare all authority bytes, including immutable records, on rejected inputs."""
        managed = self.root / ".evospec"
        return {
            str(path.relative_to(managed)): path.read_bytes()
            for path in managed.rglob("*") if path.is_file()
        }

    def agreement(self) -> str:
        return next_slice_acceptance_statement(
            self.feature_id, FEATURE, SECOND, TASK, CHECKPOINT, self.next_spec,
        )

    def parked(self):
        self.workflow.park("a la espera de continuar")
        return self.state()

    def accept(self, *, expected_revision: int | None = None, acceptance: UserAcceptance | None = None):
        current = self.state()
        return self.workflow.accept_next_slice(
            self.feature_id, SECOND, TASK, CHECKPOINT,
            consent(self.agreement()) if acceptance is None else acceptance,
            expected_revision=ExpectedRevision(current.revision if expected_revision is None else expected_revision),
            criteria=self.next_spec,
        )

    def test_r5a_01_second_slice_persists_under_same_feature_without_rewriting_old_authority(self):
        before = self.parked()
        saved = self.accept()
        after = self.state()
        self.assertEqual(saved.code, "NEXT_SLICE_ACCEPTED")
        self.assertEqual(after.revision, before.revision + 1)
        self.assertEqual(after.features, before.features)
        self.assertEqual(after.agreements[:2], before.agreements)
        self.assertEqual(after.slice_criteria_revisions[:1], before.slice_criteria_revisions)
        self.assertEqual(after.work_items, before.work_items)
        self.assertEqual(after.r3_schema_upgrade, before.r3_schema_upgrade)
        self.assertEqual(after.r3b_schema_upgrade, before.r3b_schema_upgrade)
        self.assertEqual(len(after.agreements), 4)
        self.assertEqual(len(after.changes), 2)
        self.assertEqual(len(after.slice_criteria_revisions), 2)
        active = after.active_context
        assert active is not None, "the accepted Slice must activate its Task"
        self.assertEqual(active.feature_id, before.features[0].id)
        self.assertEqual(active.current_work, TASK)
        self.assertEqual(active.slice_agreement_ref, after.agreements[3].id)
        self.assertEqual(after.agreements[3].statement, self.agreement())
        context = json.loads(execute(self.root, '{"action":"context"}'))["context"]
        inventory = context["slice_acceptance_inventory"]
        self.assertEqual(inventory["total"], 2)
        self.assertTrue(inventory["complete"])
        self.assertEqual({item["slice_title"] for item in inventory["items"]}, {FIRST, SECOND})
        self.assertEqual(context["accepted_slice"]["slice_title"], SECOND)

    def test_r5a_02_active_slot_and_noninteractive_terminal_are_fail_closed(self):
        before = self.snapshot()
        with self.assertRaises(WorkflowError):
            self.accept()
        self.assertEqual(self.snapshot(), before)
        active_args = self.cli_args()
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch(
            "builtins.input", side_effect=AssertionError("an ineligible agreement must not prompt")
        ):
            output = _TTY()
            self.assertEqual(cli_main(active_args, output), 2)
            self.assertIn("empty active slot", output.getvalue())
        self.parked()
        args = self.cli_args()
        output = io.StringIO()
        self.assertEqual(cli_main(args, output), 2)
        self.assertIn("interactive", output.getvalue())
        self.assertEqual(self.state().active_context, None)

    def test_r5a_03_cli_accepts_exact_token_but_rejects_wrong_token(self):
        self.parked()
        args = self.cli_args()
        before = self.snapshot()
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch("builtins.input", return_value="ACEPTO wrong"):
            self.assertEqual(cli_main(args, _TTY()), 2)
        self.assertEqual(self.snapshot(), before)
        token = sha256(self.agreement().encode("utf-8")).hexdigest()[:12]
        with patch("sys.stdin", _TTY()), patch("sys.stdout", _TTY()), patch("builtins.input", return_value=f"ACEPTO {token}"):
            self.assertEqual(cli_main(args, _TTY()), 0)
        self.assertEqual(self.state().agreements[-1].source, "INTERACTIVE_TERMINAL")

    def cli_args(self) -> list[str]:
        path = self.root / "next-criteria.json"
        path.write_text(json.dumps({
            "outcome": self.next_spec.outcome,
            "example": self.next_spec.example,
            "inclusions": list(self.next_spec.inclusions),
            "exclusions": list(self.next_spec.exclusions),
            "decisions": list(self.next_spec.decisions),
            "criteria": [
                {
                    "id": item.id, "expected_result": item.expected_result,
                    "expected_evidence": item.expected_evidence,
                } for item in self.next_spec.criteria
            ],
        }), encoding="utf-8")
        return [
            "--project", str(self.root), "accept-next-slice",
            "--feature-id", self.feature_id, "--slice", SECOND,
            "--task", TASK, "--checkpoint", CHECKPOINT,
            "--criteria-file", str(path), "--expected-revision", str(self.state().revision),
        ]

    def test_r5a_04_retry_same_input_is_noop_and_different_identity_fails(self):
        self.parked()
        self.accept()
        committed = self.snapshot()
        revision = self.state().revision
        again = self.accept()
        self.assertEqual(again.revision, revision)
        self.assertEqual(self.snapshot(), committed)
        with self.assertRaises(WorkflowError):
            self.workflow.accept_next_slice(
                "feature-missing", SECOND, TASK, CHECKPOINT, consent(self.agreement()),
                expected_revision=ExpectedRevision(revision), criteria=self.next_spec,
            )
        self.assertEqual(self.snapshot(), committed)

    def test_r5a_05_stale_revision_and_forged_acceptance_do_not_write(self):
        parked = self.parked()
        baseline = self.snapshot()
        with self.assertRaises(WorkflowError):
            self.accept(acceptance=UserAcceptance("agent says yes", "tty:fake"))
        with self.assertRaises(StaleRevisionError):
            self.accept(expected_revision=parked.revision - 1)
        self.assertEqual(self.snapshot(), baseline)

    def test_r5a_06_failed_state_commit_never_exposes_half_accepted_slice(self):
        parked = self.parked()
        core = CoreService.open(self.root, FailNextStateReplace())
        with self.assertRaises(ServiceError):
            core.accept_next_slice(
                parked, self.feature_id, SECOND, TASK, CHECKPOINT, consent(self.agreement()), self.next_spec,
            )
        self.assertEqual(self.state().revision, parked.revision)
        self.assertEqual(len(self.state().agreements), 2)
        # The same immutable bytes are recoverable and safely published on retry.
        self.accept()
        self.assertEqual(len(self.state().agreements), 4)

    def test_r5a_07_uncertain_postcommit_requires_recovery_and_no_duplicate(self):
        parked = self.parked()
        core = CoreService.open(self.root, FailNextStateDirectory())
        with self.assertRaises(DurabilityUncertainError):
            core.accept_next_slice(
                parked, self.feature_id, SECOND, TASK, CHECKPOINT, consent(self.agreement()), self.next_spec,
            )
        self.assertEqual(self.state().revision, parked.revision + 1)
        self.assertEqual(len(self.state().agreements), 4)
        self.assertEqual(self.accept().revision, parked.revision + 1)

    def test_r5a_08_current_task_progress_works_with_previous_parked_slice(self):
        self.parked()
        self.accept()
        context = json.loads(execute(self.root, '{"action":"context"}'))["context"]
        accepted = context["accepted_slice"]
        request = {
            "action": "accepted_task_progress", "expected_revision": context["revision"],
            "repository_id": context["repository_id"], "task_id": accepted["task_id"],
            "feature_agreement_ref": accepted["feature_agreement_ref"],
            "slice_agreement_ref": accepted["slice_agreement_ref"],
            "checkpoint": CHECKPOINT, "statement": "Se implementó el flujo de creación local",
            "evidence_refs": ["tests/test_r5a_fixture.py"],
        }
        result = json.loads(execute(self.root, json.dumps(request)))
        self.assertNotIn("error", result)
        self.assertEqual(result["result"]["code"], "TASK_PROGRESS_RECORDED")
        self.assertEqual(self.state().agreements[0:2], self.first_authority.agreements)
        original_context = self.state().work_items[0].context
        assert original_context is not None, "the parked Task retains its original Slice context"
        self.assertEqual(original_context.slice_agreement_ref, self.first_authority.agreements[1].id)
        self.assertEqual(result["context"]["task_progress"]["report_kind"], "REPORTED_UNVERIFIED")

    def test_r5a_09_second_title_collision_is_not_a_new_slice(self):
        self.parked()
        self.accept()
        self.workflow.park("pausa explícita de segundo Slice")
        before = self.snapshot()
        changed = next_slice_acceptance_statement(
            self.feature_id, FEATURE, SECOND, "Otra Task distinta", CHECKPOINT, self.next_spec,
        )
        with self.assertRaises(WorkflowError):
            self.workflow.accept_next_slice(
                self.feature_id, SECOND, "Otra Task distinta", CHECKPOINT, consent(changed),
                expected_revision=ExpectedRevision(self.state().revision), criteria=self.next_spec,
            )
        self.assertEqual(self.snapshot(), before)

    def test_r5a_10_historical_e1_receipt_binds_original_pair_after_second_slice(self):
        self.parked()
        self.accept()
        current = self.state()
        first_feature, first_slice = current.agreements[:2]
        source_digest = "a" * 64
        statement = (
            f"Autorizo migrar E1 a schema 14; repository: {first_feature.repository_id}; "
            f"revision: 2; Feature: {first_feature.id}; Slice: {first_slice.id}; "
            f"Task: {first_slice.task_id}; source-sha256: {source_digest}"
        )
        receipt = E1SchemaUpgradeReceipt(
            2, 3, source_digest, statement, "INTERACTIVE_TERMINAL",
            f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}",
            first_feature.repository_id, first_feature.id, first_slice.id,
        )
        # A historical receipt can coexist with later Slices. Tampering with
        # its original refs must still invalidate the whole authority.
        validate_state(replace(current, e1_schema_upgrade=receipt))
        with self.assertRaises(StateValidationError):
            validate_state(replace(current, e1_schema_upgrade=replace(
                receipt, slice_agreement_ref=current.agreements[-1].id,
            )))


if __name__ == "__main__":
    unittest.main()
