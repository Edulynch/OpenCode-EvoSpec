"""Operation-specific validated Core Bootstrap mutations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import platform
import sys
import stat
import unicodedata
from typing import TYPE_CHECKING, Never, final

from .domain import (
    AgreementRecord,
    SliceCriterion,
    SliceCriteriaRevision,
    SliceCriteriaHead,
    IDENTITY,
    AssessmentOperation,
    AutonomyAuthorization,
    AutonomyEnvelope,
    AutonomyEnvelopeDelta,
    AutonomyExpansionDecision,
    AutonomyExpansionId,
    AutonomyExpansionRecord,
    AutonomyEvaluation,
    AutonomyMetrics,
    GitCommitRewrite,
    GitHistoryRewritePolicy,
    IrreversibleAuthorization,
    IrreversibleAuthorizationId,
    IrreversibleAuthorizationStatus,
    IrreversibleOperationKind,
    CapabilityState,
    Change,
    ChangeId,
    ChangeLockAssessments,
    Checkpoint,
    ConvergenceAssessment,
    Decision,
    DecisionId,
    DeferredLinkedIntent,
    DeferredSideRequest,
    Drift,
    ExpectedRevision,
    ExperimentOutcome,
    Feature,
    FeatureId,
    LivingSpecId,
    LivingSpecPublication,
    LockAssessment,
    LockAuthorization,
    LockRelease,
    Observation,
    ObservationId,
    PendingIntake,
    PendingAutonomyExpansion,
    PendingCapabilitySelection,
    PendingDiscoveryRecovery,
    CapabilityCandidate,
    CapabilitySelection,
    CapabilitySelectionId,
    ParkedWork,
    OperationalState,
    RepositoryInitialization,
    RepositoryPath,
    ReviewEvidence,
    ReviewFinding,
    ReviewInspection,
    ReviewResult,
    SafetyFacts,
    SemanticLock,
    SemanticLockRevision,
    SharedIntent,
    State,
    StateValidationError,
    E1SchemaUpgradeReceipt,
    R3SchemaUpgradeReceipt,
    R3BSchemaUpgradeReceipt,
    CriterionTestReceipt,
    CoverageInput,
    CriterionCoverageApproval,
    CriterionCoverageDeclaration,
    SliceOutcomeReceipt,
    TaskProgress,
    TerminalArchive,
    VerificationEvidence,
    WorkingContext,
    WorkId,
    WorkItem,
    WorkRelation,
    WorkRelationKind,
    inspect,
    coverage_declarations_json,
    coverage_semantic_digest,
    coverage_set_digest,
    slice_outcome_semantic_id,
    is_git_branch_ref,
    is_git_identity,
    is_git_sha,
    git_rewrite_plan_digest,
    apply_autonomy_envelope_delta,
    autonomy_envelope_is_narrowing,
    autonomy_operation_is_forbidden,
    canonical_autonomy_operation,
    repository_path_is_within,
    validate_autonomy_envelope,
    validate_autonomy_envelope_delta,
    validate_state,
)
from .discovery import DiscoveryPolicy
from .lifecycle import (
    TERMINAL_STATES,
    Classification,
    LifecycleError,
    LifecycleState,
    Phase,
    advance,
    can_converge,
    has_acceptable_review,
)
from .persistence import AtomicFailure, FileOperations, R3_SCHEMA, R3B_SCHEMA, _StateStore
from .input_fingerprint import (
    COVERAGE_CONTRACT_VERSION,
    INPUT_FINGERPRINT_VERSION,
    INPUT_POLICY_DIGEST,
    INPUT_POLICY_ID,
    InputFingerprintError,
    InputSnapshot,
    R3B_APPROVAL_POLICY_TEXT,
    TEST_ADMISSION_POLICY_DIGEST,
    TEST_ADMISSION_POLICY_ID,
    TEST_ENVIRONMENT_PROFILE_ID,
    TEST_ENVIRONMENT_PROFILE_DIGEST,
    sanitized_test_environment,
    test_environment_profile_digest,
)

if TYPE_CHECKING:
    from .ports import StatePersistence


class ServiceError(Exception):
    """Raised when an explicit caller instruction conflicts with authority."""


class ServiceConstructionError(TypeError):
    """Raised when callers bypass the supported open path."""


@dataclass(frozen=True, slots=True)
class UserAcceptance:
    """Acceptance evidence; the CLI must obtain it from a human terminal prompt."""

    statement: str
    source_reference: str
    source: str = "INTERACTIVE_TERMINAL"


@dataclass(frozen=True, slots=True)
class SliceCriteriaSpec:
    """Human-readable Slice bounds, before Core assigns an immutable identity."""

    outcome: str
    example: str
    inclusions: tuple[str, ...]
    exclusions: tuple[str, ...]
    decisions: tuple[str, ...]
    criteria: tuple[SliceCriterion, ...]


@dataclass(frozen=True, slots=True)
class SliceOutcomeEligibility:
    status: str
    latest_receipt_id: str | None
    blockers: tuple[str, ...]


_PYTHON_TEST_RUNNER = (
    "import sys,unittest; root,module=sys.argv[1:3]; "
    "sys.path.insert(0,root); unittest.main(module=module,argv=[sys.argv[0]])"
)
_PYTHON_TEST_TARGET = re.compile(r"tests(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z")
_NODE_TEST_TARGET = re.compile(r"tests/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.test\.mjs\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _supported_test_descriptor(kind: str, target: str) -> bool:
    return (
        kind == "python-unittest" and _PYTHON_TEST_TARGET.fullmatch(target) is not None
        or kind == "node-test" and _NODE_TEST_TARGET.fullmatch(target) is not None
    )


def _runtime_digest(path: str) -> str:
    try:
        executable = Path(path)
        if not executable.is_absolute() or str(executable.resolve(strict=True)) != path:
            raise ServiceError("R3-B runtime executable is not a canonical absolute path")
        before = executable.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or bool(getattr(before, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > 1_073_741_824
            or (os.name != "nt" and not before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        ):
            raise ServiceError("R3-B runtime executable is not a bounded regular file")
        digest = sha256()
        with executable.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or bool(getattr(opened, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
                or opened.st_nlink != 1
            ):
                raise ServiceError("R3-B runtime executable became unsafe during verification")
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        current = executable.lstat()
    except (OSError, RuntimeError) as error:
        raise ServiceError("R3-B runtime executable cannot be verified") from error
    if any(
        left.st_ino != right.st_ino or left.st_dev != right.st_dev
        or left.st_size != right.st_size or left.st_mtime_ns != right.st_mtime_ns
        or left.st_ctime_ns != right.st_ctime_ns
        or getattr(left, "st_file_attributes", 0) != getattr(right, "st_file_attributes", 0)
        for left, right in ((before, opened), (opened, after), (after, current))
    ):
        raise ServiceError("R3-B runtime executable changed during verification")
    return digest.hexdigest()


def _runtime_version(path: str, root: Path) -> str:
    try:
        result = subprocess.run(
            [path, "--version"],
            cwd=root,
            env=sanitized_test_environment(root, path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    except (InputFingerprintError, OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ServiceError("R3-B runtime version cannot be verified") from error
    version = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or not version or len(version) > 256:
        raise ServiceError("R3-B runtime version cannot be verified")
    return version


def _drift_identity_digest(state: State, change_id: ChangeId | None) -> str:
    relevant = tuple(sorted(
        (
            str(item.id), str(item.expected_publication), item.observed, item.unresolved,
        )
        for item in state.drifts if item.change_id is None or item.change_id == change_id
    ))
    return sha256(json.dumps(
        relevant, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _slice_criteria_json(spec: SliceCriteriaSpec) -> str:
    """Use one canonical representation for the terminal prompt and Core proof."""
    return json.dumps({
        "outcome": spec.outcome, "example": spec.example,
        "inclusions": list(spec.inclusions), "exclusions": list(spec.exclusions),
        "decisions": list(spec.decisions),
        "criteria": [{"id": item.id, "expected_result": item.expected_result,
                      "expected_evidence": item.expected_evidence} for item in spec.criteria],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_slice_criteria_spec(spec: SliceCriteriaSpec) -> None:
    """Reject partial agreements and unbounded specifications before user consent."""
    if type(spec) is not SliceCriteriaSpec:
        raise ServiceError("Slice criteria must use the canonical specification shape")
    if any(type(value) is not str or not value.strip() or len(value) > 2048
           for value in (spec.outcome, spec.example)):
        raise ServiceError("Slice outcome and example must be bounded nonblank text")
    for name, values in (("inclusions", spec.inclusions), ("exclusions", spec.exclusions),
                         ("decisions", spec.decisions), ("criteria", spec.criteria)):
        if type(values) is not tuple or not 1 <= len(values) <= 32:
            raise ServiceError(f"Slice {name} must contain 1 to 32 entries")
    if any(type(value) is not str or not value.strip() or len(value) > 2048
           for value in spec.inclusions + spec.exclusions + spec.decisions):
        raise ServiceError("Slice scope and decisions must be bounded nonblank text")
    identifiers: set[str] = set()
    for criterion in spec.criteria:
        # Criteria may come from JSON: reject non-text IDs before using the regex.
        if (type(criterion) is not SliceCriterion or type(criterion.id) is not str
                or not IDENTITY.fullmatch(criterion.id)):
            raise ServiceError("Slice criterion must have a stable ID")
        if criterion.id in identifiers:
            raise ServiceError("Slice criterion IDs must be unique")
        identifiers.add(criterion.id)
        if any(type(value) is not str or not value.strip() or len(value) > 2048
               for value in (criterion.expected_result, criterion.expected_evidence)):
            raise ServiceError("Slice criterion result and expected evidence must be bounded text")
    if len(_slice_criteria_json(spec)) > 32768:
        raise ServiceError("Slice criteria specification exceeds the acceptance size limit")


def slice_acceptance_statement(
    feature_title: str, slice_title: str, initial_task: str, checkpoint: str,
    criteria: SliceCriteriaSpec | None = None,
) -> str:
    """Bind the initial TTY acceptance to exactly the agreed Slice specification."""
    statement = (
        f"Acepto Feature: {feature_title}; Slice: {slice_title}; "
        f"Task: {initial_task}; checkpoint: {checkpoint}"
    )
    if criteria is not None:
        validate_slice_criteria_spec(criteria)
        if any(value != value.strip() for value in (
            feature_title, slice_title, initial_task, checkpoint,
        )):
            raise ServiceError("criteria acceptance requires canonical unpadded Feature, Slice, Task and checkpoint")
        statement += f"; criterios: {_slice_criteria_json(criteria)}"
    return statement


def next_slice_acceptance_statement(
    feature_id: str, feature_title: str, slice_title: str, initial_task: str,
    checkpoint: str, criteria: SliceCriteriaSpec,
) -> str:
    """Present the existing Feature identity and the entire new Slice for consent."""
    validate_slice_criteria_spec(criteria)
    values = (feature_id, feature_title, slice_title, initial_task, checkpoint)
    if any(type(value) is not str or not value or value != value.strip() or len(value) > 2048 for value in values):
        raise ServiceError("next Slice acceptance requires canonical bounded Feature and Slice fields")
    return (
        f"Acepto siguiente Slice de Feature-ID: {feature_id}; Feature: {feature_title}; "
        f"Slice: {slice_title}; Task: {initial_task}; checkpoint: {checkpoint}; "
        f"criterios: {_slice_criteria_json(criteria)}"
    )


def slice_criteria_decision_statement(
    previous_revision_id: str, reason: str, criteria: SliceCriteriaSpec,
) -> str:
    """An amendment is a separate human decision, never renewed acceptance."""
    validate_slice_criteria_spec(criteria)
    if type(reason) is not str or not reason.strip() or len(reason) > 2048:
        raise ServiceError("criteria amendment requires a bounded explicit reason")
    return f"Decido revisar criterios desde {previous_revision_id}; motivo: {reason}; criterios: {_slice_criteria_json(criteria)}"


@dataclass(frozen=True, slots=True)
class StaleRevisionError(ServiceError):
    """Raised when a mutation was based on an obsolete state revision."""

    expected_revision: int
    actual_revision: int

    def __str__(self) -> str:
        return (
            f"expected state revision {self.expected_revision}, "
            f"but authority is revision {self.actual_revision}"
        )


class DurabilityUncertainError(ServiceError):
    """The state replacement may have committed; recovery must precede retry."""


def _accepted_e1_scope(state: State, root: Path) -> tuple[WorkingContext, AgreementRecord, AgreementRecord]:
    """Resolve a single accepted primary Task without trusting caller-provided scope."""
    context = state.active_context
    initialization = state.initialization
    root_identity = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    if initialization is None or initialization.repository_id != root_identity:
        raise ServiceError("E1 repository authority does not match the exact local root")
    if (context is None or context.side_of is not None or context.change_id is None
            or state.preserved_primary_context is not None or state.preserved_primary_work_id is not None
            or state.parked_work is not None or state.pending_intake is not None
            or state.deferred_side_request is not None or len(state.agreements) < 2
            or any(item.context is not None and (
                item.context.feature_agreement_ref is None or item.context.slice_agreement_ref is None
            ) for item in state.work_items)):
        raise ServiceError("E1 Task progress requires one active primary accepted Slice")
    feature = next((item for item in state.agreements if item.id == context.feature_agreement_ref), None)
    slice_record = next((item for item in state.agreements if item.id == context.slice_agreement_ref), None)
    if (feature is None or slice_record is None or feature.kind != "FEATURE" or slice_record.kind != "SLICE"
            or feature.change_id != context.change_id or slice_record.change_id != context.change_id
            or feature.feature_id != context.feature_id or slice_record.feature_id != context.feature_id
            or feature.repository_id != root_identity or slice_record.repository_id != root_identity
            or not slice_record.task_id or not slice_record.initial_task or not slice_record.checkpoint
            or context.current_work != slice_record.initial_task or context.focus != slice_record.title):
        raise ServiceError("E1 Task progress cannot expand or replace the human-approved scope")
    return context, feature, slice_record


def _normalize_coverage_declarations(
    state: State,
    root: Path,
    snapshot: InputSnapshot,
    declarations: tuple[CriterionCoverageDeclaration, ...],
) -> tuple[CriterionCoverageDeclaration, ...]:
    if type(declarations) is not tuple or any(
        type(item) is not CriterionCoverageDeclaration
        or type(item.criterion_id) is not str
        or type(item.test_kind) is not str
        or type(item.test_target) is not str
        or type(item.known_inputs) is not tuple
        or any(
            type(known) is not CoverageInput
            or type(known.kind) is not str
            or type(known.path) is not str
            for known in item.known_inputs
        )
        for item in declarations
    ):
        raise ServiceError("coverage declarations must be an explicit immutable tuple")
    context, _, accepted = _accepted_e1_scope(state, root)
    del context
    head = next(
        (item for item in state.slice_criteria_heads if item.slice_agreement_ref == accepted.id),
        None,
    )
    criteria = None if head is None else next(
        (item for item in state.slice_criteria_revisions if item.id == head.revision_id),
        None,
    )
    if criteria is None or head is None:
        raise ServiceError("coverage approval requires the current accepted criteria head")
    if (
        type(snapshot) is not InputSnapshot
        or snapshot.repository_id != accepted.repository_id
        or snapshot.policy_id != INPUT_POLICY_ID
        or snapshot.policy_digest != INPUT_POLICY_DIGEST
    ):
        raise ServiceError("coverage approval snapshot differs from the current root policy")
    expected = {item.id: item for item in criteria.criteria}
    if (
        len(declarations) != len(expected)
        or len({item.criterion_id for item in declarations}) != len(declarations)
    ):
        raise ServiceError("coverage approval must explicitly declare every current criterion once")
    files = set(snapshot.file_paths)
    directories = set(snapshot.directory_paths)
    normalized: list[CriterionCoverageDeclaration] = []
    for item in declarations:
        criterion = expected.get(item.criterion_id)
        if (
            criterion is None
            or criterion.expected_evidence != f"{item.test_kind}:{item.test_target}"
            or not _supported_test_descriptor(item.test_kind, item.test_target)
            or len(item.known_inputs) > 1024
        ):
            raise ServiceError("coverage declaration differs from the accepted test descriptor")
        seen: set[tuple[str, str]] = set()
        inputs: list[CoverageInput] = []
        for known in item.known_inputs:
            if (
                type(known) is not CoverageInput
                or type(known.kind) is not str
                or type(known.path) is not str
            ):
                raise ServiceError("known inputs must declare only exact root files or directories")
            parts = known.path.split("/")
            if (
                known.kind not in ("root-file", "root-directory")
                or not known.path or len(known.path) > 1024
                or "\\" in known.path or ":" in known.path
                or known.path.startswith("/")
                or any(part in ("", ".", "..") for part in parts)
                or parts[0].casefold() in (".git", ".evospec")
                or unicodedata.normalize("NFC", known.path) != known.path
                or (known.kind, known.path) in seen
            ):
                raise ServiceError("known-input declaration contains an unsupported root path")
            present = known.path in (files if known.kind == "root-file" else directories)
            if not present:
                raise ServiceError("known-input declaration path is absent or has the wrong type")
            seen.add((known.kind, known.path))
            inputs.append(known)
        normalized.append(CriterionCoverageDeclaration(
            item.criterion_id,
            item.test_kind,
            item.test_target,
            tuple(sorted(inputs, key=lambda value: (value.kind, value.path))),
        ))
    result = tuple(sorted(normalized, key=lambda value: value.criterion_id))
    if len(coverage_declarations_json(result)) > 32768:
        raise ServiceError("known-input coverage proposal exceeds the fixed display limit")
    return result


def _coverage_contract_digest(
    declarations: tuple[CriterionCoverageDeclaration, ...],
) -> str:
    return sha256(coverage_declarations_json(declarations).encode("utf-8")).hexdigest()


def _coverage_approvals(
    state: State,
    root: Path,
    snapshot: InputSnapshot,
    declarations: tuple[CriterionCoverageDeclaration, ...],
    statement: str,
    source_reference: str,
    source_revision: int,
    recorded_revision: int,
) -> tuple[CriterionCoverageApproval, ...]:
    _, _, accepted = _accepted_e1_scope(state, root)
    head = next(
        (item for item in state.slice_criteria_heads if item.slice_agreement_ref == accepted.id),
        None,
    )
    if head is None:
        raise ServiceError("coverage approval requires a current criteria head")
    approvals: list[CriterionCoverageApproval] = []
    for declaration in declarations:
        approval = CriterionCoverageApproval(
            "",
            "",
            accepted.repository_id,
            accepted.id,
            head.revision_id,
            declaration.criterion_id,
            declaration.test_kind,
            declaration.test_target,
            declaration.known_inputs,
            snapshot.fingerprint,
            INPUT_POLICY_ID,
            INPUT_POLICY_DIGEST,
            TEST_ADMISSION_POLICY_ID,
            TEST_ADMISSION_POLICY_DIGEST,
            TEST_ENVIRONMENT_PROFILE_ID,
            TEST_ENVIRONMENT_PROFILE_DIGEST,
            statement,
            source_reference,
            "INTERACTIVE_TERMINAL",
            source_revision,
            recorded_revision,
        )
        digest = coverage_semantic_digest(approval)
        approvals.append(replace(
            approval,
            id=f"coverage-{digest[:20]}",
            contract_digest=digest,
        ))
    return tuple(approvals)


def _validated(candidate: State, message: str) -> State:
    try:
        validate_state(candidate)
    except StateValidationError as error:
        raise ServiceError(message) from error
    return candidate


def _evaluate_autonomy(
    context: WorkingContext,
    action: str,
    paths: tuple[RepositoryPath, ...],
    irreversible: bool,
) -> AutonomyEvaluation:
    tokens = set(re.findall(r"[A-Z0-9]+", action.upper()))
    protected_action = autonomy_operation_is_forbidden(action) or (
        "FILTER" in tokens and ("BRANCH" in tokens or "REPO" in tokens)
    )
    if protected_action:
        return AutonomyEvaluation(
            AutonomyAuthorization.HUMAN_MATERIAL_DECISION,
            "force operations, history rewrites, destructive resets, tags, and releases cannot be granted by an autonomy envelope",
        )
    envelope = context.autonomy_envelope
    operation = canonical_autonomy_operation(action)
    if operation not in {canonical_autonomy_operation(item) for item in envelope.reversible_actions}:
        return AutonomyEvaluation(AutonomyAuthorization.HUMAN_MATERIAL_DECISION, "operation is outside the reversible authority")
    if irreversible and operation not in {
        canonical_autonomy_operation(item) for item in envelope.irreversible_permissions
    }:
        return AutonomyEvaluation(AutonomyAuthorization.HUMAN_MATERIAL_DECISION, "irreversible operation is not permitted")
    if any(not any(repository_path_is_within(path, root) for root in envelope.allowed_paths) for path in paths):
        return AutonomyEvaluation(AutonomyAuthorization.HUMAN_MATERIAL_DECISION, "canonical path expansion requires a human material decision")
    if envelope.operation_budget and context.autonomy_metrics.automatic_operations >= envelope.operation_budget:
        return AutonomyEvaluation(AutonomyAuthorization.HUMAN_MATERIAL_DECISION, "automatic operation budget is exhausted")
    return AutonomyEvaluation(AutonomyAuthorization.ALLOWED, "operation remains inside the autonomy envelope")


def _initial_context(change: Change, side_of: FeatureId | None = None) -> WorkingContext:
    return WorkingContext(
        change.feature_id,
        change.id,
        LifecycleState.PROPOSED,
        Phase.RESEARCH,
        None,
        None,
        (),
        None,
        (),
        (),
        (),
        None,
        (),
        VerificationEvidence((), False),
        False,
        change.classification,
        0,
        None,
        (change.id,),
        (),
        side_of=side_of,
        autonomy_envelope=AutonomyEnvelope(
            change.intent,
            reversible_actions=("progress", "assess_locks", "review", "verify"),
        ),
    )


def _record_feature(current: State, feature: Feature) -> State:
    return _validated(
        replace(current, features=current.features + (feature,)),
        "feature record was rejected",
    )


def _record_change(current: State, change: Change) -> State:
    if change.lock_assessments:
        raise ServiceError("lock assessments require the assessment operation")
    feature = next((item for item in current.features if item.id == change.feature_id), None)
    if feature is None or feature.side_of is not None:
        raise ServiceError("primary change requires an authoritative primary feature")
    if current.active_context is not None or current.preserved_primary_context is not None:
        raise ServiceError("primary change requires no active work")
    context = _initial_context(change)
    return _validated(
        replace(
            current,
            active_context=context,
            changes=current.changes + (change,),
            active_work_id=_work_id(context),
        ),
        "change record was rejected",
    )


def _activate_side(current: State, change: Change) -> State:
    primary = current.active_context
    feature = next((item for item in current.features if item.id == change.feature_id), None)
    if (
        primary is None
        or primary.side_of is not None
        or current.preserved_primary_context is not None
    ):
        raise ServiceError("side work requires one active primary context")
    if feature is None or feature.side_of != primary.feature_id:
        raise ServiceError("side change must belong to the active primary")
    context = _initial_context(change, feature.side_of)
    return _validated(
        replace(
            current,
            active_context=context,
            preserved_primary_context=primary,
            changes=current.changes + (change,),
            active_work_id=_work_id(context),
            preserved_primary_work_id=current.active_work_id,
        ),
        "side change was rejected",
    )


def _record_decision(current: State, decision: Decision) -> State:
    if decision.supersedes is not None:
        raise ServiceError("decision supersession requires the supersession operation")
    active = current.active_context
    if active is not None and (
        decision.feature_id == active.feature_id
        and (decision.change_id is None or decision.change_id in active.change_ids)
    ):
        active = replace(
            active,
            decision_ids=(
                active.decision_ids + (decision.id,)
                if decision.kind == "clarification"
                else active.decision_ids
            ),
            constraint_ids=(
                active.constraint_ids + (decision.id,)
                if decision.kind == "constraint"
                else active.constraint_ids
            ),
        )
    return _validated(
        replace(
            current,
            active_context=active,
            decisions=current.decisions + (decision,),
        ),
        "decision record was rejected",
    )


def _supersede_decision(current: State, replacement: Decision) -> State:
    if replacement.supersedes is None:
        raise ServiceError("replacement must identify the superseded decision")
    prior = next(
        (item for item in current.decisions if item.id == replacement.supersedes), None
    )
    if prior is None or any(
        item.supersedes == replacement.supersedes for item in current.decisions
    ):
        raise ServiceError("superseded decision is not current authority")
    preserved = current.preserved_primary_context
    if preserved is not None and prior.id in (
        preserved.decision_ids + preserved.constraint_ids
    ):
        raise ServiceError("preserved primary decisions cannot change during side work")

    def update(context: WorkingContext | None) -> WorkingContext | None:
        if context is None:
            return None
        return replace(
            context,
            decision_ids=tuple(
                replacement.id if item == prior.id else item
                for item in context.decision_ids
            ),
            constraint_ids=tuple(
                replacement.id if item == prior.id else item
                for item in context.constraint_ids
            ),
        )

    return _validated(
        replace(
            current,
            active_context=update(current.active_context),
            decisions=current.decisions + (replacement,),
        ),
        "decision supersession was rejected",
    )


def _record_observation(current: State, observation: Observation) -> State:
    return _validated(
        replace(current, observations=current.observations + (observation,)),
        "observation record was rejected",
    )


def _record_drift(current: State, drift: Drift) -> State:
    return _validated(
        replace(current, drifts=current.drifts + (drift,)),
        "drift record was rejected",
    )


def _record_context(current: State, context: WorkingContext) -> State:
    active = current.active_context
    if active is None:
        raise ServiceError("context transition requires active work")
    if (active.feature_id, active.change_id, active.side_of) != (
        context.feature_id,
        context.change_id,
        context.side_of,
    ):
        raise ServiceError("recorded context cannot replace active work identity")
    if context.publication != active.publication:
        raise ServiceError("context transition cannot change publication identity")
    try:
        advance(
            active.status,
            active.phase,
            context.status,
            context.phase,
            context.unresolved_material_decision,
            context.review,
            context.verification,
            context.convergence_complete,
        )
    except LifecycleError as error:
        raise ServiceError("context lifecycle transition is invalid") from error
    if context.status in TERMINAL_STATES:
        raise ServiceError("terminal work must be archived or published")
    return _validated(
        replace(current, active_context=context),
        "context transition was rejected",
    )


def _work_id(context: WorkingContext) -> WorkId:
    if context.change_id is None:
        raise ServiceError("work queue requires a change identity")
    return WorkId(f"work-{context.change_id}")


def _park_state(reason: str) -> OperationalState:
    return OperationalState.BLOCKED if "external" in reason.lower() else OperationalState.FROZEN


def _park(current: State, reason: str) -> State:
    active = current.active_context
    if active is None or current.active_work_id is None:
        raise ServiceError("parking requires one active work slot")
    active = replace(
        active,
        autonomy_metrics=replace(
            active.autonomy_metrics,
            operational_handoffs=active.autonomy_metrics.operational_handoffs + 1,
        ),
    )
    item = WorkItem(
        current.active_work_id,
        _park_state(reason),
        active,
        current.preserved_primary_context,
        current.deferred_side_request,
        current.pending_intake,
        reason,
        datetime.now(timezone.utc).isoformat(),
    )
    return _validated(
        replace(
            current,
            active_context=None,
            preserved_primary_context=None,
            deferred_side_request=None,
            pending_intake=None,
            active_work_id=None,
            preserved_primary_work_id=None,
            work_items=current.work_items + (item,),
        ),
        "work could not be parked",
    )


def _resume_parked(current: State, change_id: str) -> State:
    item = next((item for item in current.work_items if str(item.id) == change_id), None)
    if item is None or current.active_context is not None or current.preserved_primary_context is not None:
        raise ServiceError("resuming requires an empty active slot and queued work")
    if item.operational_state not in (OperationalState.READY, OperationalState.BLOCKED, OperationalState.FROZEN):
        raise ServiceError("work item is not activatable")
    if _activation_blocker(current, item) is not None:
        raise ServiceError("work item cannot safely activate")
    if item.context is None:
        raise ServiceError("queued work lacks lifecycle context")
    context = replace(
        item.context,
        autonomy_metrics=replace(
            item.context.autonomy_metrics,
            operational_handoffs=item.context.autonomy_metrics.operational_handoffs + 1,
        ),
    )
    return _validated(
        replace(
            current,
            active_context=context,
            preserved_primary_context=item.preserved_primary_context,
            deferred_side_request=item.deferred_side_request,
            pending_intake=item.pending_intake,
            active_work_id=item.id,
            preserved_primary_work_id=(None if item.preserved_primary_context is None else _work_id(item.preserved_primary_context)),
            work_items=tuple(candidate for candidate in current.work_items if candidate.id != item.id),
        ),
        "parked work could not be resumed",
    )


def _activate_work(current: State, work_id: str) -> State:
    item = next((item for item in current.work_items if str(item.id) == work_id), None)
    if item is None or current.active_context is not None or current.preserved_primary_context is not None:
        raise ServiceError("activation requires an empty active slot and queued work")
    if item.operational_state is not OperationalState.READY:
        raise ServiceError("strict activation requires ready work")
    if _activation_blocker(current, item) is not None:
        raise ServiceError("work item cannot safely activate")
    if item.context is None:
        raise ServiceError("queued work lacks lifecycle context")
    return _validated(
        replace(
            current,
            active_context=item.context,
            preserved_primary_context=item.preserved_primary_context,
            deferred_side_request=item.deferred_side_request,
            pending_intake=item.pending_intake,
            active_work_id=item.id,
            preserved_primary_work_id=(None if item.preserved_primary_context is None else _work_id(item.preserved_primary_context)),
            work_items=tuple(candidate for candidate in current.work_items if candidate.id != item.id),
        ),
        "queued work could not be activated",
    )


def _activation_blocker(current: State, item: WorkItem) -> str | None:
    if item.context is None or item.context.unresolved_material_decision:
        return "material clarification remains unresolved"
    if any(drift.unresolved and drift.change_id == item.context.change_id for drift in current.drifts):
        return "unresolved drift requires explicit reconciliation"
    change = next((change for change in current.changes if change.id == item.context.change_id), None)
    if change is None:
        return "queued work change is unavailable"
    lock_blocker = _work_item_lock_gate(current, item, change)
    if lock_blocker is not None:
        return lock_blocker
    ids = {candidate.id: candidate for candidate in current.work_items}
    for relation in current.work_relations:
        if relation.kind is WorkRelationKind.HARD and relation.successor_id == item.id:
            predecessor = ids.get(relation.predecessor_id)
            if predecessor is None or predecessor.operational_state is not OperationalState.DONE:
                return "hard dependency is not accepted"
        if relation.kind is WorkRelationKind.CONFLICT and item.id in (relation.predecessor_id, relation.successor_id):
            counterpart = relation.successor_id if relation.predecessor_id == item.id else relation.predecessor_id
            if counterpart == current.active_work_id:
                return "conflicting work is active"
    return None


@dataclass(frozen=True, slots=True)
class NextActionable:
    code: str
    work_id: WorkId | None = None
    candidates: tuple[WorkId, ...] = ()


def _next_actionable(current: State) -> NextActionable:
    if current.active_context is not None:
        return NextActionable("ACTIVE")
    eligible = tuple(
        item.id for item in current.work_items
        if item.operational_state is OperationalState.READY and _activation_blocker(current, item) is None
    )
    if not eligible:
        return NextActionable("NONE")
    if len(eligible) == 1:
        return NextActionable("SELECTED", eligible[0])
    return NextActionable("AMBIGUOUS", candidates=eligible)


def _terminal_archive(
    active: WorkingContext, terminal: WorkingContext
) -> TerminalArchive:
    expected = replace(active, status=terminal.status, phase=terminal.phase, paused_pair=None)
    if terminal != expected or terminal.status not in TERMINAL_STATES:
        raise ServiceError("terminal context must exactly complete current active context")
    try:
        advance(
            active.status,
            active.phase,
            terminal.status,
            terminal.phase,
            terminal.unresolved_material_decision,
            terminal.review,
            terminal.verification,
            terminal.convergence_complete,
        )
    except LifecycleError as error:
        raise ServiceError("terminal lifecycle transition is invalid") from error
    return TerminalArchive(expected, expected.side_of)


def _terminal_relations(current: State, terminal: WorkingContext) -> tuple[WorkRelation, ...]:
    if terminal.status is LifecycleState.ACCEPTED or current.active_work_id is None:
        return current.work_relations
    return tuple(
        relation
        for relation in current.work_relations
        if current.active_work_id not in (relation.predecessor_id, relation.successor_id)
    )


def _archive_terminal(current: State, terminal: WorkingContext) -> State:
    active = current.active_context
    if active is None or current.preserved_primary_context is not None:
        raise ServiceError("primary terminal archive requires active primary work")
    archive = _terminal_archive(active, terminal)
    done = () if terminal.status is not LifecycleState.ACCEPTED or current.active_work_id is None else (
        WorkItem(current.active_work_id, OperationalState.DONE, None, None, None, None, None,
                 datetime.now(timezone.utc).isoformat(), terminal.change_id),
    )
    return _validated(
        replace(
            current,
            active_context=None,
            terminal_archives=current.terminal_archives + (archive,),
            active_work_id=None,
            work_items=current.work_items + done,
            work_relations=_terminal_relations(current, terminal),
        ),
        "terminal archive was rejected",
    )


def _restore_primary(current: State, completed_side: WorkingContext) -> State:
    if current.active_context is None or current.preserved_primary_context is None:
        raise ServiceError("no active side and preserved primary exist")
    archive = _terminal_archive(current.active_context, completed_side)
    return _validated(
        replace(
            current,
            active_context=current.preserved_primary_context,
            preserved_primary_context=None,
            active_work_id=current.preserved_primary_work_id,
            preserved_primary_work_id=None,
            completed_side_contexts=current.completed_side_contexts + (archive,),
            terminal_archives=current.terminal_archives + (archive,),
        ),
        "side restoration was rejected",
    )


def _complete_side(current: State, completed_side: WorkingContext) -> State:
    if current.active_context is None or current.preserved_primary_context is None:
        raise ServiceError("no active side and preserved primary exist")
    archive = _terminal_archive(current.active_context, completed_side)
    request = current.deferred_side_request
    intake = None if request is None else PendingIntake(
        request.id, request.intent, request.primary_feature_id, completed_side.change_id, request.target_hint
    )
    return _validated(
        replace(
            current,
            active_context=current.preserved_primary_context,
            preserved_primary_context=None,
            active_work_id=current.preserved_primary_work_id,
            preserved_primary_work_id=None,
            completed_side_contexts=current.completed_side_contexts + (archive,),
            terminal_archives=current.terminal_archives + (archive,),
            deferred_side_request=None,
            pending_intake=intake,
            work_relations=_terminal_relations(current, completed_side),
        ),
        "side completion was rejected",
    )


def _defer_side_request(current: State, request: DeferredSideRequest) -> State:
    if current.deferred_side_request is not None:
        raise ServiceError("only one side request may be deferred")
    return _validated(
        replace(current, deferred_side_request=request),
        "deferred side request was rejected",
    )


def _initialize(current: State, initialization: RepositoryInitialization) -> State:
    if current.initialization is not None:
        return current
    return _validated(replace(current, initialization=initialization), "initialization was rejected")


def _materialize_pending(current: State, feature: Feature, change: Change) -> State:
    intake = current.pending_intake
    if intake is None:
        raise ServiceError("pending intake does not exist")
    active = current.active_context
    if (
        change.assessment is not None
        and change.assessment.capability_state == CapabilityState.ESTABLISHED
    ):
        raise ServiceError("pending intake cannot bind established work as a side feature")
    if active is not None:
        if (
            active.feature_id != intake.deferred_from_feature_id
            or active.side_of is not None
            or current.preserved_primary_context is not None
            or feature.side_of != active.feature_id
        ):
            raise ServiceError("pending intake must materialize under its restored primary")
        candidate = _record_feature(replace(current, pending_intake=None), feature)
        materialized = _activate_side(candidate, change)
        return _validated(
            materialized,
            "pending intake was rejected",
        )
    candidate = _record_feature(current, feature)
    materialized = _record_change(candidate, change)
    return _validated(replace(materialized, pending_intake=None), "pending intake was rejected")


def _record_shared_intent(
    current: State, shared: SharedIntent, deferred: DeferredLinkedIntent
) -> State:
    if deferred.shared_intent_id != shared.id:
        raise ServiceError("deferred linked intent must belong to shared intent")
    return _validated(
        replace(
            current,
            shared_intents=current.shared_intents + (shared,),
            deferred_linked_intents=current.deferred_linked_intents + (deferred,),
        ),
        "shared intent was rejected",
    )


def _materialize_linked(
    current: State, linked_id: str, feature: Feature, change: Change
) -> State:
    linked = next((item for item in current.deferred_linked_intents if item.id == linked_id), None)
    if linked is None or change.shared_intent_id != linked.shared_intent_id:
        raise ServiceError("linked intent does not match materialized change")
    if current.active_context is not None or current.preserved_primary_context is not None:
        raise ServiceError("linked work remains current until its predecessor is terminal")
    predecessor_ids = {record.id: record.shared_intent_id for record in current.changes}
    if not any(
        archive.context.change_id in predecessor_ids
        and predecessor_ids[archive.context.change_id] == linked.shared_intent_id
        for archive in current.terminal_archives
        if archive.context.change_id is not None
    ):
        raise ServiceError("linked materialization requires terminal predecessor")
    if (
        change.assessment is not None
        and change.assessment.capability_state == CapabilityState.ESTABLISHED
    ):
        selected_feature_id = change.assessment.selected_feature_id
        if selected_feature_id is None or feature.id != selected_feature_id:
            raise ServiceError("linked established work must use its selected Core feature")
        materialized = _record_change(current, change)
    else:
        candidate = _record_feature(current, feature)
        materialized = _record_change(candidate, change)
    return _validated(
        replace(
            materialized,
            deferred_linked_intents=tuple(
                item for item in materialized.deferred_linked_intents if item.id != linked.id
            ),
        ),
        "linked materialization was rejected",
    )


def _invalidate(context: WorkingContext) -> WorkingContext:
    return replace(
        context,
        review=None,
        checkpoints=tuple(Checkpoint(item.name, False) for item in context.checkpoints),
        verification=VerificationEvidence((), False),
        convergence_complete=False,
    )


def _record_alteration(current: State) -> State:
    active = current.active_context
    if active is None:
        raise ServiceError("implementation alteration requires active work")
    if (active.status, active.phase) not in (
        (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT),
        (LifecycleState.IN_PROGRESS, Phase.REVIEW),
        (LifecycleState.VERIFYING, Phase.VERIFY),
        (LifecycleState.VERIFYING, Phase.CONVERGE),
    ):
        raise ServiceError("implementation alteration requires active implementation work")
    context = replace(
        _invalidate(active),
        status=LifecycleState.IN_PROGRESS,
        phase=Phase.IMPLEMENT,
        paused_pair=None,
    )
    return _validated(
        replace(current, active_context=context),
        "implementation alteration was rejected",
    )


def _current_lock_revision(current: State, lock: SemanticLock) -> SemanticLockRevision:
    revision = next(
        (
            item
            for item in current.lock_revisions
            if item.lock_id == lock.id and item.revision == lock.current_revision
        ),
        None,
    )
    if revision is None:
        raise ServiceError("semantic lock current revision is unavailable")
    return revision


def _is_current_lock_decision(
    current: State, feature_id: FeatureId, decision_id: DecisionId | None
) -> bool:
    active = current.active_context
    if (
        decision_id is None
        or active is None
        or active.feature_id != feature_id
        or active.change_id is None
    ):
        return False
    decision = next((item for item in current.decisions if item.id == decision_id), None)
    superseded = {item.supersedes for item in current.decisions if item.supersedes is not None}
    return (
        decision is not None
        and decision.id not in superseded
        and (decision.feature_id, decision.change_id) == (feature_id, active.change_id)
        and decision.id in active.decision_ids + active.constraint_ids
    )


def _invalidate_lock_acceptance(current: State, feature_id: FeatureId) -> State:
    active = current.active_context
    if active is None or active.feature_id != feature_id:
        return current
    if (active.status, active.phase) not in (
        (LifecycleState.IN_PROGRESS, Phase.REVIEW),
        (LifecycleState.VERIFYING, Phase.VERIFY),
        (LifecycleState.VERIFYING, Phase.CONVERGE),
    ):
        return current
    return replace(
        current,
        active_context=replace(
            _invalidate(active),
            status=LifecycleState.IN_PROGRESS,
            phase=Phase.IMPLEMENT,
            paused_pair=None,
        ),
    )


def _paused_acceptance_pair(active: WorkingContext) -> bool:
    return (
        active.status == LifecycleState.PAUSED
        and active.paused_pair is not None
        and active.paused_pair[1] in (Phase.REVIEW, Phase.VERIFY, Phase.CONVERGE)
    )


def _record_lock(current: State, lock: SemanticLock, revision: SemanticLockRevision) -> State:
    active = current.active_context
    if (
        not lock.active
        or lock.current_revision != 1
        or revision.lock_id != lock.id
        or revision.revision != 1
        or any(item.id == lock.id for item in current.locks)
        or (active is not None and active.feature_id != lock.feature_id)
        or (active is not None and _paused_acceptance_pair(active))
    ):
        raise ServiceError("semantic lock creation requires its first active revision")
    baseline = next(
        (item for item in current.publications if item.feature_id == lock.feature_id),
        None,
    )
    if baseline is None or revision.baseline != baseline:
        raise ServiceError("semantic lock requires accepted current Feature truth")
    return _validated(
        replace(current, locks=current.locks + (lock,), lock_revisions=current.lock_revisions + (revision,)),
        "semantic lock was rejected",
    )


def _revise_lock(current: State, revision: SemanticLockRevision) -> State:
    lock = next((item for item in current.locks if item.id == revision.lock_id), None)
    if (
        lock is None
        or not lock.active
        or revision.revision != lock.current_revision + 1
        or current.active_context is None
        or current.active_context.feature_id != lock.feature_id
        or _paused_acceptance_pair(current.active_context)
    ):
        raise ServiceError("semantic lock revision must append to its active current revision")
    baseline = next((item for item in current.publications if item.feature_id == lock.feature_id), None)
    if baseline is None or revision.baseline != baseline:
        raise ServiceError("semantic lock revision requires accepted current Feature truth")
    prior = _current_lock_revision(current, lock)
    if not set(prior.protected_aspects).issubset(revision.protected_aspects) and not _is_current_lock_decision(current, lock.feature_id, revision.decision_id):
        raise ServiceError("semantic lock protection weakening requires a current active decision")
    current = _invalidate_lock_acceptance(current, lock.feature_id)
    return _validated(
        replace(
            current,
            locks=tuple(replace(item, current_revision=revision.revision) if item.id == lock.id else item for item in current.locks),
            lock_revisions=current.lock_revisions + (revision,),
        ),
        "semantic lock revision was rejected",
    )


def _retire_lock(current: State, release: LockRelease) -> State:
    if type(release.revision) is not int or release.revision < 1:
        raise ServiceError("lock release revision must be a positive integer")
    lock = next((item for item in current.locks if item.id == release.lock_id), None)
    decision = next((item for item in current.decisions if item.id == release.decision_id), None)
    if (
        lock is None
        or not lock.active
        or release.revision != lock.current_revision
        or decision is None
        or not _is_current_lock_decision(current, lock.feature_id, release.decision_id)
        or (current.active_context is not None and _paused_acceptance_pair(current.active_context))
        or any(item.lock_id == lock.id for item in current.lock_releases)
    ):
        raise ServiceError("semantic lock retirement requires a current Feature decision")
    current = _invalidate_lock_acceptance(current, lock.feature_id)
    return _validated(
        replace(
            current,
            locks=tuple(replace(item, active=False) if item.id == lock.id else item for item in current.locks),
            lock_releases=current.lock_releases + (release,),
        ),
        "semantic lock retirement was rejected",
    )


def _assess_locks(current: State, change_id: ChangeId, assessments: tuple[LockAssessment, ...]) -> State:
    active = current.active_context
    if (
        active is None
        or active.change_id != change_id
        or not assessments
        or _paused_acceptance_pair(active)
    ):
        raise ServiceError("lock assessment requires the current active Change")
    locks = tuple(item for item in current.locks if item.active and item.feature_id == active.feature_id)
    if {item.lock_id for item in assessments} != {item.id for item in locks} or len(assessments) != len(locks):
        raise ServiceError("lock assessment must cover each active Feature lock exactly once")
    if any(item.lock_revision != next(lock.current_revision for lock in locks if lock.id == item.lock_id) for item in assessments):
        raise ServiceError("lock assessment must reference current lock revisions")
    replacement = replace(
        next(item for item in current.changes if item.id == change_id),
        lock_assessments=assessments,
    )
    records = tuple(
        ChangeLockAssessments(
            change_id,
            assessments,
            item.history + (assessments,),
        )
        if item.change_id == change_id
        else item
        for item in current.change_lock_assessments
    )
    if not any(item.change_id == change_id for item in records):
        records += (ChangeLockAssessments(change_id, assessments, (assessments,)),)
    current = _invalidate_lock_acceptance(current, active.feature_id)
    return _validated(
        replace(
            current,
            changes=tuple(replacement if item.id == change_id else item for item in current.changes),
            change_lock_assessments=records,
        ),
        "lock assessment was rejected",
    )


def _authorize_lock(current: State, authorization: LockAuthorization) -> State:
    active = current.active_context
    if active is None or _paused_acceptance_pair(active):
        raise ServiceError("lock authorization requires current scoped Feature, Change, lock, and Decision")
    lock = next((item for item in current.locks if item.id == authorization.lock_id), None)
    decision = next((item for item in current.decisions if item.id == authorization.decision_id), None)
    if (
        active.feature_id != authorization.feature_id
        or active.change_id != authorization.change_id
        or lock is None
        or not lock.active
        or lock.feature_id != authorization.feature_id
        or lock.current_revision != authorization.lock_revision
        or decision is None
        or not _is_current_lock_decision(current, authorization.feature_id, authorization.decision_id)
    ):
        raise ServiceError("lock authorization requires current scoped Feature, Change, lock, and Decision")
    change = next(item for item in current.changes if item.id == authorization.change_id)
    assessment = next((item for item in change.lock_assessments if item.lock_id == authorization.lock_id), None)
    record = next(item for item in current.change_lock_assessments if item.change_id == authorization.change_id)
    if (
        assessment is None
        or authorization.assessment_revision != len(record.history)
        or assessment.protected_touches != authorization.protected_aspects
    ):
        raise ServiceError("lock authorization must exactly match current protected touches")
    current = _invalidate_lock_acceptance(current, authorization.feature_id)
    return _validated(
        replace(current, lock_authorizations=current.lock_authorizations + (authorization,)),
        "lock authorization was rejected",
    )


def _lock_gate(current: State) -> str | None:
    active = current.active_context
    if active is None or active.change_id is None:
        return None
    change = next(item for item in current.changes if item.id == active.change_id)
    item = WorkItem(
        _work_id(active), OperationalState.READY, active, current.preserved_primary_context,
        current.deferred_side_request, current.pending_intake, None, "",
    )
    return _work_item_lock_gate(current, item, change)


def _work_item_lock_gate(current: State, item: WorkItem, change: Change) -> str | None:
    context = item.context
    if context is None or context.change_id is None:
        return "queued work lacks lifecycle context"
    locks = tuple(lock for lock in current.locks if lock.active and lock.feature_id == context.feature_id)
    if not locks:
        return None
    assessments = {item.lock_id: item for item in change.lock_assessments}
    record = next((item for item in current.change_lock_assessments if item.change_id == context.change_id), None)
    for lock in locks:
        assessment = assessments.get(lock.id)
        if record is None or assessment is None or assessment.lock_revision != lock.current_revision:
            return "active semantic lock lacks a current assessment"
        if assessment.evasion:
            return "semantic lock assessment reports evasion"
        if assessment.conflict:
            authorized = any(
                item.lock_id == lock.id
                and item.lock_revision == lock.current_revision
                and item.feature_id == context.feature_id
                and item.change_id == context.change_id
                and item.protected_aspects == assessment.protected_touches
                and item.assessment_revision == len(record.history)
                and _is_work_item_lock_decision(current, context, item.decision_id)
                for item in current.lock_authorizations
            )
            if not authorized:
                return "protected semantic lock conflict lacks exact authorization"
    return None


def _is_work_item_lock_decision(
    current: State, context: WorkingContext, decision_id: DecisionId | None
) -> bool:
    if decision_id is None or context.change_id is None:
        return False
    decision = next((item for item in current.decisions if item.id == decision_id), None)
    superseded = {item.supersedes for item in current.decisions if item.supersedes is not None}
    return (
        decision is not None
        and decision.id not in superseded
        and (decision.feature_id, decision.change_id) == (context.feature_id, context.change_id)
        and decision.id in context.decision_ids + context.constraint_ids
    )


def _convergence_policy_gate(current: State) -> str | None:
    active = current.active_context
    if active is None or active.change_id is None:
        return "convergence requires an active Change"
    experiments = tuple(
        item
        for item in current.observations
        if item.change_id == active.change_id and item.kind == "experiment"
    )
    if active.classification == Classification.EXPERIMENT and not experiments:
        return "experiment convergence requires an explicit outcome"
    if any(item.outcome == ExperimentOutcome.REJECTED for item in experiments):
        return "rejected experiment cannot converge"
    unresolved = tuple(
        item
        for item in current.drifts
        if item.unresolved and item.change_id == active.change_id
    )
    if unresolved and not any(
        item.kind == "constraint" and item.change_id == active.change_id
        for item in current.decisions
    ):
        return "unresolved drift requires explicit reconciliation"
    return None


def _rebase_active_locks(current: State, publication: LivingSpecPublication) -> State:
    changed = tuple(
        item for item in current.locks if item.active and item.feature_id == publication.feature_id
    )
    if not changed:
        return current
    revisions = current.lock_revisions + tuple(
        SemanticLockRevision(
            lock.id,
            lock.current_revision + 1,
            publication,
            _current_lock_revision(current, lock).protected_aspects,
            _current_lock_revision(current, lock).rationale,
        )
        for lock in changed
    )
    return replace(
        current,
        locks=tuple(
            replace(lock, current_revision=lock.current_revision + 1) if lock in changed else lock
            for lock in current.locks
        ),
        lock_revisions=revisions,
    )


def _publish_candidate(
    current: State,
    feature_id: FeatureId,
    change_id: ChangeId,
    living_spec_id: LivingSpecId,
    revision: str,
    assessment: ConvergenceAssessment,
) -> tuple[State, LivingSpecPublication]:
    active = current.active_context
    if IDENTITY.fullmatch(revision) is None:
        raise ServiceError("publication revision must be a stable path segment")
    if (
        active is None
        or current.preserved_primary_context is not None
        or active.feature_id != feature_id
        or active.change_id != change_id
        or active.status != LifecycleState.VERIFYING
        or active.phase != Phase.CONVERGE
        or not active.convergence_complete
        or not can_converge(active.review, active.verification)
        or active.publication is not None
        or _lock_gate(current) is not None
        or _convergence_policy_gate(current) is not None
        or type(assessment) is not ConvergenceAssessment
        or assessment.change_id != change_id
        or not assessment.product_truth_changed
    ):
        raise ServiceError("publication requires authoritative active convergence")
    feature = next((item for item in current.features if item.id == feature_id), None)
    change = next((item for item in current.changes if item.id == change_id), None)
    if (
        feature is None
        or feature.living_spec_id != living_spec_id
        or change is None
        or change.feature_id != feature_id
    ):
        raise ServiceError("publication identity does not match authoritative work")
    prior = next(
        (item for item in current.publications if item.living_spec_id == living_spec_id),
        None,
    )
    publication = LivingSpecPublication(
        living_spec_id,
        revision,
        feature.title,
        RepositoryPath(f"living-specs/{living_spec_id}/{revision}.md"),
        None if prior is None else prior.revision,
        feature_id,
        change_id,
    )
    rebased = _rebase_active_locks(current, publication)
    accepted = replace(
        active,
        status=LifecycleState.ACCEPTED,
        phase=Phase.DONE,
        publication=publication,
    )
    archive = TerminalArchive(
        accepted,
        accepted.side_of,
        replace(assessment, product_truth_content=None),
    )
    publications = tuple(
        item for item in current.publications if item.living_spec_id != living_spec_id
    ) + (publication,)
    candidate = replace(
        rebased,
        active_context=None,
        active_work_id=None,
        publications=publications,
        terminal_archives=current.terminal_archives + (archive,),
        publication_history=current.publication_history + (publication,),
        work_items=current.work_items + (() if current.active_work_id is None else (
            WorkItem(current.active_work_id, OperationalState.DONE, None, None, None, None, None, datetime.now(timezone.utc).isoformat(), accepted.change_id),
        )),
    )
    return _validated(candidate, "Living Spec publication was rejected"), publication


def _publish_side_candidate(
    current: State,
    revision: str,
    assessment: ConvergenceAssessment,
) -> tuple[State, LivingSpecPublication]:
    active = current.active_context
    primary = current.preserved_primary_context
    if IDENTITY.fullmatch(revision) is None:
        raise ServiceError("publication revision must be a stable path segment")
    if (
        active is None
        or primary is None
        or active.side_of != primary.feature_id
        or active.status != LifecycleState.VERIFYING
        or active.phase != Phase.CONVERGE
        or not active.convergence_complete
        or not can_converge(active.review, active.verification)
        or active.publication is not None
        or active.change_id is None
        or _lock_gate(current) is not None
        or _convergence_policy_gate(current) is not None
        or type(assessment) is not ConvergenceAssessment
        or active.change_id != assessment.change_id
        or not assessment.product_truth_changed
    ):
        raise ServiceError("side publication requires authoritative active convergence")
    feature = next((item for item in current.features if item.id == active.feature_id), None)
    change = next((item for item in current.changes if item.id == active.change_id), None)
    if (
        feature is None
        or feature.living_spec_id is None
        or feature.side_of != primary.feature_id
        or change is None
        or change.feature_id != feature.id
    ):
        raise ServiceError("side publication identity does not match authoritative work")
    prior = next(
        (
            item
            for item in current.publications
            if item.living_spec_id == feature.living_spec_id
        ),
        None,
    )
    publication = LivingSpecPublication(
        feature.living_spec_id,
        revision,
        feature.title,
        RepositoryPath(f"living-specs/{feature.living_spec_id}/{revision}.md"),
        None if prior is None else prior.revision,
        feature.id,
        change.id,
    )
    rebased = _rebase_active_locks(current, publication)
    accepted = replace(
        active,
        status=LifecycleState.ACCEPTED,
        phase=Phase.DONE,
        publication=publication,
    )
    archive = TerminalArchive(
        accepted,
        primary.feature_id,
        replace(assessment, product_truth_content=None),
    )
    request = current.deferred_side_request
    intake = None if request is None else PendingIntake(
        request.id,
        request.intent,
        request.primary_feature_id,
        accepted.change_id,
        request.target_hint,
    )
    publications = tuple(
        item
        for item in current.publications
        if item.living_spec_id != feature.living_spec_id
    ) + (publication,)
    candidate = replace(
        rebased,
        active_context=primary,
        preserved_primary_context=None,
        active_work_id=current.preserved_primary_work_id,
        preserved_primary_work_id=None,
        publications=publications,
        completed_side_contexts=current.completed_side_contexts + (archive,),
        terminal_archives=current.terminal_archives + (archive,),
        publication_history=current.publication_history + (publication,),
        deferred_side_request=None,
        pending_intake=intake,
        work_items=current.work_items + (() if current.active_work_id is None else (
            WorkItem(current.active_work_id, OperationalState.DONE, None, None, None, None, None, datetime.now(timezone.utc).isoformat(), accepted.change_id),
        )),
    )
    return _validated(candidate, "side Living Spec publication was rejected"), publication


def _accept_without_publication_candidate(
    current: State, assessment: ConvergenceAssessment
) -> State:
    active = current.active_context
    if (
        active is None
        or active.change_id is None
        or active.status != LifecycleState.VERIFYING
        or active.phase != Phase.CONVERGE
        or not active.convergence_complete
        or not can_converge(active.review, active.verification)
        or active.publication is not None
        or _lock_gate(current) is not None
        or _convergence_policy_gate(current) is not None
        or type(assessment) is not ConvergenceAssessment
        or assessment.change_id != active.change_id
        or assessment.product_truth_changed
    ):
        raise ServiceError("accepted archive requires authoritative false convergence assessment")
    feature = next((item for item in current.features if item.id == active.feature_id), None)
    change = next((item for item in current.changes if item.id == active.change_id), None)
    if feature is None or change is None or change.feature_id != feature.id:
        raise ServiceError("accepted archive identity does not match authoritative work")
    accepted = replace(active, status=LifecycleState.ACCEPTED, phase=Phase.DONE)
    if accepted.side_of is None:
        archive = TerminalArchive(accepted, None, assessment)
        return _validated(
            replace(
                current,
                active_context=None,
                active_work_id=None,
                terminal_archives=current.terminal_archives + (archive,),
                work_items=current.work_items + (() if current.active_work_id is None else (
                    WorkItem(current.active_work_id, OperationalState.DONE, None, None, None, None, None, datetime.now(timezone.utc).isoformat(), accepted.change_id),
                )),
            ),
            "accepted archive was rejected",
        )
    primary = current.preserved_primary_context
    if primary is None or primary.feature_id != accepted.side_of:
        raise ServiceError("side accepted archive requires its preserved primary")
    archive = TerminalArchive(accepted, primary.feature_id, assessment)
    request = current.deferred_side_request
    intake = None if request is None else PendingIntake(
        request.id,
        request.intent,
        request.primary_feature_id,
        accepted.change_id,
        request.target_hint,
    )
    return _validated(
        replace(
            current,
            active_context=primary,
            preserved_primary_context=None,
            active_work_id=current.preserved_primary_work_id,
            preserved_primary_work_id=None,
            completed_side_contexts=current.completed_side_contexts + (archive,),
            terminal_archives=current.terminal_archives + (archive,),
            deferred_side_request=None,
            pending_intake=intake,
            work_items=current.work_items + (() if current.active_work_id is None else (
                WorkItem(current.active_work_id, OperationalState.DONE, None, None, None, None, None, datetime.now(timezone.utc).isoformat(), accepted.change_id),
            )),
        ),
        "accepted side archive was rejected",
    )


def _authoritative_state(store: _StateStore, supplied: State) -> State:
    current = store.recover()
    if supplied.revision != current.revision:
        raise StaleRevisionError(supplied.revision, current.revision)
    if supplied != current:
        raise ServiceError("supplied state is stale or does not match authority")
    return current


def _prepare_state_publication(candidate: State, revision: int) -> State:
    if candidate.revision != revision:
        raise ServiceError("state mutation was rejected")
    next_revision = revision + 1
    authorizations = tuple(
        replace(
            item,
            status=IrreversibleAuthorizationStatus.INVALIDATED,
            invalidated_revision=next_revision,
            invalidation_reason="Core authority revision changed after authorization",
        )
        if item.status is IrreversibleAuthorizationStatus.PENDING
        and item.authorized_revision != next_revision
        else replace(
            item,
            status=IrreversibleAuthorizationStatus.INVALIDATED,
            invalidated_revision=next_revision,
            invalidation_reason="Core authority revision changed after operation validation",
        )
        if item.status is IrreversibleAuthorizationStatus.READY
        and item.validated_revision != next_revision
        else item
        for item in candidate.irreversible_authorizations
    )
    saved = replace(
        candidate,
        revision=next_revision,
        irreversible_authorizations=authorizations,
    )
    try:
        validate_state(saved)
    except StateValidationError as error:
        raise ServiceError("state mutation was rejected") from error
    return saved


def _autonomy_envelope_json(envelope: AutonomyEnvelope) -> dict[str, object]:
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


def _autonomy_delta_json(delta: AutonomyEnvelopeDelta) -> dict[str, object]:
    return {
        "allowed_paths": None if delta.allowed_paths is None else [str(path) for path in delta.allowed_paths],
        "operation_budget": delta.operation_budget,
        "reversible_actions": None if delta.reversible_actions is None else list(delta.reversible_actions),
        "external_wait_allowed": delta.external_wait_allowed,
        "retry_budget": delta.retry_budget,
        "irreversible_permissions": None if delta.irreversible_permissions is None else list(delta.irreversible_permissions),
        "stop_conditions": None if delta.stop_conditions is None else list(delta.stop_conditions),
    }


def _selection_candidates(state: State, intent: str) -> tuple[CapabilityCandidate, ...]:
    return tuple(
        CapabilityCandidate(feature.id, feature.title)
        for feature in state.features
        if DiscoveryPolicy.matches_capability(feature.title, intent)
    )


def _selection_id(operation: AssessmentOperation, intent: str, candidates: tuple[CapabilityCandidate, ...]) -> CapabilitySelectionId:
    values = ":".join(str(item.feature_id) for item in candidates)
    return CapabilitySelectionId(f"selection-{sha256(f'{operation}:{intent}:{values}'.encode()).hexdigest()[:12]}")


_GIT_IDENTITY_LINE = re.compile(
    rb"^(author|committer) (.*?) <([^<>]*)> (-?[0-9]+ [+-][0-9]{4})$"
)


def _git_run(
    root: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ("git", *arguments),
            cwd=root,
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ServiceError("Core could not complete the authorized Git operation") from error


def _git_output(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    result = _git_run(root, *arguments, input_bytes=input_bytes)
    if result.returncode != 0:
        raise ServiceError("Core could not verify the authorized Git operation")
    return result.stdout


def _git_remote_refs(root: Path) -> dict[str, str]:
    output = _git_output(root, "ls-remote", "--refs", "--heads", "--tags", "origin")
    refs: dict[str, str] = {}
    for line in output.splitlines():
        try:
            sha, ref = line.decode("utf-8").split("\t", 1)
        except (UnicodeDecodeError, ValueError) as error:
            raise ServiceError("Core received malformed remote Git ref data") from error
        if ref in refs or not is_git_sha(sha):
            raise ServiceError("Core received duplicate or malformed remote Git refs")
        refs[ref] = sha
    return refs


def _git_remote_ref_sha(root: Path, target_ref: str) -> str | None:
    output = _git_output(root, "ls-remote", "--refs", "origin", target_ref)
    matches: list[str] = []
    for line in output.splitlines():
        try:
            sha, ref = line.decode("utf-8").split("\t", 1)
        except (UnicodeDecodeError, ValueError) as error:
            raise ServiceError("Core received malformed target ref data") from error
        if ref == target_ref:
            matches.append(sha)
    if len(matches) > 1 or any(not is_git_sha(value) for value in matches):
        raise ServiceError("Core received ambiguous target ref data")
    return None if not matches else matches[0]


def _git_identity_rewrite_plan(
    root: Path, authorization: IrreversibleAuthorization
) -> tuple[tuple[GitCommitRewrite, ...], tuple[tuple[str, bytes], ...]]:
    """Recompute every affected commit from the old ancestry and exact policy."""
    old_tip = authorization.expected_remote_sha
    planned = {item.old_sha: item for item in authorization.commit_rewrites}
    ordered_commits = _git_output(
        root, "rev-list", "--reverse", "--topo-order", old_tip
    ).decode("ascii").splitlines()
    if not ordered_commits or ordered_commits[-1] != old_tip:
        raise ServiceError("Core could not enumerate the authorized branch history")

    target_name = authorization.target_identity_name.encode("utf-8")
    target_email = authorization.target_identity_email.encode("utf-8")
    rewritten_parents: dict[str, str] = {}
    computed: list[GitCommitRewrite] = []
    objects: list[tuple[str, bytes]] = []

    for old_sha in ordered_commits:
        original = _git_output(root, "cat-file", "commit", old_sha)
        header, separator, message = original.partition(b"\n\n")
        if not separator:
            raise ServiceError("Core found a malformed Git commit in the authorized history")
        changed_identity = False
        identity_counts = {b"author": 0, b"committer": 0}
        rewritten_header: list[bytes] = []
        for line in header.split(b"\n"):
            if line.startswith(b"parent "):
                parent = line[7:].decode("ascii", errors="strict")
                if not is_git_sha(parent):
                    raise ServiceError("Core found a malformed parent in the authorized history")
                rewritten_header.append(
                    b"parent " + rewritten_parents.get(parent, parent).encode("ascii")
                )
                continue
            match = _GIT_IDENTITY_LINE.fullmatch(line)
            if line.startswith((b"author ", b"committer ")) and match is None:
                raise ServiceError("Core found a malformed Git identity in the authorized history")
            if match is not None:
                role, old_name, old_email, timestamp = match.groups()
                identity_counts[role] += 1
                if (old_name, old_email) != (target_name, target_email):
                    changed_identity = True
                    line = role + b" " + target_name + b" <" + target_email + b"> " + timestamp
            rewritten_header.append(line)

        if identity_counts != {b"author": 1, b"committer": 1}:
            raise ServiceError("Core requires exactly one author and committer per Git commit")

        candidate = b"\n".join(rewritten_header) + b"\n\n" + message
        candidate_sha = _git_output(
            root, "hash-object", "-t", "commit", "--stdin", input_bytes=candidate
        ).decode("ascii").strip()
        if candidate_sha == old_sha:
            if old_sha in planned:
                raise ServiceError("authorized commit map includes an unchanged commit")
            continue

        if any(line.startswith((b"gpgsig ", b"gpgsig-sha256 ")) for line in header.split(b"\n")):
            raise ServiceError("Core will not rewrite a signed Git commit")
        mapping = planned.get(old_sha)
        if (
            mapping is None
            or mapping.new_sha != candidate_sha
            or mapping.identity_normalized is not changed_identity
        ):
            raise ServiceError("persisted authorization does not match the recomputed Git commit map")
        rewritten_parents[old_sha] = candidate_sha
        computed.append(mapping)
        objects.append((candidate_sha, candidate))

    if tuple(computed) != authorization.commit_rewrites:
        raise ServiceError("persisted authorization contains missing, extra, or reordered commit mappings")
    if rewritten_parents.get(old_tip) != authorization.expected_result_sha:
        raise ServiceError("persisted authorization result SHA does not match the recomputed branch tip")
    return tuple(computed), tuple(objects)


@final
class CoreService:
    """Expose only validated operation-specific authority mutations."""

    __slots__ = ("__persistence", "__repository_root")

    def __init__(self, persistence: StatePersistence) -> Never:
        if TYPE_CHECKING:
            self.__persistence = _StateStore.__new__(
                _StateStore
            )
            self.__repository_root = Path()
        raise ServiceConstructionError("use CoreService.open()")

    @classmethod
    def open(cls, root: Path, operations: FileOperations | None = None) -> CoreService:
        service = object.__new__(cls)
        service.__persistence = _StateStore(root, operations)
        service.__repository_root = root.resolve()
        return service

    def accept_first_slice(
        self,
        state: State,
        feature_title: str,
        slice_title: str,
        initial_task: str,
        checkpoint: str,
        acceptance: UserAcceptance,
        criteria: SliceCriteriaSpec | None = None,
    ) -> State:
        """Persist a human-approved Feature, Slice and Task in one Core commit."""
        current = _authoritative_state(self.__persistence, state)
        if current.initialization is None:
            raise ServiceError("repository initialization is required before Slice acceptance")
        if not self.__persistence.e1_schema_ready():
            raise ServiceError("legacy authority is read-only for E1; no automatic migration")
        root_identity = sha256(str(self.__repository_root).encode("utf-8")).hexdigest()[:16]
        if current.initialization.repository_id != root_identity:
            raise ServiceError("the persisted repository identity belongs to another root")
        values = (feature_title, slice_title, initial_task, checkpoint)
        if any(type(value) is not str or not value.strip() or len(value) > 2048 for value in values):
            raise ServiceError("Feature, Slice, Task and checkpoint must be bounded nonblank text")
        if (
            type(acceptance) is not UserAcceptance
            or acceptance.source != "INTERACTIVE_TERMINAL"
            or not acceptance.statement.strip()
        ):
            raise ServiceError("attributable user acceptance evidence is required")
        accepted_statement = slice_acceptance_statement(
            feature_title, slice_title, initial_task, checkpoint, criteria,
        )
        expected_reference = f"tty:{sha256(accepted_statement.encode('utf-8')).hexdigest()[:12]}"
        if acceptance.statement != accepted_statement or acceptance.source_reference != expected_reference:
            raise ServiceError("acceptance must match exactly the human-confirmed agreement")
        # Bind a retry to identical canonical input and the exact initialized root.
        digest = sha256(json.dumps([
            root_identity, *values, acceptance.statement, acceptance.source_reference,
        ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
        feature_id = FeatureId(f"feature-{digest}")
        change_id = ChangeId(f"change-{digest}")
        task_id = f"task-{digest}"
        feature_ref, slice_ref = f"agreement-feature-{digest}", f"agreement-slice-{digest}"
        if current.agreements:
            if (
                len(current.agreements) == 2
                and tuple(item.id for item in current.agreements) == (feature_ref, slice_ref)
            ):
                return current
            raise ServiceError("the first Slice has already been accepted")
        if current.active_context is not None or current.features or current.changes or current.work_items:
            raise ServiceError("first Slice acceptance requires an initialized, empty work ledger")
        feature = Feature(feature_id, feature_title.strip(), LivingSpecId(f"spec-{digest}"))
        change = Change(change_id, feature_id, slice_title.strip(), Classification.FEATURE)
        feature_agreement = AgreementRecord(
            feature_ref, "FEATURE", feature_id, change_id, feature.title,
            acceptance.statement, acceptance.source, acceptance.source_reference, root_identity,
        )
        slice_agreement = AgreementRecord(
            slice_ref, "SLICE", feature_id, change_id, slice_title.strip(),
            acceptance.statement, acceptance.source, acceptance.source_reference, root_identity,
            task_id, initial_task.strip(), checkpoint.strip(),
        )
        candidate = _record_change(_record_feature(current, feature), change)
        assert candidate.active_context is not None
        criteria_revision = None if criteria is None else SliceCriteriaRevision(
            f"slice-criteria-{digest}-1", slice_ref, root_identity,
            criteria.outcome, criteria.example, criteria.inclusions, criteria.exclusions,
            criteria.decisions, criteria.criteria,
            acceptance.source, acceptance.source_reference,
        )
        candidate = replace(
            candidate,
            active_context=replace(
                candidate.active_context,
                focus=slice_title.strip(),
                current_work=initial_task.strip(),
                checkpoints=(Checkpoint(checkpoint.strip(), False),),
                feature_agreement_ref=feature_ref,
                slice_agreement_ref=slice_ref,
            ),
            agreements=(feature_agreement, slice_agreement),
            slice_criteria_revisions=() if criteria_revision is None else (criteria_revision,),
            slice_criteria_heads=() if criteria_revision is None else (SliceCriteriaHead(slice_ref, criteria_revision.id),),
        )
        saved = _prepare_state_publication(candidate, current.revision)
        try:
            self.__persistence.publish_first_slice(saved, change, saved.agreements)
        except AtomicFailure as error:
            raise ServiceError("first Slice aggregate publication was rejected") from error
        return saved

    def accept_next_slice(
        self, state: State, feature_id: str, slice_title: str, initial_task: str,
        checkpoint: str, acceptance: UserAcceptance, criteria: SliceCriteriaSpec,
    ) -> State:
        """Accept another Slice under the original Feature in a single Core revision."""
        current = _authoritative_state(self.__persistence, state)
        root_identity = sha256(str(self.__repository_root).encode("utf-8")).hexdigest()[:16]
        if current.initialization is None or current.initialization.repository_id != root_identity:
            raise ServiceError("next Slice requires initialized authority for this exact root")
        if not self.__persistence.r3b_schema_ready() or current.r3b_schema_upgrade is None:
            raise ServiceError("next Slice requires current schema-17 authority; no implicit migration")
        if type(feature_id) is not str or not feature_id or feature_id != feature_id.strip():
            raise ServiceError("existing Feature-ID must be explicit and canonical")
        feature = next((item for item in current.features if str(item.id) == feature_id and item.side_of is None), None)
        if feature is None:
            raise ServiceError("existing primary Feature-ID was not found")
        original = next((
            item for item in current.agreements if item.kind == "FEATURE" and item.feature_id == feature.id
        ), None)
        if original is None:
            raise ServiceError("existing Feature lacks an accepted origin agreement")
        statement = next_slice_acceptance_statement(
            feature_id, feature.title, slice_title, initial_task, checkpoint, criteria,
        )
        expected_reference = f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}"
        if (
            type(acceptance) is not UserAcceptance
            or acceptance.source != "INTERACTIVE_TERMINAL"
            or acceptance.statement != statement
            or acceptance.source_reference != expected_reference
        ):
            raise ServiceError("next Slice requires its own exact human terminal acceptance")
        digest = sha256(json.dumps([
            root_identity, original.id, feature_id, slice_title, initial_task, checkpoint,
            acceptance.statement, acceptance.source_reference,
        ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
        change_id = ChangeId(f"change-{digest}")
        task_id = f"task-{digest}"
        feature_ref, slice_ref = f"agreement-feature-{digest}", f"agreement-slice-{digest}"
        existing_pair = tuple(item for item in current.agreements if item.id in (feature_ref, slice_ref))
        if existing_pair:
            if (
                len(existing_pair) == 2
                and {item.id for item in existing_pair} == {feature_ref, slice_ref}
                and any(item.id == change_id and item.feature_id == feature.id for item in current.changes)
            ):
                # An identical accepted proposal is already durable. No new revision.
                return current
            raise ServiceError("next Slice identity conflicts with existing agreements")
        if any(item.kind == "SLICE" and item.feature_id == feature.id
               and item.title.casefold() == slice_title.casefold() for item in current.agreements):
            raise ServiceError("a Slice with this title is already accepted in the Feature")
        if (
            current.active_context is not None or current.preserved_primary_context is not None
            or current.parked_work is not None or current.pending_intake is not None
            or current.deferred_side_request is not None
        ):
            raise ServiceError("next Slice requires an empty active slot; manage existing work explicitly")
        change = Change(change_id, feature.id, slice_title, Classification.FEATURE)
        # The new Feature companion binds this Change to its accepted origin;
        # its statement describes the new Slice, not a replacement of the old Feature.
        feature_agreement = AgreementRecord(
            feature_ref, "FEATURE", feature.id, change_id, feature.title,
            statement, acceptance.source, acceptance.source_reference, root_identity,
        )
        slice_agreement = AgreementRecord(
            slice_ref, "SLICE", feature.id, change_id, slice_title,
            statement, acceptance.source, acceptance.source_reference, root_identity,
            task_id, initial_task, checkpoint,
        )
        criteria_revision = SliceCriteriaRevision(
            f"slice-criteria-{digest}-1", slice_ref, root_identity,
            criteria.outcome, criteria.example, criteria.inclusions, criteria.exclusions,
            criteria.decisions, criteria.criteria, acceptance.source, acceptance.source_reference,
        )
        candidate = _record_change(current, change)
        assert candidate.active_context is not None
        candidate = replace(
            candidate,
            active_context=replace(
                candidate.active_context, focus=slice_title, current_work=initial_task,
                checkpoints=(Checkpoint(checkpoint, False),),
                feature_agreement_ref=feature_ref, slice_agreement_ref=slice_ref,
            ),
            agreements=current.agreements + (feature_agreement, slice_agreement),
            slice_criteria_revisions=current.slice_criteria_revisions + (criteria_revision,),
            slice_criteria_heads=current.slice_criteria_heads + (SliceCriteriaHead(slice_ref, criteria_revision.id),),
        )
        saved = _prepare_state_publication(_validated(candidate, "next Slice was rejected"), current.revision)
        try:
            self.__persistence.publish_next_slice(saved, change, (feature_agreement, slice_agreement), criteria_revision)
        except AtomicFailure as error:
            self.__raise_e1_publication_error(current.revision, error)
        return saved

    def revise_slice_criteria(
        self, state: State, criteria: SliceCriteriaSpec, previous_revision_id: str,
        reason: str, decision: UserAcceptance,
    ) -> State:
        """Supersede accepted Slice criteria via one explicit human decision and CAS."""
        current = _authoritative_state(self.__persistence, state)
        if current.initialization is None or current.active_context is None:
            raise ServiceError("an accepted active Slice is required for criterion amendment")
        slice_ref = current.active_context.slice_agreement_ref
        if slice_ref is None:
            raise ServiceError("an accepted Slice reference is required for criterion amendment")
        agreement = next((item for item in current.agreements if item.id == slice_ref), None)
        if agreement is None or agreement.kind != "SLICE":
            raise ServiceError("Slice criteria cannot amend unaccepted scope")
        root_identity = sha256(str(self.__repository_root).encode("utf-8")).hexdigest()[:16]
        if agreement.repository_id != root_identity:
            raise ServiceError("Slice criteria repository binding does not match the current root")
        head = next((item for item in current.slice_criteria_heads if item.slice_agreement_ref == slice_ref), None)
        authoritative_previous = "NOT_RECORDED" if head is None else head.revision_id
        if previous_revision_id != authoritative_previous:
            raise ServiceError("Slice criteria revision changed; refresh before deciding")
        statement = slice_criteria_decision_statement(previous_revision_id, reason, criteria)
        expected_ref = f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}"
        if (
            type(decision) is not UserAcceptance
            or decision.source != "INTERACTIVE_TERMINAL"
            or decision.statement != statement
            or decision.source_reference != expected_ref
        ):
            raise ServiceError("criteria amendment requires its own explicit terminal decision")
        digest = sha256(json.dumps([
            root_identity, slice_ref, previous_revision_id, statement, expected_ref,
        ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
        revision = SliceCriteriaRevision(
            f"slice-criteria-{digest}", slice_ref, root_identity,
            criteria.outcome, criteria.example, criteria.inclusions, criteria.exclusions,
            criteria.decisions, criteria.criteria,
            "INTERACTIVE_TERMINAL_DECISION", expected_ref,
            None if head is None else head.revision_id, reason, statement,
        )
        new_heads = tuple(item for item in current.slice_criteria_heads if item.slice_agreement_ref != slice_ref)
        candidate = replace(
            current,
            slice_criteria_revisions=current.slice_criteria_revisions + (revision,),
            slice_criteria_heads=new_heads + (SliceCriteriaHead(slice_ref, revision.id),),
        )
        saved = _prepare_state_publication(candidate, current.revision)
        try:
            self.__persistence.publish_slice_criteria_revision(saved, revision)
        except AtomicFailure as error:
            raise ServiceError("Slice criteria amendment publication was rejected") from error
        return saved
    def e1_upgrade_statement(self, state: State) -> str:
        """Show the exact authority-bound decision the human must confirm."""
        current = _authoritative_state(self.__persistence, state)
        if self.__persistence.e1_schema_ready():
            raise ServiceError("accepted E1 authority already uses schema 14")
        _, feature, slice_record = _accepted_e1_scope(current, self.__repository_root)
        assert current.initialization is not None
        return (
            f"Autorizo migrar E1 a schema 14; repository: {current.initialization.repository_id}; "
            f"revision: {current.revision}; Feature: {feature.id}; Slice: {slice_record.id}; "
            f"Task: {slice_record.task_id}; source-sha256: {self.__persistence.state_source_sha256()}"
        )

    def confirmed_e1_upgrade(self, source_revision: int) -> State | None:
        """Recognize an already committed conversion using its immutable in-state receipt."""
        current = self.__persistence.recover()
        receipt = current.e1_schema_upgrade
        if (type(source_revision) is int and source_revision >= 0 and receipt is not None
                and receipt.source_revision == source_revision and self.__persistence.e1_schema_ready()):
            return current
        return None

    def upgrade_e1_schema(self, state: State, acceptance: UserAcceptance) -> State:
        """Convert accepted E1 schema 13 only after a revision-bound terminal decision."""
        current = _authoritative_state(self.__persistence, state)
        _, feature, slice_record = _accepted_e1_scope(current, self.__repository_root)
        statement = self.e1_upgrade_statement(current)
        token = sha256(statement.encode("utf-8")).hexdigest()[:12]
        if (type(acceptance) is not UserAcceptance or acceptance.source != "INTERACTIVE_TERMINAL"
                or acceptance.statement != statement or acceptance.source_reference != f"tty:{token}"):
            raise ServiceError("schema upgrade requires exact attributable human confirmation")

        def convert(locked: State) -> State:
            # Rebind the accepted identity and revision while holding the writer lock.
            _accepted_e1_scope(locked, self.__repository_root)
            if locked != current:
                raise ServiceError("accepted E1 authority changed before schema conversion")
            assert locked.initialization is not None
            receipt = E1SchemaUpgradeReceipt(
                locked.revision, locked.revision + 1, source_digest,
                acceptance.statement, acceptance.source, acceptance.source_reference,
                locked.initialization.repository_id, feature.id, slice_record.id,
            )
            return _prepare_state_publication(replace(locked, e1_schema_upgrade=receipt), locked.revision)

        try:
            # A concurrent in-place rewrite with the same revision invalidates consent too.
            source_digest = statement.rsplit("source-sha256: ", 1)[1]
            return self.__persistence.transact_e1(
                current.revision, convert, upgrade=True, source_digest=source_digest,
            )
        except AtomicFailure as error:
            self.__raise_e1_publication_error(current.revision, error)

    def record_accepted_task_progress(
        self, state: State, repository_id: str, task_id: str,
        feature_agreement_ref: str, slice_agreement_ref: str,
        checkpoint: str, statement: str, evidence_refs: tuple[str, ...],
    ) -> State:
        """Commit one unverified Task report and in-memory lifecycle transitions in one CAS."""
        current = _authoritative_state(self.__persistence, state)
        values = (repository_id, task_id, feature_agreement_ref, slice_agreement_ref, checkpoint, statement)
        if any(type(item) is not str or not item.strip() or len(item) > 2048 for item in values):
            raise ServiceError("E1 Task progress requires bounded nonblank identity and text")
        if (type(evidence_refs) is not tuple or len(evidence_refs) > 16
                or len(set(evidence_refs)) != len(evidence_refs)
                or any(type(item) is not str or not item.strip() or len(item) > 2048 for item in evidence_refs)):
            raise ServiceError("E1 evidence references must be bounded distinct leads")

        def record(locked: State) -> State:
            context, feature, slice_record = _accepted_e1_scope(locked, self.__repository_root)
            if (locked != current or repository_id != slice_record.repository_id
                    or feature_agreement_ref != feature.id or slice_agreement_ref != slice_record.id
                    or task_id != slice_record.task_id):
                raise ServiceError("E1 Task identity, root, agreement or revision does not match authority")
            if _evaluate_autonomy(context, "progress", (), False).authorization is not AutonomyAuthorization.ALLOWED:
                raise ServiceError("E1 Task progress is outside the current autonomy budget or permissions")
            if context.unresolved_material_decision:
                raise ServiceError("E1 Task progress cannot bypass an unresolved human decision")
            pair = (context.status, context.phase)
            try:
                if pair == (LifecycleState.PROPOSED, Phase.RESEARCH):
                    pair = advance(*pair, LifecycleState.READY, Phase.READY,
                                   context.unresolved_material_decision, context.review, context.verification,
                                   context.convergence_complete)
                if pair == (LifecycleState.READY, Phase.READY):
                    pair = advance(*pair, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT,
                                   context.unresolved_material_decision, context.review, context.verification,
                                   context.convergence_complete)
            except LifecycleError as error:
                raise ServiceError("E1 Task progress lifecycle transition is invalid") from error
            if pair != (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT):
                raise ServiceError("E1 Task progress is permitted only during implementation")
            report = TaskProgress(
                task_id, checkpoint.strip(), statement.strip(), "REPORTED_UNVERIFIED",
                # Same-user protocol clients cannot prove they were invoked by the host.
                "UNKNOWN", None, evidence_refs, locked.revision + 1,
            )
            updated = replace(
                context, status=pair[0], phase=pair[1], task_progress=report,
                current_action="accepted Task progress reported",
                resume_point=None,
                autonomy_metrics=replace(context.autonomy_metrics,
                                         automatic_operations=context.autonomy_metrics.automatic_operations + 1),
            )
            return _prepare_state_publication(replace(locked, active_context=updated), locked.revision)

        try:
            return self.__persistence.transact_e1(current.revision, record)
        except AtomicFailure as error:
            self.__raise_e1_publication_error(current.revision, error)

    def r3_upgrade_statement(self, state: State) -> str:
        """Bind terminal approval to exact schema-15 bytes and repository authority."""
        current = _authoritative_state(self.__persistence, state)
        if self.__persistence.r3_schema_ready():
            raise ServiceError("R3 schema 16 is already enabled")
        if not self.__persistence.r3_upgrade_eligible() or current.initialization is None:
            raise ServiceError("R3 upgrade requires initialized schema-15 authority")
        _, _, accepted = _accepted_e1_scope(current, self.__repository_root)
        if not any(head.slice_agreement_ref == accepted.id for head in current.slice_criteria_heads):
            raise ServiceError("R3 requires a current human-accepted criterion revision")
        return (f"Acepto habilitar verificaciones R3-A desde revision {current.revision}; "
                f"sha256: {self.__persistence.state_source_sha256()}")

    def confirmed_r3_upgrade(self, source_revision: int) -> State | None:
        """Support safe read-only recovery of a previously committed conversion."""
        current = self.__persistence.recover()
        receipt = current.r3_schema_upgrade
        if (type(source_revision) is int and receipt is not None
                and receipt.source_revision == source_revision and self.__persistence.r3_schema_ready()):
            return current
        return None

    def upgrade_r3_schema(self, state: State, decision: UserAcceptance) -> State:
        """Allow R3 writes only after exact interactive human schema consent."""
        current = _authoritative_state(self.__persistence, state)
        statement = self.r3_upgrade_statement(current)
        source_digest = statement.rsplit("sha256: ", 1)[1]
        expected_ref = f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}"
        if (type(decision) is not UserAcceptance or decision.source != "INTERACTIVE_TERMINAL"
                or decision.statement != statement or decision.source_reference != expected_ref):
            raise ServiceError("R3 migration requires its exact interactive terminal decision")

        def convert(locked: State) -> State:
            if locked != current or locked.initialization is None:
                raise ServiceError("R3 conversion authority changed after user approval")
            receipt = R3SchemaUpgradeReceipt(
                locked.revision, locked.revision + 1, source_digest, statement,
                expected_ref, locked.initialization.repository_id,
            )
            return _prepare_state_publication(replace(locked, r3_schema_upgrade=receipt), locked.revision)

        try:
            return self.__persistence.transact_r3(
                current.revision, convert, upgrade=True, source_digest=source_digest,
            )
        except AtomicFailure as error:
            self.__raise_e1_publication_error(current.revision, error)

    def r3b_upgrade_statement(
        self,
        state: State,
        declarations: tuple[CriterionCoverageDeclaration, ...],
    ) -> str:
        """Display exact explicit root-input declarations before schema-17 consent."""
        current = _authoritative_state(self.__persistence, state)
        if self.__persistence.r3b_schema_ready():
            raise ServiceError("R3-B schema 17 is already enabled")
        if (
            not self.__persistence.r3b_upgrade_eligible()
            or current.initialization is None
            or current.r3_schema_upgrade is None
        ):
            raise ServiceError("R3-B upgrade requires explicitly converted schema-16 authority")
        snapshot_state, snapshot = self.__persistence.input_snapshot(current.revision)
        if snapshot_state != current:
            raise ServiceError("R3-B source authority changed while preparing its approval")
        normalized = _normalize_coverage_declarations(
            current, self.__repository_root, snapshot, declarations,
        )
        _, _, accepted = _accepted_e1_scope(current, self.__repository_root)
        head = next(
            (item for item in current.slice_criteria_heads if item.slice_agreement_ref == accepted.id),
            None,
        )
        if head is None:
            raise ServiceError("R3-B requires a current human-accepted criteria head")
        contract_json = coverage_declarations_json(normalized)
        contract_digest = sha256(contract_json.encode("utf-8")).hexdigest()
        return (
            f"Acepto habilitar aceptación objetiva R3-B desde revision {current.revision}; "
            f"repository: {current.initialization.repository_id}; Slice: {accepted.id}; "
            f"criterios: {head.revision_id}; policy: {R3B_APPROVAL_POLICY_TEXT}; "
            f"root-input-fingerprint: {snapshot.fingerprint}; "
            f"coverage-contract-sha256: {contract_digest}; "
            f"coverage-contract-json: {contract_json}; "
            f"sha256: {self.__persistence.state_source_sha256()}"
        )

    def confirmed_r3b_upgrade(self, source_revision: int) -> State | None:
        current = self.__persistence.recover()
        receipt = current.r3b_schema_upgrade
        if (
            type(source_revision) is int
            and receipt is not None
            and receipt.source_revision == source_revision
            and self.__persistence.r3b_schema_ready()
        ):
            return current
        return None

    def upgrade_r3b_schema(
        self,
        state: State,
        decision: UserAcceptance,
        declarations: tuple[CriterionCoverageDeclaration, ...],
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        statement = self.r3b_upgrade_statement(current, declarations)
        source_digest = statement.rsplit("sha256: ", 1)[1]
        if self.__persistence.state_source_sha256() != source_digest:
            raise ServiceError("R3-B source authority changed after approval was displayed")
        expected_ref = f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}"
        if (
            type(decision) is not UserAcceptance
            or decision.source != "INTERACTIVE_TERMINAL"
            or decision.statement != statement
            or decision.source_reference != expected_ref
        ):
            raise ServiceError("R3-B migration requires its exact interactive terminal decision")

        snapshot_state, snapshot = self.__persistence.input_snapshot(current.revision)
        if snapshot_state != current or f"root-input-fingerprint: {snapshot.fingerprint};" not in statement:
            raise ServiceError("R3-B root inputs changed after migration approval")
        normalized = _normalize_coverage_declarations(
            current, self.__repository_root, snapshot, declarations,
        )
        contract_digest = _coverage_contract_digest(normalized)
        _, _, accepted = _accepted_e1_scope(current, self.__repository_root)
        head = next(
            (item for item in current.slice_criteria_heads if item.slice_agreement_ref == accepted.id),
            None,
        )
        if head is None or current.initialization is None:
            raise ServiceError("R3-B migration requires the current accepted criteria head")

        def convert(locked: State) -> State:
            locked_scope = _accepted_e1_scope(locked, self.__repository_root)
            locked_head = next(
                (item for item in locked.slice_criteria_heads if item.slice_agreement_ref == locked_scope[2].id),
                None,
            )
            if locked != current or locked.initialization is None or locked_head != head:
                raise ServiceError("R3-B authority changed after human approval")
            receipt = R3BSchemaUpgradeReceipt(
                locked.revision,
                locked.revision + 1,
                source_digest,
                statement,
                expected_ref,
                locked.initialization.repository_id,
                accepted.id,
                head.revision_id,
                snapshot.fingerprint,
                contract_digest,
            )
            approvals = _coverage_approvals(
                locked, self.__repository_root, snapshot, normalized,
                statement, expected_ref, locked.revision, locked.revision + 1,
            )
            return _prepare_state_publication(
                replace(
                    locked,
                    r3b_schema_upgrade=receipt,
                    criterion_coverage_approvals=locked.criterion_coverage_approvals + approvals,
                ),
                locked.revision,
            )

        try:
            return self.__persistence.transact_r3b(
                current.revision, convert, upgrade=True, source_digest=source_digest,
                expected_input_fingerprint=snapshot.fingerprint,
            )
        except AtomicFailure as error:
            self.__raise_e1_publication_error(current.revision, error)

    def r3b_coverage_renewal_statement(
        self,
        state: State,
        declarations: tuple[CriterionCoverageDeclaration, ...],
    ) -> str:
        """Display an exact current-root proposal for TTY-only coverage renewal."""
        current = _authoritative_state(self.__persistence, state)
        if current.r3b_schema_upgrade is None or not self.__persistence.r3b_schema_ready():
            raise ServiceError("coverage renewal requires schema-17 authority")
        snapshot_state, snapshot = self.__persistence.input_snapshot(current.revision)
        if snapshot_state != current:
            raise ServiceError("coverage renewal authority changed while preparing approval")
        normalized = _normalize_coverage_declarations(
            current, self.__repository_root, snapshot, declarations,
        )
        _, _, accepted = _accepted_e1_scope(current, self.__repository_root)
        head = next(
            (item for item in current.slice_criteria_heads if item.slice_agreement_ref == accepted.id),
            None,
        )
        if head is None or current.initialization is None:
            raise ServiceError("coverage renewal requires the current accepted criteria head")
        source_digest = self.__persistence.state_source_sha256()
        contract_json = coverage_declarations_json(normalized)
        contract_digest = sha256(contract_json.encode("utf-8")).hexdigest()
        return (
            f"Acepto renovar cobertura R3-B desde revision {current.revision}; "
            f"repository: {current.initialization.repository_id}; Slice: {accepted.id}; "
            f"criterios: {head.revision_id}; policy: {R3B_APPROVAL_POLICY_TEXT}; "
            f"root-input-fingerprint: {snapshot.fingerprint}; "
            f"coverage-contract-sha256: {contract_digest}; "
            f"coverage-contract-json: {contract_json}; state-sha256: {source_digest}"
        )

    def renew_r3b_coverage(
        self,
        state: State,
        decision: UserAcceptance,
        declarations: tuple[CriterionCoverageDeclaration, ...],
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        statement = self.r3b_coverage_renewal_statement(current, declarations)
        source_digest = statement.rsplit("state-sha256: ", 1)[1]
        if self.__persistence.state_source_sha256() != source_digest:
            raise ServiceError("coverage source authority changed after approval was displayed")
        expected_ref = f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}"
        if (
            type(decision) is not UserAcceptance
            or decision.source != "INTERACTIVE_TERMINAL"
            or decision.statement != statement
            or decision.source_reference != expected_ref
            or not statement.endswith(f"state-sha256: {source_digest}")
        ):
            raise ServiceError("coverage renewal requires its exact interactive terminal decision")
        snapshot_state, snapshot = self.__persistence.input_snapshot(current.revision)
        if snapshot_state != current or f"root-input-fingerprint: {snapshot.fingerprint};" not in statement:
            raise ServiceError("coverage root inputs changed after terminal approval")
        normalized = _normalize_coverage_declarations(
            current, self.__repository_root, snapshot, declarations,
        )

        def renew(locked: State) -> State:
            if locked != current or locked.r3b_schema_upgrade is None:
                raise ServiceError("coverage renewal authority changed after human approval")
            new_approvals = _coverage_approvals(
                locked, self.__repository_root, snapshot, normalized,
                statement, expected_ref, locked.revision, locked.revision + 1,
            )
            existing = {item.id: item for item in locked.criterion_coverage_approvals}
            additions = tuple(
                item for item in new_approvals
                if item.id not in existing
            )
            if any(
                existing[item.id].contract_digest != item.contract_digest
                for item in new_approvals if item.id in existing
            ):
                raise ServiceError("coverage approval identity conflicts with immutable history")
            if not additions:
                return locked
            return _prepare_state_publication(
                replace(
                    locked,
                    criterion_coverage_approvals=locked.criterion_coverage_approvals + additions,
                ),
                locked.revision,
            )

        try:
            return self.__persistence.transact_r3b(
                current.revision,
                renew,
                expected_input_fingerprint=snapshot.fingerprint,
                source_digest=source_digest,
                allow_noop=True,
            )
        except AtomicFailure as error:
            self.__raise_e1_publication_error(current.revision, error)

    def current_r3b_coverage_approvals(
        self,
        state: State,
        snapshot: InputSnapshot,
    ) -> tuple[CriterionCoverageApproval, ...]:
        current = _authoritative_state(self.__persistence, state)
        return self._current_r3b_coverage_approvals(current, snapshot)

    def _current_r3b_coverage_approvals(
        self,
        current: State,
        snapshot: InputSnapshot,
    ) -> tuple[CriterionCoverageApproval, ...]:
        if current.r3b_schema_upgrade is None or not self.__persistence.r3b_schema_ready():
            return ()
        try:
            _, _, accepted = _accepted_e1_scope(current, self.__repository_root)
        except ServiceError:
            return ()
        head = next(
            (item for item in current.slice_criteria_heads if item.slice_agreement_ref == accepted.id),
            None,
        )
        criteria = None if head is None else next(
            (item for item in current.slice_criteria_revisions if item.id == head.revision_id),
            None,
        )
        if criteria is None or head is None or snapshot.repository_id != accepted.repository_id:
            return ()
        file_paths = set(snapshot.file_paths)
        directory_paths = set(snapshot.directory_paths)
        result: list[CriterionCoverageApproval] = []
        for criterion in criteria.criteria:
            kind, separator, target = criterion.expected_evidence.partition(":")
            if not separator:
                continue
            approval = next(
                (
                    item for item in reversed(current.criterion_coverage_approvals)
                    if item.repository_id == accepted.repository_id
                    and item.slice_agreement_ref == accepted.id
                    and item.criteria_revision_id == head.revision_id
                    and item.criterion_id == criterion.id
                    and item.test_kind == kind
                    and item.test_target == target
                    and item.root_fingerprint == snapshot.fingerprint
                    and item.input_policy_id == INPUT_POLICY_ID
                    and item.input_policy_digest == INPUT_POLICY_DIGEST
                    and item.admission_policy_id == TEST_ADMISSION_POLICY_ID
                    and item.admission_policy_digest == TEST_ADMISSION_POLICY_DIGEST
                    and item.environment_profile_id == TEST_ENVIRONMENT_PROFILE_ID
                    and item.environment_profile_policy_digest == TEST_ENVIRONMENT_PROFILE_DIGEST
                    and (
                        not snapshot.file_paths and not snapshot.directory_paths
                        or all(
                            known.path in (
                                file_paths if known.kind == "root-file" else directory_paths
                            )
                            for known in item.known_inputs
                        )
                    )
                ),
                None,
            )
            if approval is not None:
                result.append(approval)
        return tuple(result)

    def input_snapshot(
        self, expected_revision: int | None = None,
    ) -> tuple[State, InputSnapshot]:
        try:
            return self.__persistence.input_snapshot(expected_revision)
        except AtomicFailure as error:
            if "revision is stale" in str(error):
                actual = self.__persistence.recover().revision
                raise StaleRevisionError(-1 if expected_revision is None else expected_revision, actual) from error
            raise ServiceError(str(error)) from error

    def record_criterion_test(
        self, state: State, repository_id: str, slice_agreement_ref: str,
        criteria_revision_id: str, criterion_id: str, test_kind: str,
        test_target: str, exit_code: int, output_sha256: str,
    ) -> State:
        """Record one host-reported test observation against the current accepted head."""
        current = _authoritative_state(self.__persistence, state)
        values = (repository_id, slice_agreement_ref, criteria_revision_id, criterion_id,
                  test_kind, test_target, output_sha256)
        if any(type(item) is not str or not item or len(item) > 2048 for item in values):
            raise ServiceError("R3 test result identity must be bounded and nonblank")
        if type(exit_code) is not int or not 0 <= exit_code <= 255:
            raise ServiceError("R3 test process exit code is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", output_sha256):
            raise ServiceError("R3 test output must have an exact SHA256 digest")

        def record(locked: State) -> State:
            if locked != current or locked.r3_schema_upgrade is None:
                raise ServiceError("R3 requires exact converted schema-16 authority")
            context, _, accepted = _accepted_e1_scope(locked, self.__repository_root)
            if repository_id != accepted.repository_id or slice_agreement_ref != accepted.id:
                raise ServiceError("R3 test result does not match the accepted repository and Slice")
            head = next((item for item in locked.slice_criteria_heads if item.slice_agreement_ref == accepted.id), None)
            if head is None or head.revision_id != criteria_revision_id:
                raise ServiceError("R3 criterion revision is not the current accepted head")
            revision = next((item for item in locked.slice_criteria_revisions if item.id == head.revision_id), None)
            criterion = next((item for item in revision.criteria if item.id == criterion_id), None) if revision else None
            if criterion is None or criterion.expected_evidence != f"{test_kind}:{test_target}":
                raise ServiceError("R3 test does not match the human-accepted evidence descriptor")
            if test_kind not in ("python-unittest", "node-test"):
                raise ServiceError("R3 only supports host-run deterministic test evidence")
            digest = sha256(json.dumps([
                accepted.id, criteria_revision_id, criterion_id, test_kind,
                test_target, exit_code, output_sha256, locked.revision,
            ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
            observation = CriterionTestReceipt(
                f"criterion-test-{digest}", accepted.id, criteria_revision_id,
                criterion_id, test_kind, test_target, exit_code, output_sha256,
                locked.revision, locked.revision + 1,
            )
            # A test result does not approve the Slice, change legacy verification,
            # or claim the model itself can attest to host execution.
            return _prepare_state_publication(replace(
                locked, criterion_test_receipts=locked.criterion_test_receipts + (observation,),
            ), locked.revision)

        try:
            if current.r3b_schema_upgrade is None:
                return self.__persistence.transact_r3(current.revision, record)
            _, snapshot = self.__persistence.input_snapshot(current.revision)
            return self.__persistence.transact_r3b(
                current.revision,
                record,
                expected_input_fingerprint=snapshot.fingerprint,
            )
        except AtomicFailure as error:
            self.__raise_e1_publication_error(current.revision, error)

    def record_criterion_test_r3b(
        self,
        state: State,
        repository_id: str,
        slice_agreement_ref: str,
        criteria_revision_id: str,
        criterion_id: str,
        test_kind: str,
        test_target: str,
        exit_code: int,
        output_sha256: str,
        input_fingerprint_before: str,
        input_fingerprint_after: str,
        input_policy_id: str,
        input_policy_digest: str,
        admission_policy_id: str,
        admission_policy_digest: str,
        runtime_executable: str,
        runtime_executable_sha256: str,
        runtime_version: str,
        runtime_argv: tuple[str, ...],
        environment_profile_id: str,
        environment_profile_digest: str,
        coverage_approval_id: str,
        coverage_contract_digest: str,
    ) -> State:
        """Record one fresh test only after Core rechecks its post-test workspace."""
        current = _authoritative_state(self.__persistence, state)
        for label, value in (
            ("repository", repository_id), ("Slice", slice_agreement_ref),
            ("criteria revision", criteria_revision_id), ("criterion", criterion_id),
            ("test kind", test_kind), ("test target", test_target),
            ("input fingerprint", input_fingerprint_before), ("input fingerprint", input_fingerprint_after),
            ("input policy", input_policy_id), ("input policy digest", input_policy_digest),
            ("admission policy", admission_policy_id), ("admission policy digest", admission_policy_digest),
            ("runtime executable", runtime_executable), ("runtime digest", runtime_executable_sha256),
            ("runtime version", runtime_version), ("environment profile", environment_profile_id),
            ("environment digest", environment_profile_digest),
            ("coverage approval", coverage_approval_id),
            ("coverage contract digest", coverage_contract_digest),
        ):
            if type(value) is not str or not value or len(value) > 2048:
                raise ServiceError(f"R3-B {label} must be bounded nonblank text")
        if (
            type(exit_code) is not int or not 0 <= exit_code <= 255
            or not _SHA256.fullmatch(output_sha256)
            or not _SHA256.fullmatch(input_fingerprint_before)
            or input_fingerprint_before != input_fingerprint_after
            or input_policy_id != INPUT_POLICY_ID
            or input_policy_digest != INPUT_POLICY_DIGEST
            or admission_policy_id != TEST_ADMISSION_POLICY_ID
            or admission_policy_digest != TEST_ADMISSION_POLICY_DIGEST
            or environment_profile_id != TEST_ENVIRONMENT_PROFILE_ID
            or not _SHA256.fullmatch(environment_profile_digest)
            or not re.fullmatch(r"coverage-[0-9a-f]{20}", coverage_approval_id)
            or not _SHA256.fullmatch(coverage_contract_digest)
            or type(runtime_argv) is not tuple or any(type(item) is not str or not item for item in runtime_argv)
            or not _supported_test_descriptor(test_kind, test_target)
        ):
            raise ServiceError("R3-B test provenance is unsupported or incomplete")
        external_inputs: tuple[str, ...] = ()
        if not Path(runtime_executable).is_absolute() or not _SHA256.fullmatch(runtime_executable_sha256):
            raise ServiceError("R3-B runtime identity is invalid")
        try:
            expected_environment = sanitized_test_environment(
                self.__repository_root, runtime_executable,
            )
            expected_environment_digest = test_environment_profile_digest(expected_environment)
        except InputFingerprintError as error:
            raise ServiceError("R3-B sanitized environment profile is unsupported") from error
        if environment_profile_digest != expected_environment_digest:
            raise ServiceError("R3-B sanitized environment profile digest is invalid")
        _, observed_snapshot = self.__persistence.input_snapshot(current.revision)
        if observed_snapshot.fingerprint != input_fingerprint_before:
            raise ServiceError("R3-B workspace fingerprint changed before test evidence recording")
        matching_approval = next(
            (
                item for item in self.current_r3b_coverage_approvals(current, observed_snapshot)
                if item.id == coverage_approval_id
                and item.contract_digest == coverage_contract_digest
                and item.root_fingerprint == input_fingerprint_before
                and item.criterion_id == criterion_id
                and item.test_kind == test_kind
                and item.test_target == test_target
            ),
            None,
        )
        if matching_approval is None:
            raise ServiceError("R3-B test has no current human-approved known-input contract")
        if test_kind == "python-unittest":
            expected_runtime = str(Path(sys.executable).resolve())
            expected_argv = (
                "-I", "-B", "-c", _PYTHON_TEST_RUNNER,
                str(self.__repository_root), test_target,
            )
            if (
                runtime_executable != expected_runtime
                or runtime_version != f"Python {platform.python_version()}"
                or runtime_argv != expected_argv
                or _runtime_digest(expected_runtime) != runtime_executable_sha256
            ):
                raise ServiceError("R3-B Python runtime or exact test argv is unsupported")
        else:
            version = re.fullmatch(r"v([0-9]+)\.[0-9]+\.[0-9]+(?:[-+].*)?", runtime_version)
            if (
                version is None or int(version.group(1)) < 18
                or Path(runtime_executable).name.casefold() not in ("node", "node.exe")
                or runtime_argv != ("--test", test_target)
                or _runtime_digest(runtime_executable) != runtime_executable_sha256
                or _runtime_version(runtime_executable, self.__repository_root) != runtime_version
            ):
                raise ServiceError("R3-B Node runtime or exact test argv is unsupported")

        def record(locked: State) -> State:
            if locked != current or locked.r3b_schema_upgrade is None:
                raise ServiceError("R3-B requires exact converted schema-17 authority")
            _, _, accepted = _accepted_e1_scope(locked, self.__repository_root)
            head = next(
                (item for item in locked.slice_criteria_heads if item.slice_agreement_ref == accepted.id),
                None,
            )
            revision = None if head is None else next(
                (item for item in locked.slice_criteria_revisions if item.id == head.revision_id),
                None,
            )
            criterion = None if revision is None else next(
                (item for item in revision.criteria if item.id == criterion_id), None,
            )
            if (
                repository_id != accepted.repository_id
                or slice_agreement_ref != accepted.id
                or head is None or head.revision_id != criteria_revision_id
                or criterion is None
                or criterion.expected_evidence != f"{test_kind}:{test_target}"
            ):
                raise ServiceError("R3-B test does not match the current accepted Slice criterion")
            digest = sha256(json.dumps([
                accepted.id, criteria_revision_id, criterion_id, test_kind, test_target,
                exit_code, output_sha256, locked.revision, input_fingerprint_before,
                input_policy_id, input_policy_digest, admission_policy_id,
                admission_policy_digest, list(external_inputs), runtime_executable,
                runtime_executable_sha256, runtime_version, list(runtime_argv),
                environment_profile_id, environment_profile_digest,
                coverage_approval_id, coverage_contract_digest,
            ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
            receipt = CriterionTestReceipt(
                f"criterion-test-{digest}", accepted.id, criteria_revision_id,
                criterion_id, test_kind, test_target, exit_code, output_sha256,
                locked.revision, locked.revision + 1,
                input_fingerprint_before, input_fingerprint_after, input_policy_id,
                input_policy_digest, admission_policy_id, admission_policy_digest,
                external_inputs, runtime_executable, runtime_executable_sha256,
                runtime_version, runtime_argv, environment_profile_id,
                environment_profile_digest,
                coverage_approval_id, coverage_contract_digest,
            )
            return _prepare_state_publication(replace(
                locked, criterion_test_receipts=locked.criterion_test_receipts + (receipt,),
            ), locked.revision)

        try:
            return self.__persistence.transact_r3b(
                current.revision,
                record,
                expected_input_fingerprint=input_fingerprint_after,
            )
        except AtomicFailure as error:
            self.__raise_e1_publication_error(current.revision, error)

    def _slice_outcome_candidate(
        self, state: State, snapshot: InputSnapshot,
    ) -> tuple[SliceOutcomeReceipt | None, tuple[str, ...]]:
        blockers: list[str] = []
        if state.r3b_schema_upgrade is None or not self.__persistence.r3b_schema_ready():
            return None, ("SCHEMA_17_REQUIRED",)
        if (
            type(snapshot) is not InputSnapshot
            or snapshot.version != INPUT_FINGERPRINT_VERSION
            or snapshot.policy_id != INPUT_POLICY_ID
            or snapshot.policy_digest != INPUT_POLICY_DIGEST
            or state.initialization is None
            or snapshot.repository_id != state.initialization.repository_id
        ):
            return None, ("WORKSPACE_SNAPSHOT_INVALID",)
        try:
            context, _, accepted = _accepted_e1_scope(state, self.__repository_root)
        except ServiceError:
            return None, ("ACCEPTED_SLICE_UNAVAILABLE",)
        head = next(
            (item for item in state.slice_criteria_heads if item.slice_agreement_ref == accepted.id),
            None,
        )
        criteria = None if head is None else next(
            (item for item in state.slice_criteria_revisions if item.id == head.revision_id),
            None,
        )
        if criteria is None or head is None:
            return None, ("CURRENT_CRITERIA_HEAD_UNAVAILABLE",)
        if (
            context.unresolved_material_decision
            or context.safety_facts.unresolved_decision_details
            or context.safety_facts.material_clarifications
        ):
            blockers.append("MATERIAL_DECISION_PENDING")
        relevant_drifts = tuple(
            item for item in state.drifts if item.change_id == context.change_id
        )
        if any(item.unresolved for item in relevant_drifts):
            blockers.append("RELEVANT_DRIFT_UNRESOLVED")

        latest: dict[str, CriterionTestReceipt] = {}
        for receipt in state.criterion_test_receipts:
            if (
                receipt.slice_agreement_ref == accepted.id
                and receipt.criteria_revision_id == head.revision_id
            ):
                latest[receipt.criterion_id] = receipt

        evidence_ids: list[str] = []
        approved = {
            item.criterion_id: item
            for item in self._current_r3b_coverage_approvals(state, snapshot)
        }
        for criterion in criteria.criteria:
            descriptor_kind, separator, descriptor_target = criterion.expected_evidence.partition(":")
            if not separator or not _supported_test_descriptor(descriptor_kind, descriptor_target):
                blockers.append(f"UNSUPPORTED_CRITERION:{criterion.id}")
                continue
            approval = approved.get(criterion.id)
            if approval is None:
                blockers.append(f"COVERAGE_APPROVAL_REQUIRED:{criterion.id}")
                continue
            receipt = latest.get(criterion.id)
            if receipt is None:
                blockers.append(f"CRITERION_UNVERIFIED:{criterion.id}")
                continue
            if receipt.exit_code != 0:
                blockers.append(f"CRITERION_TEST_FAILED:{criterion.id}")
                continue
            if (
                receipt.input_fingerprint_before != snapshot.fingerprint
                or receipt.input_fingerprint_after != snapshot.fingerprint
                or receipt.input_policy_id != INPUT_POLICY_ID
                or receipt.input_policy_digest != INPUT_POLICY_DIGEST
                or receipt.admission_policy_id != TEST_ADMISSION_POLICY_ID
                or receipt.admission_policy_digest != TEST_ADMISSION_POLICY_DIGEST
                or receipt.environment_profile_id != TEST_ENVIRONMENT_PROFILE_ID
                or receipt.external_inputs
                or receipt.coverage_approval_id != approval.id
                or receipt.coverage_contract_digest != approval.contract_digest
                or approval.root_fingerprint != snapshot.fingerprint
                or receipt.environment_profile_digest is None
                or not _SHA256.fullmatch(receipt.environment_profile_digest)
            ):
                blockers.append(f"CRITERION_PROVENANCE_STALE:{criterion.id}")
                continue
            expected_argv = (
                (
                    "-I", "-B", "-c", _PYTHON_TEST_RUNNER,
                    str(self.__repository_root), descriptor_target,
                )
                if descriptor_kind == "python-unittest"
                else ("--test", descriptor_target)
            )
            runtime_valid = (
                receipt.runtime_executable is not None
                and receipt.runtime_executable_sha256 is not None
                and receipt.runtime_version is not None
                and receipt.runtime_argv == expected_argv
            )
            if runtime_valid and descriptor_kind == "python-unittest":
                runtime_valid = (
                    receipt.runtime_executable == str(Path(sys.executable).resolve())
                    and receipt.runtime_version == f"Python {platform.python_version()}"
                )
            elif runtime_valid:
                version = re.fullmatch(r"v([0-9]+)\.[0-9]+\.[0-9]+(?:[-+].*)?", receipt.runtime_version or "")
                runtime_valid = (
                    version is not None and int(version.group(1)) >= 18
                    and Path(receipt.runtime_executable or "").name.casefold() in ("node", "node.exe")
                )
            try:
                runtime_valid = bool(
                    runtime_valid
                    and _runtime_digest(receipt.runtime_executable or "")
                    == receipt.runtime_executable_sha256
                )
                if runtime_valid and descriptor_kind == "node-test":
                    runtime_valid = (
                        _runtime_version(receipt.runtime_executable or "", self.__repository_root)
                        == receipt.runtime_version
                    )
            except ServiceError:
                runtime_valid = False
            try:
                if runtime_valid:
                    expected_environment = sanitized_test_environment(
                        self.__repository_root, receipt.runtime_executable or "",
                    )
                    runtime_valid = (
                        test_environment_profile_digest(expected_environment)
                        == receipt.environment_profile_digest
                    )
            except InputFingerprintError:
                runtime_valid = False
            if not runtime_valid:
                blockers.append(f"CRITERION_RUNTIME_UNVERIFIABLE:{criterion.id}")
                continue
            evidence_ids.append(receipt.id)

        if blockers:
            return None, tuple(blockers)
        basis = "ALL_CRITERIA_FRESH_SUPPORTED_TESTS"
        source = "CORE_OBJECTIVE_ACCEPTANCE"
        drift_digest = _drift_identity_digest(state, context.change_id)
        coverage_digest = coverage_set_digest(tuple(approved.values()))
        receipt = SliceOutcomeReceipt(
            "",
            accepted.repository_id,
            accepted.id,
            head.revision_id,
            tuple(evidence_ids),
            snapshot.fingerprint,
            INPUT_POLICY_ID,
            INPUT_POLICY_DIGEST,
            TEST_ADMISSION_POLICY_ID,
            TEST_ADMISSION_POLICY_DIGEST,
            drift_digest,
            basis,
            source,
            state.revision,
            state.revision + 1,
            coverage_digest,
        )
        receipt = replace(receipt, id=slice_outcome_semantic_id(receipt))
        return receipt, ()

    def slice_outcome_eligibility(
        self, state: State, snapshot: InputSnapshot,
    ) -> SliceOutcomeEligibility:
        current = _authoritative_state(self.__persistence, state)
        candidate, blockers = self._slice_outcome_candidate(current, snapshot)
        context = current.active_context
        slice_ref = None if context is None else context.slice_agreement_ref
        history = tuple(
            item for item in current.slice_outcome_receipts
            if item.slice_agreement_ref == slice_ref
        )
        matching = None if candidate is None else next(
            (item for item in history if item.id == candidate.id),
            None,
        )
        latest_id = matching.id if matching is not None else (None if not history else history[-1].id)
        if matching is not None:
            status = "CURRENT"
        elif blockers:
            status = "STALE" if history else "BLOCKED"
        elif history:
            status = "STALE"
        else:
            status = "READY"
        return SliceOutcomeEligibility(status, latest_id, blockers)

    def accept_slice_outcome(self, state: State, snapshot: InputSnapshot) -> State:
        """Append one objective Slice outcome without changing lifecycle or Product Truth."""
        current = _authoritative_state(self.__persistence, state)

        def accept(locked: State) -> State:
            if locked != current or locked.r3b_schema_upgrade is None:
                raise ServiceError("R3-B acceptance requires exact schema-17 authority")
            receipt, blockers = self._slice_outcome_candidate(locked, snapshot)
            if receipt is None:
                raise ServiceError("Slice outcome is blocked: " + ", ".join(blockers))
            if any(item.id == receipt.id for item in locked.slice_outcome_receipts):
                return locked
            return _prepare_state_publication(
                replace(
                    locked,
                    slice_outcome_receipts=locked.slice_outcome_receipts + (receipt,),
                ),
                locked.revision,
            )

        try:
            return self.__persistence.transact_r3b(
                current.revision,
                accept,
                expected_input_fingerprint=snapshot.fingerprint,
                allow_noop=True,
            )
        except AtomicFailure as error:
            self.__raise_e1_publication_error(current.revision, error)

    def __raise_e1_publication_error(self, expected_revision: int, error: AtomicFailure) -> Never:
        """Preserve uncertainty; a failed fsync after replacement is not proof of rollback."""
        if "durability is uncertain" in str(error):
            raise DurabilityUncertainError(
                "E1_DURABILITY_UNKNOWN: state may have committed; recover and inspect revision before retry"
            ) from error
        actual_revision = self.__persistence.recover().revision
        if actual_revision != expected_revision:
            raise StaleRevisionError(expected_revision, actual_revision) from error
        raise ServiceError(str(error)) from error

    def record_capability_selection(self, state: State, selection: PendingCapabilitySelection) -> State:
        current = _authoritative_state(self.__persistence, state)
        if current.pending_capability_selection is not None:
            raise ServiceError("capability selection is already pending")
        candidates = _selection_candidates(current, selection.intent)
        if (
            selection.candidates != candidates
            or len(candidates) < 2
            or selection.id != _selection_id(selection.operation, selection.intent, candidates)
            or selection.provenance != tuple(f"feature:{item.feature_id}" for item in candidates)
        ):
            raise ServiceError("capability selection is not Core-issued")
        saved = _prepare_state_publication(
            replace(current, pending_capability_selection=selection), current.revision
        )
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("capability selection was rejected") from error
        return saved

    def select_capability(self, state: State, selection_id: str, choice: str) -> State:
        current = _authoritative_state(self.__persistence, state)
        pending = current.pending_capability_selection
        if pending is None or str(pending.id) != selection_id:
            raise ServiceError("capability selection is not pending")
        candidates = _selection_candidates(current, pending.intent)
        if (
            pending.candidates != candidates
            or pending.id != _selection_id(pending.operation, pending.intent, candidates)
            or pending.provenance != tuple(f"feature:{item.feature_id}" for item in candidates)
        ):
            raise ServiceError("capability selection is stale")
        offered = {str(item.feature_id) for item in candidates}
        if choice not in offered and not (
            pending.standalone_allowed and choice == "CREATE_STANDALONE_CAPABILITY"
        ):
            raise ServiceError("capability selection choice is not offered")
        if choice in offered:
            saved = _prepare_state_publication(
                replace(current, pending_capability_selection=None), current.revision
            )
            try:
                self.__persistence.publish_feature(saved)
            except AtomicFailure as error:
                raise ServiceError("capability selection was rejected") from error
            return saved
        token = sha256(f"{pending.id}:{pending.intent}".encode()).hexdigest()[:12]
        feature = Feature(FeatureId(f"feature-{token}"), pending.intent, LivingSpecId(f"spec-feature-{token}"))
        change = Change(ChangeId(f"change-{token}"), feature.id, pending.intent, Classification.FEATURE)
        selection = CapabilitySelection(pending.id, choice, pending.intent, (), feature.id, change.id)
        candidate = _record_change(
            _record_feature(replace(current, pending_capability_selection=None), feature), change
        )
        saved = _prepare_state_publication(
            replace(candidate, capability_selection_history=candidate.capability_selection_history + (selection,)),
            current.revision,
        )
        try:
            self.__persistence.publish_change(saved, change)
        except AtomicFailure as error:
            raise ServiceError("capability selection was rejected") from error
        return saved

    def record_discovery_recovery(self, state: State, recovery: PendingDiscoveryRecovery) -> State:
        current = _authoritative_state(self.__persistence, state)
        if current.pending_discovery_recovery is not None:
            raise ServiceError("discovery recovery is already pending")
        saved = _prepare_state_publication(
            replace(current, pending_discovery_recovery=recovery), current.revision
        )
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("discovery recovery was rejected") from error
        return saved

    def resolve_discovery_recovery(
        self,
        state: State,
        recovery_id: str,
        selection: PendingCapabilitySelection | None,
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        recovery = current.pending_discovery_recovery
        if recovery is None or str(recovery.id) != recovery_id:
            raise ServiceError("discovery recovery is not pending")
        if selection is not None:
            candidates = _selection_candidates(current, selection.intent)
            if (
                selection.candidates != candidates
                or len(candidates) < 2
                or selection.id != _selection_id(selection.operation, selection.intent, candidates)
                or selection.provenance != tuple(f"feature:{item.feature_id}" for item in candidates)
            ):
                raise ServiceError("capability selection is not Core-issued")
        saved = _prepare_state_publication(
            replace(
                current,
                pending_discovery_recovery=None,
                pending_capability_selection=selection,
            ),
            current.revision,
        )
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("discovery recovery resolution was rejected") from error
        return saved

    def recover(self) -> State:
        return self.__persistence.recover()

    def e1_schema_ready(self) -> bool:
        """Inspect the persisted writer format without changing Core authority."""
        return self.__persistence.e1_schema_ready()

    def mutation_state(self, expected_revision: ExpectedRevision) -> State:
        """Recover authority only when the caller's mutation revision is current."""
        current = self.__persistence.recover()
        if type(expected_revision) is not int or expected_revision < 0:
            raise ServiceError("expected revision must be a nonnegative integer")
        if expected_revision != current.revision:
            raise StaleRevisionError(expected_revision, current.revision)
        return current

    def initialize(self, state: State, initialization: RepositoryInitialization) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_initialize(current, initialization), current.revision)
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("initialization was rejected") from error
        return saved

    def defer_side_request(self, state: State, request: DeferredSideRequest) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_defer_side_request(current, request), current.revision)
        try:
            self.__persistence.publish_primary_restoration(saved)
        except AtomicFailure as error:
            raise ServiceError("deferred side request was rejected") from error
        return saved

    def record_shared_intent(
        self, state: State, shared: SharedIntent, deferred: DeferredLinkedIntent
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _record_shared_intent(current, shared, deferred), current.revision
        )
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("shared intent was rejected") from error
        return saved

    def record_feature(self, state: State, feature: Feature) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _record_feature(current, feature), current.revision
        )
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

    def record_change(self, state: State, change: Change) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _record_change(current, change), current.revision
        )
        try:
            self.__persistence.publish_change(saved, change)
        except AtomicFailure as error:
            raise ServiceError("change publication was rejected") from error
        return saved

    def create_lock(
        self, state: State, lock: SemanticLock, revision: SemanticLockRevision
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_record_lock(current, lock, revision), current.revision)
        try:
            self.__persistence.publish_lock(saved)
        except AtomicFailure as error:
            raise ServiceError("semantic lock creation was rejected") from error
        return saved

    def revise_lock(self, state: State, revision: SemanticLockRevision) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_revise_lock(current, revision), current.revision)
        try:
            self.__persistence.publish_lock(saved)
        except AtomicFailure as error:
            raise ServiceError("semantic lock revision was rejected") from error
        return saved

    def retire_lock(self, state: State, release: LockRelease) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_retire_lock(current, release), current.revision)
        try:
            self.__persistence.publish_lock(saved)
        except AtomicFailure as error:
            raise ServiceError("semantic lock retirement was rejected") from error
        return saved

    def assess_locks(
        self, state: State, change_id: ChangeId, assessments: tuple[LockAssessment, ...]
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_assess_locks(current, change_id, assessments), current.revision)
        try:
            self.__persistence.publish_lock(saved)
        except AtomicFailure as error:
            raise ServiceError("lock assessment was rejected") from error
        return saved

    def authorize_lock(self, state: State, authorization: LockAuthorization) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_authorize_lock(current, authorization), current.revision)
        try:
            self.__persistence.publish_lock(saved)
        except AtomicFailure as error:
            raise ServiceError("lock authorization was rejected") from error
        return saved

    def record_decision(self, state: State, decision: Decision) -> State:
        current = _authoritative_state(self.__persistence, state)
        if decision.kind == "clarification" and current.active_context is not None:
            active = current.active_context
            current = replace(
                current,
                active_context=replace(
                    active,
                    autonomy_metrics=replace(
                        active.autonomy_metrics,
                        human_material_decisions=active.autonomy_metrics.human_material_decisions + 1,
                    ),
                ),
            )
        saved = _prepare_state_publication(
            _record_decision(current, decision), current.revision
        )
        try:
            self.__persistence.publish_decision(saved, decision)
        except AtomicFailure as error:
            raise ServiceError("decision publication was rejected") from error
        return saved

    def supersede_decision(self, state: State, replacement: Decision) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _supersede_decision(current, replacement), current.revision
        )
        try:
            self.__persistence.publish_decision_supersession(saved, replacement)
        except AtomicFailure as error:
            raise ServiceError("decision supersession publication was rejected") from error
        return saved

    def record_observation(self, state: State, observation: Observation) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _record_observation(current, observation), current.revision
        )
        try:
            self.__persistence.publish_observation(saved, observation)
        except AtomicFailure as error:
            raise ServiceError("observation publication was rejected") from error
        return saved

    def recover_legacy_non_material_detail(
        self,
        expected_revision: ExpectedRevision,
        repository_id: str,
        feature_id: str,
        change_id: str,
        legacy_detail: str,
    ) -> State:
        """Atomically record and remove one exact non-material legacy detail."""
        current = self.mutation_state(expected_revision)
        if any(
            type(value) is not str or not value.strip()
            for value in (repository_id, feature_id, change_id, legacy_detail)
        ):
            raise ServiceError("legacy recovery identity and detail must be nonblank strings")
        initialization = current.initialization
        context = current.active_context
        if initialization is None or context is None or context.change_id is None:
            raise ServiceError("legacy recovery requires an initialized active Change")
        if (
            repository_id != initialization.repository_id
            or feature_id != str(context.feature_id)
            or change_id != str(context.change_id)
            or not any(item.id == context.feature_id for item in current.features)
            or not any(item.id == context.change_id for item in current.changes)
        ):
            raise ServiceError("legacy recovery identity does not match the active repository, Feature and Change")
        details = context.safety_facts.unresolved_decision_details
        if (
            not context.unresolved_material_decision
            or not details
            or details[0] != legacy_detail
            or details.count(legacy_detail) != 1
        ):
            raise ServiceError("legacy recovery must match the first unique unresolved detail")
        identity = json.dumps(
            [repository_id, feature_id, change_id, legacy_detail],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        observation_id = ObservationId(
            f"legacy-non-material-{sha256(identity.encode('utf-8')).hexdigest()}"
        )
        if any(item.id == observation_id for item in current.observations):
            raise ServiceError("legacy non-material recovery was already applied")
        observation = Observation(
            observation_id,
            context.change_id,
            "legacy_non_material_recovery",
            legacy_detail,
            False,
            None,
        )
        remaining = details[1:]
        unresolved = bool(remaining or context.safety_facts.material_clarifications)
        next_context = replace(
            context,
            unresolved_material_decision=unresolved,
            safety_facts=replace(
                context.safety_facts, unresolved_decision_details=remaining
            ),
            observation_ids=context.observation_ids + (observation_id,),
        )
        candidate = _record_observation(current, observation)
        if context.status is LifecycleState.PAUSED:
            if not unresolved:
                next_context = replace(
                    next_context,
                    phase=Phase.READY,
                    paused_pair=(LifecycleState.READY, Phase.READY),
                )
            candidate = _validated(
                replace(candidate, active_context=next_context),
                "legacy recovery context was rejected",
            )
        else:
            if not unresolved:
                next_context = replace(
                    next_context,
                    status=LifecycleState.READY,
                    phase=Phase.READY,
                    paused_pair=None,
                )
            candidate = _record_context(candidate, next_context)
        saved = _prepare_state_publication(candidate, current.revision)
        try:
            self.__persistence.publish_observation(saved, observation)
        except AtomicFailure as error:
            raise ServiceError("legacy non-material recovery was rejected") from error
        return saved

    def record_drift(self, state: State, drift: Drift) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _record_drift(current, drift), current.revision
        )
        try:
            self.__persistence.publish_drift(saved, drift)
        except AtomicFailure as error:
            raise ServiceError("drift publication was rejected") from error
        return saved

    def advance_lifecycle(
        self, state: State, status: LifecycleState, phase: Phase
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        active = current.active_context
        if active is None:
            raise ServiceError("lifecycle advance requires active work")
        paused_pair = (
            (active.status, active.phase) if status == LifecycleState.PAUSED else None
        )
        context = replace(active, status=status, phase=phase, paused_pair=paused_pair)
        saved = _prepare_state_publication(
            _record_context(current, context), current.revision
        )
        try:
            self.__persistence.publish_lifecycle(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

    def evaluate_autonomy(
        self, state: State, action: str, paths: tuple[RepositoryPath, ...] = (), *, irreversible: bool = False,
    ) -> AutonomyEvaluation:
        current = _authoritative_state(self.__persistence, state)
        if current.active_context is None:
            raise ServiceError("autonomy evaluation requires active work")
        return _evaluate_autonomy(current.active_context, action, paths, irreversible)

    def propose_autonomy_expansion(
        self, state: State, delta: AutonomyEnvelopeDelta
    ) -> State:
        """Persist one exact, revision-bound envelope proposal without granting it."""
        current = _authoritative_state(self.__persistence, state)
        active = current.active_context
        initialization = current.initialization
        if active is None or active.change_id is None or initialization is None:
            raise ServiceError("autonomy expansion requires initialized active Feature and Change work")
        if active.status not in (
            LifecycleState.PROPOSED,
            LifecycleState.IN_PROGRESS,
            LifecycleState.VERIFYING,
        ):
            raise ServiceError("autonomy expansion requires active nonterminal work")
        if active.unresolved_material_decision:
            raise ServiceError("resolve the existing material decision before proposing autonomy")
        if current.pending_autonomy_expansion is not None:
            raise ServiceError("one autonomy expansion proposal is already pending")
        if any(
            item.status in (
                IrreversibleAuthorizationStatus.PENDING,
                IrreversibleAuthorizationStatus.READY,
            )
            for item in current.irreversible_authorizations
        ):
            raise ServiceError("resolve the reserved one-shot authorization before proposing autonomy")
        if type(delta) is not AutonomyEnvelopeDelta:
            raise ServiceError("autonomy expansion delta is invalid")
        try:
            validate_autonomy_envelope_delta(delta)
        except StateValidationError as error:
            raise ServiceError("autonomy expansion delta is invalid") from error
        proposed = apply_autonomy_envelope_delta(active.autonomy_envelope, delta)
        if proposed.operation_budget == 0:
            raise ServiceError("an autonomy grant requires a finite positive operation budget")
        if not set(active.autonomy_envelope.stop_conditions).issubset(proposed.stop_conditions):
            raise ServiceError("an autonomy grant cannot remove existing stop conditions")
        if any(
            autonomy_operation_is_forbidden(action)
            for action in proposed.reversible_actions + proposed.irreversible_permissions
        ):
            raise ServiceError("force operations, history rewrites, resets, tags, and releases cannot be granted")
        payload = {
            "repository_id": initialization.repository_id,
            "feature_id": str(active.feature_id),
            "change_id": str(active.change_id),
            "requested_revision": current.revision,
            "base_envelope": _autonomy_envelope_json(active.autonomy_envelope),
            "delta": _autonomy_delta_json(delta),
        }
        proposal_id = AutonomyExpansionId(
            "autonomy-" + sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:16]
        )
        pending = PendingAutonomyExpansion(
            proposal_id,
            initialization.repository_id,
            active.feature_id,
            active.change_id,
            current.revision,
            active.autonomy_envelope,
            delta,
            proposed,
        )
        updated_active = replace(
            active,
            autonomy_metrics=replace(
                active.autonomy_metrics,
                operational_handoffs=active.autonomy_metrics.operational_handoffs + 1,
            ),
        )
        saved = _prepare_state_publication(
            replace(
                current,
                active_context=updated_active,
                pending_autonomy_expansion=pending,
            ),
            current.revision,
        )
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("autonomy expansion proposal was rejected") from error
        return saved

    def resolve_autonomy_expansion(
        self,
        state: State,
        proposal_id: AutonomyExpansionId,
        decision: AutonomyExpansionDecision,
        narrowed_envelope: AutonomyEnvelope | None = None,
    ) -> State:
        """Apply only the exact proposed envelope or an explicitly narrower choice."""
        current = _authoritative_state(self.__persistence, state)
        pending = current.pending_autonomy_expansion
        active = current.active_context
        initialization = current.initialization
        if pending is None or pending.proposal_id != proposal_id:
            raise ServiceError("autonomy expansion proposal is not pending")
        if (
            active is None
            or active.feature_id != pending.feature_id
            or active.change_id != pending.change_id
            or active.autonomy_envelope != pending.base_envelope
            or initialization is None
            or initialization.repository_id != pending.repository_id
        ):
            raise ServiceError("autonomy expansion proposal no longer matches active work")
        if type(decision) is not AutonomyExpansionDecision:
            raise ServiceError("autonomy expansion decision is invalid")
        if decision is AutonomyExpansionDecision.AUTHORIZE:
            if narrowed_envelope is not None:
                raise ServiceError("authorize must accept the exact proposed envelope")
            resolved = pending.proposed_envelope
        elif decision is AutonomyExpansionDecision.REJECT:
            if narrowed_envelope is not None:
                raise ServiceError("reject cannot include envelope changes")
            resolved = pending.base_envelope
        else:
            if type(narrowed_envelope) is not AutonomyEnvelope:
                raise ServiceError("narrow requires the exact narrower envelope choice")
            try:
                validate_autonomy_envelope(narrowed_envelope)
            except StateValidationError as error:
                raise ServiceError("narrowed autonomy envelope is invalid") from error
            if (
                narrowed_envelope == pending.proposed_envelope
                or not autonomy_envelope_is_narrowing(pending.proposed_envelope, narrowed_envelope)
            ):
                raise ServiceError("narrowed envelope must be a strict subset of the exact proposal")
            resolved = narrowed_envelope
        if any(
            autonomy_operation_is_forbidden(action)
            for action in resolved.reversible_actions + resolved.irreversible_permissions
        ):
            raise ServiceError("protected operations cannot be granted by an autonomy envelope")
        record = AutonomyExpansionRecord(
            pending.proposal_id,
            pending.repository_id,
            pending.feature_id,
            pending.change_id,
            pending.requested_revision,
            current.revision + 1,
            decision,
            pending.base_envelope,
            pending.delta,
            pending.proposed_envelope,
            resolved,
        )
        updated_active = replace(
            active,
            autonomy_envelope=resolved,
            autonomy_metrics=replace(
                active.autonomy_metrics,
                human_material_decisions=active.autonomy_metrics.human_material_decisions + 1,
            ),
        )
        saved = _prepare_state_publication(
            replace(
                current,
                active_context=updated_active,
                pending_autonomy_expansion=None,
                autonomy_expansion_history=current.autonomy_expansion_history + (record,),
            ),
            current.revision,
        )
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("autonomy expansion decision was rejected") from error
        return saved

    def record_irreversible_authorization(
        self,
        state: State,
        operation_kind: IrreversibleOperationKind,
        target_ref: str,
        expected_remote_sha: str,
        rewrite_policy: GitHistoryRewritePolicy,
        target_identity_name: str,
        target_identity_email: str,
        expected_result_sha: str,
        commit_rewrites: tuple[GitCommitRewrite, ...],
        purpose: str,
        force_with_lease: bool,
    ) -> State:
        """Persist explicit one-shot authority for a single exact history rewrite."""
        current = _authoritative_state(self.__persistence, state)
        active = current.active_context
        if active is None or active.change_id is None:
            raise ServiceError("irreversible authorization requires active Feature and Change")
        if active.status not in (LifecycleState.IN_PROGRESS, LifecycleState.VERIFYING):
            raise ServiceError("irreversible authorization requires active Feature and Change")
        if operation_kind is not IrreversibleOperationKind.GIT_HISTORY_REWRITE:
            raise ServiceError("Core only supports explicit one-shot Git history rewrite authorization")
        if not is_git_branch_ref(target_ref):
            raise ServiceError("authorization target must be one canonical refs/heads branch")
        if not is_git_sha(expected_remote_sha):
            raise ServiceError("authorization requires the exact current remote SHA")
        if (
            not is_git_sha(expected_result_sha)
            or len(expected_result_sha) != len(expected_remote_sha)
            or expected_result_sha == expected_remote_sha
            or type(commit_rewrites) is not tuple
            or not commit_rewrites
            or any(type(item) is not GitCommitRewrite for item in commit_rewrites)
            or not any(
                item.old_sha == expected_remote_sha
                and item.new_sha == expected_result_sha
                for item in commit_rewrites
            )
            or not any(item.identity_normalized for item in commit_rewrites)
        ):
            raise ServiceError("authorization requires the exact nonempty rewritten commit plan")
        if (
            rewrite_policy is not GitHistoryRewritePolicy.NORMALIZE_MISMATCHED_IDENTITIES
            or not is_git_identity(target_identity_name, target_identity_email)
        ):
            raise ServiceError("authorization requires the supported exact Git identity normalization")
        if type(purpose) is not str or not purpose.strip():
            raise ServiceError("authorization purpose must be nonblank")
        if type(force_with_lease) is not bool or not force_with_lease:
            raise ServiceError("Git history rewrite requires force-with-lease")
        if any(
            item.status in (
                IrreversibleAuthorizationStatus.PENDING,
                IrreversibleAuthorizationStatus.READY,
            )
            for item in current.irreversible_authorizations
        ):
            raise ServiceError("an irreversible authorization is already pending")
        token = sha256(
            "\0".join(
                (
                    str(current.revision),
                    str(active.feature_id),
                    str(active.change_id),
                    operation_kind.value,
                    target_ref,
                    expected_remote_sha,
                    rewrite_policy.value,
                    target_identity_name,
                    target_identity_email,
                    expected_result_sha,
                    *(f"{item.old_sha}:{item.new_sha}:{int(item.identity_normalized)}" for item in commit_rewrites),
                    purpose.strip(),
                )
            ).encode("utf-8")
        ).hexdigest()[:16]
        authorization = IrreversibleAuthorization(
            IrreversibleAuthorizationId(f"irreversible-{token}"),
            operation_kind,
            active.feature_id,
            active.change_id,
            target_ref,
            expected_remote_sha,
            rewrite_policy,
            target_identity_name,
            target_identity_email,
            expected_result_sha,
            commit_rewrites,
            "0" * 64,
            purpose.strip(),
            True,
            True,
            current.revision + 1,
            IrreversibleAuthorizationStatus.PENDING,
        )
        authorization = replace(
            authorization,
            rewrite_plan_digest=git_rewrite_plan_digest(authorization),
        )
        candidate = replace(
            current,
            irreversible_authorizations=current.irreversible_authorizations
            + (authorization,),
        )
        saved = _prepare_state_publication(candidate, current.revision)
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("irreversible authorization was rejected") from error
        return saved

    def execute_irreversible_authorization(
        self, state: State, authorization_id: str
    ) -> tuple[State, bool]:
        """Execute the persisted Git rewrite with an explicit lease, then consume it."""
        current = _authoritative_state(self.__persistence, state)
        item = next(
            (value for value in current.irreversible_authorizations if str(value.id) == authorization_id),
            None,
        )
        if item is None or item.status not in (
            IrreversibleAuthorizationStatus.PENDING,
            IrreversibleAuthorizationStatus.READY,
        ):
            raise ServiceError("irreversible authorization is missing, stale, or already used")
        if item.operation_kind is not IrreversibleOperationKind.GIT_HISTORY_REWRITE:
            raise ServiceError("Core cannot execute this irreversible operation kind")
        active = current.active_context
        if (
            active is None
            or active.feature_id != item.feature_id
            or active.change_id != item.change_id
            or (
                item.status is IrreversibleAuthorizationStatus.PENDING
                and current.revision != item.authorized_revision
            )
            or (
                item.status is IrreversibleAuthorizationStatus.READY
                and current.revision != item.validated_revision
            )
        ):
            return (
                self._invalidate_irreversible_authorization(
                    current, item, "active Core authority changed before Git execution"
                ),
                False,
            )
        if not item.force_with_lease or not item.one_shot:
            raise ServiceError("Core requires one-shot force-with-lease authorization")

        root = self.__repository_root
        _git_output(root, "rev-parse", "--show-toplevel")
        fetch_urls = _git_output(root, "remote", "get-url", "--all", "origin").decode("utf-8").splitlines()
        push_urls = _git_output(
            root, "remote", "get-url", "--push", "--all", "origin"
        ).decode("utf-8").splitlines()
        if len(fetch_urls) != 1 or push_urls != fetch_urls:
            raise ServiceError("Core requires one origin URL shared by fetch and push")
        mirror = _git_run(root, "config", "--bool", "--get", "remote.origin.mirror")
        if mirror.returncode == 0 and mirror.stdout.strip().lower() == b"true":
            raise ServiceError("Core will not execute a mirror push for one-shot branch authorization")
        configured_push = _git_run(root, "config", "--get-all", "remote.origin.push")
        if configured_push.returncode == 0 and configured_push.stdout.strip():
            raise ServiceError("Core will not execute a push with configured extra refspecs")

        local_tip = _git_output(
            root, "rev-parse", "--verify", f"{item.target_ref}^{{commit}}"
        ).decode("ascii").strip()
        if local_tip != item.expected_remote_sha:
            return (
                self._invalidate_irreversible_authorization(
                    current, item, "local target branch no longer matches the authorized base SHA"
                ),
                False,
            )
        if not is_git_sha(item.expected_remote_sha) or not is_git_sha(item.expected_result_sha):
            raise ServiceError("Core found malformed Git object IDs in the authorization")

        try:
            exact_plan, objects = _git_identity_rewrite_plan(root, item)
        except ServiceError:
            raise
        if exact_plan != item.commit_rewrites:
            raise ServiceError("Core recomputed a different commit rewrite plan")

        before_refs = _git_remote_refs(root)
        remote_tip = before_refs.get(item.target_ref)
        if remote_tip != item.expected_remote_sha:
            if (
                remote_tip is not None
                and remote_tip == item.expected_result_sha
                and item.status is IrreversibleAuthorizationStatus.READY
            ):
                # Recover a completed push whose process stopped before durable consumption.
                return self._consume_executed_authorization(current, item, remote_tip), True
            return (
                self._invalidate_irreversible_authorization(
                    current, item, "target branch no longer points to the authorized remote SHA"
                ),
                False,
            )

        if item.status is IrreversibleAuthorizationStatus.PENDING:
            ready = replace(
                item,
                status=IrreversibleAuthorizationStatus.READY,
                validated_revision=current.revision + 1,
            )
            candidate = replace(
                current,
                irreversible_authorizations=tuple(
                    ready if value.id == item.id else value
                    for value in current.irreversible_authorizations
                ),
            )
            current = _prepare_state_publication(candidate, current.revision)
            try:
                self.__persistence.publish_feature(current)
            except AtomicFailure as error:
                raise ServiceError("Git execution reservation was rejected") from error
            item = ready

        for expected_sha, raw_commit in objects:
            actual_sha = _git_output(
                root, "hash-object", "-t", "commit", "-w", "--stdin", input_bytes=raw_commit
            ).decode("ascii").strip()
            if actual_sha != expected_sha:
                raise ServiceError("Core could not materialize the exact authorized commit object")

        # Push one explicit refspec and pin the lease to the authorized remote object ID.
        push = _git_run(
            root,
            "-c", "push.followTags=false",
            "-c", "push.default=nothing",
            "push", "--porcelain", "--no-follow-tags",
            f"--force-with-lease={item.target_ref}:{item.expected_remote_sha}",
            "origin",
            f"{item.expected_result_sha}:{item.target_ref}",
            timeout=300,
        )
        result_tip = _git_remote_ref_sha(root, item.target_ref)
        if result_tip != item.expected_result_sha:
            if result_tip is not None and result_tip != item.expected_remote_sha:
                latest = self.__persistence.recover()
                latest_item = next(
                    value for value in latest.irreversible_authorizations if value.id == item.id
                )
                invalidated = self._invalidate_irreversible_authorization(
                    latest,
                    latest_item,
                    "target branch changed to an unexpected remote SHA during force-with-lease push",
                )
                return invalidated, False
            if push.returncode != 0:
                raise ServiceError("force-with-lease push failed; authorization remains ready for retry")
            raise ServiceError("Git reported push success without the exact authorized remote SHA")
        if result_tip is None:
            raise ServiceError("authorized Git result ref disappeared before consumption")

        latest = self.__persistence.recover()
        latest_item = next(
            (value for value in latest.irreversible_authorizations if value.id == item.id), None
        )
        if (
            latest_item is None
            or latest_item.status is not IrreversibleAuthorizationStatus.READY
            or latest.revision != latest_item.validated_revision
        ):
            raise ServiceError("Core authority changed while the authorized Git push was running")
        after_refs = _git_remote_refs(root)
        expected_refs = dict(before_refs)
        expected_refs[item.target_ref] = item.expected_result_sha
        unaffected_refs_unchanged = after_refs == expected_refs
        consumed = self._consume_executed_authorization(latest, latest_item, result_tip)
        if not unaffected_refs_unchanged:
            raise ServiceError(
                "authorized branch was pushed and consumed, but unrelated remote refs changed concurrently"
            )
        return consumed, True

    def _consume_executed_authorization(
        self, current: State, item: IrreversibleAuthorization, resulting_remote_sha: str
    ) -> State:
        if (
            item.status is not IrreversibleAuthorizationStatus.READY
            or current.revision != item.validated_revision
            or resulting_remote_sha != item.expected_result_sha
        ):
            raise ServiceError("Core cannot consume an operation that was not executed exactly")
        consumed = replace(
            item,
            status=IrreversibleAuthorizationStatus.CONSUMED,
            consumed_revision=current.revision + 1,
            resulting_remote_sha=resulting_remote_sha,
        )
        candidate = replace(
            current,
            irreversible_authorizations=tuple(
                consumed if value.id == item.id else value
                for value in current.irreversible_authorizations
            ),
        )
        saved = _prepare_state_publication(candidate, current.revision)
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("executed irreversible authorization could not be consumed") from error
        return saved

    def _invalidate_irreversible_authorization(
        self, current: State, item: IrreversibleAuthorization, reason: str
    ) -> State:
        invalidated = replace(
            item,
            status=IrreversibleAuthorizationStatus.INVALIDATED,
            invalidated_revision=current.revision + 1,
            invalidation_reason=reason,
        )
        candidate = replace(
            current,
            irreversible_authorizations=tuple(
                invalidated if value.id == item.id else value
                for value in current.irreversible_authorizations
            ),
        )
        saved = _prepare_state_publication(candidate, current.revision)
        try:
            self.__persistence.publish_feature(saved)
        except AtomicFailure as error:
            raise ServiceError("irreversible authorization invalidation was rejected") from error
        return saved

    def park(self, state: State, reason: str) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_park(current, reason), current.revision)
        try:
            self.__persistence.publish_lifecycle(saved)
        except AtomicFailure as error:
            raise ServiceError("work could not be parked") from error
        return saved

    def resume_parked(self, state: State, change_id: str) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_resume_parked(current, change_id), current.revision)
        try:
            self.__persistence.publish_lifecycle(saved)
        except AtomicFailure as error:
            raise ServiceError("parked work could not be resumed") from error
        return saved

    def activate_work(self, state: State, work_id: str) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_activate_work(current, work_id), current.revision)
        try:
            self.__persistence.publish_lifecycle(saved)
        except AtomicFailure as error:
            raise ServiceError("work item could not be activated") from error
        return saved

    def next_actionable(self, state: State) -> NextActionable:
        return _next_actionable(_authoritative_state(self.__persistence, state))

    def set_work_state(self, state: State, work_id: WorkId, operational_state: OperationalState, reason: str | None = None) -> State:
        current = _authoritative_state(self.__persistence, state)
        item = next((item for item in current.work_items if item.id == work_id), None)
        if item is None or item.operational_state is OperationalState.DONE or operational_state is OperationalState.DONE:
            raise ServiceError("work item transition is invalid")
        candidate = replace(current, work_items=tuple(
            replace(candidate, operational_state=operational_state, reason=reason, transitioned_at=datetime.now(timezone.utc).isoformat())
            if candidate.id == work_id else candidate for candidate in current.work_items
        ))
        saved = _prepare_state_publication(_validated(candidate, "work item transition was rejected"), current.revision)
        try:
            self.__persistence.publish_lifecycle(saved)
        except AtomicFailure as error:
            raise ServiceError("work item state publication was rejected") from error
        return saved

    def add_work_relation(self, state: State, relation: WorkRelation) -> State:
        current = _authoritative_state(self.__persistence, state)
        candidate = replace(current, work_relations=current.work_relations + (relation,))
        saved = _prepare_state_publication(_validated(candidate, "work relation was rejected"), current.revision)
        try:
            self.__persistence.publish_lifecycle(saved)
        except AtomicFailure as error:
            raise ServiceError("work relation publication was rejected") from error
        return saved

    def remove_work_relation(self, state: State, relation: WorkRelation) -> State:
        current = _authoritative_state(self.__persistence, state)
        if relation not in current.work_relations:
            raise ServiceError("work relation is unavailable")
        candidate = replace(
            current,
            work_relations=tuple(item for item in current.work_relations if item != relation),
        )
        saved = _prepare_state_publication(_validated(candidate, "work relation was rejected"), current.revision)
        try:
            self.__persistence.publish_lifecycle(saved)
        except AtomicFailure as error:
            raise ServiceError("work relation publication was rejected") from error
        return saved

    def work_item_blocker(self, state: State, work_id: WorkId) -> str | None:
        current = _authoritative_state(self.__persistence, state)
        item = next((item for item in current.work_items if item.id == work_id), None)
        if item is None:
            raise ServiceError("work item is unavailable")
        return _activation_blocker(current, item)

    def record_progress(
        self,
        state: State,
        current_action: str | None,
        focus: str | None,
        completed_work: tuple[str, ...],
        current_work: str | None,
        pending_work: tuple[str, ...],
        resume_point: str | None,
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        if current.active_context is not None and current.active_context.slice_agreement_ref is not None:
            raise ServiceError("accepted E1 Task progress requires the guarded typed operation")
        active = current.active_context
        if active is None:
            raise ServiceError("progress recording requires active work")
        context = replace(
            active,
            current_action=current_action,
            focus=focus,
            completed_work=completed_work,
            current_work=current_work,
            pending_work=pending_work,
            resume_point=resume_point,
            autonomy_metrics=replace(
                active.autonomy_metrics,
                automatic_operations=active.autonomy_metrics.automatic_operations + 1,
            ),
        )
        saved = _prepare_state_publication(
            _record_context(current, context), current.revision
        )
        try:
            self.__persistence.publish_progress(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

    def record_safety_facts(
        self, state: State, unresolved_material_decision: bool, safety_facts: SafetyFacts
    ) -> State:
        """Persist caller facts without inferring or advancing workflow state."""
        current = _authoritative_state(self.__persistence, state)
        active = current.active_context
        if active is None or type(unresolved_material_decision) is not bool:
            raise ServiceError("safety facts require active work and a boolean decision flag")
        context = replace(
            active,
            unresolved_material_decision=unresolved_material_decision,
            safety_facts=safety_facts,
        )
        saved = _prepare_state_publication(
            _record_context(current, context), current.revision
        )
        try:
            self.__persistence.publish_safety_facts(saved)
        except AtomicFailure as error:
            raise ServiceError("safety facts were rejected") from error
        return saved

    def record_review(
        self,
        state: State,
        review: ReviewResult,
        checkpoints: tuple[Checkpoint, ...] = (),
        evidence: tuple[ReviewEvidence, ...] = (),
        findings: tuple[ReviewFinding, ...] = (),
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        active = current.active_context
        if active is None or (active.status, active.phase) != (
            LifecycleState.IN_PROGRESS,
            Phase.REVIEW,
        ):
            raise ServiceError("review recording requires active review work")
        if has_acceptable_review(review) and _lock_gate(current) is not None:
            raise ServiceError("review cannot safely proceed with unresolved semantic lock gates")
        saved = _prepare_state_publication(
            _record_context(
                current,
                replace(
                    active,
                    review=review,
                    checkpoints=checkpoints,
                    review_evidence=evidence,
                    review_findings=findings,
                ),
            ),
            current.revision,
        )
        try:
            self.__persistence.publish_review(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

    def record_verification(
        self, state: State, verification: VerificationEvidence
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        active = current.active_context
        if active is None or (active.status, active.phase) != (
            LifecycleState.VERIFYING,
            Phase.VERIFY,
        ):
            raise ServiceError("verification recording requires active verification work")
        saved = _prepare_state_publication(
            _record_context(current, replace(active, verification=verification)),
            current.revision,
        )
        try:
            self.__persistence.publish_verification(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

    def confirm_convergence(self, state: State) -> State:
        current = _authoritative_state(self.__persistence, state)
        active = current.active_context
        if active is None or active.phase != Phase.CONVERGE:
            raise ServiceError("convergence confirmation requires convergence phase")
        if _lock_gate(current) is not None:
            raise ServiceError("convergence cannot complete unresolved semantic lock gates")
        if _convergence_policy_gate(current) is not None:
            raise ServiceError("convergence cannot complete unresolved experiment or drift gates")
        saved = _prepare_state_publication(
            _record_context(current, replace(active, convergence_complete=True)),
            current.revision,
        )
        try:
            self.__persistence.publish_convergence(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

    def record_repair(
        self,
        state: State,
        repair_count: int,
        blocker: str | None,
        safe_next_step: str | None,
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        active = current.active_context
        if active is None:
            raise ServiceError("repair recording requires active work")
        if type(repair_count) is not int or repair_count != active.repair_count + 1:
            raise ServiceError("repair count must advance exactly once")
        context = replace(
            active,
            repair_count=repair_count,
            blocker=blocker,
            safe_next_step=safe_next_step,
        )
        saved = _prepare_state_publication(
            _record_context(current, context), current.revision
        )
        try:
            self.__persistence.publish_repair(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

    def activate_side(self, state: State, change: Change) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _activate_side(current, change), current.revision
        )
        try:
            self.__persistence.publish_side_activation(saved, change)
        except AtomicFailure as error:
            raise ServiceError("side activation publication was rejected") from error
        return saved

    def archive_terminal(self, state: State, terminal: WorkingContext) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _archive_terminal(current, terminal), current.revision
        )
        try:
            self.__persistence.publish_terminal_archive(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

    def restore_primary(
        self, state: State, completed_side: WorkingContext
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _restore_primary(current, completed_side), current.revision
        )
        try:
            self.__persistence.publish_primary_restoration(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

    def publish_accepted_convergence(
        self,
        state: State,
        feature_id: FeatureId,
        change_id: ChangeId,
        living_spec_id: LivingSpecId,
        revision: str,
        content: str,
        assessment: ConvergenceAssessment,
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        if type(content) is not str or not content.strip():
            raise ServiceError("Living Spec publication content must not be blank")
        candidate, publication = _publish_candidate(
            current, feature_id, change_id, living_spec_id, revision, assessment
        )
        saved = _prepare_state_publication(candidate, current.revision)
        try:
            self.__persistence.publish_primary_convergence(saved, publication, content)
        except AtomicFailure as error:
            raise ServiceError("Living Spec publication was rejected") from error
        return saved

    def complete_side(self, state: State, completed_side: WorkingContext) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_complete_side(current, completed_side), current.revision)
        try:
            self.__persistence.publish_primary_restoration(saved)
        except AtomicFailure as error:
            raise ServiceError("side completion was rejected") from error
        return saved

    def materialize_pending(self, state: State, feature: Feature, change: Change) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(_materialize_pending(current, feature, change), current.revision)
        try:
            self.__persistence.publish_change(saved, change)
        except AtomicFailure as error:
            raise ServiceError("pending intake was rejected") from error
        return saved

    def materialize_linked(
        self, state: State, linked_id: str, feature: Feature, change: Change
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _materialize_linked(current, linked_id, feature, change), current.revision
        )
        try:
            self.__persistence.publish_change(saved, change)
        except AtomicFailure as error:
            raise ServiceError("linked materialization was rejected") from error
        return saved

    def publish_accepted_side_convergence(
        self,
        state: State,
        revision: str,
        content: str,
        assessment: ConvergenceAssessment,
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        if type(content) is not str or not content.strip():
            raise ServiceError("Living Spec publication content must not be blank")
        candidate, publication = _publish_side_candidate(current, revision, assessment)
        saved = _prepare_state_publication(candidate, current.revision)
        try:
            self.__persistence.publish_side_convergence(saved, publication, content)
        except AtomicFailure as error:
            raise ServiceError("Living Spec publication was rejected") from error
        return saved

    def accept_convergence_without_publication(
        self, state: State, assessment: ConvergenceAssessment
    ) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _accept_without_publication_candidate(current, assessment), current.revision
        )
        try:
            self.__persistence.publish_terminal_archive(saved)
        except AtomicFailure as error:
            raise ServiceError("accepted archive was rejected") from error
        return saved

    def inspect_review(self) -> ReviewInspection:
        state, e1_schema_ready = self.__persistence.recover_snapshot()
        return replace(inspect(state), e1_schema_ready=e1_schema_ready)

    def lock_gate(self, state: State) -> str | None:
        return _lock_gate(_authoritative_state(self.__persistence, state))

    def record_implementation_alteration(self, state: State) -> State:
        current = _authoritative_state(self.__persistence, state)
        saved = _prepare_state_publication(
            _record_alteration(current), current.revision
        )
        try:
            self.__persistence.publish_implementation_alteration(saved)
        except AtomicFailure as error:
            raise ServiceError("state mutation was rejected") from error
        return saved

__all__ = (
    "CoreService",
    "ServiceConstructionError",
    "ServiceError",
    "StaleRevisionError",
)
