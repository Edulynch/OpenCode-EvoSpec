from __future__ import annotations

import hashlib
import io
import json
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from evospec.agent import project_context
from evospec.agent_protocol import execute
from evospec.cli import main as cli_main
from evospec.continuation_summary import project_continuation
from evospec.guidance import MaterialDecisionEvidence, Outcome, OutcomeKind
from evospec.domain import (
    CriterionCoverageDeclaration,
    CriterionTestReceipt,
    CoverageInput,
    ExpectedRevision,
    OperationalState,
    SliceCriterion,
)
from evospec.services import (
    CoreService,
    SliceCriteriaSpec,
    UserAcceptance,
    next_slice_acceptance_statement,
    slice_acceptance_statement,
)
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory


FEATURE = "Personal planner"
SLICE_A = "Local task list"
SLICE_B = "Create a task"
TASK_A = "Render saved tasks"
TASK_B = "Implement local task creation"
CHECKPOINT_A = "Task list survives restart"
CHECKPOINT_B = "Created task is visible after restart"


def _spec(criterion_id: str, expected: str) -> SliceCriteriaSpec:
    return SliceCriteriaSpec(
        f"User can {expected}",
        f"A user can {expected} after restart",
        (f"Local {expected}",),
        ("No account or sync",),
        ("Local persistence",),
        (SliceCriterion(criterion_id, f"{expected} works", f"python-unittest:tests.test_r5b_fixture"),),
    )


def _acceptance(statement: str) -> UserAcceptance:
    token = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]
    return UserAcceptance(statement, f"tty:{token}")


def _context(root: Path) -> dict[str, Any]:
    response = json.loads(execute(root, '{"action":"context"}'))
    if "error" in response:
        raise AssertionError(response["error"])
    return response["context"]


def _snapshot(root: Path) -> dict[str, bytes]:
    managed = root / ".evospec"
    return {
        str(path.relative_to(managed)): path.read_bytes()
        for path in managed.rglob("*") if path.is_file()
    }


def _temporary_directory() -> TemporaryDirectory[str]:
    return TemporaryDirectory(prefix="evospec-r5b-")


def _create(root: Path, *, two_slices: bool = True) -> WorkflowService:
    test_package = root / "tests"
    test_package.mkdir(parents=True)
    workflow = WorkflowService.open(root)
    initialized = workflow.init()
    criteria_a = _spec("AC-A", "render saved tasks")
    statement_a = slice_acceptance_statement(
        FEATURE, SLICE_A, TASK_A, CHECKPOINT_A, criteria_a,
    )
    accepted = workflow.accept_first_slice(
        FEATURE, SLICE_A, TASK_A, CHECKPOINT_A, _acceptance(statement_a),
        expected_revision=ExpectedRevision(initialized.revision),
        criteria=criteria_a,
    )
    r3 = workflow.r3_upgrade_agreement(expected_revision=ExpectedRevision(accepted.revision))
    upgraded = workflow.upgrade_r3_schema(
        _acceptance(r3), expected_revision=ExpectedRevision(accepted.revision),
    )
    declarations = (
        CriterionCoverageDeclaration(
            "AC-A", "python-unittest", "tests.test_r5b_fixture",
            (CoverageInput("root-directory", "tests"),),
        ),
    )
    r3b = workflow.r3b_upgrade_agreement(
        declarations, expected_revision=ExpectedRevision(upgraded.revision),
    )
    schema17 = workflow.upgrade_r3b_schema(
        _acceptance(r3b), declarations, expected_revision=ExpectedRevision(upgraded.revision),
    )
    if two_slices:
        workflow.park("waiting to continue the Feature")
        state = CoreService.open(root).recover()
        feature_id = str(state.features[0].id)
        criteria_b = _spec("AC-B", "create a task")
        statement_b = next_slice_acceptance_statement(
            feature_id, FEATURE, SLICE_B, TASK_B, CHECKPOINT_B, criteria_b,
        )
        workflow.accept_next_slice(
            feature_id, SLICE_B, TASK_B, CHECKPOINT_B, _acceptance(statement_b),
            expected_revision=ExpectedRevision(state.revision), criteria=criteria_b,
        )
    return workflow


