"""Immutable domain records with no filesystem or agent dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from collections.abc import Hashable, Iterable
from typing import NewType

from .lifecycle import (
    Classification,
    LifecycleError,
    LifecycleState,
    Phase,
    ReviewVerdict,
    TERMINAL_STATES,
    can_converge,
    validate_context_state,
)

FeatureId = NewType("FeatureId", str)
ChangeId = NewType("ChangeId", str)
DecisionId = NewType("DecisionId", str)
ObservationId = NewType("ObservationId", str)
ConstraintId = NewType("ConstraintId", str)
LivingSpecId = NewType("LivingSpecId", str)
RepositoryPathValue = NewType("RepositoryPathValue", str)
IntakeId = NewType("IntakeId", str)
SharedIntentId = NewType("SharedIntentId", str)
LockId = NewType("LockId", str)
WorkId = NewType("WorkId", str)
CapabilitySelectionId = NewType("CapabilitySelectionId", str)
IrreversibleAuthorizationId = NewType("IrreversibleAuthorizationId", str)
AutonomyExpansionId = NewType("AutonomyExpansionId", str)
ExpectedRevision = NewType("ExpectedRevision", int)


class DomainError(Exception):
    """Raised when a conceptual record violates a domain invariant."""


class ExperimentOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class OperationalState(StrEnum):
    """Queue scheduling state; deliberately independent from lifecycle truth."""

    READY = "READY"
    BLOCKED = "BLOCKED"
    FROZEN = "FROZEN"
    WAITING = "WAITING"
    DONE = "DONE"


class WorkRelationKind(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"
    CONFLICT = "CONFLICT"


class AssessmentOperation(StrEnum):
    """Caller operation recorded with an agent-neutral intake assessment."""

    NEW = "NEW"
    CHANGE = "CHANGE"


class AssessmentConfidence(StrEnum):
    """Caller confidence; Core does not infer this semantic judgment."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CapabilityState(StrEnum):
    """Caller view of the capability lifecycle, checked against Core identity."""

    PLANNED = "PLANNED"
    ESTABLISHED = "ESTABLISHED"
    PROJECT_ESTABLISHED = "PROJECT_ESTABLISHED"
    UNKNOWN = "UNKNOWN"


class AssessmentDepthValue(StrEnum):
    """Ordered deterministic workflow-depth vocabulary."""

    TARGETED = "TARGETED"
    LIGHT = "LIGHT"
    NORMAL = "NORMAL"
    DEEP = "DEEP"
    FULL = "FULL"


@dataclass(frozen=True, slots=True)
class AssessmentDepth:
    review: AssessmentDepthValue
    verification: AssessmentDepthValue
    convergence: AssessmentDepthValue


@dataclass(frozen=True, slots=True)
class AssessmentEvidence:
    source: str
    reference: RepositoryPath
    feature_id: FeatureId | None = None
    living_spec_id: LivingSpecId | None = None


@dataclass(frozen=True, slots=True)
class IntentAssessment:
    """Caller-supplied reasoning provenance accepted only after structural checks."""

    operation: AssessmentOperation
    classification: Classification
    confidence: AssessmentConfidence
    capability_state: CapabilityState
    selected_capability: str | None
    candidate_capabilities: tuple[str, ...]
    selected_feature_id: FeatureId | None
    evidence: tuple[AssessmentEvidence, ...]
    material_ambiguities: tuple[MaterialDecision | str, ...]
    recommended_depth: AssessmentDepth
    rationale: str


@dataclass(frozen=True, slots=True)
class RepositoryPath:
    value: RepositoryPathValue

    def __init__(self, value: str) -> None:
        path = PurePosixPath(value)
        if (
            not value
            or value != path.as_posix()
            or (value != "." and (path.is_absolute() or ":" in value or any(part in ("", ".", "..") for part in path.parts)))
            or "\\" in value
            or value.startswith("//")
        ):
            raise DomainError("repository reference must be a canonical nonescaping POSIX-relative path")
        object.__setattr__(self, "value", RepositoryPathValue(value))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class RepositoryContext:
    root: RepositoryPath


@dataclass(frozen=True, slots=True)
class LivingSpecPublication:
    living_spec_id: LivingSpecId
    revision: str
    capability: str
    path: RepositoryPath
    previous_revision: str | None = None
    feature_id: FeatureId | None = None
    change_id: ChangeId | None = None


@dataclass(frozen=True, slots=True)
class Feature:
    id: FeatureId
    title: str
    living_spec_id: LivingSpecId | None
    side_of: FeatureId | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryEvidence:
    path: RepositoryPath
    kind: str
    strength: str
    fingerprint: str
    source_relationship: str


@dataclass(frozen=True, slots=True)
class RepositoryInitialization:
    repository_id: str
    evidence: tuple[DiscoveryEvidence, ...]
    accepted_constitution: RepositoryPath | None = None


@dataclass(frozen=True, slots=True)
class DeferredSideRequest:
    id: IntakeId
    intent: str
    primary_feature_id: FeatureId
    reason: str
    target_hint: str | None = None


@dataclass(frozen=True, slots=True)
class PendingIntake:
    id: IntakeId
    intent: str
    deferred_from_feature_id: FeatureId
    completed_side_change_id: ChangeId | None
    target_hint: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    feature_id: FeatureId
    title: str


@dataclass(frozen=True, slots=True)
class PendingCapabilitySelection:
    id: CapabilitySelectionId
    operation: AssessmentOperation
    intent: str
    candidates: tuple[CapabilityCandidate, ...]
    standalone_allowed: bool
    provenance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryRecoveryEvidence:
    id: str
    candidate: str
    path: RepositoryPath
    fingerprint: str


@dataclass(frozen=True, slots=True)
class PendingDiscoveryRecovery:
    id: str
    operation: AssessmentOperation
    intent: str
    candidates: tuple[str, ...]
    evidence: tuple[DiscoveryRecoveryEvidence, ...]


@dataclass(frozen=True, slots=True)
class CapabilitySelection:
    id: CapabilitySelectionId
    choice: str
    intent: str
    provenance: tuple[str, ...]
    feature_id: FeatureId
    change_id: ChangeId


@dataclass(frozen=True, slots=True)
class SharedIntent:
    id: SharedIntentId
    intent: str


@dataclass(frozen=True, slots=True)
class DeferredLinkedIntent:
    id: IntakeId
    shared_intent_id: SharedIntentId
    capability_target: str
    intent: str


