"""Caller-controlled lifecycle validation for Core Bootstrap."""

from __future__ import annotations

import json
from collections.abc import Sequence
from enum import StrEnum
from hashlib import sha256
from typing import Final, NotRequired, Protocol, TypedDict, cast


class _ContinuityCriterion(TypedDict):
    id: str
    description: NotRequired[str]
    check_kind: NotRequired[str]
    check_target: NotRequired[str]


class ContinuityFeatureView(TypedDict):
    criteria: Sequence[_ContinuityCriterion]
    id: NotRequired[str]
    cycle: NotRequired[int]
    mandate_id: NotRequired[str | None]
    mandate_scope_current: NotRequired[bool]


class ContinuityTaskView(TypedDict):
    id: str
    title: str
    description: str
    increment_id: str | None
    criteria_ids: Sequence[str]
    depends_on: Sequence[str]


class _ContinuityFeatureState(TypedDict):
    status: str


class _ContinuityInputScope(TypedDict):
    policy_id: str
    policy_digest: str
    fingerprint: str


class _ContinuityCheck(TypedDict):
    criterion_id: str
    criterion_digest: str
    check_kind: str
    check_target: str
    task_id: str | None
    reported_by_session_id: str
    recorded_revision: int
    exit_code: NotRequired[int]
    output_sha256: NotRequired[str]
    observed_input_scope: NotRequired[_ContinuityInputScope]
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


class _ContinuityReviewDecision(TypedDict):
    feature_id: str
    feature_cycle: int
    criterion_id: str
    criterion_digest: str
    evidence_ref: str
    task_id: str
    task_digest: str
    mandate_id: str | None
    decision: str


class _ContinuityAttempt(TypedDict):
    task_id: str
    attempt: int
    status: str
    session_id: str
    checkpoint: str
    result: str | None
    result_status: str | None
    evidence_ref: str | None
    mandate_id: NotRequired[str]
    feature_id: NotRequired[str]
    feature_cycle: NotRequired[int]
    task_digest: NotRequired[str]
    reported_revision: NotRequired[int]
    reported_by_session_id: NotRequired[str]
    reported_input_scope: NotRequired[_ContinuityInputScope]


class _ContinuityTaskEvent(TypedDict):
    task_id: str
    event: str
    recorded_revision: int
    attempt_count: int


class _ContinuityExecution(TypedDict):
    task_id: str


class ContinuityStateView(TypedDict):
    checks: Sequence[_ContinuityCheck]
    attempts: Sequence[_ContinuityAttempt]
    execution: _ContinuityExecution | None
    paused_features: Sequence[str]
    input_snapshot: NotRequired[_ContinuityInputScope | None]
    task_events: NotRequired[Sequence[_ContinuityTaskEvent]]
    review_decisions: NotRequired[Sequence[_ContinuityReviewDecision]]

class LifecycleState(StrEnum):
    PROPOSED = "PROPOSED"
    CLARIFYING = "CLARIFYING"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    VERIFYING = "VERIFYING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ABANDONED = "ABANDONED"


class Phase(StrEnum):
    RESEARCH = "RESEARCH"
    CLARIFY = "CLARIFY"
    READY = "READY"
    IMPLEMENT = "IMPLEMENT"
    REVIEW = "REVIEW"
    VERIFY = "VERIFY"
    CONVERGE = "CONVERGE"
    DONE = "DONE"


