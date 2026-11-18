"""Read-only continuation summary over the canonical agent context projection."""

from __future__ import annotations

from dataclasses import dataclass

from .agent import AcceptedSliceContext, AgentContext, AgentTaskProgress, SliceAcceptanceItem
from .guidance import MaterialDecisionEvidence, OutcomeKind
from .input_fingerprint import (
    INPUT_POLICY_DIGEST,
    INPUT_POLICY_ID,
    TEST_ADMISSION_POLICY_DIGEST,
    TEST_ADMISSION_POLICY_ID,
    TEST_ENVIRONMENT_PROFILE_ID,
)
from .persistence import JsonValue


@dataclass(frozen=True, slots=True)
class ContinuationSummary:
    status: str
    feature_id: str | None
    feature_title: str | None
    active_slice: SliceAcceptanceItem | None
    accepted_slice: AcceptedSliceContext | None
    lifecycle: tuple[str, str] | None
    current_work: str | None
    task_progress: AgentTaskProgress | None
    criterion_verification: JsonValue
    slice_outcome_status: str | None
    terminal_history: bool
    decision: MaterialDecisionEvidence | None
    next_action: str | None
    next_action_allowed: bool | None
    next_code: str
    next_message: str
    required_input: tuple[str, ...]
    next_options_kind: str | None
    next_options: tuple[str, ...]
    other_slices: tuple[SliceAcceptanceItem, ...]
    inventory_visible: int
    inventory_total: int
    inventory_complete: bool
    inventory_truncated: bool
    inventory_history_scope: str
    queue_visible: int
    queue_total: int
    queue_complete: bool
    queue_relations_total: int
    queue_relations_complete: bool
    queue_next_code: str
    queue_candidates_total: int
    queue_candidates_complete: bool


