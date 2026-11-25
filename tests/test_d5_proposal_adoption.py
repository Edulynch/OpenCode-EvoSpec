"""D5: one explicitly accepted provisional head and ordinary Task continuation."""

from __future__ import annotations

import hashlib
import io
import json
import multiprocessing
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Protocol, TypedDict
from unittest.mock import patch

from evospec.cli import main as cli_main
from evospec.domain import (
    AssessmentConfidence,
    AssessmentEvidence,
    ConvergenceAssessment,
    ExpectedRevision,
    ProvisionalSliceCriterion,
    ProvisionalSliceHead,
    ProvisionalSliceRevision,
    RepositoryPath,
    ReviewResult,
    SliceCriterion,
    State,
    VerificationEvidence,
)
from evospec.lifecycle import ReviewVerdict
from evospec.persistence import FileOperations, StateStore
from evospec.services import (
    CoreService,
    ServiceError,
    SliceCriteriaSpec,
    StaleRevisionError,
    UserAcceptance,
)
from evospec.workflow import WorkflowError, WorkflowResult, WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_persistence import _create_d4_session_request_fixture


class _TTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class D5Workspace(TypedDict):
    repository_id: str
    feature_id: str
    selected_head: ProvisionalSliceHead
    selected_revision: ProvisionalSliceRevision
    other_head: ProvisionalSliceHead
    initial_state: State
    first_proposal_revision: int
    superseding_proposal_revision: int
    state: State


class AdoptionInputs(TypedDict):
    proposal_id: str
    revision_id: str
    proposal_revision: int
    proposal_digest: str
    slice_title: str
    initial_task: str
    checkpoint: str
    criteria: SliceCriteriaSpec
    question_answers: tuple[tuple[str, str], ...]


class _D5Barrier(Protocol):
    def wait(self, timeout: float | None = None) -> int: ...


class _D5Outcomes(Protocol):
    def put(self, item: object) -> None: ...


class FailD5StateReplace(FileOperations):
    def replace(self, source: Path, target: Path) -> None:
        if target.name == "state.json":
            raise OSError("injected D5 precommit State replacement failure")
        super().replace(source, target)


class FailD5AfterStateReplace(FileOperations):
    def __init__(self) -> None:
        self.state_pointer_replaced = False

    def replace(self, source: Path, target: Path) -> None:
        super().replace(source, target)
        if target.name == "state.json":
            self.state_pointer_replaced = True
            raise OSError("injected D5 failure after State pointer replacement")


def _acceptance(statement: str) -> UserAcceptance:
    return UserAcceptance(
        statement,
        f"tty:{hashlib.sha256(statement.encode('utf-8')).hexdigest()[:12]}",
    )


def _simulated_tty_adoption(
    workflow: WorkflowService,
    repository_id: str,
    feature_id: str,
    values: AdoptionInputs,
    expected_revision: int,
    *,
    response: str | None = None,
) -> tuple[WorkflowResult, str, str]:
    statement = workflow.provisional_slice_adoption_agreement(
        repository_id,
        feature_id,
        **values,
        expected_revision=ExpectedRevision(expected_revision),
    )
    token = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]
    output = _TTYStringIO()
    with (
        patch("evospec.services.sys.stdin", _TTYStringIO()),
        patch("evospec.services.sys.stdout", output),
        patch(
            "builtins.input",
            return_value=f"ACEPTO {token}" if response is None else response,
        ),
    ):
        result = workflow.adopt_provisional_slice(
            repository_id,
            feature_id,
            **values,
            expected_revision=ExpectedRevision(expected_revision),
        )
    return result, statement, output.getvalue()


def _d5_competing_adoption_worker(
    root: str,
    repository_id: str,
    feature_id: str,
    expected_revision: int,
    values: AdoptionInputs,
    expected_statement: str,
    ready: _D5Barrier,
    outcomes: _D5Outcomes,
) -> None:
    path = Path(root)
    workflow = WorkflowService.open(path)
    state = CoreService.open(path).recover()
    if state.revision != expected_revision:
        outcomes.put(("unexpected_revision", values["proposal_id"], state.revision))
        return
    statement = workflow.provisional_slice_adoption_agreement(
        repository_id,
        feature_id,
        **values,
        expected_revision=ExpectedRevision(expected_revision),
    )
    if statement != expected_statement:
        outcomes.put(("unexpected_statement", values["proposal_id"]))
        return
    token = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]

    def wait_for_confirmation(_prompt: str) -> str:
        ready.wait(timeout=25)
        return f"ACEPTO {token}"

    output = _TTYStringIO()
    try:
        with (
            patch("evospec.services.sys.stdin", _TTYStringIO()),
            patch("evospec.services.sys.stdout", output),
            patch("builtins.input", side_effect=wait_for_confirmation),
        ):
            result = workflow.adopt_provisional_slice(
                repository_id,
                feature_id,
                **values,
                expected_revision=ExpectedRevision(expected_revision),
            )
    except WorkflowError as error:
        outcomes.put(("rejected", values["proposal_id"], str(error)))
    else:
        outcomes.put(
            ("committed", values["proposal_id"], result.code, result.revision)
        )


def _snapshot(root: Path) -> dict[str, bytes]:
    managed = root / ".evospec"
    return {
        path.relative_to(managed).as_posix(): path.read_bytes()
        for path in managed.rglob("*")
        if path.is_file()
    }


