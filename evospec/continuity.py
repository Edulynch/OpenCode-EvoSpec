"""Composed, document-backed work continuity for fresh projects."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
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
    CONTINUITY_INPUT_POLICY_DIGEST,
    CONTINUITY_INPUT_POLICY_ID,
    ContinuityAttempt,
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
    ContinuityReopen,
    ContinuityStateData,
    ContinuityTaskDocument,
)
from .input_fingerprint import (
    InputFingerprintError,
    repository_input_snapshot,
)
from .lifecycle import (
    ContinuityFeatureView,
    ContinuityStateView,
    ContinuityTaskView,
    continuity_close_blockers,
    continuity_current_checks,
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
    "status", "input_snapshot", "verify", "pause", "resume", "close",
    "reconcile", "upgrade_markdown",
))

_DocumentRecord = TypeVar("_DocumentRecord", bound=Mapping[str, object])


class _ContinuityCheckView(TypedDict):
    criterion_id: str
    criterion_digest: str
    exit_code: int
    check_kind: str
    check_target: str
    output_sha256: str
    task_id: str | None
    reported_by_session_id: str
    recorded_revision: int
    observed_input_scope: NotRequired[dict[str, str]]
    task_digest: NotRequired[str]


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
    return {
        "checks": tuple(_continuity_check_view(check) for check in state["checks"]),
        "attempts": tuple(state["attempts"]),
        "execution": None if execution is None else {"task_id": execution["task_id"]},
        "paused_features": tuple(state["paused_features"]),
        "input_snapshot": None if input_snapshot is None else {
            "policy_id": input_snapshot["policy_id"],
            "policy_digest": input_snapshot["policy_digest"],
            "fingerprint": input_snapshot["fingerprint"],
        },
    }


def _continuity_feature_view(
    feature: ContinuityFeatureDocument,
) -> ContinuityFeatureView:
    return {"criteria": tuple(feature["criteria"])}


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
    view: _ContinuityCheckView = {
        "criterion_id": check["criterion_id"],
        "criterion_digest": check["criterion_digest"],
        "exit_code": check["exit_code"],
        "check_kind": check["check_kind"],
        "check_target": check["check_target"],
        "output_sha256": check["output_sha256"],
        "task_id": check["task_id"],
        "reported_by_session_id": check["reported_by_session_id"],
        "recorded_revision": check["recorded_revision"],
    }
    observed = check.get("observed_input_scope")
    if observed is not None:
        view["observed_input_scope"] = {
            "policy_id": observed["policy_id"],
            "policy_digest": observed["policy_digest"],
            "fingerprint": observed["fingerprint"],
        }
    task_digest = check.get("task_digest")
    if task_digest is not None:
        view["task_digest"] = task_digest
    return view


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
        and operation not in ("change", "close")
        and not (
            operation == "implement"
            and data.get("phase", "queue") == "queue"
            and data.get("task_id") is None
        )
    ):
        raise ContinuityError(
            "REOPEN_INTENT_REQUIRED",
            "mutations of a closed Feature require the explicit change operation "
            "or reconcile with reopen_feature_id equal to this Feature ID; closure "
            "history is retained",
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
        _keys(data, {"feature_id", "task_title", "task_description", "increment_id", "criteria_ids", "depends_on"})
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
        tasks.append(task)
        candidate["task_states"][task["id"]] = {"attempts": 0}
        writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))

    elif operation == "implement":
        _keys(data, {"feature_id", "task_id", "phase", "report", "result"})
        task_id = data.get("task_id")
        phase = data.get("phase", "queue")
        if phase == "queue" and task_id is None:
            return _project(store, state, session_id)
        task = _by_id(tasks, task_id, "T", "task_id")
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
                _continuity_feature_view(feature),
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
            }
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
            writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))
        else:
            raise ContinuityError("INVALID_REQUEST", "implement phase must be queue, begin, or report")

    elif operation == "verify":
        _keys(data, {
            "feature_id", "criterion_id", "exit_code", "output_sha256",
            "check_kind", "check_target", "task_id",
            "input_snapshot_before", "input_snapshot_after",
        })
        # The OpenCode tool supplies result fields only after it runs an allow-listed test.
        criterion = _by_id(feature["criteria"], data.get("criterion_id"), "C", "criterion_id")
        exit_code = data.get("exit_code")
        if type(exit_code) is not int or not 0 <= exit_code <= 255:
            raise ContinuityError("INVALID_REQUEST", "check exit code must be between 0 and 255")
        output_digest = data.get("output_sha256")
        if not isinstance(output_digest, str) or re.fullmatch(r"[0-9a-f]{64}", output_digest) is None:
            raise ContinuityError("INVALID_REQUEST", "check output digest must be a SHA-256 value")
        if (
            data.get("check_kind") != criterion["check_kind"]
            or data.get("check_target") != criterion["check_target"]
        ):
            raise ContinuityError("STALE_CRITERION", "check target differs from the current criterion")
        if candidate["execution"] is not None:
            raise ContinuityError("EXECUTION_BUSY", "reconcile the in-flight Task before verifying")
        task_id = _text(data.get("task_id"), "task_id", maximum=256)
        task = _by_id(tasks, task_id, "T", "task_id")
        if criterion["id"] not in task["criteria_ids"]:
            raise ContinuityError(
                "INVALID_REQUEST", "verification Task must be linked to the current criterion",
            )
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
        check: ContinuityCheck = {
            "criterion_id": criterion["id"], "criterion_digest": _digest(criterion),
            "check_kind": criterion["check_kind"], "check_target": criterion["check_target"],
            "exit_code": exit_code, "output_sha256": output_digest,
            "reported_by_session_id": session_id, "task_id": task_id,
            "recorded_revision": state["revision"] + 1,
            "observed_input_scope": current_input,
            "task_digest": _digest(task),
        }
        candidate["checks"].append(check)
        writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))

    elif operation == "pause":
        _keys(data, {"feature_id", "reason", "detour_kind", "detour_title", "return_task_id"})
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
        })
        execution = candidate["execution"]
        reconcile = data.get("reconcile")
        pending_detour = _pending_detour(candidate, feature_id)
        detour_result: str | None = None
        if pending_detour is not None:
            _by_id(tasks, pending_detour["return_task_id"], "T", "return_task_id")
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
            evidence_ref = _text(data.get("evidence_ref"), "evidence_ref", maximum=2048)
            entry = _active_attempt(candidate)
            entry["status"] = "RECONCILED_COMPLETE" if reconcile == "COMPLETE" else "RECONCILED_INCOMPLETE"
            entry["result_status"] = reconcile
            entry["evidence_ref"] = evidence_ref
            entry["result"] = entry.get("result") or "Outcome reconciled explicitly after interruption"
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
        _keys(data, {"feature_id"})
        try:
            current_input_snapshot = _continuity_input_scope(root, state)
        except ContinuityError as error:
            current_input_snapshot = None
            input_snapshot_error = str(error)
        else:
            input_snapshot_error = None
        blockers = continuity_close_blockers(
            _continuity_state_view(state, current_input_snapshot),
            feature_id,
            {"status": candidate["features"][feature_id]["status"]},
            _continuity_feature_view(feature),
            tuple(_continuity_task_view(item) for item in tasks),
        )
        if input_snapshot_error is not None:
            blockers = (*blockers, f"current input snapshot is unavailable: {input_snapshot_error}")
        if blockers:
            raise ContinuityError("CLOSE_BLOCKED", "; ".join(blockers))
        feature_state = candidate["features"][feature_id]
        if feature_state["status"] == "CLOSED":
            raise ContinuityError("ALREADY_CLOSED", "Feature is already closed in this cycle")
        receipt: ContinuityClosure = {
                "id": _new_id("D", candidate, {item.get("id") for item in candidate["closures"]}), "feature_id": feature_id,
            "cycle": feature_state["cycle"], "revision": state["revision"] + 1,
            "criteria": [
                {"id": item["id"], "digest": _digest(item)}
                for item in feature["criteria"]
            ],
            "session_id": session_id,
        }
        candidate["closures"].append(receipt)
        feature_state["status"] = "CLOSED"
        writes.update(_render_spec_write(root, candidate, feature_id, feature))
        writes.update(_render_task_write(root, candidate, feature_id, tasks, feature))
        writes.update(_index_write(root, candidate))

    elif operation == "change":
        _keys(data, {
            "feature_id", "outcome", "criteria", "task_title", "task_description",
            "increment_id", "criteria_ids",
        })
        changed = _amend_feature(feature, data, candidate)
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
        if len(tasks) >= _MAX_TASKS:
            raise ContinuityError("LIMIT_EXCEEDED", "Feature Task limit is exceeded")
        tasks.append(task)
        candidate["task_states"][task["id"]] = {"attempts": 0}
        feature_state = candidate["features"][feature_id]
        if feature_state["status"] == "CLOSED":
            previous = next(
                (
                    item for item in reversed(candidate["closures"])
                    if item["feature_id"] == feature_id and item["cycle"] == feature_state["cycle"]
                ),
                None,
            )
            assert previous is not None
            reopen: ContinuityReopen = {
                "feature_id": feature_id, "cycle": feature_state["cycle"] + 1,
                "previous_closure_id": previous["id"],
                "session_id": session_id,
            }
            candidate["reopens"].append(reopen)
            feature_state["cycle"] += 1
            feature_state["status"] = "OPEN"
            writes.update(_index_write(root, candidate))
        writes.update(_render_spec_write(root, candidate, feature_id, changed))
        writes.update(_render_task_write(root, candidate, feature_id, tasks, changed))
        feature = changed

    else:
        raise ContinuityError("INVALID_OPERATION", "unsupported continuity operation")

    if operation == "verify":
        observed_input_scope = candidate["checks"][-1].get("observed_input_scope")
        if (
            observed_input_scope is None
            or _continuity_input_scope(root, state) != observed_input_scope
        ):
            raise ContinuityError(
                "STALE_INPUT", "repository inputs changed before the check receipt was committed",
            )
    return _publish_mutation(store, state, candidate, writes, session_id)


def _publish_mutation(
    store: ContinuityPersistence, state: ContinuityStateData,
    candidate: ContinuityStateData, writes: dict[str, tuple[bytes, str | None]],
    session_id: str,
) -> dict[str, object]:
    candidate["revision"] = state["revision"] + 1
    read_set: dict[str, str | None] = {
        path: digest for path, digest in state["documents"].items()
    }
    _refresh_document_metadata(candidate, writes)
    store.commit(state, candidate, writes, read_set=read_set)
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
        "active_attempt": None if state["execution"] is None else _active_attempt(state),
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
    _keys(data, {"feature_id", "task_id", "phase", "report", "result"})
    execution = state["execution"]
    if execution is None or execution["task_id"] != data.get("task_id"):
        raise ContinuityError("NO_ACTIVE_ATTEMPT", "no matching checkpointed Task attempt exists")
    if execution["session_id"] != session_id:
        raise ContinuityError(
            "OUTCOME_UNKNOWN",
            "only the originating host session may report this checkpointed attempt",
        )
    owner = _task_owner(state, execution["task_id"])
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
    return _publish_state_only(store, state, candidate, session_id)


def _reconcile_attempt_state_only(
    store: ContinuityPersistence, state: ContinuityStateData,
    expected_revision: int | None, session_id: str, data: dict[str, object],
) -> dict[str, object]:
    _expect_revision(expected_revision, state["revision"])
    _keys(data, {
        "feature_id", "reconcile", "task_id", "evidence_ref", "detour_summary",
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
    if data.get("feature_id") is not None and data.get("feature_id") != owner:
        raise ContinuityError("TASK_MISMATCH", "reconciliation must target the Feature owning the checkpointed Task")
    if any(item["result"] is None for item in state["detours"]):
        raise ContinuityError(
            "DETOUR_OUTCOME_REQUIRED",
            "reconcile the checkpointed Task first; after document reconciliation, resume the pending detour with its result",
        )
    if data.get("detour_summary") is not None:
        raise ContinuityError(
            "DOCUMENT_DIVERGED",
            "state-only Task reconciliation cannot publish a detour decision; reconcile documents, then resume the detour",
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
    _keys(data, {"feature_id", "retire_ids", "reopen_feature_id"})
    feature_id = data.get("feature_id")
    if not isinstance(feature_id, str) or feature_id not in state["features"]:
        raise ContinuityError(
            "FEATURE_REQUIRED", "reconcile requires the exact current Feature ID",
        )
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
        candidate["reopens"].append({
            "feature_id": feature_id, "cycle": feature_state["cycle"] + 1,
            "previous_closure_id": previous["id"], "session_id": session_id,
        })
        feature_state["cycle"] += 1
        feature_state["status"] = "OPEN"
    elif reopen_feature is not None:
        raise ContinuityError(
            "INVALID_REQUEST", "reopen_feature_id is accepted only for a closed Feature with semantic edits",
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
    if document_changes["identity_ref_conflicts"] or document_changes["semantic"]:
        return {
            "mode": "COMPOSED", "revision": state["revision"],
            "status": (
                "DOCUMENT_CONFLICT"
                if document_changes["identity_ref_conflicts"]
                else "DOCUMENT_RECONCILIATION_REQUIRED"
            ),
            "document_changes": document_changes,
            "execution": state["execution"],
            "active_attempt": None if state["execution"] is None else _active_attempt(state),
            "features": [],
            "selected_feature": None,
            "next_task": None,
        }
    selected = state["session_features"].get(session_id) or state["default_feature_id"]
    feature_views: list[dict[str, object]] = []
    result: dict[str, object] = {
        "mode": "COMPOSED", "revision": state["revision"],
        "default_feature_id": state["default_feature_id"],
        "selected_feature_id": selected,
        "selection_scope": "session" if session_id in state["session_features"] else "worktree",
        "features": feature_views,
        "execution": state["execution"],
        "document_changes": document_changes,
    }
    if input_snapshot_error is not None:
        result["input_snapshot_error"] = input_snapshot_error
    for feature_id in sorted(state["features"]):
        parsed = parsed_documents[feature_id]
        feature = cast(ContinuityFeatureDocument, parsed["spec"])
        tasks = cast(list[ContinuityTaskDocument], parsed["tasks"])
        feature_state = state["features"][feature_id]
        continuity_state = _continuity_state_view(state, input_snapshot)
        continuity_feature = _continuity_feature_view(feature)
        task_views = tuple(_continuity_task_view(item) for item in tasks)
        current_checks = continuity_current_checks(
            continuity_state, continuity_feature, tasks=task_views,
        )
        task_projection: list[dict[str, object]] = []
        for task in tasks:
            continuity_task = _continuity_task_view(task)
            status = continuity_task_status(
                continuity_state, continuity_feature, continuity_task, task_views,
            )
            task_projection.append({
                **task, "status": status,
                "attempts": state["task_states"][task["id"]]["attempts"],
                "eligible": continuity_task_eligible(
                    continuity_state, continuity_feature, task_views, continuity_task,
                ),
            })
        feature_view: dict[str, object] = {
            "id": feature_id, "directory": feature_state["directory"],
            "kind": feature["kind"], "title": feature["title"],
            "outcome": feature["outcome"], "status": feature_state["status"],
            "cycle": feature_state["cycle"], "criteria": [
                {
                    **criterion, "digest": _digest(criterion),
                    "check": current_checks.get(criterion["id"]) or
                    _continuity_historical_check(
                        state, criterion, input_snapshot, task_views,
                    ),
                }
                for criterion in feature["criteria"]
            ],
            "increments": feature["increments"], "tasks": task_projection,
            "plan": cast(dict[str, object], parsed["plan"])["plan"],
            "decisions": parsed["decisions"],
            "paused": feature_id in state["paused_features"],
            "pause_reason": state["pause_reasons"].get(feature_id),
            "detours": [
                item for item in state["detours"] if item["feature_id"] == feature_id
            ],
            "closure_history": [
                item for item in state["closures"] if item["feature_id"] == feature_id
            ],
            "reopen_history": [
                item for item in state["reopens"] if item["feature_id"] == feature_id
            ],
        }
        feature_views.append(feature_view)
    selected_feature = next(
        (feature for feature in feature_views if feature["id"] == selected), None
    )
    result["selected_feature"] = selected_feature
    result["next_task"] = _next_task(selected_feature)
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
    state: ContinuityStateData, criterion: ContinuityCriterionDocument,
    input_snapshot: ContinuityInputScope | None,
    tasks: tuple[ContinuityTaskView, ...],
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
    current_task = next(
        (item for item in tasks if item["id"] == receipt["task_id"]),
        None,
    )
    if receipt["criterion_digest"] != _digest(criterion):
        stale_reason = "criterion_changed"
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
    return {
        "passed": False, "fresh": False,
        "historical_passed": receipt["exit_code"] == 0,
        "exit_code": receipt["exit_code"],
        "check_kind": receipt["check_kind"],
        "check_target": receipt["check_target"],
        "output_sha256": receipt["output_sha256"],
        "task_id": receipt["task_id"],
        "reported_by_session_id": receipt["reported_by_session_id"],
        "recorded_revision": receipt["recorded_revision"],
        "stale_reason": stale_reason,
    }


def _active_attempt(state: ContinuityStateData) -> ContinuityAttempt:
    execution = state["execution"]
    if execution is None:
        raise ContinuityError("NO_ACTIVE_ATTEMPT", "no Task attempt is in progress")
    for attempt in reversed(state["attempts"]):
        if attempt["task_id"] == execution["task_id"] and attempt["attempt"] == execution["attempt"]:
            return attempt
    raise ContinuityError("CORRUPT_CONTINUITY", "active Task attempt is missing from history")


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
    if kind not in ("python-unittest", "node-test"):
        raise ContinuityError("INVALID_REQUEST", "criterion check_kind must be python-unittest or node-test")
    if kind == "python-unittest" and re.fullmatch(r"tests(?:\.[A-Za-z_][A-Za-z0-9_]*)+", target) is None:
        raise ContinuityError("INVALID_REQUEST", "python check_target must name a tests.* unittest module")
    if kind == "node-test" and re.fullmatch(r"tests/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.test\.mjs", target) is None:
        raise ContinuityError("INVALID_REQUEST", "node check_target must name a tests/*.test.mjs file")
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