def project_continuation(context: AgentContext) -> ContinuationSummary:
    """Project continuation cues without selecting, activating, or accepting work."""
    inventory = context.slice_acceptance_inventory
    accepted = context.accepted_slice
    active_item = None
    if accepted is not None:
        active_item = next(
            (
                item for item in inventory.items
                if item.slice_agreement_ref == accepted.slice_agreement_ref
                and item.authority_status == "CANONICAL"
                and item.scope_status == "ACTIVE"
            ),
            None,
        )
    source_unavailable = accepted is not None and active_item is None
    if accepted is None and context.lifecycle is not None and any(
        item.authority_status == "CANONICAL"
        and item.scope_status == "ACTIVE"
        and item.feature_id == context.feature_id
        for item in inventory.items
    ):
        source_unavailable = True

    feature_id = context.feature_id or (None if active_item is None else active_item.feature_id)
    feature_title = (
        None if source_unavailable
        else active_item.feature_title if active_item is not None
        else accepted.feature_title if accepted is not None
        else _feature_title(inventory.items, feature_id)
    )
    other_slices = () if source_unavailable else tuple(
        item for item in inventory.items
        if item.authority_status == "CANONICAL"
        and item.slice_agreement_ref != (None if active_item is None else active_item.slice_agreement_ref)
        and (feature_id is None or item.feature_id == feature_id)
    )

    status = "NO_ACTIVE_WORK"
    next_action: str | None = None
    next_action_allowed: bool | None = None
    next_code = "NO_ACTIVE_WORK"
    next_message = "No active lifecycle work or eligible queued item is recorded."
    required_input: tuple[str, ...] = ()
    next_options_kind: str | None = None
    next_options: tuple[str, ...] = ()
    decision: MaterialDecisionEvidence | None = None

    if context.initialization_state == "UNINITIALIZED":
        status, next_code, next_action = "UNINITIALIZED", "INITIALIZE_AUTHORITY", "init"
        next_action_allowed = True
        next_message = "Repository authority initialization is required."
    elif context.stale:
        status, next_code = "STALE", "STALE_AUTHORITY"
        next_action_allowed = False
        next_message = context.stale_reason or "Canonical authority is stale; stop continuation guidance."
    elif source_unavailable:
        status, next_code = "SOURCE_UNAVAILABLE", "ACTIVE_SLICE_PROJECTION_UNAVAILABLE"
        next_action_allowed = False
        next_message = "The active Slice does not have a matching canonical inventory record."
    elif context.outcome is not None and context.outcome.kind is OutcomeKind.STRUCTURAL_DEFECT:
        status, next_code = "BLOCKED", "STRUCTURAL_DEFECT"
        next_action_allowed = False
        next_message = context.outcome.message
    elif (
        context.outcome is not None
        and context.outcome.kind is OutcomeKind.HUMAN_MATERIAL_DECISION
        and context.outcome.material_decision is not None
    ):
        status, next_code = "DECISION_REQUIRED", "MATERIAL_DECISION_REQUIRED"
        decision = context.outcome.material_decision
        next_message = context.outcome.message
        required_input = ("Choose one exact option offered by canonical Core.",)
        next_options_kind = "MATERIAL_DECISION"
        next_options = decision.options
    elif context.outcome is not None and context.outcome.kind is OutcomeKind.EXTERNAL_WAIT:
        status, next_code = "EXTERNAL_WAIT", "EXTERNAL_WAIT"
        next_message = context.outcome.message
        if context.lifecycle is not None and context.lifecycle[0] == "PAUSED" and "continue" in context.legal_actions:
            next_action, next_action_allowed = "continue", True
            required_input = ("Resume only after the external condition is resolved.",)
    elif context.lifecycle is None and context.work_queue.next_code in ("AMBIGUOUS", "SELECTED") and (
        "activate_work" not in context.legal_actions
    ):
        status, next_code = "BLOCKED", "QUEUE_ACTIVATION_UNAVAILABLE"
        next_message = "The queue projection does not override canonical action guidance."
        next_action_allowed = False
    elif accepted is not None:
        status = "ACTIVE_SLICE"
        next_code, next_action, next_message, required_input, next_action_allowed = _active_guidance(context)
    elif context.lifecycle is not None:
        status = "ACTIVE_WORK_WITHOUT_SLICE"
        next_code, next_action, next_message, required_input, next_action_allowed = _active_guidance(context)
        next_message = (
            "No accepted Slice is active; " + next_message
        )
    elif context.work_queue.next_code == "AMBIGUOUS":
        status, next_code, next_action = "QUEUE_AMBIGUOUS", "QUEUE_AMBIGUOUS", "activate_work"
        next_action_allowed = True
        next_options_kind = "QUEUE_CANDIDATES"
        next_options = context.work_queue.next_candidates
        required_input = ("Select one exact work item ID; no candidate is selected by this summary.",)
        next_message = "Core reports multiple eligible queued items; an explicit choice is required."
    elif context.work_queue.next_code == "SELECTED" and context.work_queue.next is not None:
        status, next_code, next_action = "QUEUE_READY", "QUEUE_SELECTED", "activate_work"
        next_action_allowed = True
        next_options_kind = "CORE_IDENTIFIED_CANDIDATE"
        next_options = (context.work_queue.next,)
        required_input = ("Explicitly activate the exact Core-identified work item.",)
        next_message = "Core identifies one eligible queue item; it is not active until explicitly activated."
    elif context.work_queue.next_code != "NONE":
        status, next_code = "SOURCE_UNAVAILABLE", "QUEUE_PROJECTION_UNAVAILABLE"
        next_action_allowed = False
        next_message = "The active-work and canonical queue projections disagree; stop continuation guidance."
    elif inventory.total == 0 and not inventory.complete:
        status, next_code = "SOURCE_UNAVAILABLE", "SLICE_INVENTORY_INCOMPLETE"
        next_action_allowed = False
        next_message = "No Slice inventory is available to establish acceptance history; stop before inferring a next Slice."
    elif inventory.total == 0:
        status, next_code = "EMPTY_SLOT", "NO_ACCEPTED_SLICE"
        next_message = (
            "No accepted Slice is recorded. This does not establish an empty work ledger "
            "or eligibility for first-Slice acceptance; consult canonical lifecycle guidance."
        )
    elif not context.r3b_schema_ready:
        status, next_code = "EMPTY_SLOT", "R3B_SCHEMA_UPGRADE_REQUIRED"
        required_input = ("Explicit schema-17 approval before accepting another Slice.",)
        next_message = "No Slice is current; next-Slice acceptance is unavailable until schema 17 is approved."
    elif any(item.authority_status == "CANONICAL" for item in inventory.items):
        status, next_code = "EMPTY_SLOT", "SLICE_ACCEPTANCE_REQUIRES_EXPLICIT_INPUT"
        required_input = (
            "An empty active/preserved/legacy parked slot with no pending intake or deferred side request.",
            "Exact existing primary Feature ID, new Slice title, initial Task, checkpoint, and complete criteria mini-spec.",
            "Explicit interactive human acceptance; this read-only summary does not select or accept a Slice.",
        )
        next_message = "No Slice is current. Another Slice requires the exact Core acceptance preconditions and explicit input."
    else:
        status, next_code = "SOURCE_UNAVAILABLE", "SLICE_INVENTORY_INCOMPLETE"
        next_action_allowed = False
        next_message = "No visible canonical Slice establishes an existing accepted Feature."

    return ContinuationSummary(
        status=status,
        feature_id=feature_id,
        feature_title=feature_title,
        active_slice=active_item,
        accepted_slice=accepted if active_item is not None else None,
        lifecycle=context.lifecycle,
        current_work=context.progress.current_work,
        task_progress=context.task_progress if active_item is not None else None,
        criterion_verification=(
            None if source_unavailable else criterion_verification(context)
        ),
        slice_outcome_status=(
            None if active_item is None or context.slice_outcome is None
            else context.slice_outcome.status
        ),
        terminal_history=context.completed_terminal,
        decision=decision,
        next_action=next_action,
        next_action_allowed=next_action_allowed,
        next_code=next_code,
        next_message=next_message,
        required_input=required_input,
        next_options_kind=next_options_kind,
        next_options=next_options,
        other_slices=other_slices,
        inventory_visible=len(inventory.items),
        inventory_total=inventory.total,
        inventory_complete=inventory.complete,
        inventory_truncated=inventory.truncated,
        inventory_history_scope=inventory.history_scope,
        queue_visible=len(context.work_queue.all),
        queue_total=context.work_queue.total,
        queue_complete=context.work_queue.complete,
        queue_relations_total=context.work_queue.relations_total,
        queue_relations_complete=context.work_queue.relations_complete,
        queue_next_code=context.work_queue.next_code,
        queue_candidates_total=context.work_queue.next_candidates_total,
        queue_candidates_complete=context.work_queue.next_candidates_complete,
    )


