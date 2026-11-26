from __future__ import annotations

import json
import multiprocessing
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeGuard, TypedDict

from evospec.continuity import ContinuityError, execute
from evospec.persistence import AtomicFailure, FileOperations, RecoveryError, StateStore
from tests._tempdir import TemporaryDirectory


class _IncrementView(TypedDict):
    id: str


class _ExecutionView(TypedDict):
    task_id: str


class _FeatureView(TypedDict):
    id: str
    directory: str
    kind: str
    status: str
    paused: bool
    pause_reason: str | None
    increments: list[_IncrementView]
    tasks: list[dict[str, object]]
    criteria: list[dict[str, object]]
    decisions: list[dict[str, object]]
    detours: list[dict[str, object]]
    closure_history: list[object]
    reopen_history: list[object]
    plan: str


class _ContinuityView(TypedDict):
    revision: int
    mode: str | None
    status: str | None
    selected_feature: _FeatureView | None
    execution: _ExecutionView | None


def _record(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _text(value: object) -> TypeGuard[str]:
    return isinstance(value, str)


def _optional_text(value: object) -> TypeGuard[str | None]:
    return value is None or isinstance(value, str)


def _increment_view(value: object) -> TypeGuard[_IncrementView]:
    return _record(value) and _text(value.get("id"))


def _execution_view(value: object) -> TypeGuard[_ExecutionView]:
    return _record(value) and _text(value.get("task_id"))


def _record_views(value: object) -> TypeGuard[list[dict[str, object]]]:
    return isinstance(value, list) and all(_record(item) for item in value)


def _increment_views(value: object) -> TypeGuard[list[_IncrementView]]:
    return isinstance(value, list) and all(_increment_view(item) for item in value)


def _feature_view(value: object) -> TypeGuard[_FeatureView]:
    if not _record(value):
        return False
    return (
        _text(value.get("id"))
        and _text(value.get("directory"))
        and _text(value.get("kind"))
        and _text(value.get("status"))
        and isinstance(value.get("paused"), bool)
        and _optional_text(value.get("pause_reason"))
        and _increment_views(value.get("increments"))
        and _record_views(value.get("tasks"))
        and _record_views(value.get("criteria"))
        and _record_views(value.get("decisions"))
        and _record_views(value.get("detours"))
        and isinstance(value.get("closure_history"), list)
        and isinstance(value.get("reopen_history"), list)
        and _text(value.get("plan"))
    )


def _continuity_view(value: object) -> _ContinuityView:
    if not _record(value):
        raise AssertionError("expected continuity response object")
    revision = value.get("revision")
    mode = value.get("mode")
    status = value.get("status")
    selected_feature = value.get("selected_feature")
    execution = value.get("execution")
    if type(revision) is not int:
        raise AssertionError("expected integer continuity revision")
    if not _optional_text(mode) or not _optional_text(status):
        raise AssertionError("expected optional text continuity fields")
    if selected_feature is not None and not _feature_view(selected_feature):
        raise AssertionError("expected selected feature view")
    if execution is not None and not _execution_view(execution):
        raise AssertionError("expected execution view")
    return {
        "revision": revision,
        "mode": mode,
        "status": status,
        "selected_feature": selected_feature,
        "execution": execution,
    }


def _selected_feature(result: _ContinuityView) -> _FeatureView:
    feature = result["selected_feature"]
    if feature is None:
        raise AssertionError("expected selected feature")
    return feature


def _execution(result: _ContinuityView) -> _ExecutionView:
    execution = result["execution"]
    if execution is None:
        raise AssertionError("expected active execution")
    return execution


def _competing_process_plan(
    root: str,
    expected_revision: int,
    plan: str,
    ready: Any,
    start: Any,
    outcomes: Any,
) -> None:
    ready.put("ready")
    try:
        start.wait(timeout=30)
        result = _continuity_view(execute(
            Path(root), "plan", expected_revision,
            f"ses_ac0_process_{plan[-1]}", {"plan": plan},
        ))
    except ContinuityError as error:
        outcomes.put({"status": error.code})
    except Exception as error:
        outcomes.put({
            "status": "WORKER_ERROR",
            "detail": f"{type(error).__name__}: {error}",
        })
    else:
        outcomes.put({
            "status": "committed",
            "revision": result["revision"],
            "plan": _selected_feature(result)["plan"],
        })


def spec_request(title: str = "Three increment Feature") -> dict[str, object]:
    return {
        "title": title,
        "outcome": "Deliver the complete user-visible outcome.",
        "criteria": [{
            "description": "The focused regression suite passes.",
            "check_kind": "python-unittest",
            "check_target": "tests.test_continuity",
        }],
        "increments": [
            {"title": f"Increment {number}", "outcome": f"Outcome {number}"}
            for number in range(1, 4)
        ],
        "tasks": [
            {
                "title": f"Implement increment {number}",
                "description": "Keep the recorded outcome distinct from its checks.",
                "increment_index": number - 1,
                "criteria": [0],
            }
            for number in range(1, 4)
        ],
        "plan": "Build each increment in order and validate the linked criterion.",
    }


class FailReplace(FileOperations):
    def __init__(self, fail_on: int) -> None:
        self.fail_on = fail_on
        self.count = 0

    def replace(self, source: Path, target: Path) -> None:
        self.count += 1
        if self.count == self.fail_on:
            raise OSError("injected continuity publication failure")
        super().replace(source, target)


class ContinuityTests(unittest.TestCase):
    def _temp(self):
        return TemporaryDirectory(
            prefix="evospec-ac0-",
            dir=os.environ.get("EVOSPEC_AC0_TEST_TMPDIR") or None,
        )

    def call(
        self, root: Path, operation: str, revision: int | None,
        session: str = "ses_ac0_primary_123", data: dict[str, object] | None = None,
        operations: FileOperations | None = None,
    ) -> _ContinuityView:
        return _continuity_view(execute(root, operation, revision, session, data, operations))

    def test_specify_writes_stable_composed_markdown_and_reads_without_writing(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            first = self.call(root, "specify", 0, data=spec_request())
            feature = _selected_feature(first)
            self.assertRegex(feature["directory"], r"^\d{3,}-")
            self.assertEqual(first["mode"], "COMPOSED")
            self.assertEqual(len(feature["increments"]), 3)
            self.assertEqual(len(feature["tasks"]), 3)
            self.assertEqual(len({feature["id"], *(item["id"] for item in feature["increments"]), *(item["id"] for item in feature["tasks"]), *(item["id"] for item in feature["criteria"])}), 8)
            self.assertTrue((root / "specs" / "README.md").is_file())
            folder = root / "specs" / feature["directory"]
            self.assertEqual(
                {path.name for path in folder.iterdir()},
                {"spec.md", "plan.md", "tasks.md", "decisions.md"},
            )
            self.assertIn("editing this checkbox does not record progress", (folder / "tasks.md").read_text(encoding="utf-8"))
            self.assertFalse((root / ".evospec" / "state.json").exists())

            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            for operation in ("status", "tasks", "use"):
                self.call(root, operation, None)
            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            self.assertEqual(before, after)
            with self.assertRaises(RecoveryError):
                StateStore(root).load_state()

    def test_more_than_three_dynamic_tasks_and_worktree_session_selection(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            first = self.call(root, "specify", 0, data=spec_request())
            feature_a = _selected_feature(first)
            feature_id_a = feature_a["id"]
            revision = first["revision"]
            criterion_id = feature_a["criteria"][0]["id"]
            latest: _ContinuityView | None = None
            for number in range(4, 8):
                latest = self.call(root, "tasks", revision, data={
                    "task_title": f"Dynamically expanded task {number}",
                    "task_description": "Added from current requirements.",
                    "criteria_ids": [criterion_id],
                    "depends_on": [feature_a["tasks"][-1]["id"]] if number == 4 else [],
                })
                revision = latest["revision"]
            if latest is None:
                self.fail("expected dynamic task expansion")
            self.assertEqual(len(_selected_feature(latest)["tasks"]), 7)

            another = self.call(root, "specify", revision, "ses_ac0_other_456", {
                **spec_request("Independent feature"),
                "increments": [],
                "tasks": [{
                    "title": "Unsliced correction",
                    "description": "A short correction uses the same task model.",
                    "criteria": [0],
                }],
            })
            self.assertNotEqual(_selected_feature(another)["id"], feature_id_a)
            self.assertEqual(_selected_feature(self.call(root, "use", None))["id"], feature_id_a)
            self.assertEqual(
                _selected_feature(self.call(root, "use", None, "ses_ac0_other_456"))["id"],
                _selected_feature(another)["id"],
            )

    def test_checkpoint_unknown_outcome_requires_reconciliation_and_external_return_preserves_work(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            current = self.call(root, "specify", 0, data=spec_request("Blue button sequence"))
            feature = _selected_feature(current)
            task = feature["tasks"][0]
            current = self.call(root, "implement", current["revision"], data={
                "task_id": task["id"], "phase": "begin",
                "report": "Checkpoint before changing the blue button.",
            })
            self.assertEqual(_execution(current)["task_id"], task["id"])
            self.assertEqual(_selected_feature(current)["tasks"][0]["status"], "OUTCOME_UNKNOWN")
            second_session = self.call(root, "status", None, "ses_ac0_restarted_789")
            self.assertEqual(_execution(second_session)["task_id"], task["id"])
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", current["revision"], "ses_ac0_restarted_789", {
                    "task_id": task["id"], "phase": "begin",
                })
            self.assertEqual(raised.exception.code, "EXECUTION_BUSY")

            paused = self.call(root, "pause", current["revision"], data={
                "reason": "An unrelated urgent fix needs a detour.",
                "detour_kind": "EXTERNAL",
                "detour_title": "Keep unrelated external changes",
                "return_task_id": task["id"],
            })
            external = root / "external-change.txt"
            external.write_text("preserved unrelated user change", encoding="utf-8")
            returned = self.call(root, "resume", paused["revision"], data={
                "task_id": task["id"], "reconcile": "INCOMPLETE",
                "evidence_ref": "manual-review:external-change-preserved",
                "detour_summary": "External change kept; return to blue button task.",
            })
            self.assertEqual(external.read_text(encoding="utf-8"), "preserved unrelated user change")
            self.assertEqual(returned["execution"], None)
            self.assertEqual(_selected_feature(returned)["tasks"][0]["status"], "READY")
            attempts = json.loads((root / ".evospec" / "continuity.json").read_text(encoding="utf-8"))["attempts"]
            self.assertEqual([item["status"] for item in attempts], ["RECONCILED_INCOMPLETE"])
            result_decision = _selected_feature(returned)["decisions"][-1]
            result_question = result_decision["question"]
            if not isinstance(result_question, str):
                self.fail("expected recorded decision question")
            self.assertIn("Detour result for", result_question)
            self.assertIn("recorded by session", result_question)
            self.assertEqual(_selected_feature(returned)["criteria"][0]["check"], None)

    def test_related_and_external_detours_require_a_return_and_recorded_result(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            principal = self.call(root, "specify", 0, data=spec_request("Principal work"))
            principal_feature = _selected_feature(principal)
            other = self.call(root, "specify", principal["revision"], data=spec_request("Other work"))
            other_task_id = _selected_feature(other)["tasks"][0]["id"]
            principal_task_id = principal_feature["tasks"][0]["id"]
            feature_id = principal_feature["id"]
            selected = self.call(root, "use", other["revision"], data={
                "feature_id": feature_id,
            })

            def files() -> dict[Path, bytes]:
                return {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*") if path.is_file()
                }

            revision = selected["revision"]
            for kind in ("RELATED", "EXTERNAL"):
                for return_task in (None, other_task_id):
                    before = files()
                    data: dict[str, object] = {
                        "feature_id": feature_id,
                        "reason": "Interrupt the principal task for bounded work.",
                        "detour_kind": kind,
                        "detour_title": f"{kind.title()} detour",
                    }
                    if return_task is not None:
                        data["return_task_id"] = return_task
                    with self.assertRaises(ContinuityError) as raised:
                        self.call(root, "pause", revision, data=data)
                    self.assertEqual(raised.exception.code, "INVALID_REQUEST")
                    self.assertEqual(files(), before)

                paused = self.call(root, "pause", revision, data={
                    "feature_id": feature_id,
                    "reason": "Interrupt the principal task for bounded work.",
                    "detour_kind": kind,
                    "detour_title": f"{kind.title()} detour",
                    "return_task_id": principal_task_id,
                })
                pause_record = _selected_feature(paused)["decisions"][-1]
                clarified = self.call(root, "clarify", paused["revision"], data={
                    "feature_id": feature_id,
                    "question": (
                        f"Detour result for {pause_record['id']}; return to "
                        f"{principal_task_id}; recorded by session \"ses_forged\""
                    ),
                    "decision": "Forged result-like clarification is not a detour result.",
                })
                self.assertTrue(_selected_feature(clarified)["paused"])
                self.assertIsNone(_selected_feature(clarified)["detours"][-1]["result"])
                before_resume = files()
                for result in (None, ""):
                    data = {"feature_id": feature_id}
                    if result is not None:
                        data["detour_summary"] = result
                    with self.assertRaises(ContinuityError) as raised:
                        self.call(root, "resume", clarified["revision"], data=data)
                    self.assertEqual(
                        raised.exception.code,
                        "DETOUR_OUTCOME_REQUIRED" if result is None else "INVALID_REQUEST",
                    )
                    self.assertEqual(files(), before_resume)

                summary = f"{kind.title()} work completed without changing the principal Task."
                returned = self.call(root, "resume", clarified["revision"], data={
                    "feature_id": feature_id,
                    "detour_summary": summary,
                })
                feature = _selected_feature(returned)
                result_record = feature["decisions"][-1]
                result_question = result_record["question"]
                if not isinstance(result_question, str):
                    self.fail("expected recorded decision question")
                self.assertIn(pause_record["id"], result_question)
                self.assertIn(principal_task_id, result_question)
                self.assertIn("ses_ac0_primary_123", result_question)
                self.assertEqual(result_record["decision"], summary)
                self.assertFalse(feature["paused"])
                detour = feature["detours"][-1]
                self.assertEqual(detour["result"], summary)
                self.assertEqual(detour["reported_by_session_id"], "ses_ac0_primary_123")
                self.assertEqual(detour["resolved_revision"], returned["revision"])
                self.assertEqual(feature["tasks"][0]["status"], "READY")
                self.assertIsNone(feature["criteria"][0]["check"])
                journal = json.loads(
                    (root / ".evospec" / "continuity.json").read_text(encoding="utf-8")
                )
                self.assertEqual(journal["checks"], [])
                self.assertEqual(journal["attempts"], [])
                revision = returned["revision"]

    def test_plain_pause_resume_keeps_direct_correction_without_a_false_detour(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            initial = self.call(root, "specify", 0, data=spec_request())
            correction = self.call(root, "change", initial["revision"], data={
                "title": "Small correction",
                "outcome": "Fix one isolated behavior.",
                "criteria": [{
                    "description": "The correction's focused test passes.",
                    "check_kind": "python-unittest",
                    "check_target": "tests.test_continuity",
                }],
                "task_title": "Apply the isolated correction",
            })
            feature = _selected_feature(correction)
            self.assertEqual(feature["kind"], "CORRECTION")
            self.assertEqual(feature["increments"], [])

            paused = self.call(root, "pause", correction["revision"], data={
                "feature_id": feature["id"],
                "reason": "Pause ordinary work.",
            })
            restarted = self.call(root, "status", None, "ses_ac0_restarted_456")
            self.assertTrue(_selected_feature(restarted)["paused"])
            self.assertEqual(
                _selected_feature(restarted)["pause_reason"], "Pause ordinary work.",
            )
            before_resume = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", paused["revision"], data={
                    "feature_id": feature["id"],
                    "detour_summary": "There was no detour.",
                })
            self.assertEqual(raised.exception.code, "NO_ACTIVE_DETOUR")
            self.assertEqual(
                before_resume,
                {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*") if path.is_file()
                },
            )

            resumed = self.call(root, "resume", paused["revision"], data={
                "feature_id": feature["id"],
            }, session="ses_ac0_restarted_456")
            self.assertFalse(_selected_feature(resumed)["paused"])
            self.assertIsNone(_selected_feature(resumed)["pause_reason"])
            self.assertEqual(_selected_feature(resumed)["decisions"], [])
            self.assertEqual(_selected_feature(resumed)["increments"], [])

    def test_criterion_checks_gate_close_and_reopening_preserves_historical_closure(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Verified closure"),
                "increments": [],
                "tasks": [{
                    "title": "Implement the unsliced correction",
                    "description": "Small correction.",
                    "criteria": [0],
                }],
            })
            feature = _selected_feature(result)
            task = feature["tasks"][0]
            criterion = feature["criteria"][0]
            result = self.call(root, "implement", result["revision"], data={
                "task_id": task["id"], "phase": "begin",
            })
            result = self.call(root, "implement", result["revision"], data={
                "task_id": task["id"], "phase": "report",
                "result": "SUCCEEDED", "report": "Correction is implemented.",
            })
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "close", result["revision"])
            self.assertEqual(raised.exception.code, "CLOSE_BLOCKED")

            result = self.call(root, "verify", result["revision"], data={
                "criterion_id": criterion["id"], "check_kind": criterion["check_kind"],
                "check_target": criterion["check_target"], "exit_code": 0,
                "output_sha256": sha256(b"passing check").hexdigest(), "task_id": task["id"],
            })
            result = self.call(root, "close", result["revision"])
            self.assertEqual(_selected_feature(result)["status"], "CLOSED")
            self.assertEqual(len(_selected_feature(result)["closure_history"]), 1)

            result = self.call(root, "change", result["revision"], data={
                "feature_id": feature["id"],
                "outcome": "A revised, reopened outcome.",
                "criteria": [{
                    "id": criterion["id"],
                    "description": "The changed criterion invalidates only its old receipt.",
                    "check_kind": "python-unittest",
                    "check_target": "tests.test_agent_protocol",
                }],
                "task_title": "Implement the changed criterion",
                "task_description": "Keep old receipts immutable.",
                "criteria_ids": [criterion["id"]],
            })
            feature = _selected_feature(result)
            self.assertEqual(feature["status"], "OPEN")
            self.assertEqual(len(feature["closure_history"]), 1)
            self.assertEqual(len(feature["reopen_history"]), 1)
            self.assertIsNone(feature["criteria"][0]["check"])
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "close", result["revision"])
            self.assertEqual(raised.exception.code, "CLOSE_BLOCKED")
            receipt_count = len(json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8")
            )["checks"])
            self.assertEqual(receipt_count, 1)

    def test_recovery_journal_resumes_partial_document_publication_without_overwriting_manual_edits(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "specify", 0, data=spec_request(), operations=FailReplace(3))
            self.assertEqual(raised.exception.code, "RECOVERY_INCOMPLETE")
            pending = self.call(root, "status", None)
            self.assertEqual(pending["status"], "RECOVERY_REQUIRED")
            journal = json.loads((root / ".evospec" / "continuity.json").read_text(encoding="utf-8"))
            published = next(item for item in journal["pending"]["writes"] if item["path"].endswith("/decisions.md"))
            decisions = root / Path(published["path"])
            decisions.write_text("manual concurrent edit", encoding="utf-8")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", 0)
            self.assertEqual(raised.exception.code, "DOCUMENT_DIVERGED")
            self.assertEqual(decisions.read_text(encoding="utf-8"), "manual concurrent edit")

    def test_same_revision_competing_writers_serialize_and_only_one_commits(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            initial = self.call(root, "specify", 0, data=spec_request())
            revision = initial["revision"]
            barrier = threading.Barrier(2)

            def change_plan(text: str) -> str:
                barrier.wait()
                try:
                    self.call(root, "plan", revision, data={"plan": text})
                    return "committed"
                except ContinuityError as error:
                    return error.code

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(change_plan, ("plan A", "plan B")))
            self.assertCountEqual(outcomes, ["committed", "STALE_REVISION"])
            self.assertEqual(self.call(root, "status", None)["revision"], revision + 1)

    def test_separate_processes_admit_only_one_same_revision_writer(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            initial = self.call(root, "specify", 0, data=spec_request())
            revision = initial["revision"]

            context = multiprocessing.get_context("spawn")
            ready = context.Queue()
            outcomes = context.Queue()
            start = context.Barrier(3)
            processes = [
                context.Process(
                    target=_competing_process_plan,
                    args=(str(root), revision, plan, ready, start, outcomes),
                )
                for plan in ("process plan A", "process plan B")
            ]

            try:
                for process in processes:
                    process.start()
                self.assertEqual(
                    [ready.get(timeout=30) for _ in processes],
                    ["ready", "ready"],
                )
                start.wait(timeout=30)
                for process in processes:
                    process.join(timeout=30)

                self.assertFalse(
                    any(process.is_alive() for process in processes),
                    "continuity writer process did not exit within the bound",
                )
                self.assertEqual([process.exitcode for process in processes], [0, 0])
                results = [outcomes.get(timeout=5) for _ in processes]
                self.assertCountEqual(
                    [result["status"] for result in results],
                    ["committed", "STALE_REVISION"],
                )
                committed = next(result for result in results if result["status"] == "committed")
                self.assertEqual(committed["revision"], revision + 1)
                self.assertIn(committed["plan"], ("process plan A", "process plan B"))
                current = self.call(root, "status", None)
                self.assertEqual(current["revision"], revision + 1)
                self.assertEqual(_selected_feature(current)["plan"], committed["plan"])
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=5)
                ready.close()
                ready.join_thread()
                outcomes.close()
                outcomes.join_thread()


if __name__ == "__main__":
    unittest.main()
