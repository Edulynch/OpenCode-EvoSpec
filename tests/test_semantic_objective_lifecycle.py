"""Core lifecycle wiring for the canonical semantic personal-tasks objective."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest
from unittest.mock import call, patch

from evospec.domain import (
    ExpectedRevision,
    OperationalState,
    request_objective_completion_condition,
    validate_state,
)
from evospec.persistence import (
    SCHEMA,
    SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
)
from evospec.request_scoped_objective import (
    REQUEST_SCOPED_OBJECTIVE,
    derive_personal_tasks,
)
from evospec.request_scoped_objective_browser import BrowserObjectiveError
from evospec.services import CoreService, SessionRequestEvidence
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory


REQUEST = "Build a web app to manage my personal tasks"
EXPLICIT_COMPLETE_REQUEST = (
    "Build a web app to manage my personal tasks, let me add new tasks "
    "and mark them as completed."
)
APP = b"""<!doctype html><html><head><meta charset="utf-8"><title>Tasks</title></head>
<body>
  <form id="task-form">
    <input data-evo-action="task-input">
    <button type="submit" data-evo-action="add-task">Add</button>
  </form>
  <ul id="tasks"></ul>
  <script>
    document.querySelector("#task-form").addEventListener("submit", event => {
      event.preventDefault();
      const input = document.querySelector('[data-evo-action="task-input"]');
      const row = document.createElement("li");
      row.dataset.evoTask = "";
      const text = document.createElement("span");
      text.textContent = input.value;
      row.append(text);
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.evoAction = "complete-task";
      row.append(checkbox);
      document.querySelector("#tasks").append(row);
      input.value = "";
    });
  </script>