def criterion_verification(context: AgentContext) -> JsonValue:
    """Return the canonical per-criterion test projection shared by CLI and protocol."""
    accepted = context.accepted_slice
    if accepted is None or accepted.criteria_revision is None:
        return None
    result: list[JsonValue] = []
    for criterion in accepted.criteria_revision.criteria:
        observations = tuple(
            item for item in context.criterion_test_receipts
            if item.criteria_revision_id == accepted.criteria_revision.id
            and item.criterion_id == criterion.id
        )
        receipt = None if not observations else observations[-1]
        strong = receipt is not None and receipt.input_fingerprint_before is not None
        criterion_blockers = () if context.slice_outcome is None else context.slice_outcome.blockers
        admission_blocked = any(
            blocker in {
                f"COVERAGE_APPROVAL_REQUIRED:{criterion.id}",
                f"UNSUPPORTED_CRITERION:{criterion.id}",
                f"CRITERION_PROVENANCE_STALE:{criterion.id}",
                f"CRITERION_RUNTIME_UNVERIFIABLE:{criterion.id}",
            }
            for blocker in criterion_blockers
        )
        fresh = bool(
            strong
            and not admission_blocked
            and context.input_snapshot is not None
            and receipt is not None
            and receipt.input_fingerprint_before == context.input_snapshot.fingerprint
            and receipt.input_fingerprint_after == context.input_snapshot.fingerprint
            and receipt.input_policy_id == INPUT_POLICY_ID
            and receipt.input_policy_digest == INPUT_POLICY_DIGEST
            and receipt.admission_policy_id == TEST_ADMISSION_POLICY_ID
            and receipt.admission_policy_digest == TEST_ADMISSION_POLICY_DIGEST
            and receipt.environment_profile_id == TEST_ENVIRONMENT_PROFILE_ID
            and any(
                approval.id == receipt.coverage_approval_id
                and approval.contract_digest == receipt.coverage_contract_digest
                and approval.criterion_id == criterion.id
                and context.input_snapshot is not None
                and approval.root_fingerprint == context.input_snapshot.fingerprint
                for approval in context.criterion_coverage_approvals
            )
            and not receipt.external_inputs
        )
        if receipt is None:
            status = "UNVERIFIED"
        elif receipt.exit_code != 0:
            status = "FAILED"
        elif not strong:
            status = "VERIFIED"
        elif context.input_snapshot is None:
            status = "REVALIDATION_REQUIRED"
        elif admission_blocked:
            status = "STALE"
        else:
            status = "VERIFIED" if fresh else "STALE"
        last_test: dict[str, JsonValue] | None = None
        if receipt is not None:
            last_test = {
                "receipt_id": receipt.id,
                "kind": receipt.test_kind,
                "target": receipt.test_target,
                "exit_code": receipt.exit_code,
                "output_sha256": receipt.output_sha256,
                "recorded_revision": receipt.recorded_revision,
            }
            if strong:
                last_test.update({
                    "input_fingerprint_before": receipt.input_fingerprint_before,
                    "input_fingerprint_after": receipt.input_fingerprint_after,
                    "input_policy_id": receipt.input_policy_id,
                    "input_policy_digest": receipt.input_policy_digest,
                    "admission_policy_id": receipt.admission_policy_id,
                    "admission_policy_digest": receipt.admission_policy_digest,
                    "external_inputs": list(receipt.external_inputs),
                    "runtime_executable": receipt.runtime_executable,
                    "runtime_executable_sha256": receipt.runtime_executable_sha256,
                    "runtime_version": receipt.runtime_version,
                    "runtime_argv": list(receipt.runtime_argv),
                    "environment_profile_id": receipt.environment_profile_id,
                    "environment_profile_digest": receipt.environment_profile_digest,
                    "coverage_approval_id": receipt.coverage_approval_id,
                    "coverage_contract_digest": receipt.coverage_contract_digest,
                })
        result.append({
            "criterion_id": criterion.id,
            "criteria_revision_id": accepted.criteria_revision.id,
            "status": status,
            "evidence_source": None if receipt is None else (
                "CORE_VALIDATED_LOCAL_TEST" if strong and not admission_blocked else "HOST_REPORTED_TEST"
            ),
            "fresh_for_current_workspace": fresh,
            "enables_slice_outcome": bool(
                fresh and receipt is not None and receipt.exit_code == 0
                and context.slice_outcome is not None
                and context.slice_outcome.status in ("READY", "CURRENT")
            ),
            "last_test": last_test,
        })
    return result