class D5ProposalAdoptionTests(unittest.TestCase):
    def _workspace(self, root: Path) -> tuple[WorkflowService, D5Workspace]:
        workflow, initial = _create_d4_session_request_fixture(root)
        assert initial.initialization is not None
        feature_id = str(initial.features[0].id)
        repository_id = initial.initialization.repository_id
        workflow.activate_provisional_slice_writes(
            repository_id,
            feature_id,
            expected_revision=ExpectedRevision(initial.revision),
        )

        def propose(
            title: str,
            outcome: str,
            expected_head: ProvisionalSliceHead | None,
            *,
            example: str | None,
            inclusions: tuple[str, ...],
            exclusions: tuple[str, ...],
            considerations: tuple[str, ...],
            questions: tuple[str, ...],
        ) -> WorkflowResult:
            state = CoreService.open(root).recover()
            return workflow.propose_provisional_slice(
                repository_id,
                feature_id,
                expected_head,
                title,
                outcome,
                example,
                inclusions,
                exclusions,
                considerations,
                questions,
                (ProvisionalSliceCriterion("c-1", "The retained result is observable", "Focused test"),),
                expected_revision=ExpectedRevision(state.revision),
            )

        first = propose(
            "Create a bounded local item",
            "A local item can be created and retained",
            None,
            example="Create one item and reopen the local view",
            inclusions=("Create one local item",),
            exclusions=("No account or synchronization",),
            considerations=("Keep persistence local",),
            questions=("Which local persistence boundary is intended?",),
        )
        state = CoreService.open(root).recover()
        selected_head = next(item for item in state.provisional_slice_heads if item.proposal_id)
        second = propose(
            "Supersede with the final bounded proposal",
            "The selected local item remains available after restart",
            selected_head,
            example="Create one item, restart, and confirm it remains",
            inclusions=("Create and retain one local item",),
            exclusions=("No account, synchronization, or unrelated UI",),
            considerations=("Keep persistence local",),
            questions=("Which local persistence boundary is intended?",),
        )
        state = CoreService.open(root).recover()
        selected_head = next(item for item in state.provisional_slice_heads if item.proposal_id == selected_head.proposal_id)
        propose(
            "Independent unselected proposal",
            "An independent bounded result",
            None,
            example="Observe an independent result",
            inclusions=("Keep this proposal unselected",),
            exclusions=("No implied adoption",),
            considerations=(),
            questions=(),
        )

        state = CoreService.open(root).recover()
        assert state.active_context is not None
        assert len(state.agreements) == 2
        initial_check = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "from pathlib import Path; import sys; "
                "p=Path(sys.argv[1], 'd4-fixture.txt'); p.write_text('D4 settled', encoding='utf-8'); "
                "print(p.read_text(encoding='utf-8'))",
                str(root),
            ],
            cwd=root,
            capture_output=True,
            check=False,
        )
        self.assertEqual(initial_check.returncode, 0, initial_check.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(initial_check.stdout.strip(), b"D4 settled")
        initial_feature, initial_slice = state.agreements
        workflow.accepted_task_progress(
            repository_id,
            initial_slice.task_id or "",
            initial_feature.id,
            initial_slice.id,
            initial_slice.checkpoint or "",
            "Reported the initial request work without claiming verification.",
            ("tests/test_d5_proposal_adoption.py",),
            expected_revision=ExpectedRevision(state.revision),
        )
        state = CoreService.open(root).recover()
        workflow.record_review(
            ReviewResult(ReviewVerdict.PASS, (), True),
            expected_revision=ExpectedRevision(state.revision),
        )
        state = CoreService.open(root).recover()
        workflow.record_verification(
            VerificationEvidence(
                (
                    "isolated fixture subprocess exit=0; "
                    f"stdout-sha256={hashlib.sha256(initial_check.stdout + initial_check.stderr).hexdigest()}",
                ),
                True,
            ),
            expected_revision=ExpectedRevision(state.revision),
        )
        state = CoreService.open(root).recover()
        active = state.active_context
        assert active is not None and active.change_id is not None
        workflow.converge(
            ConvergenceAssessment(
                active.change_id,
                False,
                AssessmentConfidence.MEDIUM,
                (AssessmentEvidence("D5 fixture", RepositoryPath("tests/test_d5_proposal_adoption.py"), active.feature_id),),
                "The initial request adds no Product Truth.",
            ),
            expected_revision=ExpectedRevision(state.revision),
        )
        closed = CoreService.open(root).recover()
        return workflow, {
            "repository_id": repository_id,
            "feature_id": feature_id,
            "selected_head": selected_head,
            "selected_revision": next(
                item for item in closed.provisional_slice_revisions
                if item.revision_id == selected_head.revision_id
            ),
            "other_head": next(item for item in closed.provisional_slice_heads if item.proposal_id != selected_head.proposal_id),
            "initial_state": initial,
            "first_proposal_revision": first.revision,
            "superseding_proposal_revision": second.revision,
            "state": closed,
        }

    def _adoption_inputs(
        self,
        context: D5Workspace,
        proposal: ProvisionalSliceRevision | None = None,
    ) -> AdoptionInputs:
        proposal = context["selected_revision"] if proposal is None else proposal
        spec = SliceCriteriaSpec(
            outcome=proposal.outcome,
            example=proposal.example or "A direct observable check of the accepted result",
            inclusions=proposal.inclusions,
            exclusions=proposal.exclusions,
            decisions=("Keep the accepted work local and bounded.",),
            criteria=tuple(
                SliceCriterion(item.id, item.expected_result, item.expected_evidence)
                for item in proposal.criteria
            ),
        )
        return {
            "proposal_id": proposal.proposal_id,
            "revision_id": proposal.revision_id,
            "proposal_revision": proposal.revision,
            "proposal_digest": proposal.digest,
            "slice_title": proposal.title,
            "initial_task": "Implement the accepted local item",
            "checkpoint": "The created item remains visible after restart",
            "criteria": spec,
            "question_answers": (
                (proposal.open_questions[0], "Use the existing local persistence mechanism only."),
            ) if proposal.open_questions else (),
        }

    def _agreement(
        self,
        workflow: WorkflowService,
        state: State,
        context: D5Workspace,
        values: AdoptionInputs,
    ) -> str:
        return workflow.provisional_slice_adoption_agreement(
            context["repository_id"],
            context["feature_id"],
            **values,
            expected_revision=ExpectedRevision(state.revision),
        )

    def test_d5_adoption_rejects_plain_receipts_at_workflow_and_service_boundaries(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-receipt-boundary-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            statement = self._agreement(workflow, before, context, values)
            receipt = _acceptance(statement)
            snapshot = _snapshot(root)

            with (
                patch("evospec.services.sys.stdin", io.StringIO()),
                patch("evospec.services.sys.stdout", io.StringIO()),
            ):
                try:
                    getattr(workflow, "adopt_provisional_slice")(
                        context["repository_id"],
                        context["feature_id"],
                        **values,
                        acceptance=receipt,
                        expected_revision=ExpectedRevision(before.revision),
                    )
                    workflow_rejected = False
                except TypeError:
                    workflow_rejected = True
            self.assertTrue(workflow_rejected, "WorkflowService must not accept caller-submitted D5 receipts")
            self.assertEqual(_snapshot(root), snapshot)

            core = CoreService.open(root)
            with (
                patch("evospec.services.sys.stdin", io.StringIO()),
                patch("evospec.services.sys.stdout", io.StringIO()),
            ):
                try:
                    getattr(core, "adopt_provisional_slice")(
                        before,
                        context["repository_id"],
                        context["feature_id"],
                        **values,
                        acceptance=receipt,
                    )
                    service_rejected = False
                except TypeError:
                    service_rejected = True
            self.assertTrue(service_rejected, "CoreService must not accept caller-submitted D5 receipts")
            self.assertEqual(_snapshot(root), snapshot)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_d5_workflow_and_core_mutations_require_terminal_before_writing(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-no-tty-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            snapshot = _snapshot(root)

            with (
                patch("evospec.services.sys.stdin", io.StringIO()),
                patch("evospec.services.sys.stdout", io.StringIO()),
                self.assertRaises(WorkflowError),
            ):
                workflow.adopt_provisional_slice(
                    context["repository_id"],
                    context["feature_id"],
                    **values,
                    expected_revision=ExpectedRevision(before.revision),
                )
            self.assertEqual(_snapshot(root), snapshot)
            self.assertEqual(CoreService.open(root).recover(), before)

            with (
                patch("evospec.services.sys.stdin", io.StringIO()),
                patch("evospec.services.sys.stdout", io.StringIO()),
                self.assertRaises(ServiceError),
            ):
                CoreService.open(root).adopt_provisional_slice(
                    before,
                    context["repository_id"],
                    context["feature_id"],
                    **values,
                )
            self.assertEqual(_snapshot(root), snapshot)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_d5_refusal_and_eof_prompt_once_and_leave_state_unchanged(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-refusal-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            statement = self._agreement(workflow, before, context, values)
            token = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]
            expected_prompt = f"Para autorizar la escritura, escribe ACEPTO {token}: "
            snapshot = _snapshot(root)

            for response in ("ACEPTO wrong", EOFError()):
                with self.subTest(response=response):
                    output = _TTYStringIO()
                    input_mock = (
                        patch("builtins.input", side_effect=response)
                        if isinstance(response, BaseException)
                        else patch("builtins.input", return_value=response)
                    )
                    with (
                        patch("evospec.services.sys.stdin", _TTYStringIO()),
                        patch("evospec.services.sys.stdout", output),
                        input_mock as prompt,
                        self.assertRaises(WorkflowError),
                    ):
                        workflow.adopt_provisional_slice(
                            context["repository_id"],
                            context["feature_id"],
                            **values,
                            expected_revision=ExpectedRevision(before.revision),
                        )
                    prompt.assert_called_once_with(expected_prompt)
                    self.assertIn(statement, output.getvalue())
                    self.assertEqual(_snapshot(root), snapshot)
                    self.assertEqual(CoreService.open(root).recover(), before)

    def _adoption_cli_arguments(
        self,
        root: Path,
        context: D5Workspace,
        values: AdoptionInputs,
        expected_revision: int,
    ) -> tuple[str, ...]:
        criteria = values["criteria"]
        assert isinstance(criteria, SliceCriteriaSpec)
        criteria_path = root / "d5-scope.json"
        criteria_path.write_text(
            json.dumps({
                "outcome": criteria.outcome,
                "example": criteria.example,
                "inclusions": criteria.inclusions,
                "exclusions": criteria.exclusions,
                "decisions": criteria.decisions,
                "criteria": [
                    {
                        "id": item.id,
                        "expected_result": item.expected_result,
                        "expected_evidence": item.expected_evidence,
                    }
                    for item in criteria.criteria
                ],
            }),
            encoding="utf-8",
        )
        arguments = [
            "--project", str(root), "adopt-provisional-slice",
            "--feature-id", str(context["feature_id"]),
            "--proposal-id", str(values["proposal_id"]),
            "--revision-id", str(values["revision_id"]),
            "--proposal-revision", str(values["proposal_revision"]),
            "--proposal-digest", str(values["proposal_digest"]),
            "--slice", str(values["slice_title"]),
            "--task", str(values["initial_task"]),
            "--checkpoint", str(values["checkpoint"]),
            "--criteria-file", str(criteria_path),
        ]
        for question, answer in values["question_answers"]:
            arguments.extend(("--question-answer", f"{question}={answer}"))
        arguments.extend(("--expected-revision", str(expected_revision)))
        return tuple(arguments)

    def test_adopt_exact_superseding_head_and_continue_only_as_unverified_task_work(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-adopt-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            source_files = _snapshot(root)
            assert before.provisional_slice_activation is not None
            selected = context["selected_revision"]
            selected_head = context["selected_head"]
            other_head = context["other_head"]
            self.assertEqual(selected.revision, 2)
            self.assertEqual(selected.provenance.kind, "AGENT_SUGGESTION")
            self.assertEqual(len(before.terminal_archives), 1)
            convergence = before.terminal_archives[0].convergence_assessment
            assert convergence is not None
            self.assertFalse(convergence.product_truth_changed)
            values = self._adoption_inputs(context)
            statement = self._agreement(workflow, before, context, values)

            result, shown_statement, output = _simulated_tty_adoption(
                workflow,
                context["repository_id"],
                context["feature_id"],
                values,
                before.revision,
            )
            adopted = CoreService.open(root).recover()

            self.assertEqual(shown_statement, statement)
            self.assertIn(statement, output)
            self.assertEqual(result.code, "PROVISIONAL_SLICE_ADOPTED")
            self.assertEqual(adopted.revision, before.revision + 1)
            self.assertEqual(adopted.features, before.features)
            self.assertEqual(adopted.agreements[:2], before.agreements)
            self.assertEqual(adopted.changes[:1], before.changes)
            self.assertEqual(adopted.terminal_archives, before.terminal_archives)
            self.assertEqual(adopted.work_items, before.work_items)
            self.assertEqual(adopted.provisional_slice_revisions, before.provisional_slice_revisions)
            self.assertEqual(adopted.provisional_slice_heads, before.provisional_slice_heads)
            self.assertIn(selected_head, adopted.provisional_slice_heads)
            self.assertIn(other_head, adopted.provisional_slice_heads)
            self.assertEqual(len(adopted.provisional_slice_adoptions), 1)
            adoption = adopted.provisional_slice_adoptions[0]
            self.assertEqual(adoption.proposal_revision_id, selected_head.revision_id)
            self.assertEqual(adoption.proposal_digest, selected_head.digest)
            self.assertEqual(adoption.statement, statement)
            self.assertEqual(len(adopted.changes), 2)
            self.assertEqual(len(adopted.agreements), 4)
            self.assertEqual(adopted.slice_criteria_revisions[0].source, "INTERACTIVE_TERMINAL")
            self.assertEqual(adopted.slice_criteria_heads[0].slice_agreement_ref, adoption.slice_agreement_ref)
            active = adopted.active_context
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.feature_id, before.features[0].id)
            self.assertEqual(active.change_id, adoption.change_id)
            self.assertEqual(active.slice_agreement_ref, adoption.slice_agreement_ref)
            self.assertEqual(active.current_work, adoption.initial_task)
            self.assertIsNone(active.task_progress)
            for relative, data in source_files.items():
                if relative != "state.json":
                    self.assertEqual(_snapshot(root)[relative], data)
            after_adoption_files = _snapshot(root)
            self.assertTrue(any(path.startswith("records/provisional-slice-adoptions/") for path in after_adoption_files))

            write_item = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "from pathlib import Path; import sys; "
                    "Path(sys.argv[1], 'd5-fixture-item.txt').write_text('retained item', encoding='utf-8')",
                    str(root),
                ],
                cwd=root,
                capture_output=True,
                check=False,
            )
            self.assertEqual(write_item.returncode, 0, write_item.stderr.decode("utf-8", errors="replace"))
            self.assertEqual((root / "d5-fixture-item.txt").read_text(encoding="utf-8"), "retained item")
            progress = workflow.accepted_task_progress(
                adoption.repository_id,
                adoption.task_id,
                adoption.feature_agreement_ref,
                adoption.slice_agreement_ref,
                adoption.checkpoint,
                "Created the accepted local fixture item; this report is not verification.",
                ("d5-fixture-item.txt",),
                expected_revision=ExpectedRevision(adopted.revision),
            )
            reported = CoreService.open(root).recover()
            self.assertEqual(progress.code, "TASK_PROGRESS_RECORDED")
            assert reported.active_context is not None and reported.active_context.task_progress is not None
            self.assertEqual(reported.active_context.task_progress.report_kind, "REPORTED_UNVERIFIED")
            self.assertFalse(reported.criterion_test_receipts)
            self.assertFalse(reported.slice_outcome_receipts)

            reopen_item = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "from pathlib import Path; import sys; "
                    "print(Path(sys.argv[1], 'd5-fixture-item.txt').read_text(encoding='utf-8'))",
                    str(root),
                ],
                cwd=root,
                capture_output=True,
                check=False,
            )
            self.assertEqual(reopen_item.returncode, 0, reopen_item.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(reopen_item.stdout.strip(), b"retained item")
            reviewed = workflow.record_review(
                ReviewResult(ReviewVerdict.PASS, (), True),
                expected_revision=ExpectedRevision(reported.revision),
            )
            verified = workflow.record_verification(
                VerificationEvidence(
                    (
                        "isolated fixture subprocess exit=0; "
                        f"stdout-sha256={hashlib.sha256(reopen_item.stdout + reopen_item.stderr).hexdigest()}; "
                        f"observed={reopen_item.stdout.decode('utf-8').strip()}",
                    ),
                    True,
                ),
                expected_revision=ExpectedRevision(reviewed.revision),
            )
            current = CoreService.open(root).recover()
            assert current.active_context is not None and current.active_context.change_id is not None
            done = workflow.converge(
                ConvergenceAssessment(
                    current.active_context.change_id,
                    False,
                    AssessmentConfidence.MEDIUM,
                    (AssessmentEvidence("caller report", RepositoryPath("tests/test_d5_proposal_adoption.py"), current.active_context.feature_id),),
                    "The Task converged without changing Product Truth.",
                ),
                expected_revision=ExpectedRevision(verified.revision),
            )
            settled = CoreService.open(root).recover()
            self.assertEqual(done.code, "DONE")
            self.assertIsNone(settled.active_context)
            self.assertEqual(len(settled.terminal_archives), 2)
            self.assertEqual(len(settled.work_items), 2)
            self.assertEqual(settled.provisional_slice_adoptions, adopted.provisional_slice_adoptions)
            self.assertEqual(settled.provisional_slice_revisions, before.provisional_slice_revisions)
            self.assertEqual(settled.provisional_slice_heads, before.provisional_slice_heads)
            self.assertFalse(settled.criterion_test_receipts)
            self.assertFalse(settled.slice_outcome_receipts)
            self.assertFalse(settled.feature_completion_receipts)
            self.assertEqual(StateStore(root).load_state(), settled)

    def test_cli_d5_adoption_requires_one_exact_tty_confirmation(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-cli-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            statement = self._agreement(workflow, before, context, values)
            token = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]
            arguments = self._adoption_cli_arguments(root, context, values, before.revision)
            snapshot = _snapshot(root)

            output = _TTYStringIO()
            with (
                patch("evospec.cli.sys.stdin", _TTYStringIO()),
                patch("evospec.cli.sys.stdout", output),
                patch("builtins.input", return_value=f"ACEPTO {'0' * 12}") as prompt,
            ):
                self.assertEqual(cli_main(arguments, output), 2)
            prompt.assert_called_once_with(
                f"Para autorizar la escritura, escribe ACEPTO {token}: ",
            )
            self.assertIn(statement, output.getvalue())
            self.assertIn("not explicitly confirmed", output.getvalue())
            self.assertEqual(_snapshot(root), snapshot)
            self.assertEqual(CoreService.open(root).recover(), before)

            output = _TTYStringIO()
            with (
                patch("evospec.cli.sys.stdin", _TTYStringIO()),
                patch("evospec.cli.sys.stdout", output),
                patch("builtins.input", return_value=f"ACEPTO {token}") as prompt,
            ):
                self.assertEqual(cli_main(arguments, output), 0)
            prompt.assert_called_once_with(
                f"Para autorizar la escritura, escribe ACEPTO {token}: ",
            )
            adopted = CoreService.open(root).recover()
            self.assertEqual(adopted.revision, before.revision + 1)
            self.assertEqual(len(adopted.provisional_slice_adoptions), 1)
            self.assertEqual(adopted.provisional_slice_adoptions[0].statement, statement)
            self.assertEqual(adopted.provisional_slice_adoptions[0].source_reference, f"tty:{token}")
            self.assertIn("PROVISIONAL_SLICE_ADOPTED", output.getvalue())

    def test_cli_d5_adoption_rejects_redirected_tty_and_stale_revision_without_prompt(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-cli-gates-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            arguments = self._adoption_cli_arguments(root, context, values, before.revision)
            snapshot = _snapshot(root)

            for redirected in ("stdin", "stdout", "target", "injected-target"):
                with self.subTest(redirected=redirected):
                    stdin = io.StringIO() if redirected == "stdin" else _TTYStringIO()
                    stdout = io.StringIO() if redirected == "stdout" else _TTYStringIO()
                    target = io.StringIO() if redirected == "target" else _TTYStringIO()
                    with (
                        patch("evospec.cli.sys.stdin", stdin),
                        patch("evospec.cli.sys.stdout", stdout),
                        patch("builtins.input") as prompt,
                    ):
                        self.assertEqual(cli_main(arguments, target), 2)
                    prompt.assert_not_called()
                    self.assertEqual(_snapshot(root), snapshot)
                    self.assertEqual(CoreService.open(root).recover(), before)

            stale_arguments = self._adoption_cli_arguments(
                root, context, values, before.revision - 1,
            )
            output = _TTYStringIO()
            with (
                patch("evospec.cli.sys.stdin", _TTYStringIO()),
                patch("evospec.cli.sys.stdout", output),
                patch("builtins.input") as prompt,
            ):
                self.assertEqual(cli_main(stale_arguments, output), 2)
            prompt.assert_not_called()
            self.assertIn("expected state revision", output.getvalue().lower())
            self.assertEqual(_snapshot(root), snapshot)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_cli_d5_state_change_while_confirmation_is_open_fails_cas(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-cli-stale-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            statement = self._agreement(workflow, before, context, values)
            token = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]
            arguments = self._adoption_cli_arguments(root, context, values, before.revision)
            after_external_change: dict[str, object] = {}

            def change_state_during_input(_prompt: str) -> str:
                workflow.propose_provisional_slice(
                    context["repository_id"],
                    context["feature_id"],
                    None,
                    "Proposal created during confirmation",
                    "A concurrent proposal remains independent",
                    "Observe the independent result",
                    ("Keep this proposal independent",),
                    ("No adoption is implied",),
                    (),
                    (),
                    (ProvisionalSliceCriterion("c-1", "An independent result", "Focused check"),),
                    expected_revision=ExpectedRevision(before.revision),
                )
                after_external_change["state"] = CoreService.open(root).recover()
                after_external_change["snapshot"] = _snapshot(root)
                return f"ACEPTO {token}"

            output = _TTYStringIO()
            with (
                patch("evospec.cli.sys.stdin", _TTYStringIO()),
                patch("evospec.cli.sys.stdout", output),
                patch("builtins.input", side_effect=change_state_during_input) as prompt,
            ):
                self.assertEqual(cli_main(arguments, output), 2)
            prompt.assert_called_once()
            self.assertIn(statement, output.getvalue())
            self.assertIn("expected state revision", output.getvalue().lower())
            self.assertEqual(
                _snapshot(root),
                after_external_change["snapshot"],
            )
            current = CoreService.open(root).recover()
            self.assertEqual(current, after_external_change["state"])
            self.assertFalse(current.provisional_slice_adoptions)

    def test_schema23_adoption_promotes_one_schema24_lane_and_rejects_a_second_choice(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-schema24-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            state_path = root / ".evospec" / "state.json"
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], 23)

            other_head = context["other_head"]
            other_proposal = next(
                item for item in before.provisional_slice_revisions
                if item.revision_id == other_head.revision_id
            )
            values = self._adoption_inputs(context)
            other_values = self._adoption_inputs(context, other_proposal)
            statement = self._agreement(workflow, before, context, values)
            adopted_result, shown_statement, output = _simulated_tty_adoption(
                workflow,
                context["repository_id"],
                context["feature_id"],
                values,
                before.revision,
            )
            adopted = CoreService.open(root).recover()
            raw = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(shown_statement, statement)
            self.assertIn(statement, output)
            self.assertEqual(adopted_result.code, "PROVISIONAL_SLICE_ADOPTED")
            self.assertEqual(raw["schema"], 24)
            self.assertEqual(raw["revision"], before.revision + 1)
            self.assertEqual(len(adopted.provisional_slice_adoptions), 1)
            adoption = adopted.provisional_slice_adoptions[0]
            self.assertEqual(adoption.proposal_id, values["proposal_id"])
            self.assertEqual(adoption.statement, statement)
            self.assertEqual(
                raw["provisional_slice_adoption_refs"],
                [{
                    "id": adoption.id,
                    "path": f"records/provisional-slice-adoptions/{adoption.id}.json",
                }],
            )
            self.assertEqual(
                adopted.features,
                before.features,
            )
            self.assertEqual(adopted.agreements[:2], before.agreements)
            self.assertEqual(len(adopted.agreements), len(before.agreements) + 2)
            self.assertEqual(adopted.changes[:1], before.changes)
            self.assertEqual(len(adopted.changes), len(before.changes) + 1)
            self.assertEqual(
                adopted.provisional_slice_revisions,
                before.provisional_slice_revisions,
            )
            self.assertEqual(adopted.provisional_slice_heads, before.provisional_slice_heads)
            self.assertEqual(len(adopted.slice_criteria_revisions), 1)
            self.assertEqual(len(adopted.slice_criteria_heads), 1)
            self.assertEqual(
                adopted.slice_criteria_heads[0].slice_agreement_ref,
                adoption.slice_agreement_ref,
            )
            self.assertEqual(
                adopted.slice_criteria_heads[0].revision_id,
                adopted.slice_criteria_revisions[0].id,
            )
            self.assertEqual(adopted.work_items, before.work_items)
            self.assertEqual(adopted.terminal_archives, before.terminal_archives)
            self.assertFalse(adopted.criterion_test_receipts)
            self.assertFalse(adopted.slice_outcome_receipts)
            self.assertFalse(adopted.feature_completion_receipts)
            assert adopted.active_context is not None
            self.assertEqual(adopted.active_context.change_id, adoption.change_id)
            self.assertEqual(adopted.active_context.slice_agreement_ref, adoption.slice_agreement_ref)
            self.assertEqual(adopted.active_context.current_work, adoption.initial_task)
            self.assertIsNone(adopted.active_context.task_progress)

            committed = _snapshot(root)
            with self.assertRaises(WorkflowError):
                workflow.adopt_provisional_slice(
                    context["repository_id"],
                    context["feature_id"],
                    **other_values,
                    expected_revision=ExpectedRevision(before.revision),
                )
            self.assertEqual(_snapshot(root), committed)
            self.assertEqual(CoreService.open(root).recover(), adopted)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], 24)

    def test_postpointer_failure_recovers_exact_adoption_and_rejects_changed_replay(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-postpointer-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            statement = self._agreement(workflow, before, context, values)
            operations = FailD5AfterStateReplace()
            interrupted = WorkflowService(root, CoreService.open(root, operations))

            with self.assertRaises(WorkflowError):
                _simulated_tty_adoption(
                    interrupted,
                    context["repository_id"],
                    context["feature_id"],
                    values,
                    before.revision,
                )
            self.assertTrue(operations.state_pointer_replaced)

            recovered = CoreService.open(root).recover()
            state_path = root / ".evospec" / "state.json"
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], 24)
            self.assertEqual(recovered.revision, before.revision + 1)
            self.assertEqual(recovered.features, before.features)
            self.assertEqual(recovered.agreements[:2], before.agreements)
            self.assertEqual(recovered.changes[:1], before.changes)
            self.assertEqual(
                recovered.provisional_slice_revisions,
                before.provisional_slice_revisions,
            )
            self.assertEqual(recovered.provisional_slice_heads, before.provisional_slice_heads)
            self.assertEqual(recovered.work_items, before.work_items)
            self.assertEqual(recovered.terminal_archives, before.terminal_archives)
            self.assertEqual(len(recovered.provisional_slice_adoptions), 1)
            adoption = recovered.provisional_slice_adoptions[0]
            self.assertEqual(adoption.proposal_id, values["proposal_id"])
            self.assertEqual(adoption.statement, statement)
            self.assertEqual(
                raw["provisional_slice_adoption_refs"],
                [{
                    "id": adoption.id,
                    "path": f"records/provisional-slice-adoptions/{adoption.id}.json",
                }],
            )
            assert recovered.active_context is not None
            self.assertEqual(recovered.active_context.change_id, adoption.change_id)
            self.assertEqual(recovered.active_context.slice_agreement_ref, adoption.slice_agreement_ref)
            self.assertEqual(recovered.active_context.current_work, adoption.initial_task)
            self.assertIsNone(recovered.active_context.task_progress)
            committed = _snapshot(root)

            replay = workflow.adopt_provisional_slice(
                context["repository_id"],
                context["feature_id"],
                **values,
                expected_revision=ExpectedRevision(before.revision),
            )
            self.assertEqual(replay.code, "PROVISIONAL_SLICE_ADOPTION_RECOVERED")
            self.assertEqual(replay.revision, recovered.revision)
            self.assertEqual(_snapshot(root), committed)
            self.assertEqual(len(CoreService.open(root).recover().provisional_slice_adoptions), 1)

            changed_values: AdoptionInputs = {
                **values,
                "checkpoint": values["checkpoint"] + " (changed)",
            }
            with self.assertRaises(WorkflowError):
                workflow.adopt_provisional_slice(
                    context["repository_id"],
                    context["feature_id"],
                    **changed_values,
                    expected_revision=ExpectedRevision(before.revision),
                )
            self.assertEqual(_snapshot(root), committed)
            self.assertEqual(CoreService.open(root).recover(), recovered)

    def test_competing_processes_can_adopt_only_one_distinct_current_proposal(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-process-cas-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            heads = {item.proposal_id: item for item in before.provisional_slice_heads}
            selected_head = context["selected_head"]
            other_head = context["other_head"]
            self.assertNotEqual(selected_head.proposal_id, other_head.proposal_id)
            self.assertIn(selected_head.proposal_id, heads)
            self.assertIn(other_head.proposal_id, heads)
            proposals = {
                head.proposal_id: next(
                    item for item in before.provisional_slice_revisions
                    if item.revision_id == head.revision_id
                )
                for head in (selected_head, other_head)
            }
            choices = [
                self._adoption_inputs(context, proposals[head.proposal_id])
                for head in (selected_head, other_head)
            ]
            statements = [
                self._agreement(workflow, before, context, values)
                for values in choices
            ]
            self.assertNotEqual(choices[0]["proposal_id"], choices[1]["proposal_id"])
            self.assertNotEqual(statements[0], statements[1])

            process_context = multiprocessing.get_context("spawn")
            ready = process_context.Barrier(2)
            outcomes = process_context.Queue()
            processes = [
                process_context.Process(
                    target=_d5_competing_adoption_worker,
                    args=(
                        str(root),
                        str(context["repository_id"]),
                        str(context["feature_id"]),
                        before.revision,
                        values,
                        statement,
                        ready,
                        outcomes,
                    ),
                )
                for values, statement in zip(choices, statements)
            ]
            try:
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(25)
                lingering = [process for process in processes if process.is_alive()]
                for process in lingering:
                    process.terminate()
                    process.join()
                self.assertFalse(lingering, "D5 adoption CAS workers did not finish")
                self.assertEqual([process.exitcode for process in processes], [0, 0])
                results = [outcomes.get(timeout=5) for _ in processes]
            finally:
                outcomes.close()
                outcomes.join_thread()

            self.assertEqual(
                sorted(result[0] for result in results),
                ["committed", "rejected"],
            )
            committed_choice = next(result for result in results if result[0] == "committed")
            rejected_choice = next(result for result in results if result[0] == "rejected")
            self.assertIn(committed_choice[1], {item["proposal_id"] for item in choices})
            self.assertIn(rejected_choice[1], {item["proposal_id"] for item in choices})
            self.assertNotEqual(committed_choice[1], rejected_choice[1])

            saved = CoreService.open(root).recover()
            raw = json.loads((root / ".evospec" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(saved.revision, before.revision + 1)
            self.assertEqual(raw["schema"], 24)
            self.assertEqual(saved.provisional_slice_revisions, before.provisional_slice_revisions)
            self.assertEqual(saved.provisional_slice_heads, before.provisional_slice_heads)
            self.assertEqual(len(saved.provisional_slice_adoptions), 1)
            adoption = saved.provisional_slice_adoptions[0]
            self.assertEqual(
                raw["provisional_slice_adoption_refs"],
                [{
                    "id": adoption.id,
                    "path": f"records/provisional-slice-adoptions/{adoption.id}.json",
                }],
            )
            self.assertEqual(adoption.proposal_id, committed_choice[1])
            self.assertEqual(adoption.source_revision, before.revision)
            self.assertEqual(adoption.statement, statements[
                next(index for index, item in enumerate(choices) if item["proposal_id"] == committed_choice[1])
            ])
            self.assertEqual(saved.features, before.features)
            self.assertEqual(saved.agreements[:2], before.agreements)
            self.assertEqual(len(saved.agreements), len(before.agreements) + 2)
            self.assertEqual(saved.changes[:1], before.changes)
            self.assertEqual(len(saved.changes), len(before.changes) + 1)
            self.assertEqual(len(saved.slice_criteria_revisions), 1)
            self.assertEqual(len(saved.slice_criteria_heads), 1)
            self.assertEqual(
                saved.slice_criteria_heads[0].slice_agreement_ref,
                adoption.slice_agreement_ref,
            )
            self.assertEqual(
                saved.slice_criteria_heads[0].revision_id,
                saved.slice_criteria_revisions[0].id,
            )
            self.assertEqual(len(saved.provisional_slice_adoptions), 1)
            self.assertFalse(saved.criterion_test_receipts)
            self.assertFalse(saved.slice_outcome_receipts)
            self.assertFalse(saved.feature_completion_receipts)
            assert saved.active_context is not None
            self.assertEqual(saved.active_context.change_id, adoption.change_id)
            self.assertEqual(saved.active_context.slice_agreement_ref, adoption.slice_agreement_ref)
            self.assertEqual(saved.active_context.current_work, adoption.initial_task)
            adoption_records = tuple(
                (root / ".evospec" / "records" / "provisional-slice-adoptions").glob("*.json")
            )
            self.assertEqual(len(adoption_records), 1)

    def test_wrong_consent_and_unanswered_question_are_read_only(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-consent-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            statement = self._agreement(workflow, before, context, values)
            snapshot = _snapshot(root)
            with self.assertRaises(WorkflowError):
                _simulated_tty_adoption(
                    workflow,
                    context["repository_id"],
                    context["feature_id"],
                    values,
                    before.revision,
                    response="ACEPTO 000000000000",
                )
            missing_answers: AdoptionInputs = {**values, "question_answers": ()}
            with self.assertRaises(WorkflowError):
                self._agreement(workflow, before, context, missing_answers)
            self.assertEqual(_snapshot(root), snapshot)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_stale_state_and_wrong_head_bindings_fail_closed(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-stale-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            stale: AdoptionInputs = {**values, "proposal_digest": "0" * 64}
            snapshot = _snapshot(root)
            with self.assertRaises(WorkflowError):
                self._agreement(workflow, before, context, stale)
            statement = self._agreement(workflow, before, context, values)
            workflow.propose_provisional_slice(
                context["repository_id"],
                context["feature_id"],
                None,
                "A later unselected head",
                "A later proposal remains unselected",
                "An independently observable result",
                ("Keep separate",),
                ("No adoption implied",),
                (),
                (),
                (ProvisionalSliceCriterion("c-1", "Independent result", "Focused check"),),
                expected_revision=ExpectedRevision(before.revision),
            )
            after_proposal = CoreService.open(root).recover()
            changed_snapshot = _snapshot(root)
            with self.assertRaises(WorkflowError):
                workflow.adopt_provisional_slice(
                    context["repository_id"],
                    context["feature_id"],
                    **values,
                    expected_revision=ExpectedRevision(before.revision),
                )
            self.assertEqual(CoreService.open(root).recover(), after_proposal)
            self.assertEqual(_snapshot(root), changed_snapshot)
            self.assertNotEqual(snapshot, changed_snapshot)

    def test_exact_replay_recovers_binding_without_another_write(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-replay-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            statement = self._agreement(workflow, before, context, values)
            first, shown_statement, output = _simulated_tty_adoption(
                workflow,
                context["repository_id"],
                context["feature_id"],
                values,
                before.revision,
            )
            self.assertEqual(shown_statement, statement)
            self.assertIn(statement, output)
            committed = _snapshot(root)
            replay = workflow.adopt_provisional_slice(
                context["repository_id"],
                context["feature_id"],
                **values,
                expected_revision=ExpectedRevision(before.revision),
            )
            self.assertEqual(first.revision, replay.revision)
            self.assertEqual(replay.code, "PROVISIONAL_SLICE_ADOPTION_RECOVERED")
            self.assertEqual(_snapshot(root), committed)
            self.assertEqual(len(CoreService.open(root).recover().provisional_slice_adoptions), 1)

    def test_wrong_root_or_head_bindings_are_read_only(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-bindings-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            snapshot = _snapshot(root)
            wrong_values: tuple[AdoptionInputs, ...] = (
                {**values, "proposal_id": "proposal-not-current"},
                {**values, "revision_id": context["other_head"].revision_id},
                {**values, "proposal_revision": values["proposal_revision"] + 1},
                {**values, "proposal_digest": "0" * 64},
            )
            for item in wrong_values:
                with self.subTest(binding=item):
                    with self.assertRaises(WorkflowError):
                        self._agreement(workflow, before, context, item)
            with self.assertRaises(WorkflowError):
                workflow.provisional_slice_adoption_agreement(
                    "wrong-root",
                    context["feature_id"],
                    **values,
                    expected_revision=ExpectedRevision(before.revision),
                )
            with self.assertRaises(StaleRevisionError):
                workflow.provisional_slice_adoption_agreement(
                    context["repository_id"],
                    context["feature_id"],
                    **values,
                    expected_revision=ExpectedRevision(before.revision - 1),
                )
            self.assertEqual(_snapshot(root), snapshot)

    def test_adoption_collision_and_precommit_fault_leave_schema23_authoritative(self) -> None:
        with TemporaryDirectory(prefix="evospec-d5-collision-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            statement = self._agreement(workflow, before, context, values)
            adoption_id = "d5-adoption-" + hashlib.sha256(statement.encode("utf-8")).hexdigest()
            collision = root / ".evospec" / "records" / "provisional-slice-adoptions" / f"{adoption_id}.json"
            collision.parent.mkdir(parents=True, exist_ok=True)
            collision.write_bytes(b'{"wrong":"immutable record"}\n')
            collision_snapshot = _snapshot(root)
            with self.assertRaises(WorkflowError):
                _simulated_tty_adoption(
                    workflow,
                    context["repository_id"],
                    context["feature_id"],
                    values,
                    before.revision,
                )
            self.assertEqual(CoreService.open(root).recover(), before)
            self.assertEqual(_snapshot(root), collision_snapshot)

        with TemporaryDirectory(prefix="evospec-d5-fault-") as temporary:
            root = Path(temporary)
            workflow, context = self._workspace(root)
            before = context["state"]
            values = self._adoption_inputs(context)
            state_bytes = (root / ".evospec" / "state.json").read_bytes()
            failing_workflow = WorkflowService(
                root,
                CoreService.open(root, FailD5StateReplace()),
            )
            with self.assertRaises(WorkflowError):
                _simulated_tty_adoption(
                    failing_workflow,
                    context["repository_id"],
                    context["feature_id"],
                    values,
                    before.revision,
                )
            self.assertEqual((root / ".evospec" / "state.json").read_bytes(), state_bytes)
            self.assertEqual(CoreService.open(root).recover(), before)
            self.assertFalse(CoreService.open(root).recover().provisional_slice_adoptions)
            recovered, _, _ = _simulated_tty_adoption(
                workflow,
                context["repository_id"],
                context["feature_id"],
                values,
                before.revision,
            )
            self.assertEqual(recovered.code, "PROVISIONAL_SLICE_ADOPTED")
            self.assertEqual(
                len(CoreService.open(root).recover().provisional_slice_adoptions),
                1,
            )


if __name__ == "__main__":
    unittest.main()