</body></html>"""


class _SemanticObjectiveFixture:
    root: Path = Path()
    workflow: WorkflowService = WorkflowService.open(Path.cwd())
    evidence: SessionRequestEvidence = SessionRequestEvidence("", "", "", "")
    request_statement = REQUEST

    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="evospec-semantic-objective-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workflow = WorkflowService.open(self.root)
        self.workflow.init()
        self._set_request(self.request_statement)

    def _set_request(self, statement: str) -> None:
        repository_id = sha256(str(self.root).encode("utf-8")).hexdigest()[:16]
        self.evidence = SessionRequestEvidence(
            "ses_semantic1234", "msg_semantic1234", statement, repository_id,
        )
        self.workflow.accept_first_slice_session(
            self.evidence,
            expected_revision=ExpectedRevision(
                CoreService.open(self.root).recover().revision,
            ),
        )

    def _snapshot(self):
        core = CoreService.open(self.root)
        return core.input_snapshot(core.recover().revision)

    def _write_app(self, content: bytes = APP) -> None:
        (self.root / "index.html").write_bytes(content)

    def _schema(self) -> int:
        return json.loads(
            (self.root / ".evospec" / "state.json").read_text()
        )["schema"]

    def _request(self, evidence: SessionRequestEvidence | None = None):
        workflow = WorkflowService.open(self.root)
        state, snapshot = self._snapshot()
        return workflow.request_scoped_objective(
            self.evidence if evidence is None else evidence,
            snapshot.fingerprint,
            expected_revision=ExpectedRevision(state.revision),
        )


class SemanticObjectiveLifecycleTests(_SemanticObjectiveFixture, unittest.TestCase):
    def test_english_spanish_and_explicit_operation_derivation(self) -> None:
        english = derive_personal_tasks(REQUEST)
        self.assertIsNotNone(english)
        assert english is not None
        self.assertEqual(english.operations, ("create", "consult"))

        spanish = derive_personal_tasks(
            "Quiero una aplicación web para gestionar mis tareas personales"
        )
        self.assertIsNotNone(spanish)
        assert spanish is not None
        self.assertEqual(spanish.operations, ("create", "consult"))

        explicit_spanish = derive_personal_tasks(
            "Quiero una app web para gestionar mis tareas y poder agregar "
            "tareas y marcarlas como completadas."
        )
        self.assertIsNotNone(explicit_spanish)
        assert explicit_spanish is not None
        self.assertEqual(
            explicit_spanish.explicit_operations, ("create", "complete"),
        )
        self.assertEqual(
            explicit_spanish.operations, ("create", "consult", "complete"),
        )

    def test_staged_proof_closure_recovery_and_replay(self) -> None:
        self._write_app()
        workflow = WorkflowService.open(self.root)
        before, snapshot = self._snapshot()
        # This stub checks Core receipt wiring, not browser behavior; the gated
        # test below exercises the real Playwright verifier.
        with patch(
            "evospec.services.verify_personal_tasks",
            side_effect=lambda content, _operations: sha256(content).hexdigest(),
        ) as browser:
            projected = workflow.request_scoped_objective_status(before, snapshot)
            self.assertIsNotNone(projected)
            assert projected is not None
            self.assertEqual(projected.status, "PENDING")
            self.assertIsNone(projected.heading)
            self.assertEqual(projected.kind, "PERSONAL_TASKS_WEB")
            self.assertEqual(
                projected.criteria,
                (
                    ("create", "DERIVED_OBJECTIVE"),
                    ("consult", "DERIVED_OBJECTIVE"),
                ),
            )
            browser.assert_not_called()
            self.assertEqual(self._schema(), SCHEMA)

            verified_result = workflow.request_scoped_objective(
                self.evidence,
                snapshot.fingerprint,
                expected_revision=ExpectedRevision(before.revision),
            )
            self.assertEqual(verified_result.code, "REQUEST_OBJECTIVE_VERIFIED")
            browser.assert_called_once_with(APP, ("create", "consult"))

            verified = CoreService.open(self.root).recover()
            validate_state(verified)
            self.assertEqual(self._schema(), SEMANTIC_REQUEST_OBJECTIVE_SCHEMA)
            criteria = verified.slice_criteria_revisions[0]
            self.assertEqual(criteria.source, REQUEST_SCOPED_OBJECTIVE)
            self.assertEqual(
                tuple((item.id, item.provenance) for item in criteria.criteria),
                (
                    ("request-objective-create", "DERIVED_OBJECTIVE"),
                    ("request-objective-consult", "DERIVED_OBJECTIVE"),
                ),
            )
            self.assertEqual(len(verified.criterion_test_receipts), 2)
            proofs = verified.criterion_test_receipts
            self.assertEqual(
                tuple(item.criterion_id for item in proofs),
                tuple(item.id for item in criteria.criteria),
            )
            self.assertEqual({item.output_sha256 for item in proofs}, {sha256(APP).hexdigest()})
            self.assertEqual({item.source_revision for item in proofs}, {before.revision})
            self.assertEqual({item.recorded_revision for item in proofs}, {verified.revision})
            self.assertEqual(
                {item.input_fingerprint_before for item in proofs},
                {snapshot.fingerprint},
            )
            self.assertEqual(
                {item.input_fingerprint_after for item in proofs},
                {snapshot.fingerprint},
            )
            self.assertEqual(
                {item.admission_policy_id for item in proofs},
                {"core-personal-tasks-browser-v1"},
            )
            self.assertTrue(all(
                item.runtime_executable is None
                and item.runtime_executable_sha256 is None
                and item.runtime_version is None
                and not item.runtime_argv
                and item.environment_profile_id is None
                and item.coverage_approval_id is None
                for item in proofs
            ))
            self.assertEqual(len(verified.slice_outcome_receipts), 1)
            outcome = verified.slice_outcome_receipts[0]
            self.assertEqual(outcome.evidence_receipt_ids, tuple(item.id for item in proofs))
            self.assertEqual(outcome.basis, "REQUEST_SCOPED_PERSONAL_TASKS")
            self.assertFalse(verified.feature_completion_receipts)

            current, current_snapshot = self._snapshot()
            current_status = workflow.request_scoped_objective_status(
                current, current_snapshot,
            )
            self.assertIsNotNone(current_status)
            assert current_status is not None
            self.assertEqual(current_status.status, "VERIFIED")
            browser.assert_called_once()

            completed_result = WorkflowService.open(self.root).request_scoped_objective(
                self.evidence,
                current_snapshot.fingerprint,
                expected_revision=ExpectedRevision(current.revision),
            )
            self.assertEqual(completed_result.code, "REQUEST_OBJECTIVE_COMPLETE")
            browser.assert_called_once()

        closed = CoreService.open(self.root).recover()
        validate_state(closed)
        self.assertEqual(len(closed.feature_completion_receipts), 1)
        closure = closed.feature_completion_receipts[0]
        objective = derive_personal_tasks(REQUEST)
        assert objective is not None
        self.assertEqual(
            closure.condition, request_objective_completion_condition(objective),
        )
        self.assertEqual(closure.source, REQUEST_SCOPED_OBJECTIVE)
        self.assertEqual(closure.source_reference, self.evidence.source_reference)
        self.assertEqual(closure.slice_outcome_receipt_ids, (outcome.id,))
        self.assertEqual(len(closed.terminal_archives), 1)
        archived = closed.terminal_archives[0].context
        self.assertEqual(archived.phase.value, "DONE")
        self.assertEqual(archived.status.value, "ACCEPTED")
        self.assertEqual(len(archived.review_evidence), len(proofs))
        for proof, review_evidence in zip(
            proofs, archived.review_evidence, strict=True,
        ):
            self.assertIn(proof.criterion_id, review_evidence.value)
            self.assertIn(proof.id, review_evidence.reference)
            self.assertIn(proof.output_sha256, review_evidence.reference)
        self.assertEqual(len(archived.verification.evidence), len(proofs))
        self.assertTrue(all(
            proof.id in item and proof.output_sha256 in item
            for proof, item in zip(
                proofs, archived.verification.evidence, strict=True,
            )
        ))
        self.assertEqual(len(closed.work_items), 1)
        self.assertIs(closed.work_items[0].operational_state, OperationalState.DONE)
        self.assertFalse(closed.publications)
        self.assertFalse(closed.publication_history)

        replay_state, replay_snapshot = self._snapshot()
        replay_status = WorkflowService.open(self.root).request_scoped_objective_status(
            replay_state, replay_snapshot,
        )
        self.assertIsNotNone(replay_status)
        assert replay_status is not None
        self.assertEqual(replay_status.status, "COMPLETE")
        replay = WorkflowService.open(self.root).request_scoped_objective(
            self.evidence,
            replay_snapshot.fingerprint,
            expected_revision=ExpectedRevision(replay_state.revision),
        )
        self.assertEqual(replay.code, "REQUEST_OBJECTIVE_ALREADY_COMPLETE")
        self.assertEqual(CoreService.open(self.root).recover(), closed)

        self._write_app(APP + b"\n<!-- changed after closure -->")
        stale_state, stale_snapshot = self._snapshot()
        stale_status = WorkflowService.open(self.root).request_scoped_objective_status(
            stale_state, stale_snapshot,
        )
        self.assertIsNotNone(stale_status)
        assert stale_status is not None
        self.assertEqual(stale_status.status, "BLOCKED")
        self.assertIn("REQUEST_OBJECTIVE_PROOF_STALE", stale_status.blockers)
        with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_PROOF_STALE"):
            WorkflowService.open(self.root).request_scoped_objective(
                self.evidence,
                stale_snapshot.fingerprint,
                expected_revision=ExpectedRevision(stale_state.revision),
            )
        self.assertEqual(CoreService.open(self.root).recover(), closed)


    def test_changed_artifact_refreshes_one_complete_fresh_proof_bundle(self) -> None:
        self._write_app()
        changed_app = APP + b"\n<!-- changed exact artifact -->"
        with patch(
            "evospec.services.verify_personal_tasks",
            side_effect=lambda content, _operations: sha256(content).hexdigest(),
        ) as browser:
            self._request()
            first_state = CoreService.open(self.root).recover()
            self.assertEqual(len(first_state.criterion_test_receipts), 2)

            self._write_app(changed_app)
            state, snapshot = self._snapshot()
            stale = WorkflowService.open(self.root).request_scoped_objective_status(
                state, snapshot,
            )
            self.assertIsNotNone(stale)
            assert stale is not None
            self.assertEqual(stale.status, "PENDING")
            self.assertIn("REQUEST_OBJECTIVE_PROOF_STALE", stale.blockers)

            self._request()
            refreshed = CoreService.open(self.root).recover()
            browser.assert_has_calls([
                call(APP, ("create", "consult")),
                call(changed_app, ("create", "consult")),
            ])
            self.assertEqual(len(refreshed.criterion_test_receipts), 4)
            latest_proofs = refreshed.criterion_test_receipts[-2:]
            self.assertEqual(
                tuple(item.criterion_id for item in latest_proofs),
                tuple(item.id for item in refreshed.slice_criteria_revisions[0].criteria),
            )
            self.assertEqual(
                {item.output_sha256 for item in latest_proofs},
                {sha256(changed_app).hexdigest()},
            )
            self.assertEqual(
                {item.source_revision for item in latest_proofs},
                {first_state.revision},
            )
            self.assertEqual(
                {item.recorded_revision for item in latest_proofs},
                {refreshed.revision},
            )
            latest_outcome = refreshed.slice_outcome_receipts[-1]
            self.assertEqual(
                latest_outcome.evidence_receipt_ids,
                tuple(item.id for item in latest_proofs),
            )
            self.assertEqual(
                latest_outcome.input_fingerprint,
                latest_proofs[0].input_fingerprint_after,
            )

            before_close, current_snapshot = self._snapshot()
            closed_result = WorkflowService.open(self.root).request_scoped_objective(
                self.evidence,
                current_snapshot.fingerprint,
                expected_revision=ExpectedRevision(before_close.revision),
            )
            self.assertEqual(closed_result.code, "REQUEST_OBJECTIVE_COMPLETE")
            self.assertEqual(browser.call_count, 2)


class ExplicitCompletionObjectiveTests(_SemanticObjectiveFixture, unittest.TestCase):
    request_statement = EXPLICIT_COMPLETE_REQUEST

    def test_only_explicit_completion_adds_the_completion_criterion(self) -> None:
        objective = derive_personal_tasks(EXPLICIT_COMPLETE_REQUEST)
        self.assertIsNotNone(objective)
        assert objective is not None
        self.assertEqual(objective.operations, ("create", "consult", "complete"))
        self._write_app()
        state, snapshot = self._snapshot()
        with patch(
            "evospec.services.verify_personal_tasks",
            side_effect=lambda content, _operations: sha256(content).hexdigest(),
        ) as browser:
            status = WorkflowService.open(self.root).request_scoped_objective_status(
                state, snapshot,
            )
            self.assertIsNotNone(status)
            assert status is not None
            self.assertEqual(status.criteria, (
                ("create", "EXPLICIT_REQUEST"),
                ("consult", "DERIVED_OBJECTIVE"),
                ("complete", "EXPLICIT_REQUEST"),
            ))
            browser.assert_not_called()
            self._request()
            browser.assert_called_once_with(
                APP, ("create", "consult", "complete"),
            )
        verified = CoreService.open(self.root).recover()
        self.assertEqual(len(verified.criterion_test_receipts), 3)
        self.assertEqual(
            tuple(item.criterion_id for item in verified.criterion_test_receipts),
            tuple(item.id for item in verified.slice_criteria_revisions[0].criteria),
        )


class SemanticObjectiveFailureTests(_SemanticObjectiveFixture, unittest.TestCase):
    def test_browser_failures_changed_inputs_and_wrong_session_never_record_proof(self) -> None:
        self._write_app()
        state, snapshot = self._snapshot()
        with patch(
            "evospec.services.verify_personal_tasks",
            side_effect=BrowserObjectiveError(
                "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE", "test runtime unavailable",
            ),
        ):
            with self.assertRaisesRegex(
                WorkflowError, "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE",
            ):
                self._request()
        self.assertEqual(CoreService.open(self.root).recover(), state)
        self.assertEqual(self._schema(), SCHEMA)

        wrong_session = SessionRequestEvidence(
            "ses_other1234", self.evidence.message_id, self.evidence.statement,
            self.evidence.repository_id,
        )
        with patch("evospec.services.verify_personal_tasks") as browser:
            with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_SESSION_MISMATCH"):
                self.workflow.request_scoped_objective(
                    wrong_session,
                    snapshot.fingerprint,
                    expected_revision=ExpectedRevision(state.revision),
                )
            browser.assert_not_called()
            with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_SESSION_MISMATCH"):
                self.workflow.request_scoped_objective(
                    replace(self.evidence, repository_id="wrong-repository"),
                    snapshot.fingerprint,
                    expected_revision=ExpectedRevision(state.revision),
                )
            browser.assert_not_called()
            with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_FINGERPRINT_STALE"):
                self.workflow.request_scoped_objective(
                    self.evidence,
                    "0" * 64,
                    expected_revision=ExpectedRevision(state.revision),
                )
            browser.assert_not_called()
        self.assertEqual(CoreService.open(self.root).recover(), state)

        state, snapshot = self._snapshot()
        changed_page = APP + b"\n<!-- concurrent edit -->"

        def edit_during_browser(content: bytes, _operations: tuple[str, ...]) -> str:
            (self.root / "index.html").write_bytes(changed_page)
            return sha256(content).hexdigest()

        with patch(
            "evospec.services.verify_personal_tasks",
            side_effect=edit_during_browser,
        ):
            with self.assertRaisesRegex(
                WorkflowError, "REQUEST_OBJECTIVE_FINGERPRINT_STALE",
            ):
                self.workflow.request_scoped_objective(
                    self.evidence,
                    snapshot.fingerprint,
                    expected_revision=ExpectedRevision(state.revision),
                )
        self.assertEqual(CoreService.open(self.root).recover(), state)
        self.assertEqual(self._schema(), SCHEMA)

    def test_missing_or_false_artifact_and_false_browser_digest_fail_closed(self) -> None:
        state, snapshot = self._snapshot()
        with self.assertRaisesRegex(WorkflowError, "REQUEST_OBJECTIVE_ARTIFACT_MISSING"):
            self.workflow.request_scoped_objective(
                self.evidence,
                snapshot.fingerprint,
                expected_revision=ExpectedRevision(state.revision),
            )
        self.assertEqual(CoreService.open(self.root).recover(), state)

        self._write_app()
        state, snapshot = self._snapshot()
        with patch(
            "evospec.services.verify_personal_tasks",
            return_value="0" * 64,
        ):
            with self.assertRaisesRegex(
                WorkflowError, "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
            ):
                self.workflow.request_scoped_objective(
                    self.evidence,
                    snapshot.fingerprint,
                    expected_revision=ExpectedRevision(state.revision),
                )
        self.assertEqual(CoreService.open(self.root).recover(), state)
        self.assertEqual(self._schema(), SCHEMA)


class StaticObjectiveProjectionCompatibilityTests(
    _SemanticObjectiveFixture, unittest.TestCase,
):
    request_statement = 'Create a static HTML page with the heading "Welcome".'

    def test_legacy_status_fields_remain_unchanged(self) -> None:
        self._write_app(
            b"<!doctype html><html><head></head><body>"
            b"<h1>Welcome</h1></body></html>\n",
        )
        state, snapshot = self._snapshot()
        status = self.workflow.request_scoped_objective_status(state, snapshot)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.status, "PENDING")
        self.assertEqual(status.heading, "Welcome")
        self.assertIsNone(status.kind)
        self.assertEqual(status.criteria, ())


_BROWSER_TESTS_ENABLED = os.environ.get("EVOSPEC_RUN_BROWSER_TESTS") == "1"


@unittest.skipUnless(
    _BROWSER_TESTS_ENABLED,
    "set EVOSPEC_RUN_BROWSER_TESTS=1 with the semantic-browser extra and Chromium",
)
class RealBrowserCoreLifecycleTests(_SemanticObjectiveFixture, unittest.TestCase):
    def test_real_browser_verifies_and_closes_the_core_objective(self) -> None:
        self._write_app()
        state, snapshot = self._snapshot()
        status = self.workflow.request_scoped_objective_status(state, snapshot)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.status, "PENDING")

        try:
            verified = self.workflow.request_scoped_objective(
                self.evidence,
                snapshot.fingerprint,
                expected_revision=ExpectedRevision(state.revision),
            )
        except WorkflowError as error:
            if "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE" in str(error):
                self.skipTest("the opt-in Playwright/Chromium runtime is unavailable")
            raise
        self.assertEqual(verified.code, "REQUEST_OBJECTIVE_VERIFIED")
        verified_state, verified_snapshot = self._snapshot()
        self.assertEqual(len(verified_state.criterion_test_receipts), 2)
        completed = WorkflowService.open(self.root).request_scoped_objective(
            self.evidence,
            verified_snapshot.fingerprint,
            expected_revision=ExpectedRevision(verified_state.revision),
        )
        self.assertEqual(completed.code, "REQUEST_OBJECTIVE_COMPLETE")
        closed = CoreService.open(self.root).recover()
        validate_state(closed)
        self.assertEqual(len(closed.feature_completion_receipts), 1)
        self.assertEqual(self._schema(), SEMANTIC_REQUEST_OBJECTIVE_SCHEMA)


if __name__ == "__main__":
    unittest.main()