class ReviewVerdict(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCKED = "BLOCKED"


class Classification(StrEnum):
    TUNE = "TUNE"
    PATCH = "PATCH"
    FEATURE = "FEATURE"
    FOUNDATION = "FOUNDATION"
    EXPERIMENT = "EXPERIMENT"


class _ReviewResult(Protocol):
    @property
    def verdict(self) -> ReviewVerdict: ...

    @property
    def safe_to_proceed(self) -> bool: ...


class _VerificationEvidence(Protocol):
    @property
    def sufficient(self) -> bool: ...


class LifecycleError(Exception):
    """Raised when caller-supplied lifecycle facts are inconsistent."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail: str = detail


PHASE_STATES: Final[dict[Phase, LifecycleState]] = {
    Phase.RESEARCH: LifecycleState.PROPOSED,
    Phase.CLARIFY: LifecycleState.CLARIFYING,
    Phase.READY: LifecycleState.READY,
    Phase.IMPLEMENT: LifecycleState.IN_PROGRESS,
    Phase.REVIEW: LifecycleState.IN_PROGRESS,
    Phase.VERIFY: LifecycleState.VERIFYING,
    Phase.CONVERGE: LifecycleState.VERIFYING,
    Phase.DONE: LifecycleState.ACCEPTED,
}
TERMINAL_STATES: Final[frozenset[LifecycleState]] = frozenset(
    (LifecycleState.ACCEPTED, LifecycleState.REJECTED, LifecycleState.ABANDONED)
)
LifecyclePair = tuple[LifecycleState, Phase]
TERMINAL_PAIRS: Final[frozenset[LifecyclePair]] = frozenset(
    (
        (LifecycleState.ACCEPTED, Phase.DONE),
        (LifecycleState.REJECTED, Phase.CLARIFY),
        (LifecycleState.REJECTED, Phase.VERIFY),
        (LifecycleState.REJECTED, Phase.CONVERGE),
        (LifecycleState.ABANDONED, Phase.READY),
        (LifecycleState.ABANDONED, Phase.IMPLEMENT),
        (LifecycleState.ABANDONED, Phase.REVIEW),
    )
)
TRANSITIONS: Final[dict[LifecyclePair, frozenset[LifecyclePair]]] = {
    (LifecycleState.PROPOSED, Phase.RESEARCH): frozenset(
        (
            (LifecycleState.CLARIFYING, Phase.CLARIFY),
            (LifecycleState.READY, Phase.READY),
        )
    ),
    (LifecycleState.CLARIFYING, Phase.CLARIFY): frozenset(
        (
            (LifecycleState.READY, Phase.READY),
            (LifecycleState.REJECTED, Phase.CLARIFY),
        )
    ),
    (LifecycleState.READY, Phase.READY): frozenset(
        (
            (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT),
            (LifecycleState.ABANDONED, Phase.READY),
        )
    ),
    (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT): frozenset(
        (
            (LifecycleState.IN_PROGRESS, Phase.REVIEW),
            (LifecycleState.PAUSED, Phase.IMPLEMENT),
            (LifecycleState.ABANDONED, Phase.IMPLEMENT),
        )
    ),
    (LifecycleState.IN_PROGRESS, Phase.REVIEW): frozenset(
        (
            (LifecycleState.VERIFYING, Phase.VERIFY),
            (LifecycleState.PAUSED, Phase.REVIEW),
            (LifecycleState.ABANDONED, Phase.REVIEW),
        )
    ),
    (LifecycleState.VERIFYING, Phase.VERIFY): frozenset(
        (
            (LifecycleState.VERIFYING, Phase.CONVERGE),
            (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT),
            (LifecycleState.PAUSED, Phase.VERIFY),
            (LifecycleState.REJECTED, Phase.VERIFY),
        )
    ),
    (LifecycleState.VERIFYING, Phase.CONVERGE): frozenset(
        (
            (LifecycleState.ACCEPTED, Phase.DONE),
            (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT),
            (LifecycleState.PAUSED, Phase.CONVERGE),
            (LifecycleState.REJECTED, Phase.CONVERGE),
        )
    ),
    (LifecycleState.ACCEPTED, Phase.DONE): frozenset(),
    (LifecycleState.REJECTED, Phase.CLARIFY): frozenset(),
    (LifecycleState.REJECTED, Phase.VERIFY): frozenset(),
    (LifecycleState.REJECTED, Phase.CONVERGE): frozenset(),
    (LifecycleState.ABANDONED, Phase.READY): frozenset(),
    (LifecycleState.ABANDONED, Phase.IMPLEMENT): frozenset(),
    (LifecycleState.ABANDONED, Phase.REVIEW): frozenset(),
}


def is_valid_pair(status: LifecycleState, phase: Phase, paused_pair: tuple[LifecycleState, Phase] | None = None) -> bool:
    """Return whether a persisted status and phase are an allowed pair."""
    match status:
        case LifecycleState.PAUSED:
            return paused_pair is not None and paused_pair[0] not in TERMINAL_STATES and PHASE_STATES[paused_pair[1]] == paused_pair[0] and phase == paused_pair[1]
        case LifecycleState.ACCEPTED | LifecycleState.REJECTED | LifecycleState.ABANDONED:
            return (status, phase) in TERMINAL_PAIRS
        case _:
            return PHASE_STATES[phase] == status


def validate_context_state(
    status: LifecycleState,
    phase: Phase,
    unresolved_material_decision: bool,
    review: _ReviewResult | None,
    verification: _VerificationEvidence,
    repair_count: int,
    paused_pair: tuple[LifecycleState, Phase] | None,
    blocker: str | None,
    safe_next_step: str | None = None,
) -> None:
    """Validate only facts supplied by a caller; this function never advances work."""
    if not is_valid_pair(status, phase, paused_pair):
        raise LifecycleError("invalid lifecycle state and phase pair")
    if status in (LifecycleState.READY, LifecycleState.IN_PROGRESS) and unresolved_material_decision:
        raise LifecycleError("unresolved material decision blocks ready and in-progress work")
    if type(repair_count) is not int or repair_count < 0 or repair_count > 2:
        raise LifecycleError("repair count must be between zero and two")
    if repair_count == 2 and status == LifecycleState.PAUSED and (not blocker or not safe_next_step):
        raise LifecycleError("repair exhaustion requires a persisted blocker and safe next step")
    if phase == Phase.CONVERGE and not can_converge(review, verification):
        raise LifecycleError("convergence requires an acceptable review and sufficient verification")


def can_converge(review: _ReviewResult | None, verification: _VerificationEvidence) -> bool:
    """Return whether caller-supplied guards permit convergence to be recorded."""
    if review is None or not verification.sufficient:
        return False
    return review.verdict == ReviewVerdict.PASS or (review.verdict == ReviewVerdict.WARN and review.safe_to_proceed)


def has_acceptable_review(review: _ReviewResult | None) -> bool:
    """Return whether a caller-supplied review permits verification to begin."""
    if review is None:
        return False
    return review.verdict == ReviewVerdict.PASS or (review.verdict == ReviewVerdict.WARN and review.safe_to_proceed)


def advance(
    current_status: LifecycleState,
    current_phase: Phase,
    requested_status: LifecycleState,
    requested_phase: Phase,
    unresolved_material_decision: bool,
    review: _ReviewResult | None,
    verification: _VerificationEvidence,
    convergence_complete: bool = False,
) -> tuple[LifecycleState, Phase]:
    """Validate an explicit caller-requested transition and return its pair."""
    current = (current_status, current_phase)
    requested = (requested_status, requested_phase)
    if current_status == LifecycleState.PAUSED:
        resumed = (PHASE_STATES[current_phase], current_phase)
        if requested not in (resumed, (LifecycleState.ABANDONED, current_phase)):
            raise LifecycleError("paused work resumes only to its preserved pair")
    elif requested != current and requested not in TRANSITIONS.get(current, frozenset()):
        raise LifecycleError("transition is not allowed")
    if current_status == LifecycleState.IN_PROGRESS and requested_status == LifecycleState.VERIFYING:
        if current_phase != Phase.REVIEW or not has_acceptable_review(review):
            raise LifecycleError("verification requires a recorded acceptable review")
    if current_status == LifecycleState.VERIFYING and requested_status == LifecycleState.ACCEPTED:
        if current_phase != Phase.CONVERGE or not convergence_complete or not can_converge(review, verification):
            raise LifecycleError("accepted work requires completed guarded convergence")
    if requested_status in (LifecycleState.REJECTED, LifecycleState.ABANDONED) and requested_phase != current_phase:
        raise LifecycleError("terminal work retains its prior valid phase")
    validate_context_state(requested_status, requested_phase, unresolved_material_decision, review, verification, 0, (PHASE_STATES[current_phase], current_phase) if requested_status == LifecycleState.PAUSED else None, None)
    return requested_status, requested_phase


def continuity_current_checks(
    state: ContinuityStateView, feature: ContinuityFeatureView,
    *,
    tasks: Sequence[ContinuityTaskView] | None = None,
    task_id: str | None = None,
) -> dict[str, dict[str, object]]:
    """Project objective receipts and reports without upgrading reports to checks."""
    input_scope = state.get("input_snapshot")
    if not isinstance(input_scope, dict):
        return {}
    criteria = {item["id"]: item for item in feature["criteria"]}
    current = {identity: _continuity_digest(item) for identity, item in criteria.items()}
    task_criteria = None if tasks is None else {
        item["id"]: set(item["criteria_ids"]) for item in tasks
    }
    task_digests = None if tasks is None else {
        item["id"]: _continuity_digest(item) for item in tasks
    }
    latest: dict[str, dict[str, object]] = {}
    latest_objective: dict[str, dict[str, object]] = {}
    latest_report: dict[str, dict[str, object]] = {}
    feature_id = feature.get("id")
    feature_cycle = feature.get("cycle")
    current_mandate = feature.get("mandate_id")
    mandate_scope_current = feature.get("mandate_scope_current", True)

    def current_receipt(
        receipt: _ContinuityCheck, criterion_id: str, receipt_task_id: object,
    ) -> bool:
        criterion = criteria[criterion_id]
        criterion_check_kind = criterion.get("check_kind")
        criterion_check_target = criterion.get("check_target")
        criterion_description = criterion.get("description")
        if (
            receipt.get("criterion_digest") != current[criterion_id]
            or receipt.get("observed_input_scope") != input_scope
            or not isinstance(receipt_task_id, str)
            or task_id is not None and receipt_task_id != task_id
            or (
                task_digests is not None
                and receipt.get("task_digest") != task_digests.get(receipt_task_id)
            )
            or (
                task_criteria is not None
                and criterion_id not in task_criteria.get(receipt_task_id, set())
            )
            or (
                isinstance(criterion_check_kind, str)
                and receipt.get("check_kind") != criterion_check_kind
            )
            or (
                isinstance(criterion_check_target, str)
                and receipt.get("check_target") != criterion_check_target
            )
            or (
                isinstance(criterion_description, str)
                and receipt.get("purpose", criterion_description) != criterion_description
            )
        ):
            return False
        if isinstance(feature_id, str):
            if receipt.get("feature_id", feature_id) != feature_id:
                return False
        if type(feature_cycle) is int:
            receipt_cycle = receipt.get("feature_cycle")
            if receipt_cycle is None:
                if feature_cycle != 1:
                    return False
            elif receipt_cycle != feature_cycle:
                return False
        if isinstance(current_mandate, str) or current_mandate is None and "mandate_id" in feature:
            if receipt.get("mandate_id") != current_mandate:
                return False
            if not mandate_scope_current:
                return False
        return True

    for receipt in state["checks"]:
        criterion_id = receipt.get("criterion_id")
        receipt_task_id = receipt.get("task_id")
        if (
            criterion_id in current
            and current_receipt(receipt, cast(str, criterion_id), receipt_task_id)
        ):
            assert isinstance(criterion_id, str)
            evidence_status = receipt.get("evidence_status")
            if evidence_status is None:
                evidence_status = (
                    "VERIFIED" if receipt.get("exit_code") == 0 else "FAILED"
                )
            projection: dict[str, object] = {
                **receipt,
                "status": evidence_status,
                "passed": evidence_status == "VERIFIED",
                "fresh": True,
                "purpose": receipt.get("purpose", criteria[criterion_id].get("description")),
                "method": receipt.get("check_kind"),
                "descriptor": receipt.get("check_target"),
            }
            if receipt.get("check_kind") in ("python-unittest", "node-test", "artifact-present"):
                latest_objective[criterion_id] = projection
            else:
                latest_report[criterion_id] = projection

    latest.update(latest_report)
    latest.update(latest_objective)
    return latest


def continuity_task_reports(
    state: ContinuityStateView,
    feature: ContinuityFeatureView,
    tasks: Sequence[ContinuityTaskView],
) -> dict[str, dict[str, object]]:
    """Project Task reports separately; they remain reports, never check receipts."""
    criteria = {
        item["id"]: item for item in feature["criteria"]
    }
    by_task = {item["id"]: item for item in tasks}
    feature_id = feature.get("id")
    feature_cycle = feature.get("cycle")
    mandate_id = feature.get("mandate_id")
    mandate_scope_current = feature.get("mandate_scope_current", True)
    input_scope = state.get("input_snapshot")
    latest: dict[str, dict[str, object]] = {}
    latest_revision: dict[str, int] = {}
    for attempt in state["attempts"]:
        task_id = attempt.get("task_id")
        if (
            not isinstance(task_id, str)
            or task_id not in by_task
            or attempt.get("status") not in (
                "REPORTED", "RECONCILED_COMPLETE", "RECONCILED_INCOMPLETE",
            )
            or not isinstance(attempt.get("result"), str)
        ):
            continue
        if (
            isinstance(feature_id, str)
            and attempt.get("feature_id", feature_id) != feature_id
            or type(feature_cycle) is int
            and attempt.get("feature_cycle", feature_cycle) != feature_cycle
            or "mandate_id" in feature
            and attempt.get("mandate_id") != mandate_id
            or not mandate_scope_current
            or "task_digest" in attempt
            and _continuity_digest(by_task[task_id]) != attempt["task_digest"]
        ):
            continue
        reported_scope = attempt.get("reported_input_scope")
        if reported_scope is not None and reported_scope != input_scope:
            continue
        session = attempt.get("reported_by_session_id", attempt.get("session_id"))
        evidence: dict[str, object] = {
            "status": "REPORTED",
            "passed": False,
            "fresh": (
                type(attempt.get("task_digest")) is str
                and reported_scope is not None
                and reported_scope == input_scope
            ),
            "task_id": task_id,
            "reported_by_session_id": session,
            "result": attempt.get("result"),
            "result_status": attempt.get("result_status"),
            "source": "TASK_REPORT",
            "evidence_ref": attempt.get("evidence_ref"),
        }
        for key in ("feature_id", "feature_cycle", "task_digest", "mandate_id"):
            if key in attempt:
                evidence[key] = attempt.get(key)
        if isinstance(reported_scope, dict):
            evidence["observed_input_scope"] = reported_scope
        revision = attempt.get("reported_revision")
        if type(revision) is int:
            evidence["recorded_revision"] = revision
        for criterion_id in by_task[task_id]["criteria_ids"]:
            if criterion_id not in criteria:
                continue
            criterion = criteria[criterion_id]
            projected = {
                **evidence,
                "purpose": criterion.get("description"),
                "method": criterion.get("check_kind"),
                "descriptor": criterion.get("check_target"),
            }
            if (
                criterion_id not in latest
                or type(revision) is int
                and revision > latest_revision.get(criterion_id, -1)
                or type(revision) is not int
                and criterion_id in latest
                and type(latest.get(criterion_id, {}).get("recorded_revision")) is not int
            ):
                latest[criterion_id] = projected
                if type(revision) is int:
                    latest_revision[criterion_id] = revision
    return latest


def continuity_task_status(
    state: ContinuityStateView,
    feature: ContinuityFeatureView,
    task: ContinuityTaskView,
    tasks: Sequence[ContinuityTaskView] | None = None,
) -> str:
    """Derive operational status; a report alone never completes a Task."""
    execution = state["execution"]
    if execution is not None and execution["task_id"] == task["id"]:
        return "OUTCOME_UNKNOWN"
    attempts = [item for item in state["attempts"] if item["task_id"] == task["id"]]
    events = [
        item for item in state.get("task_events", ())
        if item["task_id"] == task["id"]
    ]
    latest_event = events[-1] if events else None
    if latest_event is not None:
        if latest_event["event"] == "BLOCKED":
            return "BLOCKED"
        if latest_event["event"] == "CANCELED":
            return "CANCELED"
        if latest_event["event"] == "REOPENED" and len(attempts) <= latest_event["attempt_count"]:
            return "READY"
    if not attempts:
        return "READY"
    last = attempts[-1]
    feature_id = feature.get("id")
    feature_cycle = feature.get("cycle")
    if (
        isinstance(feature_id, str)
        and last.get("feature_id", feature_id) != feature_id
        or type(feature_cycle) is int
        and last.get("feature_cycle", feature_cycle) != feature_cycle
        or "mandate_id" in feature
        and last.get("mandate_id") != feature.get("mandate_id")
    ):
        return "READY"
    if last["status"] in ("STARTED", "OUTCOME_UNKNOWN"):
        return "OUTCOME_UNKNOWN"
    if last["status"] == "RECONCILED_INCOMPLETE":
        return "READY"
    if last.get("result_status") == "BLOCKED":
        return "BLOCKED"
    if last["status"] == "REPORTED" and last.get("result_status") != "SUCCEEDED":
        return "REPORTED"
    if last["status"] not in ("REPORTED", "RECONCILED_COMPLETE"):
        return "READY"
    checks = continuity_current_checks(
        state, feature, tasks=tasks, task_id=task["id"],
    )
    if latest_event is not None and latest_event["event"] == "REOPENED" and any(
        type(recorded_revision := check.get("recorded_revision")) is int
        and recorded_revision <= latest_event["recorded_revision"]
        for check in checks.values()
    ):
        return "REPORTED"
    if all(
        (check := checks.get(identity)) is not None
        and (
            check.get("passed") is True
            or (
                (review := _review_decision_current(state, feature, identity, check))
                is not None
                and review["decision"] == "ACCEPTED"
            )
        )
        for identity in task["criteria_ids"]
    ):
        return "COMPLETED"
    return "REPORTED"


def continuity_task_eligible(
    state: ContinuityStateView,
    feature: ContinuityFeatureView,
    tasks: Sequence[ContinuityTaskView],
    task: ContinuityTaskView,
) -> bool:
    """Require every dependency to be completed and the Task itself ready."""
    by_id = {item["id"]: item for item in tasks}
    if any(
        dependency not in by_id
        or continuity_task_status(state, feature, by_id[dependency], tasks) != "COMPLETED"
        for dependency in task["depends_on"]
    ):
        return False
    return continuity_task_status(state, feature, task, tasks) == "READY"


def _review_decision_current(
    state: ContinuityStateView,
    feature: ContinuityFeatureView,
    criterion_id: str,
    check: dict[str, object],
) -> _ContinuityReviewDecision | None:
    if (
        check.get("check_kind") != "review"
        or check.get("status") != "REPORTED"
        or not isinstance(check.get("evidence_ref"), str)
    ):
        return None
    feature_id = feature.get("id")
    feature_cycle = feature.get("cycle")
    for decision in reversed(state.get("review_decisions", ())):
        if (
            decision["criterion_id"] == criterion_id
            and decision["evidence_ref"] == check["evidence_ref"]
            and decision["criterion_digest"] == check.get("criterion_digest")
            and decision["task_id"] == check.get("task_id")
            and decision["task_digest"] == check.get("task_digest")
            and (
                not isinstance(feature_id, str)
                or decision["feature_id"] == feature_id
            )
            and (
                type(feature_cycle) is not int
                or decision["feature_cycle"] == feature_cycle
            )
            and (
                "mandate_id" not in feature
                or decision["mandate_id"] == feature.get("mandate_id")
            )
        ):
            return decision
    return None


def continuity_close_blockers(
    state: ContinuityStateView,
    feature_id: str,
    feature_state: _ContinuityFeatureState,
    feature: ContinuityFeatureView,
    tasks: Sequence[ContinuityTaskView],
) -> tuple[str, ...]:
    """Return objective closure blockers without treating reports as evidence."""
    blockers: list[str] = []
    if state["execution"] is not None:
        blockers.append("a checkpointed Task has an unknown outcome")
    if feature_id in state["paused_features"]:
        blockers.append("paused work must be explicitly resumed before closure")
    if not feature.get("mandate_scope_current", True):
        blockers.append("the current implementation mandate scope is stale")
    checks = continuity_current_checks(state, feature, tasks=tasks)
    missing = [
        item["id"] for item in feature["criteria"]
        if not checks.get(item["id"], {}).get("passed")
        and (
            (review := _review_decision_current(
                state, feature, item["id"], checks.get(item["id"], {}),
            ))
            is None
            or review["decision"] != "ACCEPTED"
        )
    ]
    incomplete = [
        task["id"] for task in tasks
        if continuity_task_status(state, feature, task, tasks) != "COMPLETED"
    ]
    if missing:
        blockers.append(
            "criteria lack current passing checks or explicit review decisions: "
            + ", ".join(missing)
        )
    if incomplete:
        blockers.append("Tasks are not complete: " + ", ".join(incomplete))
    if feature_state["status"] != "OPEN":
        blockers.append("only an open Feature can be closed")
    return tuple(blockers)


def _continuity_digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
