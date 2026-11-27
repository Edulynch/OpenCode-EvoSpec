"""Immutable domain records with no filesystem or agent dependencies."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Literal, NewType, NoReturn, NotRequired, TypedDict, cast

from .input_fingerprint import (
    COVERAGE_CONTRACT_VERSION,
    INPUT_POLICY_DIGEST,
    INPUT_POLICY_ID,
    LEGACY_INPUT_POLICY_DIGEST,
    LEGACY_INPUT_POLICY_ID,
    LEGACY_R3B_APPROVAL_POLICY_TEXT,
    LEGACY_TEST_ADMISSION_POLICY_DIGEST,
    R3B_APPROVAL_POLICY_TEXT,
    TEST_ADMISSION_POLICY_DIGEST,
    TEST_ADMISSION_POLICY_ID,
    TEST_ENVIRONMENT_PROFILE_DIGEST,
    TEST_ENVIRONMENT_PROFILE_ID,
)
from .lifecycle import (
    TERMINAL_STATES,
    Classification,
    LifecycleError,
    LifecycleState,
    Phase,
    ReviewVerdict,
    can_converge,
    validate_context_state,
)
from .request_scoped_objective import (
    OBJECTIVE_TEST_KIND,
    OBJECTIVE_TEST_TARGET,
    REQUEST_SCOPED_OBJECTIVE,
    TASK_APP_TEST_KIND,
    TASK_APP_TEST_TARGET,
    PersonalTasksObjective,
    StaticHeadingObjective,
    derive_request_objective,
)

REQUEST_OBJECTIVE_ADMISSION_POLICY_ID = OBJECTIVE_TEST_KIND
REQUEST_OBJECTIVE_ADMISSION_POLICY_DIGEST = sha256(
    b"core-static-heading-v1:strict-utf8:bounded-index.html:exact-single-h1"
).hexdigest()
SEMANTIC_REQUEST_OBJECTIVE_ADMISSION_POLICY_DIGEST = sha256(
    b"core-personal-tasks-browser-v2:playwright-1.65.2:chromium:"
    b"same-context-exact-page-replacement:durable-tasks-and-completion:"
    b"REQUEST_SCOPED_PERSONAL_TASKS"
).hexdigest()
SEMANTIC_REQUEST_OBJECTIVE_ADMISSION_POLICY_ID = "core-personal-tasks-browser-v2"
LEGACY_SEMANTIC_REQUEST_OBJECTIVE_ADMISSION_POLICY_ID = TASK_APP_TEST_KIND
LEGACY_SEMANTIC_REQUEST_OBJECTIVE_ADMISSION_POLICY_DIGEST = sha256(
    b"core-personal-tasks-browser-v1:playwright-1.65.2:chromium:REQUEST_SCOPED_PERSONAL_TASKS"
).hexdigest()

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
ComposedFeatureId = NewType("ComposedFeatureId", str)
ComposedIncrementId = NewType("ComposedIncrementId", str)
ComposedTaskId = NewType("ComposedTaskId", str)
ComposedCriterionId = NewType("ComposedCriterionId", str)


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
class AgreementRecord:
    """Immutable, user-accepted Feature or Slice scope owned by Core."""

    id: str
    kind: str
    feature_id: FeatureId
    change_id: ChangeId
    title: str
    statement: str
    source: str
    source_reference: str
    repository_id: str
    task_id: str | None = None
    initial_task: str | None = None
    checkpoint: str | None = None


@dataclass(frozen=True, slots=True)
class SliceCriterion:
    """Observable acceptance condition; this is a promise, not a test result."""

    id: str
    expected_result: str
    expected_evidence: str
    provenance: str | None = None


@dataclass(frozen=True, slots=True)
class SliceCriteriaRevision:
    """Immutable, attributable version of the agreed Slice boundaries."""

    id: str
    slice_agreement_ref: str
    repository_id: str
    outcome: str
    example: str
    inclusions: tuple[str, ...]
    exclusions: tuple[str, ...]
    decisions: tuple[str, ...]
    criteria: tuple[SliceCriterion, ...]
    source: str
    source_reference: str
    previous_revision_id: str | None = None
    reason: str | None = None
    decision_statement: str | None = None


def request_objective_criteria_revision(
    slice_agreement_ref: str,
    repository_id: str,
    source_reference: str,
    statement: str,
) -> SliceCriteriaRevision | None:
    """Derive exact immutable criteria for a bounded request objective."""
    objective = derive_request_objective(statement)
    if objective is None:
        return None
    digest = sha256(json.dumps(
        [slice_agreement_ref, source_reference],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:20]
    if isinstance(objective, StaticHeadingObjective):
        criterion = SliceCriterion(
            "request-objective-heading",
            f"One attribute-free H1 exactly equals: {objective.heading}",
            f"{OBJECTIVE_TEST_KIND}:{OBJECTIVE_TEST_TARGET}",
        )
        outcome = "The requested static heading is present in index.html"
        example = "index.html contains one plain H1 with the exact requested heading"
        decisions = ("No additional product decisions are included",)
        criteria = (criterion,)
    else:
        criteria = tuple(
            SliceCriterion(
                f"request-objective-{operation}",
                {
                    "create": "A personal task can be created and is durably retained in the app.",
                    "consult": "The app lists the personal tasks that have been created.",
                    "complete": "A personal task can be marked complete and its completed state is retained.",
                }[operation],
                f"{TASK_APP_TEST_KIND}:{TASK_APP_TEST_TARGET}",
                "EXPLICIT_REQUEST" if operation in objective.explicit_operations else "DERIVED_OBJECTIVE",
            )
            for operation in objective.operations
        )
        outcome = "The bounded personal-tasks objective is implemented in index.html"
        example = "The requested personal-task operations work through the local web app"
        decisions = (
            "AGENT_IMPLEMENTATION_CHOICE: UI, layout, and local persistence details are discretionary and are not acceptance criteria",
        )
    return SliceCriteriaRevision(
        f"slice-criteria-objective-{digest}",
        slice_agreement_ref,
        repository_id,
        outcome,
        example,
        ("index.html",),
        ("Every other file and behavior",),
        decisions,
        criteria,
        REQUEST_SCOPED_OBJECTIVE,
        source_reference,
    )


def request_objective_policy(
    criteria: SliceCriteriaRevision,
) -> tuple[str, str, str]:
    """Return the fixed admission policy and outcome basis for canonical criteria."""
    if type(criteria) is not SliceCriteriaRevision or criteria.source != REQUEST_SCOPED_OBJECTIVE:
        raise DomainError("request-objective policy requires Core-derived criteria")
    if len(criteria.criteria) == 1 and criteria.criteria[0].id == "request-objective-heading":
        return (
            OBJECTIVE_TEST_KIND,
            REQUEST_OBJECTIVE_ADMISSION_POLICY_DIGEST,
            "REQUEST_SCOPED_STATIC_HEADING",
        )
    if tuple(item.id for item in criteria.criteria) and all(
        item.id.startswith("request-objective-")
        and item.expected_evidence == f"{TASK_APP_TEST_KIND}:{TASK_APP_TEST_TARGET}"
        and item.provenance in ("EXPLICIT_REQUEST", "DERIVED_OBJECTIVE")
        for item in criteria.criteria
    ):
        return (
            SEMANTIC_REQUEST_OBJECTIVE_ADMISSION_POLICY_ID,
            SEMANTIC_REQUEST_OBJECTIVE_ADMISSION_POLICY_DIGEST,
            "REQUEST_SCOPED_PERSONAL_TASKS",
        )
    raise DomainError("request-objective criteria have no canonical admission policy")


def _request_objective_policy_versions(
    criteria: SliceCriteriaRevision,
) -> tuple[tuple[str, str], ...]:
    policy_id, current_digest, outcome_basis = request_objective_policy(criteria)
    if outcome_basis == "REQUEST_SCOPED_PERSONAL_TASKS":
        return tuple(dict.fromkeys((
            (policy_id, current_digest),
            (
                LEGACY_SEMANTIC_REQUEST_OBJECTIVE_ADMISSION_POLICY_ID,
                LEGACY_SEMANTIC_REQUEST_OBJECTIVE_ADMISSION_POLICY_DIGEST,
            ),
        )))
    return ((policy_id, current_digest),)


def request_objective_completion_condition(
    objective: StaticHeadingObjective | PersonalTasksObjective,
) -> str:
    """Render the exact Core-verified request objective used for closure."""
    if type(objective) is StaticHeadingObjective:
        return f"Core verified request-scoped static heading: {objective.heading}"
    if type(objective) is PersonalTasksObjective:
        return (
            "Core verified request-scoped personal tasks objective: "
            + ", ".join(objective.operations)
        )
    raise DomainError("request-objective completion requires a canonical objective")


@dataclass(frozen=True, slots=True)
class SliceCriteriaHead:
    """Core-owned current pointer; old criterion revisions remain accessible."""

    slice_agreement_ref: str
    revision_id: str


@dataclass(frozen=True, slots=True)
class ProvisionalSliceCriterion:
    id: str
    expected_result: str
    expected_evidence: str


@dataclass(frozen=True, slots=True)
class ProvisionalSliceSourceReference:
    session_id: str
    message_id: str


@dataclass(frozen=True, slots=True)
class ProvisionalSliceProvenance:
    kind: str
    source_reference: ProvisionalSliceSourceReference | None = None
    expression: str | None = None
    expression_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ProvisionalSliceRevision:
    proposal_id: str
    revision_id: str
    revision: int
    repository_id: str
    feature_id: FeatureId
    title: str
    outcome: str
    example: str | None
    inclusions: tuple[str, ...]
    exclusions: tuple[str, ...]
    considerations: tuple[str, ...]
    open_questions: tuple[str, ...]
    criteria: tuple[ProvisionalSliceCriterion, ...]
    provenance: ProvisionalSliceProvenance
    previous_revision_id: str | None
    digest: str

    @property
    def id(self) -> str:
        return self.revision_id


@dataclass(frozen=True, slots=True)
class ProvisionalSliceHead:
    proposal_id: str
    revision_id: str
    revision: int
    digest: str


@dataclass(frozen=True, slots=True)
class ProvisionalSliceAdoption:
    """Immutable D5 mapping from one suggested head to one accepted Slice."""

    id: str
    repository_id: str
    feature_id: FeatureId
    proposal_id: str
    proposal_revision_id: str
    proposal_revision: int
    proposal_digest: str
    source_schema: int
    source_revision: int
    source_state_sha256: str
    statement: str
    source: str
    source_reference: str
    feature_agreement_ref: str
    slice_agreement_ref: str
    change_id: ChangeId
    criteria_revision_id: str
    task_id: str
    slice_title: str
    initial_task: str
    checkpoint: str
    outcome: str
    example: str
    inclusions: tuple[str, ...]
    exclusions: tuple[str, ...]
    decisions: tuple[str, ...]
    criteria: tuple[SliceCriterion, ...]
    question_answers: tuple[tuple[str, str], ...]
    recorded_revision: int


def provisional_slice_adoption_statement_text(
    *,
    repository_id: str,
    feature_id: str,
    source_revision: int,
    source_state_sha256: str,
    proposal: ProvisionalSliceRevision,
    slice_title: str,
    initial_task: str,
    checkpoint: str,
    outcome: str,
    example: str,
    inclusions: tuple[str, ...],
    exclusions: tuple[str, ...],
    decisions: tuple[str, ...],
    criteria: tuple[SliceCriterion, ...],
    question_answers: tuple[tuple[str, str], ...],
) -> str:
    """Render the exact bounded human decision for one selected D4 head."""
    accepted_spec = {
        "slice_title": slice_title,
        "initial_task": initial_task,
        "checkpoint": checkpoint,
        "outcome": outcome,
        "example": example,
        "inclusions": list(inclusions),
        "exclusions": list(exclusions),
        "decisions": list(decisions),
        "proposal_criteria_mapping": [
            {
                "proposal_criterion_id": source.id,
                "accepted_criterion_id": accepted.id,
                "expected_result": accepted.expected_result,
                "expected_evidence": accepted.expected_evidence,
            }
            for source, accepted in zip(proposal.criteria, criteria, strict=True)
        ],
        "material_question_answers": [
            {"question": question, "answer": answer}
            for question, answer in question_answers
        ],
    }
    mini_spec = json.dumps(
        accepted_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return "\n".join((
        "EvoSpec D5: explicit interactive acceptance of one provisional AGENT_SUGGESTION head",
        f"Repository: {repository_id}",
        f"Feature: {feature_id}",
        f"Current proposal head: {proposal.proposal_id} / {proposal.revision_id} / revision {proposal.revision}",
        f"Current proposal digest: {proposal.digest}",
        f"Expected State revision: {source_revision}",
        f"Expected State SHA-256: {source_state_sha256}",
        "The selected suggestion is not itself acceptance; every other head and all history remain unchanged.",
        f"Accepted mini-spec: {mini_spec}",
        "Validation: nonblank bounded text; 1–32 inclusions, exclusions, decisions and criteria; "
        "unique stable criterion IDs; each text field at most 2 KiB; canonical mini-spec at most 32 KiB.",
        "Every selected material question is answered explicitly; accepted criteria map one-to-one "
        "to the exact retained proposal criteria.",
        "This creates one actionable Task only. It does not verify criteria, accept a Slice outcome, "
        "complete the Feature, or publish Product Truth.",
        "Schema 23 to 24 occurs only with this accepted mapping; no general migration or downgrade is authorized.",
    ))


def provisional_slice_adoption_identity_digest(
    *,
    repository_id: str,
    feature_id: str,
    proposal_id: str,
    proposal_revision_id: str,
    proposal_revision: int,
    proposal_digest: str,
    source_revision: int,
    source_state_sha256: str,
    statement: str,
    source_reference: str,
) -> str:
    """Derive stable accepted-record IDs from the complete consent binding."""
    return sha256(json.dumps(
        [
            repository_id,
            feature_id,
            proposal_id,
            proposal_revision_id,
            proposal_revision,
            proposal_digest,
            source_revision,
            source_state_sha256,
            statement,
            source_reference,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:16]


SESSION_REQUEST_TASK = "Implementar únicamente el resultado expresado en la petición"
SESSION_REQUEST_CHECKPOINT = "Comprobar el resultado observable expresado en la petición"
D4_PROVISIONAL_SLICE_ACTIVATION_MARKER = "D4_SCHEMA15_AUTO_FIRST"


@dataclass(frozen=True, slots=True)
class ProvisionalSliceActivation:
    """Core record of the narrowly eligible schema-15 session-request opt-in."""

    marker: str
    source_schema: int
    source_revision: int
    source_sha256: str
    repository_id: str
    feature_id: FeatureId
    feature_agreement_ref: str
    slice_agreement_ref: str
    recorded_revision: int


@dataclass(frozen=True, slots=True)
class CoverageInput:
    """One explicit root file or directory-presence dependency declaration."""

    kind: str
    path: str


@dataclass(frozen=True, slots=True)
class CriterionCoverageDeclaration:
    """User-proposed known inputs for one exact accepted test descriptor."""

    criterion_id: str
    test_kind: str
    test_target: str
    known_inputs: tuple[CoverageInput, ...]


@dataclass(frozen=True, slots=True)
class CriterionCoverageApproval:
    """Immutable TTY-approved known-input contract for one exact criterion."""

    id: str
    contract_digest: str
    repository_id: str
    slice_agreement_ref: str
    criteria_revision_id: str
    criterion_id: str
    test_kind: str
    test_target: str
    known_inputs: tuple[CoverageInput, ...]
    root_fingerprint: str
    input_policy_id: str
    input_policy_digest: str
    admission_policy_id: str
    admission_policy_digest: str
    environment_profile_id: str
    environment_profile_policy_digest: str
    statement: str
    source_reference: str
    source: str
    source_revision: int
    recorded_revision: int


def coverage_declarations_json(
    declarations: tuple[CriterionCoverageDeclaration, ...],
) -> str:
    """Return the one canonical, human-displayable coverage proposal."""
    return json.dumps(
        {
            "version": COVERAGE_CONTRACT_VERSION,
            "criteria": [
                {
                    "criterion_id": item.criterion_id,
                    "test_kind": item.test_kind,
                    "test_target": item.test_target,
                    "known_inputs": [
                        {"kind": known.kind, "path": known.path}
                        for known in sorted(item.known_inputs, key=lambda value: (value.kind, value.path))
                    ],
                }
                for item in sorted(declarations, key=lambda value: value.criterion_id)
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def coverage_semantic_digest(approval: CriterionCoverageApproval) -> str:
    values = [
        approval.repository_id,
        approval.slice_agreement_ref,
        approval.criteria_revision_id,
        approval.criterion_id,
        approval.test_kind,
        approval.test_target,
        [
            [item.kind, item.path]
            for item in sorted(approval.known_inputs, key=lambda value: (value.kind, value.path))
        ],
        approval.root_fingerprint,
        approval.input_policy_id,
        approval.input_policy_digest,
        approval.admission_policy_id,
        approval.admission_policy_digest,
        approval.environment_profile_id,
        approval.environment_profile_policy_digest,
    ]
    return sha256(json.dumps(
        values, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def coverage_set_digest(approvals: tuple[CriterionCoverageApproval, ...]) -> str:
    values = [
        [
            item.criterion_id,
            item.test_kind,
            item.test_target,
            [
                [known.kind, known.path]
                for known in sorted(item.known_inputs, key=lambda known: (known.kind, known.path))
            ],
        ]
        for item in sorted(approvals, key=lambda value: value.criterion_id)
    ]
    return sha256(json.dumps(
        values, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class R3SchemaUpgradeReceipt:
    """A terminal-approved conversion of an exact schema-15 authority snapshot."""

    source_revision: int
    converted_revision: int
    source_sha256: str
    statement: str
    source_reference: str
    repository_id: str


@dataclass(frozen=True, slots=True)
class R3BSchemaUpgradeReceipt:
    """A terminal-approved conversion of exact schema-16 Slice authority."""

    source_revision: int
    converted_revision: int
    source_sha256: str
    statement: str
    source_reference: str
    repository_id: str
    slice_agreement_ref: str
    criteria_revision_id: str
    input_fingerprint: str | None = None
    coverage_contract_digest: str | None = None


@dataclass(frozen=True, slots=True)
class CriterionTestReceipt:
    """One host-authorized test observation, not final Slice acceptance."""

    id: str
    slice_agreement_ref: str
    criteria_revision_id: str
    criterion_id: str
    test_kind: str
    test_target: str
    exit_code: int
    output_sha256: str
    source_revision: int
    recorded_revision: int
    input_fingerprint_before: str | None = None
    input_fingerprint_after: str | None = None
    input_policy_id: str | None = None
    input_policy_digest: str | None = None
    admission_policy_id: str | None = None
    admission_policy_digest: str | None = None
    external_inputs: tuple[str, ...] = ()
    runtime_executable: str | None = None
    runtime_executable_sha256: str | None = None
    runtime_version: str | None = None
    runtime_argv: tuple[str, ...] = ()
    environment_profile_id: str | None = None
    environment_profile_digest: str | None = None
    coverage_approval_id: str | None = None
    coverage_contract_digest: str | None = None


@dataclass(frozen=True, slots=True)
class SliceOutcomeReceipt:
    """Immutable automatic outcome for one accepted Slice criteria head."""

    id: str
    repository_id: str
    slice_agreement_ref: str
    criteria_revision_id: str
    evidence_receipt_ids: tuple[str, ...]
    input_fingerprint: str
    input_policy_id: str
    input_policy_digest: str
    admission_policy_id: str
    admission_policy_digest: str
    relevant_drift_digest: str
    basis: str
    source: str
    source_revision: int
    recorded_revision: int
    coverage_contract_digest: str | None = None


@dataclass(frozen=True, slots=True)
class FeatureCompletionReceipt:
    """Human-confirmed closure of one exact Feature and its verified Slice set."""

    id: str
    repository_id: str
    feature_id: FeatureId
    feature_agreement_refs: tuple[str, ...]
    condition: str
    slice_agreement_refs: tuple[str, ...]
    slice_outcome_receipt_ids: tuple[str, ...]
    input_fingerprint: str
    statement: str
    source: str
    source_reference: str
    source_revision: int
    recorded_revision: int
    cycle_id: int = 0


@dataclass(frozen=True, slots=True)
class FeatureReopenReceipt:
    """Human authorization to continue the same Feature in a new, auditable cycle."""

    id: str
    repository_id: str
    feature_id: FeatureId
    cycle_id: int
    previous_completion_receipt_id: str
    reason: str
    purpose: str
    input_fingerprint: str
    statement: str
    source: str
    source_reference: str
    source_revision: int
    recorded_revision: int


def feature_reopen_semantic_id(receipt: FeatureReopenReceipt) -> str:
    """Identify one human decision without depending on publication timing."""
    values = [
        receipt.repository_id, receipt.feature_id, receipt.cycle_id,
        receipt.previous_completion_receipt_id, receipt.reason, receipt.purpose,
        receipt.input_fingerprint, receipt.statement, receipt.source,
        receipt.source_reference,
    ]
    return "feature-reopen-" + sha256(json.dumps(
        values, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:20]


def feature_reopen_statement_text(
    *, feature: Feature, cycle_id: int, previous_completion_receipt_id: str,
    reason: str, purpose: str, repository_id: str, source_revision: int,
    input_fingerprint: str,
) -> str:
    """Keep the exact words approved by a human independently recoverable."""
    return "\n".join((
        "EvoSpec: explicit Feature reopening, not Slice acceptance or publication",
        f"Feature: {feature.title} ({feature.id})",
        f"New cycle: {cycle_id}",
        f"Previous completion receipt: {previous_completion_receipt_id}",
        f"Human reason: {reason}",
        f"New purpose: {purpose}",
        f"Repository: {repository_id}",
        f"Expected revision: {source_revision}",
        f"Root input fingerprint: {input_fingerprint}",
        "Schema conversion: 18 to 19 on reopening, or schema 19 retained",
        "No Slice is created, accepted, activated, or resumed by this decision",
        "This does not authorize model execution, merge, tags, or release",
    ))


def feature_cycle_completion_statement_text(
    *, feature: Feature, cycle_id: int, previous_reopen_receipt_id: str,
    condition: str, repository_id: str, source_revision: int,
    input_fingerprint: str, feature_agreement_refs: tuple[str, ...],
    slice_outcomes: tuple[tuple[str, str], ...],
) -> str:
    """A later closure must explicitly name its cycle and fresh evidence."""
    lines = [
        "EvoSpec: explicit Feature cycle completion, not Slice acceptance or publication",
        f"Feature: {feature.title} ({feature.id})",
        f"Cycle: {cycle_id}",
        f"Reopening authority: {previous_reopen_receipt_id}",
        f"Human-approved completion condition: {condition}",
        f"Repository: {repository_id}",
        f"Expected revision: {source_revision}",
        f"Root input fingerprint: {input_fingerprint}",
        "Feature agreement references: " + ", ".join(feature_agreement_refs),
        "Verified terminal Slices and current outcome receipts:",
    ]
    lines.extend(f"- {slice_ref}: {outcome_ref}" for slice_ref, outcome_ref in slice_outcomes)
    lines.extend((
        "Schema 19 retained; earlier cycle receipts remain immutable",
        "This does not authorize model execution, merge, tags, release, or reopening",
    ))
    return "\n".join(lines)


def feature_completion_semantic_id(receipt: FeatureCompletionReceipt) -> str:
    """Tie a Feature closure to its human decision and immutable Slice evidence."""
    values: list[object] = [
        receipt.repository_id, receipt.feature_id,
        list(receipt.feature_agreement_refs), receipt.condition,
        list(receipt.slice_agreement_refs), list(receipt.slice_outcome_receipt_ids),
        receipt.input_fingerprint, receipt.statement, receipt.source,
        receipt.source_reference,
    ]
    # Schema-18 closures retain their original semantic identity byte for byte.
    if receipt.cycle_id:
        values.append(receipt.cycle_id)
    return "feature-completion-" + sha256(json.dumps(
        values, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:20]


def request_objective_completion_statement(
    *,
    source_reference: str,
    request_sha256: str,
    heading: str | None = None,
    outcome_receipt_id: str,
    input_fingerprint: str,
    source_revision: int,
    operations: tuple[str, ...] = (),
) -> str:
    """Canonical system provenance; it is explicitly not a human decision."""
    if heading is not None and not operations:
        return "\n".join((
            "EvoSpec: Core request-scoped objective completion; not human approval or Product Truth publication",
            f"Original session request: {source_reference}",
            f"Request SHA-256: {request_sha256}",
            f"Verified static heading: {heading}",
            f"Slice outcome receipt: {outcome_receipt_id}",
            f"Root input fingerprint: {input_fingerprint}",
            f"Expected revision: {source_revision}",
            "No Product Truth was published",
        ))
    if (
        heading is not None
        or type(operations) is not tuple
        or not operations
        or any(operation not in ("create", "consult", "complete") for operation in operations)
        or len(set(operations)) != len(operations)
        or operations != tuple(operation for operation in ("create", "consult", "complete") if operation in operations)
    ):
        raise DomainError("request-objective completion statement has no canonical objective")
    return "\n".join((
        "EvoSpec: Core request-scoped objective completion; not human approval or Product Truth publication",
        f"Original session request: {source_reference}",
        f"Request SHA-256: {request_sha256}",
        f"Verified personal-tasks operations: {', '.join(operations)}",
        f"Slice outcome receipt: {outcome_receipt_id}",
        f"Root input fingerprint: {input_fingerprint}",
        f"Expected revision: {source_revision}",
        "No Product Truth was published",
    ))


def feature_cycle_status(
    state: State, feature_id: FeatureId,
) -> tuple[int, FeatureReopenReceipt | None, FeatureCompletionReceipt | None]:
    """Derive current cycle authority; historical closures never close a newer cycle."""
    reopenings = tuple(item for item in state.feature_reopen_receipts if item.feature_id == feature_id)
    latest_reopen = max(reopenings, key=lambda item: item.cycle_id) if reopenings else None
    cycle_id = 0 if latest_reopen is None else latest_reopen.cycle_id
    closure = next((item for item in state.feature_completion_receipts
                    if item.feature_id == feature_id and item.cycle_id == cycle_id), None)
    return cycle_id, latest_reopen, closure


def feature_completion_statement_text(
    *, feature: Feature, condition: str, repository_id: str,
    source_revision: int, input_fingerprint: str,
    feature_agreement_refs: tuple[str, ...],
    slice_outcomes: tuple[tuple[str, str], ...],
    previous_closure_exists: bool,
    schema19_retained: bool = False,
) -> str:
    """Render the exact human decision also required when recovering its receipt."""
    lines = [
        "EvoSpec: explicit Feature completion, not Slice acceptance or publication",
        f"Feature: {feature.title} ({feature.id})",
        f"Human-approved completion condition: {condition}",
        f"Repository: {repository_id}",
        f"Expected revision: {source_revision}",
        f"Root input fingerprint: {input_fingerprint}",
        "Feature agreement references: " + ", ".join(feature_agreement_refs),
        "Verified terminal Slices and current outcome receipts:",
    ]
    lines.extend(f"- {slice_ref}: {outcome_ref}" for slice_ref, outcome_ref in slice_outcomes)
    lines.append(
        "Schema 19 retained; no automatic legacy migration"
        if schema19_retained else
        "Schema 18 retained; no automatic legacy migration"
        if previous_closure_exists else
        "Schema conversion: 17 to 18 on first closure; no automatic legacy migration"
    )
    lines.append("This does not authorize model execution, merge, tags, release, or reopening")
    return "\n".join(lines)


def slice_outcome_semantic_id(receipt: SliceOutcomeReceipt) -> str:
    """Content-address semantic outcome, excluding evidence IDs and revision noise."""
    values = [
        receipt.repository_id,
        receipt.slice_agreement_ref,
        receipt.criteria_revision_id,
        receipt.input_fingerprint,
        receipt.input_policy_id,
        receipt.input_policy_digest,
        receipt.admission_policy_id,
        receipt.admission_policy_digest,
        receipt.relevant_drift_digest,
        receipt.coverage_contract_digest,
        receipt.basis,
        receipt.source,
    ]
    return "slice-outcome-" + sha256(json.dumps(
        values, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:20]


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
class TaskProgress:
    """One unverified operational report, never acceptance of completed value."""

    task_id: str
    checkpoint: str
    statement: str
    report_kind: str
    source_kind: str
    source_reference: str | None
    evidence_refs: tuple[str, ...]
    recorded_revision: int


@dataclass(frozen=True, slots=True)
class E1SchemaUpgradeReceipt:
    """Durable proof of exactly which old E1 authority the human converted."""

    source_revision: int
    converted_revision: int
    source_sha256: str
    statement: str
    source: str
    source_reference: str
    repository_id: str
    feature_agreement_ref: str
    slice_agreement_ref: str


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
    feature_agreement_ref: str | None = None
    slice_agreement_ref: str | None = None
    task_progress: TaskProgress | None = None


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
    agreements: tuple[AgreementRecord, ...] = ()
    e1_schema_upgrade: E1SchemaUpgradeReceipt | None = None
    slice_criteria_revisions: tuple[SliceCriteriaRevision, ...] = ()
    slice_criteria_heads: tuple[SliceCriteriaHead, ...] = ()
    r3_schema_upgrade: R3SchemaUpgradeReceipt | None = None
    criterion_test_receipts: tuple[CriterionTestReceipt, ...] = ()
    r3b_schema_upgrade: R3BSchemaUpgradeReceipt | None = None
    slice_outcome_receipts: tuple[SliceOutcomeReceipt, ...] = ()
    criterion_coverage_approvals: tuple[CriterionCoverageApproval, ...] = ()
    feature_completion_receipts: tuple[FeatureCompletionReceipt, ...] = ()
    feature_reopen_receipts: tuple[FeatureReopenReceipt, ...] = ()
    provisional_slice_revisions: tuple[ProvisionalSliceRevision, ...] = ()
    provisional_slice_heads: tuple[ProvisionalSliceHead, ...] = ()
    provisional_slice_activation: ProvisionalSliceActivation | None = None
    provisional_slice_adoptions: tuple[ProvisionalSliceAdoption, ...] = ()


IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_GIT_REF_FORBIDDEN = re.compile(r"[\x00-\x20\x7f ~^:?*\\\[]")
_GIT_EMAIL = re.compile(r"[^\s<>@]+@[^\s<>@]+\Z")


class StateValidationError(Exception):
    """Raised when state relationships are incomplete or inconsistent."""


class ContinuityError(DomainError):
    """Stable failure for the opt-in document-backed continuity mode."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


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
    if context.task_progress is not None:
        report = context.task_progress
        if type(report) is not TaskProgress:
            raise StateValidationError("Task progress must use the typed E1 record")
        for value, label in (
            (report.task_id, "Task progress id"),
            (report.checkpoint, "Task progress checkpoint"),
            (report.statement, "Task progress statement"),
        ):
            if type(value) is not str or not value.strip() or len(value) > 2048:
                raise StateValidationError(f"{label} must be bounded nonblank text")
        if report.report_kind != "REPORTED_UNVERIFIED" or report.source_kind not in ("UNKNOWN", "AGENT_REPORTED"):
            raise StateValidationError("Task progress cannot claim verification or host attestation")
        if report.source_reference is not None:
            raise StateValidationError("unattested Task progress cannot claim a source reference")
        if (type(report.evidence_refs) is not tuple or len(report.evidence_refs) > 16
                or len(set(report.evidence_refs)) != len(report.evidence_refs)
                or any(type(item) is not str or not item.strip() or len(item) > 2048 for item in report.evidence_refs)):
            raise StateValidationError("Task progress evidence references must be bounded leads")
        if type(report.recorded_revision) is not int or report.recorded_revision < 1:
            raise StateValidationError("Task progress revision is invalid")
        if context.resume_point is not None:
            raise StateValidationError("E1 Task progress cannot duplicate its checkpoint in resume_point")


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