@dataclass(frozen=True, slots=True)
class Change:
    id: ChangeId
    feature_id: FeatureId
    intent: str
    classification: Classification | None = None
    shared_intent_id: SharedIntentId | None = None
    assessment: IntentAssessment | None = None
    lock_assessments: tuple[LockAssessment, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticLock:
    """Current projection of a Feature-owned protected semantic contract."""

    id: LockId
    feature_id: FeatureId
    current_revision: int
    active: bool = True


@dataclass(frozen=True, slots=True)
class SemanticLockRevision:
    """Immutable revision of one semantic lock and its accepted truth baseline."""

    lock_id: LockId
    revision: int
    baseline: LivingSpecPublication
    protected_aspects: tuple[str, ...]
    rationale: str
    decision_id: DecisionId | None = None


@dataclass(frozen=True, slots=True)
class LockRelease:
    """Immutable retirement record retained after a lock stops being active."""

    lock_id: LockId
    revision: int
    decision_id: DecisionId


@dataclass(frozen=True, slots=True)
class LockAssessment:
    """Agent-supplied change-scoped impact statement for one current lock revision."""

    lock_id: LockId
    lock_revision: int
    affected_aspects: tuple[str, ...]
    protected_touches: tuple[str, ...]
    preserving_path: str
    conflict: bool
    evasion: bool
    confidence: AssessmentConfidence
    evidence: tuple[str, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class LockAuthorization:
    """Scoped permission for one exact protected-touch set in one active Change."""

    lock_id: LockId
    lock_revision: int
    feature_id: FeatureId
    change_id: ChangeId
    decision_id: DecisionId
    protected_aspects: tuple[str, ...]
    assessment_revision: int = 1


@dataclass(frozen=True, slots=True)
class ChangeLockAssessments:
    """Durable assessment projection for one immutable Change record."""

    change_id: ChangeId
    assessments: tuple[LockAssessment, ...]
    history: tuple[tuple[LockAssessment, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class Decision:
    id: DecisionId
    feature_id: FeatureId
    kind: str
    statement: str
    change_id: ChangeId | None = None
    supersedes: DecisionId | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    id: ObservationId
    change_id: ChangeId
    kind: str
    statement: str
    accepted_into_truth: bool = False
    outcome: ExperimentOutcome | None = None


@dataclass(frozen=True, slots=True)
class Drift:
    id: ObservationId
    expected_publication: LivingSpecId
    observed: str
    change_id: ChangeId | None = None
    unresolved: bool = True


@dataclass(frozen=True, slots=True)
class ReviewResult:
    verdict: ReviewVerdict
    findings: tuple[str, ...]
    safe_to_proceed: bool


class ReviewEvidenceCategory(StrEnum):
    """Deterministic source categories consumed by workflow review."""

    WORKFLOW = "WORKFLOW"
    REQUIREMENTS = "REQUIREMENTS"
    CLARIFICATION = "CLARIFICATION"
    CONSTRAINT = "CONSTRAINT"
    PRODUCT_TRUTH = "PRODUCT_TRUTH"
    PROGRESS = "PROGRESS"
    TECHNICAL = "TECHNICAL"


class ReviewEvidenceKind(StrEnum):
    """The compact form of an inspected review input."""

    FACT = "FACT"
    STATUS = "STATUS"
    REFERENCE = "REFERENCE"


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    category: ReviewEvidenceCategory
    kind: ReviewEvidenceKind
    source: str
    required: bool
    value: str
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    severity: ReviewVerdict
    category: ReviewEvidenceCategory
    message: str
    rationale: str
    evidence_reference: str | None = None
    resolved: bool = False


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    evidence: tuple[str, ...]
    sufficient: bool


@dataclass(frozen=True, slots=True)
class Checkpoint:
    name: str
    valid: bool


@dataclass(frozen=True, slots=True)
class SafetyFacts:
    """Caller-recorded safety facts that never trigger workflow actions."""

    unresolved_decision_details: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    review_depth: str | None = None
    verification_depth: str | None = None
    convergence_depth: str | None = None
    material_clarifications: tuple[MaterialClarification, ...] = ()


@dataclass(frozen=True, slots=True)
class MaterialDecision:
    decision: str
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterialClarification:
    id: str
    decision: str
    options: tuple[str, ...]


def validate_material_decision(decision: str, options: tuple[str, ...]) -> None:
    if type(decision) is not str or not decision.strip() or type(options) is not tuple or len(options) < 2:
        raise StateValidationError("material decision requires a concrete decision and at least two options")
    if any(type(option) is not str or not option.strip() for option in options) or len(set(options)) != len(options):
        raise StateValidationError("material decision options must be nonempty and distinct")


class AutonomyAuthorization(StrEnum):
    ALLOWED = "ALLOWED"
    HUMAN_MATERIAL_DECISION = "HUMAN_MATERIAL_DECISION"


class IrreversibleOperationKind(StrEnum):
    """Irreversible operations whose authority must never come from a broad envelope."""

    GIT_HISTORY_REWRITE = "GIT_HISTORY_REWRITE"
    GIT_FORCE_PUSH = "GIT_FORCE_PUSH"
    DESTRUCTIVE_RESET = "DESTRUCTIVE_RESET"


class GitHistoryRewritePolicy(StrEnum):
    """The sole commit-identity rewrite Core can authorize."""

    NORMALIZE_MISMATCHED_IDENTITIES = "NORMALIZE_MISMATCHED_IDENTITIES"


class IrreversibleAuthorizationStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True, slots=True)
class GitCommitRewrite:
    """One exact old-to-new commit mapping in a reviewed rewrite preview."""

    old_sha: str
    new_sha: str
    identity_normalized: bool


@dataclass(frozen=True, slots=True)
class IrreversibleAuthorization:
    """Auditable one-shot authority for one exact irreversible operation."""

    id: IrreversibleAuthorizationId
    operation_kind: IrreversibleOperationKind
    feature_id: FeatureId
    change_id: ChangeId
    target_ref: str
    expected_remote_sha: str
    rewrite_policy: GitHistoryRewritePolicy
    target_identity_name: str
    target_identity_email: str
    expected_result_sha: str
    commit_rewrites: tuple[GitCommitRewrite, ...]
    rewrite_plan_digest: str
    purpose: str
    force_with_lease: bool
    one_shot: bool
    authorized_revision: int
    status: IrreversibleAuthorizationStatus
    validated_revision: int | None = None
    consumed_revision: int | None = None
    resulting_remote_sha: str | None = None
    invalidated_revision: int | None = None
    invalidation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class IrreversibleAuthorizationSummary:
    """Provider-neutral projection of persisted irreversible authority and audit."""

    id: str
    operation_kind: str
    feature_id: str
    change_id: str
    target_ref: str
    expected_remote_sha: str
    rewrite_policy: str
    target_identity_name: str
    target_identity_email: str
    expected_result_sha: str
    commit_rewrites: tuple[GitCommitRewrite, ...]
    rewrite_plan_digest: str
    purpose: str
    force_with_lease: bool
    one_shot: bool
    authorized_revision: int
    status: str
    validated_revision: int | None
    consumed_revision: int | None
    resulting_remote_sha: str | None
    invalidated_revision: int | None
    invalidation_reason: str | None


def git_rewrite_plan_digest(authorization: IrreversibleAuthorization) -> str:
    """Hash the complete immutable operation scope and reviewed commit mapping."""
    payload = {
        "authorization_id": str(authorization.id),
        "operation_kind": authorization.operation_kind.value,
        "feature_id": str(authorization.feature_id),
        "change_id": str(authorization.change_id),
        "target_ref": authorization.target_ref,
        "expected_remote_sha": authorization.expected_remote_sha,
        "rewrite_policy": authorization.rewrite_policy.value,
        "target_identity_name": authorization.target_identity_name,
        "target_identity_email": authorization.target_identity_email,
        "expected_result_sha": authorization.expected_result_sha,
        "commit_rewrites": [
            {
                "old_sha": item.old_sha,
                "new_sha": item.new_sha,
                "identity_normalized": item.identity_normalized,
            }
            for item in authorization.commit_rewrites
        ],
        "purpose": authorization.purpose,
        "force_with_lease": authorization.force_with_lease,
        "one_shot": authorization.one_shot,
        "authorized_revision": authorization.authorized_revision,
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class AutonomyEnvelope:
    """Core-owned, conservative authority for one durable work context."""

    goal: str
    allowed_paths: tuple[RepositoryPath, ...] = ()
    operation_budget: int = 0
    reversible_actions: tuple[str, ...] = ()
    external_wait_allowed: bool = True
    retry_budget: int = 0
    irreversible_permissions: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = (
        "material decision required",
        "scope expansion proposed",
        "irreversible operation proposed",
    )


@dataclass(frozen=True, slots=True)
class AutonomyEnvelopeDelta:
    """Exact optional field changes proposed for one active envelope."""

    allowed_paths: tuple[RepositoryPath, ...] | None = None
    operation_budget: int | None = None
    reversible_actions: tuple[str, ...] | None = None
    external_wait_allowed: bool | None = None
    retry_budget: int | None = None
    irreversible_permissions: tuple[str, ...] | None = None
    stop_conditions: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class PendingAutonomyExpansion:
    """Immutable revision-bound proposal; requesting it never grants authority."""

    proposal_id: AutonomyExpansionId
    repository_id: str
    feature_id: FeatureId
    change_id: ChangeId
    requested_revision: int
    base_envelope: AutonomyEnvelope
    delta: AutonomyEnvelopeDelta
    proposed_envelope: AutonomyEnvelope


class AutonomyExpansionDecision(StrEnum):
    AUTHORIZE = "AUTHORIZE"
    REJECT = "REJECT"
    NARROW = "NARROW"


@dataclass(frozen=True, slots=True)
class AutonomyExpansionRecord:
    """Durable audit of one exact grant, rejection, or safe narrowing."""

    proposal_id: AutonomyExpansionId
    repository_id: str
    feature_id: FeatureId
    change_id: ChangeId
    requested_revision: int
    resolved_revision: int
    decision: AutonomyExpansionDecision
    base_envelope: AutonomyEnvelope
    delta: AutonomyEnvelopeDelta
    proposed_envelope: AutonomyEnvelope
    resolved_envelope: AutonomyEnvelope


def apply_autonomy_envelope_delta(
    envelope: AutonomyEnvelope, delta: AutonomyEnvelopeDelta
) -> AutonomyEnvelope:
    """Return the exact result of applying non-null delta fields to an envelope."""
    return AutonomyEnvelope(
        envelope.goal,
        envelope.allowed_paths if delta.allowed_paths is None else delta.allowed_paths,
        envelope.operation_budget if delta.operation_budget is None else delta.operation_budget,
        envelope.reversible_actions if delta.reversible_actions is None else delta.reversible_actions,
        envelope.external_wait_allowed if delta.external_wait_allowed is None else delta.external_wait_allowed,
        envelope.retry_budget if delta.retry_budget is None else delta.retry_budget,
        envelope.irreversible_permissions if delta.irreversible_permissions is None else delta.irreversible_permissions,
        envelope.stop_conditions if delta.stop_conditions is None else delta.stop_conditions,
    )


def repository_path_is_within(path: RepositoryPath, root: RepositoryPath) -> bool:
    """Check canonical POSIX path-root containment by path segments."""
    path_parts = PurePosixPath(str(path)).parts
    root_parts = PurePosixPath(str(root)).parts
    return len(root_parts) <= len(path_parts) and path_parts[: len(root_parts)] == root_parts


def autonomy_envelope_is_narrowing(
    current: AutonomyEnvelope, proposed: AutonomyEnvelope
) -> bool:
    """Return whether the proposal only removes authority or reduces budgets."""
    return (
        current.goal == proposed.goal
        and all(
            any(repository_path_is_within(path, existing) for existing in current.allowed_paths)
            for path in proposed.allowed_paths
        )
        and (
            current.operation_budget == 0
            or proposed.operation_budget != 0
            and proposed.operation_budget <= current.operation_budget
        )
        and {
            canonical_autonomy_operation(action) for action in proposed.reversible_actions
        }.issubset({canonical_autonomy_operation(action) for action in current.reversible_actions})
        and (current.external_wait_allowed or not proposed.external_wait_allowed)
        and proposed.retry_budget <= current.retry_budget
        and {
            canonical_autonomy_operation(action) for action in proposed.irreversible_permissions
        }.issubset({canonical_autonomy_operation(action) for action in current.irreversible_permissions})
        and set(current.stop_conditions).issubset(proposed.stop_conditions)
    )


def autonomy_operation_is_forbidden(action: str) -> bool:
    """Keep force operations, history rewrites, resets, tags, and releases out of envelopes."""
    tokens = set(re.findall(r"[A-Z0-9]+", action.upper()))
    return bool(
        tokens & {"FORCE", "REWRITE", "REBASE", "AMEND", "RESET", "TAG", "RELEASE"}
        or "DELETE" in tokens and tokens & {"REF", "REFS", "BRANCH"}
        or "FILTER" in tokens and tokens & {"BRANCH", "REPO"}
        or "PUSH" in tokens and bool(tokens & {"DELETE", "MIRROR", "PRUNE"})
        or "PUSH" in tokens and re.search(r"(?:^|\s):[^\s]+", action) is not None
    )


def canonical_autonomy_operation(action: str) -> str:
    """Normalize operation labels for exact envelope membership checks."""
    return re.sub(r"[^A-Za-z0-9]+", "_", action.strip()).strip("_").lower()


@dataclass(frozen=True, slots=True)
class AutonomyMetrics:
    """Monotonic, context-owned counters for autonomy coordination categories."""

    human_material_decisions: int = 0
    operational_handoffs: int = 0
    automatic_operations: int = 0


@dataclass(frozen=True, slots=True)
class AutonomyEvaluation:
    authorization: AutonomyAuthorization
    reason: str


@dataclass(frozen=True, slots=True)
class ProductTruthContent:
    """Caller-authored content for one accepted Product Truth publication."""

    value: str


@dataclass(frozen=True, slots=True)
class ConvergenceAssessment:
    """Agent-supplied decision whether accepted work changes Product Truth."""

    change_id: ChangeId
    product_truth_changed: bool
    confidence: AssessmentConfidence
    evidence: tuple[AssessmentEvidence, ...]
    rationale: str
    product_truth_content: ProductTruthContent | None = None


@dataclass(frozen=True, slots=True)
class WorkingContext:
    feature_id: FeatureId
    change_id: ChangeId | None
    status: LifecycleState
    phase: Phase
    current_action: str | None
    focus: str | None
    completed_work: tuple[str, ...]
    current_work: str | None
    pending_work: tuple[str, ...]
    decision_ids: tuple[DecisionId, ...]
    constraint_ids: tuple[DecisionId, ...]
    review: ReviewResult | None
    checkpoints: tuple[Checkpoint, ...]
    verification: VerificationEvidence
    convergence_complete: bool
    classification: Classification | None
    repair_count: int
    resume_point: str | None
    change_ids: tuple[ChangeId, ...]
    observation_ids: tuple[ObservationId, ...]
    paused_pair: tuple[LifecycleState, Phase] | None = None
    blocker: str | None = None
    safe_next_step: str | None = None
    future_lock_relations: tuple[str, ...] = ()
    unresolved_material_decision: bool = False
    side_of: FeatureId | None = None
    publication: LivingSpecPublication | None = None
    safety_facts: SafetyFacts = SafetyFacts()
    review_evidence: tuple[ReviewEvidence, ...] = ()
    review_findings: tuple[ReviewFinding, ...] = ()
    autonomy_envelope: AutonomyEnvelope = field(
        default=AutonomyEnvelope("legacy work"), compare=False
    )
    autonomy_metrics: AutonomyMetrics = field(default=AutonomyMetrics(), compare=False)


@dataclass(frozen=True, slots=True)
class TerminalArchive:
    """Complete immutable terminal context with explicit primary linkage."""

    context: WorkingContext
    parent_feature_id: FeatureId | None = None
    convergence_assessment: ConvergenceAssessment | None = None
    legacy_unassessed_acceptance: bool = False


@dataclass(frozen=True, slots=True)
class ParkedWork:
    """One inactive, durable ownership snapshot awaiting explicit resumption."""

    context: WorkingContext
    preserved_primary_context: WorkingContext | None
    deferred_side_request: DeferredSideRequest | None
    pending_intake: PendingIntake | None
    reason: str
    parked_at: str


@dataclass(frozen=True, slots=True)
class WorkItem:
    """One durable non-active work record and its lifecycle ownership snapshot."""

    id: WorkId
    operational_state: OperationalState
    context: WorkingContext | None
    preserved_primary_context: WorkingContext | None
    deferred_side_request: DeferredSideRequest | None
    pending_intake: PendingIntake | None
    reason: str | None
    transitioned_at: str
    terminal_change_id: ChangeId | None = None


@dataclass(frozen=True, slots=True)
class WorkRelation:
    """A directed dependency or canonical undirected conflict between work items."""

    predecessor_id: WorkId
    successor_id: WorkId
    kind: WorkRelationKind


@dataclass(frozen=True, slots=True)
class State:
    revision: int
    active_context: WorkingContext | None
    preserved_primary_context: WorkingContext | None = None
    publications: tuple[LivingSpecPublication, ...] = ()
    completed_side_contexts: tuple[TerminalArchive, ...] = ()
    features: tuple[Feature, ...] = ()
    changes: tuple[Change, ...] = ()
    decisions: tuple[Decision, ...] = ()
    observations: tuple[Observation, ...] = ()
    drifts: tuple[Drift, ...] = ()
    terminal_archives: tuple[TerminalArchive, ...] = ()
    publication_history: tuple[LivingSpecPublication, ...] = ()
    initialization: RepositoryInitialization | None = None
    deferred_side_request: DeferredSideRequest | None = None
    pending_intake: PendingIntake | None = None
    shared_intents: tuple[SharedIntent, ...] = ()
    deferred_linked_intents: tuple[DeferredLinkedIntent, ...] = ()
    locks: tuple[SemanticLock, ...] = ()
    lock_revisions: tuple[SemanticLockRevision, ...] = ()
    lock_releases: tuple[LockRelease, ...] = ()
    lock_authorizations: tuple[LockAuthorization, ...] = ()
    change_lock_assessments: tuple[ChangeLockAssessments, ...] = ()
    work_items: tuple[WorkItem, ...] = ()
    work_relations: tuple[WorkRelation, ...] = ()
    parked_work: ParkedWork | None = None
    active_work_id: WorkId | None = None
    preserved_primary_work_id: WorkId | None = None
    pending_capability_selection: PendingCapabilitySelection | None = None
    capability_selection_history: tuple[CapabilitySelection, ...] = ()
    pending_discovery_recovery: PendingDiscoveryRecovery | None = None
    irreversible_authorizations: tuple[IrreversibleAuthorization, ...] = ()
    pending_autonomy_expansion: PendingAutonomyExpansion | None = None
    autonomy_expansion_history: tuple[AutonomyExpansionRecord, ...] = ()


IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_GIT_REF_FORBIDDEN = re.compile(r"[\x00-\x20\x7f ~^:?*\\\[]")
_GIT_EMAIL = re.compile(r"[^\s<>@]+@[^\s<>@]+\Z")


class StateValidationError(Exception):
    """Raised when state relationships are incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class RecordIndex:
    features: dict[FeatureId, Feature]
    changes: dict[ChangeId, Change]
    decisions: dict[DecisionId, Decision]
    observations: dict[ObservationId, Observation]
    locks: dict[LockId, SemanticLock]


def _unique(values: Iterable[Hashable], label: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise StateValidationError(f"{label} identities must be unique")


def _identity(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise StateValidationError(f"{label} must be a string")
    if IDENTITY.fullmatch(value) is None:
        raise StateValidationError(f"{label} is not a valid stable identity")


def _text(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise StateValidationError(f"{label} must be a string")
    if not value:
        raise StateValidationError(f"{label} must not be empty")


def is_git_sha(value: str) -> bool:
    """Return whether value is a complete lowercase SHA-1 or SHA-256 object id."""
    return type(value) is str and _GIT_SHA.fullmatch(value) is not None


def is_sha256_digest(value: str) -> bool:
    """Return whether value is one lowercase SHA-256 digest."""
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def is_git_branch_ref(value: str) -> bool:
    """Accept one canonical, non-wildcard branch ref and reject tags or expressions."""
    prefix = "refs/heads/"
    if type(value) is not str or not value.startswith(prefix):
        return False
    branch = value[len(prefix):]
    if (
        not branch
        or branch.startswith("/")
        or branch.endswith(("/", "."))
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or _GIT_REF_FORBIDDEN.search(branch) is not None
    ):
        return False
    return all(
        part not in (".", "..")
        and not part.startswith(".")
        and not part.endswith(".")
        and not part.casefold().endswith(".lock")
        for part in branch.split("/")
    )


def is_git_identity(name: str, email: str) -> bool:
    """Require one exact printable Git identity with a simple email address."""
    return (
        type(name) is str
        and bool(name.strip())
        and name == name.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in name)
        and type(email) is str
        and _GIT_EMAIL.fullmatch(email) is not None
    )


def _optional_string(value: str | None, label: str) -> None:
    if value is not None and not isinstance(value, str):
        raise StateValidationError(f"{label} must be a string or null")


def _strings(values: tuple[str, ...], label: str) -> None:
    if type(values) is not tuple or any(not isinstance(value, str) for value in values):
        raise StateValidationError(f"{label} must be a tuple of strings")


def _validate_assessment(
    assessment: IntentAssessment,
    features: dict[FeatureId, Feature],
    publication_history: tuple[LivingSpecPublication, ...],
) -> None:
    if (
        type(assessment) is not IntentAssessment
        or type(assessment.operation) is not AssessmentOperation
        or type(assessment.classification) is not Classification
        or type(assessment.confidence) is not AssessmentConfidence
        or type(assessment.capability_state) is not CapabilityState
        or type(assessment.recommended_depth) is not AssessmentDepth
        or type(assessment.evidence) is not tuple
        or type(assessment.candidate_capabilities) is not tuple
        or type(assessment.material_ambiguities) is not tuple
    ):
        raise StateValidationError("intent assessment shape is invalid")
    _optional_string(assessment.selected_capability, "assessment selected capability")
    _optional_string(assessment.selected_feature_id, "assessment selected feature")
    _strings(assessment.candidate_capabilities, "assessment candidate capabilities")
    # Historical assessed Changes retain legacy prose; new intake rejects it before mutation.
    for ambiguity in assessment.material_ambiguities:
        if type(ambiguity) is MaterialDecision:
            validate_material_decision(ambiguity.decision, ambiguity.options)
        elif type(ambiguity) is not str or not ambiguity.strip():
            raise StateValidationError("assessment ambiguity shape is invalid")
    if len(set(str(item) for item in assessment.material_ambiguities)) != len(assessment.material_ambiguities):
        raise StateValidationError("assessment ambiguities must be distinct")
    _text(assessment.rationale, "assessment rationale")
    if any(not item for item in assessment.candidate_capabilities):
        raise StateValidationError("intent assessment facts must not be empty")
    for value in (
        assessment.recommended_depth.review,
        assessment.recommended_depth.verification,
        assessment.recommended_depth.convergence,
    ):
        if type(value) is not AssessmentDepthValue:
            raise StateValidationError("assessment depth is invalid")
    for evidence in assessment.evidence:
        if type(evidence) is not AssessmentEvidence or type(evidence.reference) is not RepositoryPath:
            raise StateValidationError("assessment evidence shape is invalid")
        _text(evidence.source, "assessment evidence source")
        _optional_string(evidence.feature_id, "assessment evidence feature")
        _optional_string(evidence.living_spec_id, "assessment evidence Living Spec")
        try:
            RepositoryPath(str(evidence.reference))
        except DomainError as error:
            raise StateValidationError("assessment evidence reference is invalid") from error
        if evidence.feature_id is not None and evidence.feature_id not in features:
            raise StateValidationError("assessment evidence feature must be Core authority")
        if evidence.living_spec_id is not None and not any(
            publication.living_spec_id == evidence.living_spec_id
            for publication in publication_history
        ):
            raise StateValidationError("assessment evidence Living Spec must be Core truth")
    if not assessment.evidence:
        raise StateValidationError("intent assessment requires attributable evidence")
    match assessment.operation:
        case AssessmentOperation.NEW:
            if assessment.classification in (Classification.PATCH, Classification.TUNE):
                raise StateValidationError("new assessment classification is incompatible")
        case AssessmentOperation.CHANGE:
            if assessment.classification == Classification.FEATURE:
                raise StateValidationError("change assessment classification is incompatible")
        case unreachable:
            raise StateValidationError(f"unknown assessment operation {unreachable}")
    match assessment.capability_state:
        case CapabilityState.PLANNED:
            if assessment.selected_feature_id is not None:
                raise StateValidationError("planned assessment cannot select an established feature")
        case CapabilityState.ESTABLISHED:
            selected = assessment.selected_feature_id
            if selected is None or assessment.selected_capability is None:
                raise StateValidationError("established assessment requires selected Core identity")
            feature = features.get(selected)
            if feature is None or feature.title != assessment.selected_capability:
                raise StateValidationError("established assessment conflicts with Core feature identity")
        case CapabilityState.PROJECT_ESTABLISHED:
            if assessment.selected_capability is None:
                raise StateValidationError("project-established assessment requires a selected capability")
            if assessment.selected_feature_id is not None:
                raise StateValidationError("project-established assessment cannot select a Core feature")
        case CapabilityState.UNKNOWN:
            if assessment.selected_feature_id is not None:
                raise StateValidationError("unknown assessment cannot select a Core feature")
        case unreachable:
            raise StateValidationError(f"unknown capability state {unreachable}")


def validate_assessment_authority(state: State, assessment: IntentAssessment) -> None:
    """Validate caller assessment evidence and Core identity before workflow mutation."""
    _validate_assessment(
        assessment,
        {feature.id: feature for feature in state.features},
        state.publication_history,
    )


def _validate_convergence_assessment(
    assessment: ConvergenceAssessment,
    change_id: ChangeId | None,
    features: dict[FeatureId, Feature],
    publication_history: tuple[LivingSpecPublication, ...],
    require_product_truth_content: bool = False,
) -> None:
    if (
        type(assessment) is not ConvergenceAssessment
        or change_id is None
        or assessment.change_id != change_id
        or type(assessment.product_truth_changed) is not bool
        or type(assessment.confidence) is not AssessmentConfidence
        or type(assessment.evidence) is not tuple
    ):
        raise StateValidationError("convergence assessment does not match active Change")
    _identity(assessment.change_id, "convergence assessment change")
    _text(assessment.rationale, "convergence assessment rationale")
    content = assessment.product_truth_content
    if content is not None:
        if type(content) is not ProductTruthContent:
            raise StateValidationError("Product Truth content shape is invalid")
        _text(content.value, "Product Truth content")
        if not content.value.strip():
            raise StateValidationError("Product Truth content must not be blank")
    if require_product_truth_content and assessment.product_truth_changed and content is None:
        raise StateValidationError("changed Product Truth requires explicit publication content")
    if not assessment.evidence:
        raise StateValidationError("convergence assessment requires attributable evidence")
    for evidence in assessment.evidence:
        if type(evidence) is not AssessmentEvidence or type(evidence.reference) is not RepositoryPath:
            raise StateValidationError("convergence assessment evidence shape is invalid")
        _text(evidence.source, "convergence assessment evidence source")
        _optional_string(evidence.feature_id, "convergence assessment evidence feature")
        _optional_string(evidence.living_spec_id, "convergence assessment evidence Living Spec")
        if evidence.feature_id is not None and evidence.feature_id not in features:
            raise StateValidationError("convergence assessment evidence feature must be Core authority")
        if evidence.living_spec_id is not None and not any(
            publication.living_spec_id == evidence.living_spec_id
            for publication in publication_history
        ):
            raise StateValidationError("convergence assessment evidence Living Spec must be Core truth")


def validate_convergence_assessment_authority(
    state: State, assessment: ConvergenceAssessment
) -> None:
    """Validate an agent decision against the currently active authoritative Change."""
    context = state.active_context
    _validate_convergence_assessment(
        assessment,
        None if context is None else context.change_id,
        {feature.id: feature for feature in state.features},
        state.publication_history,
        True,
    )


def _validate_lock_assessment(
    assessment: LockAssessment,
    revisions: dict[tuple[LockId, int], SemanticLockRevision],
) -> None:
    if (
        type(assessment) is not LockAssessment
        or type(assessment.lock_revision) is not int
        or assessment.lock_revision < 1
        or type(assessment.conflict) is not bool
        or type(assessment.evasion) is not bool
        or type(assessment.confidence) is not AssessmentConfidence
        or type(assessment.affected_aspects) is not tuple
        or type(assessment.protected_touches) is not tuple
        or type(assessment.evidence) is not tuple
    ):
        raise StateValidationError("lock assessment shape is invalid")
    revision = revisions.get((assessment.lock_id, assessment.lock_revision))
    if revision is None:
        raise StateValidationError("lock assessment must reference an immutable lock revision")
    _strings(assessment.affected_aspects, "lock assessment affected aspects")
    _strings(assessment.protected_touches, "lock assessment protected touches")
    _strings(assessment.evidence, "lock assessment evidence")
    _unique(assessment.affected_aspects, "lock assessment affected aspect")
    _unique(assessment.protected_touches, "lock assessment protected touch")
    if any(not value for value in assessment.affected_aspects + assessment.protected_touches + assessment.evidence):
        raise StateValidationError("lock assessment facts must not be empty")
    _text(assessment.preserving_path, "lock assessment preserving path")
    _text(assessment.rationale, "lock assessment rationale")
    if not set(assessment.protected_touches).issubset(revision.protected_aspects):
        raise StateValidationError("lock assessment protected touches must be lock aspects")


def _validate_locks(state: State, features: dict[FeatureId, Feature], changes: dict[ChangeId, Change], decisions: dict[DecisionId, Decision]) -> dict[LockId, SemanticLock]:
    if any(type(item) is not SemanticLock for item in state.locks):
        raise StateValidationError("semantic lock shape is invalid")
    if any(type(item) is not SemanticLockRevision for item in state.lock_revisions):
        raise StateValidationError("semantic lock revision shape is invalid")
    if any(type(item) is not LockRelease for item in state.lock_releases):
        raise StateValidationError("lock release shape is invalid")
    if any(type(item) is not LockAuthorization for item in state.lock_authorizations):
        raise StateValidationError("lock authorization shape is invalid")
    if any(type(item) is not ChangeLockAssessments for item in state.change_lock_assessments):
        raise StateValidationError("change lock assessment record shape is invalid")
    _unique((item.id for item in state.locks), "semantic lock")
    locks = {item.id: item for item in state.locks}
    revisions: dict[tuple[LockId, int], SemanticLockRevision] = {}
    for lock in state.locks:
        _identity(lock.id, "semantic lock")
        _identity(lock.feature_id, "semantic lock feature")
        if lock.feature_id not in features or type(lock.current_revision) is not int or lock.current_revision < 1 or type(lock.active) is not bool:
            raise StateValidationError("semantic lock authority is invalid")
    for revision in state.lock_revisions:
        if type(revision.revision) is not int or revision.revision < 1:
            raise StateValidationError("semantic lock revision number is invalid")
        if revision.lock_id not in locks:
            raise StateValidationError("semantic lock revision must belong to a lock")
        if (revision.lock_id, revision.revision) in revisions:
            raise StateValidationError("semantic lock revision identity must be unique")
        _publication_shape(revision.baseline)
        if revision.baseline.feature_id != locks[revision.lock_id].feature_id or revision.baseline not in state.publication_history:
            raise StateValidationError("semantic lock baseline must be accepted Feature truth")
        _strings(revision.protected_aspects, "semantic lock protected aspects")
        _unique(revision.protected_aspects, "semantic lock protected aspect")
        if not revision.protected_aspects or any(not item for item in revision.protected_aspects):
            raise StateValidationError("semantic lock requires protected aspects")
        _text(revision.rationale, "semantic lock rationale")
        _optional_string(revision.decision_id, "semantic lock revision decision")
        if revision.decision_id is not None:
            decision = decisions.get(revision.decision_id)
            if decision is None or decision.feature_id != locks[revision.lock_id].feature_id:
                raise StateValidationError("semantic lock revision decision must retain Feature authority")
        revisions[(revision.lock_id, revision.revision)] = revision
    for lock in state.locks:
        history = tuple(item.revision for item in state.lock_revisions if item.lock_id == lock.id)
        if history != tuple(range(1, lock.current_revision + 1)):
            raise StateValidationError("semantic lock history must be append-only and contiguous")
        if lock.active:
            current_revision = revisions[(lock.id, lock.current_revision)]
            baseline = next((item for item in state.publications if item.feature_id == lock.feature_id), None)
            if baseline is None or current_revision.baseline != baseline:
                raise StateValidationError("active semantic lock must track current accepted Feature truth")
    _unique((item.lock_id for item in state.lock_releases), "lock release")
    releases = {item.lock_id: item for item in state.lock_releases}
    for release in state.lock_releases:
        if type(release.revision) is not int or release.revision < 1:
            raise StateValidationError("lock release revision must be a positive integer")
        lock = locks.get(release.lock_id)
        decision = decisions.get(release.decision_id)
        if lock is None or release.revision != lock.current_revision or decision is None or decision.feature_id != lock.feature_id:
            raise StateValidationError("lock release must retain scoped current authority")
    if any(lock.active == (lock.id in releases) for lock in state.locks):
        raise StateValidationError("semantic lock active projection conflicts with release history")
    for authorization in state.lock_authorizations:
        revision = revisions.get((authorization.lock_id, authorization.lock_revision))
        change = changes.get(authorization.change_id)
        decision = decisions.get(authorization.decision_id)
        if (
            revision is None
            or authorization.lock_id not in locks
            or type(authorization.lock_revision) is not int
            or authorization.lock_revision < 1
            or type(authorization.assessment_revision) is not int
            or authorization.assessment_revision < 1
            or authorization.feature_id != locks[authorization.lock_id].feature_id
            or change is None
            or change.feature_id != authorization.feature_id
            or decision is None
            or (decision.feature_id, decision.change_id) != (authorization.feature_id, authorization.change_id)
        ):
            raise StateValidationError("lock authorization scope is invalid")
        _strings(authorization.protected_aspects, "lock authorization aspects")
        _unique(authorization.protected_aspects, "lock authorization aspect")
        if not authorization.protected_aspects or set(authorization.protected_aspects) - set(revision.protected_aspects):
            raise StateValidationError("lock authorization aspects are invalid")
        record = next((item for item in state.change_lock_assessments if item.change_id == authorization.change_id), None)
        if record is None or authorization.assessment_revision > len(record.history):
            raise StateValidationError("lock authorization must retain an assessment history revision")
        assessments = tuple(item for item in record.history[authorization.assessment_revision - 1] if item.lock_id == authorization.lock_id and item.lock_revision == authorization.lock_revision)
        if len(assessments) != 1 or assessments[0].protected_touches != authorization.protected_aspects:
            raise StateValidationError("lock authorization must exactly match its assessment")
    _unique((item.change_id for item in state.change_lock_assessments), "change lock assessment record")
    if any(item.change_id not in changes for item in state.change_lock_assessments):
        raise StateValidationError("change lock assessment history must belong to an existing Change")
    assessment_records = {item.change_id: item.assessments for item in state.change_lock_assessments}
    for change in state.changes:
        if type(change.lock_assessments) is not tuple:
            raise StateValidationError("change lock assessments must be a tuple")
        if change.lock_assessments != assessment_records.get(change.id, ()):
            raise StateValidationError("change lock assessments must match durable change record")
        record = next((item for item in state.change_lock_assessments if item.change_id == change.id), None)
        if record is not None:
            if type(record.history) is not tuple or not record.history or record.history[-1] != record.assessments:
                raise StateValidationError("change lock assessment history must retain the current projection")
            for historical in record.history:
                if type(historical) is not tuple:
                    raise StateValidationError("change lock assessment history must be immutable tuples")
                _unique((item.lock_id for item in historical), "historical change lock assessment")
                for assessment in historical:
                    _validate_lock_assessment(assessment, revisions)
        _unique((item.lock_id for item in change.lock_assessments), "change lock assessment")
        for assessment in change.lock_assessments:
            _validate_lock_assessment(assessment, revisions)
    return locks


def _validate_review_records(context: WorkingContext) -> None:
    if type(context.review_evidence) is not tuple or type(context.review_findings) is not tuple:
        raise StateValidationError("review records must be tuples")
    for evidence in context.review_evidence:
        if (
            type(evidence) is not ReviewEvidence
            or type(evidence.category) is not ReviewEvidenceCategory
            or type(evidence.kind) is not ReviewEvidenceKind
            or type(evidence.required) is not bool
        ):
            raise StateValidationError("review evidence shape is invalid")
        _text(evidence.source, "review evidence source")
        _text(evidence.value, "review evidence value")
        _optional_string(evidence.reference, "review evidence reference")
    for finding in context.review_findings:
        if (
            type(finding) is not ReviewFinding
            or type(finding.severity) is not ReviewVerdict
            or type(finding.category) is not ReviewEvidenceCategory
            or type(finding.resolved) is not bool
        ):
            raise StateValidationError("review finding shape is invalid")
        _text(finding.message, "review finding message")
        _text(finding.rationale, "review finding rationale")
        _optional_string(finding.evidence_reference, "review finding evidence reference")


def _validate_autonomy_envelope(envelope: AutonomyEnvelope) -> None:
    if type(envelope) is not AutonomyEnvelope:
        raise StateValidationError("autonomy envelope shape is invalid")
    _text(envelope.goal, "autonomy goal")
    if type(envelope.allowed_paths) is not tuple or any(type(path) is not RepositoryPath for path in envelope.allowed_paths):
        raise StateValidationError("autonomy allowed paths are invalid")
    _unique(envelope.allowed_paths, "autonomy allowed path")
    for path in envelope.allowed_paths:
        try:
            if RepositoryPath(str(path)) != path:
                raise DomainError("noncanonical path")
        except DomainError as error:
            raise StateValidationError("autonomy allowed path is invalid") from error
    for values, label in (
        (envelope.reversible_actions, "autonomy reversible actions"),
        (envelope.irreversible_permissions, "autonomy irreversible permissions"),
        (envelope.stop_conditions, "autonomy stop conditions"),
    ):
        _strings(values, label)
        _unique(values, label)
        if any(not value.strip() or value != value.strip() for value in values):
            raise StateValidationError(f"{label} must contain canonical nonblank strings")
    for actions in (envelope.reversible_actions, envelope.irreversible_permissions):
        normalized_actions = tuple(canonical_autonomy_operation(action) for action in actions)
        if any(not action for action in normalized_actions) or len(normalized_actions) != len(set(normalized_actions)):
            raise StateValidationError("autonomy operation names must be canonical and unique")
    if (
        type(envelope.operation_budget) is not int
        or envelope.operation_budget < 0
        or type(envelope.retry_budget) is not int
        or envelope.retry_budget < 0
        or type(envelope.external_wait_allowed) is not bool
    ):
        raise StateValidationError("autonomy envelope budget is invalid")


def _validate_autonomy_delta(delta: AutonomyEnvelopeDelta) -> None:
    if type(delta) is not AutonomyEnvelopeDelta or all(
        value is None
        for value in (
            delta.allowed_paths,
            delta.operation_budget,
            delta.reversible_actions,
            delta.external_wait_allowed,
            delta.retry_budget,
            delta.irreversible_permissions,
            delta.stop_conditions,
        )
    ):
        raise StateValidationError("autonomy envelope delta is empty or invalid")
    if delta.allowed_paths is not None:
        if type(delta.allowed_paths) is not tuple or any(type(path) is not RepositoryPath for path in delta.allowed_paths):
            raise StateValidationError("autonomy delta paths are invalid")
        _unique(delta.allowed_paths, "autonomy delta path")
        for path in delta.allowed_paths:
            try:
                if RepositoryPath(str(path)) != path:
                    raise DomainError("noncanonical path")
            except DomainError as error:
                raise StateValidationError("autonomy delta path is invalid") from error
    for value, label in (
        (delta.reversible_actions, "autonomy delta reversible actions"),
        (delta.irreversible_permissions, "autonomy delta irreversible permissions"),
        (delta.stop_conditions, "autonomy delta stop conditions"),
    ):
        if value is not None:
            _strings(value, label)
            _unique(value, label)
            if any(not item.strip() or item != item.strip() for item in value):
                raise StateValidationError(f"{label} must contain canonical nonblank strings")
    for actions in (delta.reversible_actions or (), delta.irreversible_permissions or ()):
        normalized_actions = tuple(canonical_autonomy_operation(action) for action in actions)
        if any(not action for action in normalized_actions) or len(normalized_actions) != len(set(normalized_actions)):
            raise StateValidationError("autonomy delta operation names must be canonical and unique")
    if delta.operation_budget is not None and (type(delta.operation_budget) is not int or delta.operation_budget < 0):
        raise StateValidationError("autonomy delta operation budget is invalid")
    if delta.retry_budget is not None and (type(delta.retry_budget) is not int or delta.retry_budget < 0):
        raise StateValidationError("autonomy delta retry budget is invalid")
    if delta.external_wait_allowed is not None and type(delta.external_wait_allowed) is not bool:
        raise StateValidationError("autonomy delta external wait flag is invalid")


def validate_autonomy_envelope(envelope: AutonomyEnvelope) -> None:
    """Validate one standalone envelope supplied to a Core autonomy operation."""
    _validate_autonomy_envelope(envelope)


def validate_autonomy_envelope_delta(delta: AutonomyEnvelopeDelta) -> None:
    """Validate one standalone exact delta supplied to a Core autonomy operation."""
    _validate_autonomy_delta(delta)


def _context_shape(context: WorkingContext) -> None:
    if type(context) is not WorkingContext:
        raise StateValidationError("context shape is invalid")
    if (
        type(context.status) is not LifecycleState
        or type(context.phase) is not Phase
        or (context.classification is not None and type(context.classification) is not Classification)
        or type(context.repair_count) is not int
    ):
        raise StateValidationError("context lifecycle values are invalid")
    for value, label in (
        (context.feature_id, "context feature"),
        (context.change_id, "context change"),
        (context.current_action, "current action"),
        (context.focus, "context focus"),
        (context.current_work, "current work"),
        (context.resume_point, "resume point"),
        (context.blocker, "context blocker"),
        (context.safe_next_step, "safe next step"),
        (context.side_of, "side feature"),
    ):
        _optional_string(value, label)
    for values, label in (
        (context.completed_work, "completed work"),
        (context.pending_work, "pending work"),
        (context.decision_ids, "context decision ids"),
        (context.constraint_ids, "context constraint ids"),
        (context.change_ids, "context change ids"),
        (context.observation_ids, "context observation ids"),
        (context.future_lock_relations, "future lock relations"),
    ):
        _strings(values, label)
    if type(context.verification) is not VerificationEvidence:
        raise StateValidationError("verification shape is invalid")
    _strings(context.verification.evidence, "verification evidence")
    if type(context.review) is not ReviewResult and context.review is not None:
        raise StateValidationError("review shape is invalid")
    if context.review is not None:
        if type(context.review.verdict) is not ReviewVerdict:
            raise StateValidationError("review verdict is invalid")
        _strings(context.review.findings, "review findings")
    _validate_review_records(context)
    if type(context.checkpoints) is not tuple:
        raise StateValidationError("checkpoints must be a tuple")
    for checkpoint in context.checkpoints:
        if type(checkpoint) is not Checkpoint or not isinstance(checkpoint.name, str):
            raise StateValidationError("checkpoint shape is invalid")
    if context.paused_pair is not None and (
        type(context.paused_pair) is not tuple
        or len(context.paused_pair) != 2
        or type(context.paused_pair[0]) is not LifecycleState
        or type(context.paused_pair[1]) is not Phase
    ):
        raise StateValidationError("paused pair shape is invalid")
    if type(context.safety_facts) is not SafetyFacts:
        raise StateValidationError("safety facts shape is invalid")
    facts = context.safety_facts
    _strings(facts.unresolved_decision_details, "unresolved decision details")
    if type(facts.material_clarifications) is not tuple:
        raise StateValidationError("material clarifications shape is invalid")
    for clarification in facts.material_clarifications:
        if type(clarification) is not MaterialClarification or type(clarification.id) is not str or not clarification.id.strip():
            raise StateValidationError("material clarification shape is invalid")
        validate_material_decision(clarification.decision, clarification.options)
    _unique(tuple(item.id for item in facts.material_clarifications), "material clarification id")
    _strings(facts.scope, "safety scope")
    _strings(facts.risks, "safety risks")
    for value, label in (
        (facts.review_depth, "review depth"),
        (facts.verification_depth, "verification depth"),
        (facts.convergence_depth, "convergence depth"),
    ):
        _optional_string(value, label)
    _validate_autonomy_envelope(context.autonomy_envelope)
    if type(context.autonomy_metrics) is not AutonomyMetrics:
        raise StateValidationError("autonomy metrics shape is invalid")
    if any(type(value) is not int or value < 0 for value in (
        context.autonomy_metrics.human_material_decisions,
        context.autonomy_metrics.operational_handoffs,
        context.autonomy_metrics.automatic_operations,
    )):
        raise StateValidationError("autonomy metrics are invalid")
    if context.publication is not None:
        _publication_shape(context.publication)


def _publication_shape(publication: LivingSpecPublication) -> None:
    if type(publication) is not LivingSpecPublication:
        raise StateValidationError("publication shape is invalid")
    for value, label in (
        (publication.living_spec_id, "publication living spec"),
        (publication.revision, "publication revision"),
        (publication.capability, "publication capability"),
        (publication.feature_id, "publication feature"),
        (publication.change_id, "publication change"),
    ):
        if not isinstance(value, str):
            raise StateValidationError(f"{label} must be a string")
    _optional_string(publication.previous_revision, "publication previous revision")
    if type(publication.path) is not RepositoryPath or not isinstance(publication.path.value, str):
        raise StateValidationError("publication path shape is invalid")
    try:
        RepositoryPath(publication.path.value)
    except DomainError as error:
        raise StateValidationError("publication path is invalid") from error


def _validate_records(state: State) -> RecordIndex:
    if any(type(item) is not Feature for item in state.features):
        raise StateValidationError("feature record shape is invalid")
    if any(type(item) is not Change for item in state.changes):
        raise StateValidationError("change record shape is invalid")
    if any(type(item) is not Decision for item in state.decisions):
        raise StateValidationError("decision record shape is invalid")
    if any(type(item) is not Observation for item in state.observations):
        raise StateValidationError("observation record shape is invalid")
    for feature in state.features:
        _identity(feature.id, "feature")
        _text(feature.title, "feature title")
        _optional_string(feature.living_spec_id, "living spec")
        _optional_string(feature.side_of, "side feature")
    for change in state.changes:
        _identity(change.id, "change")
        _identity(change.feature_id, "change feature")
        _text(change.intent, "change intent")
        if change.classification is not None and type(change.classification) is not Classification:
            raise StateValidationError("change classification is invalid")
        if change.shared_intent_id is not None and change.shared_intent_id not in {
            item.id for item in state.shared_intents
        }:
            raise StateValidationError("change shared intent must be authoritative")
    for decision in state.decisions:
        _identity(decision.id, "decision")
        _identity(decision.feature_id, "decision feature")
        _text(decision.statement, "decision statement")
        _optional_string(decision.change_id, "decision change")
        _optional_string(decision.supersedes, "decision supersedes")
        if not isinstance(decision.kind, str):
            raise StateValidationError("decision kind must be a string")
    for observation in state.observations:
        _identity(observation.id, "observation")
        _identity(observation.change_id, "observation change")
        _text(observation.kind, "observation kind")
        _text(observation.statement, "observation statement")
        if type(observation.accepted_into_truth) is not bool:
            raise StateValidationError("observation truth flag must be boolean")
        if observation.outcome is not None and type(observation.outcome) is not ExperimentOutcome:
            raise StateValidationError("experiment outcome is invalid")
    features = {item.id: item for item in state.features}
    _unique((item.id for item in state.features), "feature")
    _unique(
        (item.living_spec_id for item in state.features if item.living_spec_id is not None),
        "living spec",
    )
    for feature in state.features:
        if feature.living_spec_id is not None:
            _identity(feature.living_spec_id, "living spec")
        if feature.side_of is not None:
            parent = features.get(feature.side_of)
            if parent is None or parent.side_of is not None or feature.side_of == feature.id:
                raise StateValidationError("side feature must link a distinct known primary feature")
    _unique((item.id for item in state.changes), "change")
    changes = {item.id: item for item in state.changes}
    for change in state.changes:
        if change.feature_id not in features:
            raise StateValidationError("change must reference a known feature")
        if change.assessment is not None:
            _validate_assessment(change.assessment, features, state.publication_history)
            if change.classification != change.assessment.classification:
                raise StateValidationError("change classification must preserve assessment provenance")
            if (
                change.assessment.capability_state == CapabilityState.ESTABLISHED
                and change.feature_id != change.assessment.selected_feature_id
            ):
                raise StateValidationError("established assessment must bind its selected Core feature")
    _unique((item.id for item in state.decisions), "decision")
    decisions = {item.id: item for item in state.decisions}
    positions = {item.id: index for index, item in enumerate(state.decisions)}
    superseded: set[DecisionId] = set()
    for decision in state.decisions:
        if decision.kind not in ("clarification", "constraint"):
            raise StateValidationError("decision kind is invalid")
        if decision.feature_id not in features:
            raise StateValidationError("decision must reference a known feature")
        if decision.change_id is not None:
            change = changes.get(decision.change_id)
            if change is None or change.feature_id != decision.feature_id:
                raise StateValidationError("decision change must belong to its feature")
        if decision.supersedes is not None:
            prior = decisions.get(decision.supersedes)
            if prior is None or prior.id == decision.id or positions[prior.id] >= positions[decision.id]:
                raise StateValidationError("decision supersession must reference a prior decision")
            if (prior.feature_id, prior.kind, prior.change_id) != (
                decision.feature_id,
                decision.kind,
                decision.change_id,
            ):
                raise StateValidationError("decision supersession scope must be unchanged")
            if prior.id in superseded:
                raise StateValidationError("a decision cannot have multiple replacements")
            superseded.add(prior.id)
    _unique((item.id for item in state.observations), "observation")
    observations = {item.id: item for item in state.observations}
    for observation in state.observations:
        if observation.change_id not in changes:
            raise StateValidationError("observation must reference a known change")
        if observation.kind == "experiment":
            if observation.outcome is None:
                raise StateValidationError("experiment outcome must be explicit")
        elif observation.outcome is not None:
            raise StateValidationError("only experiments have experiment outcomes")
        if observation.accepted_into_truth and observation.outcome != ExperimentOutcome.ACCEPTED:
            raise StateValidationError("only an accepted experiment may be marked for truth")
    locks = _validate_locks(state, features, changes, decisions)
    return RecordIndex(features, changes, decisions, observations, locks)


def _validate_context(
    context: WorkingContext,
    records: RecordIndex,
    publication_history: tuple[LivingSpecPublication, ...],
    require_current_decisions: bool = True,
) -> None:
    _context_shape(context)
    if (
        type(context.unresolved_material_decision) is not bool
        or type(context.convergence_complete) is not bool
        or type(context.verification.sufficient) is not bool
        or any(type(item.valid) is not bool for item in context.checkpoints)
        or (context.review is not None and type(context.review.safe_to_proceed) is not bool)
    ):
        raise StateValidationError("context flags must be boolean")
    facts = context.safety_facts
    if (
        any(
            type(item) is not str
            for item in facts.unresolved_decision_details + facts.scope + facts.risks
        )
        or (
            context.review is not None
            and type(context.review.verdict) is not ReviewVerdict
        )
        or (
            context.review is not None
            and any(type(item) is not str for item in context.review.findings)
        )
        or any(
            type(item) is not str
            for item in (facts.review_depth, facts.verification_depth, facts.convergence_depth)
            if item is not None
        )
    ):
        raise StateValidationError("nested context facts must contain strings")
    if (
        any(not item for item in facts.unresolved_decision_details + facts.scope + facts.risks)
        or any(
            item == ""
            for item in (facts.review_depth, facts.verification_depth, facts.convergence_depth)
            if item is not None
        )
    ):
        raise StateValidationError("safety facts must contain nonempty caller facts")
    if facts.unresolved_decision_details and not context.unresolved_material_decision:
        raise StateValidationError("unresolved decision details require a blocking decision flag")
    if facts.material_clarifications and not context.unresolved_material_decision:
        raise StateValidationError("material clarifications require a blocking decision flag")
    try:
        validate_context_state(
            context.status,
            context.phase,
            context.unresolved_material_decision,
            context.review,
            context.verification,
            context.repair_count,
            context.paused_pair,
            context.blocker,
            context.safe_next_step,
        )
    except LifecycleError as error:
        raise StateValidationError("context lifecycle is invalid") from error
    feature = records.features.get(context.feature_id)
    if feature is None or feature.side_of != context.side_of:
        raise StateValidationError("context feature or side linkage is invalid")
    publication = context.publication
    if publication is not None and (
        context.change_id is None
        or publication.feature_id != context.feature_id
        or publication.change_id != context.change_id
        or feature.living_spec_id != publication.living_spec_id
        or publication.capability != feature.title
        or str(publication.path)
        != f"living-specs/{publication.living_spec_id}/{publication.revision}.md"
        or sum(item == publication for item in publication_history) != 1
    ):
        raise StateValidationError("context publication reference is invalid")
    if context.change_id is not None:
        change = records.changes.get(context.change_id)
        if change is None or change.feature_id != context.feature_id:
            raise StateValidationError("current change must belong to the context feature")
        if context.classification != change.classification:
            raise StateValidationError("context classification must match its current change")
        if context.change_id not in context.change_ids:
            raise StateValidationError("current change must occur in context change history")
    for change_id in context.change_ids:
        change = records.changes.get(change_id)
        if change is None or change.feature_id != context.feature_id:
            raise StateValidationError("context change history is invalid")
    superseded = {
        item.supersedes for item in records.decisions.values() if item.supersedes is not None
    }
    _unique(context.decision_ids, "context clarification")
    _unique(context.constraint_ids, "context constraint")
    _unique(context.change_ids, "context change")
    _unique(context.observation_ids, "context observation")
    for decision_id in context.decision_ids + context.constraint_ids:
        decision = records.decisions.get(decision_id)
        if decision is None or decision.feature_id != context.feature_id:
            raise StateValidationError("context decision reference is invalid")
        if require_current_decisions and decision_id in superseded:
            raise StateValidationError("active context cannot reference a superseded decision")
        if decision.change_id is not None and decision.change_id not in context.change_ids:
            raise StateValidationError("context decision change scope is invalid")
    if any(records.decisions[item].kind != "clarification" for item in context.decision_ids):
        raise StateValidationError("clarification references must identify clarifications")
    if any(records.decisions[item].kind != "constraint" for item in context.constraint_ids):
        raise StateValidationError("constraint references must identify constraints")
    for observation_id in context.observation_ids:
        observation = records.observations.get(observation_id)
        if observation is None or observation.change_id not in context.change_ids:
            raise StateValidationError("context observation reference is invalid")


def _validate_archive(
    archive: TerminalArchive,
    records: RecordIndex,
    publication_history: tuple[LivingSpecPublication, ...],
) -> None:
    if type(archive) is not TerminalArchive:
        raise StateValidationError("archive shape is invalid")
    _optional_string(archive.parent_feature_id, "archive parent feature")
    _validate_context(archive.context, records, publication_history, False)
    if archive.context.status not in TERMINAL_STATES:
        raise StateValidationError("archive context must be terminal")
    if archive.parent_feature_id != archive.context.side_of:
        raise StateValidationError("archive parent linkage must match its context")
    assessment = archive.convergence_assessment
    if type(archive.legacy_unassessed_acceptance) is not bool:
        raise StateValidationError("archive legacy acceptance provenance is invalid")
    if archive.legacy_unassessed_acceptance and (
        archive.context.status != LifecycleState.ACCEPTED
        or archive.context.publication is not None
        or assessment is not None
    ):
        raise StateValidationError("legacy acceptance provenance is invalid")
    if assessment is not None:
        if archive.context.status != LifecycleState.ACCEPTED:
            raise StateValidationError("only accepted archives may retain convergence assessment")
        _validate_convergence_assessment(
            assessment,
            archive.context.change_id,
            records.features,
            publication_history,
        )
    if archive.context.status == LifecycleState.ACCEPTED and (
        archive.context.phase != Phase.DONE
        or not archive.context.convergence_complete
        or not can_converge(archive.context.review, archive.context.verification)
    ):
        raise StateValidationError("accepted archive requires completed guarded convergence")
    if (
        archive.context.status == LifecycleState.ACCEPTED
        and archive.context.publication is None
        and not archive.legacy_unassessed_acceptance
        and (assessment is None or assessment.product_truth_changed)
    ):
        raise StateValidationError("accepted archive without publication requires false convergence assessment")
    if archive.context.status == LifecycleState.ACCEPTED and archive.context.publication is not None and (
        assessment is not None and not assessment.product_truth_changed
    ):
        raise StateValidationError("published accepted archive requires true convergence assessment")


def _validate_publications(state: State, records: RecordIndex) -> None:
    for publication in state.publications + state.publication_history:
        _publication_shape(publication)
    current_ids = tuple(item.living_spec_id for item in state.publications)
    _unique(current_ids, "current Living Spec")
    history_keys = tuple(
        f"{item.living_spec_id}:{item.revision}" for item in state.publication_history
    )
    _unique(history_keys, "publication history")
    accepted_archives: dict[tuple[FeatureId, ChangeId], TerminalArchive] = {}
    for archive in state.terminal_archives:
        change_id = archive.context.change_id
        if archive.context.status == LifecycleState.ACCEPTED and change_id is not None:
            accepted_archives[(archive.context.feature_id, change_id)] = archive
    previous_by_spec: dict[LivingSpecId, str] = {}
    for publication in state.publication_history:
        feature_id = publication.feature_id
        change_id = publication.change_id
        feature = records.features.get(feature_id) if feature_id is not None else None
        if (
            IDENTITY.fullmatch(publication.living_spec_id) is None
            or IDENTITY.fullmatch(publication.revision) is None
            or feature_id is None
            or change_id is None
            or feature is None
            or feature.living_spec_id != publication.living_spec_id
            or publication.capability != feature.title
            or (feature_id, change_id) not in accepted_archives
        ):
            raise StateValidationError("publication authority linkage is invalid")
        if publication.previous_revision != previous_by_spec.get(publication.living_spec_id):
            raise StateValidationError("publication predecessor is not authoritative history")
        if accepted_archives[(feature_id, change_id)].context.publication != publication:
            raise StateValidationError("accepted context publication identity is invalid")
        if str(publication.path) != (
            f"living-specs/{publication.living_spec_id}/{publication.revision}.md"
        ):
            raise StateValidationError("publication path does not match its identity")
        previous_by_spec[publication.living_spec_id] = publication.revision
    latest = {
        item.living_spec_id: item
        for item in state.publication_history
        if previous_by_spec.get(item.living_spec_id) == item.revision
    }
    if set(state.publications) != set(latest.values()):
        raise StateValidationError("current publications must select latest authoritative history")


def _validate_irreversible_authorizations(state: State) -> None:
    authorizations = state.irreversible_authorizations
    if type(authorizations) is not tuple or any(
        type(item) is not IrreversibleAuthorization for item in authorizations
    ):
        raise StateValidationError("irreversible authorization history is invalid")
    _unique((item.id for item in authorizations), "irreversible authorization")
    features = {item.id for item in state.features}
    changes = {item.id: item for item in state.changes}
    active = state.active_context
    pending = 0
    for item in authorizations:
        _identity(item.id, "irreversible authorization")
        if (
            type(item.operation_kind) is not IrreversibleOperationKind
            or item.operation_kind is not IrreversibleOperationKind.GIT_HISTORY_REWRITE
            or type(item.status) is not IrreversibleAuthorizationStatus
        ):
            raise StateValidationError("irreversible authorization operation is invalid")
        _identity(item.feature_id, "irreversible authorization feature")
        _identity(item.change_id, "irreversible authorization change")
        change = changes.get(item.change_id)
        if item.feature_id not in features or change is None or change.feature_id != item.feature_id:
            raise StateValidationError("irreversible authorization must reference Core Feature and Change")
        if not is_git_branch_ref(item.target_ref):
            raise StateValidationError("irreversible authorization target must be one canonical branch ref")
        if not is_git_sha(item.expected_remote_sha):
            raise StateValidationError("irreversible authorization expected remote SHA is invalid")
        if (
            type(item.rewrite_policy) is not GitHistoryRewritePolicy
            or item.rewrite_policy is not GitHistoryRewritePolicy.NORMALIZE_MISMATCHED_IDENTITIES
            or not is_git_identity(item.target_identity_name, item.target_identity_email)
        ):
            raise StateValidationError("irreversible authorization identity rewrite specification is invalid")
        rewrites = item.commit_rewrites
        if (
            type(rewrites) is not tuple
            or not rewrites
            or any(type(rewrite) is not GitCommitRewrite for rewrite in rewrites)
        ):
            raise StateValidationError("irreversible authorization rewrite plan is invalid")
        old_shas: set[str] = set()
        new_shas: set[str] = set()
        for rewrite in rewrites:
            if (
                not is_git_sha(rewrite.old_sha)
                or not is_git_sha(rewrite.new_sha)
                or len(rewrite.old_sha) != len(item.expected_remote_sha)
                or len(rewrite.new_sha) != len(item.expected_remote_sha)
                or rewrite.old_sha == rewrite.new_sha
                or type(rewrite.identity_normalized) is not bool
                or rewrite.old_sha in old_shas
                or rewrite.new_sha in new_shas
            ):
                raise StateValidationError("irreversible authorization commit mapping is invalid")
            old_shas.add(rewrite.old_sha)
            new_shas.add(rewrite.new_sha)
        if (
            not is_git_sha(item.expected_result_sha)
            or len(item.expected_result_sha) != len(item.expected_remote_sha)
            or item.expected_result_sha == item.expected_remote_sha
            or not any(
                rewrite.old_sha == item.expected_remote_sha
                and rewrite.new_sha == item.expected_result_sha
                for rewrite in rewrites
            )
            or not any(rewrite.identity_normalized for rewrite in rewrites)
            or not is_sha256_digest(item.rewrite_plan_digest)
            or git_rewrite_plan_digest(item) != item.rewrite_plan_digest
        ):
            raise StateValidationError("irreversible authorization exact result plan is invalid")
        _text(item.purpose, "irreversible authorization purpose")
        if type(item.force_with_lease) is not bool or not item.force_with_lease:
            raise StateValidationError("history rewrite authorization requires force-with-lease")
        if type(item.one_shot) is not bool or not item.one_shot:
            raise StateValidationError("irreversible authorization must be one-shot")
        if (
            type(item.authorized_revision) is not int
            or not 1 <= item.authorized_revision <= state.revision
        ):
            raise StateValidationError("irreversible authorization revision is invalid")
        for revision, label in (
            (item.validated_revision, "validated"),
            (item.consumed_revision, "consumed"),
            (item.invalidated_revision, "invalidated"),
        ):
            if revision is not None and (type(revision) is not int or not 1 <= revision <= state.revision):
                raise StateValidationError(f"irreversible authorization {label} revision is invalid")
        if item.resulting_remote_sha is not None and not is_git_sha(item.resulting_remote_sha):
            raise StateValidationError("irreversible authorization result SHA is invalid")
        _optional_string(item.invalidation_reason, "irreversible authorization invalidation reason")
        if item.status in (
            IrreversibleAuthorizationStatus.PENDING,
            IrreversibleAuthorizationStatus.READY,
        ):
            pending += 1
            if active is None or active.feature_id != item.feature_id or active.change_id != item.change_id:
                raise StateValidationError("pending irreversible authorization requires its active Change")
            if item.status is IrreversibleAuthorizationStatus.PENDING:
                if (
                    state.revision != item.authorized_revision
                    or item.validated_revision is not None
                    or item.consumed_revision is not None
                    or item.resulting_remote_sha is not None
                    or item.invalidated_revision is not None
                    or item.invalidation_reason is not None
                ):
                    raise StateValidationError("pending irreversible authorization is stale or malformed")
            elif (
                item.validated_revision != state.revision
                or item.validated_revision is None
                or item.consumed_revision is not None
                or item.resulting_remote_sha is not None
                or item.invalidated_revision is not None
                or item.invalidation_reason is not None
            ):
                raise StateValidationError("ready irreversible authorization is stale or malformed")
        elif item.status is IrreversibleAuthorizationStatus.CONSUMED:
            if (
                item.validated_revision is None
                or item.consumed_revision is None
                or item.consumed_revision <= item.validated_revision
                or item.resulting_remote_sha is None
                or item.invalidated_revision is not None
                or item.invalidation_reason is not None
            ):
                raise StateValidationError("consumed irreversible authorization audit is incomplete")
        elif item.status is IrreversibleAuthorizationStatus.INVALIDATED:
            if (
                item.invalidated_revision is None
                or item.invalidated_revision <= item.authorized_revision
                or item.invalidation_reason is None
                or not item.invalidation_reason.strip()
                or item.consumed_revision is not None
                or item.resulting_remote_sha is not None
            ):
                raise StateValidationError("invalidated irreversible authorization audit is incomplete")
        else:
            raise StateValidationError("irreversible authorization status is invalid")
    if pending > 1:
        raise StateValidationError("only one pending irreversible authorization is permitted")


def _validate_autonomy_expansions(state: State) -> None:
    pending = state.pending_autonomy_expansion
    history = state.autonomy_expansion_history
    if pending is not None:
        if type(pending) is not PendingAutonomyExpansion:
            raise StateValidationError("pending autonomy expansion shape is invalid")
        _identity(pending.proposal_id, "autonomy expansion proposal")
        _text(pending.repository_id, "autonomy expansion repository")
        _identity(pending.feature_id, "autonomy expansion feature")
        _identity(pending.change_id, "autonomy expansion change")
        if type(pending.requested_revision) is not int or pending.requested_revision < 0:
            raise StateValidationError("autonomy expansion request revision is invalid")
        _validate_autonomy_envelope(pending.base_envelope)
        _validate_autonomy_delta(pending.delta)
        _validate_autonomy_envelope(pending.proposed_envelope)
        if pending.proposed_envelope != apply_autonomy_envelope_delta(pending.base_envelope, pending.delta):
            raise StateValidationError("pending autonomy expansion no longer matches its exact delta")
        active = state.active_context
        if (
            active is None
            or state.initialization is None
            or state.initialization.repository_id != pending.repository_id
            or active.feature_id != pending.feature_id
            or active.change_id != pending.change_id
            or active.autonomy_envelope != pending.base_envelope
            or state.revision != pending.requested_revision + 1
        ):
            raise StateValidationError("pending autonomy expansion is stale or belongs to different active work")
        if any(
            autonomy_operation_is_forbidden(action)
            for action in pending.proposed_envelope.reversible_actions
            + pending.proposed_envelope.irreversible_permissions
        ):
            raise StateValidationError("protected Git, tag, or release operations cannot be granted by an envelope")
    if type(history) is not tuple or any(type(item) is not AutonomyExpansionRecord for item in history):
        raise StateValidationError("autonomy expansion audit history is invalid")
    _unique((item.proposal_id for item in history), "autonomy expansion audit proposal")
    history_ids: set[AutonomyExpansionId] = set()
    features = {item.id: item for item in state.features}
    changes = {item.id: item for item in state.changes}
    for record in history:
        _identity(record.proposal_id, "autonomy expansion audit proposal")
        _text(record.repository_id, "autonomy expansion audit repository")
        _identity(record.feature_id, "autonomy expansion audit feature")
        _identity(record.change_id, "autonomy expansion audit change")
        if (
            type(record.requested_revision) is not int
            or record.requested_revision < 0
            or type(record.resolved_revision) is not int
            or record.resolved_revision <= record.requested_revision
            or record.resolved_revision > state.revision
            or type(record.decision) is not AutonomyExpansionDecision
        ):
            raise StateValidationError("autonomy expansion audit decision is invalid")
        feature = features.get(record.feature_id)
        change = changes.get(record.change_id)
        if feature is None or change is None or change.feature_id != feature.id:
            raise StateValidationError("autonomy expansion audit must retain Feature and Change identity")
        _validate_autonomy_envelope(record.base_envelope)
        _validate_autonomy_delta(record.delta)
        _validate_autonomy_envelope(record.proposed_envelope)
        _validate_autonomy_envelope(record.resolved_envelope)
        if record.proposed_envelope != apply_autonomy_envelope_delta(record.base_envelope, record.delta):
            raise StateValidationError("autonomy expansion audit does not retain its exact delta")
        if any(
            autonomy_operation_is_forbidden(action)
            for action in record.proposed_envelope.reversible_actions
            + record.proposed_envelope.irreversible_permissions
        ):
            raise StateValidationError("autonomy expansion audit contains protected operations")
        if record.decision is AutonomyExpansionDecision.AUTHORIZE and record.resolved_envelope != record.proposed_envelope:
            raise StateValidationError("authorized autonomy envelope must match the exact proposal")
        if record.decision is AutonomyExpansionDecision.REJECT and record.resolved_envelope != record.base_envelope:
            raise StateValidationError("rejected autonomy proposal must preserve the base envelope")
        if record.decision is AutonomyExpansionDecision.NARROW and (
            not autonomy_envelope_is_narrowing(record.proposed_envelope, record.resolved_envelope)
            or record.resolved_envelope == record.proposed_envelope
        ):
            raise StateValidationError("narrowed autonomy envelope must be a strict subset of the proposal")
        history_ids.add(record.proposal_id)
    if pending is not None and pending.proposal_id in history_ids:
        raise StateValidationError("resolved autonomy expansion cannot be replayed")


def validate_state(state: State) -> None:
    """Validate scalar values and all cross-record authority relationships."""
    if type(state) is not State:
        raise StateValidationError("state shape is invalid")
    if type(state.revision) is not int or state.revision < 0:
        raise StateValidationError("state revision must be a nonnegative integer")
    initialization = state.initialization
    if initialization is not None:
        if type(initialization) is not RepositoryInitialization or not initialization.repository_id:
            raise StateValidationError("repository initialization is invalid")
        for evidence in initialization.evidence:
            if (
                type(evidence) is not DiscoveryEvidence
                or not evidence.kind
                or not evidence.strength
                or not evidence.fingerprint
                or not evidence.source_relationship
            ):
                raise StateValidationError("discovery evidence is invalid")
    parked = state.parked_work
    if parked is not None:
        if type(parked) is not ParkedWork:
            raise StateValidationError("parked work shape is invalid")
        _text(parked.reason, "parked work reason")
        _text(parked.parked_at, "parked work timestamp")
        if type(parked.context) is not WorkingContext:
            raise StateValidationError("parked work context shape is invalid")
        if parked.preserved_primary_context is not None and type(parked.preserved_primary_context) is not WorkingContext:
            raise StateValidationError("parked primary context shape is invalid")
    if type(state.work_items) is not tuple or type(state.work_relations) is not tuple:
        raise StateValidationError("work queue collections must be tuples")
    if any(type(item) is not WorkItem for item in state.work_items):
        raise StateValidationError("work item shape is invalid")
    _unique((item.id for item in state.work_items), "work item")
    work_ids = {item.id for item in state.work_items}
    if state.active_context is None:
        if state.active_work_id is not None or state.preserved_primary_work_id is not None:
            raise StateValidationError("active work identity requires active context")
    elif (
        state.active_context.change_id is None
        or state.active_work_id != WorkId(f"work-{state.active_context.change_id}")
        or state.active_work_id in work_ids
    ):
        raise StateValidationError("active context requires an exclusive work identity")
    if state.preserved_primary_context is None:
        if state.preserved_primary_work_id is not None:
            raise StateValidationError("preserved work identity requires primary context")
    elif (
        state.preserved_primary_context.change_id is None
        or state.preserved_primary_work_id != WorkId(f"work-{state.preserved_primary_context.change_id}")
        or state.preserved_primary_work_id == state.active_work_id
    ):
        raise StateValidationError("preserved primary requires distinct work identity")
    for item in state.work_items:
        _identity(item.id, "work item")
        if type(item.operational_state) is not OperationalState:
            raise StateValidationError("work item operational state is invalid")
        _optional_string(item.reason, "work item reason")
        _text(item.transitioned_at, "work item transition timestamp")
        if item.operational_state is OperationalState.DONE:
            if (
                item.context is not None
                or item.terminal_change_id is None
                or item.id != WorkId(f"work-{item.terminal_change_id}")
            ):
                raise StateValidationError("done work must reference accepted terminal work")
            continue
        if (
            item.context is None
            or item.context.change_id is None
            or item.id != WorkId(f"work-{item.context.change_id}")
            or item.context.status in TERMINAL_STATES
            or item.terminal_change_id is not None
        ):
            raise StateValidationError("nonterminal work item must own nonterminal lifecycle context")
        if item.preserved_primary_context is not None and type(item.preserved_primary_context) is not WorkingContext:
            raise StateValidationError("work item primary context shape is invalid")
        if item.preserved_primary_context is None and item.context.side_of is not None:
            raise StateValidationError("queued side requires preserved primary")
        if item.preserved_primary_context is not None and item.context.side_of != item.preserved_primary_context.feature_id:
            raise StateValidationError("queued primary must own queued side")
        if item.deferred_side_request is not None and (
            type(item.deferred_side_request) is not DeferredSideRequest
            or item.preserved_primary_context is None
            or item.context.side_of != item.deferred_side_request.primary_feature_id
        ):
            raise StateValidationError("queued deferred side request is invalid")
        if item.pending_intake is not None and (
            type(item.pending_intake) is not PendingIntake
            or item.preserved_primary_context is not None
            or item.context.side_of is not None
            or item.pending_intake.deferred_from_feature_id != item.context.feature_id
        ):
            raise StateValidationError("queued pending intake is invalid")
    accepted_archives = tuple(
        archive
        for archive in state.terminal_archives
        if archive.context.status is LifecycleState.ACCEPTED and archive.context.change_id is not None
    )
    for item in state.work_items:
        if (
            item.operational_state is OperationalState.DONE
            and sum(archive.context.change_id == item.terminal_change_id for archive in accepted_archives) != 1
        ):
            raise StateValidationError("done work must retain accepted terminal truth")
    relation_keys: set[tuple[WorkId, WorkId, WorkRelationKind]] = set()
    for relation in state.work_relations:
        if type(relation) is not WorkRelation or type(relation.kind) is not WorkRelationKind:
            raise StateValidationError("work relation shape is invalid")
        if relation.predecessor_id not in work_ids and relation.predecessor_id != state.active_work_id:
            raise StateValidationError("work relation predecessor is unknown")
        if relation.successor_id not in work_ids and relation.successor_id != state.active_work_id:
            raise StateValidationError("work relation successor is unknown")
        if relation.kind in (WorkRelationKind.HARD, WorkRelationKind.SOFT) and relation.predecessor_id == relation.successor_id:
            raise StateValidationError("dependency relation cannot reference itself")
        if relation.kind is WorkRelationKind.CONFLICT:
            if relation.predecessor_id == relation.successor_id or str(relation.predecessor_id) > str(relation.successor_id):
                raise StateValidationError("conflict relation must be canonical and non-self")
        key = (relation.predecessor_id, relation.successor_id, relation.kind)
        if key in relation_keys:
            raise StateValidationError("work relation identities must be unique")
        relation_keys.add(key)
    deferred = state.deferred_side_request
    if deferred is not None:
        if (
            type(deferred) is not DeferredSideRequest
            or not deferred.intent
            or state.active_context is None
            or state.preserved_primary_context is None
            or state.active_context.side_of != deferred.primary_feature_id
        ):
            raise StateValidationError("deferred side request requires active side work")
    intake = state.pending_intake
    if intake is not None:
        if type(intake) is not PendingIntake:
            raise StateValidationError("pending intake is invalid")
        _identity(intake.id, "pending intake")
        _text(intake.intent, "pending intake intent")
        _identity(intake.deferred_from_feature_id, "pending intake primary feature")
        _optional_string(intake.completed_side_change_id, "pending intake completed side change")
        _optional_string(intake.target_hint, "pending intake target hint")
        if state.preserved_primary_context is not None:
            raise StateValidationError("pending intake cannot coexist with side preservation")
    selection = state.pending_capability_selection
    if selection is not None:
        if (
            type(selection) is not PendingCapabilitySelection
            or type(selection.operation) is not AssessmentOperation
            or type(selection.candidates) is not tuple
            or type(selection.standalone_allowed) is not bool
            or type(selection.provenance) is not tuple
            or state.active_context is not None
            or state.pending_intake is not None
            or state.pending_discovery_recovery is not None
        ):
            raise StateValidationError("pending capability selection is invalid")
        _identity(selection.id, "capability selection")
        _text(selection.intent, "capability selection intent")
        if len(selection.candidates) < 2:
            raise StateValidationError("capability selection requires ambiguity")
        if any(type(item) is not CapabilityCandidate or not item.title for item in selection.candidates):
            raise StateValidationError("capability selection candidate is invalid")
        _unique((item.feature_id for item in selection.candidates), "capability selection candidate")
        features_by_id = {item.id: item for item in state.features}
        if any(
            (feature := features_by_id.get(item.feature_id)) is None or feature.title != item.title
            for item in selection.candidates
        ):
            raise StateValidationError("capability selection candidate must be Core authority")
        _strings(selection.provenance, "capability selection provenance")
        if selection.provenance != tuple(f"feature:{item.feature_id}" for item in selection.candidates):
            raise StateValidationError("capability selection provenance is invalid")
    recovery = state.pending_discovery_recovery
    if recovery is not None:
        if (
            type(recovery) is not PendingDiscoveryRecovery
            or recovery.operation is not AssessmentOperation.NEW
            or type(recovery.candidates) is not tuple
            or type(recovery.evidence) is not tuple
            or state.active_context is not None
            or state.pending_intake is not None
            or selection is not None
        ):
            raise StateValidationError("pending discovery recovery is invalid")
        _identity(recovery.id, "discovery recovery")
        _text(recovery.intent, "discovery recovery intent")
        _strings(recovery.candidates, "discovery recovery candidates")
        _unique(recovery.candidates, "discovery recovery candidate")
        if not recovery.candidates:
            raise StateValidationError("discovery recovery requires candidates and evidence")
        for item in recovery.evidence:
            if type(item) is not DiscoveryRecoveryEvidence:
                raise StateValidationError("discovery recovery evidence is invalid")
            _identity(item.id, "discovery recovery evidence")
            _text(item.candidate, "discovery recovery evidence candidate")
            if item.candidate not in recovery.candidates or not item.fingerprint:
                raise StateValidationError("discovery recovery evidence is invalid")
            try:
                RepositoryPath(str(item.path))
            except DomainError as error:
                raise StateValidationError("discovery recovery evidence path is invalid") from error
        _unique((item.id for item in recovery.evidence), "discovery recovery evidence")
    history = state.capability_selection_history
    if type(history) is not tuple or any(type(item) is not CapabilitySelection for item in history):
        raise StateValidationError("capability selection history is invalid")
    _unique((item.id for item in history), "capability selection history")
    features_by_id = {item.id: item for item in state.features}
    changes_by_id = {item.id: item for item in state.changes}
    for item in history:
        _identity(item.id, "capability selection history")
        _text(item.choice, "capability selection history choice")
        _text(item.intent, "capability selection history intent")
        _strings(item.provenance, "capability selection history provenance")
        if (
            item.choice != "CREATE_STANDALONE_CAPABILITY"
            or item.feature_id not in features_by_id
            or item.change_id not in changes_by_id
            or changes_by_id[item.change_id].feature_id != item.feature_id
            or changes_by_id[item.change_id].intent != item.intent
        ):
            raise StateValidationError("capability selection history is not authoritative")
    if any(type(item) is not SharedIntent or not item.intent for item in state.shared_intents):
        raise StateValidationError("shared intent is invalid")
    _unique((item.id for item in state.shared_intents), "shared intent")
    if any(
        type(item) is not DeferredLinkedIntent or not item.capability_target or not item.intent
        for item in state.deferred_linked_intents
    ):
        raise StateValidationError("deferred linked intent is invalid")
    _unique((item.id for item in state.deferred_linked_intents), "deferred linked intent")
    if any(item.shared_intent_id not in {shared.id for shared in state.shared_intents} for item in state.deferred_linked_intents):
        raise StateValidationError("deferred linked intent must reference shared intent")
    for values, label in (
        (state.publications, "publications"),
        (state.completed_side_contexts, "completed side contexts"),
        (state.features, "features"),
        (state.changes, "changes"),
        (state.decisions, "decisions"),
        (state.observations, "observations"),
        (state.drifts, "drifts"),
        (state.terminal_archives, "terminal archives"),
        (state.publication_history, "publication history"),
        (state.locks, "semantic locks"),
        (state.lock_revisions, "semantic lock revisions"),
        (state.lock_releases, "lock releases"),
        (state.lock_authorizations, "lock authorizations"),
        (state.change_lock_assessments, "change lock assessments"),
    ):
        if type(values) is not tuple:
            raise StateValidationError(f"{label} must be a tuple")
    if state.active_context is not None and type(state.active_context) is not WorkingContext:
        raise StateValidationError("active context shape is invalid")
    if (
        state.preserved_primary_context is not None
        and type(state.preserved_primary_context) is not WorkingContext
    ):
        raise StateValidationError("preserved primary context shape is invalid")
    records = _validate_records(state)
    _validate_irreversible_authorizations(state)
    _validate_autonomy_expansions(state)
    if intake is not None:
        active = state.active_context
        if (
            intake.deferred_from_feature_id not in records.features
            or (
                active is not None
                and (
                    active.feature_id != intake.deferred_from_feature_id
                    or active.side_of is not None
                )
            )
        ):
            raise StateValidationError("pending intake must retain its restored primary")
        if intake.completed_side_change_id is not None:
            completed = next(
                (
                    archive
                    for archive in state.terminal_archives
                    if archive.context.change_id == intake.completed_side_change_id
                ),
                None,
            )
            if completed is None or completed.parent_feature_id != intake.deferred_from_feature_id:
                raise StateValidationError("pending intake completed side provenance is invalid")
    if any(type(item) is not Drift for item in state.drifts):
        raise StateValidationError("drift record shape is invalid")
    for drift in state.drifts:
        _identity(drift.id, "drift")
        _identity(drift.expected_publication, "expected publication")
        if not isinstance(drift.observed, str):
            raise StateValidationError("drift observation must be a string")
        _optional_string(drift.change_id, "drift change")
    _unique(
        tuple(item.id for item in state.observations) + tuple(item.id for item in state.drifts),
        "observation and drift",
    )
    contexts = tuple(
        item
        for item in (state.active_context, state.preserved_primary_context)
        if item is not None
    ) + (() if parked is None else tuple(
        item for item in (parked.context, parked.preserved_primary_context) if item is not None
    )) + tuple(
        context
        for item in state.work_items
        for context in (item.context, item.preserved_primary_context)
        if context is not None
    )
    for context in contexts:
        _validate_context(context, records, state.publication_history)
    if state.active_context is not None and state.active_context.status in TERMINAL_STATES:
        raise StateValidationError("terminal context cannot be active work")
    if state.preserved_primary_context is not None:
        if (
            state.active_context is None
            or state.active_context.side_of != state.preserved_primary_context.feature_id
        ):
            raise StateValidationError("preserved primary requires its linked active side")
    elif state.active_context is not None and state.active_context.side_of is not None:
        raise StateValidationError("active side requires a preserved primary")
    if parked is not None:
        parked_context = parked.context
        parked_primary = parked.preserved_primary_context
        if parked_context.status in TERMINAL_STATES:
            raise StateValidationError("terminal context cannot be parked work")
        if parked_primary is not None:
            if parked_context.side_of != parked_primary.feature_id:
                raise StateValidationError("parked side requires its linked primary")
        elif parked_context.side_of is not None:
            raise StateValidationError("parked side requires a preserved primary")
        parked_deferred = parked.deferred_side_request
        if parked_deferred is not None and (
            type(parked_deferred) is not DeferredSideRequest
            or not parked_deferred.intent
            or parked_primary is None
            or parked_context.side_of != parked_deferred.primary_feature_id
        ):
            raise StateValidationError("parked deferred side request is invalid")
        parked_intake = parked.pending_intake
        if parked_intake is not None:
            if (
                type(parked_intake) is not PendingIntake
                or parked_primary is not None
                or parked_context.side_of is not None
                or parked_intake.deferred_from_feature_id != parked_context.feature_id
            ):
                raise StateValidationError("parked pending intake is invalid")
            _identity(parked_intake.id, "parked pending intake")
            _text(parked_intake.intent, "parked pending intake intent")
            _identity(parked_intake.deferred_from_feature_id, "parked pending intake primary feature")
            _optional_string(parked_intake.completed_side_change_id, "parked pending intake completed side change")
            _optional_string(parked_intake.target_hint, "parked pending intake target hint")
    for archive in state.terminal_archives:
        _validate_archive(archive, records, state.publication_history)
    for archive in state.completed_side_contexts:
        _validate_archive(archive, records, state.publication_history)
    _unique(
        (
            f"{item.context.feature_id}:{item.context.change_id}"
            for item in state.terminal_archives
        ),
        "terminal archive",
    )
    completed_sides = tuple(
        item for item in state.terminal_archives if item.parent_feature_id is not None
    )
    if state.completed_side_contexts != completed_sides:
        raise StateValidationError(
            "completed sides must exactly project ordered side terminal archives"
        )
    owner_ids = tuple(
        context.change_id for context in contexts if context.change_id is not None
    ) + tuple(
        archive.context.change_id
        for archive in state.terminal_archives
        if archive.context.change_id is not None
    )
    for change in state.changes:
        if owner_ids.count(change.id) != 1:
            raise StateValidationError("each change must have exactly one lifecycle owner")
    _validate_publications(state, records)
    publication_ids = {item.living_spec_id for item in state.publications}
    _unique((item.id for item in state.drifts), "drift")
    for drift in state.drifts:
        if type(drift.unresolved) is not bool:
            raise StateValidationError("drift unresolved flag must be boolean")
        if (
            drift.expected_publication not in publication_ids
            or drift.change_id is None
            or drift.change_id not in records.changes
        ):
            raise StateValidationError("drift must reference current unresolved product truth")
        if not drift.unresolved and not any(
            item.kind == "constraint" and item.change_id == drift.change_id
            for item in state.decisions
        ):
            raise StateValidationError("resolved drift requires its reconciliation decision")


@dataclass(frozen=True, slots=True)
class ReviewInspection:
    """Every durable fact available to a holistic caller review."""

    active_context: WorkingContext | None
    preserved_primary_context: WorkingContext | None
    features: tuple[Feature, ...]
    changes: tuple[Change, ...]
    decisions: tuple[Decision, ...]
    observations: tuple[Observation, ...]
    drifts: tuple[Drift, ...]
    publications: tuple[LivingSpecPublication, ...]
    publication_history: tuple[LivingSpecPublication, ...]
    terminal_archives: tuple[TerminalArchive, ...]
    state: State
    locks: tuple[SemanticLock, ...] = ()
    lock_revisions: tuple[SemanticLockRevision, ...] = ()
    lock_releases: tuple[LockRelease, ...] = ()
    lock_authorizations: tuple[LockAuthorization, ...] = ()

    @property
    def context(self) -> WorkingContext | None:
        return self.active_context

    @property
    def current_decisions(self) -> tuple[Decision, ...]:
        superseded = {
            item.supersedes for item in self.decisions if item.supersedes is not None
        }
        return tuple(item for item in self.decisions if item.id not in superseded)


def inspect(state: State) -> ReviewInspection:
    """Project durable state into a read-only holistic review value."""
    return ReviewInspection(
        state.active_context,
        state.preserved_primary_context,
        state.features,
        state.changes,
        state.decisions,
        state.observations,
        state.drifts,
        state.publications,
        state.publication_history,
        state.terminal_archives,
        state,
        state.locks,
        state.lock_revisions,
        state.lock_releases,
        state.lock_authorizations,
    )
