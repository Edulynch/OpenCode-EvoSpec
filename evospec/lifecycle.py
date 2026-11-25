"""Caller-controlled lifecycle validation for Core Bootstrap."""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
import json
from typing import Final, Protocol


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
    state: dict[str, object], feature: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Project only receipts whose criterion digest still matches canonical text."""
    current = {
        item["id"]: _continuity_digest(item)
        for item in feature["criteria"]
    }
    latest: dict[str, dict[str, object]] = {}
    for receipt in state["checks"]:
        criterion_id = receipt.get("criterion_id")
        if criterion_id in current and receipt.get("criterion_digest") == current[criterion_id]:
            latest[criterion_id] = {
                "passed": receipt.get("exit_code") == 0,
                "exit_code": receipt.get("exit_code"),
                "check_kind": receipt.get("check_kind"),
                "check_target": receipt.get("check_target"),
                "output_sha256": receipt.get("output_sha256"),
                "task_id": receipt.get("task_id"),
                "reported_by_session_id": receipt.get("reported_by_session_id"),
                "recorded_revision": receipt.get("recorded_revision"),
            }
    return latest


def continuity_task_status(
    state: dict[str, object], feature: dict[str, object], task: dict[str, object],
) -> str:
    """Derive operational status; a report alone never completes a Task."""
    execution = state["execution"]
    if execution is not None and execution["task_id"] == task["id"]:
        return "OUTCOME_UNKNOWN"
    attempts = [item for item in state["attempts"] if item["task_id"] == task["id"]]
    if not attempts:
        return "READY"
    last = attempts[-1]
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
    checks = continuity_current_checks(state, feature)
    if all(checks.get(identity, {}).get("passed") for identity in task["criteria_ids"]):
        return "COMPLETED"
    return "REPORTED"


def continuity_task_eligible(
    state: dict[str, object], feature: dict[str, object],
    tasks: list[dict[str, object]], task: dict[str, object],
) -> bool:
    """Require every dependency to be completed and the Task itself ready."""
    by_id = {item["id"]: item for item in tasks}
    checks = continuity_current_checks(state, feature)
    if any(
        dependency not in by_id
        or continuity_task_status(state, feature, by_id[dependency]) != "COMPLETED"
        for dependency in task["depends_on"]
    ):
        return False
    return continuity_task_status(state, feature, task) == "READY"


def continuity_close_blockers(
    state: dict[str, object], feature_id: str,
    feature_state: dict[str, object], feature: dict[str, object],
    tasks: list[dict[str, object]],
) -> tuple[str, ...]:
    """Return objective closure blockers without treating reports as evidence."""
    blockers: list[str] = []
    if state["execution"] is not None:
        blockers.append("a checkpointed Task has an unknown outcome")
    if feature_id in state["paused_features"]:
        blockers.append("paused work must be explicitly resumed before closure")
    checks = continuity_current_checks(state, feature)
    missing = [
        item["id"] for item in feature["criteria"]
        if not checks.get(item["id"], {}).get("passed")
    ]
    incomplete = [
        task["id"] for task in tasks
        if continuity_task_status(state, feature, task) != "COMPLETED"
    ]
    if missing:
        blockers.append("criteria lack current passing checks: " + ", ".join(missing))
    if incomplete:
        blockers.append("Tasks are not complete: " + ", ".join(incomplete))
    if feature_state["status"] != "OPEN":
        blockers.append("only an open Feature can be closed")
    return tuple(blockers)


def _continuity_digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