def _validate_d4_provisional_slice_state(state: State) -> None:
    if type(state.provisional_slice_revisions) is not tuple or type(state.provisional_slice_heads) is not tuple:
        raise StateValidationError("provisional Slice authority must be immutable tuples")
    if any(type(item) is not ProvisionalSliceRevision for item in state.provisional_slice_revisions):
        raise StateValidationError("provisional Slice revision shape is invalid")
    if any(type(item) is not ProvisionalSliceHead for item in state.provisional_slice_heads):
        raise StateValidationError("provisional Slice head shape is invalid")
    if type(state.provisional_slice_adoptions) is not tuple or any(
        type(item) is not ProvisionalSliceAdoption
        for item in state.provisional_slice_adoptions
    ):
        raise StateValidationError("D5 provisional Slice adoptions must be immutable typed records")
    _unique((item.revision_id for item in state.provisional_slice_revisions), "provisional Slice revision")
    _unique((item.proposal_id for item in state.provisional_slice_heads), "provisional Slice head")
    _unique((item.id for item in state.provisional_slice_adoptions), "D5 provisional Slice adoption")
    activation = state.provisional_slice_activation
    if activation is not None:
        _validate_d4_activation_metadata(state, activation)
        _validate_d4_activation_source(
            state, activation, adopted=bool(state.provisional_slice_adoptions),
        )
    if state.provisional_slice_adoptions:
        if len(state.provisional_slice_adoptions) != 1:
            raise StateValidationError("D5 permits exactly one provisional Slice adoption")
        _validate_d5_provisional_slice_adoption(state, state.provisional_slice_adoptions[0])