def _active_guidance(
    context: AgentContext,
) -> tuple[str, str | None, str, tuple[str, ...], bool]:
    start = next((item for item in context.guidance if item.action.value == "new"), None)
    if start is None:
        return "GUIDANCE_UNAVAILABLE", None, "Canonical next-action guidance is unavailable.", (), False
    if start.allowed:
        return "INTAKE_AVAILABLE", start.action.value, start.message, (), True
    if start.next_action is None:
        return start.reason_code or "GUIDANCE_BLOCKED", None, start.message, (), False
    guidance = next(
        (item for item in context.guidance if item.action is start.next_action),
        None,
    )
    if guidance is None:
        return "GUIDANCE_UNAVAILABLE", start.next_action.value, start.message, (), False
    if guidance.allowed and guidance.action.value in context.legal_actions:
        return "CANONICAL_GUIDANCE", guidance.action.value, guidance.message, (), True
    return (
        guidance.reason_code or "GUIDANCE_BLOCKED",
        guidance.action.value,
        guidance.message,
        guidance.required_actions and tuple(item.value for item in guidance.required_actions) or (),
        False,
    )


def _feature_title(items: tuple[SliceAcceptanceItem, ...], feature_id: str | None) -> str | None:
    if feature_id is None:
        return None
    titles = {
        item.feature_title for item in items
        if item.authority_status == "CANONICAL"
        and item.feature_id == feature_id
        and item.feature_title is not None
    }
    return next(iter(titles)) if len(titles) == 1 else None
