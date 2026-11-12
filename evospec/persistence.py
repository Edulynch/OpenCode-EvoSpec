"""Strict recovery and byte-level atomic persistence."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Final, Protocol, TypeAlias, TypeVar

from .domain import (
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
    validate_state,
)
from .lifecycle import Classification, LifecycleState, Phase, ReviewVerdict
from .discovery import DiscoveryPolicy

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
SCHEMA: Final = 12
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
    )
)
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
    )
)
_CAPABILITY_SELECTION_FIELDS: Final = frozenset(("pending_capability_selection", "capability_selection_history"))
_DISCOVERY_RECOVERY_FIELD: Final = frozenset(("pending_discovery_recovery",))
STATE_FIELDS_V11: Final = STATE_FIELDS - frozenset(("pending_autonomy_expansion", "autonomy_expansion_history"))
STATE_FIELDS_V10: Final = STATE_FIELDS_V11 - frozenset(("irreversible_authorizations",))
STATE_FIELDS_V9_PRE_SELECTION: Final = STATE_FIELDS_V10 - _CAPABILITY_SELECTION_FIELDS - _DISCOVERY_RECOVERY_FIELD
STATE_FIELDS_V9_PRE_RECOVERY: Final = STATE_FIELDS_V10 - _DISCOVERY_RECOVERY_FIELD
STATE_FIELDS_V8: Final = STATE_FIELDS_V10 - frozenset(("active_work_id", "preserved_primary_work_id", "work_items", "work_relations")) - _CAPABILITY_SELECTION_FIELDS - _DISCOVERY_RECOVERY_FIELD | frozenset(("parked_work",))
STATE_FIELDS_V7: Final = STATE_FIELDS_V8 - frozenset(("parked_work",))
STATE_FIELDS_V5: Final = STATE_FIELDS_V7 - frozenset(
    ("locks", "lock_revisions", "lock_releases", "lock_authorizations", "change_lock_assessments")
)
CONTEXT_FIELDS_V9: Final = CONTEXT_FIELDS - frozenset(("autonomy_envelope", "autonomy_metrics"))
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


def _context_to_json(context: WorkingContext | None) -> JsonValue:
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
    return {
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
    }


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
    fields = CONTEXT_FIELDS if schema >= 10 or (isinstance(value, dict) and set(value) == set(CONTEXT_FIELDS)) else CONTEXT_FIELDS_V9
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
    )


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


def _recover_state(root: Path) -> State:
    state_path = _artifact_path(root, "state.json")
    if not state_path.exists():
        return State(0, None)
    try:
        decoded = _read_json(state_path)
        if not isinstance(decoded, dict):
            raise DecodeError("state fields are not exact")
        schema_value = decoded.get("schema")
        schema = _integer(schema_value, "schema")
        if schema in (9, 10):
            raw = _object(decoded, STATE_FIELDS_V10, "state")
        elif schema in (11, SCHEMA):
            decoded_fields = set(decoded)
            if schema == SCHEMA and decoded_fields == set(STATE_FIELDS):
                raw = _object(decoded, STATE_FIELDS, "state")
            elif decoded_fields == set(STATE_FIELDS_V11):
                raw = {
                    "pending_autonomy_expansion": None,
                    "autonomy_expansion_history": [],
                    **_object(decoded, STATE_FIELDS_V11, "state"),
                }
            elif decoded_fields == set(STATE_FIELDS_V10):
                raw = {
                    "irreversible_authorizations": [],
                    "pending_autonomy_expansion": None,
                    "autonomy_expansion_history": [],
                    **_object(decoded, STATE_FIELDS_V10, "state"),
                }
            elif decoded_fields == STATE_FIELDS_V9_PRE_SELECTION:
                raw = {
                    "pending_capability_selection": None,
                    "capability_selection_history": [],
                    "pending_discovery_recovery": None,
                    "irreversible_authorizations": [],
                    "pending_autonomy_expansion": None,
                    "autonomy_expansion_history": [],
                    **_object(decoded, STATE_FIELDS_V9_PRE_SELECTION, "state"),
                }
            elif decoded_fields == STATE_FIELDS_V9_PRE_RECOVERY:
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
            legacy_fields = STATE_FIELDS_V5 if schema == 5 else STATE_FIELDS_V7 if schema in (6, 7) else STATE_FIELDS_V8 if schema == 8 else STATE_FIELDS_V10 if schema == 10 else STATE_FIELDS_V11 if schema == 11 else STATE_FIELDS
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
        if schema not in (5, 6, 7, 8, 9, 10, 11, SCHEMA):
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
        )
        validate_state(state)
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


def _archive_to_json(archive: TerminalArchive) -> JsonValue:
    return {
        "context": _context_to_json(archive.context),
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


def _work_item_to_json(item: WorkItem) -> JsonValue:
    return {
        "id": item.id,
        "operational_state": item.operational_state,
        "context": _context_to_json(item.context),
        "preserved_primary_context": _context_to_json(item.preserved_primary_context),
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


def _state_bytes(state: State) -> bytes:
    value: dict[str, JsonValue] = {
            "schema": SCHEMA,
            "revision": state.revision,
            "active_context": _context_to_json(state.active_context),
            "active_work_id": state.active_work_id,
            "preserved_primary_work_id": state.preserved_primary_work_id,
            "preserved_primary_context": _context_to_json(state.preserved_primary_context),
            "publications": [_publication_to_json(item) for item in state.publications],
            "completed_side_contexts": [_archive_to_json(item) for item in state.completed_side_contexts],
            "features": [_feature_to_json(item) for item in state.features],
            "change_refs": [
                {"id": item.id, "path": f"records/changes/{item.id}.json"}
                for item in state.changes
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
            "terminal_archives": [_archive_to_json(item) for item in state.terminal_archives],
            "publication_history": [
                _publication_to_json(item) for item in state.publication_history
            ],
            "locks": [_lock_to_json(item) for item in state.locks],
            "lock_revisions": [_lock_revision_to_json(item) for item in state.lock_revisions],
            "lock_releases": [_lock_release_to_json(item) for item in state.lock_releases],
            "lock_authorizations": [_lock_authorization_to_json(item) for item in state.lock_authorizations],
            "change_lock_assessments": [_change_lock_assessments_to_json(item) for item in state.change_lock_assessments],
            "work_items": [_work_item_to_json(item) for item in state.work_items],
            "work_relations": [_work_relation_to_json(item) for item in state.work_relations],
    }
    value.update(_extensions_to_json(state))
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


class StateStore:
    """Read-only authoritative state recovery."""

    __slots__ = ("__root",)

    def __init__(self, root: Path) -> None:
        self.__root = _resolved_root(root)

    def load_state(self) -> State:
        return _recover_state(self.__root)



def _atomic_replace(path: Path, data: bytes, operations: FileOperations) -> None:
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
    except OSError:
        return


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
        return _recover_state(self.__root)

    def publish_feature(self, state: State) -> None:
        self.__publish_state(state)

    def publish_lifecycle(self, state: State) -> None:
        self.__publish_state(state)

    def publish_progress(self, state: State) -> None:
        self.__publish_state(state)

    def publish_safety_facts(self, state: State) -> None:
        self.__publish_state(state)

    def publish_review(self, state: State) -> None:
        self.__publish_state(state)

    def publish_verification(self, state: State) -> None:
        self.__publish_state(state)

    def publish_convergence(self, state: State) -> None:
        self.__publish_state(state)

    def publish_repair(self, state: State) -> None:
        self.__publish_state(state)

    def publish_terminal_archive(self, state: State) -> None:
        self.__publish_state(state)

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
        self.__publish_immutable(
            f"records/observations/{observation.id}.json",
            encode_json(_observation_to_json(observation)),
        )
        self.__publish_state(state)

    def publish_drift(self, state: State, drift: Drift) -> None:
        self.__publish_immutable(
            f"records/observations/{drift.id}.json",
            encode_json(_drift_to_json(drift)),
        )
        self.__publish_state(state)

    def publish_primary_convergence(
        self, state: State, publication: LivingSpecPublication, content: str
    ) -> None:
        self.__publish_living_spec(state, publication, content)

    def publish_side_convergence(
        self, state: State, publication: LivingSpecPublication, content: str
    ) -> None:
        self.__publish_living_spec(state, publication, content)

    def __publish_state(self, state: State) -> None:
        with _state_publish_lock(self.__root):
            current = _recover_state(self.__root)
            if state.revision != current.revision + 1:
                raise AtomicFailure("state revision changed before publication")
            ready = tuple(
                item
                for item in current.irreversible_authorizations
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
                        else after[identity].status
                        in (
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
                _artifact_path(self.__root, "state.json"),
                _state_bytes(state),
                self.__operations,
            )

    def __publish_change(self, state: State, change: Change) -> None:
        self.__publish_immutable(
            f"records/changes/{change.id}.json",
            encode_json(_change_to_json(change)),
        )
        self.__publish_state(state)

    def __publish_decision(self, state: State, decision: Decision) -> None:
        self.__publish_immutable(
            f"records/decisions/{decision.id}.json",
            encode_json(_decision_to_json(decision)),
        )
        self.__publish_state(state)

    def __publish_living_spec(
        self, state: State, publication: LivingSpecPublication, content: str
    ) -> None:
        self.__publish_immutable(publication.path.value, content.encode("utf-8"))
        self.__publish_state(state)

    def __publish_immutable(self, relative: str, data: bytes) -> None:
        destination = _artifact_path(self.__root, relative)
        if destination.exists():
            if destination.read_bytes() != data:
                raise AtomicFailure("immutable artifact already exists with different content")
            return
        _atomic_replace(destination, data, self.__operations)


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