def _validate_d4_activation_metadata(
    state: State, activation: ProvisionalSliceActivation,
) -> None:
    if (
        type(activation) is not ProvisionalSliceActivation
        or activation.marker != D4_PROVISIONAL_SLICE_ACTIVATION_MARKER
        or type(activation.source_schema) is not int
        or activation.source_schema != 15
        or type(activation.source_revision) is not int
        or activation.source_revision != 2
        or type(activation.recorded_revision) is not int
        or activation.recorded_revision != activation.source_revision + 1
        or activation.recorded_revision > state.revision
        or type(activation.source_sha256) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", activation.source_sha256)
        or type(activation.repository_id) is not str
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", activation.repository_id)
        or type(activation.feature_id) is not str
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", activation.feature_id)
        or type(activation.feature_agreement_ref) is not str
        or type(activation.slice_agreement_ref) is not str
        or not activation.feature_agreement_ref
        or not activation.slice_agreement_ref
        or state.initialization is None
        or state.initialization.repository_id != activation.repository_id
    ):
        raise StateValidationError("D4 schema-15 provisional Slice activation is invalid")


def _validate_d4_activation_source(
    state: State, activation: ProvisionalSliceActivation, *, adopted: bool = False,
) -> None:
    feature_agreement = next(
        (item for item in state.agreements if item.id == activation.feature_agreement_ref),
        None,
    )
    slice_agreement = next(
        (item for item in state.agreements if item.id == activation.slice_agreement_ref),
        None,
    )
    source_reference = (
        None if slice_agreement is None
        else re.fullmatch(
            r"session:(ses_[A-Za-z0-9_-]{3,128}):(msg_[A-Za-z0-9_-]{3,128}):([0-9a-f]{16})",
            slice_agreement.source_reference,
        )
    )
    if (
        len(state.agreements) != (4 if adopted else 2)
        or feature_agreement is None or feature_agreement.kind != "FEATURE"
        or slice_agreement is None or slice_agreement.kind != "SLICE"
        or feature_agreement.feature_id != activation.feature_id
        or slice_agreement.feature_id != activation.feature_id
        or feature_agreement.change_id != slice_agreement.change_id
        or feature_agreement.repository_id != activation.repository_id
        or slice_agreement.repository_id != activation.repository_id
        or feature_agreement.source != "SESSION_REQUEST"
        or slice_agreement.source != "SESSION_REQUEST"
        or feature_agreement.statement != slice_agreement.statement
        or feature_agreement.source_reference != slice_agreement.source_reference
        or not feature_agreement.statement
        or not feature_agreement.source_reference
        or source_reference is None
        or source_reference.group(3) != sha256(
            feature_agreement.statement.encode("utf-8")
        ).hexdigest()[:16]
        or feature_agreement.title == ""
        or slice_agreement.initial_task != SESSION_REQUEST_TASK
        or slice_agreement.checkpoint != SESSION_REQUEST_CHECKPOINT
    ):
        raise StateValidationError("D4 activation is not bound to the initial session-request Feature")


