"""R2-C2A: the first safe session request is not a fabricated TTY approval."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import unittest

from evospec.agent import _accepted_slice_for_context, _slice_acceptance_inventory
from evospec.domain import ExpectedRevision, StateValidationError, validate_state
from evospec.services import CoreService, SessionRequestEvidence, StaleRevisionError
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory


REQUEST = (
    "Quiero una aplicación sencilla para llevar mis tareas, donde pueda agregar "
    "tareas nuevas y marcar con un check las que ya terminé. "
    "Las que no tengan check están pendientes."
)


class SessionRequestFirstSliceTests(unittest.TestCase):
    def evidence(self, root: Path, text: str = REQUEST) -> SessionRequestEvidence:
        # The real adapter reads these fields from OpenCode; these fixtures
        # deliberately do not pretend that a Python test authenticates a human.
        repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
        return SessionRequestEvidence(
            session_id="ses_test1234", message_id="msg_test1234",
            statement=text, repository_id=repository_id,
        )

    def test_first_session_slice_persists_exact_intent_without_tty_or_other_edits(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            evidence = self.evidence(root)
            start = CoreService.open(root).recover().revision
            result = workflow.accept_first_slice_session(
                evidence, expected_revision=ExpectedRevision(start),
            )
            self.assertEqual(result.code, "SESSION_SLICE_RECORDED")
            state = CoreService.open(root).recover()
            validate_state(state)
            self.assertEqual(state.revision, start + 1)
            self.assertEqual([entry.kind for entry in state.agreements], ["FEATURE", "SLICE"])
            self.assertTrue(all(entry.source == "SESSION_REQUEST" for entry in state.agreements))
            self.assertTrue(all(entry.statement == REQUEST for entry in state.agreements))
            self.assertEqual(state.agreements[0].title, REQUEST)
            self.assertEqual(state.agreements[1].title, REQUEST)
            self.assertEqual(state.agreements[0].source_reference, evidence.source_reference)
            self.assertTrue(evidence.source_reference.startswith("session:ses_test1234:msg_test1234:"))
            # The active context is guaranteed by the accepted first Slice,
            # but the persisted State type also supports terminal/no-work states.
            active = state.active_context
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.current_work,
                             "Implementar únicamente el resultado expresado en la petición")
            scope = active.autonomy_envelope
            self.assertEqual(tuple(map(str, scope.allowed_paths)), ("index.html",))
            self.assertEqual(scope.operation_budget, 8)
            self.assertEqual(scope.irreversible_permissions, ())
            self.assertEqual(scope.retry_budget, 0)
            self.assertIn("edit", scope.reversible_actions)
            self.assertFalse(state.slice_criteria_heads, "criteria cannot be invented from a prompt")
            # Retries against the already accepted exact request are no-ops.
            retry = workflow.accept_first_slice_session(
                evidence, expected_revision=ExpectedRevision(state.revision),
            )
            self.assertEqual(retry.revision, state.revision)
            self.assertEqual(CoreService.open(root).recover().revision, state.revision)

    def test_multiline_request_keeps_exact_message_and_trimmed_display_titles(self) -> None:
        # User instructions commonly contain paragraphs, Markdown lists, and
        # trailing newlines from the host. They are not a new approval request.
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            _ = workflow.init()
            text = (
                "  Quiero una aplicación para mis tareas.\r\n"
                "\r\n"
                "- Agregar tareas nuevas.\r\n"
                "- Marcar o desmarcar las completadas.\r\n"
                "\tNo publicar ni hacer commits.\r\n"
            )
            evidence = self.evidence(root, text)
            initial = CoreService.open(root).recover().revision
            result = workflow.accept_first_slice_session(
                evidence, expected_revision=ExpectedRevision(initial),
            )
            self.assertEqual(result.code, "SESSION_SLICE_RECORDED")
            state = CoreService.open(root).recover()
            validate_state(state)
            self.assertEqual(state.agreements[0].statement, text)
            self.assertEqual(state.agreements[1].statement, text)
            self.assertEqual(state.agreements[0].title, text.strip())
            self.assertEqual(state.agreements[1].title, text.strip())
            self.assertEqual(state.agreements[0].source_reference, evidence.source_reference)
            self.assertEqual(
                evidence.source_reference.rsplit(":", 1)[-1],
                sha256(text.encode("utf-8")).hexdigest()[:16],
            )
            active = state.active_context
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.focus, text.strip())
            self.assertFalse(state.slice_criteria_heads)
            unchanged = workflow.accept_first_slice_session(
                evidence, expected_revision=ExpectedRevision(state.revision),
            )
            self.assertEqual(unchanged.revision, state.revision)
            # Whitespace remains part of the exact source evidence: changing
            # even one line break without updating its digest is tampering.
            forged = replace(state.agreements[0], statement=text.replace("\r\n", "\n"))
            with self.assertRaises(StateValidationError):
                validate_state(replace(state, agreements=(forged, state.agreements[1])))

    def test_unsupported_control_characters_do_not_become_session_scope(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            _ = workflow.init()
            revision = CoreService.open(root).recover().revision
            for value in ("Tarea\x00invalida", "Tarea\x0binvalida", "  \n  "):
                # The source must be rejected before publishing any agreement.
                with self.subTest(value=repr(value)):
                    with self.assertRaises(WorkflowError):
                        workflow.accept_first_slice_session(
                            self.evidence(root, value),
                            expected_revision=ExpectedRevision(revision),
                        )
                    self.assertEqual(CoreService.open(root).recover().revision, revision)

    def test_rejects_existing_file_other_root_wrong_message_and_stale_state(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            rev = CoreService.open(root).recover().revision
            legitimate = self.evidence(root)
            with self.assertRaises(WorkflowError):
                workflow.accept_first_slice_session(
                    replace(legitimate, repository_id="other-root"),
                    expected_revision=ExpectedRevision(rev),
                )
            with self.assertRaises(WorkflowError):
                workflow.accept_first_slice_session(
                    replace(legitimate, message_id="../unsafe"),
                    expected_revision=ExpectedRevision(rev),
                )
            with self.assertRaises(WorkflowError):
                workflow.accept_first_slice_session(
                    replace(legitimate, statement="X" * 2049),
                    expected_revision=ExpectedRevision(rev),
                )
            (root / "index.html").write_text("existing user data", encoding="utf-8")
            with self.assertRaises(WorkflowError):
                workflow.accept_first_slice_session(
                    legitimate, expected_revision=ExpectedRevision(rev),
                )
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "existing user data")
            (root / "index.html").unlink()
            result = workflow.accept_first_slice_session(
                legitimate, expected_revision=ExpectedRevision(rev),
            )
            self.assertEqual(result.code, "SESSION_SLICE_RECORDED")
            # The revision guard intentionally raises its typed stale error
            # before any ordinary workflow rejection is attempted.
            with self.assertRaises(StaleRevisionError):
                workflow.accept_first_slice_session(
                    replace(legitimate, message_id="msg_different"),
                    expected_revision=ExpectedRevision(rev),
                )

    def test_tampered_session_source_fails_canonical_state_validation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            evidence = self.evidence(root)
            workflow.accept_first_slice_session(
                evidence, expected_revision=ExpectedRevision(CoreService.open(root).recover().revision),
            )
            state = CoreService.open(root).recover()
            forged = replace(state.agreements[0], statement="Otra solicitud")
            with self.assertRaises(StateValidationError):
                validate_state(replace(state, agreements=(forged, state.agreements[1])))
            other_source = replace(state.agreements[1], source="INTERACTIVE_TERMINAL")
            with self.assertRaises(StateValidationError):
                validate_state(replace(state, agreements=(state.agreements[0], other_source)))

    def test_mismatched_session_pair_is_not_projected_as_canonical(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            evidence = self.evidence(root)
            workflow.accept_first_slice_session(
                evidence, expected_revision=ExpectedRevision(CoreService.open(root).recover().revision),
            )
            state = CoreService.open(root).recover()
            feature, accepted_slice = state.agreements
            mismatches = (
                replace(feature, source_reference=feature.source_reference + ":other"),
                replace(feature, statement="a different message"),
                replace(feature, source="INTERACTIVE_TERMINAL"),
            )

            for mismatched_feature in mismatches:
                with self.subTest(feature=mismatched_feature):
                    malformed = replace(state, agreements=(mismatched_feature, accepted_slice))
                    with self.assertRaises(StateValidationError):
                        validate_state(malformed)
                    inventory = _slice_acceptance_inventory(malformed)
                    self.assertFalse(inventory.complete)
                    self.assertEqual(inventory.items[0].authority_status, "UNAVAILABLE")
                    self.assertEqual(inventory.items[0].scope_status, "UNAVAILABLE")
                    self.assertIsNone(inventory.items[0].feature_id)
                    self.assertIsNone(
                        _accepted_slice_for_context(malformed, malformed.active_context),
                    )


if __name__ == "__main__":
    unittest.main()