def _report_active_task(root: Path) -> dict[str, object]:
    context = _context(root)
    accepted = context["accepted_slice"]
    assert isinstance(accepted, dict)
    result = json.loads(execute(root, json.dumps({
        "action": "accepted_task_progress",
        "expected_revision": context["revision"],
        "repository_id": context["repository_id"],
        "task_id": accepted["task_id"],
        "feature_agreement_ref": accepted["feature_agreement_ref"],
        "slice_agreement_ref": accepted["slice_agreement_ref"],
        "checkpoint": CHECKPOINT_B,
        "statement": "Local task creation was reported.",
        "evidence_refs": ["tests/test_r5b_fixture.py"],
    })))
    if "error" in result:
        raise AssertionError(result["error"])
    return _context(root)


class R5BContinuationTests(unittest.TestCase):
    def test_cli_and_protocol_share_reopened_multislice_projection_read_only(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            _create(root)
            self._assert_task_reported_unverified(_report_active_task(root))
            before = _snapshot(root)
            prior = CoreService.open(root).recover()

            first = json.loads(execute(root, '{"action":"context"}'))["context"]
            reopened = json.loads(execute(root, '{"action":"context"}'))["context"]
            output = io.StringIO()
            self.assertEqual(cli_main(["--project", str(root), "status"], output), 0)

            self.assertEqual(first["revision"], reopened["revision"])
            summary = first["continuation"]
            self.assertEqual(summary["status"], "ACTIVE_SLICE")
            self.assertEqual(summary["feature_title"], FEATURE)
            self.assertEqual(summary["active_slice"]["slice_title"], SLICE_B)
            self.assertEqual(summary["task"]["task_id"], first["accepted_slice"]["task_id"])
            self.assertEqual(summary["task"]["initial_task"], TASK_B)
            self.assertEqual(summary["task"]["initial_checkpoint"], CHECKPOINT_B)
            self.assertEqual(summary["task_report"]["report_kind"], "REPORTED_UNVERIFIED")
            self.assertEqual(summary["criterion_verification"][0]["status"], "UNVERIFIED")
            parked = next(item for item in summary["other_slices"] if item["slice_title"] == SLICE_A)
            self.assertEqual(parked["scope_status"], "PARKED")
            self.assertEqual(parked["provenance"], "PARKED_WORK_ITEM")
            self.assertEqual(parked["criteria_status"], "RECORDED")
            self.assertNotIn("task_progress", parked)
            self.assertNotIn("receipt", parked)
            self.assertEqual(summary["inventory"]["history_scope"], "AGREEMENT_RECORDS_ONLY_NONEXHAUSTIVE")
            self.assertEqual(summary["inventory"]["total"], 2)
            self.assertEqual(summary["work_queue"]["total"], 2)
            self.assertIn("Active Slice: " + SLICE_B, output.getvalue())
            self.assertIn("Task report: REPORTED_UNVERIFIED", output.getvalue())
            self.assertIn("Other Slices (not active):", output.getvalue())
            self.assertIn(SLICE_A, output.getvalue())
            after = CoreService.open(root).recover()
            self.assertEqual(after, prior)
            self.assertEqual(_snapshot(root), before)

    def test_empty_slot_and_feature_lifecycle_without_slice_are_not_current(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            workflow = _create(root, two_slices=False)
            workflow.park("explicitly waiting")
            empty = _context(root)["continuation"]
            self.assertEqual(empty["status"], "EMPTY_SLOT")
            self.assertIsNone(empty["active_slice"])
            self.assertEqual(empty["next"]["code"], "SLICE_ACCEPTANCE_REQUIRES_EXPLICIT_INPUT")
            self.assertIsNone(empty["next"]["action"])
            self.assertTrue(any("existing primary Feature ID" in item for item in empty["next"]["required_input"]))
            self.assertEqual(empty["other_slices"][0]["scope_status"], "PARKED")
            output = io.StringIO()
            self.assertEqual(cli_main(["--project", str(root), "status"], output), 0)
            self.assertIn("Active Slice: none (no Slice is CURRENT)", output.getvalue())
            self.assertIn("Explicit interactive human acceptance", output.getvalue())

        with _temporary_directory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(Path(root))
            workflow.init()
            workflow.new("A Feature with lifecycle work but no accepted Slice")
            response = _context(root)
            summary = response["continuation"]
            self.assertEqual(summary["status"], "ACTIVE_WORK_WITHOUT_SLICE")
            self.assertIsNone(summary["active_slice"])
            output = io.StringIO()
            self.assertEqual(cli_main(["--project", str(root), "status"], output), 0)
            self.assertIn("Active lifecycle without accepted Slice", output.getvalue())
            self.assertNotIn("Active Slice:", output.getvalue())

    def test_terminal_ordinary_work_does_not_imply_first_slice_eligibility(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("An ordinary capability without a Slice agreement")
            workflow.reject()
            self.assertIsNone(_context(root)["accepted_slice"])
            summary = _context(root)["continuation"]
            self.assertEqual(summary["next"]["code"], "NO_ACCEPTED_SLICE")
            self.assertIsNone(summary["next"]["action"])
            self.assertEqual(summary["next"]["required_input"], [])
            output = io.StringIO()
            self.assertEqual(cli_main(["--project", str(root), "status"], output), 0)
            self.assertNotIn("Next: new", output.getvalue())
            self.assertIn("does not establish an empty work ledger", output.getvalue())

    def test_material_decision_does_not_invent_a_core_action(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            _create(root)
            context = project_context(WorkflowService.open(root))
            decision = MaterialDecisionEvidence("Resolve the proposal", ("AUTHORIZE", "REJECT", "NARROW"))
            summary = project_continuation(replace(
                context, outcome=Outcome(OutcomeKind.HUMAN_MATERIAL_DECISION, "Decision required", decision),
            ))
            self.assertEqual(summary.status, "DECISION_REQUIRED")
            self.assertIsNone(summary.next_action)
            self.assertEqual(summary.decision, decision)
            self.assertEqual(summary.next_options, decision.options)

    def test_ambiguous_queue_exposes_canonical_choices_without_selecting(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            workflow = _create(root, two_slices=False)
            workflow.park("first item deferred")
            workflow.new("A second independent capability")
            workflow.park("second item deferred")
            core = CoreService.open(root)
            state = core.recover()
            for item in state.work_items:
                state = core.set_work_state(state, item.id, OperationalState.READY)

            context = project_context(WorkflowService.open(root))
            summary = project_continuation(context)
            self.assertEqual(summary.status, "QUEUE_AMBIGUOUS")
            self.assertEqual(summary.next_action, "activate_work")
            self.assertEqual(len(summary.next_options), 2)
            self.assertIsNone(context.accepted_slice)
            self.assertIsNone(context.work_queue.next)
            self.assertEqual(context.work_queue.next_candidates, summary.next_options)

    def test_task_criterion_and_outcome_states_remain_distinct(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            _create(root)
            _report_active_task(root)
            context = _context(root)
            accepted = context["accepted_slice"]
            criterion = accepted["criteria_revision"]["criteria"][0]
            result = json.loads(execute(root, json.dumps({
                "action": "criterion_test_result",
                "expected_revision": context["revision"],
                "repository_id": context["repository_id"],
                "slice_agreement_ref": accepted["slice_agreement_ref"],
                "criteria_revision_id": accepted["criteria_revision"]["id"],
                "criterion_id": criterion["id"],
                "test_kind": "python-unittest",
                "test_target": "tests.test_r5b_fixture",
                "exit_code": 0,
                "output_sha256": hashlib.sha256(b"host reported pass").hexdigest(),
            })))
            self.assertNotIn("error", result)
            self.assertNotIn("continuation", result["context"])
            summary = _context(root)["continuation"]
            self.assertEqual(summary["task_report"]["report_kind"], "REPORTED_UNVERIFIED")
            self.assertEqual(summary["criterion_verification"][0]["status"], "VERIFIED")
            self.assertFalse(summary["criterion_verification"][0]["fresh_for_current_workspace"])
            self.assertNotEqual(summary["slice_outcome_status"], "CURRENT")
            self.assertFalse(summary["terminal_history"])

            agent_context = project_context(WorkflowService.open(root))
            accepted_context = agent_context.accepted_slice
            assert accepted_context is not None and accepted_context.criteria_revision is not None
            receipt = CriterionTestReceipt(
                "typed-stale-receipt", accepted_context.slice_agreement_ref,
                accepted_context.criteria_revision.id, "AC-B", "python-unittest",
                "tests.test_r5b_fixture", 0, "a" * 64, agent_context.revision,
                agent_context.revision, input_fingerprint_before="b" * 64,
                input_fingerprint_after="b" * 64,
            )
            assert agent_context.slice_outcome is not None
            stale_context = replace(
                agent_context,
                criterion_test_receipts=(receipt,),
                slice_outcome=replace(
                    agent_context.slice_outcome,
                    blockers=("CRITERION_PROVENANCE_STALE:AC-B",),
                ),
                completed_terminal=True,
            )
            stale = project_continuation(stale_context)
            verifications = stale.criterion_verification
            assert isinstance(verifications, list) and isinstance(verifications[0], dict)
            self.assertEqual(verifications[0]["status"], "STALE")
            self.assertEqual(stale.slice_outcome_status, agent_context.slice_outcome.status)
            self.assertTrue(stale.terminal_history)

    def test_partial_bounds_staleness_errors_and_missing_integration_fail_closed(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            _create(root)
            context = project_context(WorkflowService.open(root))
            assert context.accepted_slice is not None
            active_ref = context.accepted_slice.slice_agreement_ref
            inventory = context.slice_acceptance_inventory
            bounded = replace(
                context,
                slice_acceptance_inventory=replace(
                    inventory, total=40, complete=False, truncated=True,
                    items=tuple(item for item in inventory.items if item.slice_agreement_ref == active_ref),
                ),
                work_queue=replace(
                    context.work_queue, total=40, complete=False,
                    next_candidates_total=33, next_candidates_complete=False,
                    relations_total=70, relations_complete=False,
                ),
                stale=True,
                stale_reason="semantic lock assessment is stale",
            )
            summary = project_continuation(bounded)
            self.assertEqual(summary.status, "STALE")
            self.assertEqual(summary.inventory_total, 40)
            self.assertTrue(summary.inventory_truncated)
            self.assertFalse(summary.inventory_complete)
            self.assertEqual(summary.queue_candidates_total, 33)
            self.assertFalse(summary.queue_candidates_complete)
            self.assertEqual(summary.queue_relations_total, 70)
            self.assertFalse(summary.queue_relations_complete)

            self.assertEqual(_context(root)["continuation"]["status"], "ACTIVE_SLICE")
            output = io.StringIO()
            self.assertEqual(cli_main(["--project", str(root), "status"], output), 0)
            self.assertNotIn("OpenCode:", output.getvalue())

        with _temporary_directory() as temporary:
            root = Path(temporary)
            before = _snapshot(root)
            response = json.loads(execute(root, '{"action":"context"}'))
            self.assertEqual(response["context"]["continuation"]["status"], "UNINITIALIZED")
            self.assertEqual(_snapshot(root), before)

        with _temporary_directory() as temporary:
            root = Path(temporary)
            _create(root)
            copied = Path(temporary) / "wrong-root"
            copied.mkdir()
            shutil.copytree(root / ".evospec", copied / ".evospec")
            before = _snapshot(copied)
            response = json.loads(execute(copied, '{"action":"context"}'))
            self.assertEqual(response["error"]["code"], "WORKFLOW_ERROR")
            self.assertEqual(_snapshot(copied), before)

        with _temporary_directory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            state_file = root / ".evospec" / "state.json"
            state_file.write_text("{corrupt", encoding="utf-8")
            before = state_file.read_bytes()
            response = json.loads(execute(root, '{"action":"context"}'))
            self.assertEqual(response["error"]["code"], "WORKFLOW_ERROR")
            self.assertEqual(state_file.read_bytes(), before)

        with _temporary_directory() as temporary:
            root = Path(temporary)
            WorkflowService.open(root).init()
            state_file = root / ".evospec" / "state.json"
            unsupported = json.loads(state_file.read_text(encoding="utf-8"))
            unsupported["schema"] = 999
            state_file.write_text(json.dumps(unsupported), encoding="utf-8")
            before = state_file.read_bytes()
            response = json.loads(execute(root, '{"action":"context"}'))
            self.assertEqual(response["error"]["code"], "WORKFLOW_ERROR")
            self.assertEqual(state_file.read_bytes(), before)

    def test_single_slice_cli_and_protocol_remain_consistent(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            _create(root, two_slices=False)
            protocol = _context(root)["continuation"]
            output = io.StringIO()
            self.assertEqual(cli_main(["--project", str(root), "status"], output), 0)
            self.assertEqual(protocol["status"], "ACTIVE_SLICE")
            self.assertEqual(protocol["active_slice"]["slice_title"], SLICE_A)
            self.assertIn(f"Accepted Slice: {SLICE_A}", output.getvalue())
            self.assertIn(f"Active Slice: {SLICE_A}", output.getvalue())

    @staticmethod
    def _assert_task_reported_unverified(context: dict[str, object]) -> None:
        summary = context["continuation"]
        assert isinstance(summary, dict)
        assert isinstance(summary["task_report"], dict)
        assert isinstance(summary["criterion_verification"], list)
        assert summary["task_report"]["report_kind"] == "REPORTED_UNVERIFIED"
        assert summary["criterion_verification"][0]["status"] == "UNVERIFIED"


if __name__ == "__main__":
    unittest.main()