def _validate_d5_provisional_slice_adoption(
    state: State, adoption: ProvisionalSliceAdoption,
) -> None:
    """Tie one accepted D4 head to its exact immutable consent and Task records."""
    revisions = {item.revision_id: item for item in state.provisional_slice_revisions}
    proposal = revisions.get(adoption.proposal_revision_id)
    head = next(
        (item for item in state.provisional_slice_heads if item.proposal_id == adoption.proposal_id),
        None,
    )
    activation = state.provisional_slice_activation
    if (
        activation is None
        or proposal is None
        or head is None
        or proposal.provenance.kind != "AGENT_SUGGESTION"
        or proposal.proposal_id != adoption.proposal_id
        or proposal.revision != adoption.proposal_revision
        or proposal.digest != adoption.proposal_digest
        or head.revision_id != proposal.revision_id
        or head.revision != proposal.revision
        or head.digest != proposal.digest
        or adoption.repository_id != activation.repository_id
        or adoption.feature_id != activation.feature_id
        or adoption.source_schema != 23
        or type(adoption.source_revision) is not int
        or adoption.source_revision < activation.recorded_revision
        or adoption.recorded_revision != adoption.source_revision + 1
        or adoption.recorded_revision > state.revision
        or not is_sha256_digest(adoption.source_state_sha256)
        or adoption.source != "INTERACTIVE_TERMINAL"
        or type(adoption.statement) is not str
    ):
        raise StateValidationError("D5 adoption does not bind the exact current provisional head")
    if (
        type(adoption.question_answers) is not tuple
        or any(
            type(item) is not tuple or len(item) != 2
            or any(type(value) is not str or not value.strip() for value in item)
            for item in adoption.question_answers
        )
        or len(adoption.question_answers) != len(proposal.open_questions)
        or tuple(item[0] for item in adoption.question_answers) != proposal.open_questions
        or type(adoption.criteria) is not tuple
        or len(adoption.criteria) != len(proposal.criteria)
        or any(type(item) is not SliceCriterion for item in adoption.criteria)
        or tuple(
            (item.id, item.expected_result, item.expected_evidence)
            for item in adoption.criteria
        ) != tuple(
            (item.id, item.expected_result, item.expected_evidence)
            for item in proposal.criteria
        )
    ):
        raise StateValidationError("D5 adoption questions or criteria do not map to the selected suggestion")
    if any(
        type(values) is not tuple
        for values in (adoption.inclusions, adoption.exclusions, adoption.decisions)
    ):
        raise StateValidationError("D5 accepted scope must use immutable tuples")
    text_values = (
        adoption.slice_title, adoption.initial_task, adoption.checkpoint,
        adoption.outcome, adoption.example, *adoption.inclusions,
        *adoption.exclusions, *adoption.decisions,
        *(pair[1] for pair in adoption.question_answers),
        *(value for item in adoption.criteria
          for value in (item.id, item.expected_result, item.expected_evidence)),
    )
    if (
        any(
            type(value) is not str
            or not value.strip()
            or value != value.strip()
            or len(value.encode("utf-8")) > 2048
            for value in text_values
        )
        or type(adoption.inclusions) is not tuple
        or type(adoption.exclusions) is not tuple
        or type(adoption.decisions) is not tuple
        or not adoption.inclusions
        or not adoption.exclusions
        or not adoption.decisions
        or not adoption.criteria
    ):
        raise StateValidationError("D5 accepted mini-spec is not normalized or bounded")
    try:
        if any(
            len(question.encode("utf-8")) > 2048
            for question, _ in adoption.question_answers
        ):
            raise StateValidationError("D5 proposal question exceeds 2 KiB")
    except UnicodeEncodeError as error:
        raise StateValidationError("D5 proposal question is not valid UTF-8") from error
    expected_statement = provisional_slice_adoption_statement_text(
        repository_id=adoption.repository_id,
        feature_id=str(adoption.feature_id),
        source_revision=adoption.source_revision,
        source_state_sha256=adoption.source_state_sha256,
        proposal=proposal,
        slice_title=adoption.slice_title,
        initial_task=adoption.initial_task,
        checkpoint=adoption.checkpoint,
        outcome=adoption.outcome,
        example=adoption.example,
        inclusions=adoption.inclusions,
        exclusions=adoption.exclusions,
        decisions=adoption.decisions,
        criteria=adoption.criteria,
        question_answers=adoption.question_answers,
    )
    if (
        adoption.statement != expected_statement
        or adoption.source_reference != (
            "tty:" + sha256(adoption.statement.encode("utf-8")).hexdigest()[:12]
        )
        or adoption.id != "d5-adoption-" + sha256(adoption.statement.encode("utf-8")).hexdigest()
    ):
        raise StateValidationError("D5 acceptance statement does not match its exact adopted specification")
    identity_digest = provisional_slice_adoption_identity_digest(
        repository_id=adoption.repository_id,
        feature_id=str(adoption.feature_id),
        proposal_id=adoption.proposal_id,
        proposal_revision_id=adoption.proposal_revision_id,
        proposal_revision=adoption.proposal_revision,
        proposal_digest=adoption.proposal_digest,
        source_revision=adoption.source_revision,
        source_state_sha256=adoption.source_state_sha256,
        statement=adoption.statement,
        source_reference=adoption.source_reference,
    )
    if (
        adoption.feature_agreement_ref != f"agreement-feature-{identity_digest}"
        or adoption.slice_agreement_ref != f"agreement-slice-{identity_digest}"
        or adoption.change_id != f"change-{identity_digest}"
        or adoption.criteria_revision_id != f"slice-criteria-{identity_digest}-1"
        or adoption.task_id != f"task-{identity_digest}"
    ):
        raise StateValidationError("D5 accepted identities do not match the consent binding")
    original_feature = next(
        (item for item in state.agreements if item.id == activation.feature_agreement_ref),
        None,
    )
    original_slice = next(
        (item for item in state.agreements if item.id == activation.slice_agreement_ref),
        None,
    )
    feature_agreement = next(
        (item for item in state.agreements if item.id == adoption.feature_agreement_ref),
        None,
    )
    slice_agreement = next(
        (item for item in state.agreements if item.id == adoption.slice_agreement_ref),
        None,
    )
    feature = next((item for item in state.features if item.id == adoption.feature_id), None)
    change = next((item for item in state.changes if item.id == adoption.change_id), None)
    criteria_revision = next(
        (item for item in state.slice_criteria_revisions if item.id == adoption.criteria_revision_id),
        None,
    )
    if (
        original_feature is None
        or original_slice is None
        or feature is None
        or change is None
        or feature_agreement is None
        or slice_agreement is None
        or criteria_revision is None
        or len(state.features) != 1
        or len(state.changes) != 2
        or len(state.agreements) != 4
        or state.agreements[:2] != (original_feature, original_slice)
        or state.agreements[2:] != (feature_agreement, slice_agreement)
        or state.changes[0].id != original_feature.change_id
        or change.feature_id != feature.id
        or change.intent != adoption.slice_title
        or change.classification is not Classification.FEATURE
        or feature_agreement.kind != "FEATURE"
        or feature_agreement.feature_id != feature.id
        or feature_agreement.change_id != change.id
        or feature_agreement.title != feature.title
        or feature_agreement.statement != adoption.statement
        or feature_agreement.source != adoption.source
        or feature_agreement.source_reference != adoption.source_reference
        or feature_agreement.repository_id != adoption.repository_id
        or any(value is not None for value in (
            feature_agreement.task_id,
            feature_agreement.initial_task,
            feature_agreement.checkpoint,
        ))
        or slice_agreement.kind != "SLICE"
        or slice_agreement.feature_id != feature.id
        or slice_agreement.change_id != change.id
        or slice_agreement.title != adoption.slice_title
        or slice_agreement.statement != adoption.statement
        or slice_agreement.source != adoption.source
        or slice_agreement.source_reference != adoption.source_reference
        or slice_agreement.repository_id != adoption.repository_id
        or slice_agreement.task_id != adoption.task_id
        or slice_agreement.initial_task != adoption.initial_task
        or slice_agreement.checkpoint != adoption.checkpoint
        or any(
            context.current_work != adoption.initial_task
            or context.focus != adoption.slice_title
            for context in (
                state.active_context,
                *(item.context for item in state.terminal_archives),
            )
            if context is not None and context.slice_agreement_ref == adoption.slice_agreement_ref
        )
        or criteria_revision.slice_agreement_ref != slice_agreement.id
        or criteria_revision.repository_id != adoption.repository_id
        or criteria_revision.outcome != adoption.outcome
        or criteria_revision.example != adoption.example
        or criteria_revision.inclusions != adoption.inclusions
        or criteria_revision.exclusions != adoption.exclusions
        or criteria_revision.decisions != adoption.decisions
        or criteria_revision.criteria != adoption.criteria
        or criteria_revision.source != adoption.source
        or criteria_revision.source_reference != adoption.source_reference
        or criteria_revision.previous_revision_id is not None
        or criteria_revision.reason is not None
        or criteria_revision.decision_statement is not None
        or state.slice_criteria_revisions != (criteria_revision,)
        or state.slice_criteria_heads != (SliceCriteriaHead(slice_agreement.id, criteria_revision.id),)
        or state.criterion_test_receipts
        or state.slice_outcome_receipts
        or state.criterion_coverage_approvals
        or state.feature_completion_receipts
        or state.feature_reopen_receipts
    ):
        raise StateValidationError("D5 adoption records are inconsistent with accepted Core authority")


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
    if type(state.agreements) is not tuple or any(
        type(agreement) is not AgreementRecord for agreement in state.agreements
    ):
        raise StateValidationError("agreement records must be immutable tuples")
    _unique((agreement.id for agreement in state.agreements), "agreement")
    for agreement in state.agreements:
        _identity(agreement.id, "agreement")
        if agreement.kind not in ("FEATURE", "SLICE"):
            raise StateValidationError("agreement kind is invalid")
        for value in (agreement.title, agreement.statement, agreement.source,
                      agreement.source_reference, agreement.repository_id):
            _text(value, "agreement provenance")
        if agreement.source == "SESSION_REQUEST":
            # A session message is not a human signature. Verify only the
            # internal binding of recorded text to session/message identity;
            # the OpenCode adapter must fetch the actual admitted message.
            match = re.fullmatch(
                r"session:(ses_[A-Za-z0-9_-]{3,128}):(msg_[A-Za-z0-9_-]{3,128}):([0-9a-f]{16})",
                agreement.source_reference,
            )
            if (match is None or match.group(3) != sha256(agreement.statement.encode("utf-8")).hexdigest()[:16]):
                raise StateValidationError("session request agreement has invalid message provenance")
        elif agreement.source == "INTERACTIVE_TERMINAL":
            # Legacy terminal receipts are cryptographically bound to their
            # own canonical statement, not interchangeable with SESSION_REQUEST.
            expected_ref = "tty:" + sha256(agreement.statement.encode("utf-8")).hexdigest()[:12]
            if agreement.source_reference != expected_ref:
                raise StateValidationError("terminal agreement reference does not match its statement")
        else:
            raise StateValidationError("agreement provenance must identify an available channel")
        if initialization is None or agreement.repository_id != initialization.repository_id:
            raise StateValidationError("agreement repository identity is not authoritative")
        if agreement.kind == "FEATURE" and any(
            value is not None for value in (agreement.task_id, agreement.initial_task, agreement.checkpoint)
        ):
            raise StateValidationError("Feature agreement cannot own Slice task details")
        if agreement.kind == "SLICE" and (
            not agreement.task_id or not agreement.initial_task or not agreement.checkpoint
        ):
            raise StateValidationError("Slice agreement requires its initial Task and checkpoint")
    # Both records must refer to the same admitted request. A model-proposed
    # title is never independently authorized to widen its original meaning.
    for agreement in state.agreements:
        if agreement.source != "SESSION_REQUEST" or agreement.kind != "SLICE":
            continue
        owner = next((item for item in state.agreements if item.kind == "FEATURE"
                      and item.feature_id == agreement.feature_id
                      and item.change_id == agreement.change_id), None)
        if (owner is None or owner.source != "SESSION_REQUEST"
                or owner.source_reference != agreement.source_reference
                or owner.statement != agreement.statement
                # The exact source statement retains user formatting.
                # Only display titles may trim outer whitespace; internal
                # line breaks and the original source digest remain untouched.
                or owner.title != agreement.statement.strip()
                or agreement.title != agreement.statement.strip()):
            raise StateValidationError("SESSION_REQUEST Slice must preserve exact original user scope")
    receipt = state.e1_schema_upgrade
    if receipt is not None:
        if type(receipt) is not E1SchemaUpgradeReceipt:
            raise StateValidationError("E1 schema upgrade receipt is not typed authority")
        if (type(receipt.source_revision) is not int or receipt.source_revision < 1
                or type(receipt.converted_revision) is not int
                or receipt.converted_revision != receipt.source_revision + 1
                or receipt.converted_revision > state.revision
                or type(receipt.source_sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", receipt.source_sha256) is None):
            raise StateValidationError("E1 schema upgrade source identity is invalid")
        # The historical E1 receipt authorizes its original pair, not a permanent
        # limit on how many later Slice agreements the repository may accept.
        if (initialization is None or receipt.repository_id != initialization.repository_id
                or len(state.agreements) < 2):
            raise StateValidationError("E1 schema upgrade receipt does not bind accepted authority")
        feature = next((item for item in state.agreements if item.id == receipt.feature_agreement_ref), None)
        slice_record = next((item for item in state.agreements if item.id == receipt.slice_agreement_ref), None)
        if feature is None or slice_record is None or feature.kind != "FEATURE" or slice_record.kind != "SLICE":
            raise StateValidationError("E1 schema upgrade receipt must bind the exact Feature and Slice")
        statement = (
            f"Autorizo migrar E1 a schema 14; repository: {receipt.repository_id}; "
            f"revision: {receipt.source_revision}; Feature: {feature.id}; Slice: {slice_record.id}; "
            f"Task: {slice_record.task_id}; source-sha256: {receipt.source_sha256}"
        )
        if (receipt.statement != statement or receipt.source != "INTERACTIVE_TERMINAL"
                or receipt.source_reference != f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}"):
            raise StateValidationError("E1 schema upgrade human approval evidence is invalid")
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
    agreement_index = {agreement.id: agreement for agreement in state.agreements}
    for agreement in state.agreements:
        if (
            agreement.feature_id not in records.features
            or agreement.change_id not in records.changes
            or records.changes[agreement.change_id].feature_id != agreement.feature_id
        ):
            raise StateValidationError("agreement must own an authoritative Feature and Change")
    agreement_contexts = tuple(
        context for context in (state.active_context, state.preserved_primary_context)
        if context is not None
    ) + tuple(
        context for item in state.work_items
        for context in (item.context, item.preserved_primary_context)
        if context is not None
    ) + tuple(archive.context for archive in state.terminal_archives)
    for context in agreement_contexts:
        feature_ref, slice_ref = context.feature_agreement_ref, context.slice_agreement_ref
        if (feature_ref is None) != (slice_ref is None):
            raise StateValidationError("Feature and Slice agreement references must be paired")
        if feature_ref is not None and slice_ref is not None:
            feature_agreement = agreement_index.get(feature_ref)
            slice_agreement = agreement_index.get(slice_ref)
            if (
                feature_agreement is None or slice_agreement is None
                or feature_agreement.kind != "FEATURE" or slice_agreement.kind != "SLICE"
                or feature_agreement.feature_id != context.feature_id
                or slice_agreement.feature_id != context.feature_id
                or slice_agreement.change_id != context.change_id
                or feature_agreement.change_id != context.change_id
            ):
                raise StateValidationError("context agreement references are not authoritative")
        if context.task_progress is not None:
            report = context.task_progress
            slice_agreement = agreement_index.get(slice_ref) if slice_ref is not None else None
            if (
                slice_agreement is None
                or slice_agreement.task_id != report.task_id
                or report.recorded_revision > state.revision
            ):
                raise StateValidationError("Task progress does not belong to the accepted Slice")
    for agreement in state.agreements:
        if agreement.kind == "SLICE" and not any(
            context.slice_agreement_ref == agreement.id for context in agreement_contexts
        ):
            raise StateValidationError("Slice agreement has no owning context")
    if type(state.slice_criteria_revisions) is not tuple or type(state.slice_criteria_heads) is not tuple:
        raise StateValidationError("Slice criteria authority must be immutable tuples")
    if any(type(item) is not SliceCriteriaRevision for item in state.slice_criteria_revisions):
        raise StateValidationError("Slice criteria revision shape is invalid")
    if any(type(item) is not SliceCriteriaHead for item in state.slice_criteria_heads):
        raise StateValidationError("Slice criteria head shape is invalid")
    _unique((revision.id for revision in state.slice_criteria_revisions), "Slice criteria revision")
    _unique((head.slice_agreement_ref for head in state.slice_criteria_heads), "Slice criteria head")
    revision_index = {revision.id: revision for revision in state.slice_criteria_revisions}
    for revision in state.slice_criteria_revisions:
        _identity(revision.id, "Slice criteria revision")
        agreement = agreement_index.get(revision.slice_agreement_ref)
        if agreement is None or agreement.kind != "SLICE" or revision.repository_id != agreement.repository_id:
            raise StateValidationError("Slice criteria must be bound to the accepted Slice and repository")
        for value in (revision.outcome, revision.example, revision.source, revision.source_reference):
            _text(value, "Slice criteria content or provenance")
        for name, values in (("inclusions", revision.inclusions), ("exclusions", revision.exclusions),
                             ("decisions", revision.decisions), ("criteria", revision.criteria)):
            if type(values) is not tuple or not values:
                raise StateValidationError(f"Slice criteria {name} must be a nonempty tuple")
        if any(type(value) is not str or not value.strip() for value in
               revision.inclusions + revision.exclusions + revision.decisions):
            raise StateValidationError("Slice criteria scope and decisions must be nonblank text")
        _unique((item.id for item in revision.criteria), "Slice criterion")
        for criterion in revision.criteria:
            if type(criterion) is not SliceCriterion:
                raise StateValidationError("Slice criterion shape is invalid")
            _identity(criterion.id, "Slice criterion")
            _text(criterion.expected_result, "expected result")
            _text(criterion.expected_evidence, "expected evidence")
            if criterion.provenance not in (None, "EXPLICIT_REQUEST", "DERIVED_OBJECTIVE"):
                raise StateValidationError("Slice criterion provenance is invalid")
            if criterion.provenance is not None and revision.source != REQUEST_SCOPED_OBJECTIVE:
                raise StateValidationError("criterion provenance is reserved for Core-derived request objectives")
        # Reconstruct the exact mini-spec that the human saw. A changed JSON
        # record must not silently retain the old terminal authorization.
        canonical_spec = json.dumps({
            "outcome": revision.outcome, "example": revision.example,
            "inclusions": list(revision.inclusions), "exclusions": list(revision.exclusions),
            "decisions": list(revision.decisions),
            "criteria": [{"id": item.id, "expected_result": item.expected_result,
                          "expected_evidence": item.expected_evidence} for item in revision.criteria],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if revision.previous_revision_id is None:
            if revision.source == REQUEST_SCOPED_OBJECTIVE:
                expected_objective = request_objective_criteria_revision(
                    revision.slice_agreement_ref,
                    revision.repository_id,
                    revision.source_reference,
                    agreement.statement,
                )
                owners = tuple(
                    item for item in state.agreements
                    if item.kind == "FEATURE"
                    and item.feature_id == agreement.feature_id
                    and item.change_id == agreement.change_id
                )
                if (
                    expected_objective is None
                    or revision != expected_objective
                    or agreement.source != "SESSION_REQUEST"
                    or len(owners) != 1
                    or owners[0].source != "SESSION_REQUEST"
                    or owners[0].statement != agreement.statement
                    or owners[0].source_reference != agreement.source_reference
                ):
                    raise StateValidationError(
                        "request-scoped criteria must be the exact Core-derived objective"
                    )
                continue
            initial = revision.reason is None and revision.source == "INTERACTIVE_TERMINAL"
            legacy_first_amendment = bool(revision.reason) and revision.source == "INTERACTIVE_TERMINAL_DECISION"
            if not (initial or legacy_first_amendment):
                raise StateValidationError("first criteria require initial acceptance or a separate decision")
            if initial:
                owning_feature = next(
                    (item for item in state.agreements
                     if item.kind == "FEATURE" and item.feature_id == agreement.feature_id
                     and item.change_id == agreement.change_id),
                    None,
                )
                adoption = next(
                    (
                        item for item in state.provisional_slice_adoptions
                        if item.slice_agreement_ref == agreement.id
                    ),
                    None,
                )
                if adoption is not None:
                    if (
                        owning_feature is None
                        or revision.id != adoption.criteria_revision_id
                        or revision.source_reference != adoption.source_reference
                        or agreement.statement != adoption.statement
                        or owning_feature.statement != adoption.statement
                        or owning_feature.source_reference != adoption.source_reference
                    ):
                        raise StateValidationError("D5 criteria must match their exact accepted adoption")
                    continue
                first_statement = (
                    f"Acepto Feature: {owning_feature.title if owning_feature is not None else ''}; "
                    f"Slice: {agreement.title}; Task: {agreement.initial_task}; "
                    f"checkpoint: {agreement.checkpoint}; criterios: {canonical_spec}"
                )
                next_statement = (
                    f"Acepto siguiente Slice de Feature-ID: {agreement.feature_id}; "
                    f"Feature: {owning_feature.title if owning_feature is not None else ''}; "
                    f"Slice: {agreement.title}; Task: {agreement.initial_task}; "
                    f"checkpoint: {agreement.checkpoint}; criterios: {canonical_spec}"
                )
                if (
                    owning_feature is None
                    or revision.decision_statement is not None
                    or revision.source_reference != agreement.source_reference
                    or revision.id != f"slice-criteria-{agreement.id.removeprefix('agreement-slice-')}-1"
                    or agreement.statement not in (first_statement, next_statement)
                    or owning_feature.statement != agreement.statement
                    or owning_feature.source_reference != agreement.source_reference
                ):
                    raise StateValidationError("initial criteria must match the exact accepted agreement")
        else:
            predecessor = revision_index.get(revision.previous_revision_id)
            if predecessor is None or predecessor.slice_agreement_ref != revision.slice_agreement_ref:
                raise StateValidationError("Slice criteria predecessor is missing or belongs elsewhere")
            if predecessor.source == REQUEST_SCOPED_OBJECTIVE:
                raise StateValidationError("request-objective criteria are not amendable by decision")
            if not revision.reason or revision.source != "INTERACTIVE_TERMINAL_DECISION":
                raise StateValidationError("criteria amendment requires an attributable separate decision")
        if revision.source == "INTERACTIVE_TERMINAL_DECISION":
            previous = revision.previous_revision_id or "NOT_RECORDED"
            statement = f"Decido revisar criterios desde {previous}; motivo: {revision.reason}; criterios: {canonical_spec}"
            expected_ref = f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}"
            digest = sha256(json.dumps([
                revision.repository_id, revision.slice_agreement_ref, previous, statement, expected_ref,
            ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
            if (
                revision.decision_statement != statement
                or revision.source_reference != expected_ref
                or revision.id != f"slice-criteria-{digest}"
            ):
                raise StateValidationError("amended criteria must match the exact recorded human decision")
    reachable: set[str] = set()
    for head in state.slice_criteria_heads:
        revision = revision_index.get(head.revision_id)
        if revision is None or revision.slice_agreement_ref != head.slice_agreement_ref:
            raise StateValidationError("Slice criteria head must target its accepted Slice")
        while revision is not None:
            if revision.id in reachable:
                raise StateValidationError("Slice criteria revision history must be linear and acyclic")
            reachable.add(revision.id)
            revision = revision_index.get(revision.previous_revision_id) if revision.previous_revision_id else None
    if reachable != set(revision_index):
        raise StateValidationError("Slice criteria history must be reachable from Core-owned heads")
    _validate_d4_provisional_slice_state(state)
    receipt = state.r3_schema_upgrade
    if receipt is not None:
        if type(receipt) is not R3SchemaUpgradeReceipt or state.initialization is None:
            raise StateValidationError("R3 schema receipt has no initialized authority")
        expected_statement = (
            f"Acepto habilitar verificaciones R3-A desde revision {receipt.source_revision}; "
            f"sha256: {receipt.source_sha256}"
        )
        if (
            receipt.source_revision < 0
            or receipt.converted_revision != receipt.source_revision + 1
            or receipt.converted_revision > state.revision
            or not re.fullmatch(r"[0-9a-f]{64}", receipt.source_sha256)
            or receipt.statement != expected_statement
            or receipt.source_reference != f"tty:{sha256(expected_statement.encode('utf-8')).hexdigest()[:12]}"
            or receipt.repository_id != state.initialization.repository_id
        ):
            raise StateValidationError("R3 schema upgrade does not match its exact human approval")
    r3b_receipt = state.r3b_schema_upgrade
    if r3b_receipt is not None:
        if type(r3b_receipt) is not R3BSchemaUpgradeReceipt:
            raise StateValidationError("R3-B schema receipt shape is invalid")
        slice_agreement = agreement_index.get(r3b_receipt.slice_agreement_ref)
        criteria_revision = revision_index.get(r3b_receipt.criteria_revision_id)
        legacy_statement = (
            f"Acepto habilitar aceptación objetiva R3-B desde revision "
            f"{r3b_receipt.source_revision}; repository: {r3b_receipt.repository_id}; "
            f"Slice: {r3b_receipt.slice_agreement_ref}; criterios: "
            f"{r3b_receipt.criteria_revision_id}; policy: {LEGACY_R3B_APPROVAL_POLICY_TEXT}; "
            f"sha256: {r3b_receipt.source_sha256}"
        )
        new_statement_prefix = (
            f"Acepto habilitar aceptación objetiva R3-B desde revision "
            f"{r3b_receipt.source_revision}; repository: {r3b_receipt.repository_id}; "
            f"Slice: {r3b_receipt.slice_agreement_ref}; criterios: "
            f"{r3b_receipt.criteria_revision_id}; policy: {R3B_APPROVAL_POLICY_TEXT}; "
            f"root-input-fingerprint: {r3b_receipt.input_fingerprint}; "
            f"coverage-contract-sha256: {r3b_receipt.coverage_contract_digest}; "
            "coverage-contract-json: "
        )
        statement_valid = (
            r3b_receipt.statement == legacy_statement
            if r3b_receipt.input_fingerprint is None
            else (
                r3b_receipt.statement.startswith(new_statement_prefix)
                and r3b_receipt.statement.endswith(f"; sha256: {r3b_receipt.source_sha256}")
            )
        )
        if (
            state.initialization is None
            or receipt is None
            or r3b_receipt.source_revision < receipt.converted_revision
            or slice_agreement is None
            or slice_agreement.kind != "SLICE"
            or slice_agreement.repository_id != r3b_receipt.repository_id
            or criteria_revision is None
            or criteria_revision.slice_agreement_ref != r3b_receipt.slice_agreement_ref
            or r3b_receipt.repository_id != state.initialization.repository_id
            or r3b_receipt.source_revision < 0
            or r3b_receipt.converted_revision != r3b_receipt.source_revision + 1
            or r3b_receipt.converted_revision > state.revision
            or not is_sha256_digest(r3b_receipt.source_sha256)
            or (
                r3b_receipt.input_fingerprint is not None
                and not is_sha256_digest(r3b_receipt.input_fingerprint)
            )
            or (
                r3b_receipt.coverage_contract_digest is not None
                and not is_sha256_digest(r3b_receipt.coverage_contract_digest)
            )
            or (r3b_receipt.input_fingerprint is None)
            != (r3b_receipt.coverage_contract_digest is None)
            or not statement_valid
            or r3b_receipt.source_reference
            != f"tty:{sha256(r3b_receipt.statement.encode('utf-8')).hexdigest()[:12]}"
        ):
            raise StateValidationError("R3-B schema upgrade does not match its exact human approval")
    if type(state.criterion_coverage_approvals) is not tuple:
        raise StateValidationError("criterion coverage approvals must be immutable")
    if any(type(item) is not CriterionCoverageApproval for item in state.criterion_coverage_approvals):
        raise StateValidationError("criterion coverage approval shape is invalid")
    _unique((item.id for item in state.criterion_coverage_approvals), "criterion coverage approval")
    initialization = state.initialization
    coverage_by_id = {item.id: item for item in state.criterion_coverage_approvals}
    for approval in state.criterion_coverage_approvals:
        agreement = agreement_index.get(approval.slice_agreement_ref)
        criteria = revision_index.get(approval.criteria_revision_id)
        criterion = None if criteria is None else next(
            (item for item in criteria.criteria if item.id == approval.criterion_id), None,
        )
        if (
            r3b_receipt is None
            or initialization is None
            or agreement is None
            or agreement.kind != "SLICE"
            or agreement.repository_id != approval.repository_id
            or criteria is None
            or criteria.slice_agreement_ref != approval.slice_agreement_ref
            or criterion is None
            or criterion.expected_evidence != f"{approval.test_kind}:{approval.test_target}"
            or approval.repository_id != initialization.repository_id
            or approval.source != "INTERACTIVE_TERMINAL"
            or not approval.statement
            or approval.source_reference != f"tty:{sha256(approval.statement.encode('utf-8')).hexdigest()[:12]}"
            or approval.input_policy_id != INPUT_POLICY_ID
            or approval.input_policy_digest != INPUT_POLICY_DIGEST
            or approval.admission_policy_id != TEST_ADMISSION_POLICY_ID
            or approval.admission_policy_digest != TEST_ADMISSION_POLICY_DIGEST
            or approval.environment_profile_id != TEST_ENVIRONMENT_PROFILE_ID
            or approval.environment_profile_policy_digest != TEST_ENVIRONMENT_PROFILE_DIGEST
            or not is_sha256_digest(approval.root_fingerprint)
            or not is_sha256_digest(approval.contract_digest)
            or approval.contract_digest != coverage_semantic_digest(approval)
            or approval.id != f"coverage-{approval.contract_digest[:20]}"
            or type(approval.known_inputs) is not tuple
            or len(approval.known_inputs) > 1024
            or len({(item.kind, item.path) for item in approval.known_inputs}) != len(approval.known_inputs)
            or any(
                type(item) is not CoverageInput
                or item.kind not in ("root-file", "root-directory")
                or not item.path
                or len(item.path) > 1024
                or "\\" in item.path
                or ":" in item.path
                or item.path.startswith("/")
                or any(part in ("", ".", "..") for part in item.path.split("/"))
                or item.path.split("/", 1)[0].casefold() in (".git", ".evospec")
                or unicodedata.normalize("NFC", item.path) != item.path
                for item in approval.known_inputs
            )
            or approval.recorded_revision < r3b_receipt.converted_revision
            or approval.recorded_revision != approval.source_revision + 1
            or approval.recorded_revision > state.revision
            or f"repository: {approval.repository_id};" not in approval.statement
            or f"Slice: {approval.slice_agreement_ref};" not in approval.statement
            or f"criterios: {approval.criteria_revision_id};" not in approval.statement
            or f"root-input-fingerprint: {approval.root_fingerprint};" not in approval.statement
            or f"policy: {R3B_APPROVAL_POLICY_TEXT};" not in approval.statement
            or (
                approval.statement != r3b_receipt.statement
                and f"Acepto renovar cobertura R3-B desde revision {approval.source_revision};"
                not in approval.statement
            )
        ):
            raise StateValidationError("criterion coverage approval is not attributable or supported")
    if r3b_receipt is not None and r3b_receipt.coverage_contract_digest is not None:
        initial_approvals = tuple(
            item for item in state.criterion_coverage_approvals
            if item.source_revision == r3b_receipt.source_revision
            and item.recorded_revision == r3b_receipt.converted_revision
            and item.statement == r3b_receipt.statement
            and item.source_reference == r3b_receipt.source_reference
            and item.root_fingerprint == r3b_receipt.input_fingerprint
            and item.slice_agreement_ref == r3b_receipt.slice_agreement_ref
            and item.criteria_revision_id == r3b_receipt.criteria_revision_id
        )
        initial_revision = revision_index[r3b_receipt.criteria_revision_id]
        if (
            len(initial_approvals) != len(initial_revision.criteria)
            or {item.criterion_id for item in initial_approvals}
            != {item.id for item in initial_revision.criteria}
            or not initial_approvals
            or sha256(coverage_declarations_json(tuple(
                CriterionCoverageDeclaration(
                    item.criterion_id, item.test_kind, item.test_target, item.known_inputs,
                )
                for item in initial_approvals
            )).encode("utf-8")).hexdigest() != r3b_receipt.coverage_contract_digest
        ):
            raise StateValidationError("R3-B migration lacks its exact approved criterion coverage contracts")
    approval_groups: dict[tuple[str, str, str, int, int], list[CriterionCoverageApproval]] = {}
    for approval in state.criterion_coverage_approvals:
        key = (
            approval.statement,
            approval.slice_agreement_ref,
            approval.criteria_revision_id,
            approval.source_revision,
            approval.recorded_revision,
        )
        approval_groups.setdefault(key, []).append(approval)
    for approvals in approval_groups.values():
        assert r3b_receipt is not None
        first_approval = approvals[0]
        covered_criteria = revision_index[first_approval.criteria_revision_id].criteria
        declarations = tuple(
            CriterionCoverageDeclaration(
                item.criterion_id, item.test_kind, item.test_target, item.known_inputs,
            )
            for item in approvals
        )
        contract_json = coverage_declarations_json(declarations)
        contract_digest = sha256(contract_json.encode("utf-8")).hexdigest()
        if first_approval.statement == r3b_receipt.statement:
            expected_statement = (
                f"Acepto habilitar aceptación objetiva R3-B desde revision "
                f"{r3b_receipt.source_revision}; repository: {r3b_receipt.repository_id}; "
                f"Slice: {r3b_receipt.slice_agreement_ref}; criterios: "
                f"{r3b_receipt.criteria_revision_id}; policy: {R3B_APPROVAL_POLICY_TEXT}; "
                f"root-input-fingerprint: {r3b_receipt.input_fingerprint}; "
                f"coverage-contract-sha256: {contract_digest}; "
                f"coverage-contract-json: {contract_json}; "
                f"sha256: {r3b_receipt.source_sha256}"
            )
        else:
            state_digest = re.search(
                r"state-sha256: ([0-9a-f]{64})$", first_approval.statement,
            )
            expected_statement = (
                None if state_digest is None else
                f"Acepto renovar cobertura R3-B desde revision {first_approval.source_revision}; "
                f"repository: {first_approval.repository_id}; "
                f"Slice: {first_approval.slice_agreement_ref}; "
                f"criterios: {first_approval.criteria_revision_id}; "
                f"policy: {R3B_APPROVAL_POLICY_TEXT}; "
                f"root-input-fingerprint: {first_approval.root_fingerprint}; "
                f"coverage-contract-sha256: {contract_digest}; "
                f"coverage-contract-json: {contract_json}; "
                f"state-sha256: {state_digest.group(1)}"
            )
        if (
            len({item.criterion_id for item in approvals}) != len(approvals)
            or {item.criterion_id for item in approvals}
            != {item.id for item in covered_criteria}
            or first_approval.statement != expected_statement
        ):
            raise StateValidationError("criterion coverage approval does not match its displayed TTY proposal")
    if type(state.criterion_test_receipts) is not tuple:
        raise StateValidationError("R3 test observations must be immutable")
    if any(type(item) is not CriterionTestReceipt for item in state.criterion_test_receipts):
        raise StateValidationError("R3 test receipt shape is invalid")
    _unique((item.id for item in state.criterion_test_receipts), "R3 criterion test receipt")
    if state.criterion_test_receipts and receipt is None and any(
        revision_index.get(item.criteria_revision_id) is None
        or revision_index[item.criteria_revision_id].source != REQUEST_SCOPED_OBJECTIVE
        for item in state.criterion_test_receipts
    ):
        raise StateValidationError("R3 test evidence requires explicit schema authority")
    for observation in state.criterion_test_receipts:
        revision = revision_index.get(observation.criteria_revision_id)
        if (revision is None or revision.slice_agreement_ref != observation.slice_agreement_ref
                or not any(item.id == observation.criterion_id for item in revision.criteria)):
            raise StateValidationError("R3 test evidence must target an accepted criterion revision")
        request_objective_test = revision.source == REQUEST_SCOPED_OBJECTIVE
        request_policy: tuple[str, str, str] | None = None
        request_policy_versions: tuple[tuple[str, str], ...] = ()
        if request_objective_test:
            try:
                request_policy = request_objective_policy(revision)
                request_policy_versions = _request_objective_policy_versions(revision)
            except DomainError as error:
                raise StateValidationError("request-objective criteria have no canonical policy") from error
            expected_evidence_kind = (
                TASK_APP_TEST_KIND
                if request_policy[2] == "REQUEST_SCOPED_PERSONAL_TASKS"
                else request_policy[0]
            )
            if (
                observation.test_kind not in {
                    policy_id for policy_id, _ in request_policy_versions
                }
                or not any(
                    item.expected_evidence
                    == f"{expected_evidence_kind}:{observation.test_target}"
                    for item in revision.criteria
                    if item.id == observation.criterion_id
                )
            ):
                raise StateValidationError(
                    "request-objective evidence descriptor differs from its canonical policy"
                )
        elif not any(
            item.expected_evidence == f"{observation.test_kind}:{observation.test_target}"
            for item in revision.criteria if item.id == observation.criterion_id
        ):
            raise StateValidationError("R3 test command differs from the accepted evidence contract")
        if request_objective_test:
            assert request_policy is not None
            if (
                observation.test_kind not in {
                    policy_id for policy_id, _ in request_policy_versions
                }
                or observation.test_target != OBJECTIVE_TEST_TARGET
            ):
                raise StateValidationError("request-objective evidence descriptor is not canonical")
        else:
            if observation.test_kind not in ("python-unittest", "node-test"):
                raise StateValidationError("R3 evidence type is not an approved deterministic test")
            if (observation.test_kind == "python-unittest" and
                    not re.fullmatch(r"tests(?:\.[A-Za-z_][A-Za-z0-9_]*)+", observation.test_target)):
                raise StateValidationError("R3 Python test target is not a supported test module")
            if (observation.test_kind == "node-test" and
                    not re.fullmatch(r"tests/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.test\.mjs", observation.test_target)):
                raise StateValidationError("R3 Node test target must be a relative test file")
        if (observation.exit_code < 0 or observation.source_revision < 0
                or observation.recorded_revision != observation.source_revision + 1
                or observation.recorded_revision > state.revision
                or (receipt is None and not request_objective_test)
                or (receipt is not None and observation.source_revision < receipt.converted_revision)
                or not re.fullmatch(r"[0-9a-f]{64}", observation.output_sha256)):
            raise StateValidationError("R3 test result has invalid revision or output identity")
        provenance = (
            observation.input_fingerprint_before,
            observation.input_fingerprint_after,
            observation.input_policy_id,
            observation.input_policy_digest,
            observation.admission_policy_id,
            observation.admission_policy_digest,
            observation.runtime_executable,
            observation.runtime_executable_sha256,
            observation.runtime_version,
            observation.environment_profile_id,
            observation.environment_profile_digest,
        )
        has_r3b_provenance = (
            any(value is not None for value in provenance)
            or bool(observation.external_inputs)
            or bool(observation.runtime_argv)
        )
        has_coverage_binding = (
            observation.coverage_approval_id is not None
            or observation.coverage_contract_digest is not None
        )
        identity_values: list[object] = [
            observation.slice_agreement_ref, observation.criteria_revision_id,
            observation.criterion_id, observation.test_kind, observation.test_target,
            observation.exit_code, observation.output_sha256, observation.source_revision,
        ]
        if has_r3b_provenance:
            identity_values.extend((
                observation.input_fingerprint_before, observation.input_policy_id,
                observation.input_policy_digest, observation.admission_policy_id,
                observation.admission_policy_digest, list(observation.external_inputs),
                observation.runtime_executable, observation.runtime_executable_sha256,
                observation.runtime_version, list(observation.runtime_argv),
                observation.environment_profile_id, observation.environment_profile_digest,
            ))
            if has_coverage_binding:
                identity_values.extend((
                    observation.coverage_approval_id,
                    observation.coverage_contract_digest,
                ))
        expected_id = "criterion-test-" + sha256(json.dumps(
            identity_values, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()[:20]
        if observation.id != expected_id:
            raise StateValidationError("R3 test receipt identity does not match its recorded result")
        if request_objective_test:
            assert request_policy is not None
            if (
                not has_r3b_provenance
                or not is_sha256_digest(observation.input_fingerprint_before or "")
                or observation.input_fingerprint_before != observation.input_fingerprint_after
                or observation.input_policy_id != INPUT_POLICY_ID
                or observation.input_policy_digest != INPUT_POLICY_DIGEST
                or (
                    observation.test_kind,
                    observation.admission_policy_id,
                    observation.admission_policy_digest,
                ) not in tuple(
                    (policy_id, policy_id, policy_digest)
                    for policy_id, policy_digest in request_policy_versions
                )
                or observation.external_inputs
                or observation.runtime_executable is not None
                or observation.runtime_executable_sha256 is not None
                or observation.runtime_version is not None
                or observation.runtime_argv
                or observation.environment_profile_id is not None
                or observation.environment_profile_digest is not None
                or has_coverage_binding
            ):
                raise StateValidationError("request-objective evidence must not claim runtime or coverage")
        elif has_r3b_provenance:
            if (
                r3b_receipt is None
                or any(value is None for value in provenance)
                or observation.input_fingerprint_before != observation.input_fingerprint_after
                or not is_sha256_digest(observation.input_fingerprint_before or "")
                or not is_sha256_digest(observation.input_policy_digest or "")
                or not is_sha256_digest(observation.admission_policy_digest or "")
                or not is_sha256_digest(observation.runtime_executable_sha256 or "")
                or not is_sha256_digest(observation.environment_profile_digest or "")
                or type(observation.external_inputs) is not tuple
                or observation.external_inputs
                or type(observation.runtime_argv) is not tuple
                or not observation.runtime_argv
                or any(type(value) is not str or not value for value in observation.runtime_argv)
                or (
                    observation.input_policy_id,
                    observation.input_policy_digest,
                    observation.admission_policy_digest,
                ) not in (
                    (INPUT_POLICY_ID, INPUT_POLICY_DIGEST, TEST_ADMISSION_POLICY_DIGEST),
                    (LEGACY_INPUT_POLICY_ID, LEGACY_INPUT_POLICY_DIGEST, LEGACY_TEST_ADMISSION_POLICY_DIGEST),
                )
                or observation.environment_profile_id != TEST_ENVIRONMENT_PROFILE_ID
                or has_coverage_binding and (
                    observation.coverage_approval_id is None
                    or not is_sha256_digest(observation.coverage_contract_digest or "")
                    or (
                        (coverage_approval := coverage_by_id.get(observation.coverage_approval_id)) is None
                        or coverage_approval.contract_digest != observation.coverage_contract_digest
                        or state.initialization is None
                        or coverage_approval.repository_id != state.initialization.repository_id
                        or coverage_approval.slice_agreement_ref != observation.slice_agreement_ref
                        or coverage_approval.criteria_revision_id != observation.criteria_revision_id
                        or coverage_approval.criterion_id != observation.criterion_id
                        or coverage_approval.test_kind != observation.test_kind
                        or coverage_approval.test_target != observation.test_target
                        or coverage_approval.root_fingerprint != observation.input_fingerprint_before
                    )
                )
            ):
                raise StateValidationError("R3-B test provenance is incomplete or unsupported")
    if type(state.slice_outcome_receipts) is not tuple:
        raise StateValidationError("Slice outcome receipts must be immutable")
    if any(type(item) is not SliceOutcomeReceipt for item in state.slice_outcome_receipts):
        raise StateValidationError("Slice outcome receipt shape is invalid")
    _unique((item.id for item in state.slice_outcome_receipts), "Slice outcome receipt")
    if state.slice_outcome_receipts and r3b_receipt is None and any(
        revision_index.get(item.criteria_revision_id) is None
        or revision_index[item.criteria_revision_id].source != REQUEST_SCOPED_OBJECTIVE
        for item in state.slice_outcome_receipts
    ):
        raise StateValidationError("automatic Slice outcomes require explicit schema-17 authority")
    test_receipts = {item.id: item for item in state.criterion_test_receipts}
    for outcome in state.slice_outcome_receipts:
        agreement = agreement_index.get(outcome.slice_agreement_ref)
        criteria = revision_index.get(outcome.criteria_revision_id)
        request_objective_outcome = (
            criteria is not None and criteria.source == REQUEST_SCOPED_OBJECTIVE
        )
        request_policy: tuple[str, str, str] | None = None
        request_policy_versions: tuple[tuple[str, str], ...] = ()
        if criteria is not None and criteria.source == REQUEST_SCOPED_OBJECTIVE:
            try:
                request_policy = request_objective_policy(criteria)
                request_policy_versions = _request_objective_policy_versions(criteria)
            except DomainError as error:
                raise StateValidationError("request-objective criteria have no canonical policy") from error
        request_admission_policy_valid = (
            (outcome.admission_policy_id, outcome.admission_policy_digest)
            in request_policy_versions
            if request_policy is not None
            else outcome.admission_policy_digest == TEST_ADMISSION_POLICY_DIGEST
        )
        current_input_policy = (
            outcome.input_policy_id == INPUT_POLICY_ID
            and outcome.input_policy_digest == INPUT_POLICY_DIGEST
            and request_admission_policy_valid
        )
        legacy_input_policy = (
            outcome.input_policy_id == LEGACY_INPUT_POLICY_ID
            and outcome.input_policy_digest == LEGACY_INPUT_POLICY_DIGEST
            and outcome.admission_policy_digest == LEGACY_TEST_ADMISSION_POLICY_DIGEST
            and outcome.coverage_contract_digest is None
        )
        if (
            agreement is None
            or agreement.kind != "SLICE"
            or agreement.repository_id != outcome.repository_id
            or criteria is None
            or criteria.slice_agreement_ref != outcome.slice_agreement_ref
            or not outcome.evidence_receipt_ids
            or len(outcome.evidence_receipt_ids) != len(set(outcome.evidence_receipt_ids))
            or len(outcome.evidence_receipt_ids) != len(criteria.criteria)
            or type(outcome.input_policy_id) is not str
            or not (
                current_input_policy
                or legacy_input_policy
                or request_objective_outcome and current_input_policy
            )
            or type(outcome.admission_policy_id) is not str
            or request_policy is not None
            and (outcome.admission_policy_id, outcome.admission_policy_digest)
            not in request_policy_versions
            or request_policy is None
            and outcome.admission_policy_id != TEST_ADMISSION_POLICY_ID
            or (
                not request_objective_outcome
                and outcome.admission_policy_digest != TEST_ADMISSION_POLICY_DIGEST
                and not legacy_input_policy
            )
            or (
                outcome.coverage_contract_digest is not None
                and not is_sha256_digest(outcome.coverage_contract_digest)
            )
            or (request_objective_outcome and outcome.coverage_contract_digest is not None)
            or any(not is_sha256_digest(value) for value in (
                outcome.input_fingerprint,
                outcome.input_policy_digest,
                outcome.admission_policy_digest,
                outcome.relevant_drift_digest,
            ))
            or (
                outcome.basis != (request_policy[2] if request_policy is not None else "")
                or outcome.source != REQUEST_SCOPED_OBJECTIVE
                if request_objective_outcome
                else outcome.basis != "ALL_CRITERIA_FRESH_SUPPORTED_TESTS"
                or outcome.source != "CORE_OBJECTIVE_ACCEPTANCE"
            )
            or outcome.source_revision < 0
            or outcome.recorded_revision != outcome.source_revision + 1
            or outcome.recorded_revision > state.revision
        ):
            raise StateValidationError("Slice outcome receipt is invalid")
        selected = tuple(test_receipts.get(identity) for identity in outcome.evidence_receipt_ids)
        if request_objective_outcome:
            assert request_policy is not None
            expected_criterion_ids = tuple(item.id for item in criteria.criteria)
            if (
                len(selected) != len(criteria.criteria)
                or any(
                    receipt is None
                    or receipt.exit_code != 0
                    or receipt.criterion_id != criterion.id
                    or receipt.test_kind != outcome.admission_policy_id
                    or receipt.test_target != OBJECTIVE_TEST_TARGET
                    or receipt.slice_agreement_ref != outcome.slice_agreement_ref
                    or receipt.criteria_revision_id != outcome.criteria_revision_id
                    or receipt.input_fingerprint_before != outcome.input_fingerprint
                    or receipt.input_fingerprint_after != outcome.input_fingerprint
                    or receipt.input_policy_id != outcome.input_policy_id
                    or receipt.input_policy_digest != outcome.input_policy_digest
                    or receipt.admission_policy_id != outcome.admission_policy_id
                    or receipt.admission_policy_digest != outcome.admission_policy_digest
                    or receipt.source_revision != outcome.source_revision
                    or receipt.recorded_revision != outcome.recorded_revision
                    or receipt.runtime_executable is not None
                    or receipt.runtime_executable_sha256 is not None
                    or receipt.runtime_version is not None
                    or receipt.runtime_argv
                    or receipt.environment_profile_id is not None
                    or receipt.environment_profile_digest is not None
                    or receipt.coverage_approval_id is not None
                    or receipt.coverage_contract_digest is not None
                    for criterion, receipt in zip(criteria.criteria, selected)
                )
                or tuple(receipt.criterion_id for receipt in selected if receipt is not None)
                != expected_criterion_ids
                or (
                    request_policy[2] == "REQUEST_SCOPED_PERSONAL_TASKS"
                    and len({receipt.output_sha256 for receipt in selected if receipt is not None}) != 1
                )
            ):
                raise StateValidationError("request-objective outcome must reference every matching Core proof")
        elif any(
            receipt is None
            or receipt.exit_code != 0
            or receipt.slice_agreement_ref != outcome.slice_agreement_ref
            or receipt.criteria_revision_id != outcome.criteria_revision_id
            or receipt.input_fingerprint_before != outcome.input_fingerprint
            or receipt.input_fingerprint_after != outcome.input_fingerprint
            or receipt.input_policy_id != outcome.input_policy_id
            or receipt.input_policy_digest != outcome.input_policy_digest
            or receipt.admission_policy_id != outcome.admission_policy_id
            or receipt.admission_policy_digest != outcome.admission_policy_digest
            or receipt.environment_profile_id != TEST_ENVIRONMENT_PROFILE_ID
            or receipt.environment_profile_digest is None
            or not is_sha256_digest(receipt.environment_profile_digest)
            or receipt.external_inputs
            or not receipt.runtime_executable
            or not receipt.runtime_executable_sha256
            or not receipt.runtime_version
            or not receipt.runtime_argv
            or (
                outcome.coverage_contract_digest is not None
                and (
                    receipt.coverage_approval_id is None
                    or receipt.coverage_contract_digest is None
                    or (
                        (approval := coverage_by_id.get(receipt.coverage_approval_id)) is None
                        or approval.contract_digest != receipt.coverage_contract_digest
                        or approval.root_fingerprint != outcome.input_fingerprint
                    )
                )
            )
            for receipt in selected
        ):
            raise StateValidationError("Slice outcome must reference matching successful test provenance")
        if {receipt.criterion_id for receipt in selected if receipt is not None} != {
            criterion.id for criterion in criteria.criteria
        }:
            raise StateValidationError("Slice outcome must cover each current accepted criterion")
        if request_objective_outcome:
            identity_valid = outcome.id == slice_outcome_semantic_id(outcome)
        elif outcome.coverage_contract_digest is not None:
            selected_approvals = tuple(
                coverage_by_id[receipt.coverage_approval_id]
                for receipt in selected
                if receipt is not None and receipt.coverage_approval_id in coverage_by_id
            )
            identity_valid = (
                len(selected_approvals) == len(criteria.criteria)
                and coverage_set_digest(selected_approvals) == outcome.coverage_contract_digest
                and outcome.id == slice_outcome_semantic_id(outcome)
            )
        else:
            expected_id = "slice-outcome-" + sha256(json.dumps([
                outcome.repository_id, outcome.slice_agreement_ref, outcome.criteria_revision_id,
                list(outcome.evidence_receipt_ids), outcome.input_fingerprint, outcome.input_policy_id,
                outcome.input_policy_digest, outcome.admission_policy_id, outcome.admission_policy_digest,
                outcome.relevant_drift_digest, outcome.basis, outcome.source,
            ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
            identity_valid = outcome.id == expected_id
        if not identity_valid:
            raise StateValidationError("Slice outcome receipt identity does not match its exact basis")
    # A Feature closure is a separate human decision, never an inferred Slice outcome.
    if type(state.feature_completion_receipts) is not tuple or any(
        type(item) is not FeatureCompletionReceipt for item in state.feature_completion_receipts
    ):
        raise StateValidationError("Feature completion receipts must be immutable records")
    _unique((item.id for item in state.feature_completion_receipts), "Feature completion receipt")
    _unique(((item.feature_id, item.cycle_id) for item in state.feature_completion_receipts), "completed Feature cycle")
    if type(state.feature_reopen_receipts) is not tuple or any(
        type(item) is not FeatureReopenReceipt for item in state.feature_reopen_receipts
    ):
        raise StateValidationError("Feature reopening receipts must be immutable records")
    _unique((item.id for item in state.feature_reopen_receipts), "Feature reopening receipt")
    _unique(((item.feature_id, item.cycle_id) for item in state.feature_reopen_receipts), "reopened Feature cycle")
    closures_by_cycle = {(item.feature_id, item.cycle_id): item for item in state.feature_completion_receipts}
    reopens_by_cycle = {(item.feature_id, item.cycle_id): item for item in state.feature_reopen_receipts}
    for reopening in state.feature_reopen_receipts:
        if type(reopening.cycle_id) is not int or type(reopening.statement) is not str:
            raise StateValidationError("Feature reopening receipt has invalid primitive fields")
        previous = closures_by_cycle.get((reopening.feature_id, reopening.cycle_id - 1))
        feature = records.features.get(reopening.feature_id)
        if (
            state.initialization is None or feature is None or feature.side_of is not None
            or reopening.repository_id != state.initialization.repository_id
            or type(reopening.cycle_id) is not int or reopening.cycle_id < 1
            or previous is None or reopening.previous_completion_receipt_id != previous.id
            or previous.recorded_revision > reopening.source_revision
            or any(type(value) is not str or not value.strip() or value != value.strip()
                   or len(value) > 2048 for value in (reopening.reason, reopening.purpose))
            or not is_sha256_digest(reopening.input_fingerprint)
            or reopening.source != "INTERACTIVE_TERMINAL"
            or reopening.source_reference != "tty:" + sha256(reopening.statement.encode("utf-8")).hexdigest()[:12]
            or reopening.source_revision < 0
            or reopening.recorded_revision != reopening.source_revision + 1
            or reopening.recorded_revision > state.revision
            or reopening.id != feature_reopen_semantic_id(reopening)
            or reopening.statement != feature_reopen_statement_text(
                feature=feature, cycle_id=reopening.cycle_id,
                previous_completion_receipt_id=previous.id, reason=reopening.reason,
                purpose=reopening.purpose, repository_id=reopening.repository_id,
                source_revision=reopening.source_revision,
                input_fingerprint=reopening.input_fingerprint,
            )
        ):
            raise StateValidationError("Feature reopening receipt authority is invalid")
    outcomes_by_id = {item.id: item for item in state.slice_outcome_receipts}
    for closure in state.feature_completion_receipts:
        # Old Slice agreements are a prefix of the append-only agreement ledger.
        # New-cycle Slices may extend that ledger without rewriting past closures.
        feature_records = tuple(item for item in state.agreements
                                if item.kind == "FEATURE" and item.feature_id == closure.feature_id)
        slice_records = tuple(item for item in state.agreements
                              if item.kind == "SLICE" and item.feature_id == closure.feature_id)
        expected_features = tuple(sorted(
            item.id for item in feature_records[:len(closure.feature_agreement_refs)]
        ))
        expected_slices = tuple(sorted(
            item.id for item in slice_records[:len(closure.slice_agreement_refs)]
        ))
        reopening = reopens_by_cycle.get((closure.feature_id, closure.cycle_id))
        previous = closures_by_cycle.get((closure.feature_id, closure.cycle_id - 1))
        selected = tuple(outcomes_by_id.get(identity) for identity in closure.slice_outcome_receipt_ids)
        request_objective_closure = closure.source == REQUEST_SCOPED_OBJECTIVE
        if (
            state.initialization is None
            or closure.repository_id != state.initialization.repository_id
            or closure.feature_id not in records.features
            or not expected_features or not expected_slices
            or (closure.cycle_id == 0 and reopening is not None)
            or (closure.cycle_id > 0 and (reopening is None or previous is None
                or reopening.recorded_revision > closure.source_revision))
            or type(closure.cycle_id) is not int or closure.cycle_id < 0
            or closure.feature_agreement_refs != expected_features
            or closure.slice_agreement_refs != expected_slices
            or len(closure.slice_outcome_receipt_ids) != len(expected_slices)
            or any(item is None for item in selected)
            or any(
                item is None or item.slice_agreement_ref != reference
                or item.input_fingerprint != closure.input_fingerprint
                for item, reference in zip(selected, expected_slices)
            )
            or not isinstance(closure.condition, str)
            or not closure.condition.strip() or closure.condition != closure.condition.strip()
            or len(closure.condition) > 2048
            or not is_sha256_digest(closure.input_fingerprint)
            or closure.source not in ("INTERACTIVE_TERMINAL", REQUEST_SCOPED_OBJECTIVE)
            or type(closure.statement) is not str
            or (
                not request_objective_closure
                and closure.source_reference != "tty:" + sha256(closure.statement.encode("utf-8")).hexdigest()[:12]
            )
            or not closure.statement.strip()
            or closure.source_revision < 0
            or closure.recorded_revision != closure.source_revision + 1
            or closure.recorded_revision > state.revision
            or closure.id != feature_completion_semantic_id(closure)
        ):
            raise StateValidationError("Feature completion receipt authority or evidence is invalid")
        # Hash consistency alone is insufficient: the recorded human words must
        # still describe this exact Feature, condition, snapshot, and Slice set.
        if request_objective_closure:
            request_agreement = next((
                item for item in state.agreements
                if item.kind == "SLICE" and item.id in closure.slice_agreement_refs
            ), None)
            request_features = tuple(
                item for item in state.agreements
                if item.kind == "FEATURE"
                and item.feature_id == closure.feature_id
            )
            objective = None if request_agreement is None else derive_request_objective(
                request_agreement.statement,
            )
            objective_outcome = (
                selected[0]
                if len(selected) == 1
                and selected[0] is not None
                and selected[0].source == REQUEST_SCOPED_OBJECTIVE
                else None
            )
            expected_condition = (
                None if objective is None else request_objective_completion_condition(objective)
            )
            expected_statement = None if (
                request_agreement is None or objective is None or objective_outcome is None
            ) else request_objective_completion_statement(
                source_reference=request_agreement.source_reference,
                request_sha256=sha256(request_agreement.statement.encode("utf-8")).hexdigest(),
                heading=objective.heading if isinstance(objective, StaticHeadingObjective) else None,
                outcome_receipt_id=objective_outcome.id,
                input_fingerprint=closure.input_fingerprint,
                source_revision=closure.source_revision,
                operations=objective.operations if isinstance(objective, PersonalTasksObjective) else (),
            )
            objective_proofs = (
                ()
                if objective_outcome is None
                else tuple(
                    test_receipts.get(identity)
                    for identity in objective_outcome.evidence_receipt_ids
                )
            )
            objective_archives = () if request_agreement is None else tuple(
                item for item in state.terminal_archives
                if item.context.feature_id == closure.feature_id
                and item.context.change_id == request_agreement.change_id
            )
            objective_work = () if request_agreement is None else tuple(
                item for item in state.work_items
                if item.terminal_change_id == request_agreement.change_id
            )
            if (
                request_agreement is None
                or len(state.agreements) != 2
                or len(request_features) != 1
                or request_agreement.feature_id != closure.feature_id
                or request_agreement.source != "SESSION_REQUEST"
                or request_features[0].source != "SESSION_REQUEST"
                or request_features[0].statement != request_agreement.statement
                or request_features[0].source_reference != request_agreement.source_reference
                or closure.source_reference != request_agreement.source_reference
                or objective is None
                or objective_outcome is None
                or expected_condition != closure.condition
                or objective_outcome.input_fingerprint != closure.input_fingerprint
                or objective_outcome.recorded_revision > closure.source_revision
                or any(
                    proof is None or proof.recorded_revision > closure.source_revision
                    for proof in objective_proofs
                )
                or closure.statement != expected_statement
                or len(objective_archives) != 1
                or objective_archives[0].context.status is not LifecycleState.ACCEPTED
                or objective_archives[0].context.phase is not Phase.DONE
                or objective_archives[0].context.publication is not None
                or objective_archives[0].parent_feature_id is not None
                or objective_archives[0].context.review is None
                or objective_archives[0].context.review.verdict is not ReviewVerdict.PASS
                or not objective_archives[0].context.verification.sufficient
                or not objective_archives[0].context.convergence_complete
                or objective_archives[0].convergence_assessment is None
                or objective_archives[0].convergence_assessment.change_id != request_agreement.change_id
                or objective_archives[0].convergence_assessment.product_truth_changed
                or any(
                    item.feature_id == closure.feature_id
                    for item in state.publications + state.publication_history
                )
                or len(objective_work) != 1
                or objective_work[0].operational_state is not OperationalState.DONE
                or objective_work[0].context is not None
            ):
                raise StateValidationError("request-objective Feature completion is not bound to its Core proof")
        else:
            expected_statement = feature_completion_statement_text(
                feature=records.features[closure.feature_id], condition=closure.condition,
                repository_id=closure.repository_id, source_revision=closure.source_revision,
                input_fingerprint=closure.input_fingerprint,
                feature_agreement_refs=closure.feature_agreement_refs,
                slice_outcomes=tuple(zip(closure.slice_agreement_refs, closure.slice_outcome_receipt_ids)),
                previous_closure_exists=any(
                    other is not closure and other.recorded_revision <= closure.source_revision
                    for other in state.feature_completion_receipts
                ),
                schema19_retained=any(item.recorded_revision <= closure.source_revision
                                      for item in state.feature_reopen_receipts),
            ) if closure.cycle_id == 0 else feature_cycle_completion_statement_text(
                feature=records.features[closure.feature_id], cycle_id=closure.cycle_id,
                previous_reopen_receipt_id=reopening.id if reopening is not None else "",
                condition=closure.condition, repository_id=closure.repository_id,
                source_revision=closure.source_revision,
                input_fingerprint=closure.input_fingerprint,
                feature_agreement_refs=closure.feature_agreement_refs,
                slice_outcomes=tuple(zip(closure.slice_agreement_refs, closure.slice_outcome_receipt_ids)),
            )
        if closure.statement != expected_statement:
            raise StateValidationError("Feature completion statement differs from its authority basis")
    _unique(
        (agreement.task_id for agreement in state.agreements if agreement.task_id is not None),
        "Slice Task",
    )
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
    # This read-only value comes from the same state.json read as `state`.
    e1_schema_ready: bool | None = None
    persisted_schema: int | None = None

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


_CONTINUITY_FIELDS = frozenset((
    "schema", "mode", "repository_id", "revision", "features",
    "default_feature_id", "session_features", "task_states", "attempts",
    "execution", "checks", "closures", "reopens", "paused_features",
    "pause_reasons", "detours", "documents", "ids", "pending",
))
_CONTINUITY_SEMANTIC_FIELDS = _CONTINUITY_FIELDS | frozenset(("semantic_digests",))
_CONTINUITY_IDENTITY_FIELDS = _CONTINUITY_SEMANTIC_FIELDS | frozenset(("identity_metadata",))
_CONTINUITY_OPTIONAL_FIELDS = frozenset(("mandate", "task_events"))
_CONTINUITY_ID = re.compile(r"^[FSTCD]-[0-9a-f]{32}$")
_CONTINUITY_DIR = re.compile(r"^[0-9]{3,}-[a-z0-9]+(?:-[a-z0-9]+)*$")
_CONTINUITY_DOC = re.compile(
    r"^(?:specs/README\.md|specs/[0-9]{3,}-[a-z0-9]+(?:-[a-z0-9]+)*/"
    r"(?:spec|plan|tasks|decisions)\.md)$"
)


class ContinuityFeatureIndex(TypedDict):
    directory: str
    status: Literal["OPEN", "CLOSED"]
    cycle: int


class ContinuityCriterionDocument(TypedDict):
    id: str
    description: str
    check_kind: str
    check_target: str


class ContinuityIncrementDocument(TypedDict):
    id: str
    title: str
    outcome: str


class ContinuityFeatureDocument(TypedDict):
    id: str
    kind: str
    title: str
    outcome: str
    criteria: list[ContinuityCriterionDocument]
    increments: list[ContinuityIncrementDocument]


class ContinuityPlanDocument(TypedDict):
    feature_id: str
    plan: str


class ContinuityTaskDocument(TypedDict):
    id: str
    title: str
    description: str
    increment_id: str | None
    criteria_ids: list[str]
    depends_on: list[str]


class ContinuityDecisionDocument(TypedDict):
    id: str
    question: str
    decision: str


class ContinuityTaskState(TypedDict):
    attempts: int


class ContinuityMandateReference(TypedDict):
    id: str
    digest: str


class ContinuityMandate(TypedDict):
    id: str
    feature_id: str
    mode: str
    criteria: list[ContinuityMandateReference]
    increments: list[ContinuityMandateReference]
    source_revision: int
    recorded_revision: int
    session_id: str
    close_feature: bool


class ContinuityTaskEvent(TypedDict):
    task_id: str
    event: str
    reason: str
    evidence_ref: str
    session_id: str
    recorded_revision: int
    attempt_count: int


class ContinuityAttempt(TypedDict):
    task_id: str
    attempt: int
    status: str
    session_id: str
    checkpoint: str
    result: str | None
    result_status: str | None
    evidence_ref: str | None
    mandate_id: NotRequired[str]


class ContinuityExecution(TypedDict):
    task_id: str
    attempt: int
    session_id: str
    status: str


CONTINUITY_INPUT_POLICY_ID = "continuity-root-input-v1"
_CONTINUITY_INPUT_POLICY = {
    "version": CONTINUITY_INPUT_POLICY_ID,
    "base_policy": [INPUT_POLICY_ID, INPUT_POLICY_DIGEST],
    "coverage": "all regular files and directories, tracked, ignored or untracked",
    "base_excluded_root_entries": [".git", ".evospec"],
    "excluded_outputs": [
        "specs/README.md",
        "exact paths in the current continuity state.documents map",
        "Feature directories containing those tracked projections",
    ],
    "scope_digest": [
        "sorted normalized relative file path and content SHA-256",
        "sorted normalized relative directory path and presence",
    ],
    "scan_safety": "base policy bounds, stable double scan and unsafe-entry rejection",
}
CONTINUITY_INPUT_POLICY_DIGEST = sha256(
    json.dumps(
        _CONTINUITY_INPUT_POLICY,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


class ContinuityInputScope(TypedDict):
    policy_id: str
    policy_digest: str
    fingerprint: str


class ContinuityCheck(TypedDict):
    criterion_id: str
    criterion_digest: str
    check_kind: str
    check_target: str
    exit_code: int
    output_sha256: str
    reported_by_session_id: str
    task_id: str | None
    recorded_revision: int
    observed_input_scope: NotRequired[ContinuityInputScope]
    task_digest: NotRequired[str]


class ContinuityCriterionDigest(TypedDict):
    id: str
    digest: str


class ContinuityClosure(TypedDict):
    id: str
    feature_id: str
    cycle: int
    revision: int
    criteria: list[ContinuityCriterionDigest]
    session_id: str


class ContinuityReopen(TypedDict):
    feature_id: str
    cycle: int
    previous_closure_id: str
    session_id: str


class ContinuityDetour(TypedDict):
    id: str
    feature_id: str
    kind: str
    title: str
    return_task_id: str
    result: str | None
    reported_by_session_id: str | None
    created_revision: int
    resolved_revision: int | None


class ContinuityPendingWrite(TypedDict):
    path: str
    before: str | None
    content: str
    sha256: str


class ContinuityPending(TypedDict):
    next_state: dict[str, object]
    writes: list[ContinuityPendingWrite]
    read_set: NotRequired[dict[str, str | None]]


class ContinuityIdentityMetadata(TypedDict):
    feature_id: str
    kind: str
    active: bool
    digest: str


class ContinuityStateData(TypedDict):
    schema: int
    mode: str
    repository_id: str
    revision: int
    features: dict[str, ContinuityFeatureIndex]
    default_feature_id: str | None
    session_features: dict[str, str]
    task_states: dict[str, ContinuityTaskState]
    attempts: list[ContinuityAttempt]
    execution: ContinuityExecution | None
    checks: list[ContinuityCheck]
    closures: list[ContinuityClosure]
    reopens: list[ContinuityReopen]
    paused_features: list[str]
    pause_reasons: dict[str, str]
    detours: list[ContinuityDetour]
    documents: dict[str, str]
    ids: list[str]
    pending: ContinuityPending | None
    semantic_digests: NotRequired[dict[str, str]]
    identity_metadata: NotRequired[dict[str, ContinuityIdentityMetadata]]
    mandate: NotRequired[ContinuityMandate]
    task_events: NotRequired[list[ContinuityTaskEvent]]


def validate_continuity_state(
    value: object, *, repository_id: str | None = None, pending_state: bool = False,
) -> None:
    """Validate the bounded operational index; Feature definitions remain in Markdown."""
    def invalid(detail: str) -> NoReturn:
        raise ContinuityError("CORRUPT_CONTINUITY", detail)

    def valid_text(value: object, maximum: int) -> bool:
        if (
            type(value) is not str or not value.strip()
            or any(ord(char) < 32 and char != "\t" for char in value)
        ):
            return False
        try:
            return len(value.encode("utf-8")) <= maximum
        except UnicodeEncodeError:
            return False

    allowed_fields = tuple(
        fields | optional
        for fields in (
            _CONTINUITY_FIELDS, _CONTINUITY_SEMANTIC_FIELDS,
            _CONTINUITY_IDENTITY_FIELDS,
        )
        for optional in (
            frozenset(), frozenset(("mandate",)), frozenset(("task_events",)),
            _CONTINUITY_OPTIONAL_FIELDS,
        )
    )
    if type(value) is not dict or frozenset(value) not in allowed_fields:
        invalid("continuity state fields are not exact")
    state = cast(dict[str, object], cast(object, value))
    if (
        type(state["schema"]) is not int or state["schema"] != 1
        or state["mode"] != "COMPOSED"
        or not isinstance(state["repository_id"], str)
        or (repository_id is not None and state["repository_id"] != repository_id)
        or type(state["revision"]) is not int or state["revision"] < 0
        or any(type(state[key]) is not dict for key in (
            "features", "session_features", "task_states", "pause_reasons", "documents",
        ))
        or any(type(state[key]) is not list for key in (
            "attempts", "checks", "closures", "reopens", "paused_features", "detours", "ids",
        ))
        or ("task_events" in state and type(state["task_events"]) is not list)
        or ("mandate" in state and type(state["mandate"]) is not dict)
        or (state["execution"] is not None and type(state["execution"]) is not dict)
        or (state["pending"] is not None and type(state["pending"]) is not dict)
    ):
        invalid("continuity state values are invalid or belong to another root")
    if pending_state and state["pending"] is not None:
        invalid("journal next state cannot contain another pending transaction")

    features = cast(dict[str, object], state["features"])
    session_features = cast(dict[str, object], state["session_features"])
    task_states = cast(dict[str, object], state["task_states"])
    attempts = cast(list[object], state["attempts"])
    execution = cast(dict[str, object] | None, state["execution"])
    checks = cast(list[object], state["checks"])
    closures = cast(list[object], state["closures"])
    reopens = cast(list[object], state["reopens"])
    paused_features = cast(list[object], state["paused_features"])
    pause_reasons = cast(dict[str, object], state["pause_reasons"])
    detours = cast(list[object], state["detours"])
    document_map = cast(dict[str, object], state["documents"])
    semantic_map = cast(
        dict[str, object], state.get("semantic_digests", {}),
    )
    identity_metadata = cast(
        dict[str, object], state.get("identity_metadata", {}),
    )
    mandate = state.get("mandate")
    task_events = cast(list[object], state.get("task_events", []))
    ids = cast(list[object], state["ids"])
    revision = cast(int, state["revision"])
    pending = cast(dict[str, object] | None, state["pending"])
    if len(features) > 1000:
        invalid("Feature index exceeds its supported bound")
    directories: set[str] = set()
    for feature_id, entry in features.items():
        if (
            not isinstance(feature_id, str) or _CONTINUITY_ID.fullmatch(feature_id) is None
            or feature_id[0] != "F" or type(entry) is not dict
            or set(entry) != {"directory", "status", "cycle"}
            or not isinstance(entry["directory"], str)
            or _CONTINUITY_DIR.fullmatch(entry["directory"]) is None
            or entry["directory"] in directories
            or entry["status"] not in ("OPEN", "CLOSED")
            or type(entry["cycle"]) is not int or entry["cycle"] < 1
        ):
            invalid("Feature index is invalid")
        directories.add(cast(ContinuityFeatureIndex, cast(object, entry))["directory"])
    if state["default_feature_id"] is not None and (
        not isinstance(state["default_feature_id"], str)
        or state["default_feature_id"] not in features
    ):
        invalid("worktree default references an unknown Feature")
    if any(
        not isinstance(session, str) or not session or len(session) > 256
        or not isinstance(feature_id, str) or feature_id not in features
        for session, feature_id in session_features.items()
    ):
        invalid("session selection is invalid")
    if any(not isinstance(item, str) for item in paused_features):
        invalid("paused Feature reference is invalid")
    if len(paused_features) > 1 or len(set(paused_features)) != len(paused_features):
        invalid("at most one composed Feature can be paused at a time")
    if any(feature_id not in features for feature_id in paused_features):
        invalid("paused Feature reference is invalid")
    if set(pause_reasons) != set(paused_features) or any(
        not isinstance(feature_id, str) or feature_id not in features
        or not valid_text(reason, 2048)
        for feature_id, reason in pause_reasons.items()
    ):
        invalid("paused Feature reasons do not match operational pause state")

    counters: dict[str, int] = {}
    for task_id, entry in task_states.items():
        if (
            not isinstance(task_id, str) or _CONTINUITY_ID.fullmatch(task_id) is None
            or task_id[0] != "T" or type(entry) is not dict
            or set(entry) != {"attempts"}
            or type(entry["attempts"]) is not int or entry["attempts"] < 0
        ):
            invalid("Task attempt counters are invalid")
        counters[task_id] = 0
    for attempt in attempts:
        if (
            type(attempt) is not dict
            or set(attempt) not in (
                {
                    "task_id", "attempt", "status", "session_id", "checkpoint",
                    "result", "result_status", "evidence_ref",
                },
                {
                    "task_id", "attempt", "status", "session_id", "checkpoint",
                    "result", "result_status", "evidence_ref", "mandate_id",
                },
            )
            or not isinstance(attempt["task_id"], str)
            or attempt["task_id"] not in counters
            or type(attempt["attempt"]) is not int or attempt["attempt"] < 1
            or attempt["status"] not in (
                "STARTED", "OUTCOME_UNKNOWN", "REPORTED",
                "RECONCILED_COMPLETE", "RECONCILED_INCOMPLETE",
            )
            or not isinstance(attempt["session_id"], str) or not attempt["session_id"]
            or not isinstance(attempt["checkpoint"], str)
            or (attempt["result"] is not None and not isinstance(attempt["result"], str))
            or attempt["result_status"] not in (
                None, "SUCCEEDED", "BLOCKED", "UNKNOWN", "COMPLETE", "INCOMPLETE",
            )
            or (attempt["evidence_ref"] is not None and not isinstance(attempt["evidence_ref"], str))
            or (
                "mandate_id" in attempt
                and (
                    not isinstance(attempt["mandate_id"], str)
                    or re.fullmatch(r"D-[0-9a-f]{32}", attempt["mandate_id"]) is None
                )
            )
        ):
            invalid("Task attempt history is invalid")
        counters[attempt["task_id"]] += 1
        if attempt["attempt"] != counters[attempt["task_id"]]:
            invalid("Task attempt numbering is not contiguous")
    if any(
        cast(ContinuityTaskState, cast(object, task_states[task_id]))["attempts"] != count
        for task_id, count in counters.items()
    ):
        invalid("Task attempt counters disagree with history")

    legacy_check_fields = {
        "criterion_id", "criterion_digest", "check_kind", "check_target",
        "exit_code", "output_sha256", "reported_by_session_id", "task_id",
        "recorded_revision",
    }
    for check in checks:
        check_fields = set(check) if type(check) is dict else set()
        if (
            type(check) is not dict
            or check_fields not in (
                legacy_check_fields,
                legacy_check_fields | {"observed_input_scope"},
                legacy_check_fields | {"task_digest"},
                legacy_check_fields | {"observed_input_scope", "task_digest"},
            )
            or not isinstance(check["criterion_id"], str)
            or _CONTINUITY_ID.fullmatch(check["criterion_id"]) is None
            or check["criterion_id"][0] != "C"
            or not isinstance(check["criterion_digest"], str)
            or re.fullmatch(r"[0-9a-f]{64}", check["criterion_digest"]) is None
            or check["check_kind"] not in ("python-unittest", "node-test")
            or not isinstance(check["check_target"], str)
            or type(check["exit_code"]) is not int or not 0 <= check["exit_code"] <= 255
            or not isinstance(check["output_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", check["output_sha256"]) is None
            or not isinstance(check["reported_by_session_id"], str)
            or not check["reported_by_session_id"]
            or (
                check["task_id"] is not None
                and (
                    not isinstance(check["task_id"], str)
                    or check["task_id"] not in counters
                )
            )
            or (
                "observed_input_scope" in check
                and check["task_id"] is None
            )
            or type(check["recorded_revision"]) is not int
            or not 1 <= check["recorded_revision"] <= revision
            or (
                "task_digest" in check
                and (
                    not isinstance(check["task_digest"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", check["task_digest"]) is None
                    or check["task_id"] is None
                )
            )
        ):
            invalid("criterion check history is invalid")
        if "observed_input_scope" in check:
            scope = check["observed_input_scope"]
            if (
                type(scope) is not dict
                or set(scope) != {"policy_id", "policy_digest", "fingerprint"}
                or not isinstance(scope["policy_id"], str)
                or not scope["policy_id"].strip()
                or len(scope["policy_id"]) > 128
                or not isinstance(scope["policy_digest"], str)
                or re.fullmatch(r"[0-9a-f]{64}", scope["policy_digest"]) is None
                or not isinstance(scope["fingerprint"], str)
                or re.fullmatch(r"[0-9a-f]{64}", scope["fingerprint"]) is None
            ):
                invalid("criterion check input scope is invalid")

    if any(
        not isinstance(path, str) or _CONTINUITY_DOC.fullmatch(path) is None
        or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for path, digest in document_map.items()
    ):
        invalid("document digest index is invalid")
    if type(semantic_map) is not dict or any(
        not isinstance(path, str) or _CONTINUITY_DOC.fullmatch(path) is None
        or path not in document_map
        or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for path, digest in semantic_map.items()
    ):
        invalid("semantic document digest index is invalid")
    if any(not isinstance(item, str) or _CONTINUITY_ID.fullmatch(item) is None for item in ids):
        invalid("stable identity registry is invalid")
    if (
        len(ids) != len(set(ids))
        or any(item not in ids for item in (*features.keys(), *task_states.keys()))
    ):
        invalid("stable identity registry is invalid")
    if type(identity_metadata) is not dict or any(
        not isinstance(identity, str) or _CONTINUITY_ID.fullmatch(identity) is None
        or identity not in ids or type(record) is not dict
        or set(record) != {"feature_id", "kind", "active", "digest"}
        or not isinstance(record.get("feature_id"), str)
        or record.get("feature_id") not in features
        or not isinstance(record.get("kind"), str)
        or record.get("kind") != identity[0]
        or type(record.get("active")) is not bool
        or not isinstance(record.get("digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", cast(str, record.get("digest"))) is None
        for identity, record in identity_metadata.items()
    ):
        invalid("typed identity metadata is invalid")
    if mandate is not None:
        if (
            type(mandate) is not dict
            or set(mandate) != {
                "id", "feature_id", "mode", "criteria", "increments",
                "source_revision", "recorded_revision", "session_id", "close_feature",
            }
            or not isinstance(mandate["id"], str)
            or re.fullmatch(r"D-[0-9a-f]{32}", mandate["id"]) is None
            or mandate["id"] not in ids
            or not isinstance(mandate["feature_id"], str)
            or mandate["feature_id"] not in features
            or mandate["mode"] != "ALL"
            or type(mandate["criteria"]) is not list
            or not mandate["criteria"]
            or type(mandate["increments"]) is not list
            or type(mandate["source_revision"]) is not int
            or type(mandate["recorded_revision"]) is not int
            or not 0 <= mandate["source_revision"] < mandate["recorded_revision"] <= revision
            or mandate["recorded_revision"] != mandate["source_revision"] + 1
            or not valid_text(mandate["session_id"], 256)
            or type(mandate["close_feature"]) is not bool
        ):
            invalid("implementation mandate is invalid")
        metadata_records = cast(
            dict[str, dict[str, object]],
            cast(object, identity_metadata),
        )
        for name, prefix in (("criteria", "C"), ("increments", "S")):
            references = cast(list[object], mandate[name])
            reference_ids: set[str] = set()
            for reference in references:
                reference_identity = (
                    reference.get("id") if type(reference) is dict else None
                )
                metadata = (
                    metadata_records.get(cast(str, reference_identity))
                    if isinstance(reference_identity, str) else None
                )
                if (
                    type(reference) is not dict
                    or set(reference) != {"id", "digest"}
                    or not isinstance(reference["id"], str)
                    or _CONTINUITY_ID.fullmatch(reference["id"]) is None
                    or reference["id"][0] != prefix
                    or reference["id"] in reference_ids
                    or not isinstance(reference["digest"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", reference["digest"]) is None
                    or metadata is None
                    or metadata["feature_id"] != mandate["feature_id"]
                    or metadata["kind"] != prefix
                ):
                    invalid("implementation mandate scope references are invalid")
                reference_ids.add(cast(str, reference["id"]))
    if task_events:
        event_revisions: set[int] = set()
        last_events: dict[str, tuple[str, int]] = {}
        previous_revision = 0
        for event in task_events:
            if (
                type(event) is not dict
                or set(event) != {
                    "task_id", "event", "reason", "evidence_ref", "session_id",
                    "recorded_revision", "attempt_count",
                }
                or not isinstance(event["task_id"], str)
                or event["task_id"] not in counters
                or event["event"] not in ("BLOCKED", "CANCELED", "REOPENED")
                or not valid_text(event["reason"], 2048)
                or not valid_text(event["evidence_ref"], 2048)
                or not valid_text(event["session_id"], 256)
                or type(event["recorded_revision"]) is not int
                or not 1 <= event["recorded_revision"] <= revision
                or event["recorded_revision"] in event_revisions
                or event["recorded_revision"] <= previous_revision
                or type(event["attempt_count"]) is not int
                or not 0 <= event["attempt_count"] <= counters[event["task_id"]]
            ):
                invalid("Task lifecycle event is invalid")
            previous = last_events.get(cast(str, event["task_id"]))
            current_kind = cast(str, event["event"])
            if previous is not None and event["attempt_count"] < previous[1]:
                invalid("Task lifecycle attempt counts cannot move backward")
            if current_kind == "REOPENED":
                if previous is None:
                    if event["attempt_count"] < 1:
                        invalid("Task reopening requires prior Task work")
                elif previous[0] not in ("BLOCKED", "CANCELED", "REOPENED"):
                    invalid("Task reopening has no blocked, canceled, or completed predecessor")
                elif previous[0] == "REOPENED" and event["attempt_count"] <= previous[1]:
                    invalid("a reopened Task requires a new attempt before reopening again")
            elif previous is not None and (
                previous[0] in ("BLOCKED", "CANCELED")
                or previous[0] == "REOPENED"
                and event["attempt_count"] <= previous[1]
            ):
                invalid("Task lifecycle event transition is invalid")
            event_revisions.add(cast(int, event["recorded_revision"]))
            previous_revision = cast(int, event["recorded_revision"])
            last_events[cast(str, event["task_id"])] = (
                cast(str, event["event"]), cast(int, event["attempt_count"]),
            )
    detour_ids: set[str] = set()
    pending_detours = 0
    for detour in detours:
        if (
            type(detour) is not dict
            or set(detour) != {
                "id", "feature_id", "kind", "title", "return_task_id",
                "result", "reported_by_session_id", "created_revision",
                "resolved_revision",
            }
            or not isinstance(detour["id"], str)
            or _CONTINUITY_ID.fullmatch(detour["id"]) is None
            or detour["id"][0] != "D" or detour["id"] not in ids
            or detour["id"] in detour_ids
            or not isinstance(detour["feature_id"], str)
            or detour["feature_id"] not in features
            or detour["kind"] not in ("RELATED", "EXTERNAL")
            or not valid_text(detour["title"], 2048)
            or not isinstance(detour["return_task_id"], str)
            or _CONTINUITY_ID.fullmatch(detour["return_task_id"]) is None
            or detour["return_task_id"][0] != "T"
            or detour["return_task_id"] not in counters
            or type(detour["created_revision"]) is not int
            or not 1 <= detour["created_revision"] <= revision
            or (
                detour["result"] is None
                and (
                    detour["reported_by_session_id"] is not None
                    or detour["resolved_revision"] is not None
                    or detour["feature_id"] not in paused_features
                )
            )
            or (
                detour["result"] is not None
                and (
                    not valid_text(detour["result"], 4096)
                    or not valid_text(detour["reported_by_session_id"], 256)
                    or type(detour["resolved_revision"]) is not int
                    or not detour["created_revision"] < detour["resolved_revision"] <= revision
                )
            )
        ):
            invalid("detour operational history is invalid")
        detour_ids.add(detour["id"])
        if detour["result"] is None:
            pending_detours += 1
    if pending_detours > 1:
        invalid("at most one detour can be unresolved")

    for item in closures:
        if (
            type(item) is not dict
            or set(item) != {"id", "feature_id", "cycle", "revision", "criteria", "session_id"}
            or not isinstance(item["id"], str) or _CONTINUITY_ID.fullmatch(item["id"]) is None
            or item["id"][0] != "D" or item["id"] not in ids
            or not isinstance(item["feature_id"], str)
            or item["feature_id"] not in features
            or type(item["cycle"]) is not int or item["cycle"] < 1
            or item["cycle"] > cast(
                ContinuityFeatureIndex, cast(object, features[item["feature_id"]])
            )["cycle"]
            or type(item["revision"]) is not int or not 1 <= item["revision"] <= revision
            or type(item["criteria"]) is not list
            or any(
                type(criterion) is not dict
                or set(criterion) != {"id", "digest"}
                or not isinstance(criterion["id"], str)
                or _CONTINUITY_ID.fullmatch(criterion["id"]) is None
                or criterion["id"][0] != "C"
                or not isinstance(criterion["digest"], str)
                or re.fullmatch(r"[0-9a-f]{64}", criterion["digest"]) is None
                for criterion in cast(list[object], item["criteria"])
            )
            or len({
                cast(dict[str, object], cast(object, criterion))["id"]
                for criterion in cast(list[object], item["criteria"])
            }) != len(cast(list[object], item["criteria"]))
            or not isinstance(item["session_id"], str) or not item["session_id"]
        ):
            invalid("Feature closure history is invalid")
    closure_cycles: dict[tuple[str, int], str] = {}
    closure_id_set: set[str] = set()
    validated_closures = cast(list[ContinuityClosure], cast(object, closures))
    for item in validated_closures:
        key = (item["feature_id"], item["cycle"])
        if key in closure_cycles or item["id"] in closure_id_set:
            invalid("Feature cycle has duplicate closure receipts")
        closure_cycles[key] = item["id"]
        closure_id_set.add(item["id"])
    for item in reopens:
        if (
            type(item) is not dict
            or set(item) != {"feature_id", "cycle", "previous_closure_id", "session_id"}
            or not isinstance(item["feature_id"], str)
            or item["feature_id"] not in features
            or type(item["cycle"]) is not int or item["cycle"] < 2
            or item["cycle"] > cast(
                ContinuityFeatureIndex, cast(object, features[item["feature_id"]])
            )["cycle"]
            or item["previous_closure_id"] != closure_cycles.get(
                (item["feature_id"], item["cycle"] - 1),
            )
            or not isinstance(item["session_id"], str) or not item["session_id"]
        ):
            invalid("Feature reopen history is invalid")
    reopen_cycles: set[tuple[str, int]] = set()
    validated_reopens = cast(list[ContinuityReopen], cast(object, reopens))
    for item in validated_reopens:
        key = (item["feature_id"], item["cycle"])
        if key in reopen_cycles:
            invalid("Feature cycle has duplicate reopen records")
        reopen_cycles.add(key)
    for feature_id, entry in features.items():
        cycle = cast(ContinuityFeatureIndex, cast(object, entry))["cycle"]
        if (
            any((feature_id, index) not in closure_cycles for index in range(1, cycle))
            or (
                cast(ContinuityFeatureIndex, cast(object, entry))["status"] == "CLOSED"
                and (feature_id, cycle) not in closure_cycles
            )
            or any((feature_id, index) not in reopen_cycles for index in range(2, cycle + 1))
        ):
            invalid("Feature closure and reopen history does not match its current cycle")

    if execution is not None:
        if (
            set(execution) != {"task_id", "attempt", "session_id", "status"}
            or not isinstance(execution["task_id"], str)
            or execution["task_id"] not in counters
            or type(execution["attempt"]) is not int or execution["attempt"] < 1
            or not isinstance(execution["session_id"], str) or not execution["session_id"]
            or execution["status"] != "OUTCOME_UNKNOWN"
        ):
            invalid("in-flight Task checkpoint is invalid")
        checked_execution = cast(ContinuityExecution, cast(object, execution))
        checked_attempts = cast(list[ContinuityAttempt], cast(object, attempts))
        active = next((
            item for item in reversed(checked_attempts)
            if item["task_id"] == checked_execution["task_id"]
            and item["attempt"] == checked_execution["attempt"]
        ), None)
        if active is None or active["status"] not in ("STARTED", "OUTCOME_UNKNOWN"):
            invalid("in-flight Task attempt does not match its history")
        if mandate is not None and active.get("mandate_id") != mandate["id"]:
            invalid("in-flight Task checkpoint is not bound to the current mandate")

    if pending is None:
        return
    if type(pending) is not dict or set(pending) not in (
        {"next_state", "writes"}, {"next_state", "writes", "read_set"},
    ):
        invalid("recovery journal is invalid")
    pending_record = cast(dict[str, object], cast(object, pending))
    next_state_value = pending_record["next_state"]
    writes_value = pending_record["writes"]
    if (
        type(next_state_value) is not dict or type(writes_value) is not list
        or cast(dict[str, object], cast(object, next_state_value)).get("revision") != revision + 1
        or cast(dict[str, object], cast(object, next_state_value)).get("pending") is not None
    ):
        invalid("recovery journal next state is inconsistent")
    next_state = cast(dict[str, object], cast(object, next_state_value))
    writes = cast(list[object], writes_value)
    read_set_value = pending_record.get("read_set", {})
    if type(read_set_value) is not dict or any(
        not isinstance(path, str) or _CONTINUITY_DOC.fullmatch(path) is None
        or (
            digest is not None
            and (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            )
        )
        for path, digest in cast(dict[str, object], read_set_value).items()
    ):
        invalid("recovery journal read set is invalid")
    validate_continuity_state(
        next_state, repository_id=repository_id, pending_state=True,
    )
    seen_paths: set[str] = set()
    for item in writes:
        if (
            type(item) is not dict or set(item) != {"path", "before", "content", "sha256"}
            or not isinstance(item["path"], str) or _CONTINUITY_DOC.fullmatch(item["path"]) is None
            or item["path"] in seen_paths
            or (item["before"] is not None and (
                not isinstance(item["before"], str)
                or re.fullmatch(r"[0-9a-f]{64}", item["before"]) is None
            ))
            or not isinstance(item["content"], str)
            or not isinstance(item["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
            or cast(dict[str, object], next_state["documents"]).get(item["path"]) != item["sha256"]
        ):
            invalid("recovery journal write is invalid")
        try:
            content_bytes = item["content"].encode("utf-8")
        except UnicodeEncodeError:
            invalid("recovery journal text is not valid UTF-8")
        if len(content_bytes) > 512 * 1024 or sha256(content_bytes).hexdigest() != item["sha256"]:
            invalid("recovery journal document digest or size is invalid")
        seen_paths.add(item["path"])
