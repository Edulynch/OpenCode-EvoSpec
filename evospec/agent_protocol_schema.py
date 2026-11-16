"""JSON parsing and serialization for the provider-neutral agent protocol."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TypeVar

from . import domain
from .lifecycle import Classification, ReviewVerdict
from .persistence import JsonValue

T = TypeVar("T")

ACTION_KEYS = {
    "context": frozenset(("action",)),
    "autonomy_evaluate": frozenset(("action", "expected_revision", "operation", "paths", "irreversible")),
    "propose_autonomy_expansion": frozenset(("action", "expected_revision", "delta")),
    "resolve_autonomy_expansion": frozenset(("action", "expected_revision", "proposal_id", "decision", "narrowed_envelope")),
    "init": frozenset(("action", "expected_revision")),
    "new": frozenset(("action", "expected_revision", "intent", "assessment")),
    "change": frozenset(("action", "expected_revision", "intent", "assessment")),
    "clarify": frozenset(("action", "expected_revision", "answers")),
    "structure_material_clarification": frozenset(("action", "expected_revision", "repository_id", "feature_id", "change_id", "legacy_detail", "decision", "options")),
    "recover_legacy_non_material_detail": frozenset(("action", "expected_revision", "repository_id", "feature_id", "change_id", "legacy_detail")),
    "continue": frozenset(("action", "expected_revision")),
    "park": frozenset(("action", "expected_revision", "reason")),
    "resume": frozenset(("action", "expected_revision", "change_id")),
    "list_work": frozenset(("action", "expected_revision", "filter")),
    "next_work": frozenset(("action", "expected_revision")),
    "activate_work": frozenset(("action", "expected_revision", "work_id")),
    "set_work_state": frozenset(("action", "expected_revision", "work_id", "operational_state", "reason")),
    "add_work_relation": frozenset(("action", "expected_revision", "predecessor_id", "successor_id", "kind")),
    "remove_work_relation": frozenset(("action", "expected_revision", "predecessor_id", "successor_id", "kind")),
    "reject": frozenset(("action", "expected_revision")),
    "progress": frozenset(("action", "expected_revision", "current_action", "focus", "completed_work", "current_work", "pending_work", "resume_point")),
    "accepted_task_progress": frozenset((
        "action", "expected_revision", "repository_id", "task_id",
        "feature_agreement_ref", "slice_agreement_ref", "checkpoint",
        "statement", "evidence_refs",
    )),
    "assess_locks": frozenset(("action", "expected_revision", "assessments")),
    "review": frozenset(("action", "expected_revision", "review")),
    "verify": frozenset(("action", "expected_revision", "verification")),
    "converge": frozenset(("action", "expected_revision", "assessment")),
    "invalidate_assurance": frozenset(("action", "expected_revision")),
    "select_capability": frozenset(("action", "expected_revision", "selection_id", "choice")),
    "recover_discovery": frozenset(("action", "expected_revision", "recovery_id", "candidate", "evidence_ids")),
    "authorize_irreversible": frozenset(("action", "expected_revision", "operation_kind", "target_ref", "expected_remote_sha", "rewrite_policy", "target_identity_name", "target_identity_email", "expected_result_sha", "commit_rewrites", "purpose", "force_with_lease")),
    "execute_irreversible": frozenset(("action", "expected_revision", "authorization_id")),
}
INTENT_ASSESSMENT_KEYS = frozenset(("operation", "classification", "confidence", "capability_state", "selected_capability", "candidate_capabilities", "selected_feature_id", "evidence", "material_ambiguities", "recommended_depth", "rationale"))
ASSESSMENT_DEPTH_KEYS = frozenset(("review", "verification", "convergence"))
EVIDENCE_KEYS = frozenset(("source", "reference", "feature_id", "living_spec_id"))
LOCK_ASSESSMENT_KEYS = frozenset(("lock_id", "lock_revision", "affected_aspects", "protected_touches", "preserving_path", "conflict", "evasion", "confidence", "evidence", "rationale"))
CONVERGENCE_ASSESSMENT_KEYS = frozenset(("change_id", "product_truth_changed", "product_truth_content", "confidence", "evidence", "rationale"))
REVIEW_KEYS = frozenset(("verdict", "findings", "safe_to_proceed"))
VERIFICATION_KEYS = frozenset(("evidence", "sufficient"))
AUTONOMY_DELTA_KEYS = frozenset(("allowed_paths", "operation_budget", "reversible_actions", "external_wait_allowed", "retry_budget", "irreversible_permissions", "stop_conditions"))
AUTONOMY_ENVELOPE_KEYS = frozenset(("goal",)) | AUTONOMY_DELTA_KEYS
CLARIFICATION_ANSWER_KEYS = frozenset(("question_id", "answer"))


class ProtocolError(Exception):
    """Raised when transport input cannot become a typed facade request."""


def decode_request(payload: str) -> dict[str, JsonValue]:
    """Decode one strict JSON request object."""

    def reject_constant(value: str) -> JsonValue:
        raise ProtocolError(f"non-finite JSON number {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        decoded: dict[str, JsonValue] = {}
        for key, item in pairs:
            if key in decoded:
                raise ProtocolError("duplicate request key")
            decoded[key] = item
        return decoded

    try:
        value: JsonValue = json.loads(payload, parse_constant=reject_constant, object_pairs_hook=reject_duplicate_keys)
    except (ValueError, RecursionError) as error:
        raise ProtocolError(str(error)) from error
    return _mapping(value, "request")


def parse_action(request: dict[str, JsonValue]) -> str:
    return _string(request, "action")


def validate_action_request(request: dict[str, JsonValue], action: str) -> None:
    """Reject keys outside the declared root contract for an action."""
    try:
        _keys(request, "request", ACTION_KEYS[action])
    except KeyError as error:
        raise ProtocolError("unsupported action") from error


def parse_expected_revision(request: dict[str, JsonValue]) -> domain.ExpectedRevision:
    revision = _integer(request, "expected_revision")
    if revision < 0:
        raise ProtocolError("expected_revision must be nonnegative")
    return domain.ExpectedRevision(revision)


def parse_repository_paths(request: dict[str, JsonValue], name: str) -> tuple[domain.RepositoryPath, ...]:
    values = _strings(request, name)
    try:
        return tuple(domain.RepositoryPath(value) for value in values)
    except domain.DomainError as error:
        raise ProtocolError(f"invalid {name}") from error


def parse_autonomy_envelope_delta(request: dict[str, JsonValue]) -> domain.AutonomyEnvelopeDelta:
    raw = _mapping(_value(request, "delta"), "autonomy envelope delta")
    _keys(raw, "autonomy envelope delta", AUTONOMY_DELTA_KEYS)
    return _parse_autonomy_delta(raw, "autonomy envelope delta")


def parse_narrowed_autonomy_envelope(request: dict[str, JsonValue]) -> domain.AutonomyEnvelope | None:
    value = _value(request, "narrowed_envelope")
    if value is None:
        return None
    raw = _mapping(value, "narrowed autonomy envelope")
    _keys(raw, "narrowed autonomy envelope", AUTONOMY_ENVELOPE_KEYS)
    goal = _string(raw, "goal")
    if not goal.strip():
        raise ProtocolError("narrowed autonomy goal must be nonblank")
    delta = _parse_autonomy_delta(raw, "narrowed autonomy envelope")
    if any(value is None for value in (
        delta.allowed_paths,
        delta.operation_budget,
        delta.reversible_actions,
        delta.external_wait_allowed,
        delta.retry_budget,
        delta.irreversible_permissions,
        delta.stop_conditions,
    )):
        raise ProtocolError("narrowed autonomy envelope must specify every authority field")
    assert delta.allowed_paths is not None
    assert delta.operation_budget is not None
    assert delta.reversible_actions is not None
    assert delta.external_wait_allowed is not None
    assert delta.retry_budget is not None
    assert delta.irreversible_permissions is not None
    assert delta.stop_conditions is not None
    return domain.AutonomyEnvelope(
        goal,
        delta.allowed_paths,
        delta.operation_budget,
        delta.reversible_actions,
        delta.external_wait_allowed,
        delta.retry_budget,
        delta.irreversible_permissions,
        delta.stop_conditions,
    )


def parse_autonomy_expansion_decision(request: dict[str, JsonValue]) -> domain.AutonomyExpansionDecision:
    return _enum(
        domain.AutonomyExpansionDecision,
        _string(request, "decision"),
        "autonomy expansion decision",
    )


def _parse_autonomy_delta(raw: dict[str, JsonValue], name: str) -> domain.AutonomyEnvelopeDelta:
    path_value = _value(raw, "allowed_paths")
    action_value = _value(raw, "reversible_actions")
    irreversible_value = _value(raw, "irreversible_permissions")
    stop_value = _value(raw, "stop_conditions")
    operation_value = _value(raw, "operation_budget")
    wait_value = _value(raw, "external_wait_allowed")
    retry_value = _value(raw, "retry_budget")

    def optional_strings(value: JsonValue, label: str) -> tuple[str, ...] | None:
        if value is None:
            return None
        if type(value) is not list or any(type(item) is not str for item in value):
            raise ProtocolError(f"{name} {label} must be an array of strings or null")
        return tuple(item for item in value if isinstance(item, str))

    def optional_integer(value: JsonValue, label: str) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value < 0:
            raise ProtocolError(f"{name} {label} must be a nonnegative integer or null")
        return value

    if path_value is None:
        paths = None
    elif type(path_value) is list and all(type(item) is str for item in path_value):
        try:
            paths = tuple(domain.RepositoryPath(item) for item in path_value if isinstance(item, str))
        except domain.DomainError as error:
            raise ProtocolError(f"{name} contains an invalid repository path") from error
    else:
        raise ProtocolError(f"{name} allowed_paths must be an array of canonical paths or null")
    if wait_value is not None and type(wait_value) is not bool:
        raise ProtocolError(f"{name} external_wait_allowed must be a boolean or null")
    return domain.AutonomyEnvelopeDelta(
        paths,
        optional_integer(operation_value, "operation_budget"),
        optional_strings(action_value, "reversible_actions"),
        wait_value,
        optional_integer(retry_value, "retry_budget"),
        optional_strings(irreversible_value, "irreversible_permissions"),
        optional_strings(stop_value, "stop_conditions"),
    )


def parse_intent_assessment(
    request: dict[str, JsonValue], operation: domain.AssessmentOperation
) -> domain.IntentAssessment:
    raw = _mapping(_value(request, "assessment"), "assessment")
    _keys(raw, "assessment", INTENT_ASSESSMENT_KEYS)
    supplied_operation = _enum(
        domain.AssessmentOperation,
        _string(raw, "operation"),
        "assessment operation",
    )
    if supplied_operation != operation:
        raise ProtocolError("assessment operation does not match action")
    depth = _mapping(_value(raw, "recommended_depth"), "recommended depth")
    _keys(depth, "recommended depth", ASSESSMENT_DEPTH_KEYS)
    return domain.IntentAssessment(
        supplied_operation,
        _enum(Classification, _string(raw, "classification"), "assessment classification"),
        _enum(domain.AssessmentConfidence, _string(raw, "confidence"), "assessment confidence"),
        _enum(domain.CapabilityState, _string(raw, "capability_state"), "assessment capability state"),
        _optional_string(raw, "selected_capability"),
        _strings(raw, "candidate_capabilities"),
        _optional_feature_id(raw, "selected_feature_id"),
        _evidence(raw, "evidence"),
        parse_material_decisions(raw, "material_ambiguities"),
        domain.AssessmentDepth(
            _enum(domain.AssessmentDepthValue, _string(depth, "review"), "assessment review depth"),
            _enum(domain.AssessmentDepthValue, _string(depth, "verification"), "assessment verification depth"),
            _enum(domain.AssessmentDepthValue, _string(depth, "convergence"), "assessment convergence depth"),
        ),
        _string(raw, "rationale"),
    )


def parse_lock_assessments(request: dict[str, JsonValue]) -> tuple[domain.LockAssessment, ...]:
    values = _list(_value(request, "assessments"), "assessments")
    return tuple(
        _lock_assessment(_mapping(item, "lock assessment")) for item in values
    )


def parse_convergence_assessment(request: dict[str, JsonValue]) -> domain.ConvergenceAssessment:
    raw = _mapping(_value(request, "assessment"), "convergence assessment")
    _keys(raw, "convergence assessment", CONVERGENCE_ASSESSMENT_KEYS)
    product_truth_changed = _boolean(raw, "product_truth_changed")
    return domain.ConvergenceAssessment(
        domain.ChangeId(_string(raw, "change_id")),
        product_truth_changed,
        _enum(domain.AssessmentConfidence, _string(raw, "confidence"), "convergence confidence"),
        _evidence(raw, "evidence"),
        _string(raw, "rationale"),
        _product_truth_content(raw, product_truth_changed),
    )


def parse_review_result(request: dict[str, JsonValue]) -> domain.ReviewResult:
    raw = _mapping(_value(request, "review"), "review")
    _keys(raw, "review", REVIEW_KEYS)
    return domain.ReviewResult(
        _enum(ReviewVerdict, _string(raw, "verdict"), "review verdict"),
        _strings(raw, "findings"),
        _boolean(raw, "safe_to_proceed"),
    )


def parse_verification_evidence(
    request: dict[str, JsonValue],
) -> domain.VerificationEvidence:
    raw = _mapping(_value(request, "verification"), "verification")
    _keys(raw, "verification", VERIFICATION_KEYS)
    return domain.VerificationEvidence(
        _strings(raw, "evidence"),
        _boolean(raw, "sufficient"),
    )


def parse_clarification_answers(
    request: dict[str, JsonValue],
) -> tuple[tuple[str, str], ...]:
    values = _list(_value(request, "answers"), "answers")
    return tuple(
        _clarification_answer(_mapping(value, "clarification answer"))
        for value in values
    )


def parse_optional_string(raw: dict[str, JsonValue], name: str) -> str | None:
    return _optional_string(raw, name)


def parse_string(raw: dict[str, JsonValue], name: str) -> str:
    return _string(raw, name)


def parse_nonblank_string(raw: dict[str, JsonValue], name: str) -> str:
    value = _string(raw, name)
    if not value.strip():
        raise ProtocolError(f"{name} must be a nonblank string")
    return value


def parse_nonblank_strings(raw: dict[str, JsonValue], name: str) -> tuple[str, ...]:
    values = _strings(raw, name)
    if any(not value.strip() for value in values):
        raise ProtocolError(f"{name} must contain nonblank strings")
    return values


def parse_strings(raw: dict[str, JsonValue], name: str) -> tuple[str, ...]:
    return _strings(raw, name)


def parse_work_filter(raw: dict[str, JsonValue]) -> str:
    value = _string(raw, "filter")
    if value not in {"all", "pending", "actionable", "blocked", "frozen", "waiting", "active", "next"}:
        raise ProtocolError("invalid work filter")
    return value


def parse_operational_state(raw: dict[str, JsonValue]) -> domain.OperationalState:
    return _enum(domain.OperationalState, _string(raw, "operational_state"), "operational state")


def parse_work_relation_kind(raw: dict[str, JsonValue]) -> domain.WorkRelationKind:
    return _enum(domain.WorkRelationKind, _string(raw, "kind"), "work relation kind")


def parse_irreversible_operation_kind(
    raw: dict[str, JsonValue],
) -> domain.IrreversibleOperationKind:
    return _enum(
        domain.IrreversibleOperationKind,
        _string(raw, "operation_kind"),
        "irreversible operation kind",
    )


def parse_material_decisions(raw: dict[str, JsonValue], name: str) -> tuple[domain.MaterialDecision, ...]:
    decisions = []
    for value in _list(_value(raw, name), name):
        item = _mapping(value, "material decision")
        _keys(item, "material decision", frozenset(("decision", "options")))
        decision = _string(item, "decision")
        options = _strings(item, "options")
        try:
            domain.validate_material_decision(decision, options)
        except domain.StateValidationError as error:
            raise ProtocolError(str(error)) from error
        decisions.append(domain.MaterialDecision(decision, options))
    if len({item.decision for item in decisions}) != len(decisions):
        raise ProtocolError("material decisions must be distinct")
    return tuple(decisions)


def parse_git_history_rewrite_policy(
    raw: dict[str, JsonValue],
) -> domain.GitHistoryRewritePolicy:
    return _enum(
        domain.GitHistoryRewritePolicy,
        _string(raw, "rewrite_policy"),
        "Git history rewrite policy",
    )


def parse_git_commit_rewrites(
    raw: dict[str, JsonValue],
) -> tuple[domain.GitCommitRewrite, ...]:
    return tuple(
        _git_commit_rewrite(_mapping(value, "commit rewrite"))
        for value in _list(_value(raw, "commit_rewrites"), "commit_rewrites")
    )


def _git_commit_rewrite(raw: dict[str, JsonValue]) -> domain.GitCommitRewrite:
    _keys(raw, "commit rewrite", frozenset(("old_sha", "new_sha", "identity_normalized")))
    return domain.GitCommitRewrite(
        _string(raw, "old_sha"),
        _string(raw, "new_sha"),
        _boolean(raw, "identity_normalized"),
    )


def parse_boolean(raw: dict[str, JsonValue], name: str) -> bool:
    return _boolean(raw, name)


def _lock_assessment(raw: dict[str, JsonValue]) -> domain.LockAssessment:
    _keys(raw, "lock assessment", LOCK_ASSESSMENT_KEYS)
    return domain.LockAssessment(
        domain.LockId(_string(raw, "lock_id")),
        _integer(raw, "lock_revision"),
        _strings(raw, "affected_aspects"),
        _strings(raw, "protected_touches"),
        _string(raw, "preserving_path"),
        _boolean(raw, "conflict"),
        _boolean(raw, "evasion"),
        _enum(domain.AssessmentConfidence, _string(raw, "confidence"), "lock assessment confidence"),
        _strings(raw, "evidence"),
        _string(raw, "rationale"),
    )


def _evidence(raw: dict[str, JsonValue], name: str) -> tuple[domain.AssessmentEvidence, ...]:
    return tuple(
        _evidence_item(_mapping(value, "evidence"))
        for value in _list(_value(raw, name), name)
    )


def _evidence_item(raw: dict[str, JsonValue]) -> domain.AssessmentEvidence:
    _keys(raw, "evidence", EVIDENCE_KEYS)
    return domain.AssessmentEvidence(
        _string(raw, "source"),
        _repository_path(raw, "reference"),
        _optional_feature_id(raw, "feature_id"),
        _optional_living_spec_id(raw, "living_spec_id"),
    )


def _repository_path(raw: dict[str, JsonValue], name: str) -> domain.RepositoryPath:
    try:
        return domain.RepositoryPath(_string(raw, name))
    except domain.DomainError as error:
        raise ProtocolError(f"invalid {name}") from error


def _product_truth_content(
    raw: dict[str, JsonValue], product_truth_changed: bool
) -> domain.ProductTruthContent | None:
    value = raw.get("product_truth_content")
    if value is None:
        if product_truth_changed:
            raise ProtocolError("changed Product Truth requires product_truth_content")
        return None
    if type(value) is not str or not value.strip():
        raise ProtocolError("product_truth_content must be a nonblank string")
    return domain.ProductTruthContent(value)


def _value(raw: dict[str, JsonValue], name: str) -> JsonValue:
    try:
        return raw[name]
    except KeyError as error:
        raise ProtocolError(f"missing {name}") from error


def _mapping(value: JsonValue, name: str) -> dict[str, JsonValue]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ProtocolError(f"{name} must be an object")
    return value


def _keys(raw: dict[str, JsonValue], name: str, allowed: frozenset[str]) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ProtocolError(f"unknown {name} key {sorted(unknown)[0]}")


def _clarification_answer(raw: dict[str, JsonValue]) -> tuple[str, str]:
    _keys(raw, "clarification answer", CLARIFICATION_ANSWER_KEYS)
    return (_string(raw, "question_id"), _string(raw, "answer"))


def _list(value: JsonValue, name: str) -> list[JsonValue]:
    if type(value) is not list:
        raise ProtocolError(f"{name} must be an array")
    return value


def _string(raw: dict[str, JsonValue], name: str) -> str:
    value = _value(raw, name)
    if type(value) is not str:
        raise ProtocolError(f"{name} must be a string")
    return value


def _optional_string(raw: dict[str, JsonValue], name: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if type(value) is not str:
        raise ProtocolError(f"{name} must be a string or null")
    return value


def _strings(raw: dict[str, JsonValue], name: str) -> tuple[str, ...]:
    values = _list(_value(raw, name), name)
    if any(type(value) is not str for value in values):
        raise ProtocolError(f"{name} must contain strings")
    return tuple(value for value in values if isinstance(value, str))


def _integer(raw: dict[str, JsonValue], name: str) -> int:
    value = _value(raw, name)
    if type(value) is not int:
        raise ProtocolError(f"{name} must be an integer")
    return value


def _boolean(raw: dict[str, JsonValue], name: str) -> bool:
    value = _value(raw, name)
    if type(value) is not bool:
        raise ProtocolError(f"{name} must be a boolean")
    return value


def _optional_feature_id(
    raw: dict[str, JsonValue], name: str
) -> domain.FeatureId | None:
    value = _optional_string(raw, name)
    return None if value is None else domain.FeatureId(value)


def _optional_living_spec_id(
    raw: dict[str, JsonValue], name: str
) -> domain.LivingSpecId | None:
    value = _optional_string(raw, name)
    return None if value is None else domain.LivingSpecId(value)


def _enum(factory: Callable[[str], T], value: str, name: str) -> T:
    try:
        return factory(value)
    except ValueError as error:
        raise ProtocolError(f"invalid {name}") from error
