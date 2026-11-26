"""Strict recovery and byte-level atomic persistence."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from copy import deepcopy
from importlib import import_module
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Final, Protocol, TypeAlias, TypeVar, cast

from .domain import (
    AgreementRecord,
    SliceCriterion,
    SliceCriteriaRevision,
    SliceCriteriaHead,
    AssessmentConfidence,
    AssessmentDepth,
    AssessmentDepthValue,
    AssessmentEvidence,
    AssessmentOperation,
    AutonomyEnvelope,
    AutonomyEnvelopeDelta,
    AutonomyExpansionDecision,
    AutonomyExpansionId,
    AutonomyExpansionRecord,
    AutonomyMetrics,
    CapabilityState,
    Change,
    ChangeLockAssessments,
    ChangeId,
    Checkpoint,
    ConvergenceAssessment,
    ContinuityError,
    Decision,
    DecisionId,
    DomainError,
    Drift,
    DeferredLinkedIntent,
    DeferredSideRequest,
    DiscoveryRecoveryEvidence,
    DiscoveryEvidence,
    ExperimentOutcome,
    Feature,
    FeatureId,
    D4_PROVISIONAL_SLICE_ACTIVATION_MARKER,
    SESSION_REQUEST_CHECKPOINT,
    SESSION_REQUEST_TASK,
    GitCommitRewrite,
    GitHistoryRewritePolicy,
    IrreversibleAuthorization,
    IrreversibleAuthorizationId,
    IrreversibleAuthorizationStatus,
    IrreversibleOperationKind,
    LivingSpecId,
    LivingSpecPublication,
    LockAssessment,
    LockAuthorization,
    LockId,
    LockRelease,
    Observation,
    ObservationId,
    IntakeId,
    IntentAssessment,
    MaterialDecision,
    ProvisionalSliceActivation,
    ProvisionalSliceAdoption,
    ProvisionalSliceCriterion,
    ProvisionalSliceHead,
    ProvisionalSliceProvenance,
    ProvisionalSliceRevision,
    ProvisionalSliceSourceReference,
    MaterialClarification,
    RepositoryPath,
    RepositoryInitialization,
    ReviewEvidence,
    ReviewEvidenceCategory,
    ReviewEvidenceKind,
    ReviewFinding,
    ReviewResult,
    SafetyFacts,
    SharedIntent,
    SharedIntentId,
    SemanticLock,
    SemanticLockRevision,
    State,
    StateValidationError,
    E1SchemaUpgradeReceipt,
    R3SchemaUpgradeReceipt,
    R3BSchemaUpgradeReceipt,
    CriterionTestReceipt,
    CoverageInput,
    CriterionCoverageApproval,
    SliceOutcomeReceipt,
    FeatureCompletionReceipt,
    FeatureReopenReceipt,
    ContinuityPending,
    ContinuityPendingWrite,
    ContinuityStateData,
    TaskProgress,
    TerminalArchive,
    PendingIntake,
    PendingDiscoveryRecovery,
    PendingAutonomyExpansion,
    PendingCapabilitySelection,
    CapabilityCandidate,
    CapabilitySelection,
    CapabilitySelectionId,
    ParkedWork,
    OperationalState,
    WorkId,
    WorkItem,
    WorkRelation,
    WorkRelationKind,
    VerificationEvidence,
    WorkingContext,
    request_objective_criteria_revision,
    request_objective_policy,
    provisional_slice_adoption_statement_text,
    is_sha256_digest,
    validate_state,
    validate_continuity_state,
)
from .lifecycle import (
    Classification,
    LifecycleState,
    Phase,
    ReviewVerdict,
    can_converge,
    has_acceptable_review,
)
from .discovery import DiscoveryPolicy
from .input_fingerprint import (
    INPUT_POLICY_DIGEST,
    INPUT_POLICY_ID,
    InputFingerprintError,
    InputSnapshot,
    repository_input_snapshot,
)
from .request_scoped_objective import REQUEST_SCOPED_OBJECTIVE

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
# Schema 14 contains the accepted Task-progress contract; schema 15 additionally
# records Slice criteria. Keep distinct decoders for both durable layouts.
SCHEMA: Final = 15
R3_SCHEMA: Final = 16
R3B_SCHEMA: Final = 17
FEATURE_COMPLETION_SCHEMA: Final = 18
FEATURE_CYCLE_SCHEMA: Final = 19
REQUEST_OBJECTIVE_SCHEMA: Final = 20
SEMANTIC_REQUEST_OBJECTIVE_SCHEMA: Final = 21
PROVISIONAL_SLICE_SCHEMA: Final = 22
D4_PROVISIONAL_SLICE_SCHEMA: Final = 23
D5_PROVISIONAL_SLICE_SCHEMA: Final = 24
_CONTINUITY_RECOVERY_MAX_BYTES: Final = 4 * 1024 * 1024
_D4_LIFECYCLE_OPERATIONS: Final = frozenset((
    "lifecycle", "review", "verification", "convergence", "accepted_terminal_archive",
))
_D5_LIFECYCLE_OPERATIONS: Final = _D4_LIFECYCLE_OPERATIONS
LEGACY_WRITE_SCHEMA: Final = 13
E1_TASK_PROGRESS_SCHEMA: Final = 14
INTEGRATION_REGISTRY_SCHEMA: Final = 1

_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()
STATE_FIELDS: Final = frozenset(
    (
        "schema",
        "revision",
        "active_context",
        "preserved_primary_context",
        "publications",
        "completed_side_contexts",
        "features",
        "change_refs",
        "decision_refs",
        "observation_refs",
        "drift_refs",
        "terminal_archives",
        "publication_history",
        "initialization",
        "deferred_side_request",
        "pending_intake",
        "shared_intents",
        "deferred_linked_intents",
        "locks",
        "lock_revisions",
        "lock_releases",
        "lock_authorizations",
        "change_lock_assessments",
        "active_work_id",
        "preserved_primary_work_id",
        "work_items",
        "work_relations",
        "pending_capability_selection",
        "capability_selection_history",
        "pending_discovery_recovery",
        "irreversible_authorizations",
        "pending_autonomy_expansion",
        "autonomy_expansion_history",
        "agreement_refs",
        "slice_criteria_refs",
        "slice_criteria_heads",
        "e1_schema_upgrade",
        "r3_schema_upgrade",
        "criterion_test_receipts",
    )
)
STATE_FIELDS_V16: Final = STATE_FIELDS
STATE_FIELDS_V17_LEGACY: Final = STATE_FIELDS | frozenset(("r3b_schema_upgrade", "slice_outcome_receipts"))
STATE_FIELDS_V17: Final = STATE_FIELDS_V17_LEGACY | frozenset(("criterion_coverage_approvals",))
STATE_FIELDS_V18: Final = STATE_FIELDS_V17 | frozenset(("feature_completion_receipts",))
STATE_FIELDS_V19: Final = STATE_FIELDS_V18 | frozenset(("feature_reopen_receipts",))
STATE_FIELDS_V20: Final = STATE_FIELDS_V19
STATE_FIELDS_V21: Final = STATE_FIELDS_V20
STATE_FIELDS_V22: Final = STATE_FIELDS_V21 | frozenset(("provisional_slice_refs", "provisional_slice_heads"))
STATE_FIELDS_V23: Final = STATE_FIELDS_V22 | frozenset(("provisional_slice_activation",))
STATE_FIELDS_V24: Final = STATE_FIELDS_V23 | frozenset(("provisional_slice_adoption_refs",))
CONTEXT_FIELDS: Final = frozenset(
    (
        "feature_id",
        "change_id",
        "status",
        "phase",
        "current_action",
        "focus",
        "completed_work",
        "current_work",
        "pending_work",
        "decision_ids",
        "constraint_ids",
        "review",
        "checkpoints",
        "verification",
        "convergence_complete",
        "classification",
        "repair_count",
        "resume_point",
        "change_ids",
        "observation_ids",
        "paused_pair",
        "blocker",
        "safe_next_step",
        "future_lock_relations",
        "unresolved_material_decision",
        "side_of",
        "publication",
        "safety_facts",
        "review_evidence",
        "review_findings",
        "autonomy_envelope",
        "autonomy_metrics",
        "feature_agreement_ref",
        "slice_agreement_ref",
        "task_progress",
    )
)
_CAPABILITY_SELECTION_FIELDS: Final = frozenset(("pending_capability_selection", "capability_selection_history"))
_DISCOVERY_RECOVERY_FIELD: Final = frozenset(("pending_discovery_recovery",))
STATE_FIELDS_V15: Final = STATE_FIELDS_V16 - frozenset(("r3_schema_upgrade", "criterion_test_receipts"))
STATE_FIELDS_V14: Final = STATE_FIELDS_V15 - frozenset(("slice_criteria_refs", "slice_criteria_heads"))
STATE_FIELDS_V13: Final = STATE_FIELDS_V14 - frozenset(("e1_schema_upgrade",))
STATE_FIELDS_V12: Final = STATE_FIELDS_V13 - frozenset(("agreement_refs",))
STATE_FIELDS_V11: Final = STATE_FIELDS_V12 - frozenset(("pending_autonomy_expansion", "autonomy_expansion_history"))
STATE_FIELDS_V10: Final = STATE_FIELDS_V11 - frozenset(("irreversible_authorizations",))
STATE_FIELDS_V9_PRE_SELECTION: Final = STATE_FIELDS_V10 - _CAPABILITY_SELECTION_FIELDS - _DISCOVERY_RECOVERY_FIELD
STATE_FIELDS_V9_PRE_RECOVERY: Final = STATE_FIELDS_V10 - _DISCOVERY_RECOVERY_FIELD
STATE_FIELDS_V8: Final = STATE_FIELDS_V10 - frozenset(("active_work_id", "preserved_primary_work_id", "work_items", "work_relations")) - _CAPABILITY_SELECTION_FIELDS - _DISCOVERY_RECOVERY_FIELD | frozenset(("parked_work",))
STATE_FIELDS_V7: Final = STATE_FIELDS_V8 - frozenset(("parked_work",))
STATE_FIELDS_V5: Final = STATE_FIELDS_V7 - frozenset(
    ("locks", "lock_revisions", "lock_releases", "lock_authorizations", "change_lock_assessments")
)
CONTEXT_FIELDS_V13: Final = CONTEXT_FIELDS - frozenset(("task_progress",))
CONTEXT_FIELDS_V12: Final = CONTEXT_FIELDS_V13 - frozenset(("feature_agreement_ref", "slice_agreement_ref"))
CONTEXT_FIELDS_V9: Final = CONTEXT_FIELDS_V12 - frozenset(("autonomy_envelope", "autonomy_metrics"))
CONTEXT_FIELDS_V2: Final = CONTEXT_FIELDS_V9 - frozenset(("safety_facts",))


class DecodeError(Exception):
    """Raised when JSON does not exactly match its persisted schema."""


class RecoveryError(Exception):
    """Raised when persisted authority cannot be safely recovered."""


class IntegrationRegistryError(Exception):
    """Raised when durable integration registration cannot be safely used."""


class AtomicFailure(Exception):
    """Raised when publication did not cross the replacement boundary."""


class BinaryFile(Protocol):
    def write(self, data: bytes, /) -> int: ...

    def flush(self) -> None: ...

    def fileno(self) -> int: ...

    def close(self) -> None: ...


class FileOperations:
    """Injectable operating-system calls for deterministic failure tests."""

    def flush(self, handle: BinaryFile) -> None:
        handle.flush()

    def write(self, handle: BinaryFile, data: bytes) -> int:
        return handle.write(data)

    def fsync_file(self, descriptor: int) -> None:
        os.fsync(descriptor)

    def close(self, handle: BinaryFile) -> None:
        handle.close()

    def replace(self, source: Path, target: Path) -> None:
        os.replace(source, target)

    def fsync_directory(self, directory: Path) -> None:
        if os.name == "posix":
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def unlink(self, path: Path) -> None:
        path.unlink(missing_ok=True)


def encode_json(value: JsonValue) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def decode_json(text: str) -> JsonValue:
    def reject_constant(value: str) -> float:
        raise DecodeError(f"non-finite JSON number {value}")

    def reject_duplicate_keys(
        pairs: list[tuple[str, JsonValue]],
    ) -> dict[str, JsonValue]:
        decoded: dict[str, JsonValue] = {}
        for key, item in pairs:
            if key in decoded:
                raise DecodeError(f"duplicate JSON key {key}")
            decoded[key] = item
        return decoded

    try:
        value: JsonValue = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (ValueError, RecursionError, DecodeError) as error:
        raise DecodeError("invalid JSON") from error
    return value


def _object(value: JsonValue, fields: frozenset[str], label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise DecodeError(f"{label} fields are not exact")
    return value


def _string(value: JsonValue, label: str) -> str:
    if not isinstance(value, str):
        raise DecodeError(f"{label} must be a string")
    return value


def _optional_string(value: JsonValue, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _boolean(value: JsonValue, label: str) -> bool:
    if type(value) is not bool:
        raise DecodeError(f"{label} must be a boolean")
    return value


def _integer(value: JsonValue, label: str) -> int:
    if type(value) is not int:
        raise DecodeError(f"{label} must be an integer")
    return value


def _optional_integer(value: JsonValue, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label)


def _array(value: JsonValue, label: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise DecodeError(f"{label} must be an array")
    return value


def _strings(value: JsonValue, label: str) -> tuple[str, ...]:
    return tuple(_string(item, label) for item in _array(value, label))


def _publication_to_json(publication: LivingSpecPublication | None) -> JsonValue:
    if publication is None:
        return None
    return {
        "living_spec_id": publication.living_spec_id,
        "revision": publication.revision,
        "capability": publication.capability,
        "path": str(publication.path),
        "previous_revision": publication.previous_revision,
        "feature_id": publication.feature_id,
        "change_id": publication.change_id,
    }


def _publication_from_json(value: JsonValue) -> LivingSpecPublication | None:
    if value is None:
        return None
    raw = _object(
        value,
        frozenset(
            (
                "living_spec_id",
                "revision",
                "capability",
                "path",
                "previous_revision",
                "feature_id",
                "change_id",
            )
        ),
        "publication",
    )
    previous = _optional_string(raw["previous_revision"], "previous_revision")
    feature_id = _string(raw["feature_id"], "publication feature_id")
    change_id = _string(raw["change_id"], "publication change_id")
    return LivingSpecPublication(
        LivingSpecId(_string(raw["living_spec_id"], "living_spec_id")),
        _string(raw["revision"], "publication revision"),
        _string(raw["capability"], "publication capability"),
        RepositoryPath(_string(raw["path"], "publication path")),
        previous,
        FeatureId(feature_id),
        ChangeId(change_id),
    )


def _required_publication_from_json(value: JsonValue) -> LivingSpecPublication:
    publication = _publication_from_json(value)
    if publication is None:
        raise DecodeError("publication array entry cannot be null")
    return publication


def _context_to_json(context: WorkingContext | None, schema: int = SCHEMA) -> JsonValue:
    if context is None:
        return None
    review: JsonValue = None
    if context.review is not None:
        review = {
            "verdict": context.review.verdict,
            "findings": list(context.review.findings),
            "safe_to_proceed": context.review.safe_to_proceed,
        }
    paused: JsonValue = None
    if context.paused_pair is not None:
        paused = [context.paused_pair[0], context.paused_pair[1]]
    value: dict[str, JsonValue] = {
        "feature_id": context.feature_id,
        "change_id": context.change_id,
        "status": context.status,
        "phase": context.phase,
        "current_action": context.current_action,
        "focus": context.focus,
        "completed_work": list(context.completed_work),
        "current_work": context.current_work,
        "pending_work": list(context.pending_work),
        "decision_ids": list(context.decision_ids),
        "constraint_ids": list(context.constraint_ids),
        "review": review,
        "checkpoints": [
            {"name": item.name, "valid": item.valid} for item in context.checkpoints
        ],
        "verification": {
            "evidence": list(context.verification.evidence),
            "sufficient": context.verification.sufficient,
        },
        "convergence_complete": context.convergence_complete,
        "classification": context.classification,
        "repair_count": context.repair_count,
        "resume_point": context.resume_point,
        "change_ids": list(context.change_ids),
        "observation_ids": list(context.observation_ids),
        "paused_pair": paused,
        "blocker": context.blocker,
        "safe_next_step": context.safe_next_step,
        "future_lock_relations": list(context.future_lock_relations),
        "unresolved_material_decision": context.unresolved_material_decision,
        "side_of": context.side_of,
        "publication": _publication_to_json(context.publication),
        "safety_facts": {
            "unresolved_decision_details": list(context.safety_facts.unresolved_decision_details),
            "material_clarifications": [
                {"id": item.id, "decision": item.decision, "options": list(item.options)}
                for item in context.safety_facts.material_clarifications
            ],
            "scope": list(context.safety_facts.scope),
            "risks": list(context.safety_facts.risks),
            "review_depth": context.safety_facts.review_depth,
            "verification_depth": context.safety_facts.verification_depth,
            "convergence_depth": context.safety_facts.convergence_depth,
        },
        "review_evidence": [
            {
                "category": item.category,
                "kind": item.kind,
                "source": item.source,
                "required": item.required,
                "value": item.value,
                "reference": item.reference,
            }
            for item in context.review_evidence
        ],
        "review_findings": [
            {
                "severity": item.severity,
                "category": item.category,
                "message": item.message,
                "rationale": item.rationale,
                "evidence_reference": item.evidence_reference,
                "resolved": item.resolved,
            }
            for item in context.review_findings
        ],
        "autonomy_envelope": {
            "goal": context.autonomy_envelope.goal,
            "allowed_paths": [str(path) for path in context.autonomy_envelope.allowed_paths],
            "operation_budget": context.autonomy_envelope.operation_budget,
            "reversible_actions": list(context.autonomy_envelope.reversible_actions),
            "external_wait_allowed": context.autonomy_envelope.external_wait_allowed,
            "retry_budget": context.autonomy_envelope.retry_budget,
            "irreversible_permissions": list(context.autonomy_envelope.irreversible_permissions),
            "stop_conditions": list(context.autonomy_envelope.stop_conditions),
        },
        "autonomy_metrics": {
            "human_material_decisions": context.autonomy_metrics.human_material_decisions,
            "operational_handoffs": context.autonomy_metrics.operational_handoffs,
            "automatic_operations": context.autonomy_metrics.automatic_operations,
        },
        "feature_agreement_ref": context.feature_agreement_ref,
        "slice_agreement_ref": context.slice_agreement_ref,
    }
    if schema >= 14:
        report = context.task_progress
        value["task_progress"] = None if report is None else {
            "task_id": report.task_id,
            "checkpoint": report.checkpoint,
            "statement": report.statement,
            "report_kind": report.report_kind,
            "source_kind": report.source_kind,
            "source_reference": report.source_reference,
            "evidence_refs": list(report.evidence_refs),
            "recorded_revision": report.recorded_revision,
        }
    elif context.task_progress is not None:
        raise ValueError("typed E1 Task progress cannot be encoded in an old schema")
    return value


def _review(value: JsonValue) -> ReviewResult | None:
    if value is None:
        return None
    raw = _object(
        value,
        frozenset(("verdict", "findings", "safe_to_proceed")),
        "review",
    )
    try:
        verdict = ReviewVerdict(_string(raw["verdict"], "review verdict"))
    except ValueError as error:
        raise DecodeError("invalid review verdict") from error
    return ReviewResult(
        verdict,
        _strings(raw["findings"], "review findings"),
        _boolean(raw["safe_to_proceed"], "review safe_to_proceed"),
    )


def _safety_facts(value: JsonValue) -> SafetyFacts:
    fields = frozenset(
            (
                "unresolved_decision_details",
                "scope",
                "risks",
                "review_depth",
                "verification_depth",
                "convergence_depth",
            )
        )
    if not isinstance(value, dict):
        raise DecodeError("safety_facts fields are not exact")
    has_typed = "material_clarifications" in value
    raw = _object(value, fields | frozenset(("material_clarifications",)) if has_typed else fields, "safety_facts")
    return SafetyFacts(
        _strings(raw["unresolved_decision_details"], "unresolved decision details"),
        _strings(raw["scope"], "safety scope"),
        _strings(raw["risks"], "safety risks"),
        _optional_string(raw["review_depth"], "review depth"),
        _optional_string(raw["verification_depth"], "verification depth"),
        _optional_string(raw["convergence_depth"], "convergence depth"),
        tuple(
            MaterialClarification(
                _string(item["id"], "clarification id"),
                _string(item["decision"], "clarification decision"),
                _strings(item["options"], "clarification options"),
            )
            for value_item in _array(raw["material_clarifications"], "material clarifications")
            for item in [_object(value_item, frozenset(("id", "decision", "options")), "material clarification")]
        ) if has_typed else (),
    )


def _review_evidence(value: JsonValue) -> tuple[ReviewEvidence, ...]:
    records: list[ReviewEvidence] = []
    for value_item in _array(value, "review evidence"):
        item = _object(
            value_item,
            frozenset(("category", "kind", "source", "required", "value", "reference")),
            "review evidence",
        )
        try:
            category = ReviewEvidenceCategory(_string(item["category"], "review evidence category"))
            kind = ReviewEvidenceKind(_string(item["kind"], "review evidence kind"))
        except ValueError as error:
            raise DecodeError("invalid review evidence enum") from error
        records.append(
            ReviewEvidence(
                category,
                kind,
                _string(item["source"], "review evidence source"),
                _boolean(item["required"], "review evidence required"),
                _string(item["value"], "review evidence value"),
                _optional_string(item["reference"], "review evidence reference"),
            )
        )
    return tuple(records)


def _review_findings(value: JsonValue) -> tuple[ReviewFinding, ...]:
    records: list[ReviewFinding] = []
    for value_item in _array(value, "review findings"):
        item = _object(
            value_item,
            frozenset(("severity", "category", "message", "rationale", "evidence_reference", "resolved")),
            "review finding",
        )
        try:
            severity = ReviewVerdict(_string(item["severity"], "review finding severity"))
            category = ReviewEvidenceCategory(_string(item["category"], "review finding category"))
        except ValueError as error:
            raise DecodeError("invalid review finding enum") from error
        records.append(
            ReviewFinding(
                severity,
                category,
                _string(item["message"], "review finding message"),
                _string(item["rationale"], "review finding rationale"),
                _optional_string(item["evidence_reference"], "review finding evidence reference"),
                _boolean(item["resolved"], "review finding resolved"),
            )
        )
    return tuple(records)


def _context_from_json(value: JsonValue, schema: int) -> WorkingContext | None:
    if value is None:
        return None
    fields = (
        CONTEXT_FIELDS if schema >= 14
        else CONTEXT_FIELDS_V13 if schema == 13
        else CONTEXT_FIELDS_V12 if schema >= 10 or (isinstance(value, dict) and set(value) == set(CONTEXT_FIELDS_V12))
        else CONTEXT_FIELDS_V9
    )
    raw = _object(value, fields, "context")
    try:
        status = LifecycleState(_string(raw["status"], "status"))
        phase = Phase(_string(raw["phase"], "phase"))
        classification_text = _optional_string(raw["classification"], "classification")
        classification = (
            None if classification_text is None else Classification(classification_text)
        )
    except ValueError as error:
        raise DecodeError("invalid lifecycle enum") from error
    checkpoints: list[Checkpoint] = []
    for value_item in _array(raw["checkpoints"], "checkpoints"):
        item = _object(
            value_item, frozenset(("name", "valid")), "checkpoint"
        )
        checkpoints.append(
            Checkpoint(
                _string(item["name"], "checkpoint name"),
                _boolean(item["valid"], "checkpoint valid"),
            )
        )
    verification_raw = _object(
        raw["verification"], frozenset(("evidence", "sufficient")), "verification"
    )
    paused: tuple[LifecycleState, Phase] | None = None
    if raw["paused_pair"] is not None:
        items = _array(raw["paused_pair"], "paused_pair")
        if len(items) != 2:
            raise DecodeError("paused_pair must contain two values")
        try:
            paused = (
                LifecycleState(_string(items[0], "paused status")),
                Phase(_string(items[1], "paused phase")),
            )
        except ValueError as error:
            raise DecodeError("invalid paused pair") from error
    change_id = _optional_string(raw["change_id"], "change_id")
    side_of = _optional_string(raw["side_of"], "side_of")
    task_progress = None
    if schema >= 14 and raw["task_progress"] is not None:
        report = _object(raw["task_progress"], frozenset((
            "task_id", "checkpoint", "statement", "report_kind", "source_kind",
            "source_reference", "evidence_refs", "recorded_revision",
        )), "Task progress")
        task_progress = TaskProgress(
            _string(report["task_id"], "Task progress id"),
            _string(report["checkpoint"], "Task checkpoint"),
            _string(report["statement"], "Task statement"),
            _string(report["report_kind"], "Task report kind"),
            _string(report["source_kind"], "Task source kind"),
            _optional_string(report["source_reference"], "Task source reference"),
            _strings(report["evidence_refs"], "Task evidence references"),
            _integer(report["recorded_revision"], "Task report revision"),
        )
    return WorkingContext(
        FeatureId(_string(raw["feature_id"], "feature_id")),
        None if change_id is None else ChangeId(change_id),
        status,
        phase,
        _optional_string(raw["current_action"], "current_action"),
        _optional_string(raw["focus"], "focus"),
        _strings(raw["completed_work"], "completed_work"),
        _optional_string(raw["current_work"], "current_work"),
        _strings(raw["pending_work"], "pending_work"),
        tuple(DecisionId(item) for item in _strings(raw["decision_ids"], "decision_ids")),
        tuple(
            DecisionId(item) for item in _strings(raw["constraint_ids"], "constraint_ids")
        ),
        _review(raw["review"]),
        tuple(checkpoints),
        VerificationEvidence(
            _strings(verification_raw["evidence"], "verification evidence"),
            _boolean(verification_raw["sufficient"], "verification sufficient"),
        ),
        _boolean(raw["convergence_complete"], "convergence_complete"),
        classification,
        _integer(raw["repair_count"], "repair_count"),
        _optional_string(raw["resume_point"], "resume_point"),
        tuple(ChangeId(item) for item in _strings(raw["change_ids"], "change_ids")),
        tuple(
            ObservationId(item)
            for item in _strings(raw["observation_ids"], "observation_ids")
        ),
        paused,
        _optional_string(raw["blocker"], "blocker"),
        _optional_string(raw["safe_next_step"], "safe_next_step"),
        _strings(raw["future_lock_relations"], "future_lock_relations"),
        _boolean(raw["unresolved_material_decision"], "unresolved_material_decision"),
        None if side_of is None else FeatureId(side_of),
        _publication_from_json(raw["publication"]),
        SafetyFacts() if schema == 2 else _safety_facts(raw["safety_facts"]),
        _review_evidence(raw["review_evidence"]),
        _review_findings(raw["review_findings"]),
        _autonomy_envelope(raw["autonomy_envelope"]) if schema >= 10 else AutonomyEnvelope(_string(raw["focus"], "focus") if raw["focus"] is not None else "legacy work"),
        _autonomy_metrics(raw["autonomy_metrics"]) if schema >= 10 else AutonomyMetrics(),
        feature_agreement_ref=_optional_string(raw["feature_agreement_ref"], "Feature agreement reference") if schema >= 13 else None,
        slice_agreement_ref=_optional_string(raw["slice_agreement_ref"], "Slice agreement reference") if schema >= 13 else None,
        task_progress=task_progress,
    )


_AGREEMENT_FIELDS: Final = frozenset((
    "id", "kind", "feature_id", "change_id", "title", "statement", "source",
    "source_reference", "repository_id", "task_id", "initial_task", "checkpoint",
))


def _agreement_to_json(agreement: AgreementRecord) -> JsonValue:
    """Encode one immutable agreement without inferring missing user consent."""
    return {name: getattr(agreement, name) for name in _AGREEMENT_FIELDS}


def _agreement_from_json(value: JsonValue) -> AgreementRecord:
    raw = _object(value, _AGREEMENT_FIELDS, "agreement")
    return AgreementRecord(
        _string(raw["id"], "agreement id"),
        _string(raw["kind"], "agreement kind"),
        FeatureId(_string(raw["feature_id"], "agreement feature")),
        ChangeId(_string(raw["change_id"], "agreement change")),
        _string(raw["title"], "agreement title"),
        _string(raw["statement"], "agreement statement"),
        _string(raw["source"], "agreement source"),
        _string(raw["source_reference"], "agreement source reference"),
        _string(raw["repository_id"], "agreement repository"),
        _optional_string(raw["task_id"], "agreement task"),
        _optional_string(raw["initial_task"], "agreement initial task"),
        _optional_string(raw["checkpoint"], "agreement checkpoint"),
    )


_SLICE_CRITERIA_REVISION_FIELDS: Final = frozenset((
    "id", "slice_agreement_ref", "repository_id", "outcome", "example",
    "inclusions", "exclusions", "decisions", "criteria", "source",
    "source_reference", "previous_revision_id", "reason", "decision_statement",
))


def _slice_criteria_to_json(revision: SliceCriteriaRevision) -> JsonValue:
    """Encode immutable intent; outcome evidence is deliberately not a verdict."""
    return {
        "id": revision.id,
        "slice_agreement_ref": revision.slice_agreement_ref,
        "repository_id": revision.repository_id,
        "outcome": revision.outcome,
        "example": revision.example,
        "inclusions": list(revision.inclusions),
        "exclusions": list(revision.exclusions),
        "decisions": list(revision.decisions),
        "criteria": [
            {
                "id": item.id,
                "expected_result": item.expected_result,
                "expected_evidence": item.expected_evidence,
                **({"provenance": item.provenance} if item.provenance is not None else {}),
            }
            for item in revision.criteria
        ],
        "source": revision.source,
        "source_reference": revision.source_reference,
        "previous_revision_id": revision.previous_revision_id,
        "reason": revision.reason,
        "decision_statement": revision.decision_statement,
    }


def _slice_criteria_from_json(value: JsonValue) -> SliceCriteriaRevision:
    raw = _object(value, _SLICE_CRITERIA_REVISION_FIELDS, "Slice criteria revision")
    criteria = tuple(
        SliceCriterion(
            _string(item["id"], "criterion id"),
            _string(item["expected_result"], "expected result"),
            _string(item["expected_evidence"], "expected evidence"),
            _string(item["provenance"], "criterion provenance")
            if "provenance" in item else None,
        )
        for entry in _array(raw["criteria"], "criteria")
        for item in (
            _object(
                entry,
                frozenset(("id", "expected_result", "expected_evidence", "provenance"))
                if isinstance(entry, dict) and "provenance" in entry
                else frozenset(("id", "expected_result", "expected_evidence")),
                "criterion",
            ),
        )
    )
    return SliceCriteriaRevision(
        _string(raw["id"], "criteria revision id"),
        _string(raw["slice_agreement_ref"], "criteria agreement ref"),
        _string(raw["repository_id"], "criteria repository"),
        _string(raw["outcome"], "criteria outcome"),
        _string(raw["example"], "criteria example"),
        tuple(_string(item, "inclusion") for item in _array(raw["inclusions"], "inclusions")),
        tuple(_string(item, "exclusion") for item in _array(raw["exclusions"], "exclusions")),
        tuple(_string(item, "decision") for item in _array(raw["decisions"], "decisions")),
        criteria,
        _string(raw["source"], "criteria source"),
        _string(raw["source_reference"], "criteria source reference"),
        _optional_string(raw["previous_revision_id"], "previous criteria revision"),
        _optional_string(raw["reason"], "criteria amendment reason"),
        _optional_string(raw["decision_statement"], "criteria decision statement"),
    )


_PROVISIONAL_SLICE_FIELDS: Final = frozenset((
    "proposal_id", "revision_id", "revision", "repository_id", "feature_id",
    "title", "outcome", "example", "inclusions", "exclusions",
    "considerations", "open_questions", "criteria", "provenance",
    "previous_revision_id", "digest",
))
_PROVISIONAL_SLICE_GENESIS_FIELDS: Final = (
    "repository_id", "feature_id", "title", "outcome", "example",
    "inclusions", "exclusions", "considerations", "open_questions",
    "criteria", "provenance",
)
_PROVISIONAL_SLICE_CRITERION_FIELDS: Final = frozenset((
    "id", "expected_result", "expected_evidence",
))


def _provisional_slice_provenance_to_json(
    provenance: ProvisionalSliceProvenance,
) -> JsonValue:
    if provenance.kind == "AGENT_SUGGESTION":
        return {"kind": "AGENT_SUGGESTION"}
    source = provenance.source_reference
    if provenance.kind == "USER_EXPRESSED" and source is not None:
        return {
            "kind": "USER_EXPRESSED",
            "source_reference": {
                "session_id": source.session_id,
                "message_id": source.message_id,
            },
            "expression": provenance.expression,
            "expression_sha256": provenance.expression_sha256,
        }
    raise DecodeError("provisional Slice provenance shape is invalid")


def _provisional_slice_payload(revision: ProvisionalSliceRevision) -> dict[str, JsonValue]:
    return {
        "proposal_id": revision.proposal_id,
        "revision_id": revision.revision_id,
        "revision": revision.revision,
        "repository_id": revision.repository_id,
        "feature_id": str(revision.feature_id),
        "title": revision.title,
        "outcome": revision.outcome,
        "example": revision.example,
        "inclusions": list(revision.inclusions),
        "exclusions": list(revision.exclusions),
        "considerations": list(revision.considerations),
        "open_questions": list(revision.open_questions),
        "criteria": [
            {
                "id": item.id,
                "expected_result": item.expected_result,
                "expected_evidence": item.expected_evidence,
            }
            for item in revision.criteria
        ],
        "provenance": _provisional_slice_provenance_to_json(revision.provenance),
        "previous_revision_id": revision.previous_revision_id,
    }


def _provisional_slice_genesis_id(revision: ProvisionalSliceRevision) -> str:
    payload = _provisional_slice_payload(revision)
    genesis = {name: payload[name] for name in _PROVISIONAL_SLICE_GENESIS_FIELDS}
    return "ps-" + sha256(encode_json(genesis)).hexdigest()


def _provisional_slice_digest(revision: ProvisionalSliceRevision) -> str:
    return sha256(encode_json(_provisional_slice_payload(revision))).hexdigest()


def _provisional_slice_to_json(revision: ProvisionalSliceRevision) -> JsonValue:
    return {**_provisional_slice_payload(revision), "digest": revision.digest}


def _provisional_nonblank(value: JsonValue, label: str) -> str:
    text = _string(value, label)
    if not text.strip():
        raise DecodeError(f"{label} must not be blank")
    return text


def _provisional_slice_provenance_from_json(value: JsonValue) -> ProvisionalSliceProvenance:
    if not isinstance(value, dict):
        raise DecodeError("provisional Slice provenance must be an object")
    kind = _string(value.get("kind"), "provisional Slice provenance kind")
    if kind == "AGENT_SUGGESTION":
        raw = _object(value, frozenset(("kind",)), "agent suggestion provenance")
        return ProvisionalSliceProvenance(_string(raw["kind"], "provenance kind"))
    if kind != "USER_EXPRESSED":
        raise DecodeError("provisional Slice provenance kind is invalid")
    raw = _object(
        value,
        frozenset(("kind", "source_reference", "expression", "expression_sha256")),
        "user expression provenance",
    )
    source = _object(
        raw["source_reference"],
        frozenset(("session_id", "message_id")),
        "user expression source reference",
    )
    session_id = _provisional_nonblank(source["session_id"], "expression session id")
    message_id = _provisional_nonblank(source["message_id"], "expression message id")
    expression = _provisional_nonblank(raw["expression"], "user expression")
    expression_sha256 = _string(raw["expression_sha256"], "expression SHA-256")
    if (
        re.fullmatch(r"ses_[A-Za-z0-9_-]{3,128}", session_id) is None
        or re.fullmatch(r"msg_[A-Za-z0-9_-]{3,128}", message_id) is None
        or not is_sha256_digest(expression_sha256)
        or expression_sha256 != sha256(expression.encode("utf-8")).hexdigest()
    ):
        raise DecodeError("user expression provenance is invalid")
    return ProvisionalSliceProvenance(
        "USER_EXPRESSED",
        ProvisionalSliceSourceReference(session_id, message_id),
        expression,
        expression_sha256,
    )


def _provisional_slice_from_json(
    value: JsonValue, *, schema: int = PROVISIONAL_SLICE_SCHEMA,
) -> ProvisionalSliceRevision:
    raw = _object(value, _PROVISIONAL_SLICE_FIELDS, "provisional Slice revision")
    proposal_id = _string(raw["proposal_id"], "provisional Slice proposal id")
    revision_id = _string(raw["revision_id"], "provisional Slice revision id")
    revision_number = _integer(raw["revision"], "provisional Slice revision")
    repository_id = _provisional_nonblank(raw["repository_id"], "provisional Slice repository")
    feature_id = _provisional_nonblank(raw["feature_id"], "provisional Slice Feature")
    title = _provisional_nonblank(raw["title"], "provisional Slice title")
    outcome = _provisional_nonblank(raw["outcome"], "provisional Slice outcome")
    example = (
        None if raw["example"] is None
        else _provisional_nonblank(raw["example"], "provisional Slice example")
    )
    arrays = tuple(
        tuple(
            _provisional_nonblank(item, f"provisional Slice {label}")
            for item in _array(raw[label], f"provisional Slice {label}")
        )
        for label in ("inclusions", "exclusions", "considerations", "open_questions")
    )
    criteria = tuple(
        ProvisionalSliceCriterion(
            _provisional_nonblank(item["id"], "provisional criterion id"),
            _provisional_nonblank(item["expected_result"], "provisional criterion result"),
            _provisional_nonblank(item["expected_evidence"], "provisional criterion evidence"),
        )
        for entry in _array(raw["criteria"], "provisional Slice criteria")
        for item in (
            _object(entry, _PROVISIONAL_SLICE_CRITERION_FIELDS, "provisional Slice criterion"),
        )
    )
    provenance = _provisional_slice_provenance_from_json(raw["provenance"])
    previous_revision_id = _optional_string(
        raw["previous_revision_id"], "previous provisional Slice revision",
    )
    digest = _string(raw["digest"], "provisional Slice digest")
    record = ProvisionalSliceRevision(
        proposal_id,
        revision_id,
        revision_number,
        repository_id,
        FeatureId(feature_id),
        title,
        outcome,
        example,
        arrays[0],
        arrays[1],
        arrays[2],
        arrays[3],
        criteria,
        provenance,
        previous_revision_id,
        digest,
    )
    _validate_provisional_slice_record(record, bounded=schema >= D4_PROVISIONAL_SLICE_SCHEMA)
    return record


def _validate_provisional_slice_record(
    revision: ProvisionalSliceRevision, *, bounded: bool = False,
) -> None:
    if type(revision) is not ProvisionalSliceRevision:
        raise DecodeError("provisional Slice revision is invalid")
    if any(type(value) is not str for value in (
        revision.proposal_id, revision.revision_id, revision.repository_id,
        revision.feature_id, revision.title, revision.outcome, revision.digest,
    )):
        raise DecodeError("provisional Slice revision strings are invalid")
    if revision.example is not None and type(revision.example) is not str:
        raise DecodeError("provisional Slice example is invalid")
    if (
        revision.previous_revision_id is not None
        and type(revision.previous_revision_id) is not str
    ):
        raise DecodeError("provisional Slice predecessor is invalid")
    if type(revision.provenance) is not ProvisionalSliceProvenance:
        raise DecodeError("provisional Slice provenance is invalid")
    if _provisional_slice_provenance_from_json(
        _provisional_slice_provenance_to_json(revision.provenance)
    ) != revision.provenance:
        raise DecodeError("provisional Slice provenance is invalid")
    if type(revision.revision) is not int or revision.revision < 1:
        raise DecodeError("provisional Slice revision number is invalid")
    if any(type(values) is not tuple for values in (
        revision.inclusions, revision.exclusions, revision.considerations,
        revision.open_questions, revision.criteria,
    )):
        raise DecodeError("provisional Slice arrays are invalid")
    if any(
        type(value) is not str or not value.strip()
        for values in (
            revision.inclusions, revision.exclusions, revision.considerations,
            revision.open_questions,
        )
        for value in values
    ):
        raise DecodeError("provisional Slice scope text is invalid")
    if any(type(item) is not ProvisionalSliceCriterion for item in revision.criteria):
        raise DecodeError("provisional Slice criterion shape is invalid")
    if any(
        any(type(value) is not str or not value.strip() for value in (
            item.id, item.expected_result, item.expected_evidence,
        ))
        or re.fullmatch(r"c-[1-9][0-9]*", item.id) is None
        for item in revision.criteria
    ):
        raise DecodeError("provisional Slice criterion is invalid")
    if (
        re.fullmatch(r"ps-[0-9a-f]{64}", revision.proposal_id) is None
        or revision.revision_id != f"{revision.proposal_id}-r{revision.revision}"
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", revision.feature_id) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", revision.repository_id) is None
        or any(type(value) is not str or not value.strip() for value in (
            revision.repository_id, revision.feature_id, revision.title, revision.outcome,
        ))
        or (revision.example is not None and not revision.example.strip())
        or len({item.id for item in revision.criteria}) != len(revision.criteria)
        or (revision.revision == 1 and revision.previous_revision_id is not None)
        or (revision.revision > 1 and (
            not revision.previous_revision_id
            or re.fullmatch(
                r"ps-[0-9a-f]{64}-r[1-9][0-9]*",
                revision.previous_revision_id,
            ) is None
        ))
        or not is_sha256_digest(revision.digest)
        or revision.digest != _provisional_slice_digest(revision)
    ):
        raise DecodeError("provisional Slice revision is invalid")
    if revision.revision == 1 and revision.proposal_id != _provisional_slice_genesis_id(revision):
        raise DecodeError("provisional Slice genesis identity is invalid")
    if bounded:
        texts = (
            revision.repository_id, str(revision.feature_id), revision.title,
            revision.outcome, *( () if revision.example is None else (revision.example,) ),
            *revision.inclusions, *revision.exclusions, *revision.considerations,
            *revision.open_questions,
            *(value for item in revision.criteria for value in (
                item.id, item.expected_result, item.expected_evidence,
            )),
        )
        if (
            revision.provenance.kind != "AGENT_SUGGESTION"
            or any(len(value.encode("utf-8")) > 2048 for value in texts)
            or any(len(values) > 16 for values in (
                revision.inclusions, revision.exclusions,
                revision.considerations, revision.open_questions,
                revision.criteria,
            ))
            or len(encode_json(_provisional_slice_to_json(revision))) > 64 * 1024
        ):
            raise DecodeError("D4 provisional Slice payload exceeds its fixed bounds")


def _provisional_slice_head_from_json(value: JsonValue) -> ProvisionalSliceHead:
    raw = _object(
        value,
        frozenset(("proposal_id", "revision_id", "revision", "digest")),
        "provisional Slice head",
    )
    proposal_id = _string(raw["proposal_id"], "provisional Slice head proposal")
    revision_id = _string(raw["revision_id"], "provisional Slice head revision id")
    revision = _integer(raw["revision"], "provisional Slice head revision")
    digest = _string(raw["digest"], "provisional Slice head digest")
    if (
        type(revision) is not int or revision < 1
        or re.fullmatch(r"ps-[0-9a-f]{64}", proposal_id) is None
        or revision_id != f"{proposal_id}-r{revision}"
        or not is_sha256_digest(digest)
    ):
        raise DecodeError("provisional Slice head is invalid")
    return ProvisionalSliceHead(proposal_id, revision_id, revision, digest)


_PROVISIONAL_SLICE_ACTIVATION_FIELDS: Final = frozenset((
    "marker", "source_schema", "source_revision", "source_sha256",
    "repository_id", "feature_id", "feature_agreement_ref",
    "slice_agreement_ref", "recorded_revision",
))


def _provisional_slice_activation_to_json(
    activation: ProvisionalSliceActivation | None,
) -> JsonValue:
    if activation is None:
        return None
    return {
        "marker": activation.marker,
        "source_schema": activation.source_schema,
        "source_revision": activation.source_revision,
        "source_sha256": activation.source_sha256,
        "repository_id": activation.repository_id,
        "feature_id": str(activation.feature_id),
        "feature_agreement_ref": activation.feature_agreement_ref,
        "slice_agreement_ref": activation.slice_agreement_ref,
        "recorded_revision": activation.recorded_revision,
    }


def _provisional_slice_activation_from_json(value: JsonValue) -> ProvisionalSliceActivation:
    raw = _object(value, _PROVISIONAL_SLICE_ACTIVATION_FIELDS, "D4 activation")
    return ProvisionalSliceActivation(
        _string(raw["marker"], "D4 activation marker"),
        _integer(raw["source_schema"], "D4 source schema"),
        _integer(raw["source_revision"], "D4 source revision"),
        _string(raw["source_sha256"], "D4 source SHA-256"),
        _string(raw["repository_id"], "D4 repository"),
        FeatureId(_string(raw["feature_id"], "D4 Feature")),
        _string(raw["feature_agreement_ref"], "D4 Feature agreement"),
        _string(raw["slice_agreement_ref"], "D4 Slice agreement"),
        _integer(raw["recorded_revision"], "D4 activation revision"),
    )


_PROVISIONAL_SLICE_ADOPTION_FIELDS: Final = frozenset((
    "id", "repository_id", "feature_id", "proposal_id", "proposal_revision_id",
    "proposal_revision", "proposal_digest", "source_schema", "source_revision",
    "source_state_sha256", "statement", "source", "source_reference",
    "feature_agreement_ref", "slice_agreement_ref", "change_id",
    "criteria_revision_id", "task_id", "slice_title", "initial_task", "checkpoint",
    "outcome", "example", "inclusions", "exclusions", "decisions", "criteria",
    "question_answers", "recorded_revision",
))


def _provisional_slice_adoption_to_json(adoption: ProvisionalSliceAdoption) -> JsonValue:
    return {
        "id": adoption.id,
        "repository_id": adoption.repository_id,
        "feature_id": str(adoption.feature_id),
        "proposal_id": adoption.proposal_id,
        "proposal_revision_id": adoption.proposal_revision_id,
        "proposal_revision": adoption.proposal_revision,
        "proposal_digest": adoption.proposal_digest,
        "source_schema": adoption.source_schema,
        "source_revision": adoption.source_revision,
        "source_state_sha256": adoption.source_state_sha256,
        "statement": adoption.statement,
        "source": adoption.source,
        "source_reference": adoption.source_reference,
        "feature_agreement_ref": adoption.feature_agreement_ref,
        "slice_agreement_ref": adoption.slice_agreement_ref,
        "change_id": str(adoption.change_id),
        "criteria_revision_id": adoption.criteria_revision_id,
        "task_id": adoption.task_id,
        "slice_title": adoption.slice_title,
        "initial_task": adoption.initial_task,
        "checkpoint": adoption.checkpoint,
        "outcome": adoption.outcome,
        "example": adoption.example,
        "inclusions": list(adoption.inclusions),
        "exclusions": list(adoption.exclusions),
        "decisions": list(adoption.decisions),
        "criteria": [
            {
                "id": item.id,
                "expected_result": item.expected_result,
                "expected_evidence": item.expected_evidence,
                **({"provenance": item.provenance} if item.provenance is not None else {}),
            }
            for item in adoption.criteria
        ],
        "question_answers": [list(item) for item in adoption.question_answers],
        "recorded_revision": adoption.recorded_revision,
    }


def _provisional_slice_adoption_from_json(value: JsonValue) -> ProvisionalSliceAdoption:
    raw = _object(value, _PROVISIONAL_SLICE_ADOPTION_FIELDS, "D5 provisional Slice adoption")
    criteria = tuple(
        SliceCriterion(
            _string(item["id"], "D5 accepted criterion id"),
            _string(item["expected_result"], "D5 accepted criterion result"),
            _string(item["expected_evidence"], "D5 accepted criterion evidence"),
            _string(item["provenance"], "D5 accepted criterion provenance")
            if "provenance" in item else None,
        )
        for entry in _array(raw["criteria"], "D5 accepted criteria")
        for item in (
            _object(
                entry,
                frozenset(("id", "expected_result", "expected_evidence", "provenance"))
                if isinstance(entry, dict) and "provenance" in entry
                else frozenset(("id", "expected_result", "expected_evidence")),
                "D5 accepted criterion",
            ),
        )
    )
    questions: list[tuple[str, str]] = []
    for entry in _array(raw["question_answers"], "D5 question answers"):
        pair = tuple(
            _string(answer, "D5 question answer")
            for answer in _array(entry, "D5 question-answer pair")
        )
        if len(pair) != 2:
            raise DecodeError("D5 question-answer pair must contain exactly two strings")
        questions.append((pair[0], pair[1]))
    return ProvisionalSliceAdoption(
        _string(raw["id"], "D5 adoption id"),
        _string(raw["repository_id"], "D5 adoption repository"),
        FeatureId(_string(raw["feature_id"], "D5 adoption Feature")),
        _string(raw["proposal_id"], "D5 adoption proposal"),
        _string(raw["proposal_revision_id"], "D5 adoption proposal revision id"),
        _integer(raw["proposal_revision"], "D5 adoption proposal revision"),
        _string(raw["proposal_digest"], "D5 adoption proposal digest"),
        _integer(raw["source_schema"], "D5 adoption source schema"),
        _integer(raw["source_revision"], "D5 adoption source revision"),
        _string(raw["source_state_sha256"], "D5 adoption source State digest"),
        _string(raw["statement"], "D5 adoption statement"),
        _string(raw["source"], "D5 adoption source"),
        _string(raw["source_reference"], "D5 adoption source reference"),
        _string(raw["feature_agreement_ref"], "D5 Feature agreement"),
        _string(raw["slice_agreement_ref"], "D5 Slice agreement"),
        ChangeId(_string(raw["change_id"], "D5 adoption Change")),
        _string(raw["criteria_revision_id"], "D5 criteria revision"),
        _string(raw["task_id"], "D5 adopted Task"),
        _string(raw["slice_title"], "D5 Slice title"),
        _string(raw["initial_task"], "D5 initial Task"),
        _string(raw["checkpoint"], "D5 checkpoint"),
        _string(raw["outcome"], "D5 outcome"),
        _string(raw["example"], "D5 example"),
        _strings(raw["inclusions"], "D5 inclusions"),
        _strings(raw["exclusions"], "D5 exclusions"),
        _strings(raw["decisions"], "D5 decisions"),
        criteria,
        tuple(questions),
        _integer(raw["recorded_revision"], "D5 adoption recorded revision"),
    )


_E1_UPGRADE_FIELDS: Final = frozenset((
    "source_revision", "converted_revision", "source_sha256", "statement",
    "source", "source_reference", "repository_id", "feature_agreement_ref", "slice_agreement_ref",
))


def _e1_upgrade_from_json(value: JsonValue) -> E1SchemaUpgradeReceipt | None:
    if value is None:
        return None
    raw = _object(value, _E1_UPGRADE_FIELDS, "E1 schema upgrade receipt")
    return E1SchemaUpgradeReceipt(
        _integer(raw["source_revision"], "E1 source revision"),
        _integer(raw["converted_revision"], "E1 converted revision"),
        _string(raw["source_sha256"], "E1 source SHA-256"),
        _string(raw["statement"], "E1 approval statement"),
        _string(raw["source"], "E1 approval source"),
        _string(raw["source_reference"], "E1 approval reference"),
        _string(raw["repository_id"], "E1 upgrade repository"),
        _string(raw["feature_agreement_ref"], "E1 upgrade Feature"),
        _string(raw["slice_agreement_ref"], "E1 upgrade Slice"),
    )


def _e1_upgrade_to_json(receipt: E1SchemaUpgradeReceipt | None) -> JsonValue:
    return None if receipt is None else {name: getattr(receipt, name) for name in _E1_UPGRADE_FIELDS}


def _autonomy_envelope(value: JsonValue) -> AutonomyEnvelope:
    raw = _object(value, frozenset(("goal", "allowed_paths", "operation_budget", "reversible_actions", "external_wait_allowed", "retry_budget", "irreversible_permissions", "stop_conditions")), "autonomy envelope")
    return AutonomyEnvelope(
        _string(raw["goal"], "autonomy goal"),
        tuple(RepositoryPath(item) for item in _strings(raw["allowed_paths"], "autonomy allowed paths")),
        _integer(raw["operation_budget"], "autonomy operation budget"),
        _strings(raw["reversible_actions"], "autonomy reversible actions"),
        _boolean(raw["external_wait_allowed"], "autonomy external wait allowed"),
        _integer(raw["retry_budget"], "autonomy retry budget"),
        _strings(raw["irreversible_permissions"], "autonomy irreversible permissions"),
        _strings(raw["stop_conditions"], "autonomy stop conditions"),
    )


def _autonomy_metrics(value: JsonValue) -> AutonomyMetrics:
    raw = _object(value, frozenset(("human_material_decisions", "operational_handoffs", "automatic_operations")), "autonomy metrics")
    return AutonomyMetrics(
        _integer(raw["human_material_decisions"], "human material decisions"),
        _integer(raw["operational_handoffs"], "operational handoffs"),
        _integer(raw["automatic_operations"], "automatic operations"),
    )


def _feature_to_json(feature: Feature) -> JsonValue:
    return {
        "id": feature.id,
        "title": feature.title,
        "living_spec_id": feature.living_spec_id,
        "side_of": feature.side_of,
    }


def _feature_from_json(value: JsonValue) -> Feature:
    raw = _object(
        value, frozenset(("id", "title", "living_spec_id", "side_of")), "feature"
    )
    living_spec = _optional_string(raw["living_spec_id"], "living_spec_id")
    side_of = _optional_string(raw["side_of"], "side_of")
    return Feature(
        FeatureId(_string(raw["id"], "feature id")),
        _string(raw["title"], "feature title"),
        None if living_spec is None else LivingSpecId(living_spec),
        None if side_of is None else FeatureId(side_of),
    )


def _change_to_json(change: Change) -> JsonValue:
    return {
        "schema": 3,
        "id": change.id,
        "feature_id": change.feature_id,
        "intent": change.intent,
        "classification": change.classification,
        "shared_intent_id": change.shared_intent_id,
        "assessment": _assessment_to_json(change.assessment),
        "lock_assessments": [_lock_assessment_to_json(item) for item in change.lock_assessments],
    }


def _convergence_assessment_to_json(assessment: ConvergenceAssessment | None) -> JsonValue:
    if assessment is None:
        return None
    return {
        "change_id": assessment.change_id,
        "product_truth_changed": assessment.product_truth_changed,
        "confidence": assessment.confidence,
        "evidence": [
            {
                "source": item.source,
                "reference": str(item.reference),
                "feature_id": item.feature_id,
                "living_spec_id": item.living_spec_id,
            }
            for item in assessment.evidence
        ],
        "rationale": assessment.rationale,
    }


def _convergence_assessment_from_json(value: JsonValue) -> ConvergenceAssessment | None:
    if value is None:
        return None
    raw = _object(
        value,
        frozenset(("change_id", "product_truth_changed", "confidence", "evidence", "rationale")),
        "convergence assessment",
    )
    try:
        confidence = AssessmentConfidence(_string(raw["confidence"], "convergence assessment confidence"))
    except ValueError as error:
        raise DecodeError("invalid convergence assessment confidence") from error
    evidence = tuple(
        AssessmentEvidence(
            _string(item["source"], "convergence assessment evidence source"),
            RepositoryPath(_string(item["reference"], "convergence assessment evidence reference")),
            None if item["feature_id"] is None else FeatureId(_string(item["feature_id"], "convergence assessment evidence feature")),
            None if item["living_spec_id"] is None else LivingSpecId(_string(item["living_spec_id"], "convergence assessment evidence Living Spec")),
        )
        for value_item in _array(raw["evidence"], "convergence assessment evidence")
        for item in [_object(value_item, frozenset(("source", "reference", "feature_id", "living_spec_id")), "convergence assessment evidence")]
    )
    return ConvergenceAssessment(
        ChangeId(_string(raw["change_id"], "convergence assessment change")),
        _boolean(raw["product_truth_changed"], "convergence assessment product truth flag"),
        confidence,
        evidence,
        _string(raw["rationale"], "convergence assessment rationale"),
    )


def _lock_assessment_to_json(assessment: LockAssessment) -> JsonValue:
    return {
        "lock_id": assessment.lock_id,
        "lock_revision": assessment.lock_revision,
        "affected_aspects": list(assessment.affected_aspects),
        "protected_touches": list(assessment.protected_touches),
        "preserving_path": assessment.preserving_path,
        "conflict": assessment.conflict,
        "evasion": assessment.evasion,
        "confidence": assessment.confidence,
        "evidence": list(assessment.evidence),
        "rationale": assessment.rationale,
    }


def _lock_assessment_from_json(value: JsonValue) -> LockAssessment:
    raw = _object(
        value,
        frozenset(("lock_id", "lock_revision", "affected_aspects", "protected_touches", "preserving_path", "conflict", "evasion", "confidence", "evidence", "rationale")),
        "lock assessment",
    )
    try:
        confidence = AssessmentConfidence(_string(raw["confidence"], "lock assessment confidence"))
    except ValueError as error:
        raise DecodeError("invalid lock assessment confidence") from error
    return LockAssessment(
        LockId(_string(raw["lock_id"], "lock assessment lock")),
        _integer(raw["lock_revision"], "lock assessment revision"),
        _strings(raw["affected_aspects"], "lock assessment affected aspects"),
        _strings(raw["protected_touches"], "lock assessment protected touches"),
        _string(raw["preserving_path"], "lock assessment preserving path"),
        _boolean(raw["conflict"], "lock assessment conflict"),
        _boolean(raw["evasion"], "lock assessment evasion"),
        confidence,
        _strings(raw["evidence"], "lock assessment evidence"),
        _string(raw["rationale"], "lock assessment rationale"),
    )


def _assessment_to_json(assessment: IntentAssessment | None) -> JsonValue:
    if assessment is None:
        return None
    return {
        "operation": assessment.operation,
        "classification": assessment.classification,
        "confidence": assessment.confidence,
        "capability_state": assessment.capability_state,
        "selected_capability": assessment.selected_capability,
        "candidate_capabilities": list(assessment.candidate_capabilities),
        "selected_feature_id": assessment.selected_feature_id,
        "evidence": [
            {
                "source": item.source,
                "reference": str(item.reference),
                "feature_id": item.feature_id,
                "living_spec_id": item.living_spec_id,
            }
            for item in assessment.evidence
        ],
        "material_ambiguities": [
            {"decision": item.decision, "options": list(item.options)}
            if isinstance(item, MaterialDecision) else item
            for item in assessment.material_ambiguities
        ],
        "recommended_depth": {
            "review": assessment.recommended_depth.review,
            "verification": assessment.recommended_depth.verification,
            "convergence": assessment.recommended_depth.convergence,
        },
        "rationale": assessment.rationale,
    }


def _assessment_from_json(value: JsonValue) -> IntentAssessment | None:
    if value is None:
        return None
    raw = _object(
        value,
        frozenset((
            "operation", "classification", "confidence", "capability_state",
            "selected_capability", "candidate_capabilities", "selected_feature_id",
            "evidence", "material_ambiguities", "recommended_depth", "rationale",
        )),
        "intent assessment",
    )
    depth = _object(
        raw["recommended_depth"],
        frozenset(("review", "verification", "convergence")),
        "assessment depth",
    )
    evidence = tuple(
        AssessmentEvidence(
            _string(item["source"], "assessment evidence source"),
            RepositoryPath(_string(item["reference"], "assessment evidence reference")),
            None if item["feature_id"] is None else FeatureId(_string(item["feature_id"], "assessment evidence feature")),
            None if item["living_spec_id"] is None else LivingSpecId(_string(item["living_spec_id"], "assessment evidence Living Spec")),
        )
        for value_item in _array(raw["evidence"], "assessment evidence")
        for item in [_object(value_item, frozenset(("source", "reference", "feature_id", "living_spec_id")), "assessment evidence")]
    )
    try:
        operation = AssessmentOperation(_string(raw["operation"], "assessment operation"))
        classification = Classification(_string(raw["classification"], "assessment classification"))
        confidence = AssessmentConfidence(_string(raw["confidence"], "assessment confidence"))
        capability_state = CapabilityState(_string(raw["capability_state"], "assessment capability state"))
        recommended_depth = AssessmentDepth(
            AssessmentDepthValue(_string(depth["review"], "assessment review depth")),
            AssessmentDepthValue(_string(depth["verification"], "assessment verification depth")),
            AssessmentDepthValue(_string(depth["convergence"], "assessment convergence depth")),
        )
    except ValueError as error:
        raise DecodeError("invalid intent assessment enum") from error
    selected_feature = _optional_string(raw["selected_feature_id"], "assessment selected feature")
    return IntentAssessment(
        operation,
        classification,
        confidence,
        capability_state,
        _optional_string(raw["selected_capability"], "assessment selected capability"),
        _strings(raw["candidate_capabilities"], "assessment candidates"),
        None if selected_feature is None else FeatureId(selected_feature),
        evidence,
        tuple(
            MaterialDecision(_string(item["decision"], "material decision"), _strings(item["options"], "material options"))
            if isinstance(item, dict) else _string(item, "legacy ambiguity")
            for value_item in _array(raw["material_ambiguities"], "assessment ambiguities")
            for item in [(_object(value_item, frozenset(("decision", "options")), "material decision") if isinstance(value_item, dict) else value_item)]
        ),
        recommended_depth,
        _string(raw["rationale"], "assessment rationale"),
    )


def _change_from_json(value: JsonValue) -> Change:
    if not isinstance(value, dict):
        raise DecodeError("change fields are not exact")
    fields = frozenset(("schema", "id", "feature_id", "intent", "classification", "shared_intent_id"))
    extended = fields | frozenset(("assessment",))
    locked = extended | frozenset(("lock_assessments",))
    raw = _object(value, locked if "lock_assessments" in value else extended if "assessment" in value else fields, "change")
    schema = _integer(raw["schema"], "change schema")
    if (
        schema not in (1, 2, 3)
        or (schema == 1 and "assessment" in raw)
        or (schema == 2 and "assessment" not in raw)
        or (schema == 3 and "lock_assessments" not in raw)
    ):
        raise DecodeError("unknown change schema")
    classification_text = _optional_string(raw["classification"], "classification")
    try:
        classification = (
            None if classification_text is None else Classification(classification_text)
        )
    except ValueError as error:
        raise DecodeError("invalid change classification") from error
    shared = _optional_string(raw["shared_intent_id"], "change shared intent")
    return Change(
        ChangeId(_string(raw["id"], "change id")),
        FeatureId(_string(raw["feature_id"], "change feature_id")),
        _string(raw["intent"], "change intent"),
        classification,
        None if shared is None else SharedIntentId(shared),
        None if schema == 1 else _assessment_from_json(raw["assessment"]),
        () if schema < 3 else tuple(_lock_assessment_from_json(item) for item in _array(raw["lock_assessments"], "lock assessments")),
    )


def _decision_to_json(decision: Decision) -> JsonValue:
    return {
        "schema": 1,
        "id": decision.id,
        "feature_id": decision.feature_id,
        "kind": decision.kind,
        "statement": decision.statement,
        "change_id": decision.change_id,
        "supersedes": decision.supersedes,
    }


def _decision_from_json(value: JsonValue) -> Decision:
    raw = _object(
        value,
        frozenset(
            ("schema", "id", "feature_id", "kind", "statement", "change_id", "supersedes")
        ),
        "decision",
    )
    if _integer(raw["schema"], "decision schema") != 1:
        raise DecodeError("unknown decision schema")
    change_id = _optional_string(raw["change_id"], "decision change_id")
    supersedes = _optional_string(raw["supersedes"], "decision supersedes")
    return Decision(
        DecisionId(_string(raw["id"], "decision id")),
        FeatureId(_string(raw["feature_id"], "decision feature_id")),
        _string(raw["kind"], "decision kind"),
        _string(raw["statement"], "decision statement"),
        None if change_id is None else ChangeId(change_id),
        None if supersedes is None else DecisionId(supersedes),
    )


def _observation_to_json(observation: Observation) -> JsonValue:
    return {
        "schema": 1,
        "id": observation.id,
        "change_id": observation.change_id,
        "kind": observation.kind,
        "statement": observation.statement,
        "accepted_into_truth": observation.accepted_into_truth,
        "outcome": observation.outcome,
    }


def _observation_from_json(value: JsonValue) -> Observation:
    raw = _object(
        value,
        frozenset(
            (
                "schema",
                "id",
                "change_id",
                "kind",
                "statement",
                "accepted_into_truth",
                "outcome",
            )
        ),
        "observation",
    )
    if _integer(raw["schema"], "observation schema") != 1:
        raise DecodeError("unknown observation schema")
    outcome_text = _optional_string(raw["outcome"], "observation outcome")
    try:
        outcome = None if outcome_text is None else ExperimentOutcome(outcome_text)
    except ValueError as error:
        raise DecodeError("invalid experiment outcome") from error
    return Observation(
        ObservationId(_string(raw["id"], "observation id")),
        ChangeId(_string(raw["change_id"], "observation change_id")),
        _string(raw["kind"], "observation kind"),
        _string(raw["statement"], "observation statement"),
        _boolean(raw["accepted_into_truth"], "accepted_into_truth"),
        outcome,
    )


def _drift_to_json(drift: Drift) -> JsonValue:
    return {
        "schema": 2,
        "id": drift.id,
        "expected_publication": drift.expected_publication,
        "observed": drift.observed,
        "change_id": drift.change_id,
        "unresolved": drift.unresolved,
    }


def _drift_from_json(value: JsonValue) -> Drift:
    raw = _object(
        value,
        frozenset(("schema", "id", "expected_publication", "observed", "change_id", "unresolved")),
        "drift",
    )
    if _integer(raw["schema"], "drift schema") != 2:
        raise DecodeError("unknown drift schema")
    return Drift(
        ObservationId(_string(raw["id"], "drift id")),
        LivingSpecId(_string(raw["expected_publication"], "expected publication")),
        _string(raw["observed"], "drift observation"),
        ChangeId(_string(raw["change_id"], "drift change_id")),
        _boolean(raw["unresolved"], "drift unresolved"),
    )


def _lock_to_json(lock: SemanticLock) -> JsonValue:
    return {"id": lock.id, "feature_id": lock.feature_id, "current_revision": lock.current_revision, "active": lock.active}


def _lock_from_json(value: JsonValue) -> SemanticLock:
    raw = _object(value, frozenset(("id", "feature_id", "current_revision", "active")), "semantic lock")
    return SemanticLock(
        LockId(_string(raw["id"], "semantic lock id")),
        FeatureId(_string(raw["feature_id"], "semantic lock feature")),
        _integer(raw["current_revision"], "semantic lock revision"),
        _boolean(raw["active"], "semantic lock active"),
    )


def _lock_revision_to_json(revision: SemanticLockRevision) -> JsonValue:
    return {
        "lock_id": revision.lock_id,
        "revision": revision.revision,
        "baseline": _publication_to_json(revision.baseline),
        "protected_aspects": list(revision.protected_aspects),
        "rationale": revision.rationale,
        "decision_id": revision.decision_id,
    }


def _lock_revision_from_json(value: JsonValue) -> SemanticLockRevision:
    raw = _object(value, frozenset(("lock_id", "revision", "baseline", "protected_aspects", "rationale", "decision_id")), "semantic lock revision")
    baseline = _publication_from_json(raw["baseline"])
    if baseline is None:
        raise DecodeError("semantic lock baseline cannot be null")
    return SemanticLockRevision(
        LockId(_string(raw["lock_id"], "semantic lock revision lock")),
        _integer(raw["revision"], "semantic lock revision number"),
        baseline,
        _strings(raw["protected_aspects"], "semantic lock protected aspects"),
        _string(raw["rationale"], "semantic lock rationale"),
        None if raw["decision_id"] is None else DecisionId(_string(raw["decision_id"], "semantic lock revision decision")),
    )


def _lock_release_to_json(release: LockRelease) -> JsonValue:
    return {"lock_id": release.lock_id, "revision": release.revision, "decision_id": release.decision_id}


def _lock_release_from_json(value: JsonValue) -> LockRelease:
    raw = _object(value, frozenset(("lock_id", "revision", "decision_id")), "lock release")
    revision = _integer(raw["revision"], "lock release revision")
    if revision < 1:
        raise DecodeError("lock release revision must be a positive integer")
    return LockRelease(
        LockId(_string(raw["lock_id"], "lock release lock")),
        revision,
        DecisionId(_string(raw["decision_id"], "lock release decision")),
    )


def _lock_authorization_to_json(authorization: LockAuthorization) -> JsonValue:
    return {
        "lock_id": authorization.lock_id,
        "lock_revision": authorization.lock_revision,
        "feature_id": authorization.feature_id,
        "change_id": authorization.change_id,
        "decision_id": authorization.decision_id,
        "protected_aspects": list(authorization.protected_aspects),
        "assessment_revision": authorization.assessment_revision,
    }


def _lock_authorization_from_json(value: JsonValue) -> LockAuthorization:
    raw = _object(value, frozenset(("lock_id", "lock_revision", "feature_id", "change_id", "decision_id", "protected_aspects", "assessment_revision")), "lock authorization")
    return LockAuthorization(
        LockId(_string(raw["lock_id"], "lock authorization lock")),
        _integer(raw["lock_revision"], "lock authorization revision"),
        FeatureId(_string(raw["feature_id"], "lock authorization feature")),
        ChangeId(_string(raw["change_id"], "lock authorization change")),
        DecisionId(_string(raw["decision_id"], "lock authorization decision")),
        _strings(raw["protected_aspects"], "lock authorization aspects"),
        _integer(raw["assessment_revision"], "lock authorization assessment revision"),
    )


def _autonomy_envelope_to_json(envelope: AutonomyEnvelope) -> JsonValue:
    return {
        "goal": envelope.goal,
        "allowed_paths": [str(path) for path in envelope.allowed_paths],
        "operation_budget": envelope.operation_budget,
        "reversible_actions": list(envelope.reversible_actions),
        "external_wait_allowed": envelope.external_wait_allowed,
        "retry_budget": envelope.retry_budget,
        "irreversible_permissions": list(envelope.irreversible_permissions),
        "stop_conditions": list(envelope.stop_conditions),
    }


def _irreversible_authorization_to_json(
    authorization: IrreversibleAuthorization,
) -> JsonValue:
    return {
        "id": authorization.id,
        "operation_kind": authorization.operation_kind.value,
        "feature_id": authorization.feature_id,
        "change_id": authorization.change_id,
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
        "rewrite_plan_digest": authorization.rewrite_plan_digest,
        "purpose": authorization.purpose,
        "force_with_lease": authorization.force_with_lease,
        "one_shot": authorization.one_shot,
        "authorized_revision": authorization.authorized_revision,
        "status": authorization.status.value,
        "validated_revision": authorization.validated_revision,
        "consumed_revision": authorization.consumed_revision,
        "resulting_remote_sha": authorization.resulting_remote_sha,
        "invalidated_revision": authorization.invalidated_revision,
        "invalidation_reason": authorization.invalidation_reason,
    }


def _irreversible_authorization_from_json(value: JsonValue) -> IrreversibleAuthorization:
    raw = _object(
        value,
        frozenset((
            "id", "operation_kind", "feature_id", "change_id", "target_ref",
            "expected_remote_sha", "rewrite_policy", "target_identity_name",
            "target_identity_email", "expected_result_sha", "commit_rewrites",
            "rewrite_plan_digest", "purpose", "force_with_lease", "one_shot",
            "authorized_revision", "status", "validated_revision", "consumed_revision",
            "resulting_remote_sha", "invalidated_revision", "invalidation_reason",
        )),
        "irreversible authorization",
    )
    try:
        operation_kind = IrreversibleOperationKind(
            _string(raw["operation_kind"], "irreversible operation kind")
        )
        status = IrreversibleAuthorizationStatus(
            _string(raw["status"], "irreversible authorization status")
        )
        rewrite_policy = GitHistoryRewritePolicy(
            _string(raw["rewrite_policy"], "irreversible authorization rewrite policy")
        )
    except ValueError as error:
        raise DecodeError("invalid irreversible authorization enum") from error
    return IrreversibleAuthorization(
        IrreversibleAuthorizationId(_string(raw["id"], "irreversible authorization id")),
        operation_kind,
        FeatureId(_string(raw["feature_id"], "irreversible authorization feature")),
        ChangeId(_string(raw["change_id"], "irreversible authorization change")),
        _string(raw["target_ref"], "irreversible authorization target ref"),
        _string(raw["expected_remote_sha"], "irreversible authorization expected SHA"),
        rewrite_policy,
        _string(raw["target_identity_name"], "irreversible authorization identity name"),
        _string(raw["target_identity_email"], "irreversible authorization identity email"),
        _string(raw["expected_result_sha"], "irreversible authorization result SHA"),
        tuple(
            GitCommitRewrite(
                _string(item["old_sha"], "commit rewrite old SHA"),
                _string(item["new_sha"], "commit rewrite new SHA"),
                _boolean(item["identity_normalized"], "commit rewrite identity normalized"),
            )
            for value in _array(raw["commit_rewrites"], "irreversible commit rewrites")
            for item in [_object(value, frozenset(("old_sha", "new_sha", "identity_normalized")), "commit rewrite")]
        ),
        _string(raw["rewrite_plan_digest"], "irreversible authorization plan digest"),
        _string(raw["purpose"], "irreversible authorization purpose"),
        _boolean(raw["force_with_lease"], "irreversible authorization force-with-lease"),
        _boolean(raw["one_shot"], "irreversible authorization one-shot"),
        _integer(raw["authorized_revision"], "irreversible authorization revision"),
        status,
        _optional_integer(raw["validated_revision"], "irreversible authorization validated revision"),
        _optional_integer(raw["consumed_revision"], "irreversible authorization consumed revision"),
        _optional_string(raw["resulting_remote_sha"], "irreversible authorization result SHA"),
        _optional_integer(raw["invalidated_revision"], "irreversible authorization invalidated revision"),
        _optional_string(raw["invalidation_reason"], "irreversible authorization invalidation reason"),
    )


def _change_lock_assessments_to_json(record: ChangeLockAssessments) -> JsonValue:
    return {
        "change_id": record.change_id,
        "assessments": [_lock_assessment_to_json(item) for item in record.assessments],
        "history": [[_lock_assessment_to_json(item) for item in assessment_set] for assessment_set in record.history],
    }


def _change_lock_assessments_from_json(value: JsonValue) -> ChangeLockAssessments:
    raw = _object(value, frozenset(("change_id", "assessments", "history")), "change lock assessments")
    return ChangeLockAssessments(
        ChangeId(_string(raw["change_id"], "change lock assessment change")),
        tuple(_lock_assessment_from_json(item) for item in _array(raw["assessments"], "change lock assessments")),
        tuple(tuple(_lock_assessment_from_json(item) for item in _array(assessment_set, "historical change lock assessments")) for assessment_set in _array(raw["history"], "change lock assessment history")),
    )


class _Identified(Protocol):
    @property
    def id(self) -> str: ...


Record = TypeVar("Record", bound=_Identified)


def _resolved_root(root: Path) -> Path:
    try:
        resolved = root.expanduser().resolve()
    except OSError as error:
        raise RecoveryError("repository root cannot be resolved") from error
    if not resolved.is_dir():
        raise RecoveryError("repository root must be an accessible directory")
    return resolved


def _artifact_path(root: Path, relative: str) -> Path:
    path = root / ".evospec" / RepositoryPath(relative).value
    try:
        resolved = path.resolve()
    except OSError as error:
        raise RecoveryError("artifact path cannot be resolved") from error
    if not resolved.is_relative_to(root):
        raise RecoveryError("artifact path escapes the repository root")
    return path


def _read_json(path: Path) -> JsonValue:
    return decode_json(path.read_text(encoding="utf-8"))


def _references_from_json(
    value: JsonValue, label: str
) -> tuple[tuple[str, RepositoryPath], ...]:
    references: list[tuple[str, RepositoryPath]] = []
    for value_item in _array(value, label):
        item = _object(value_item, frozenset(("id", "path")), label)
        references.append(
            (
                _string(item["id"], f"{label} id"),
                RepositoryPath(_string(item["path"], f"{label} path")),
            )
        )
    return tuple(references)


def _load_references(
    root: Path,
    value: JsonValue,
    label: str,
    directory: str,
    decoder: Callable[[JsonValue], Record],
) -> tuple[Record, ...]:
    loaded: list[Record] = []
    for identity, path in _references_from_json(value, label):
        expected = RepositoryPath(f"{directory}/{identity}.json")
        if path != expected:
            raise DecodeError(f"{label} path does not match identity")
        item = decoder(_read_json(_artifact_path(root, path.value)))
        if item.id != identity:
            raise DecodeError(f"{label} record identity mismatch")
        loaded.append(item)
    return tuple(loaded)


def _load_provisional_slice_revisions(
    root: Path, value: JsonValue, *, schema: int = PROVISIONAL_SLICE_SCHEMA,
) -> tuple[ProvisionalSliceRevision, ...]:
    references = _references_from_json(value, "provisional_slice_refs")
    if schema >= D4_PROVISIONAL_SLICE_SCHEMA and len(references) > 32 * 32:
        raise DecodeError("D4 provisional Slice project revision limit is exceeded")
    identities = tuple(identity for identity, _ in references)
    if identities != tuple(sorted(identities)) or len(identities) != len(set(identities)):
        raise DecodeError("provisional Slice references must be unique and sorted")
    revisions: list[ProvisionalSliceRevision] = []
    for identity, path in references:
        expected = RepositoryPath(f"records/provisional-slices/{identity}.json")
        if path != expected:
            raise DecodeError("provisional Slice record path does not match revision identity")
        revision = _provisional_slice_from_json(
            _read_json(_artifact_path(root, path.value)), schema=schema,
        )
        if revision.revision_id != identity:
            raise DecodeError("provisional Slice record identity mismatch")
        revisions.append(revision)
    return tuple(revisions)


def _load_provisional_slice_heads(
    value: JsonValue, *, schema: int = PROVISIONAL_SLICE_SCHEMA,
) -> tuple[ProvisionalSliceHead, ...]:
    entries = _array(value, "provisional Slice heads")
    if schema >= D4_PROVISIONAL_SLICE_SCHEMA and len(entries) > 32:
        raise DecodeError("D4 provisional Slice project head limit is exceeded")
    heads = tuple(
        _provisional_slice_head_from_json(entry)
        for entry in entries
    )
    proposals = tuple(item.proposal_id for item in heads)
    if proposals != tuple(sorted(proposals)) or len(proposals) != len(set(proposals)):
        raise DecodeError("provisional Slice heads must be unique and sorted")
    return heads


def _load_provisional_slice_adoptions(
    root: Path, value: JsonValue,
) -> tuple[ProvisionalSliceAdoption, ...]:
    references = _references_from_json(value, "provisional_slice_adoption_refs")
    if len(references) != 1:
        raise DecodeError("schema 24 requires exactly one D5 adoption mapping")
    identities = tuple(identity for identity, _ in references)
    if identities != tuple(sorted(identities)) or len(identities) != len(set(identities)):
        raise DecodeError("D5 adoption references must be unique and sorted")
    loaded: list[ProvisionalSliceAdoption] = []
    for identity, path in references:
        expected = RepositoryPath(f"records/provisional-slice-adoptions/{identity}.json")
        if path != expected:
            raise DecodeError("D5 adoption record path does not match its identity")
        adoption = _provisional_slice_adoption_from_json(
            _read_json(_artifact_path(root, path.value)),
        )
        if adoption.id != identity:
            raise DecodeError("D5 adoption record identity mismatch")
        loaded.append(adoption)
    return tuple(loaded)


def _d4_only_state_fields(candidate: State, current: State, *fields: str) -> bool:
    return replace(
        candidate,
        revision=current.revision,
        **{field: getattr(current, field) for field in fields},
    ) == current


def _d4_has_reported_task(context: WorkingContext, task_id: str | None = None) -> bool:
    return (
        context.task_progress is not None
        and context.task_progress.report_kind == "REPORTED_UNVERIFIED"
        and (task_id is None or context.task_progress.task_id == task_id)
    )


def _validate_d4_lifecycle_commit(
    root: Path, current: State, candidate: State, operation: str,
) -> None:
    """Admit only the canonical D4 review, verify, converge and archive commits."""
    if operation not in _D4_LIFECYCLE_OPERATIONS:
        raise AtomicFailure("D4 lifecycle operation is unsupported")
    if candidate.revision != current.revision + 1:
        raise AtomicFailure("D4 lifecycle operation must publish exactly one revision")
    active = current.active_context
    updated = candidate.active_context
    if active is None:
        raise AtomicFailure("D4 lifecycle operation requires active work")

    if operation == "lifecycle":
        transitions = {
            (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT): (
                LifecycleState.IN_PROGRESS, Phase.REVIEW,
            ),
            (LifecycleState.IN_PROGRESS, Phase.REVIEW): (
                LifecycleState.VERIFYING, Phase.VERIFY,
            ),
            (LifecycleState.VERIFYING, Phase.VERIFY): (
                LifecycleState.VERIFYING, Phase.CONVERGE,
            ),
        }
        target = transitions.get((active.status, active.phase))
        if (
            target is None
            or not _d4_has_reported_task(active)
            or not _d4_only_state_fields(candidate, current, "active_context")
            or updated is None
        ):
            raise AtomicFailure("D4 lifecycle phase advance is not authorized")
        status, phase = target
        if active.phase is Phase.REVIEW and not has_acceptable_review(active.review):
            raise AtomicFailure("D4 verification phase requires acceptable review")
        if active.phase is Phase.VERIFY and (
            not has_acceptable_review(active.review)
            or not active.verification.sufficient
            or not can_converge(active.review, active.verification)
        ):
            raise AtomicFailure("D4 convergence phase requires sufficient assurance")
        if active.convergence_complete:
            raise AtomicFailure("D4 lifecycle phase advance is not authorized")
        if updated != replace(active, status=status, phase=phase, paused_pair=None):
            raise AtomicFailure("D4 lifecycle phase advance changed unrelated context")
    elif operation == "review":
        if (
            (active.status, active.phase) != (LifecycleState.IN_PROGRESS, Phase.REVIEW)
            or not _d4_has_reported_task(active)
            or not _d4_only_state_fields(candidate, current, "active_context")
            or updated is None
            or updated.review is None
            or updated != replace(
                active,
                review=updated.review,
                checkpoints=updated.checkpoints,
                review_evidence=updated.review_evidence,
                review_findings=updated.review_findings,
            )
        ):
            raise AtomicFailure("D4 review record changed unauthorized state")
    elif operation == "verification":
        if (
            (active.status, active.phase) != (LifecycleState.VERIFYING, Phase.VERIFY)
            or not _d4_has_reported_task(active)
            or not has_acceptable_review(active.review)
            or not _d4_only_state_fields(candidate, current, "active_context")
            or updated is None
            or updated != replace(active, verification=updated.verification)
        ):
            raise AtomicFailure("D4 verification record changed unauthorized state")
    elif operation == "convergence":
        if (
            (active.status, active.phase) != (LifecycleState.VERIFYING, Phase.CONVERGE)
            or not _d4_has_reported_task(active)
            or not has_acceptable_review(active.review)
            or not active.verification.sufficient
            or not can_converge(active.review, active.verification)
            or active.convergence_complete
            or not _d4_only_state_fields(candidate, current, "active_context")
            or updated != replace(active, convergence_complete=True)
        ):
            raise AtomicFailure("D4 convergence confirmation changed unauthorized state")
    else:
        if (
            (active.status, active.phase) != (LifecycleState.VERIFYING, Phase.CONVERGE)
            or not _d4_has_reported_task(active)
            or not has_acceptable_review(active.review)
            or not active.verification.sufficient
            or not can_converge(active.review, active.verification)
            or not active.convergence_complete
            or active.change_id is None
            or active.publication is not None
            or candidate.active_context is not None
            or candidate.active_work_id is not None
            or not _d4_only_state_fields(
                candidate, current, "active_context", "active_work_id",
                "terminal_archives", "work_items",
            )
            or len(candidate.terminal_archives) != 1
            or candidate.terminal_archives[:len(current.terminal_archives)]
            != current.terminal_archives
            or len(candidate.work_items) != 1
            or candidate.work_items[:len(current.work_items)] != current.work_items
        ):
            raise AtomicFailure("D4 accepted terminal archive changed unauthorized state")
        archive = candidate.terminal_archives[0]
        assessment = archive.convergence_assessment
        work_item = candidate.work_items[0]
        if (
            archive.context != replace(
                active, status=LifecycleState.ACCEPTED, phase=Phase.DONE,
            )
            or archive.parent_feature_id is not None
            or archive.legacy_unassessed_acceptance
            or type(assessment) is not ConvergenceAssessment
            or assessment.change_id != active.change_id
            or assessment.product_truth_changed is not False
            or assessment.product_truth_content is not None
            or work_item.id != WorkId(f"work-{active.change_id}")
            or work_item.operational_state is not OperationalState.DONE
            or work_item.context is not None
            or work_item.preserved_primary_context is not None
            or work_item.deferred_side_request is not None
            or work_item.pending_intake is not None
            or work_item.reason is not None
            or work_item.terminal_change_id != active.change_id
        ):
            raise AtomicFailure("D4 accepted terminal archive is inconsistent")
    try:
        validate_state(candidate)
        _validate_provisional_slice_authority(
            root, candidate, schema=D4_PROVISIONAL_SLICE_SCHEMA,
        )
    except (StateValidationError, DecodeError, DomainError) as error:
        raise AtomicFailure("D4 lifecycle candidate is invalid") from error


def _validate_d5_lifecycle_commit(
    root: Path, current: State, candidate: State, operation: str,
) -> None:
    """Admit only ordinary Task progress, assurance and closure for adopted D5."""
    if operation not in _D5_LIFECYCLE_OPERATIONS:
        raise AtomicFailure("D5 lifecycle operation is unsupported")
    if candidate.revision != current.revision + 1 or len(current.provisional_slice_adoptions) != 1:
        raise AtomicFailure("D5 lifecycle operation must advance one adopted Task revision")
    adoption = current.provisional_slice_adoptions[0]
    active = current.active_context
    updated = candidate.active_context
    if active is None:
        raise AtomicFailure("D5 lifecycle operation requires active adopted Task work")

    if operation == "lifecycle":
        transitions = {
            (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT): (
                LifecycleState.IN_PROGRESS, Phase.REVIEW,
            ),
            (LifecycleState.IN_PROGRESS, Phase.REVIEW): (
                LifecycleState.VERIFYING, Phase.VERIFY,
            ),
            (LifecycleState.VERIFYING, Phase.VERIFY): (
                LifecycleState.VERIFYING, Phase.CONVERGE,
            ),
        }
        target = transitions.get((active.status, active.phase))
        if (
            target is None
            or not _d4_has_reported_task(active, adoption.task_id)
            or not _d4_only_state_fields(candidate, current, "active_context")
            or updated is None
        ):
            raise AtomicFailure("D5 lifecycle phase advance is not authorized")
        status, phase = target
        if active.phase is Phase.REVIEW and not has_acceptable_review(active.review):
            raise AtomicFailure("D5 verification phase requires acceptable review")
        if active.phase is Phase.VERIFY and (
            not has_acceptable_review(active.review)
            or not active.verification.sufficient
            or not can_converge(active.review, active.verification)
        ):
            raise AtomicFailure("D5 convergence phase requires sufficient assurance")
        if active.convergence_complete or updated != replace(
            active, status=status, phase=phase, paused_pair=None,
        ):
            raise AtomicFailure("D5 lifecycle phase advance changed unauthorized context")
    elif operation == "review":
        if (
            (active.status, active.phase) != (LifecycleState.IN_PROGRESS, Phase.REVIEW)
            or not _d4_has_reported_task(active, adoption.task_id)
            or not _d4_only_state_fields(candidate, current, "active_context")
            or updated is None
            or updated.review is None
            or updated != replace(
                active,
                review=updated.review,
                checkpoints=updated.checkpoints,
                review_evidence=updated.review_evidence,
                review_findings=updated.review_findings,
            )
        ):
            raise AtomicFailure("D5 review record changed unauthorized state")
    elif operation == "verification":
        if (
            (active.status, active.phase) != (LifecycleState.VERIFYING, Phase.VERIFY)
            or not _d4_has_reported_task(active, adoption.task_id)
            or not has_acceptable_review(active.review)
            or not _d4_only_state_fields(candidate, current, "active_context")
            or updated is None
            or updated != replace(active, verification=updated.verification)
        ):
            raise AtomicFailure("D5 verification record changed unauthorized state")
    elif operation == "convergence":
        if (
            (active.status, active.phase) != (LifecycleState.VERIFYING, Phase.CONVERGE)
            or not _d4_has_reported_task(active, adoption.task_id)
            or not has_acceptable_review(active.review)
            or not active.verification.sufficient
            or not can_converge(active.review, active.verification)
            or active.convergence_complete
            or not _d4_only_state_fields(candidate, current, "active_context")
            or updated != replace(active, convergence_complete=True)
        ):
            raise AtomicFailure("D5 convergence confirmation changed unauthorized state")
    else:
        if (
            (active.status, active.phase) != (LifecycleState.VERIFYING, Phase.CONVERGE)
            or not _d4_has_reported_task(active, adoption.task_id)
            or not has_acceptable_review(active.review)
            or not active.verification.sufficient
            or not can_converge(active.review, active.verification)
            or not active.convergence_complete
            or active.change_id != adoption.change_id
            or active.publication is not None
            or candidate.active_context is not None
            or candidate.active_work_id is not None
            or not _d4_only_state_fields(
                candidate, current, "active_context", "active_work_id",
                "terminal_archives", "work_items",
            )
            or len(candidate.terminal_archives) != len(current.terminal_archives) + 1
            or candidate.terminal_archives[:len(current.terminal_archives)]
            != current.terminal_archives
            or len(candidate.work_items) != len(current.work_items) + 1
            or candidate.work_items[:len(current.work_items)] != current.work_items
        ):
            raise AtomicFailure("D5 accepted terminal archive changed unauthorized state")
        archive = candidate.terminal_archives[-1]
        assessment = archive.convergence_assessment
        work_item = candidate.work_items[-1]
        if (
            archive.context != replace(
                active, status=LifecycleState.ACCEPTED, phase=Phase.DONE,
            )
            or archive.parent_feature_id is not None
            or archive.legacy_unassessed_acceptance
            or type(assessment) is not ConvergenceAssessment
            or assessment.change_id != adoption.change_id
            or assessment.product_truth_changed is not False
            or assessment.product_truth_content is not None
            or work_item.id != WorkId(f"work-{adoption.change_id}")
            or work_item.operational_state is not OperationalState.DONE
            or work_item.context is not None
            or work_item.preserved_primary_context is not None
            or work_item.deferred_side_request is not None
            or work_item.pending_intake is not None
            or work_item.reason is not None
            or work_item.terminal_change_id != adoption.change_id
        ):
            raise AtomicFailure("D5 accepted terminal archive is inconsistent")
    try:
        validate_state(candidate)
        _validate_provisional_slice_authority(
            root, candidate, schema=D5_PROVISIONAL_SLICE_SCHEMA,
        )
    except (StateValidationError, DecodeError, DomainError) as error:
        raise AtomicFailure("D5 lifecycle candidate is invalid") from error


def _validate_d4_lifecycle_recovery(state: State, activation: ProvisionalSliceActivation) -> None:
    """Restrict schema-23 recovery to the initial lane or its one accepted close."""
    if (
        state.preserved_primary_context is not None
        or state.completed_side_contexts
        or state.publications
        or state.publication_history
        or state.decisions
        or state.observations
        or state.drifts
        or state.locks
        or state.lock_revisions
        or state.lock_releases
        or state.lock_authorizations
        or state.change_lock_assessments
        or state.work_relations
        or state.parked_work is not None
        or state.preserved_primary_work_id is not None
        or state.pending_intake is not None
        or state.deferred_side_request is not None
        or state.shared_intents
        or state.deferred_linked_intents
        or state.pending_capability_selection is not None
        or state.capability_selection_history
        or state.pending_discovery_recovery is not None
        or state.irreversible_authorizations
        or state.pending_autonomy_expansion is not None
        or state.autonomy_expansion_history
        or state.e1_schema_upgrade is not None
        or state.r3_schema_upgrade is not None
        or state.r3b_schema_upgrade is not None
        or state.slice_criteria_revisions
        or state.slice_criteria_heads
        or state.criterion_test_receipts
        or state.slice_outcome_receipts
        or state.criterion_coverage_approvals
        or state.feature_completion_receipts
        or state.feature_reopen_receipts
    ):
        raise DecodeError("schema 23 contains authority outside the initial D4 lane")

    change = state.changes[0]
    slice_agreement = state.agreements[1]
    work_id = WorkId(f"work-{change.id}")
    if state.terminal_archives:
        if (
            len(state.terminal_archives) != 1
            or len(state.work_items) != 1
            or state.active_context is not None
            or state.active_work_id is not None
        ):
            raise DecodeError("schema 23 terminal recovery requires one accepted archive and completed work item")
        archive = state.terminal_archives[0]
        context = archive.context
        assessment = archive.convergence_assessment
        item = state.work_items[0]
        if (
            archive.parent_feature_id is not None
            or archive.legacy_unassessed_acceptance
            or context.feature_id != activation.feature_id
            or context.change_id != change.id
            or context.feature_agreement_ref != activation.feature_agreement_ref
            or context.slice_agreement_ref != activation.slice_agreement_ref
            or context.status is not LifecycleState.ACCEPTED
            or context.phase is not Phase.DONE
            or context.side_of is not None
            or context.publication is not None
            or not context.convergence_complete
            or not _d4_has_reported_task(context, slice_agreement.task_id)
            or not has_acceptable_review(context.review)
            or not context.verification.sufficient
            or not can_converge(context.review, context.verification)
            or type(assessment) is not ConvergenceAssessment
            or assessment.change_id != change.id
            or assessment.product_truth_changed is not False
            or assessment.product_truth_content is not None
            or item.id != work_id
            or item.operational_state is not OperationalState.DONE
            or item.context is not None
            or item.preserved_primary_context is not None
            or item.deferred_side_request is not None
            or item.pending_intake is not None
            or item.reason is not None
            or item.terminal_change_id != change.id
        ):
            raise DecodeError("schema 23 accepted terminal archive is inconsistent")
        return

    context = state.active_context
    if (
        context is None
        or state.active_work_id != work_id
        or state.work_items
        or context.feature_id != activation.feature_id
        or context.change_id != change.id
        or context.feature_agreement_ref != activation.feature_agreement_ref
        or context.slice_agreement_ref != activation.slice_agreement_ref
        or context.side_of is not None
        or context.publication is not None
    ):
        raise DecodeError("schema 23 active lifecycle pointers are inconsistent")
    pair = (context.status, context.phase)
    if pair == (LifecycleState.PROPOSED, Phase.RESEARCH):
        valid = (
            context.task_progress is None
            and context.review is None
            and not context.verification.sufficient
            and not context.convergence_complete
        )
    elif pair == (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT):
        valid = (
            _d4_has_reported_task(context, slice_agreement.task_id)
            and context.review is None
        )
    elif pair == (LifecycleState.IN_PROGRESS, Phase.REVIEW):
        valid = _d4_has_reported_task(context, slice_agreement.task_id)
    elif pair == (LifecycleState.VERIFYING, Phase.VERIFY):
        valid = (
            _d4_has_reported_task(context, slice_agreement.task_id)
            and has_acceptable_review(context.review)
        )
    elif pair == (LifecycleState.VERIFYING, Phase.CONVERGE):
        valid = (
            _d4_has_reported_task(context, slice_agreement.task_id)
            and has_acceptable_review(context.review)
            and context.verification.sufficient
            and can_converge(context.review, context.verification)
        )
    else:
        valid = False
    if not valid:
        raise DecodeError("schema 23 active lifecycle state is inconsistent")


def _validate_d5_lifecycle_recovery(
    state: State, activation: ProvisionalSliceActivation,
) -> None:
    """Keep schema 24 to the settled D4 Change plus one adopted Task."""
    if (
        state.preserved_primary_context is not None
        or state.completed_side_contexts
        or state.publications
        or state.publication_history
        or state.decisions
        or state.observations
        or state.drifts
        or state.locks
        or state.lock_revisions
        or state.lock_releases
        or state.lock_authorizations
        or state.change_lock_assessments
        or state.work_relations
        or state.parked_work is not None
        or state.preserved_primary_work_id is not None
        or state.pending_intake is not None
        or state.deferred_side_request is not None
        or state.shared_intents
        or state.deferred_linked_intents
        or state.pending_capability_selection is not None
        or state.capability_selection_history
        or state.pending_discovery_recovery is not None
        or state.irreversible_authorizations
        or state.pending_autonomy_expansion is not None
        or state.autonomy_expansion_history
        or state.e1_schema_upgrade is not None
        or state.r3_schema_upgrade is not None
        or state.r3b_schema_upgrade is not None
        or len(state.provisional_slice_adoptions) != 1
        or len(state.slice_criteria_revisions) != 1
        or len(state.slice_criteria_heads) != 1
        or state.criterion_test_receipts
        or state.slice_outcome_receipts
        or state.criterion_coverage_approvals
        or state.feature_completion_receipts
        or state.feature_reopen_receipts
        or len(state.features) != 1
        or len(state.changes) != 2
        or len(state.agreements) != 4
        or len(state.terminal_archives) not in (1, 2)
        or len(state.work_items) != len(state.terminal_archives)
    ):
        raise DecodeError("schema 24 contains authority outside one adopted Task lane")

    adoption = state.provisional_slice_adoptions[0]
    original_archive = state.terminal_archives[0]
    original_item = state.work_items[0]
    original_state = replace(
        state,
        active_context=None,
        active_work_id=None,
        changes=state.changes[:1],
        agreements=state.agreements[:2],
        slice_criteria_revisions=(),
        slice_criteria_heads=(),
        provisional_slice_adoptions=(),
        terminal_archives=(original_archive,),
        work_items=(original_item,),
    )
    _validate_d4_lifecycle_recovery(original_state, activation)
    if (
        original_archive.context.change_id != state.changes[0].id
        or state.changes[1].id != adoption.change_id
        or state.agreements[2].id != adoption.feature_agreement_ref
        or state.agreements[3].id != adoption.slice_agreement_ref
        or state.agreements[3].task_id != adoption.task_id
        or original_item.id != WorkId(f"work-{state.changes[0].id}")
        or original_item.operational_state is not OperationalState.DONE
        or original_item.terminal_change_id != state.changes[0].id
    ):
        raise DecodeError("schema 24 does not preserve the original settled D4 Change")

    if len(state.terminal_archives) == 2:
        if state.active_context is not None or state.active_work_id is not None:
            raise DecodeError("schema 24 settled Task cannot coexist with active work")
        archive = state.terminal_archives[1]
        context = archive.context
        assessment = archive.convergence_assessment
        item = state.work_items[1]
        if (
            context.feature_id != adoption.feature_id
            or context.change_id != adoption.change_id
            or context.feature_agreement_ref != adoption.feature_agreement_ref
            or context.slice_agreement_ref != adoption.slice_agreement_ref
            or context.status is not LifecycleState.ACCEPTED
            or context.phase is not Phase.DONE
            or context.side_of is not None
            or context.publication is not None
            or not context.convergence_complete
            or not _d4_has_reported_task(context, adoption.task_id)
            or not has_acceptable_review(context.review)
            or not context.verification.sufficient
            or not can_converge(context.review, context.verification)
            or type(assessment) is not ConvergenceAssessment
            or assessment.change_id != adoption.change_id
            or assessment.product_truth_changed is not False
            or assessment.product_truth_content is not None
            or archive.parent_feature_id is not None
            or archive.legacy_unassessed_acceptance
            or item.id != WorkId(f"work-{adoption.change_id}")
            or item.operational_state is not OperationalState.DONE
            or item.context is not None
            or item.terminal_change_id != adoption.change_id
        ):
            raise DecodeError("schema 24 accepted Task archive is inconsistent")
        return

    context = state.active_context
    if (
        context is None
        or state.active_work_id != WorkId(f"work-{adoption.change_id}")
        or len(state.work_items) != 1
        or context.feature_id != adoption.feature_id
        or context.change_id != adoption.change_id
        or context.feature_agreement_ref != adoption.feature_agreement_ref
        or context.slice_agreement_ref != adoption.slice_agreement_ref
        or context.side_of is not None
        or context.publication is not None
    ):
        raise DecodeError("schema 24 active Task pointers are inconsistent")
    pair = (context.status, context.phase)
    if pair == (LifecycleState.PROPOSED, Phase.RESEARCH):
        valid = (
            context.task_progress is None
            and context.review is None
            and not context.verification.sufficient
            and not context.convergence_complete
        )
    elif pair == (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT):
        valid = _d4_has_reported_task(context, adoption.task_id) and context.review is None
    elif pair == (LifecycleState.IN_PROGRESS, Phase.REVIEW):
        valid = _d4_has_reported_task(context, adoption.task_id)
    elif pair == (LifecycleState.VERIFYING, Phase.VERIFY):
        valid = _d4_has_reported_task(context, adoption.task_id) and has_acceptable_review(context.review)
    elif pair == (LifecycleState.VERIFYING, Phase.CONVERGE):
        valid = (
            _d4_has_reported_task(context, adoption.task_id)
            and has_acceptable_review(context.review)
            and context.verification.sufficient
            and can_converge(context.review, context.verification)
        )
    else:
        valid = False
    if not valid:
        raise DecodeError("schema 24 active Task lifecycle state is inconsistent")


def _validate_provisional_slice_authority(
    root: Path, state: State, *, schema: int = PROVISIONAL_SLICE_SCHEMA,
) -> None:
    revisions = state.provisional_slice_revisions
    heads = state.provisional_slice_heads
    if bool(revisions) != bool(heads):
        raise DecodeError("provisional Slice revisions and heads must be paired")
    if schema == D4_PROVISIONAL_SLICE_SCHEMA:
        activation = state.provisional_slice_activation
        if (
            activation is None
            or activation.marker != D4_PROVISIONAL_SLICE_ACTIVATION_MARKER
            or activation.source_schema != 15
            or activation.source_revision != 2
            or activation.recorded_revision != 3
            or activation.recorded_revision > state.revision
            or len(state.features) != 1
            or len(state.changes) != 1
            or len(state.agreements) != 2
            or state.features[0].id != activation.feature_id
            or state.changes[0].feature_id != activation.feature_id
            or state.agreements[0].id != activation.feature_agreement_ref
            or state.agreements[1].id != activation.slice_agreement_ref
            or state.agreements[0].kind != "FEATURE"
            or state.agreements[1].kind != "SLICE"
            or state.agreements[0].feature_id != activation.feature_id
            or state.agreements[1].feature_id != activation.feature_id
            or state.agreements[0].change_id != state.agreements[1].change_id
            or state.changes[0].id != state.agreements[0].change_id
            or state.r3_schema_upgrade is not None
            or state.r3b_schema_upgrade is not None
            or state.e1_schema_upgrade is not None
            or state.slice_criteria_revisions
            or state.slice_criteria_heads
            or state.criterion_test_receipts
            or state.slice_outcome_receipts
            or state.criterion_coverage_approvals
            or state.feature_completion_receipts
            or state.feature_reopen_receipts
        ):
            raise DecodeError("schema 23 requires the exact D4 initial-session activation lane")
        if len(revisions) > 32 or len(heads) > 32:
            raise DecodeError("D4 provisional Slice project limits are exceeded")
        _validate_d4_lifecycle_recovery(state, activation)
    elif schema == D5_PROVISIONAL_SLICE_SCHEMA:
        activation = state.provisional_slice_activation
        adoption = state.provisional_slice_adoptions
        if (
            activation is None
            or activation.marker != D4_PROVISIONAL_SLICE_ACTIVATION_MARKER
            or activation.recorded_revision > state.revision
            or len(adoption) != 1
            or len(state.features) != 1
            or len(state.changes) != 2
            or len(state.agreements) != 4
            or state.features[0].id != activation.feature_id
            or state.changes[0].feature_id != activation.feature_id
            or state.agreements[0].id != activation.feature_agreement_ref
            or state.agreements[1].id != activation.slice_agreement_ref
            or state.agreements[0].kind != "FEATURE"
            or state.agreements[1].kind != "SLICE"
            or state.agreements[0].feature_id != activation.feature_id
            or state.agreements[1].feature_id != activation.feature_id
            or state.agreements[0].change_id != state.agreements[1].change_id
            or state.changes[0].id != state.agreements[0].change_id
            or state.r3_schema_upgrade is not None
            or state.r3b_schema_upgrade is not None
            or state.e1_schema_upgrade is not None
            or len(state.slice_criteria_revisions) != 1
            or len(state.slice_criteria_heads) != 1
            or state.criterion_test_receipts
            or state.slice_outcome_receipts
            or state.criterion_coverage_approvals
            or state.feature_completion_receipts
            or state.feature_reopen_receipts
            or len(revisions) > 32
            or len(heads) > 32
        ):
            raise DecodeError("schema 24 requires the exact D5 adopted-head lane")
        _validate_d5_lifecycle_recovery(state, activation)
    resolved_root = _resolved_root(root)
    initialization = state.initialization
    repository_id = sha256(str(resolved_root).encode("utf-8")).hexdigest()[:16]
    if initialization is None or initialization.repository_id != repository_id:
        if schema >= D4_PROVISIONAL_SLICE_SCHEMA or revisions or heads:
            raise DecodeError("provisional Slice repository identity does not match this root")
    if not revisions and not heads:
        return
    features = {item.id: item for item in state.features}
    revisions_by_proposal: dict[str, list[ProvisionalSliceRevision]] = {}
    for revision in revisions:
        _validate_provisional_slice_record(
            revision, bounded=schema >= D4_PROVISIONAL_SLICE_SCHEMA,
        )
        if revision.repository_id != repository_id:
            raise DecodeError("provisional Slice record belongs to a different repository")
        feature = features.get(revision.feature_id)
        feature_agreements = tuple(
            item for item in state.agreements
            if item.kind == "FEATURE" and item.feature_id == revision.feature_id
        )
        slice_agreements = tuple(
            item for item in state.agreements
            if item.kind == "SLICE" and item.feature_id == revision.feature_id
        )
        feature_changes = tuple(item.change_id for item in feature_agreements)
        slice_changes = tuple(item.change_id for item in slice_agreements)
        agreement_contexts = tuple(
            context for context in (state.active_context, state.preserved_primary_context)
            if context is not None
        ) + tuple(
            context for item in state.work_items
            for context in (item.context, item.preserved_primary_context)
            if context is not None
        ) + tuple(archive.context for archive in state.terminal_archives)
        context_pairs = {
            (context.feature_id, context.change_id, context.feature_agreement_ref, context.slice_agreement_ref)
            for context in agreement_contexts
            if context.feature_agreement_ref is not None and context.slice_agreement_ref is not None
        }
        if (
            feature is None
            or not feature_agreements
            or len(feature_changes) != len(set(feature_changes))
            or len(slice_changes) != len(set(slice_changes))
            or set(feature_changes) != set(slice_changes)
            or any(
                item.repository_id != repository_id or item.title != feature.title
                for item in feature_agreements
            )
            or any(item.repository_id != repository_id for item in slice_agreements)
            or any(
                feature_agreement.source != slice_agreement.source
                or feature_agreement.statement != slice_agreement.statement
                or feature_agreement.source_reference != slice_agreement.source_reference
                or (
                    revision.feature_id,
                    feature_agreement.change_id,
                    feature_agreement.id,
                    slice_agreement.id,
                ) not in context_pairs
                for feature_agreement in feature_agreements
                for slice_agreement in slice_agreements
                if slice_agreement.change_id == feature_agreement.change_id
            )
        ):
            raise DecodeError("provisional Slice Feature lacks canonical accepted Feature/Change pairs")
        revisions_by_proposal.setdefault(revision.proposal_id, []).append(revision)
    heads_by_proposal = {item.proposal_id: item for item in heads}
    if set(heads_by_proposal) != set(revisions_by_proposal):
        raise DecodeError("provisional Slice records must be reachable from exactly one head")
    for proposal_id, proposal_revisions in revisions_by_proposal.items():
        ordered = sorted(proposal_revisions, key=lambda item: item.revision)
        if schema >= D4_PROVISIONAL_SLICE_SCHEMA and len(ordered) > 32:
            raise DecodeError("D4 provisional Slice history limit is exceeded")
        first = ordered[0]
        previous_id: str | None = None
        used_criteria: set[str] = set()
        maximum_criterion = 0
        for expected_revision, revision in enumerate(ordered, start=1):
            if (
                revision.revision != expected_revision
                or revision.proposal_id != proposal_id
                or revision.repository_id != first.repository_id
                or revision.feature_id != first.feature_id
                or revision.previous_revision_id != previous_id
            ):
                raise DecodeError("provisional Slice history is not one contiguous linear chain")
            criterion_numbers: list[int] = []
            for item in revision.criteria:
                match = re.fullmatch(r"c-([1-9][0-9]*)", item.id)
                if match is None:
                    raise DecodeError("provisional Slice criterion identities are not canonical")
                criterion_numbers.append(int(match.group(1)))
            if criterion_numbers != sorted(criterion_numbers):
                raise DecodeError("provisional Slice criterion identities are not canonical")
            current_ids = {item.id for item in revision.criteria}
            previous_ids = (
                set() if expected_revision == 1
                else {item.id for item in ordered[expected_revision - 2].criteria}
            )
            new_numbers = [
                number for number, item in zip(criterion_numbers, revision.criteria)
                if item.id not in used_criteria
            ]
            if expected_revision == 1:
                if criterion_numbers != list(range(1, len(criterion_numbers) + 1)):
                    raise DecodeError("provisional Slice genesis criteria must start at c-1")
            else:
                if any(item.id in used_criteria and item.id not in previous_ids for item in revision.criteria):
                    raise DecodeError("removed provisional Slice criteria cannot be resurrected")
                if new_numbers != list(range(
                    maximum_criterion + 1,
                    maximum_criterion + len(new_numbers) + 1,
                )):
                    raise DecodeError("new provisional Slice criteria must use never-used next IDs")
            used_criteria.update(current_ids)
            if criterion_numbers:
                maximum_criterion = max(maximum_criterion, max(criterion_numbers))
            previous_id = revision.revision_id
        head = heads_by_proposal[proposal_id]
        final = ordered[-1]
        if (
            head.revision_id != final.revision_id
            or head.revision != final.revision
            or head.digest != final.digest
        ):
            raise DecodeError("provisional Slice head does not match the final immutable revision")


def _d4_initial_session_request(
    root: Path, state: State,
) -> tuple[AgreementRecord, AgreementRecord]:
    """Revalidate the one untouched schema-15 Core admission D4 can opt into."""
    if (
        state.revision != 2
        or state.initialization is None
        or state.initialization.repository_id
        != sha256(str(_resolved_root(root)).encode("utf-8")).hexdigest()[:16]
        or len(state.features) != 1
        or len(state.changes) != 1
        or len(state.agreements) != 2
        or state.active_context is None
        or state.preserved_primary_context is not None
        or state.work_items
        or state.work_relations
        or state.terminal_archives
        or state.completed_side_contexts
        or state.publications
        or state.publication_history
        or state.decisions
        or state.observations
        or state.drifts
        or state.locks
        or state.lock_revisions
        or state.lock_releases
        or state.lock_authorizations
        or state.pending_intake is not None
        or state.deferred_side_request is not None
        or state.pending_capability_selection is not None
        or state.capability_selection_history
        or state.pending_discovery_recovery is not None
        or state.irreversible_authorizations
        or state.pending_autonomy_expansion is not None
        or state.autonomy_expansion_history
        or state.e1_schema_upgrade is not None
        or state.r3_schema_upgrade is not None
        or state.r3b_schema_upgrade is not None
        or state.slice_criteria_revisions
        or state.slice_criteria_heads
        or state.criterion_test_receipts
        or state.slice_outcome_receipts
        or state.criterion_coverage_approvals
        or state.feature_completion_receipts
        or state.feature_reopen_receipts
        or state.provisional_slice_revisions
        or state.provisional_slice_heads
        or state.provisional_slice_activation is not None
    ):
        raise DecodeError("D4 activation requires the untouched initial schema-15 session request")
    feature_agreement, slice_agreement = state.agreements
    feature = state.features[0]
    change = state.changes[0]
    context = state.active_context
    reference_match = re.fullmatch(
        r"session:(ses_[A-Za-z0-9_-]{3,128}):(msg_[A-Za-z0-9_-]{3,128}):([0-9a-f]{16})",
        slice_agreement.source_reference,
    )
    if reference_match is None:
        raise DecodeError("D4 activation requires canonical SESSION_REQUEST provenance")
    expected_source_digest = sha256(slice_agreement.statement.encode("utf-8")).hexdigest()[:16]
    repository_id = state.initialization.repository_id
    expected_digest = sha256(json.dumps([
        repository_id,
        slice_agreement.statement,
        slice_agreement.statement,
        SESSION_REQUEST_TASK,
        SESSION_REQUEST_CHECKPOINT,
        slice_agreement.statement,
        slice_agreement.source_reference,
    ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    if (
        reference_match.group(3) != expected_source_digest
        or feature_agreement.kind != "FEATURE"
        or slice_agreement.kind != "SLICE"
        or feature_agreement.id != f"agreement-feature-{expected_digest}"
        or slice_agreement.id != f"agreement-slice-{expected_digest}"
        or feature_agreement.feature_id != f"feature-{expected_digest}"
        or slice_agreement.feature_id != feature_agreement.feature_id
        or feature_agreement.change_id != f"change-{expected_digest}"
        or slice_agreement.change_id != feature_agreement.change_id
        or feature_agreement.source != "SESSION_REQUEST"
        or slice_agreement.source != "SESSION_REQUEST"
        or feature_agreement.source_reference != slice_agreement.source_reference
        or feature_agreement.statement != slice_agreement.statement
        or feature_agreement.repository_id != repository_id
        or slice_agreement.repository_id != repository_id
        or feature_agreement.title != slice_agreement.statement.strip()
        or slice_agreement.title != slice_agreement.statement.strip()
        or slice_agreement.task_id != f"task-{expected_digest}"
        or slice_agreement.initial_task != SESSION_REQUEST_TASK
        or slice_agreement.checkpoint != SESSION_REQUEST_CHECKPOINT
        or feature.id != feature_agreement.feature_id
        or feature.title != feature_agreement.title
        or str(feature.living_spec_id) != f"spec-{expected_digest}"
        or change.id != feature_agreement.change_id
        or change.feature_id != feature.id
        or change.intent != slice_agreement.statement.strip()
        or change.classification is not Classification.FEATURE
        or context.feature_id != feature.id
        or context.change_id != change.id
        or context.feature_agreement_ref != feature_agreement.id
        or context.slice_agreement_ref != slice_agreement.id
        or context.focus != slice_agreement.title
        or context.current_work != SESSION_REQUEST_TASK
        or context.task_progress is not None
        or context.status is not LifecycleState.PROPOSED
        or context.phase is not Phase.RESEARCH
    ):
        raise DecodeError("D4 activation does not match Core's initial SESSION_REQUEST acceptance")
    return feature_agreement, slice_agreement


def _new_d4_provisional_slice_revision(
    repository_id: str,
    feature_id: str,
    expected_head: ProvisionalSliceHead | None,
    title: str,
    outcome: str,
    example: str | None,
    inclusions: tuple[str, ...],
    exclusions: tuple[str, ...],
    considerations: tuple[str, ...],
    open_questions: tuple[str, ...],
    criteria: tuple[ProvisionalSliceCriterion, ...],
) -> ProvisionalSliceRevision:
    provenance = ProvisionalSliceProvenance("AGENT_SUGGESTION")
    genesis: dict[str, JsonValue] = {
        "repository_id": repository_id,
        "feature_id": feature_id,
        "title": title,
        "outcome": outcome,
        "example": example,
        "inclusions": list(inclusions),
        "exclusions": list(exclusions),
        "considerations": list(considerations),
        "open_questions": list(open_questions),
        "criteria": [
            {
                "id": item.id,
                "expected_result": item.expected_result,
                "expected_evidence": item.expected_evidence,
            }
            for item in criteria
        ],
        "provenance": _provisional_slice_provenance_to_json(provenance),
    }
    proposal_id = (
        "ps-" + sha256(encode_json(genesis)).hexdigest()
        if expected_head is None else expected_head.proposal_id
    )
    number = 1 if expected_head is None else expected_head.revision + 1
    revision = ProvisionalSliceRevision(
        proposal_id,
        f"{proposal_id}-r{number}",
        number,
        repository_id,
        FeatureId(feature_id),
        title,
        outcome,
        example,
        inclusions,
        exclusions,
        considerations,
        open_questions,
        criteria,
        provenance,
        None if expected_head is None else expected_head.revision_id,
        "",
    )
    revision = replace(revision, digest=_provisional_slice_digest(revision))
    _validate_provisional_slice_record(revision, bounded=True)
    return revision


def _archive_from_json(value: JsonValue, schema: int) -> TerminalArchive:
    fields = frozenset(("context", "parent_feature_id"))
    raw = _object(
        value,
        fields | frozenset(("convergence_assessment", "legacy_unassessed_acceptance"))
        if schema >= 7
        else fields,
        "terminal archive",
    )
    context = _context_from_json(raw["context"], schema)
    if context is None:
        raise DecodeError("terminal archive context cannot be null")
    parent = _optional_string(raw["parent_feature_id"], "archive parent_feature_id")
    return TerminalArchive(
        context,
        None if parent is None else FeatureId(parent),
        _convergence_assessment_from_json(raw["convergence_assessment"]) if schema >= 7 else None,
        _boolean(raw["legacy_unassessed_acceptance"], "archive legacy acceptance provenance")
        if schema >= 7
        else context.status == LifecycleState.ACCEPTED and context.publication is None,
    )


def _work_id(context: WorkingContext) -> WorkId:
    if context.change_id is None:
        raise DecodeError("queued work requires a change identity")
    return WorkId(f"work-{context.change_id}")


def _legacy_operational_state(reason: str) -> OperationalState:
    return OperationalState.BLOCKED if "external" in reason.lower() else OperationalState.FROZEN


def _legacy_preserved_work_id(value: JsonValue, schema: int) -> WorkId | None:
    context = _context_from_json(value, schema)
    return None if context is None else _work_id(context)


def _r3_upgrade_to_json(receipt: R3SchemaUpgradeReceipt | None) -> JsonValue:
    if receipt is None:
        return None
    return {"source_revision": receipt.source_revision, "converted_revision": receipt.converted_revision,
            "source_sha256": receipt.source_sha256, "statement": receipt.statement,
            "source_reference": receipt.source_reference, "repository_id": receipt.repository_id}


def _r3_upgrade_from_json(value: JsonValue) -> R3SchemaUpgradeReceipt | None:
    if value is None:
        return None
    raw = _object(value, frozenset(("source_revision", "converted_revision", "source_sha256",
                                    "statement", "source_reference", "repository_id")), "R3 upgrade")
    return R3SchemaUpgradeReceipt(
        _integer(raw["source_revision"], "R3 source revision"),
        _integer(raw["converted_revision"], "R3 converted revision"),
        _string(raw["source_sha256"], "R3 source digest"),
        _string(raw["statement"], "R3 approval"),
        _string(raw["source_reference"], "R3 approval reference"),
        _string(raw["repository_id"], "R3 repository identity"),
    )


_CRITERION_TEST_FIELDS: Final = frozenset((
    "id", "slice_agreement_ref", "criteria_revision_id", "criterion_id",
    "test_kind", "test_target", "exit_code", "output_sha256",
    "source_revision", "recorded_revision",
))
_CRITERION_TEST_R3B_FIELDS: Final = frozenset((
    "input_fingerprint_before", "input_fingerprint_after", "input_policy_id",
    "input_policy_digest", "admission_policy_id", "admission_policy_digest",
    "external_inputs", "runtime_executable", "runtime_executable_sha256",
    "runtime_version", "runtime_argv", "environment_profile_id",
    "environment_profile_digest",
))
_CRITERION_TEST_COVERAGE_FIELDS: Final = frozenset((
    "coverage_approval_id", "coverage_contract_digest",
))


def _coverage_input_to_json(value: CoverageInput) -> JsonValue:
    return {"kind": value.kind, "path": value.path}


def _coverage_input_from_json(value: JsonValue) -> CoverageInput:
    raw = _object(value, frozenset(("kind", "path")), "coverage input")
    return CoverageInput(
        _string(raw["kind"], "coverage input kind"),
        _string(raw["path"], "coverage input path"),
    )


def _coverage_approval_to_json(approval: CriterionCoverageApproval) -> JsonValue:
    return {
        "id": approval.id,
        "contract_digest": approval.contract_digest,
        "repository_id": approval.repository_id,
        "slice_agreement_ref": approval.slice_agreement_ref,
        "criteria_revision_id": approval.criteria_revision_id,
        "criterion_id": approval.criterion_id,
        "test_kind": approval.test_kind,
        "test_target": approval.test_target,
        "known_inputs": [_coverage_input_to_json(item) for item in approval.known_inputs],
        "root_fingerprint": approval.root_fingerprint,
        "input_policy_id": approval.input_policy_id,
        "input_policy_digest": approval.input_policy_digest,
        "admission_policy_id": approval.admission_policy_id,
        "admission_policy_digest": approval.admission_policy_digest,
        "environment_profile_id": approval.environment_profile_id,
        "environment_profile_policy_digest": approval.environment_profile_policy_digest,
        "statement": approval.statement,
        "source_reference": approval.source_reference,
        "source": approval.source,
        "source_revision": approval.source_revision,
        "recorded_revision": approval.recorded_revision,
    }


def _coverage_approval_from_json(value: JsonValue) -> CriterionCoverageApproval:
    raw = _object(value, frozenset((
        "id", "contract_digest", "repository_id", "slice_agreement_ref",
        "criteria_revision_id", "criterion_id", "test_kind", "test_target",
        "known_inputs", "root_fingerprint", "input_policy_id", "input_policy_digest",
        "admission_policy_id", "admission_policy_digest", "environment_profile_id",
        "environment_profile_policy_digest", "statement", "source_reference", "source",
        "source_revision", "recorded_revision",
    )), "criterion coverage approval")
    return CriterionCoverageApproval(
        _string(raw["id"], "coverage approval id"),
        _string(raw["contract_digest"], "coverage contract digest"),
        _string(raw["repository_id"], "coverage repository"),
        _string(raw["slice_agreement_ref"], "coverage Slice"),
        _string(raw["criteria_revision_id"], "coverage criteria revision"),
        _string(raw["criterion_id"], "coverage criterion"),
        _string(raw["test_kind"], "coverage test kind"),
        _string(raw["test_target"], "coverage test target"),
        tuple(_coverage_input_from_json(item) for item in _array(raw["known_inputs"], "known inputs")),
        _string(raw["root_fingerprint"], "coverage root fingerprint"),
        _string(raw["input_policy_id"], "coverage input policy"),
        _string(raw["input_policy_digest"], "coverage input policy digest"),
        _string(raw["admission_policy_id"], "coverage admission policy"),
        _string(raw["admission_policy_digest"], "coverage admission policy digest"),
        _string(raw["environment_profile_id"], "coverage environment profile"),
        _string(raw["environment_profile_policy_digest"], "coverage environment policy digest"),
        _string(raw["statement"], "coverage approval statement"),
        _string(raw["source_reference"], "coverage approval reference"),
        _string(raw["source"], "coverage approval source"),
        _integer(raw["source_revision"], "coverage source revision"),
        _integer(raw["recorded_revision"], "coverage recorded revision"),
    )


def _r3b_upgrade_to_json(receipt: R3BSchemaUpgradeReceipt | None) -> JsonValue:
    if receipt is None:
        return None
    value: dict[str, JsonValue] = {
        "source_revision": receipt.source_revision,
        "converted_revision": receipt.converted_revision,
        "source_sha256": receipt.source_sha256,
        "statement": receipt.statement,
        "source_reference": receipt.source_reference,
        "repository_id": receipt.repository_id,
        "slice_agreement_ref": receipt.slice_agreement_ref,
        "criteria_revision_id": receipt.criteria_revision_id,
    }
    if receipt.input_fingerprint is not None or receipt.coverage_contract_digest is not None:
        value["input_fingerprint"] = receipt.input_fingerprint
        value["coverage_contract_digest"] = receipt.coverage_contract_digest
    return value


def _r3b_upgrade_from_json(value: JsonValue) -> R3BSchemaUpgradeReceipt | None:
    if value is None:
        return None
    fields = frozenset((
        "source_revision", "converted_revision", "source_sha256", "statement",
        "source_reference", "repository_id", "slice_agreement_ref", "criteria_revision_id",
    ))
    extended = fields | frozenset(("input_fingerprint", "coverage_contract_digest"))
    actual = set(value) if isinstance(value, dict) else set()
    raw = _object(value, extended if actual == extended else fields, "R3-B upgrade")
    return R3BSchemaUpgradeReceipt(
        _integer(raw["source_revision"], "R3-B source revision"),
        _integer(raw["converted_revision"], "R3-B converted revision"),
        _string(raw["source_sha256"], "R3-B source digest"),
        _string(raw["statement"], "R3-B approval"),
        _string(raw["source_reference"], "R3-B approval reference"),
        _string(raw["repository_id"], "R3-B repository identity"),
        _string(raw["slice_agreement_ref"], "R3-B Slice"),
        _string(raw["criteria_revision_id"], "R3-B criteria revision"),
        None if "input_fingerprint" not in raw else _string(raw["input_fingerprint"], "R3-B input fingerprint"),
        None if "coverage_contract_digest" not in raw else _string(raw["coverage_contract_digest"], "R3-B coverage digest"),
    )


def _criterion_test_to_json(receipt: CriterionTestReceipt, schema: int) -> JsonValue:
    value: dict[str, JsonValue] = {
        "id": receipt.id, "slice_agreement_ref": receipt.slice_agreement_ref,
        "criteria_revision_id": receipt.criteria_revision_id, "criterion_id": receipt.criterion_id,
        "test_kind": receipt.test_kind, "test_target": receipt.test_target,
        "exit_code": receipt.exit_code, "output_sha256": receipt.output_sha256,
        "source_revision": receipt.source_revision, "recorded_revision": receipt.recorded_revision,
    }
    provenance = (
        receipt.input_fingerprint_before, receipt.input_fingerprint_after, receipt.input_policy_id,
        receipt.input_policy_digest, receipt.admission_policy_id, receipt.admission_policy_digest,
        receipt.runtime_executable, receipt.runtime_executable_sha256, receipt.runtime_version,
        receipt.environment_profile_id, receipt.environment_profile_digest,
        receipt.coverage_approval_id, receipt.coverage_contract_digest,
    )
    if schema < R3B_SCHEMA:
        if any(item is not None for item in provenance) or receipt.external_inputs or receipt.runtime_argv:
            raise ValueError("R3-B test provenance cannot be encoded in an older schema")
        return value
    if any(item is not None for item in provenance):
        value.update({
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
        })
        if receipt.coverage_approval_id is not None or receipt.coverage_contract_digest is not None:
            value["coverage_approval_id"] = receipt.coverage_approval_id
            value["coverage_contract_digest"] = receipt.coverage_contract_digest
    return value


def _criterion_test_from_json(value: JsonValue, schema: int) -> CriterionTestReceipt:
    if not isinstance(value, dict):
        raise DecodeError("R3 criterion test fields are not exact")
    fields = _CRITERION_TEST_FIELDS
    extended = fields | _CRITERION_TEST_R3B_FIELDS
    covered = extended | _CRITERION_TEST_COVERAGE_FIELDS
    actual = set(value)
    allowed = (
        covered if schema >= R3B_SCHEMA and actual == covered
        else extended if schema >= R3B_SCHEMA and actual == extended
        else fields
    )
    raw = _object(value, allowed, "R3 criterion test")
    def optional_r3_string(key: str, label: str) -> str | None:
        if key not in raw or schema in (
            REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
            PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA,
            D5_PROVISIONAL_SLICE_SCHEMA,
        ) and raw[key] is None:
            return None
        return _string(raw[key], label)

    return CriterionTestReceipt(
        _string(raw["id"], "R3 test id"), _string(raw["slice_agreement_ref"], "R3 Slice"),
        _string(raw["criteria_revision_id"], "R3 criteria revision"),
        _string(raw["criterion_id"], "R3 criterion"),
        _string(raw["test_kind"], "R3 test kind"),
        _string(raw["test_target"], "R3 test target"),
        _integer(raw["exit_code"], "R3 test exit code"),
        _string(raw["output_sha256"], "R3 test output digest"),
        _integer(raw["source_revision"], "R3 source revision"),
        _integer(raw["recorded_revision"], "R3 recorded revision"),
        None if "input_fingerprint_before" not in raw else _string(raw["input_fingerprint_before"], "R3 input fingerprint before"),
        None if "input_fingerprint_after" not in raw else _string(raw["input_fingerprint_after"], "R3 input fingerprint after"),
        None if "input_policy_id" not in raw else _string(raw["input_policy_id"], "R3 input policy id"),
        None if "input_policy_digest" not in raw else _string(raw["input_policy_digest"], "R3 input policy digest"),
        None if "admission_policy_id" not in raw else _string(raw["admission_policy_id"], "R3 admission policy id"),
        None if "admission_policy_digest" not in raw else _string(raw["admission_policy_digest"], "R3 admission policy digest"),
        () if "external_inputs" not in raw else _strings(raw["external_inputs"], "R3 external inputs"),
        optional_r3_string("runtime_executable", "R3 runtime executable"),
        optional_r3_string("runtime_executable_sha256", "R3 runtime executable digest"),
        optional_r3_string("runtime_version", "R3 runtime version"),
        () if "runtime_argv" not in raw else _strings(raw["runtime_argv"], "R3 runtime argv"),
        optional_r3_string("environment_profile_id", "R3 environment profile"),
        optional_r3_string("environment_profile_digest", "R3 environment profile digest"),
        optional_r3_string("coverage_approval_id", "R3 coverage approval id"),
        optional_r3_string("coverage_contract_digest", "R3 coverage contract digest"),
    )


_SLICE_OUTCOME_FIELDS: Final = frozenset((
    "id", "repository_id", "slice_agreement_ref", "criteria_revision_id",
    "evidence_receipt_ids", "input_fingerprint", "input_policy_id",
    "input_policy_digest", "admission_policy_id", "admission_policy_digest",
    "relevant_drift_digest", "basis", "source", "source_revision", "recorded_revision",
))


def _slice_outcome_to_json(receipt: SliceOutcomeReceipt) -> JsonValue:
    value: dict[str, JsonValue] = {
        "id": receipt.id,
        "repository_id": receipt.repository_id,
        "slice_agreement_ref": receipt.slice_agreement_ref,
        "criteria_revision_id": receipt.criteria_revision_id,
        "evidence_receipt_ids": list(receipt.evidence_receipt_ids),
        "input_fingerprint": receipt.input_fingerprint,
        "input_policy_id": receipt.input_policy_id,
        "input_policy_digest": receipt.input_policy_digest,
        "admission_policy_id": receipt.admission_policy_id,
        "admission_policy_digest": receipt.admission_policy_digest,
        "relevant_drift_digest": receipt.relevant_drift_digest,
        "basis": receipt.basis,
        "source": receipt.source,
        "source_revision": receipt.source_revision,
        "recorded_revision": receipt.recorded_revision,
    }
    if receipt.coverage_contract_digest is not None:
        value["coverage_contract_digest"] = receipt.coverage_contract_digest
    return value


def _slice_outcome_from_json(value: JsonValue) -> SliceOutcomeReceipt:
    if not isinstance(value, dict):
        raise DecodeError("Slice outcome receipt fields are not exact")
    covered = _SLICE_OUTCOME_FIELDS | frozenset(("coverage_contract_digest",))
    raw = _object(value, covered if set(value) == covered else _SLICE_OUTCOME_FIELDS, "Slice outcome receipt")
    return SliceOutcomeReceipt(
        _string(raw["id"], "Slice outcome id"),
        _string(raw["repository_id"], "Slice outcome repository"),
        _string(raw["slice_agreement_ref"], "Slice outcome agreement"),
        _string(raw["criteria_revision_id"], "Slice outcome criteria revision"),
        _strings(raw["evidence_receipt_ids"], "Slice outcome evidence receipts"),
        _string(raw["input_fingerprint"], "Slice outcome fingerprint"),
        _string(raw["input_policy_id"], "Slice outcome input policy"),
        _string(raw["input_policy_digest"], "Slice outcome input policy digest"),
        _string(raw["admission_policy_id"], "Slice outcome admission policy"),
        _string(raw["admission_policy_digest"], "Slice outcome admission policy digest"),
        _string(raw["relevant_drift_digest"], "Slice outcome drift digest"),
        _string(raw["basis"], "Slice outcome basis"),
        _string(raw["source"], "Slice outcome source"),
        _integer(raw["source_revision"], "Slice outcome source revision"),
        _integer(raw["recorded_revision"], "Slice outcome recorded revision"),
        None if "coverage_contract_digest" not in raw else _string(raw["coverage_contract_digest"], "Slice outcome coverage digest"),
    )


_FEATURE_COMPLETION_FIELDS: Final = frozenset((
    "id", "repository_id", "feature_id", "feature_agreement_refs", "condition",
    "slice_agreement_refs", "slice_outcome_receipt_ids", "input_fingerprint",
    "statement", "source", "source_reference", "source_revision", "recorded_revision",
))


def _feature_completion_to_json(receipt: FeatureCompletionReceipt) -> JsonValue:
    """Persist only the exact human-confirmed Feature closure basis."""
    value: dict[str, JsonValue] = {
        "id": receipt.id,
        "repository_id": receipt.repository_id,
        "feature_id": receipt.feature_id,
        "feature_agreement_refs": list(receipt.feature_agreement_refs),
        "condition": receipt.condition,
        "slice_agreement_refs": list(receipt.slice_agreement_refs),
        "slice_outcome_receipt_ids": list(receipt.slice_outcome_receipt_ids),
        "input_fingerprint": receipt.input_fingerprint,
        "statement": receipt.statement,
        "source": receipt.source,
        "source_reference": receipt.source_reference,
        "source_revision": receipt.source_revision,
        "recorded_revision": receipt.recorded_revision,
    }
    # Keep legacy schema-18 receipts byte-identical when carried into schema 19.
    if receipt.cycle_id:
        value["cycle_id"] = receipt.cycle_id
    return value


def _feature_completion_from_json(value: JsonValue) -> FeatureCompletionReceipt:
    fields = _FEATURE_COMPLETION_FIELDS | frozenset(("cycle_id",))
    raw = _object(value, fields if isinstance(value, dict) and set(value) == fields
                  else _FEATURE_COMPLETION_FIELDS, "Feature completion receipt")
    return FeatureCompletionReceipt(
        _string(raw["id"], "Feature completion id"),
        _string(raw["repository_id"], "Feature completion repository"),
        FeatureId(_string(raw["feature_id"], "Feature completion Feature")),
        _strings(raw["feature_agreement_refs"], "Feature completion agreements"),
        _string(raw["condition"], "Feature completion condition"),
        _strings(raw["slice_agreement_refs"], "Feature completion Slices"),
        _strings(raw["slice_outcome_receipt_ids"], "Feature completion outcomes"),
        _string(raw["input_fingerprint"], "Feature completion fingerprint"),
        _string(raw["statement"], "Feature completion statement"),
        _string(raw["source"], "Feature completion source"),
        _string(raw["source_reference"], "Feature completion source reference"),
        _integer(raw["source_revision"], "Feature completion source revision"),
        _integer(raw["recorded_revision"], "Feature completion recorded revision"),
        _integer(raw["cycle_id"], "Feature completion cycle") if "cycle_id" in raw else 0,
    )


_FEATURE_REOPEN_FIELDS: Final = frozenset((
    "id", "repository_id", "feature_id", "cycle_id", "previous_completion_receipt_id",
    "reason", "purpose", "input_fingerprint", "statement", "source",
    "source_reference", "source_revision", "recorded_revision",
))


def _feature_reopen_to_json(receipt: FeatureReopenReceipt) -> JsonValue:
    """Store the exact human reopening decision, never a mutable cycle pointer."""
    return {
        "id": receipt.id, "repository_id": receipt.repository_id,
        "feature_id": receipt.feature_id, "cycle_id": receipt.cycle_id,
        "previous_completion_receipt_id": receipt.previous_completion_receipt_id,
        "reason": receipt.reason, "purpose": receipt.purpose,
        "input_fingerprint": receipt.input_fingerprint, "statement": receipt.statement,
        "source": receipt.source, "source_reference": receipt.source_reference,
        "source_revision": receipt.source_revision, "recorded_revision": receipt.recorded_revision,
    }


def _feature_reopen_from_json(value: JsonValue) -> FeatureReopenReceipt:
    raw = _object(value, _FEATURE_REOPEN_FIELDS, "Feature reopening receipt")
    return FeatureReopenReceipt(
        _string(raw["id"], "Feature reopening id"),
        _string(raw["repository_id"], "Feature reopening repository"),
        FeatureId(_string(raw["feature_id"], "Feature reopening Feature")),
        _integer(raw["cycle_id"], "Feature reopening cycle"),
        _string(raw["previous_completion_receipt_id"], "Feature reopening previous closure"),
        _string(raw["reason"], "Feature reopening reason"),
        _string(raw["purpose"], "Feature reopening purpose"),
        _string(raw["input_fingerprint"], "Feature reopening fingerprint"),
        _string(raw["statement"], "Feature reopening statement"),
        _string(raw["source"], "Feature reopening source"),
        _string(raw["source_reference"], "Feature reopening source reference"),
        _integer(raw["source_revision"], "Feature reopening source revision"),
        _integer(raw["recorded_revision"], "Feature reopening recorded revision"),
    )


def _recover_state_with_schema(root: Path) -> tuple[State, int | None]:
    """Read the persisted schema and its state from the same authority snapshot."""
    state_path = _artifact_path(root, "state.json")
    if not state_path.exists():
        return State(0, None), None
    try:
        decoded = _read_json(state_path)
        if not isinstance(decoded, dict):
            raise DecodeError("state fields are not exact")
        schema_value = decoded.get("schema")
        schema = _integer(schema_value, "schema")
        if schema < SCHEMA:
            # Legacy fixtures may be derived from a newer blank State. Empty
            # R1 extension slots carry no authority and can be ignored; never
            # discard a populated criterion ledger or current head.
            for field in ("slice_criteria_refs", "slice_criteria_heads"):
                if field in decoded:
                    if decoded[field] != []:
                        raise DecodeError("legacy schema cannot contain Slice criteria authority")
                    decoded.pop(field)
        if schema in (9, 10):
            raw = _object(decoded, STATE_FIELDS_V10, "state")
        elif schema in (11, 12, LEGACY_WRITE_SCHEMA, E1_TASK_PROGRESS_SCHEMA, SCHEMA, R3_SCHEMA, R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA, PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA):
            decoded_fields = set(decoded)
            if schema == D5_PROVISIONAL_SLICE_SCHEMA and decoded_fields == set(STATE_FIELDS_V24):
                raw = _object(decoded, STATE_FIELDS_V24, "state")
            elif schema == D4_PROVISIONAL_SLICE_SCHEMA and decoded_fields == set(STATE_FIELDS_V23):
                raw = _object(decoded, STATE_FIELDS_V23, "state")
            elif schema == PROVISIONAL_SLICE_SCHEMA and decoded_fields == set(STATE_FIELDS_V22):
                raw = _object(decoded, STATE_FIELDS_V22, "state")
            elif schema == REQUEST_OBJECTIVE_SCHEMA and decoded_fields == set(STATE_FIELDS_V20):
                raw = _object(decoded, STATE_FIELDS_V20, "state")
            elif schema == SEMANTIC_REQUEST_OBJECTIVE_SCHEMA and decoded_fields == set(STATE_FIELDS_V21):
                raw = _object(decoded, STATE_FIELDS_V21, "state")
            elif schema == FEATURE_CYCLE_SCHEMA and decoded_fields == set(STATE_FIELDS_V19):
                raw = _object(decoded, STATE_FIELDS_V19, "state")
            elif schema == FEATURE_COMPLETION_SCHEMA and decoded_fields == set(STATE_FIELDS_V18):
                raw = _object(decoded, STATE_FIELDS_V18, "state")
            elif schema == R3B_SCHEMA and decoded_fields == set(STATE_FIELDS_V17):
                raw = _object(decoded, STATE_FIELDS_V17, "state")
            elif schema == R3B_SCHEMA and decoded_fields == set(STATE_FIELDS_V17_LEGACY):
                raw = {**_object(decoded, STATE_FIELDS_V17_LEGACY, "state"), "criterion_coverage_approvals": []}
            elif schema == R3_SCHEMA and decoded_fields == set(STATE_FIELDS_V16):
                raw = _object(decoded, STATE_FIELDS, "state")
            elif schema == SCHEMA and decoded_fields == set(STATE_FIELDS_V15):
                raw = _object(decoded, STATE_FIELDS_V15, "state")
            elif schema == E1_TASK_PROGRESS_SCHEMA and decoded_fields == set(STATE_FIELDS_V14):
                raw = _object(decoded, STATE_FIELDS_V14, "state")
            elif schema == LEGACY_WRITE_SCHEMA and decoded_fields == set(STATE_FIELDS_V13):
                raw = _object(decoded, STATE_FIELDS_V13, "state")
            elif schema == 12 and decoded_fields == set(STATE_FIELDS_V12):
                raw = _object(decoded, STATE_FIELDS_V12, "state")
            elif schema < SCHEMA and decoded_fields == set(STATE_FIELDS_V11):
                raw = {
                    "pending_autonomy_expansion": None,
                    "autonomy_expansion_history": [],
                    **_object(decoded, STATE_FIELDS_V11, "state"),
                }
            elif schema < SCHEMA and decoded_fields == set(STATE_FIELDS_V10):
                raw = {
                    "irreversible_authorizations": [],
                    "pending_autonomy_expansion": None,
                    "autonomy_expansion_history": [],
                    **_object(decoded, STATE_FIELDS_V10, "state"),
                }
            elif schema < SCHEMA and decoded_fields == STATE_FIELDS_V9_PRE_SELECTION:
                raw = {
                    "pending_capability_selection": None,
                    "capability_selection_history": [],
                    "pending_discovery_recovery": None,
                    "irreversible_authorizations": [],
                    "pending_autonomy_expansion": None,
                    "autonomy_expansion_history": [],
                    **_object(decoded, STATE_FIELDS_V9_PRE_SELECTION, "state"),
                }
            elif schema < SCHEMA and decoded_fields == STATE_FIELDS_V9_PRE_RECOVERY:
                raw = {
                    "pending_discovery_recovery": None,
                    "irreversible_authorizations": [],
                    "pending_autonomy_expansion": None,
                    "autonomy_expansion_history": [],
                    **_object(decoded, STATE_FIELDS_V9_PRE_RECOVERY, "state"),
                }
            else:
                raise DecodeError("state fields are not exact")
        else:
            legacy_fields = STATE_FIELDS_V5 if schema == 5 else STATE_FIELDS_V7 if schema in (6, 7) else STATE_FIELDS_V8 if schema == 8 else STATE_FIELDS_V10 if schema == 10 else STATE_FIELDS_V11 if schema == 11 else STATE_FIELDS_V15
            legacy_extras = set(decoded) - set(legacy_fields)
            legacy_extension_fields = _CAPABILITY_SELECTION_FIELDS | _DISCOVERY_RECOVERY_FIELD
            raw = _object(
                (
                    {
                        key: value
                        for key, value in decoded.items()
                        if key not in legacy_extension_fields
                    }
                    if schema != SCHEMA
                    and legacy_extras <= legacy_extension_fields
                    and (
                        "pending_capability_selection" not in decoded
                        or decoded["pending_capability_selection"] is None
                    )
                    and (
                        "capability_selection_history" not in decoded
                        or decoded["capability_selection_history"] == []
                    )
                    and (
                        "pending_discovery_recovery" not in decoded
                        or decoded["pending_discovery_recovery"] is None
                    )
                    else decoded
                ),
                legacy_fields,
                "state",
            )
        if schema not in (5, 6, 7, 8, 9, 10, 11, 12, LEGACY_WRITE_SCHEMA, E1_TASK_PROGRESS_SCHEMA, SCHEMA, R3_SCHEMA, R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA, PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA):
            raise DecodeError("unknown state schema")
        (
            initialization, deferred, intake, selection, recovery, selection_history,
            shared, linked, irreversible_authorizations, pending_autonomy_expansion,
            autonomy_expansion_history,
        ) = _extensions_from_json(raw, schema)
        change_records = _load_references(root, raw["change_refs"], "change_refs", "records/changes", _change_from_json)
        assessment_records = () if schema == 5 else tuple(
            _change_lock_assessments_from_json(value)
            for value in _array(raw["change_lock_assessments"], "change lock assessments")
        )
        assessments_by_change = {item.change_id: item.assessments for item in assessment_records}
        active_context = _context_from_json(raw["active_context"], schema)
        legacy_parked = _parked_work_from_json(raw["parked_work"], schema) if schema == 8 else None
        work_items = (
            tuple(_work_item_from_json(value, schema) for value in _array(raw["work_items"], "work items"))
            if schema >= 9
            else (() if legacy_parked is None else (_legacy_work_item(legacy_parked),))
        )
        state = State(
            _integer(raw["revision"], "revision"),
            active_context,
            _context_from_json(raw["preserved_primary_context"], schema),
            tuple(
                _required_publication_from_json(value)
                for value in _array(raw["publications"], "publications")
            ),
            tuple(
                _archive_from_json(value, schema)
                for value in _array(raw["completed_side_contexts"], "completed_side_contexts")
            ),
            tuple(_feature_from_json(value) for value in _array(raw["features"], "features")),
            tuple(replace(change, lock_assessments=assessments_by_change.get(change.id, ())) for change in change_records),
            _load_references(root, raw["decision_refs"], "decision_refs", "records/decisions", _decision_from_json),
            _load_references(root, raw["observation_refs"], "observation_refs", "records/observations", _observation_from_json),
            _load_references(root, raw["drift_refs"], "drift_refs", "records/observations", _drift_from_json),
            tuple(
                _archive_from_json(value, schema)
                for value in _array(raw["terminal_archives"], "terminal_archives")
            ),
            tuple(
                _required_publication_from_json(value)
                for value in _array(raw["publication_history"], "publication_history")
            ),
            initialization,
            deferred,
            intake,
            shared,
            linked,
            () if schema == 5 else tuple(_lock_from_json(value) for value in _array(raw["locks"], "semantic locks")),
            () if schema == 5 else tuple(_lock_revision_from_json(value) for value in _array(raw["lock_revisions"], "semantic lock revisions")),
            () if schema == 5 else tuple(_lock_release_from_json(value) for value in _array(raw["lock_releases"], "lock releases")),
            () if schema == 5 else tuple(_lock_authorization_from_json(value) for value in _array(raw["lock_authorizations"], "lock authorizations")),
            assessment_records,
            work_items,
            () if schema < 9 else tuple(_work_relation_from_json(value) for value in _array(raw["work_relations"], "work relations")),
            None,
            (WorkId(_string(raw["active_work_id"], "active work id")) if raw["active_work_id"] is not None else None)
            if schema >= 9
            else (None if active_context is None else _work_id(active_context)),
            (WorkId(_string(raw["preserved_primary_work_id"], "preserved primary work id")) if raw["preserved_primary_work_id"] is not None else None)
            if schema >= 9
            else _legacy_preserved_work_id(raw["preserved_primary_context"], schema),
            selection,
            selection_history,
            recovery,
            irreversible_authorizations,
            pending_autonomy_expansion,
            autonomy_expansion_history,
            agreements=_load_references(root, raw["agreement_refs"], "agreement_refs", "records/agreements", _agreement_from_json) if schema >= 13 else (),
            e1_schema_upgrade=_e1_upgrade_from_json(raw["e1_schema_upgrade"]) if schema >= 14 else None,
            slice_criteria_revisions=_load_references(root, raw["slice_criteria_refs"], "slice_criteria_refs", "records/slice-criteria", _slice_criteria_from_json) if schema >= 15 else (),
            slice_criteria_heads=tuple(
                SliceCriteriaHead(
                    _string(item["slice_agreement_ref"], "criteria head agreement"),
                    _string(item["revision_id"], "criteria head revision"),
                )
                for entry in _array(raw["slice_criteria_heads"], "Slice criteria heads")
                for item in (_object(entry, frozenset(("slice_agreement_ref", "revision_id")), "Slice criteria head"),)
            ) if schema >= 15 else (),
            r3_schema_upgrade=_r3_upgrade_from_json(raw["r3_schema_upgrade"]) if schema >= R3_SCHEMA else None,
            criterion_test_receipts=tuple(
                _criterion_test_from_json(item, schema)
                for item in _array(raw["criterion_test_receipts"], "R3 criterion tests")
            ) if schema >= R3_SCHEMA else (),
            r3b_schema_upgrade=_r3b_upgrade_from_json(raw["r3b_schema_upgrade"]) if schema >= R3B_SCHEMA else None,
            slice_outcome_receipts=tuple(
                _slice_outcome_from_json(item)
                for item in _array(raw["slice_outcome_receipts"], "Slice outcome receipts")
            ) if schema >= R3B_SCHEMA else (),
            criterion_coverage_approvals=tuple(
                _coverage_approval_from_json(item)
                for item in _array(raw.get("criterion_coverage_approvals", []), "criterion coverage approvals")
            ) if schema >= R3B_SCHEMA else (),
            feature_completion_receipts=tuple(
                _feature_completion_from_json(item)
                for item in _array(raw["feature_completion_receipts"], "Feature completion receipts")
            ) if schema >= FEATURE_COMPLETION_SCHEMA else (),
            feature_reopen_receipts=tuple(
                _feature_reopen_from_json(item)
                for item in _array(raw["feature_reopen_receipts"], "Feature reopening receipts")
            ) if schema >= FEATURE_CYCLE_SCHEMA else (),
            provisional_slice_revisions=(
                _load_provisional_slice_revisions(root, raw["provisional_slice_refs"], schema=schema)
                if schema in (PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA) else ()
            ),
            provisional_slice_heads=(
                _load_provisional_slice_heads(raw["provisional_slice_heads"], schema=schema)
                if schema in (PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA) else ()
            ),
            provisional_slice_activation=(
                _provisional_slice_activation_from_json(raw["provisional_slice_activation"])
                if schema in (D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA) else None
            ),
            provisional_slice_adoptions=(
                _load_provisional_slice_adoptions(root, raw["provisional_slice_adoption_refs"])
                if schema == D5_PROVISIONAL_SLICE_SCHEMA else ()
            ),
        )
        if schema >= R3B_SCHEMA and schema not in (
            REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
            PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA,
            D5_PROVISIONAL_SLICE_SCHEMA,
            D5_PROVISIONAL_SLICE_SCHEMA,
        ) and state.r3b_schema_upgrade is None:
            raise DecodeError("schema 17 requires an explicit R3-B conversion receipt")
        if schema == FEATURE_CYCLE_SCHEMA and not state.feature_reopen_receipts:
            raise DecodeError("schema 19 requires explicit Feature reopening authority")
        has_request_objective = (
            any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.slice_criteria_revisions)
            or any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.slice_outcome_receipts)
            or any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.feature_completion_receipts)
        )
        if schema < REQUEST_OBJECTIVE_SCHEMA and has_request_objective:
            raise DecodeError("request-objective authority requires schema 20")
        if schema in (REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA):
            lane = _request_objective_schema_for_state(state)
            if (
                lane != schema
                or not any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.slice_criteria_revisions)
                or not any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.slice_outcome_receipts)
            ):
                raise DecodeError(f"schema {schema} requires its canonical request-objective proof")
        elif schema in (PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA) and has_request_objective:
            if _request_objective_schema_for_state(state) is None:
                raise DecodeError("schema 22 cannot weaken existing request-objective proof")
        validate_state(state)
        if schema in (PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA):
            _validate_provisional_slice_authority(root, state, schema=schema)
        _validate_capability_selection_authority(state, schema)
        contexts = tuple(
            context
            for context in (state.active_context, state.preserved_primary_context)
            if context is not None
        ) + tuple(archive.context for archive in state.terminal_archives)
        for publication in state.publication_history + tuple(
            context.publication
            for context in contexts
            if context.publication is not None
        ):
            if not _artifact_path(root, publication.path.value).is_file():
                raise DecodeError("authoritative publication is missing")
            if publication.previous_revision is not None and not _artifact_path(
                root,
                f"living-specs/{publication.living_spec_id}/{publication.previous_revision}.md",
            ).is_file():
                raise DecodeError("publication predecessor is missing")
    except (OSError, UnicodeDecodeError, DecodeError, DomainError, StateValidationError) as error:
        raise RecoveryError("state authority cannot be recovered") from error
    return state, schema


def _recover_state(root: Path) -> State:
    """Keep existing callers focused on domain state, without schema metadata."""
    state, _ = _recover_state_with_schema(root)
    return state


def _selection_id(operation: AssessmentOperation, intent: str, candidates: tuple[CapabilityCandidate, ...]) -> CapabilitySelectionId:
    values = ":".join(str(item.feature_id) for item in candidates)
    return CapabilitySelectionId(f"selection-{sha256(f'{operation}:{intent}:{values}'.encode()).hexdigest()[:12]}")


def _selection_candidates(state: State, intent: str) -> tuple[CapabilityCandidate, ...]:
    return tuple(
        CapabilityCandidate(feature.id, feature.title)
        for feature in state.features
        if DiscoveryPolicy.matches_capability(feature.title, intent)
    )


def _validate_capability_selection_authority(state: State, schema: int) -> None:
    if schema < 10:
        return
    selection = state.pending_capability_selection
    if selection is not None:
        candidates = _selection_candidates(state, selection.intent)
        if (
            selection.candidates != candidates
            or selection.id != _selection_id(selection.operation, selection.intent, candidates)
            or selection.provenance != tuple(f"feature:{item.feature_id}" for item in candidates)
        ):
            raise StateValidationError("pending capability selection is not Core-issued")


def _archive_to_json(archive: TerminalArchive, schema: int = SCHEMA) -> JsonValue:
    return {
        "context": _context_to_json(archive.context, schema),
        "parent_feature_id": archive.parent_feature_id,
        "convergence_assessment": _convergence_assessment_to_json(archive.convergence_assessment),
        "legacy_unassessed_acceptance": archive.legacy_unassessed_acceptance,
    }


def _parked_work_to_json(parked: ParkedWork | None) -> JsonValue:
    if parked is None:
        return None
    return {
        "context": _context_to_json(parked.context),
        "preserved_primary_context": _context_to_json(parked.preserved_primary_context),
        "deferred_side_request": _deferred_side_request_to_json(parked.deferred_side_request),
        "pending_intake": _pending_intake_to_json(parked.pending_intake),
        "reason": parked.reason,
        "parked_at": parked.parked_at,
    }


def _parked_work_from_json(value: JsonValue, schema: int) -> ParkedWork | None:
    if value is None:
        return None
    raw = _object(value, frozenset(("context", "preserved_primary_context", "deferred_side_request", "pending_intake", "reason", "parked_at")), "parked work")
    context = _context_from_json(raw["context"], schema)
    if context is None:
        raise DecodeError("parked work context cannot be null")
    return ParkedWork(
        context,
        _context_from_json(raw["preserved_primary_context"], schema),
        _deferred_side_request_from_json(raw["deferred_side_request"]),
        _pending_intake_from_json(raw["pending_intake"]),
        _string(raw["reason"], "parked work reason"),
        _string(raw["parked_at"], "parked work timestamp"),
    )


def _legacy_work_item(parked: ParkedWork) -> WorkItem:
    return WorkItem(
        _work_id(parked.context),
        _legacy_operational_state(parked.reason),
        parked.context,
        parked.preserved_primary_context,
        parked.deferred_side_request,
        parked.pending_intake,
        parked.reason,
        parked.parked_at,
    )


def _work_item_to_json(item: WorkItem, schema: int = SCHEMA) -> JsonValue:
    return {
        "id": item.id,
        "operational_state": item.operational_state,
        "context": _context_to_json(item.context, schema),
        "preserved_primary_context": _context_to_json(item.preserved_primary_context, schema),
        "deferred_side_request": _deferred_side_request_to_json(item.deferred_side_request),
        "pending_intake": _pending_intake_to_json(item.pending_intake),
        "reason": item.reason,
        "transitioned_at": item.transitioned_at,
        "terminal_change_id": item.terminal_change_id,
    }


def _work_item_from_json(value: JsonValue, schema: int) -> WorkItem:
    raw = _object(value, frozenset((
        "id", "operational_state", "context", "preserved_primary_context",
        "deferred_side_request", "pending_intake", "reason", "transitioned_at",
        "terminal_change_id",
    )), "work item")
    terminal_change_id = _optional_string(raw["terminal_change_id"], "work item terminal change")
    try:
        operational_state = OperationalState(
            _string(raw["operational_state"], "work item operational state")
        )
    except ValueError as error:
        raise DecodeError("invalid work item operational state") from error
    return WorkItem(
        WorkId(_string(raw["id"], "work item id")),
        operational_state,
        _context_from_json(raw["context"], schema),
        _context_from_json(raw["preserved_primary_context"], schema),
        _deferred_side_request_from_json(raw["deferred_side_request"]),
        _pending_intake_from_json(raw["pending_intake"]),
        _optional_string(raw["reason"], "work item reason"),
        _string(raw["transitioned_at"], "work item transition timestamp"),
        None if terminal_change_id is None else ChangeId(terminal_change_id),
    )


def _work_relation_to_json(relation: WorkRelation) -> JsonValue:
    return {
        "predecessor_id": relation.predecessor_id,
        "successor_id": relation.successor_id,
        "kind": relation.kind,
    }


def _work_relation_from_json(value: JsonValue) -> WorkRelation:
    raw = _object(value, frozenset(("predecessor_id", "successor_id", "kind")), "work relation")
    try:
        kind = WorkRelationKind(_string(raw["kind"], "work relation kind"))
    except ValueError as error:
        raise DecodeError("invalid work relation kind") from error
    return WorkRelation(
        WorkId(_string(raw["predecessor_id"], "work relation predecessor")),
        WorkId(_string(raw["successor_id"], "work relation successor")),
        kind,
    )


def _extensions_to_json(state: State) -> dict[str, JsonValue]:
    initialization: JsonValue = None
    if state.initialization is not None:
        initialization = {
            "repository_id": state.initialization.repository_id,
            "accepted_constitution": None if state.initialization.accepted_constitution is None else str(state.initialization.accepted_constitution),
            "evidence": [
                {"path": str(item.path), "kind": item.kind, "strength": item.strength, "fingerprint": item.fingerprint, "source_relationship": item.source_relationship}
                for item in state.initialization.evidence
            ],
        }
    deferred: JsonValue = None
    deferred = _deferred_side_request_to_json(state.deferred_side_request)
    intake: JsonValue = None
    intake = _pending_intake_to_json(state.pending_intake)
    selection: JsonValue = None if state.pending_capability_selection is None else {
        "id": state.pending_capability_selection.id,
        "operation": state.pending_capability_selection.operation,
        "intent": state.pending_capability_selection.intent,
        "candidates": [
            {"feature_id": item.feature_id, "title": item.title}
            for item in state.pending_capability_selection.candidates
        ],
        "standalone_allowed": state.pending_capability_selection.standalone_allowed,
        "provenance": list(state.pending_capability_selection.provenance),
    }
    recovery: JsonValue = None if state.pending_discovery_recovery is None else {
        "id": state.pending_discovery_recovery.id,
        "operation": state.pending_discovery_recovery.operation,
        "intent": state.pending_discovery_recovery.intent,
        "candidates": list(state.pending_discovery_recovery.candidates),
        "evidence": [
            {"id": item.id, "candidate": item.candidate, "path": str(item.path), "fingerprint": item.fingerprint}
            for item in state.pending_discovery_recovery.evidence
        ],
    }
    autonomy_expansion: JsonValue = _autonomy_expansion_proposal_to_json(
        state.pending_autonomy_expansion
    )
    return {
        "initialization": initialization,
        "deferred_side_request": deferred,
        "pending_intake": intake,
        "pending_capability_selection": selection,
        "capability_selection_history": [
            {"id": item.id, "choice": item.choice, "intent": item.intent, "provenance": list(item.provenance), "feature_id": item.feature_id, "change_id": item.change_id}
            for item in state.capability_selection_history
        ],
        "pending_discovery_recovery": recovery,
        "shared_intents": [{"id": item.id, "intent": item.intent} for item in state.shared_intents],
        "deferred_linked_intents": [{"id": item.id, "shared_intent_id": item.shared_intent_id, "capability_target": item.capability_target, "intent": item.intent} for item in state.deferred_linked_intents],
        "irreversible_authorizations": [
            _irreversible_authorization_to_json(item)
            for item in state.irreversible_authorizations
        ],
        "pending_autonomy_expansion": autonomy_expansion,
        "autonomy_expansion_history": [
            _autonomy_expansion_record_to_json(item)
            for item in state.autonomy_expansion_history
        ],
    }


def _autonomy_delta_to_json(delta: AutonomyEnvelopeDelta) -> JsonValue:
    return {
        "allowed_paths": None if delta.allowed_paths is None else [str(path) for path in delta.allowed_paths],
        "operation_budget": delta.operation_budget,
        "reversible_actions": None if delta.reversible_actions is None else list(delta.reversible_actions),
        "external_wait_allowed": delta.external_wait_allowed,
        "retry_budget": delta.retry_budget,
        "irreversible_permissions": None if delta.irreversible_permissions is None else list(delta.irreversible_permissions),
        "stop_conditions": None if delta.stop_conditions is None else list(delta.stop_conditions),
    }


def _autonomy_delta_from_json(value: JsonValue) -> AutonomyEnvelopeDelta:
    raw = _object(value, frozenset((
        "allowed_paths", "operation_budget", "reversible_actions", "external_wait_allowed",
        "retry_budget", "irreversible_permissions", "stop_conditions",
    )), "autonomy envelope delta")
    paths_raw = raw["allowed_paths"]
    actions_raw = raw["reversible_actions"]
    irreversible_raw = raw["irreversible_permissions"]
    stops_raw = raw["stop_conditions"]
    external_wait_raw = raw["external_wait_allowed"]
    operation_raw = raw["operation_budget"]
    retry_raw = raw["retry_budget"]
    return AutonomyEnvelopeDelta(
        None if paths_raw is None else tuple(
            RepositoryPath(item) for item in _strings(paths_raw, "autonomy delta paths")
        ),
        None if operation_raw is None else _integer(operation_raw, "autonomy delta operation budget"),
        None if actions_raw is None else _strings(actions_raw, "autonomy delta reversible actions"),
        None if external_wait_raw is None else _boolean(external_wait_raw, "autonomy delta external wait"),
        None if retry_raw is None else _integer(retry_raw, "autonomy delta retry budget"),
        None if irreversible_raw is None else _strings(irreversible_raw, "autonomy delta irreversible permissions"),
        None if stops_raw is None else _strings(stops_raw, "autonomy delta stop conditions"),
    )


def _autonomy_expansion_proposal_to_json(proposal: PendingAutonomyExpansion | None) -> JsonValue:
    if proposal is None:
        return None
    return {
        "proposal_id": proposal.proposal_id,
        "repository_id": proposal.repository_id,
        "feature_id": proposal.feature_id,
        "change_id": proposal.change_id,
        "requested_revision": proposal.requested_revision,
        "base_envelope": _autonomy_envelope_to_json(proposal.base_envelope),
        "delta": _autonomy_delta_to_json(proposal.delta),
        "proposed_envelope": _autonomy_envelope_to_json(proposal.proposed_envelope),
    }


def _autonomy_expansion_proposal_from_json(value: JsonValue) -> PendingAutonomyExpansion | None:
    if value is None:
        return None
    raw = _object(value, frozenset((
        "proposal_id", "repository_id", "feature_id", "change_id", "requested_revision", "base_envelope", "delta", "proposed_envelope",
    )), "pending autonomy expansion")
    return PendingAutonomyExpansion(
        AutonomyExpansionId(_string(raw["proposal_id"], "autonomy proposal id")),
        _string(raw["repository_id"], "autonomy proposal repository"),
        FeatureId(_string(raw["feature_id"], "autonomy proposal feature")),
        ChangeId(_string(raw["change_id"], "autonomy proposal change")),
        _integer(raw["requested_revision"], "autonomy proposal request revision"),
        _autonomy_envelope(raw["base_envelope"]),
        _autonomy_delta_from_json(raw["delta"]),
        _autonomy_envelope(raw["proposed_envelope"]),
    )


def _autonomy_expansion_record_to_json(record: AutonomyExpansionRecord) -> JsonValue:
    return {
        "proposal_id": record.proposal_id,
        "repository_id": record.repository_id,
        "feature_id": record.feature_id,
        "change_id": record.change_id,
        "requested_revision": record.requested_revision,
        "resolved_revision": record.resolved_revision,
        "decision": record.decision,
        "base_envelope": _autonomy_envelope_to_json(record.base_envelope),
        "delta": _autonomy_delta_to_json(record.delta),
        "proposed_envelope": _autonomy_envelope_to_json(record.proposed_envelope),
        "resolved_envelope": _autonomy_envelope_to_json(record.resolved_envelope),
    }


def _autonomy_expansion_record_from_json(value: JsonValue) -> AutonomyExpansionRecord:
    raw = _object(value, frozenset((
        "proposal_id", "repository_id", "feature_id", "change_id", "requested_revision", "resolved_revision", "decision",
        "base_envelope", "delta", "proposed_envelope", "resolved_envelope",
    )), "autonomy expansion audit")
    try:
        decision = AutonomyExpansionDecision(_string(raw["decision"], "autonomy expansion decision"))
    except ValueError as error:
        raise DecodeError("invalid autonomy expansion decision") from error
    return AutonomyExpansionRecord(
        AutonomyExpansionId(_string(raw["proposal_id"], "autonomy audit proposal id")),
        _string(raw["repository_id"], "autonomy audit repository"),
        FeatureId(_string(raw["feature_id"], "autonomy audit feature")),
        ChangeId(_string(raw["change_id"], "autonomy audit change")),
        _integer(raw["requested_revision"], "autonomy audit request revision"),
        _integer(raw["resolved_revision"], "autonomy audit resolved revision"),
        decision,
        _autonomy_envelope(raw["base_envelope"]),
        _autonomy_delta_from_json(raw["delta"]),
        _autonomy_envelope(raw["proposed_envelope"]),
        _autonomy_envelope(raw["resolved_envelope"]),
    )


def _extensions_from_json(raw: dict[str, JsonValue], schema: int) -> tuple[RepositoryInitialization | None, DeferredSideRequest | None, PendingIntake | None, PendingCapabilitySelection | None, PendingDiscoveryRecovery | None, tuple[CapabilitySelection, ...], tuple[SharedIntent, ...], tuple[DeferredLinkedIntent, ...], tuple[IrreversibleAuthorization, ...], PendingAutonomyExpansion | None, tuple[AutonomyExpansionRecord, ...]]:
    initialization: RepositoryInitialization | None = None
    if raw["initialization"] is not None:
        item = _object(raw["initialization"], frozenset(("repository_id", "accepted_constitution", "evidence")), "initialization")
        evidence = tuple(
            DiscoveryEvidence(
                RepositoryPath(_string(entry["path"], "evidence path")),
                _string(entry["kind"], "evidence kind"),
                _string(entry["strength"], "evidence strength"),
                _string(entry["fingerprint"], "evidence fingerprint"),
                _string(entry["source_relationship"], "evidence relationship"),
            )
            for value in _array(item["evidence"], "evidence")
            for entry in [_object(value, frozenset(("path", "kind", "strength", "fingerprint", "source_relationship")), "evidence")]
        )
        constitution = _optional_string(item["accepted_constitution"], "accepted constitution")
        initialization = RepositoryInitialization(_string(item["repository_id"], "repository id"), evidence, None if constitution is None else RepositoryPath(constitution))
    deferred: DeferredSideRequest | None = None
    deferred = _deferred_side_request_from_json(raw["deferred_side_request"])
    intake: PendingIntake | None = None
    intake = _pending_intake_from_json(raw["pending_intake"])
    selection: PendingCapabilitySelection | None = None
    recovery: PendingDiscoveryRecovery | None = None
    if schema >= 10 and raw["pending_capability_selection"] is not None:
        item = _object(raw["pending_capability_selection"], frozenset(("id", "operation", "intent", "candidates", "standalone_allowed", "provenance")), "pending capability selection")
        try:
            operation = AssessmentOperation(_string(item["operation"], "capability selection operation"))
        except ValueError as error:
            raise DecodeError("invalid capability selection operation") from error
        standalone_allowed = item["standalone_allowed"]
        if type(standalone_allowed) is not bool:
            raise DecodeError("capability selection standalone flag is invalid")
        selection = PendingCapabilitySelection(
            CapabilitySelectionId(_string(item["id"], "capability selection id")),
            operation,
            _string(item["intent"], "capability selection intent"),
            tuple(
                CapabilityCandidate(FeatureId(_string(entry["feature_id"], "capability selection feature")), _string(entry["title"], "capability selection title"))
                for value in _array(item["candidates"], "capability selection candidates")
                for entry in [_object(value, frozenset(("feature_id", "title")), "capability selection candidate")]
            ),
            standalone_allowed,
            _strings(item["provenance"], "capability selection provenance"),
        )
    history = () if schema < 10 else tuple(
        CapabilitySelection(
            CapabilitySelectionId(_string(item["id"], "capability selection history id")),
            _string(item["choice"], "capability selection history choice"),
            _string(item["intent"], "capability selection history intent"),
            _strings(item["provenance"], "capability selection history provenance"),
            FeatureId(_string(item["feature_id"], "capability selection history feature")),
            ChangeId(_string(item["change_id"], "capability selection history change")),
        )
        for value in _array(raw["capability_selection_history"], "capability selection history")
        for item in [_object(value, frozenset(("id", "choice", "intent", "provenance", "feature_id", "change_id")), "capability selection history")]
    )
    if schema >= 10 and raw["pending_discovery_recovery"] is not None:
        item = _object(raw["pending_discovery_recovery"], frozenset(("id", "operation", "intent", "candidates", "evidence")), "pending discovery recovery")
        try:
            operation = AssessmentOperation(_string(item["operation"], "discovery recovery operation"))
        except ValueError as error:
            raise DecodeError("invalid discovery recovery operation") from error
        recovery = PendingDiscoveryRecovery(
            _string(item["id"], "discovery recovery id"),
            operation,
            _string(item["intent"], "discovery recovery intent"),
            _strings(item["candidates"], "discovery recovery candidates"),
            tuple(
                DiscoveryRecoveryEvidence(
                    _string(entry["id"], "discovery recovery evidence id"),
                    _string(entry["candidate"], "discovery recovery evidence candidate"),
                    RepositoryPath(_string(entry["path"], "discovery recovery evidence path")),
                    _string(entry["fingerprint"], "discovery recovery evidence fingerprint"),
                )
                for value in _array(item["evidence"], "discovery recovery evidence")
                for entry in [_object(value, frozenset(("id", "candidate", "path", "fingerprint")), "discovery recovery evidence")]
            ),
        )
    shared = tuple(SharedIntent(SharedIntentId(_string(item["id"], "shared intent id")), _string(item["intent"], "shared intent")) for value in _array(raw["shared_intents"], "shared intents") for item in [_object(value, frozenset(("id", "intent")), "shared intent")])
    linked = tuple(DeferredLinkedIntent(IntakeId(_string(item["id"], "linked id")), SharedIntentId(_string(item["shared_intent_id"], "linked shared intent")), _string(item["capability_target"], "linked target"), _string(item["intent"], "linked intent")) for value in _array(raw["deferred_linked_intents"], "linked intents") for item in [_object(value, frozenset(("id", "shared_intent_id", "capability_target", "intent")), "linked intent")])
    authorizations = () if schema < 11 else tuple(
        _irreversible_authorization_from_json(value)
        for value in _array(raw["irreversible_authorizations"], "irreversible authorizations")
    )
    pending_autonomy_expansion = None
    expansion_history: tuple[AutonomyExpansionRecord, ...] = ()
    if schema >= 12:
        pending_autonomy_expansion = _autonomy_expansion_proposal_from_json(
            raw["pending_autonomy_expansion"]
        )
        expansion_history = tuple(
            _autonomy_expansion_record_from_json(value)
            for value in _array(raw["autonomy_expansion_history"], "autonomy expansion history")
        )
    return (
        initialization, deferred, intake, selection, recovery, history, shared, linked,
        authorizations, pending_autonomy_expansion, expansion_history,
    )


def _deferred_side_request_to_json(item: DeferredSideRequest | None) -> JsonValue:
    if item is None:
        return None
    return {"id": item.id, "intent": item.intent, "primary_feature_id": item.primary_feature_id, "reason": item.reason, "target_hint": item.target_hint}


def _deferred_side_request_from_json(value: JsonValue) -> DeferredSideRequest | None:
    if value is None:
        return None
    item = _object(value, frozenset(("id", "intent", "primary_feature_id", "reason", "target_hint")), "deferred side request")
    return DeferredSideRequest(IntakeId(_string(item["id"], "deferred id")), _string(item["intent"], "deferred intent"), FeatureId(_string(item["primary_feature_id"], "deferred primary")), _string(item["reason"], "deferred reason"), _optional_string(item["target_hint"], "deferred target"))


def _pending_intake_to_json(item: PendingIntake | None) -> JsonValue:
    if item is None:
        return None
    return {"id": item.id, "intent": item.intent, "deferred_from_feature_id": item.deferred_from_feature_id, "completed_side_change_id": item.completed_side_change_id, "target_hint": item.target_hint}


def _pending_intake_from_json(value: JsonValue) -> PendingIntake | None:
    if value is None:
        return None
    item = _object(value, frozenset(("id", "intent", "deferred_from_feature_id", "completed_side_change_id", "target_hint")), "pending intake")
    completed = _optional_string(item["completed_side_change_id"], "completed side change")
    return PendingIntake(IntakeId(_string(item["id"], "intake id")), _string(item["intent"], "intake intent"), FeatureId(_string(item["deferred_from_feature_id"], "intake feature")), None if completed is None else ChangeId(completed), _optional_string(item["target_hint"], "intake target"))


def _request_objective_schema_for_state(state: State) -> int | None:
    """Identify the static or semantic lane from the immutable session agreement."""
    features = tuple(item for item in state.agreements if item.kind == "FEATURE")
    slices = tuple(item for item in state.agreements if item.kind == "SLICE")
    if (
        len(state.agreements) != 2
        or len(features) != 1
        or len(slices) != 1
        or features[0].source != "SESSION_REQUEST"
        or slices[0].source != "SESSION_REQUEST"
        or features[0].statement != slices[0].statement
        or features[0].source_reference != slices[0].source_reference
        or features[0].feature_id != slices[0].feature_id
        or features[0].change_id != slices[0].change_id
        or state.initialization is None
        or slices[0].repository_id != state.initialization.repository_id
    ):
        return None
    expected = request_objective_criteria_revision(
        slices[0].id, slices[0].repository_id, slices[0].source_reference,
        slices[0].statement,
    )
    if expected is None:
        return None
    if state.slice_criteria_revisions and state.slice_criteria_revisions != (expected,):
        return None
    if state.slice_criteria_heads and state.slice_criteria_heads != (
        SliceCriteriaHead(slices[0].id, expected.id),
    ):
        return None
    if bool(state.slice_criteria_revisions) != bool(state.slice_criteria_heads):
        return None
    return (
        REQUEST_OBJECTIVE_SCHEMA
        if expected.criteria[0].id == "request-objective-heading"
        else SEMANTIC_REQUEST_OBJECTIVE_SCHEMA
    )


def _state_bytes(state: State, schema: int = SCHEMA) -> bytes:
    """Preserve each accepted layout until a scoped authority decision changes it."""
    if schema not in (LEGACY_WRITE_SCHEMA, E1_TASK_PROGRESS_SCHEMA, SCHEMA, R3_SCHEMA, R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA, PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA):
        raise ValueError("unsupported state writer schema")
    if schema < SCHEMA and (state.slice_criteria_revisions or state.slice_criteria_heads):
        raise ValueError("Slice criteria cannot be silently downgraded")
    if schema < D4_PROVISIONAL_SLICE_SCHEMA and state.provisional_slice_activation is not None:
        raise ValueError("D4 provisional Slice activation cannot be silently downgraded")
    if schema >= D4_PROVISIONAL_SLICE_SCHEMA and state.provisional_slice_activation is None:
        raise ValueError("schema 23 requires the D4 provisional Slice activation marker")
    if schema < D5_PROVISIONAL_SLICE_SCHEMA and state.provisional_slice_adoptions:
        raise ValueError("D5 provisional Slice adoption cannot be silently downgraded")
    if schema == D5_PROVISIONAL_SLICE_SCHEMA and len(state.provisional_slice_adoptions) != 1:
        raise ValueError("schema 24 requires exactly one D5 provisional Slice adoption")
    if schema < PROVISIONAL_SLICE_SCHEMA and (
        state.provisional_slice_revisions or state.provisional_slice_heads
    ):
        raise ValueError("provisional Slice authority cannot be silently downgraded")
    if (
        schema >= R3B_SCHEMA
        and schema not in (
            REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
            PROVISIONAL_SLICE_SCHEMA, D4_PROVISIONAL_SLICE_SCHEMA,
            D5_PROVISIONAL_SLICE_SCHEMA,
        )
        and state.r3b_schema_upgrade is None
    ):
        raise ValueError("schema 17 requires its separate human-approved conversion receipt")
    if schema == FEATURE_CYCLE_SCHEMA and not state.feature_reopen_receipts:
        raise ValueError("schema 19 requires a human reopening receipt")
    if schema >= PROVISIONAL_SLICE_SCHEMA:
        value_d1: dict[str, JsonValue] = {
            "provisional_slice_refs": [
                {
                    "id": item.revision_id,
                    "path": f"records/provisional-slices/{item.revision_id}.json",
                }
                for item in sorted(state.provisional_slice_revisions, key=lambda item: item.revision_id)
            ],
            "provisional_slice_heads": [
                {
                    "proposal_id": item.proposal_id,
                    "revision_id": item.revision_id,
                    "revision": item.revision,
                    "digest": item.digest,
                }
                for item in sorted(state.provisional_slice_heads, key=lambda item: item.proposal_id)
            ],
        }
        if schema >= D4_PROVISIONAL_SLICE_SCHEMA:
            value_d1["provisional_slice_activation"] = _provisional_slice_activation_to_json(
                state.provisional_slice_activation,
            )
        if schema >= D5_PROVISIONAL_SLICE_SCHEMA:
            value_d1["provisional_slice_adoption_refs"] = [
                {
                    "id": item.id,
                    "path": f"records/provisional-slice-adoptions/{item.id}.json",
                }
                for item in state.provisional_slice_adoptions
            ]
    else:
        value_d1 = {}
    if schema in (REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA) and (
        _request_objective_schema_for_state(state) != schema
        or not any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.slice_criteria_revisions)
        or not any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.slice_outcome_receipts)
    ):
        raise ValueError(f"schema {schema} requires its canonical request-objective proof")
    if schema < REQUEST_OBJECTIVE_SCHEMA and (
        any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.slice_criteria_revisions)
        or any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.slice_outcome_receipts)
        or any(item.source == REQUEST_SCOPED_OBJECTIVE for item in state.feature_completion_receipts)
    ):
        raise ValueError("request-objective authority cannot be downgraded from schema 20")
    value: dict[str, JsonValue] = {
            "schema": schema,
            "revision": state.revision,
            "active_context": _context_to_json(state.active_context, schema),
            "active_work_id": state.active_work_id,
            "preserved_primary_work_id": state.preserved_primary_work_id,
            "preserved_primary_context": _context_to_json(state.preserved_primary_context, schema),
            "publications": [_publication_to_json(item) for item in state.publications],
            "completed_side_contexts": [_archive_to_json(item, schema) for item in state.completed_side_contexts],
            "features": [_feature_to_json(item) for item in state.features],
            "change_refs": [
                {"id": item.id, "path": f"records/changes/{item.id}.json"}
                for item in state.changes
            ],
            "agreement_refs": [
                {"id": item.id, "path": f"records/agreements/{item.id}.json"}
                for item in state.agreements
            ],
            "slice_criteria_refs": [
                {"id": item.id, "path": f"records/slice-criteria/{item.id}.json"}
                for item in state.slice_criteria_revisions
            ],
            "slice_criteria_heads": [
                {"slice_agreement_ref": item.slice_agreement_ref, "revision_id": item.revision_id}
                for item in state.slice_criteria_heads
            ],
            "decision_refs": [
                {"id": item.id, "path": f"records/decisions/{item.id}.json"}
                for item in state.decisions
            ],
            "observation_refs": [
                {"id": item.id, "path": f"records/observations/{item.id}.json"}
                for item in state.observations
            ],
            "drift_refs": [
                {"id": item.id, "path": f"records/observations/{item.id}.json"}
                for item in state.drifts
            ],
            "terminal_archives": [_archive_to_json(item, schema) for item in state.terminal_archives],
            "publication_history": [
                _publication_to_json(item) for item in state.publication_history
            ],
            "locks": [_lock_to_json(item) for item in state.locks],
            "lock_revisions": [_lock_revision_to_json(item) for item in state.lock_revisions],
            "lock_releases": [_lock_release_to_json(item) for item in state.lock_releases],
            "lock_authorizations": [_lock_authorization_to_json(item) for item in state.lock_authorizations],
            "change_lock_assessments": [_change_lock_assessments_to_json(item) for item in state.change_lock_assessments],
            "work_items": [_work_item_to_json(item, schema) for item in state.work_items],
            "work_relations": [_work_relation_to_json(item) for item in state.work_relations],
    }
    value.update(_extensions_to_json(state))
    value.update(value_d1)
    if schema < SCHEMA:
        # Earlier schemas have no criterion ledger; an empty slot is not authority.
        value.pop("slice_criteria_refs")
        value.pop("slice_criteria_heads")
    if schema >= 14:
        value["e1_schema_upgrade"] = _e1_upgrade_to_json(state.e1_schema_upgrade)
    elif state.e1_schema_upgrade is not None:
        raise ValueError("human E1 schema upgrade receipt cannot be downgraded")
    if schema >= R3_SCHEMA:
        value["r3_schema_upgrade"] = _r3_upgrade_to_json(state.r3_schema_upgrade)
        value["criterion_test_receipts"] = [
            _criterion_test_to_json(item, schema) for item in state.criterion_test_receipts
        ]
    elif state.r3_schema_upgrade is not None or state.criterion_test_receipts:
        raise ValueError("R3 approval or criterion test evidence cannot be downgraded")
    if schema >= R3B_SCHEMA:
        value["r3b_schema_upgrade"] = _r3b_upgrade_to_json(state.r3b_schema_upgrade)
        value["slice_outcome_receipts"] = [
            _slice_outcome_to_json(item) for item in state.slice_outcome_receipts
        ]
        value["criterion_coverage_approvals"] = [
            _coverage_approval_to_json(item) for item in state.criterion_coverage_approvals
        ]
    elif state.r3b_schema_upgrade is not None or state.slice_outcome_receipts or state.criterion_coverage_approvals:
        raise ValueError("R3-B approval or Slice outcome evidence cannot be downgraded")
    if schema >= FEATURE_COMPLETION_SCHEMA:
        value["feature_completion_receipts"] = [
            _feature_completion_to_json(item) for item in state.feature_completion_receipts
        ]
    elif state.feature_completion_receipts:
        raise ValueError("Feature completion authority cannot be silently downgraded")
    if schema >= FEATURE_CYCLE_SCHEMA:
        value["feature_reopen_receipts"] = [
            _feature_reopen_to_json(item) for item in state.feature_reopen_receipts
        ]
    elif state.feature_reopen_receipts:
        raise ValueError("Feature reopening authority cannot be silently downgraded")
    return encode_json(value)


@contextmanager
def _state_publish_lock(root: Path) -> Iterator[None]:
    """Serialize state compare-and-publish across threads and Core processes."""
    key = str(root)
    with _STATE_LOCKS_GUARD:
        process_lock = _STATE_LOCKS.setdefault(key, threading.RLock())
    with process_lock:
        lock_directory = Path(tempfile.gettempdir()) / "evospec-state-locks"
        lock_directory.mkdir(parents=True, exist_ok=True)
        lock_path = lock_directory / f"{sha256(key.encode('utf-8')).hexdigest()}.lock"
        with lock_path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _reject_composed_continuity_mode(root: Path) -> None:
    """Legacy readers fail closed when the shared-lock opt-in marker exists."""
    marker = _artifact_path(root, "continuity.json")
    if marker.exists() or marker.is_symlink():
        raise RecoveryError("COMPOSED_CONTINUITY_MODE_ACTIVE: legacy authority is disabled")


class StateStore:
    """Read-only authoritative state recovery."""

    __slots__ = ("__root",)

    def __init__(self, root: Path) -> None:
        self.__root = _resolved_root(root)

    def load_state(self) -> State:
        _reject_composed_continuity_mode(self.__root)
        return _recover_state(self.__root)



def _atomic_replace(
    path: Path, data: bytes, operations: FileOperations, *, strict_directory: bool = False,
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    except OSError as error:
        raise AtomicFailure("atomic temporary setup failed") from error
    temporary = Path(name)
    try:
        handle = os.fdopen(descriptor, "wb")
    except OSError as error:
        try:
            os.close(descriptor)
        except OSError as cleanup_error:
            del cleanup_error
        try:
            operations.unlink(temporary)
        except OSError as cleanup_error:
            del cleanup_error
        raise AtomicFailure("atomic temporary open failed") from error
    try:
        if operations.write(handle, data) != len(data):
            raise OSError("incomplete atomic write")
        operations.flush(handle)
        operations.fsync_file(handle.fileno())
        operations.close(handle)
        operations.replace(temporary, path)
    except OSError as error:
        try:
            handle.close()
        except OSError as cleanup_error:
            del cleanup_error
        try:
            operations.unlink(temporary)
        except OSError as cleanup_error:
            del cleanup_error
        raise AtomicFailure("atomic publication failed before replacement") from error
    try:
        operations.fsync_directory(path.parent)
    except OSError as error:
        # The rename may already be visible. Report uncertainty to the E1 caller;
        # its next step must be recovery, never an assumed automatic retry.
        if strict_directory:
            raise AtomicFailure("publication durability is uncertain after replacement") from error


def _encode_recoverable_continuity(
    state: ContinuityStateData, description: str,
) -> bytes:
    encoded = encode_json(cast(JsonValue, cast(object, state)))
    if len(encoded) > _CONTINUITY_RECOVERY_MAX_BYTES:
        raise ContinuityError(
            "LIMIT_EXCEEDED",
            f"serialized continuity {description} exceeds its 4 MiB recovery bound",
        )
    return encoded


class ContinuityPersistence:
    """Shared-lock state and recoverable Markdown publication for composed work."""

    __slots__ = ("__root", "__operations")

    def __init__(self, root: Path, operations: FileOperations | None = None) -> None:
        self.__root = _resolved_root(root)
        self.__operations = FileOperations() if operations is None else operations

    @property
    def root(self) -> Path:
        return self.__root

    @property
    def repository_id(self) -> str:
        return sha256(str(self.__root).encode("utf-8")).hexdigest()[:16]

    def transaction(self):
        return _state_publish_lock(self.__root)

    def assert_legacy_absent(self) -> None:
        path = _artifact_path(self.__root, "state.json")
        if path.exists() or path.is_symlink():
            raise ContinuityError(
                "MIXED_AUTHORITY",
                "legacy state.json and composed continuity cannot coexist; no migration or automatic mode switch is supported",
            )

    def load(self) -> ContinuityStateData | None:
        marker = _artifact_path(self.__root, "continuity.json")
        self.__safe_document_path(".evospec/continuity.json")
        self.assert_legacy_absent()
        if not marker.exists():
            return None
        try:
            with marker.open("rb") as handle:
                encoded = handle.read(_CONTINUITY_RECOVERY_MAX_BYTES + 1)
            if len(encoded) > _CONTINUITY_RECOVERY_MAX_BYTES:
                raise ContinuityError(
                    "CORRUPT_CONTINUITY", "continuity.json exceeds its recovery bound",
                )
            raw = decode_json(encoded.decode("utf-8"))
        except ContinuityError:
            raise
        except (OSError, UnicodeDecodeError, DecodeError, RecursionError) as error:
            raise ContinuityError(
                "CORRUPT_CONTINUITY", "continuity.json cannot be recovered",
            ) from error
        validate_continuity_state(raw, repository_id=self.repository_id)
        return cast(ContinuityStateData, cast(object, raw))

    def check_documents(self, state: ContinuityStateData) -> None:
        for relative, expected in state["documents"].items():
            actual = self.__document_hash(relative)
            if actual != expected:
                raise ContinuityError(
                    "DOCUMENT_DIVERGED",
                    f"{relative} changed outside Core; preserve it and reconcile manually",
                )

    def commit(
        self, current: ContinuityStateData, candidate: ContinuityStateData,
        writes: dict[str, tuple[bytes, str | None]],
        *, read_set: dict[str, str | None] | None = None,
    ) -> None:
        if candidate["pending"] is not None:
            raise ContinuityError("INVALID_STATE", "candidate state cannot contain a pending journal")
        next_state = deepcopy(candidate)
        documents = dict(candidate["documents"])
        pending_writes: list[ContinuityPendingWrite] = []
        observed: dict[str, str | None] = (
            dict(current["documents"]) if read_set is None else dict(read_set)
        )
        for relative, (content, before_hash) in sorted(writes.items()):
            self.__safe_document_path(relative)
            if type(content) is not bytes or len(content) > 512 * 1024:
                raise ContinuityError("LIMIT_EXCEEDED", f"{relative} exceeds the document size limit")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ContinuityError("INVALID_DOCUMENT", f"{relative} is not UTF-8") from error
            actual_hash = self.__document_hash(relative)
            if relative not in observed:
                observed[relative] = before_hash
            if actual_hash != observed[relative]:
                raise ContinuityError(
                    "DOCUMENT_DIVERGED",
                    f"{relative} changed after it was read; reread and reconcile before writing",
                )
            digest = sha256(content).hexdigest()
            if actual_hash != before_hash and actual_hash != digest:
                raise ContinuityError(
                    "DOCUMENT_DIVERGED",
                    f"{relative} changed; reconcile it manually before writing",
                )
            documents[relative] = digest
            pending_writes.append({
                "path": relative, "before": before_hash,
                "content": text, "sha256": digest,
            })
        for relative, expected in observed.items():
            actual = self.__document_hash(relative)
            intended = next(
                (item["sha256"] for item in pending_writes if item["path"] == relative),
                None,
            )
            if actual != expected and actual != intended:
                raise ContinuityError(
                    "DOCUMENT_DIVERGED",
                    f"{relative} changed after it was read; state was not published",
                )
        next_state["documents"] = documents
        next_state["revision"] = candidate["revision"]
        next_state["pending"] = None
        validate_continuity_state(
            next_state, repository_id=self.repository_id, pending_state=True,
        )
        _ = _encode_recoverable_continuity(next_state, "state")
        next_state_record: dict[str, object] = {}
        next_state_record.update(next_state)
        pending_record: ContinuityPending = {
            "next_state": next_state_record,
            "writes": pending_writes,
            "read_set": observed,
        }
        journal: ContinuityStateData = {
            **current,
            "pending": pending_record,
        }
        validate_continuity_state(journal, repository_id=self.repository_id)
        journal_bytes = _encode_recoverable_continuity(journal, "recovery journal")
        marker = _artifact_path(self.__root, "continuity.json")
        try:
            _atomic_replace(
                marker,
                journal_bytes,
                self.__operations,
                strict_directory=True,
            )
        except (AtomicFailure, RecoveryError) as error:
            raise ContinuityError(
                "PUBLICATION_UNCERTAIN",
                "continuity journal publication is uncertain; reread before retrying",
            ) from error
        self.recover_pending(journal)

    def recover_pending(self, state: ContinuityStateData) -> None:
        validate_continuity_state(state, repository_id=self.repository_id)
        _ = _encode_recoverable_continuity(state, "recovery journal")
        pending = state["pending"]
        if pending is None:
            raise ContinuityError("NO_RECOVERY", "there is no pending document transaction")
        self.assert_legacy_absent()
        next_state = pending["next_state"]
        validate_continuity_state(
            next_state, repository_id=self.repository_id, pending_state=True,
        )
        next_state = cast(ContinuityStateData, cast(object, next_state))
        final_state = deepcopy(next_state)
        final_state["pending"] = None
        validate_continuity_state(final_state, repository_id=self.repository_id)
        final_state_bytes = _encode_recoverable_continuity(final_state, "state")
        read_set = pending.get("read_set", {})
        if not isinstance(read_set, dict):
            raise ContinuityError("CORRUPT_CONTINUITY", "recovery read set is invalid")
        for relative, expected in read_set.items():
            actual = self.__document_hash(relative)
            intended = next(
                (
                    item["sha256"] for item in pending["writes"]
                    if item["path"] == relative
                ),
                None,
            )
            if actual != expected and actual != intended:
                raise ContinuityError(
                    "DOCUMENT_DIVERGED",
                    f"{relative} changed before document publication; manual reconciliation is required",
                )
        prepared_writes: list[tuple[str, Path, bytes]] = []
        for item in pending["writes"]:
            relative = item["path"]
            content = item["content"]
            expected = item["sha256"]
            before = item["before"]
            if not isinstance(content, str):
                raise ContinuityError("CORRUPT_CONTINUITY", "recovery journal text is invalid")
            try:
                data = content.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ContinuityError("CORRUPT_CONTINUITY", "journal text is not UTF-8") from error
            if sha256(data).hexdigest() != expected:
                raise ContinuityError("CORRUPT_CONTINUITY", "journal document digest does not match")
            destination = self.__safe_document_path(relative)
            actual = self.__document_hash(relative)
            if actual == expected:
                continue
            if actual != before:
                raise ContinuityError(
                    "DOCUMENT_DIVERGED",
                    f"{relative} no longer matches either journal version; manual reconciliation is required",
                )
            prepared_writes.append((relative, destination, data))
        for relative, destination, data in prepared_writes:
            actual = self.__document_hash(relative)
            item = next(
                (entry for entry in pending["writes"] if entry["path"] == relative),
                None,
            )
            if item is None:
                raise ContinuityError("CORRUPT_CONTINUITY", "recovery write disappeared")
            if actual == item["sha256"]:
                continue
            if actual != item["before"]:
                raise ContinuityError(
                    "DOCUMENT_DIVERGED",
                    f"{relative} changed immediately before replacement; manual reconciliation is required",
                )
            try:
                _atomic_replace(destination, data, self.__operations, strict_directory=True)
            except (AtomicFailure, RecoveryError) as error:
                raise ContinuityError(
                    "RECOVERY_INCOMPLETE",
                    f"publication for {relative} remains journaled; reconcile again after inspecting it",
                ) from error
        # Keep the final check: a noncooperating editor can race the preflight
        # while a multi-document journal is being applied.
        for relative, expected in read_set.items():
            actual = self.__document_hash(relative)
            intended = next(
                (
                    item["sha256"] for item in pending["writes"]
                    if item["path"] == relative
                ),
                None,
            )
            if actual != expected and actual != intended:
                raise ContinuityError(
                    "DOCUMENT_DIVERGED",
                    f"{relative} changed before state publication; manual reconciliation is required",
                )
        try:
            _atomic_replace(
                _artifact_path(self.__root, "continuity.json"),
                final_state_bytes,
                self.__operations,
                strict_directory=True,
            )
        except (AtomicFailure, RecoveryError) as error:
            raise ContinuityError(
                "PUBLICATION_UNCERTAIN",
                "documents are published but the journal may still be pending; reread before retrying",
            ) from error

    def __document_hash(self, relative: object) -> str | None:
        path = self.__safe_document_path(relative)
        if not path.exists():
            return None
        try:
            if path.is_symlink() or not path.is_file():
                raise ContinuityError("UNSAFE_PATH", f"{path.name} is not a regular file")
            return sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise ContinuityError("DOCUMENT_UNREADABLE", f"{path.name} cannot be read") from error

    def __safe_document_path(self, relative: object) -> Path:
        if type(relative) is not str or re.fullmatch(
            r"(?:\.evospec/continuity\.json|specs/README\.md|"
            r"specs/[0-9]{3,}-[a-z0-9]+(?:-[a-z0-9]+)*/"
            r"(?:spec|plan|tasks|decisions)\.md)",
            relative,
        ) is None:
            raise ContinuityError("UNSAFE_PATH", "continuity path is outside the managed document set")
        path = self.__root / Path(relative)
        current = self.__root
        try:
            for part in Path(relative).parts:
                current = current / part
                if current.is_symlink():
                    raise ContinuityError("UNSAFE_PATH", "continuity refuses symbolic-link targets")
            if not path.resolve().is_relative_to(self.__root):
                raise ContinuityError("UNSAFE_PATH", "continuity path escapes the repository")
        except OSError as error:
            raise ContinuityError("UNSAFE_PATH", "continuity path cannot be resolved") from error
        return path


def load_integration_providers(root: Path) -> tuple[str, ...]:
    """Recover tool-owned provider registrations without loading lifecycle authority."""
    path = integration_registry_path(root)
    if not path.exists():
        return ()
    try:
        raw = _object(
            _read_json(path),
            frozenset(("schema", "providers")),
            "integration registry",
        )
        if _integer(raw["schema"], "integration registry schema") != INTEGRATION_REGISTRY_SCHEMA:
            raise DecodeError("unknown integration registry schema")
        providers = _strings(raw["providers"], "integration providers")
        if any(not provider for provider in providers) or len(providers) != len(set(providers)):
            raise DecodeError("integration providers must be unique nonempty strings")
        return providers
    except (OSError, UnicodeDecodeError, DecodeError) as error:
        raise IntegrationRegistryError("integration registry cannot be recovered") from error


def save_integration_providers(root: Path, providers: set[str]) -> None:
    """Atomically save the exact set of registered provider names."""
    try:
        _atomic_replace(
            integration_registry_path(root),
            integration_registry_content(providers).encode("utf-8"),
            FileOperations(),
        )
    except AtomicFailure as error:
        raise IntegrationRegistryError("integration registry could not be saved") from error


def integration_registry_path(root: Path) -> Path:
    """Return the tool-owned registry artifact path under an accessible repository."""
    return _artifact_path(_resolved_root(root), "integrations.json")


def integration_registry_content(providers: set[str]) -> str:
    """Render canonical integration-registry JSON for a validated provider set."""
    if any(not provider for provider in providers):
        raise IntegrationRegistryError("integration providers must be nonempty strings")
    provider_values: list[JsonValue] = []
    for provider in sorted(providers):
        provider_values.append(provider)
    value: dict[str, JsonValue] = {
        "schema": INTEGRATION_REGISTRY_SCHEMA,
        "providers": provider_values,
    }
    return encode_json(value).decode("utf-8")


class _StateStore:
    """Private concrete persistence collaborator for CoreService operations."""

    __slots__: tuple[str, str] = ("__operations", "__root")

    def __init__(self, root: Path, operations: FileOperations | None = None) -> None:
        if operations is not None and not isinstance(operations, FileOperations):
            raise TypeError("operations must be supported")
        self.__root = _resolved_root(root)
        self.__operations = FileOperations() if operations is None else operations

    def recover(self) -> State:
        state, _, _ = self.recover_snapshot()
        return state

    def composed_continuity(
        self, operation: str, expected_revision: int | None,
        session_id: str, data: dict[str, object],
    ) -> dict[str, object]:
        """Route composed mutations through persistence-owned file operations."""
        execute_continuity = cast(
            Callable[
                [Path, str, int | None, str, dict[str, object], FileOperations],
                dict[str, object],
            ],
            getattr(import_module(".continuity", __package__), "execute"),
        )

        return execute_continuity(
            self.__root, operation, expected_revision, session_id, data,
            self.__operations,
        )

    def recover_snapshot(self) -> tuple[State, bool | None, int | None]:
        """Pair write readiness and the persisted schema with one recovered State."""
        _reject_composed_continuity_mode(self.__root)
        state, schema = _recover_state_with_schema(self.__root)
        if state.agreements:
            expected_id = sha256(str(self.__root).encode("utf-8")).hexdigest()[:16]
            if state.initialization is None or state.initialization.repository_id != expected_id:
                raise RecoveryError("accepted Slice state belongs to a different repository root")
        ready_schemas = (
            E1_TASK_PROGRESS_SCHEMA, SCHEMA, R3_SCHEMA, R3B_SCHEMA,
            FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA,
            REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
            D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA,
        )
        return state, (schema in ready_schemas if state.agreements else None), schema

    def e1_schema_ready(self) -> bool:
        """Legacy state remains readable but cannot silently enter the E1 writer path."""
        raw = _read_json(_artifact_path(self.__root, "state.json"))
        return isinstance(raw, dict) and raw.get("schema") in (
            E1_TASK_PROGRESS_SCHEMA, SCHEMA, R3_SCHEMA, R3B_SCHEMA,
            FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA,
            REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
            D4_PROVISIONAL_SLICE_SCHEMA, D5_PROVISIONAL_SLICE_SCHEMA,
        )

    def activate_provisional_slice_writes(
        self, state: State, repository_id: str, feature_id: str,
    ) -> State:
        """Enable only the revision-bound initial schema-15 session-request lane."""
        with _state_publish_lock(self.__root):
            current, schema = _recover_state_with_schema(self.__root)
            if state != current:
                raise AtomicFailure("D4 activation source State is stale")
            if current.provisional_slice_activation is not None:
                activation = current.provisional_slice_activation
                if (
                    schema == D4_PROVISIONAL_SLICE_SCHEMA
                    and activation.repository_id == repository_id
                    and str(activation.feature_id) == feature_id
                ):
                    return current
                raise AtomicFailure("D4 activation authority already differs")
            if schema != SCHEMA:
                raise AtomicFailure("D4 activation requires exact schema-15 source authority")
            try:
                feature_agreement, slice_agreement = _d4_initial_session_request(
                    self.__root, current,
                )
            except DecodeError as error:
                raise AtomicFailure("D4 activation source is not eligible") from error
            if (
                repository_id != feature_agreement.repository_id
                or feature_id != str(feature_agreement.feature_id)
                or slice_agreement.feature_id != feature_id
            ):
                raise AtomicFailure("D4 activation repository or Feature identity is incorrect")
            source_path = _artifact_path(self.__root, "state.json")
            source_digest = sha256(source_path.read_bytes()).hexdigest()
            activation = ProvisionalSliceActivation(
                D4_PROVISIONAL_SLICE_ACTIVATION_MARKER,
                SCHEMA,
                current.revision,
                source_digest,
                repository_id,
                FeatureId(feature_id),
                feature_agreement.id,
                slice_agreement.id,
                current.revision + 1,
            )
            candidate = replace(
                current,
                revision=current.revision + 1,
                provisional_slice_activation=activation,
            )
            try:
                validate_state(candidate)
                _validate_provisional_slice_authority(
                    self.__root, candidate, schema=D4_PROVISIONAL_SLICE_SCHEMA,
                )
            except (StateValidationError, DecodeError, DomainError) as error:
                raise AtomicFailure("D4 activation candidate is invalid") from error
            self.__publish_state_locked(
                candidate,
                strict_directory=True,
                allow_d4_activation=True,
                expected_current=current,
            )
            return candidate

    def publish_d4_provisional_slice(
        self,
        state: State,
        repository_id: str,
        feature_id: str,
        expected_head: ProvisionalSliceHead | None,
        title: str,
        outcome: str,
        example: str | None,
        inclusions: tuple[str, ...],
        exclusions: tuple[str, ...],
        considerations: tuple[str, ...],
        open_questions: tuple[str, ...],
        criteria: tuple[ProvisionalSliceCriterion, ...],
    ) -> State:
        """Publish one bounded AGENT_SUGGESTION and its current head under one CAS."""
        with _state_publish_lock(self.__root):
            current, schema = _recover_state_with_schema(self.__root)
            if state != current:
                raise AtomicFailure("D4 provisional Slice State revision is stale")
            if schema != D4_PROVISIONAL_SLICE_SCHEMA:
                raise AtomicFailure("D4 provisional Slice activation is required")
            activation = current.provisional_slice_activation
            if (
                activation is None
                or repository_id != activation.repository_id
                or feature_id != str(activation.feature_id)
            ):
                raise AtomicFailure("D4 proposal repository or Feature differs from activation")
            if expected_head is not None and type(expected_head) is not ProvisionalSliceHead:
                raise AtomicFailure("D4 expected head must be the exact Core head or null")
            try:
                revision = _new_d4_provisional_slice_revision(
                    repository_id, feature_id, expected_head, title, outcome, example,
                    inclusions, exclusions, considerations, open_questions, criteria,
                )
            except (DecodeError, DomainError, AttributeError, TypeError) as error:
                raise AtomicFailure("D4 provisional Slice payload is invalid") from error
            heads = {item.proposal_id: item for item in current.provisional_slice_heads}
            current_head = heads.get(revision.proposal_id)
            revisions_by_id = {
                item.revision_id: item for item in current.provisional_slice_revisions
            }
            if expected_head is None:
                if (
                    current_head is not None
                    and current_head.revision_id == revision.revision_id
                    and revisions_by_id.get(revision.revision_id) == revision
                ):
                    return current
                if current_head is not None:
                    raise AtomicFailure("D4 create expected a null current head")
                if len(current.provisional_slice_heads) >= 32:
                    raise AtomicFailure("D4 provisional Slice project head limit is exceeded")
            else:
                prior_revision = revisions_by_id.get(expected_head.revision_id)
                expected_recorded_head = (
                    None if prior_revision is None
                    else ProvisionalSliceHead(
                        prior_revision.proposal_id, prior_revision.revision_id,
                        prior_revision.revision, prior_revision.digest,
                    )
                )
                if expected_recorded_head != expected_head:
                    raise AtomicFailure("D4 expected head is not a recorded immutable revision")
                if current_head != expected_head:
                    if (
                        current_head is not None
                        and current_head.revision_id == revision.revision_id
                        and revisions_by_id.get(revision.revision_id) == revision
                    ):
                        return current
                    raise AtomicFailure("D4 expected provisional Slice head is stale")
                if expected_head.revision >= 32:
                    raise AtomicFailure("D4 provisional Slice revision limit is exceeded")
            if any(item.revision_id == revision.revision_id for item in current.provisional_slice_revisions):
                raise AtomicFailure("D4 immutable provisional Slice revision conflicts")
            candidate_head = ProvisionalSliceHead(
                revision.proposal_id, revision.revision_id, revision.revision, revision.digest,
            )
            candidate_heads = tuple(sorted(
                tuple(
                    item for item in current.provisional_slice_heads
                    if item.proposal_id != revision.proposal_id
                ) + (candidate_head,),
                key=lambda item: item.proposal_id,
            ))
            candidate = replace(
                current,
                revision=current.revision + 1,
                provisional_slice_revisions=tuple(sorted(
                    current.provisional_slice_revisions + (revision,),
                    key=lambda item: item.revision_id,
                )),
                provisional_slice_heads=candidate_heads,
            )
            try:
                validate_state(candidate)
                _validate_provisional_slice_authority(
                    self.__root, candidate, schema=D4_PROVISIONAL_SLICE_SCHEMA,
                )
            except (StateValidationError, DecodeError, DomainError) as error:
                raise AtomicFailure("D4 provisional Slice candidate is invalid") from error
            record_bytes = encode_json(_provisional_slice_to_json(revision))
            if len(record_bytes) > 64 * 1024:
                raise AtomicFailure("D4 provisional Slice payload exceeds 64 KiB")
            self.__publish_immutable(
                f"records/provisional-slices/{revision.revision_id}.json",
                record_bytes,
                strict_directory=True,
            )
            self.__publish_state_locked(
                candidate,
                strict_directory=True,
                allow_d4_provisional_slice=True,
                expected_current=current,
            )
            return candidate

    def _publish_d5_provisional_slice_adoption(
        self,
        state: State,
        change: Change,
        agreements: tuple[AgreementRecord, AgreementRecord],
        criteria: SliceCriteriaRevision,
        adoption: ProvisionalSliceAdoption,
        source_state_sha256: str,
    ) -> None:
        """Publish the one D5 adoption aggregate and schema-24 pointer under CAS."""
        with _state_publish_lock(self.__root):
            current, schema = _recover_state_with_schema(self.__root)
            if schema != D4_PROVISIONAL_SLICE_SCHEMA:
                raise AtomicFailure("D5 adoption requires exact schema-23 source authority")
            if (
                state.revision != current.revision + 1
                or current.provisional_slice_adoptions
                or current.provisional_slice_activation is None
                or current.active_context is not None
                or current.active_work_id is not None
                or len(current.features) != 1
                or len(current.changes) != 1
                or len(current.agreements) != 2
                or len(current.terminal_archives) != 1
                or len(current.work_items) != 1
            ):
                raise AtomicFailure("D5 adoption source is not the closed initial D4 lane")
            state_path = _artifact_path(self.__root, "state.json")
            actual_source_digest = sha256(state_path.read_bytes()).hexdigest()
            if (
                source_state_sha256 != actual_source_digest
                or adoption.source_schema != D4_PROVISIONAL_SLICE_SCHEMA
                or adoption.source_revision != current.revision
                or adoption.source_state_sha256 != actual_source_digest
                or adoption.recorded_revision != state.revision
            ):
                raise AtomicFailure("D5 adoption source bytes changed after consent")
            if (
                len(agreements) != 2
                or agreements[0].kind != "FEATURE"
                or agreements[1].kind != "SLICE"
                or state.agreements != current.agreements + agreements
                or state.changes != current.changes + (change,)
                or state.slice_criteria_revisions != (criteria,)
                or state.slice_criteria_heads != (
                    SliceCriteriaHead(agreements[1].id, criteria.id),
                )
                or state.provisional_slice_revisions != current.provisional_slice_revisions
                or state.provisional_slice_heads != current.provisional_slice_heads
                or state.provisional_slice_activation != current.provisional_slice_activation
                or state.provisional_slice_adoptions != (adoption,)
                or change.id in {item.id for item in current.changes}
                or any(item.id in {existing.id for existing in current.agreements} for item in agreements)
                or criteria.id in {item.id for item in current.slice_criteria_revisions}
                or replace(
                    state,
                    revision=current.revision,
                    active_context=current.active_context,
                    active_work_id=current.active_work_id,
                    changes=current.changes,
                    agreements=current.agreements,
                    slice_criteria_revisions=current.slice_criteria_revisions,
                    slice_criteria_heads=current.slice_criteria_heads,
                    provisional_slice_adoptions=current.provisional_slice_adoptions,
                ) != current
            ):
                raise AtomicFailure("D5 adoption must append only its exact accepted aggregate")
            if self.__writer_schema_locked(current, allow_d5_adoption=True) != D5_PROVISIONAL_SLICE_SCHEMA:
                raise AtomicFailure("D5 schema-24 adoption is unavailable")
            try:
                validate_state(state)
                _validate_provisional_slice_authority(
                    self.__root, state, schema=D5_PROVISIONAL_SLICE_SCHEMA,
                )
            except (StateValidationError, DecodeError, DomainError) as error:
                raise AtomicFailure("D5 adoption candidate is invalid") from error

            artifacts = (
                (
                    f"records/agreements/{agreements[0].id}.json",
                    encode_json(_agreement_to_json(agreements[0])),
                ),
                (
                    f"records/agreements/{agreements[1].id}.json",
                    encode_json(_agreement_to_json(agreements[1])),
                ),
                (
                    f"records/changes/{change.id}.json",
                    encode_json(_change_to_json(change)),
                ),
                (
                    f"records/slice-criteria/{criteria.id}.json",
                    encode_json(_slice_criteria_to_json(criteria)),
                ),
                (
                    f"records/provisional-slice-adoptions/{adoption.id}.json",
                    encode_json(_provisional_slice_adoption_to_json(adoption)),
                ),
            )
            if any(len(data) > 128 * 1024 for _, data in artifacts):
                raise AtomicFailure("D5 immutable adoption record exceeds 128 KiB")
            for relative, data in artifacts:
                path = _artifact_path(self.__root, relative)
                if path.exists() and path.read_bytes() != data:
                    raise AtomicFailure("D5 immutable adoption record identity collides")
            for relative, data in artifacts:
                self.__publish_immutable(relative, data, strict_directory=True)
            self.__publish_state_locked(
                state,
                strict_directory=True,
                allow_d5_adoption=True,
                expected_current=current,
            )

    def r3_schema_ready(self) -> bool:
        """Schema 15 stays readable; it never becomes an R3 writer without consent."""
        raw = _read_json(_artifact_path(self.__root, "state.json"))
        return isinstance(raw, dict) and raw.get("schema") in (R3_SCHEMA, R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA)

    def r3b_schema_ready(self) -> bool:
        """Schema 16 stays readable; schema 17 requires its own explicit decision."""
        raw = _read_json(_artifact_path(self.__root, "state.json"))
        return isinstance(raw, dict) and raw.get("schema") in (R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA)

    def request_objective_schema_ready(self) -> bool:
        """Recognize either exact canonical request-objective writer schema."""
        raw = _read_json(_artifact_path(self.__root, "state.json"))
        return isinstance(raw, dict) and raw.get("schema") in (
            REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
        )

    def r3b_upgrade_eligible(self) -> bool:
        raw = _read_json(_artifact_path(self.__root, "state.json"))
        return isinstance(raw, dict) and raw.get("schema") == R3_SCHEMA

    def input_snapshot(self, expected_revision: int | None = None) -> tuple[State, InputSnapshot]:
        """Scan the exact current initialized root while state publication is excluded."""
        with _state_publish_lock(self.__root):
            current = self.recover()
            if expected_revision is not None and current.revision != expected_revision:
                raise AtomicFailure("R3-B input snapshot revision is stale")
            if current.initialization is None:
                raise AtomicFailure("R3-B input snapshot requires initialized repository authority")
            try:
                snapshot = repository_input_snapshot(
                    self.__root, current.initialization.repository_id,
                )
            except InputFingerprintError as error:
                raise AtomicFailure(f"R3-B input snapshot failed: {error}") from error
            return current, snapshot

    def r3_upgrade_eligible(self) -> bool:
        """Only actual schema-15 bytes can be considered for R3 conversion."""
        raw = _read_json(_artifact_path(self.__root, "state.json"))
        return isinstance(raw, dict) and raw.get("schema") == SCHEMA

    def state_source_sha256(self) -> str:
        """Bind a human schema decision to exact source-state bytes, not only revision."""
        return sha256(_artifact_path(self.__root, "state.json").read_bytes()).hexdigest()

    def publish_feature(self, state: State) -> None:
        self.__publish_state(state)

    def publish_first_slice(self, state: State, change: Change, agreements: tuple[AgreementRecord, ...]) -> None:
        """Commit Feature/Slice/Task as one authority revision after durable records."""
        with _state_publish_lock(self.__root):
            # Validate the CAS before creating records that may become inert orphans.
            current = self.__check_state_revision(state)
            if self.__writer_schema_locked(current) not in (SCHEMA, R3_SCHEMA, R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA):
                raise AtomicFailure("first E1 Slice requires the current state schema")
            for agreement in agreements:
                self.__publish_immutable(
                    f"records/agreements/{agreement.id}.json",
                    encode_json(_agreement_to_json(agreement)),
                    strict_directory=True,
                )
            self.__publish_immutable(
                f"records/changes/{change.id}.json", encode_json(_change_to_json(change)),
                strict_directory=True,
            )
            for revision in state.slice_criteria_revisions:
                self.__publish_immutable(
                    f"records/slice-criteria/{revision.id}.json",
                    encode_json(_slice_criteria_to_json(revision)), strict_directory=True,
                )
            self.__publish_state_locked(state, strict_directory=True)

    def publish_next_slice(
        self, state: State, change: Change,
        agreements: tuple[AgreementRecord, AgreementRecord],
        criteria: SliceCriteriaRevision,
    ) -> None:
        """Publish a new accepted Slice and all of its immutable records under one CAS."""
        with _state_publish_lock(self.__root):
            current = self.__check_state_revision(state)
            if self.__writer_schema_locked(current) not in (R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA):
                raise AtomicFailure("next Slice requires schema-17 authority")
            if (
                len(agreements) != 2 or agreements[0].kind != "FEATURE"
                or agreements[1].kind != "SLICE"
                or state.agreements != current.agreements + agreements
                or state.changes != current.changes + (change,)
                or state.slice_criteria_revisions != current.slice_criteria_revisions + (criteria,)
                or len(state.slice_criteria_heads) != len(current.slice_criteria_heads) + 1
                or state.slice_criteria_heads[:-1] != current.slice_criteria_heads
                or state.slice_criteria_heads[-1].revision_id != criteria.id
                or state.slice_criteria_heads[-1].slice_agreement_ref != agreements[1].id
            ):
                raise AtomicFailure("next Slice must append one exact accepted aggregate")
            for agreement in agreements:
                self.__publish_immutable(
                    f"records/agreements/{agreement.id}.json",
                    encode_json(_agreement_to_json(agreement)), strict_directory=True,
                )
            self.__publish_immutable(
                f"records/changes/{change.id}.json",
                encode_json(_change_to_json(change)), strict_directory=True,
            )
            self.__publish_immutable(
                f"records/slice-criteria/{criteria.id}.json",
                encode_json(_slice_criteria_to_json(criteria)), strict_directory=True,
            )
            self.__publish_state_locked(state, strict_directory=True, allow_next_slice=True)

    def publish_slice_criteria_revision(self, state: State, revision: SliceCriteriaRevision) -> None:
        """Publish an amendment under the same State CAS as its new head pointer."""
        with _state_publish_lock(self.__root):
            current = self.__check_state_revision(state)
            # Refuse an ineligible schema before creating even an orphan record.
            self.__writer_schema_locked(current, allow_criteria_upgrade=True)
            self.__publish_immutable(
                f"records/slice-criteria/{revision.id}.json",
                encode_json(_slice_criteria_to_json(revision)), strict_directory=True,
            )
            self.__publish_state_locked(state, strict_directory=True, allow_criteria_upgrade=True)

    def publish_lifecycle(self, state: State) -> None:
        self.__publish_state(state, d4_operation="lifecycle")

    def publish_progress(self, state: State) -> None:
        self.__publish_state(state)

    def publish_safety_facts(self, state: State) -> None:
        self.__publish_state(state)

    def publish_review(self, state: State) -> None:
        self.__publish_state(state, d4_operation="review")

    def publish_verification(self, state: State) -> None:
        self.__publish_state(state, d4_operation="verification")

    def publish_convergence(self, state: State) -> None:
        self.__publish_state(state, d4_operation="convergence")

    def publish_repair(self, state: State) -> None:
        self.__publish_state(state)

    def publish_terminal_archive(self, state: State) -> None:
        self.__publish_state(state, d4_operation="accepted_terminal_archive")

    def publish_primary_restoration(self, state: State) -> None:
        self.__publish_state(state)

    def publish_implementation_alteration(self, state: State) -> None:
        self.__publish_state(state)

    def publish_lock(self, state: State) -> None:
        self.__publish_state(state)

    def publish_change(self, state: State, change: Change) -> None:
        self.__publish_change(state, change)

    def publish_side_activation(self, state: State, change: Change) -> None:
        self.__publish_change(state, change)

    def publish_decision(self, state: State, decision: Decision) -> None:
        self.__publish_decision(state, decision)

    def publish_decision_supersession(
        self, state: State, replacement: Decision
    ) -> None:
        self.__publish_decision(state, replacement)

    def publish_observation(self, state: State, observation: Observation) -> None:
        self.__publish_with_immutable(state,
            f"records/observations/{observation.id}.json",
            encode_json(_observation_to_json(observation)),
        )

    def publish_drift(self, state: State, drift: Drift) -> None:
        self.__publish_with_immutable(state,
            f"records/observations/{drift.id}.json",
            encode_json(_drift_to_json(drift)),
        )

    def transact_e1(
        self, expected_revision: int, transform: Callable[[State], State], *,
        upgrade: bool = False, source_digest: str | None = None,
        allow_d4_task_progress: bool = False,
        allow_d5_task_progress: bool = False,
    ) -> State:
        """Validate and publish one E1 operation entirely under the state writer lock."""
        with _state_publish_lock(self.__root):
            current = self.recover()
            if current.revision != expected_revision:
                raise AtomicFailure("E1 expected revision is stale")
            if upgrade and (source_digest is None or self.state_source_sha256() != source_digest):
                raise AtomicFailure("E1 conversion source identity changed after human approval")
            source_schema = self.__writer_schema_locked(
                current, allow_e1_upgrade=upgrade,
                allow_d4_task_progress=allow_d4_task_progress,
                allow_d5_task_progress=allow_d5_task_progress,
            )
            if source_schema not in (
                E1_TASK_PROGRESS_SCHEMA, SCHEMA, R3_SCHEMA, R3B_SCHEMA,
                FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA,
                REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
                *((D4_PROVISIONAL_SLICE_SCHEMA,) if allow_d4_task_progress else ()),
                *((D5_PROVISIONAL_SLICE_SCHEMA,) if allow_d5_task_progress else ()),
            ):
                raise AtomicFailure("E1_TASK_PROGRESS_REQUIRES_SCHEMA_14")
            candidate = transform(current)
            if candidate.revision != current.revision + 1:
                raise AtomicFailure("E1 operation must publish exactly one revision")
            if source_schema == D4_PROVISIONAL_SLICE_SCHEMA:
                if (
                    not allow_d4_task_progress
                    or replace(candidate, revision=current.revision, active_context=current.active_context) != current
                ):
                    raise AtomicFailure("D4 Task progress must preserve all accepted authority")
                try:
                    validate_state(candidate)
                    _validate_provisional_slice_authority(
                        self.__root, candidate, schema=D4_PROVISIONAL_SLICE_SCHEMA,
                    )
                except (StateValidationError, DecodeError) as error:
                    raise AtomicFailure("D4 Task progress candidate is invalid") from error
            elif source_schema == D5_PROVISIONAL_SLICE_SCHEMA:
                if (
                    not allow_d5_task_progress
                    or replace(candidate, revision=current.revision, active_context=current.active_context) != current
                ):
                    raise AtomicFailure("D5 Task progress must preserve all accepted authority")
                try:
                    validate_state(candidate)
                    _validate_provisional_slice_authority(
                        self.__root, candidate, schema=D5_PROVISIONAL_SLICE_SCHEMA,
                    )
                except (StateValidationError, DecodeError) as error:
                    raise AtomicFailure("D5 Task progress candidate is invalid") from error
            self.__publish_state_locked(
                candidate, strict_directory=True, allow_e1_upgrade=upgrade,
                allow_d4_task_progress=allow_d4_task_progress,
                allow_d5_task_progress=allow_d5_task_progress,
                expected_current=current,
            )
            return candidate

    def transact_r3(
        self, expected_revision: int, transform: Callable[[State], State], *,
        upgrade: bool = False, source_digest: str | None = None,
    ) -> State:
        """Publish R3 authority under the exact state lock and explicit schema gate."""
        with _state_publish_lock(self.__root):
            current = self.recover()
            if current.revision != expected_revision:
                raise AtomicFailure("R3 expected revision is stale")
            if upgrade and (source_digest is None or self.state_source_sha256() != source_digest):
                raise AtomicFailure("R3 conversion source identity changed after human approval")
            output_schema = self.__writer_schema_locked(current, allow_r3_upgrade=upgrade)
            if output_schema != R3_SCHEMA:
                raise AtomicFailure("R3_SCHEMA_UPGRADE_REQUIRED")
            candidate = transform(current)
            if candidate.revision != current.revision + 1:
                raise AtomicFailure("R3 operation must publish exactly one revision")
            self.__publish_state_locked(candidate, strict_directory=True, allow_r3_upgrade=upgrade)
            return candidate

    def transact_r3b(
        self,
        expected_revision: int,
        transform: Callable[[State], State],
        *,
        expected_input_fingerprint: str | None = None,
        upgrade: bool = False,
        source_digest: str | None = None,
        allow_noop: bool = False,
    ) -> State:
        """Publish one schema-17 action after a fresh in-lock workspace scan."""
        with _state_publish_lock(self.__root):
            current = self.recover()
            if current.revision != expected_revision:
                raise AtomicFailure("R3-B expected revision is stale")
            if source_digest is not None and self.state_source_sha256() != source_digest:
                raise AtomicFailure("R3-B conversion source identity changed after human approval")
            output_schema = self.__writer_schema_locked(current, allow_r3b_upgrade=upgrade)
            if output_schema not in (R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA):
                raise AtomicFailure("R3B_SCHEMA_UPGRADE_REQUIRED")
            if not upgrade and expected_input_fingerprint is None:
                raise AtomicFailure("R3-B operation requires its observed workspace fingerprint")
            if expected_input_fingerprint is not None:
                if current.initialization is None:
                    raise AtomicFailure("R3-B operation requires initialized repository authority")
                try:
                    snapshot = repository_input_snapshot(
                        self.__root, current.initialization.repository_id,
                    )
                except InputFingerprintError as error:
                    raise AtomicFailure(f"R3-B fresh input scan failed: {error}") from error
                if snapshot.fingerprint != expected_input_fingerprint:
                    raise AtomicFailure("R3-B workspace fingerprint changed before publication")
            candidate = transform(current)
            if allow_noop and candidate == current:
                return current
            if candidate.revision != current.revision + 1:
                raise AtomicFailure("R3-B operation must publish exactly one revision")
            self.__publish_state_locked(
                candidate, strict_directory=True, allow_r3b_upgrade=upgrade,
            )
            return candidate

    def transact_request_objective(
        self, expected_revision: int, transform: Callable[[State], State], *,
        expected_input_fingerprint: str,
    ) -> State:
        """Record one Core-derived request objective without synthetic R3 approvals."""
        with _state_publish_lock(self.__root):
            current = self.recover()
            if current.revision != expected_revision:
                raise AtomicFailure("REQUEST_OBJECTIVE_STALE_REVISION")
            output_schema = self.__writer_schema_locked(
                current, allow_request_objective=True,
            )
            if output_schema not in (
                REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
            ) or current.initialization is None:
                raise AtomicFailure("REQUEST_OBJECTIVE_SCHEMA_UNAVAILABLE")
            try:
                snapshot = repository_input_snapshot(
                    self.__root, current.initialization.repository_id,
                )
            except InputFingerprintError as error:
                raise AtomicFailure(f"REQUEST_OBJECTIVE_SNAPSHOT_FAILED: {error}") from error
            if snapshot.fingerprint != expected_input_fingerprint:
                raise AtomicFailure("REQUEST_OBJECTIVE_FINGERPRINT_STALE")
            candidate = transform(current)
            if candidate == current:
                raw = _read_json(_artifact_path(self.__root, "state.json"))
                if not isinstance(raw, dict) or raw.get("schema") != output_schema:
                    raise AtomicFailure("REQUEST_OBJECTIVE_SCHEMA_UPGRADE_REQUIRED")
                return current
            if candidate.revision != current.revision + 1:
                raise AtomicFailure("REQUEST_OBJECTIVE must publish exactly one revision")
            try:
                validate_state(candidate)
            except StateValidationError as error:
                raise AtomicFailure(f"REQUEST_OBJECTIVE candidate is invalid: {error}") from error
            self.__validate_request_objective_delta(
                current, candidate, expected_input_fingerprint, output_schema,
            )
            try:
                after = repository_input_snapshot(
                    self.__root, current.initialization.repository_id,
                )
            except InputFingerprintError as error:
                raise AtomicFailure(f"REQUEST_OBJECTIVE_SNAPSHOT_FAILED: {error}") from error
            if after.fingerprint != expected_input_fingerprint:
                raise AtomicFailure("REQUEST_OBJECTIVE_FINGERPRINT_STALE")
            if len(candidate.slice_criteria_revisions) == len(current.slice_criteria_revisions) + 1:
                revision = candidate.slice_criteria_revisions[-1]
                self.__publish_immutable(
                    f"records/slice-criteria/{revision.id}.json",
                    encode_json(_slice_criteria_to_json(revision)),
                    strict_directory=True,
                )
            self.__publish_state_locked(
                candidate,
                strict_directory=True,
                allow_request_objective=True,
            )
            return candidate

    def __validate_request_objective_delta(
        self, current: State, candidate: State,
        expected_input_fingerprint: str, output_schema: int,
    ) -> None:
        criteria_added = len(candidate.slice_criteria_revisions) - len(current.slice_criteria_revisions)
        heads_added = len(candidate.slice_criteria_heads) - len(current.slice_criteria_heads)
        proof_added = len(candidate.criterion_test_receipts) - len(current.criterion_test_receipts)
        outcome_added = len(candidate.slice_outcome_receipts) - len(current.slice_outcome_receipts)
        if (
            _request_objective_schema_for_state(candidate) != output_schema
            or not candidate.slice_criteria_revisions
        ):
            raise AtomicFailure("REQUEST_OBJECTIVE candidate is not the selected canonical lane")
        criteria = candidate.slice_criteria_revisions[-1]
        try:
            policy_id, policy_digest, outcome_basis = request_objective_policy(criteria)
        except DomainError as error:
            raise AtomicFailure("REQUEST_OBJECTIVE criteria have no canonical policy") from error
        new_proofs = candidate.criterion_test_receipts[len(current.criterion_test_receipts):]
        new_outcomes = candidate.slice_outcome_receipts[len(current.slice_outcome_receipts):]
        if criteria_added not in (0, 1) or heads_added not in (0, 1):
            raise AtomicFailure("REQUEST_OBJECTIVE may append at most one canonical criteria head")
        if (
            candidate.slice_criteria_revisions[:len(current.slice_criteria_revisions)]
            != current.slice_criteria_revisions
            or candidate.slice_criteria_heads[:len(current.slice_criteria_heads)]
            != current.slice_criteria_heads
            or heads_added != criteria_added
            or proof_added != len(criteria.criteria)
            or candidate.criterion_test_receipts[:len(current.criterion_test_receipts)]
            != current.criterion_test_receipts
            or outcome_added != 1
            or candidate.slice_outcome_receipts[:len(current.slice_outcome_receipts)]
            != current.slice_outcome_receipts
            or replace(
                candidate,
                revision=current.revision,
                slice_criteria_revisions=current.slice_criteria_revisions,
                slice_criteria_heads=current.slice_criteria_heads,
                criterion_test_receipts=current.criterion_test_receipts,
                slice_outcome_receipts=current.slice_outcome_receipts,
            ) != current
        ):
            raise AtomicFailure("REQUEST_OBJECTIVE changed authority outside its exact receipt bundle")
        if tuple(item.criterion_id for item in new_proofs) != tuple(
            item.id for item in criteria.criteria
        ):
            raise AtomicFailure("REQUEST_OBJECTIVE must append one ordered proof per criterion")
        if any(
            proof.slice_agreement_ref != criteria.slice_agreement_ref
            or proof.criteria_revision_id != criteria.id
            or proof.test_kind != policy_id
            or proof.test_target != "index.html"
            or proof.exit_code != 0
            or proof.source_revision != current.revision
            or proof.recorded_revision != candidate.revision
            or proof.input_fingerprint_before != expected_input_fingerprint
            or proof.input_fingerprint_after != expected_input_fingerprint
            or proof.input_policy_id != INPUT_POLICY_ID
            or proof.input_policy_digest != INPUT_POLICY_DIGEST
            or proof.admission_policy_id != policy_id
            or proof.admission_policy_digest != policy_digest
            or proof.external_inputs
            or proof.runtime_executable is not None
            or proof.runtime_executable_sha256 is not None
            or proof.runtime_version is not None
            or proof.runtime_argv
            or proof.environment_profile_id is not None
            or proof.environment_profile_digest is not None
            or proof.coverage_approval_id is not None
            or proof.coverage_contract_digest is not None
            or not is_sha256_digest(proof.output_sha256)
            for proof in new_proofs
        ):
            raise AtomicFailure("REQUEST_OBJECTIVE proof bundle has unsupported or mismatched provenance")
        if (
            output_schema == SEMANTIC_REQUEST_OBJECTIVE_SCHEMA
            and len({proof.output_sha256 for proof in new_proofs}) != 1
        ):
            # One browser execution observes one artifact; each criterion gets a
            # separate receipt, all naming that same exact verified byte stream.
            raise AtomicFailure("REQUEST_OBJECTIVE browser proofs must name one artifact digest")
        outcome = new_outcomes[0]
        if (
            outcome.evidence_receipt_ids != tuple(item.id for item in new_proofs)
            or outcome.slice_agreement_ref != criteria.slice_agreement_ref
            or outcome.criteria_revision_id != criteria.id
            or outcome.repository_id != criteria.repository_id
            or outcome.input_fingerprint != expected_input_fingerprint
            or outcome.input_policy_id != INPUT_POLICY_ID
            or outcome.input_policy_digest != INPUT_POLICY_DIGEST
            or outcome.admission_policy_id != policy_id
            or outcome.admission_policy_digest != policy_digest
            or outcome.basis != outcome_basis
            or outcome.source != REQUEST_SCOPED_OBJECTIVE
            or outcome.source_revision != current.revision
            or outcome.recorded_revision != candidate.revision
        ):
            raise AtomicFailure("REQUEST_OBJECTIVE outcome differs from its exact proof bundle")

    def transact_feature_completion(
        self, expected_revision: int, transform: Callable[[State], State],
        *, expected_input_fingerprint: str, request_objective: bool = False,
    ) -> State:
        """Commit human-approved Feature closure and schema 17→18 as one atomic step."""
        with _state_publish_lock(self.__root):
            current = self.recover()
            if current.revision != expected_revision:
                raise AtomicFailure("Feature completion expected revision is stale")
            output_schema = self.__writer_schema_locked(
                current,
                allow_feature_completion=True,
                allow_request_objective=request_objective,
            )
            if (
                output_schema not in (
                    FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA,
                    REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
                )
                or current.initialization is None
                or request_objective and output_schema not in (
                    REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA,
                )
            ):
                raise AtomicFailure("FEATURE_COMPLETION_REQUIRES_SCHEMA_17")
            try:
                snapshot = repository_input_snapshot(self.__root, current.initialization.repository_id)
            except InputFingerprintError as error:
                raise AtomicFailure(f"Feature completion input scan failed: {error}") from error
            if snapshot.fingerprint != expected_input_fingerprint:
                raise AtomicFailure("Feature completion root inputs changed before publication")
            candidate = transform(current)
            if request_objective:
                try:
                    validate_state(candidate)
                except StateValidationError as error:
                    raise AtomicFailure(f"REQUEST_OBJECTIVE completion candidate is invalid: {error}") from error
                if candidate.revision != current.revision + 1:
                    raise AtomicFailure("REQUEST_OBJECTIVE completion must publish exactly one revision")
                if (
                    len(candidate.feature_completion_receipts)
                    != len(current.feature_completion_receipts) + 1
                    or candidate.feature_completion_receipts[:len(current.feature_completion_receipts)]
                    != current.feature_completion_receipts
                    or len(candidate.terminal_archives) != len(current.terminal_archives) + 1
                    or candidate.terminal_archives[:len(current.terminal_archives)]
                    != current.terminal_archives
                    or len(candidate.work_items) != len(current.work_items) + 1
                    or candidate.work_items[:len(current.work_items)] != current.work_items
                    or candidate.active_context is not None
                    or candidate.active_work_id is not None
                    or replace(
                        candidate,
                        revision=current.revision,
                        active_context=current.active_context,
                        active_work_id=current.active_work_id,
                        terminal_archives=current.terminal_archives,
                        work_items=current.work_items,
                        feature_completion_receipts=current.feature_completion_receipts,
                    ) != current
                ):
                    raise AtomicFailure("REQUEST_OBJECTIVE completion changed authority outside its exact closure")
                try:
                    after = repository_input_snapshot(
                        self.__root, current.initialization.repository_id,
                    )
                except InputFingerprintError as error:
                    raise AtomicFailure(f"REQUEST_OBJECTIVE_SNAPSHOT_FAILED: {error}") from error
                if after.fingerprint != expected_input_fingerprint:
                    raise AtomicFailure("REQUEST_OBJECTIVE_FINGERPRINT_STALE")
            if candidate == current:
                return current
            if candidate.revision != current.revision + 1:
                raise AtomicFailure("Feature completion must publish exactly one revision")
            self.__publish_state_locked(
                candidate,
                strict_directory=True,
                allow_feature_completion=True,
                allow_request_objective=request_objective,
            )
            return candidate

    def transact_feature_reopening(
        self, expected_revision: int, transform: Callable[[State], State],
        *, expected_input_fingerprint: str,
    ) -> State:
        """Convert 18→19 and append exactly one human reopening atomically."""
        with _state_publish_lock(self.__root):
            current = self.recover()
            if current.revision != expected_revision:
                raise AtomicFailure("Feature reopening expected revision is stale")
            output_schema = self.__writer_schema_locked(current, allow_feature_reopening=True)
            if output_schema not in (FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA) or current.initialization is None:
                raise AtomicFailure("FEATURE_REOPENING_REQUIRES_SCHEMA_18")
            try:
                snapshot = repository_input_snapshot(self.__root, current.initialization.repository_id)
            except InputFingerprintError as error:
                raise AtomicFailure(f"Feature reopening input scan failed: {error}") from error
            if snapshot.fingerprint != expected_input_fingerprint:
                raise AtomicFailure("Feature reopening root inputs changed before publication")
            candidate = transform(current)
            if candidate.revision != current.revision + 1:
                raise AtomicFailure("Feature reopening must publish exactly one revision")
            self.__publish_state_locked(
                candidate, strict_directory=True, allow_feature_reopening=True,
            )
            return candidate

    def publish_primary_convergence(
        self, state: State, publication: LivingSpecPublication, content: str
    ) -> None:
        self.__publish_living_spec(state, publication, content)

    def publish_side_convergence(
        self, state: State, publication: LivingSpecPublication, content: str
    ) -> None:
        self.__publish_living_spec(state, publication, content)

    def __publish_state(self, state: State, *, d4_operation: str | None = None) -> None:
        with _state_publish_lock(self.__root):
            if d4_operation is not None:
                current, schema = _recover_state_with_schema(self.__root)
                if schema == D4_PROVISIONAL_SLICE_SCHEMA:
                    self.__publish_state_locked(
                        state,
                        allow_d4_lifecycle=d4_operation,
                        expected_current=current,
                    )
                    return
                if schema == D5_PROVISIONAL_SLICE_SCHEMA:
                    self.__publish_state_locked(
                        state,
                        allow_d5_lifecycle=d4_operation,
                        expected_current=current,
                    )
                    return
            self.__publish_state_locked(state)

    def __check_state_revision(self, state: State) -> State:
        current = _recover_state(self.__root)
        if state.revision != current.revision + 1:
            raise AtomicFailure("state revision changed before publication")
        return current

    def __writer_schema_locked(
        self, current: State, *, allow_e1_upgrade: bool = False, allow_criteria_upgrade: bool = False,
        allow_r3_upgrade: bool = False, allow_r3b_upgrade: bool = False,
        allow_feature_completion: bool = False, allow_feature_reopening: bool = False,
        allow_request_objective: bool = False, allow_d4_activation: bool = False,
        allow_d4_task_progress: bool = False, allow_d4_provisional_slice: bool = False,
        allow_d4_lifecycle: str | None = None,
        allow_d5_adoption: bool = False, allow_d5_task_progress: bool = False,
        allow_d5_lifecycle: str | None = None,
    ) -> int:
        """Choose the output version from persisted authority, never caller input."""
        state_path = _artifact_path(self.__root, "state.json")
        if not state_path.exists():
            if allow_e1_upgrade or allow_r3_upgrade or allow_r3b_upgrade:
                raise AtomicFailure("E1 conversion requires existing schema-13 authority")
            return SCHEMA
        raw = _read_json(state_path)
        if not isinstance(raw, dict) or type(raw.get("schema")) is not int:
            raise AtomicFailure("persisted schema authority is invalid")
        source_schema = raw["schema"]
        if source_schema == PROVISIONAL_SLICE_SCHEMA:
            raise AtomicFailure("D1_PROVISIONAL_SCHEMA_READ_ONLY: ordinary writers are disabled")
        if allow_d4_activation:
            if source_schema != SCHEMA:
                raise AtomicFailure("D4 activation requires exact schema-15 source authority")
            return D4_PROVISIONAL_SLICE_SCHEMA
        if allow_d5_adoption:
            if source_schema != D4_PROVISIONAL_SLICE_SCHEMA or current.provisional_slice_adoptions:
                raise AtomicFailure("D5 adoption requires exact schema-23 source authority")
            return D5_PROVISIONAL_SLICE_SCHEMA
        if source_schema == D5_PROVISIONAL_SLICE_SCHEMA:
            if not current.provisional_slice_adoptions:
                raise AtomicFailure("D5 schema 24 is missing its adoption authority")
            if allow_d5_task_progress or allow_d5_lifecycle in _D5_LIFECYCLE_OPERATIONS:
                return D5_PROVISIONAL_SLICE_SCHEMA
            raise AtomicFailure("D5 schema 24 permits only its supported Core writers")
        if source_schema == D4_PROVISIONAL_SLICE_SCHEMA:
            if current.provisional_slice_activation is None:
                raise AtomicFailure("D4 schema 23 is missing its activation authority")
            if allow_d4_task_progress or allow_d4_provisional_slice:
                return D4_PROVISIONAL_SLICE_SCHEMA
            if allow_d4_lifecycle in _D4_LIFECYCLE_OPERATIONS:
                return D4_PROVISIONAL_SLICE_SCHEMA
            raise AtomicFailure("D4 schema 23 permits only its supported Core writers")
        if allow_request_objective:
            if source_schema == SCHEMA:
                if (
                    _request_objective_schema_for_state(current) is None
                    or current.r3_schema_upgrade is not None
                    or current.r3b_schema_upgrade is not None
                    or current.slice_criteria_revisions
                    or current.slice_criteria_heads
                    or current.criterion_test_receipts
                    or current.slice_outcome_receipts
                    or current.criterion_coverage_approvals
                ):
                    raise AtomicFailure("REQUEST_OBJECTIVE_REQUIRES_EXACT_UNCONVERTED_SESSION_REQUEST")
                lane = _request_objective_schema_for_state(current)
                assert lane is not None
                return lane
            if (
                source_schema not in (REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA)
                or _request_objective_schema_for_state(current) != source_schema
                or current.r3b_schema_upgrade is not None
                or not current.slice_outcome_receipts
                or not any(
                    item.source == REQUEST_SCOPED_OBJECTIVE
                    for item in current.slice_criteria_revisions
                )
                or current.feature_completion_receipts
            ):
                raise AtomicFailure("REQUEST_OBJECTIVE_REQUIRES_ACCEPTED_SCHEMA_15_AUTHORITY")
            return source_schema
        if allow_e1_upgrade:
            if source_schema != LEGACY_WRITE_SCHEMA or not current.agreements:
                raise AtomicFailure("only accepted E1 schema-13 authority may be converted")
            return E1_TASK_PROGRESS_SCHEMA
        if allow_r3_upgrade:
            if source_schema != SCHEMA or current.r3_schema_upgrade is not None:
                raise AtomicFailure("only unconverted schema-15 authority may enter R3")
            return R3_SCHEMA
        if allow_r3b_upgrade:
            if source_schema != R3_SCHEMA or current.r3b_schema_upgrade is not None:
                raise AtomicFailure("only unconverted schema-16 authority may enter R3-B")
            return R3B_SCHEMA
        if allow_feature_completion:
            if source_schema in (REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA):
                if (
                    _request_objective_schema_for_state(current) != source_schema
                    or not current.slice_outcome_receipts
                    or not any(
                        item.source == REQUEST_SCOPED_OBJECTIVE
                        for item in current.slice_criteria_revisions
                    )
                ):
                    raise AtomicFailure("REQUEST_OBJECTIVE completion requires its Core proof")
                return source_schema
            if source_schema not in (R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA) or current.r3b_schema_upgrade is None:
                raise AtomicFailure("Feature completion requires explicitly upgraded R3-B authority")
            return FEATURE_CYCLE_SCHEMA if source_schema == FEATURE_CYCLE_SCHEMA else FEATURE_COMPLETION_SCHEMA
        if allow_feature_reopening:
            if (
                source_schema in (REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA)
                and current.feature_completion_receipts
                and _request_objective_schema_for_state(current) == source_schema
            ):
                return source_schema
            if source_schema not in (FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA) or not current.feature_completion_receipts:
                raise AtomicFailure("Feature reopening requires a previous completed Feature")
            return FEATURE_CYCLE_SCHEMA
        if source_schema == LEGACY_WRITE_SCHEMA and current.agreements:
            raise AtomicFailure("E1_SCHEMA_UPGRADE_REQUIRED: ordinary writers are read-only")
        if allow_criteria_upgrade:
            if source_schema not in (E1_TASK_PROGRESS_SCHEMA, SCHEMA, R3_SCHEMA, R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA):
                raise AtomicFailure("Slice criteria amendment requires E1 schema 14 or 15")
            return max(SCHEMA, source_schema)
        return source_schema if source_schema in (E1_TASK_PROGRESS_SCHEMA, SCHEMA, R3_SCHEMA, R3B_SCHEMA, FEATURE_COMPLETION_SCHEMA, FEATURE_CYCLE_SCHEMA, REQUEST_OBJECTIVE_SCHEMA, SEMANTIC_REQUEST_OBJECTIVE_SCHEMA) else LEGACY_WRITE_SCHEMA

    def __publish_state_locked(
        self, state: State, *, strict_directory: bool = False, allow_e1_upgrade: bool = False,
        allow_criteria_upgrade: bool = False, allow_r3_upgrade: bool = False,
        allow_r3b_upgrade: bool = False, allow_next_slice: bool = False,
        allow_feature_completion: bool = False, allow_feature_reopening: bool = False,
        allow_request_objective: bool = False, allow_d4_activation: bool = False,
        allow_d4_task_progress: bool = False, allow_d4_provisional_slice: bool = False,
        allow_d4_lifecycle: str | None = None,
        allow_d5_adoption: bool = False, allow_d5_task_progress: bool = False,
        allow_d5_lifecycle: str | None = None,
        expected_current: State | None = None,
    ) -> None:
        continuity = _artifact_path(self.__root, "continuity.json")
        if continuity.exists() or continuity.is_symlink():
            raise AtomicFailure(
                "COMPOSED_CONTINUITY_MODE_ACTIVE: legacy state writers are disabled"
            )
        current = self.__check_state_revision(state)
        if expected_current is not None and current != expected_current:
            raise AtomicFailure("State authority changed before scoped publication")
        output_schema = self.__writer_schema_locked(
            current, allow_e1_upgrade=allow_e1_upgrade,
            allow_criteria_upgrade=allow_criteria_upgrade, allow_r3_upgrade=allow_r3_upgrade,
            allow_r3b_upgrade=allow_r3b_upgrade,
            allow_feature_completion=allow_feature_completion,
            allow_feature_reopening=allow_feature_reopening,
            allow_request_objective=allow_request_objective,
            allow_d4_activation=allow_d4_activation,
            allow_d4_task_progress=allow_d4_task_progress,
            allow_d4_provisional_slice=allow_d4_provisional_slice,
            allow_d4_lifecycle=allow_d4_lifecycle,
            allow_d5_adoption=allow_d5_adoption,
            allow_d5_task_progress=allow_d5_task_progress,
            allow_d5_lifecycle=allow_d5_lifecycle,
        )
        if allow_d4_activation and (
            current.provisional_slice_activation is not None
            or state.provisional_slice_activation is None
            or replace(
                state, revision=current.revision,
                provisional_slice_activation=current.provisional_slice_activation,
            ) != current
        ):
            raise AtomicFailure("D4 activation may add only its immutable marker")
        if allow_d4_task_progress and output_schema == D4_PROVISIONAL_SLICE_SCHEMA and (
            replace(state, revision=current.revision, active_context=current.active_context) != current
        ):
            raise AtomicFailure("D4 Task progress may change only active Task context")
        if allow_d4_provisional_slice and (
            output_schema != D4_PROVISIONAL_SLICE_SCHEMA
            or replace(
                state,
                revision=current.revision,
                provisional_slice_revisions=current.provisional_slice_revisions,
                provisional_slice_heads=current.provisional_slice_heads,
            ) != current
        ):
            raise AtomicFailure("D4 proposal may change only its immutable history and current head")
        if allow_d4_lifecycle is not None:
            if output_schema != D4_PROVISIONAL_SLICE_SCHEMA:
                raise AtomicFailure("D4 lifecycle writer requires schema 23")
            _validate_d4_lifecycle_commit(
                self.__root, current, state, allow_d4_lifecycle,
            )
        if allow_d5_adoption and (
            output_schema != D5_PROVISIONAL_SLICE_SCHEMA
            or current.provisional_slice_adoptions
            or len(state.provisional_slice_adoptions) != 1
        ):
            raise AtomicFailure("D5 adoption writer requires its one exact schema conversion")
        if allow_d5_task_progress and output_schema == D5_PROVISIONAL_SLICE_SCHEMA and (
            replace(state, revision=current.revision, active_context=current.active_context) != current
        ):
            raise AtomicFailure("D5 Task progress may change only active Task context")
        if allow_d5_lifecycle is not None:
            if output_schema != D5_PROVISIONAL_SLICE_SCHEMA:
                raise AtomicFailure("D5 lifecycle writer requires schema 24")
            _validate_d5_lifecycle_commit(
                self.__root, current, state, allow_d5_lifecycle,
            )
        if (
            current.provisional_slice_activation != state.provisional_slice_activation
            and not allow_d4_activation
        ):
            raise AtomicFailure("D4 activation marker is immutable")
        if (
            (
                state.provisional_slice_revisions != current.provisional_slice_revisions
                or state.provisional_slice_heads != current.provisional_slice_heads
            )
            and not allow_d4_provisional_slice
        ):
            raise AtomicFailure("provisional Slice history requires its Core writer")
        if state.provisional_slice_adoptions != current.provisional_slice_adoptions and not (
            allow_d5_adoption
            and not current.provisional_slice_adoptions
            and len(state.provisional_slice_adoptions) == 1
        ):
            raise AtomicFailure("D5 adoption history requires its one-time Core writer")
        if current.agreements and state.agreements != current.agreements and not (
            (
                allow_next_slice or allow_d5_adoption
            )
            and state.agreements[:len(current.agreements)] == current.agreements
            and len(state.agreements) == len(current.agreements) + 2
        ):
            raise AtomicFailure("the original accepted E1 agreements cannot be replaced")
        if (current.e1_schema_upgrade != state.e1_schema_upgrade
                and not (allow_e1_upgrade and current.e1_schema_upgrade is None
                         and state.e1_schema_upgrade is not None)):
            raise AtomicFailure("the human E1 conversion receipt is immutable")
        if (current.r3_schema_upgrade != state.r3_schema_upgrade
                and not (allow_r3_upgrade and current.r3_schema_upgrade is None
                         and state.r3_schema_upgrade is not None)):
            raise AtomicFailure("the human R3 conversion receipt is immutable")
        if (current.r3b_schema_upgrade != state.r3b_schema_upgrade
                and not (allow_r3b_upgrade and current.r3b_schema_upgrade is None
                         and state.r3b_schema_upgrade is not None)):
            raise AtomicFailure("the human R3-B conversion receipt is immutable")
        if state.criterion_test_receipts[:len(current.criterion_test_receipts)] != current.criterion_test_receipts:
            raise AtomicFailure("R3 criterion test results are append-only")
        if state.slice_outcome_receipts[:len(current.slice_outcome_receipts)] != current.slice_outcome_receipts:
            raise AtomicFailure("Slice outcome receipts are append-only")
        if state.criterion_coverage_approvals[:len(current.criterion_coverage_approvals)] != current.criterion_coverage_approvals:
            raise AtomicFailure("criterion coverage approvals are append-only")
        if (
            state.feature_completion_receipts[:len(current.feature_completion_receipts)]
            != current.feature_completion_receipts
            or (state.feature_completion_receipts != current.feature_completion_receipts and not allow_feature_completion)
            or (allow_feature_completion and len(state.feature_completion_receipts) != len(current.feature_completion_receipts) + 1)
        ):
            raise AtomicFailure("Feature completion requires exactly one append-only receipt")
        if (
            state.feature_reopen_receipts[:len(current.feature_reopen_receipts)]
            != current.feature_reopen_receipts
            or (state.feature_reopen_receipts != current.feature_reopen_receipts and not allow_feature_reopening)
            or (allow_feature_reopening and len(state.feature_reopen_receipts) != len(current.feature_reopen_receipts) + 1)
        ):
            raise AtomicFailure("Feature reopening requires exactly one append-only receipt")
        if (allow_feature_reopening and replace(
            state, revision=current.revision,
            feature_reopen_receipts=current.feature_reopen_receipts,
        ) != current):
            raise AtomicFailure("Feature reopening cannot modify any earlier authority or work")
        ready = tuple(
            item for item in current.irreversible_authorizations
            if item.status is IrreversibleAuthorizationStatus.READY
        )
        if ready:
            only_authorization_changed = replace(
                state,
                revision=current.revision,
                irreversible_authorizations=current.irreversible_authorizations,
            ) == current
            before = {item.id: item for item in current.irreversible_authorizations}
            after = {item.id: item for item in state.irreversible_authorizations}
            resolved = (
                only_authorization_changed
                and before.keys() == after.keys()
                and all(
                    after[identity] == previous
                    if previous.status is not IrreversibleAuthorizationStatus.READY
                    else after[identity].status in (
                        IrreversibleAuthorizationStatus.CONSUMED,
                        IrreversibleAuthorizationStatus.INVALIDATED,
                    )
                    for identity, previous in before.items()
                )
            )
            if not resolved:
                raise AtomicFailure(
                    "Core mutations are blocked while an irreversible operation is reserved"
                )
        _atomic_replace(
            _artifact_path(self.__root, "state.json"), _state_bytes(state, output_schema), self.__operations,
            strict_directory=strict_directory,
        )

    def __publish_with_immutable(self, state: State, relative: str, data: bytes) -> None:
        """Reject ineligible writers before any auxiliary artifact can be created."""
        with _state_publish_lock(self.__root):
            current = self.__check_state_revision(state)
            self.__writer_schema_locked(current)
            self.__publish_immutable(relative, data)
            self.__publish_state_locked(state)

    def __publish_change(self, state: State, change: Change) -> None:
        self.__publish_with_immutable(state,
            f"records/changes/{change.id}.json",
            encode_json(_change_to_json(change)),
        )

    def __publish_decision(self, state: State, decision: Decision) -> None:
        self.__publish_with_immutable(state,
            f"records/decisions/{decision.id}.json",
            encode_json(_decision_to_json(decision)),
        )

    def __publish_living_spec(
        self, state: State, publication: LivingSpecPublication, content: str
    ) -> None:
        self.__publish_with_immutable(state, publication.path.value, content.encode("utf-8"))

    def __publish_immutable(
        self, relative: str, data: bytes, *, strict_directory: bool = False,
    ) -> None:
        continuity = _artifact_path(self.__root, "continuity.json")
        if continuity.exists() or continuity.is_symlink():
            raise AtomicFailure(
                "COMPOSED_CONTINUITY_MODE_ACTIVE: legacy record writers are disabled"
            )
        destination = _artifact_path(self.__root, relative)
        if destination.exists():
            if destination.read_bytes() != data:
                raise AtomicFailure("immutable artifact already exists with different content")
            if strict_directory:
                try:
                    self.__operations.fsync_directory(destination.parent)
                except OSError as error:
                    raise AtomicFailure("existing immutable record durability is uncertain") from error
            return
        _atomic_replace(destination, data, self.__operations, strict_directory=strict_directory)


__all__ = (
    "AtomicFailure",
    "BinaryFile",
    "DecodeError",
    "FileOperations",
    "IntegrationRegistryError",
    "RecoveryError",
    "StateStore",
    "decode_json",
    "integration_registry_content",
    "integration_registry_path",
    "load_integration_providers",
    "save_integration_providers",
)
