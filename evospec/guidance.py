"""Pure provider-neutral action guidance over authoritative workflow facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .domain import IrreversibleAuthorizationStatus, OperationalState, ReviewInspection, WorkingContext
from .lifecycle import LifecycleState, Phase, can_converge, has_acceptable_review


class Intervention(StrEnum):
    AUTOMATIC = "AUTOMATIC"
    USER = "USER"
    NONE = "NONE"


class OutcomeKind(StrEnum):
    """The bounded, fact-derived disposition of the current Core projection."""

    HUMAN_MATERIAL_DECISION = "HUMAN_MATERIAL_DECISION"
    SAFE_AUTOMATIC_OPERATION = "SAFE_AUTOMATIC_OPERATION"
    BOUNDED_DELEGATION = "BOUNDED_DELEGATION"
    EXTERNAL_WAIT = "EXTERNAL_WAIT"
    STRUCTURAL_DEFECT = "STRUCTURAL_DEFECT"


@dataclass(frozen=True, slots=True)
class MaterialDecisionEvidence:
    """A concrete, Core-offered material decision with distinct choices."""

    decision: str
    options: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.decision.strip() or len(self.options) < 2:
            raise ValueError("material decision evidence requires a decision and two options")
        if any(not option.strip() for option in self.options) or len(set(self.options)) != len(self.options):
            raise ValueError("material decision options must be distinct nonblank values")


@dataclass(frozen=True, slots=True)
class Outcome:
    """Read-only disposition derived from authoritative facts, never an action name."""

    kind: OutcomeKind
    message: str
    material_decision: MaterialDecisionEvidence | None = None

    def __post_init__(self) -> None:
        if self.kind is OutcomeKind.HUMAN_MATERIAL_DECISION and self.material_decision is None:
            raise ValueError("human outcomes require material decision evidence")
        if self.kind is not OutcomeKind.HUMAN_MATERIAL_DECISION and self.material_decision is not None:
            raise ValueError("only human outcomes may expose material decision evidence")

    @property
    def intervention(self) -> Intervention:
        """Project the legacy intervention field without reclassifying the outcome."""
        return {
            OutcomeKind.HUMAN_MATERIAL_DECISION: Intervention.USER,
            OutcomeKind.SAFE_AUTOMATIC_OPERATION: Intervention.AUTOMATIC,
            OutcomeKind.BOUNDED_DELEGATION: Intervention.AUTOMATIC,
            OutcomeKind.EXTERNAL_WAIT: Intervention.AUTOMATIC,
            OutcomeKind.STRUCTURAL_DEFECT: Intervention.NONE,
        }[self.kind]


class Action(StrEnum):
    CONTEXT = "context"
    INIT = "init"
    NEW = "new"
    CHANGE = "change"
    CLARIFY = "clarify"
    CONTINUE = "continue"
    PARK = "park"
    RESUME = "resume"
    LIST_WORK = "list_work"
    NEXT_WORK = "next_work"
    ACTIVATE_WORK = "activate_work"
    SET_WORK_STATE = "set_work_state"
    ADD_WORK_RELATION = "add_work_relation"
    REMOVE_WORK_RELATION = "remove_work_relation"
    REJECT = "reject"
    PROGRESS = "progress"
    ASSESS_LOCKS = "assess_locks"
    REVIEW = "review"
    VERIFY = "verify"
    CONVERGE = "converge"
    INVALIDATE_ASSURANCE = "invalidate_assurance"
    AUTHORIZE_IRREVERSIBLE = "authorize_irreversible"
    EXECUTE_IRREVERSIBLE = "execute_irreversible"
    PROPOSE_AUTONOMY_EXPANSION = "propose_autonomy_expansion"
    RESOLVE_AUTONOMY_EXPANSION = "resolve_autonomy_expansion"


@dataclass(frozen=True, slots=True)
class ActionGuidance:
    action: Action
    allowed: bool
    reason_code: str | None
    blocker: str | None
    required_actions: tuple[Action, ...]
    next_action: Action | None
    intervention: Intervention
    scope: tuple[str, ...]
    message: str


_ACTIONS = tuple(Action)
_COMMON = (Action.CONTEXT, Action.NEW, Action.CHANGE, Action.CONTINUE, Action.PARK, Action.RESUME)
_ACTION_ORDER = {
    Action.CONTEXT: 0, Action.NEW: 1, Action.CHANGE: 2, Action.CONTINUE: 3, Action.RESUME: 4,
    Action.LIST_WORK: 5, Action.NEXT_WORK: 6, Action.ACTIVATE_WORK: 7,
    Action.SET_WORK_STATE: 8, Action.ADD_WORK_RELATION: 9, Action.REMOVE_WORK_RELATION: 10,
    Action.PARK: 11, Action.CLARIFY: 12, Action.PROGRESS: 12, Action.ASSESS_LOCKS: 13,
    Action.REVIEW: 14, Action.INVALIDATE_ASSURANCE: 15, Action.REJECT: 16,
    Action.VERIFY: 17, Action.CONVERGE: 18,
    Action.AUTHORIZE_IRREVERSIBLE: 19, Action.EXECUTE_IRREVERSIBLE: 20,
    Action.PROPOSE_AUTONOMY_EXPANSION: 21, Action.RESOLVE_AUTONOMY_EXPANSION: 22,
}


def project(
    inspection: ReviewInspection, *, queue_activation_available: bool = False
) -> tuple[ActionGuidance, ...]:
    """Project all canonical action facts from one durable inspection snapshot."""
    return tuple(
        for_action(
            inspection, action, queue_activation_available=queue_activation_available
        )
        for action in _ACTIONS
    )


def allowed_actions(
    inspection: ReviewInspection, *, queue_activation_available: bool = False
) -> tuple[str, ...]:
    """Return canonical protocol action names that are currently available."""
    guidance = project(
        inspection, queue_activation_available=queue_activation_available
    )
    if inspection.state.initialization is None:
        return tuple(item.action.value for item in guidance if item.allowed and item.action is Action.INIT) + (Action.CONTEXT.value,)
    return tuple(
        item.action.value
        for item in sorted((item for item in guidance if item.allowed), key=lambda item: _ACTION_ORDER[item.action])
    )


def for_action(
    inspection: ReviewInspection,
    action: Action | str,
    *,
    queue_activation_available: bool = False,
) -> ActionGuidance:
    """Return the deterministic non-mutating action decision for one request."""
    selected = Action(action)
    state = inspection.state
    context = state.active_context
    if selected is Action.CONTEXT:
        return _allow(selected, "authoritative workflow context is available")
    if state.initialization is None and selected is Action.INIT:
        return _allow(selected, "repository authority can be initialized")
    if state.initialization is None:
        return _block(selected, "INIT_REQUIRED", "repository authority initialization is required", (Action.INIT,), Action.INIT)
    if selected is Action.INIT:
        return _block(selected, "ALREADY_INITIALIZED", "repository authority is already initialized", (), None)
    if (
        state.pending_autonomy_expansion is not None
        and selected is not Action.RESOLVE_AUTONOMY_EXPANSION
    ):
        return _block(
            selected,
            "AUTONOMY_EXPANSION_PENDING",
            "resolve the exact pending autonomy proposal before another Core action",
            (Action.RESOLVE_AUTONOMY_EXPANSION,),
            Action.RESOLVE_AUTONOMY_EXPANSION,
            () if context is None else _scope(context),
        )
    pending_authorizations = tuple(
        item
        for item in state.irreversible_authorizations
        if item.status in (
            IrreversibleAuthorizationStatus.PENDING,
            IrreversibleAuthorizationStatus.READY,
        )
    )
    if selected is Action.AUTHORIZE_IRREVERSIBLE:
        if context is None or context.change_id is None:
            return _block(selected, "NO_ACTIVE_WORK", "one-shot authorization requires active Feature and Change", (Action.NEW, Action.CHANGE), Action.NEW)
        if context.status is LifecycleState.PAUSED:
            return _block(selected, "NOTHING_TO_RESUME", "paused work must resume before authorization", (Action.CONTINUE,), Action.CONTINUE, _scope(context))
        if context.status not in (LifecycleState.IN_PROGRESS, LifecycleState.VERIFYING):
            return _block(selected, "ACTIVE_WORK_REQUIRED", "one-shot authorization requires active, nonterminal Feature and Change work", (), None, _scope(context))
        if pending_authorizations:
            return _block(selected, "IRREVERSIBLE_AUTH_PENDING", "an irreversible authorization is already pending", (), Action.EXECUTE_IRREVERSIBLE, _scope(context))
        return _allow(selected, "explicit human authorization can be recorded for one exact operation", _scope(context))
    if selected is Action.EXECUTE_IRREVERSIBLE:
        if pending_authorizations:
            return _allow(selected, "Core can validate and execute the exact authorized Git operation", () if context is None else _scope(context))
        return _block(selected, "NO_PENDING_IRREVERSIBLE_AUTH", "no pending irreversible authorization can be executed", (), None)
    if selected is Action.PROPOSE_AUTONOMY_EXPANSION:
        if context is None or context.change_id is None:
            return _block(selected, "NO_ACTIVE_WORK", "autonomy expansion requires active Feature and Change work", (Action.NEW, Action.CHANGE), Action.NEW)
        if context.status not in (LifecycleState.PROPOSED, LifecycleState.IN_PROGRESS, LifecycleState.VERIFYING):
            return _block(selected, "ACTIVE_WORK_REQUIRED", "autonomy expansion requires active, nonterminal Feature and Change work", (), None, _scope(context))
        if context.status is LifecycleState.PAUSED:
            return _block(selected, "NOTHING_TO_RESUME", "paused work must resume before proposing autonomy", (Action.CONTINUE,), Action.CONTINUE, _scope(context))
        if context.unresolved_material_decision:
            return _block(selected, "MATERIAL_DECISION_PENDING", "resolve the existing material decision before proposing autonomy", (Action.CLARIFY,), Action.CLARIFY, _scope(context))
        if pending_authorizations:
            return _block(selected, "IRREVERSIBLE_AUTH_PENDING", "resolve the reserved one-shot authorization before proposing autonomy", (), Action.EXECUTE_IRREVERSIBLE, _scope(context))
        if state.pending_autonomy_expansion is not None:
            return _block(selected, "AUTONOMY_EXPANSION_PENDING", "resolve the pending autonomy proposal before another request", (), Action.RESOLVE_AUTONOMY_EXPANSION, _scope(context))
        return _allow(selected, "one exact autonomy envelope delta can be proposed for explicit human review", _scope(context))
    if selected is Action.RESOLVE_AUTONOMY_EXPANSION:
        if state.pending_autonomy_expansion is None:
            return _block(selected, "NO_AUTONOMY_EXPANSION_PENDING", "no exact autonomy expansion proposal is awaiting a decision", (), None)
        return _allow(selected, "the pending exact autonomy proposal can be authorized, rejected, or narrowed", () if context is None else _scope(context))
    if state.pending_autonomy_expansion is not None:
        return _block(
            selected,
            "AUTONOMY_EXPANSION_PENDING",
            "resolve the exact pending autonomy proposal before another Core action",
            (Action.RESOLVE_AUTONOMY_EXPANSION,),
            Action.RESOLVE_AUTONOMY_EXPANSION,
            () if context is None else _scope(context),
        )
    if selected in (Action.LIST_WORK, Action.NEXT_WORK):
        return _allow(selected, "work queue can be projected")
    if selected is Action.ACTIVATE_WORK:
        if queue_activation_available:
            return _allow(selected, "ready queued work can be activated after Core revalidation")
        return _block(selected, "NO_ACTIONABLE_WORK", "no ready queued work satisfies strict activation", (), None)
    if selected is Action.SET_WORK_STATE:
        if any(item.operational_state is not OperationalState.DONE for item in state.work_items):
            return _allow(selected, "nonterminal queued work can change operational state")
        return _block(selected, "NO_MUTABLE_WORK", "no nonterminal queued work can change operational state", (), None)
    identities = set(item.id for item in state.work_items)
    if state.active_work_id is not None:
        identities.add(state.active_work_id)
    if selected is Action.ADD_WORK_RELATION:
        if len(identities) >= 2:
            return _allow(selected, "known work identities can be related")
        return _block(selected, "RELATION_ENDPOINTS_REQUIRED", "two known work identities are required to add a relation", (), None)
    if selected is Action.REMOVE_WORK_RELATION:
        if state.work_relations:
            return _allow(selected, "existing work relations can be removed")
        return _block(selected, "NO_WORK_RELATIONS", "no work relation is available to remove", (), None)
    if selected is Action.PARK:
        if context is None:
            return _block(selected, "NO_ACTIVE_WORK", "no active work is available to park", (Action.NEW, Action.CHANGE), Action.NEW)
        return _allow(selected, "active work can be parked exactly", _scope(context))
    if selected is Action.RESUME:
        if context is not None:
            return _block(selected, "ACTIVE_WORK", "active work occupies the slot required to resume parked work", (), _recovery_action(context), _scope(context))
        if not state.work_items:
            return _block(selected, "NO_PARKED_WORK", "no queued work is available to resume", (Action.NEW, Action.CHANGE), Action.NEW)
        return _allow(selected, "queued work can resume after revalidation")
    if selected in (Action.NEW, Action.CHANGE):
        if state.pending_intake is not None:
            return _block(selected, "PENDING_INTAKE", "pending intake requires an explicit user selection", (), None)
        if context is not None:
            return _block(selected, "ACTIVE_WORK", "active work requires lifecycle progress or explicit terminal handling before intake", (), _recovery_action(context), _scope(context))
        return _allow(selected, "intake is available")
    if selected is Action.CLARIFY:
        if context is None:
            return _block(selected, "NO_ACTIVE_WORK", "no active work requires clarification", (Action.NEW, Action.CHANGE), Action.NEW)
        if not context.unresolved_material_decision:
            return _block(selected, "CLARIFICATION_NOT_REQUIRED", "no unresolved material clarification", (), _recovery_action(context), _scope(context))
        return _allow(selected, "material clarification is required", _scope(context))
    if selected is Action.CONTINUE:
        if context is None:
            return _block(selected, "NOTHING_TO_RESUME", "no paused work is available to resume", (Action.NEW, Action.CHANGE), Action.NEW)
        if context.status is LifecycleState.PAUSED:
            return _allow(selected, "paused work can resume its preserved lifecycle pair", _scope(context), _next_after_continue(context))
        return _block(selected, "NOTHING_TO_RESUME", "active work does not require resumption", (), _recovery_action(context), _scope(context))
    if context is None:
        return _block(selected, "NO_ACTIVE_WORK", "the action requires active work", (Action.NEW, Action.CHANGE), Action.NEW)
    if context.status is LifecycleState.PAUSED:
        return _block(selected, "NOTHING_TO_RESUME", "paused work must resume before this action", (Action.CONTINUE,), Action.CONTINUE, _scope(context))
    return _active_guidance(inspection, selected, context)


def _active_guidance(inspection: ReviewInspection, action: Action, context: WorkingContext) -> ActionGuidance:
    scope = _scope(context)
    if action is Action.PROGRESS:
        if context.phase in (Phase.RESEARCH, Phase.READY, Phase.IMPLEMENT):
            return _allow(action, "implementation progress can be recorded", scope)
        return _block(action, "REVIEW_REQUIRED", "progress is unavailable after implementation review begins", (Action.REVIEW,), Action.REVIEW, scope)
    if action is Action.REVIEW:
        if context.phase in (Phase.RESEARCH, Phase.READY, Phase.IMPLEMENT, Phase.REVIEW):
            return _allow(action, "review can be recorded", scope)
        return _block(action, "VERIFICATION_REQUIRED", "review assurance is already complete enough to verify", (Action.VERIFY,), Action.VERIFY, scope)
    if action is Action.VERIFY:
        if context.phase not in (Phase.REVIEW, Phase.VERIFY) or not has_acceptable_review(context.review):
            return _block(action, "REVIEW_REQUIRED", "verification requires an acceptable recorded review", (Action.REVIEW,), Action.REVIEW, scope)
        return _allow(action, "verification can be recorded", scope)
    if action is Action.CONVERGE:
        if not has_acceptable_review(context.review):
            return _block(action, "REVIEW_REQUIRED", "convergence requires an acceptable recorded review", (Action.REVIEW,), Action.REVIEW, scope)
        if not context.verification.sufficient:
            return _block(action, "VERIFICATION_REQUIRED", "convergence requires sufficient verification", (Action.VERIFY,), Action.VERIFY, scope)
        if context.phase not in (Phase.VERIFY, Phase.CONVERGE) or not can_converge(context.review, context.verification):
            return _block(action, "VERIFICATION_REQUIRED", "convergence requires verification lifecycle availability", (Action.VERIFY,), Action.VERIFY, scope)
        if _stale_lock(inspection, context):
            return _block(action, "ASSURANCE_REQUIRED", "semantic lock assessment is missing or stale", (Action.ASSESS_LOCKS,), Action.ASSESS_LOCKS, scope)
        return _allow(action, "convergence can be recorded", scope)
    if action is Action.INVALIDATE_ASSURANCE:
        if context.phase in (Phase.REVIEW, Phase.VERIFY, Phase.CONVERGE):
            return _allow(action, "recorded assurance can be invalidated", scope)
        return _block(action, "REVIEW_REQUIRED", "assurance exists only after review begins", (Action.PROGRESS,), Action.PROGRESS, scope)
    if action is Action.ASSESS_LOCKS:
        if context.change_id is None:
            return _block(action, "NO_ACTIVE_WORK", "lock assessment requires an active change", (Action.NEW, Action.CHANGE), Action.NEW, scope)
        return _allow(action, "semantic locks can be assessed", scope)
    if action is Action.REJECT:
        if context.phase is Phase.CLARIFY:
            return _block(action, "CLARIFICATION_NOT_REQUIRED", "clarifying work cannot be rejected through this action", (Action.CLARIFY,), Action.CLARIFY, scope)
        return _allow(action, "active work can be terminalized", scope)
    return _block(action, "LIFECYCLE_UNAVAILABLE", "action is unavailable for the active lifecycle pair", (), _recovery_action(context), scope)


def _stale_lock(inspection: ReviewInspection, context: WorkingContext) -> bool:
    if context.change_id is None:
        return False
    change = next((item for item in inspection.changes if item.id == context.change_id), None)
    assessments = () if change is None else change.lock_assessments
    return any(
        lock.active and lock.feature_id == context.feature_id
        and not any(assessment.lock_id == lock.id and assessment.lock_revision == lock.current_revision for assessment in assessments)
        for lock in inspection.locks
    )


def _scope(context: WorkingContext) -> tuple[str, ...]:
    return context.safety_facts.scope


def _next_for_context(context: WorkingContext) -> Action | None:
    if context.unresolved_material_decision:
        return Action.CLARIFY
    return {
        Phase.RESEARCH: Action.PROGRESS,
        Phase.READY: Action.PROGRESS,
        Phase.IMPLEMENT: Action.REVIEW,
        Phase.REVIEW: Action.VERIFY if has_acceptable_review(context.review) else Action.REVIEW,
        Phase.VERIFY: Action.CONVERGE if can_converge(context.review, context.verification) else Action.VERIFY,
        Phase.CONVERGE: Action.CONVERGE,
        Phase.CLARIFY: Action.CLARIFY,
    }.get(context.phase)


def _recovery_action(context: WorkingContext) -> Action | None:
    return Action.CONTINUE if context.status is LifecycleState.PAUSED else _next_for_context(context)


def _next_after_continue(context: WorkingContext) -> Action | None:
    paused_pair = context.paused_pair
    if paused_pair is None:
        return None
    _, phase = paused_pair
    return {
        Phase.RESEARCH: Action.PROGRESS,
        Phase.READY: Action.PROGRESS,
        Phase.IMPLEMENT: Action.REVIEW,
        Phase.REVIEW: Action.VERIFY if has_acceptable_review(context.review) else Action.REVIEW,
        Phase.VERIFY: Action.CONVERGE if can_converge(context.review, context.verification) else Action.VERIFY,
        Phase.CONVERGE: Action.CONVERGE,
        Phase.CLARIFY: Action.CLARIFY,
    }.get(phase)


def _allow(action: Action, message: str, scope: tuple[str, ...] = (), next_action: Action | None = None) -> ActionGuidance:
    resolved_next = action if next_action is None else next_action
    return ActionGuidance(action, True, None, None, (), resolved_next, _intervention(resolved_next), scope, message)


def _block(action: Action, reason_code: str, message: str, required_actions: tuple[Action, ...], next_action: Action | None, scope: tuple[str, ...] = ()) -> ActionGuidance:
    return ActionGuidance(action, False, reason_code, message, required_actions, next_action, _intervention(next_action), scope, message)


def _intervention(action: Action | None) -> Intervention:
    if action is None:
        return Intervention.NONE
    if action in (Action.INIT, Action.NEW, Action.CHANGE, Action.CLARIFY, Action.REJECT, Action.CONVERGE, Action.PARK, Action.RESUME, Action.AUTHORIZE_IRREVERSIBLE, Action.RESOLVE_AUTONOMY_EXPANSION):
        return Intervention.USER
    return Intervention.AUTOMATIC
