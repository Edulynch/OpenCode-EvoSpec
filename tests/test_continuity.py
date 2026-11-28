from __future__ import annotations

import json
import multiprocessing
import os
import threading
import unittest
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, NotRequired, TypedDict, TypeGuard
from unittest.mock import patch

from evospec.continuity import ContinuityError, execute
from evospec.continuity_documents import parse_all as parse_all_documents
from evospec.domain import ContinuityStateData
from evospec.input_fingerprint import (
    InputFingerprintError,
    repository_input_snapshot,
)
from evospec.persistence import (
    AtomicFailure,
    ContinuityPersistence,
    FileOperations,
    RecoveryError,
    StateStore,
)
from tests._tempdir import TemporaryDirectory


class _IncrementView(TypedDict):
    id: str
    title: str
    outcome: str


class _TaskView(TypedDict):
    id: str
    title: str
    description: str
    increment_id: str | None
    criteria_ids: list[str]
    depends_on: list[str]
    status: str
    attempts: int
    eligible: bool
    attempt_history: NotRequired[list[dict[str, object]]]
    latest_event: NotRequired[dict[str, object]]


class _CheckView(TypedDict):
    fresh: bool
    passed: bool
    task_id: str | None
    status: NotRequired[str]
    source: NotRequired[str]
    report: NotRequired[str]
    artifact_present: NotRequired[bool]
    artifact_sha256: NotRequired[str]
    method: NotRequired[str]
    descriptor: NotRequired[str]
    evidence_ref: NotRequired[str]
    historical_passed: NotRequired[bool]
    stale_reason: NotRequired[str]
    mandate_id: NotRequired[str]


class _CriterionView(TypedDict):
    id: str
    description: str
    check_kind: str
    check_target: str
    digest: str
    check: _CheckView | None
    evidence_status: NotRequired[str]
    review_decision: NotRequired[dict[str, object]]


class _DecisionView(TypedDict):
    id: str
    question: str
    decision: str


class _ExecutionView(TypedDict):
    task_id: str


class _MandateIncrementView(TypedDict):
    id: str
    task_ids: list[str]


class _FeatureView(TypedDict):
    id: str
    directory: str
    kind: str
    title: str
    outcome: str
    status: str
    cycle: int
    paused: bool
    pause_reason: str | None
    increments: list[_IncrementView]
    tasks: list[_TaskView]
    criteria: list[_CriterionView]
    decisions: list[_DecisionView]
    detours: list[dict[str, object]]
    closure_history: list[dict[str, object]]
    reopen_history: list[dict[str, object]]
    plan: str
    task_events: NotRequired[list[dict[str, object]]]
    mandate: NotRequired[dict[str, object]]
    acceptance: dict[str, object] | None
    acceptance_history: list[dict[str, object]]
    pending_review_decisions: list[dict[str, object]]
    close_readiness: dict[str, object]


class _DocumentConflictView(TypedDict):
    feature_id: str
    path: str
    detail: str


class _DocumentChangesView(TypedDict):
    semantic: list[str]
    format_notes: list[str]
    generated: list[str]
    identity_ref_conflicts: list[_DocumentConflictView]


class _PublicationConflictView(TypedDict):
    path: str
    current: str
    intended: str
    current_truncated: bool
    intended_truncated: bool


class _PublicationRecoveryView(TypedDict):
    token: str
    conflict_paths: list[str]
    conflicts: list[_PublicationConflictView]
    omitted_conflicts: int


class _ContinuityView(TypedDict):
    revision: int
    mode: str | None
    status: str | None
    selected_feature: _FeatureView | None
    execution: _ExecutionView | None
    input_snapshot_error: str | None
    selected_feature_id: NotRequired[str | None]
    selection_scope: NotRequired[str]
    features: NotRequired[list[_FeatureView]]
    document_changes: NotRequired[_DocumentChangesView]
    publication_recovery: NotRequired[_PublicationRecoveryView]
    mandate: NotRequired[dict[str, object]]
    active_attempt: NotRequired[dict[str, object] | None]
    execution_feature_id: NotRequired[str | None]
    mandate_history: NotRequired[list[dict[str, object]]]


