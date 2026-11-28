"""Composed, document-backed work continuity for fresh projects."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Final, NotRequired, TypeVar, TypedDict, cast
from uuid import uuid4

from .continuity_documents import (
    decisions_write as _write_decisions,
)
from .continuity_documents import (
    parse_document_content as _parse_document_content,
)
from .continuity_documents import (
    parse_all as _parse_all_documents,
)
from .continuity_documents import (
    replace_aliases as _replace_document_aliases,
)
from .continuity_documents import (
    refresh_generated_checklist as _refresh_generated_checklist,
)
from .continuity_documents import (
    semantic_digest_for_content as _semantic_digest_for_content,
)
from .continuity_documents import (
    feature_document_writes as _writes_for_feature,
)
from .continuity_documents import (
    index_write as _index_write,
)
from .continuity_documents import (
    index_write_selected as _index_write_selected,
)
from .continuity_documents import (
    initial_documents as _build_initial_documents,
)
from .continuity_documents import (
    new_directory as _new_directory,
)
from .continuity_documents import (
    pending_document_preservation_issue as _pending_document_preservation_issue,
)
from .continuity_documents import (
    pending_index_preservation_issue as _pending_index_preservation_issue,
)
from .continuity_documents import (
    plan_write as _write_plan,
)
from .continuity_documents import (
    read_decisions as _read_decisions,
)
from .continuity_documents import (
    read_feature as _read_feature,
)
from .continuity_documents import (
    read_plan as _read_plan,
)
from .continuity_documents import (
    read_tasks as _read_tasks,
)
from .continuity_documents import (
    spec_write as _write_spec,
)
from .continuity_documents import (
    tasks_write as _write_tasks,
)
from .domain import (
    CONTINUITY_CHECK_METHODS,
    CONTINUITY_INPUT_POLICY_DIGEST,
    CONTINUITY_INPUT_POLICY_ID,
    ContinuityAttempt,
    ContinuityAcceptance,
    ContinuityAcceptanceCriterion,
    ContinuityAcceptanceTask,
    ContinuityCheck,
    ContinuityClosure,
    ContinuityCriterionDocument,
    ContinuityDecisionDocument,
    ContinuityDetour,
    ContinuityError,
    ContinuityExecution,
    ContinuityFeatureDocument,
    ContinuityFeatureIndex,
    ContinuityIdentityMetadata,
    ContinuityIncrementDocument,
    ContinuityInputScope,
    ContinuityMandate,
    ContinuityMandateReference,
    ContinuityReopen,
    ContinuityReviewDecision,
    ContinuityStateData,
    ContinuityTaskEvent,
    ContinuityTaskDocument,
    continuity_check_descriptor_valid,
)
from .input_fingerprint import (
    InputSnapshot,
    InputFingerprintError,
    repository_input_snapshot,
)
from .lifecycle import (
    ContinuityFeatureView,
    ContinuityStateView,
    ContinuityTaskView,
    continuity_close_blockers,
    continuity_current_checks,
    continuity_task_reports,
    continuity_task_eligible,
    continuity_task_status,
)
from .persistence import (
    ContinuityPersistence,
    FileOperations,
)

SCHEMA: Final = 1
_ID = re.compile(r"^[FSTCD]-[0-9a-f]{32}$")
_ALIAS = re.compile(r"^[CSTD]-[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_MAX_FEATURES = 1000
_MAX_TASKS = 1000
_MAX_CRITERIA = 200
_MAX_INCREMENTS = 100
_MAX_DECISIONS = 1000
_COMMANDS = frozenset((
    "specify", "clarify", "plan", "tasks", "implement", "use", "change",
    "status", "input_snapshot", "verify", "review_decision", "accept",
    "pause", "resume", "close", "reopen",
    "reconcile", "upgrade_markdown",
))

_DocumentRecord = TypeVar("_DocumentRecord", bound=Mapping[str, object])


class _ContinuityCheckView(TypedDict):
    criterion_id: str
    criterion_digest: str
    check_kind: str
    check_target: str
    task_id: str | None
    reported_by_session_id: str
    recorded_revision: int
    exit_code: NotRequired[int]
    output_sha256: NotRequired[str]
    observed_input_scope: NotRequired[ContinuityInputScope]
    task_digest: NotRequired[str]
    mandate_id: NotRequired[str]
    feature_id: NotRequired[str]
    feature_cycle: NotRequired[int]
    purpose: NotRequired[str]
    evidence_status: NotRequired[str]
    source: NotRequired[str]
    evidence_ref: NotRequired[str]
    artifact_present: NotRequired[bool]
    artifact_sha256: NotRequired[str]
    report: NotRequired[str]


def _plain_record(record: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    result.update(record)
    return result


def _plain_records(
    records: list[_DocumentRecord],
) -> list[dict[str, object]]:
    return [_plain_record(record) for record in records]


def _initial_documents(
    feature: ContinuityFeatureDocument,
    tasks: list[ContinuityTaskDocument],
    plan: str,
) -> dict[str, dict[str, object]]:
    return _build_initial_documents(
        _plain_record(feature), _plain_records(tasks), plan,
    )


def _render_task_write(
    root: Path, state: ContinuityStateData, feature_id: str,
    tasks: list[ContinuityTaskDocument], feature: ContinuityFeatureDocument,
) -> dict[str, tuple[bytes, str | None]]:
    try:
        input_snapshot = _continuity_input_scope(root, state)
    except ContinuityError:
        render_state = state
    else:
        render_state = cast(
            ContinuityStateData,
            cast(object, {**state, "input_snapshot": input_snapshot}),
        )
    return _write_tasks(
        root, render_state, feature_id, tasks, _plain_record(feature),
    )


def _continuity_input_scope(
    root: Path, state: ContinuityStateData,
) -> ContinuityInputScope:
    """Hash root inputs; omit only Git, Core state, and Core-owned projections."""
    try:
        snapshot = repository_input_snapshot(root, state["repository_id"])
    except InputFingerprintError as error:
        raise ContinuityError("INPUT_SNAPSHOT_UNAVAILABLE", str(error)) from error
    return _continuity_input_scope_from_snapshot(state, snapshot)


def _continuity_input_scope_from_snapshot(
    state: ContinuityStateData, snapshot: InputSnapshot,
) -> ContinuityInputScope:
    excluded = set(state["documents"]) | {"specs/README.md"}
    excluded_directories = {
        path.rsplit("/", 1)[0]
        for path in excluded
        if path.startswith("specs/") and path.count("/") == 2
    }
    payload = {
        "policy_id": CONTINUITY_INPUT_POLICY_ID,
        "policy_digest": CONTINUITY_INPUT_POLICY_DIGEST,
        "repository_id": state["repository_id"],
        "files": [
            [path, digest]
            for path, digest in snapshot.file_sha256
            if path not in excluded
        ],
        "directories": [
            path for path in snapshot.directory_paths
            if path not in excluded_directories
        ],
    }
    fingerprint = sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "policy_id": CONTINUITY_INPUT_POLICY_ID,
        "policy_digest": CONTINUITY_INPUT_POLICY_DIGEST,
        "fingerprint": fingerprint,
    }


def _input_scope_argument(value: object, name: str) -> ContinuityInputScope:
    if type(value) is not dict:
        raise ContinuityError("INVALID_REQUEST", f"{name} is not a valid Core input snapshot")
    if set(value) != {"policy_id", "policy_digest", "fingerprint"}:
        raise ContinuityError("INVALID_REQUEST", f"{name} is not a valid Core input snapshot")
    policy_id = value.get("policy_id")
    policy_digest = value.get("policy_digest")
    fingerprint = value.get("fingerprint")
    if (
        not isinstance(policy_id, str)
        or policy_id != CONTINUITY_INPUT_POLICY_ID
        or not isinstance(policy_digest, str)
        or policy_digest != CONTINUITY_INPUT_POLICY_DIGEST
        or not isinstance(fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
    ):
        raise ContinuityError("INVALID_REQUEST", f"{name} is not a valid Core input snapshot")
    return {
        "policy_id": CONTINUITY_INPUT_POLICY_ID,
        "policy_digest": CONTINUITY_INPUT_POLICY_DIGEST,
        "fingerprint": fingerprint,
    }


def _render_decision_write(
    root: Path, state: ContinuityStateData, feature_id: str,
    decisions: list[ContinuityDecisionDocument],
    feature: ContinuityFeatureDocument,
) -> dict[str, tuple[bytes, str | None]]:
    return _write_decisions(
        root, state, feature_id,
        _plain_records(decisions), _plain_record(feature),
    )


def _render_plan_write(
    root: Path, state: ContinuityStateData, feature_id: str,
    feature: ContinuityFeatureDocument, plan: str,
) -> dict[str, tuple[bytes, str | None]]:
    return _write_plan(root, state, feature_id, _plain_record(feature), plan)


def _render_spec_write(
    root: Path, state: ContinuityStateData, feature_id: str,
    feature: ContinuityFeatureDocument,
) -> dict[str, tuple[bytes, str | None]]:
    return _write_spec(root, state, feature_id, _plain_record(feature))


def _continuity_state_view(
    state: ContinuityStateData,
    input_snapshot: ContinuityInputScope | None = None,
) -> ContinuityStateView:
    execution = state["execution"]
    view: ContinuityStateView = {
        "checks": tuple(_continuity_check_view(check) for check in state["checks"]),
        "attempts": (),
        "execution": None if execution is None else {"task_id": execution["task_id"]},
        "paused_features": tuple(state["paused_features"]),
    }
    view["attempts"] = tuple(state["attempts"])
    view["task_events"] = tuple(state.get("task_events", ()))
    view["review_decisions"] = tuple(state.get("review_decisions", ()))
    view["input_snapshot"] = None if input_snapshot is None else {
        "policy_id": input_snapshot["policy_id"],
        "policy_digest": input_snapshot["policy_digest"],
        "fingerprint": input_snapshot["fingerprint"],
    }
    return view


def _continuity_feature_view(
    feature: ContinuityFeatureDocument,
    state: ContinuityStateData | None = None,
    tasks: list[ContinuityTaskDocument] | None = None,
) -> ContinuityFeatureView:
    view = cast(
        ContinuityFeatureView,
        cast(object, {"criteria": tuple(feature["criteria"])}),
    )
    if state is not None:
        feature_id = feature["id"]
        view["id"] = feature_id
        view["cycle"] = state["features"][feature_id]["cycle"]
        mandate = _feature_mandate(state, feature_id)
        view["mandate_id"] = None if mandate is None else mandate["id"]
        view["mandate_scope_current"] = (
            mandate is None
            or (
                _mandate_scope_matches(mandate, feature)
                and (
                    tasks is None
                    or all(
                        (
                            task["increment_id"] is None
                            or task["increment_id"] in {
                                item["id"] for item in mandate["increments"]
                            }
                        )
                        and set(task["criteria_ids"]) <= {
                            item["id"] for item in mandate["criteria"]
                        }
                        for task in tasks
                    )
                )
            )
        )
    return view


def _continuity_task_view(task: ContinuityTaskDocument) -> ContinuityTaskView:
    return {
        "id": task["id"],
        "title": task["title"],
        "description": task["description"],
        "increment_id": task["increment_id"],
        "criteria_ids": tuple(task["criteria_ids"]),
        "depends_on": tuple(task["depends_on"]),
    }


def _continuity_check_view(check: ContinuityCheck) -> _ContinuityCheckView:
    return cast(_ContinuityCheckView, cast(object, dict(check)))


def _current_review_decision(
    state: ContinuityStateData,
    feature_id: str,
    cycle: int,
    criterion: ContinuityCriterionDocument,
    check: Mapping[str, object],
    mandate_id: str | None,
) -> ContinuityReviewDecision | None:
    for decision in reversed(state.get("review_decisions", [])):
        if (
            decision["feature_id"] == feature_id
            and decision["feature_cycle"] == cycle
            and decision["criterion_id"] == criterion["id"]
            and decision["criterion_digest"] == _digest(criterion)
            and decision["evidence_ref"] == check.get("evidence_ref")
            and decision["task_id"] == check.get("task_id")
            and decision["task_digest"] == check.get("task_digest")
            and decision["mandate_id"] == mandate_id
        ):
            return decision
    return None


def _acceptance_basis(
    state: ContinuityStateData,
    feature_id: str,
    feature: ContinuityFeatureDocument,
    tasks: list[ContinuityTaskDocument],
    input_snapshot: ContinuityInputScope,
    mandate_id: str | None,
) -> tuple[list[ContinuityAcceptanceCriterion], list[ContinuityAcceptanceTask]]:
    feature_view = _continuity_feature_view(feature, state, tasks)
    state_view = _continuity_state_view(state, input_snapshot)
    task_views = tuple(_continuity_task_view(task) for task in tasks)
    checks = continuity_current_checks(state_view, feature_view, tasks=task_views)
    criteria: list[ContinuityAcceptanceCriterion] = []
    for criterion in feature["criteria"]:
        check = checks.get(criterion["id"])
        if check is None:
            raise ContinuityError(
                "ACCEPTANCE_BLOCKED",
                f"criterion {criterion['id']} has no current attributable evidence",
            )
        evidence_status = check.get("status")
        review_decision = (
            _current_review_decision(
                state, feature_id, state["features"][feature_id]["cycle"],
                criterion, check, mandate_id,
            )
            if evidence_status == "REPORTED" and criterion["check_kind"] == "review"
            else None
        )
        if evidence_status != "VERIFIED" and not (
            evidence_status == "REPORTED"
            and review_decision is not None
            and review_decision["decision"] == "ACCEPTED"
        ):
            raise ContinuityError(
                "ACCEPTANCE_BLOCKED",
                f"criterion {criterion['id']} is not verified or explicitly accepted as a review",
            )
        check_ref = check.get("evidence_ref")
        check_revision = check.get("recorded_revision")
        task_id = check.get("task_id")
        task_digest = check.get("task_digest")
        reported_by = check.get("reported_by_session_id")
        if (
            not isinstance(check_ref, str)
            or type(check_revision) is not int
            or not isinstance(task_id, str)
            or not isinstance(task_digest, str)
            or not isinstance(reported_by, str)
            or check.get("mandate_id") != mandate_id
        ):
            raise ContinuityError(
                "ACCEPTANCE_BLOCKED",
                f"criterion {criterion['id']} does not have current revision-bound evidence",
            )
        criteria.append({
            "criterion_id": criterion["id"],
            "criterion_digest": _digest(criterion),
            "evidence_status": cast(str, evidence_status),
            "check_ref": check_ref,
            "check_revision": check_revision,
            "task_id": task_id,
            "task_digest": task_digest,
            "reported_by_session_id": reported_by,
            "review_decision_id": (
                None if review_decision is None else review_decision["id"]
            ),
        })

    accepted_tasks: list[ContinuityAcceptanceTask] = []
    for task, task_view in zip(tasks, task_views, strict=True):
        if continuity_task_status(state_view, feature_view, task_view, task_views) != "COMPLETED":
            raise ContinuityError(
                "ACCEPTANCE_BLOCKED", f"Task {task['id']} is not complete",
            )
        attempt = next(
            (
                item for item in reversed(state["attempts"])
                if item["task_id"] == task["id"]
                and item.get("feature_id") == feature_id
                and item.get("feature_cycle") == state["features"][feature_id]["cycle"]
                and item.get("task_digest") == _digest(task)
                and item.get("mandate_id") == mandate_id
            ),
            None,
        )
        evidence_ref = None if attempt is None else attempt.get("evidence_ref")
        reported_revision = None if attempt is None else attempt.get("reported_revision")
        reported_by_session_id = (
            None if attempt is None else attempt.get("reported_by_session_id")
        )
        if (
            attempt is None
            or attempt["status"] not in ("REPORTED", "RECONCILED_COMPLETE")
            or not isinstance(evidence_ref, str)
            or type(reported_revision) is not int
            or not isinstance(reported_by_session_id, str)
        ):
            raise ContinuityError(
                "ACCEPTANCE_BLOCKED",
                f"Task {task['id']} has no current attributable completed attempt",
            )
        accepted_tasks.append({
            "task_id": task["id"],
            "task_digest": _digest(task),
            "attempt": attempt["attempt"],
            "attempt_status": attempt["status"],
            "evidence_ref": evidence_ref,
            "reported_revision": reported_revision,
            "reported_by_session_id": reported_by_session_id,
        })
    return criteria, accepted_tasks


def _latest_acceptance(
    state: ContinuityStateData, feature_id: str, cycle: int,
) -> ContinuityAcceptance | None:
    return next(
        (
            item for item in reversed(state.get("acceptances", []))
            if item["feature_id"] == feature_id and item["cycle"] == cycle
        ),
        None,
    )


def _acceptance_matches_current(
    state: ContinuityStateData,
    acceptance: ContinuityAcceptance,
    feature_id: str,
    feature: ContinuityFeatureDocument,
    tasks: list[ContinuityTaskDocument],
    input_snapshot: ContinuityInputScope,
    mandate_id: str | None,
) -> bool:
    if (
        acceptance["feature_id"] != feature_id
        or acceptance["cycle"] != state["features"][feature_id]["cycle"]
        or acceptance["revision"] != state["revision"]
        or acceptance["mandate_id"] != mandate_id
        or acceptance["input_snapshot"] != input_snapshot
    ):
        return False
    try:
        criteria, accepted_tasks = _acceptance_basis(
            state, feature_id, feature, tasks, input_snapshot, mandate_id,
        )
    except ContinuityError:
        return False
    return acceptance["criteria"] == criteria and acceptance["tasks"] == accepted_tasks


def execute(
    root: Path,
    operation: str,
    expected_revision: int | None,
    session_id: str,
    data: dict[str, object] | None = None,
    operations: FileOperations | None = None,
) -> dict[str, object]:
    """Run one read or revision-bound write; host identity is supplied by its adapter."""
    if operation not in _COMMANDS:
        raise ContinuityError("INVALID_OPERATION", "unsupported continuity operation")
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
        raise ContinuityError("INVALID_SESSION", "host session identity is required")
    if data is None:
        data = {}
    if type(data) is not dict:
        raise ContinuityError("INVALID_REQUEST", "continuity data must be an object")
    store = ContinuityPersistence(root, operations)
    root = store.root

    with store.transaction():
        state = store.load()
        if operation == "specify" and state is None:
            _expect_revision(expected_revision, 0)
            return _specify(root, store, session_id, data)
        if state is None:
            raise ContinuityError(
                "CONTINUITY_NOT_INITIALIZED",
                "only specify may opt a fresh repository into composed continuity",
            )
        if state["pending"] is not None:
            if operation in ("status", "tasks", "use"):
                return _project_pending(store, state)
            if operation != "resume":
                raise ContinuityError(
                    "RECOVERY_REQUIRED",
                    "a document publication is incomplete; use resume to reconcile its journal",
                )
            _expect_revision(expected_revision, state["revision"])
            if (
                "publication_resolution" in data
                or "preserve_current_paths" in data
            ):
                _keys(data, {"publication_resolution", "preserve_current_paths"})
                _resolve_pending_publication(store, state, data)
                recovered = store.load()
                assert recovered is not None
                return _project(store, recovered, session_id)
            store.recover_pending(state)
            recovered = store.load()
            assert recovered is not None
            return _project(store, recovered, session_id)
        if operation == "implement" and data.get("phase") == "report":
            if _has_legacy_documents(root, state):
                raise ContinuityError(
                    "UPGRADE_REQUIRED",
                    "legacy JSON-in-Markdown documents require explicit upgrade_markdown before reporting",
                )
            try:
                store.check_documents(state)
            except ContinuityError as error:
                if error.code != "DOCUMENT_DIVERGED":
                    raise
                return _report_attempt_state_only(
                    store, state, expected_revision, session_id, data,
                )
        elif operation == "resume" and data.get("reconcile") in ("COMPLETE", "INCOMPLETE"):
            if _has_legacy_documents(root, state):
                raise ContinuityError(
                    "UPGRADE_REQUIRED",
                    "legacy JSON-in-Markdown documents require explicit upgrade_markdown before reconciliation",
                )
            try:
                store.check_documents(state)
            except ContinuityError as error:
                if error.code != "DOCUMENT_DIVERGED":
                    raise
                return _reconcile_attempt_state_only(
                    store, state, expected_revision, session_id, data,
                )
        elif operation not in ("status", "tasks", "use", "reconcile", "upgrade_markdown"):
            store.check_documents(state)
        if (
            operation not in ("status", "tasks", "use", "input_snapshot", "upgrade_markdown")
            and _has_legacy_documents(root, state)
            and operation != "reconcile"
        ):
            raise ContinuityError(
                "UPGRADE_REQUIRED",
                "clean JSON-in-Markdown documents require explicit upgrade_markdown before writes",
            )
        if operation == "input_snapshot":
            _keys(data, set())
            _expect_revision(expected_revision, state["revision"])
            return {
                "mode": "COMPOSED", "revision": state["revision"],
                "input_snapshot": _continuity_input_scope(root, state),
            }
        if operation in ("status", "tasks") and not data:
            return _project(store, state, session_id)
        if operation == "use" and not data:
            return _project(store, state, session_id)
        _expect_revision(expected_revision, state["revision"])
        return _mutate(store, state, operation, session_id, data)


def _specify(
    root: Path, store: ContinuityPersistence,
    session_id: str, data: dict[str, object],
) -> dict[str, object]:
    _keys(data, {"title", "outcome", "criteria", "increments", "tasks", "plan"})
    state = _empty_state(root)
    record, task_records = _new_feature(data, "FEATURE", state)
    folder, ordinal = _new_directory(root, record["title"])
    feature_id = record["id"]
    plan = _text(
        data.get("plan", ""), "plan", maximum=16_384,
        multiline=True, allow_empty=True,
    )
    docs = _initial_documents(record, task_records, plan)
    feature_state: ContinuityFeatureIndex = {
        "directory": folder, "status": "OPEN", "cycle": 1,
    }
    state["features"] = {feature_id: feature_state}
    state["default_feature_id"] = feature_id
    state["session_features"] = {session_id: feature_id}
    state["task_states"] = {task["id"]: {"attempts": 0} for task in task_records}
    state["revision"] = 1
    writes = _writes_for_feature(root, state, feature_id, docs, initial=True)
    writes.update(_index_write(root, state, new_feature=(feature_id, folder, record["title"])))
    _refresh_document_metadata(state, writes)
    store.commit(_empty_state(root), state, writes)
    recovered = store.load()
    assert recovered is not None
    return _project(store, recovered, session_id)


def _mutate(
    store: ContinuityPersistence, state: ContinuityStateData, operation: str,
    session_id: str, data: dict[str, object],
) -> dict[str, object]:
    root = store.root
    if operation == "status":
        _keys(data, set())
        return _project(store, state, session_id)
    if operation == "reconcile":
        return _reconcile_documents(store, state, session_id, data)
    if operation == "upgrade_markdown":
        return _upgrade_markdown(store, state, session_id, data)
    if (operation == "tasks" and not data) or (operation == "use" and not data):
        return _project(store, state, session_id)

    if operation == "reopen":
        feature_id = data.get("feature_id")
        if not isinstance(feature_id, str) or not feature_id.strip():
            raise ContinuityError(
                "FEATURE_REQUIRED", "reopen requires an explicit Feature ID",
            )

    if operation == "specify":
        _keys(data, {"title", "outcome", "criteria", "increments", "tasks", "plan"})
        if len(state["features"]) >= _MAX_FEATURES:
            raise ContinuityError("LIMIT_EXCEEDED", "project Feature limit is exceeded")
        candidate = _copy_state(state)
        record, task_records = _new_feature(data, "FEATURE", candidate)
        folder, _ordinal = _new_directory(root, record["title"])
        feature_state: ContinuityFeatureIndex = {
            "directory": folder, "status": "OPEN", "cycle": 1,
        }
        candidate["features"][record["id"]] = feature_state
        candidate["default_feature_id"] = record["id"]
        candidate["session_features"][session_id] = record["id"]
        for task in task_records:
            candidate["task_states"][task["id"]] = {"attempts": 0}
        plan = _text(
            data.get("plan", ""), "plan", maximum=16_384,
            multiline=True, allow_empty=True,
        )
        docs = _initial_documents(record, task_records, plan)
        writes = _writes_for_feature(root, candidate, record["id"], docs, initial=True)
        writes.update(_index_write(
            root, candidate, new_feature=(record["id"], folder, record["title"]),
        ))
        return _publish_mutation(store, state, candidate, writes, session_id)

    if operation == "change" and data.get("feature_id") is None:
        _keys(data, {"title", "outcome", "criteria", "task_title", "task_description"})
        criteria_value = data.get("criteria")
        record, task_records = _new_feature({
            "title": data.get("title"),
            "outcome": data.get("outcome"),
            "criteria": criteria_value,
            "increments": [],
            "tasks": [{
                "title": data.get("task_title"),
                "description": data.get("task_description", ""),
                "criteria": list(range(len(criteria_value)))
                if type(criteria_value) is list else [],
            }],
            "plan": "",
        }, "CORRECTION", state)
        folder, ordinal = _new_directory(root, record["title"])
        candidate = _copy_state(state)
        if len(candidate["features"]) >= _MAX_FEATURES:
            raise ContinuityError("LIMIT_EXCEEDED", "project Feature limit is exceeded")
        feature_state: ContinuityFeatureIndex = {
            "directory": folder, "status": "OPEN", "cycle": 1,
        }
        candidate["features"][record["id"]] = feature_state
        candidate["default_feature_id"] = record["id"]
        candidate["session_features"][session_id] = record["id"]
        for task in task_records:
            candidate["task_states"][task["id"]] = {"attempts": 0}
        docs = _initial_documents(record, task_records, "")
        writes = _writes_for_feature(root, candidate, record["id"], docs, initial=True)
        writes.update(_index_write(root, candidate, new_feature=(record["id"], folder, record["title"])))
        return _publish_mutation(store, state, candidate, writes, session_id)

    if operation == "accept":
        _keys(data, {"feature_id", "reason", "mandate_id"})
        if not isinstance(data.get("feature_id"), str) or not data["feature_id"]:
            raise ContinuityError(
                "FEATURE_REQUIRED", "accept requires an explicit Feature ID",
            )
    feature_id = _selected_feature(state, session_id, data.get("feature_id"))
    if operation == "resume" and state["execution"] is not None:
        owner = _execution_feature(root, state)
        if data.get("feature_id") is not None and data["feature_id"] != owner:
            raise ContinuityError("TASK_MISMATCH", "resume must target the Feature owning the checkpointed Task")
        feature_id = owner
    feature = _read_feature(root, state, feature_id)
    tasks = _read_tasks(root, state, feature_id)
    decisions = _read_decisions(root, state, feature_id)
    candidate = _copy_state(state)
    writes: dict[str, tuple[bytes, str | None]] = {}
    commit_guard: Callable[[], None] | None = None
    feature_path = candidate["features"][feature_id]["directory"]

    if operation == "use":
        _keys(data, {"feature_id", "scope"})
        scope = data.get("scope", "session")
        if scope not in ("session", "worktree"):
            raise ContinuityError("INVALID_REQUEST", "selection scope must be session or worktree")
        if scope == "worktree":
            if candidate["default_feature_id"] != feature_id:
                candidate["default_feature_id"] = feature_id
                writes.update(_index_write(root, candidate))
        else:
            candidate["session_features"][session_id] = feature_id

    elif (
        candidate["features"][feature_id]["status"] == "CLOSED"
        and operation not in ("reopen", "close")
        and not (
            operation == "implement"
            and data.get("phase", "queue") == "queue"
            and data.get("task_id") is None
        )
    ):
        raise ContinuityError(
            "REOPEN_INTENT_REQUIRED",
            "reopen a closed Feature explicitly with a reason before semantic changes; "
            "closure history is retained",
        )

    elif operation == "clarify":
        _keys(data, {"feature_id", "question", "decision"})
        question = _text(data.get("question"), "question", maximum=2048)
        answer = _text(data.get("decision"), "decision", maximum=2048)
        if len(decisions) >= _MAX_DECISIONS:
            raise ContinuityError("LIMIT_EXCEEDED", "decision history limit is exceeded")
        decisions.append({"id": _new_id("D", candidate, {item["id"] for item in decisions}), "question": question, "decision": answer})
        writes.update(_render_decision_write(root, candidate, feature_id, decisions, feature))

    elif operation == "plan":
        _keys(data, {"feature_id", "plan"})
        plan = _text(data.get("plan"), "plan", maximum=16_384, multiline=True)
        writes.update(_render_plan_write(root, candidate, feature_id, feature, plan))

    elif operation == "tasks":
        _keys(data, {
            "feature_id", "task_title", "task_description", "increment_id",
            "criteria_ids", "depends_on", "mandate_id", "task_event",
            "task_id", "reason", "evidence_ref",
        })
        mandate = _validate_mandate_request(
            candidate, feature_id, data, feature, tasks,
        )
        if "task_event" in data:
            if candidate["execution"] is not None:
                raise ContinuityError(
                    "EXECUTION_BUSY", "reconcile the checkpointed Task before changing Task status",
                )
            if mandate is None:
                raise ContinuityError(
                    "MANDATE_REQUIRED", "Task lifecycle events require an active implementation mandate",
                )
            task = _by_id(tasks, data.get("task_id"), "T", "task_id")
            if not _mandate_allows_task(mandate, task):
                raise ContinuityError(
                    "MANDATE_SCOPE", "Task is outside the active implementation mandate",
                )
            event_kind = data.get("task_event")
            if event_kind not in ("BLOCKED", "CANCELED", "REOPENED"):
                raise ContinuityError(
                    "INVALID_REQUEST", "task_event must be BLOCKED, CANCELED, or REOPENED",
                )
            reason = _text(data.get("reason"), "reason", maximum=2048)
            evidence_ref = _text(data.get("evidence_ref"), "evidence_ref", maximum=2048)
            try:
                current_snapshot = _continuity_input_scope(root, state)
            except ContinuityError:
                current_snapshot = None
            task_view = _continuity_task_view(task)
            task_status = continuity_task_status(
                _continuity_state_view(state, current_snapshot),
                _continuity_feature_view(feature, state, tasks), task_view,
                tuple(_continuity_task_view(item) for item in tasks),
            )
            previous_event = next((
                item for item in reversed(candidate.get("task_events", []))
                if item["task_id"] == task["id"]
            ), None)
            if event_kind == "REOPENED":
                if task_status not in ("BLOCKED", "CANCELED", "REPORTED", "COMPLETED"):
                    raise ContinuityError(
                        "TASK_NOT_REOPENABLE",
                        "only a blocked, canceled, reported, or completed Task can be reopened",
                    )
            elif task_status in ("BLOCKED", "CANCELED") or (
                previous_event is not None
                and previous_event["event"] == "REOPENED"
                and candidate["task_states"][task["id"]]["attempts"]
                <= previous_event["attempt_count"]
            ):
                raise ContinuityError(
                    "TASK_TRANSITION_INVALID",
                    "resolve or reopen this Task before recording another blocked/canceled result",
                )
            candidate.setdefault("task_events", []).append({
                "task_id": task["id"], "event": event_kind,
                "reason": reason, "evidence_ref": evidence_ref,
                "session_id": session_id,
                "recorded_revision": state["revision"] + 1,
                "attempt_count": candidate["task_states"][task["id"]]["attempts"],
            })
            writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))
        else:
            title = _text(data.get("task_title"), "task_title", maximum=2048)
            description = _text(data.get("task_description", ""), "task_description", maximum=4096, multiline=True, allow_empty=True)
            criteria_ids = _id_list(data.get("criteria_ids"), "criteria_ids", "C", _criterion_ids(feature))
            if not criteria_ids:
                raise ContinuityError("INVALID_REQUEST", "a Task must be linked to at least one current criterion")
            increment_id = data.get("increment_id")
            increments = feature["increments"]
            if increment_id is not None and increment_id not in {item["id"] for item in increments}:
                raise ContinuityError("INVALID_REQUEST", "increment_id is not part of the selected Feature")
            assert increment_id is None or isinstance(increment_id, str)
            depends_on = _id_list(data.get("depends_on", []), "depends_on", "T", {item["id"] for item in tasks})
            if len(tasks) >= _MAX_TASKS:
                raise ContinuityError("LIMIT_EXCEEDED", "Feature Task limit is exceeded")
            task: ContinuityTaskDocument = {
                "id": _new_id("T", candidate), "title": title, "description": description,
                "increment_id": increment_id, "criteria_ids": criteria_ids,
                "depends_on": depends_on,
            }
            if mandate is not None and not _mandate_allows_task(mandate, task):
                raise ContinuityError(
                    "MANDATE_SCOPE",
                    "new Task must use only criteria and Increments bound to the active mandate",
                )
            tasks.append(task)
            candidate["task_states"][task["id"]] = {"attempts": 0}
            writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))

    elif operation == "implement":
        _keys(data, {
            "feature_id", "task_id", "phase", "report", "result", "mandate_id",
            "mode", "close_feature", "replace_mandate_id",
        })
        task_id = data.get("task_id")
        phase = data.get("phase", "queue")
        if phase == "start":
            if not isinstance(data.get("feature_id"), str):
                raise ContinuityError(
                    "FEATURE_REQUIRED", "implement start requires the exact Feature ID",
                )
            if task_id is not None or data.get("mode") != "ALL":
                raise ContinuityError(
                    "INVALID_REQUEST", "implement start requires mode ALL and no task_id",
                )
            close_feature = data.get("close_feature", False)
            if type(close_feature) is not bool:
                raise ContinuityError("INVALID_REQUEST", "close_feature must be a boolean")
            if candidate["execution"] is not None:
                raise ContinuityError(
                    "EXECUTION_BUSY", "reconcile the checkpointed Task before starting a mandate",
                )
            if feature_id in candidate["paused_features"]:
                raise ContinuityError(
                    "FEATURE_PAUSED", "resume the selected Feature before starting a mandate",
                )
            current_mandate = _feature_mandate(candidate, feature_id)
            replace_id = data.get("replace_mandate_id")
            if current_mandate is not None:
                if (
                    current_mandate["feature_id"] == feature_id
                    and _mandate_scope_matches(current_mandate, feature)
                    and current_mandate["close_feature"] == close_feature
                ):
                    if replace_id not in (None, current_mandate["id"]):
                        raise ContinuityError(
                            "MANDATE_MISMATCH",
                            "replacement must name the current mandate for this Feature",
                        )
                    return _project(store, state, session_id)
                if replace_id != current_mandate["id"]:
                    raise ContinuityError(
                        "MANDATE_ACTIVE",
                        "an active mandate can be replaced only by naming its exact ID after it is finished or stale",
                    )
                if current_mandate["feature_id"] != feature_id:
                    raise ContinuityError(
                        "FEATURE_MISMATCH", "a mandate can only be replaced for its bound Feature",
                    )
                try:
                    old_snapshot = _continuity_input_scope(root, state)
                except ContinuityError:
                    old_snapshot = None
                old_status = _implementation_projection(
                    state, current_mandate, feature, tasks, old_snapshot,
                )["status"]
                if old_status not in ("FINISHED", "STALE_SCOPE"):
                    raise ContinuityError(
                        "MANDATE_ACTIVE",
                        "finish the current mandate or reconcile its changed scope before replacing it",
                    )
            elif replace_id is not None:
                raise ContinuityError(
                    "MANDATE_MISMATCH", "there is no current mandate with that replacement ID",
                )
            current_mandates = _mutable_mandates(candidate)
            mandate_id = _new_id("D", candidate)
            new_mandate: ContinuityMandate = {
                "id": mandate_id, "feature_id": feature_id, "mode": "ALL",
                "criteria": [
                    {"id": item["id"], "digest": _digest(item)}
                    for item in feature["criteria"]
                ],
                "increments": [
                    {
                        "id": item["id"],
                        "digest": _digest({
                            "id": item["id"], "outcome": item["outcome"],
                        }),
                    }
                    for item in feature["increments"]
                ],
                "source_revision": state["revision"],
                "recorded_revision": state["revision"] + 1,
                "session_id": session_id, "close_feature": close_feature,
                "outcome_digest": _digest(feature["outcome"]),
            }
            if current_mandate is not None:
                candidate.setdefault("mandate_history", []).append(current_mandate)
            current_mandates[feature_id] = new_mandate
            candidate["mandates"] = current_mandates
            return _publish_state_only(store, state, candidate, session_id)
        if phase == "queue" and task_id is None:
            _validate_mandate_request(
                candidate, feature_id, data, feature, tasks,
            )
            return _project(store, state, session_id)
        task = _by_id(tasks, task_id, "T", "task_id")
        execution = candidate["execution"]
        mandate = _validate_mandate_request(
            candidate, feature_id, data, feature, tasks,
            allow_unbound_recovery=(
                phase == "report"
                and execution is not None
                and execution["task_id"] == task["id"]
            ),
        )
        if mandate is not None and not _mandate_allows_task(mandate, task):
            raise ContinuityError(
                "MANDATE_SCOPE", "Task is outside the active implementation mandate",
            )
        if phase == "begin":
            execution = candidate["execution"]
            if execution is not None:
                raise ContinuityError(
                    "EXECUTION_BUSY",
                    "one Task attempt is already unresolved for this worktree; reconcile it before dispatch",
                )
            if feature_id in candidate["paused_features"]:
                raise ContinuityError("FEATURE_PAUSED", "resume the selected Feature before dispatch")
            try:
                input_snapshot = _continuity_input_scope(root, candidate)
            except ContinuityError:
                input_snapshot = None
            if not continuity_task_eligible(
                _continuity_state_view(candidate, input_snapshot),
                _continuity_feature_view(feature, candidate, tasks),
                tuple(_continuity_task_view(item) for item in tasks),
                _continuity_task_view(task),
            ):
                raise ContinuityError("TASK_NOT_ELIGIBLE", "Task dependencies or current checks are not complete")
            task_state = candidate["task_states"][task["id"]]
            attempt = task_state["attempts"] + 1
            task_state["attempts"] = attempt
            entry: ContinuityAttempt = {
                "task_id": task["id"], "attempt": attempt, "status": "STARTED",
                "session_id": session_id, "checkpoint": _text(
                    data.get("report", f"Attempt {attempt} checkpointed before effects"),
                    "report", maximum=2048, allow_empty=False,
                ),
                "result": None, "result_status": None, "evidence_ref": None,
                "feature_id": feature_id,
                "feature_cycle": candidate["features"][feature_id]["cycle"],
                "task_digest": _digest(task),
                "started_revision": state["revision"] + 1,
            }
            if mandate is not None:
                entry["mandate_id"] = mandate["id"]
            candidate["attempts"].append(entry)
            execution_record: ContinuityExecution = {
                "task_id": task["id"], "attempt": attempt, "session_id": session_id,
                "status": "OUTCOME_UNKNOWN",
            }
            candidate["execution"] = execution_record
            writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))
        elif phase == "report":
            execution = candidate["execution"]
            if execution is None or execution["task_id"] != task["id"]:
                raise ContinuityError("NO_ACTIVE_ATTEMPT", "no matching checkpointed Task attempt exists")
            if execution["session_id"] != session_id:
                raise ContinuityError(
                    "OUTCOME_UNKNOWN",
                    "a different host session cannot report or repeat this attempt; reconcile its outcome first",
                )
            result = data.get("result")
            if result not in ("SUCCEEDED", "BLOCKED", "UNKNOWN"):
                raise ContinuityError("INVALID_REQUEST", "result must be SUCCEEDED, BLOCKED, or UNKNOWN")
            statement = _text(data.get("report"), "report", maximum=4096)
            entry = _active_attempt(candidate)
            entry["result"] = statement
            entry["result_status"] = result
            entry["status"] = "REPORTED" if result != "UNKNOWN" else "OUTCOME_UNKNOWN"
            if result == "UNKNOWN":
                execution["status"] = "OUTCOME_UNKNOWN"
            else:
                candidate["execution"] = None
                entry["evidence_ref"] = f"session:{session_id}:task:{task['id']}:attempt:{entry['attempt']}"
                entry["reported_revision"] = state["revision"] + 1
                entry["reported_by_session_id"] = session_id
                try:
                    entry["reported_input_scope"] = _continuity_input_scope(root, candidate)
                except ContinuityError:
                    pass
            writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))
        else:
            raise ContinuityError("INVALID_REQUEST", "implement phase must be queue, begin, or report")

    elif operation == "verify":
        criterion = _by_id(feature["criteria"], data.get("criterion_id"), "C", "criterion_id")
        if (
            data.get("check_kind") != criterion["check_kind"]
            or data.get("check_target") != criterion["check_target"]
        ):
            raise ContinuityError("STALE_CRITERION", "check target differs from the current criterion")
        if "feature_id" in data and data["feature_id"] != feature_id:
            raise ContinuityError("FEATURE_MISMATCH", "evidence Feature differs from the selected Feature")
        if candidate["execution"] is not None:
            raise ContinuityError("EXECUTION_BUSY", "reconcile the in-flight Task before verifying")
        task_id = _text(data.get("task_id"), "task_id", maximum=256)
        task = _by_id(tasks, task_id, "T", "task_id")
        mandate = _validate_mandate_request(
            candidate, feature_id, data, feature, tasks,
        )
        if mandate is not None and not _mandate_allows_task(mandate, task):
            raise ContinuityError(
                "MANDATE_SCOPE", "verification Task is outside the active implementation mandate",
            )
        if criterion["id"] not in task["criteria_ids"]:
            raise ContinuityError(
                "INVALID_REQUEST", "verification Task must be linked to the current criterion",
            )
        method = criterion["check_kind"]
        source: str
        evidence_status: str
        artifact_present: bool | None = None
        artifact_sha256: str | None = None
        report: str | None = None
        if method in ("python-unittest", "node-test"):
            _keys(data, {
                "feature_id", "criterion_id", "exit_code", "output_sha256",
                "check_kind", "check_target", "task_id",
                "input_snapshot_before", "input_snapshot_after", "mandate_id",
            })
            exit_code = data.get("exit_code")
            if type(exit_code) is not int or not 0 <= exit_code <= 255:
                raise ContinuityError("INVALID_REQUEST", "check exit code must be between 0 and 255")
            output_digest = data.get("output_sha256")
            if not isinstance(output_digest, str) or re.fullmatch(r"[0-9a-f]{64}", output_digest) is None:
                raise ContinuityError("INVALID_REQUEST", "check output digest must be a SHA-256 value")
            input_before = _input_scope_argument(
                data.get("input_snapshot_before"), "input_snapshot_before",
            )
            input_after = _input_scope_argument(
                data.get("input_snapshot_after"), "input_snapshot_after",
            )
            current_input = _continuity_input_scope(root, state)
            if input_before != input_after or input_before != current_input:
                raise ContinuityError(
                    "STALE_INPUT", "repository inputs changed before the check receipt was admitted",
                )
            evidence_status = "VERIFIED" if exit_code == 0 else "FAILED"
            source = "CALLER_FORWARDED_UNATTESTED"
        elif method == "artifact-present":
            _keys(data, {
                "feature_id", "criterion_id", "check_kind", "check_target",
                "task_id", "mandate_id",
            })
            target = criterion["check_target"]
            if target in state["documents"] or target == "specs/README.md":
                raise ContinuityError(
                    "UNSAFE_ARTIFACT", "artifact evidence cannot target a Core-managed Feature document",
                )
            try:
                snapshot = repository_input_snapshot(root, state["repository_id"])
            except InputFingerprintError as error:
                raise ContinuityError("INPUT_SNAPSHOT_UNAVAILABLE", str(error)) from error
            current_input = _continuity_input_scope_from_snapshot(state, snapshot)
            file_digests = dict(snapshot.file_sha256)
            if target in file_digests:
                artifact_present = True
                artifact_sha256 = file_digests[target]
            elif target in snapshot.directory_paths:
                raise ContinuityError(
                    "UNSAFE_ARTIFACT", "artifact target names a directory, not a regular file",
                )
            else:
                artifact_present = False
            input_before = current_input
            input_after = current_input
            evidence_status = "VERIFIED" if artifact_present else "FAILED"
            source = "CORE_OBSERVED"
        elif method == "review":
            _keys(data, {
                "feature_id", "criterion_id", "check_kind", "check_target",
                "task_id", "mandate_id", "report",
            })
            current_input = _continuity_input_scope(root, state)
            input_before = current_input
            input_after = current_input
            report = _text(data.get("report"), "review report", maximum=4096, multiline=True)
            evidence_status = "REPORTED"
            source = "CALLER_REPORTED"
        else:
            raise ContinuityError(
                "UNSUPPORTED_EVIDENCE_METHOD",
                "build, integration, and functional descriptors are not executable Core evidence",
            )

        recorded_revision = state["revision"] + 1
        feature_cycle = candidate["features"][feature_id]["cycle"]
        check: ContinuityCheck = {
            "criterion_id": criterion["id"], "criterion_digest": _digest(criterion),
            "check_kind": criterion["check_kind"], "check_target": criterion["check_target"],
            "reported_by_session_id": session_id, "task_id": task_id,
            "recorded_revision": recorded_revision,
            "observed_input_scope": current_input,
            "task_digest": _digest(task),
            "feature_id": feature_id,
            "feature_cycle": feature_cycle,
            "purpose": criterion["description"],
            "evidence_status": evidence_status,
            "source": source,
            "evidence_ref": (
                f"continuity:{feature_id}:{feature_cycle}:{criterion['id']}:{recorded_revision}"
            ),
            "input_snapshot_before": input_before,
            "input_snapshot_after": input_after,
        }
        if method in ("python-unittest", "node-test"):
            check["exit_code"] = cast(int, data["exit_code"])
            check["output_sha256"] = cast(str, data["output_sha256"])
        elif method == "artifact-present":
            assert artifact_present is not None
            check["artifact_present"] = artifact_present
            if artifact_sha256 is not None:
                check["artifact_sha256"] = artifact_sha256
        elif report is not None:
            check["report"] = report
        if mandate is not None:
            check["mandate_id"] = mandate["id"]
        candidate["checks"].append(check)
        writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))

    elif operation == "review_decision":
        _keys(data, {
            "feature_id", "criterion_id", "evidence_ref", "decision",
            "reason", "mandate_id",
        })
        criterion = _by_id(
            feature["criteria"], data.get("criterion_id"), "C", "criterion_id",
        )
        if criterion["check_kind"] != "review":
            raise ContinuityError(
                "INVALID_REVIEW_DECISION",
                "only a review criterion can receive an explicit review decision",
            )
        mandate = _validate_mandate_request(
            candidate, feature_id, data, feature, tasks,
        )
        if mandate is not None and not _mandate_scope_matches(mandate, feature):
            raise ContinuityError(
                "MANDATE_STALE_SCOPE", "review decision is outside the current mandate scope",
            )
        try:
            input_snapshot = _continuity_input_scope(root, state)
        except ContinuityError as error:
            raise ContinuityError(
                "REVIEW_EVIDENCE_STALE", "current repository inputs are unavailable",
            ) from error
        current_check = continuity_current_checks(
            _continuity_state_view(state, input_snapshot),
            _continuity_feature_view(feature, state, tasks),
            tasks=tuple(_continuity_task_view(item) for item in tasks),
        ).get(criterion["id"])
        evidence_ref = data.get("evidence_ref")
        if (
            current_check is None
            or current_check.get("status") != "REPORTED"
            or current_check.get("source") != "CALLER_REPORTED"
            or current_check.get("check_kind") != "review"
            or not isinstance(evidence_ref, str)
            or current_check.get("evidence_ref") != evidence_ref
        ):
            raise ContinuityError(
                "REVIEW_EVIDENCE_STALE",
                "review decision must identify the exact current review report",
            )
        task_id = current_check.get("task_id")
        task_digest = current_check.get("task_digest")
        if (
            not isinstance(task_id, str)
            or not isinstance(task_digest, str)
            or task_id not in {item["id"] for item in tasks}
        ):
            raise ContinuityError(
                "REVIEW_EVIDENCE_STALE",
                "review report has no current attributable Task",
            )
        decision_kind = data.get("decision")
        if decision_kind not in ("ACCEPTED", "REJECTED"):
            raise ContinuityError(
                "INVALID_REQUEST", "decision must be ACCEPTED or REJECTED",
            )
        review: ContinuityReviewDecision = {
            "id": _new_id("D", candidate),
            "feature_id": feature_id,
            "feature_cycle": candidate["features"][feature_id]["cycle"],
            "criterion_id": criterion["id"],
            "criterion_digest": _digest(criterion),
            "evidence_ref": evidence_ref,
            "task_id": task_id,
            "task_digest": task_digest,
            "mandate_id": None if mandate is None else mandate["id"],
            "decision": cast(str, decision_kind),
            "reason": _text(data.get("reason"), "reason", maximum=2048),
            "revision": state["revision"] + 1,
            "session_id": session_id,
        }
        candidate.setdefault("review_decisions", []).append(review)
        writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))

    elif operation == "accept":
        mandate = _validate_mandate_request(
            candidate, feature_id, data, feature, tasks,
        )
        try:
            input_snapshot = _continuity_input_scope(root, state)
        except ContinuityError as error:
            raise ContinuityError(
                "ACCEPTANCE_BLOCKED", "current repository inputs are unavailable",
            ) from error
        blockers = continuity_close_blockers(
            _continuity_state_view(state, input_snapshot),
            feature_id,
            {"status": candidate["features"][feature_id]["status"]},
            _continuity_feature_view(feature, candidate, tasks),
            tuple(_continuity_task_view(item) for item in tasks),
        )
        if blockers:
            raise ContinuityError("ACCEPTANCE_BLOCKED", "; ".join(blockers))
        criteria, accepted_tasks = _acceptance_basis(
            state, feature_id, feature, tasks, input_snapshot,
            None if mandate is None else mandate["id"],
        )
        acceptance: ContinuityAcceptance = {
            "id": _new_id("D", candidate),
            "feature_id": feature_id,
            "cycle": candidate["features"][feature_id]["cycle"],
            "basis_revision": state["revision"],
            "revision": state["revision"] + 1,
            "session_id": session_id,
            "reason": _text(data.get("reason"), "reason", maximum=2048),
            "mandate_id": None if mandate is None else mandate["id"],
            "input_snapshot": input_snapshot,
            "criteria": criteria,
            "tasks": accepted_tasks,
        }
        candidate.setdefault("acceptances", []).append(acceptance)
        return _publish_state_only(store, state, candidate, session_id)

    elif operation == "pause":
        _keys(data, {
            "feature_id", "reason", "detour_kind", "detour_title",
            "return_task_id", "mandate_id",
        })
        mandate = _validate_mandate_request(
            candidate, feature_id, data, feature, tasks,
        )
        if mandate is not None and candidate["execution"] is not None:
            raise ContinuityError(
                "EXECUTION_BUSY", "reconcile the checkpointed Task before pausing or detouring",
            )
        reason = _text(data.get("reason"), "reason", maximum=2048)
        detour_kind = data.get("detour_kind")
        if detour_kind is not None:
            if detour_kind not in ("RELATED", "EXTERNAL"):
                raise ContinuityError("INVALID_REQUEST", "detour_kind must be RELATED or EXTERNAL")
            if any(item["result"] is None for item in candidate["detours"]):
                raise ContinuityError(
                    "DETOUR_OUTCOME_REQUIRED",
                    "record the pending detour result before starting another detour",
                )
            detour_title = _text(data.get("detour_title"), "detour_title", maximum=2048)
            return_task = _by_id(
                tasks, data.get("return_task_id"), "T", "return_task_id",
            )
            if mandate is not None and not _mandate_allows_task(mandate, return_task):
                raise ContinuityError(
                    "MANDATE_SCOPE", "detour return Task is outside the active mandate",
                )
            return_task_id = return_task["id"]
            detour_id = _new_id("D", candidate, {item["id"] for item in decisions})
            detour: ContinuityDetour = {
                "id": detour_id, "feature_id": feature_id,
                "kind": detour_kind, "title": detour_title,
                "return_task_id": return_task_id, "result": None,
                "reported_by_session_id": None,
                "created_revision": state["revision"] + 1,
                "resolved_revision": None,
            }
            candidate["detours"].append(detour)
            decisions.append({
                "id": detour_id,
                "question": (
                    f"[EvoSpec detour {detour_id}] {detour_kind}; "
                    f"return to {return_task_id}"
                ),
                "decision": f"{detour_title}: {reason}",
            })
            writes.update(_render_decision_write(root, candidate, feature_id, decisions, feature))
        candidate["paused_features"] = sorted(set(candidate["paused_features"]) | {feature_id})
        candidate["pause_reasons"][feature_id] = reason
        writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))

    elif operation == "resume":
        _keys(data, {
            "feature_id", "reconcile", "task_id", "evidence_ref", "detour_summary",
            "mandate_id",
        })
        execution = candidate["execution"]
        reconcile = data.get("reconcile")
        mandate = _validate_mandate_request(
            candidate, feature_id, data, feature, tasks,
            allow_unbound_recovery=(
                execution is not None
                and reconcile in ("COMPLETE", "INCOMPLETE")
                and data.get("task_id") == execution["task_id"]
            ),
        )
        pending_detour = _pending_detour(candidate, feature_id)
        detour_result: str | None = None
        if pending_detour is not None:
            return_task = _by_id(tasks, pending_detour["return_task_id"], "T", "return_task_id")
            if mandate is not None and not _mandate_allows_task(mandate, return_task):
                raise ContinuityError(
                    "MANDATE_SCOPE", "detour return Task is outside the active mandate",
                )
            if data.get("detour_summary") is None:
                raise ContinuityError(
                    "DETOUR_OUTCOME_REQUIRED",
                    "resume requires a recorded result for the related or external detour",
                )
            detour_result = _text(
                data["detour_summary"], "detour_summary", maximum=4096,
            )
        elif data.get("detour_summary") is not None:
            raise ContinuityError(
                "NO_ACTIVE_DETOUR",
                "there is no related or external detour to record a result for",
            )
        if execution is not None:
            if reconcile not in ("COMPLETE", "INCOMPLETE"):
                raise ContinuityError(
                    "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION",
                    "resume requires an explicit COMPLETE or INCOMPLETE reconciliation with evidence_ref",
                )
            if data.get("task_id") != execution["task_id"]:
                raise ContinuityError("TASK_MISMATCH", "reconciliation must name the exact checkpointed Task")
            if mandate is not None and not any(
                task["id"] == execution["task_id"] and _mandate_allows_task(mandate, task)
                for task in tasks
            ):
                raise ContinuityError(
                    "MANDATE_SCOPE", "checkpointed Task is outside the active mandate",
                )
            evidence_ref = _text(data.get("evidence_ref"), "evidence_ref", maximum=2048)
            entry = _active_attempt(candidate)
            entry["status"] = "RECONCILED_COMPLETE" if reconcile == "COMPLETE" else "RECONCILED_INCOMPLETE"
            entry["result_status"] = reconcile
            entry["evidence_ref"] = evidence_ref
            entry["result"] = entry.get("result") or "Outcome reconciled explicitly after interruption"
            entry["reported_revision"] = state["revision"] + 1
            entry["reported_by_session_id"] = session_id
            try:
                entry["reported_input_scope"] = _continuity_input_scope(root, candidate)
            except ContinuityError:
                pass
            candidate["execution"] = None
            writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))
        elif reconcile is not None:
            raise ContinuityError("NO_ACTIVE_ATTEMPT", "there is no unresolved Task attempt to reconcile")
        if pending_detour is not None:
            assert detour_result is not None
            decisions.append({
                "id": _new_id("D", candidate, {item["id"] for item in decisions}),
                "question": (
                    f"Detour result for {pending_detour['id']}; "
                    f"return to {pending_detour['return_task_id']}; "
                    f"recorded by session {json.dumps(session_id, ensure_ascii=False)}"
                ),
                "decision": detour_result,
            })
            writes.update(_render_decision_write(root, candidate, feature_id, decisions, feature))
            pending_detour["result"] = detour_result
            pending_detour["reported_by_session_id"] = session_id
            pending_detour["resolved_revision"] = state["revision"] + 1
        candidate["paused_features"] = sorted(
            item for item in candidate["paused_features"] if item != feature_id
        )
        candidate["pause_reasons"].pop(feature_id, None)

    elif operation == "close":
        _keys(data, {"feature_id", "mandate_id", "acceptance_id"})
        mandate = _validate_mandate_request(
            candidate, feature_id, data, feature, tasks,
        )
        feature_state = candidate["features"][feature_id]
        if feature_state["status"] == "CLOSED":
            raise ContinuityError("ALREADY_CLOSED", "Feature is already closed in this cycle")
        acceptance_id = data.get("acceptance_id")
        close_acceptance = next(
            (
                item for item in reversed(candidate.get("acceptances", []))
                if item["feature_id"] == feature_id
                and item["cycle"] == feature_state["cycle"]
            ),
            None,
        )
        if (
            close_acceptance is None
            or not isinstance(acceptance_id, str)
            or close_acceptance["id"] != acceptance_id
        ):
            raise ContinuityError(
                "ACCEPTANCE_REQUIRED",
                "close requires the exact latest acceptance decision for this Feature cycle",
            )
        try:
            current_input_snapshot = _continuity_input_scope(root, state)
        except ContinuityError as error:
            raise ContinuityError(
                "CLOSE_BLOCKED", "current input snapshot is unavailable",
            ) from error
        if close_acceptance["revision"] != state["revision"]:
            raise ContinuityError(
                "ACCEPTANCE_STALE",
                "state changed after acceptance; reread, recheck, and record a new explicit acceptance",
            )
        blockers = continuity_close_blockers(
            _continuity_state_view(state, current_input_snapshot),
            feature_id,
            {"status": feature_state["status"]},
            _continuity_feature_view(feature, candidate, tasks),
            tuple(_continuity_task_view(item) for item in tasks),
        )
        if blockers:
            raise ContinuityError("CLOSE_BLOCKED", "; ".join(blockers))
        criteria_basis, tasks_basis = _acceptance_basis(
            state, feature_id, feature, tasks, current_input_snapshot,
            None if mandate is None else mandate["id"],
        )
        if (
            close_acceptance["feature_id"] != feature_id
            or close_acceptance["cycle"] != feature_state["cycle"]
            or close_acceptance["mandate_id"] != (
                None if mandate is None else mandate["id"]
            )
            or close_acceptance["input_snapshot"] != current_input_snapshot
            or close_acceptance["criteria"] != criteria_basis
            or close_acceptance["tasks"] != tasks_basis
        ):
            raise ContinuityError(
                "ACCEPTANCE_STALE",
                "accepted criteria, checks, Tasks, mandate, or repository inputs changed",
            )
        accepted_input_snapshot = close_acceptance["input_snapshot"]

        def check_close_input_snapshot() -> None:
            try:
                latest_input_snapshot = _continuity_input_scope(root, state)
            except ContinuityError as error:
                raise ContinuityError(
                    "CLOSE_BLOCKED", "current input snapshot is unavailable",
                ) from error
            if latest_input_snapshot != accepted_input_snapshot:
                raise ContinuityError(
                    "ACCEPTANCE_STALE",
                    "repository inputs changed before closure publication",
                )

        commit_guard = check_close_input_snapshot
        receipt: ContinuityClosure = {
            "id": _new_id("D", candidate),
            "feature_id": feature_id,
            "cycle": feature_state["cycle"], "revision": state["revision"] + 1,
            "criteria": [
                {"id": item["id"], "digest": _digest(item)}
                for item in feature["criteria"]
            ],
            "session_id": session_id,
            "acceptance_id": close_acceptance["id"],
            "basis_revision": close_acceptance["basis_revision"],
            "mandate_id": None if mandate is None else mandate["id"],
        }
        candidate["closures"].append(receipt)
        feature_state["status"] = "CLOSED"
        writes.update(_render_spec_write(root, candidate, feature_id, feature))
        writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))
        writes.update(_index_write(root, candidate))

    elif operation == "reopen":
        _keys(data, {"feature_id", "reason"})
        feature_state = candidate["features"][feature_id]
        if feature_state["status"] != "CLOSED":
            raise ContinuityError(
                "FEATURE_NOT_CLOSED", "only a closed Feature can be explicitly reopened",
            )
        previous = next(
            (
                item for item in reversed(candidate["closures"])
                if item["feature_id"] == feature_id
                and item["cycle"] == feature_state["cycle"]
            ),
            None,
        )
        if previous is None:
            raise ContinuityError(
                "CORRUPT_CONTINUITY", "closed Feature has no current closure receipt",
            )
        candidate["reopens"].append({
            "feature_id": feature_id,
            "cycle": feature_state["cycle"] + 1,
            "previous_closure_id": previous["id"],
            "session_id": session_id,
            "reason": _text(data.get("reason"), "reason", maximum=2048),
            "revision": state["revision"] + 1,
        })
        feature_state["cycle"] += 1
        feature_state["status"] = "OPEN"
        writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))
        writes.update(_index_write(root, candidate))

    elif operation == "change":
        _keys(data, {
            "feature_id", "outcome", "criteria", "task_title", "task_description",
            "increment_id", "criteria_ids", "mandate_id",
        })
        mandate = _validate_mandate_request(
            candidate, feature_id, data, feature, tasks,
        )
        changed = _amend_feature(feature, data, candidate)
        if mandate is not None and not _mandate_scope_matches(mandate, changed):
            raise ContinuityError(
                "MANDATE_STALE_SCOPE",
                "criterion definitions cannot change under this mandate; reconcile explicitly, then start a new mandate",
            )
        task_title = _text(data.get("task_title"), "task_title", maximum=2048)
        task_description = _text(data.get("task_description", ""), "task_description", maximum=4096, multiline=True, allow_empty=True)
        criteria_ids = _id_list(
            data.get("criteria_ids", [item["id"] for item in changed["criteria"]]),
            "criteria_ids", "C", _criterion_ids(changed),
        )
        if not criteria_ids:
            criteria_ids = [item["id"] for item in changed["criteria"]]
        increment_id = data.get("increment_id")
        if increment_id is not None and increment_id not in {item["id"] for item in changed["increments"]}:
            raise ContinuityError("INVALID_REQUEST", "increment_id is not part of the selected Feature")
        assert increment_id is None or isinstance(increment_id, str)
        task: ContinuityTaskDocument = {
            "id": _new_id("T", candidate), "title": task_title,
            "description": task_description, "increment_id": increment_id,
            "criteria_ids": criteria_ids, "depends_on": [],
        }
        if mandate is not None and not _mandate_allows_task(mandate, task):
            raise ContinuityError(
                "MANDATE_SCOPE", "new Task must use only criteria and Increments bound to the active mandate",
            )
        if len(tasks) >= _MAX_TASKS:
            raise ContinuityError("LIMIT_EXCEEDED", "Feature Task limit is exceeded")
        tasks.append(task)
        candidate["task_states"][task["id"]] = {"attempts": 0}
        writes.update(_render_spec_write(root, candidate, feature_id, changed))
        writes.update(_render_task_write(root, candidate, feature_id, tasks, changed))
        feature = changed

    else:
        raise ContinuityError("INVALID_OPERATION", "unsupported continuity operation")

    if operation in ("verify", "review_decision"):
        observed_input_scope = (
            candidate["checks"][-1].get("observed_input_scope")
            if operation == "verify"
            else next(
                (
                    item.get("observed_input_scope")
                    for item in candidate["checks"]
                    if item.get("evidence_ref")
                    == candidate.get("review_decisions", [])[-1]["evidence_ref"]
                ),
                None,
            )
        )
        if (
            observed_input_scope is None
            or _continuity_input_scope(root, state) != observed_input_scope
        ):
            raise ContinuityError(
                "STALE_INPUT",
                "repository inputs changed before the evidence decision was committed",
            )
    return _publish_mutation(
        store, state, candidate, writes, session_id,
        commit_guard=commit_guard,
    )


def _publish_mutation(
    store: ContinuityPersistence, state: ContinuityStateData,
    candidate: ContinuityStateData, writes: dict[str, tuple[bytes, str | None]],
    session_id: str, *, commit_guard: Callable[[], None] | None = None,
) -> dict[str, object]:
    candidate["revision"] = state["revision"] + 1
    read_set: dict[str, str | None] = {
        path: digest for path, digest in state["documents"].items()
    }
    _refresh_document_metadata(candidate, writes)
    store.commit(
        state, candidate, writes, read_set=read_set,
        commit_guard=commit_guard,
    )
    saved = store.load()
    assert saved is not None
    return _project(store, saved, session_id)


def _empty_state(root: Path) -> ContinuityStateData:
    return {
        "schema": SCHEMA, "mode": "COMPOSED",
        "repository_id": sha256(str(root).encode("utf-8")).hexdigest()[:16],
        "revision": 0, "features": {}, "default_feature_id": None,
        "session_features": {}, "task_states": {}, "attempts": [],
        "execution": None, "checks": [], "closures": [], "reopens": [],
        "paused_features": [], "pause_reasons": {}, "detours": [],
        "documents": {}, "ids": [], "pending": None,
        "semantic_digests": {}, "identity_metadata": {},
    }


def _refresh_document_metadata(
    state: ContinuityStateData,
    writes: dict[str, tuple[bytes, str | None]],
) -> None:
    semantic_digests = dict(state.get("semantic_digests", {}))
    identity_metadata = cast(
        dict[str, ContinuityIdentityMetadata],
        dict(state.get("identity_metadata", {})),
    )
    for relative, (content, _before) in writes.items():
        if relative == "specs/README.md":
            continue
        name = relative.rsplit("/", 1)[-1][:-3]
        directory = relative.split("/")[1]
        feature_id = next(
            (
                identity for identity, entry in state["features"].items()
                if entry["directory"] == directory
            ),
            None,
        )
        if feature_id is None:
            raise ContinuityError("INVALID_DOCUMENT", "document has no owning Feature")
        parsed = _parse_document_content(content, relative, name, feature_id)
        semantic_digests[relative] = _semantic_digest_for_content(
            content, relative, name, feature_id,
        )
        entities: list[tuple[str, str, str]] = []
        if name == "spec":
            entities.append((feature_id, "F", _digest(parsed)))
            entities.extend(
                (cast(str, item["id"]), "C", _digest(item))
                for item in cast(list[dict[str, object]], parsed["criteria"])
            )
            entities.extend(
                (cast(str, item["id"]), "S", _digest(item))
                for item in cast(list[dict[str, object]], parsed["increments"])
            )
        elif name == "tasks":
            entities.extend(
                (cast(str, item["id"]), "T", _digest(item))
                for item in cast(list[dict[str, object]], parsed["tasks"])
            )
        elif name == "decisions":
            entities.extend(
                (cast(str, item["id"]), "D", _digest(item))
                for item in cast(list[dict[str, object]], parsed["decisions"])
            )
        ids = {identity for identity, _kind, _digest_value in entities}
        kinds = {kind for _identity, kind, _digest_value in entities}
        for identity, metadata in identity_metadata.items():
            if metadata["feature_id"] == feature_id and metadata["kind"] in kinds:
                metadata["active"] = identity in ids
        for identity, kind, digest in entities:
            identity_metadata[identity] = {
                "feature_id": feature_id, "kind": kind,
                "active": True, "digest": digest,
            }
    state["semantic_digests"] = semantic_digests
    state["identity_metadata"] = identity_metadata


def _has_legacy_documents(root: Path, state: ContinuityStateData) -> bool:
    for relative in state["documents"]:
        if relative == "specs/README.md":
            continue
        path = root / Path(relative)
        if _file_digest(path) is None:
            continue
        content = path.read_bytes()
        if b"<!-- evospec-data:start -->" in content or b"<!-- evospec-data:end -->" in content:
            return True
    return False


def _project_pending(
    store: ContinuityPersistence, state: ContinuityStateData,
) -> dict[str, object]:
    result: dict[str, object] = {
        "mode": "COMPOSED", "revision": state["revision"],
        "status": "RECOVERY_REQUIRED", "next_action": "resume",
        "execution": state["execution"],
        "execution_feature_id": _task_owner(
            state, state["execution"]["task_id"],
        ) if state["execution"] is not None else None,
        "active_attempt": (
            None if state["execution"] is None
            else _active_attempt_projection(state)
        ),
    }
    recovery = store.pending_resolution_preview(state)
    if recovery is not None:
        result["publication_recovery"] = recovery
        result["next_action"] = (
            "inspect current and intended bytes; explicitly preserve every third-version path "
            "or repair it, then refresh status"
        )
    return result


def _report_attempt_state_only(
    store: ContinuityPersistence, state: ContinuityStateData,
    expected_revision: int | None, session_id: str, data: dict[str, object],
) -> dict[str, object]:
    _expect_revision(expected_revision, state["revision"])
    _keys(data, {
        "feature_id", "task_id", "phase", "report", "result", "mandate_id",
    })
    execution = state["execution"]
    if execution is None or execution["task_id"] != data.get("task_id"):
        raise ContinuityError("NO_ACTIVE_ATTEMPT", "no matching checkpointed Task attempt exists")
    owner = _task_owner(state, execution["task_id"])
    mandate = _require_mandate_id(state, owner, data)
    active_attempt = _active_attempt(state)
    if mandate is not None and active_attempt.get("mandate_id") != mandate["id"]:
        raise ContinuityError(
            "MANDATE_MISMATCH", "the checkpoint was not started under this Feature mandate",
        )
    if execution["session_id"] != session_id:
        raise ContinuityError(
            "OUTCOME_UNKNOWN",
            "only the originating host session may report this checkpointed attempt",
        )
    if data.get("feature_id") is not None and data.get("feature_id") != owner:
        raise ContinuityError("TASK_MISMATCH", "report must target the Feature owning the checkpointed Task")
    if data.get("phase") != "report":
        raise ContinuityError("INVALID_REQUEST", "state-only reporting accepts only phase=report")
    result = data.get("result")
    if result not in ("SUCCEEDED", "BLOCKED", "UNKNOWN"):
        raise ContinuityError("INVALID_REQUEST", "result must be SUCCEEDED, BLOCKED, or UNKNOWN")
    statement = _text(data.get("report"), "report", maximum=4096)
    candidate = _copy_state(state)
    entry = _active_attempt(candidate)
    if entry["session_id"] != execution["session_id"]:
        raise ContinuityError("CORRUPT_CONTINUITY", "checkpoint and Task attempt sessions do not match")
    entry["result"] = statement
    entry["result_status"] = result
    entry["status"] = "REPORTED" if result != "UNKNOWN" else "OUTCOME_UNKNOWN"
    if result != "UNKNOWN":
        candidate["execution"] = None
        entry["evidence_ref"] = (
            f"session:{session_id}:task:{execution['task_id']}:attempt:{entry['attempt']}"
        )
        entry["reported_revision"] = state["revision"] + 1
        entry["reported_by_session_id"] = session_id
        try:
            entry["reported_input_scope"] = _continuity_input_scope(store.root, candidate)
        except ContinuityError:
            pass
    return _publish_state_only(store, state, candidate, session_id)


def _reconcile_attempt_state_only(
    store: ContinuityPersistence, state: ContinuityStateData,
    expected_revision: int | None, session_id: str, data: dict[str, object],
) -> dict[str, object]:
    _expect_revision(expected_revision, state["revision"])
    _keys(data, {
        "feature_id", "reconcile", "task_id", "evidence_ref", "detour_summary",
        "mandate_id",
    })
    execution = state["execution"]
    if execution is None:
        raise ContinuityError("NO_ACTIVE_ATTEMPT", "there is no unresolved Task attempt to reconcile")
    if data.get("reconcile") not in ("COMPLETE", "INCOMPLETE"):
        raise ContinuityError(
            "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION",
            "resume requires explicit COMPLETE or INCOMPLETE reconciliation with evidence_ref",
        )
    if data.get("task_id") != execution["task_id"]:
        raise ContinuityError("TASK_MISMATCH", "reconciliation must name the exact checkpointed Task")
    owner = _task_owner(state, execution["task_id"])
    mandate = _require_mandate_id(state, owner, data)
    if (
        mandate is not None
        and _active_attempt(state).get("mandate_id") != mandate["id"]
    ):
        raise ContinuityError(
            "MANDATE_MISMATCH", "the checkpoint was not started under this Feature mandate",
        )
    if data.get("feature_id") is not None and data.get("feature_id") != owner:
        raise ContinuityError("TASK_MISMATCH", "reconciliation must target the Feature owning the checkpointed Task")
    if data.get("detour_summary") is not None:
        raise ContinuityError(
            "DOCUMENT_DIVERGED",
            "state-only Task reconciliation cannot publish a detour decision; reconcile documents, then resume the detour",
        )
    pending_detours = [item for item in state["detours"] if item["result"] is None]
    if pending_detours and (
        owner is None
        or len(pending_detours) != 1
        or pending_detours[0]["feature_id"] != owner
        or pending_detours[0]["return_task_id"] != execution["task_id"]
    ):
        raise ContinuityError(
            "DETOUR_OUTCOME_REQUIRED",
            "resolve unrelated pending detours separately; only preserve a detour returning to the checkpointed Task",
        )
    evidence_ref = _text(data.get("evidence_ref"), "evidence_ref", maximum=2048)
    candidate = _copy_state(state)
    entry = _active_attempt(candidate)
    entry["status"] = (
        "RECONCILED_COMPLETE"
        if data["reconcile"] == "COMPLETE"
        else "RECONCILED_INCOMPLETE"
    )
    entry["result_status"] = cast(str, data["reconcile"])
    entry["evidence_ref"] = evidence_ref
    entry["result"] = entry.get("result") or "Outcome reconciled explicitly after interruption"
    entry["reported_revision"] = state["revision"] + 1
    entry["reported_by_session_id"] = session_id
    try:
        entry["reported_input_scope"] = _continuity_input_scope(store.root, candidate)
    except ContinuityError:
        pass
    candidate["execution"] = None
    return _publish_state_only(store, state, candidate, session_id)


def _publish_state_only(
    store: ContinuityPersistence, state: ContinuityStateData,
    candidate: ContinuityStateData, session_id: str,
) -> dict[str, object]:
    candidate["revision"] = state["revision"] + 1
    store.commit(state, candidate, {}, read_set={})
    saved = store.load()
    assert saved is not None
    return _project(store, saved, session_id)


def _task_owner(state: ContinuityStateData, task_id: str) -> str | None:
    metadata = state.get("identity_metadata", {}).get(task_id)
    if (
        metadata is None or metadata.get("kind") != "T"
        or not metadata.get("active")
    ):
        return None
    feature_id = metadata.get("feature_id")
    return feature_id if isinstance(feature_id, str) and feature_id in state["features"] else None


def _feature_mandate(
    state: ContinuityStateData, feature_id: str,
) -> ContinuityMandate | None:
    mandates = state.get("mandates")
    if mandates is not None:
        return mandates.get(feature_id)
    legacy = state.get("mandate")
    return legacy if legacy is not None and legacy["feature_id"] == feature_id else None


def _current_mandates(
    state: ContinuityStateData,
) -> dict[str, ContinuityMandate]:
    current = dict(state.get("mandates", {}))
    legacy = state.get("mandate")
    if legacy is not None:
        current.setdefault(legacy["feature_id"], legacy)
    return current


def _mutable_mandates(
    state: ContinuityStateData,
) -> dict[str, ContinuityMandate]:
    current = _current_mandates(state)
    cast(dict[str, object], cast(object, state)).pop("mandate", None)
    state["mandates"] = current
    state.setdefault("mandate_history", [])
    return current


def _validate_mandate_request(
    state: ContinuityStateData, feature_id: str, data: dict[str, object],
    feature: ContinuityFeatureDocument, tasks: list[ContinuityTaskDocument],
    *, check_scope: bool = True, allow_unbound_recovery: bool = False,
) -> ContinuityMandate | None:
    mandate = _feature_mandate(state, feature_id)
    supplied = data.get("mandate_id")
    if mandate is None:
        if supplied is not None:
            if any(item["id"] == supplied for item in _current_mandates(state).values()):
                raise ContinuityError(
                    "FEATURE_MISMATCH",
                    "the supplied mandate belongs to a different Feature",
                )
            raise ContinuityError("MANDATE_REQUIRED", "there is no active implementation mandate for this Feature")
        return None
    if supplied != mandate["id"]:
        raise ContinuityError(
            "MANDATE_MISMATCH", "the exact current Core mandate ID for this Feature is required",
        )
    if check_scope and not allow_unbound_recovery:
        if "outcome_digest" not in mandate:
            raise ContinuityError(
                "MANDATE_STALE_SCOPE",
                "legacy mandate has no normative Feature Outcome binding; reconcile and explicitly replace it before new effects",
            )
        if not _mandate_scope_matches(mandate, feature):
            raise ContinuityError(
                "MANDATE_STALE_SCOPE",
                "accepted Feature Outcome, criterion, or Increment outcome changed; explicitly reconcile and start a new mandate",
            )
        increment_ids = {item["id"] for item in mandate["increments"]}
        criterion_ids = {item["id"] for item in mandate["criteria"]}
        if any(
            (
                task["increment_id"] is not None
                and task["increment_id"] not in increment_ids
            )
            or not set(task["criteria_ids"]) <= criterion_ids
            for task in tasks
        ):
            raise ContinuityError(
                "MANDATE_STALE_SCOPE", "a current Task falls outside the bound Increment and criterion IDs",
            )
    return mandate


def _require_mandate_id(
    state: ContinuityStateData, feature_id: str | None,
    data: dict[str, object],
) -> ContinuityMandate | None:
    supplied = data.get("mandate_id")
    mandate = (
        _feature_mandate(state, feature_id)
        if feature_id is not None
        else next(
            (item for item in _current_mandates(state).values() if item["id"] == supplied),
            None,
        )
    )
    if mandate is None:
        if supplied is not None:
            if any(item["id"] == supplied for item in _current_mandates(state).values()):
                raise ContinuityError(
                    "FEATURE_MISMATCH",
                    "the supplied mandate belongs to a different Feature",
                )
            raise ContinuityError("MANDATE_REQUIRED", "there is no active implementation mandate")
        return None
    if supplied != mandate["id"]:
        raise ContinuityError(
            "MANDATE_MISMATCH", "the exact active Core mandate ID is required",
        )
    if feature_id is not None and feature_id != mandate["feature_id"]:
        raise ContinuityError(
            "FEATURE_MISMATCH", "the operation must target the Feature bound to its mandate",
        )
    return mandate


def _mandate_allows_task(
    mandate: ContinuityMandate, task: ContinuityTaskDocument,
) -> bool:
    criteria = {item["id"] for item in mandate["criteria"]}
    increments = {item["id"] for item in mandate["increments"]}
    return (
        task["increment_id"] is None or task["increment_id"] in increments
    ) and set(task["criteria_ids"]) <= criteria


def _resolve_pending_publication(
    store: ContinuityPersistence, state: ContinuityStateData,
    data: dict[str, object],
) -> None:
    token = data.get("publication_resolution")
    paths_value = data.get("preserve_current_paths")
    if not isinstance(token, str) or re.fullmatch(r"pubres-v1-[0-9a-f]{64}", token) is None:
        raise ContinuityError("INVALID_REQUEST", "publication_resolution must be the current status token")
    if type(paths_value) is not list or any(not isinstance(path, str) for path in paths_value):
        raise ContinuityError("INVALID_REQUEST", "preserve_current_paths must be a list of exact managed paths")
    paths = cast(list[str], paths_value)
    if not paths or len(paths) != len(set(paths)):
        raise ContinuityError("INVALID_REQUEST", "preserve_current_paths must be nonempty and unique")
    pending = state["pending"]
    assert pending is not None
    next_state = cast(ContinuityStateData, cast(object, pending["next_state"]))
    writes = {item["path"]: item for item in pending["writes"]}
    for relative in paths:
        write = writes.get(relative)
        if write is None:
            continue
        current = store.read_document_bytes(relative)
        if current is None:
            raise ContinuityError(
                "PENDING_RESOLUTION_UNSAFE",
                f"{relative} is missing; merge the queued document into ordinary Markdown and refresh status",
            )
        intended = write["content"].encode("utf-8")
        if relative == "specs/README.md":
            issue = _pending_index_preservation_issue(current, next_state)
        else:
            parts = relative.split("/")
            if len(parts) != 3:
                raise ContinuityError("UNSAFE_PATH", "publication resolution path is not a Feature document")
            feature_matches = [
                feature_id for feature_id, feature_state in next_state["features"].items()
                if feature_state["directory"] == parts[1]
            ]
            if len(feature_matches) != 1:
                raise ContinuityError("CORRUPT_CONTINUITY", "pending document has no unique Feature owner")
            feature_id = feature_matches[0]
            spec_path = f"specs/{parts[1]}/spec.md"
            spec_write = writes.get(spec_path)
            intended_spec = (
                None if spec_write is None else spec_write["content"].encode("utf-8")
            )
            current_spec = store.read_document_bytes(spec_path)
            if current_spec is None:
                current_spec = intended_spec
            if intended_spec is None:
                intended_spec = current_spec

            def parse_feature(content: bytes | None) -> dict[str, object] | None:
                if content is None:
                    return None
                if b"<!-- evospec-data:" in content:
                    return None
                value = _parse_document_content(content, spec_path, "spec", feature_id)
                return value

            try:
                current_feature = parse_feature(current_spec)
                intended_feature = parse_feature(intended_spec)
            except (ContinuityError, KeyError, TypeError, ValueError) as error:
                raise ContinuityError(
                    "PENDING_RESOLUTION_UNSAFE",
                    f"{relative} cannot be safely preserved; merge valid Feature Markdown and refresh status: {error}",
                ) from error
            issue = _pending_document_preservation_issue(
                relative, feature_id, current, intended,
                current_feature=current_feature,
                intended_feature=intended_feature,
            )
        if issue is not None:
            raise ContinuityError("PENDING_RESOLUTION_UNSAFE", f"{relative}: {issue}")
    store.resolve_pending_preserving_current(state, token, paths)


def _reconcile_documents(
    store: ContinuityPersistence, state: ContinuityStateData,
    session_id: str, data: dict[str, object],
) -> dict[str, object]:
    _keys(data, {
        "feature_id", "retire_ids", "reopen_feature_id", "reopen_reason",
        "mandate_id",
    })
    feature_id = data.get("feature_id")
    if not isinstance(feature_id, str) or feature_id not in state["features"]:
        raise ContinuityError(
            "FEATURE_REQUIRED", "reconcile requires the exact current Feature ID",
        )
    _require_mandate_id(state, feature_id, data)
    if _has_legacy_documents(store.root, state):
        raise ContinuityError(
            "UPGRADE_REQUIRED",
            "JSON-in-Markdown documents are not human authority; use explicit upgrade_markdown first",
        )
    try:
        parsed = _parse_all_documents(store.root, state, feature_id)
    except (ContinuityError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, ContinuityError):
            raise
        raise ContinuityError("DOCUMENT_CONFLICT", "edited Feature documents are ambiguous or invalid") from error
    candidate = _copy_state(state)
    metadata = candidate.get("identity_metadata")
    if not isinstance(metadata, dict) or not metadata:
        raise ContinuityError(
            "UPGRADE_REQUIRED", "typed identity metadata is missing; upgrade clean composed Markdown first",
        )
    identity_metadata = cast(dict[str, ContinuityIdentityMetadata], metadata)
    aliases: dict[str, str] = {}
    declarations: list[tuple[str, dict[str, object], str]] = []
    feature = cast(dict[str, object], parsed["spec"])
    tasks = cast(list[dict[str, object]], parsed["tasks"])
    decisions = cast(list[dict[str, object]], parsed["decisions"])
    declarations.extend(
        (cast(str, item["id"]), item, "C")
        for item in cast(list[dict[str, object]], feature["criteria"])
    )
    declarations.extend(
        (cast(str, item["id"]), item, "S")
        for item in cast(list[dict[str, object]], feature["increments"])
    )
    declarations.extend((cast(str, item["id"]), item, "T") for item in tasks)
    declarations.extend((cast(str, item["id"]), item, "D") for item in decisions)
    if feature.get("id") != feature_id:
        raise ContinuityError("IDENTITY_CONFLICT", "edited specification changed its Feature identity")
    for identity, record, kind in declarations:
        if _ID.fullmatch(identity):
            prior = identity_metadata.get(identity)
            if (
                prior is None or prior["feature_id"] != feature_id
                or prior["kind"] != kind or not prior["active"]
            ):
                raise ContinuityError(
                    "IDENTITY_CONFLICT",
                    f"{identity} is unknown, retired, or belongs to another Feature; use a new typed alias",
                )
            if kind == "D" and prior["digest"] != _digest(record):
                raise ContinuityError(
                    "DECISION_HISTORY_IMMUTABLE",
                    f"decision {identity} is historical; append a new decision instead of editing it",
                )
        else:
            if identity in aliases:
                raise ContinuityError("IDENTITY_CONFLICT", f"author alias {identity} is declared more than once")
            aliases[identity] = _new_id(kind, candidate)
    for item in cast(list[dict[str, object]], feature["criteria"]):
        item["id"] = aliases.get(cast(str, item["id"]), item["id"])
    for item in cast(list[dict[str, object]], feature["increments"]):
        item["id"] = aliases.get(cast(str, item["id"]), item["id"])
    for task in tasks:
        task["id"] = aliases.get(cast(str, task["id"]), cast(str, task["id"]))
        increment = task["increment_id"]
        task["increment_id"] = None if increment is None else aliases.get(cast(str, increment), increment)
        task["criteria_ids"] = [
            aliases.get(cast(str, item), cast(str, item))
            for item in cast(list[object], task["criteria_ids"])
        ]
        task["depends_on"] = [
            aliases.get(cast(str, item), cast(str, item))
            for item in cast(list[object], task["depends_on"])
        ]
    for item in decisions:
        item["id"] = aliases.get(cast(str, item["id"]), cast(str, item["id"]))

    current_ids = {
        cast(str, feature["id"]),
        *(cast(str, item["id"]) for item in cast(list[dict[str, object]], feature["criteria"])),
        *(cast(str, item["id"]) for item in cast(list[dict[str, object]], feature["increments"])),
        *(cast(str, item["id"]) for item in tasks),
        *(cast(str, item["id"]) for item in decisions),
    }
    prior_active = {
        identity for identity, item in identity_metadata.items()
        if item["feature_id"] == feature_id and item["active"]
    }
    retired = prior_active - current_ids
    requested_retired = data.get("retire_ids", [])
    if type(requested_retired) is not list or any(
        not isinstance(item, str) or _ID.fullmatch(item) is None
        for item in requested_retired
    ) or len(requested_retired) != len(set(requested_retired)):
        raise ContinuityError("INVALID_REQUEST", "retire_ids must be a unique list of exact stable IDs")
    if set(requested_retired) != retired:
        raise ContinuityError(
            "RETIRE_IDS_REQUIRED",
            "removed identities require the exact retire_ids list; identities are never inferred or reused",
        )
    if any(identity.startswith(("F-", "D-")) for identity in retired):
        raise ContinuityError(
            "IDENTITY_RETIREMENT_BLOCKED",
            "Feature and decision history identities cannot be retired",
        )
    execution = candidate["execution"]
    if execution is not None and execution["task_id"] in retired:
        raise ContinuityError("EXECUTION_BUSY", "reconcile cannot retire the checkpointed Task")
    if execution is not None:
        raise ContinuityError(
            "EXECUTION_BUSY", "resolve the checkpointed Task before reconciling semantic document edits",
        )
    for task in tasks:
        task_id = cast(str, task["id"])
        if task_id not in candidate["task_states"]:
            if task_id in aliases.values():
                candidate["task_states"][task_id] = {"attempts": 0}
            else:
                raise ContinuityError("CORRUPT_CONTINUITY", "current Task has no operational attempt counter")

    paths = {
        name: f"specs/{state['features'][feature_id]['directory']}/{name}.md"
        for name in ("spec", "plan", "tasks", "decisions")
    }
    read_set: dict[str, str | None] = {}
    baseline = state.get("semantic_digests", {})
    parsed_docs = {
        "spec": feature,
        "plan": cast(dict[str, object], parsed["plan"]),
        "tasks": {"feature_id": feature_id, "tasks": tasks},
        "decisions": {"feature_id": feature_id, "decisions": decisions},
    }
    semantic_changed = any(
        (
            baseline.get(paths[name]) != _semantic_digest(name, cast(dict[str, object], value))
        )
        for name, value in parsed_docs.items()
        if cast(dict[str, str | None], parsed["bytes_digests"]).get(paths[name]) is not None
    ) or any(
        baseline.get(paths[name]) is not None
        and cast(dict[str, str | None], parsed["bytes_digests"]).get(paths[name]) is None
        for name in ("plan", "decisions")
    )
    feature_state = candidate["features"][feature_id]
    reopen_feature = data.get("reopen_feature_id")
    if feature_state["status"] == "CLOSED" and semantic_changed:
        if reopen_feature != feature_id:
            raise ContinuityError(
                "REOPEN_INTENT_REQUIRED",
                "semantic edits to a closed Feature require reopen_feature_id to equal its exact Feature ID",
            )
        previous = next(
            (
                item for item in reversed(candidate["closures"])
                if item["feature_id"] == feature_id and item["cycle"] == feature_state["cycle"]
            ),
            None,
        )
        if previous is None:
            raise ContinuityError("CORRUPT_CONTINUITY", "closed Feature has no current closure receipt")
        reopen: ContinuityReopen = {
            "feature_id": feature_id, "cycle": feature_state["cycle"] + 1,
            "previous_closure_id": previous["id"], "session_id": session_id,
            "reason": _text(
                data.get("reopen_reason"), "reopen_reason", maximum=2048,
            ),
            "revision": state["revision"] + 1,
        }
        candidate["reopens"].append(reopen)
        feature_state["cycle"] += 1
        feature_state["status"] = "OPEN"
    elif reopen_feature is not None or data.get("reopen_reason") is not None:
        raise ContinuityError(
            "INVALID_REQUEST",
            "reopen intent is accepted only for semantic edits to a closed Feature",
        )

    for identity in retired:
        identity_metadata[identity]["active"] = False
    candidate["identity_metadata"] = identity_metadata
    present = cast(dict[str, str | None], parsed["bytes_digests"])
    observed_semantics = cast(
        dict[str, str | None], parsed["semantic_digests"],
    )
    writes: dict[str, tuple[bytes, str | None]] = {}
    candidate["documents"] = dict(state["documents"])
    semantic_digests = dict(state.get("semantic_digests", {}))
    try:
        reconcile_input_snapshot = _continuity_input_scope(store.root, candidate)
    except ContinuityError:
        reconcile_input_snapshot = None
    checklist_state = cast(
        ContinuityStateData,
        cast(object, {**candidate, "input_snapshot": reconcile_input_snapshot}),
    )
    for name, relative in paths.items():
        observed_digest = present[relative]
        read_set[relative] = observed_digest
        if observed_digest is None:
            candidate["documents"].pop(relative, None)
            semantic_digests.pop(relative, None)
            continue
        path = store.root / Path(relative)
        content = path.read_bytes()
        if sha256(content).hexdigest() != observed_digest:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} changed while reconciliation was reading it")
        updated = _replace_document_aliases(content, aliases)
        if name == "tasks":
            updated = _refresh_generated_checklist(
                updated, checklist_state, feature_id, feature,
                cast(list[ContinuityTaskDocument], cast(object, tasks)),
            )
        writes[relative] = (updated, observed_digest)
    read_set["specs/README.md"] = _file_digest(store.root / "specs" / "README.md")
    index_writes = _index_write_selected(
        store.root, candidate, feature_id, cast(str, feature["title"]),
    )
    writes.update(index_writes)
    candidate["documents"] = {
        path: digest for path, digest in candidate["documents"].items()
        if not (
            path.startswith(f"specs/{state['features'][feature_id]['directory']}/")
            and path.rsplit("/", 1)[-1] in ("spec.md", "plan.md", "tasks.md", "decisions.md")
            and present.get(path) is None
        )
    }
    candidate["semantic_digests"] = semantic_digests
    _refresh_document_metadata(candidate, writes)
    candidate["revision"] = state["revision"] + 1
    document_drift = any(
        present.get(relative) != state["documents"].get(relative)
        or observed_semantics.get(relative) != state.get("semantic_digests", {}).get(relative)
        for relative in paths.values()
    ) or read_set["specs/README.md"] != state["documents"].get("specs/README.md")
    if not document_drift and not semantic_changed:
        raise ContinuityError("NO_DOCUMENT_CHANGES", "the selected documents already match Core state")
    store.commit(state, candidate, writes, read_set=read_set)
    saved = store.load()
    assert saved is not None
    return _project(store, saved, session_id)


def _upgrade_markdown(
    store: ContinuityPersistence, state: ContinuityStateData,
    session_id: str, data: dict[str, object],
) -> dict[str, object]:
    _keys(data, set())
    store.check_documents(state)
    if not _has_legacy_documents(store.root, state):
        if state.get("identity_metadata") and state.get("semantic_digests"):
            return _project(store, state, session_id)
        raise ContinuityError("UPGRADE_UNSAFE", "upgrade requires clean JSON-in-Markdown source documents")
    candidate = _copy_state(state)
    writes: dict[str, tuple[bytes, str | None]] = {}
    for feature_id, feature_state in state["features"].items():
        directory = feature_state["directory"]
        expected = {
            f"specs/{directory}/{name}.md"
            for name in ("spec", "tasks", "plan", "decisions")
        }
        if not {path for path in state["documents"] if path.startswith(f"specs/{directory}/")} <= expected:
            raise ContinuityError("UPGRADE_UNSAFE", "legacy Feature has unexpected managed documents")
        required = {f"specs/{directory}/spec.md", f"specs/{directory}/tasks.md"}
        if not required <= set(state["documents"]):
            raise ContinuityError("UPGRADE_UNSAFE", "legacy Feature is missing required documents")
        legacy_paths = [
            path for path in expected
            if path in state["documents"]
        ]
        if any(
            not (store.root / Path(path)).is_file()
            or b"<!-- evospec-data:start -->" not in (store.root / Path(path)).read_bytes()
            or b"<!-- evospec-data:end -->" not in (store.root / Path(path)).read_bytes()
            for path in legacy_paths
        ):
            raise ContinuityError(
                "UPGRADE_UNSAFE", "mixed or malformed Markdown format; no document was overwritten",
            )
        feature = _read_feature(store.root, state, feature_id)
        tasks = _read_tasks(store.root, state, feature_id)
        plan = _read_plan(store.root, state, feature_id)["plan"]
        decisions = _read_decisions(store.root, state, feature_id)
        docs = _initial_documents(
            cast(ContinuityFeatureDocument, feature),
            tasks,
            plan,
        )
        if f"specs/{directory}/plan.md" in state["documents"]:
            docs["plan"] = {"feature_id": feature_id, "plan": plan}
        if (
            f"specs/{directory}/decisions.md" in state["documents"]
            or decisions
        ):
            docs["decisions"] = {
                "feature_id": feature_id,
                "decisions": _plain_records(decisions),
            }
        writes.update(_writes_for_feature(store.root, state, feature_id, docs))
    _refresh_document_metadata(candidate, writes)
    candidate["revision"] = state["revision"] + 1
    store.commit(
        state, candidate, writes,
        read_set={path: digest for path, digest in state["documents"].items()},
    )
    saved = store.load()
    assert saved is not None
    return _project(store, saved, session_id)


def _semantic_digest(name: str, value: dict[str, object]) -> str:
    return sha256(
        (name + "\0" + json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )).encode("utf-8")
    ).hexdigest()


def _file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        if path.is_symlink() or not path.is_file():
            raise ContinuityError("UNSAFE_PATH", f"{path.name} is not a regular file")
        return sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ContinuityError("DOCUMENT_UNREADABLE", f"{path.name} cannot be read") from error


def _mandate_scope_matches(
    mandate: ContinuityMandate, feature: ContinuityFeatureDocument,
) -> bool:
    return (
        feature["id"] == mandate["feature_id"]
        and mandate.get("outcome_digest") == _digest(feature["outcome"])
        and mandate["criteria"] == [
            {"id": item["id"], "digest": _digest(item)}
            for item in feature["criteria"]
        ]
        and mandate["increments"] == [
            {
                "id": item["id"],
                "digest": _digest({"id": item["id"], "outcome": item["outcome"]}),
            }
            for item in feature["increments"]
        ]
    )


def _implementation_projection(
    state: ContinuityStateData, mandate: ContinuityMandate,
    feature: ContinuityFeatureDocument | None,
    tasks: list[ContinuityTaskDocument] | None,
    input_snapshot: ContinuityInputScope | None,
) -> dict[str, object]:
    view: dict[str, object] = {
        "id": mandate["id"], "mode": mandate["mode"],
        "feature_id": mandate["feature_id"],
        "source_revision": mandate["source_revision"],
        "recorded_revision": mandate["recorded_revision"],
        "started_by_session_id": mandate["session_id"],
        "close_feature": mandate["close_feature"],
        "outcome_digest": mandate.get("outcome_digest"),
        "criteria_scope": mandate["criteria"],
        "increments_scope": mandate["increments"],
        "criterion_ids": [item["id"] for item in mandate["criteria"]],
        "increment_ids": [item["id"] for item in mandate["increments"]],
        "next_task": None,
    }
    if feature is None or tasks is None or not _mandate_scope_matches(mandate, feature):
        view.update({
            "status": "STALE_SCOPE",
            "stale_reason": (
                "legacy mandate has no normative Feature Outcome binding"
                if "outcome_digest" not in mandate
                else "accepted Outcome, criterion, or Increment definition is unavailable or changed"
            ),
            "coverage": {
                "criteria_total": len(mandate["criteria"]), "criteria_passing": 0,
                "tasks_total": 0, "tasks_completed": 0,
                "increments_total": len(mandate["increments"]), "increments_finished": 0,
            },
            "criteria": [{"id": item["id"], "status": "STALE"} for item in mandate["criteria"]],
            "increments": [
                {"id": item["id"], "status": "STALE", "task_ids": []}
                for item in mandate["increments"]
            ],
            "tasks": [],
        })
        return view

    criterion_ids = {item["id"] for item in mandate["criteria"]}
    increment_ids = {item["id"] for item in mandate["increments"]}
    if any(
        (
            task["increment_id"] is not None
            and task["increment_id"] not in increment_ids
        )
        or not set(task["criteria_ids"]) <= criterion_ids
        for task in tasks
    ):
        view.update({
            "status": "STALE_SCOPE",
            "stale_reason": "a Task is not within the bound Increment and criterion IDs",
        })
        return view

    current_state = _continuity_state_view(state, input_snapshot)
    current_feature = _continuity_feature_view(feature, state, tasks)
    task_views = tuple(_continuity_task_view(item) for item in tasks)
    checks = continuity_current_checks(
        current_state, current_feature, tasks=task_views,
    )
    task_reports = continuity_task_reports(
        current_state, current_feature, task_views,
    )
    task_projection: list[dict[str, object]] = []
    by_increment: dict[str | None, list[dict[str, object]]] = {}
    linked_tasks: dict[str, list[str]] = {identity: [] for identity in criterion_ids}
    for task, task_view in zip(tasks, task_views, strict=True):
        status = continuity_task_status(current_state, current_feature, task_view, task_views)
        projection: dict[str, object] = {
            "id": task["id"], "increment_id": task["increment_id"],
            "status": status,
            "eligible": continuity_task_eligible(
                current_state, current_feature, task_views, task_view,
            ),
        }
        task_projection.append(projection)
        by_increment.setdefault(task["increment_id"], []).append(projection)
        for identity in task["criteria_ids"]:
            linked_tasks[identity].append(task["id"])

    criteria: list[dict[str, object]] = []
    for reference in mandate["criteria"]:
        identity = reference["id"]
        criterion = next(
            (item for item in feature["criteria"] if item["id"] == identity),
            None,
        )
        evidence = checks.get(identity)
        historical = None
        if evidence is None and criterion is not None:
            historical = _continuity_historical_check(
                state, feature, criterion, input_snapshot, task_views,
                mandate["feature_id"],
            )
        report = task_reports.get(identity)
        evidence_status = (
            evidence.get("status", "STALE") if evidence is not None
            else "STALE" if historical is not None and report is None
            else "REPORTED" if report is not None
            else "PENDING"
        )
        criteria.append({
            "id": identity,
            "status": {
                "VERIFIED": "PASSING",
                "FAILED": "FAILING",
                "REPORTED": "REPORTED",
                "STALE": "STALE",
            }.get(cast(str, evidence_status), "PENDING"),
            "evidence_status": evidence_status,
            "evidence": evidence or report or historical,
            **({"historical_evidence": historical} if historical is not None else {}),
        })
    increments: list[dict[str, object]] = []
    for reference in mandate["increments"]:
        increment_tasks = by_increment.get(reference["id"], [])
        statuses = [item["status"] for item in increment_tasks]
        if not increment_tasks:
            status = "PENDING"
        elif "OUTCOME_UNKNOWN" in statuses:
            status = "UNKNOWN_EFFECT"
        elif any(item in ("BLOCKED", "CANCELED") for item in statuses):
            status = "BLOCKED"
        elif all(item == "COMPLETED" for item in statuses):
            status = "FINISHED"
        elif "REPORTED" in statuses:
            status = "REPORTED"
        elif any(
            item["status"] == "READY" and not item["eligible"]
            for item in increment_tasks
        ):
            status = "INELIGIBLE"
        else:
            status = "PENDING"
        increments.append({
            "id": reference["id"], "status": status,
            "task_ids": [item["id"] for item in increment_tasks],
        })

    missing_increment_tasks = [
        item["id"] for item in increments if not item["task_ids"]
    ]
    if mandate["increments"]:
        ordered_groups = (
            ([by_increment[None]] if by_increment.get(None) else [])
            + [by_increment.get(item["id"], []) for item in mandate["increments"]]
        )
    else:
        ordered_groups = [by_increment.get(None, [])]
    next_task: str | None = None
    for group in ordered_groups:
        if not group:
            break
        incomplete = next((item for item in group if item["status"] != "COMPLETED"), None)
        if incomplete is None:
            continue
        if incomplete["status"] == "READY" and incomplete["eligible"]:
            next_task = cast(str, incomplete["id"])
        break

    unassigned_criteria = [
        identity for identity, linked in linked_tasks.items() if not linked
    ]
    passing_count = sum(item["status"] == "PASSING" for item in criteria)
    failed_count = sum(item["status"] == "FAILING" for item in criteria)
    reported_count = sum(item["status"] == "REPORTED" for item in criteria)
    stale_count = sum(item["status"] == "STALE" for item in criteria)
    execution = state["execution"]
    execution_owner = (
        None if execution is None
        else _task_owner(state, execution["task_id"])
    )
    if execution is not None and execution_owner == mandate["feature_id"]:
        status = "UNKNOWN_EFFECT"
    elif mandate["feature_id"] in state["paused_features"]:
        status = "PAUSED"
    elif unassigned_criteria:
        status = "INELIGIBLE"
    elif any(item["status"] in ("BLOCKED", "CANCELED") for item in task_projection):
        status = "BLOCKED"
    elif any(item["status"] == "FAILING" for item in criteria):
        status = "BLOCKED"
    elif missing_increment_tasks:
        status = "PENDING"
    elif (
        all(item["status"] == "COMPLETED" for item in task_projection)
        and passing_count == len(mandate["criteria"])
        and all(item["status"] == "FINISHED" for item in increments)
    ):
        status = "FINISHED"
    elif any(item["status"] == "REPORTED" for item in task_projection):
        status = "REPORTED"
    elif next_task is None and any(
        item["status"] != "COMPLETED" for item in task_projection
    ):
        status = "INELIGIBLE"
    else:
        status = "PENDING"

    if execution is not None and execution_owner != mandate["feature_id"]:
        next_task = None
    view.update({
        "status": status, "criteria": criteria, "increments": increments,
        "tasks": task_projection, "next_task": next_task,
        "missing_increment_task_ids": missing_increment_tasks,
        "unassigned_criterion_ids": unassigned_criteria,
        "coverage": {
            "criteria_total": len(mandate["criteria"]),
            "criteria_passing": passing_count,
            "criteria_failed": failed_count,
            "criteria_reported": reported_count,
            "criteria_stale": stale_count,
            "tasks_total": len(task_projection),
            "tasks_completed": sum(
                item["status"] == "COMPLETED" for item in task_projection
            ),
            "increments_total": len(mandate["increments"]),
            "increments_finished": sum(item["status"] == "FINISHED" for item in increments),
        },
    })
    return view


def _project(
    store: ContinuityPersistence, state: ContinuityStateData, session_id: str,
) -> dict[str, object]:
    if state["pending"] is not None:
        return {
            "mode": "COMPOSED", "revision": state["revision"],
            "status": "RECOVERY_REQUIRED", "next_action": "resume",
        }
    root = store.root
    try:
        input_snapshot = _continuity_input_scope(root, state)
    except ContinuityError as error:
        input_snapshot = None
        input_snapshot_error = str(error)
    else:
        input_snapshot_error = None
    document_changes = _document_diagnostics(root, state, input_snapshot)
    parsed_documents = cast(
        dict[str, dict[str, object]],
        document_changes.pop("_parsed_documents", {}),
    )
    selected = state["session_features"].get(session_id) or state["default_feature_id"]
    mandate_history = list(state.get("mandate_history", []))
    if document_changes["identity_ref_conflicts"] or document_changes["semantic"]:
        selected_mandate = (
            None if selected is None else _feature_mandate(state, selected)
        )
        selected_documents = (
            None if selected is None else parsed_documents.get(selected)
        )
        result: dict[str, object] = {
            "mode": "COMPOSED", "revision": state["revision"],
            "status": (
                "DOCUMENT_CONFLICT"
                if document_changes["identity_ref_conflicts"]
                else "DOCUMENT_RECONCILIATION_REQUIRED"
            ),
            "document_changes": document_changes,
            "execution": state["execution"],
            "execution_feature_id": (
                None if state["execution"] is None
                else _task_owner(state, state["execution"]["task_id"])
            ),
            "active_attempt": (
                None if state["execution"] is None
                else _active_attempt_projection(state)
            ),
            "features": [],
            "selected_feature_id": selected,
            "selected_feature": None,
            "next_task": None,
            "mandate_history": mandate_history,
        }
        if selected_mandate is not None:
            selected_feature = (
                None if selected_documents is None
                else cast(ContinuityFeatureDocument, selected_documents["spec"])
            )
            selected_tasks = (
                None if selected_documents is None
                else cast(list[ContinuityTaskDocument], selected_documents["tasks"])
            )
            result["mandate"] = _implementation_projection(
                state, selected_mandate, selected_feature, selected_tasks,
                input_snapshot,
            )
        return result
    feature_views: list[dict[str, object]] = []
    result: dict[str, object] = {
        "mode": "COMPOSED", "revision": state["revision"],
        "default_feature_id": state["default_feature_id"],
        "selected_feature_id": selected,
        "selection_scope": "session" if session_id in state["session_features"] else "worktree",
        "features": feature_views,
        "execution": state["execution"],
        "execution_feature_id": (
            None if state["execution"] is None
            else _task_owner(state, state["execution"]["task_id"])
        ),
        "active_attempt": (
            None if state["execution"] is None
            else _active_attempt_projection(state)
        ),
        "document_changes": document_changes,
        "mandate_history": mandate_history,
    }
    if input_snapshot_error is not None:
        result["input_snapshot_error"] = input_snapshot_error
    for feature_id in sorted(state["features"]):
        parsed = parsed_documents[feature_id]
        feature = cast(ContinuityFeatureDocument, parsed["spec"])
        tasks = cast(list[ContinuityTaskDocument], parsed["tasks"])
        feature_state = state["features"][feature_id]
        feature_mandate = _feature_mandate(state, feature_id)
        mandate_id = None if feature_mandate is None else feature_mandate["id"]
        continuity_state = _continuity_state_view(state, input_snapshot)
        continuity_feature = _continuity_feature_view(feature, state, tasks)
        task_views = tuple(_continuity_task_view(item) for item in tasks)
        current_checks = continuity_current_checks(
            continuity_state, continuity_feature, tasks=task_views,
        )
        task_reports = continuity_task_reports(
            continuity_state, continuity_feature, task_views,
        )
        acceptance = _latest_acceptance(
            state, feature_id, feature_state["cycle"],
        )
        acceptance_current = bool(
            acceptance is not None
            and input_snapshot is not None
            and _acceptance_matches_current(
                state, acceptance, feature_id, feature, tasks, input_snapshot,
                mandate_id,
            )
        )
        close_blockers = list(continuity_close_blockers(
            continuity_state, feature_id,
            {"status": feature_state["status"]},
            continuity_feature, task_views,
        ))
        acceptance_consumed = bool(
            acceptance is not None
            and any(
                item.get("acceptance_id") == acceptance["id"]
                for item in state["closures"]
                if item["feature_id"] == feature_id
            )
        )
        if feature_state["status"] == "CLOSED":
            close_status = "CLOSED"
        elif acceptance is None:
            close_blockers.append("an explicit current Feature acceptance is required")
            close_status = "ACCEPTANCE_REQUIRED" if not close_blockers[:-1] else "BLOCKED"
        elif not acceptance_current:
            close_blockers.append("the latest Feature acceptance is stale")
            close_status = "ACCEPTANCE_STALE"
        else:
            close_status = "READY" if not close_blockers else "BLOCKED"
        task_projection: list[dict[str, object]] = []
        for task in tasks:
            continuity_task = _continuity_task_view(task)
            status = continuity_task_status(
                continuity_state, continuity_feature, continuity_task, task_views,
            )
            latest_event = next((
                item for item in reversed(state.get("task_events", []))
                if item["task_id"] == task["id"]
            ), None)
            task_projection.append({
                **task, "status": status,
                "attempts": state["task_states"][task["id"]]["attempts"],
                "attempt_history": [
                    item for item in state["attempts"]
                    if item["task_id"] == task["id"]
                ],
                "eligible": continuity_task_eligible(
                    continuity_state, continuity_feature, task_views, continuity_task,
                ),
                **({"latest_event": latest_event} if latest_event is not None else {}),
            })
        criterion_projection: list[dict[str, object]] = []
        pending_review_decisions: list[dict[str, object]] = []
        for criterion in feature["criteria"]:
            check = current_checks.get(criterion["id"])
            review_decision = (
                None if check is None else _current_review_decision(
                    state, feature_id, feature_state["cycle"], criterion,
                    check,
                    mandate_id,
                )
            )
            if (
                criterion["check_kind"] == "review"
                and check is not None
                and check.get("status") == "REPORTED"
                and (
                    review_decision is None
                    or review_decision["decision"] != "ACCEPTED"
                )
            ):
                pending_review: dict[str, object] = {
                    "criterion_id": criterion["id"],
                    "evidence_ref": check.get("evidence_ref"),
                    "reported_by_session_id": check.get("reported_by_session_id"),
                    "decision_required": True,
                }
                if review_decision is not None:
                    pending_review["decision"] = review_decision["decision"]
                    pending_review["review_decision_id"] = review_decision["id"]
                pending_review_decisions.append(pending_review)
            historical_check = None
            if check is None:
                historical_check = _continuity_historical_check(
                    state, feature, criterion, input_snapshot, task_views, feature_id,
                )
            task_report = task_reports.get(criterion["id"])
            evidence_status = (
                check.get("status", (
                    "VERIFIED" if check.get("passed")
                    else "STALE" if not check.get("fresh")
                    else "FAILED"
                )) if check is not None
                else "REPORTED" if task_report is not None
                else "STALE" if historical_check is not None
                else "PENDING"
            )
            criterion_projection.append({
                **criterion,
                "digest": _digest(criterion),
                "check": check or historical_check,
                "evidence_status": evidence_status,
                "evidence": check or task_report or historical_check,
                **(
                    {"reported_evidence": task_report}
                    if task_report is not None else {}
                ),
                **(
                    {"historical_evidence": historical_check}
                    if historical_check is not None and task_report is not None else {}
                ),
                **(
                    {"review_decision": review_decision}
                    if review_decision is not None else {}
                ),
                "purpose": criterion["description"],
                "method": criterion["check_kind"],
                "descriptor": criterion["check_target"],
            })
        feature_view: dict[str, object] = {
            "id": feature_id, "directory": feature_state["directory"],
            "kind": feature["kind"], "title": feature["title"],
            "outcome": feature["outcome"], "status": feature_state["status"],
            "cycle": feature_state["cycle"], "criteria": criterion_projection,
            "increments": feature["increments"], "tasks": task_projection,
            "plan": cast(dict[str, object], parsed["plan"])["plan"],
            "decisions": parsed["decisions"],
            "paused": feature_id in state["paused_features"],
            "pause_reason": state["pause_reasons"].get(feature_id),
            "detours": [
                item for item in state["detours"] if item["feature_id"] == feature_id
            ],
            "task_events": [
                item for item in state.get("task_events", [])
                if any(task["id"] == item["task_id"] for task in tasks)
            ],
            "closure_history": [
                item for item in state["closures"] if item["feature_id"] == feature_id
            ],
            "reopen_history": [
                item for item in state["reopens"] if item["feature_id"] == feature_id
            ],
            "acceptance": (
                None if acceptance is None else {
                    **acceptance,
                    "status": (
                        "CONSUMED" if acceptance_consumed
                        else "CURRENT" if acceptance_current else "STALE"
                    ),
                }
            ),
            "acceptance_history": [
                item for item in state.get("acceptances", [])
                if item["feature_id"] == feature_id
            ],
            "pending_review_decisions": pending_review_decisions,
            "close_readiness": {
                "status": close_status,
                "blockers": close_blockers,
            },
        }
        if feature_mandate is not None:
            feature_view["mandate"] = _implementation_projection(
                state, feature_mandate, feature, tasks, input_snapshot,
            )
        feature_views.append(feature_view)
    selected_feature = next(
        (feature for feature in feature_views if feature["id"] == selected), None
    )
    result["selected_feature"] = selected_feature
    selected_mandate = (
        None if selected is None else _feature_mandate(state, selected)
    )
    if selected_mandate is None:
        result["next_task"] = _next_task(selected_feature)
    else:
        mandate_documents = parsed_documents.get(selected_mandate["feature_id"])
        implementation = _implementation_projection(
            state, selected_mandate,
            None if mandate_documents is None else cast(
                ContinuityFeatureDocument, mandate_documents["spec"],
            ),
            None if mandate_documents is None else cast(
                list[ContinuityTaskDocument], mandate_documents["tasks"],
            ),
            input_snapshot,
        )
        result["mandate"] = implementation
        result["next_task"] = (
            implementation["next_task"]
            if selected == selected_mandate["feature_id"]
            and state["execution"] is None else None
        )
    return result


def _next_task(feature: object) -> str | None:
    if type(feature) is not dict or feature.get("paused") or feature.get("status") != "OPEN":
        return None
    return next(
        (
            task["id"] for task in feature["tasks"]
            if task["status"] == "READY" and task["eligible"]
        ),
        None,
    )


def _document_diagnostics(
    root: Path, state: ContinuityStateData,
    input_snapshot: ContinuityInputScope | None,
) -> dict[str, object]:
    semantic: set[str] = set()
    format_notes: set[str] = set()
    generated: set[str] = set()
    conflicts: list[dict[str, str]] = []
    parsed_documents: dict[str, dict[str, object]] = {}
    semantic_baseline = state.get("semantic_digests", {})
    identity_metadata = state.get("identity_metadata", {})
    for feature_id in sorted(state["features"]):
        try:
            parsed = _parse_all_documents(
                root, state, feature_id, input_snapshot,
            )
        except ContinuityError as error:
            conflicts.append({
                "feature_id": feature_id,
                "path": state["features"][feature_id]["directory"],
                "detail": str(error),
            })
            continue
        parsed_documents[feature_id] = parsed
        feature = cast(dict[str, object], parsed["spec"])
        identities: list[tuple[str, str]] = [(feature_id, "F")]
        identities.extend(
            (cast(str, item["id"]), "C")
            for item in cast(list[dict[str, object]], feature["criteria"])
        )
        identities.extend(
            (cast(str, item["id"]), "S")
            for item in cast(list[dict[str, object]], feature["increments"])
        )
        identities.extend(
            (cast(str, item["id"]), "T")
            for item in cast(list[dict[str, object]], parsed["tasks"])
        )
        identities.extend(
            (cast(str, item["id"]), "D")
            for item in cast(list[dict[str, object]], parsed["decisions"])
        )
        if identity_metadata:
            for identity, kind in identities:
                if _ID.fullmatch(identity):
                    metadata = identity_metadata.get(identity)
                    if (
                        metadata is None or metadata["feature_id"] != feature_id
                        or metadata["kind"] != kind or not metadata["active"]
                    ):
                        conflicts.append({
                            "feature_id": feature_id,
                            "path": state["features"][feature_id]["directory"],
                            "detail": (
                                f"{identity} is unknown, retired, or belongs to another "
                                "Feature or identity kind"
                            ),
                        })
                elif (
                    _ALIAS.fullmatch(identity) is None
                    or not identity.startswith(kind + "-")
                ):
                    conflicts.append({
                        "feature_id": feature_id,
                        "path": state["features"][feature_id]["directory"],
                        "detail": f"{identity} is not a stable identity or typed author alias",
                    })
        byte_digests = cast(dict[str, str | None], parsed["bytes_digests"])
        semantic_digests = cast(dict[str, str | None], parsed["semantic_digests"])
        generated_current = cast(dict[str, bool], parsed["generated_current"])
        for relative, actual in byte_digests.items():
            expected_bytes = state["documents"].get(relative)
            expected_semantics = semantic_baseline.get(relative)
            actual_semantics = semantic_digests[relative]
            if actual != expected_bytes:
                if expected_semantics is None and expected_bytes is None:
                    semantic.add(relative)
                elif expected_semantics is None:
                    conflicts.append({
                        "feature_id": feature_id, "path": relative,
                        "detail": "semantic baseline is unavailable; use explicit upgrade_markdown or inspect the legacy source",
                    })
                elif actual_semantics != expected_semantics:
                    semantic.add(relative)
                elif generated_current.get(relative) is not False:
                    format_notes.add(relative)
            elif (
                expected_semantics is not None
                and actual_semantics != expected_semantics
            ):
                semantic.add(relative)
            if relative in generated_current and not generated_current[relative]:
                generated.add(relative)
    readme = "specs/README.md"
    try:
        readme_path = root / Path(readme)
        actual_readme_digest = _file_digest(readme_path)
        actual_readme = (
            readme_path.read_bytes() if actual_readme_digest is not None else None
        )
        expected_readme = state["documents"].get(readme)
        if actual_readme_digest != expected_readme:
            if actual_readme is not None and expected_readme is not None:
                format_notes.add(readme)
            try:
                expected_generated = _index_write(root, state, allow_external=True)[readme][0]
            except ContinuityError as error:
                conflicts.append({"feature_id": "", "path": readme, "detail": str(error)})
            else:
                if actual_readme != expected_generated:
                    generated.add(readme)
                elif actual_readme is not None:
                    format_notes.add(readme)
    except OSError as error:
        conflicts.append({"feature_id": "", "path": readme, "detail": f"{readme} cannot be read: {error}"})
    return {
        "semantic": sorted(semantic),
        "format_notes": sorted(format_notes),
        "generated": sorted(generated),
        "identity_ref_conflicts": conflicts,
        "_parsed_documents": parsed_documents,
    }


def _continuity_historical_check(
    state: ContinuityStateData, feature: ContinuityFeatureDocument,
    criterion: ContinuityCriterionDocument,
    input_snapshot: ContinuityInputScope | None,
    tasks: tuple[ContinuityTaskView, ...], feature_id: str,
) -> dict[str, object] | None:
    receipt = next(
        (
            item for item in reversed(state["checks"])
            if item["criterion_id"] == criterion["id"]
        ),
        None,
    )
    if receipt is None:
        return None
    observed = receipt.get("observed_input_scope")
    current_mandate = _feature_mandate(state, feature_id)
    current_mandate_id = None if current_mandate is None else current_mandate["id"]
    current_task = next(
        (item for item in tasks if item["id"] == receipt["task_id"]),
        None,
    )
    if (
        receipt["criterion_digest"] != _digest(criterion)
        or receipt["check_kind"] != criterion["check_kind"]
        or receipt["check_target"] != criterion["check_target"]
        or receipt.get("purpose", criterion["description"]) != criterion["description"]
    ):
        stale_reason = "criterion_changed"
    elif (
        receipt.get("feature_id", feature_id) != feature_id
        or (
            receipt.get("feature_cycle") is not None
            and receipt.get("feature_cycle") != state["features"][feature_id]["cycle"]
        )
        or (
            receipt.get("feature_cycle") is None
            and state["features"][feature_id]["cycle"] != 1
        )
    ):
        stale_reason = "feature_cycle_changed"
    elif (
        receipt.get("mandate_id") != current_mandate_id
        or current_mandate is not None
        and (
            not _mandate_scope_matches(current_mandate, feature)
            or any(
                (
                    task["increment_id"] is not None
                    and task["increment_id"] not in {
                        item["id"] for item in current_mandate["increments"]
                    }
                )
                or not set(task["criteria_ids"]) <= {
                    item["id"] for item in current_mandate["criteria"]
                }
                for task in tasks
            )
        )
    ):
        stale_reason = "mandate_changed"
    elif (
        current_task is None
        or receipt.get("task_digest") != _digest(current_task)
    ):
        stale_reason = "task_changed"
    elif observed is None:
        stale_reason = "legacy_receipt"
    elif input_snapshot is None:
        stale_reason = "input_snapshot_unavailable"
    elif observed != input_snapshot:
        stale_reason = "input_changed"
    elif not any(
        task["id"] == receipt["task_id"]
        and criterion["id"] in task["criteria_ids"]
        for task in tasks
    ):
        stale_reason = "task_not_current"
    else:
        return None
    evidence_status = receipt.get("evidence_status")
    historical_passed = (
        evidence_status == "VERIFIED"
        if isinstance(evidence_status, str)
        else receipt.get("exit_code") == 0
    )
    result: dict[str, object] = {
        "passed": False, "fresh": False,
        "status": "STALE",
        "historical_passed": historical_passed,
        "check_kind": receipt["check_kind"],
        "check_target": receipt["check_target"],
        "task_id": receipt["task_id"],
        "reported_by_session_id": receipt["reported_by_session_id"],
        "recorded_revision": receipt["recorded_revision"],
        "stale_reason": stale_reason,
        "purpose": receipt.get("purpose", criterion["description"]),
        "method": receipt["check_kind"],
        "descriptor": receipt["check_target"],
    }
    for key in (
        "exit_code", "output_sha256", "mandate_id", "feature_id",
        "feature_cycle", "evidence_status", "source", "evidence_ref",
        "artifact_present", "artifact_sha256", "report",
    ):
        if key in receipt:
            result[key] = receipt.get(key)
    return result


def _active_attempt(state: ContinuityStateData) -> ContinuityAttempt:
    execution = state["execution"]
    if execution is None:
        raise ContinuityError("NO_ACTIVE_ATTEMPT", "no Task attempt is in progress")
    for attempt in reversed(state["attempts"]):
        if attempt["task_id"] == execution["task_id"] and attempt["attempt"] == execution["attempt"]:
            return attempt
    raise ContinuityError("CORRUPT_CONTINUITY", "active Task attempt is missing from history")


def _active_attempt_projection(state: ContinuityStateData) -> dict[str, object]:
    attempt = dict(_active_attempt(state))
    execution = state["execution"]
    assert execution is not None
    owner = _task_owner(state, execution["task_id"])
    if owner is not None:
        attempt["feature_id"] = owner
    return attempt


def _pending_detour(
    state: ContinuityStateData, feature_id: str,
) -> ContinuityDetour | None:
    return next(
        (
            item for item in state["detours"]
            if item["feature_id"] == feature_id and item["result"] is None
        ),
        None,
    )


def _execution_feature(root: Path, state: ContinuityStateData) -> str:
    execution = state["execution"]
    assert execution is not None
    owners = [
        feature_id for feature_id in state["features"]
        if any(task["id"] == execution["task_id"] for task in _read_tasks(root, state, feature_id))
    ]
    if len(owners) != 1:
        raise ContinuityError("CORRUPT_CONTINUITY", "checkpointed Task must belong to exactly one Feature")
    return owners[0]


def _new_feature(
    data: dict[str, object], kind: str, prior_state: ContinuityStateData,
) -> tuple[ContinuityFeatureDocument, list[ContinuityTaskDocument]]:
    _keys(data, {"title", "outcome", "criteria", "increments", "tasks", "plan"})
    title = _text(data.get("title"), "title", maximum=2048)
    outcome = _text(data.get("outcome"), "outcome", maximum=8192, multiline=True)
    criteria_raw = data.get("criteria")
    if type(criteria_raw) is not list or not criteria_raw or len(criteria_raw) > _MAX_CRITERIA:
        raise ContinuityError("INVALID_REQUEST", "a Feature needs 1 to 200 explicit acceptance criteria")
    allocated: set[str] = set()
    criteria = [_new_criterion(item, prior_state, allocated) for item in criteria_raw]
    if len({item["check_target"] for item in criteria}) > _MAX_CRITERIA:
        raise ContinuityError("INVALID_REQUEST", "criteria are invalid")
    increments_raw = data.get("increments", [])
    if type(increments_raw) is not list or len(increments_raw) > _MAX_INCREMENTS:
        raise ContinuityError("INVALID_REQUEST", "increments must be an array of at most 100 items")
    increments: list[ContinuityIncrementDocument] = [
        {
            "id": _new_id("S", prior_state, allocated),
            "title": _text(item.get("title") if type(item) is dict else None, "increment title", maximum=2048),
            "outcome": _text(item.get("outcome") if type(item) is dict else None, "increment outcome", maximum=4096, multiline=True),
        }
        for item in increments_raw
    ]
    if any(type(item) is not dict or set(item) != {"title", "outcome"} for item in increments_raw):
        raise ContinuityError("INVALID_REQUEST", "each increment needs exactly title and outcome")
    tasks_raw = data.get("tasks")
    if type(tasks_raw) is not list or not tasks_raw or len(tasks_raw) > _MAX_TASKS:
        raise ContinuityError("INVALID_REQUEST", "a Feature needs at least one Task (up to 1000)")
    task_records: list[ContinuityTaskDocument] = []
    for index, raw in enumerate(tasks_raw):
        if type(raw) is not dict or set(raw) - {
            "title", "description", "increment_index", "criteria", "depends_on",
        }:
            raise ContinuityError("INVALID_REQUEST", "Task fields are invalid")
        title_value = _text(raw.get("title"), "Task title", maximum=2048)
        description = _text(raw.get("description", ""), "Task description", maximum=4096, multiline=True, allow_empty=True)
        increment_index = raw.get("increment_index")
        if increment_index is not None and (
            type(increment_index) is not int or not 0 <= increment_index < len(increments)
        ):
            raise ContinuityError("INVALID_REQUEST", "Task increment_index is invalid")
        criterion_indexes = raw.get("criteria", list(range(len(criteria))))
        if type(criterion_indexes) is not list or not criterion_indexes or any(
            type(value) is not int or not 0 <= value < len(criteria) for value in criterion_indexes
        ):
            raise ContinuityError("INVALID_REQUEST", "Task criteria must name current criterion indexes")
        dependency_indexes = raw.get("depends_on", [index - 1] if index else [])
        if type(dependency_indexes) is not list or any(
            type(value) is not int or not 0 <= value < index for value in dependency_indexes
        ):
            raise ContinuityError("INVALID_REQUEST", "Task dependencies must refer to prior Task indexes")
        task_records.append({
            "id": _new_id("T", prior_state, allocated),
            "title": title_value, "description": description,
            "increment_id": None if increment_index is None else increments[increment_index]["id"],
            "criteria_ids": [criteria[value]["id"] for value in criterion_indexes],
            "depends_on": [task_records[value]["id"] for value in dependency_indexes],
        })
    record: ContinuityFeatureDocument = {
        "id": _new_id("F", prior_state, allocated), "kind": kind, "title": title, "outcome": outcome,
        "criteria": criteria, "increments": increments,
    }
    return record, task_records


def _new_criterion(
    raw: object, state: ContinuityStateData,
    allocated: set[str] | None = None,
) -> ContinuityCriterionDocument:
    if type(raw) is not dict or set(raw) - {"id", "description", "check_kind", "check_target"}:
        raise ContinuityError("INVALID_REQUEST", "criterion fields are invalid")
    description = _text(raw.get("description"), "criterion description", maximum=2048)
    kind = raw.get("check_kind")
    target = _text(raw.get("check_target"), "criterion check_target", maximum=512)
    if (
        not isinstance(kind, str)
        or kind not in CONTINUITY_CHECK_METHODS
        or not continuity_check_descriptor_valid(kind, target)
    ):
        raise ContinuityError(
            "INVALID_REQUEST",
            "criterion check must use an allowlisted test, exact repository artifact path, or descriptive method",
        )
    return {
        "id": _new_id("C", state, allocated),
        "description": description, "check_kind": kind, "check_target": target,
    }


def _amend_feature(
    feature: ContinuityFeatureDocument, data: dict[str, object],
    state: ContinuityStateData,
) -> ContinuityFeatureDocument:
    result = deepcopy(feature)
    if "outcome" in data:
        result["outcome"] = _text(data["outcome"], "outcome", maximum=8192, multiline=True)
    criteria = data.get("criteria", [])
    if type(criteria) is not list or len(criteria) > _MAX_CRITERIA:
        raise ContinuityError("INVALID_REQUEST", "criteria amendment must be an array")
    current = {item["id"]: item for item in result["criteria"]}
    allocated: set[str] = {
        item["id"] for item in (*feature["criteria"], *feature["increments"])
    }
    for raw in criteria:
        if type(raw) is not dict or set(raw) - {"id", "description", "check_kind", "check_target"}:
            raise ContinuityError("INVALID_REQUEST", "criterion amendment fields are invalid")
        criterion_id = raw.get("id")
        if criterion_id is None:
            amended = _new_criterion(raw, state, allocated)
            if len(result["criteria"]) >= _MAX_CRITERIA:
                raise ContinuityError("LIMIT_EXCEEDED", "Feature criterion limit is exceeded")
            result["criteria"].append(amended)
        else:
            existing = current.get(criterion_id)
            if existing is None:
                raise ContinuityError("INVALID_REQUEST", "criterion id is not in the selected Feature")
            amended = _new_criterion({**raw, "id": None}, state, allocated)
            amended["id"] = criterion_id
            result["criteria"] = [
                amended if item["id"] == criterion_id else item for item in result["criteria"]
            ]
    return result


def _selected_feature(
    state: ContinuityStateData, session_id: str, supplied: object,
) -> str:
    feature_id = supplied or state["session_features"].get(session_id) or state["default_feature_id"]
    if not isinstance(feature_id, str) or feature_id not in state["features"]:
        raise ContinuityError("FEATURE_REQUIRED", "select an existing Feature before this operation")
    return feature_id


def _id_list(value: object, name: str, prefix: str, allowed: set[str]) -> list[str]:
    if type(value) is not list or any(
        not isinstance(item, str) or item not in allowed or not item.startswith(prefix + "-")
        for item in value
    ):
        raise ContinuityError("INVALID_REQUEST", f"{name} must contain current {prefix} IDs")
    if len(value) != len(set(value)):
        raise ContinuityError("INVALID_REQUEST", f"{name} contains duplicate IDs")
    return value


def _criterion_ids(feature: ContinuityFeatureDocument) -> set[str]:
    return {item["id"] for item in feature["criteria"]}


_Record = TypeVar("_Record", ContinuityTaskDocument, ContinuityCriterionDocument)


def _by_id(
    values: list[_Record], value: object, prefix: str, name: str,
) -> _Record:
    if not isinstance(value, str) or _ID.fullmatch(value) is None or value[0] != prefix:
        raise ContinuityError("INVALID_REQUEST", f"{name} is invalid")
    for item in values:
        if item["id"] == value:
            return item
    raise ContinuityError("INVALID_REQUEST", f"{name} does not belong to the selected Feature")


def _new_id(
    prefix: str, state: ContinuityStateData, allocated: set[str] | None = None,
) -> str:
    existing = set(state["ids"])
    existing.update(state["features"])
    existing.update(state["task_states"])
    for _ in range(100):
        candidate = f"{prefix}-{uuid4().hex}"
        if candidate not in existing and (allocated is None or candidate not in allocated):
            if allocated is not None:
                allocated.add(candidate)
            state["ids"].append(candidate)
            return candidate
    raise ContinuityError("ID_ALLOCATION_FAILED", "could not allocate a collision-free identity")


def _text(
    value: object, name: str, *, maximum: int, multiline: bool = False,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ContinuityError("INVALID_REQUEST", f"{name} must be text")
    try:
        value_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ContinuityError("INVALID_REQUEST", f"{name} contains invalid Unicode") from error
    if value_length > maximum or (not allow_empty and not value.strip()):
        raise ContinuityError("INVALID_REQUEST", f"{name} is empty or exceeds its limit")
    if "\x00" in value or any(ord(char) < 32 and char not in ("\n", "\r", "\t") for char in value):
        raise ContinuityError("INVALID_REQUEST", f"{name} contains unsupported control characters")
    if not multiline and ("\n" in value or "\r" in value):
        raise ContinuityError("INVALID_REQUEST", f"{name} must be one line")
    return value


def _keys(data: dict[str, object], allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ContinuityError("INVALID_REQUEST", f"unknown continuity field {sorted(unknown)[0]}")


def _expect_revision(actual: int | None, expected: int) -> None:
    if type(actual) is not int or actual < 0:
        raise ContinuityError("INVALID_REQUEST", "expected_revision must be a nonnegative integer")
    if actual != expected:
        raise ContinuityError(
            "STALE_REVISION",
            f"expected revision {actual}, current revision is {expected}; reread and reconcile",
        )


def _copy_state(state: ContinuityStateData) -> ContinuityStateData:
    return deepcopy(state)


def _digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


__all__ = ("ContinuityError", "execute")