def _record(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _text(value: object) -> TypeGuard[str]:
    return isinstance(value, str)


def _optional_text(value: object) -> TypeGuard[str | None]:
    return value is None or isinstance(value, str)


def _text_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _mandate_increment_view(value: object) -> TypeGuard[_MandateIncrementView]:
    return (
        _record(value)
        and _text(value.get("id"))
        and _text_list(value.get("task_ids"))
    )


def _mandate_increment_views(value: object) -> TypeGuard[list[_MandateIncrementView]]:
    return isinstance(value, list) and all(_mandate_increment_view(item) for item in value)


def _digest_map(value: object) -> TypeGuard[dict[str, str | None]]:
    return _record(value) and all(_optional_text(item) for item in value.values())


def _check_view(value: object) -> TypeGuard[_CheckView]:
    if not _record(value):
        return False
    fresh = value.get("fresh")
    passed = value.get("passed")
    task_id = value.get("task_id")
    historical_passed = value.get("historical_passed")
    stale_reason = value.get("stale_reason")
    return (
        isinstance(fresh, bool)
        and isinstance(passed, bool)
        and _optional_text(task_id)
        and (
            "historical_passed" not in value
            or isinstance(historical_passed, bool)
        )
        and ("stale_reason" not in value or isinstance(stale_reason, str))
    )


def _criterion_check(criterion: _CriterionView) -> _CheckView:
    check = criterion.get("check")
    if not _check_view(check):
        raise AssertionError("expected a typed criterion check")
    return check


def _increment_view(value: object) -> TypeGuard[_IncrementView]:
    return (
        _record(value)
        and _text(value.get("id"))
        and _text(value.get("title"))
        and _text(value.get("outcome"))
    )


def _task_view(value: object) -> TypeGuard[_TaskView]:
    return (
        _record(value)
        and _text(value.get("id"))
        and _text(value.get("title"))
        and _text(value.get("description"))
        and _optional_text(value.get("increment_id"))
        and _text_list(value.get("criteria_ids"))
        and _text_list(value.get("depends_on"))
        and _text(value.get("status"))
        and type(value.get("attempts")) is int
        and isinstance(value.get("eligible"), bool)
    )


def _criterion_view(value: object) -> TypeGuard[_CriterionView]:
    if not _record(value):
        return False
    check = value.get("check")
    return (
        _text(value.get("id"))
        and _text(value.get("description"))
        and _text(value.get("check_kind"))
        and _text(value.get("check_target"))
        and _text(value.get("digest"))
        and (check is None or _check_view(check))
    )


def _decision_view(value: object) -> TypeGuard[_DecisionView]:
    return (
        _record(value)
        and _text(value.get("id"))
        and _text(value.get("question"))
        and _text(value.get("decision"))
    )


def _execution_view(value: object) -> TypeGuard[_ExecutionView]:
    return _record(value) and _text(value.get("task_id"))


def _record_views(value: object) -> TypeGuard[list[dict[str, object]]]:
    return isinstance(value, list) and all(_record(item) for item in value)


def _task_views(value: object) -> TypeGuard[list[_TaskView]]:
    return isinstance(value, list) and all(_task_view(item) for item in value)


def _increment_views(value: object) -> TypeGuard[list[_IncrementView]]:
    return isinstance(value, list) and all(_increment_view(item) for item in value)


def _criterion_views(value: object) -> TypeGuard[list[_CriterionView]]:
    return isinstance(value, list) and all(_criterion_view(item) for item in value)


def _decision_views(value: object) -> TypeGuard[list[_DecisionView]]:
    return isinstance(value, list) and all(_decision_view(item) for item in value)


def _feature_view(value: object) -> TypeGuard[_FeatureView]:
    if not _record(value):
        return False
    return (
        _text(value.get("id"))
        and _text(value.get("directory"))
        and _text(value.get("kind"))
        and _text(value.get("title"))
        and _text(value.get("outcome"))
        and _text(value.get("status"))
        and type(value.get("cycle")) is int
        and isinstance(value.get("paused"), bool)
        and _optional_text(value.get("pause_reason"))
        and _increment_views(value.get("increments"))
        and _task_views(value.get("tasks"))
        and _criterion_views(value.get("criteria"))
        and _decision_views(value.get("decisions"))
        and _record_views(value.get("detours"))
        and _record_views(value.get("closure_history"))
        and _record_views(value.get("reopen_history"))
        and "acceptance" in value
        and (
            value.get("acceptance") is None
            or _record(value.get("acceptance"))
        )
        and _record_views(value.get("acceptance_history"))
        and _record_views(value.get("pending_review_decisions"))
        and _record(value.get("close_readiness"))
        and _text(value.get("plan"))
    )


def _feature_views(value: object) -> TypeGuard[list[_FeatureView]]:
    return isinstance(value, list) and all(_feature_view(item) for item in value)


def _document_conflict_view(value: object) -> TypeGuard[_DocumentConflictView]:
    return (
        _record(value)
        and _text(value.get("feature_id"))
        and _text(value.get("path"))
        and _text(value.get("detail"))
    )


def _document_changes_view(value: object) -> TypeGuard[_DocumentChangesView]:
    if not _record(value):
        return False
    conflicts = value.get("identity_ref_conflicts")
    return (
        _text_list(value.get("semantic"))
        and _text_list(value.get("format_notes"))
        and _text_list(value.get("generated"))
        and isinstance(conflicts, list)
        and all(_document_conflict_view(item) for item in conflicts)
    )


def _publication_conflict_view(value: object) -> TypeGuard[_PublicationConflictView]:
    return (
        _record(value)
        and _text(value.get("path"))
        and _text(value.get("current"))
        and _text(value.get("intended"))
        and isinstance(value.get("current_truncated"), bool)
        and isinstance(value.get("intended_truncated"), bool)
    )


def _publication_recovery_view(value: object) -> TypeGuard[_PublicationRecoveryView]:
    if not _record(value):
        return False
    conflicts = value.get("conflicts")
    omitted = value.get("omitted_conflicts")
    return (
        _text(value.get("token"))
        and _text_list(value.get("conflict_paths"))
        and isinstance(conflicts, list)
        and all(_publication_conflict_view(item) for item in conflicts)
        and type(omitted) is int
    )


def _continuity_view(value: object) -> _ContinuityView:
    if not _record(value):
        raise AssertionError("expected continuity response object")
    revision = value.get("revision")
    mode = value.get("mode")
    status = value.get("status")
    selected_feature = value.get("selected_feature")
    execution = value.get("execution")
    input_snapshot_error = value.get("input_snapshot_error")
    selected_feature_id = value.get("selected_feature_id")
    selection_scope = value.get("selection_scope")
    features = value.get("features")
    document_changes = value.get("document_changes")
    publication_recovery = value.get("publication_recovery")
    mandate = value.get("mandate")
    active_attempt = value.get("active_attempt")
    execution_feature_id = value.get("execution_feature_id")
    mandate_history = value.get("mandate_history")
    if type(revision) is not int:
        raise AssertionError("expected integer continuity revision")
    if (
        not _optional_text(mode) or not _optional_text(status)
        or not _optional_text(input_snapshot_error)
    ):
        raise AssertionError("expected optional text continuity fields")
    if selected_feature is not None and not _feature_view(selected_feature):
        raise AssertionError("expected selected feature view")
    if execution is not None and not _execution_view(execution):
        raise AssertionError("expected execution view")
    result: _ContinuityView = {
        "revision": revision,
        "mode": mode,
        "status": status,
        "selected_feature": selected_feature,
        "execution": execution,
        "input_snapshot_error": input_snapshot_error,
    }
    if "selected_feature_id" in value:
        if not _optional_text(selected_feature_id):
            raise AssertionError("expected optional selected Feature ID")
        result["selected_feature_id"] = selected_feature_id
    if "selection_scope" in value:
        if not _text(selection_scope):
            raise AssertionError("expected selection scope")
        result["selection_scope"] = selection_scope
    if "features" in value:
        if not _feature_views(features):
            raise AssertionError("expected Feature views")
        result["features"] = features
    if "document_changes" in value:
        if not _document_changes_view(document_changes):
            raise AssertionError("expected document change view")
        result["document_changes"] = document_changes
    if "publication_recovery" in value:
        if not _publication_recovery_view(publication_recovery):
            raise AssertionError("expected a typed publication recovery projection")
        result["publication_recovery"] = publication_recovery
    if "mandate" in value:
        if not _record(mandate):
            raise AssertionError("expected a typed implementation mandate projection")
        result["mandate"] = mandate
    if "active_attempt" in value:
        if active_attempt is not None and not _record(active_attempt):
            raise AssertionError("expected an optional active Task attempt")
        result["active_attempt"] = active_attempt
    if "execution_feature_id" in value:
        if not _optional_text(execution_feature_id):
            raise AssertionError("expected an optional unresolved execution owner")
        result["execution_feature_id"] = execution_feature_id
    if "mandate_history" in value:
        if not _record_views(mandate_history):
            raise AssertionError("expected attributable mandate history")
        result["mandate_history"] = mandate_history
    return result


def _document_changes(result: _ContinuityView) -> _DocumentChangesView:
    changes = result.get("document_changes")
    if changes is None:
        raise AssertionError("expected continuity document diagnostics")
    return changes


def _publication_recovery(result: _ContinuityView) -> _PublicationRecoveryView:
    recovery = result.get("publication_recovery")
    if recovery is None:
        raise AssertionError("expected pending-publication recovery details")
    return recovery


def _features(result: _ContinuityView) -> list[_FeatureView]:
    features = result.get("features")
    if features is None:
        raise AssertionError("expected continuity Feature list")
    return features


def _stale_reason(check: _CheckView) -> str:
    reason = check.get("stale_reason")
    if reason is None:
        raise AssertionError("expected a stale-check reason")
    return reason


def _historical_passed(check: _CheckView) -> bool:
    passed = check.get("historical_passed")
    if passed is None:
        raise AssertionError("expected historical check status")
    return passed


def _selected_feature(result: _ContinuityView) -> _FeatureView:
    feature = result["selected_feature"]
    if feature is None:
        raise AssertionError("expected selected feature")
    return feature


def _mandate(result: _ContinuityView) -> dict[str, object]:
    value = result.get("mandate")
    if value is None:
        raise AssertionError("expected a composed implementation mandate")
    return value


def _mandate_id(result: _ContinuityView) -> str:
    mandate_id = _mandate(result).get("id")
    if not _text(mandate_id):
        raise AssertionError("expected a composed implementation mandate ID")
    return mandate_id


def _mandate_increments(result: _ContinuityView) -> list[_MandateIncrementView]:
    increments = _mandate(result).get("increments")
    if not _mandate_increment_views(increments):
        raise AssertionError("expected typed mandate increments")
    return increments


def _stable_feature_ids(feature: _FeatureView) -> tuple[str, ...]:
    return (
        feature["id"],
        *(item["id"] for item in feature["criteria"]),
        *(item["id"] for item in feature["increments"]),
        *(item["id"] for item in feature["tasks"]),
        *(item["id"] for item in feature["decisions"]),
    )


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


def _rewrite_as_legacy_fixture(root: Path) -> ContinuityStateData:
    store = ContinuityPersistence(root)
    root = store.root
    state = store.load()
    if state is None:
        raise AssertionError("expected initialized continuity state")
    state_path = root / ".evospec" / "continuity.json"
    documents = state["documents"]
    for feature_id, feature_state in state["features"].items():
        parsed = parse_all_documents(root, state, feature_id)
        directory = feature_state["directory"]
        values = {
            "spec": parsed["spec"],
            "plan": parsed["plan"],
            "tasks": {"feature_id": feature_id, "tasks": parsed["tasks"]},
            "decisions": {
                "feature_id": feature_id, "decisions": parsed["decisions"],
            },
        }
        for name, value in values.items():
            relative_path = PurePosixPath("specs", directory, f"{name}.md")
            relative = relative_path.as_posix()
            if relative not in documents:
                continue
            body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            content = (
                f"# Legacy {name}\n\n"
                f"<!-- evospec-feature: {feature_id} -->\n\n"
                "<!-- evospec-data:start -->\n"
                "```json\n"
                f"{body}\n"
                "```\n"
                "<!-- evospec-data:end -->\n"
            ).encode("utf-8")
            root.joinpath(*relative_path.parts).write_bytes(content)
            documents[relative] = sha256(content).hexdigest()
    state.pop("semantic_digests", None)
    state.pop("identity_metadata", None)
    state.pop("review_decisions", None)
    state.pop("acceptances", None)
    for closure in state["closures"]:
        for key in ("acceptance_id", "basis_revision", "mandate_id"):
            closure.pop(key, None)
    for reopen in state["reopens"]:
        for key in ("reason", "revision"):
            reopen.pop(key, None)
    for check in state["checks"]:
        check.pop("task_digest", None)
        for key in (
            "feature_id", "feature_cycle", "purpose", "evidence_status",
            "source", "evidence_ref", "input_snapshot_before", "input_snapshot_after",
        ):
            check.pop(key, None)
    for attempt in state["attempts"]:
        for key in (
            "feature_id", "feature_cycle", "task_digest", "started_revision",
            "reported_revision", "reported_by_session_id", "reported_input_scope",
        ):
            attempt.pop(key, None)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return state


class FailReplace(FileOperations):
    def __init__(self, fail_on: int) -> None:
        self.fail_on = fail_on
        self.count = 0

    def replace(self, source: Path, target: Path) -> None:
        self.count += 1
        if self.count == self.fail_on:
            raise OSError("injected continuity publication failure")
        super().replace(source, target)


class EditOnJournalReplace(FileOperations):
    def __init__(self, path: Path, content: str) -> None:
        self.path = path
        self.content = content
        self.edited = False

    def replace(self, source: Path, target: Path) -> None:
        if not self.edited and target.name == "continuity.json":
            self.path.write_text(self.content, encoding="utf-8")
            self.edited = True
        super().replace(source, target)


class TrackReplacements(FileOperations):
    def __init__(self) -> None:
        self.targets: list[Path] = []

    def replace(self, source: Path, target: Path) -> None:
        self.targets.append(target)
        super().replace(source, target)


class ContinuityTests(unittest.TestCase):
    def _temp(self):
        return TemporaryDirectory(
            prefix="evospec-ac0-",
            dir=os.environ.get("EVOSPEC_AC0_TEST_TMPDIR") or None,
        )

    def call(
        self, root: Path, operation: str, revision: int | None,
        session: str = "ses_ac0_primary_123", data: Mapping[str, object] | None = None,
        operations: FileOperations | None = None,
    ) -> _ContinuityView:
        payload = None if data is None else dict(data)
        return _continuity_view(
            execute(root, operation, revision, session, payload, operations),
        )

    def input_snapshot(
        self, root: Path, revision: int,
        session: str = "ses_ac0_primary_123",
    ) -> dict[str, object]:
        result = execute(root, "input_snapshot", revision, session)
        if not _record(result):
            self.fail("expected a Core input snapshot response")
        snapshot = result.get("input_snapshot")
        if not _record(snapshot):
            self.fail("expected a Core input snapshot")
        policy_id = snapshot.get("policy_id")
        policy_digest = snapshot.get("policy_digest")
        fingerprint = snapshot.get("fingerprint")
        if not _text(policy_id):
            self.fail("expected a Core input snapshot policy ID")
        if not _text(policy_digest):
            self.fail("expected a Core input snapshot policy digest")
        if not _text(fingerprint):
            self.fail("expected a Core input snapshot fingerprint")
        if set(snapshot) != {"policy_id", "policy_digest", "fingerprint"}:
            self.fail("expected the exact Core input snapshot fields")
        return {
            "policy_id": policy_id,
            "policy_digest": policy_digest,
            "fingerprint": fingerprint,
        }

    def record_check(
        self, root: Path, revision: int, task: _TaskView,
        criterion: _CriterionView, *, mandate_id: str | None = None,
        session: str = "ses_ac0_primary_123", feature_id: str | None = None,
    ) -> _ContinuityView:
        before = self.input_snapshot(root, revision, session)
        after = self.input_snapshot(root, revision, session)
        self.assertEqual(before, after)
        data: dict[str, object] = {
            "criterion_id": criterion["id"],
            "check_kind": criterion["check_kind"],
            "check_target": criterion["check_target"],
            "exit_code": 0,
            "output_sha256": sha256(b"passing check").hexdigest(),
            "task_id": task["id"],
            "input_snapshot_before": before,
            "input_snapshot_after": after,
        }
        if feature_id is not None:
            data["feature_id"] = feature_id
        if mandate_id is not None:
            data["mandate_id"] = mandate_id
        return self.call(
            root, "verify", revision, session=session, data=data,
        )

    def report_success(
        self, root: Path, result: _ContinuityView, task: _TaskView,
    ) -> _ContinuityView:
        started = self.call(root, "implement", result["revision"], data={
            "task_id": task["id"], "phase": "begin",
        })
        return self.call(root, "implement", started["revision"], data={
            "task_id": task["id"], "phase": "report",
            "result": "SUCCEEDED", "report": "Implementation is complete.",
        })

    def accept_feature(
        self, root: Path, result: _ContinuityView, *,
        mandate_id: str | None = None,
        session: str = "ses_ac0_primary_123",
    ) -> _ContinuityView:
        feature = _selected_feature(result)
        data: dict[str, object] = {
            "feature_id": feature["id"],
            "reason": "The current attributable evidence and completed work are accepted.",
        }
        if mandate_id is not None:
            data["mandate_id"] = mandate_id
        return self.call(root, "accept", result["revision"], session=session, data=data)

    def close_accepted(
        self, root: Path, accepted: _ContinuityView, *,
        mandate_id: str | None = None,
        session: str = "ses_ac0_primary_123",
    ) -> _ContinuityView:
        feature = _selected_feature(accepted)
        acceptance = feature.get("acceptance")
        if not _record(acceptance) or not _text(acceptance.get("id")):
            self.fail("expected the current explicit Feature acceptance")
        data: dict[str, object] = {
            "feature_id": feature["id"], "acceptance_id": acceptance["id"],
        }
        if mandate_id is not None:
            data["mandate_id"] = mandate_id
        return self.call(root, "close", accepted["revision"], session=session, data=data)

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
            for path in folder.glob("*.md"):
                content = path.read_bytes()
                self.assertNotIn(b"<!-- evospec-data:start -->", content)
                self.assertNotIn(b"<!-- evospec-data:end -->", content)
            self.assertEqual(
                {path.name for path in folder.iterdir()},
                {"spec.md", "plan.md", "tasks.md"},
            )
            self.assertIn("checkboxes do not record progress", (folder / "tasks.md").read_text(encoding="utf-8"))
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

    def test_manual_filtering_edit_is_read_only_then_reconciles_and_reverifies(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            request = {
                **spec_request("Filter records"),
                "increments": [],
                "tasks": [{
                    "title": "Implement the unfiltered list",
                    "description": "Render all records.",
                    "criteria": [0],
                }],
                "plan": "",
            }
            initial = self.call(root, "specify", 0, data=request)
            feature = _selected_feature(initial)
            old_task = feature["tasks"][0]
            criterion = feature["criteria"][0]
            completed = self.report_success(root, initial, old_task)
            completed = self.record_check(
                root, completed["revision"], old_task, criterion,
            )

            folder = root / "specs" / feature["directory"]
            spec_path = folder / "spec.md"
            tasks_path = folder / "tasks.md"
            spec = spec_path.read_text(encoding="utf-8")
            spec = spec.replace(
                "Deliver the complete user-visible outcome.",
                "Show only records matching the requested filter.",
            ).replace(
                "The focused regression suite passes.",
                "Only matching records remain visible.",
            )
            spec_path.write_text(spec, encoding="utf-8")
            tasks = tasks_path.read_text(encoding="utf-8")
            start = tasks.index(f"### `{old_task['id']}` — Implement the unfiltered list")
            end = tasks.index("<!-- evospec-generated:start -->", start)
            definition = (
                "### `T-filter-task` — Implement filtered search\n"
                "- Increment: `none`\n"
                f"- Criteria: `{criterion['id']}`\n"
                "- Depends on: none\n"
                "- Description:\n"
                "  Filter records before rendering the result list.\n\n"
            )
            tasks_path.write_text(
                tasks[:start] + definition + tasks[end:],
                encoding="utf-8",
            )

            before_status = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            proposed = self.call(root, "status", None)
            after_status = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            self.assertEqual(before_status, after_status)
            self.assertEqual(proposed["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
            changes = _document_changes(proposed)
            self.assertEqual(
                set(changes["semantic"]),
                {
                    f"specs/{feature['directory']}/spec.md",
                    f"specs/{feature['directory']}/tasks.md",
                },
            )
            self.assertEqual(changes["identity_ref_conflicts"], [])
            self.assertEqual(_features(proposed), [])

            reconciled = self.call(root, "reconcile", proposed["revision"], data={
                "feature_id": feature["id"],
                "retire_ids": [old_task["id"]],
            })
            adopted = _selected_feature(reconciled)
            new_task = adopted["tasks"][0]
            self.assertRegex(new_task["id"], r"^T-[0-9a-f]{32}$")
            self.assertEqual(new_task["criteria_ids"], [criterion["id"]])
            self.assertEqual(new_task["title"], "Implement filtered search")
            stale = _criterion_check(adopted["criteria"][0])
            self.assertFalse(stale["fresh"])
            self.assertEqual(_stale_reason(stale), "criterion_changed")
            self.assertEqual(new_task["status"], "READY")

            started = self.call(root, "implement", reconciled["revision"], data={
                "task_id": new_task["id"], "phase": "begin",
            })
            reported = self.call(root, "implement", started["revision"], data={
                "task_id": new_task["id"], "phase": "report",
                "result": "SUCCEEDED", "report": "Filtering is implemented.",
            })
            verified = self.record_check(
                root, reported["revision"], new_task, adopted["criteria"][0],
            )
            self.assertEqual(_selected_feature(verified)["tasks"][0]["status"], "COMPLETED")
            accepted = self.accept_feature(root, verified)
            closed = self.close_accepted(root, accepted)
            self.assertEqual(_selected_feature(closed)["status"], "CLOSED")
            self.assertEqual(len(_selected_feature(closed)["closure_history"]), 1)

    def test_direct_plan_edit_is_read_only_until_reconcile(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            initial = self.call(root, "specify", 0, data={
                **spec_request("Hand-edited plan"),
                "increments": [],
                "tasks": [{"title": "Build the first path", "criteria": [0]}],
                "plan": "Build the first path before polishing it.",
            })
            feature = _selected_feature(initial)
            plan_path = root / "specs" / feature["directory"] / "plan.md"
            authored = plan_path.read_text(encoding="utf-8").replace(
                "Build the first path before polishing it.",
                "Build the first path, then add the filter.",
            )
            plan_path.write_text(authored, encoding="utf-8")
            before_status = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }

            proposed = self.call(root, "status", None)

            self.assertEqual(proposed["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
            self.assertEqual(proposed["revision"], initial["revision"])
            self.assertEqual(
                _document_changes(proposed)["semantic"],
                [f"specs/{feature['directory']}/plan.md"],
            )
            self.assertEqual(before_status, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            })

            reconciled = self.call(root, "reconcile", proposed["revision"], data={
                "feature_id": feature["id"],
            })

            self.assertEqual(_selected_feature(reconciled)["plan"], "Build the first path, then add the filter.")
            self.assertEqual(plan_path.read_text(encoding="utf-8"), authored)
            self.assertEqual(
                _document_changes(self.call(root, "status", None))["semantic"],
                [],
            )

    def test_generated_index_tracks_worktree_default_and_lifecycle(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            first = self.call(root, "specify", 0, data={
                **spec_request("First indexed Feature"),
                "increments": [],
                "tasks": [{"title": "Complete first Feature", "criteria": [0]}],
                "plan": "",
            })
            first_feature = _selected_feature(first)
            readme = root / "specs" / "README.md"

            def index_entry(feature_id: str) -> str:
                matches = [
                    line for line in readme.read_text(encoding="utf-8").splitlines()
                    if f"`{feature_id}`" in line
                ]
                self.assertEqual(len(matches), 1)
                return matches[0]

            first_entry = index_entry(first_feature["id"])
            self.assertIn(" — OPEN — worktree default", first_entry)
            first_link = first_entry.split("](", 1)[1].split(")", 1)[0]
            self.assertTrue((root / "specs" / first_link).is_file())

            before_note = "## Human overview\n\nKeep this introductory text.\n"
            after_note = "\nHuman appendix remains unchanged.\n"
            text = readme.read_text(encoding="utf-8")
            text = text.replace(
                "<!-- evospec-index:start -->",
                before_note + "<!-- evospec-index:start -->",
                1,
            ).replace(
                "<!-- evospec-index:end -->",
                "<!-- evospec-index:end -->" + after_note,
                1,
            )
            readme.write_text(text, encoding="utf-8")
            reconcile_operations = TrackReplacements()
            reconciled = self.call(root, "reconcile", first["revision"], data={
                "feature_id": first_feature["id"],
            }, operations=reconcile_operations)
            self.assertNotIn(readme.resolve(), {
                path.resolve() for path in reconcile_operations.targets
            })
            self.assertIn(before_note.strip(), readme.read_text(encoding="utf-8"))
            self.assertIn(after_note.strip(), readme.read_text(encoding="utf-8"))

            second = self.call(root, "specify", reconciled["revision"], data={
                **spec_request("Second indexed Feature"),
                "increments": [],
                "tasks": [{"title": "Complete second Feature", "criteria": [0]}],
                "plan": "",
            })
            second_feature = _selected_feature(second)
            self.assertIn(" — OPEN", index_entry(first_feature["id"]))
            self.assertNotIn("worktree default", index_entry(first_feature["id"]))
            self.assertIn(" — OPEN — worktree default", index_entry(second_feature["id"]))
            second_link = index_entry(second_feature["id"]).split("](", 1)[1].split(")", 1)[0]
            self.assertTrue((root / "specs" / second_link).is_file())

            selected_default = self.call(root, "use", second["revision"], data={
                "feature_id": first_feature["id"], "scope": "worktree",
            })
            session_status = self.call(root, "status", None)
            self.assertEqual(session_status.get("selected_feature_id"), second_feature["id"])
            self.assertEqual(session_status.get("selection_scope"), "session")
            self.assertIn(" — OPEN — worktree default", index_entry(first_feature["id"]))
            self.assertNotIn("worktree default", index_entry(second_feature["id"]))

            no_index_rewrite = TrackReplacements()
            same_default = self.call(root, "use", selected_default["revision"], data={
                "feature_id": first_feature["id"], "scope": "worktree",
            }, operations=no_index_rewrite)
            self.assertNotIn(readme.resolve(), {
                path.resolve() for path in no_index_rewrite.targets
            })
            session_selected = self.call(root, "use", same_default["revision"], data={
                "feature_id": first_feature["id"], "scope": "session",
            })
            reported = self.report_success(
                root, session_selected, first_feature["tasks"][0],
            )
            verified = self.record_check(
                root, reported["revision"],
                first_feature["tasks"][0], first_feature["criteria"][0],
            )
            accepted = self.accept_feature(root, verified)
            acceptance = _selected_feature(accepted).get("acceptance")
            if not _record(acceptance) or not _text(acceptance.get("id")):
                self.fail("expected an explicit acceptance before closure")
            close_operations = TrackReplacements()
            closed = self.call(
                root, "close", accepted["revision"], data={
                    "feature_id": first_feature["id"],
                    "acceptance_id": acceptance["id"],
                }, operations=close_operations,
            )
            self.assertIn(readme.resolve(), {
                path.resolve() for path in close_operations.targets
            })
            self.assertIn(" — CLOSED — worktree default", index_entry(first_feature["id"]))
            self.assertIn(before_note.strip(), readme.read_text(encoding="utf-8"))
            self.assertIn(after_note.strip(), readme.read_text(encoding="utf-8"))
            self.assertFalse(
                (root / "specs" / first_feature["directory"] / "plan.md").exists(),
            )
            self.assertFalse(
                (root / "specs" / first_feature["directory"] / "decisions.md").exists(),
            )
            self.assertEqual(_selected_feature(closed)["status"], "CLOSED")

    def test_direct_addition_of_pending_criterion_and_task_preserves_completed_work(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            initial = self.call(root, "specify", 0, data={
                "title": "Create and filter records",
                "outcome": "Create a usable record list and let users filter it.",
                "criteria": [
                    {
                        "description": "The records page is created and loads.",
                        "check_kind": "python-unittest",
                        "check_target": "tests.test_continuity",
                    },
                    {
                        "description": "All existing records are shown.",
                        "check_kind": "python-unittest",
                        "check_target": "tests.test_continuity",
                    },
                ],
                "increments": [],
                "tasks": [
                    {
                        "title": "Create the records page",
                        "description": "Create the page and load its records.",
                        "criteria": [0],
                    },
                    {
                        "title": "Render all records",
                        "description": "Show every loaded record.",
                        "criteria": [1],
                    },
                ],
                "plan": "",
            })
            feature = _selected_feature(initial)
            old_feature_id = feature["id"]
            old_criteria = list(feature["criteria"])
            old_tasks = list(feature["tasks"])
            first = self.report_success(root, initial, old_tasks[0])
            first = self.record_check(
                root, first["revision"], old_tasks[0], old_criteria[0],
            )
            self.assertEqual(
                _criterion_check(_selected_feature(first)["criteria"][0])["fresh"],
                True,
            )

            folder = root / "specs" / feature["directory"]
            spec_path = folder / "spec.md"
            tasks_path = folder / "tasks.md"
            spec = spec_path.read_text(encoding="utf-8")
            criterion_definition = (
                "- `C-filter` — Only records matching the requested filter are shown.\n"
                "  - Check: `python-unittest`\n"
                "  - Target: `tests.test_continuity`\n"
            )
            marker = "\n\n## Increments (optional Slices)"
            self.assertIn(marker, spec)
            spec_path.write_text(
                spec.replace(marker, f"\n\n{criterion_definition}\n{marker[2:]}", 1),
                encoding="utf-8",
            )
            tasks = tasks_path.read_text(encoding="utf-8")
            definition = (
                "### `T-filter` — Implement record filtering\n"
                "- Increment: `none`\n"
                "- Criteria: `C-filter`\n"
                "- Depends on: none\n"
                "- Description:\n"
                "  Filter the loaded records before rendering them.\n\n"
            )
            generated = "<!-- evospec-generated:start -->"
            before_generated, after_generated = tasks.split(generated, 1)
            tasks_path.write_text(
                before_generated.rstrip("\r\n") + "\n\n" + definition
                + generated + after_generated,
                encoding="utf-8",
            )
            before_status = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            revision_before_status = first["revision"]

            proposed = self.call(root, "status", None)

            self.assertEqual(proposed["revision"], revision_before_status)
            self.assertEqual(proposed["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
            self.assertEqual(
                set(_document_changes(proposed)["semantic"]),
                {
                    f"specs/{feature['directory']}/spec.md",
                    f"specs/{feature['directory']}/tasks.md",
                },
            )
            self.assertEqual(before_status, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            })

            reconciled = self.call(root, "reconcile", proposed["revision"], data={
                "feature_id": old_feature_id,
            })
            adopted = _selected_feature(reconciled)
            current_criteria = adopted["criteria"]
            current_tasks = adopted["tasks"]
            new_criterion = next(
                item for item in current_criteria
                if item["description"] == "Only records matching the requested filter are shown."
            )
            new_task = next(
                item for item in current_tasks
                if item["title"] == "Implement record filtering"
            )
            self.assertEqual(adopted["id"], old_feature_id)
            self.assertTrue({item["id"] for item in old_criteria} <= {
                item["id"] for item in current_criteria
            })
            self.assertTrue({item["id"] for item in old_tasks} <= {
                item["id"] for item in current_tasks
            })
            self.assertRegex(new_criterion["id"], r"^C-[0-9a-f]{32}$")
            self.assertRegex(new_task["id"], r"^T-[0-9a-f]{32}$")
            self.assertEqual(new_task["criteria_ids"], [new_criterion["id"]])
            self.assertEqual(new_task["status"], "READY")
            self.assertTrue(_criterion_check(current_criteria[0])["fresh"])
            self.assertIsNone(current_criteria[2].get("check"))
            self.assertEqual(
                {item["id"] for item in current_tasks},
                {item["id"] for item in old_tasks} | {new_task["id"]},
            )

            second = self.report_success(
                root, reconciled, next(item for item in current_tasks if item["title"] == "Render all records"),
            )
            second = self.record_check(
                root, second["revision"],
                next(item for item in _selected_feature(second)["tasks"] if item["title"] == "Render all records"),
                next(item for item in _selected_feature(second)["criteria"] if item["description"] == "All existing records are shown."),
            )
            third = self.report_success(
                root, second, next(item for item in _selected_feature(second)["tasks"] if item["id"] == new_task["id"]),
            )
            completed = self.record_check(
                root, third["revision"],
                next(item for item in _selected_feature(third)["tasks"] if item["id"] == new_task["id"]),
                next(item for item in _selected_feature(third)["criteria"] if item["id"] == new_criterion["id"]),
            )
            complete_feature = _selected_feature(completed)
            self.assertTrue(all(item["status"] == "COMPLETED" for item in complete_feature["tasks"]))
            self.assertTrue(all(
                _criterion_check(item)["fresh"] and _criterion_check(item)["passed"]
                for item in complete_feature["criteria"]
            ))
            accepted = self.accept_feature(root, completed)
            closed = self.close_accepted(root, accepted)
            self.assertEqual(_selected_feature(closed)["status"], "CLOSED")
            self.assertEqual(len(_selected_feature(closed)["closure_history"]), 1)

    def test_format_notes_and_generated_checkbox_are_separate_from_semantics(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            initial = self.call(root, "specify", 0, data={
                **spec_request("Notes and checkbox"),
                "increments": [],
                "tasks": [{"title": "One task", "criteria": [0]}],
                "plan": "",
            })
            feature = _selected_feature(initial)
            folder = root / "specs" / feature["directory"]
            spec_path = folder / "spec.md"
            tasks_path = folder / "tasks.md"
            spec = spec_path.read_text(encoding="utf-8")
            spec_path.write_text(
                spec.replace(
                    "<!-- evospec-notes:end -->",
                    "Author note: keep the filtering example small.\n<!-- evospec-notes:end -->",
                ),
                encoding="utf-8",
            )
            original_files = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            note_status = self.call(root, "status", None)
            self.assertEqual(original_files, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            })
            self.assertEqual(_document_changes(note_status)["semantic"], [])
            self.assertEqual(
                _document_changes(note_status)["format_notes"],
                [f"specs/{feature['directory']}/spec.md"],
            )
            self.assertEqual(_document_changes(note_status)["generated"], [])
            noted = self.call(root, "reconcile", note_status["revision"], data={
                "feature_id": feature["id"],
            })
            self.assertIn(
                "Author note: keep the filtering example small.",
                spec_path.read_text(encoding="utf-8"),
            )

            task_id = _selected_feature(noted)["tasks"][0]["id"]
            tasks = tasks_path.read_text(encoding="utf-8")
            tasks_path.write_text(
                tasks.replace(f"- [ ] `{task_id}`", f"- [x] `{task_id}`"),
                encoding="utf-8",
            )
            before_checkbox_status = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            checkbox_status = self.call(root, "status", None)
            self.assertEqual(before_checkbox_status, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            })
            self.assertEqual(_document_changes(checkbox_status)["semantic"], [])
            self.assertEqual(_document_changes(checkbox_status)["format_notes"], [])
            self.assertEqual(
                _document_changes(checkbox_status)["generated"],
                [f"specs/{feature['directory']}/tasks.md"],
            )
            self.assertEqual(
                _selected_feature(checkbox_status)["tasks"][0]["status"],
                "READY",
            )
            refreshed = self.call(root, "reconcile", checkbox_status["revision"], data={
                "feature_id": feature["id"],
            })
            self.assertEqual(_selected_feature(refreshed)["tasks"][0]["status"], "READY")
            self.assertIn(f"- [ ] `{task_id}`", tasks_path.read_text(encoding="utf-8"))
            self.assertEqual(
                _document_changes(self.call(root, "status", None))["generated"],
                [],
            )

    def test_status_and_reconcile_fail_closed_on_unknown_duplicate_or_dangling_ids(self) -> None:
        for invalid_edit in ("unknown-stable-id", "duplicate-id", "dangling-reference"):
            with self.subTest(invalid_edit=invalid_edit), self._temp() as temporary:
                root = Path(temporary)
                initial = self.call(root, "specify", 0, data={
                    **spec_request("Identity conflict"),
                    "increments": [],
                    "tasks": [{"title": "One task", "criteria": [0]}],
                    "plan": "",
                })
                feature = _selected_feature(initial)
                folder = root / "specs" / feature["directory"]
                spec_path = folder / "spec.md"
                tasks_path = folder / "tasks.md"
                criterion_id = feature["criteria"][0]["id"]
                if invalid_edit == "unknown-stable-id":
                    replacement = "C-" + "0" * 32
                    spec_path.write_text(
                        spec_path.read_text(encoding="utf-8").replace(
                            criterion_id, replacement,
                        ),
                        encoding="utf-8",
                    )
                    tasks_path.write_text(
                        tasks_path.read_text(encoding="utf-8").replace(
                            criterion_id, replacement,
                        ),
                        encoding="utf-8",
                    )
                elif invalid_edit == "duplicate-id":
                    content = spec_path.read_text(encoding="utf-8")
                    start = content.index("## Acceptance criteria\n\n") + len(
                        "## Acceptance criteria\n\n",
                    )
                    end = content.index("\n\n## Increments", start)
                    block = content[start:end]
                    spec_path.write_text(
                        content[:end] + "\n\n" + block + content[end:],
                        encoding="utf-8",
                    )
                else:
                    tasks_path.write_text(
                        tasks_path.read_text(encoding="utf-8").replace(
                            criterion_id, "C-" + "0" * 32,
                        ),
                        encoding="utf-8",
                    )
                before = {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*") if path.is_file()
                }
                projected = self.call(root, "status", None)
                self.assertEqual(
                    projected["status"], "DOCUMENT_CONFLICT",
                )
                self.assertTrue(_document_changes(projected)["identity_ref_conflicts"])
                self.assertEqual(_features(projected), [])
                self.assertEqual(before, {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*") if path.is_file()
                })
                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "reconcile", initial["revision"], data={
                        "feature_id": feature["id"],
                    })
                self.assertIn(
                    raised.exception.code,
                    ("IDENTITY_CONFLICT", "DOCUMENT_DIVERGED"),
                )

    def test_closed_feature_reconcile_requires_exact_explicit_reopen(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            initial = self.call(root, "specify", 0, data={
                **spec_request("Explicit reopen"),
                "increments": [],
                "tasks": [{"title": "Implement current behavior", "criteria": [0]}],
                "plan": "",
            })
            feature = _selected_feature(initial)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            reported = self.report_success(root, initial, task)
            verified = self.record_check(
                root, reported["revision"], task, criterion,
            )
            accepted = self.accept_feature(root, verified)
            closed = self.close_accepted(root, accepted)
            folder = root / "specs" / feature["directory"]
            spec_path = folder / "spec.md"
            spec_path.write_text(
                spec_path.read_text(encoding="utf-8").replace(
                    "The focused regression suite passes.",
                    "The revised behavior passes its focused regression.",
                ),
                encoding="utf-8",
            )
            proposed = self.call(root, "status", None)
            self.assertEqual(proposed["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            for reopen_feature_id in ("F-" + "0" * 32, None):
                data: dict[str, object] = {"feature_id": feature["id"]}
                if reopen_feature_id is not None:
                    data["reopen_feature_id"] = reopen_feature_id
                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "reconcile", closed["revision"], data=data)
                self.assertEqual(raised.exception.code, "REOPEN_INTENT_REQUIRED")
                self.assertEqual(before, {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*") if path.is_file()
                })
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "reconcile", closed["revision"], data={
                    "feature_id": feature["id"],
                    "reopen_feature_id": feature["id"],
                })
            self.assertEqual(raised.exception.code, "INVALID_REQUEST")
            self.assertEqual(before, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            })
            reopened = self.call(root, "reconcile", closed["revision"], data={
                "feature_id": feature["id"],
                "reopen_feature_id": feature["id"],
                "reopen_reason": "The revised semantic document needs a new cycle.",
            })
            current = _selected_feature(reopened)
            self.assertEqual(current["status"], "OPEN")
            reopened_state = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(reopened_state["features"][feature["id"]]["cycle"], 2)
            self.assertEqual(len(current["closure_history"]), 1)
            self.assertEqual(len(current["reopen_history"]), 1)
            self.assertEqual(
                current["reopen_history"][0]["reason"],
                "The revised semantic document needs a new cycle.",
            )
            self.assertEqual(
                _stale_reason(_criterion_check(current["criteria"][0])),
                "criterion_changed",
            )
            self.assertIn(
                f"`{feature['id']}` — OPEN — worktree default",
                (root / "specs" / "README.md").read_text(encoding="utf-8"),
            )

    def test_closed_feature_rejects_semantic_writes_until_explicit_change(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            initial = self.call(root, "specify", 0, data={
                **spec_request("Closed semantic writes"),
                "increments": [],
                "tasks": [{"title": "Complete current behavior", "criteria": [0]}],
                "plan": "",
            })
            feature = _selected_feature(initial)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            reported = self.report_success(root, initial, task)
            verified = self.record_check(
                root, reported["revision"], task, criterion,
            )
            accepted = self.accept_feature(root, verified)
            closed = self.close_accepted(root, accepted)
            folder = root / "specs" / feature["directory"]
            before_files = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            closed_state = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            attempts = (
                ("clarify", {"question": "Should this change?", "decision": "Not yet."}),
                ("plan", {"plan": "Add the unapproved next step."}),
                ("tasks", {
                    "task_title": "Add an incomplete task",
                    "task_description": "This must not be published while closed.",
                    "criteria_ids": [criterion["id"]],
                }),
                ("change", {
                    "feature_id": feature["id"],
                    "task_title": "Implicitly reopen",
                    "task_description": "A semantic change is not reopen intent.",
                    "criteria_ids": [criterion["id"]],
                }),
                ("implement", {"task_id": task["id"], "phase": "begin"}),
                ("verify", {}),
                ("pause", {
                    "reason": "This detour must not append history while closed.",
                    "detour_kind": "RELATED",
                    "detour_title": "Inspect a related issue",
                    "return_task_id": task["id"],
                }),
            )
            for operation, data in attempts:
                with self.subTest(operation=operation), self.assertRaises(
                    ContinuityError,
                ) as raised:
                    self.call(root, operation, closed["revision"], data=data)
                self.assertEqual(raised.exception.code, "REOPEN_INTENT_REQUIRED")
                self.assertIn("reopen", str(raised.exception))
                self.assertEqual(before_files, {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*") if path.is_file()
                })
                current_state = json.loads(
                    (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
                )
                self.assertEqual(current_state, closed_state)

            self.assertFalse((folder / "plan.md").exists())
            self.assertFalse((folder / "decisions.md").exists())
            for operation, revision, data in (
                ("status", None, None),
                ("tasks", None, None),
                ("implement", closed["revision"], {"phase": "queue"}),
            ):
                query = self.call(root, operation, revision, data=data)
                self.assertEqual(_selected_feature(query)["status"], "CLOSED")
            self.assertEqual(before_files, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            })
            closed_feature = _selected_feature(closed)
            self.assertEqual(len(closed_feature["tasks"]), 1)
            self.assertEqual(closed_feature["tasks"][0]["status"], "COMPLETED")
            self.assertEqual(len(closed_feature["closure_history"]), 1)
            self.assertEqual(closed_feature["reopen_history"], [])

            reopened = self.call(root, "reopen", closed["revision"], data={
                "feature_id": feature["id"],
                "reason": "A new follow-up Task is explicitly authorized.",
            })
            changed = self.call(root, "change", reopened["revision"], data={
                "feature_id": feature["id"],
                "outcome": "An explicitly reopened follow-up outcome.",
                "task_title": "Implement the follow-up",
                "task_description": "Continue in a new recorded cycle.",
                "criteria_ids": [criterion["id"]],
            })
            reopened_feature = _selected_feature(changed)
            self.assertEqual(reopened_feature["status"], "OPEN")
            self.assertEqual(reopened_feature["cycle"], 2)
            self.assertEqual(len(reopened_feature["closure_history"]), 1)
            self.assertEqual(len(reopened_feature["reopen_history"]), 1)
            self.assertEqual(len(reopened_feature["tasks"]), 2)
            self.assertEqual(reopened_feature["tasks"][1]["status"], "READY")
            self.assertIn(
                f"`{feature['id']}` — OPEN — worktree default",
                (root / "specs" / "README.md").read_text(encoding="utf-8"),
            )

    def test_schema_one_legacy_fixture_upgrade_preserves_history_and_checkpoint(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            first = self.call(root, "specify", 0, data={
                **spec_request("Closed legacy Feature"),
                "increments": [],
                "tasks": [{"title": "Complete first Feature", "criteria": [0]}],
                "plan": "",
            })
            feature = _selected_feature(first)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            reported = self.report_success(root, first, task)
            verified = self.record_check(
                root, reported["revision"], task, criterion,
            )
            accepted = self.accept_feature(root, verified)
            closed = self.close_accepted(root, accepted)
            second = self.call(root, "specify", closed["revision"], data={
                **spec_request("Checkpointed legacy Feature"),
                "increments": [],
                "tasks": [{"title": "Continue second Feature", "criteria": [0]}],
                "plan": "",
            })
            second_feature = _selected_feature(second)
            checkpointed = self.call(root, "implement", second["revision"], data={
                "task_id": second_feature["tasks"][0]["id"],
                "phase": "begin",
                "report": "Preserve this checkpoint through the fixture upgrade.",
            })
            self.assertEqual(
                _execution(checkpointed)["task_id"],
                second_feature["tasks"][0]["id"],
            )
            legacy = _rewrite_as_legacy_fixture(root)
            legacy_checks = json.loads(json.dumps(legacy["checks"]))
            legacy_attempts = json.loads(json.dumps(legacy["attempts"]))
            legacy_execution = json.loads(json.dumps(legacy["execution"]))
            legacy_closures = json.loads(json.dumps(legacy["closures"]))
            revision = legacy["revision"]

            before_blocked_write = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "plan", revision, data={"plan": "not a legacy write"})
            self.assertEqual(raised.exception.code, "UPGRADE_REQUIRED")
            self.assertEqual(before_blocked_write, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            })

            upgraded = _continuity_view(execute(
                root, "upgrade_markdown", revision, "ses_ac0_primary_123",
            ))
            self.assertEqual(upgraded["revision"], revision + 1)
            saved = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(saved["checks"], legacy_checks)
            self.assertEqual(saved["attempts"], legacy_attempts)
            self.assertEqual(saved["execution"], legacy_execution)
            self.assertEqual(saved["closures"], legacy_closures)
            upgraded_features = _features(upgraded)
            first_after = next(
                item for item in upgraded_features
                if item["id"] == feature["id"]
            )
            second_after = next(
                item for item in upgraded_features
                if item["id"] == second_feature["id"]
            )
            self.assertEqual(first_after["status"], "CLOSED")
            self.assertEqual(len(first_after["closure_history"]), 1)
            self.assertEqual(second_after["tasks"][0]["status"], "OUTCOME_UNKNOWN")
            legacy_check = _criterion_check(first_after["criteria"][0])
            self.assertFalse(legacy_check["fresh"])
            self.assertTrue(_historical_passed(legacy_check))
            self.assertEqual(_stale_reason(legacy_check), "task_changed")
            for path in (root / "specs").glob("*/*.md"):
                self.assertNotIn(b"<!-- evospec-data:start -->", path.read_bytes())

    def test_legacy_upgrade_refuses_dirty_or_mixed_markdown_sources(self) -> None:
        for source_kind in ("dirty", "mixed"):
            with self.subTest(source_kind=source_kind), self._temp() as temporary:
                root = ContinuityPersistence(Path(temporary)).root
                initial = self.call(root, "specify", 0, data={
                    **spec_request("Legacy safety"),
                    "increments": [],
                    "tasks": [{"title": "One task", "criteria": [0]}],
                    "plan": "",
                })
                feature = _selected_feature(initial)
                legacy = _rewrite_as_legacy_fixture(root)
                spec_path = root / "specs" / feature["directory"] / "spec.md"
                if source_kind == "dirty":
                    spec_path.write_text(
                        spec_path.read_text(encoding="utf-8") + "\nConcurrent note.\n",
                        encoding="utf-8",
                    )
                else:
                    from evospec.continuity_documents import render_document

                    parsed = parse_all_documents(root, legacy, feature["id"])
                    spec = parsed.get("spec")
                    if not _record(spec):
                        raise AssertionError("expected parsed legacy Feature document")
                    content = render_document(
                        "spec", spec, legacy, feature["id"], spec,
                    ).encode("utf-8")
                    spec_path.write_bytes(content)
                    relative = PurePosixPath(
                        "specs", feature["directory"], "spec.md",
                    ).as_posix()
                    legacy["documents"][relative] = sha256(content).hexdigest()
                    (root / ".evospec" / "continuity.json").write_text(
                        json.dumps(
                            legacy, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"),
                        ),
                        encoding="utf-8",
                    )
                before = {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*") if path.is_file()
                }
                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "upgrade_markdown", initial["revision"])
                self.assertEqual(
                    raised.exception.code,
                    "DOCUMENT_DIVERGED" if source_kind == "dirty" else "UPGRADE_UNSAFE",
                )
                self.assertEqual(before, {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*") if path.is_file()
                })

    def test_reconcile_read_set_race_stops_before_replacing_any_document(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            first = self.call(root, "specify", 0, data={
                **spec_request("Selected Feature"),
                "increments": [],
                "tasks": [{"title": "Selected Task", "criteria": [0]}],
                "plan": "",
            })
            first_feature = _selected_feature(first)
            second = self.call(root, "specify", first["revision"], data={
                **spec_request("Concurrent Feature"),
                "increments": [],
                "tasks": [{"title": "Other Task", "criteria": [0]}],
                "plan": "",
            })
            selected_spec = root / "specs" / first_feature["directory"] / "spec.md"
            selected_original = selected_spec.read_text(encoding="utf-8")
            selected_spec.write_text(
                selected_original.replace(
                    "Deliver the complete user-visible outcome.",
                    "Adopt this human-authored outcome.",
                ),
                encoding="utf-8",
            )
            other_feature = _selected_feature(second)
            other_spec = root / "specs" / other_feature["directory"] / "spec.md"
            raced_content = "Concurrent editor update; preserve this text.\n"
            other_original = other_spec.read_bytes()
            operations = EditOnJournalReplace(selected_spec, raced_content)
            before_revision = second["revision"]
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "reconcile", before_revision, data={
                    "feature_id": first_feature["id"],
                }, operations=operations)
            self.assertEqual(raised.exception.code, "DOCUMENT_DIVERGED")
            self.assertTrue(operations.edited)
            self.assertEqual(selected_spec.read_text(encoding="utf-8"), raced_content)
            self.assertEqual(other_spec.read_bytes(), other_original)
            state = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(state["revision"], before_revision)
            self.assertIsNotNone(state["pending"])

    def test_reconcile_ignores_unrelated_feature_drift_in_both_edit_orders(self) -> None:
        for unrelated_first in (True, False):
            with self.subTest(unrelated_first=unrelated_first), self._temp() as temporary:
                root = ContinuityPersistence(Path(temporary)).root
                first = self.call(root, "specify", 0, data={
                    **spec_request("Selected Feature"),
                    "increments": [],
                    "tasks": [{"title": "Selected Task", "criteria": [0]}],
                    "plan": "",
                })
                selected = _selected_feature(first)
                second = self.call(root, "specify", first["revision"], data={
                    **spec_request("Unrelated Feature"),
                    "increments": [],
                    "tasks": [{"title": "Unrelated Task", "criteria": [0]}],
                    "plan": "",
                })
                unrelated = _selected_feature(second)
                selected_ids = _stable_feature_ids(selected)
                unrelated_ids = _stable_feature_ids(unrelated)
                selected_spec = root / "specs" / selected["directory"] / "spec.md"
                unrelated_spec = root / "specs" / unrelated["directory"] / "spec.md"
                readme = root / "specs" / "README.md"
                selected_original = selected_spec.read_text(encoding="utf-8")
                unrelated_original = unrelated_spec.read_text(encoding="utf-8")
                index_original = readme.read_text(encoding="utf-8")
                selected_row = next(
                    line for line in index_original.splitlines()
                    if f"`{selected['id']}`" in line
                )
                unrelated_row = next(
                    line for line in index_original.splitlines()
                    if f"`{unrelated['id']}`" in line
                )

                def edit_selected() -> None:
                    selected_spec.write_text(
                        selected_original.replace(
                            f"# {selected['title']}",
                            "# Selected Feature with a human title",
                        ).replace(
                            "Deliver the complete user-visible outcome.",
                            "Adopt only this selected Feature's human outcome.",
                        ),
                        encoding="utf-8",
                    )

                def edit_unrelated() -> None:
                    unrelated_spec.write_text(
                        unrelated_original.replace(
                            f"# {unrelated['title']}",
                            "# Unrelated human title",
                        ),
                        encoding="utf-8",
                    )
                    readme.write_text(
                        index_original.replace(
                            unrelated_row,
                            unrelated_row.replace(
                                f"[{unrelated['title']}]",
                                "[Unrelated human title]",
                            ),
                        ),
                        encoding="utf-8",
                    )

                if unrelated_first:
                    edit_unrelated()
                    edit_selected()
                else:
                    edit_selected()
                    edit_unrelated()
                unrelated_bytes = unrelated_spec.read_bytes()
                unrelated_row_bytes = next(
                    line.encode("utf-8")
                    for line in readme.read_text(encoding="utf-8").splitlines()
                    if f"`{unrelated['id']}`" in line
                )

                proposed = self.call(root, "status", None)
                self.assertEqual(proposed["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
                reconciled = self.call(root, "reconcile", proposed["revision"], data={
                    "feature_id": selected["id"],
                })
                self.assertEqual(reconciled["revision"], proposed["revision"] + 1)
                self.assertEqual(
                    reconciled["status"], "DOCUMENT_RECONCILIATION_REQUIRED",
                    "a remaining semantic drift intentionally withholds every Feature projection",
                )
                self.assertIsNone(reconciled["selected_feature"])
                self.assertEqual(_features(reconciled), [])
                selected_after_first = selected_spec.read_text(encoding="utf-8")
                self.assertIn("# Selected Feature with a human title", selected_after_first)
                self.assertIn("Adopt only this selected Feature's human outcome.", selected_after_first)
                accepted_state = ContinuityPersistence(root).load()
                if accepted_state is None:
                    self.fail("expected first reconciliation revision to be committed")
                selected_path = f"specs/{selected['directory']}/spec.md"
                parsed_selected = parse_all_documents(
                    root, accepted_state, selected["id"],
                )
                parsed_semantic_digests = parsed_selected.get("semantic_digests")
                accepted_semantic_digests = accepted_state.get("semantic_digests")
                if (
                    not _digest_map(parsed_semantic_digests)
                    or not _digest_map(accepted_semantic_digests)
                ):
                    self.fail("expected typed semantic digests for the accepted Feature")
                self.assertEqual(
                    accepted_state["documents"][selected_path],
                    sha256(selected_spec.read_bytes()).hexdigest(),
                )
                self.assertEqual(
                    accepted_semantic_digests[selected_path],
                    parsed_semantic_digests[selected_path],
                )
                self.assertEqual(unrelated_spec.read_bytes(), unrelated_bytes)
                self.assertEqual(
                    next(
                        line.encode("utf-8")
                        for line in readme.read_text(encoding="utf-8").splitlines()
                        if f"`{unrelated['id']}`" in line
                    ),
                    unrelated_row_bytes,
                )
                after_rows = readme.read_text(encoding="utf-8").splitlines()
                self.assertEqual(
                    [line for line in after_rows if f"`{unrelated['id']}`" in line],
                    [unrelated_row.replace(
                        f"[{unrelated['title']}]",
                        "[Unrelated human title]",
                    )],
                )
                after_status = self.call(root, "status", None)
                self.assertEqual(
                    after_status["status"], "DOCUMENT_RECONCILIATION_REQUIRED",
                )
                self.assertEqual(
                    _document_changes(after_status)["semantic"],
                    [f"specs/{unrelated['directory']}/spec.md"],
                )
                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "plan", reconciled["revision"], data={
                        "plan": "ordinary writes remain blocked",
                    })
                self.assertEqual(raised.exception.code, "DOCUMENT_DIVERGED")
                self.assertEqual(unrelated_spec.read_bytes(), unrelated_bytes)
                self.assertEqual(
                    next(
                        line.encode("utf-8")
                        for line in readme.read_text(encoding="utf-8").splitlines()
                        if f"`{unrelated['id']}`" in line
                    ),
                    unrelated_row_bytes,
                )

                second_proposed = self.call(root, "status", None)
                second_reconciled = self.call(
                    root, "reconcile", second_proposed["revision"], data={
                        "feature_id": unrelated["id"],
                    },
                )
                self.assertEqual(
                    second_reconciled["revision"], reconciled["revision"] + 1,
                )
                final_features = {
                    item["id"]: item for item in _features(second_reconciled)
                }
                selected_final = final_features[selected["id"]]
                unrelated_final = final_features[unrelated["id"]]
                self.assertEqual(
                    selected_final["title"], "Selected Feature with a human title",
                )
                self.assertEqual(
                    selected_final["outcome"],
                    "Adopt only this selected Feature's human outcome.",
                )
                self.assertEqual(unrelated_final["title"], "Unrelated human title")
                self.assertEqual(_stable_feature_ids(selected_final), selected_ids)
                self.assertEqual(_stable_feature_ids(unrelated_final), unrelated_ids)
                final_rows = readme.read_text(encoding="utf-8").splitlines()
                self.assertIn(
                    f"- [Unrelated human title]({unrelated['directory']}/spec.md) — "
                    f"`{unrelated['id']}` — OPEN — worktree default",
                    final_rows,
                )

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

    def test_checkpoint_report_and_resume_are_state_only_under_document_drift(self) -> None:
        for drift_kind, reconciliation in (
            ("semantic", "COMPLETE"),
            ("notes", "INCOMPLETE"),
        ):
            with self.subTest(drift_kind=drift_kind), self._temp() as temporary:
                root = Path(temporary)
                created = self.call(root, "specify", 0, data={
                    **spec_request("Drifted checkpoint"),
                    "increments": [],
                    "tasks": [{"title": "Resolve the checkpoint", "criteria": [0]}],
                    "plan": "",
                })
                feature = _selected_feature(created)
                task = feature["tasks"][0]
                started = self.call(root, "implement", created["revision"], data={
                    "task_id": task["id"], "phase": "begin",
                    "report": "Checkpoint before any Task effects.",
                })
                spec_path = root / "specs" / feature["directory"] / "spec.md"
                original = spec_path.read_text(encoding="utf-8")
                if drift_kind == "semantic":
                    changed = original.replace(
                        "Deliver the complete user-visible outcome.",
                        "A human is revising this outcome.",
                    )
                else:
                    changed = original.replace(
                        "<!-- evospec-notes:end -->",
                        "Human note retained during recovery.\n<!-- evospec-notes:end -->",
                    )
                spec_path.write_text(changed, encoding="utf-8")
                markdown_before = {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*.md")
                }
                status = self.call(root, "status", None)
                if drift_kind == "semantic":
                    self.assertEqual(status["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
                else:
                    self.assertIn(
                        f"specs/{feature['directory']}/spec.md",
                        _document_changes(status)["format_notes"],
                    )
                self.assertEqual(_execution(status)["task_id"], task["id"])

                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "reconcile", status["revision"], data={
                        "feature_id": feature["id"],
                    })
                self.assertEqual(raised.exception.code, "EXECUTION_BUSY")
                self.assertEqual(markdown_before, {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*.md")
                })

                for expected, session, task_id, code in (
                    (started["revision"] - 1, "ses_ac0_primary_123", task["id"], "STALE_REVISION"),
                    (started["revision"], "ses_ac0_other_123", task["id"], "OUTCOME_UNKNOWN"),
                    (started["revision"], "ses_ac0_primary_123", "T-" + "f" * 32, "NO_ACTIVE_ATTEMPT"),
                ):
                    with self.assertRaises(ContinuityError) as raised:
                        self.call(root, "implement", expected, session, {
                            "task_id": task_id, "phase": "report",
                            "result": "UNKNOWN", "report": "Do not repeat the attempt.",
                        })
                    self.assertEqual(raised.exception.code, code)

                unknown = self.call(root, "implement", started["revision"], data={
                    "task_id": task["id"], "phase": "report",
                    "result": "UNKNOWN", "report": "The effect outcome remains unknown.",
                })
                unknown_state = json.loads(
                    (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
                )
                self.assertEqual(unknown_state["revision"], unknown["revision"])
                self.assertEqual(unknown_state["attempts"][0]["status"], "OUTCOME_UNKNOWN")
                self.assertEqual(unknown_state["execution"]["task_id"], task["id"])
                self.assertEqual(markdown_before, {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*.md")
                })

                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "resume", unknown["revision"] - 1, data={
                        "task_id": task["id"], "reconcile": reconciliation,
                        "evidence_ref": "manual-review:stale",
                    })
                self.assertEqual(raised.exception.code, "STALE_REVISION")
                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "resume", unknown["revision"], data={
                        "task_id": "T-" + "e" * 32,
                        "reconcile": reconciliation,
                        "evidence_ref": "manual-review:wrong-task",
                    })
                self.assertEqual(raised.exception.code, "TASK_MISMATCH")

                resolved = self.call(root, "resume", unknown["revision"], data={
                    "task_id": task["id"], "reconcile": reconciliation,
                    "evidence_ref": f"manual-review:{drift_kind}-checkpoint",
                })
                resolved_state = json.loads(
                    (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
                )
                self.assertIsNone(resolved_state["execution"])
                self.assertEqual(len(resolved_state["attempts"]), 1)
                self.assertEqual(
                    resolved_state["attempts"][0]["status"],
                    "RECONCILED_" + reconciliation,
                )
                self.assertEqual(
                    resolved_state["attempts"][0]["evidence_ref"],
                    f"manual-review:{drift_kind}-checkpoint",
                )
                self.assertEqual(resolved_state["checks"], [])
                self.assertEqual(
                    resolved_state["documents"], unknown_state["documents"],
                    "state-only recovery must not adopt or rewrite drifted document digests",
                )
                self.assertEqual(markdown_before, {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*.md")
                })
                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "plan", resolved["revision"], data={
                        "plan": "ordinary writes stay blocked until reconcile",
                    })
                self.assertEqual(raised.exception.code, "DOCUMENT_DIVERGED")

                reconciled = self.call(root, "reconcile", resolved["revision"], data={
                    "feature_id": feature["id"],
                })
                adopted = _selected_feature(reconciled)
                self.assertEqual(
                    adopted["tasks"][0]["status"],
                    "REPORTED" if reconciliation == "COMPLETE" else "READY",
                    "an outcome report or reconciliation is not a passing criterion check",
                )
                if reconciliation == "INCOMPLETE":
                    continued = self.call(root, "implement", reconciled["revision"], data={
                        "task_id": task["id"], "phase": "begin",
                    })
                    state_after_continue = json.loads(
                        (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
                    )
                    self.assertEqual(state_after_continue["attempts"][-1]["attempt"], 2)
                    self.assertEqual(_execution(continued)["task_id"], task["id"])

    def test_checkpoint_drift_reconcile_preserves_pending_detour_until_explicit_result(self) -> None:
        with self._temp() as temporary:
            root = ContinuityPersistence(Path(temporary)).root
            session_a = "ses_ac0_primary_123"
            session_b = "ses_ac0_restarted_789"
            created = self.call(root, "specify", 0, session_a, {
                **spec_request("Checkpoint with a pending detour"),
                "increments": [],
                "tasks": [{"title": "Resume the checkpoint", "criteria": [0]}],
                "plan": "",
            })
            feature = _selected_feature(created)
            feature_id = feature["id"]
            task_id = feature["tasks"][0]["id"]

            started = self.call(root, "implement", created["revision"], session_a, {
                "task_id": task_id, "phase": "begin",
                "report": "Checkpoint before the related detour.",
            })
            paused = self.call(root, "pause", started["revision"], session_a, {
                "reason": "Inspect a related dependency before continuing.",
                "detour_kind": "RELATED",
                "detour_title": "Inspect the dependency",
                "return_task_id": task_id,
            })
            marker = root / ".evospec" / "continuity.json"
            state_before = json.loads(marker.read_text(encoding="utf-8"))
            checkpoint_attempt = state_before["attempts"][0]
            pending_detour = state_before["detours"][0]
            detour_decision = next(
                item for item in _selected_feature(paused)["decisions"]
                if item["id"] == pending_detour["id"]
            )
            ids_before = set(state_before["ids"])
            self.assertEqual(paused["revision"], 3)
            self.assertEqual(checkpoint_attempt["status"], "STARTED")
            self.assertEqual(checkpoint_attempt["checkpoint"], "Checkpoint before the related detour.")
            self.assertIsNone(pending_detour["result"])
            self.assertEqual(pending_detour["return_task_id"], task_id)
            self.assertEqual(state_before["paused_features"], [feature_id])

            spec_path = root / "specs" / feature["directory"] / "spec.md"
            original_spec = spec_path.read_text(encoding="utf-8")
            human_spec = original_spec.replace(
                "Deliver the complete user-visible outcome.",
                "Deliver the outcome with a human-authored refinement.",
            )
            self.assertNotEqual(human_spec, original_spec)
            spec_path.write_text(human_spec, encoding="utf-8")
            human_spec_bytes = spec_path.read_bytes()
            markdown_before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*.md")
            }

            # Session B observes the same revision, but must not combine the
            # state-only checkpoint reconciliation with the detour result.
            status_b = self.call(root, "status", None, session_b)
            self.assertEqual(status_b["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
            self.assertEqual(status_b["revision"], paused["revision"])
            marker_before_reconcile = marker.read_bytes()
            checkpoint_reconcile = {
                "task_id": task_id,
                "reconcile": "INCOMPLETE",
                "evidence_ref": "manual-review:checkpoint-outcome",
            }
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", status_b["revision"], session_b, {
                    **checkpoint_reconcile,
                    "detour_summary": "Do not record a detour result in state-only recovery.",
                })
            self.assertEqual(raised.exception.code, "DOCUMENT_DIVERGED")
            self.assertEqual(marker.read_bytes(), marker_before_reconcile)
            self.assertEqual(markdown_before, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*.md")
            })

            # State-only reconciliation resolves only the checkpoint. It must
            # leave the human edit and the paused detour byte/state-identical.
            state_only_operations = TrackReplacements()
            state_only = self.call(
                root, "resume", status_b["revision"], session_b,
                checkpoint_reconcile, operations=state_only_operations,
            )
            self.assertEqual(state_only["revision"], paused["revision"] + 1)
            self.assertFalse(any(path.suffix == ".md" for path in state_only_operations.targets))
            state_after_checkpoint = json.loads(marker.read_text(encoding="utf-8"))
            reconciled_attempt = state_after_checkpoint["attempts"][0]
            self.assertIsNone(state_after_checkpoint["execution"])
            self.assertEqual(len(state_after_checkpoint["attempts"]), 1)
            self.assertEqual(reconciled_attempt["task_id"], task_id)
            self.assertEqual(reconciled_attempt["attempt"], checkpoint_attempt["attempt"])
            self.assertEqual(reconciled_attempt["session_id"], session_a)
            self.assertEqual(reconciled_attempt["checkpoint"], checkpoint_attempt["checkpoint"])
            self.assertEqual(reconciled_attempt["status"], "RECONCILED_INCOMPLETE")
            self.assertEqual(reconciled_attempt["evidence_ref"], checkpoint_reconcile["evidence_ref"])
            self.assertEqual(state_after_checkpoint["detours"], state_before["detours"])
            self.assertEqual(state_after_checkpoint["paused_features"], [feature_id])
            self.assertEqual(state_after_checkpoint["ids"], state_before["ids"])
            self.assertEqual(state_after_checkpoint["checks"], [])
            self.assertEqual(state_after_checkpoint["documents"], state_before["documents"])
            self.assertEqual(markdown_before, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*.md")
            })

            # Document adoption is a separate explicit revision and still
            # cannot unpause the Feature or consume the pending detour.
            reconciled = self.call(root, "reconcile", state_only["revision"], session_b, {
                "feature_id": feature_id,
            })
            self.assertEqual(reconciled["revision"], state_only["revision"] + 1)
            adopted_feature = _selected_feature(reconciled)
            self.assertEqual(adopted_feature["outcome"], "Deliver the outcome with a human-authored refinement.")
            self.assertTrue(adopted_feature["paused"])
            self.assertEqual(adopted_feature["detours"], state_before["detours"])
            self.assertEqual(adopted_feature["tasks"][0]["id"], task_id)
            self.assertIsNone(adopted_feature["criteria"][0]["check"])
            state_after_documents = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(state_after_documents["attempts"], state_after_checkpoint["attempts"])
            self.assertEqual(state_after_documents["detours"], state_before["detours"])
            self.assertEqual(state_after_documents["paused_features"], [feature_id])
            self.assertEqual(state_after_documents["checks"], [])
            self.assertEqual(set(state_after_documents["ids"]), ids_before)
            self.assertEqual(spec_path.read_bytes(), human_spec_bytes)

            marker_before_blocked_resume = marker.read_bytes()
            with self.assertRaises(ContinuityError) as raised:
                self.call(
                    root, "resume", reconciled["revision"], session_b,
                    checkpoint_reconcile,
                )
            self.assertEqual(raised.exception.code, "DETOUR_OUTCOME_REQUIRED")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", reconciled["revision"], session_b, {
                    "task_id": task_id, "phase": "begin",
                })
            self.assertEqual(raised.exception.code, "FEATURE_PAUSED")
            self.assertEqual(marker.read_bytes(), marker_before_blocked_resume)
            self.assertEqual(spec_path.read_bytes(), human_spec_bytes)

            detour_evidence = (
                "Dependency inspection confirmed compatibility "
                "(evidence: manual-review:dependency-check-17)."
            )
            returned = self.call(root, "resume", reconciled["revision"], session_b, {
                "detour_summary": detour_evidence,
            })
            returned_feature = _selected_feature(returned)
            self.assertEqual(returned["revision"], reconciled["revision"] + 1)
            self.assertFalse(returned_feature["paused"])
            self.assertEqual(returned_feature["tasks"][0]["id"], task_id)
            self.assertEqual(returned_feature["detours"][0]["id"], pending_detour["id"])
            self.assertEqual(returned_feature["detours"][0]["return_task_id"], task_id)
            self.assertEqual(returned_feature["detours"][0]["result"], detour_evidence)
            self.assertIn(detour_decision, returned_feature["decisions"])
            self.assertEqual(
                returned_feature["detours"][0]["reported_by_session_id"], session_b,
            )
            self.assertEqual(
                returned_feature["detours"][0]["resolved_revision"], returned["revision"],
            )
            self.assertIsNone(returned_feature["criteria"][0]["check"])
            state_after_detour = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(state_after_detour["attempts"], state_after_documents["attempts"])
            self.assertEqual(state_after_detour["checks"], [])
            self.assertEqual(state_after_detour["paused_features"], [])
            self.assertEqual(
                set(state_after_detour["ids"]) - ids_before,
                {returned_feature["decisions"][-1]["id"]},
            )
            self.assertEqual(spec_path.read_bytes(), human_spec_bytes)

            resumed = self.call(root, "implement", returned["revision"], session_b, {
                "task_id": task_id, "phase": "begin",
            })
            self.assertEqual(_execution(resumed)["task_id"], task_id)
            final_state = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(len(final_state["attempts"]), 2)
            self.assertEqual(final_state["attempts"][0], state_after_detour["attempts"][0])
            self.assertEqual(final_state["attempts"][1]["attempt"], 2)
            self.assertEqual(final_state["attempts"][1]["session_id"], session_b)
            self.assertEqual(final_state["checks"], [])
            self.assertIsNone(_selected_feature(resumed)["criteria"][0]["check"])
            self.assertEqual(_selected_feature(resumed)["tasks"][0]["status"], "OUTCOME_UNKNOWN")

    def test_success_report_under_drift_remains_unverified_after_reconcile(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            created = self.call(root, "specify", 0, data={
                **spec_request("Reported without proof"),
                "increments": [],
                "tasks": [{"title": "Report implementation", "criteria": [0]}],
                "plan": "",
            })
            feature = _selected_feature(created)
            task = feature["tasks"][0]
            started = self.call(root, "implement", created["revision"], data={
                "task_id": task["id"], "phase": "begin",
            })
            spec_path = root / "specs" / feature["directory"] / "spec.md"
            spec_path.write_text(
                spec_path.read_text(encoding="utf-8").replace(
                    "<!-- evospec-notes:end -->",
                    "Human note.\n<!-- evospec-notes:end -->",
                ),
                encoding="utf-8",
            )
            reported = self.call(root, "implement", started["revision"], data={
                "task_id": task["id"], "phase": "report",
                "result": "SUCCEEDED", "report": "The implementation appears complete.",
            })
            persisted = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            self.assertIsNone(persisted["execution"])
            self.assertEqual(persisted["attempts"][0]["status"], "REPORTED")
            self.assertEqual(persisted["checks"], [])
            reconciled = self.call(root, "reconcile", reported["revision"], data={
                "feature_id": feature["id"],
            })
            self.assertEqual(
                _selected_feature(reconciled)["tasks"][0]["status"], "REPORTED",
            )

    def test_all_mandate_binds_four_increment_scope_and_accepts_a_later_task(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            request = spec_request("Four increment mandate")
            increments = request.get("increments")
            if not _record_views(increments):
                self.fail("expected the fixture's increment requests")
            request["increments"] = [
                *increments,
                {"title": "Increment 4", "outcome": "Persist the useful result."},
            ]
            created = self.call(root, "specify", 0, data=request)
            feature = _selected_feature(created)
            fourth = feature["increments"][3]
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", created["revision"], data={
                    "phase": "start", "mode": "ALL",
                })
            self.assertEqual(raised.exception.code, "FEATURE_REQUIRED")
            started = self.call(root, "implement", created["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
            })
            mandate = _mandate(started)
            mandate_id = _mandate_id(started)
            self.assertEqual(started["revision"], created["revision"] + 1)
            self.assertEqual(mandate["mode"], "ALL")
            self.assertEqual(mandate["feature_id"], feature["id"])
            self.assertFalse(mandate["close_feature"])
            self.assertEqual(mandate["source_revision"], created["revision"])
            self.assertEqual(mandate["recorded_revision"], started["revision"])
            self.assertEqual(mandate["criterion_ids"], [item["id"] for item in feature["criteria"]])
            self.assertEqual(mandate["increment_ids"], [item["id"] for item in feature["increments"]])
            self.assertEqual(mandate["status"], "PENDING")
            self.assertEqual(mandate["missing_increment_task_ids"], [fourth["id"]])

            saved = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            stored_mandate = saved["mandates"][feature["id"]]
            self.assertEqual(
                stored_mandate["criteria"][0]["digest"],
                saved["identity_metadata"][feature["criteria"][0]["id"]]["digest"],
            )
            self.assertEqual(stored_mandate["increments"][3]["id"], fourth["id"])
            self.assertEqual(
                stored_mandate["increments"][3]["digest"],
                sha256(json.dumps(
                    {"id": fourth["id"], "outcome": fourth["outcome"]},
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")).hexdigest(),
            )

            repeated = self.call(root, "implement", started["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
            })
            self.assertEqual(repeated["revision"], started["revision"])
            self.assertEqual(_mandate(repeated)["id"], mandate_id)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", started["revision"], data={
                    "feature_id": feature["id"], "phase": "queue",
                })
            self.assertEqual(raised.exception.code, "MANDATE_MISMATCH")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", started["revision"], data={
                    "feature_id": feature["id"], "task_id": feature["tasks"][0]["id"],
                    "phase": "begin",
                })
            self.assertEqual(raised.exception.code, "MANDATE_MISMATCH")

            added = self.call(root, "tasks", started["revision"], data={
                "feature_id": feature["id"], "mandate_id": mandate_id,
                "task_title": "Persist the result",
                "task_description": "Keep the accepted task list across reloads.",
                "increment_id": fourth["id"],
                "criteria_ids": [feature["criteria"][0]["id"]],
                "depends_on": [feature["tasks"][-1]["id"]],
            })
            added_mandate = _mandate(added)
            self.assertEqual(added_mandate["id"], mandate_id)
            self.assertEqual(added_mandate["missing_increment_task_ids"], [])
            self.assertEqual(
                next(
                    item for item in _mandate_increments(added)
                    if item["id"] == fourth["id"]
                )["task_ids"],
                [_selected_feature(added)["tasks"][-1]["id"]],
            )
            self.assertEqual(added_mandate["next_task"], feature["tasks"][0]["id"])
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "tasks", added["revision"], data={
                    "feature_id": feature["id"], "task_title": "Bypass the mandate",
                    "criteria_ids": [feature["criteria"][0]["id"]],
                })
            self.assertEqual(raised.exception.code, "MANDATE_MISMATCH")
            self.assertEqual(self.call(root, "status", None)["revision"], added["revision"])

    def test_reconciled_scope_change_stales_mandate_until_exact_replacement(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            created = self.call(root, "specify", 0, data=spec_request("Mandate scope change"))
            feature = _selected_feature(created)
            started = self.call(root, "implement", created["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
            })
            old_mandate = _mandate(started)
            spec_path = root / "specs" / feature["directory"] / "spec.md"
            spec_path.write_text(
                spec_path.read_text(encoding="utf-8").replace(
                    "The focused regression suite passes.",
                    "The accepted criterion has an explicitly revised definition.",
                ),
                encoding="utf-8",
            )
            proposed = self.call(root, "status", None)
            reconciled = self.call(root, "reconcile", proposed["revision"], data={
                "feature_id": feature["id"], "mandate_id": old_mandate["id"],
            })
            stale = _mandate(reconciled)
            self.assertEqual(stale["id"], old_mandate["id"])
            self.assertEqual(stale["status"], "STALE_SCOPE")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "accept", reconciled["revision"], data={
                    "feature_id": feature["id"],
                    "mandate_id": old_mandate["id"],
                    "reason": "This must not accept a stale scope.",
                })
            self.assertEqual(raised.exception.code, "MANDATE_STALE_SCOPE")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", reconciled["revision"], data={
                    "feature_id": feature["id"], "task_id": feature["tasks"][0]["id"],
                    "phase": "begin",
                })
            self.assertEqual(raised.exception.code, "MANDATE_MISMATCH")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", reconciled["revision"], data={
                    "feature_id": feature["id"], "task_id": feature["tasks"][0]["id"],
                    "phase": "begin", "mandate_id": old_mandate["id"],
                })
            self.assertEqual(raised.exception.code, "MANDATE_STALE_SCOPE")

            restarted = self.call(root, "implement", reconciled["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
                "replace_mandate_id": old_mandate["id"],
            })
            new_mandate = _mandate(restarted)
            self.assertNotEqual(new_mandate["id"], old_mandate["id"])
            self.assertEqual(new_mandate["source_revision"], reconciled["revision"])
            self.assertEqual(new_mandate["status"], "PENDING")
            self.assertEqual(restarted["revision"], reconciled["revision"] + 1)

    def test_feature_mandates_can_be_replaced_in_reverse_creation_order(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            session_a, session_b = "ses_ac1_history_a", "ses_ac1_history_b"
            created_a = self.call(
                root, "specify", 0, session_a, spec_request("History Feature A"),
            )
            feature_a = _selected_feature(created_a)
            created_b = self.call(
                root, "specify", created_a["revision"], session_b,
                spec_request("History Feature B"),
            )
            feature_b = _selected_feature(created_b)
            task_a, task_b = feature_a["tasks"][0], feature_b["tasks"][0]
            criterion_a, criterion_b = feature_a["criteria"][0], feature_b["criteria"][0]

            started_a = self.call(root, "implement", created_b["revision"], session_a, {
                "feature_id": feature_a["id"], "phase": "start", "mode": "ALL",
            })
            mandate_a_id = _mandate_id(started_a)
            started_b = self.call(root, "implement", started_a["revision"], session_b, {
                "feature_id": feature_b["id"], "phase": "start", "mode": "ALL",
            })
            mandate_b_id = _mandate_id(started_b)
            recorded_a = _mandate(started_a)["recorded_revision"]
            recorded_b = _mandate(started_b)["recorded_revision"]
            if type(recorded_a) is not int or type(recorded_b) is not int:
                self.fail("expected integer mandate creation revisions")
            self.assertLess(recorded_a, recorded_b)

            revision = started_b["revision"]
            for feature, task, criterion, mandate_id, session in (
                (feature_a, task_a, criterion_a, mandate_a_id, session_a),
                (feature_b, task_b, criterion_b, mandate_b_id, session_b),
            ):
                begun = self.call(root, "implement", revision, session, {
                    "feature_id": feature["id"], "task_id": task["id"],
                    "mandate_id": mandate_id, "phase": "begin",
                })
                reported = self.call(root, "implement", begun["revision"], session, {
                    "feature_id": feature["id"], "task_id": task["id"],
                    "mandate_id": mandate_id, "phase": "report",
                    "result": "SUCCEEDED", "report": f"Completed {feature['title']}.",
                })
                checked = self.record_check(
                    root, reported["revision"], task, criterion,
                    mandate_id=mandate_id, session=session,
                    feature_id=feature["id"],
                )
                revision = checked["revision"]

            for feature, outcome in (
                (feature_a, "Feature A has a materially revised outcome."),
                (feature_b, "Feature B has a materially revised outcome."),
            ):
                spec_path = root / "specs" / feature["directory"] / "spec.md"
                spec_path.write_text(
                    spec_path.read_text(encoding="utf-8").replace(
                        "Deliver the complete user-visible outcome.", outcome,
                    ),
                    encoding="utf-8",
                )

            reconciled: dict[str, _ContinuityView] = {}
            for feature, mandate_id, session in (
                (feature_b, mandate_b_id, session_b),
                (feature_a, mandate_a_id, session_a),
            ):
                drifted = self.call(root, "status", None, session)
                self.assertEqual(drifted["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
                self.assertEqual(_mandate_id(drifted), mandate_id)
                self.assertEqual(_mandate(drifted)["status"], "STALE_SCOPE")
                reconciled[feature["id"]] = self.call(
                    root, "reconcile", drifted["revision"], session, {
                        "feature_id": feature["id"], "mandate_id": mandate_id,
                    },
                )
                self.assertEqual(_mandate_id(reconciled[feature["id"]]), mandate_id)
                self.assertEqual(
                    _mandate(reconciled[feature["id"]])["status"], "STALE_SCOPE",
                )

            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", reconciled[feature_a["id"]]["revision"], session_b, {
                    "feature_id": feature_b["id"], "task_id": feature_b["tasks"][1]["id"],
                    "mandate_id": mandate_b_id, "phase": "begin",
                })
            self.assertEqual(raised.exception.code, "MANDATE_STALE_SCOPE")

            replaced_b = self.call(root, "implement", reconciled[feature_a["id"]]["revision"], session_b, {
                "feature_id": feature_b["id"], "phase": "start", "mode": "ALL",
                "replace_mandate_id": mandate_b_id,
            })
            new_mandate_b_id = _mandate_id(replaced_b)
            replaced_a = self.call(root, "implement", replaced_b["revision"], session_a, {
                "feature_id": feature_a["id"], "phase": "start", "mode": "ALL",
                "replace_mandate_id": mandate_a_id,
            })
            new_mandate_a_id = _mandate_id(replaced_a)

            saved = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            history = saved["mandate_history"]
            self.assertEqual(
                [item["id"] for item in history], [mandate_b_id, mandate_a_id],
            )
            self.assertEqual(
                [item["feature_id"] for item in history],
                [feature_b["id"], feature_a["id"]],
            )
            self.assertEqual(
                set(saved["mandates"]), {feature_a["id"], feature_b["id"]},
            )
            self.assertEqual(saved["mandates"][feature_a["id"]]["id"], new_mandate_a_id)
            self.assertEqual(saved["mandates"][feature_b["id"]]["id"], new_mandate_b_id)
            attempts_by_task = {item["task_id"]: item for item in saved["attempts"]}
            checks_by_task = {item["task_id"]: item for item in saved["checks"]}
            self.assertEqual(attempts_by_task[task_a["id"]]["mandate_id"], mandate_a_id)
            self.assertEqual(attempts_by_task[task_b["id"]]["mandate_id"], mandate_b_id)
            self.assertEqual(checks_by_task[task_a["id"]]["mandate_id"], mandate_a_id)
            self.assertEqual(checks_by_task[task_b["id"]]["mandate_id"], mandate_b_id)
            self.assertEqual(
                {item["id"] for item in history[0]["criteria"]},
                {item["id"] for item in feature_b["criteria"]},
            )
            self.assertEqual(
                {item["id"] for item in history[1]["criteria"]},
                {item["id"] for item in feature_a["criteria"]},
            )
            self.assertEqual(
                {item["id"] for item in history[0]["increments"]},
                {item["id"] for item in feature_b["increments"]},
            )
            self.assertEqual(
                {item["id"] for item in history[1]["increments"]},
                {item["id"] for item in feature_a["increments"]},
            )

    def test_feature_mandates_are_local_while_unknown_execution_stays_global(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            session_a = "ses_ac1_feature_a"
            session_b = "ses_ac1_feature_b"
            recovery_session = "ses_ac1_feature_recovery"

            def one_increment_request(title: str) -> dict[str, object]:
                request = spec_request(title)
                increments = request.get("increments")
                tasks = request.get("tasks")
                if (
                    not _record_views(increments) or not increments
                    or not _record_views(tasks) or not tasks
                ):
                    raise AssertionError("expected the fixture's Increment and Task requests")
                request["increments"] = [increments[0]]
                request["tasks"] = [tasks[0]]
                return request

            created_a = self.call(
                root, "specify", 0, session_a, one_increment_request("Feature A"),
            )
            feature_a = _selected_feature(created_a)
            created_b = self.call(
                root, "specify", created_a["revision"], session_b,
                one_increment_request("Feature B"),
            )
            feature_b = _selected_feature(created_b)
            task_a, criterion_a = feature_a["tasks"][0], feature_a["criteria"][0]
            task_b, criterion_b = feature_b["tasks"][0], feature_b["criteria"][0]

            started_a = self.call(root, "implement", created_b["revision"], session_a, {
                "feature_id": feature_a["id"], "phase": "start", "mode": "ALL",
            })
            mandate_a_id = _mandate_id(started_a)
            paused_a = self.call(root, "pause", started_a["revision"], session_a, {
                "feature_id": feature_a["id"], "mandate_id": mandate_a_id,
                "reason": "Keep Feature A paused while Feature B is completed.",
            })
            self.assertEqual(_mandate(paused_a)["status"], "PAUSED")

            feature_b_spec = root / "specs" / feature_b["directory"] / "spec.md"
            original_b_spec = feature_b_spec.read_text(encoding="utf-8")
            feature_b_spec.write_text(
                original_b_spec.replace(
                    "Deliver the complete user-visible outcome.",
                    "Deliver Feature B's independently revised outcome.",
                ),
                encoding="utf-8",
            )
            b_status = self.call(root, "status", None, session_b)
            self.assertEqual(b_status["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
            reconciled_b = self.call(root, "reconcile", b_status["revision"], session_b, {
                "feature_id": feature_b["id"],
            })
            selected_a = self.call(root, "status", None, session_a)
            self.assertEqual(_mandate_id(selected_a), mandate_a_id)
            self.assertEqual(_mandate(selected_a)["status"], "PAUSED")

            started_b = self.call(root, "implement", reconciled_b["revision"], session_b, {
                "feature_id": feature_b["id"], "phase": "start", "mode": "ALL",
            })
            mandate_b_id = _mandate_id(started_b)
            begun_b = self.call(root, "implement", started_b["revision"], session_b, {
                "feature_id": feature_b["id"], "task_id": task_b["id"],
                "mandate_id": mandate_b_id, "phase": "begin",
            })
            reported_b = self.call(root, "implement", begun_b["revision"], session_b, {
                "feature_id": feature_b["id"], "task_id": task_b["id"],
                "mandate_id": mandate_b_id, "phase": "report",
                "result": "SUCCEEDED", "report": "Feature B work completed.",
            })
            checked_b = self.record_check(
                root, reported_b["revision"], task_b, criterion_b,
                mandate_id=mandate_b_id, session=session_b,
                feature_id=feature_b["id"],
            )
            self.assertEqual(_mandate(checked_b)["status"], "FINISHED")
            self.assertEqual(_mandate_id(self.call(root, "status", None, session_a)), mandate_a_id)

            resumed_a = self.call(root, "resume", checked_b["revision"], session_a, {
                "feature_id": feature_a["id"], "mandate_id": mandate_a_id,
            })
            self.assertEqual(_mandate(resumed_a)["id"], mandate_a_id)
            self.assertEqual(_mandate(resumed_a)["status"], "PENDING")
            checkpointed_a = self.call(root, "implement", resumed_a["revision"], session_a, {
                "feature_id": feature_a["id"], "task_id": task_a["id"],
                "mandate_id": mandate_a_id, "phase": "begin",
                "report": "Feature A checkpoint before simulated effects.",
            })
            self.assertEqual(checkpointed_a.get("execution_feature_id"), feature_a["id"])
            active_attempt = checkpointed_a.get("active_attempt")
            self.assertIsNotNone(active_attempt)
            assert active_attempt is not None
            self.assertEqual(active_attempt["feature_id"], feature_a["id"])
            self.assertEqual(active_attempt["task_id"], task_a["id"])
            self.assertEqual(active_attempt["mandate_id"], mandate_a_id)

            blocked_attempts: tuple[tuple[str, dict[str, object], str], ...] = (
                (
                    "implement",
                    {
                        "feature_id": feature_b["id"], "task_id": task_b["id"],
                        "mandate_id": mandate_b_id, "phase": "begin",
                    },
                    "EXECUTION_BUSY",
                ),
                (
                    "implement",
                    {
                        "feature_id": feature_b["id"], "phase": "start",
                        "mode": "ALL", "replace_mandate_id": mandate_b_id,
                    },
                    "EXECUTION_BUSY",
                ),
                (
                    "implement",
                    {
                        "feature_id": feature_a["id"], "phase": "start",
                        "mode": "ALL", "replace_mandate_id": mandate_a_id,
                    },
                    "EXECUTION_BUSY",
                ),
            )
            for operation, data, expected in blocked_attempts:
                with self.assertRaises(ContinuityError) as raised:
                    self.call(
                        root, operation, checkpointed_a["revision"], session_b, data,
                    )
                self.assertEqual(raised.exception.code, expected)

            selected_while_a_unknown = self.call(
                root, "status", None, session_a,
            )
            self.assertEqual(selected_while_a_unknown.get("selected_feature_id"), feature_a["id"])
            self.assertEqual(selected_while_a_unknown.get("execution_feature_id"), feature_a["id"])
            self.assertEqual(
                _mandate(selected_while_a_unknown)["status"], "UNKNOWN_EFFECT",
            )
            feature_b_view = next(
                item for item in _features(selected_while_a_unknown)
                if item["id"] == feature_b["id"]
            )
            feature_b_mandate = feature_b_view.get("mandate")
            self.assertIsNotNone(feature_b_mandate)
            assert feature_b_mandate is not None
            self.assertEqual(feature_b_mandate["id"], mandate_b_id)
            self.assertEqual(feature_b_mandate["status"], "FINISHED")

            b_plan = root / "specs" / feature_b["directory"] / "plan.md"
            b_plan.write_text(
                b_plan.read_text(encoding="utf-8")
                .replace("Build each increment in order", "Refine Feature B's plan"),
                encoding="utf-8",
            )
            drifted_b = self.call(root, "status", None, session_b)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "reconcile", drifted_b["revision"], session_b, {
                    "feature_id": feature_b["id"], "mandate_id": mandate_b_id,
                })
            self.assertEqual(raised.exception.code, "EXECUTION_BUSY")

            for task_id, supplied_mandate, expected in (
                (task_a["id"], None, "MANDATE_MISMATCH"),
                (task_b["id"], mandate_a_id, "TASK_MISMATCH"),
            ):
                data: dict[str, object] = {
                    "feature_id": feature_a["id"], "task_id": task_id,
                    "reconcile": "COMPLETE", "evidence_ref": "fixture:observed-a",
                }
                if supplied_mandate is not None:
                    data["mandate_id"] = supplied_mandate
                with self.assertRaises(ContinuityError) as raised:
                    self.call(
                        root, "resume", checkpointed_a["revision"],
                        recovery_session, data,
                    )
                self.assertEqual(raised.exception.code, expected)

            recovered_a = self.call(
                root, "resume", checkpointed_a["revision"], recovery_session, {
                    "feature_id": feature_a["id"], "task_id": task_a["id"],
                    "mandate_id": mandate_a_id, "reconcile": "COMPLETE",
                    "evidence_ref": "fixture:observed-a",
                },
            )
            self.assertIsNone(recovered_a.get("active_attempt"))
            reconciled_b_plan = self.call(
                root, "reconcile", recovered_a["revision"], session_b, {
                    "feature_id": feature_b["id"], "mandate_id": mandate_b_id,
                },
            )
            self.assertEqual(_mandate(reconciled_b_plan)["id"], mandate_b_id)
            self.assertEqual(_mandate(reconciled_b_plan)["status"], "FINISHED")

            checked_a = self.record_check(
                root, reconciled_b_plan["revision"], task_a, criterion_a,
                mandate_id=mandate_a_id, session=session_a,
                feature_id=feature_a["id"],
            )
            returned_a = self.call(root, "status", None, session_a)
            returned_b = self.call(root, "status", None, session_b)
            self.assertEqual(_selected_feature(returned_a)["id"], feature_a["id"])
            self.assertEqual(_mandate_id(returned_a), mandate_a_id)
            self.assertEqual(_mandate(returned_a)["status"], "FINISHED")
            self.assertEqual(_selected_feature(returned_b)["id"], feature_b["id"])
            self.assertEqual(_mandate_id(returned_b), mandate_b_id)
            self.assertEqual(_mandate(returned_b)["status"], "FINISHED")
            repeated_a = self.call(root, "implement", returned_a["revision"], session_a, {
                "feature_id": feature_a["id"], "phase": "start", "mode": "ALL",
                "replace_mandate_id": mandate_a_id,
            })
            self.assertEqual(repeated_a["revision"], returned_a["revision"])
            self.assertEqual(_mandate_id(repeated_a), mandate_a_id)

            saved = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                {feature_a["id"], feature_b["id"]}, set(saved["mandates"]),
            )
            self.assertEqual(saved["mandates"][feature_a["id"]]["id"], mandate_a_id)
            self.assertEqual(saved["mandates"][feature_b["id"]]["id"], mandate_b_id)
            self.assertEqual(
                [item["mandate_id"] for item in saved["attempts"]],
                [mandate_b_id, mandate_a_id],
            )
            self.assertEqual(
                {item["mandate_id"] for item in saved["checks"]},
                {mandate_a_id, mandate_b_id},
            )
            recovered_attempt = next(
                item for item in saved["attempts"] if item["task_id"] == task_a["id"]
            )
            self.assertEqual(recovered_attempt["status"], "RECONCILED_COMPLETE")
            self.assertEqual(recovered_attempt["mandate_id"], mandate_a_id)
            self.assertEqual(recovered_attempt["evidence_ref"], "fixture:observed-a")

    def test_mandate_binds_normative_outcomes_but_allows_titles_notes_and_plan_refinement(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            created = self.call(root, "specify", 0, data=spec_request())
            feature = _selected_feature(created)
            criterion = feature["criteria"][0]
            task = feature["tasks"][0]
            first_increment = feature["increments"][0]
            started = self.call(root, "implement", created["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
            })
            original_mandate_id = _mandate_id(started)
            checkpointed = self.call(root, "implement", started["revision"], data={
                "feature_id": feature["id"], "task_id": task["id"],
                "mandate_id": original_mandate_id, "phase": "begin",
            })
            reported = self.call(root, "implement", checkpointed["revision"], data={
                "feature_id": feature["id"], "task_id": task["id"],
                "mandate_id": original_mandate_id, "phase": "report",
                "result": "SUCCEEDED", "report": "The first Task effect completed.",
            })
            checked = self.record_check(
                root, reported["revision"], task, criterion,
                mandate_id=original_mandate_id, feature_id=feature["id"],
            )

            spec_path = root / "specs" / feature["directory"] / "spec.md"
            original_spec = spec_path.read_text(encoding="utf-8")
            titled_spec = original_spec.replace(
                "# Three increment Feature", "# Display title changed",
            ).replace(
                f"**{first_increment['title']}**", "**Renamed increment for display**",
            ).replace(
                "<!-- evospec-notes:start -->\n<!-- evospec-notes:end -->",
                "<!-- evospec-notes:start -->\nThese notes are non-normative.\n<!-- evospec-notes:end -->",
            ).replace("\n", "\r\n")
            spec_path.write_bytes(titled_spec.encode("utf-8"))
            title_status = self.call(root, "status", None)
            self.assertEqual(title_status["status"], "DOCUMENT_RECONCILIATION_REQUIRED")
            self.assertEqual(_mandate_id(title_status), original_mandate_id)
            self.assertEqual(_mandate(title_status)["status"], "PENDING")
            reconciled_titles = self.call(root, "reconcile", title_status["revision"], data={
                "feature_id": feature["id"], "mandate_id": original_mandate_id,
            })
            titled_feature = _selected_feature(reconciled_titles)
            self.assertEqual(titled_feature["title"], "Display title changed")
            self.assertEqual(titled_feature["increments"][0]["id"], first_increment["id"])
            self.assertEqual(_mandate_id(reconciled_titles), original_mandate_id)
            self.assertEqual(_mandate(reconciled_titles)["status"], "PENDING")

            normalized_spec = spec_path.read_text(encoding="utf-8").replace(
                "\r\n", "\n",
            ).replace(
                "These notes are non-normative.",
                "Formatting and notes do not change the accepted Outcome.",
            )
            spec_path.write_bytes(normalized_spec.encode("utf-8"))
            format_status = self.call(root, "status", None)
            document_changes = format_status.get("document_changes")
            self.assertIsNotNone(document_changes)
            assert document_changes is not None
            self.assertIn(
                f"specs/{feature['directory']}/spec.md",
                document_changes["format_notes"],
            )
            reconciled_format = self.call(
                root, "reconcile", format_status["revision"], data={
                    "feature_id": feature["id"], "mandate_id": original_mandate_id,
                },
            )
            self.assertEqual(_mandate_id(reconciled_format), original_mandate_id)
            self.assertEqual(_mandate(reconciled_format)["status"], "PENDING")

            technical_plan = (
                f"Increment `{first_increment['id']}` — technical refinement 2:\n"
                "Use a bounded JSON parse around local storage reads; keep valid "
                "records and return an empty list for malformed storage."
            )
            refined_plan = self.call(root, "plan", reconciled_format["revision"], data={
                "feature_id": feature["id"], "plan": technical_plan,
            })
            self.assertIn(first_increment["id"], _selected_feature(refined_plan)["plan"])
            self.assertIn("bounded JSON parse", _selected_feature(refined_plan)["plan"])
            self.assertEqual(_mandate_id(refined_plan), original_mandate_id)
            self.assertEqual(_mandate(refined_plan)["status"], "PENDING")
            self.assertEqual(
                [item["id"] for item in _mandate_increments(refined_plan)],
                [item["id"] for item in feature["increments"]],
            )

            current_spec = spec_path.read_text(encoding="utf-8")
            spec_path.write_text(
                current_spec.replace(
                    "Deliver the complete user-visible outcome.",
                    "Deliver the revised normative Feature outcome.",
                ),
                encoding="utf-8",
            )
            outcome_status = self.call(root, "status", None)
            self.assertEqual(_mandate(outcome_status)["status"], "STALE_SCOPE")
            reconciled_outcome = self.call(
                root, "reconcile", outcome_status["revision"], data={
                    "feature_id": feature["id"],
                    "mandate_id": original_mandate_id,
                },
            )
            self.assertEqual(_mandate(reconciled_outcome)["status"], "STALE_SCOPE")
            self.assertEqual(
                _criterion_check(
                    _selected_feature(reconciled_outcome)["criteria"][0]
                ).get("mandate_id"),
                original_mandate_id,
            )
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", reconciled_outcome["revision"], data={
                    "feature_id": feature["id"], "task_id": task["id"],
                    "mandate_id": original_mandate_id, "phase": "begin",
                })
            self.assertEqual(raised.exception.code, "MANDATE_STALE_SCOPE")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", reconciled_outcome["revision"], data={
                    "feature_id": feature["id"], "phase": "start", "mode": "ALL",
                })
            self.assertEqual(raised.exception.code, "MANDATE_ACTIVE")
            replaced_outcome = self.call(root, "implement", reconciled_outcome["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
                "replace_mandate_id": original_mandate_id,
            })
            outcome_mandate_id = _mandate_id(replaced_outcome)
            self.assertNotEqual(outcome_mandate_id, original_mandate_id)
            self.assertEqual(_mandate(replaced_outcome)["status"], "PENDING")

            current_spec = spec_path.read_text(encoding="utf-8")
            spec_path.write_text(
                current_spec.replace(
                    first_increment["outcome"],
                    "A changed normative Increment outcome requiring new authority.",
                ),
                encoding="utf-8",
            )
            increment_status = self.call(root, "status", None)
            self.assertEqual(_mandate(increment_status)["status"], "STALE_SCOPE")
            reconciled_increment = self.call(
                root, "reconcile", increment_status["revision"], data={
                    "feature_id": feature["id"], "mandate_id": outcome_mandate_id,
                },
            )
            self.assertEqual(_mandate(reconciled_increment)["status"], "STALE_SCOPE")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", reconciled_increment["revision"], data={
                    "feature_id": feature["id"], "phase": "start", "mode": "ALL",
                })
            self.assertEqual(raised.exception.code, "MANDATE_ACTIVE")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", reconciled_increment["revision"], data={
                    "feature_id": feature["id"], "task_id": task["id"],
                    "mandate_id": outcome_mandate_id, "phase": "begin",
                })
            self.assertEqual(raised.exception.code, "MANDATE_STALE_SCOPE")
            replaced_increment = self.call(
                root, "implement", reconciled_increment["revision"], data={
                    "feature_id": feature["id"], "phase": "start", "mode": "ALL",
                    "replace_mandate_id": outcome_mandate_id,
                },
            )
            self.assertNotEqual(_mandate_id(replaced_increment), outcome_mandate_id)
            saved = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                [item["id"] for item in saved["mandate_history"]],
                [original_mandate_id, outcome_mandate_id],
            )
            self.assertEqual(
                saved["checks"][0]["mandate_id"], original_mandate_id,
                "old check provenance remains unchanged after exact replacements",
            )
            self.assertEqual(saved["attempts"][0]["task_id"], task["id"])
            self.assertEqual(saved["attempts"][0]["mandate_id"], original_mandate_id)
            self.assertEqual(saved["attempts"][0]["status"], "REPORTED")

    def test_legacy_unbound_mandate_keeps_exact_unknown_recovery_and_history(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            created = self.call(root, "specify", 0, data={
                **spec_request("Legacy unbound mandate"),
                "increments": [],
                "tasks": [{"title": "Legacy checkpoint", "criteria": [0]}],
                "plan": "",
            })
            feature = _selected_feature(created)
            task = feature["tasks"][0]
            started = self.call(root, "implement", created["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
            })
            mandate_id = _mandate_id(started)
            checkpointed = self.call(root, "implement", started["revision"], data={
                "feature_id": feature["id"], "task_id": task["id"],
                "mandate_id": mandate_id, "phase": "begin",
            })

            state_path = root / ".evospec" / "continuity.json"
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            legacy_mandate = saved.pop("mandates").pop(feature["id"])
            legacy_mandate.pop("outcome_digest")
            saved.pop("mandate_history")
            saved["mandate"] = legacy_mandate
            state_path.write_text(
                json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )

            recovered_status = self.call(root, "status", None, "ses_ac1_legacy_recovery")
            self.assertEqual(_mandate(recovered_status)["id"], mandate_id)
            self.assertEqual(_mandate(recovered_status)["status"], "STALE_SCOPE")
            self.assertEqual(recovered_status.get("execution_feature_id"), feature["id"])
            legacy_active_attempt = recovered_status.get("active_attempt")
            self.assertIsNotNone(legacy_active_attempt)
            assert legacy_active_attempt is not None
            self.assertEqual(legacy_active_attempt["mandate_id"], mandate_id)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", checkpointed["revision"], data={
                    "feature_id": feature["id"], "task_id": task["id"],
                    "mandate_id": mandate_id, "phase": "begin",
                })
            self.assertEqual(raised.exception.code, "MANDATE_STALE_SCOPE")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", checkpointed["revision"], "ses_ac1_legacy_recovery", {
                    "feature_id": feature["id"], "task_id": task["id"],
                    "reconcile": "INCOMPLETE", "evidence_ref": "fixture:legacy-observation",
                })
            self.assertEqual(raised.exception.code, "MANDATE_MISMATCH")

            exact_recovery = self.call(
                root, "resume", checkpointed["revision"], "ses_ac1_legacy_recovery", {
                    "feature_id": feature["id"], "task_id": task["id"],
                    "mandate_id": mandate_id, "reconcile": "INCOMPLETE",
                    "evidence_ref": "fixture:legacy-observation",
                },
            )
            self.assertIsNone(exact_recovery.get("active_attempt"))
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", exact_recovery["revision"], data={
                    "feature_id": feature["id"], "phase": "start", "mode": "ALL",
                })
            self.assertEqual(raised.exception.code, "MANDATE_ACTIVE")
            replacement = self.call(root, "implement", exact_recovery["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
                "replace_mandate_id": mandate_id,
            })
            self.assertNotEqual(_mandate_id(replacement), mandate_id)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("mandate", saved)
            self.assertEqual(saved["mandate_history"], [legacy_mandate])
            self.assertEqual(saved["attempts"][0]["mandate_id"], mandate_id)
            self.assertEqual(
                saved["attempts"][0]["evidence_ref"], "fixture:legacy-observation",
            )

    def test_cyclic_markdown_task_dependencies_remain_conflicted_under_a_mandate(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            created = self.call(root, "specify", 0, data={
                **spec_request("Reject task cycles"),
                "increments": [],
                "tasks": [
                    {"title": "Task A", "criteria": [0]},
                    {"title": "Task B", "criteria": [0], "depends_on": [0]},
                ],
                "plan": "",
            })
            feature = _selected_feature(created)
            task_a, task_b = feature["tasks"]
            started = self.call(root, "implement", created["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
            })
            mandate_id = _mandate_id(started)
            tasks_path = root / "specs" / feature["directory"] / "tasks.md"
            content = tasks_path.read_text(encoding="utf-8")
            task_a_start = content.index(f"### `{task_a['id']}` — Task A\n")
            dependency_line = content.index("- Depends on: none\n", task_a_start)
            content = (
                content[:dependency_line]
                + f"- Depends on: `{task_b['id']}`\n"
                + content[dependency_line + len("- Depends on: none\n"):]
            )
            tasks_path.write_text(content, encoding="utf-8")
            marker = root / ".evospec" / "continuity.json"
            before_status = marker.read_bytes()

            status = self.call(root, "status", None)

            self.assertEqual(status["status"], "DOCUMENT_CONFLICT")
            self.assertEqual(_features(status), [])
            self.assertEqual(marker.read_bytes(), before_status)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "reconcile", started["revision"], data={
                    "feature_id": feature["id"], "mandate_id": mandate_id,
                })
            self.assertEqual(raised.exception.code, "DOCUMENT_DIVERGED")
            self.assertEqual(marker.read_bytes(), before_status)
            self.assertIn(task_b["id"], tasks_path.read_text(encoding="utf-8"))

    def test_task_block_cancel_reopen_preserves_identity_and_requires_new_attempt_and_check(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            created = self.call(root, "specify", 0, data={
                **spec_request("Task lifecycle events"),
                "increments": [],
                "tasks": [{"title": "Implement one scoped behavior", "criteria": [0]}],
                "plan": "",
            })
            feature = _selected_feature(created)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            started = self.call(root, "implement", created["revision"], data={
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
            })
            mandate_id = _mandate_id(started)
            begun = self.call(root, "implement", started["revision"], data={
                "feature_id": feature["id"], "task_id": task["id"],
                "phase": "begin", "mandate_id": mandate_id,
            })
            reported = self.call(root, "implement", begun["revision"], data={
                "feature_id": feature["id"], "task_id": task["id"],
                "phase": "report", "mandate_id": mandate_id,
                "result": "SUCCEEDED", "report": "The scoped behavior is implemented.",
            })
            checked = self.record_check(
                root, reported["revision"], task, criterion,
                mandate_id=mandate_id, feature_id=feature["id"],
            )
            self.assertEqual(_selected_feature(checked)["tasks"][0]["status"], "COMPLETED")
            lifecycle_data = {
                "feature_id": feature["id"], "task_id": task["id"],
                "mandate_id": mandate_id, "reason": "Keep this status transition explicit.",
                "evidence_ref": "fixture:task-lifecycle-observation",
            }
            files_before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            with self.assertRaises(ContinuityError) as raised:
                self.call(
                    root, "tasks", checked["revision"] - 1,
                    data={**lifecycle_data, "task_event": "BLOCKED"},
                )
            self.assertEqual(raised.exception.code, "STALE_REVISION")
            with self.assertRaises(ContinuityError) as raised:
                self.call(
                    root, "tasks", checked["revision"],
                    data={**lifecycle_data, "task_event": "DONE"},
                )
            self.assertEqual(raised.exception.code, "INVALID_REQUEST")
            with self.assertRaises(ContinuityError) as raised:
                self.call(
                    root, "tasks", checked["revision"],
                    data={**lifecycle_data, "task_event": "BLOCKED", "evidence_ref": ""},
                )
            self.assertEqual(raised.exception.code, "INVALID_REQUEST")
            self.assertEqual(files_before, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            })

            blocked = self.call(root, "tasks", checked["revision"], data={
                **lifecycle_data, "task_event": "BLOCKED",
            })
            self.assertEqual(_selected_feature(blocked)["tasks"][0]["id"], task["id"])
            self.assertEqual(_selected_feature(blocked)["tasks"][0]["status"], "BLOCKED")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "tasks", blocked["revision"], data={
                    **lifecycle_data, "task_event": "CANCELED",
                })
            self.assertEqual(raised.exception.code, "TASK_TRANSITION_INVALID")
            reopened = self.call(root, "tasks", blocked["revision"], data={
                **lifecycle_data, "task_event": "REOPENED",
                "reason": "Apply the correction and recheck the same work.",
            })
            self.assertEqual(_selected_feature(reopened)["tasks"][0]["status"], "READY")
            second_attempt = self.call(root, "implement", reopened["revision"], data={
                "feature_id": feature["id"], "task_id": task["id"],
                "phase": "begin", "mandate_id": mandate_id,
            })
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "tasks", second_attempt["revision"], data={
                    **lifecycle_data, "task_event": "CANCELED",
                })
            self.assertEqual(raised.exception.code, "EXECUTION_BUSY")
            second_report = self.call(root, "implement", second_attempt["revision"], data={
                "feature_id": feature["id"], "task_id": task["id"],
                "phase": "report", "mandate_id": mandate_id,
                "result": "SUCCEEDED", "report": "The reopened work is implemented.",
            })
            self.assertEqual(_selected_feature(second_report)["tasks"][0]["status"], "REPORTED")
            second_check = self.record_check(
                root, second_report["revision"], task, criterion,
                mandate_id=mandate_id, feature_id=feature["id"],
            )
            self.assertEqual(_selected_feature(second_check)["tasks"][0]["status"], "COMPLETED")

            canceled = self.call(root, "tasks", second_check["revision"], data={
                **lifecycle_data, "task_event": "CANCELED",
                "reason": "The accepted work was explicitly canceled.",
            })
            self.assertEqual(_selected_feature(canceled)["tasks"][0]["status"], "CANCELED")
            self.assertNotEqual(_selected_feature(canceled)["tasks"][0]["status"], "COMPLETED")
            reopened_again = self.call(root, "tasks", canceled["revision"], data={
                **lifecycle_data, "task_event": "REOPENED",
                "reason": "Reopen the same stable Task identity.",
            })
            reopened_task = _selected_feature(reopened_again)["tasks"][0]
            self.assertEqual(reopened_task["id"], task["id"])
            self.assertEqual(reopened_task["status"], "READY")
            state = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                [item["event"] for item in state["task_events"]],
                ["BLOCKED", "REOPENED", "CANCELED", "REOPENED"],
            )
            self.assertEqual(
                [item["evidence_ref"] for item in state["task_events"]],
                ["fixture:task-lifecycle-observation"] * 4,
            )
            self.assertEqual(state["attempts"][1]["mandate_id"], mandate_id)
            self.assertEqual(
                _mandate(reopened_again)["status"], "PENDING",
                "reopening does not pass the Task or satisfy the mandate",
            )

    def test_mandated_unknown_attempt_is_visible_and_reconciled_in_a_fresh_session(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            session_a = "ses_ac1_primary_123"
            session_b = "ses_ac1_recovery_456"
            created = self.call(root, "specify", 0, session_a, {
                **spec_request("Mandated recovery"),
                "increments": [],
                "tasks": [
                    {"title": "Checkpoint the first Task", "criteria": [0]},
                    {"title": "Continue to the next Task", "criteria": [0]},
                ],
                "plan": "",
            })
            feature = _selected_feature(created)
            first_task, next_task = feature["tasks"]
            started = self.call(root, "implement", created["revision"], session_a, {
                "feature_id": feature["id"], "phase": "start", "mode": "ALL",
            })
            mandate_id = _mandate_id(started)
            checkpointed = self.call(root, "implement", started["revision"], session_a, {
                "feature_id": feature["id"], "task_id": first_task["id"],
                "phase": "begin", "mandate_id": mandate_id,
                "report": "Checkpoint before simulated host effects.",
            })
            recovered_status = self.call(root, "status", None, session_b)
            recovered_mandate = _mandate(recovered_status)
            self.assertEqual(recovered_mandate["id"], mandate_id)
            self.assertEqual(recovered_mandate["feature_id"], feature["id"])
            self.assertEqual(recovered_mandate["mode"], "ALL")
            self.assertEqual(recovered_mandate["status"], "UNKNOWN_EFFECT")
            self.assertIsNone(recovered_mandate["next_task"])
            active_attempt = recovered_status.get("active_attempt")
            if active_attempt is None:
                self.fail("expected the persisted Task attempt checkpoint")
            self.assertEqual(active_attempt["task_id"], first_task["id"])
            self.assertEqual(
                active_attempt["checkpoint"], "Checkpoint before simulated host effects.",
            )
            self.assertEqual(active_attempt["session_id"], session_a)
            projected_task = _selected_feature(recovered_status)["tasks"][0]
            self.assertEqual(projected_task["status"], "OUTCOME_UNKNOWN")
            attempt_history = projected_task.get("attempt_history")
            if attempt_history is None:
                self.fail("expected the projected Task attempt history")
            self.assertEqual(attempt_history[0]["mandate_id"], mandate_id)

            for phase_data, expected_code in (
                (
                    {
                        "feature_id": feature["id"], "task_id": first_task["id"],
                        "phase": "begin", "mandate_id": mandate_id,
                    },
                    "EXECUTION_BUSY",
                ),
                (
                    {
                        "feature_id": feature["id"], "task_id": first_task["id"],
                        "phase": "report", "mandate_id": mandate_id,
                        "result": "SUCCEEDED", "report": "Do not retarget the first session.",
                    },
                    "OUTCOME_UNKNOWN",
                ),
            ):
                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "implement", recovered_status["revision"], session_b, phase_data)
                self.assertEqual(raised.exception.code, expected_code)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", recovered_status["revision"], session_b, {
                    "feature_id": feature["id"], "task_id": first_task["id"],
                    "reconcile": "COMPLETE",
                    "evidence_ref": "fixture:observed-effect",
                })
            self.assertEqual(raised.exception.code, "MANDATE_MISMATCH")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", recovered_status["revision"], session_b, {
                    "feature_id": feature["id"], "task_id": next_task["id"],
                    "mandate_id": mandate_id, "reconcile": "COMPLETE",
                    "evidence_ref": "fixture:wrong-task",
                })
            self.assertEqual(raised.exception.code, "TASK_MISMATCH")

            reconciled = self.call(root, "resume", recovered_status["revision"], session_b, {
                "feature_id": feature["id"], "task_id": first_task["id"],
                "mandate_id": mandate_id, "reconcile": "COMPLETE",
                "evidence_ref": "fixture:observed-effect",
            })
            self.assertIsNone(reconciled.get("active_attempt"))
            self.assertEqual(
                _selected_feature(reconciled)["tasks"][0]["status"], "REPORTED",
            )
            persisted = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(len(persisted["attempts"]), 1)
            self.assertEqual(persisted["attempts"][0]["evidence_ref"], "fixture:observed-effect")
            self.assertEqual(persisted["attempts"][0]["mandate_id"], mandate_id)
            checked = self.record_check(
                root, reconciled["revision"], first_task, feature["criteria"][0],
                mandate_id=mandate_id, session=session_b, feature_id=feature["id"],
            )
            self.assertEqual(_selected_feature(checked)["tasks"][0]["status"], "COMPLETED")
            self.assertEqual(_mandate(checked)["next_task"], next_task["id"])
            self.assertEqual(_selected_feature(checked)["status"], "OPEN")
            self.assertEqual(
                len(json.loads(
                    (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
                )["attempts"]),
                1,
                "fresh-session recovery does not replay or duplicate the original attempt",
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

    def test_related_mandate_detour_pauses_before_effect_and_returns_exact_task(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            root.joinpath("index.html").write_text(
                ".add-button { background: #888888; }\n", encoding="utf-8",
            )
            created = self.call(root, "specify", 0, data=spec_request("Related mandate detour"))
            feature = _selected_feature(created)
            feature_id = feature["id"]
            task1, return_task, later_task = feature["tasks"]
            criterion = feature["criteria"][0]
            started = self.call(root, "implement", created["revision"], data={
                "feature_id": feature_id, "phase": "start", "mode": "ALL",
            })
            mandate_id = _mandate_id(started)
            attempt = self.call(root, "implement", started["revision"], data={
                "feature_id": feature_id, "task_id": task1["id"],
                "mandate_id": mandate_id, "phase": "begin",
            })
            reported = self.call(root, "implement", attempt["revision"], data={
                "feature_id": feature_id, "task_id": task1["id"],
                "mandate_id": mandate_id, "phase": "report",
                "result": "SUCCEEDED", "report": "Create/list behavior is in place.",
            })
            checked = self.record_check(
                root, reported["revision"], task1, criterion,
                mandate_id=mandate_id, feature_id=feature_id,
            )
            self.assertEqual(_mandate(checked)["next_task"], return_task["id"])

            request = "Antes cambia el botón de agregar a azul y luego sigue"
            paused = self.call(root, "pause", checked["revision"], data={
                "feature_id": feature_id, "mandate_id": mandate_id,
                "reason": request, "detour_kind": "RELATED",
                "detour_title": request, "return_task_id": return_task["id"],
            })
            paused_feature = _selected_feature(paused)
            pending_detour = paused_feature["detours"][-1]
            self.assertEqual(_mandate(paused)["id"], mandate_id)
            self.assertEqual(_mandate(paused)["status"], "PAUSED")
            self.assertEqual(_mandate(paused)["next_task"], return_task["id"])
            self.assertEqual(pending_detour["kind"], "RELATED")
            self.assertEqual(pending_detour["return_task_id"], return_task["id"])
            self.assertIsNone(pending_detour["result"])
            self.assertEqual(paused["execution"], None)
            self.assertEqual(paused_feature["tasks"][1]["attempts"], 0)

            marker = root / ".evospec" / "continuity.json"
            before_effect = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(before_effect["paused_features"], [feature_id])
            self.assertIsNone(before_effect["execution"])
            stored_detour = next(
                item for item in before_effect["detours"]
                if item["id"] == pending_detour["id"]
            )
            self.assertEqual(stored_detour["return_task_id"], return_task["id"])
            self.assertIsNone(stored_detour["result"])
            self.assertEqual(
                before_effect["mandates"][feature_id]["id"], mandate_id,
                "the accepted mandate is durably paused before the related effect",
            )

            before_blocked_begin = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "implement", paused["revision"], data={
                    "feature_id": feature_id, "task_id": return_task["id"],
                    "mandate_id": mandate_id, "phase": "begin",
                })
            self.assertEqual(raised.exception.code, "FEATURE_PAUSED")
            self.assertEqual(before_blocked_begin, {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            })

            button = root / "index.html"
            button.write_text(
                ".add-button { background: #1565c0; }\n", encoding="utf-8",
            )
            self.assertIn("background: #1565c0;", button.read_text(encoding="utf-8"))
            result = (
                f"{request}; exact #1565c0 CSS property checked; "
                f"return to {return_task['id']}."
            )
            resumed = self.call(root, "resume", paused["revision"], data={
                "feature_id": feature_id, "mandate_id": mandate_id,
                "detour_summary": result,
            })
            resumed_feature = _selected_feature(resumed)
            self.assertFalse(resumed_feature["paused"])
            self.assertEqual(resumed_feature["detours"][-1]["result"], result)
            self.assertEqual(resumed_feature["detours"][-1]["return_task_id"], return_task["id"])
            self.assertEqual(_mandate(resumed)["id"], mandate_id)
            self.assertEqual(
                [item["id"] for item in resumed_feature["increments"]],
                [item["id"] for item in feature["increments"]],
            )
            self.assertEqual(
                [item["id"] for item in resumed_feature["criteria"]],
                [item["id"] for item in feature["criteria"]],
            )
            self.assertEqual(
                [item["id"] for item in resumed_feature["tasks"]],
                [item["id"] for item in feature["tasks"]],
            )
            self.assertEqual(resumed["execution"], None)
            self.assertEqual(resumed_feature["tasks"][2]["attempts"], 0)
            self.assertEqual(len(_features(resumed)), 1)
            self.assertEqual(_features(resumed)[0]["id"], feature_id)

            refreshed = self.record_check(
                root, resumed["revision"], task1, criterion,
                mandate_id=mandate_id, feature_id=feature_id,
            )
            self.assertEqual(_mandate(refreshed)["next_task"], return_task["id"])
            returned_attempt = self.call(root, "implement", refreshed["revision"], data={
                "feature_id": feature_id, "task_id": return_task["id"],
                "mandate_id": mandate_id, "phase": "begin",
            })
            self.assertEqual(_execution(returned_attempt)["task_id"], return_task["id"])
            self.assertEqual(
                _selected_feature(returned_attempt)["tasks"][1]["attempts"], 1,
            )
            self.assertEqual(
                _selected_feature(returned_attempt)["tasks"][2]["attempts"], 0,
            )

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
            self.assertEqual(raised.exception.code, "ACCEPTANCE_REQUIRED")
            with self.assertRaises(ContinuityError) as raised:
                self.accept_feature(root, result)
            self.assertEqual(raised.exception.code, "ACCEPTANCE_BLOCKED")

            result = self.record_check(root, result["revision"], task, criterion)
            verified = _selected_feature(result)
            verified_check = _criterion_check(verified["criteria"][0])
            self.assertTrue(verified_check["fresh"])
            self.assertTrue(verified_check["passed"])
            self.assertEqual(verified["tasks"][0]["status"], "COMPLETED")
            accepted = self.accept_feature(root, result)
            result = self.close_accepted(root, accepted)
            self.assertEqual(_selected_feature(result)["status"], "CLOSED")
            self.assertEqual(len(_selected_feature(result)["closure_history"]), 1)

            change_data: dict[str, object] = {
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
            }
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "change", result["revision"], data=change_data)
            self.assertEqual(raised.exception.code, "REOPEN_INTENT_REQUIRED")
            reopened = self.call(root, "reopen", result["revision"], data={
                "feature_id": feature["id"],
                "reason": "The follow-up outcome needs a new Feature cycle.",
            })
            result = self.call(root, "change", reopened["revision"], data=change_data)
            feature = _selected_feature(result)
            self.assertEqual(feature["status"], "OPEN")
            self.assertEqual(len(feature["closure_history"]), 1)
            self.assertEqual(len(feature["reopen_history"]), 1)
            changed_check = _criterion_check(feature["criteria"][0])
            self.assertFalse(changed_check["fresh"])
            self.assertEqual(
                changed_check.get("stale_reason"), "criterion_changed",
            )
            with self.assertRaises(ContinuityError) as raised:
                self.accept_feature(root, result)
            self.assertEqual(raised.exception.code, "ACCEPTANCE_BLOCKED")
            receipt_count = len(json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8")
            )["checks"])
            self.assertEqual(receipt_count, 1)

    def test_artifact_presence_is_core_observed_exact_and_freshness_bound(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Artifact evidence"),
                "criteria": [
                    {
                        "description": "The release manifest exists.",
                        "check_kind": "artifact-present",
                        "check_target": "dist/manifest.json",
                    },
                    {
                        "description": "The integration report exists.",
                        "check_kind": "artifact-present",
                        "check_target": "dist/integration.json",
                    },
                ],
                "increments": [],
                "tasks": [{
                    "title": "Produce the release outputs",
                    "criteria": [0, 1],
                }],
            })
            feature = _selected_feature(result)
            task = feature["tasks"][0]
            criterion = feature["criteria"][0]
            data = {
                "criterion_id": criterion["id"],
                "check_kind": criterion["check_kind"],
                "check_target": criterion["check_target"],
                "task_id": task["id"],
            }
            state_path = root / ".evospec" / "continuity.json"
            before = state_path.read_bytes()
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "verify", result["revision"], data={
                    **data, "artifact_present": True,
                })
            self.assertEqual(raised.exception.code, "INVALID_REQUEST")
            self.assertEqual(state_path.read_bytes(), before)

            missing = self.call(root, "verify", result["revision"], data=data)
            feature = _selected_feature(missing)
            failed = _criterion_check(feature["criteria"][0])
            self.assertEqual(failed.get("status"), "FAILED")
            self.assertFalse(failed["passed"])
            self.assertEqual(failed.get("source"), "CORE_OBSERVED")
            self.assertFalse(failed.get("artifact_present"))
            self.assertNotIn("artifact_sha256", failed)
            self.assertEqual(
                feature["criteria"][1]["check"],
                None,
                "one artifact observation cannot satisfy another criterion",
            )
            persisted = json.loads(state_path.read_text(encoding="utf-8"))["checks"][0]
            self.assertEqual(persisted["evidence_status"], "FAILED")
            self.assertEqual(persisted["feature_id"], feature["id"])
            self.assertEqual(persisted["feature_cycle"], feature["cycle"])
            self.assertEqual(persisted["purpose"], criterion["description"])
            self.assertEqual(persisted["input_snapshot_before"], persisted["observed_input_scope"])
            self.assertEqual(persisted["input_snapshot_after"], persisted["observed_input_scope"])
            self.assertNotIn("exit_code", persisted)
            self.assertNotIn("output_sha256", persisted)

            artifact = root / "dist" / "manifest.json"
            artifact.parent.mkdir()
            artifact.write_text('{"release": true}\n', encoding="utf-8")
            stale = self.call(root, "status", None)
            stale_check = _criterion_check(_selected_feature(stale)["criteria"][0])
            self.assertEqual(stale_check.get("status"), "STALE")
            self.assertFalse(stale_check["fresh"])
            self.assertEqual(stale_check.get("stale_reason"), "input_changed")

            verified = self.call(root, "verify", stale["revision"], data=data)
            verified_feature = _selected_feature(verified)
            receipt = _criterion_check(verified_feature["criteria"][0])
            self.assertEqual(receipt.get("status"), "VERIFIED")
            self.assertTrue(receipt["fresh"])
            self.assertTrue(receipt["passed"])
            self.assertTrue(receipt.get("artifact_present"))
            self.assertEqual(receipt.get("artifact_sha256"), sha256(artifact.read_bytes()).hexdigest())
            self.assertEqual(receipt.get("method"), "artifact-present")
            self.assertEqual(receipt.get("descriptor"), "dist/manifest.json")

    def test_artifact_descriptor_rejects_traversal_and_core_paths(self) -> None:
        for target in (
            "../outside.txt", "/outside.txt", r"dist\report.json",
            ".evospec/continuity.json", ".git/config",
        ):
            with self.subTest(target=target), self._temp() as temporary:
                with self.assertRaises(ContinuityError) as raised:
                    self.call(Path(temporary), "specify", 0, data={
                        **spec_request("Unsafe artifact descriptor"),
                        "criteria": [{
                            "description": "This target must be rejected.",
                            "check_kind": "artifact-present",
                            "check_target": target,
                        }],
                    })
                self.assertEqual(raised.exception.code, "INVALID_REQUEST")

    def test_malformed_artifact_receipt_is_rejected_on_read(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            artifact = root / "dist" / "manifest.json"
            artifact.parent.mkdir()
            artifact.write_text('{"ok": true}\n', encoding="utf-8")
            result = self.call(root, "specify", 0, data={
                **spec_request("Malformed artifact history"),
                "criteria": [{
                    "description": "The manifest exists.",
                    "check_kind": "artifact-present",
                    "check_target": "dist/manifest.json",
                }],
                "increments": [],
                "tasks": [{"title": "Produce the manifest", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            criterion, task = feature["criteria"][0], feature["tasks"][0]
            self.call(root, "verify", result["revision"], data={
                "criterion_id": criterion["id"],
                "check_kind": criterion["check_kind"],
                "check_target": criterion["check_target"],
                "task_id": task["id"],
            })
            state_path = root / ".evospec" / "continuity.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["checks"][0]["check_target"] = "../outside.json"
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            with self.assertRaises(ContinuityError) as raised:
                ContinuityPersistence(root).load()
            self.assertEqual(raised.exception.code, "CORRUPT_CONTINUITY")

    def test_review_reports_remain_reported_and_descriptive_methods_do_not_execute(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Review is not a functional check"),
                "criteria": [{
                    "description": "A reviewer considers the implementation safe.",
                    "check_kind": "review",
                    "check_target": "Human review of the implementation",
                }],
                "increments": [],
                "tasks": [{"title": "Implement the behavior", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            reported = self.call(root, "verify", result["revision"], data={
                "criterion_id": criterion["id"],
                "check_kind": "review",
                "check_target": criterion["check_target"],
                "task_id": task["id"],
                "report": "Reviewer says the change looks safe.",
            })
            check = _criterion_check(_selected_feature(reported)["criteria"][0])
            self.assertEqual(check.get("status"), "REPORTED")
            self.assertFalse(check["passed"])
            self.assertEqual(check.get("source"), "CALLER_REPORTED")
            self.assertEqual(check.get("report"), "Reviewer says the change looks safe.")
            self.assertNotIn("exit_code", check)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "close", reported["revision"])
            self.assertEqual(raised.exception.code, "ACCEPTANCE_REQUIRED")
            self.assertTrue(
                _selected_feature(reported)["pending_review_decisions"][0]["decision_required"],
            )
            with self.assertRaises(ContinuityError) as raised:
                self.accept_feature(root, reported)
            self.assertEqual(raised.exception.code, "ACCEPTANCE_BLOCKED")

            descriptive = self.call(root, "specify", reported["revision"], data={
                **spec_request("Descriptive functional method"),
                "criteria": [{
                    "description": "The user-visible behavior works.",
                    "check_kind": "functional",
                    "check_target": "Exercise the documented user journey",
                }],
                "increments": [],
                "tasks": [{"title": "Implement the behavior", "criteria": [0]}],
            })
            feature = _selected_feature(descriptive)
            criterion = feature["criteria"][0]
            task = feature["tasks"][0]
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "verify", descriptive["revision"], data={
                    "criterion_id": criterion["id"],
                    "check_kind": "functional",
                    "check_target": criterion["check_target"],
                    "task_id": task["id"],
                    "exit_code": 0,
                    "output_sha256": sha256(b"pretend functional pass").hexdigest(),
                })
            self.assertEqual(raised.exception.code, "UNSUPPORTED_EVIDENCE_METHOD")

    def test_explicit_acceptance_closes_with_either_mandate_close_intent(self) -> None:
        for close_feature in (False, True):
            with self.subTest(close_feature=close_feature), self._temp() as temporary:
                root = Path(temporary)
                created = self.call(root, "specify", 0, data={
                    **spec_request(f"Explicit acceptance {close_feature}"),
                    "increments": [],
                    "tasks": [{"title": "Complete the accepted Task", "criteria": [0]}],
                })
                feature = _selected_feature(created)
                task, criterion = feature["tasks"][0], feature["criteria"][0]
                started = self.call(root, "implement", created["revision"], data={
                    "feature_id": feature["id"], "phase": "start", "mode": "ALL",
                    "close_feature": close_feature,
                })
                mandate_id = _mandate_id(started)
                began = self.call(root, "implement", started["revision"], data={
                    "feature_id": feature["id"], "task_id": task["id"],
                    "phase": "begin", "mandate_id": mandate_id,
                })
                reported = self.call(root, "implement", began["revision"], data={
                    "feature_id": feature["id"], "task_id": task["id"],
                    "phase": "report", "result": "SUCCEEDED",
                    "report": "The Task result is caller-reported.",
                    "mandate_id": mandate_id,
                })
                verified = self.record_check(
                    root, reported["revision"], task, criterion,
                    mandate_id=mandate_id, feature_id=feature["id"],
                )
                current = _selected_feature(verified)
                self.assertIsNone(current["acceptance"])
                self.assertEqual(current["close_readiness"]["status"], "ACCEPTANCE_REQUIRED")
                with self.assertRaises(ContinuityError) as raised:
                    self.call(root, "close", verified["revision"], data={
                        "feature_id": feature["id"], "mandate_id": mandate_id,
                    })
                self.assertEqual(raised.exception.code, "ACCEPTANCE_REQUIRED")
                self.assertEqual(self.call(root, "status", None)["revision"], verified["revision"])

                accepted = self.accept_feature(root, verified, mandate_id=mandate_id)
                acceptance = _selected_feature(accepted)["acceptance"]
                if not _record(acceptance):
                    self.fail("expected a revision-bound explicit acceptance")
                acceptance_criteria = acceptance.get("criteria")
                acceptance_tasks = acceptance.get("tasks")
                if (
                    not _record_views(acceptance_criteria)
                    or not acceptance_criteria
                    or not _record_views(acceptance_tasks)
                    or not acceptance_tasks
                ):
                    self.fail("expected acceptance evidence for criteria and Tasks")
                self.assertEqual(acceptance["status"], "CURRENT")
                self.assertEqual(acceptance["feature_id"], feature["id"])
                self.assertEqual(acceptance["cycle"], 1)
                self.assertEqual(acceptance["mandate_id"], mandate_id)
                self.assertEqual(acceptance["basis_revision"], verified["revision"])
                self.assertEqual(acceptance["revision"], accepted["revision"])
                self.assertEqual(acceptance["session_id"], "ses_ac0_primary_123")
                self.assertEqual(
                    acceptance_criteria[0].get("check_ref"),
                    _criterion_check(
                        _selected_feature(verified)["criteria"][0],
                    ).get("evidence_ref"),
                )
                attempt_history = _selected_feature(verified)["tasks"][0].get(
                    "attempt_history",
                )
                if (
                    not isinstance(attempt_history, list)
                    or not attempt_history
                    or not _record(attempt_history[-1])
                ):
                    self.fail("expected a completed Task attempt in the projection")
                self.assertEqual(
                    acceptance_tasks[0].get("evidence_ref"),
                    attempt_history[-1].get("evidence_ref"),
                )

                closed = self.close_accepted(
                    root, accepted, mandate_id=mandate_id,
                )
                closed_feature = _selected_feature(closed)
                self.assertEqual(closed_feature["status"], "CLOSED")
                closed_acceptance = closed_feature["acceptance"]
                if not _record(closed_acceptance):
                    self.fail("expected the closure to consume its acceptance")
                self.assertEqual(closed_acceptance.get("status"), "CONSUMED")
                persisted = json.loads(
                    (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
                )
                self.assertEqual(persisted["mandates"][feature["id"]]["id"], mandate_id)
                self.assertEqual(persisted["mandates"][feature["id"]]["close_feature"], close_feature)
                self.assertEqual(len(persisted.get("mandate_history", [])), 0)
                self.assertEqual(persisted["task_states"][task["id"]]["attempts"], 1)
                closure = persisted["closures"][-1]
                self.assertEqual(closure["acceptance_id"], acceptance["id"])
                self.assertEqual(closure["mandate_id"], mandate_id)
                self.assertEqual(closure["basis_revision"], verified["revision"])

    def test_acceptance_is_explicit_for_no_mandate_and_stales_after_new_evidence(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("No mandate acceptance"),
                "increments": [],
                "tasks": [{"title": "Complete the Task", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            result = self.report_success(root, result, task)
            verified = self.record_check(root, result["revision"], task, criterion)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "close", verified["revision"], data={
                    "feature_id": feature["id"],
                })
            self.assertEqual(raised.exception.code, "ACCEPTANCE_REQUIRED")

            accepted = self.accept_feature(root, verified)
            first_acceptance = _selected_feature(accepted)["acceptance"]
            if not _record(first_acceptance):
                self.fail("expected an explicit baseline acceptance")
            self.assertIsNone(first_acceptance["mandate_id"])
            self.assertEqual(first_acceptance["status"], "CURRENT")
            updated_evidence = self.record_check(
                root, accepted["revision"],
                _selected_feature(accepted)["tasks"][0],
                _selected_feature(accepted)["criteria"][0],
            )
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "close", updated_evidence["revision"], data={
                    "feature_id": feature["id"],
                    "acceptance_id": first_acceptance["id"],
                })
            self.assertEqual(raised.exception.code, "ACCEPTANCE_STALE")

            current_acceptance = self.accept_feature(root, updated_evidence)
            closed = self.close_accepted(root, current_acceptance)
            state = json.loads(
                (root / ".evospec" / "continuity.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(len(state["acceptances"]), 2)
            self.assertEqual(
                state["closures"][-1]["acceptance_id"],
                state["acceptances"][-1]["id"],
            )
            self.assertEqual(_selected_feature(closed)["status"], "CLOSED")

    def test_accept_requires_an_explicit_feature_id_without_mutating_state(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Explicit acceptance target"),
                "increments": [],
                "tasks": [{"title": "Complete the Task", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            reported = self.report_success(root, result, task)
            verified = self.record_check(
                root, reported["revision"], task, criterion,
            )
            state_path = root / ".evospec" / "continuity.json"
            before = state_path.read_bytes()

            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "accept", verified["revision"], data={
                    "reason": "The current work is accepted.",
                })

            self.assertEqual(raised.exception.code, "FEATURE_REQUIRED")
            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(self.call(root, "status", None)["revision"], verified["revision"])

            accepted = self.accept_feature(root, verified)
            acceptance = _selected_feature(accepted).get("acceptance")
            if not _record(acceptance):
                self.fail("expected a recorded explicit acceptance")
            self.assertEqual(acceptance["feature_id"], feature["id"])
            closed = self.close_accepted(root, accepted)
            self.assertEqual(_selected_feature(closed)["status"], "CLOSED")

    def test_close_rechecks_accepted_input_at_commit_admission(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            fixture = root / "src" / "close-guard.txt"
            fixture.parent.mkdir()
            fixture.write_text("accepted input\n", encoding="utf-8")
            result = self.call(root, "specify", 0, data={
                **spec_request("Close input guard"),
                "increments": [],
                "tasks": [{"title": "Complete the guarded Task", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            reported = self.report_success(root, result, task)
            verified = self.record_check(
                root, reported["revision"], task, criterion,
            )
            accepted = self.accept_feature(root, verified)
            acceptance = _selected_feature(accepted)["acceptance"]
            if not _record(acceptance):
                self.fail("expected a recorded explicit acceptance")
            state_path = root / ".evospec" / "continuity.json"
            state_before = state_path.read_bytes()
            managed_before = {
                path.relative_to(root): path.read_bytes()
                for path in (root / "specs").rglob("*") if path.is_file()
            }
            snapshot_calls = 0

            def change_input_after_close_evaluation(
                scan_root: Path, repository_id: str,
            ):
                nonlocal snapshot_calls
                snapshot_calls += 1
                snapshot = repository_input_snapshot(scan_root, repository_id)
                if snapshot_calls == 2:
                    fixture.write_text("changed after close evaluation\n", encoding="utf-8")
                return snapshot

            with patch(
                "evospec.continuity.repository_input_snapshot",
                side_effect=change_input_after_close_evaluation,
            ):
                with self.assertRaises(ContinuityError) as raised:
                    self.close_accepted(root, accepted)

            self.assertEqual(raised.exception.code, "ACCEPTANCE_STALE")
            self.assertEqual(snapshot_calls, 3)
            self.assertEqual(fixture.read_text(encoding="utf-8"), "changed after close evaluation\n")
            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(
                managed_before,
                {
                    path.relative_to(root): path.read_bytes()
                    for path in (root / "specs").rglob("*") if path.is_file()
                },
            )
            persisted = json.loads(state_before)
            self.assertEqual(persisted["features"][feature["id"]]["status"], "OPEN")
            self.assertEqual(persisted["closures"], [])
            self.assertEqual(len(persisted["acceptances"]), 1)
            self.assertEqual(len(persisted["attempts"]), 1)
            self.assertEqual(len(persisted["checks"]), 1)
            self.assertEqual(self.call(root, "status", None)["revision"], accepted["revision"])

            fixture.write_text("accepted input\n", encoding="utf-8")
            snapshot_calls = 0

            def unavailable_at_commit_admission(
                scan_root: Path, repository_id: str,
            ):
                nonlocal snapshot_calls
                snapshot_calls += 1
                if snapshot_calls == 3:
                    raise InputFingerprintError("injected unavailable input")
                return repository_input_snapshot(scan_root, repository_id)

            with patch(
                "evospec.continuity.repository_input_snapshot",
                side_effect=unavailable_at_commit_admission,
            ):
                with self.assertRaises(ContinuityError) as raised:
                    self.close_accepted(root, accepted)

            self.assertEqual(raised.exception.code, "CLOSE_BLOCKED")
            self.assertEqual(snapshot_calls, 3)
            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(
                managed_before,
                {
                    path.relative_to(root): path.read_bytes()
                    for path in (root / "specs").rglob("*") if path.is_file()
                },
            )
            self.assertEqual(self.call(root, "status", None)["revision"], accepted["revision"])

    def test_review_report_requires_an_explicit_reasoned_decision(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Explicit review decision"),
                "criteria": [{
                    "description": "A reviewer assesses the change.",
                    "check_kind": "review",
                    "check_target": "Review the implementation",
                }],
                "increments": [],
                "tasks": [{"title": "Implement and review the change", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            result = self.report_success(root, result, task)
            reported = self.call(root, "verify", result["revision"], data={
                "feature_id": feature["id"], "criterion_id": criterion["id"],
                "check_kind": "review", "check_target": criterion["check_target"],
                "task_id": task["id"], "report": "The review report is caller supplied.",
            })
            reported_feature = _selected_feature(reported)
            report = _criterion_check(reported_feature["criteria"][0])
            self.assertEqual(report.get("status"), "REPORTED")
            self.assertFalse(report["passed"])
            self.assertEqual(reported_feature["tasks"][0]["status"], "REPORTED")
            with self.assertRaises(ContinuityError) as raised:
                self.accept_feature(root, reported)
            self.assertEqual(raised.exception.code, "ACCEPTANCE_BLOCKED")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "review_decision", reported["revision"], data={
                    "feature_id": feature["id"], "criterion_id": criterion["id"],
                    "evidence_ref": "not-the-current-review",
                    "decision": "ACCEPTED", "reason": "Wrong report.",
                })
            self.assertEqual(raised.exception.code, "REVIEW_EVIDENCE_STALE")

            rejected = self.call(root, "review_decision", reported["revision"], data={
                "feature_id": feature["id"], "criterion_id": criterion["id"],
                "evidence_ref": report.get("evidence_ref"),
                "decision": "REJECTED", "reason": "The report does not resolve the concern.",
            })
            self.assertEqual(_selected_feature(rejected)["tasks"][0]["status"], "REPORTED")
            with self.assertRaises(ContinuityError) as raised:
                self.accept_feature(root, rejected)
            self.assertEqual(raised.exception.code, "ACCEPTANCE_BLOCKED")

            decided = self.call(root, "review_decision", rejected["revision"], data={
                "feature_id": feature["id"], "criterion_id": criterion["id"],
                "evidence_ref": report.get("evidence_ref"),
                "decision": "ACCEPTED", "reason": "The subjective review is explicitly accepted.",
            })
            decided_feature = _selected_feature(decided)
            self.assertEqual(decided_feature["tasks"][0]["status"], "COMPLETED")
            self.assertFalse(_criterion_check(decided_feature["criteria"][0])["passed"])
            review_decision = decided_feature["criteria"][0].get("review_decision")
            if not _record(review_decision):
                self.fail("expected the accepted review decision in the projection")
            self.assertEqual(review_decision.get("decision"), "ACCEPTED")
            accepted = self.accept_feature(root, decided)
            acceptance = _selected_feature(accepted)["acceptance"]
            if not _record(acceptance):
                self.fail("expected acceptance after the explicit review decision")
            acceptance_criteria = acceptance.get("criteria")
            if not _record_views(acceptance_criteria) or not acceptance_criteria:
                self.fail("expected accepted review evidence")
            self.assertEqual(
                acceptance_criteria[0].get("evidence_status"), "REPORTED",
            )
            self.assertEqual(
                acceptance_criteria[0].get("review_decision_id"),
                review_decision.get("id"),
            )
            closed = self.close_accepted(root, accepted)
            self.assertEqual(_selected_feature(closed)["status"], "CLOSED")

    def test_acceptance_rechecks_unknown_effects_pause_and_incomplete_tasks(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            created = self.call(root, "specify", 0, data={
                **spec_request("Acceptance safety gates"),
                "increments": [],
                "tasks": [{"title": "Resolve the interrupted Task", "criteria": [0]}],
            })
            feature = _selected_feature(created)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            unknown = self.call(root, "implement", created["revision"], data={
                "feature_id": feature["id"], "task_id": task["id"],
                "phase": "begin",
            })
            with self.assertRaises(ContinuityError) as raised:
                self.accept_feature(root, unknown)
            self.assertEqual(raised.exception.code, "ACCEPTANCE_BLOCKED")
            self.assertEqual(self.call(root, "status", None)["revision"], unknown["revision"])

            reconciled = self.call(root, "resume", unknown["revision"], data={
                "feature_id": feature["id"], "task_id": task["id"],
                "reconcile": "INCOMPLETE",
                "evidence_ref": "Observed that the interrupted Task did not finish.",
            })
            reported = self.report_success(
                root, reconciled, _selected_feature(reconciled)["tasks"][0],
            )
            verified = self.record_check(
                root, reported["revision"],
                _selected_feature(reported)["tasks"][0],
                _selected_feature(reported)["criteria"][0],
            )
            paused = self.call(root, "pause", verified["revision"], data={
                "feature_id": feature["id"], "reason": "Wait for an explicit review.",
            })
            with self.assertRaises(ContinuityError) as raised:
                self.accept_feature(root, paused)
            self.assertEqual(raised.exception.code, "ACCEPTANCE_BLOCKED")
            resumed = self.call(root, "resume", paused["revision"], data={
                "feature_id": feature["id"],
            })
            accepted = self.accept_feature(root, resumed)
            closed = self.close_accepted(root, accepted)
            self.assertEqual(_selected_feature(closed)["status"], "CLOSED")
            self.assertEqual(_selected_feature(closed)["tasks"][0]["attempts"], 2)

    def test_evidence_from_prior_feature_cycle_is_stale(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Cycle-bound evidence"),
                "increments": [],
                "tasks": [{"title": "Implement behavior", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            result = self.report_success(root, result, task)
            result = self.record_check(root, result["revision"], task, criterion)
            accepted = self.accept_feature(root, result)
            result = self.close_accepted(root, accepted)
            state_path = root / ".evospec" / "continuity.json"
            state_before = state_path.read_bytes()
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "reopen", result["revision"], data={
                    "reason": "The target must not be inferred from selection.",
                })
            self.assertEqual(raised.exception.code, "FEATURE_REQUIRED")
            self.assertEqual(state_path.read_bytes(), state_before)
            unchanged = _selected_feature(self.call(root, "status", None))
            self.assertEqual(unchanged["status"], "CLOSED")
            self.assertEqual(unchanged["cycle"], 1)
            self.assertEqual(unchanged["reopen_history"], [])
            self.assertEqual(self.call(root, "status", None)["revision"], result["revision"])
            reopened = self.call(root, "reopen", result["revision"], data={
                "feature_id": feature["id"],
                "reason": "The same criterion needs work in a new Feature cycle.",
            })
            changed = self.call(root, "change", reopened["revision"], data={
                "feature_id": feature["id"],
                "task_title": "Implement the same criterion again",
                "task_description": "The next Feature cycle keeps the same definition.",
                "criteria_ids": [criterion["id"]],
            })
            current = _selected_feature(changed)
            self.assertEqual(current["cycle"], 2)
            check = _criterion_check(current["criteria"][0])
            self.assertEqual(check.get("status"), "STALE")
            self.assertFalse(check["passed"])
            self.assertEqual(check.get("stale_reason"), "feature_cycle_changed")

    def test_input_snapshot_is_read_only_and_preserves_the_exact_revision(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data=spec_request())
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }

            snapshot = execute(
                root, "input_snapshot", result["revision"], "ses_ac0_primary_123",
            )

            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            self.assertEqual(snapshot["revision"], result["revision"])
            input_snapshot = snapshot.get("input_snapshot")
            if not _record(input_snapshot):
                self.fail("expected a Core input snapshot")
            self.assertEqual(
                set(input_snapshot),
                {"policy_id", "policy_digest", "fingerprint"},
            )
            self.assertEqual(before, after)

            changed = self.call(root, "plan", result["revision"], data={"plan": "Updated plan"})
            with self.assertRaises(ContinuityError) as raised:
                self.input_snapshot(root, result["revision"])
            self.assertEqual(raised.exception.code, "STALE_REVISION")
            self.assertEqual(changed["revision"], result["revision"] + 1)

    def test_historical_receipt_shape_remains_readable_but_never_counts_as_fresh(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Legacy receipt"),
                "increments": [],
                "tasks": [{"title": "Implement behavior", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            result = self.report_success(root, result, task)
            result = self.record_check(root, result["revision"], task, criterion)
            state_path = root / ".evospec" / "continuity.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["checks"][0].pop("observed_input_scope")
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )

            current = self.call(root, "status", None)
            feature = _selected_feature(current)
            check = _criterion_check(feature["criteria"][0])
            self.assertFalse(check["fresh"])
            self.assertFalse(check["passed"])
            self.assertTrue(check.get("historical_passed"))
            self.assertEqual(check.get("stale_reason"), "legacy_receipt")
            self.assertEqual(feature["tasks"][0]["status"], "REPORTED")

    def test_unavailable_input_snapshot_keeps_history_but_fails_freshness_closed(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Unavailable snapshot"),
                "increments": [],
                "tasks": [{"title": "Implement behavior", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            result = self.report_success(root, result, task)
            result = self.record_check(root, result["revision"], task, criterion)

            with patch(
                "evospec.continuity.repository_input_snapshot",
                side_effect=InputFingerprintError("fixture input scan failure"),
            ):
                current = self.call(root, "status", None)
            feature = _selected_feature(current)
            check = _criterion_check(feature["criteria"][0])
            self.assertFalse(check["fresh"])
            self.assertFalse(check["passed"])
            self.assertTrue(check.get("historical_passed"))
            self.assertEqual(check.get("stale_reason"), "input_snapshot_unavailable")
            input_snapshot_error = current["input_snapshot_error"]
            if input_snapshot_error is None:
                self.fail("expected the input snapshot scan error")
            self.assertIn("fixture input scan failure", input_snapshot_error)
            self.assertEqual(feature["tasks"][0]["status"], "REPORTED")

    def test_verify_rejects_different_before_and_after_input_snapshots(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Changing inputs"),
                "increments": [],
                "tasks": [{"title": "Implement behavior", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            implementation = root / "implementation.py"
            implementation.write_text("value = 1\n", encoding="utf-8")
            result = self.report_success(root, result, task)
            before = self.input_snapshot(root, result["revision"])
            implementation.write_text("value = 2\n", encoding="utf-8")
            after = self.input_snapshot(root, result["revision"])
            state_path = root / ".evospec" / "continuity.json"
            state_before = state_path.read_bytes()

            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "verify", result["revision"], data={
                    "criterion_id": criterion["id"],
                    "check_kind": criterion["check_kind"],
                    "check_target": criterion["check_target"],
                    "exit_code": 0,
                    "output_sha256": sha256(b"passing check").hexdigest(),
                    "task_id": task["id"],
                    "input_snapshot_before": before,
                    "input_snapshot_after": after,
                })

            self.assertEqual(raised.exception.code, "STALE_INPUT")
            self.assertEqual(state_path.read_bytes(), state_before)

    def test_implementation_input_change_invalidates_a_passing_check(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Implementation freshness"),
                "increments": [],
                "tasks": [{"title": "Implement behavior", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            implementation = root / "src" / "implementation.py"
            implementation.parent.mkdir()
            implementation.write_text("value = 1\n", encoding="utf-8")

            result = self.report_success(root, result, task)
            result = self.record_check(root, result["revision"], task, criterion)
            implementation.write_text("value = 2\n", encoding="utf-8")

            current = self.call(root, "status", None)
            feature = _selected_feature(current)
            check = _criterion_check(feature["criteria"][0])
            self.assertFalse(check["fresh"])
            self.assertFalse(check["passed"])
            self.assertEqual(feature["tasks"][0]["status"], "REPORTED")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "close", current["revision"])
            self.assertEqual(raised.exception.code, "ACCEPTANCE_REQUIRED")
            with self.assertRaises(ContinuityError) as raised:
                self.accept_feature(root, current)
            self.assertEqual(raised.exception.code, "ACCEPTANCE_BLOCKED")

    def test_test_bytes_change_invalidates_a_passing_check_under_the_same_descriptor(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Test freshness"),
                "increments": [],
                "tasks": [{"title": "Implement behavior", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            task, criterion = feature["tasks"][0], feature["criteria"][0]
            test_file = root / "tests" / "test_sample.py"
            test_file.parent.mkdir()
            test_file.write_text("def test_value(): assert 1 == 1\n", encoding="utf-8")

            result = self.report_success(root, result, task)
            result = self.record_check(root, result["revision"], task, criterion)
            test_file.write_text("def test_value(): assert 1 == 2\n", encoding="utf-8")

            current = self.call(root, "status", None)
            feature = _selected_feature(current)
            check = _criterion_check(feature["criteria"][0])
            self.assertFalse(check["fresh"])
            self.assertFalse(check["passed"])
            self.assertEqual(feature["tasks"][0]["status"], "REPORTED")

    def test_new_task_does_not_inherit_an_existing_criterion_check(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            result = self.call(root, "specify", 0, data={
                **spec_request("Task-bound freshness"),
                "increments": [],
                "tasks": [{"title": "First task", "criteria": [0]}],
            })
            feature = _selected_feature(result)
            first_task, criterion = feature["tasks"][0], feature["criteria"][0]
            result = self.report_success(root, result, first_task)
            result = self.record_check(root, result["revision"], first_task, criterion)

            result = self.call(root, "tasks", result["revision"], data={
                "task_title": "Second task",
                "criteria_ids": [criterion["id"]],
                "depends_on": [],
            })
            new_task = _selected_feature(result)["tasks"][-1]
            result = self.report_success(root, result, new_task)
            feature = _selected_feature(self.call(root, "status", None))
            self.assertEqual(feature["tasks"][-1]["status"], "REPORTED")
            self.assertEqual(
                _criterion_check(feature["criteria"][0])["task_id"], first_task["id"],
            )

            result = self.record_check(root, result["revision"], new_task, criterion)
            feature = _selected_feature(result)
            self.assertEqual(feature["tasks"][-1]["status"], "COMPLETED")

    def test_recovery_journal_resumes_partial_document_publication_without_overwriting_manual_edits(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "specify", 0, data=spec_request(), operations=FailReplace(3))
            self.assertEqual(raised.exception.code, "RECOVERY_INCOMPLETE")
            pending = self.call(root, "status", None)
            self.assertEqual(pending["status"], "RECOVERY_REQUIRED")
            journal = json.loads((root / ".evospec" / "continuity.json").read_text(encoding="utf-8"))
            published = next(item for item in journal["pending"]["writes"] if item["path"].endswith("/spec.md"))
            spec = root / Path(published["path"])
            spec.write_text("manual concurrent edit", encoding="utf-8")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", 0)
            self.assertEqual(raised.exception.code, "DOCUMENT_DIVERGED")
            self.assertEqual(spec.read_text(encoding="utf-8"), "manual concurrent edit")

    def test_publication_uncertain_third_version_uses_revision_bound_resolution_token(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            created = self.call(root, "specify", 0, data={
                **spec_request("Journal decision identity"),
                "increments": [],
                "tasks": [{"title": "Keep the admitted checkpoint", "criteria": [0]}],
                "plan": "",
            })
            task = _selected_feature(created)["tasks"][0]
            started = self.call(root, "implement", created["revision"], data={
                "task_id": task["id"], "phase": "begin",
                "report": "Checkpoint before the journaled decision.",
            })
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "clarify", started["revision"], data={
                    "question": "Which recovery decision remains queued?",
                    "decision": "Preserve the explicit decision identity.",
                }, operations=FailReplace(3))
            self.assertEqual(raised.exception.code, "PUBLICATION_UNCERTAIN")

            marker = root / ".evospec" / "continuity.json"
            before = json.loads(marker.read_text(encoding="utf-8"))
            pending = before["pending"]
            self.assertEqual(before["revision"], started["revision"])
            self.assertEqual(pending["next_state"]["revision"], started["revision"] + 1)
            decision_id = next(
                identity for identity in pending["next_state"]["ids"]
                if identity not in before["ids"]
            )
            write = pending["writes"][0]
            self.assertTrue(write["path"].endswith("/decisions.md"))
            self.assertIn(decision_id, write["content"])
            decisions_path = root / Path(write["path"])
            intended = write["content"]
            preserved = intended.replace(
                "<!-- evospec-notes:start -->",
                "<!-- evospec-notes:start -->\nExternal note kept byte-for-byte.\n",
            )
            decisions_path.write_text(preserved, encoding="utf-8")
            status = _continuity_view(execute(
                root, "status", None, "ses_ac0_primary_123",
            ))
            recovery = _publication_recovery(status)
            self.assertEqual(
                recovery["conflict_paths"], [write["path"]],
            )
            self.assertIn("External note kept byte-for-byte.", recovery["conflicts"][0]["current"])
            self.assertIn(decision_id, recovery["conflicts"][0]["intended"])
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", started["revision"])
            self.assertEqual(raised.exception.code, "DOCUMENT_DIVERGED")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "reconcile", started["revision"], data={
                    "feature_id": _selected_feature(created)["id"],
                })
            self.assertEqual(raised.exception.code, "RECOVERY_REQUIRED")

            recovered = self.call(root, "resume", started["revision"], data={
                "publication_resolution": recovery["token"],
                "preserve_current_paths": recovery["conflict_paths"],
            })
            after = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(recovered["revision"], pending["next_state"]["revision"])
            self.assertIsNone(after["pending"])
            self.assertIn(decision_id, after["ids"])
            self.assertEqual(after["attempts"], pending["next_state"]["attempts"])
            self.assertEqual(after["execution"], pending["next_state"]["execution"])
            self.assertEqual(decisions_path.read_text(encoding="utf-8"), preserved)

    def test_pending_third_version_resolution_preserves_note_and_recovers_remaining_writes(self) -> None:
        with self._temp() as temporary:
            root = Path(temporary)
            created = self.call(root, "specify", 0, data={
                **spec_request("Recover a paused checkpoint"),
                "increments": [],
                "tasks": [{"title": "Keep the checkpoint", "criteria": [0]}],
                "plan": "",
            })
            feature = _selected_feature(created)
            task = feature["tasks"][0]
            started = self.call(root, "implement", created["revision"], data={
                "task_id": task["id"], "phase": "begin",
                "report": "Checkpoint before the paused detour.",
            })
            paused = self.call(root, "pause", started["revision"], data={
                "reason": "Record the related interruption.",
                "detour_kind": "RELATED",
                "detour_title": "Inspect the external dependency",
                "return_task_id": task["id"],
            })
            operations = FailReplace(3)
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", paused["revision"], data={
                    "task_id": task["id"],
                    "reconcile": "INCOMPLETE",
                    "evidence_ref": "manual-review:resume-after-detour",
                    "detour_summary": "The dependency was inspected; return to the Task.",
                }, operations=operations)
            self.assertEqual(raised.exception.code, "RECOVERY_INCOMPLETE")
            self.assertEqual(operations.count, 3)

            marker = root / ".evospec" / "continuity.json"
            before_recovery = json.loads(marker.read_text(encoding="utf-8"))
            pending = before_recovery["pending"]
            self.assertIsNotNone(pending)
            self.assertEqual(before_recovery["revision"], paused["revision"])
            self.assertEqual(pending["next_state"]["revision"], paused["revision"] + 1)
            queued_ids = set(pending["next_state"]["ids"]) - set(before_recovery["ids"])
            self.assertEqual(len(queued_ids), 1)
            result_decision_id = queued_ids.pop()
            detour_id = _selected_feature(paused)["detours"][0]["id"]
            self.assertEqual(before_recovery["execution"]["task_id"], task["id"])
            self.assertEqual(
                before_recovery["attempts"][0]["checkpoint"],
                "Checkpoint before the paused detour.",
            )
            next_state = pending["next_state"]
            self.assertIsNone(next_state["execution"])
            self.assertEqual(next_state["attempts"][0]["status"], "RECONCILED_INCOMPLETE")
            self.assertEqual(next_state["attempts"][0]["result_status"], "INCOMPLETE")
            self.assertEqual(
                next_state["attempts"][0]["evidence_ref"],
                "manual-review:resume-after-detour",
            )
            self.assertEqual(
                next_state["attempts"][0]["checkpoint"],
                "Checkpoint before the paused detour.",
            )
            self.assertEqual(next_state["detours"][0]["id"], detour_id)
            self.assertEqual(
                next_state["detours"][0]["result"],
                "The dependency was inspected; return to the Task.",
            )
            self.assertIn(result_decision_id, next_state["ids"])
            decisions_path = root / "specs" / feature["directory"] / "decisions.md"
            task_path = root / "specs" / feature["directory"] / "tasks.md"
            task_bytes_before = task_path.read_bytes()
            decision_write = next(
                item for item in pending["writes"]
                if item["path"].endswith("/decisions.md")
            )
            task_write = next(
                item for item in pending["writes"]
                if item["path"].endswith("/tasks.md")
            )
            self.assertIn(result_decision_id, decision_write["content"])
            self.assertIn(detour_id, decision_write["content"])
            self.assertEqual(
                sha256(decisions_path.read_bytes()).hexdigest(),
                decision_write["sha256"],
                "the first queued Markdown write really published",
            )
            self.assertEqual(task_path.read_bytes(), task_bytes_before)
            self.assertNotEqual(
                task_path.read_bytes(), task_write["content"].encode("utf-8"),
                "the remaining queued Task write really did not publish",
            )

            invalid = decision_write["content"]
            decision_start = invalid.index(f"### `{result_decision_id}`")
            notes_start = invalid.index("<!-- evospec-notes:start -->")
            invalid = (
                invalid[:decision_start]
                + invalid[notes_start:].replace(
                    "<!-- evospec-notes:start -->",
                    "<!-- evospec-notes:start -->\nHuman note kept only in the conflicting version.",
                    1,
                )
            )
            decisions_path.write_bytes(invalid.encode("utf-8"))
            pending_response = _continuity_view(execute(
                root, "status", None, "ses_ac0_primary_123",
            ))
            self.assertEqual(pending_response["status"], "RECOVERY_REQUIRED")
            recovery = _publication_recovery(pending_response)
            self.assertEqual(
                recovery["conflict_paths"],
                [f"specs/{feature['directory']}/decisions.md"],
            )
            conflict = recovery["conflicts"][0]
            self.assertIn(invalid, conflict["current"])
            self.assertIn(result_decision_id, conflict["intended"])
            self.assertEqual(_execution(pending_response)["task_id"], task["id"])
            self.assertEqual(
                before_recovery["attempts"][0]["checkpoint"],
                "Checkpoint before the paused detour.",
            )
            before_bad_attempt = marker.read_bytes()

            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "reconcile", paused["revision"], data={
                    "feature_id": feature["id"],
                })
            self.assertEqual(raised.exception.code, "RECOVERY_REQUIRED")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", paused["revision"])
            self.assertEqual(raised.exception.code, "DOCUMENT_DIVERGED")
            self.assertEqual(marker.read_bytes(), before_bad_attempt)

            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", paused["revision"], data={
                    "publication_resolution": recovery["token"],
                    "preserve_current_paths": ["uv.lock"],
                })
            self.assertEqual(raised.exception.code, "INVALID_REQUEST")
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", paused["revision"], data={
                    "publication_resolution": recovery["token"],
                    "preserve_current_paths": recovery["conflict_paths"],
                })
            self.assertEqual(raised.exception.code, "PENDING_RESOLUTION_UNSAFE")
            self.assertEqual(marker.read_bytes(), before_bad_attempt)
            self.assertNotIn(result_decision_id, decisions_path.read_text(encoding="utf-8"))

            stale_token = recovery["token"]
            preserved = decision_write["content"].replace(
                "<!-- evospec-notes:start -->",
                "<!-- evospec-notes:start -->\nExternal author note retained.\n",
            )
            decisions_path.write_bytes(preserved.encode("utf-8"))
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", paused["revision"], data={
                    "publication_resolution": stale_token,
                    "preserve_current_paths": recovery["conflict_paths"],
                })
            self.assertEqual(raised.exception.code, "STALE_PUBLICATION_RESOLUTION")
            self.assertEqual(marker.read_bytes(), before_bad_attempt)

            fresh_response = _continuity_view(execute(
                root, "status", None, "ses_ac0_primary_123",
            ))
            fresh_recovery = _publication_recovery(fresh_response)
            self.assertNotEqual(fresh_recovery["token"], stale_token)
            self.assertIn("External author note retained.", fresh_recovery["conflicts"][0]["current"])
            with self.assertRaises(ContinuityError) as raised:
                self.call(root, "resume", paused["revision"] - 1, data={
                    "publication_resolution": fresh_recovery["token"],
                    "preserve_current_paths": fresh_recovery["conflict_paths"],
                })
            self.assertEqual(raised.exception.code, "STALE_REVISION")
            self.assertEqual(marker.read_bytes(), before_bad_attempt)

            recovered = self.call(root, "resume", paused["revision"], data={
                "publication_resolution": fresh_recovery["token"],
                "preserve_current_paths": fresh_recovery["conflict_paths"],
            })
            recovered_state = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(recovered["revision"], pending["next_state"]["revision"])
            self.assertIsNone(recovered_state["pending"])
            self.assertEqual(recovered_state["attempts"], pending["next_state"]["attempts"])
            self.assertEqual(recovered_state["execution"], pending["next_state"]["execution"])
            self.assertEqual(recovered_state["ids"], pending["next_state"]["ids"])
            self.assertEqual(
                recovered_state["detours"], pending["next_state"]["detours"],
            )
            self.assertIn(detour_id, recovered_state["ids"])
            self.assertIn(result_decision_id, recovered_state["ids"])
            self.assertEqual(
                recovered_state["attempts"][0]["checkpoint"],
                "Checkpoint before the paused detour.",
            )
            self.assertEqual(decisions_path.read_bytes(), preserved.encode("utf-8"))
            self.assertEqual(task_path.read_bytes(), task_write["content"].encode("utf-8"))
            self.assertIn(
                f"specs/{feature['directory']}/decisions.md",
                _document_changes(recovered)["format_notes"],
            )
            self.assertEqual(recovered_state["checks"], [])
            self.assertEqual(
                _selected_feature(recovered)["tasks"][0]["status"], "READY",
                "INCOMPLETE recovery does not auto-pass or complete the Task",
            )

            reconciled = self.call(root, "reconcile", recovered["revision"], data={
                "feature_id": feature["id"],
            })
            self.assertEqual(
                _selected_feature(reconciled)["tasks"][0]["status"], "READY",
            )
            self.assertNotIn(
                f"specs/{feature['directory']}/decisions.md",
                _document_changes(reconciled)["format_notes"],
            )
            continued = self.call(root, "implement", reconciled["revision"], data={
                "task_id": task["id"], "phase": "begin",
            })
            self.assertEqual(
                _execution(continued)["task_id"], task["id"],
            )
            self.assertEqual(
                _selected_feature(continued)["tasks"][0]["status"], "OUTCOME_UNKNOWN",
            )
            continued_state = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(continued_state["attempts"][0]["status"], "RECONCILED_INCOMPLETE")
            self.assertEqual(continued_state["attempts"][1]["attempt"], 2)
            self.assertEqual(continued_state["checks"], [])
            self.assertEqual(
                _selected_feature(continued)["criteria"][0]["check"], None,
                "recovery and a new begin do not produce a passing check",
            )

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
